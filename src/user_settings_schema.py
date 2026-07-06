"""用户级设置字段与类型规范。"""
from typing import Any, Dict

USER_SETTING_KEYS = {
    "CODEBUDDY_MODELS",
    "CODEBUDDY_FORCED_REASONING_MODELS",
    "CODEBUDDY_FORCED_TEMPERATURE",
    "CODEBUDDY_STRIP_MODEL_NAMESPACE",
    "CODEBUDDY_AUTO_ROTATION_ENABLED",
    "CODEBUDDY_ROTATION_COUNT",
}

BOOL_USER_SETTING_KEYS = {
    "CODEBUDDY_STRIP_MODEL_NAMESPACE",
    "CODEBUDDY_AUTO_ROTATION_ENABLED",
}


def _parse_bool_setting(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("true", "1", "t", "y", "yes", "on"):
            return True
        if normalized in ("false", "0", "f", "n", "no", "off"):
            return False
    raise ValueError(f"Invalid boolean setting value: {value!r}")


def coerce_user_setting(key: str, value: Any) -> Any:
    if key not in USER_SETTING_KEYS:
        raise ValueError(f"Unsupported user setting: {key}")

    if key in BOOL_USER_SETTING_KEYS:
        return _parse_bool_setting(value)

    if key == "CODEBUDDY_ROTATION_COUNT":
        if isinstance(value, bool):
            raise ValueError("CODEBUDDY_ROTATION_COUNT must be a positive integer")
        try:
            rotation_count = int(value)
        except (TypeError, ValueError) as e:
            raise ValueError("CODEBUDDY_ROTATION_COUNT must be a positive integer") from e
        if not isinstance(value, int) and str(value).strip() != str(rotation_count):
            raise ValueError("CODEBUDDY_ROTATION_COUNT must be a positive integer")
        if rotation_count < 1:
            raise ValueError("CODEBUDDY_ROTATION_COUNT must be a positive integer")
        return rotation_count

    if key == "CODEBUDDY_FORCED_TEMPERATURE":
        if value is None:
            return ""
        raw_value = str(value).strip()
        if not raw_value:
            return ""
        try:
            temperature = float(raw_value)
        except ValueError as e:
            raise ValueError("CODEBUDDY_FORCED_TEMPERATURE must be a number or empty") from e
        if not 0 <= temperature <= 2:
            raise ValueError("CODEBUDDY_FORCED_TEMPERATURE must be between 0 and 2")
        if temperature.is_integer():
            return int(temperature)
        return temperature

    return str(value or "")


def sanitize_user_settings(raw_settings: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: coerce_user_setting(key, value)
        for key, value in raw_settings.items()
        if key in USER_SETTING_KEYS
    }
