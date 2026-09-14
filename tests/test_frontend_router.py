
import tests  # 在生产模块导入前隔离测试数据目录。
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import httpx
from fastapi import FastAPI, HTTPException

from src.frontend_router import (
    get_frontend_index_response,
    get_frontend_static_response,
    router as frontend_router,
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

    async def test_missing_built_frontend_fails_fast(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with mock.patch("src.frontend_router.DIST_DIR", Path(tmp_dir) / "dist"):
                with self.assertRaises(HTTPException) as context:
                    await get_frontend_index_response()

        self.assertEqual(context.exception.status_code, 503)
        self.assertEqual(context.exception.detail, "Built frontend not found.")

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

    def test_readme_logo_has_synchronized_public_copy(self):
        repository_root = Path(__file__).resolve().parent.parent
        relative_logo_path = Path("frontend/public/assets/codebuddy2api.svg")
        readme = (repository_root / "README.md").read_text(encoding="utf-8")
        public_logo = repository_root / relative_logo_path
        source_logo = repository_root / "frontend/src/assets/codebuddy2api.svg"

        self.assertIn(f'src="{relative_logo_path.as_posix()}"', readme)
        self.assertTrue(public_logo.is_file())
        self.assertEqual(public_logo.read_bytes(), source_logo.read_bytes())

    async def test_hashed_vite_assets_receive_long_lived_immutable_cache(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            dist_dir = Path(tmp_dir)
            (dist_dir / "assets").mkdir()
            for filename in (
                "index-D9AGu9yF.js",
                "index-D9AGu9yF.css",
                "font-DjKNqYRj.woff2",
                "codebuddy2api-D9AGu9yF.svg",
                "theme-init-0123456789ab.js",
            ):
                (dist_dir / "assets" / filename).write_bytes(b"asset")

            with mock.patch("src.frontend_router.DIST_DIR", dist_dir):
                for filename in (
                    "index-D9AGu9yF.js",
                    "index-D9AGu9yF.css",
                    "font-DjKNqYRj.woff2",
                    "codebuddy2api-D9AGu9yF.svg",
                    "theme-init-0123456789ab.js",
                ):
                    with self.subTest(filename=filename):
                        response = await get_frontend_static_response(f"assets/{filename}")
                        self.assertEqual(
                            response.headers["Cache-Control"],
                            "public, max-age=31536000, immutable",
                        )

    async def test_descriptive_asset_suffix_is_not_treated_as_content_hash(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            dist_dir = Path(tmp_dir)
            (dist_dir / "assets").mkdir()
            (dist_dir / "assets" / "codebuddy2api-monochrome.svg").write_bytes(b"asset")

            with mock.patch("src.frontend_router.DIST_DIR", dist_dir):
                response = await get_frontend_static_response(
                    "assets/codebuddy2api-monochrome.svg"
                )

        self.assertEqual(
            response.headers["Cache-Control"],
            "public, max-age=0, must-revalidate",
        )

    async def test_unhashed_assets_honor_conditional_requests(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root_dir = Path(tmp_dir)
            dist_dir = root_dir / "dist"
            public_dir = root_dir / "public"
            (public_dir / "assets").mkdir(parents=True)
            asset_file = public_dir / "assets" / "theme-init.js"
            asset_file.write_text(
                "document.documentElement.classList.add('dark');",
                encoding="utf-8",
            )
            modified_at = datetime(1994, 11, 6, 8, 49, 37, tzinfo=timezone.utc).timestamp()
            os.utime(asset_file, (modified_at, modified_at))

            app = FastAPI()
            app.include_router(frontend_router)
            transport = httpx.ASGITransport(app=app)
            with (
                mock.patch("src.frontend_router.DIST_DIR", dist_dir),
                mock.patch("src.frontend_router.PUBLIC_DIR", public_dir),
            ):
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://localhost",
                ) as client:
                    first = await client.get("/assets/theme-init.js")
                    by_etag = await client.get(
                        "/assets/theme-init.js",
                        headers={"If-None-Match": first.headers["ETag"]},
                    )
                    by_weak_etag = await client.get(
                        "/assets/theme-init.js",
                        headers={"If-None-Match": f"W/{first.headers['ETag']}"},
                    )
                    by_wildcard = await client.get(
                        "/assets/theme-init.js",
                        headers={"If-None-Match": "*"},
                    )
                    by_modified_time = await client.get(
                        "/assets/theme-init.js",
                        headers={"If-Modified-Since": first.headers["Last-Modified"]},
                    )
                    by_asctime_date = await client.get(
                        "/assets/theme-init.js",
                        headers={"If-Modified-Since": "Sun Nov  6 08:49:37 1994"},
                    )
                    etag_precedence = await client.get(
                        "/assets/theme-init.js",
                        headers={
                            "If-None-Match": '"different"',
                            "If-Modified-Since": first.headers["Last-Modified"],
                        },
                    )
                    malformed_date = await client.get(
                        "/assets/theme-init.js",
                        headers={"If-Modified-Since": "not-a-date"},
                    )
                    invalid_calendar_dates = [
                        await client.get(
                            "/assets/theme-init.js",
                            headers={"If-Modified-Since": value},
                        )
                        for value in (
                            "Thu, 99 Dec 2099 23:59:59 GMT",
                            "Thu, 01 Dec 2099 99:59:59 GMT",
                            "Thu, 01 Dec 2099 23:99:59 GMT",
                            "Thu, 01 Dec 2099 23:59:99 GMT",
                            "Thu, 01 Dec 2099 23:59:59 -0000",
                            "Sun Nov 6 08:49:37 1994",
                        )
                    ]

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.headers["Cache-Control"], "public, max-age=0, must-revalidate")
        for response in (
            by_etag,
            by_weak_etag,
            by_wildcard,
            by_modified_time,
            by_asctime_date,
        ):
            with self.subTest(headers=dict(response.request.headers)):
                self.assertEqual(response.status_code, 304)
                self.assertEqual(response.content, b"")
                self.assertEqual(
                    response.headers["Cache-Control"],
                    "public, max-age=0, must-revalidate",
                )
                self.assertEqual(response.headers["ETag"], first.headers["ETag"])
        self.assertEqual(etag_precedence.status_code, 200)
        self.assertEqual(malformed_date.status_code, 200)
        for response in invalid_calendar_dates:
            with self.subTest(value=response.request.headers["If-Modified-Since"]):
                self.assertEqual(response.status_code, 200)

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
        asset_response = object()
        with (
            mock.patch(
                "src.frontend_router.get_frontend_index_response",
                new=mock.AsyncMock(return_value=frontend_response),
            ) as get_frontend,
            mock.patch(
                "src.frontend_router.get_frontend_static_response",
                new=mock.AsyncMock(return_value=asset_response),
            ) as get_asset,
        ):
            self.assertIs(await serve_frontend(), frontend_response)
            self.assertIs(await serve_admin(), frontend_response)
            request = mock.Mock(headers=object())
            self.assertIs(await serve_frontend_asset("app.js", request), asset_response)

        self.assertEqual(get_frontend.await_count, 2)
        get_asset.assert_awaited_once_with("assets/app.js", request.headers)


if __name__ == "__main__":
    unittest.main()
