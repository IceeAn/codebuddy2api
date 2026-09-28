"""限制日志中的异常内容、认证信息及控制字符。"""
import logging
import re


def safe_error_text(value: str) -> str:
    """对允许显示的短错误文本移除认证信息和控制字符。"""
    value = re.sub(r"(?i)\bBearer\s+[^\s\"',;]+", "Bearer [已隐藏]", value)
    value = re.sub(r"(?i)(\b(?:state|access_token|refresh_token|authorization|api_key)\s*[=:]\s*)[^\s&\"',;]+", r"\1[已隐藏]", value)
    value = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[已隐藏]", value)
    return "".join(char if ord(char) >= 32 and ord(char) != 127 else " " for char in value)[:512]


class SafeLogFilter(logging.Filter):
    """异常只保留类型；禁止异常对象和 traceback 泄露请求数据。"""

    def filter(self, record):
        if isinstance(record.args, tuple):
            record.args = tuple(type(arg).__name__ if isinstance(arg, BaseException) else arg for arg in record.args)
        else:
            record.args = {key: type(arg).__name__ if isinstance(arg, BaseException) else arg for key, arg in record.args.items()}
        message = record.getMessage()
        if record.exc_info:
            message += f" ({record.exc_info[0].__name__})"
        record.msg = safe_error_text(message)
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def configure_safe_logging():
    """即使应用开启 DEBUG，也不启用第三方传输细节日志。"""
    for name in ("httpx", "httpcore", "httpcore.connection", "httpcore.http11", "httpcore.http2", "httpcore.proxy", "httpcore.socks"):
        logging.getLogger(name).disabled = True
    for logger in (logging.getLogger(), logging.getLogger("uvicorn"), logging.getLogger("uvicorn.error")):
        for handler in logger.handlers:
            handler.addFilter(SafeLogFilter())
