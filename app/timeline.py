"""Canonical editable-timeline model + Shotstack adapter.

The "Edit document" is the single source of truth for the embedded video editor.
It is provider-agnostic: the Shotstack Studio editor mutates it in the browser,
and at render time we convert it either to Shotstack render JSON (cloud) or feed
it to app/video.py (local FFmpeg fallback).

Canonical schema (schema_version=1)
───────────────────────────────────
{
  "schema_version": 1,
  "source": {"kind": "article", "id": "<id>"},
  "output": {"format": "mp4", "width": 1080, "height": 1920, "fps": 30},
  "duration": <float seconds>,
  "soundtrack": {"asset_kind": "audio", "asset_id": "<id>", "src": "/audio/<id>"} | null,
  "tracks": [
    {"type": "subtitles", "clips": [ <SubtitleClip>, ... ]},   # rendered on top
    {"type": "visual",     "clips": [ <VisualClip>, ... ]}
  ]
}

SubtitleClip: {id, start, length, text, style{...}}
VisualClip:   {id, scene_number, start, length, asset_kind(image|video),
               asset_id, src, fit, transition{in,out},
               animate{mode(none|effect|ai), effect, provider, scene_video_id}}

`src` values are API-relative (e.g. "/image/<id>"); the frontend prepends the API
base for editor preview, and the renderer swaps them for public object-storage
URLs (see app/render_shotstack.py).
"""
from __future__ import annotations

from typing import Callable, Optional

from sqlalchemy.orm import Session

from app.captions import storyboard_to_captions
from app.models import Article, AudioAsset, ImageAsset, SceneVideoAsset
from app.schemas import Storyboard

SCHEMA_VERSION = 1

DEFAULT_SUBTITLE_STYLE = {
    "font_family": "Montserrat ExtraBold",
    "font_size": 48,
    "color": "#FFFFFF",
    "background": "#000000B3",
    "position": "bottom",
    "align": "center",
}

# Shotstack motion effects we expose for the free "animate" tier (Ken Burns / pan).
MOTION_EFFECTS = {"zoomIn", "zoomOut", "slideLeft", "slideRight", "slideUp", "slideDown"}


# ── Build canonical timeline from an article ───────────────────────────────────

def build_timeline_from_article(db: Session, article: Article) -> dict:
    """Assemble a canonical Edit document from an article's storyboard + assets.

    Prefers the latest ready scene clip per scene (animated) over the still image.
    Subtitle timing comes from the WPM-calibrated caption builder, rescaled to the
    real audio duration when available.
    """
    storyboard = _load_storyboard(article)

    audio = _latest_ready_audio(db, article.id)
    audio_duration = float(audio.estimated_seconds) if audio and audio.estimated_seconds else None

    images = _scene_map(
        db.query(ImageAsset)
        .filter(ImageAsset.article_id == article.id, ImageAsset.deleted_at.is_(None))
        .order_by(ImageAsset.scene_number, ImageAsset.created_at)
        .all(),
        ready_only=True,
    )
    scene_clips = _scene_map(
        db.query(SceneVideoAsset)
        .filter(SceneVideoAsset.article_id == article.id, SceneVideoAsset.deleted_at.is_(None))
        .order_by(SceneVideoAsset.scene_number, SceneVideoAsset.created_at)
        .all(),
        ready_only=True,
    )

    visual_clips: list[dict] = []
    for scene in storyboard.scenes:
        n = scene.scene_number
        start = round(float(scene.start_time_estimate), 3)
        length = round(float(scene.duration_estimate), 3)
        clip = scene_clips.get(n)
        img = images.get(n)

        if clip is not None:
            asset_kind, asset_id, src = "video", clip.id, f"/scene-videos/{clip.id}"
            animate = {"mode": "ai", "effect": None, "provider": clip.provider, "scene_video_id": clip.id}
        elif img is not None:
            asset_kind, asset_id, src = "image", img.id, f"/image/{img.id}"
            animate = {"mode": "none", "effect": None, "provider": None, "scene_video_id": None}
        else:
            # No rendered asset yet — keep a placeholder slot so the editor still
            # shows the scene and the user can regenerate/animate it.
            asset_kind, asset_id, src = "image", None, None
            animate = {"mode": "none", "effect": None, "provider": None, "scene_video_id": None}

        visual_clips.append({
            "id": f"scene-{n}",
            "scene_number": n,
            "start": start,
            "length": length,
            "asset_kind": asset_kind,
            "asset_id": asset_id,
            "src": src,
            "fit": "cover",
            "transition": {"in": _transition_name(scene.transition), "out": None},
            "animate": animate,
            "on_screen_text": scene.on_screen_text or "",
        })

    subtitle_clips: list[dict] = []
    for cap in storyboard_to_captions(storyboard, actual_audio_duration=audio_duration):
        subtitle_clips.append({
            "id": f"sub-{cap.index}",
            "start": round(cap.start_time, 3),
            "length": round(max(cap.end_time - cap.start_time, 0.1), 3),
            "text": cap.text,
            "style": dict(DEFAULT_SUBTITLE_STYLE),
        })

    duration = audio_duration or (storyboard.total_duration_estimate if storyboard.scenes else 0.0)
    width, height = _output_dims(article)

    return {
        "schema_version": SCHEMA_VERSION,
        "source": {"kind": "article", "id": article.id},
        "output": {"format": "mp4", "width": width, "height": height, "fps": 30},
        "duration": round(float(duration), 3),
        "soundtrack": (
            {"asset_kind": "audio", "asset_id": audio.id, "src": f"/audio/{audio.id}"}
            if audio else None
        ),
        "tracks": [
            {"type": "subtitles", "clips": subtitle_clips},
            {"type": "visual", "clips": visual_clips},
        ],
    }


