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

from openai import OpenAI

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


def generate_scene_image(visual_prompt: str) -> bytes:
    """Call DALL-E and return raw PNG bytes."""
    resp = _client.images.generate(
        model=IMAGE_MODEL,
        prompt=_safe_prompt(visual_prompt),
        size=IMAGE_SIZE,
        quality=IMAGE_QUALITY,
        n=1,
        response_format="b64_json",
    )
    return base64.b64decode(resp.data[0].b64_json)


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


def generate_and_save(visual_prompt: str, output_path: str) -> bool:
    """Generate an image and save to disk. Returns True on success, False on failure.

    On failure, writes a black placeholder so callers don't need to handle missing files.
    """
    try:
        img_bytes = generate_scene_image(visual_prompt)
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
