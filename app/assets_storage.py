"""Object-storage helper for exposing generated assets to external renderers.

The Shotstack cloud renderer fetches source assets (images, audio, scene clips)
over HTTP, so they must live behind public/presigned URLs rather than only on the
worker's local disk. This module uploads a local file to Cloudflare R2 (an
S3-compatible bucket) and returns a URL the renderer can fetch.

Backends (ASSET_STORAGE_BACKEND):
  local  → no upload; returns None (caller falls back to FFmpeg/local rendering)
  r2     → upload to R2 and return a public (custom-domain) or presigned URL

Configuration: see R2_* vars in .env.example.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_BACKEND = os.getenv("ASSET_STORAGE_BACKEND", "local").lower().strip()
_BUCKET = os.getenv("R2_BUCKET", "").strip()
_ENDPOINT = os.getenv("R2_ENDPOINT_URL", "").strip()
_ACCESS_KEY = os.getenv("R2_ACCESS_KEY_ID", "").strip()
_SECRET_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "").strip()
_PUBLIC_BASE = os.getenv("R2_PUBLIC_BASE_URL", "").strip().rstrip("/")
# Presigned-URL lifetime when no public custom domain is configured.
_PRESIGN_TTL = int(os.getenv("R2_PRESIGN_TTL_SECONDS", "86400"))

_CONTENT_TYPES = {
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
}

_client = None
_client_lock = threading.Lock()


def storage_enabled() -> bool:
    """True when an external object-storage backend is configured."""
    return _BACKEND == "r2"


def _get_client():
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            import boto3  # imported lazily so local-only deploys don't need it
            from botocore.config import Config

            if not (_ENDPOINT and _ACCESS_KEY and _SECRET_KEY and _BUCKET):
                raise RuntimeError(
                    "ASSET_STORAGE_BACKEND=r2 but R2_ENDPOINT_URL / R2_ACCESS_KEY_ID / "
                    "R2_SECRET_ACCESS_KEY / R2_BUCKET are not all set."
                )
            _client = boto3.client(
                "s3",
                endpoint_url=_ENDPOINT,
                aws_access_key_id=_ACCESS_KEY,
                aws_secret_access_key=_SECRET_KEY,
                # R2 ignores region but boto3 requires one; "auto" is the R2 convention.
                region_name="auto",
                config=Config(signature_version="s3v4"),
            )
    return _client


def _key_for(local_path: str, prefix: str) -> str:
    """Content-addressed key so re-uploading the same file is idempotent."""
    h = hashlib.sha1()
    with open(local_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    digest = h.hexdigest()[:16]
    ext = os.path.splitext(local_path)[1].lower()
    prefix = prefix.strip("/")
    return f"{prefix}/{digest}{ext}" if prefix else f"{digest}{ext}"


def _content_type(path: str) -> str:
    return _CONTENT_TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")


def public_url_for_key(key: str) -> str:
    client = _get_client()
    if _PUBLIC_BASE:
        return f"{_PUBLIC_BASE}/{key}"
    # No custom domain → hand out a presigned GET URL.
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": _BUCKET, "Key": key},
        ExpiresIn=_PRESIGN_TTL,
    )


def upload_asset(local_path: str, prefix: str = "assets") -> Optional[str]:
    """Upload a local file and return a renderer-fetchable URL.

    Returns None when storage is disabled (local backend) or the file is missing,
    so callers can fall back to the local FFmpeg path. Raises on real upload errors.
    """
    if not storage_enabled():
        return None
    if not local_path or not os.path.exists(local_path):
        logger.warning("upload_asset: missing file %s", local_path)
        return None

    client = _get_client()
    key = _key_for(local_path, prefix)

    # Skip the upload if the content-addressed object already exists.
    try:
        client.head_object(Bucket=_BUCKET, Key=key)
        logger.debug("upload_asset: %s already present", key)
        return public_url_for_key(key)
    except Exception:
        pass

    client.upload_file(
        local_path, _BUCKET, key,
        ExtraArgs={"ContentType": _content_type(local_path)},
    )
    logger.info("upload_asset: uploaded %s → r2://%s/%s", local_path, _BUCKET, key)
    return public_url_for_key(key)