# ── Canonical → Shotstack render JSON ──────────────────────────────────────────

def to_shotstack(timeline: dict, resolve_src: Callable[[str, str], str]) -> dict:
    """Convert a canonical timeline to a Shotstack Edit (render) document.

    ``resolve_src(asset_kind, asset_id)`` must return an absolute, publicly
    fetchable URL for the given asset (the Shotstack renderer downloads sources).
    """
    output = timeline.get("output", {})
    width = int(output.get("width", 1080))
    height = int(output.get("height", 1920))

    shotstack_tracks: list[dict] = []
    for track in timeline.get("tracks", []):
        clips = (
            [_subtitle_to_shotstack(c) for c in track.get("clips", [])]
            if track.get("type") == "subtitles"
            else [_visual_to_shotstack(c, resolve_src) for c in track.get("clips", [])]
        )
        clips = [c for c in clips if c is not None]
        if clips:
            shotstack_tracks.append({"clips": clips})

    sl = timeline.get("soundtrack")
    soundtrack = None
    if sl and sl.get("asset_id"):
        soundtrack = {"src": resolve_src(sl["asset_kind"], sl["asset_id"]), "effect": "fadeOut"}

    edit: dict = {
        "timeline": {"background": "#000000", "tracks": shotstack_tracks},
        "output": {"format": output.get("format", "mp4"),
                   "size": {"width": width, "height": height},
                   "fps": int(output.get("fps", 30))},
    }
    if soundtrack:
        edit["timeline"]["soundtrack"] = soundtrack
    return edit


def _visual_to_shotstack(clip: dict, resolve_src: Callable[[str, str], str]) -> Optional[dict]:
    asset_id = clip.get("asset_id")
    if not asset_id:
        return None  # placeholder scene with no rendered asset — skip in render
    asset_kind = clip.get("asset_kind", "image")
    url = resolve_src(asset_kind, asset_id)

    out: dict = {
        "asset": {"type": "video" if asset_kind == "video" else "image", "src": url},
        "start": clip["start"],
        "length": clip["length"],
        "fit": clip.get("fit", "cover"),
    }

    animate = clip.get("animate") or {}
    # Free tier: deterministic motion effect baked into the render JSON. The AI
    # tier already swapped the still for a video asset upstream, so no effect here.
    if animate.get("mode") == "effect" and animate.get("effect") in MOTION_EFFECTS:
        out["effect"] = animate["effect"]

    transition = clip.get("transition") or {}
    t = {k: v for k, v in (("in", transition.get("in")), ("out", transition.get("out"))) if v}
    if t:
        out["transition"] = t
    return out


def _hex6(color: str | None, fallback: str) -> str:
    """Shotstack rejects 8-digit #RRGGBBAA — drop alpha down to #RRGGBB."""
    if isinstance(color, str):
        c = color.strip()
        if len(c) == 9 and c.startswith("#"):
            return c[:7]
        if len(c) == 7 and c.startswith("#"):
            return c
    return fallback


def _subtitle_to_shotstack(clip: dict) -> dict:
    style = clip.get("style") or {}
    pos = style.get("position", "bottom")
    return {
        "asset": {
            "type": "title",
            "text": clip.get("text", ""),
            "style": "subtitle",
            "color": _hex6(style.get("color"), "#FFFFFF"),
            "size": _size_bucket(style.get("font_size", 48)),
            "background": _hex6(style.get("background"), "#000000"),
            "position": pos,
        },
        "start": clip["start"],
        "length": clip["length"],
        "position": pos,
    }


# ── Helpers ────────────────────────────────────────────────────────────────────

def _load_storyboard(article: Article) -> Storyboard:
    raw = article.storyboard_json or {"scenes": [], "total_duration_estimate": 0.0}
    return Storyboard.model_validate(raw)


def _scene_map(rows: list, ready_only: bool) -> dict:
    """Latest row per scene_number (rows arrive ordered by created_at ascending)."""
    out: dict = {}
    for r in rows:
        if ready_only and getattr(r, "status", "ready") not in ("ready", "fallback"):
            continue
        if getattr(r, "file_path", "sentinel") is None:
            continue
        out[r.scene_number] = r  # later (newer) rows overwrite older ones
    return out


def _latest_ready_audio(db: Session, article_id: str) -> Optional[AudioAsset]:
    return (
        db.query(AudioAsset)
        .filter(
            AudioAsset.article_id == article_id,
            AudioAsset.deleted_at.is_(None),
            AudioAsset.status == "ready",
        )
        .order_by(AudioAsset.created_at.desc())
        .first()
    )


def _transition_name(name: Optional[str]) -> Optional[str]:
    mapping = {"cut": None, "fade": "fade", "dissolve": "fade"}
    return mapping.get((name or "cut").lower(), "fade")


def _size_bucket(font_size: int) -> str:
    if font_size >= 64:
        return "x-large"
    if font_size >= 48:
        return "large"
    if font_size >= 32:
        return "medium"
    return "small"


def _output_dims(article: Article) -> tuple[int, int]:
    """9:16 short-form default; respects platform if a single one is selected."""
    try:
        from app.platforms import get_platform_profile  # optional, best-effort
        platforms = article.selected_platforms or []
        if len(platforms) == 1:
            p = get_platform_profile(platforms[0])
            if p:
                return int(p["width"]), int(p["height"])
    except Exception:
        pass
    return 1080, 1920
