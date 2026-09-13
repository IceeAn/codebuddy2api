"""纯文本计数及用户分词资源管理路由。"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from .auth_router import require_api_key_user, require_session_user
from .auth_types import AuthenticatedUser
from .private_response import PrivateNoStoreRoute
from .tokenizer_engine import ALLOWED_FILES, ENCODERS, TokenizerError
from .tokenizer_runtime import tokenizer_runtime
from .tokenizer_service import count_tokens
from .tokenizer_store import get_tokenizer_store


class TextCountRequest(BaseModel):
    model: str
    text: str


class TokenCountResponse(BaseModel):
    input_tokens: int


def create_text_router(auth, *, include_in_schema):
    router = APIRouter(route_class=PrivateNoStoreRoute)

    @router.post(
        "/v1/count_tokens",
        response_model=TokenCountResponse,
        include_in_schema=include_in_schema,
    )
    async def count(body: TextCountRequest, user: AuthenticatedUser = Depends(auth)):
        try:
            data, headers = await count_tokens(body.model_dump(), user)
            return JSONResponse(data, headers=headers)
        except TokenizerError as error:
            raise HTTPException(error.status_code, str(error)) from error

    return router


external_tokenizer_router = create_text_router(
    require_api_key_user, include_in_schema=True
)
playground_tokenizer_router = create_text_router(
    require_session_user, include_in_schema=False
)
admin_tokenizer_router = APIRouter(
    route_class=PrivateNoStoreRoute, prefix="/tokenizers"
)


@admin_tokenizer_router.get("")
def resources(user: AuthenticatedUser = Depends(require_session_user)):
    from config import get_tokenizer_limits

    return {
        **get_tokenizer_store().listing(user.username),
        "encoders": list(ENCODERS),
        "limits": get_tokenizer_limits(),
    }


@admin_tokenizer_router.post("/resources")
async def upload(
    request: Request, user: AuthenticatedUser = Depends(require_session_user)
):
    try:
        async with request.form(
            max_files=8, max_fields=4, max_part_size=1024 * 1024
        ) as form:
            files = {}
            for key, part in form.multi_items():
                if isinstance(part, UploadFile):
                    if (
                        key != "files"
                        or part.filename not in ALLOWED_FILES
                        or part.filename in files
                    ):
                        raise TokenizerError("上传包含重复或不支持的文件")
                    files[part.filename] = await part.read()
            profile = {
                "encoder": form.get("encoder", "auto"),
                "format": form.get("format", "hf"),
                "template_name": form.get("template_name", ""),
            }
            validated = await run_in_threadpool(
                tokenizer_runtime.execute,
                "validate",
                {"files": files, "profile": profile},
            )
            result = await run_in_threadpool(
                get_tokenizer_store().create,
                user.username,
                form.get("name", ""),
                files,
                validated,
            )
        return JSONResponse(result, status_code=201)
    except TokenizerError as error:
        raise HTTPException(error.status_code, str(error)) from error


@admin_tokenizer_router.post("/builtin/{resource_id}/snapshot", status_code=201)
def snapshot(resource_id: str, user: AuthenticatedUser = Depends(require_session_user)):
    try:
        return get_tokenizer_store().snapshot_builtin(user.username, resource_id)
    except TokenizerError as error:
        raise HTTPException(error.status_code, str(error)) from error


@admin_tokenizer_router.put("/mappings")
async def save_mappings(
    request: Request, user: AuthenticatedUser = Depends(require_session_user)
):
    try:
        body = await request.json()
        if not isinstance(body, dict) or set(body) != {"mappings"}:
            raise TokenizerError("请求必须包含 mappings 对象")
        store = get_tokenizer_store()
        await run_in_threadpool(store.save_mappings, user.username, body["mappings"])
        return await run_in_threadpool(store.listing, user.username)
    except (ValueError, TokenizerError) as error:
        raise HTTPException(
            getattr(error, "status_code", 400),
            str(error) if isinstance(error, TokenizerError) else "请求 JSON 无效",
        ) from error


@admin_tokenizer_router.delete("/resources/{resource_id}")
def delete(resource_id: str, user: AuthenticatedUser = Depends(require_session_user)):
    try:
        get_tokenizer_store().delete(user.username, resource_id)
        return {"deleted": True}
    except TokenizerError as error:
        raise HTTPException(error.status_code, str(error)) from error
