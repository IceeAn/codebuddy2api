"""
Configuration management for CodeBuddy2API

Implements startup configuration plus user-scoped runtime settings.

Startup configuration priority:
1. Environment variables (for deployment and security boundaries)
2. Hard-coded defaults

User settings are persisted by username in data/codebuddy2api.sqlite3.
Users without saved settings inherit the startup defaults.
"""
import ipaddress
import logging
import os
import re
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

_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")

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
    "CODEBUDDY_CSP_FRAME_ANCESTORS": "none",
    "CODEBUDDY_MAX_REQUEST_BODY_BYTES": 16 * 1024 * 1024,
    "CODEBUDDY_LOGIN_RATE_WINDOW_SECONDS": 60,
    "CODEBUDDY_LOGIN_GLOBAL_MAX_ATTEMPTS": 60,
    "CODEBUDDY_LOGIN_IP_MAX_ATTEMPTS": 10,
    "CODEBUDDY_LOGIN_USERNAME_MAX_ATTEMPTS": 5,
    "CODEBUDDY_LOGIN_MAX_CONCURRENCY": 2,
    "CODEBUDDY_MAX_CONCURRENT_REQUESTS": "",
    "CODEBUDDY_SSL_VERIFY": True,
    "CODEBUDDY_LOG_LEVEL": "INFO",
    "CODEBUDDY_MODELS": ",".join(DEFAULT_CODEBUDDY_MODELS),
    "CODEBUDDY_FORCED_REASONING_MODELS": ",".join(DEFAULT_FORCED_REASONING_MODELS),
    "CODEBUDDY_FORCED_TEMPERATURE": "1",
    "CODEBUDDY_STRIP_MODEL_NAMESPACE": True,
    "CODEBUDDY_AUTO_ROTATION_ENABLED": True,
    "CODEBUDDY_AUTO_CHECKIN_ENABLED": False,
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

    dotenv_path = _APPLICATION_ROOT / ".env"
    if dotenv_path.is_file():
        try:
            from dotenv import load_dotenv
        except ImportError:
            logger.warning(
                "python-dotenv is not installed; skipping environment file %s.",
                dotenv_path,
            )
        else:
            load_dotenv(dotenv_path=dotenv_path)
            logger.info("Loaded environment variables from %s.", dotenv_path)
    else:
        logger.warning(
            "Optional environment file %s was not found; continuing with process "
            "environment variables and defaults.",
            dotenv_path,
        )

    for key in config:
        env_value = os.getenv(key)
        if env_value is not None:
            config[key] = env_value
    with _USER_SETTINGS_LOCK:
        _config_cache = config
        # 启动配置必须在服务启动时快速失败，禁止延迟到首次请求。
        _validate_startup_config()
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


