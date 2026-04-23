"""FFmpeg-based video assembly.

Combines a per-scene image slideshow, an MP3 audio track, and an optional
SRT subtitle file into an MP4 suitable for short-form social media (9:16).

Requires ffmpeg and ffprobe to be available on PATH (installed in Docker image).
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile

logger = logging.getLogger(__name__)

DEFAULT_WIDTH = int(os.getenv("VIDEO_WIDTH", "1080"))
DEFAULT_HEIGHT = int(os.getenv("VIDEO_HEIGHT", "1920"))


def assemble_video(
    scenes: list[dict],   # [{"image_path": str, "duration": float}, ...]
    audio_path: str,
    output_path: str,
    srt_path: str | None = None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> float:
    """Assemble an MP4 from scene images, audio, and optional subtitles.

    Each image is shown for its scene's duration. The audio track drives the
    final length (-shortest). Returns the actual video duration in seconds.
    """
    if not scenes:
        raise ValueError("No scenes provided for video assembly")

    concat_file = _write_concat_file(scenes)
    try:
        vf = _build_vf(width, height, srt_path)
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_file,
            "-i", audio_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            "-shortest",
            output_path,
        ]
        logger.info("Running FFmpeg: %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            # If subtitle filter failed, retry without subtitles
            if srt_path and "subtitles" in result.stderr:
                logger.warning("Subtitle filter failed, retrying without subtitles")
                return assemble_video(scenes, audio_path, output_path, srt_path=None, width=width, height=height)
            # Log full stderr server-side; surface only a safe summary to callers
            last_line = result.stderr.strip().split("\n")[-1][:200] if result.stderr else ""
            logger.error("FFmpeg failed (exit %d):\n%s", result.returncode, result.stderr[-2000:])
            raise RuntimeError(f"Video assembly failed (exit {result.returncode}): {last_line}")
    finally:
        try:
            os.unlink(concat_file)
        except OSError:
            pass

    return probe_duration(output_path)


def probe_duration(path: str) -> float:
    """Return video/audio duration in seconds using ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        capture_output=True, text=True, timeout=15,
    )
    try:
        return float(result.stdout.strip())
    except (ValueError, AttributeError):
        return 0.0


def _write_concat_file(scenes: list[dict]) -> str:
    """Write an FFmpeg concat demuxer file and return its path."""
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    )
    for scene in scenes:
        # Escape single quotes in paths for the concat format
        img = scene["image_path"].replace("'", r"'\''")
        dur = max(float(scene["duration"]), 0.5)
        f.write(f"file '{img}'\n")
        f.write(f"duration {dur:.3f}\n")
    # FFmpeg concat demuxer requires the last entry without a duration line
    if scenes:
        img = scenes[-1]["image_path"].replace("'", r"'\''")
        f.write(f"file '{img}'\n")
    f.close()
    return f.name


def normalize_clip(
    input_path: str,
    output_path: str,
    target_duration: float,
    width: int  = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> None:
    """Trim or pad a video clip to exactly target_duration seconds.

    - If the clip is longer: trim with -t.
    - If the clip is shorter: freeze the last frame to fill the gap via tpad.
    The output is a silent H.264 clip ready for concat.
    """
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
        f"setsar=1,fps=24,"
        f"tpad=stop_mode=clone:stop_duration={target_duration:.3f}"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-t", f"{target_duration:.3f}",
        "-an",
        output_path,
    ]
    logger.info("Normalizing clip → %.2fs: %s", target_duration, output_path)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        last_line = result.stderr.strip().split("\n")[-1][:200] if result.stderr else ""
        logger.error("FFmpeg normalize_clip failed (exit %d):\n%s", result.returncode, result.stderr[-1000:])
        raise RuntimeError(f"Clip normalization failed (exit {result.returncode}): {last_line}")


def assemble_video_from_clips(
    scene_clips: list[dict],   # [{"clip_path": str, "duration": float}, ...]
    audio_path: str,
    output_path: str,
    srt_path: str | None = None,
    width: int  = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> float:
    """Concatenate pre-normalised video clips, attach audio, burn optional subtitles.

    Each clip in scene_clips must already be normalised to its target duration
    (silent H.264). The audio track drives the final length via -shortest.
    Returns actual video duration in seconds.
    """
    if not scene_clips:
        raise ValueError("No clips provided for animated video assembly")

    concat_file = _write_clips_concat_file(scene_clips)
    try:
        vf  = _build_vf(width, height, srt_path)
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_file,
            "-i", audio_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            "-shortest",
            output_path,
        ]
        logger.info("Assembling animated video: %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            if srt_path and "subtitles" in result.stderr:
                logger.warning("Subtitle filter failed in animated assembly, retrying without")
                return assemble_video_from_clips(
                    scene_clips, audio_path, output_path,
                    srt_path=None, width=width, height=height,
                )
            last_line = result.stderr.strip().split("\n")[-1][:200] if result.stderr else ""
            logger.error("FFmpeg animated assembly failed (exit %d):\n%s", result.returncode, result.stderr[-2000:])
            raise RuntimeError(f"Animated video assembly failed (exit {result.returncode}): {last_line}")
    finally:
        try:
            os.unlink(concat_file)
        except OSError:
            pass

    return probe_duration(output_path)


def _write_clips_concat_file(clips: list[dict]) -> str:
    """Write an FFmpeg concat demuxer file for a list of video clip paths."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
    for clip in clips:
        path = clip["clip_path"].replace("'", r"'\''")
        f.write(f"file '{path}'\n")
    f.close()
    return f.name


def _build_vf(width: int, height: int, srt_path: str | None) -> str:
    """Build the FFmpeg -vf filtergraph string."""
    # Scale the image to fit within target dimensions preserving aspect ratio,
    # then pad with black bars to reach exactly target dimensions.
    scale = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
        f"setsar=1,fps=24"
    )
    if not srt_path:
        return scale

    # Escape the SRT path for the subtitles filter (colon is a separator char)
    escaped = srt_path.replace("\\", "/").replace(":", r"\:")
    subtitle_style = (
        "FontSize=20,PrimaryColour=&H00ffffff,"
        "OutlineColour=&H00000000,Outline=2,"
        "BackColour=&H80000000,BorderStyle=4,"
        "Alignment=2,MarginV=30"
    )
    return f"{scale},subtitles='{escaped}':force_style='{subtitle_style}'"
