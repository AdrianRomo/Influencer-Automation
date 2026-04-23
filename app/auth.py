"""JWT authentication + API-key encryption helpers."""
from __future__ import annotations

import base64
import hashlib
import os
import secrets as _secrets
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


# ── Per-user DEK (envelope encryption) ────────────────────────────────────
# Each user's API keys are encrypted with a per-user DEK (data encryption key)
# rather than directly with the master Fernet key.  The DEK itself is
# encrypted (wrapped) with the master Fernet.  Rotating SECRET_KEY only
# requires re-wrapping DEKs, not re-encrypting all stored API key values.

def get_or_create_dek(keys_row) -> bytes:
    """Return the 32-byte DEK for this user's row, generating it if absent.

    Mutates keys_row.dek_enc when creating a new DEK; caller must commit
    the row to persist it.
    """
    if keys_row.dek_enc:
        return _fernet().decrypt(keys_row.dek_enc.encode("utf-8"))
    dek = os.urandom(32)
    keys_row.dek_enc = _fernet().encrypt(dek).decode("utf-8")
    return dek


def encrypt_with_dek(value: str, dek: bytes) -> str:
    """Encrypt value using the user's DEK."""
    return Fernet(base64.urlsafe_b64encode(dek)).encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_with_dek(enc: str, dek: bytes) -> str:
    """Decrypt a value using the user's DEK."""
    return Fernet(base64.urlsafe_b64encode(dek)).decrypt(enc.encode("utf-8")).decode("utf-8")


# ── Refresh tokens ─────────────────────────────────────────────────────────
# Refresh tokens are random opaque strings; persistence and validation live
# in main.py (Redis-backed).  This module provides only token generation.

REFRESH_TOKEN_EXPIRE_SECONDS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "30")) * 86400


def generate_refresh_token() -> str:
    """Return a cryptographically random 40-byte URL-safe token."""
    return _secrets.token_urlsafe(40)
