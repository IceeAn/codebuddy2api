"""
Serves the frontend for CodeBuddy2API management interface.
"""
import os

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()

# Get the absolute path to the admin interface file
HTML_FILE_PATH = os.path.join(os.path.dirname(__file__), "..", "frontend", "admin.html")


@router.get("/", response_class=FileResponse, include_in_schema=False)
async def serve_frontend():
    """Serves the CodeBuddy2API admin interface."""
    if not os.path.exists(HTML_FILE_PATH):
        return "Frontend file not found. Please ensure frontend/admin.html exists."

    # 添加缓存控制头，防止浏览器缓存
    headers = {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0"
    }
    return FileResponse(HTML_FILE_PATH, media_type="text/html", headers=headers)


@router.get("/admin", response_class=FileResponse, include_in_schema=False)
async def serve_admin():
    """Alternative route for admin interface."""
    if not os.path.exists(HTML_FILE_PATH):
        return "Frontend file not found. Please ensure frontend/admin.html exists."

    # 添加缓存控制头，防止浏览器缓存
    headers = {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0"
    }
    return FileResponse(HTML_FILE_PATH, media_type="text/html", headers=headers)
