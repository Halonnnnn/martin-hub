import base64
import hashlib
import secrets

import bcrypt
from cryptography.fernet import Fernet, InvalidToken

TOKEN_PREFIX = "martin_"
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PUBLIC_ID_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def gen_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(24)


def gen_public_id(prefix: str) -> str:
    """对外唯一码：1 位类型前缀 + 8 位随机小写字母/数字（排除易混字符）。"""
    return prefix + "".join(secrets.choice(PUBLIC_ID_ALPHABET) for _ in range(8))


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def gen_pairing_code() -> str:
    return "-".join(
        "".join(secrets.choice(CODE_ALPHABET) for _ in range(4)) for _ in range(2)
    )


def gen_invite_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(12))


def gen_temp_code() -> str:
    """一次性临时授权码：TMP-XXXX-XXXX，去掉易混字符。"""
    return "TMP-" + "-".join(
        "".join(secrets.choice(CODE_ALPHABET) for _ in range(4)) for _ in range(2)
    )


def normalize_temp_code(code: str) -> str:
    """输入容忍大小写、分隔符和空白；只保留字母数字。"""
    return "".join(ch for ch in code.upper() if ch.isalnum())


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


def _fernet(secret_key: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode()).digest())
    return Fernet(key)


def encrypt_secret(secret_key: str, plain: str) -> str:
    return _fernet(secret_key).encrypt(plain.encode()).decode()


def decrypt_secret(secret_key: str, cipher: str) -> str | None:
    try:
        return _fernet(secret_key).decrypt(cipher.encode()).decode()
    except (InvalidToken, ValueError):
        return None
