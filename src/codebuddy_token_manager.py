"""CodeBuddy Token Manager - 管理 CodeBuddy 认证 token。"""
import hashlib
import logging
import os
import threading
import time
from copy import deepcopy
from typing import Any, Dict, List, Optional

from .auth_types import AuthenticatedUser
from .credential_rotation import CredentialRotationPolicy, TokenExpiry
from .credential_store import CodeBuddyCredentialStore, CredentialRecord, build_user_credentials_dirname

logger = logging.getLogger(__name__)


class CodeBuddyTokenManager:
    """CodeBuddy Token 管理器。"""

    def __init__(self, creds_dir=None, username: Optional[str] = None):
        if creds_dir is None:
            from config import get_codebuddy_creds_dir

            creds_dir = get_codebuddy_creds_dir()

        self.username = username
        self.store = CodeBuddyCredentialStore(creds_dir)
        self.creds_dir = self.store.creds_dir
        self.state_file = self.store.state_file
        self.token_expiry = TokenExpiry()
        self.rotation_policy = CredentialRotationPolicy(self.token_expiry)
        self.credentials: List[CredentialRecord] = []
        self.current_index: int = 0
        self.usage_count: int = 0
        self.auto_rotation_enabled: bool = True
        self._lock = threading.RLock()
        self._generations: Dict[str, int] = {}
        self._quota_generations: Dict[str, int] = {}
        self.load_all_tokens()
        self.load_state()

    def _is_auto_rotation_enabled(self) -> bool:
        from config import get_auto_rotation_enabled

        if self.username:
            return get_auto_rotation_enabled(self.username)
        return self.auto_rotation_enabled

    def _set_auto_rotation_enabled(self, enabled: bool) -> bool:
        from config import update_settings

        if self.username:
            update_settings({"CODEBUDDY_AUTO_ROTATION_ENABLED": enabled}, username=self.username)
        self.auto_rotation_enabled = enabled
        return enabled

    def load_all_tokens(self):
        """加载所有 token 文件。"""
        current_filename = self._credential_filename(self.current_index)
        self.credentials = self.store.load_credentials()
        from .codebuddy_oauth import TokenParser

        for record in self.credentials:
            data = record["data"]
            if data.get("credential_schema_version") == 2 and data.get("user_id"):
                continue
            normalized = TokenParser.build_credential_data(data)
            compatibility = normalized.setdefault("compatibility_data", {})
            if "full_response" in data:
                compatibility["legacy_full_response"] = data["full_response"]
            record["data"] = {
                **normalized,
                **{key: value for key, value in data.items() if key != "full_response"},
                "credential_schema_version": 2,
            }
        current_ids = {
            self._credential_id_from_filename(os.path.basename(record["file_path"]))
            for record in self.credentials
        }
        for credential_id in current_ids:
            self._generations.setdefault(credential_id, 0)
            self._quota_generations.setdefault(credential_id, 0)

        next_current_index = self._find_credential_index_by_filename(current_filename)
        if next_current_index is not None:
            self.current_index = next_current_index
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

    @staticmethod
    def _credential_id_from_filename(filename: str) -> str:
        """基于凭证文件名生成稳定、不可路径穿越的公开 ID。"""
        return hashlib.sha256(filename.encode("utf-8")).hexdigest()[:16]

    def _credential_id(self, index: Optional[int]) -> Optional[str]:
        filename = self._credential_filename(index)
        if not filename:
            return None
        return self._credential_id_from_filename(filename)

    def _find_credential_index_by_filename(self, filename: Optional[str]) -> Optional[int]:
        if not filename:
            return None
        for index, credential in enumerate(self.credentials):
            if os.path.basename(credential["file_path"]) == filename:
                return index
        return None

    def _find_credential_index_by_id(self, credential_id: str) -> Optional[int]:
        for index, credential in enumerate(self.credentials):
            filename = os.path.basename(credential["file_path"])
            if self._credential_id_from_filename(filename) == credential_id:
                return index
        return None

    def load_state(self):
        """加载管理器状态。"""
        try:
            state = self.store.load_manager_state()
            if not state:
                return

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

            logger.info(f"State loaded: auto_rotation={self._is_auto_rotation_enabled()}, current_index={self.current_index}")
        except Exception as e:
            logger.warning(f"Failed to load manager state: {e}")

    def save_state(self):
        """保存管理器状态。"""
        try:
            state = {
                "current_index": self.current_index,
                "current_filename": self._credential_filename(self.current_index),
                "saved_at": int(time.time()),
            }

            self.store.save_manager_state(state)
        except Exception as e:
            logger.error(f"Failed to save manager state: {e}")

    def is_token_expired(self, credential_data: Dict) -> bool:
        """检查 token 是否过期。"""
        return self.token_expiry.is_expired(credential_data)

    def get_next_credential(self) -> Optional[Dict]:
        """根据当前轮换策略获取下一个可用凭证。"""
        selected = self.select_next_credential()
        return selected[1] if selected is not None else None

    def select_next_credential(self) -> Optional[tuple[str, Dict, int]]:
        """原子返回下一张凭证的稳定 ID 与数据，供请求归属使用。"""
        from config import get_rotation_count

        with self._lock:
            selection = self.rotation_policy.select(
                credentials=self.credentials,
                current_index=self.current_index,
                usage_count=self.usage_count,
                auto_rotation_enabled=self._is_auto_rotation_enabled(),
                rotation_count=get_rotation_count(self.username),
            )
            self.current_index = selection.current_index
            self.usage_count = selection.usage_count

            if not selection.credential_record:
                return None

            if selection.log_message:
                logger.info(selection.log_message)

            filename = os.path.basename(selection.credential_record["file_path"])
            credential_id = self._credential_id_from_filename(filename)
            return (
                credential_id,
                selection.credential_record["data"],
                self._quota_generations.get(credential_id, 0),
            )

    def preview_next_credential(self) -> Optional[tuple[str, Dict]]:
        """预览下一张可用凭证，不修改当前索引和使用计数。"""
        from config import get_rotation_count

        selection = self.rotation_policy.select(
            credentials=self.credentials,
            current_index=self.current_index,
            usage_count=self.usage_count,
            auto_rotation_enabled=self._is_auto_rotation_enabled(),
            rotation_count=get_rotation_count(self.username),
        )
        if not selection.credential_record:
            return None
        filename = os.path.basename(selection.credential_record["file_path"])
        return (
            self._credential_id_from_filename(filename),
            selection.credential_record["data"],
        )

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

            if isinstance(data.get("expires_at"), (int, float)):
                expires_at = int(data["expires_at"])
                time_remaining = expires_at - int(time.time())
            elif data.get("created_at") and data.get("expires_in"):
                expires_at = data["created_at"] + data["expires_in"]
                time_remaining = expires_at - int(time.time())

            user_info = data.get("user_info", {})
            credentials_info.append(
                {
                    "credential_id": self._credential_id_from_filename(filename),
                    "index": index,
                    "filename": filename,
                    "user_id": data.get("user_id", "unknown"),
                    "email": user_info.get("email") or data.get("user_id"),
                    "nickname": data.get("nickname") or user_info.get("nickname"),
                    "preferred_username": user_info.get("preferred_username"),
                    "name": user_info.get("name"),
                    "created_at": data.get("created_at"),
                    "expires_in": data.get("expires_in"),
                    "expires_at": expires_at,
                    "time_remaining": time_remaining,
                    "is_expired": is_expired,
                    "token_type": data.get("token_type", "Bearer"),
                    "scope": data.get("scope"),
                    "domain": data.get("domain"),
                    "enterprise_id": data.get("enterprise_id"),
                    "enterprise_name": data.get("enterprise_name"),
                    "department_full_name": data.get("department_full_name"),
                    "account_type": data.get("account_type"),
                    "account_id": data.get("account_id"),
                    "account_count": len(data.get("accounts") or []),
                    "auth_source": data.get("auth_source", "manual"),
                    "has_refresh_token": bool(data.get("refresh_token")),
                    "session_state": data.get("session_state"),
                }
            )

        return credentials_info

    def get_credential_info_by_id(self, credential_id: str) -> Optional[Dict[str, Any]]:
        """按稳定 ID 返回不含凭证密钥的管理信息。"""
        return next(
            (
                info for info in self.get_credentials_info()
                if info.get("credential_id") == credential_id
            ),
            None,
        )

    def get_credential_by_id(self, credential_id: str) -> Optional[Dict]:
        """按公开 credential_id 获取凭证内容。"""
        index = self._find_credential_index_by_id(credential_id)
        if index is None:
            return None
        return self.credentials[index]["data"]

    def snapshot_credential_by_id(self, credential_id: str) -> Optional[tuple[Dict, int]]:
        """返回用于异步更新的凭证副本及当前 generation。"""
        with self._lock:
            index = self._find_credential_index_by_id(credential_id)
            if index is None:
                return None
            return deepcopy(self.credentials[index]["data"]), self._generations.get(credential_id, 0)

    def snapshot_credential_for_request_by_id(
            self, credential_id: str,
    ) -> Optional[tuple[Dict, int]]:
        """原子返回请求所用凭证副本与额度代次。"""
        with self._lock:
            index = self._find_credential_index_by_id(credential_id)
            if index is None:
                return None
            return (
                deepcopy(self.credentials[index]["data"]),
                self._quota_generations.get(credential_id, 0),
            )

    def get_quota_generation_by_id(self, credential_id: str) -> Optional[int]:
        """返回额度身份代次；已删除凭证仍保留最后代次以拒绝旧请求。"""
        with self._lock:
            return self._quota_generations.get(credential_id)

    def bump_quota_generation(self, credential_id: str) -> int:
        """使旧账号或旧凭证请求的额度用量失效。"""
        with self._lock:
            generation = self._quota_generations.get(credential_id, 0) + 1
            self._quota_generations[credential_id] = generation
            return generation

    def replace_credential_by_id(
            self,
            credential_id: str,
            credential_data: Dict[str, Any],
            *,
            expected_generation: int,
            quota_changed: bool = False,
    ) -> bool:
        """仅当凭证未被并发修改时原位原子更新。"""
        with self._lock:
            index = self._find_credential_index_by_id(credential_id)
            if index is None or self._generations.get(credential_id, 0) != expected_generation:
                return False
            filename = os.path.basename(self.credentials[index]["file_path"])
            self.store.replace_credential(credential_data, filename)
            self.credentials[index]["data"] = deepcopy(credential_data)
            self._generations[credential_id] = expected_generation + 1
            if quota_changed:
                self._quota_generations[credential_id] = (
                    self._quota_generations.get(credential_id, 0) + 1
                )
            return True

    def add_credential(self, bearer_token: str, filename: Optional[str] = None) -> bool:
        """添加新的凭证。"""
        if not filename:
            next_index = len(self.credentials) + 1
            while True:
                candidate = f"codebuddy_token_{next_index}.json"
                if not os.path.exists(self.store.resolve_credential_path(candidate)):
                    filename = candidate
                    break
                next_index += 1

        from .codebuddy_oauth import TokenParser

        credential_data = TokenParser.build_credential_data({"bearer_token": bearer_token})
        return self.add_credential_with_data(credential_data, filename)

    def add_credential_with_data(self, credential_data: Dict[str, Any], filename: Optional[str] = None) -> bool:
        """添加新的凭证。"""
        if not filename:
            user_id = credential_data.get("user_id", "unknown")
            timestamp = credential_data.get("created_at", int(time.time()))
            safe_user_id = "".join(c for c in str(user_id) if c.isalnum() or c in "._-")[:20]
            filename = f"codebuddy_{safe_user_id}_{timestamp}.json"
        if "created_at" not in credential_data:
            credential_data["created_at"] = int(time.time())

        while True:
            candidate = self.store.next_available_filename(filename)
            try:
                safe_filename = self.store.save_credential(
                    credential_data, candidate, indent=4, create_new=True
                )
                logger.info(f"Added new credential: {safe_filename}")
                self.load_all_tokens()
                return True
            except FileExistsError:
                logger.debug("Credential filename collision, retrying: %s", candidate)
            except Exception as e:
                logger.error(f"Failed to save credential: {e}")
                return False

    def delete_credential_by_index(self, index: int) -> bool:
        """删除指定索引的凭证文件，并重新加载列表。"""
        try:
            with self._lock:
                if not (0 <= index < len(self.credentials)):
                    logger.error(f"Invalid credential index for deletion: {index}")
                    return False

                file_path = self.credentials[index]["file_path"]
                filename = os.path.basename(file_path)
                credential_id = self._credential_id_from_filename(filename)

                if self.store.delete_credential_file(file_path):
                    logger.info(f"Deleted credential file: {filename}")
                else:
                    logger.warning(f"Credential file already missing: {filename}")

                self._generations[credential_id] = self._generations.get(credential_id, 0) + 1
                self.bump_quota_generation(credential_id)
                self.load_all_tokens()
                return True
        except Exception as e:
            logger.error(f"Failed to delete credential at index {index}: {e}")
            return False

    def delete_credential_by_id(self, credential_id: str) -> bool:
        """按公开 credential_id 删除凭证文件。"""
        with self._lock:
            index = self._find_credential_index_by_id(credential_id)
            if index is None:
                return False
            return self.delete_credential_by_index(index)

    def set_current_credential(self, index: int) -> bool:
        """选择指定索引的凭证，并固定使用该凭证。"""
        if 0 <= index < len(self.credentials):
            self.current_index = index
            self.usage_count = 0
            self.disable_auto_rotation()
            credential_filename = os.path.basename(self.credentials[index]["file_path"])
            logger.info(f"Selected fixed credential: {credential_filename} (index: {index})")
            self.save_state()
            return True

        logger.error(f"Invalid credential index: {index}")
        return False

    def set_current_credential_by_id(self, credential_id: str) -> bool:
        """按公开 credential_id 选择当前凭证。"""
        index = self._find_credential_index_by_id(credential_id)
        if index is None:
            logger.error("Invalid credential id: %s", credential_id)
            return False
        return self.set_current_credential(index)

    def enable_auto_rotation(self):
        """开启自动轮换。"""
        self.usage_count = 0
        self._set_auto_rotation_enabled(True)
        logger.info("Auto rotation enabled")
        self.save_state()

    def disable_auto_rotation(self):
        """关闭自动轮换。"""
        self._set_auto_rotation_enabled(False)
        logger.info("Auto rotation disabled")
        self.save_state()

    def toggle_auto_rotation(self):
        """切换自动轮换状态。"""
        enabled = not self._is_auto_rotation_enabled()
        if enabled:
            self.enable_auto_rotation()
        else:
            self.disable_auto_rotation()
        status = "enabled" if enabled else "disabled"
        logger.info(f"Auto rotation toggled: {status}")
        return enabled

    def get_current_credential_info(self) -> Dict:
        """获取当前使用的凭证信息。"""
        from config import get_rotation_count

        rotation_count = get_rotation_count(self.username)
        auto_rotation_enabled = self._is_auto_rotation_enabled()

        if not self.credentials:
            return {
                "status": "no_credentials",
                "rotation_count": rotation_count,
                "auto_rotation_enabled": auto_rotation_enabled,
            }

        if not auto_rotation_enabled:
            if not (0 <= self.current_index < len(self.credentials)):
                self.current_index = 0
            credential = self.credentials[self.current_index]
            filename = os.path.basename(credential["file_path"])
            return {
                "status": "auto_rotation_disabled",
                "credential_id": self._credential_id_from_filename(filename),
                "index": self.current_index,
                "filename": filename,
                "user_id": credential["data"].get("user_id", "unknown"),
                "enterprise_id": credential["data"].get("enterprise_id"),
                "rotation_count": rotation_count,
                "auto_rotation_enabled": False,
            }

        if not (0 <= self.current_index < len(self.credentials)):
            self.current_index = 0
        credential = self.credentials[self.current_index]
        filename = os.path.basename(credential["file_path"])
        return {
            "status": "auto_rotation",
            "credential_id": self._credential_id_from_filename(filename),
            "index": self.current_index,
            "filename": filename,
            "user_id": credential["data"].get("user_id", "unknown"),
            "enterprise_id": credential["data"].get("enterprise_id"),
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
            manager = CodeBuddyTokenManager(creds_dir=user_creds_dir, username=username)
            self._managers[owner_dirname] = manager
        return manager


codebuddy_token_managers = CodeBuddyTokenManagerRegistry()


def get_token_manager_for_user(user: AuthenticatedUser) -> CodeBuddyTokenManager:
    """获取当前本系统用户专属的 CodeBuddy 凭证管理器。"""
    return codebuddy_token_managers.for_user(user)
