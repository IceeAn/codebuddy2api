"""管理页会话存储。"""
import secrets
import threading
import time
from typing import Any, Dict, Optional

from .auth_error_codes import AuthErrorCode
from .auth_types import AuthenticatedUser, SESSION_TTL_SECONDS
from .users_store import UserRecord, users_store


class SessionStore:
    """进程内管理页会话存储。"""

    def __init__(self, max_sessions_per_user: int = 10):
        self._lock = threading.RLock()
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.max_sessions_per_user = max_sessions_per_user

    def cleanup_expired(self, now: Optional[float] = None) -> None:
        with self._lock:
            if now is None:
                now = time.time()
            expired_session_ids = [
                session_id
                for session_id, session_data in self.sessions.items()
                if session_data.get("expires_at", 0) <= now
            ]
            for session_id in expired_session_ids:
                self.sessions.pop(session_id, None)

    def create(self, username: str, record: Optional[UserRecord] = None) -> str:
        with self._lock:
            now = time.time()
            if record is None:
                record = users_store.get_record(username)
            if record is None:
                raise RuntimeError("Cannot create a session for a missing system user")
            self.cleanup_expired(now)
            owned_sessions = sorted(
                (
                    (session_id, session_data)
                    for session_id, session_data in self.sessions.items()
                    if session_data.get("username") == username
                ),
                key=lambda item: item[1].get("created_at", 0),
            )
            while len(owned_sessions) >= self.max_sessions_per_user:
                oldest_session_id, _oldest_session = owned_sessions.pop(0)
                self.sessions.pop(oldest_session_id, None)
            session_id = secrets.token_urlsafe(32)
            self.sessions[session_id] = {
                "username": username,
                "created_at": now,
                "expires_at": now + SESSION_TTL_SECONDS,
                "auth_revision": record.auth_revision,
                "password_change_required": record.password_change_required,
            }
            return session_id

    def get_user_with_reason(
        self, session_id: Optional[str]
    ) -> tuple[Optional[AuthenticatedUser], Optional[str]]:
        """返回会话及失效原因；原因仅用于稳定的账号业务错误。"""
        with self._lock:
            if not session_id:
                return None, None

            self.cleanup_expired()
            session_data = self.sessions.get(session_id)
            if not session_data:
                return None, None

            revoked_reason = session_data.get("revoked_reason")
            if revoked_reason:
                self.sessions.pop(session_id, None)
                return None, str(revoked_reason)

            username = str(session_data.get("username") or "")
            record = users_store.get_record(username) if username else None
            if record is None:
                self.sessions.pop(session_id, None)
                return None, None
            if users_store.is_bootstrap_expired(username):
                self.sessions.pop(session_id, None)
                return None, AuthErrorCode.BOOTSTRAP_EXPIRED.value
            if session_data.get("auth_revision") != record.auth_revision:
                self.sessions.pop(session_id, None)
                return None, AuthErrorCode.PASSWORD_CHANGED_ELSEWHERE.value

            session_data["expires_at"] = time.time() + SESSION_TTL_SECONDS
            return AuthenticatedUser(
                username=username,
                source="session_cookie",
                password_change_required=record.password_change_required,
                auth_revision=record.auth_revision,
            ), None

    def get_user(self, session_id: Optional[str]) -> Optional[AuthenticatedUser]:
        user, _reason = self.get_user_with_reason(session_id)
        return user

    def invalidate(self, session_id: Optional[str]) -> None:
        with self._lock:
            if session_id:
                self.sessions.pop(session_id, None)

    def revoke_user(self, username: str, reason: str) -> None:
        """标记账号的所有会话失效，并让下一次请求得到明确原因。"""
        with self._lock:
            for session_data in self.sessions.values():
                if session_data.get("username") == username:
                    session_data["revoked_reason"] = reason


session_store = SessionStore()
