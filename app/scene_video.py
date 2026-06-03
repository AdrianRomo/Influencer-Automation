"""
Provider abstraction for image-to-video scene clip generation.

Providers
─────────
  StaticImageProvider   FFmpeg still-image → fixed-duration clip (free fallback)
  SeedanceProvider      Seedance AI image-to-video API (first animated provider)

Usage
─────
    from app.scene_video import get_scene_video_provider, JobState

    provider = get_scene_video_provider()          # reads SCENE_VIDEO_PROVIDER env
    job_id   = provider.submit(image_path, prompt, duration_hint=6.0)
    while True:
        job = provider.poll(job_id)
        if job.state in (JobState.READY, JobState.FAILED):
            break
        time.sleep(5)
    if job.state == JobState.READY:
        provider.download(job, "/data/video/scene_1_clip.mp4")

Adding a new provider
─────────────────────
  1. Subclass SceneVideoProvider.
  2. Implement submit(), poll(), download().
  3. Register in _PROVIDERS.
  4. Set SCENE_VIDEO_PROVIDER=<name>.
"""
from __future__ import annotations

import abc
import base64
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import requests

logger = logging.getLogger(__name__)


# ── Status contract ───────────────────────────────────────────────────────────

class JobState(str, Enum):
    PENDING    = "pending"
    PROCESSING = "processing"
    READY      = "ready"
    FAILED     = "failed"


@dataclass
class SceneVideoJob:
    job_id:       str
    state:        JobState
    provider:     str
    download_url: str | None          = None
    error:        str | None          = None
    raw:          dict[str, Any]      = field(default_factory=dict)

    @property
    def done(self) -> bool:
        return self.state in (JobState.READY, JobState.FAILED)


# ── Abstract base ─────────────────────────────────────────────────────────────

class SceneVideoProvider(abc.ABC):
    """Contract every provider must implement."""

    name: str = "base"

    @abc.abstractmethod
    def submit(
        self,
        image_path: str,
        prompt: str,
        duration_hint: float = 5.0,
        **kwargs: Any,
    ) -> str:
        """Start generation. Returns an opaque provider job ID."""

    @abc.abstractmethod
    def poll(self, job_id: str) -> SceneVideoJob:
        """Return current status for a job."""

    @abc.abstractmethod
    def download(self, job: SceneVideoJob, output_path: str) -> None:
        """Download the finished clip to output_path."""

    # ── Blocking helper (for use inside Celery tasks) ─────────────────────────

    def generate_sync(
        self,
        image_path: str,
        prompt: str,
        output_path: str,
        duration_hint: float = 5.0,
        poll_interval: float = 5.0,
        timeout: float = 300.0,
        **kwargs: Any,
    ) -> SceneVideoJob:
        """Submit → poll until done → download. Raises on timeout or failure."""
        job_id   = self.submit(image_path, prompt, duration_hint, **kwargs)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.poll(job_id)
            if job.state == JobState.READY:
                self.download(job, output_path)
                return job
            if job.state == JobState.FAILED:
                raise RuntimeError(
                    f"[{self.name}] job {job_id} failed: {job.error}"
                )
            time.sleep(poll_interval)
        raise RuntimeError(
            f"[{self.name}] job {job_id} timed out after {timeout:.0f}s"
        )


# ── Static / fallback provider ────────────────────────────────────────────────

class StaticImageProvider(SceneVideoProvider):
    """
    Converts a still image to a fixed-duration H.264 clip with FFmpeg.
    No external API — always available as a fallback.
    """
    name = "static"

    def __init__(self, width: int | None = None, height: int | None = None) -> None:
        # Dimensions can be overridden per-instance (e.g. low-cost mode) so the
        # fallback clip matches the resolution of the AI clips it sits beside.
        self._WIDTH  = width  or int(os.getenv("VIDEO_WIDTH",  "1080"))
        self._HEIGHT = height or int(os.getenv("VIDEO_HEIGHT", "1920"))

    def submit(
        self,
        image_path: str,
        prompt: str,
        duration_hint: float = 5.0,
        **kwargs: Any,
    ) -> str:
        # Encode metadata in job_id so poll/download don't need extra state
        return f"static|{image_path}|{duration_hint:.3f}"

    def poll(self, job_id: str) -> SceneVideoJob:
        return SceneVideoJob(
            job_id=job_id,
            state=JobState.READY,
            provider=self.name,
        )

    def download(self, job: SceneVideoJob, output_path: str) -> None:
        parts       = job.job_id.split("|", 2)
        image_path  = parts[1] if len(parts) > 1 else ""
        duration    = float(parts[2]) if len(parts) > 2 else 5.0
        _image_to_clip(image_path, output_path, duration, self._WIDTH, self._HEIGHT)


