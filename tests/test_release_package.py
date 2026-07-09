import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.build_release_package import build_package, validate_release_version


class ReleasePackageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository_root = Path(self.temp_dir.name) / "repo"
        self.output_dir = Path(self.temp_dir.name) / "output"
        self.repository_root.mkdir()
        self._create_required_tree()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write(self, relative_path, content="placeholder"):
        path = self.repository_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _create_required_tree(self):
        for relative_path in (
            "README.md",
            "CHANGELOG.md",
            "LICENSE",
            "LICENSING.md",
            ".env.example",
            "docker-compose.yml",
            "requirements.txt",
            "config.py",
            "web.py",
            "frontend/package.json",
            "frontend/admin.html",
            "frontend/dist/index.html",
            "frontend/dist/assets/app.js",
            "frontend/public/assets/logo.svg",
            "secrets/users.txt.example",
            "src/router.py",
            "scripts/hash_password.py",
        ):
            if relative_path == "web.py":
                self._write(relative_path, 'APP_VERSION = "1.2.3"\n')
            elif relative_path == "frontend/package.json":
                self._write(relative_path, '{"version": "1.2.3"}\n')
            else:
                self._write(relative_path)

        for relative_path in (
            ".git/config",
            "data/codebuddy2api.sqlite3",
            "frontend/node_modules/vue/index.js",
            "secrets/users.txt",
            "tests/test_example.py",
        ):
            self._write(relative_path, "must not be packaged")

    def test_build_package_contains_runtime_files(self):
        artifacts = build_package(self.repository_root, "v1.2.3", self.output_dir)

        self.assertTrue(artifacts.tarball.is_file())
        self.assertTrue(artifacts.zipfile.is_file())
        self.assertTrue(artifacts.checksums.is_file())

        with tarfile.open(artifacts.tarball, "r:gz") as archive:
            tar_names = set(archive.getnames())

        self.assertIn("codebuddy2api/VERSION", tar_names)
        self.assertIn("codebuddy2api/web.py", tar_names)
        self.assertIn("codebuddy2api/src/router.py", tar_names)
        self.assertIn("codebuddy2api/scripts/hash_password.py", tar_names)
        self.assertIn("codebuddy2api/frontend/dist/index.html", tar_names)
        self.assertIn("codebuddy2api/frontend/dist/assets/app.js", tar_names)
        self.assertIn("codebuddy2api/frontend/public/assets/logo.svg", tar_names)
        self.assertIn("codebuddy2api/secrets/users.txt.example", tar_names)
        self.assertNotIn("codebuddy2api/secrets/users.txt", tar_names)
        self.assertNotIn("codebuddy2api/data/codebuddy2api.sqlite3", tar_names)
        self.assertNotIn("codebuddy2api/frontend/node_modules/vue/index.js", tar_names)
        self.assertNotIn("codebuddy2api/tests/test_example.py", tar_names)

        with zipfile.ZipFile(artifacts.zipfile) as archive:
            zip_names = set(archive.namelist())

        self.assertEqual(tar_names, zip_names)
        checksums = artifacts.checksums.read_text(encoding="utf-8")
        self.assertIn("codebuddy2api.tar.gz", checksums)
        self.assertIn("codebuddy2api.zip", checksums)

    def test_build_package_requires_frontend_dist(self):
        (self.repository_root / "frontend" / "dist" / "index.html").unlink()

        with self.assertRaisesRegex(RuntimeError, "Build the frontend first"):
            build_package(self.repository_root, "v1.2.3", self.output_dir)

    def test_build_package_rejects_non_stable_tag(self):
        with self.assertRaisesRegex(RuntimeError, "v1.2.3"):
            build_package(self.repository_root, "latest", self.output_dir)

        self.assertFalse(self.output_dir.exists())

    def test_build_package_rejects_tag_that_differs_from_application_version(self):
        with self.assertRaisesRegex(RuntimeError, "v1.2.3"):
            build_package(self.repository_root, "v1.2.4", self.output_dir)

        self.assertFalse(self.output_dir.exists())

    def test_release_version_requires_matching_frontend_version(self):
        self._write("frontend/package.json", '{"version": "1.2.4"}\n')

        with self.assertRaisesRegex(RuntimeError, "frontend/package.json"):
            validate_release_version(self.repository_root, "v1.2.3")

    def test_release_version_requires_one_literal_application_version(self):
        for source in (
            "APP_VERSION = get_version()\n",
            'APP_VERSION = "1.2.3"\nAPP_VERSION = "1.2.3"\n',
            "OTHER_VERSION = '1.2.3'\n",
        ):
            with self.subTest(source=source):
                self._write("web.py", source)
                with self.assertRaisesRegex(RuntimeError, "APP_VERSION"):
                    validate_release_version(self.repository_root, "v1.2.3")
