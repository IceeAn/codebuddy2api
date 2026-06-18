"""管理页会话存储。"""
import secrets
import time
from typing import Any, Dict, Optional

from .auth_types import AuthenticatedUser, SESSION_TTL_SECONDS
from .users_store import users_store


class SessionStore:
    """进程内管理页会话存储。"""

    def __init__(self):
        self.sessions: Dict[str, Dict[str, Any]] = {}

    def cleanup_expired(self) -> None:
        now = time.time()
        expired_session_ids = [
            session_id
            for session_id, session_data in self.sessions.items()
            if session_data.get("expires_at", 0) <= now
        ]
        for session_id in expired_session_ids:
            self.sessions.pop(session_id, None)

    def create(self, username: str) -> str:
        self.cleanup_expired()
        session_id = secrets.token_urlsafe(32)
        self.sessions[session_id] = {
            "username": username,
            "expires_at": time.time() + SESSION_TTL_SECONDS,
        }
        return session_id

    def get_user(self, session_id: Optional[str]) -> Optional[AuthenticatedUser]:
        if not session_id:
            return None

        self.cleanup_expired()
        session_data = self.sessions.get(session_id)
        if not session_data:
            return None

        username = str(session_data.get("username") or "")
        if not username or not users_store.has_username(username):
            self.sessions.pop(session_id, None)
            return None

        session_data["expires_at"] = time.time() + SESSION_TTL_SECONDS
        return AuthenticatedUser(username=username, source="session_cookie")

    def invalidate(self, session_id: Optional[str]) -> None:
        if session_id:
            self.sessions.pop(session_id, None)


session_store = SessionStore()