def _image_to_clip(
    image_path: str,
    output_path: str,
    duration: float,
    width: int  = 1080,
    height: int = 1920,
) -> None:
    """FFmpeg: still PNG/JPEG → silent H.264 MP4 of exact duration."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
        f"setsar=1,fps=24"
    )
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", image_path,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-t", f"{duration:.3f}",
        "-an",
        output_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        raise RuntimeError(
            f"FFmpeg image-to-clip failed (exit {res.returncode}):\n{res.stderr[-500:]}"
        )


# ── Seedance provider ─────────────────────────────────────────────────────────

class SeedanceProvider(SceneVideoProvider):
    """
    Seedance AI image-to-video provider.

    Required env vars
    ─────────────────
      SEEDANCE_API_KEY      API key (Bearer token)

    Optional env vars
    ─────────────────
      SEEDANCE_BASE_URL     Default: https://api.seedance.ai/v1
      SEEDANCE_MODEL        Default: seedance-1-lite
      SEEDANCE_RESOLUTION   720p | 1080p  (default: 720p)
      SEEDANCE_DURATION     Preferred clip length in seconds; snapped to
                            nearest supported value (default: 5)

    API contract (Seedance v1)
    ──────────────────────────
      POST  /videos/image-to-video   → { id, status }
      GET   /videos/{id}             → { id, status, output?, error? }
      GET   /videos/{id}/download    → (redirect to CDN URL)

    status values: queued | processing | succeeded | failed
    """

    name = "seedance"

    # Provider-supported clip durations (seconds)
    _SUPPORTED_DURATIONS = [5, 10]

    def __init__(self) -> None:
        api_key = os.environ.get("SEEDANCE_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("SEEDANCE_API_KEY is required for the Seedance provider")
        self._api_key   = api_key
        self._base_url  = os.getenv("SEEDANCE_BASE_URL", "https://api.seedance.ai/v1").rstrip("/")
        self._model      = os.getenv("SEEDANCE_MODEL", "seedance-1-lite")
        self._resolution = os.getenv("SEEDANCE_RESOLUTION", "720p")
        self._pref_dur   = int(os.getenv("SEEDANCE_DURATION", "5"))

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    # ── Submit ────────────────────────────────────────────────────────────────

    def submit(
        self,
        image_path: str,
        prompt: str,
        duration_hint: float = 5.0,
        **kwargs: Any,
    ) -> str:
        duration = self._snap_duration(duration_hint)
        image_uri = _encode_image_as_data_uri(image_path)

        payload: dict[str, Any] = {
            "model":      self._model,
            "prompt":     prompt,
            "image":      image_uri,
            "duration":   duration,
            "resolution": self._resolution,
        }
        if "seed" in kwargs:
            payload["seed"] = kwargs["seed"]

        resp = requests.post(
            f"{self._base_url}/videos/image-to-video",
            headers=self._headers,
            json=payload,
            timeout=60,
        )
        self._raise_for_status(resp, "submit")
        data   = resp.json()
        job_id = data.get("id") or data.get("task_id") or data.get("job_id")
        if not job_id:
            raise RuntimeError(f"Seedance submit: no job ID in response: {data}")
        logger.info("Seedance job submitted id=%s model=%s dur=%ds", job_id, self._model, duration)
        return str(job_id)

    # ── Poll ──────────────────────────────────────────────────────────────────

    def poll(self, job_id: str) -> SceneVideoJob:
        resp = requests.get(
            f"{self._base_url}/videos/{job_id}",
            headers=self._headers,
            timeout=30,
        )
        self._raise_for_status(resp, "poll")
        data = resp.json()

        raw_status = (
            data.get("status") or data.get("state") or ""
        ).lower().strip()

        if raw_status in ("succeeded", "completed", "done", "success", "ready"):
            state = JobState.READY
        elif raw_status in ("failed", "error", "cancelled", "canceled"):
            state = JobState.FAILED
        else:
            state = JobState.PROCESSING

        # Try several common field names for the output URL
        download_url: str | None = None
        for key in ("output", "video_url", "url", "download_url"):
            val = data.get(key)
            if isinstance(val, str) and val.startswith("http"):
                download_url = val
                break
        result = data.get("result") or {}
        if isinstance(result, dict) and not download_url:
            for key in ("url", "video_url", "output"):
                val = result.get(key)
                if isinstance(val, str) and val.startswith("http"):
                    download_url = val
                    break

        err = data.get("error") or (data.get("message") if state == JobState.FAILED else None)

        return SceneVideoJob(
            job_id=job_id,
            state=state,
            provider=self.name,
            download_url=download_url,
            error=str(err) if err else None,
            raw=data,
        )

    # ── Download ──────────────────────────────────────────────────────────────

    def download(self, job: SceneVideoJob, output_path: str) -> None:
        url = job.download_url
        if not url:
            # Re-poll once to get the final URL (some APIs only populate it at SUCCESS)
            refreshed = self.poll(job.job_id)
            url = refreshed.download_url
        if not url:
            # Try the dedicated download endpoint
            url = f"{self._base_url}/videos/{job.job_id}/download"

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        resp = requests.get(url, stream=True, timeout=120, headers=self._headers)
        self._raise_for_status(resp, "download")
        with open(output_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=65_536):
                fh.write(chunk)
        logger.info("Seedance clip downloaded → %s", output_path)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _snap_duration(self, hint: float) -> int:
        return min(self._SUPPORTED_DURATIONS, key=lambda d: abs(d - hint))

    @staticmethod
    def _raise_for_status(resp: requests.Response, op: str) -> None:
        if not resp.ok:
            body = resp.text[:300]
            raise RuntimeError(
                f"Seedance {op} HTTP {resp.status_code}: {body}"
            )


# ── Runway provider ───────────────────────────────────────────────────────────

class RunwayVideoProvider(SceneVideoProvider):
    """
    Runway Gen-4 image-to-video provider.

    Required env vars
    ─────────────────
      RUNWAY_API_KEY        API key (Bearer token)

    Optional env vars
    ─────────────────
      RUNWAY_BASE_URL       Default: https://api.dev.runwayml.com
      RUNWAY_MODEL          Default: gen4_turbo
      RUNWAY_VERSION        API date header (default: 2024-11-06)
      RUNWAY_RATIO          Output ratio (default: 720:1280 — vertical 9:16)
      RUNWAY_DURATION       Preferred clip length; snapped to {5,10} (default: 5)

    API contract (Runway dev API)
    ─────────────────────────────
      POST /v1/image_to_video  → { id }
      GET  /v1/tasks/{id}      → { id, status, output?, failure? }

    status values: PENDING | THROTTLED | RUNNING | SUCCEEDED | FAILED
    """

    name = "runway"

    _SUPPORTED_DURATIONS = [5, 10]

    def __init__(self) -> None:
        api_key = os.environ.get("RUNWAY_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("RUNWAY_API_KEY is required for the Runway provider")
        self._api_key  = api_key
        self._base_url = os.getenv("RUNWAY_BASE_URL", "https://api.dev.runwayml.com").rstrip("/")
        self._model    = os.getenv("RUNWAY_MODEL", "gen4_turbo")
        self._version  = os.getenv("RUNWAY_VERSION", "2024-11-06")
        self._ratio    = os.getenv("RUNWAY_RATIO", "720:1280")
        self._pref_dur = int(os.getenv("RUNWAY_DURATION", "5"))

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization":    f"Bearer {self._api_key}",
            "X-Runway-Version": self._version,
            "Content-Type":     "application/json",
        }

    def submit(
        self,
        image_path: str,
        prompt: str,
        duration_hint: float = 5.0,
        **kwargs: Any,
    ) -> str:
        duration  = self._snap_duration(duration_hint)
        image_uri = _encode_image_as_data_uri(image_path)

        payload: dict[str, Any] = {
            "model":       self._model,
            "promptImage": image_uri,
            "promptText":  prompt,
            "ratio":       self._ratio,
            "duration":    duration,
        }
        if "seed" in kwargs:
            payload["seed"] = kwargs["seed"]

        resp = requests.post(
            f"{self._base_url}/v1/image_to_video",
            headers=self._headers,
            json=payload,
            timeout=60,
        )
        self._raise_for_status(resp, "submit")
        data   = resp.json()
        job_id = data.get("id") or data.get("task_id")
        if not job_id:
            raise RuntimeError(f"Runway submit: no job ID in response: {data}")
        logger.info("Runway job submitted id=%s model=%s dur=%ds", job_id, self._model, duration)
        return str(job_id)

    def poll(self, job_id: str) -> SceneVideoJob:
        resp = requests.get(
            f"{self._base_url}/v1/tasks/{job_id}",
            headers=self._headers,
            timeout=30,
        )
        self._raise_for_status(resp, "poll")
        data = resp.json()

        raw_status = (data.get("status") or "").upper().strip()
        if raw_status in ("SUCCEEDED", "COMPLETE", "COMPLETED"):
            state = JobState.READY
        elif raw_status in ("FAILED", "ERROR", "CANCELLED", "CANCELED"):
            state = JobState.FAILED
        else:
            state = JobState.PROCESSING

        # Runway returns output as a list of URLs
        download_url: str | None = None
        output = data.get("output")
        if isinstance(output, list) and output:
            first = output[0]
            if isinstance(first, str) and first.startswith("http"):
                download_url = first
        elif isinstance(output, str) and output.startswith("http"):
            download_url = output

        err = data.get("failure") or data.get("failureCode") or data.get("error")

        return SceneVideoJob(
            job_id=job_id,
            state=state,
            provider=self.name,
            download_url=download_url,
            error=str(err) if err else None,
            raw=data,
        )

    def download(self, job: SceneVideoJob, output_path: str) -> None:
        url = job.download_url
        if not url:
            refreshed = self.poll(job.job_id)
            url = refreshed.download_url
        if not url:
            raise RuntimeError(f"Runway job {job.job_id} has no output URL to download")

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        # The output URL is a presigned CDN link — no auth header needed.
        resp = requests.get(url, stream=True, timeout=120)
        self._raise_for_status(resp, "download")
        with open(output_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=65_536):
                fh.write(chunk)
        logger.info("Runway clip downloaded → %s", output_path)

    def _snap_duration(self, hint: float) -> int:
        return min(self._SUPPORTED_DURATIONS, key=lambda d: abs(d - hint))

    @staticmethod
    def _raise_for_status(resp: requests.Response, op: str) -> None:
        if not resp.ok:
            body = resp.text[:300]
            raise RuntimeError(f"Runway {op} HTTP {resp.status_code}: {body}")


# ── Image encoding helper ─────────────────────────────────────────────────────

def _encode_image_as_data_uri(image_path: str) -> str:
    """Read an image file and return a base64 data URI."""
    with open(image_path, "rb") as fh:
        raw = fh.read()
    ext  = os.path.splitext(image_path)[1].lower().lstrip(".") or "png"
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"
    b64  = base64.b64encode(raw).decode()
    return f"data:{mime};base64,{b64}"


# ── Provider registry ─────────────────────────────────────────────────────────

_PROVIDERS: dict[str, type[SceneVideoProvider]] = {
    "runway":   RunwayVideoProvider,
    "seedance": SeedanceProvider,
    "static":   StaticImageProvider,
    # Aliases so config can read like the spec (black == static fallback).
    "black":    StaticImageProvider,
}


def get_scene_video_provider(name: str | None = None) -> SceneVideoProvider:
    """
    Return an initialised provider instance.

    Reads SCENE_VIDEO_PROVIDER env var when name is None.
    Falls back to StaticImageProvider if the requested provider can't be
    initialised (missing API key, unknown name, etc.).
    """
    provider_name = (name or os.getenv("SCENE_VIDEO_PROVIDER", "static")).lower().strip()
    cls = _PROVIDERS.get(provider_name)
    if cls is None:
        logger.warning(
            "Unknown SCENE_VIDEO_PROVIDER %r — falling back to static", provider_name
        )
        return StaticImageProvider()
    try:
        return cls()
    except Exception as exc:
        logger.warning(
            "Provider %r init failed (%s) — falling back to static", provider_name, exc
        )
        return StaticImageProvider()


# ── Visual prompt builder ──────────────────────────────────────────────────────

# Negative directive appended to every clip prompt: AI video models otherwise
# love to hallucinate captions/watermarks, which clash with the burned-in SRT.
NO_TEXT_DIRECTIVE = (
    "Do not include captions, subtitles, on-screen text, logos, watermarks, "
    "UI elements, or any readable words."
)

DEFAULT_VIDEO_STYLE = os.getenv(
    "VIDEO_STYLE",
    "cinematic, realistic, smooth camera movement, soft lighting, "
    "high quality, social media ready",
)


def build_video_prompt(
    visual_prompt: str,
    *,
    style: str | None = None,
    animation_prompt: str | None = None,
) -> str:
    """Assemble the text prompt sent to an image-to-video provider.

    Combines the scene's visual description with an optional animation/style
    directive and always appends NO_TEXT_DIRECTIVE so generated footage stays
    free of text that would fight the subtitle overlay.
    """
    style = style or DEFAULT_VIDEO_STYLE
    parts: list[str] = []
    if animation_prompt and animation_prompt.strip():
        parts.append(animation_prompt.strip())
    if visual_prompt and visual_prompt.strip():
        parts.append(visual_prompt.strip())
    parts.append(f"Style: {style}.")
    parts.append(NO_TEXT_DIRECTIVE)
    return " ".join(parts)


# ── Cost / premium render plan ──────────────────────────────────────────────────

@dataclass(frozen=True)
class RenderPlan:
    """Resolved cost-mode decisions for an animated render.

    width/height       output resolution (already orientation-aware)
    base_provider      provider name used for ordinary scenes
    premium_provider   provider name for premium scenes (or None)
    premium_indices    0-based scene indices that should use premium_provider
    """
    cost_mode:        str
    width:            int
    height:           int
    base_provider:    str
    premium_provider: str | None
    premium_indices:  frozenset[int]

    def provider_for(self, scene_index: int) -> str:
        """Return the provider name to use for a given 0-based scene index."""
        if self.premium_provider and scene_index in self.premium_indices:
            return self.premium_provider
        return self.base_provider


def _parse_resolution(value: str, fallback: tuple[int, int]) -> tuple[int, int]:
    try:
        w, h = value.lower().split("x", 1)
        return int(w), int(h)
    except (ValueError, AttributeError):
        return fallback


def _low_cost_dims(base_w: int, base_h: int) -> tuple[int, int]:
    """Low-cost resolution, oriented to match the base aspect (portrait/landscape)."""
    lo, hi = sorted(_parse_resolution(
        os.getenv("VIDEO_LOW_COST_RESOLUTION", "720x1280"), (720, 1280)
    ))
    return (hi, lo) if base_w >= base_h else (lo, hi)


def resolve_render_plan(
    base_width: int,
    base_height: int,
    base_provider: str | None = None,
) -> RenderPlan:
    """Resolve VIDEO_COST_MODE + premium config into a concrete RenderPlan.

    Modes (env VIDEO_COST_MODE, default "balanced"):
      low       → low-cost resolution, premium disabled (cheapest model only)
      balanced  → base resolution, premium only on configured scenes (if set)
      quality   → base resolution, premium enabled (defaults to scene 0 / hook)

    Premium scenes come from VIDEO_PREMIUM_SCENES (comma-separated 0-based
    indices, default "0") and only apply when VIDEO_PREMIUM_PROVIDER is set.
    """
    mode = os.getenv("VIDEO_COST_MODE", "balanced").lower().strip()
    base_provider = (
        base_provider or os.getenv("SCENE_VIDEO_PROVIDER", "static")
    ).lower().strip()

    width, height = base_width, base_height
    premium_provider = (os.getenv("VIDEO_PREMIUM_PROVIDER", "").lower().strip() or None)
    premium_raw = os.getenv("VIDEO_PREMIUM_SCENES", "0")
    premium_indices = frozenset(
        int(x) for x in premium_raw.split(",") if x.strip().lstrip("-").isdigit()
    )

    if mode == "low":
        width, height = _low_cost_dims(base_width, base_height)
        premium_provider = None
        premium_indices = frozenset()
    elif mode == "quality":
        # Ensure at least the hook scene gets the premium model when configured.
        if premium_provider and not premium_indices:
            premium_indices = frozenset({0})
    # "balanced" (and anything else): base resolution, honour explicit config.

    return RenderPlan(
        cost_mode=mode,
        width=width,
        height=height,
        base_provider=base_provider,
        premium_provider=premium_provider,
        premium_indices=premium_indices,
    )
