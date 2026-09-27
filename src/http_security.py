"""HTTP 来源校验及完整响应生命周期的并发准入。"""
import ipaddress
import re
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware

from .request_limits import RequestBodyLimitMiddleware


def normalize_origin(value: str, *, referer: bool = False) -> str | None:
    """规范化完整来源，拒绝用户信息、控制字符和歧义主机。"""
    if not value or any(ord(char) <= 32 or ord(char) == 127 or char == "\\" for char in value):
        return None
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
        if (parsed.scheme not in {"http", "https"} or not host or "%" in host or parsed.username is not None
                or (not referer and (parsed.path not in {"", "/"} or parsed.query or parsed.fragment))):
            return None
        if ":" in host:
            host = f"[{ipaddress.IPv6Address(host).compressed}]"
        else:
            host = host.encode("idna").decode("ascii").lower()
            if len(host) > 253 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")):
                return None
    except (ValueError, UnicodeError):
        return None
    default_port = 443 if parsed.scheme == "https" else 80
    suffix = f":{port}" if port is not None and port != default_port else ""
    return f"{parsed.scheme}://{host}{suffix}"


class SessionOriginMiddleware:
    """在读取请求体和执行认证前拒绝管理台跨来源写入。"""

    def __init__(self, app, *, public_origin: str = ""):
        self.app = app
        self.public_origin = public_origin

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        protected = any(path == prefix or path.startswith(prefix + "/") for prefix in ("/auth", "/codebuddy/auth", "/api/admin"))
        if scope["type"] == "http" and protected and scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            headers = Headers(scope=scope)
            expected = self.public_origin or normalize_origin(f"{scope['scheme']}://{headers.get('host', '')}")
            origin = headers.get("origin")
            actual = normalize_origin(origin) if origin is not None else normalize_origin(headers.get("referer", ""), referer=True)
            if not expected or actual != expected or headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
                await RequestBodyLimitMiddleware._send_error(scope, receive, send, 403, "请求来源校验失败")
                return
            if path.startswith("/api/admin/playground/") and headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                await RequestBodyLimitMiddleware._send_error(scope, receive, send, 415, "管理台测试请求必须使用 application/json")
                return
        await self.app(scope, receive, send)


class ExternalCORSMiddleware(CORSMiddleware):
    """跨域许可仅适用于 API Key 协议入口。"""

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if path.startswith(("/openai/", "/anthropic/")):
            await super().__call__(scope, receive, send)
        else:
            await self.app(scope, receive, send)
