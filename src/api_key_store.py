"""服务 API Key 文件存储。"""
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import get_codebuddy_creds_dir
from .auth_types import API_KEY_PREFIX, DUMMY_PASSWORD_HASH, AuthenticatedUser
from .password_hashing import create_password_hash, verify_password
from .users_store import users_store

logger = logging.getLogger(__name__)


class ApiKeyStore:
    """管理用户生成的 sk- API Key。"""

    def __init__(self):
        self._keys: List[Dict[str, Any]] = []
        self._loaded_path: Optional[Path] = None
        self._loaded_mtime: Optional[float] = None

    def _resolve_api_keys_file(self) -> Path:
        creds_dir = Path(get_codebuddy_creds_dir())
        if not creds_dir.is_absolute():
            creds_dir = Path.cwd() / creds_dir
        return creds_dir / "api_keys.json"

    def _load_if_needed(self) -> None:
        api_keys_file = self._resolve_api_keys_file()
        try:
            stat_result = api_keys_file.stat()
        except FileNotFoundError:
            self._keys = []
            self._loaded_path = api_keys_file
            self._loaded_mtime = None
            return

        if api_keys_file.is_symlink() or not api_keys_file.is_file():
            self._keys = []
            self._loaded_path = api_keys_file
            self._loaded_mtime = None
            logger.warning("Configured API keys file is not a regular file: %s", api_keys_file)
            return

        current_mtime = stat_result.st_mtime
        if self._loaded_path == api_keys_file and self._loaded_mtime == current_mtime:
            return

        try:
            with api_keys_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.error("Failed to load API keys file %s: %s", api_keys_file, e)
            data = {}

        keys = data.get("keys", []) if isinstance(data, dict) else []
        self._keys = [key for key in keys if isinstance(key, dict)]
        self._loaded_path = api_keys_file
        self._loaded_mtime = current_mtime

    def _save(self) -> None:
        api_keys_file = self._resolve_api_keys_file()
        api_keys_file.parent.mkdir(parents=True, exist_ok=True)

        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW

        fd = os.open(api_keys_file, flags, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                fd = None
                json.dump({"keys": self._keys}, f, indent=2, ensure_ascii=False)
            os.chmod(api_keys_file, 0o600)
            self._loaded_path = api_keys_file
            self._loaded_mtime = api_keys_file.stat().st_mtime
        except Exception:
            if fd is not None:
                os.close(fd)
            raise

    @staticmethod
    def _safe_key_name(name: str) -> str:
        return str(name or "").strip()[:80] or "API Key"

    @staticmethod
    def _preview(api_key: str) -> str:
        return f"{api_key[:10]}...{api_key[-4:]}"

    def create_key(self, username: str, name: str = "") -> Dict[str, Any]:
        self._load_if_needed()
        api_key = f"{API_KEY_PREFIX}{secrets.token_urlsafe(32)}"
        created_at = int(time.time())
        key_record = {
            "id": f"key_{secrets.token_urlsafe(12)}",
            "username": username,
            "name": self._safe_key_name(name),
            "key_hash": create_password_hash(api_key),
            "preview": self._preview(api_key),
            "created_at": created_at,
            "last_used_at": None,
        }
        self._keys.append(key_record)
        self._save()
        return {
            "id": key_record["id"],
            "name": key_record["name"],
            "api_key": api_key,
            "preview": key_record["preview"],
            "created_at": created_at,
            "last_used_at": None,
        }

    def verify(self, api_key: str) -> Optional[AuthenticatedUser]:
        if not api_key.startswith(API_KEY_PREFIX):
            verify_password(api_key, DUMMY_PASSWORD_HASH)
            return None

        self._load_if_needed()
        matched_key: Optional[Dict[str, Any]] = None
        for key_record in self._keys:
            key_hash = key_record.get("key_hash")
            if isinstance(key_hash, str) and verify_password(api_key, key_hash):
                matched_key = key_record
                break

        if not matched_key:
            verify_password(api_key, DUMMY_PASSWORD_HASH)
            return None

        username = str(matched_key.get("username") or "")
        if not username or not users_store.has_username(username):
            return None

        matched_key["last_used_at"] = int(time.time())
        self._save()
        return AuthenticatedUser(username=username, source="api_key")

    def list_keys(self, username: str) -> List[Dict[str, Any]]:
        self._load_if_needed()
        return [
            {
                "id": key_record.get("id"),
                "name": key_record.get("name"),
                "preview": key_record.get("preview"),
                "created_at": key_record.get("created_at"),
                "last_used_at": key_record.get("last_used_at"),
            }
            for key_record in self._keys
            if key_record.get("username") == username
        ]

    def delete_key(self, username: str, key_id: str) -> bool:
        self._load_if_needed()
        original_count = len(self._keys)
        self._keys = [
            key_record
            for key_record in self._keys
            if not (key_record.get("username") == username and key_record.get("id") == key_id)
        ]
        if len(self._keys) == original_count:
            return False
        self._save()
        return True


api_key_store = ApiKeyStore()
