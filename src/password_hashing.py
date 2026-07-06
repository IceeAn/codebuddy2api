"""
密码哈希工具。
"""
import base64
import binascii
import hashlib
import hmac
import secrets

PBKDF2_ALGORITHM = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 600000
PBKDF2_MIN_ITERATIONS = 600000
PBKDF2_MAX_ITERATIONS = 1000000
PBKDF2_SALT_BYTES = 16
PBKDF2_DIGEST_BYTES = 32


def create_password_hash(password: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    """生成可写入 secrets/users.txt 的 PBKDF2-SHA256 密码哈希。"""
    if (
        isinstance(iterations, bool)
        or not isinstance(iterations, int)
        or not PBKDF2_MIN_ITERATIONS <= iterations <= PBKDF2_MAX_ITERATIONS
    ):
        raise ValueError("PBKDF2 iterations are outside the supported range")

    salt = secrets.token_bytes(PBKDF2_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
        dklen=PBKDF2_DIGEST_BYTES,
    )
    salt_b64 = base64.urlsafe_b64encode(salt).decode("ascii").rstrip("=")
    digest_b64 = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return f"{PBKDF2_ALGORITHM}${iterations}${salt_b64}${digest_b64}"


def verify_password(password: str, password_hash: str) -> bool:
    """校验 PBKDF2-SHA256 密码哈希。"""
    try:
        algorithm, iterations_raw, salt_b64, digest_b64 = password_hash.split("$")
        if algorithm != PBKDF2_ALGORITHM:
            return False

        if not iterations_raw.isascii() or not iterations_raw.isdigit():
            return False
        iterations = int(iterations_raw)
        if str(iterations) != iterations_raw:
            return False
        if not PBKDF2_MIN_ITERATIONS <= iterations <= PBKDF2_MAX_ITERATIONS:
            return False

        salt = _decode_unpadded_base64(salt_b64)
        expected_digest = _decode_unpadded_base64(digest_b64)
        if len(salt) != PBKDF2_SALT_BYTES or len(expected_digest) != PBKDF2_DIGEST_BYTES:
            return False
        actual_digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            iterations,
            dklen=PBKDF2_DIGEST_BYTES,
        )
        return hmac.compare_digest(actual_digest, expected_digest)
    except (AttributeError, TypeError, UnicodeError, ValueError, binascii.Error):
        return False


def _decode_unpadded_base64(value: str) -> bytes:
    if not isinstance(value, str) or not value or "=" in value:
        raise ValueError("Expected non-empty unpadded Base64URL")
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError("Base64URL must contain ASCII characters only") from error
    if len(encoded) % 4 == 1:
        raise ValueError("Invalid unpadded Base64URL length")

    padded = encoded + b"=" * (-len(encoded) % 4)
    decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=")
    if not hmac.compare_digest(canonical, encoded):
        raise ValueError("Non-canonical Base64URL")
    return decoded
