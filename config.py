"""
Configuration management for CodeBuddy2API

Implements a multi-layered configuration system with hot-reloading.
Priority order:
1. In-memory config (for hot-settings from the UI)
2. config.json file (only for explicitly hot-reloadable safe settings)
3. Environment variables (for deployment, e.g., Docker and security boundaries)
4. Hard-coded defaults
"""
import os
import json
import logging
from typing import Dict, Any, Optional
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)

DEFAULT_FORCED_REASONING_MODELS = (
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "glm-5.1",
    "glm-5.2",
)

DEFAULT_CODEBUDDY_MODELS = (
    "glm-5.2",
    "glm-5.1",
    "glm-5.0",
    "glm-5.0-turbo",
    "glm-5v-turbo",
    "glm-4.7",
    "minimax-m2.7",
    "minimax-m2.5",
    "kimi-k2.6",
    "kimi-k2.5",
    "hy3-preview",
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "deepseek-v3-2-volc",
    "lite",
)

# --- Private State ---
_config_cache: Dict[str, Any] = {}
_CONFIG_JSON_PATH = 'config/config.json'  # Use a path inside a directory

_DEFAULT_CONFIG = {
    "CODEBUDDY_HOST": "127.0.0.1",
    "CODEBUDDY_PORT": 8001,
    "CODEBUDDY_USERS_FILE": "secrets/users.txt",
    "CODEBUDDY_API_ENDPOINT": "https://copilot.tencent.com",
    "CODEBUDDY_ALLOWED_API_ENDPOINTS": "https://copilot.tencent.com,https://www.codebuddy.ai",
    "CODEBUDDY_CREDS_DIR": ".codebuddy_creds",
    "CODEBUDDY_ALLOWED_HOSTS": "localhost,127.0.0.1",
    "CODEBUDDY_ALLOWED_ORIGINS": "",
    "CODEBUDDY_SSL_VERIFY": True,
    "CODEBUDDY_LOG_LEVEL": "INFO",
    "CODEBUDDY_MODELS": ",".join(DEFAULT_CODEBUDDY_MODELS),
    "CODEBUDDY_FORCED_REASONING_MODELS": ",".join(DEFAULT_FORCED_REASONING_MODELS),
    "CODEBUDDY_FORCED_TEMPERATURE": "1",
    "CODEBUDDY_STRIP_MODEL_NAMESPACE": True,
    "CODEBUDDY_ROTATION_COUNT": 1
}

_HOT_RELOADABLE_CONFIG_KEYS = {
    "CODEBUDDY_LOG_LEVEL",
    "CODEBUDDY_MODELS",
    "CODEBUDDY_FORCED_REASONING_MODELS",
    "CODEBUDDY_FORCED_TEMPERATURE",
    "CODEBUDDY_STRIP_MODEL_NAMESPACE",
    "CODEBUDDY_ROTATION_COUNT",
}

# --- Core Functions ---

def load_config():
    """
    Loads configuration from all sources into the in-memory cache.
    This should be called once at application startup.
    """
    global _config_cache
    
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
            
    if os.path.exists(_CONFIG_JSON_PATH):
        try:
            with open(_CONFIG_JSON_PATH, 'r', encoding='utf-8') as f:
                content = f.read()
                if content:
                    persisted_config = {
                        key: value
                        for key, value in json.loads(content).items()
                        if key in _HOT_RELOADABLE_CONFIG_KEYS
                    }
                    config.update(persisted_config)
                    logger.info(f"Loaded and merged persisted settings from {_CONFIG_JSON_PATH}.")
        except Exception as e:
            logger.error(f"Error loading {_CONFIG_JSON_PATH}: {e}")

    _config_cache = config
    logger.info("Configuration loaded successfully.")


def _get_config_value(key: str) -> Any:
    return _config_cache.get(key, _DEFAULT_CONFIG.get(key))

