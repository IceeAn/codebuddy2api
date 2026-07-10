"""
Configuration management for CodeBuddy2API

Implements startup configuration plus user-scoped runtime settings.

Startup configuration priority:
1. Environment variables (for deployment and security boundaries)
2. Hard-coded defaults

User settings are persisted by username in data/codebuddy2api.sqlite3.
Users without saved settings inherit the startup defaults.
"""
import os
import logging
import threading
from pathlib import Path
from typing import Dict, Any, Optional
from urllib.parse import urlsplit, urlunsplit

from src.sqlite_database import SQLiteDatabase, resolve_database_path
from src.user_settings_schema import (
    USER_SETTING_KEYS as _USER_SETTING_KEYS,
    coerce_user_setting as _coerce_user_setting,
    sanitize_user_settings as _sanitize_user_settings,
)
from src.user_settings_store import UserSettingsStore

logger = logging.getLogger(__name__)

# 相对运行数据路径始终以应用根目录为基准，不受进程工作目录影响。
_APPLICATION_ROOT = Path(__file__).resolve().parent

DEFAULT_FORCED_REASONING_MODELS = (
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "glm-5.1",
    "glm-5.2",
)

DEFAULT_CODEBUDDY_MODELS = (
    "glm-5.2",
    "deepseek-v4-pro",
)

# --- Private State ---
_config_cache: Dict[str, Any] = {}

_DEFAULT_CONFIG = {
    "CODEBUDDY_HOST": "127.0.0.1",
    "CODEBUDDY_PORT": 8001,
    "CODEBUDDY_USERS_FILE": "secrets/users.txt",
    "CODEBUDDY_API_ENDPOINT": "https://copilot.tencent.com",
    "CODEBUDDY_ALLOWED_API_ENDPOINTS": "https://copilot.tencent.com,https://www.codebuddy.ai",
    "CODEBUDDY_DATA_DIR": "data",
    "CODEBUDDY_ALLOWED_HOSTS": "localhost,127.0.0.1",
    "CODEBUDDY_ALLOWED_ORIGINS": "",
    "CODEBUDDY_SSL_VERIFY": True,
    "CODEBUDDY_LOG_LEVEL": "INFO",
    "CODEBUDDY_MODELS": ",".join(DEFAULT_CODEBUDDY_MODELS),
    "CODEBUDDY_FORCED_REASONING_MODELS": ",".join(DEFAULT_FORCED_REASONING_MODELS),
    "CODEBUDDY_FORCED_TEMPERATURE": "1",
    "CODEBUDDY_STRIP_MODEL_NAMESPACE": True,
    "CODEBUDDY_AUTO_ROTATION_ENABLED": True,
    "CODEBUDDY_ROTATION_COUNT": 1
}

_user_settings_cache: Dict[str, Dict[str, Any]] = {}
_USER_SETTINGS_LOCK = threading.RLock()

# --- Core Functions ---

def load_config():
    """
    Loads configuration from all sources into the in-memory cache.
    This should be called once at application startup.
    """
    global _config_cache, _user_settings_cache
    
    config = _DEFAULT_CONFIG.copy()
    
    try:
        from dotenv import load_dotenv
        load_dotenv()
        logger.info("Loaded environment variables from .env file.")
    except ImportError:
        logger.warning("python-dotenv not installed, skipping .env file loading.")

    for key in config:
        env_value = os.getenv(key)
        if env_value is not None:
            config[key] = env_value
    with _USER_SETTINGS_LOCK:
        _config_cache = config
        _user_settings_cache = _load_user_settings()
    logger.info("Configuration loaded successfully.")


def _get_config_value(key: str) -> Any:
    return _config_cache.get(key, _DEFAULT_CONFIG.get(key))


def _username_from_user(user: Any = None, username: Optional[str] = None) -> Optional[str]:
    if username is not None:
        return str(username).strip()
    if user is None:
        return None
    if isinstance(user, str):
        return user.strip()
    value = getattr(user, "username", None)
    if value is None:
        return None
    return str(value).strip()


def _get_user_config_value(key: str, user: Any = None, username: Optional[str] = None) -> Any:
    with _USER_SETTINGS_LOCK:
        user_key = _username_from_user(user, username)
        if user_key and key in _user_settings_cache.get(user_key, {}):
            return _user_settings_cache[user_key][key]
        return _get_config_value(key)


def _to_bool(value: Any) -> bool:
    """将配置值转换为布尔值。"""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "t", "y", "yes", "on")


def _parse_csv(value: Any) -> list:
    """解析逗号分隔配置，自动过滤空项。"""
    if value is None:
        return []
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _load_user_settings() -> Dict[str, Dict[str, Any]]:
    raw_users = UserSettingsStore(get_database_path()).load_all()
    return {
        str(username): _sanitize_user_settings(settings)
        for username, settings in raw_users.items()
    }


def _normalize_base_url(url: Any) -> str:
    """标准化 HTTPS base URL，避免不同写法绕过白名单。"""
    raw_url = str(url or "").strip().rstrip("/")
    parsed = urlsplit(raw_url)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        return ""
    return urlunsplit((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path.rstrip("/"),
        "",
        "",
    ))


# --- Public Getter Functions ---

def get_active_config() -> Dict[str, Any]:
    return {key: _config_cache.get(key) for key in _DEFAULT_CONFIG}


def get_editable_config(user: Any = None, username: Optional[str] = None) -> Dict[str, Any]:
    """返回当前用户可编辑设置；未保存的用户使用系统默认配置。"""
    with _USER_SETTINGS_LOCK:
        return {
            key: _get_editable_config_value(key, user, username)
            for key in _USER_SETTING_KEYS
        }


