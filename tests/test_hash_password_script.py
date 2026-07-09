import contextlib
import io
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import hash_password
from src.password_hashing import create_password_hash
from src.users_store import UsersFileStore


class UsersFileWriterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.users_file = self.temp_path / "users.txt"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_creates_private_users_file(self):
        hash_password.replace_user_record(
            self.users_file,
            "admin",
            "new-hash",
        )

        self.assertEqual(self.users_file.read_text(encoding="utf-8"), "admin:new-hash\n")
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.users_file.stat().st_mode), 0o600)

    def test_adds_separator_when_existing_file_has_no_final_newline(self):
        self.users_file.write_text("alice:alice-hash", encoding="utf-8")

        hash_password.replace_user_record(self.users_file, "bob", "bob-hash")

        self.assertEqual(
            self.users_file.read_text(encoding="utf-8"),
            "alice:alice-hash\nbob:bob-hash\n",
        )

    def test_replaces_all_records_for_existing_username(self):
        self.users_file.write_text(
            "# 系统用户\n"
            "admin:old-hash-1\n"
            "invalid-line\n"
            "alice:alice-hash\n"
            " admin :old-hash-2\n",
            encoding="utf-8",
        )

        hash_password.replace_user_record(self.users_file, "admin", "new-hash")

        self.assertEqual(
            self.users_file.read_text(encoding="utf-8"),
            "# 系统用户\n"
            "invalid-line\n"
            "alice:alice-hash\n"
            "admin:new-hash\n",
        )

    def test_replacing_user_record_updates_the_effective_password(self):
        old_hash = create_password_hash("old-password")
        new_hash = create_password_hash("new-password")
        hash_password.replace_user_record(self.users_file, "admin", old_hash)

        with mock.patch(
            "src.users_store.get_users_file_path", return_value=str(self.users_file)
        ):
            self.assertTrue(UsersFileStore().verify("admin", "old-password"))

        hash_password.replace_user_record(self.users_file, "admin", new_hash)

        with mock.patch(
            "src.users_store.get_users_file_path", return_value=str(self.users_file)
        ):
            store = UsersFileStore()
            self.assertTrue(store.verify("admin", "new-password"))
            self.assertFalse(store.verify("admin", "old-password"))
        self.assertEqual(
            sum(
                line.startswith("admin:")
                for line in self.users_file.read_text(encoding="utf-8").splitlines()
            ),
            1,
        )

    @unittest.skipUnless(os.name == "posix", "POSIX 文件权限测试")
    def test_preserves_or_tightens_posix_permissions(self):
        for initial_mode, expected_mode in (
            (0o400, 0o400),
            (0o600, 0o600),
            (0o444, 0o400),
            (0o644, 0o600),
            (0o700, 0o600),
        ):
            with self.subTest(initial_mode=oct(initial_mode)):
                if self.users_file.exists():
                    self.users_file.chmod(0o600)
                self.users_file.write_text("alice:hash\n", encoding="utf-8")
                self.users_file.chmod(initial_mode)
                original_stat = self.users_file.stat()

                hash_password.replace_user_record(self.users_file, "bob", "hash")

                updated_stat = self.users_file.stat()
                self.assertEqual(stat.S_IMODE(updated_stat.st_mode), expected_mode)
                self.assertEqual(updated_stat.st_uid, original_stat.st_uid)
                self.assertEqual(updated_stat.st_gid, original_stat.st_gid)

    def test_replace_failure_keeps_original_file(self):
        self.users_file.write_text("admin:old-hash\n", encoding="utf-8")

        with (
            mock.patch("scripts.hash_password.os.replace", side_effect=OSError("boom")),
            self.assertRaisesRegex(OSError, "boom"),
        ):
            hash_password.replace_user_record(self.users_file, "admin", "new-hash")

        self.assertEqual(
            self.users_file.read_text(encoding="utf-8"), "admin:old-hash\n"
        )
        self.assertEqual(list(self.temp_path.iterdir()), [self.users_file])

    @unittest.skipUnless(os.name == "posix", "POSIX 文件类型测试")
    def test_rejects_symbolic_links_and_hard_links(self):
        source = self.temp_path / "source.txt"
        source.write_text("admin:old-hash\n", encoding="utf-8")
        self.users_file.symlink_to(source)

        with self.assertRaisesRegex(RuntimeError, "symbolic link"):
            hash_password.replace_user_record(self.users_file, "admin", "new-hash")

        self.users_file.unlink()
        os.link(source, self.users_file)
        with self.assertRaisesRegex(RuntimeError, "multiple hard links"):
            hash_password.replace_user_record(self.users_file, "admin", "new-hash")

    def test_rejects_non_regular_file(self):
        self.users_file.mkdir()

        with self.assertRaisesRegex(RuntimeError, "regular file"):
            hash_password.replace_user_record(self.users_file, "admin", "new-hash")

    def test_rejects_usernames_that_cannot_form_one_valid_record(self):
        invalid_usernames = (
            "",
            "   ",
            "#admin",
            "  #admin",
            "admin:root",
            "admin\nroot",
            "admin\rroot",
        )
        for username in invalid_usernames:
            with self.subTest(username=username):
                self.users_file.write_text("admin:old-hash\n", encoding="utf-8")

                with self.assertRaisesRegex(ValueError, "用户名"):
                    hash_password.replace_user_record(
                        self.users_file, username, "new-hash"
                    )

                self.assertEqual(
                    self.users_file.read_text(encoding="utf-8"), "admin:old-hash\n"
                )


class HashPasswordCliTests(unittest.TestCase):
    def test_stdout_mode_remains_compatible(self):
        with (
            mock.patch.object(hash_password, "create_password_hash", return_value="hash"),
            mock.patch.object(
                hash_password.sys,
                "argv",
                ["hash_password.py", "admin", "--password", "secret"],
            ),
            contextlib.redirect_stdout(io.StringIO()) as stdout,
        ):
            hash_password.main()

        self.assertEqual(stdout.getvalue(), "admin:hash\n")

    def test_output_mode_updates_users_file_without_printing_secret_material(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            users_file = Path(temp_dir) / "users.txt"
            with (
                mock.patch.object(
                    hash_password, "create_password_hash", return_value="hash"
                ),
                mock.patch.object(hash_password, "replace_user_record") as replace,
                mock.patch.object(
                    hash_password.sys,
                    "argv",
                    [
                        "hash_password.py",
                        "admin",
                        "--password",
                        "secret",
                        "--output",
                        str(users_file),
                    ],
                ),
                contextlib.redirect_stdout(io.StringIO()) as stdout,
            ):
                hash_password.main()

        replace.assert_called_once_with(users_file, "admin", "hash")
        self.assertEqual(stdout.getvalue(), "")

    def test_rejects_invalid_username_before_reading_or_hashing_password(self):
        for username in ("", "#admin", "admin:root", "admin\nroot"):
            with self.subTest(username=username):
                with (
                    mock.patch.object(
                        hash_password.getpass, "getpass"
                    ) as get_password,
                    mock.patch.object(
                        hash_password, "create_password_hash"
                    ) as create_hash,
                    mock.patch.object(
                        hash_password.sys,
                        "argv",
                        ["hash_password.py", username],
                    ),
                    contextlib.redirect_stderr(io.StringIO()),
                    self.assertRaises(SystemExit) as raised,
                ):
                    hash_password.main()

                self.assertEqual(raised.exception.code, 2)
                get_password.assert_not_called()
                create_hash.assert_not_called()


if __name__ == "__main__":
    unittest.main()
