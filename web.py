"""
Main Web Service for CodeBuddy2API
"""
import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

# Import the routers
from src.auth_router import router as service_auth_router
from src.auth_router import require_session_user
from src.admin_router import router as admin_router
from src.codebuddy_auth_router import router as codebuddy_auth_router
from src.frontend_router import router as frontend_router
from src.openai_router import external_openai_router, playground_openai_router
from src.private_response import PrivateNoStoreFastAPI, PrivateNoStoreRoute
from src.request_limits import RequestBodyLimitMiddleware
from src.stats_router import router as stats_router
from src.stream_service import UpstreamAPIError, lifecycle_manager
from src.usage_stats_store import usage_stats_retention_manager
from src.users_store import validate_configured_users_file
from src.uvicorn_limits import to_uvicorn_limit_concurrency

from config import (
    get_allowed_hosts,
    get_allowed_origins,
    get_log_level,
    get_max_concurrent_requests,
    get_max_request_body_bytes,
    get_server_host,
    get_server_port,
    initialize_database,
)

# 配置日志
logging.basicConfig(
    level=getattr(logging, get_log_level().upper()),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
APP_VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("Starting CodeBuddy2API Service")
    try:
        # 启动时初始化资源
        validate_configured_users_file()
        initialize_database()
        await usage_stats_retention_manager.startup()
        await lifecycle_manager.startup()
        yield
    finally:
        # 关闭时清理资源
        await usage_stats_retention_manager.shutdown()
        await lifecycle_manager.shutdown()
        logger.info("CodeBuddy2API Service stopped")


# 创建FastAPI应用
app = PrivateNoStoreFastAPI(
    title="CodeBuddy2API",
    description="CodeBuddy API proxy with OpenAI-compatible interface",
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(
    RequestBodyLimitMiddleware,
    max_body_bytes=get_max_request_body_bytes(),
    login_max_body_bytes=8 * 1024,
)
docs_router = APIRouter(route_class=PrivateNoStoreRoute)


@app.exception_handler(UpstreamAPIError)
async def upstream_api_error_handler(_request, error: UpstreamAPIError):
    """使用 OpenAI 错误信封返回可识别的上游失败。"""
    return JSONResponse(
        status_code=error.status_code,
        content={"error": error.error},
        headers=error.headers,
    )


@docs_router.get("/openapi.json", include_in_schema=False)
async def protected_openapi(_user=Depends(require_session_user)):
    """仅向已登录的管理台用户返回 OpenAPI schema。"""
    return JSONResponse(app.openapi())


@docs_router.get("/docs", include_in_schema=False)
async def protected_swagger_ui(_user=Depends(require_session_user)):
    """仅向已登录的管理台用户返回 Swagger UI。"""
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title=f"{app.title} - Swagger UI",
        swagger_ui_parameters={
            "deepLinking": False,
            "validatorUrl": None,
            "persistAuthorization": False,
            "filter": True,
            "displayRequestDuration": True,
        },
    )


@docs_router.get("/redoc", include_in_schema=False)
async def protected_redoc(_user=Depends(require_session_user)):
    """仅向已登录的管理台用户返回 ReDoc。"""
    return get_redoc_html(openapi_url="/openapi.json", title=f"{app.title} - ReDoc")


app.include_router(docs_router)

# Host 头校验，公网部署时请通过 CODEBUDDY_ALLOWED_HOSTS 配置域名
allowed_hosts = get_allowed_hosts()
if allowed_hosts:
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=allowed_hosts,
    )

# CORS中间件：默认不开跨域，只有显式配置 CODEBUDDY_ALLOWED_ORIGINS 时才启用
allowed_origins = get_allowed_origins()
if allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Conversation-ID", "X-Conversation-Request-ID", "X-Conversation-Message-ID", "X-Request-ID"],
    )

# 挂载前端路由
app.include_router(
    frontend_router,
    tags=["Frontend"]
)

# 挂载本系统管理页登录会话路由
app.include_router(
    service_auth_router,
    tags=["Service Authentication"]
)

# 挂载CodeBuddy认证路由
app.include_router(
    codebuddy_auth_router,
    prefix="/codebuddy",
    tags=["CodeBuddy OAuth2 Authentication"]
)

# 挂载仅供外部客户端使用的 OpenAI 兼容路由，仅接受 API Key
app.include_router(
    external_openai_router,
    prefix="/openai",
    tags=["OpenAI Compatible API"]
)

# 挂载管理台测试使用的 OpenAI 兼容路由，仅接受会话 Cookie
app.include_router(
    playground_openai_router,
    prefix="/api/admin/playground/openai",
    tags=["Admin Playground OpenAI Compatible API"]
)

# 挂载管理页专用 API 路由
app.include_router(
    admin_router,
    prefix="/api/admin",
    tags=["Admin Management"]
)

# 挂载管理台持久化请求统计 API
app.include_router(
    stats_router,
    prefix="/api/admin/stats",
    tags=["Admin Usage Statistics"],
)

# 健康检查端点
@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "healthy", "service": "codebuddy2api"}


def run_server():
    import uvicorn

    port = get_server_port()
    host = get_server_host()
    log_level = get_log_level().lower()
    max_concurrent_requests = get_max_concurrent_requests()
    uvicorn_limit_concurrency = to_uvicorn_limit_concurrency(
        max_concurrent_requests
    )

    logger.info("=" * 60)
    logger.info("Starting CodeBuddy2API")
    logger.info("=" * 60)
    logger.info(f"Main Service: http://{host}:{port}")
    logger.info("=" * 60)
    logger.info("Web Interface:")
    logger.info(f"   Admin Panel: http://{host}:{port}/")
    logger.info("=" * 60)
    logger.info("API Endpoints:")
    logger.info(f"   Models: GET http://{host}:{port}/openai/v1/models")
    logger.info(f"   Chat: POST http://{host}:{port}/openai/v1/chat/completions")
    logger.info(f"   Admin API: GET http://{host}:{port}/api/admin/status")
    logger.info("=" * 60)
    logger.info("Authentication:")
    logger.info("   Mount secrets/users.txt for multi-user authentication")
    logger.info("   Web UI uses HttpOnly session cookies")
    logger.info("   API clients must use Bearer sk-... keys generated in the Web UI")
    logger.info("=" * 60)

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=log_level,
        access_log=False,
        use_colors=None,
        server_header=False,
        limit_concurrency=uvicorn_limit_concurrency,
    )


if __name__ == "__main__":
    run_server()
