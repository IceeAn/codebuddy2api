"""管理页前端静态资源路由。"""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"
INDEX_FILE = "index.html"
LEGACY_ADMIN_FILE = FRONTEND_DIR / "admin.html"

NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


def _safe_dist_file(relative_path: str) -> Path:
    dist_root = DIST_DIR.resolve()
    candidate = (DIST_DIR / relative_path).resolve()
    try:
        candidate.relative_to(dist_root)
    except ValueError:
        raise HTTPException(status_code=404, detail="Frontend asset not found")

    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Frontend asset not found")
    return candidate


async def get_frontend_index_response() -> FileResponse:
    """返回 Vue 管理台入口；未构建时回退到旧版单文件管理台。"""
    index_path = DIST_DIR / INDEX_FILE
    if not index_path.is_file():
        return await get_legacy_admin_response()

    return FileResponse(
        index_path,
        media_type="text/html",
        headers=NO_CACHE_HEADERS,
    )


async def get_legacy_admin_response() -> FileResponse:
    """返回旧版单文件管理台入口。"""
    if not LEGACY_ADMIN_FILE.is_file():
        raise HTTPException(
            status_code=503,
            detail="Legacy admin frontend not found.",
        )

    return FileResponse(
        LEGACY_ADMIN_FILE,
        media_type="text/html",
        headers=NO_CACHE_HEADERS,
    )


async def get_frontend_static_response(asset_path: str) -> FileResponse:
    """返回 Vite 构建产物中的静态资源。"""
    file_path = _safe_dist_file(asset_path)
    return FileResponse(file_path)


@router.get("/", response_class=FileResponse, include_in_schema=False)
async def serve_frontend():
    return await get_frontend_index_response()


@router.get("/admin", response_class=FileResponse, include_in_schema=False)
async def serve_admin():
    return await get_legacy_admin_response()


@router.get("/assets/{asset_path:path}", response_class=FileResponse, include_in_schema=False)
async def serve_frontend_asset(asset_path: str):
    return await get_frontend_static_response(f"assets/{asset_path}")