def _get_editable_config_value(key: str, user: Any = None, username: Optional[str] = None) -> Any:
    owner = user if user is not None else username
    if key == "CODEBUDDY_FORCED_TEMPERATURE":
        return get_forced_temperature(owner)
    if key == "CODEBUDDY_STRIP_MODEL_NAMESPACE":
        return get_strip_model_namespace(owner)
    if key == "CODEBUDDY_AUTO_ROTATION_ENABLED":
        return get_auto_rotation_enabled(owner)
    if key == "CODEBUDDY_ROTATION_COUNT":
        return get_rotation_count(owner)
    return str(_get_user_config_value(key, user, username) or "")


def get_server_host() -> str:
    return str(_get_config_value("CODEBUDDY_HOST"))

def get_server_port() -> int:
    return int(_get_config_value("CODEBUDDY_PORT"))

def get_users_file_path() -> str:
    return str(_get_config_value("CODEBUDDY_USERS_FILE"))

def get_codebuddy_api_endpoint() -> str:
    endpoint = _normalize_base_url(_get_config_value("CODEBUDDY_API_ENDPOINT"))
    default_endpoint = _normalize_base_url(_DEFAULT_CONFIG["CODEBUDDY_API_ENDPOINT"])
    allowed_endpoints = get_allowed_api_endpoints()

    if endpoint and endpoint in allowed_endpoints:
        return endpoint

    logger.error(
        "Blocked unsafe CODEBUDDY_API_ENDPOINT '%s'. Falling back to '%s'.",
        _get_config_value("CODEBUDDY_API_ENDPOINT"),
        default_endpoint,
    )
    return default_endpoint


def get_codebuddy_api_host() -> str:
    """返回当前 CodeBuddy 上游域名，用于 Host / X-Domain 请求头。"""
    return urlsplit(get_codebuddy_api_endpoint()).netloc


def get_allowed_api_endpoints() -> list:
    allowed = [
        _normalize_base_url(item)
        for item in _parse_csv(_get_config_value("CODEBUDDY_ALLOWED_API_ENDPOINTS"))
    ]
    allowed = [item for item in allowed if item]
    default_endpoint = _normalize_base_url(_DEFAULT_CONFIG["CODEBUDDY_API_ENDPOINT"])
    if default_endpoint not in allowed:
        allowed.append(default_endpoint)
    return allowed

def get_codebuddy_creds_dir() -> str:
    return str(Path(get_data_dir()) / "credentials")


def get_data_dir() -> str:
    """返回以应用根目录为基准解析后的绝对运行数据目录。"""
    data_dir = Path(str(_get_config_value("CODEBUDDY_DATA_DIR")))
    if not data_dir.is_absolute():
        data_dir = _APPLICATION_ROOT / data_dir
    return str(data_dir)


def get_database_path() -> Path:
    """返回 API Key、用户设置与持久化统计共用的 SQLite 数据库路径。"""
    return resolve_database_path(get_data_dir())


def initialize_database() -> None:
    """初始化空数据库及 schema；用户设置仍在首次保存时写入。"""
    with SQLiteDatabase(get_database_path()).connect():
        pass


def get_allowed_origins() -> list:
    return _parse_csv(_get_config_value("CODEBUDDY_ALLOWED_ORIGINS"))


def get_allowed_hosts() -> list:
    return _parse_csv(_get_config_value("CODEBUDDY_ALLOWED_HOSTS"))


def get_ssl_verify() -> bool:
    return _to_bool(_get_config_value("CODEBUDDY_SSL_VERIFY"))

def get_log_level() -> str:
    return str(_get_config_value("CODEBUDDY_LOG_LEVEL")).upper()

def get_available_models(user: Any = None) -> list:
    models_str = str(_get_user_config_value("CODEBUDDY_MODELS", user))
    return [model.strip() for model in models_str.split(",")]


def get_forced_reasoning_models(user: Any = None) -> list:
    return _parse_csv(_get_user_config_value("CODEBUDDY_FORCED_REASONING_MODELS", user))


def get_forced_temperature(user: Any = None) -> Optional[float]:
    value = _get_user_config_value("CODEBUDDY_FORCED_TEMPERATURE", user)
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return value

    raw_value = str(value).strip()
    if not raw_value:
        return None

    try:
        temperature = float(raw_value)
    except ValueError:
        logger.warning("Invalid CODEBUDDY_FORCED_TEMPERATURE value: %s", value)
        return None

    if temperature.is_integer():
        return int(temperature)
    return temperature


def get_strip_model_namespace(user: Any = None) -> bool:
    value = _get_user_config_value("CODEBUDDY_STRIP_MODEL_NAMESPACE", user)
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return _to_bool(value)


def get_auto_rotation_enabled(user: Any = None) -> bool:
    return _to_bool(_get_user_config_value("CODEBUDDY_AUTO_ROTATION_ENABLED", user))


def get_rotation_count(user: Any = None) -> int:
    rotation_count = int(_get_user_config_value("CODEBUDDY_ROTATION_COUNT", user))
    if rotation_count < 1:
        raise ValueError("CODEBUDDY_ROTATION_COUNT must be a positive integer")
    return rotation_count

# --- Public Setter for Hot-Reload ---

def update_settings(new_settings: Dict[str, Any], user: Any = None, username: Optional[str] = None):
    """更新当前用户的可编辑设置并持久化。"""
    user_key = _username_from_user(user, username)
    if not user_key:
        raise ValueError("username is required when updating user settings")

    sanitized = {
        key: _coerce_user_setting(key, value)
        for key, value in new_settings.items()
    }
    with _USER_SETTINGS_LOCK:
        UserSettingsStore(get_database_path()).update(user_key, sanitized)
        _user_settings_cache.setdefault(user_key, {}).update(sanitized)

# --- Initial Load ---
load_config()