def _to_bool(value: Any, key: str) -> bool:
    """严格解析启动布尔值，拒绝未知拼写。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("true", "1", "t", "y", "yes", "on"):
            return True
        if normalized in ("false", "0", "f", "n", "no", "off"):
            return False
    raise ValueError(f"{key} must be a boolean value")


def _parse_csv(value: Any) -> list:
    """解析逗号分隔配置，过滤空项并保持有序去重。"""
    if value is None:
        return []
    items = []
    for raw_item in str(value).split(","):
        item = raw_item.strip()
        if item and item not in items:
            items.append(item)
    return items


def _to_positive_int(value: Any, key: str) -> int:
    """严格解析正整数启动配置。"""
    if isinstance(value, bool):
        raise ValueError(f"{key} must be a positive integer")
    raw_value = str(value).strip()
    if not raw_value.isascii() or not raw_value.isdigit():
        raise ValueError(f"{key} must be a positive integer")
    parsed = int(raw_value)
    if parsed <= 0:
        raise ValueError(f"{key} must be a positive integer")
    return parsed


def _load_user_settings() -> Dict[str, Dict[str, Any]]:
    raw_users = UserSettingsStore(get_database_path()).load_all()
    return {
        str(username): _sanitize_user_settings(settings)
        for username, settings in raw_users.items()
    }


def _normalize_base_url(url: Any) -> str:
    """标准化 HTTPS base URL，避免不同写法绕过白名单。"""
    raw_value = str(url or "")
    if any(ord(character) < 32 or ord(character) == 127 for character in raw_value):
        return ""
    raw_url = raw_value.strip().rstrip("/")
    try:
        parsed = urlsplit(raw_url)
        hostname = parsed.hostname
        parsed_port = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme.lower() != "https"
        or not hostname
        or parsed.username is not None
        or parsed.query
        or parsed.fragment
    ):
        return ""
    host = hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    if parsed_port is not None:
        host = f"{host}:{parsed_port}"
    return urlunsplit((
        parsed.scheme.lower(),
        host,
        parsed.path.rstrip("/"),
        "",
        "",
    ))


# --- Public Getter Functions ---


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
    if key == "CODEBUDDY_AUTO_CHECKIN_ENABLED":
        return get_auto_checkin_enabled(owner)
    if key == "CODEBUDDY_ROTATION_COUNT":
        return get_rotation_count(owner)
    return str(_get_user_config_value(key, user, username) or "")


def get_server_host() -> str:
    return str(_get_config_value("CODEBUDDY_HOST"))

def get_server_port() -> int:
    port = _to_positive_int(_get_config_value("CODEBUDDY_PORT"), "CODEBUDDY_PORT")
    if port > 65535:
        raise ValueError("CODEBUDDY_PORT must be between 1 and 65535")
    return port

def get_users_file_path() -> str:
    return str(_get_config_value("CODEBUDDY_USERS_FILE"))

def get_codebuddy_api_endpoint() -> str:
    endpoint = _normalize_base_url(_get_config_value("CODEBUDDY_API_ENDPOINT"))
    allowed_endpoints = get_allowed_api_endpoints()

    if not allowed_endpoints:
        raise ValueError("CODEBUDDY_ALLOWED_API_ENDPOINTS must contain at least one valid HTTPS URL")
    if not endpoint:
        raise ValueError("CODEBUDDY_API_ENDPOINT must be a valid HTTPS base URL")
    if endpoint not in allowed_endpoints:
        raise ValueError("CODEBUDDY_API_ENDPOINT is not present in CODEBUDDY_ALLOWED_API_ENDPOINTS")
    return endpoint


def get_codebuddy_api_host() -> str:
    """返回当前 CodeBuddy 上游域名，用于 Host / X-Domain 请求头。"""
    return urlsplit(get_codebuddy_api_endpoint()).netloc


def get_allowed_api_endpoints() -> list:
    allowed = []
    for item in _parse_csv(_get_config_value("CODEBUDDY_ALLOWED_API_ENDPOINTS")):
        normalized = _normalize_base_url(item)
        if not normalized:
            raise ValueError(
                "CODEBUDDY_ALLOWED_API_ENDPOINTS must contain only valid HTTPS base URLs"
            )
        if normalized not in allowed:
            allowed.append(normalized)
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


def get_csp_frame_ancestors() -> str:
    """校验并规范化 CSP frame-ancestors 来源列表。"""
    key = "CODEBUDDY_CSP_FRAME_ANCESTORS"
    raw_value = str(_get_config_value(key))
    if any(ord(character) < 32 or ord(character) == 127 for character in raw_value):
        raise ValueError(f"{key} must contain only safe ancestor sources")

    tokens = raw_value.split()
    if len(tokens) == 1 and tokens[0].lower() in {"none", "'none'"}:
        return "'none'"
    if not tokens or any(token.lower() in {"none", "'none'"} for token in tokens):
        raise ValueError(f"{key} must be none, self, or HTTP/HTTPS origins")

    normalized_sources = []
    for token in tokens:
        if token.lower() in {"self", "'self'"}:
            normalized_sources.append("'self'")
            continue
        try:
            parsed = urlsplit(token)
            hostname = parsed.hostname
            port = parsed.port
        except ValueError as error:
            raise ValueError(f"{key} contains an invalid origin") from error
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not hostname
            or "*" in hostname
            or parsed.username is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(f"{key} contains an invalid origin")
        if ":" in hostname or parsed.netloc.startswith("["):
            try:
                if "%" in hostname:
                    raise ValueError("scoped IPv6 addresses are not valid CSP host sources")
                host = f"[{ipaddress.IPv6Address(hostname).compressed}]"
            except ValueError as error:
                raise ValueError(f"{key} contains an invalid origin") from error
        else:
            try:
                host = hostname.encode("idna").decode("ascii").lower()
            except UnicodeError as error:
                raise ValueError(f"{key} contains an invalid origin") from error
            if (
                len(host) > 253
                or any(not _DNS_LABEL.fullmatch(label) for label in host.split("."))
            ):
                raise ValueError(f"{key} contains an invalid origin")
        if port is not None:
            host = f"{host}:{port}"
        normalized_sources.append(f"{parsed.scheme.lower()}://{host}")

    return " ".join(dict.fromkeys(normalized_sources))


def get_max_request_body_bytes() -> int:
    return _to_positive_int(
        _get_config_value("CODEBUDDY_MAX_REQUEST_BODY_BYTES"),
        "CODEBUDDY_MAX_REQUEST_BODY_BYTES",
    )


def get_login_rate_window_seconds() -> int:
    return _to_positive_int(
        _get_config_value("CODEBUDDY_LOGIN_RATE_WINDOW_SECONDS"),
        "CODEBUDDY_LOGIN_RATE_WINDOW_SECONDS",
    )


def get_login_global_max_attempts() -> int:
    return _to_positive_int(
        _get_config_value("CODEBUDDY_LOGIN_GLOBAL_MAX_ATTEMPTS"),
        "CODEBUDDY_LOGIN_GLOBAL_MAX_ATTEMPTS",
    )


def get_login_ip_max_attempts() -> int:
    return _to_positive_int(
        _get_config_value("CODEBUDDY_LOGIN_IP_MAX_ATTEMPTS"),
        "CODEBUDDY_LOGIN_IP_MAX_ATTEMPTS",
    )


def get_login_username_max_attempts() -> int:
    return _to_positive_int(
        _get_config_value("CODEBUDDY_LOGIN_USERNAME_MAX_ATTEMPTS"),
        "CODEBUDDY_LOGIN_USERNAME_MAX_ATTEMPTS",
    )


def get_login_max_concurrency() -> int:
    return _to_positive_int(
        _get_config_value("CODEBUDDY_LOGIN_MAX_CONCURRENCY"),
        "CODEBUDDY_LOGIN_MAX_CONCURRENCY",
    )


def get_max_concurrent_requests() -> Optional[int]:
    value = _get_config_value("CODEBUDDY_MAX_CONCURRENT_REQUESTS")
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return _to_positive_int(value, "CODEBUDDY_MAX_CONCURRENT_REQUESTS")


def get_ssl_verify() -> bool:
    return _to_bool(_get_config_value("CODEBUDDY_SSL_VERIFY"), "CODEBUDDY_SSL_VERIFY")

def get_log_level() -> str:
    log_level = str(_get_config_value("CODEBUDDY_LOG_LEVEL")).strip().upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError(
            "CODEBUDDY_LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL"
        )
    return log_level

def get_available_models(user: Any = None) -> list:
    return _parse_csv(_get_user_config_value("CODEBUDDY_MODELS", user))


def get_forced_reasoning_models(user: Any = None) -> list:
    return _parse_csv(_get_user_config_value("CODEBUDDY_FORCED_REASONING_MODELS", user))


def get_forced_temperature(user: Any = None) -> Optional[float]:
    value = _get_user_config_value("CODEBUDDY_FORCED_TEMPERATURE", user)
    if value is None:
        return None
    raw_value = str(value).strip()
    if not raw_value:
        return None
    return _coerce_user_setting("CODEBUDDY_FORCED_TEMPERATURE", value)


def get_strip_model_namespace(user: Any = None) -> bool:
    value = _get_user_config_value("CODEBUDDY_STRIP_MODEL_NAMESPACE", user)
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return _to_bool(value, "CODEBUDDY_STRIP_MODEL_NAMESPACE")


def get_auto_rotation_enabled(user: Any = None) -> bool:
    return _to_bool(
        _get_user_config_value("CODEBUDDY_AUTO_ROTATION_ENABLED", user),
        "CODEBUDDY_AUTO_ROTATION_ENABLED",
    )


def get_auto_checkin_enabled(user: Any = None) -> bool:
    return _to_bool(
        _get_user_config_value("CODEBUDDY_AUTO_CHECKIN_ENABLED", user),
        "CODEBUDDY_AUTO_CHECKIN_ENABLED",
    )


def get_rotation_count(user: Any = None) -> int:
    return _coerce_user_setting(
        "CODEBUDDY_ROTATION_COUNT",
        _get_user_config_value("CODEBUDDY_ROTATION_COUNT", user),
    )


def _validate_startup_config() -> None:
    """一次验证全部安全边界和强类型启动配置。"""
    get_codebuddy_api_endpoint()
    get_server_port()
    get_ssl_verify()
    get_log_level()
    get_forced_temperature()
    get_strip_model_namespace()
    get_auto_rotation_enabled()
    get_auto_checkin_enabled()
    get_rotation_count()
    get_max_request_body_bytes()
    get_login_rate_window_seconds()
    get_login_global_max_attempts()
    get_login_ip_max_attempts()
    get_login_username_max_attempts()
    get_login_max_concurrency()
    get_max_concurrent_requests()
    get_csp_frame_ancestors()

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
