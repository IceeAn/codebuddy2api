"""
CodeBuddy Token Manager - 管理CodeBuddy认证token
"""
import os
import glob
import hashlib
import json
import time
import logging
import re
from typing import Dict, Optional, List, Any
from .auth import AuthenticatedUser
from .usage_stats_manager import usage_stats_manager

logger = logging.getLogger(__name__)


def build_user_credentials_dirname(username: str) -> str:
    """为本系统用户生成稳定且安全的凭证目录名。"""
    normalized = re.sub(r"[^A-Za-z0-9._-]", "_", str(username or "").strip())[:48]
    normalized = normalized.strip("._-") or "user"
    digest = hashlib.sha256(str(username or "").encode("utf-8")).hexdigest()[:12]
    return f"{normalized}_{digest}"


class CodeBuddyTokenManager:
    """CodeBuddy Token管理器"""
    
    def __init__(self, creds_dir=None):
        if creds_dir is None:
            from config import get_codebuddy_creds_dir, get_rotation_count
            creds_dir = get_codebuddy_creds_dir()
        
        self.creds_dir = os.path.realpath(os.path.join(os.path.dirname(__file__), '..', creds_dir))
        self.state_file = os.path.join(self.creds_dir, 'manager_state.json')
        self.credentials = []
        self.current_index = 0  # Start from the first credential
        self.usage_count = 0    # Counter for the current credential usage
        self.manual_selected_index = None  # 手动选择的凭证索引
        self.auto_rotation_enabled = True  # 自动轮换开关，默认开启
        self.load_all_tokens()
        self.load_state()  # 加载保存的状态
    
    def load_all_tokens(self):
        """加载所有token文件"""
        self.credentials = []
        self.current_index = -1
        
        logger.info(f"Loading CodeBuddy credentials from: {self.creds_dir}")
        
        if not os.path.exists(self.creds_dir):
            os.makedirs(self.creds_dir, exist_ok=True)
            logger.warning(f"Credentials directory created at {self.creds_dir}. No credentials found.")
            return
        
        token_files = sorted(glob.glob(os.path.join(self.creds_dir, '*.json')))
        for file_path in token_files:
            try:
                if os.path.basename(file_path) == os.path.basename(self.state_file):
                    continue

                if os.path.islink(file_path):
                    logger.warning(f"Skipping symlink credential file: {os.path.basename(file_path)}")
                    continue

                real_file_path = os.path.realpath(file_path)
                if not self._is_path_inside_creds_dir(real_file_path):
                    logger.warning(f"Skipping credential outside credentials directory: {os.path.basename(file_path)}")
                    continue

                with open(real_file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if 'bearer_token' in data:
                        self.credentials.append({
                            'file_path': real_file_path,
                            'data': data
                        })
                        logger.info(f"Successfully loaded credential: {os.path.basename(real_file_path)}")
                    else:
                        logger.warning(f"Skipping invalid credential file (missing bearer_token): {os.path.basename(file_path)}")
            except Exception as e:
                logger.error(f"Failed to load credential file {os.path.basename(file_path)}: {e}")
        
        logger.info(f"Loaded a total of {len(self.credentials)} CodeBuddy credentials.")
    
    def load_state(self):
        """加载管理器状态"""
        try:
            if os.path.exists(self.state_file):
                if os.path.islink(self.state_file) or not self._is_path_inside_creds_dir(os.path.realpath(self.state_file)):
                    logger.warning("Skipping unsafe manager state file")
                    return

                with open(self.state_file, 'r', encoding='utf-8') as f:
                    state = json.load(f)
                    
                # 恢复状态，但要验证索引是否还有效
                saved_manual_index = state.get('manual_selected_index')
                if saved_manual_index is not None and 0 <= saved_manual_index < len(self.credentials):
                    # 验证凭证文件是否还存在
                    if saved_manual_index < len(self.credentials):
                        saved_filename = state.get('manual_selected_filename')
                        current_filename = os.path.basename(self.credentials[saved_manual_index]['file_path'])
                        if saved_filename == current_filename:
                            self.manual_selected_index = saved_manual_index
                            self.current_index = saved_manual_index
                            logger.info(f"Restored manual selection: {current_filename} (index: {saved_manual_index})")
                        else:
                            logger.warning(f"Saved credential filename mismatch, ignoring saved selection")
                
                # 恢复自动轮换状态
                self.auto_rotation_enabled = state.get('auto_rotation_enabled', True)
                
                # 恢复当前索引（如果没有手动选择的话）
                if self.manual_selected_index is None:
                    saved_current_index = state.get('current_index', 0)
                    if 0 <= saved_current_index < len(self.credentials):
                        self.current_index = saved_current_index
                    
                logger.info(f"State loaded: auto_rotation={self.auto_rotation_enabled}, current_index={self.current_index}")
        except Exception as e:
            logger.warning(f"Failed to load manager state: {e}")
    
    def save_state(self):
        """保存管理器状态"""
        try:
            # 确保目录存在
            if not os.path.exists(self.creds_dir):
                os.makedirs(self.creds_dir, exist_ok=True)
            
            state = {
                'auto_rotation_enabled': self.auto_rotation_enabled,
                'current_index': self.current_index,
                'manual_selected_index': self.manual_selected_index,
                'manual_selected_filename': None,
                'saved_at': int(time.time())
            }
            
            # 如果有手动选择，保存文件名用于验证
            if self.manual_selected_index is not None and 0 <= self.manual_selected_index < len(self.credentials):
                state['manual_selected_filename'] = os.path.basename(
                    self.credentials[self.manual_selected_index]['file_path']
                )
            
            self._write_json_file(self.state_file, state, indent=2)
                
            logger.debug(f"Manager state saved to {self.state_file}")
        except Exception as e:
            logger.error(f"Failed to save manager state: {e}")
    
    def is_token_expired(self, credential_data: Dict) -> bool:
        """检查token是否过期"""
        try:
            created_at = credential_data.get('created_at')
            expires_in = credential_data.get('expires_in')
            
            if not created_at or not expires_in:
                # 如果没有过期信息，按未过期处理。
                return False
            
            current_time = int(time.time())
            expiry_time = created_at + expires_in
            
            # 提前5分钟认为过期，留出刷新时间
            buffer_time = 300  # 5分钟
            is_expired = current_time >= (expiry_time - buffer_time)
            
            if is_expired:
                user_id = credential_data.get('user_id', 'unknown')
                logger.warning(f"Token for user {user_id} is expired or will expire soon")
            
            return is_expired
        except Exception as e:
            logger.error(f"Error checking token expiry: {e}")
            return False
    
    def get_next_credential(self) -> Optional[Dict]:
        """获取下一个可用的凭证，根据轮换策略，并检查过期状态"""
        from config import get_rotation_count

        if not self.credentials:
            return None
        
        # 过滤掉过期的凭证
        valid_credentials = []
        for i, cred in enumerate(self.credentials):
            if not self.is_token_expired(cred['data']):
                valid_credentials.append((i, cred))
            else:
                filename = os.path.basename(cred['file_path'])
                logger.warning(f"Skipping expired credential: {filename}")
        
        if not valid_credentials:
            logger.error("No valid (non-expired) credentials available")
            return None
        
        # 如果当前索引无效或指向过期凭证，重置到第一个有效凭证
        current_valid_indices = [i for i, _ in valid_credentials]
        if self.current_index not in current_valid_indices:
            self.current_index = current_valid_indices[0]
            self.usage_count = 0
            logger.info(f"Reset to first valid credential index: {self.current_index}")

        rotation_count = get_rotation_count()
        
        # 如果有手动选择的凭证，优先使用（如果未过期）
        if self.manual_selected_index is not None and 0 <= self.manual_selected_index < len(self.credentials):
            manual_cred = self.credentials[self.manual_selected_index]
            if not self.is_token_expired(manual_cred['data']):
                credential_filename = os.path.basename(manual_cred['file_path'])
                usage_stats_manager.record_credential_usage(credential_filename)
                logger.info(f"Using manually selected credential: {credential_filename}")
                return manual_cred['data']
            else:
                logger.warning("Manually selected credential is expired, falling back to automatic rotation")
                self.manual_selected_index = None
        
        # 找到当前索引在有效凭证中的位置
        try:
            current_valid_position = current_valid_indices.index(self.current_index)
        except ValueError:
            current_valid_position = 0
            self.current_index = current_valid_indices[0]
            self.usage_count = 0
        
        # 检查是否需要轮换：需要同时满足自动轮换开启 且 轮换次数大于0
        should_rotate = self.auto_rotation_enabled and rotation_count > 0
        
        if not should_rotate:
            # 不轮换：固定使用当前凭证
            credential = self.credentials[self.current_index]
            credential_filename = os.path.basename(credential['file_path'])
            usage_stats_manager.record_credential_usage(credential_filename)
            if rotation_count == 0:
                logger.info(f"Using fixed credential (rotation count is 0): {credential_filename}")
            else:
                logger.info(f"Using fixed credential (auto rotation disabled): {credential_filename}")
            return credential['data']

        # 自动轮换逻辑：当开关开启且轮换次数>0时
        if self.usage_count >= rotation_count:
            # 轮换到下一个有效凭证
            next_valid_position = (current_valid_position + 1) % len(valid_credentials)
            self.current_index = current_valid_indices[next_valid_position]
            self.usage_count = 0  # 重置计数器
            logger.info("Credential rotation triggered.")

        credential = self.credentials[self.current_index]
        self.usage_count += 1
        
        # Record usage stats
        credential_filename = os.path.basename(credential['file_path'])
        usage_stats_manager.record_credential_usage(credential_filename)
        
        logger.info(
            f"Using credential: {credential_filename} "
            f"(Usage: {self.usage_count}/{rotation_count})"
        )
        return credential['data']
    
    def get_all_credentials(self) -> List[Dict]:
        """获取所有凭证"""
        return [cred['data'] for cred in self.credentials]
    
    def get_credentials_info(self) -> List[Dict]:
        """获取所有凭证的详细信息，包括过期状态"""
        credentials_info = []
        for i, cred in enumerate(self.credentials):
            data = cred['data']
            filename = os.path.basename(cred['file_path'])
            
            # 计算过期信息
            is_expired = self.is_token_expired(data)
            expires_at = None
            time_remaining = None
            
            if data.get('created_at') and data.get('expires_in'):
                expires_at = data['created_at'] + data['expires_in']
                time_remaining = expires_at - int(time.time())
            
            # 提取用户信息
            user_info = data.get('user_info', {})
            
            info = {
                'index': i,
                'filename': filename,
                'user_id': data.get('user_id', 'unknown'),
                'email': user_info.get('email') or data.get('user_id'),
                'name': user_info.get('name'),
                'created_at': data.get('created_at'),
                'expires_in': data.get('expires_in'),
                'expires_at': expires_at,
                'time_remaining': time_remaining,
                'is_expired': is_expired,
                'token_type': data.get('token_type', 'Bearer'),
                'scope': data.get('scope'),
                'domain': data.get('domain'),
                'has_refresh_token': bool(data.get('refresh_token')),
                'session_state': data.get('session_state')
            }
            
            credentials_info.append(info)
        
        return credentials_info
    
    def add_credential(self, bearer_token: str, user_id: str = None, filename: str = None) -> bool:
        """添加新的凭证。"""
        if not filename:
            filename = f"codebuddy_token_{len(self.credentials) + 1}.json"
        
        credential_data = {
            "bearer_token": bearer_token,
            "user_id": user_id,
            "created_at": int(time.time())
        }
        
        return self.add_credential_with_data(credential_data, filename)
    
    def add_credential_with_data(self, credential_data: Dict[str, Any], filename: str = None) -> bool:
        """添加新的凭证（完整数据版本）"""
        if not filename:
            user_id = credential_data.get('user_id', 'unknown')
            timestamp = credential_data.get('created_at', int(time.time()))
            safe_user_id = "".join(c for c in str(user_id) if c.isalnum() or c in "._-")[:20]
            filename = f"codebuddy_{safe_user_id}_{timestamp}.json"
        
        filename = self._sanitize_filename(filename)
        file_path = self._resolve_credential_path(filename)
        
        # 确保必要字段存在
        if 'created_at' not in credential_data:
            credential_data['created_at'] = int(time.time())
        
        try:
            # 确保目录存在
            if not os.path.exists(self.creds_dir):
                os.makedirs(self.creds_dir, exist_ok=True)
            
            self._write_json_file(file_path, credential_data, indent=4)
            
            logger.info(f"Added new credential: {filename}")
            self.load_all_tokens()  # 重新加载
            return True
        except Exception as e:
            logger.error(f"Failed to save credential: {e}")
            return False

    def _sanitize_filename(self, filename: str) -> str:
        """清理凭证文件名，禁止路径穿越和特殊路径。"""
        filename = os.path.basename(str(filename).strip())
        filename = re.sub(r"[^A-Za-z0-9._-]", "_", filename)
        if not filename or filename in (".", ".."):
            filename = f"codebuddy_token_{int(time.time())}.json"
        if not filename.endswith(".json"):
            filename += ".json"
        return filename

    def _resolve_credential_path(self, filename: str) -> str:
        """解析凭证路径，并确保目标仍在凭证目录内。"""
        file_path = os.path.realpath(os.path.join(self.creds_dir, filename))
        if not self._is_path_inside_creds_dir(file_path):
            raise ValueError("Credential filename resolves outside credentials directory")
        return file_path

    def _is_path_inside_creds_dir(self, file_path: str) -> bool:
        """判断真实文件路径是否位于凭证目录内。"""
        real_file_path = os.path.realpath(file_path)
        creds_dir_with_sep = self.creds_dir + os.sep
        return real_file_path != self.creds_dir and real_file_path.startswith(creds_dir_with_sep)

    def _write_json_file(self, file_path: str, data: Dict[str, Any], indent: int):
        """以 0600 权限写 JSON 文件，并避免跟随符号链接。"""
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW

        fd = os.open(file_path, flags, 0o600)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                fd = None
                json.dump(data, f, indent=indent, ensure_ascii=False)
            os.chmod(file_path, 0o600)
        except Exception:
            if fd is not None:
                os.close(fd)
            raise

    def delete_credential_by_index(self, index: int) -> bool:
        """删除指定索引的凭证文件，并重新加载列表"""
        try:
            if not (0 <= index < len(self.credentials)):
                logger.error(f"Invalid credential index for deletion: {index}")
                return False

            file_path = self.credentials[index]['file_path']
            filename = os.path.basename(file_path)

            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Deleted credential file: {filename}")
            else:
                logger.warning(f"Credential file already missing: {filename}")

            # 重新加载凭证列表，重置索引等状态
            self.load_all_tokens()
            # 清理手动选择（若已删除的索引影响手动选择状态）
            if self.manual_selected_index is not None and self.manual_selected_index == index:
                self.manual_selected_index = None
                logger.info("Cleared manual selection because deleted credential was selected")
            return True
        except Exception as e:
            logger.error(f"Failed to delete credential at index {index}: {e}")
            return False

    def set_manual_credential(self, index: int) -> bool:
        """手动选择指定索引的凭证"""
        if 0 <= index < len(self.credentials):
            self.manual_selected_index = index
            self.current_index = index  # 更新当前索引
            credential_filename = os.path.basename(self.credentials[index]['file_path'])
            logger.info(f"Manually selected credential: {credential_filename} (index: {index})")
            self.save_state()  # 保存状态
            return True
        else:
            logger.error(f"Invalid credential index: {index}")
            return False
    
    def clear_manual_selection(self):
        """清除手动选择，恢复自动轮换"""
        self.manual_selected_index = None
        logger.info("Cleared manual credential selection, resumed automatic rotation")
        self.save_state()  # 保存状态
    
    def enable_auto_rotation(self):
        """开启自动轮换"""
        self.auto_rotation_enabled = True
        logger.info("Auto rotation enabled")
    
    def disable_auto_rotation(self):
        """关闭自动轮换"""
        self.auto_rotation_enabled = False
        logger.info("Auto rotation disabled")
    
    def toggle_auto_rotation(self):
        """切换自动轮换状态"""
        self.auto_rotation_enabled = not self.auto_rotation_enabled
        status = "enabled" if self.auto_rotation_enabled else "disabled"
        logger.info(f"Auto rotation toggled: {status}")
        self.save_state()  # 保存状态
        return self.auto_rotation_enabled
    
    def get_current_credential_info(self) -> Dict:
        """获取当前使用的凭证信息"""
        from config import get_rotation_count
        
        if not self.credentials:
            return {"status": "no_credentials"}
        
        rotation_count = get_rotation_count()
        
        if self.manual_selected_index is not None and 0 <= self.manual_selected_index < len(self.credentials):
            credential = self.credentials[self.manual_selected_index]
            return {
                "status": "manual_selected",
                "index": self.manual_selected_index,
                "filename": os.path.basename(credential['file_path']),
                "user_id": credential['data'].get('user_id', 'unknown')
            }
        elif not self.auto_rotation_enabled:
            # 确保current_index有效
            if not (0 <= self.current_index < len(self.credentials)):
                self.current_index = 0
            credential = self.credentials[self.current_index]
            return {
                "status": "auto_rotation_disabled",
                "index": self.current_index,
                "filename": os.path.basename(credential['file_path']),
                "user_id": credential['data'].get('user_id', 'unknown'),
                "rotation_count": rotation_count,
                "auto_rotation_enabled": False
            }
        elif rotation_count == 0:
            # 确保current_index有效
            if not (0 <= self.current_index < len(self.credentials)):
                self.current_index = 0
            credential = self.credentials[self.current_index]
            return {
                "status": "rotation_count_zero",
                "index": self.current_index,
                "filename": os.path.basename(credential['file_path']),
                "user_id": credential['data'].get('user_id', 'unknown'),
                "rotation_count": rotation_count,
                "auto_rotation_enabled": True
            }
        else:
            # 确保current_index有效
            if not (0 <= self.current_index < len(self.credentials)):
                self.current_index = 0
            credential = self.credentials[self.current_index]
            return {
                "status": "auto_rotation",
                "index": self.current_index,
                "filename": os.path.basename(credential['file_path']),
                "user_id": credential['data'].get('user_id', 'unknown'),
                "usage_count": self.usage_count,
                "rotation_count": rotation_count,
                "auto_rotation_enabled": True
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
