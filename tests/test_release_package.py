import hashlib
import json
import os
import struct
import tarfile
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from scripts.build_release_package import build_package, validate_release_version


class ReleasePackageTests(unittest.TestCase):
    source_date_epoch = 1_700_000_000

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
            "release_runtime_lock.py",
            "config.py",
            "web.py",
            "frontend/package.json",
            "frontend/dist/index.html",
            "frontend/dist/assets/app.js",
            "frontend/public/assets/logo.svg",
            "secrets/users.txt.example",
            "src/router.py",
            "scripts/hash_password.py",
            "scripts/update_release.py",
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

    def _build(self, output_dir=None):
        return build_package(
            self.repository_root,
            "v1.2.3",
            output_dir or self.output_dir,
            source_date_epoch=self.source_date_epoch,
        )

    @staticmethod
    def _sha256(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_build_package_contains_runtime_files(self):
        artifacts = self._build()

        self.assertTrue(artifacts.tarball.is_file())
        self.assertTrue(artifacts.zipfile.is_file())
        self.assertTrue(artifacts.checksums.is_file())

        with tarfile.open(artifacts.tarball, "r:gz") as archive:
            tar_names = set(archive.getnames())

        self.assertIn("codebuddy2api/VERSION", tar_names)
        self.assertIn("codebuddy2api/RELEASE_MANIFEST.json", tar_names)
        self.assertIn("codebuddy2api/web.py", tar_names)
        self.assertIn("codebuddy2api/release_runtime_lock.py", tar_names)
        self.assertIn("codebuddy2api/src/router.py", tar_names)
        self.assertIn("codebuddy2api/scripts/hash_password.py", tar_names)
        self.assertIn("codebuddy2api/frontend/dist/index.html", tar_names)
        self.assertIn("codebuddy2api/frontend/dist/assets/app.js", tar_names)
        self.assertIn("codebuddy2api/frontend/public/assets/logo.svg", tar_names)
        self.assertIn("codebuddy2api/secrets/users.txt.example", tar_names)
        self.assertNotIn("codebuddy2api/secrets/users.txt", tar_names)
        self.assertNotIn("codebuddy2api/data/codebuddy2api.sqlite3", tar_names)
        self.assertNotIn("codebuddy2api/frontend/node_modules/vue/index.js", tar_names)
        self.assertNotIn("codebuddy2api/frontend/admin.html", tar_names)
        self.assertNotIn("codebuddy2api/tests/test_example.py", tar_names)

        with zipfile.ZipFile(artifacts.zipfile) as archive:
            zip_names = set(archive.namelist())

        self.assertEqual(tar_names, zip_names)
        with zipfile.ZipFile(artifacts.zipfile) as archive:
            manifest = json.loads(
                archive.read("codebuddy2api/RELEASE_MANIFEST.json")
            )
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["version"], "v1.2.3")
        self.assertEqual(
            manifest["replace_directories"],
            ["frontend", "scripts", "src"],
        )
        self.assertEqual(
            set(manifest["files"]),
            {name.removeprefix("codebuddy2api/") for name in zip_names},
        )
        checksums = artifacts.checksums.read_text(encoding="utf-8")
        self.assertIn("codebuddy2api.tar.gz", checksums)
        self.assertIn("codebuddy2api.zip", checksums)

    def test_build_package_excludes_workspace_artifacts_and_unlisted_files(self):
        for relative_path in (
            "src/__pycache__/router.cpython-312.pyc",
            "src/debug.txt",
            "scripts/__pycache__/tool.cpython-312.pyc",
            "scripts/debug.log",
            "frontend/dist/.DS_Store",
            "frontend/public/Thumbs.db",
        ):
            self._write(relative_path, "must not be packaged")

        artifacts = self._build()

        with tarfile.open(artifacts.tarball, "r:gz") as archive:
            names = set(archive.getnames())
        for relative_path in (
            "src/__pycache__/router.cpython-312.pyc",
            "src/debug.txt",
            "scripts/__pycache__/tool.cpython-312.pyc",
            "scripts/debug.log",
            "frontend/dist/.DS_Store",
            "frontend/public/Thumbs.db",
        ):
            self.assertNotIn(f"codebuddy2api/{relative_path}", names)

    def test_build_package_rejects_file_and_directory_symbolic_links(self):
        external_file = Path(self.temp_dir.name) / "external.txt"
        external_file.write_text("outside repository", encoding="utf-8")
        external_dir = Path(self.temp_dir.name) / "external"
        external_dir.mkdir()
        (external_dir / "secret.txt").write_text("secret", encoding="utf-8")

        links = (
            (self.repository_root / "src" / "external.py", external_file, False),
            (self.repository_root / "src" / "external", external_dir, True),
        )
        for link, target, target_is_directory in links:
            with self.subTest(link=link.name):
                link.symlink_to(target, target_is_directory=target_is_directory)
                with self.assertRaisesRegex(RuntimeError, "Symbolic link"):
                    self._build()
                link.unlink()

    def test_build_package_rejects_symbolic_link_in_parent_path(self):
        frontend = self.repository_root / "frontend"
        external_frontend = Path(self.temp_dir.name) / "external-frontend"
        frontend.rename(external_frontend)
        frontend.symlink_to(external_frontend, target_is_directory=True)

        with self.assertRaisesRegex(RuntimeError, "Symbolic link"):
            self._build()

    def test_build_package_rejects_non_regular_files(self):
        fifo = self.repository_root / "src" / "release.fifo"
        os.mkfifo(fifo)

        with self.assertRaisesRegex(RuntimeError, "regular file"):
            self._build()

    def test_build_package_rejects_output_inside_release_input_directory(self):
        output_dir = self.repository_root / "frontend" / "dist" / "release"

        with self.assertRaisesRegex(RuntimeError, "output directory"):
            self._build(output_dir)

        self.assertFalse(output_dir.exists())

    def test_build_package_is_reproducible_and_normalizes_metadata(self):
        first = self._build(Path(self.temp_dir.name) / "first")
        for path in self.repository_root.rglob("*"):
            if path.is_file():
                normalized_time = self.source_date_epoch + 120
                os.utime(path, (normalized_time, normalized_time))
        second = self._build(Path(self.temp_dir.name) / "second")

        self.assertEqual(self._sha256(first.tarball), self._sha256(second.tarball))
        self.assertEqual(self._sha256(first.zipfile), self._sha256(second.zipfile))
        self.assertEqual(
            first.checksums.read_bytes(),
            second.checksums.read_bytes(),
        )

        gzip_header = first.tarball.read_bytes()[:10]
        self.assertEqual(struct.unpack("<I", gzip_header[4:8])[0], self.source_date_epoch)
        self.assertEqual(gzip_header[3] & 0x08, 0)

        with tarfile.open(first.tarball, "r:gz") as archive:
            member = archive.getmember("codebuddy2api/VERSION")
            self.assertEqual(member.mtime, self.source_date_epoch)
            self.assertEqual(member.uid, 0)
            self.assertEqual(member.gid, 0)
            self.assertEqual(member.uname, "")
            self.assertEqual(member.gname, "")
            self.assertEqual(member.mode, 0o644)

        expected_zip_time = datetime.fromtimestamp(
            self.source_date_epoch,
            tz=timezone.utc,
        ).timetuple()[:6]
        with zipfile.ZipFile(first.zipfile) as archive:
            member = archive.getinfo("codebuddy2api/VERSION")
            self.assertEqual(member.date_time, expected_zip_time)
            self.assertEqual(member.create_system, 3)
            self.assertEqual(member.external_attr >> 16, 0o100644)

    def test_build_package_keeps_old_artifacts_when_generation_fails(self):
        self.output_dir.mkdir()
        old_contents = {
            "codebuddy2api.tar.gz": b"old tarball",
            "codebuddy2api.zip": b"old zip",
            "SHA256SUMS.txt": b"old checksums",
        }
        for name, content in old_contents.items():
            (self.output_dir / name).write_bytes(content)

        with mock.patch(
            "scripts.build_release_package._write_zip",
            side_effect=RuntimeError("simulated failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated failure"):
                self._build()

        self.assertEqual(
            {path.name: path.read_bytes() for path in self.output_dir.iterdir()},
            old_contents,
        )

    def test_build_package_requires_frontend_dist(self):
        (self.repository_root / "frontend" / "dist" / "index.html").unlink()

        with self.assertRaisesRegex(RuntimeError, "Build the frontend first"):
            self._build()

    def test_build_package_rejects_non_stable_tag(self):
        with self.assertRaisesRegex(RuntimeError, "v1.2.3"):
            build_package(
                self.repository_root,
                "latest",
                self.output_dir,
                source_date_epoch=self.source_date_epoch,
            )

        self.assertFalse(self.output_dir.exists())

    def test_build_package_rejects_tag_that_differs_from_application_version(self):
        with self.assertRaisesRegex(RuntimeError, "v1.2.3"):
            build_package(
                self.repository_root,
                "v1.2.4",
                self.output_dir,
                source_date_epoch=self.source_date_epoch,
            )

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
