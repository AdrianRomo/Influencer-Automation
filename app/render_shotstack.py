"""Shotstack render provider for the embedded editor.

Flow:
  1. resolve canonical-timeline asset refs → public object-storage URLs (R2)
  2. convert to Shotstack render JSON (app.timeline.to_shotstack)
  3. POST to the Shotstack render endpoint → job id
  4. poll status; on "done", download the MP4 to VIDEO_DIR

Status contract (Shotstack): queued → fetching → rendering → saving → done,
or failed. See https://shotstack.io/docs/api/ .
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Callable, Optional

import requests
from sqlalchemy.orm import Session

from app import assets_storage
from app.models import AudioAsset, ImageAsset, SceneVideoAsset
from app.timeline import to_shotstack

logger = logging.getLogger(__name__)

_API_KEY = os.getenv("SHOTSTACK_API_KEY", "").strip()
# e.g. https://api.shotstack.io/edit/stage/render — status is GET <url>/<id>.
_RENDER_URL = os.getenv("SHOTSTACK_RENDER_URL", "https://api.shotstack.io/edit/stage/render").strip().rstrip("/")
_VIDEO_DIR = os.getenv("VIDEO_DIR", "/data/video")
_TIMEOUT = 60

_TERMINAL_OK = "done"
_TERMINAL_FAIL = "failed"


def shotstack_enabled() -> bool:
    return bool(_API_KEY) and assets_storage.storage_enabled()


@dataclass
class RenderStatus:
    state: str                      # queued|fetching|rendering|saving|done|failed
    url: Optional[str] = None       # rendered video URL when done
    error: Optional[str] = None
    done: bool = False
    failed: bool = False


def _headers() -> dict:
    if not _API_KEY:
        raise RuntimeError("SHOTSTACK_API_KEY is not set")
    return {"x-api-key": _API_KEY, "Content-Type": "application/json", "Accept": "application/json"}


# ── Asset URL resolution ───────────────────────────────────────────────────────

def make_src_resolver(db: Session) -> Callable[[str, str], str]:
    """Build a resolver mapping (asset_kind, asset_id) → public URL for rendering.

    Uploads the asset's local file to object storage on demand. Results are cached
    per-call so the same asset isn't uploaded twice in one render.
    """
    cache: dict[tuple[str, str], str] = {}

    def resolve(asset_kind: str, asset_id: str) -> str:
        key = (asset_kind, asset_id)
        if key in cache:
            return cache[key]

        if asset_kind == "image":
            row = db.get(ImageAsset, asset_id)
            prefix = "images"
        elif asset_kind == "video":
            row = db.get(SceneVideoAsset, asset_id)
            prefix = "clips"
        elif asset_kind == "audio":
            row = db.get(AudioAsset, asset_id)
            prefix = "audio"
        else:
            raise ValueError(f"Unknown asset_kind: {asset_kind}")

        path = getattr(row, "file_path", None) if row else None
        if not path or not os.path.exists(path):
            raise RuntimeError(f"Asset {asset_kind}:{asset_id} has no local file to upload")

        url = assets_storage.upload_asset(path, prefix=prefix)
        if not url:
            raise RuntimeError("Object storage is not configured (ASSET_STORAGE_BACKEND)")
        cache[key] = url
        return url

    return resolve


# ── Render lifecycle ───────────────────────────────────────────────────────────

def submit_render(timeline: dict, db: Session) -> str:
    """Resolve assets, convert, and POST a render. Returns the Shotstack job id."""
    edit = to_shotstack(timeline, make_src_resolver(db))
    resp = requests.post(_RENDER_URL, json=edit, headers=_headers(), timeout=_TIMEOUT)
    if resp.status_code >= 300:
        raise RuntimeError(f"Shotstack submit HTTP {resp.status_code}: {resp.text[:500]}")
    data = resp.json().get("response", {})
    job_id = data.get("id")
    if not job_id:
        raise RuntimeError(f"Shotstack submit: no job id in response: {resp.text[:500]}")
    logger.info("Shotstack render submitted id=%s", job_id)
    return job_id


def poll_render(job_id: str) -> RenderStatus:
    resp = requests.get(f"{_RENDER_URL}/{job_id}", headers=_headers(), timeout=_TIMEOUT)
    if resp.status_code >= 300:
        raise RuntimeError(f"Shotstack poll HTTP {resp.status_code}: {resp.text[:500]}")
    data = resp.json().get("response", {})
    state = (data.get("status") or "unknown").lower()
    return RenderStatus(
        state=state,
        url=data.get("url"),
        error=data.get("error"),
        done=state == _TERMINAL_OK,
        failed=state == _TERMINAL_FAIL,
    )


def download_render(url: str, dest_path: str) -> str:
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                if chunk:
                    f.write(chunk)
    logger.info("Shotstack render downloaded → %s", dest_path)
    return dest_path


def render_output_path(timeline_id: str) -> str:
    return os.path.join(_VIDEO_DIR, f"edit_{timeline_id}.mp4")
