"""CodeBuddy 凭证文件存储。"""
import glob
import hashlib
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, TypedDict

logger = logging.getLogger(__name__)


class CredentialRecord(TypedDict):
    """单个凭证文件及其解析后的内容。"""

    file_path: str
    data: Dict[str, Any]


def build_user_credentials_dirname(username: str) -> str:
    """为本系统用户生成稳定且安全的凭证目录名。"""
    normalized = re.sub(r"[^A-Za-z0-9._-]", "_", str(username or "").strip())[:48]
    normalized = normalized.strip("._-") or "user"
    digest = hashlib.sha256(str(username or "").encode("utf-8")).hexdigest()[:12]
    return f"{normalized}_{digest}"


class CodeBuddyCredentialStore:
    """负责 CodeBuddy 凭证目录中的文件安全读写。"""

    def __init__(self, creds_dir: str):
        self.creds_dir = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", creds_dir))
        self.state_file = os.path.join(self.creds_dir, "manager_state.json")

    def load_credentials(self) -> List[CredentialRecord]:
        """加载所有 token 文件。"""
        credentials: List[CredentialRecord] = []
        logger.info(f"Loading CodeBuddy credentials from: {self.creds_dir}")

        if not os.path.exists(self.creds_dir):
            os.makedirs(self.creds_dir, exist_ok=True)
            logger.warning(f"Credentials directory created at {self.creds_dir}. No credentials found.")
            return credentials

        token_files = sorted(glob.glob(os.path.join(self.creds_dir, "*.json")))
        for file_path in token_files:
            try:
                if os.path.basename(file_path) == os.path.basename(self.state_file):
                    continue

                if os.path.islink(file_path):
                    logger.warning(f"Skipping symlink credential file: {os.path.basename(file_path)}")
                    continue

                real_file_path = os.path.realpath(file_path)
                if not self.is_path_inside_creds_dir(real_file_path):
                    logger.warning(f"Skipping credential outside credentials directory: {os.path.basename(file_path)}")
                    continue

                with open(real_file_path, "r", encoding="utf-8") as f:
                    data: Dict[str, Any] = json.load(f)
                    if "bearer_token" in data:
                        credential: CredentialRecord = {
                            "file_path": real_file_path,
                            "data": data,
                        }
                        credentials.append(credential)
                        logger.info(f"Successfully loaded credential: {os.path.basename(real_file_path)}")
                    else:
                        logger.warning(
                            f"Skipping invalid credential file (missing bearer_token): {os.path.basename(file_path)}")
            except Exception as e:
                logger.error(f"Failed to load credential file {os.path.basename(file_path)}: {e}")

        logger.info(f"Loaded a total of {len(credentials)} CodeBuddy credentials.")
        return credentials

    def load_manager_state(self) -> Optional[Dict[str, Any]]:
        """加载管理器状态文件。"""
        if not os.path.exists(self.state_file):
            return None

        if os.path.islink(self.state_file) or not self.is_path_inside_creds_dir(os.path.realpath(self.state_file)):
            logger.warning("Skipping unsafe manager state file")
            return None

        with open(self.state_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_manager_state(self, state: Dict[str, Any]):
        """保存管理器状态文件。"""
        self.ensure_directory()
        self.write_json_file(self.state_file, state, indent=2)
        logger.debug(f"Manager state saved to {self.state_file}")

    def save_credential(self, credential_data: Dict[str, Any], filename: str, indent: int = 4, create_new: bool = False) -> str:
        """保存凭证数据并返回安全文件名；新增凭证时拒绝覆盖已有文件。"""
        safe_filename = self.sanitize_filename(filename)
        file_path = self.resolve_credential_path(safe_filename)
        self.ensure_directory()
        self.write_json_file(file_path, credential_data, indent=indent, create_new=create_new)
        return safe_filename

    def next_available_filename(self, filename: str) -> str:
        """基于候选文件名查找当前凭证目录中不会碰撞的安全文件名。"""
        safe_filename = self.sanitize_filename(filename)
        if not os.path.exists(self.resolve_credential_path(safe_filename)):
            return safe_filename

        stem, ext = os.path.splitext(safe_filename)
        suffix = 1
        while True:
            candidate = f"{stem}_{suffix}{ext or '.json'}"
            if not os.path.exists(self.resolve_credential_path(candidate)):
                return candidate
            suffix += 1

    def delete_credential_file(self, file_path: str) -> bool:
        """删除指定凭证文件。"""
        real_file_path = os.path.realpath(file_path)
        if not self.is_path_inside_creds_dir(real_file_path):
            raise ValueError("Credential path resolves outside credentials directory")

        if os.path.exists(real_file_path):
            os.remove(real_file_path)
            return True
        return False

    def ensure_directory(self):
        if not os.path.exists(self.creds_dir):
            os.makedirs(self.creds_dir, exist_ok=True)

    def sanitize_filename(self, filename: str) -> str:
        """清理凭证文件名，禁止路径穿越和特殊路径。"""
        filename = os.path.basename(str(filename).strip())
        filename = re.sub(r"[^A-Za-z0-9._-]", "_", filename)
        if not filename or filename in (".", ".."):
            filename = f"codebuddy_token_{int(time.time())}.json"
        if not filename.endswith(".json"):
            filename += ".json"
        return filename

    def resolve_credential_path(self, filename: str) -> str:
        """解析凭证路径，并确保目标仍在凭证目录内。"""
        file_path = os.path.realpath(os.path.join(self.creds_dir, filename))
        if not self.is_path_inside_creds_dir(file_path):
            raise ValueError("Credential filename resolves outside credentials directory")
        return file_path

    def is_path_inside_creds_dir(self, file_path: str) -> bool:
        """判断真实文件路径是否位于凭证目录内。"""
        real_file_path = os.path.realpath(file_path)
        creds_dir_with_sep = self.creds_dir + os.sep
        return real_file_path != self.creds_dir and real_file_path.startswith(creds_dir_with_sep)

    def write_json_file(self, file_path: str, data: Dict[str, Any], indent: int, create_new: bool = False):
        """以 0600 权限写 JSON 文件，并在新增凭证时通过原子创建避免覆盖。"""
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if create_new:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW

        fd = os.open(file_path, flags, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                fd = None
                json.dump(data, f, indent=indent, ensure_ascii=False)
            os.chmod(file_path, 0o600)
        except Exception:
            if fd is not None:
                os.close(fd)
            raise
