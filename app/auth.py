"""JWT authentication + API-key encryption helpers."""
from __future__ import annotations

import base64
import hashlib
import os
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from cryptography.fernet import Fernet
from jose import JWTError, jwt

SECRET_KEY = os.getenv("SECRET_KEY", "INSECURE-please-set-SECRET_KEY-in-env")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "720"))  # 12 hours default

_fernet_instance: Fernet | None = None


def _fernet() -> Fernet:
    global _fernet_instance
    if _fernet_instance is None:
        derived = hashlib.sha256(SECRET_KEY.encode("utf-8")).digest()
        _fernet_instance = Fernet(base64.urlsafe_b64encode(derived))
    return _fernet_instance


# ── Passwords ──────────────────────────────────────────────────────────────
# SHA-256 pre-hash keeps the bcrypt input at exactly 32 bytes, safely under
# bcrypt's 72-byte limit regardless of password length or bcrypt version.

def _prehash(password: str) -> bytes:
    return hashlib.sha256(password.encode("utf-8")).digest()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(_prehash(plain), hashed.encode("utf-8"))


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
