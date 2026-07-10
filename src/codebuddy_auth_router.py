"""CodeBuddy 认证路由。"""
import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse

from .auth_router import require_session_user
from .auth_types import AuthenticatedUser
from .codebuddy_oauth import (
    AUTH_START_FAILED_MESSAGE,
    consume_auth_state,
    poll_codebuddy_auth_status,
    remember_auth_state,
    save_codebuddy_token,
    start_codebuddy_auth,
    validate_auth_state_owner,
)
from .private_response import PrivateNoStoreRoute

logger = logging.getLogger(__name__)
router = APIRouter(route_class=PrivateNoStoreRoute)


@router.get("/auth/start", summary="Start CodeBuddy Authentication")
async def start_device_auth(_user: AuthenticatedUser = Depends(require_session_user)):
    """启动 CodeBuddy 认证流程。"""
    try:
        logger.info("开始启动CodeBuddy认证流程...")
        real_auth_result = await start_codebuddy_auth()

        if real_auth_result.get("success"):
            auth_state = real_auth_result.get("auth_state")
            if not auth_state:
                logger.error("CodeBuddy认证API成功响应缺少auth_state")
                return {
                    "success": False,
                    "error": "invalid_auth_state",
                    "message": "认证服务返回了无效的 auth_state",
                }
            if not remember_auth_state(auth_state, _user):
                logger.error("CodeBuddy认证API返回了仍被占用的auth_state")
                return {
                    "success": False,
                    "error": "duplicate_auth_state",
                    "message": "认证服务返回了重复的 auth_state，请稍后重试",
                }
            logger.info("CodeBuddy认证API启动成功!")
            return real_auth_result

        logger.warning(f"真实认证API失败: {real_auth_result}")
        return real_auth_result

    except Exception:
        logger.exception("认证启动过程发生异常")
        return {
            "success": False,
            "error": "Unexpected error",
            "message": AUTH_START_FAILED_MESSAGE,
        }


@router.post("/auth/poll", summary="Poll for OAuth token")
async def poll_for_token(
        device_code: Optional[str] = Body(None, embed=True),
        code_verifier: Optional[str] = Body(None, embed=True),
        auth_state: Optional[str] = Body(None, embed=True),
        _user: AuthenticatedUser = Depends(require_session_user),
):
    """轮询 CodeBuddy token 端点。"""
    if device_code or code_verifier:
        logger.debug("忽略旧版 OAuth poll 字段 device_code/code_verifier")

    if not auth_state:
        return JSONResponse(
            content={
                "error": "missing_parameters",
                "error_description": "缺少必要的参数：auth_state",
            },
            status_code=400,
        )

    if not validate_auth_state_owner(auth_state, _user):
        raise HTTPException(status_code=403, detail="Invalid or expired auth_state")

    logger.info(f"轮询CodeBuddy认证状态: {auth_state}")
    poll_result = await poll_codebuddy_auth_status(auth_state)

    if poll_result.get("status") == "success":
        token_data = poll_result.get("token_data", {})
        if token_data:
            bearer_token = token_data.get("access_token") or token_data.get("bearer_token")
            if bearer_token:
                if not consume_auth_state(auth_state, _user):
                    raise HTTPException(status_code=409, detail="Invalid or already consumed auth_state")
                token_saved = await save_codebuddy_token(token_data, _user)
                if not token_saved:
                    raise HTTPException(status_code=500, detail="凭证保存失败，请重新认证")
                return JSONResponse(
                    content={"saved": True, "message": "认证成功"},
                    status_code=200,
                )

            return JSONResponse(
                content={
                    "error": "invalid_token_response",
                    "error_description": "API返回的响应中没有找到token",
                },
                status_code=400,
            )

    if poll_result.get("status") == "pending":
        return JSONResponse(
            content={
                "error": "authorization_pending",
                "error_description": poll_result.get("message", "等待用户登录..."),
                "code": poll_result.get("code"),
            },
            status_code=400,
        )

    return JSONResponse(
        content={
            "error": "auth_error",
            "error_description": poll_result.get("message", "认证过程发生错误"),
            "details": poll_result,
        },
        status_code=400,
    )


@router.post("/auth/cancel", summary="Cancel CodeBuddy Authentication")
async def cancel_auth(
        auth_state: Optional[str] = Body(None, embed=True),
        _user: AuthenticatedUser = Depends(require_session_user),
):
    """取消当前用户尚未完成的认证，并立即使 state 失效。"""
    if not auth_state:
        raise HTTPException(status_code=400, detail="Missing auth_state")
    if not consume_auth_state(auth_state, _user):
        raise HTTPException(status_code=403, detail="Invalid or expired auth_state")
    return {"cancelled": True}


@router.get(
    "/auth/callback",
    summary="OAuth2 callback endpoint",
)
async def oauth_callback(code: str = None, state: str = None, error: str = None):
    """OAuth2 回调端点。"""
    if error:
        return JSONResponse(
            content={"error": error, "error_description": "授权被拒绝或出现错误"},
            status_code=400,
        )

    return JSONResponse(
        content={
            "message": "授权成功！请返回应用程序。",
            "code": code,
            "state": state,
        }
    )