def _update_config_value(key: str, value: Any):
    global _config_cache
    _config_cache[key] = value
    # Downgrade to debug to avoid verbose logging in production
    logger.debug(f"Hot-reloaded setting '{key}' to new value.")


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


def save_config_to_json():
    """
    Saves the entire current in-memory configuration to config.json.
    This is simpler and more robust, ensuring a complete snapshot is always saved.
    This will create the file if it doesn't exist.
    """
    try:
        # Ensure the directory exists before writing the file
        config_dir = os.path.dirname(_CONFIG_JSON_PATH)
        if not os.path.exists(config_dir):
            os.makedirs(config_dir)
            logger.info(f"Created config directory at {config_dir}")

        with open(_CONFIG_JSON_PATH, 'w', encoding='utf-8') as f:
            # 仅持久化允许热更新的非敏感配置，避免把密码和安全边界写入配置文件。
            config_to_save = {key: _config_cache.get(key) for key in _HOT_RELOADABLE_CONFIG_KEYS}
            json.dump(config_to_save, f, indent=4)
        logger.info(f"Settings successfully persisted to {_CONFIG_JSON_PATH}.")
    except Exception as e:
        logger.error(f"Failed to save config to {_CONFIG_JSON_PATH}: {e}")
        raise

# --- Public Getter Functions ---

def get_active_config() -> Dict[str, Any]:
    return {key: _config_cache.get(key) for key in _DEFAULT_CONFIG}


def get_editable_config() -> Dict[str, Any]:
    """仅返回允许运行时热更新的非敏感配置。"""
    return {key: _config_cache.get(key) for key in _HOT_RELOADABLE_CONFIG_KEYS}


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
    return str(_get_config_value("CODEBUDDY_CREDS_DIR"))


def get_allowed_origins() -> list:
    return _parse_csv(_get_config_value("CODEBUDDY_ALLOWED_ORIGINS"))


def get_allowed_hosts() -> list:
    return _parse_csv(_get_config_value("CODEBUDDY_ALLOWED_HOSTS"))


def get_ssl_verify() -> bool:
    return _to_bool(_get_config_value("CODEBUDDY_SSL_VERIFY"))

def get_log_level() -> str:
    return str(_get_config_value("CODEBUDDY_LOG_LEVEL")).upper()

def get_available_models() -> list:
    models_str = str(_get_config_value("CODEBUDDY_MODELS"))
    return [model.strip() for model in models_str.split(",")]


def get_forced_reasoning_models() -> list:
    return _parse_csv(_get_config_value("CODEBUDDY_FORCED_REASONING_MODELS"))


def get_forced_temperature() -> Optional[float]:
    value = _get_config_value("CODEBUDDY_FORCED_TEMPERATURE")
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


def get_strip_model_namespace() -> bool:
    value = _get_config_value("CODEBUDDY_STRIP_MODEL_NAMESPACE")
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return _to_bool(value)


def get_rotation_count() -> int:
    return int(_get_config_value("CODEBUDDY_ROTATION_COUNT"))

# --- Public Setter for Hot-Reload ---

def update_settings(new_settings: Dict[str, Any]):
    """Updates the live config and persists it to config.json."""
    ignored_keys = []
    for key, value in new_settings.items():
        if key not in _HOT_RELOADABLE_CONFIG_KEYS:
            ignored_keys.append(key)
            continue

        if key in _config_cache:
            original_type = type(_DEFAULT_CONFIG.get(key, value))
            try:
                if original_type is bool:
                    typed_value = _to_bool(value)
                else:
                    typed_value = original_type(value)
                _update_config_value(key, typed_value)
            except (ValueError, TypeError):
                logger.warning(f"Could not cast new value for '{key}' to {original_type}. Using as string.")
                _update_config_value(key, value)

    if ignored_keys:
        logger.warning("Ignored non-editable settings update: %s", ", ".join(sorted(ignored_keys)))

    save_config_to_json()

# --- Initial Load ---
load_config()
