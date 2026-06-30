"""CodeBuddy 凭证过期判断和轮换策略。"""
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .credential_store import CredentialRecord

logger = logging.getLogger(__name__)


class TokenExpiry:
    """判断 CodeBuddy token 是否已过期。"""

    def is_expired(self, credential_data: Dict[str, Any]) -> bool:
        try:
            created_at = credential_data.get("created_at")
            expires_in = credential_data.get("expires_in")

            if not created_at or not expires_in:
                return False

            current_time = int(time.time())
            expiry_time = created_at + expires_in
            buffer_time = 300
            is_expired = current_time >= (expiry_time - buffer_time)

            if is_expired:
                user_id = credential_data.get("user_id", "unknown")
                logger.warning(f"Token for user {user_id} is expired or will expire soon")

            return is_expired
        except Exception as e:
            logger.error(f"Error checking token expiry: {e}")
            return False


@dataclass
class CredentialSelection:
    """轮换策略选择结果。"""

    credential_record: Optional[CredentialRecord]
    current_index: int
    usage_count: int
    log_message: Optional[str] = None

    @property
    def filename(self) -> Optional[str]:
        if not self.credential_record:
            return None
        return os.path.basename(self.credential_record["file_path"])


class CredentialRotationPolicy:
    """根据当前凭证、自动轮换开关和轮换次数选择凭证。"""

    def __init__(self, token_expiry: TokenExpiry):
        self.token_expiry = token_expiry

    def select(
        self,
        credentials: List[CredentialRecord],
        current_index: int,
        usage_count: int,
        auto_rotation_enabled: bool,
        rotation_count: int,
    ) -> CredentialSelection:
        if not credentials:
            return CredentialSelection(None, current_index, usage_count)

        valid_credentials = []
        for index, credential in enumerate(credentials):
            if not self.token_expiry.is_expired(credential["data"]):
                valid_credentials.append((index, credential))
            else:
                filename = os.path.basename(credential["file_path"])
                logger.warning(f"Skipping expired credential: {filename}")

        if not valid_credentials:
            logger.error("No valid (non-expired) credentials available")
            return CredentialSelection(None, current_index, usage_count)

        current_valid_indices = [index for index, _ in valid_credentials]
        if current_index not in current_valid_indices:
            current_index = current_valid_indices[0]
            usage_count = 0
            logger.info(f"Reset to first valid credential index: {current_index}")

        try:
            current_valid_position = current_valid_indices.index(current_index)
        except ValueError:
            current_valid_position = 0
            current_index = current_valid_indices[0]
            usage_count = 0

        should_rotate = auto_rotation_enabled
        if not should_rotate:
            credential = credentials[current_index]
            filename = os.path.basename(credential["file_path"])
            message = f"Using fixed credential (auto rotation disabled): {filename}"
            return CredentialSelection(
                credential,
                current_index,
                usage_count,
                message,
            )

        if usage_count >= rotation_count:
            next_valid_position = (current_valid_position + 1) % len(valid_credentials)
            current_index = current_valid_indices[next_valid_position]
            usage_count = 0
            logger.info("Credential rotation triggered.")

        credential = credentials[current_index]
        usage_count += 1
        return CredentialSelection(
            credential,
            current_index,
            usage_count,
            f"Using credential: {os.path.basename(credential['file_path'])} (Usage: {usage_count}/{rotation_count})",
        )
