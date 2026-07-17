import json
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest import mock

import release_runtime_lock
from scripts import update_release


class UpdateReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _release_files(self, version="v1.2.3"):
        plain_version = version.removeprefix("v")
        files = {
            "VERSION": f"{version}\n",
            "README.md": "readme",
            "release_runtime_lock.py": "runtime lock",
            "requirements.txt": "",
            "web.py": f'APP_VERSION = "{plain_version}"\n',
            "frontend/package.json": json.dumps({"version": plain_version}),
            "frontend/dist/index.html": "index",
            "frontend/public/logo.svg": "logo",
            "scripts/update_release.py": "updater",
            "src/router.py": "router",
            "secrets/users.txt.example": "example",
        }
        manifest_files = sorted([*files, update_release.MANIFEST_FILENAME])
        files[update_release.MANIFEST_FILENAME] = json.dumps(
            {
                "schema_version": 1,
                "version": version,
                "replace_directories": ["frontend", "scripts", "src"],
                "files": manifest_files,
            },
            sort_keys=True,
        )
        return files

    def _write_zip(self, name="codebuddy2api-test.zip", files=None):
        path = self.root / name
        with zipfile.ZipFile(path, "w") as archive:
            for relative_path, content in (files or self._release_files()).items():
                info = zipfile.ZipInfo(f"codebuddy2api/{relative_path}")
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, content)
        return path

    def _write_tar(self, name="codebuddy2api-test.tar.gz", files=None):
        source = self.root / "tar-source" / "codebuddy2api"
        for relative_path, content in (files or self._release_files()).items():
            path = source / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        archive_path = self.root / name
        with tarfile.open(archive_path, "w:gz") as archive:
            for path in sorted(source.rglob("*")):
                if path.is_file():
                    archive.add(path, arcname=path.relative_to(source.parent))
        return archive_path

    def _install_release_tree(self, project, version):
        files = self._release_files(version)
        for relative_path, content in files.items():
            path = project / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    def test_project_root_comes_from_script_path_not_working_directory(self):
        script = self.root / "project" / "scripts" / "update_release.py"
        script.parent.mkdir(parents=True)
        script.touch()

        self.assertEqual(
            update_release.resolve_project_root(script),
            (self.root / "project").resolve(),
        )

    def test_script_can_load_runtime_lock_outside_project_working_directory(self):
        script = Path(update_release.__file__).resolve()

        result = subprocess.run(
            [sys.executable, str(script), "--help"],
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("update", result.stdout)

    def test_stages_valid_zip_and_tar_release_files(self):
        for archive_path in (self._write_zip(), self._write_tar()):
            with self.subTest(name=archive_path.name):
                staging = self.root / f"staging-{archive_path.suffixes[-1]}"
                staged = update_release.stage_local_release(archive_path, staging)
                self.assertEqual(staged.version, "v1.2.3")
                self.assertEqual(staged.root.name, "codebuddy2api")

    def test_local_release_filename_is_strict(self):
        for name in (
            "other.zip",
            "codebuddy2api.tgz",
            "CodeBuddy2API.zip",
            "codebuddy2api.tar",
        ):
            with self.subTest(name=name):
                with self.assertRaisesRegex(update_release.UpdateError, "文件名"):
                    update_release.validate_release_filename(Path(name))

    def test_rejects_archive_traversal_and_symlink(self):
        traversal = self.root / "codebuddy2api-traversal.zip"
        with zipfile.ZipFile(traversal, "w") as archive:
            archive.writestr("codebuddy2api/../outside", "bad")
        with self.assertRaisesRegex(update_release.UpdateError, "路径"):
            update_release.stage_local_release(traversal, self.root / "traversal")

        symlink = self.root / "codebuddy2api-symlink.zip"
        with zipfile.ZipFile(symlink, "w") as archive:
            info = zipfile.ZipInfo("codebuddy2api/link")
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            archive.writestr(info, "target")
        with self.assertRaisesRegex(update_release.UpdateError, "普通文件"):
            update_release.stage_local_release(symlink, self.root / "symlink")

    def test_rejects_windows_drive_paths(self):
        for value in ("C:outside", "C:/outside", "c:"):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(update_release.UpdateError, "非法路径"),
            ):
                update_release._normalized_relative_path(value, context="Release 清单")
            with (
                self.subTest(archive_value=value),
                self.assertRaisesRegex(update_release.UpdateError, "非法路径"),
            ):
                update_release._archive_relative_path(
                    f"{update_release.PROJECT_ROOT_NAME}/{value}"
                )

    def test_rejects_duplicate_and_case_conflicting_archive_paths(self):
        for index, names in enumerate((
            ("codebuddy2api/README.md", "codebuddy2api/README.md"),
            ("codebuddy2api/README.md", "codebuddy2api/readme.md"),
        )):
            archive_path = self.root / f"codebuddy2api-conflict-{index}.zip"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(archive_path, "w") as archive:
                    for name in names:
                        info = zipfile.ZipInfo(name)
                        info.create_system = 3
                        info.external_attr = 0o100644 << 16
                        archive.writestr(info, "conflict")
            with self.subTest(names=names):
                with self.assertRaisesRegex(update_release.UpdateError, "冲突"):
                    update_release.stage_local_release(
                        archive_path,
                        self.root / f"conflict-{index}",
                    )

    def test_rejects_manifest_member_and_version_mismatches(self):
        files = self._release_files()
        manifest = json.loads(files[update_release.MANIFEST_FILENAME])
        manifest["files"].remove("README.md")
        files[update_release.MANIFEST_FILENAME] = json.dumps(manifest)
        archive = self._write_zip("codebuddy2api-manifest.zip", files)
        with self.assertRaisesRegex(update_release.UpdateError, "清单"):
            update_release.stage_local_release(archive, self.root / "manifest")

        files = self._release_files()
        files["web.py"] = 'APP_VERSION = "9.9.9"\n'
        archive = self._write_zip("codebuddy2api-version.zip", files)
        with self.assertRaisesRegex(update_release.UpdateError, "版本"):
            update_release.stage_local_release(archive, self.root / "version")

    def test_backup_replaces_previous_backup_and_copies_everything_else(self):
        project = self.root / "project"
        (project / "data").mkdir(parents=True)
        (project / "data" / "db").write_text("first", encoding="utf-8")
        (project / "venv" / "bin").mkdir(parents=True)
        (project / "venv" / "bin" / "python").write_text("python", encoding="utf-8")
        (project / ".env").write_text("SECRET=one", encoding="utf-8")
        (project / release_runtime_lock.LOCK_FILENAME).write_bytes(b"\0")

        first = update_release.create_latest_backup(project, "v1.0.0", "v1.1.0")
        self.assertEqual(first.name, "latest")
        self.assertEqual((first / "project" / "data" / "db").read_text(), "first")

        (project / "data" / "db").write_text("second", encoding="utf-8")
        update_release.create_latest_backup(project, "v1.1.0", "v1.2.0")
        backup_root = project / update_release.BACKUP_DIRECTORY_NAME
        self.assertEqual([path.name for path in backup_root.iterdir()], ["latest"])
        self.assertEqual(
            (backup_root / "latest" / "project" / "data" / "db").read_text(),
            "second",
        )
        self.assertFalse(
            (backup_root / "latest" / "project" / update_release.BACKUP_DIRECTORY_NAME).exists()
        )
        self.assertFalse(
            (backup_root / "latest" / "project" / release_runtime_lock.LOCK_FILENAME).exists()
        )

    def test_backup_interrupt_removes_pending_snapshot(self):
        project = self.root / "project-interrupted-backup"
        project.mkdir()
        (project / "large-file").write_text("partial", encoding="utf-8")
        interrupt = KeyboardInterrupt()

        with (
            mock.patch.object(update_release, "_copy_entry", side_effect=interrupt),
            self.assertRaises(KeyboardInterrupt) as raised,
        ):
            update_release.create_latest_backup(project, "v1.0.0", "v1.1.0")

        self.assertIs(raised.exception, interrupt)
        backup_root = project / update_release.BACKUP_DIRECTORY_NAME
        self.assertEqual(tuple(backup_root.iterdir()), ())

    def test_rollback_restores_latest_and_rotates_pre_rollback_state(self):
        project = self.root / "project"
        project.mkdir()
        (project / "state.txt").write_text("old", encoding="utf-8")
        (project / "VERSION").write_text("v1.0.0\n", encoding="utf-8")
        update_release.create_latest_backup(project, "v1.0.0", "v1.1.0")
        lock_path = project / release_runtime_lock.LOCK_FILENAME
        lock_path.write_bytes(b"locked")
        (project / "state.txt").write_text("new", encoding="utf-8")
        (project / "VERSION").write_text("v1.1.0\n", encoding="utf-8")
        (project / "new-only.txt").write_text("new", encoding="utf-8")

        update_release.rollback_latest(project)

        self.assertEqual((project / "state.txt").read_text(), "old")
        self.assertFalse((project / "new-only.txt").exists())
        latest_project = (
            project / update_release.BACKUP_DIRECTORY_NAME / "latest" / "project"
        )
        self.assertEqual((latest_project / "state.txt").read_text(), "new")
        self.assertTrue((latest_project / "new-only.txt").is_file())
        self.assertEqual(lock_path.read_bytes(), b"locked")

    def test_rollback_preserves_safety_snapshot_when_compensation_restore_fails(self):
        project = self.root / "project"
        project.mkdir()
        (project / "state.txt").write_text("old", encoding="utf-8")
        (project / "VERSION").write_text("v1.0.0\n", encoding="utf-8")
        update_release.create_latest_backup(project, "v1.0.0", "v1.1.0")
        (project / "state.txt").write_text("new", encoding="utf-8")
        (project / "VERSION").write_text("v1.1.0\n", encoding="utf-8")

        with (
            mock.patch(
                "scripts.update_release._restore_snapshot",
                side_effect=[
                    update_release.UpdateError("首次恢复失败"),
                    update_release.UpdateError("补偿恢复失败"),
                ],
            ),
            self.assertRaisesRegex(update_release.UpdateError, "已保留.*pending"),
        ):
            update_release.rollback_latest(project)

        backup_root = project / update_release.BACKUP_DIRECTORY_NAME
        pending = [
            path
            for path in backup_root.iterdir()
            if path.name.startswith(".pending-")
        ]
        self.assertEqual(len(pending), 1)
        self.assertEqual(
            (pending[0] / "project" / "state.txt").read_text(encoding="utf-8"),
            "new",
        )

    def test_rollback_interrupt_restores_pre_rollback_snapshot_and_propagates(self):
        project = self.root / "project-interrupted-rollback"
        project.mkdir()
        (project / "state.txt").write_text("old", encoding="utf-8")
        (project / "VERSION").write_text("v1.0.0\n", encoding="utf-8")
        update_release.create_latest_backup(project, "v1.0.0", "v1.1.0")
        (project / "state.txt").write_text("new", encoding="utf-8")
        (project / "VERSION").write_text("v1.1.0\n", encoding="utf-8")
        interrupt = KeyboardInterrupt()
        original_restore = update_release._restore_snapshot
        restore_count = 0

        def interrupt_then_restore(project_root, snapshot):
            nonlocal restore_count
            restore_count += 1
            if restore_count == 1:
                (project_root / "state.txt").write_text("partial", encoding="utf-8")
                (project_root / "partial.txt").write_text("partial", encoding="utf-8")
                raise interrupt
            return original_restore(project_root, snapshot)

        with (
            mock.patch(
                "scripts.update_release._restore_snapshot",
                side_effect=interrupt_then_restore,
            ),
            self.assertRaises(KeyboardInterrupt) as raised,
        ):
            update_release.rollback_latest(project)

        self.assertIs(raised.exception, interrupt)
        self.assertEqual((project / "state.txt").read_text(encoding="utf-8"), "new")
        self.assertEqual((project / "VERSION").read_text().strip(), "v1.1.0")
        self.assertFalse((project / "partial.txt").exists())
        backup_root = project / update_release.BACKUP_DIRECTORY_NAME
        self.assertEqual(
            sorted(path.name for path in backup_root.iterdir()),
            [update_release.LATEST_BACKUP_NAME],
        )

    def test_rollback_interrupt_preserves_safety_snapshot_when_compensation_is_interrupted(
        self,
    ):
        project = self.root / "project-double-interrupted-rollback"
        project.mkdir()
        (project / "state.txt").write_text("old", encoding="utf-8")
        (project / "VERSION").write_text("v1.0.0\n", encoding="utf-8")
        update_release.create_latest_backup(project, "v1.0.0", "v1.1.0")
        (project / "state.txt").write_text("new", encoding="utf-8")
        (project / "VERSION").write_text("v1.1.0\n", encoding="utf-8")

        with (
            mock.patch(
                "scripts.update_release._restore_snapshot",
                side_effect=[KeyboardInterrupt(), KeyboardInterrupt()],
            ),
            self.assertRaisesRegex(update_release.UpdateError, "安全备份已保留"),
        ):
            update_release.rollback_latest(project)

        backup_root = project / update_release.BACKUP_DIRECTORY_NAME
        pending = [path for path in backup_root.iterdir() if path.name.startswith(".pending-")]
        self.assertEqual(len(pending), 1)
        self.assertEqual(
            (pending[0] / "project" / "state.txt").read_text(encoding="utf-8"),
            "new",
        )

    def test_successful_rollback_removes_all_stale_complete_backups(self):
        project = self.root / "project"
        project.mkdir()
        (project / "state.txt").write_text("old", encoding="utf-8")
        (project / "VERSION").write_text("v1.0.0\n", encoding="utf-8")
        update_release.create_latest_backup(project, "v1.0.0", "v1.1.0")
        (project / "state.txt").write_text("new", encoding="utf-8")
        (project / "VERSION").write_text("v1.1.0\n", encoding="utf-8")
        backup_root = project / update_release.BACKUP_DIRECTORY_NAME
        for name in (".pending-stale", ".previous"):
            stale_snapshot = backup_root / name / "project"
            stale_snapshot.mkdir(parents=True)
            (stale_snapshot / "state.txt").write_text("stale", encoding="utf-8")

        update_release.rollback_latest(project)

        self.assertEqual(
            sorted(path.name for path in backup_root.iterdir()),
            [update_release.LATEST_BACKUP_NAME],
        )

    def test_rollback_cleanup_failure_after_commit_returns_warning(self):
        project = self.root / "project"
        project.mkdir()
        (project / "state.txt").write_text("old", encoding="utf-8")
        (project / "VERSION").write_text("v1.0.0\n", encoding="utf-8")
        update_release.create_latest_backup(project, "v1.0.0", "v1.1.0")
        (project / "state.txt").write_text("new", encoding="utf-8")
        (project / "VERSION").write_text("v1.1.0\n", encoding="utf-8")
        original_remove = update_release._remove_path

        def fail_committed_cleanup(path):
            if path.name == ".rollback-source":
                raise PermissionError("denied")
            return original_remove(path)

        with mock.patch(
            "scripts.update_release._remove_path",
            side_effect=fail_committed_cleanup,
        ):
            result = update_release.rollback_latest(project)

        self.assertEqual((project / "state.txt").read_text(encoding="utf-8"), "old")
        latest_project = (
            project / update_release.BACKUP_DIRECTORY_NAME / "latest" / "project"
        )
        self.assertEqual(
            (latest_project / "state.txt").read_text(encoding="utf-8"),
            "new",
        )
        self.assertEqual(len(result.cleanup_warnings), 1)
        self.assertIn(".rollback-source", result.cleanup_warnings[0])

    def test_rollback_stale_cleanup_failure_aborts_before_project_change(self):
        project = self.root / "project"
        project.mkdir()
        (project / "state.txt").write_text("old", encoding="utf-8")
        (project / "VERSION").write_text("v1.0.0\n", encoding="utf-8")
        update_release.create_latest_backup(project, "v1.0.0", "v1.1.0")
        (project / "state.txt").write_text("new", encoding="utf-8")
        (project / "VERSION").write_text("v1.1.0\n", encoding="utf-8")
        backup_root = project / update_release.BACKUP_DIRECTORY_NAME
        stale = backup_root / ".previous"
        stale.mkdir()
        original_remove = update_release._remove_path

        def fail_stale_cleanup(path):
            if path == stale:
                raise PermissionError("denied")
            return original_remove(path)

        with (
            mock.patch(
                "scripts.update_release._remove_path",
                side_effect=fail_stale_cleanup,
            ),
            self.assertRaisesRegex(update_release.UpdateError, "回滚尚未开始"),
        ):
            update_release.rollback_latest(project)

        self.assertEqual((project / "state.txt").read_text(encoding="utf-8"), "new")
        self.assertEqual(
            (backup_root / "latest" / "project" / "state.txt").read_text(
                encoding="utf-8"
            ),
            "old",
        )

    def test_update_project_preserves_runtime_data_and_rebuilds_environment(self):
        project = self.root / "project"
        project.mkdir()
        self._install_release_tree(project, "v1.0.0")
        (project / "data").mkdir()
        (project / "data" / "db").write_text("runtime", encoding="utf-8")
        (project / ".env").write_text("SECRET=value", encoding="utf-8")
        (project / "custom.txt").write_text("custom", encoding="utf-8")
        (project / "venv" / "bin").mkdir(parents=True)
        (project / "venv" / "bin" / "old").write_text("old", encoding="utf-8")
        archive = self._write_zip("codebuddy2api-v1.1.0.zip", self._release_files("v1.1.0"))

        def fake_run(command, check):
            self.assertTrue(check)
            if command[1:3] == ["-m", "venv"]:
                python_path = project / "venv" / "bin" / "python3"
                python_path.parent.mkdir(parents=True)
                python_path.write_text("new", encoding="utf-8")
            return mock.Mock(returncode=0)

        with mock.patch("scripts.update_release.subprocess.run", side_effect=fake_run):
            old, new, backup = update_release.update_project(
                project,
                release_file=archive,
            )

        self.assertEqual((old, new), ("v1.0.0", "v1.1.0"))
        self.assertEqual((project / "VERSION").read_text().strip(), "v1.1.0")
        self.assertEqual((project / "data" / "db").read_text(), "runtime")
        self.assertEqual((project / ".env").read_text(), "SECRET=value")
        self.assertEqual((project / "custom.txt").read_text(), "custom")
        self.assertEqual((project / "venv" / "bin" / "python3").read_text(), "new")
        self.assertEqual((backup / "project" / "VERSION").read_text().strip(), "v1.0.0")
        self.assertTrue((backup / "project" / "venv" / "bin" / "old").is_file())

    def test_update_failure_restores_exact_previous_project(self):
        project = self.root / "project"
        project.mkdir()
        self._install_release_tree(project, "v1.0.0")
        (project / "state.txt").write_text("original", encoding="utf-8")
        archive = self._write_zip("codebuddy2api-v1.1.0.zip", self._release_files("v1.1.0"))

        def fail_deploy(project_root, _current, _staged, *, reuse_venv=False):
            self.assertFalse(reuse_venv)
            (project_root / "state.txt").write_text("damaged", encoding="utf-8")
            (project_root / "partial.txt").write_text("partial", encoding="utf-8")
            raise update_release.UpdateError("simulated")

        with mock.patch("scripts.update_release._deploy_release", side_effect=fail_deploy):
            with self.assertRaisesRegex(update_release.UpdateError, "simulated"):
                update_release.update_project(project, release_file=archive)

        self.assertEqual((project / "state.txt").read_text(), "original")
        self.assertFalse((project / "partial.txt").exists())
        self.assertEqual((project / "VERSION").read_text().strip(), "v1.0.0")

    def test_update_interrupt_restores_snapshot_and_propagates(self):
        project = self.root / "project-interrupted-update"
        project.mkdir()
        self._install_release_tree(project, "v1.0.0")
        (project / "state.txt").write_text("original", encoding="utf-8")
        venv_marker = project / "venv" / "environment.txt"
        venv_marker.parent.mkdir()
        venv_marker.write_text("old-environment", encoding="utf-8")
        archive = self._write_zip(
            "codebuddy2api-interrupted-update.zip",
            self._release_files("v1.1.0"),
        )
        interrupt = KeyboardInterrupt()

        def interrupt_deploy(project_root, _current, _staged, *, reuse_venv=False):
            self.assertFalse(reuse_venv)
            (project_root / "state.txt").write_text("partial", encoding="utf-8")
            shutil.rmtree(project_root / "venv")
            (project_root / "partial.txt").write_text("partial", encoding="utf-8")
            raise interrupt

        with (
            mock.patch(
                "scripts.update_release._deploy_release",
                side_effect=interrupt_deploy,
            ),
            self.assertRaises(KeyboardInterrupt) as raised,
        ):
            update_release.update_project(project, release_file=archive)

        self.assertIs(raised.exception, interrupt)
        self.assertEqual((project / "state.txt").read_text(), "original")
        self.assertEqual(venv_marker.read_text(), "old-environment")
        self.assertFalse((project / "partial.txt").exists())
        self.assertEqual((project / "VERSION").read_text().strip(), "v1.0.0")

    def test_update_interrupt_reports_failed_compensation_as_update_error(self):
        project = self.root / "project-interrupted-compensation"
        project.mkdir()
        self._install_release_tree(project, "v1.0.0")
        archive = self._write_zip(
            "codebuddy2api-interrupted-compensation.zip",
            self._release_files("v1.1.0"),
        )

        with (
            mock.patch(
                "scripts.update_release._deploy_release",
                side_effect=KeyboardInterrupt(),
            ),
            mock.patch(
                "scripts.update_release._restore_snapshot",
                side_effect=KeyboardInterrupt(),
            ),
            self.assertRaisesRegex(update_release.UpdateError, "自动恢复失败"),
        ):
            update_release.update_project(project, release_file=archive)

        backup = (
            project
            / update_release.BACKUP_DIRECTORY_NAME
            / update_release.LATEST_BACKUP_NAME
            / "project"
        )
        self.assertEqual((backup / "VERSION").read_text().strip(), "v1.0.0")

    def test_reuse_venv_synchronizes_dependency_closure_and_removes_extras(self):
        project = self.root / "project-reuse-venv"
        venv = project / "venv"
        venv_python = venv / "bin" / "python3"
        venv_python.parent.mkdir(parents=True)
        venv_python.touch()
        (venv / "pyvenv.cfg").write_text(
            "home = /usr/bin\ninclude-system-site-packages = false\n",
            encoding="utf-8",
        )
        (project / "requirements.txt").write_text("fastapi==1.0\n", encoding="utf-8")
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            if "-c" in command:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps(
                        {
                            "prefix": str(venv.resolve()),
                            "version": [3, 12],
                            "pip_version": "24.3.1",
                        }
                    ),
                )
            if "--report" in command:
                report = Path(command[command.index("--report") + 1])
                report.write_text(
                    json.dumps(
                        {
                            "version": "1",
                            "install": [
                                {"metadata": {"name": "FastAPI"}},
                                {"metadata": {"name": "AnyIO"}},
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0)
            if "list" in command:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps(
                        [
                            {"name": "FastAPI"},
                            {"name": "anyio"},
                            {"name": "Old_Dep"},
                            {"name": "pip"},
                            {"name": "setuptools"},
                        ]
                    ),
                )
            return subprocess.CompletedProcess(command, 0)

        with mock.patch("scripts.update_release.subprocess.run", side_effect=fake_run):
            removed = update_release._synchronize_virtual_environment(project)

        self.assertEqual(removed, ("old-dep",))
        self.assertTrue(
            any(
                command[4:8] == [
                    "--dry-run",
                    "--ignore-installed",
                    "--quiet",
                    "--report",
                ]
                for command in commands
            )
        )
        self.assertIn(
            [
                str(venv_python),
                "-m",
                "pip",
                "install",
                "--upgrade",
                "--upgrade-strategy",
                "eager",
                "-r",
                str(project / "requirements.txt"),
            ],
            commands,
        )
        self.assertIn(
            [str(venv_python), "-m", "pip", "uninstall", "-y", "old-dep"],
            commands,
        )
        self.assertEqual(commands[-1], [str(venv_python), "-m", "pip", "check"])

    def test_reuse_venv_upgrades_legacy_pip_before_generating_report(self):
        project = self.root / "project-legacy-pip"
        venv = project / "venv"
        venv_python = venv / "bin" / "python3"
        venv_python.parent.mkdir(parents=True)
        venv_python.touch()
        (venv / "pyvenv.cfg").write_text(
            "home = /usr/bin\ninclude-system-site-packages = false\n",
            encoding="utf-8",
        )
        (project / "requirements.txt").write_text("fastapi==1.0\n", encoding="utf-8")
        commands = []
        inspection_count = 0

        def fake_run(command, **kwargs):
            nonlocal inspection_count
            commands.append(command)
            if "-c" in command:
                inspection_count += 1
                pip_version = "21.3.1" if inspection_count == 1 else "23.0"
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps(
                        {
                            "prefix": str(venv.resolve()),
                            "version": [3, 10],
                            "pip_version": pip_version,
                        }
                    ),
                )
            if "--report" in command:
                report = Path(command[command.index("--report") + 1])
                report.write_text(
                    json.dumps(
                        {
                            "version": "1",
                            "install": [{"metadata": {"name": "FastAPI"}}],
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0)
            if "list" in command:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps([{"name": "fastapi"}, {"name": "pip"}]),
                )
            return subprocess.CompletedProcess(command, 0)

        with mock.patch("scripts.update_release.subprocess.run", side_effect=fake_run):
            update_release._synchronize_virtual_environment(project)

        upgrade = [
            str(venv_python),
            "-m",
            "pip",
            "install",
            "--upgrade",
            "pip>=23.0",
        ]
        self.assertIn(upgrade, commands)
        report_index = next(
            index for index, command in enumerate(commands) if "--report" in command
        )
        self.assertLess(commands.index(upgrade), report_index)
        self.assertEqual(inspection_count, 2)

    def test_reuse_venv_rejects_failed_pip_upgrade_and_unknown_report_version(self):
        project = self.root / "project-pip-upgrade-failure"
        venv = project / "venv"
        venv_python = venv / "bin" / "python3"
        venv_python.parent.mkdir(parents=True)
        venv_python.touch()
        (venv / "pyvenv.cfg").write_text(
            "home = /usr/bin\ninclude-system-site-packages = false\n",
            encoding="utf-8",
        )
        (project / "requirements.txt").write_text("fastapi==1.0\n", encoding="utf-8")

        def unchanged_legacy_pip(command, **kwargs):
            if "-c" in command:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps(
                        {
                            "prefix": str(venv.resolve()),
                            "version": [3, 10],
                            "pip_version": "21.3.1",
                        }
                    ),
                )
            return subprocess.CompletedProcess(command, 0)

        with (
            mock.patch(
                "scripts.update_release.subprocess.run",
                side_effect=unchanged_legacy_pip,
            ),
            self.assertRaisesRegex(update_release.UpdateError, "pip.*23.0"),
        ):
            update_release._synchronize_virtual_environment(project)

        report = self.root / "unsupported-report.json"
        report.write_text(
            json.dumps(
                {
                    "version": "2",
                    "install": [{"metadata": {"name": "FastAPI"}}],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(update_release.UpdateError, "报告版本"):
            update_release._desired_distribution_names(report)

    def test_reuse_venv_rejects_invalid_environment_and_pip_report(self):
        project = self.root / "project-invalid-venv"
        project.mkdir()
        (project / "requirements.txt").write_text("fastapi==1.0\n", encoding="utf-8")

        with self.assertRaisesRegex(update_release.UpdateError, "旧虚拟环境"):
            update_release._synchronize_virtual_environment(project)

        venv = project / "venv"
        venv_python = venv / "bin" / "python3"
        venv_python.parent.mkdir(parents=True)
        venv_python.touch()
        (venv / "pyvenv.cfg").write_text(
            "home = /usr/bin\ninclude-system-site-packages = false\n",
            encoding="utf-8",
        )

        def malformed_report(command, **kwargs):
            if "-c" in command:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps(
                        {
                            "prefix": str(venv.resolve()),
                            "version": [3, 12],
                            "pip_version": "24.3.1",
                        }
                    ),
                )
            report = Path(command[command.index("--report") + 1])
            report.write_text(
                json.dumps({"install": [{"metadata": {}}]}),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0)

        with (
            mock.patch(
                "scripts.update_release.subprocess.run",
                side_effect=malformed_report,
            ),
            self.assertRaisesRegex(update_release.UpdateError, "依赖解析报告"),
        ):
            update_release._synchronize_virtual_environment(project)

    def test_reuse_venv_rejects_system_site_packages_before_running_python(self):
        for index, configured_value in enumerate(("true", " TRUE ")):
            with self.subTest(configured_value=configured_value):
                project = self.root / f"project-system-site-{index}"
                venv = project / "venv"
                venv_python = update_release._venv_python_path(venv)
                venv_python.parent.mkdir(parents=True)
                venv_python.touch()
                (venv / "pyvenv.cfg").write_text(
                    "home = /usr/bin\n"
                    f" Include-System-Site-Packages = {configured_value}\n",
                    encoding="utf-8",
                )

                with (
                    mock.patch("scripts.update_release.subprocess.run") as run,
                    self.assertRaisesRegex(
                        update_release.UpdateError,
                        "系统 site-packages",
                    ),
                ):
                    update_release._inspect_reusable_venv(project)

                run.assert_not_called()

    def test_reuse_venv_rejects_ambiguous_system_site_packages_configuration(self):
        configurations = {
            "missing": "home = /usr/bin\n",
            "duplicate": (
                "include-system-site-packages = false\n"
                "include-system-site-packages = false\n"
            ),
            "invalid": "include-system-site-packages = yes\n",
        }
        for name, content in configurations.items():
            with self.subTest(name=name):
                project = self.root / f"project-system-site-{name}"
                venv = project / "venv"
                venv_python = update_release._venv_python_path(venv)
                venv_python.parent.mkdir(parents=True)
                venv_python.touch()
                (venv / "pyvenv.cfg").write_text(content, encoding="utf-8")

                with (
                    mock.patch("scripts.update_release.subprocess.run") as run,
                    self.assertRaisesRegex(
                        update_release.UpdateError,
                        "include-system-site-packages",
                    ),
                ):
                    update_release._inspect_reusable_venv(project)

                run.assert_not_called()

    def test_system_site_packages_rejection_restores_update_snapshot(self):
        project = self.root / "project-system-site-restore"
        project.mkdir()
        self._install_release_tree(project, "v1.0.0")
        venv = project / "venv"
        venv_python = update_release._venv_python_path(venv)
        venv_python.parent.mkdir(parents=True)
        venv_python.touch()
        (venv / "pyvenv.cfg").write_text(
            "include-system-site-packages = true\n",
            encoding="utf-8",
        )
        marker = venv / "environment.txt"
        marker.write_text("old-environment", encoding="utf-8")
        archive = self._write_zip(
            "codebuddy2api-system-site-restore.zip",
            self._release_files("v1.1.0"),
        )

        with self.assertRaisesRegex(update_release.UpdateError, "系统 site-packages"):
            update_release.update_project(
                project,
                release_file=archive,
                reuse_venv=True,
            )

        self.assertEqual(marker.read_text(encoding="utf-8"), "old-environment")
        self.assertEqual((project / "VERSION").read_text().strip(), "v1.0.0")

    def test_update_project_passes_reuse_venv_to_deployment(self):
        project = self.root / "project-reuse-deploy"
        project.mkdir()
        self._install_release_tree(project, "v1.0.0")
        archive = self._write_zip(
            "codebuddy2api-reuse-deploy.zip",
            self._release_files("v1.1.0"),
        )

        with mock.patch("scripts.update_release._deploy_release") as deploy:
            update_release.update_project(
                project,
                release_file=archive,
                reuse_venv=True,
            )

        self.assertTrue(deploy.call_args.kwargs["reuse_venv"])

    def test_reuse_venv_failure_restores_original_environment(self):
        project = self.root / "project-reuse-restore"
        project.mkdir()
        self._install_release_tree(project, "v1.0.0")
        marker = project / "venv" / "environment.txt"
        marker.parent.mkdir()
        marker.write_text("old-environment", encoding="utf-8")
        archive = self._write_zip(
            "codebuddy2api-reuse-restore.zip",
            self._release_files("v1.1.0"),
        )

        def fail_synchronization(project_root):
            (project_root / "venv" / "environment.txt").write_text(
                "partial-environment",
                encoding="utf-8",
            )
            raise update_release.UpdateError("依赖同步失败")

        with (
            mock.patch(
                "scripts.update_release._synchronize_virtual_environment",
                side_effect=fail_synchronization,
            ),
            self.assertRaisesRegex(update_release.UpdateError, "依赖同步失败"),
        ):
            update_release.update_project(
                project,
                release_file=archive,
                reuse_venv=True,
            )

        self.assertEqual(marker.read_text(encoding="utf-8"), "old-environment")
        self.assertEqual((project / "VERSION").read_text().strip(), "v1.0.0")

    def test_update_allows_managed_file_to_become_directory(self):
        project = self.root / "project-file-to-directory"
        project.mkdir()
        self._install_release_tree(project, "v1.0.0")
        current_manifest_path = project / update_release.MANIFEST_FILENAME
        current_manifest = json.loads(current_manifest_path.read_text(encoding="utf-8"))
        current_manifest["files"] = sorted([*current_manifest["files"], "config"])
        current_manifest_path.write_text(
            json.dumps(current_manifest, sort_keys=True),
            encoding="utf-8",
        )
        (project / "config").write_text("old-config", encoding="utf-8")

        files = self._release_files("v1.1.0")
        staged_manifest = json.loads(files[update_release.MANIFEST_FILENAME])
        staged_manifest["files"] = sorted(
            [*staged_manifest["files"], "config/settings.json"]
        )
        files[update_release.MANIFEST_FILENAME] = json.dumps(
            staged_manifest,
            sort_keys=True,
        )
        files["config/settings.json"] = "new-config"
        archive = self._write_zip("codebuddy2api-file-to-directory.zip", files)

        with mock.patch("scripts.update_release.subprocess.run"):
            update_release.update_project(project, release_file=archive)

        self.assertTrue((project / "config").is_dir())
        self.assertEqual(
            (project / "config" / "settings.json").read_text(encoding="utf-8"),
            "new-config",
        )

    def test_deploy_rejects_unmanaged_file_as_new_managed_parent(self):
        project = self.root / "project-unmanaged-parent"
        project.mkdir()
        self._install_release_tree(project, "v1.0.0")
        current_manifest = update_release._ensure_safe_installation(project)
        (project / "custom").write_text("user-data", encoding="utf-8")
        files = self._release_files("v1.1.0")
        manifest = json.loads(files[update_release.MANIFEST_FILENAME])
        manifest["files"] = sorted([*manifest["files"], "custom/settings.json"])
        files[update_release.MANIFEST_FILENAME] = json.dumps(manifest, sort_keys=True)
        files["custom/settings.json"] = "release-value"
        archive = self._write_zip("codebuddy2api-unmanaged-parent.zip", files)
        staged = update_release.stage_local_release(
            archive,
            self.root / "staging-unmanaged-parent",
        )

        with self.assertRaisesRegex(update_release.UpdateError, "类型无效"):
            update_release._deploy_release(project, current_manifest, staged)

        self.assertEqual(
            (project / "custom").read_text(encoding="utf-8"),
            "user-data",
        )

    @unittest.skipUnless(hasattr(os, "symlink"), "平台不支持符号链接")
    def test_deploy_rejects_new_paths_with_symlink_conflicts_before_writing(self):
        for conflict_kind in ("target", "parent"):
            with self.subTest(conflict_kind=conflict_kind):
                project = self.root / f"project-{conflict_kind}"
                project.mkdir()
                self._install_release_tree(project, "v1.0.0")
                current_manifest = update_release._ensure_safe_installation(project)
                files = self._release_files("v1.1.0")
                manifest = json.loads(files[update_release.MANIFEST_FILENAME])
                managed_path = (
                    "new-config.txt"
                    if conflict_kind == "target"
                    else "new-config/settings.txt"
                )
                files[managed_path] = "release-value"
                manifest["files"] = sorted([*manifest["files"], managed_path])
                files[update_release.MANIFEST_FILENAME] = json.dumps(
                    manifest,
                    sort_keys=True,
                )
                archive = self._write_zip(
                    f"codebuddy2api-{conflict_kind}.zip",
                    files,
                )
                staging = self.root / f"staging-{conflict_kind}"
                staged = update_release.stage_local_release(archive, staging)
                external = self.root / f"external-{conflict_kind}"
                if conflict_kind == "target":
                    external.write_text("external", encoding="utf-8")
                    (project / managed_path).symlink_to(external)
                else:
                    external.mkdir()
                    (external / "settings.txt").write_text(
                        "external",
                        encoding="utf-8",
                    )
                    (project / "new-config").symlink_to(
                        external,
                        target_is_directory=True,
                    )

                with self.assertRaisesRegex(update_release.UpdateError, "符号链接"):
                    update_release._deploy_release(project, current_manifest, staged)

                external_file = (
                    external
                    if conflict_kind == "target"
                    else external / "settings.txt"
                )
                self.assertEqual(external_file.read_text(encoding="utf-8"), "external")
                self.assertEqual(
                    (project / "README.md").read_text(encoding="utf-8"),
                    "readme",
                )

    def test_update_rejects_git_checkout_and_project_virtual_environment(self):
        project = self.root / "project"
        project.mkdir()
        (project / ".git").mkdir()
        with self.assertRaisesRegex(update_release.UpdateError, "Git"):
            update_release._ensure_execution_context(project)

        (project / ".git").rmdir()
        executable = project / "venv" / "bin" / "python3"
        executable.parent.mkdir(parents=True)
        executable.touch()
        with mock.patch("scripts.update_release.sys.executable", str(executable)):
            with self.assertRaisesRegex(update_release.UpdateError, "虚拟环境"):
                update_release._ensure_execution_context(project)

    @unittest.skipUnless(hasattr(os, "symlink"), "平台不支持符号链接")
    def test_update_rejects_symlinked_project_virtual_environment_interpreter(self):
        project = self.root / "project-symlinked-venv"
        executable = project / "venv" / "bin" / "python3"
        executable.parent.mkdir(parents=True)
        system_python = self.root / "system" / "python3"
        system_python.parent.mkdir()
        system_python.touch()
        executable.symlink_to(system_python)

        with (
            mock.patch("scripts.update_release.sys.executable", str(executable)),
            mock.patch("scripts.update_release.sys.prefix", str(project / "venv")),
            mock.patch(
                "scripts.update_release.sys.base_prefix",
                str(system_python.parent),
            ),
            self.assertRaisesRegex(update_release.UpdateError, "虚拟环境"),
        ):
            update_release._ensure_execution_context(project)

    @unittest.skipUnless(hasattr(os, "symlink"), "平台不支持符号链接")
    def test_update_rejects_external_symlink_to_project_interpreter(self):
        project = self.root / "project-external-interpreter-link"
        project_python = project / "venv" / "bin" / "python3"
        project_python.parent.mkdir(parents=True)
        project_python.touch()
        external_python = self.root / "external-bin" / "python3"
        external_python.parent.mkdir()
        external_python.symlink_to(project_python)
        system_prefix = self.root / "system-prefix"

        with (
            mock.patch("scripts.update_release.sys.executable", str(external_python)),
            mock.patch("scripts.update_release.sys.prefix", str(system_prefix)),
            mock.patch("scripts.update_release.sys.base_prefix", str(system_prefix)),
            self.assertRaisesRegex(update_release.UpdateError, "虚拟环境"),
        ):
            update_release._ensure_execution_context(project)

    @unittest.skipUnless(hasattr(os, "symlink"), "平台不支持符号链接")
    def test_update_rejects_external_symlink_to_project_virtual_environment_prefix(
        self,
    ):
        project = self.root / "project-external-prefix-link"
        project_venv = project / "venv"
        project_venv.mkdir(parents=True)
        external_prefix = self.root / "external-venv"
        external_prefix.symlink_to(project_venv, target_is_directory=True)
        system_prefix = self.root / "system-prefix"
        executable = system_prefix / "bin" / "python3"
        executable.parent.mkdir(parents=True)
        executable.touch()

        with (
            mock.patch("scripts.update_release.sys.executable", str(executable)),
            mock.patch("scripts.update_release.sys.prefix", str(external_prefix)),
            mock.patch("scripts.update_release.sys.base_prefix", str(system_prefix)),
            self.assertRaisesRegex(update_release.UpdateError, "虚拟环境"),
        ):
            update_release._ensure_execution_context(project)

    def test_update_rejects_project_virtual_environment_prefix(self):
        project = self.root / "project-venv-prefix"
        project.mkdir()
        system_prefix = self.root / "system-prefix-for-venv"
        executable = system_prefix / "bin" / "python3"
        executable.parent.mkdir(parents=True)
        executable.touch()

        with (
            mock.patch("scripts.update_release.sys.executable", str(executable)),
            mock.patch("scripts.update_release.sys.prefix", str(project / "venv")),
            mock.patch("scripts.update_release.sys.base_prefix", str(system_prefix)),
            self.assertRaisesRegex(update_release.UpdateError, "虚拟环境"),
        ):
            update_release._ensure_execution_context(project)

    def test_update_allows_project_external_system_python(self):
        project = self.root / "project-external-python"
        project.mkdir()
        system_prefix = self.root / "system-prefix"
        executable = system_prefix / "bin" / "python3"
        executable.parent.mkdir(parents=True)
        executable.touch()

        with (
            mock.patch("scripts.update_release.sys.executable", str(executable)),
            mock.patch("scripts.update_release.sys.prefix", str(system_prefix)),
            mock.patch("scripts.update_release.sys.base_prefix", str(system_prefix)),
        ):
            update_release._ensure_execution_context(project)

    def test_cli_replaces_stopped_confirmation_with_yes_option(self):
        parser = update_release._build_parser()
        update_args = parser.parse_args(["update", "--yes", "--reuse-venv"])
        rollback_args = parser.parse_args(["rollback", "-y"])

        self.assertTrue(update_args.yes)
        self.assertTrue(update_args.reuse_venv)
        self.assertTrue(rollback_args.yes)
        with (
            mock.patch("sys.stderr", new=io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            parser.parse_args(["update", "--confirm-stopped"])

    def test_main_rejects_noninteractive_use_as_command_line_error(self):
        with (
            mock.patch.object(sys, "argv", ["update_release.py", "rollback"]),
            mock.patch.object(update_release.sys.stdin, "isatty", return_value=False),
            mock.patch("sys.stderr", new=io.StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            update_release.main()

        self.assertEqual(raised.exception.code, 2)

    def test_main_reports_post_commit_cleanup_warning_as_rollback_success(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        result = update_release.RollbackResult(
            cleanup_warnings=("无法删除 .rollback-source：denied",)
        )
        with (
            mock.patch.object(sys, "argv", ["update_release.py", "rollback", "--yes"]),
            mock.patch.object(update_release, "resolve_project_root", return_value=self.root),
            mock.patch.object(update_release, "_run_cli_operation", return_value=result),
            mock.patch("sys.stdout", new=stdout),
            mock.patch("sys.stderr", new=stderr),
        ):
            update_release.main()

        self.assertIn("回滚完成", stdout.getvalue())
        self.assertIn("回滚已经成功", stderr.getvalue())
        self.assertIn("不要重新执行回滚", stderr.getvalue())

    def test_main_passes_reuse_venv_to_update(self):
        stdout = io.StringIO()
        lock = mock.MagicMock()
        lock.__enter__.return_value = lock
        with (
            mock.patch.object(
                sys,
                "argv",
                ["update_release.py", "update", "--yes", "--reuse-venv"],
            ),
            mock.patch.object(update_release, "resolve_project_root", return_value=self.root),
            mock.patch.object(update_release, "acquire_runtime_lock", return_value=lock),
            mock.patch.object(
                update_release,
                "update_project",
                return_value=("v1.0.0", "v1.1.0", self.root / "backup"),
            ) as update,
            mock.patch("sys.stdout", new=stdout),
        ):
            update_release.main()

        self.assertTrue(update.call_args.kwargs["reuse_venv"])
        self.assertIn("更新完成", stdout.getvalue())

    def test_interactive_lock_waits_for_stop_then_confirms(self):
        project = self.root / "project"
        project.mkdir()
        lock = mock.MagicMock()
        lock.__enter__.return_value = lock
        acquire = mock.Mock(
            side_effect=[release_runtime_lock.RuntimeLockBusy("busy"), lock]
        )
        operation = mock.Mock(return_value="done")

        with (
            mock.patch.object(update_release, "acquire_runtime_lock", acquire),
            mock.patch.object(update_release.sys.stdin, "isatty", return_value=True),
            mock.patch("builtins.input", side_effect=["", "y"]),
        ):
            result = update_release._run_cli_operation(
                project,
                action="更新",
                assume_yes=False,
                operation=operation,
            )

        self.assertEqual(result, "done")
        self.assertEqual(acquire.call_count, 2)
        operation.assert_called_once_with()

    def test_cli_cancellation_and_noninteractive_rules(self):
        project = self.root / "project"
        project.mkdir()
        lock = mock.MagicMock()
        lock.__enter__.return_value = lock

        with (
            mock.patch.object(update_release, "acquire_runtime_lock", return_value=lock),
            mock.patch.object(update_release.sys.stdin, "isatty", return_value=True),
            mock.patch("builtins.input", return_value="n"),
            self.assertRaises(update_release.UpdateCancelled),
        ):
            update_release._run_cli_operation(
                project,
                action="更新",
                assume_yes=False,
                operation=mock.Mock(),
            )

        with (
            mock.patch.object(update_release.sys.stdin, "isatty", return_value=False),
            self.assertRaisesRegex(update_release.UpdateError, "非交互"),
        ):
            update_release._run_cli_operation(
                project,
                action="更新",
                assume_yes=False,
                operation=mock.Mock(),
            )

    def test_yes_skips_prompt_but_never_bypasses_busy_lock(self):
        project = self.root / "project"
        project.mkdir()
        operation = mock.Mock(return_value="done")
        lock = mock.MagicMock()
        lock.__enter__.return_value = lock

        with (
            mock.patch.object(update_release, "acquire_runtime_lock", return_value=lock),
            mock.patch("builtins.input") as prompt,
        ):
            self.assertEqual(
                update_release._run_cli_operation(
                    project,
                    action="更新",
                    assume_yes=True,
                    operation=operation,
                ),
                "done",
            )
        prompt.assert_not_called()

        with (
            mock.patch.object(
                update_release,
                "acquire_runtime_lock",
                side_effect=release_runtime_lock.RuntimeLockBusy("busy"),
            ),
            self.assertRaisesRegex(update_release.UpdateError, "运行"),
        ):
            update_release._run_cli_operation(
                project,
                action="更新",
                assume_yes=True,
                operation=operation,
            )

    def test_cli_holds_real_lock_for_entire_operation(self):
        project = self.root / "project"
        project.mkdir()
        self._install_release_tree(project, "v1.0.0")

        def assert_locked():
            with self.assertRaises(release_runtime_lock.RuntimeLockBusy):
                release_runtime_lock.acquire_runtime_lock(
                    project,
                    required=True,
                    purpose="服务",
                )
            return "locked"

        self.assertEqual(
            update_release._run_cli_operation(
                project,
                action="更新",
                assume_yes=True,
                operation=assert_locked,
            ),
            "locked",
        )

        lock = release_runtime_lock.acquire_runtime_lock(
            project,
            required=True,
            purpose="服务",
        )
        lock.close()

    def test_interactive_eof_and_keyboard_interrupt_are_not_accepted(self):
        project = self.root / "project"
        project.mkdir()
        lock = mock.MagicMock()
        lock.__enter__.return_value = lock

        for error in (EOFError(), KeyboardInterrupt()):
            expected = (
                update_release.UpdateError
                if isinstance(error, EOFError)
                else KeyboardInterrupt
            )
            with (
                self.subTest(error=type(error).__name__),
                mock.patch.object(
                    update_release,
                    "acquire_runtime_lock",
                    return_value=lock,
                ),
                mock.patch.object(update_release.sys.stdin, "isatty", return_value=True),
                mock.patch("builtins.input", side_effect=error),
                self.assertRaises(expected),
            ):
                update_release._run_cli_operation(
                    project,
                    action="更新",
                    assume_yes=False,
                    operation=mock.Mock(),
                )

    def test_rollback_can_recover_installation_with_missing_version_file(self):
        project = self.root / "project"
        project.mkdir()
        (project / "VERSION").write_text("v1.0.0\n", encoding="utf-8")
        (project / update_release.MANIFEST_FILENAME).write_text(
            json.dumps({"schema_version": 1, "version": "v1.0.0"}),
            encoding="utf-8",
        )
        (project / release_runtime_lock.LOCK_FILENAME).write_bytes(b"\0")
        update_release.create_latest_backup(project, "v1.0.0", "v1.1.0")
        (project / "VERSION").unlink()
        (project / "partial.txt").write_text("partial", encoding="utf-8")

        update_release._run_cli_operation(
            project,
            action="回滚",
            assume_yes=True,
            operation=lambda: update_release.rollback_latest(project),
        )

        self.assertEqual((project / "VERSION").read_text().strip(), "v1.0.0")
        self.assertFalse((project / "partial.txt").exists())

    def test_remote_checksum_must_match(self):
        archive = self.root / "codebuddy2api.zip"
        archive.write_bytes(b"archive")
        digest = hashlib.sha256(b"archive").hexdigest()
        checksums = self.root / "SHA256SUMS.txt"
        checksums.write_text(f"{digest}  codebuddy2api.zip\n", encoding="utf-8")
        update_release._verify_remote_checksum(archive, checksums)

        archive.write_bytes(b"changed")
        with self.assertRaisesRegex(update_release.UpdateError, "SHA-256"):
            update_release._verify_remote_checksum(archive, checksums)

    @unittest.skipUnless(hasattr(os, "symlink"), "平台不支持符号链接")
    def test_backup_preserves_symbolic_links(self):
        project = self.root / "project"
        project.mkdir()
        (project / "target").write_text("target", encoding="utf-8")
        (project / "link").symlink_to("target")

        backup = update_release.create_latest_backup(project, "v1.0.0", "v1.1.0")

        self.assertTrue((backup / "project" / "link").is_symlink())
        self.assertEqual(os.readlink(backup / "project" / "link"), "target")

    @unittest.skipUnless(hasattr(os, "symlink"), "平台不支持符号链接")
    def test_installation_rejects_symlink_in_managed_parent_path(self):
        project = self.root / "project"
        project.mkdir()
        self._install_release_tree(project, "v1.0.0")
        external = self.root / "external-secrets"
        external.mkdir()
        (external / "users.txt.example").write_text("external", encoding="utf-8")
        shutil.rmtree(project / "secrets")
        (project / "secrets").symlink_to(external, target_is_directory=True)

        with self.assertRaisesRegex(update_release.UpdateError, "符号链接"):
            update_release._ensure_safe_installation(project)


if __name__ == "__main__":
    unittest.main()
