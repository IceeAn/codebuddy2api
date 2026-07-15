import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

from src.frontend_router import (
    get_frontend_index_response,
    get_frontend_static_response,
    get_legacy_admin_response,
    serve_admin,
    serve_frontend,
    serve_frontend_asset,
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
                self.assertEqual(
                    response.headers["Cache-Control"],
                    "public, max-age=0, must-revalidate",
                )

                with self.assertRaises(HTTPException) as context:
                    await get_frontend_static_response("../secret.txt")

        self.assertEqual(context.exception.status_code, 404)

    async def test_static_assets_fall_back_to_public_dir_without_build(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root_dir = Path(tmp_dir)
            dist_dir = root_dir / "dist"
            public_dir = root_dir / "public"
            (public_dir / "assets").mkdir(parents=True)
            icon_file = public_dir / "assets" / "codebuddy2api.svg"
            icon_file.write_text("<svg></svg>", encoding="utf-8")

            with (
                mock.patch("src.frontend_router.DIST_DIR", dist_dir),
                mock.patch("src.frontend_router.PUBLIC_DIR", public_dir),
            ):
                response = await get_frontend_static_response("assets/codebuddy2api.svg")

        self.assertEqual(Path(response.path).resolve(), icon_file.resolve())
        self.assertEqual(
            response.headers["Cache-Control"],
            "public, max-age=0, must-revalidate",
        )

    async def test_hashed_vite_assets_receive_long_lived_immutable_cache(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            dist_dir = Path(tmp_dir)
            (dist_dir / "assets").mkdir()
            for filename in (
                "index-D9AGu9yF.js",
                "index-D9AGu9yF.css",
                "font-DjKNqYRj.woff2",
            ):
                (dist_dir / "assets" / filename).write_bytes(b"asset")

            with mock.patch("src.frontend_router.DIST_DIR", dist_dir):
                for filename in (
                    "index-D9AGu9yF.js",
                    "index-D9AGu9yF.css",
                    "font-DjKNqYRj.woff2",
                ):
                    with self.subTest(filename=filename):
                        response = await get_frontend_static_response(f"assets/{filename}")
                        self.assertEqual(
                            response.headers["Cache-Control"],
                            "public, max-age=31536000, immutable",
                        )

    async def test_missing_static_asset_inside_dist_returns_404(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                mock.patch("src.frontend_router.DIST_DIR", Path(tmp_dir) / "dist"),
                mock.patch("src.frontend_router.PUBLIC_DIR", Path(tmp_dir) / "public"),
            ):
                with self.assertRaises(HTTPException) as context:
                    await get_frontend_static_response("assets/missing.js")

        self.assertEqual(context.exception.status_code, 404)

    async def test_route_handlers_delegate_to_response_helpers(self):
        frontend_response = object()
        admin_response = object()
        asset_response = object()
        with (
            mock.patch(
                "src.frontend_router.get_frontend_index_response",
                new=mock.AsyncMock(return_value=frontend_response),
            ) as get_frontend,
            mock.patch(
                "src.frontend_router.get_legacy_admin_response",
                new=mock.AsyncMock(return_value=admin_response),
            ) as get_admin,
            mock.patch(
                "src.frontend_router.get_frontend_static_response",
                new=mock.AsyncMock(return_value=asset_response),
            ) as get_asset,
        ):
            self.assertIs(await serve_frontend(), frontend_response)
            self.assertIs(await serve_admin(), admin_response)
            self.assertIs(await serve_frontend_asset("app.js"), asset_response)

        get_frontend.assert_awaited_once_with()
        get_admin.assert_awaited_once_with()
        get_asset.assert_awaited_once_with("assets/app.js")


if __name__ == "__main__":
    unittest.main()
