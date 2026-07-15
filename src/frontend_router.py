"""管理页前端静态资源路由。"""
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

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
    r"^assets/.+-[A-Za-z0-9_-]{8,}\.(?:css|js|mjs|woff|woff2|ttf|otf)$"
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

async def get_frontend_static_response(asset_path: str) -> FileResponse:
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
    return FileResponse(file_path, headers=headers)


@router.get("/", response_class=FileResponse, include_in_schema=False)
async def serve_frontend():
    return await get_frontend_index_response()


@router.get("/admin", response_class=FileResponse, include_in_schema=False)
async def serve_admin():
    return await get_frontend_index_response()


@router.get("/assets/{asset_path:path}", response_class=FileResponse, include_in_schema=False)
async def serve_frontend_asset(asset_path: str):
    return await get_frontend_static_response(f"assets/{asset_path}")
