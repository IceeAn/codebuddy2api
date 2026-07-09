import configparser
import json
import runpy
import unittest
from pathlib import Path
from unittest import mock

import config
import web


class RepositoryConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.repository_root = Path(__file__).resolve().parents[1]

    def test_docker_build_context_excludes_runtime_database(self):
        entries = {
            line.strip()
            for line in (self.repository_root / ".dockerignore").read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }

        self.assertIn("data/", entries)

    def test_compose_forces_database_into_persistent_mount(self):
        compose_lines = {
            line.strip()
            for line in (self.repository_root / "docker-compose.yml").read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }

        self.assertIn("CODEBUDDY_DATA_DIR: /app/data", compose_lines)

    def test_release_workflow_uploads_local_runtime_packages(self):
        workflow = (
            self.repository_root / ".github" / "workflows" / "release.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("pnpm run build:bundle", workflow)
        self.assertIn('python3 scripts/build_release_package.py "${TAG}"', workflow)
        self.assertIn("dist/release/codebuddy2api.tar.gz", workflow)
        self.assertIn("dist/release/codebuddy2api.zip", workflow)
        self.assertIn("dist/release/SHA256SUMS.txt", workflow)

    def test_container_entrypoint_forces_database_into_persistent_mount(self):
        entrypoint = (self.repository_root / "entrypoint.sh").read_text(encoding="utf-8")

        self.assertIn('CODEBUDDY_DATA_DIR="/app/data"', entrypoint)
        self.assertIn("export CODEBUDDY_DATA_DIR", entrypoint)

    def test_coverage_configuration_measures_all_production_modules(self):
        coverage_config = configparser.ConfigParser()
        coverage_config.read(self.repository_root / ".coveragerc", encoding="utf-8")

        self.assertEqual(coverage_config.get("run", "source").split(), ["."])
        included = set(coverage_config.get("report", "include").split())
        self.assertEqual(included, {"config.py", "src/*.py", "web.py"})

    def test_frontend_package_uses_application_version(self):
        package = json.loads(
            (self.repository_root / "frontend" / "package.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(package["version"], web.app.version)

class ServerStartupTests(unittest.TestCase):
    def test_run_server_uses_uvicorn(self):
        with (
            mock.patch.object(web, "get_server_host", return_value="127.0.0.2"),
            mock.patch.object(web, "get_server_port", return_value=9001),
            mock.patch("uvicorn.run") as run,
        ):
            web.run_server()

        run.assert_called_once_with(
            web.app,
            host="127.0.0.2",
            port=9001,
            log_level="info",
            access_log=False,
            use_colors=True,
        )

    def test_main_module_enables_cors_and_invokes_server(self):
        with (
            mock.patch.object(config, "get_allowed_hosts", return_value=[]),
            mock.patch.object(config, "get_allowed_origins", return_value=["https://client.example"]),
            mock.patch("uvicorn.run") as run,
        ):
            namespace = runpy.run_module("web", run_name="__main__")

        run.assert_called_once()
        middleware_classes = [item.cls for item in namespace["app"].user_middleware]
        from fastapi.middleware.cors import CORSMiddleware

        self.assertIn(CORSMiddleware, middleware_classes)


class ServerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_starts_and_stops_resources(self):
        with (
            mock.patch.object(web, "initialize_database") as initialize_database,
            mock.patch.object(web.lifecycle_manager, "startup", new=mock.AsyncMock()) as startup,
            mock.patch.object(web.lifecycle_manager, "shutdown", new=mock.AsyncMock()) as shutdown,
        ):
            async with web.lifespan(web.app):
                initialize_database.assert_called_once_with()
                startup.assert_awaited_once_with()
                shutdown.assert_not_awaited()

        shutdown.assert_awaited_once_with()

    async def test_health_and_root_endpoints_return_metadata(self):
        health = await web.health_check()
        root = await web.root()

        self.assertEqual(health["status"], "healthy")
        self.assertEqual(root["service"], "CodeBuddy2API")
        self.assertRegex(web.app.version, r"^\d+\.\d+\.\d+$")
        self.assertEqual(root["version"], web.app.version)
        self.assertEqual(root["endpoints"]["chat"], "/openai/v1/chat/completions")


if __name__ == "__main__":
    unittest.main()
