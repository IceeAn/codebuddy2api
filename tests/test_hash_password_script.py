import contextlib
import io
import json
import unittest
from unittest import mock

import config
from scripts import hash_password, manage_users
from src.users_store import AUTH_STATE_INITIALIZED, users_store
from tests.helpers import TempConfigMixin


class ManageUsersCliTests(TempConfigMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        config._config_cache["CODEBUDDY_USERS_FILE"] = str(self.temp_path / "missing.txt")
        config.initialize_database()

    def _run(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = manage_users.main(list(arguments))
        return result, stdout.getvalue(), stderr.getvalue()

    def test_set_user_and_add_user_alias_write_sqlite_accounts(self):
        first = self._run("set-user", "alice", "--password", "alice-password")
        second = self._run("add-user", "bob", "--password", "bob-password")

        self.assertEqual((first[0], second[0]), (0, 0))
        self.assertIn("alice", first[1])
        self.assertIn("bob", second[1])
        self.assertEqual(first[2] + second[2], "")
        self.assertEqual(users_store.state(), AUTH_STATE_INITIALIZED)
        self.assertTrue(users_store.verify("alice", "alice-password"))
        self.assertTrue(users_store.verify("bob", "bob-password"))

    def test_set_user_prompts_twice_only_on_a_tty(self):
        with (
            mock.patch.object(manage_users.sys.stdin, "isatty", return_value=True),
            mock.patch.object(
                manage_users.getpass,
                "getpass",
                side_effect=["prompt-password", "prompt-password"],
            ) as prompt,
        ):
            result, _stdout, stderr = self._run("set-user", "alice")

        self.assertEqual(result, 0)
        self.assertEqual(prompt.call_count, 2)
        self.assertEqual(stderr, "")

    def test_set_user_rejects_non_tty_or_mismatched_prompt(self):
        with mock.patch.object(manage_users.sys.stdin, "isatty", return_value=False):
            result, _stdout, stderr = self._run("set-user", "alice")
        self.assertEqual(result, 1)
        self.assertIn("--password", stderr)

        with (
            mock.patch.object(manage_users.sys.stdin, "isatty", return_value=True),
            mock.patch.object(
                manage_users.getpass,
                "getpass",
                side_effect=["one-password", "other-password"],
            ),
        ):
            result, _stdout, stderr = self._run("set-user", "alice")
        self.assertEqual(result, 1)
        self.assertIn("不一致", stderr)

    def test_list_users_human_and_json_are_sorted_without_hashes(self):
        users_store.set_user("bob", "bob-password")
        users_store.set_user("alice", "alice-password")

        human = self._run("list-users")
        encoded = self._run("list-users", "--json")

        self.assertEqual((human[0], encoded[0]), (0, 0))
        self.assertLess(human[1].index("alice"), human[1].index("bob"))
        self.assertNotIn("pbkdf2", human[1] + encoded[1])
        body = json.loads(encoded[1])
        self.assertEqual([item["username"] for item in body["users"]], ["alice", "bob"])
        self.assertEqual(
            set(body["users"][0]),
            {"username", "password_change_required", "updated_at"},
        )

    def test_delete_requires_confirmation_and_preserves_last_user(self):
        users_store.set_user("alice", "alice-password")
        users_store.set_user("bob", "bob-password")
        with mock.patch("builtins.input", return_value="wrong"):
            cancelled = self._run("delete-user", "bob")
        deleted = self._run("delete-user", "bob", "--yes")
        last = self._run("delete-user", "alice", "--yes")

        self.assertEqual(cancelled[0], 1)
        self.assertIn("取消", cancelled[2])
        self.assertEqual(deleted[0], 0)
        self.assertIn("bob", deleted[1])
        self.assertEqual(last[0], 1)
        self.assertIn("最后一个", last[2])

    def test_list_before_initialization_returns_exit_one(self):
        result, _stdout, stderr = self._run("list-users")
        self.assertEqual(result, 1)
        self.assertIn("set-user", stderr)

    def test_list_before_database_exists_returns_same_initialization_guidance(self):
        config.get_database_path().unlink()

        result, _stdout, stderr = self._run("list-users")

        self.assertEqual(result, 1)
        self.assertIn("账号系统尚未初始化", stderr)


class RetiredHashPasswordCliTests(unittest.TestCase):
    def test_old_command_only_prints_migration_guidance_and_returns_one(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(hash_password.sys, "argv", ["hash_password.py", "ignored"]),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            result = hash_password.main()

        self.assertEqual(result, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("manage_users.py set-user", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
