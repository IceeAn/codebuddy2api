"""服务 API Key SQLite 存储。"""
import base64
import binascii
import hashlib
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import get_data_dir
from .auth_types import API_KEY_PREFIX, AuthenticatedUser
from .sqlite_database import SQLiteDatabase, resolve_database_path
from .users_store import users_store

API_KEY_SECRET_BYTES = 40
API_KEY_ENCODED_LENGTH = 54


class ApiKeyStore:
    """管理用户生成的 sk- API Key。"""

    def _resolve_database_path(self) -> Path:
        return resolve_database_path(get_data_dir())

    def _database(self) -> SQLiteDatabase:
        return SQLiteDatabase(self._resolve_database_path())

    @staticmethod
    def _decode_api_key(api_key: str) -> Optional[bytes]:
        if not isinstance(api_key, str) or not api_key.startswith(API_KEY_PREFIX):
            return None
        encoded = api_key[len(API_KEY_PREFIX):]
        if len(encoded) != API_KEY_ENCODED_LENGTH:
            return None
        if "=" in encoded:
            return None
        try:
            encoded_bytes = encoded.encode("ascii")
            padded = encoded_bytes + b"=" * (-len(encoded_bytes) % 4)
            secret = base64.b64decode(padded, altchars=b"-_", validate=True)
        except (UnicodeError, ValueError, binascii.Error):
            return None
        canonical = base64.urlsafe_b64encode(secret).rstrip(b"=")
        if len(secret) != API_KEY_SECRET_BYTES or not secrets.compare_digest(
            canonical, encoded_bytes
        ):
            return None
        return secret

    @staticmethod
    def _digest_api_key(api_key: str) -> bytes:
        return hashlib.sha256(api_key.encode("utf-8")).digest()

    @staticmethod
    def _safe_key_name(name: str) -> str:
        return str(name or "").strip()[:80] or "API Key"

    @staticmethod
    def _preview(api_key: str) -> str:
        return f"{api_key[:10]}...{api_key[-4:]}"

    def create_key(self, username: str, name: str = "") -> Dict[str, Any]:
        api_key = f"{API_KEY_PREFIX}{secrets.token_urlsafe(API_KEY_SECRET_BYTES)}"
        created_at = int(time.time())
        with self._database().connect() as connection:
            key_record = {
                "id": f"key_{secrets.token_urlsafe(12)}",
                "username": username,
                "name": self._safe_key_name(name),
                "key_digest": self._digest_api_key(api_key),
                "preview": self._preview(api_key),
                "created_at": created_at,
                "last_used_at": None,
            }
            connection.execute(
                """
                INSERT INTO api_keys(id, username, name, key_digest, preview, created_at, last_used_at)
                VALUES (:id, :username, :name, :key_digest, :preview, :created_at, :last_used_at)
                """,
                key_record,
            )
        return {
            "id": key_record["id"],
            "name": key_record["name"],
            "api_key": api_key,
            "preview": key_record["preview"],
            "created_at": created_at,
            "last_used_at": None,
        }

    def verify(self, api_key: str) -> Optional[AuthenticatedUser]:
        if self._decode_api_key(api_key) is None:
            return None

        database = self._database()
        if not database.path.exists():
            return None
        with database.connect() as connection:
            key_digest = self._digest_api_key(api_key)
            cursor = connection.execute(
                """
                SELECT id, username, last_used_at
                FROM api_keys
                WHERE key_digest = ?
                """,
                (key_digest,),
            )
            try:
                key_record = cursor.fetchone()
            finally:
                cursor.close()
            if key_record is None:
                return None

            username = key_record["username"]
            if not users_store.has_username(username):
                return None

            current_minute = int(time.time()) // 60 * 60
            if key_record["last_used_at"] is None or key_record["last_used_at"] < current_minute:
                cursor = connection.execute(
                    """
                    UPDATE api_keys
                    SET last_used_at = ?
                    WHERE id = ? AND key_digest = ?
                      AND (last_used_at IS NULL OR last_used_at < ?)
                    """,
                    (current_minute, key_record["id"], key_digest, current_minute),
                )
                if cursor.rowcount > 0:
                    return AuthenticatedUser(username=username, source="api_key")

            still_exists = connection.execute(
                "SELECT 1 FROM api_keys WHERE id = ? AND key_digest = ?",
                (key_record["id"], key_digest),
            ).fetchone()
            if still_exists is None:
                return None
            return AuthenticatedUser(username=username, source="api_key")

    def list_keys(self, username: str) -> List[Dict[str, Any]]:
        database = self._database()
        if not database.path.exists():
            return []
        with database.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, name, preview, created_at, last_used_at
                FROM api_keys
                WHERE username = ?
                ORDER BY created_at, id
                """,
                (username,),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_key(self, username: str, key_id: str) -> bool:
        database = self._database()
        if not database.path.exists():
            return False
        with database.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM api_keys WHERE username = ? AND id = ?",
                (username, key_id),
            )
            return cursor.rowcount > 0


api_key_store = ApiKeyStore()
