"""管理页前端静态资源路由。"""
import asyncio
import re
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from starlette.datastructures import Headers

router = APIRouter()

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"
PUBLIC_DIR = FRONTEND_DIR / "public"
INDEX_FILE = "index.html"

NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}
IMMUTABLE_ASSET_HEADERS = {
    "Cache-Control": "public, max-age=31536000, immutable",
}
REVALIDATE_ASSET_HEADERS = {
    "Cache-Control": "public, max-age=0, must-revalidate",
}
_HASHED_VITE_ASSET = re.compile(
    r"^assets/.+-(?:[A-Za-z0-9_-]{8}|[a-f0-9]{12})\."
    r"(?:css|js|mjs|svg|woff|woff2|ttf|otf|json|md|ya?ml|example|txt|png|jpe?g|webp|gif)$"
)
_ASCTIME_DATE = re.compile(
    r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) "
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
    r"(?:[0-9]{2}| [0-9]) [0-9]{2}:[0-9]{2}:[0-9]{2} [0-9]{4}"
)
_NOT_MODIFIED_HEADERS = frozenset(
    {"cache-control", "content-location", "date", "etag", "expires", "last-modified", "vary"}
)


def _safe_static_file(relative_path: str) -> Path:
    for asset_dir in (DIST_DIR, PUBLIC_DIR):
        asset_root = asset_dir.resolve()
        candidate = (asset_dir / relative_path).resolve()
        try:
            candidate.relative_to(asset_root)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate

    raise HTTPException(status_code=404, detail="Frontend asset not found")


async def get_frontend_index_response() -> FileResponse:
    """返回已构建的 Vue 管理台入口。"""
    index_path = DIST_DIR / INDEX_FILE
    if not index_path.is_file():
        raise HTTPException(status_code=503, detail="Built frontend not found.")

    return FileResponse(
        index_path,
        media_type="text/html",
        headers=NO_CACHE_HEADERS,
    )

def _is_not_modified(response_headers: Headers, request_headers: Headers) -> bool:
    if_none_match = request_headers.get("if-none-match")
    if if_none_match is not None:
        etag = response_headers["etag"]
        return any(
            candidate == "*" or candidate.removeprefix("W/") == etag
            for candidate in (value.strip() for value in if_none_match.split(","))
        )

    if_modified_since = request_headers.get("if-modified-since")
    if if_modified_since is None:
        return False
    try:
        modified_since = parsedate_to_datetime(if_modified_since)
        if modified_since.tzinfo is None:
            if _ASCTIME_DATE.fullmatch(if_modified_since) is None:
                return False
            modified_since = modified_since.replace(tzinfo=timezone.utc)
        else:
            modified_since = modified_since.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return False
    last_modified = parsedate_to_datetime(response_headers["last-modified"]).astimezone(
        timezone.utc
    )
    return modified_since >= last_modified


def _not_modified_response(headers: Headers) -> Response:
    return Response(
        status_code=304,
        headers={
            name: value for name, value in headers.items() if name in _NOT_MODIFIED_HEADERS
        },
    )


async def get_frontend_static_response(
    asset_path: str,
    request_headers: Headers | None = None,
) -> Response:
    """优先返回 Vite 构建产物，未构建时回退到公共静态资源。"""
    file_path = _safe_static_file(asset_path)
    try:
        file_path.relative_to(DIST_DIR.resolve())
    except ValueError:
        headers = REVALIDATE_ASSET_HEADERS
    else:
        headers = (
            IMMUTABLE_ASSET_HEADERS
            if _HASHED_VITE_ASSET.fullmatch(asset_path)
            else REVALIDATE_ASSET_HEADERS
        )
    stat_result = await asyncio.to_thread(file_path.stat)
    response = FileResponse(file_path, headers=headers, stat_result=stat_result)
    if request_headers is not None and _is_not_modified(response.headers, request_headers):
        return _not_modified_response(response.headers)
    return response


@router.get("/", response_class=FileResponse, include_in_schema=False)
async def serve_frontend():
    return await get_frontend_index_response()


@router.get("/admin", response_class=FileResponse, include_in_schema=False)
async def serve_admin():
    return await get_frontend_index_response()


@router.get("/assets/{asset_path:path}", response_class=FileResponse, include_in_schema=False)
async def serve_frontend_asset(asset_path: str, request: Request):
    return await get_frontend_static_response(f"assets/{asset_path}", request.headers)
