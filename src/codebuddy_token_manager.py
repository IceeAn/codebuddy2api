"""CodeBuddy Token Manager - 管理 CodeBuddy 认证 token。"""
import logging
import os
import time
from typing import Any, Dict, List, Optional

from .auth_types import AuthenticatedUser
from .credential_rotation import CredentialRotationPolicy, TokenExpiry
from .credential_store import CodeBuddyCredentialStore, CredentialRecord, build_user_credentials_dirname
from .usage_stats_manager import usage_stats_manager

logger = logging.getLogger(__name__)


class CodeBuddyTokenManager:
    """CodeBuddy Token 管理器。"""

    def __init__(self, creds_dir=None):
        if creds_dir is None:
            from config import get_codebuddy_creds_dir

            creds_dir = get_codebuddy_creds_dir()

        self.store = CodeBuddyCredentialStore(creds_dir)
        self.creds_dir = self.store.creds_dir
        self.state_file = self.store.state_file
        self.token_expiry = TokenExpiry()
        self.rotation_policy = CredentialRotationPolicy(self.token_expiry)
        self.credentials: List[CredentialRecord] = []
        self.current_index: int = 0
        self.usage_count: int = 0
        self.manual_selected_index: Optional[int] = None
        self.auto_rotation_enabled: bool = True
        self.load_all_tokens()
        self.load_state()

    def load_all_tokens(self):
        """加载所有 token 文件。"""
        current_filename = self._credential_filename(self.current_index)
        manual_filename = self._credential_filename(self.manual_selected_index)
        self.credentials = self.store.load_credentials()
        self.manual_selected_index = self._find_credential_index_by_filename(manual_filename)
        if manual_filename and self.manual_selected_index is None:
            logger.info("Cleared manual selection because selected credential is no longer available")

        next_current_index = self._find_credential_index_by_filename(current_filename)
        if next_current_index is not None:
            self.current_index = next_current_index
        elif self.manual_selected_index is not None:
            self.current_index = self.manual_selected_index
            self.usage_count = 0
        elif self.credentials:
            self.current_index = 0
            self.usage_count = 0
        else:
            self.current_index = -1
            self.usage_count = 0

    def _credential_filename(self, index: Optional[int]) -> Optional[str]:
        if index is None or not (0 <= index < len(self.credentials)):
            return None
        return os.path.basename(self.credentials[index]["file_path"])

    def _find_credential_index_by_filename(self, filename: Optional[str]) -> Optional[int]:
        if not filename:
            return None
        for index, credential in enumerate(self.credentials):
            if os.path.basename(credential["file_path"]) == filename:
                return index
        return None

    def load_state(self):
        """加载管理器状态。"""
        try:
            state = self.store.load_manager_state()
            if not state:
                return

            saved_manual_filename = state.get("manual_selected_filename")
            saved_manual_index = self._find_credential_index_by_filename(saved_manual_filename)
            if saved_manual_index is None:
                legacy_manual_index = state.get("manual_selected_index")
                if legacy_manual_index is not None and 0 <= legacy_manual_index < len(self.credentials):
                    legacy_filename = self._credential_filename(legacy_manual_index)
                    if not saved_manual_filename or saved_manual_filename == legacy_filename:
                        saved_manual_index = legacy_manual_index

            if saved_manual_index is not None:
                self.manual_selected_index = saved_manual_index
                self.current_index = saved_manual_index
                current_filename = self._credential_filename(saved_manual_index)
                logger.info(f"Restored manual selection: {current_filename} (index: {saved_manual_index})")
            elif saved_manual_filename:
                logger.warning("Saved credential filename mismatch, ignoring saved selection")

            self.auto_rotation_enabled = state.get("auto_rotation_enabled", True)

            if self.manual_selected_index is None:
                saved_current_filename = state.get("current_filename")
                saved_current_index = self._find_credential_index_by_filename(saved_current_filename)
                if saved_current_index is not None:
                    self.current_index = saved_current_index
                elif saved_current_filename:
                    logger.warning(
                        "Saved current credential filename mismatch, falling back to first available credential")
                else:
                    saved_current_index = state.get("current_index", 0)
                    if 0 <= saved_current_index < len(self.credentials):
                        self.current_index = saved_current_index

            logger.info(f"State loaded: auto_rotation={self.auto_rotation_enabled}, current_index={self.current_index}")
        except Exception as e:
            logger.warning(f"Failed to load manager state: {e}")

    def save_state(self):
        """保存管理器状态。"""
        try:
            state = {
                "auto_rotation_enabled": self.auto_rotation_enabled,
                "current_index": self.current_index,
                "current_filename": self._credential_filename(self.current_index),
                "manual_selected_index": self.manual_selected_index,
                "manual_selected_filename": None,
                "saved_at": int(time.time()),
            }

            if self.manual_selected_index is not None and 0 <= self.manual_selected_index < len(self.credentials):
                state["manual_selected_filename"] = os.path.basename(
                    self.credentials[self.manual_selected_index]["file_path"]
                )

            self.store.save_manager_state(state)
        except Exception as e:
            logger.error(f"Failed to save manager state: {e}")

    def is_token_expired(self, credential_data: Dict) -> bool:
        """检查 token 是否过期。"""
        return self.token_expiry.is_expired(credential_data)

    def get_next_credential(self) -> Optional[Dict]:
        """根据当前轮换策略获取下一个可用凭证。"""
        from config import get_rotation_count

        selection = self.rotation_policy.select(
            credentials=self.credentials,
            current_index=self.current_index,
            usage_count=self.usage_count,
            manual_selected_index=self.manual_selected_index,
            auto_rotation_enabled=self.auto_rotation_enabled,
            rotation_count=get_rotation_count(),
        )
        self.current_index = selection.current_index
        self.usage_count = selection.usage_count
        self.manual_selected_index = selection.manual_selected_index

        if not selection.credential_record:
            return None

        if selection.filename:
            usage_stats_manager.record_credential_usage(selection.filename)
        if selection.log_message:
            logger.info(selection.log_message)

        return selection.credential_record["data"]

    def get_all_credentials(self) -> List[Dict]:
        """获取所有凭证。"""
        return [cred["data"] for cred in self.credentials]

    def get_credentials_info(self) -> List[Dict]:
        """获取所有凭证的详细信息，包括过期状态。"""
        credentials_info = []
        for index, cred in enumerate(self.credentials):
            data = cred["data"]
            filename = os.path.basename(cred["file_path"])
            is_expired = self.is_token_expired(data)
            expires_at = None
            time_remaining = None

            if data.get("created_at") and data.get("expires_in"):
                expires_at = data["created_at"] + data["expires_in"]
                time_remaining = expires_at - int(time.time())

            user_info = data.get("user_info", {})
            credentials_info.append(
                {
                    "index": index,
                    "filename": filename,
                    "user_id": data.get("user_id", "unknown"),
                    "email": user_info.get("email") or data.get("user_id"),
                    "name": user_info.get("name"),
                    "created_at": data.get("created_at"),
                    "expires_in": data.get("expires_in"),
                    "expires_at": expires_at,
                    "time_remaining": time_remaining,
                    "is_expired": is_expired,
                    "token_type": data.get("token_type", "Bearer"),
                    "scope": data.get("scope"),
                    "domain": data.get("domain"),
                    "has_refresh_token": bool(data.get("refresh_token")),
                    "session_state": data.get("session_state"),
                }
            )

        return credentials_info

    def add_credential(self, bearer_token: str, user_id: str = None, filename: str = None) -> bool:
        """添加新的凭证。"""
        if not filename:
            filename = f"codebuddy_token_{len(self.credentials) + 1}.json"

        credential_data = {
            "bearer_token": bearer_token,
            "user_id": user_id,
            "created_at": int(time.time()),
        }
        return self.add_credential_with_data(credential_data, filename)

    def add_credential_with_data(self, credential_data: Dict[str, Any], filename: str = None) -> bool:
        """添加新的凭证。"""
        if not filename:
            user_id = credential_data.get("user_id", "unknown")
            timestamp = credential_data.get("created_at", int(time.time()))
            safe_user_id = "".join(c for c in str(user_id) if c.isalnum() or c in "._-")[:20]
            filename = f"codebuddy_{safe_user_id}_{timestamp}.json"

        if "created_at" not in credential_data:
            credential_data["created_at"] = int(time.time())

        try:
            safe_filename = self.store.save_credential(credential_data, filename, indent=4)
            logger.info(f"Added new credential: {safe_filename}")
            self.load_all_tokens()
            return True
        except Exception as e:
            logger.error(f"Failed to save credential: {e}")
            return False

    def _sanitize_filename(self, filename: str) -> str:
        return self.store.sanitize_filename(filename)

    def _resolve_credential_path(self, filename: str) -> str:
        return self.store.resolve_credential_path(filename)

    def _is_path_inside_creds_dir(self, file_path: str) -> bool:
        return self.store.is_path_inside_creds_dir(file_path)

    def _write_json_file(self, file_path: str, data: Dict[str, Any], indent: int):
        self.store.write_json_file(file_path, data, indent)

    def delete_credential_by_index(self, index: int) -> bool:
        """删除指定索引的凭证文件，并重新加载列表。"""
        try:
            if not (0 <= index < len(self.credentials)):
                logger.error(f"Invalid credential index for deletion: {index}")
                return False

            file_path = self.credentials[index]["file_path"]
            filename = os.path.basename(file_path)

            if self.store.delete_credential_file(file_path):
                logger.info(f"Deleted credential file: {filename}")
            else:
                logger.warning(f"Credential file already missing: {filename}")

            self.load_all_tokens()
            return True
        except Exception as e:
            logger.error(f"Failed to delete credential at index {index}: {e}")
            return False

    def set_manual_credential(self, index: int) -> bool:
        """手动选择指定索引的凭证。"""
        if 0 <= index < len(self.credentials):
            self.manual_selected_index = index
            self.current_index = index
            credential_filename = os.path.basename(self.credentials[index]["file_path"])
            logger.info(f"Manually selected credential: {credential_filename} (index: {index})")
            self.save_state()
            return True

        logger.error(f"Invalid credential index: {index}")
        return False

    def clear_manual_selection(self):
        """清除手动选择，恢复自动轮换。"""
        self.manual_selected_index = None
        logger.info("Cleared manual credential selection, resumed automatic rotation")
        self.save_state()

    def enable_auto_rotation(self):
        """开启自动轮换。"""
        self.auto_rotation_enabled = True
        logger.info("Auto rotation enabled")

    def disable_auto_rotation(self):
        """关闭自动轮换。"""
        self.auto_rotation_enabled = False
        logger.info("Auto rotation disabled")

    def toggle_auto_rotation(self):
        """切换自动轮换状态。"""
        self.auto_rotation_enabled = not self.auto_rotation_enabled
        status = "enabled" if self.auto_rotation_enabled else "disabled"
        logger.info(f"Auto rotation toggled: {status}")
        self.save_state()
        return self.auto_rotation_enabled

    def get_current_credential_info(self) -> Dict:
        """获取当前使用的凭证信息。"""
        from config import get_rotation_count

        if not self.credentials:
            return {"status": "no_credentials"}

        rotation_count = get_rotation_count()

        if self.manual_selected_index is not None and 0 <= self.manual_selected_index < len(self.credentials):
            credential = self.credentials[self.manual_selected_index]
            return {
                "status": "manual_selected",
                "index": self.manual_selected_index,
                "filename": os.path.basename(credential["file_path"]),
                "user_id": credential["data"].get("user_id", "unknown"),
            }
        if not self.auto_rotation_enabled:
            if not (0 <= self.current_index < len(self.credentials)):
                self.current_index = 0
            credential = self.credentials[self.current_index]
            return {
                "status": "auto_rotation_disabled",
                "index": self.current_index,
                "filename": os.path.basename(credential["file_path"]),
                "user_id": credential["data"].get("user_id", "unknown"),
                "rotation_count": rotation_count,
                "auto_rotation_enabled": False,
            }
        if rotation_count == 0:
            if not (0 <= self.current_index < len(self.credentials)):
                self.current_index = 0
            credential = self.credentials[self.current_index]
            return {
                "status": "rotation_count_zero",
                "index": self.current_index,
                "filename": os.path.basename(credential["file_path"]),
                "user_id": credential["data"].get("user_id", "unknown"),
                "rotation_count": rotation_count,
                "auto_rotation_enabled": True,
            }

        if not (0 <= self.current_index < len(self.credentials)):
            self.current_index = 0
        credential = self.credentials[self.current_index]
        return {
            "status": "auto_rotation",
            "index": self.current_index,
            "filename": os.path.basename(credential["file_path"]),
            "user_id": credential["data"].get("user_id", "unknown"),
            "usage_count": self.usage_count,
            "rotation_count": rotation_count,
            "auto_rotation_enabled": True,
        }


class CodeBuddyTokenManagerRegistry:
    """按本系统用户隔离 CodeBuddy 凭证管理器。"""

    def __init__(self):
        self._managers: Dict[str, CodeBuddyTokenManager] = {}

    def for_user(self, user: AuthenticatedUser) -> CodeBuddyTokenManager:
        return self.for_username(user.username)

    def for_username(self, username: str) -> CodeBuddyTokenManager:
        owner_dirname = build_user_credentials_dirname(username)
        manager = self._managers.get(owner_dirname)
        if manager is None:
            from config import get_codebuddy_creds_dir

            user_creds_dir = os.path.join(get_codebuddy_creds_dir(), "users", owner_dirname)
            manager = CodeBuddyTokenManager(creds_dir=user_creds_dir)
            self._managers[owner_dirname] = manager
        return manager


codebuddy_token_managers = CodeBuddyTokenManagerRegistry()


def get_token_manager_for_user(user: AuthenticatedUser) -> CodeBuddyTokenManager:
    """获取当前本系统用户专属的 CodeBuddy 凭证管理器。"""
    return codebuddy_token_managers.for_user(user)
