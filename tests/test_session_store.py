import time
import unittest
from unittest import mock

from src.session_store import SessionStore


class SessionStoreTests(unittest.TestCase):
    def test_create_stores_session_and_get_user_extends_expiry(self):
        store = SessionStore()

        with mock.patch("src.session_store.users_store.has_username", return_value=True):
            session_id = store.create("admin")
            original_expiry = store.sessions[session_id]["expires_at"]
            with mock.patch("src.session_store.time.time", return_value=time.time() + 10):
                user = store.get_user(session_id)

        self.assertEqual(user.username, "admin")
        self.assertEqual(user.source, "session_cookie")
        self.assertGreater(store.sessions[session_id]["expires_at"], original_expiry)

    def test_get_user_returns_none_for_missing_or_empty_session_id(self):
        store = SessionStore()

        self.assertIsNone(store.get_user(None))
        self.assertIsNone(store.get_user(""))
        self.assertIsNone(store.get_user("missing"))

    def test_cleanup_expired_removes_boundary_expired_sessions(self):
        store = SessionStore()
        store.sessions = {
            "expired": {"username": "admin", "expires_at": 100},
            "active": {"username": "admin", "expires_at": 101},
        }

        with mock.patch("src.session_store.time.time", return_value=100):
            store.cleanup_expired()

        self.assertNotIn("expired", store.sessions)
        self.assertIn("active", store.sessions)

    def test_get_user_removes_session_when_username_no_longer_exists(self):
        store = SessionStore()
        store.sessions["sid"] = {"username": "ghost", "expires_at": time.time() + 60}

        with mock.patch("src.session_store.users_store.has_username", return_value=False):
            self.assertIsNone(store.get_user("sid"))

        self.assertNotIn("sid", store.sessions)

    def test_invalidate_is_idempotent(self):
        store = SessionStore()
        store.sessions["sid"] = {"username": "admin", "expires_at": time.time() + 60}

        store.invalidate("sid")
        store.invalidate("sid")
        store.invalidate(None)

        self.assertEqual(store.sessions, {})


if __name__ == "__main__":
    unittest.main()
