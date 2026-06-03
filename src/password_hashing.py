"""
密码哈希工具。
"""
import base64
import binascii
import hashlib
import hmac
import secrets

PBKDF2_ALGORITHM = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 390000


def create_password_hash(password: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    """生成可写入 secrets/users.txt 的 PBKDF2-SHA256 密码哈希。"""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    salt_b64 = base64.urlsafe_b64encode(salt).decode("ascii").rstrip("=")
    digest_b64 = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return f"{PBKDF2_ALGORITHM}${iterations}${salt_b64}${digest_b64}"


def verify_password(password: str, password_hash: str) -> bool:
    """校验 PBKDF2-SHA256 密码哈希。"""
    try:
        algorithm, iterations_raw, salt_b64, digest_b64 = password_hash.split("$", 3)
        if algorithm != PBKDF2_ALGORITHM:
            return False

        iterations = int(iterations_raw)
        if iterations < 100000:
            return False

        salt = _decode_unpadded_base64(salt_b64)
        expected_digest = _decode_unpadded_base64(digest_b64)
        actual_digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual_digest, expected_digest)
    except (ValueError, binascii.Error):
        return False


def _decode_unpadded_base64(value: str) -> bytes:
    missing_padding = len(value) % 4
    if missing_padding:
        value += "=" * (4 - missing_padding)
    return base64.urlsafe_b64decode(value.encode("ascii"))
