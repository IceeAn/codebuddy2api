import errno
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import release_runtime_lock


class ReleaseRuntimeLockTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _mark_release(self, version="v1.2.3"):
        (self.root / "VERSION").write_text(f"{version}\n", encoding="utf-8")
        (self.root / "RELEASE_MANIFEST.json").write_text(
            json.dumps({"schema_version": 1, "version": version}),
            encoding="utf-8",
        )

    def test_detects_release_but_skips_git_and_unmarked_directories(self):
        self.assertFalse(release_runtime_lock.is_release_installation(self.root))

        self._mark_release()
        self.assertTrue(release_runtime_lock.is_release_installation(self.root))

        (self.root / ".git").mkdir()
        self.assertFalse(release_runtime_lock.is_release_installation(self.root))

    def test_existing_lock_keeps_release_mode_during_manifest_replacement(self):
        (self.root / release_runtime_lock.LOCK_FILENAME).write_bytes(b"\0")

        self.assertTrue(release_runtime_lock.is_release_installation(self.root))

        (self.root / "VERSION").write_text("v1.2.3\n", encoding="utf-8")
        with self.assertRaisesRegex(release_runtime_lock.RuntimeLockError, "不完整"):
            release_runtime_lock.is_release_installation(self.root)

    def test_rejects_partial_invalid_and_symbolic_release_markers(self):
        (self.root / "VERSION").write_text("v1.2.3\n", encoding="utf-8")
        with self.assertRaisesRegex(release_runtime_lock.RuntimeLockError, "标记"):
            release_runtime_lock.is_release_installation(self.root)

        (self.root / "RELEASE_MANIFEST.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(release_runtime_lock.RuntimeLockError, "标记"):
            release_runtime_lock.is_release_installation(self.root)

        (self.root / "RELEASE_MANIFEST.json").unlink()
        if hasattr(os, "symlink"):
            try:
                (self.root / "RELEASE_MANIFEST.json").symlink_to(
                    self.root / "VERSION"
                )
            except OSError:
                return
            with self.assertRaisesRegex(release_runtime_lock.RuntimeLockError, "普通文件"):
                release_runtime_lock.is_release_installation(self.root)

    def test_rejects_unreadable_and_unparseable_release_markers(self):
        marker = self.root / "VERSION"
        with (
            mock.patch.object(Path, "lstat", side_effect=OSError("unreadable")),
            self.assertRaisesRegex(release_runtime_lock.RuntimeLockError, "无法读取"),
        ):
            release_runtime_lock._require_regular_file(marker, "标记")

        self._mark_release()
        (self.root / "RELEASE_MANIFEST.json").write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(release_runtime_lock.RuntimeLockError, "无法解析"):
            release_runtime_lock.is_release_installation(self.root)

    def test_required_lock_rejects_non_release_directory(self):
        self.assertIsNone(
            release_runtime_lock.acquire_runtime_lock(
                self.root,
                required=False,
                purpose="服务",
            )
        )
        with self.assertRaisesRegex(release_runtime_lock.RuntimeLockError, "Release"):
            release_runtime_lock.acquire_runtime_lock(
                self.root,
                required=True,
                purpose="更新",
            )

    def test_exclusive_lock_blocks_competitors_and_releases_idempotently(self):
        self._mark_release()
        first = release_runtime_lock.acquire_runtime_lock(
            self.root,
            required=True,
            purpose="服务",
        )
        self.assertIsNotNone(first)
        self.assertEqual(first.purpose, "服务")
        if os.name == "posix":
            self.assertEqual(
                stat.S_IMODE(
                    (self.root / release_runtime_lock.LOCK_FILENAME).stat().st_mode
                ),
                0o600,
            )
        with self.assertRaises(release_runtime_lock.RuntimeLockBusy):
            release_runtime_lock.acquire_runtime_lock(
                self.root,
                required=True,
                purpose="更新",
            )

        first.close()
        first.close()
        with release_runtime_lock.acquire_runtime_lock(
            self.root,
            required=True,
            purpose="更新",
        ) as second:
            self.assertTrue(second.acquired)
        self.assertFalse(second.acquired)

    def test_context_manager_releases_after_operation_error(self):
        self._mark_release()
        with self.assertRaisesRegex(RuntimeError, "simulated"):
            with release_runtime_lock.acquire_runtime_lock(
                self.root,
                required=True,
                purpose="更新",
            ):
                raise RuntimeError("simulated")

        lock = release_runtime_lock.acquire_runtime_lock(
            self.root,
            required=True,
            purpose="更新",
        )
        lock.close()

    def test_other_process_blocks_and_process_exit_releases_lock(self):
        self._mark_release()
        repository_root = Path(__file__).resolve().parents[1]
        child_code = "\n".join(
            (
                "import sys",
                "from pathlib import Path",
                "from release_runtime_lock import acquire_runtime_lock",
                "lock = acquire_runtime_lock(Path(sys.argv[1]), required=True, purpose='服务')",
                "print('locked', flush=True)",
                "sys.stdin.read()",
            )
        )
        with subprocess.Popen(
            [sys.executable, "-c", child_code, str(self.root)],
            cwd=repository_root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as process:
            try:
                self.assertEqual(process.stdout.readline().strip(), "locked")
                with self.assertRaises(release_runtime_lock.RuntimeLockBusy):
                    release_runtime_lock.acquire_runtime_lock(
                        self.root,
                        required=True,
                        purpose="更新",
                    )
            finally:
                process.kill()
                process.communicate(timeout=10)

        lock = release_runtime_lock.acquire_runtime_lock(
            self.root,
            required=True,
            purpose="更新",
        )
        lock.close()

    def test_rejects_invalid_lock_file(self):
        self._mark_release()
        lock_path = self.root / release_runtime_lock.LOCK_FILENAME
        lock_path.mkdir()
        with self.assertRaisesRegex(release_runtime_lock.RuntimeLockError, "普通文件"):
            release_runtime_lock.acquire_runtime_lock(
                self.root,
                required=True,
                purpose="更新",
            )

        lock_path.rmdir()
        if hasattr(os, "symlink"):
            try:
                lock_path.symlink_to(self.root / "VERSION")
            except OSError:
                return
            with self.assertRaisesRegex(release_runtime_lock.RuntimeLockError, "符号链接"):
                release_runtime_lock.acquire_runtime_lock(
                    self.root,
                    required=True,
                    purpose="更新",
                )

    def test_lock_file_open_and_validation_failures_are_explicit(self):
        lock_path = self.root / release_runtime_lock.LOCK_FILENAME
        with (
            mock.patch.object(
                release_runtime_lock.os,
                "open",
                side_effect=OSError("denied"),
            ),
            self.assertRaisesRegex(release_runtime_lock.RuntimeLockError, "无法打开"),
        ):
            release_runtime_lock._open_lock_file(lock_path)

        with (
            mock.patch.object(release_runtime_lock.stat, "S_ISREG", return_value=False),
            self.assertRaisesRegex(release_runtime_lock.RuntimeLockError, "普通文件"),
        ):
            release_runtime_lock._open_lock_file(lock_path)

        with mock.patch.object(release_runtime_lock.os, "name", "nt"):
            descriptor = release_runtime_lock._open_lock_file(lock_path)
        os.close(descriptor)

    def test_posix_and_windows_lock_backends_map_busy_and_unlock(self):
        descriptor = 17
        fake_fcntl = SimpleNamespace(
            LOCK_EX=1,
            LOCK_NB=2,
            LOCK_UN=4,
            flock=mock.Mock(),
        )
        with (
            mock.patch.object(release_runtime_lock.os, "name", "posix"),
            mock.patch.dict(sys.modules, {"fcntl": fake_fcntl}),
        ):
            release_runtime_lock._lock_descriptor(descriptor)
            release_runtime_lock._unlock_descriptor(descriptor)
        fake_fcntl.flock.assert_has_calls(
            [mock.call(descriptor, 3), mock.call(descriptor, 4)]
        )

        fake_fcntl.flock.side_effect = OSError(errno.EAGAIN, "busy")
        with (
            mock.patch.object(release_runtime_lock.os, "name", "posix"),
            mock.patch.dict(sys.modules, {"fcntl": fake_fcntl}),
            self.assertRaises(release_runtime_lock.RuntimeLockBusy),
        ):
            release_runtime_lock._lock_descriptor(descriptor)

        fake_msvcrt = SimpleNamespace(
            LK_NBLCK=10,
            LK_UNLCK=11,
            locking=mock.Mock(),
        )
        with (
            mock.patch.object(release_runtime_lock.os, "name", "nt"),
            mock.patch.object(release_runtime_lock.os, "lseek") as seek,
            mock.patch.dict(sys.modules, {"msvcrt": fake_msvcrt}),
        ):
            release_runtime_lock._lock_descriptor(descriptor)
            release_runtime_lock._unlock_descriptor(descriptor)
        self.assertEqual(seek.call_args_list, [mock.call(descriptor, 0, os.SEEK_SET)] * 2)
        fake_msvcrt.locking.assert_has_calls(
            [mock.call(descriptor, 10, 1), mock.call(descriptor, 11, 1)]
        )

        fake_msvcrt.locking.side_effect = OSError(errno.EACCES, "busy")
        with (
            mock.patch.object(release_runtime_lock.os, "name", "nt"),
            mock.patch.object(release_runtime_lock.os, "lseek"),
            mock.patch.dict(sys.modules, {"msvcrt": fake_msvcrt}),
            self.assertRaises(release_runtime_lock.RuntimeLockBusy),
        ):
            release_runtime_lock._lock_descriptor(descriptor)

    def test_backend_rejects_unexpected_errors_and_platforms(self):
        fake_fcntl = SimpleNamespace(
            LOCK_EX=1,
            LOCK_NB=2,
            LOCK_UN=4,
            flock=mock.Mock(side_effect=OSError(errno.EIO, "io")),
        )
        with (
            mock.patch.object(release_runtime_lock.os, "name", "posix"),
            mock.patch.dict(sys.modules, {"fcntl": fake_fcntl}),
            self.assertRaisesRegex(release_runtime_lock.RuntimeLockError, "加锁"),
        ):
            release_runtime_lock._lock_descriptor(17)

        with (
            mock.patch.object(release_runtime_lock.os, "name", "unsupported"),
            self.assertRaisesRegex(release_runtime_lock.RuntimeLockError, "平台"),
        ):
            release_runtime_lock._lock_descriptor(17)

        fake_fcntl.flock.side_effect = OSError(errno.EIO, "io")
        with (
            mock.patch.object(release_runtime_lock.os, "name", "posix"),
            mock.patch.dict(sys.modules, {"fcntl": fake_fcntl}),
            self.assertRaisesRegex(release_runtime_lock.RuntimeLockError, "解锁"),
        ):
            release_runtime_lock._unlock_descriptor(17)

        with (
            mock.patch.object(release_runtime_lock.os, "name", "unsupported"),
            self.assertRaisesRegex(release_runtime_lock.RuntimeLockError, "平台"),
        ):
            release_runtime_lock._unlock_descriptor(17)


if __name__ == "__main__":
    unittest.main()
