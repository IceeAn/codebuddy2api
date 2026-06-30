import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

from src.frontend_router import (
    get_frontend_index_response,
    get_frontend_static_response,
    get_legacy_admin_response,
)


class FrontendRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_serves_built_vue_index_with_no_cache_headers(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            dist_dir = Path(tmp_dir)
            (dist_dir / "index.html").write_text("<div id=\"app\"></div>", encoding="utf-8")

            with mock.patch("src.frontend_router.DIST_DIR", dist_dir):
                response = await get_frontend_index_response()

        self.assertEqual(response.media_type, "text/html")
        self.assertEqual(response.headers["Cache-Control"], "no-cache, no-store, must-revalidate")

    async def test_missing_built_frontend_falls_back_to_legacy_admin(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            admin_file = Path(tmp_dir) / "admin.html"
            admin_file.write_text("<title>CodeBuddy2API 管理控制面板</title>", encoding="utf-8")

            with (
                mock.patch("src.frontend_router.DIST_DIR", Path(tmp_dir) / "dist"),
                mock.patch("src.frontend_router.LEGACY_ADMIN_FILE", admin_file),
            ):
                response = await get_frontend_index_response()

        self.assertEqual(response.media_type, "text/html")
        self.assertEqual(Path(response.path), admin_file)
        self.assertEqual(response.headers["Cache-Control"], "no-cache, no-store, must-revalidate")

    async def test_serves_legacy_admin_html_with_no_cache_headers(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            admin_file = Path(tmp_dir) / "admin.html"
            admin_file.write_text("<title>CodeBuddy2API 管理控制面板</title>", encoding="utf-8")

            with mock.patch("src.frontend_router.LEGACY_ADMIN_FILE", admin_file):
                response = await get_legacy_admin_response()

        self.assertEqual(response.media_type, "text/html")
        self.assertEqual(response.headers["Cache-Control"], "no-cache, no-store, must-revalidate")

    async def test_missing_legacy_admin_fails_fast(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with mock.patch("src.frontend_router.LEGACY_ADMIN_FILE", Path(tmp_dir) / "admin.html"):
                with self.assertRaises(HTTPException) as context:
                    await get_legacy_admin_response()

        self.assertEqual(context.exception.status_code, 503)

    async def test_static_assets_cannot_escape_dist_dir(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            dist_dir = Path(tmp_dir)
            (dist_dir / "assets").mkdir()
            (dist_dir / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")

            with mock.patch("src.frontend_router.DIST_DIR", dist_dir):
                response = await get_frontend_static_response("assets/app.js")
                self.assertTrue(str(response.path).endswith("assets/app.js"))

                with self.assertRaises(HTTPException) as context:
                    await get_frontend_static_response("../secret.txt")

        self.assertEqual(context.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
