"""JWT authentication + API-key encryption helpers."""
from __future__ import annotations

import base64
import hashlib
import os
from datetime import datetime, timedelta
from typing import Optional

from cryptography.fernet import Fernet
from jose import JWTError, jwt
from passlib.context import CryptContext

SECRET_KEY = os.getenv("SECRET_KEY", "INSECURE-please-set-SECRET_KEY-in-env")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "10080"))  # 7 days

# bcrypt_sha256 pre-hashes with HMAC-SHA256 before bcrypt, bypassing bcrypt's
# 72-byte limit while also preventing password-length side-channels.
_pwd_context = CryptContext(schemes=["bcrypt_sha256"], deprecated="auto")


_fernet_instance: Fernet | None = None


def _fernet() -> Fernet:
    global _fernet_instance
    if _fernet_instance is None:
        derived = hashlib.sha256(SECRET_KEY.encode("utf-8")).digest()
        _fernet_instance = Fernet(base64.urlsafe_b64encode(derived))
    return _fernet_instance


# ── Passwords ──────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


# ── JWT ────────────────────────────────────────────────────────────────────

def create_access_token(user_id: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": user_id, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[str]:
    """Return user_id (sub) or None if the token is invalid/expired."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        sub = payload.get("sub")
        return str(sub) if sub else None
    except JWTError:
        return None


# ── API-key encryption (Fernet symmetric, AES-128-CBC) ─────────────────────

def encrypt_api_key(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_api_key(enc_value: str) -> str:
    return _fernet().decrypt(enc_value.encode("utf-8")).decode("utf-8")
