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

    def test_compose_forces_all_runtime_data_into_single_persistent_mount(self):
        compose_text = (self.repository_root / "docker-compose.yml").read_text(
            encoding="utf-8"
        )
        compose_lines = {
            line.strip()
            for line in compose_text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }

        self.assertIn("CODEBUDDY_DATA_DIR: /app/data", compose_lines)
        self.assertIn("image: ghcr.io/iceean/codebuddy2api:latest", compose_lines)
        self.assertIn("- ./data:/app/data", compose_lines)
        self.assertIn("- ./secrets:/app/secrets:ro", compose_lines)
        self.assertNotIn(".codebuddy_creds", compose_text)
        self.assertNotIn(
            "- ./secrets/users.txt:/app/secrets/users.txt:ro", compose_lines
        )
        self.assertNotIn("build:", compose_text)

    def test_compose_env_file_is_optional(self):
        compose_text = (self.repository_root / "docker-compose.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("env_file:", compose_text)
        self.assertIn("path: .env", compose_text)
        self.assertIn("required: false", compose_text)

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
        self.assertNotIn(".codebuddy_creds", entrypoint)

    def test_container_entrypoint_supports_user_setup_commands(self):
        entrypoint = (self.repository_root / "entrypoint.sh").read_text(encoding="utf-8")

        self.assertIn("hash-password)", entrypoint)
        self.assertIn("add-user)", entrypoint)
        self.assertIn('users_file="/app/secrets/users.txt"', entrypoint)
        self.assertIn('mkdir -p "${users_dir}"', entrypoint)
        self.assertIn('exec codebuddy2api-hash-password "$@"', entrypoint)
        self.assertIn(
            'codebuddy2api-hash-password "$@" --output "${users_file}"', entrypoint
        )
        self.assertNotIn("chmod 644", entrypoint)

    def test_container_entrypoint_uses_private_runtime_users_copy(self):
        entrypoint = (self.repository_root / "entrypoint.sh").read_text(encoding="utf-8")

        self.assertIn('runtime_users_file="/run/codebuddy2api/users.txt"', entrypoint)
        self.assertIn('CODEBUDDY_USERS_FILE="${runtime_users_file}"', entrypoint)
        self.assertIn("export CODEBUDDY_USERS_FILE", entrypoint)
        self.assertIn('-m 400 -o "${APP_USER}" -g "${APP_USER}"', entrypoint)

    def test_dockerfile_uses_recommended_runtime_and_hash_command(self):
        dockerfile = (self.repository_root / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("ARG PYTHON_VERSION=3.12", dockerfile)
        self.assertIn(
            "FROM --platform=$BUILDPLATFORM node:${NODE_VERSION}-slim AS frontend-build",
            dockerfile,
        )
        self.assertIn("FROM python:${PYTHON_VERSION}-slim AS runtime", dockerfile)
        self.assertNotIn("COPY . .", dockerfile)
        for runtime_copy in (
            "COPY config.py web.py ./",
            "COPY src ./src",
            "COPY scripts/hash_password.py ./scripts/hash_password.py",
            "COPY frontend/admin.html ./frontend/admin.html",
            "COPY frontend/public ./frontend/public",
        ):
            with self.subTest(runtime_copy=runtime_copy):
                self.assertIn(runtime_copy, dockerfile)
        self.assertIn(
            "ln -s /app/scripts/hash_password.py /usr/local/bin/codebuddy2api-hash-password",
            dockerfile,
        )
        self.assertNotIn(".codebuddy_creds", dockerfile)

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
            mock.patch.object(web, "validate_configured_users_file") as validate_users,
            mock.patch.object(web, "initialize_database") as initialize_database,
            mock.patch.object(
                web.usage_stats_retention_manager,
                "startup",
                new=mock.AsyncMock(),
            ) as retention_startup,
            mock.patch.object(
                web.usage_stats_retention_manager,
                "shutdown",
                new=mock.AsyncMock(),
            ) as retention_shutdown,
            mock.patch.object(web.lifecycle_manager, "startup", new=mock.AsyncMock()) as startup,
            mock.patch.object(web.lifecycle_manager, "shutdown", new=mock.AsyncMock()) as shutdown,
        ):
            async with web.lifespan(web.app):
                validate_users.assert_called_once_with()
                initialize_database.assert_called_once_with()
                retention_startup.assert_awaited_once_with()
                startup.assert_awaited_once_with()
                retention_shutdown.assert_not_awaited()
                shutdown.assert_not_awaited()

        retention_shutdown.assert_awaited_once_with()
        shutdown.assert_awaited_once_with()

    async def test_lifespan_stops_before_resource_startup_when_users_file_is_invalid(self):
        with (
            mock.patch.object(
                web,
                "validate_configured_users_file",
                side_effect=RuntimeError("missing users"),
            ) as validate_users,
            mock.patch.object(web, "initialize_database") as initialize_database,
            mock.patch.object(
                web.usage_stats_retention_manager,
                "startup",
                new=mock.AsyncMock(),
            ) as retention_startup,
            mock.patch.object(
                web.usage_stats_retention_manager,
                "shutdown",
                new=mock.AsyncMock(),
            ) as retention_shutdown,
            mock.patch.object(web.lifecycle_manager, "startup", new=mock.AsyncMock()) as startup,
            mock.patch.object(web.lifecycle_manager, "shutdown", new=mock.AsyncMock()) as shutdown,
        ):
            with self.assertRaisesRegex(RuntimeError, "missing users"):
                async with web.lifespan(web.app):
                    pass

        validate_users.assert_called_once_with()
        initialize_database.assert_not_called()
        retention_startup.assert_not_awaited()
        startup.assert_not_awaited()
        retention_shutdown.assert_awaited_once_with()
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
