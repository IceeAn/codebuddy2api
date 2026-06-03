"""
Main Web Service for CodeBuddy2API
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

# Import the routers
from src.auth import router as service_auth_router
from src.codebuddy_router import router as codebuddy_router, lifecycle_manager
from src.codebuddy_auth_router import router as codebuddy_auth_router
from src.settings_router import router as settings_router
from src.frontend_router import router as frontend_router

from config import get_server_host, get_server_port, get_log_level, get_allowed_origins, get_allowed_hosts

# 配置日志
logging.basicConfig(
    level=getattr(logging, get_log_level().upper()),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("Starting CodeBuddy2API Service")
    try:
        # 启动时初始化资源
        await lifecycle_manager.startup()
        yield
    finally:
        # 关闭时清理资源
        await lifecycle_manager.shutdown()
        logger.info("CodeBuddy2API Service stopped")


# 创建FastAPI应用
app = FastAPI(
    title="CodeBuddy2API",
    description="CodeBuddy API proxy with OpenAI-compatible interface",
    version="1.0.0",
    lifespan=lifespan
)

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

# 挂载CodeBuddy API路由
app.include_router(
    codebuddy_router,
    prefix="/codebuddy",
    tags=["CodeBuddy Compatible API"]
)

# 挂载设置路由
app.include_router(
    settings_router,
    prefix="/api",
    tags=["Settings Management"]
)

# 健康检查端点
@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "healthy", "service": "codebuddy2api"}


@app.get("/")
async def root():
    """根路径信息"""
    return {
        "service": "CodeBuddy2API",
        "version": "1.0.0",
        "description": "CodeBuddy API proxy with OpenAI-compatible interface",
        "endpoints": {
            "models": "/codebuddy/v1/models",
            "chat": "/codebuddy/v1/chat/completions",
            "credentials": "/codebuddy/v1/credentials",
            "auth_start": "/codebuddy/auth/start",
            "auth_poll": "/codebuddy/auth/poll",
            "auth_callback": "/codebuddy/auth/callback",
            "get_settings": "/api/settings",
            "save_settings": "/api/settings"
        }
    }


if __name__ == "__main__":
    from hypercorn.asyncio import serve
    from hypercorn.config import Config
    
    port = get_server_port()
    host = get_server_host()
    
    logger.info("=" * 60)
    logger.info("Starting CodeBuddy2API")
    logger.info("=" * 60)
    logger.info(f"Main Service: http://{host}:{port}")
    logger.info("=" * 60)
    logger.info("Web Interface:")
    logger.info(f"   Admin Panel: http://{host}:{port}/")
    logger.info("=" * 60)
    logger.info("API Endpoints:")
    logger.info(f"   Models: GET http://{host}:{port}/codebuddy/v1/models")
    logger.info(f"   Chat: POST http://{host}:{port}/codebuddy/v1/chat/completions")
    logger.info(f"   Credentials: GET http://{host}:{port}/codebuddy/v1/credentials")
    logger.info("=" * 60)
    logger.info("Authentication:")
    logger.info("   Mount secrets/users.txt for multi-user authentication")
    logger.info("   Web UI uses HttpOnly session cookies")
    logger.info("   API clients must use Bearer sk-... keys generated in the Web UI")
    logger.info("=" * 60)

    config = Config()
    config.bind = [f"{host}:{port}"]
    config.accesslog = None
    config.errorlog = "-"
    config.loglevel = "INFO"
    config.use_colors = True

    asyncio.run(serve(app, config))
