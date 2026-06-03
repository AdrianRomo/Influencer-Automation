"""Unit tests for the subtitle force_style builder used by FFmpeg assembly.

The builder must only ever emit values derived from a constrained set of enums
(never raw user text) so nothing can be injected into the -vf filtergraph.
"""
from __future__ import annotations

from app.video import _subtitle_force_style, _build_vf


def test_default_style_matches_boxed_bottom_medium():
    s = _subtitle_force_style(None)
    assert "FontSize=20" in s
    assert "Alignment=2" in s          # bottom-center
    assert "BorderStyle=4" in s        # boxed
    assert "BackColour=&H80000000" in s


def test_position_maps_to_alignment():
    assert "Alignment=2" in _subtitle_force_style({"position": "bottom"})
    assert "Alignment=5" in _subtitle_force_style({"position": "center"})
    assert "Alignment=8" in _subtitle_force_style({"position": "top"})


def test_size_maps_to_font_size():
    assert "FontSize=16" in _subtitle_force_style({"size": "small"})
    assert "FontSize=20" in _subtitle_force_style({"size": "medium"})
    assert "FontSize=26" in _subtitle_force_style({"size": "large"})


def test_outline_preset_has_no_box():
    s = _subtitle_force_style({"preset": "outline"})
    assert "BorderStyle=1" in s
    assert "BackColour" not in s


def test_bold_preset_is_yellow_and_bold():
    s = _subtitle_force_style({"preset": "bold"})
    assert "Bold=1" in s
    assert "PrimaryColour=&H0000ffff" in s   # yellow (BGR)


def test_unknown_values_fall_back_to_defaults():
    s = _subtitle_force_style({"position": "diagonal", "size": "huge", "preset": "neon"})
    assert "FontSize=20" in s
    assert "Alignment=2" in s
    assert "BorderStyle=4" in s              # boxed default


def test_build_vf_without_srt_has_no_subtitles_filter():
    vf = _build_vf(1080, 1920, None)
    assert "subtitles" not in vf
    assert "scale=1080:1920" in vf


def test_build_vf_with_srt_embeds_force_style():
    vf = _build_vf(1080, 1920, "/data/x.srt", {"preset": "outline"})
    assert "subtitles='/data/x.srt'" in vf
    assert "force_style=" in vf
    assert "BorderStyle=1" in vf
