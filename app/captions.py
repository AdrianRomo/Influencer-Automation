"""Caption/subtitle generation from storyboard timing data.

Converts storyboard scenes (which carry start_time_estimate and duration_estimate)
into SRT and WebVTT subtitle formats suitable for video players and FFmpeg assembly.

No LLM calls — timing comes from the WPM-calibrated estimates computed during
storyboard generation. Accuracy improves as voice calibration accumulates samples.
"""
from __future__ import annotations

import re
from typing import List

from app.schemas import CaptionEntry, Storyboard


def _word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+", text))


def _split_sentences(text: str) -> List[str]:
    """Split narration into individual sentences for finer caption granularity."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _srt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds % 1) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _vtt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds % 1) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def storyboard_to_captions(storyboard: Storyboard) -> List[CaptionEntry]:
    """Convert storyboard scenes into caption entries.

    Each scene's narration is split at sentence boundaries. Timing within the
    scene is distributed proportionally by word count so shorter sentences get
    less screen time than longer ones.
    """
    entries: List[CaptionEntry] = []
    index = 1

    for scene in storyboard.scenes:
        if not scene.narration:
            continue

        sentences = _split_sentences(scene.narration)
        if not sentences:
            continue

        scene_start = scene.start_time_estimate
        scene_dur = scene.duration_estimate

        word_counts = [_word_count(s) for s in sentences]
        total_words = sum(word_counts) or 1

        cursor = scene_start
        for sentence, wc in zip(sentences, word_counts):
            duration = scene_dur * (wc / total_words)
            entries.append(CaptionEntry(
                index=index,
                start_time=round(cursor, 3),
                end_time=round(cursor + duration, 3),
                text=sentence,
            ))
            cursor += duration
            index += 1

    return entries


def captions_to_srt(captions: List[CaptionEntry]) -> str:
    """Render caption entries as an SRT file string."""
    blocks = []
    for c in captions:
        blocks.append(f"{c.index}\n{_srt_time(c.start_time)} --> {_srt_time(c.end_time)}\n{c.text}")
    return "\n\n".join(blocks) + "\n"


def captions_to_vtt(captions: List[CaptionEntry]) -> str:
    """Render caption entries as a WebVTT file string (for HTML5 video players)."""
    lines = ["WEBVTT", ""]
    for c in captions:
        lines.append(f"{_vtt_time(c.start_time)} --> {_vtt_time(c.end_time)}")
        lines.append(c.text)
        lines.append("")
    return "\n".join(lines)
