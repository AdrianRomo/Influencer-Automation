"""Tests for summarize.py JSON parsing + retry logic."""
from __future__ import annotations

import json

import pytest

from app.summarize import _extract_json, _parse_scenes_with_retry, generate_social_captions


def test_extract_json_strips_markdown_fence():
    raw = "```json\n[{\"scene_number\": 1}]\n```"
    assert _extract_json(raw) == '[{"scene_number": 1}]'


def test_extract_json_strips_bare_fence():
    raw = "```\n{\"a\": 1}\n```"
    assert _extract_json(raw) == '{"a": 1}'


def test_extract_json_leaves_clean_json_alone():
    raw = '[{"scene_number": 1}]'
    assert _extract_json(raw) == raw


def test_parse_scenes_succeeds_on_first_try():
    scenes = _parse_scenes_with_retry(
        raw=json.dumps([
            {"scene_number": 1, "narration": "Hola", "visual_prompt": "scene 1",
             "on_screen_text": "t", "asset_type": "title-card", "transition": "cut"}
        ]),
        user_prompt="ignored",
        model="fake-model",
        api_key=None,
        collector=None,
    )
    assert len(scenes) == 1
    assert scenes[0]["scene_number"] == 1


def test_parse_scenes_retries_with_corrective_prompt(mocker):
    """On parse failure, code retries once with a corrective prompt."""
    call = mocker.patch("app.summarize._call_llm")
    call.return_value = json.dumps([
        {"scene_number": 1, "narration": "ok", "visual_prompt": "v",
         "on_screen_text": "", "asset_type": "b-roll", "transition": "cut"}
    ])

    scenes = _parse_scenes_with_retry(
        raw="this is not json",
        user_prompt="original prompt",
        model="fake",
        api_key=None,
        collector=None,
    )
    assert call.call_count == 1  # one retry after the initial raw fails
    assert len(scenes) == 1


def test_parse_scenes_returns_empty_after_repeated_failure(mocker):
    call = mocker.patch("app.summarize._call_llm")
    call.return_value = "still not json"
    scenes = _parse_scenes_with_retry(
        raw="not json",
        user_prompt="prompt",
        model="fake",
        api_key=None,
        collector=None,
    )
    assert scenes == []


def test_social_captions_retries_on_malformed_response(mocker):
    # First call returns raw (bad); retry returns valid JSON
    call = mocker.patch("app.summarize._call_llm")
    call.side_effect = [
        "not valid json at all",
        json.dumps({"tiktok": {"caption": "hi", "hashtags": ["#a"]}}),
    ]
    result = generate_social_captions(
        title="T", script="S", platforms=["tiktok"],
        output_language="es-MX", api_key=None, collector=None,
    )
    assert call.call_count == 2
    assert result == {"tiktok": {"caption": "hi", "hashtags": ["#a"]}}


def test_social_captions_returns_empty_when_unparseable(mocker):
    call = mocker.patch("app.summarize._call_llm")
    call.return_value = "never valid"
    result = generate_social_captions(
        title="T", script="S", platforms=["tiktok"],
        output_language="es-MX", api_key=None, collector=None,
    )
    assert result == {}
