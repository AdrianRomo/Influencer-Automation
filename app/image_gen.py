"""DALL-E image generation for storyboard scenes.

Generates one PNG image per scene using the visual_prompt from the storyboard.
Falls back to a black placeholder frame when the API call fails or is rate-limited,
so a single bad scene never blocks video assembly.
"""
from __future__ import annotations

import base64
import logging
import os
import subprocess
from typing import TYPE_CHECKING

from openai import OpenAI

if TYPE_CHECKING:
    from app.usage import UsageCollector

logger = logging.getLogger(__name__)

_client = OpenAI()

IMAGE_MODEL = os.getenv("IMAGE_MODEL", "dall-e-3")
# 1024x1792 is DALL-E 3's portrait option — close to 9:16 for short-form video
IMAGE_SIZE = os.getenv("IMAGE_SIZE", "1024x1792")
IMAGE_QUALITY = os.getenv("IMAGE_QUALITY", "standard")  # standard | hd

# Safety prefix ensures medical prompts don't trigger content filters
_PROMPT_PREFIX = (
    "Professional photorealistic medical illustration, clean and informative, "
    "no text overlays, no gore, suitable for health education: "
)
_MAX_PROMPT_CHARS = 900  # well under DALL-E's 4000-char limit after prefix


def _safe_prompt(visual_prompt: str) -> str:
    raw = (_PROMPT_PREFIX + visual_prompt.strip())
    return raw[:_MAX_PROMPT_CHARS + len(_PROMPT_PREFIX)]


def generate_scene_image(
    visual_prompt: str,
    api_key: str | None = None,
    collector: "UsageCollector | None" = None,
    scene_number: int | None = None,
) -> bytes:
    """Call DALL-E and return raw PNG bytes."""
    c = OpenAI(api_key=api_key) if api_key else _client
    resp = c.images.generate(
        model=IMAGE_MODEL,
        prompt=_safe_prompt(visual_prompt),
        size=IMAGE_SIZE,
        quality=IMAGE_QUALITY,
        n=1,
        response_format="b64_json",
    )
    img_bytes = base64.b64decode(resp.data[0].b64_json)

    if collector is not None:
        try:
            from app.pricing import estimate_openai_image_cost, get_image_pricing_snapshot
            cost = estimate_openai_image_cost(model=IMAGE_MODEL, size=IMAGE_SIZE, quality=IMAGE_QUALITY, count=1)
            collector.record(
                provider="openai",
                operation="image",
                model=IMAGE_MODEL,
                image_count=1,
                image_size=IMAGE_SIZE,
                image_quality=IMAGE_QUALITY,
                estimated_cost_usd=cost,
                pricing_snapshot=get_image_pricing_snapshot(IMAGE_MODEL, IMAGE_SIZE, IMAGE_QUALITY),
                metadata={"scene_number": scene_number} if scene_number is not None else None,
            )
        except Exception:
            pass

    return img_bytes


def generate_thumbnail(
    title: str,
    scene_prompt: str | None = None,
    api_key: str | None = None,
    collector: "UsageCollector | None" = None,
) -> bytes:
    """Generate a DALL-E 3 cover/thumbnail image for the article."""
    topic = title[:200]
    visual_hint = f" Visual reference: {scene_prompt[:150]}." if scene_prompt else ""
    prompt = (
        f"Eye-catching social media cover image for a medical health video about: {topic}.{visual_hint} "
        "Professional photorealistic healthcare aesthetic. Cinematic lighting. "
        "No text, no watermarks, no logos. Clean composition."
    )[:900]

    c = OpenAI(api_key=api_key) if api_key else _client
    resp = c.images.generate(
        model=IMAGE_MODEL,
        prompt=prompt,
        size=IMAGE_SIZE,
        quality=IMAGE_QUALITY,
        n=1,
        response_format="b64_json",
    )
    img_bytes = base64.b64decode(resp.data[0].b64_json)

    if collector is not None:
        try:
            from app.pricing import estimate_openai_image_cost, get_image_pricing_snapshot
            cost = estimate_openai_image_cost(model=IMAGE_MODEL, size=IMAGE_SIZE, quality=IMAGE_QUALITY, count=1)
            collector.record(
                provider="openai",
                operation="thumbnail",
                model=IMAGE_MODEL,
                image_count=1,
                image_size=IMAGE_SIZE,
                image_quality=IMAGE_QUALITY,
                estimated_cost_usd=cost,
                pricing_snapshot=get_image_pricing_snapshot(IMAGE_MODEL, IMAGE_SIZE, IMAGE_QUALITY),
            )
        except Exception:
            pass

    return img_bytes


def create_placeholder_image(output_path: str, width: int = 1024, height: int = 1792) -> None:
    """Write a solid black PNG using FFmpeg — used when image generation fails."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=c=black:s={width}x{height}:r=1",
            "-frames:v", "1", output_path,
        ],
        capture_output=True,
        check=True,
        timeout=15,
    )


def generate_and_save(
    visual_prompt: str,
    output_path: str,
    api_key: str | None = None,
    collector: "UsageCollector | None" = None,
    scene_number: int | None = None,
) -> bool:
    """Generate an image and save to disk. Returns True on success, False on failure.

    On failure, writes a black placeholder so callers don't need to handle missing files.
    """
    try:
        img_bytes = generate_scene_image(visual_prompt, api_key=api_key,
                                         collector=collector, scene_number=scene_number)
        with open(output_path, "wb") as fh:
            fh.write(img_bytes)
        return True
    except Exception as exc:
        logger.warning("Image generation failed (%s), using placeholder: %s", type(exc).__name__, exc)
        try:
            create_placeholder_image(output_path)
        except Exception as fallback_exc:
            logger.error("Placeholder creation also failed: %s", fallback_exc)
            raise RuntimeError(f"Could not produce image for scene: {exc}") from exc
        return False
