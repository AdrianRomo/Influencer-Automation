"""Unit tests for storyboard → captions conversion."""
from __future__ import annotations

import pytest

from app.captions import storyboard_to_captions, captions_to_srt, captions_to_vtt
from app.schemas import Storyboard, StoryboardScene


def _make_scene(n: int, narration: str, start: float, dur: float) -> StoryboardScene:
    return StoryboardScene(
        scene_number=n,
        narration=narration,
        visual_prompt="",
        on_screen_text="",
        asset_type="b-roll",
        transition="cut",
        start_time_estimate=start,
        duration_estimate=dur,
    )


def _sb(scenes: list[StoryboardScene]) -> Storyboard:
    total = sum(s.duration_estimate for s in scenes)
    return Storyboard(scenes=scenes, total_duration_estimate=round(total, 1))


def test_single_scene_single_sentence_timing():
    sb = _sb([_make_scene(1, "Hola mundo.", 0.0, 3.0)])
    caps = storyboard_to_captions(sb)
    assert len(caps) == 1
    assert caps[0].start_time == 0.0
    assert caps[0].end_time == pytest.approx(3.0)
    assert caps[0].text == "Hola mundo."


def test_multiple_sentences_split_proportionally_by_word_count():
    sb = _sb([_make_scene(1, "Una frase corta. Esta segunda frase es bastante más larga.", 0.0, 10.0)])
    caps = storyboard_to_captions(sb)
    assert len(caps) == 2
    # First sentence has 3 words, second has 8 → first gets 3/11 of 10s
    assert caps[0].end_time < caps[1].end_time
    assert caps[1].end_time == pytest.approx(10.0, abs=0.01)


def test_empty_narration_is_skipped():
    sb = _sb([
        _make_scene(1, "", 0.0, 2.0),
        _make_scene(2, "Solo esta.", 2.0, 3.0),
    ])
    caps = storyboard_to_captions(sb)
    assert len(caps) == 1
    assert caps[0].text == "Solo esta."


def test_actual_audio_duration_rescales_to_anchor_end():
    """When estimates overshoot the real audio length, captions rescale down."""
    sb = _sb([
        _make_scene(1, "Primera frase.", 0.0, 5.0),
        _make_scene(2, "Segunda frase.", 5.0, 5.0),
    ])
    # Estimated end is 10s; pretend actual audio is 8s
    caps = storyboard_to_captions(sb, actual_audio_duration=8.0)
    assert caps[-1].end_time == pytest.approx(8.0, abs=0.01)
    # First caption should have shrunk proportionally
    assert caps[0].end_time < 5.0


def test_actual_audio_duration_ignored_when_close_enough():
    """If estimate already matches audio to within 0.1s, don't touch timestamps."""
    sb = _sb([_make_scene(1, "Una frase.", 0.0, 5.0)])
    caps = storyboard_to_captions(sb, actual_audio_duration=5.05)
    # Should be unchanged from the no-rescale case
    assert caps[0].end_time == pytest.approx(5.0)


def test_srt_format():
    sb = _sb([_make_scene(1, "Hola.", 0.0, 1.5)])
    srt = captions_to_srt(storyboard_to_captions(sb))
    assert "1\n" in srt
    assert "00:00:00,000 --> 00:00:01,500" in srt
    assert "Hola." in srt


def test_vtt_format():
    sb = _sb([_make_scene(1, "Hola.", 0.0, 1.5)])
    vtt = captions_to_vtt(storyboard_to_captions(sb))
    assert vtt.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:01.500" in vtt


def test_no_division_by_zero_on_empty_storyboard():
    caps = storyboard_to_captions(_sb([]))
    assert caps == []
