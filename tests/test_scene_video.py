"""Unit tests for the scene-video provider system, prompt builder, cost-mode
render plan, and the FFmpeg clip helpers.

No real external APIs or ffmpeg binaries are touched — HTTP is monkeypatched
and subprocess.run is stubbed where a real binary would otherwise be invoked.
"""
from __future__ import annotations

import os

import pytest

from app import scene_video, video
from app.scene_video import (
    StaticImageProvider,
    SeedanceProvider,
    RunwayVideoProvider,
    JobState,
    SceneVideoJob,
    get_scene_video_provider,
    build_video_prompt,
    resolve_render_plan,
    NO_TEXT_DIRECTIVE,
)


# ── Fake HTTP plumbing ──────────────────────────────────────────────────────

class FakeResp:
    def __init__(self, *, ok=True, status_code=200, payload=None, content=b"", text=""):
        self.ok = ok
        self.status_code = status_code
        self._payload = payload or {}
        self._content = content
        self.text = text or ""

    def json(self):
        return self._payload

    def iter_content(self, chunk_size=65_536):
        yield self._content


@pytest.fixture
def png(tmp_path):
    """A tiny on-disk PNG so providers can base64-encode an image."""
    p = tmp_path / "scene.png"
    # 1x1 PNG
    p.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
        b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    return str(p)


# ── Provider selection / fallback ───────────────────────────────────────────

def test_select_static_provider():
    assert isinstance(get_scene_video_provider("static"), StaticImageProvider)


def test_black_alias_maps_to_static():
    assert isinstance(get_scene_video_provider("black"), StaticImageProvider)


def test_unknown_provider_falls_back_to_static():
    assert isinstance(get_scene_video_provider("does-not-exist"), StaticImageProvider)


def test_runway_without_key_falls_back_to_static(monkeypatch):
    monkeypatch.delenv("RUNWAY_API_KEY", raising=False)
    assert isinstance(get_scene_video_provider("runway"), StaticImageProvider)


def test_runway_with_key_selected(monkeypatch):
    monkeypatch.setenv("RUNWAY_API_KEY", "rw-test")
    assert isinstance(get_scene_video_provider("runway"), RunwayVideoProvider)


def test_seedance_with_key_selected(monkeypatch):
    monkeypatch.setenv("SEEDANCE_API_KEY", "sd-test")
    assert isinstance(get_scene_video_provider("seedance"), SeedanceProvider)


# ── Prompt builder ──────────────────────────────────────────────────────────

def test_prompt_includes_no_text_directive():
    prompt = build_video_prompt("A doctor reviewing an x-ray in a bright clinic")
    assert NO_TEXT_DIRECTIVE in prompt
    # The negative directive must explicitly forbid the offending elements.
    for banned in ("subtitles", "captions", "text", "logos", "watermarks"):
        assert banned in prompt.lower()


def test_prompt_includes_scene_and_animation():
    prompt = build_video_prompt(
        "A close-up of a heartbeat monitor",
        animation_prompt="slow push-in",
    )
    assert "heartbeat monitor" in prompt
    assert "slow push-in" in prompt


# ── Cost-mode render plan ───────────────────────────────────────────────────

def test_plan_balanced_keeps_resolution(monkeypatch):
    monkeypatch.setenv("VIDEO_COST_MODE", "balanced")
    monkeypatch.delenv("VIDEO_PREMIUM_PROVIDER", raising=False)
    plan = resolve_render_plan(1080, 1920, base_provider="runway")
    assert (plan.width, plan.height) == (1080, 1920)
    assert plan.base_provider == "runway"
    assert plan.premium_provider is None
    # No premium provider → ordinary provider for every scene.
    assert plan.provider_for(0) == "runway"
    assert plan.provider_for(5) == "runway"


def test_plan_low_mode_downscales_and_disables_premium(monkeypatch):
    monkeypatch.setenv("VIDEO_COST_MODE", "low")
    monkeypatch.setenv("VIDEO_PREMIUM_PROVIDER", "seedance")
    monkeypatch.setenv("VIDEO_LOW_COST_RESOLUTION", "720x1280")
    plan = resolve_render_plan(1080, 1920, base_provider="runway")
    # Portrait base → portrait low-cost dims.
    assert (plan.width, plan.height) == (720, 1280)
    assert plan.premium_provider is None
    assert plan.premium_indices == frozenset()


def test_plan_low_mode_orientation_aware(monkeypatch):
    monkeypatch.setenv("VIDEO_COST_MODE", "low")
    monkeypatch.setenv("VIDEO_LOW_COST_RESOLUTION", "720x1280")
    # Landscape base → landscape low-cost dims.
    plan = resolve_render_plan(1920, 1080, base_provider="static")
    assert (plan.width, plan.height) == (1280, 720)


def test_plan_quality_mode_premiums_hook_scene(monkeypatch):
    monkeypatch.setenv("VIDEO_COST_MODE", "quality")
    monkeypatch.setenv("VIDEO_PREMIUM_PROVIDER", "seedance")
    monkeypatch.delenv("VIDEO_PREMIUM_SCENES", raising=False)
    plan = resolve_render_plan(1080, 1920, base_provider="runway")
    assert plan.premium_provider == "seedance"
    assert plan.provider_for(0) == "seedance"   # hook scene → premium
    assert plan.provider_for(1) == "runway"     # rest → base


def test_plan_premium_scenes_parsed_from_env(monkeypatch):
    monkeypatch.setenv("VIDEO_COST_MODE", "balanced")
    monkeypatch.setenv("VIDEO_PREMIUM_PROVIDER", "seedance")
    monkeypatch.setenv("VIDEO_PREMIUM_SCENES", "0,2")
    plan = resolve_render_plan(1080, 1920, base_provider="runway")
    assert plan.provider_for(0) == "seedance"
    assert plan.provider_for(1) == "runway"
    assert plan.provider_for(2) == "seedance"


# ── Runway provider HTTP flow ───────────────────────────────────────────────

def test_runway_submit_builds_payload(monkeypatch, png):
    monkeypatch.setenv("RUNWAY_API_KEY", "rw-test")
    monkeypatch.setenv("RUNWAY_MODEL", "gen4_turbo")
    monkeypatch.setenv("RUNWAY_RATIO", "720:1280")
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return FakeResp(payload={"id": "task-123"})

    monkeypatch.setattr(scene_video.requests, "post", fake_post)
    prov = RunwayVideoProvider()
    job_id = prov.submit(png, "a cinematic clip", duration_hint=6.0)

    assert job_id == "task-123"
    assert captured["url"].endswith("/v1/image_to_video")
    assert captured["json"]["model"] == "gen4_turbo"
    assert captured["json"]["ratio"] == "720:1280"
    assert captured["json"]["duration"] == 5          # 6 snaps to nearest {5,10}
    assert captured["json"]["promptImage"].startswith("data:image/png;base64,")
    assert captured["headers"]["X-Runway-Version"]


def test_runway_poll_status_mapping(monkeypatch):
    monkeypatch.setenv("RUNWAY_API_KEY", "rw-test")
    prov = RunwayVideoProvider()

    monkeypatch.setattr(
        scene_video.requests, "get",
        lambda *a, **k: FakeResp(payload={"status": "RUNNING"}),
    )
    assert prov.poll("t1").state == JobState.PROCESSING

    monkeypatch.setattr(
        scene_video.requests, "get",
        lambda *a, **k: FakeResp(payload={"status": "FAILED", "failure": "bad image"}),
    )
    failed = prov.poll("t1")
    assert failed.state == JobState.FAILED
    assert "bad image" in failed.error

    monkeypatch.setattr(
        scene_video.requests, "get",
        lambda *a, **k: FakeResp(payload={
            "status": "SUCCEEDED",
            "output": ["https://cdn.example/clip.mp4"],
        }),
    )
    ready = prov.poll("t1")
    assert ready.state == JobState.READY
    assert ready.download_url == "https://cdn.example/clip.mp4"


def test_runway_download_writes_file(monkeypatch, tmp_path):
    monkeypatch.setenv("RUNWAY_API_KEY", "rw-test")
    prov = RunwayVideoProvider()
    monkeypatch.setattr(
        scene_video.requests, "get",
        lambda *a, **k: FakeResp(content=b"FAKEMP4DATA"),
    )
    out = tmp_path / "out" / "clip.mp4"
    job = SceneVideoJob(
        job_id="t1", state=JobState.READY, provider="runway",
        download_url="https://cdn.example/clip.mp4",
    )
    prov.download(job, str(out))
    assert out.read_bytes() == b"FAKEMP4DATA"


def test_runway_http_error_raises(monkeypatch, png):
    monkeypatch.setenv("RUNWAY_API_KEY", "rw-test")
    prov = RunwayVideoProvider()
    monkeypatch.setattr(
        scene_video.requests, "post",
        lambda *a, **k: FakeResp(ok=False, status_code=401, text="unauthorized"),
    )
    with pytest.raises(RuntimeError, match="Runway submit HTTP 401"):
        prov.submit(png, "prompt")


# ── FFmpeg command generation (no real ffmpeg) ──────────────────────────────

def test_normalize_clip_command(monkeypatch, tmp_path):
    captured = {}

    class _Res:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Res()

    monkeypatch.setattr(video.subprocess, "run", fake_run)
    video.normalize_clip(
        str(tmp_path / "in.mp4"), str(tmp_path / "out.mp4"),
        target_duration=6.0, width=720, height=1280,
    )
    cmd = captured["cmd"]
    vf = cmd[cmd.index("-vf") + 1]
    assert "scale=720:1280" in vf
    assert "tpad=stop_mode=clone" in vf      # pads short clips
    assert "-an" in cmd                      # strips audio
    # Output trimmed to exact target duration.
    assert cmd[cmd.index("-t") + 1] == "6.000"


def test_assemble_from_clips_uses_shortest(monkeypatch, tmp_path):
    captured = {}

    class _Res:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Res()

    # Audio drives final length; probe returns the audio duration.
    monkeypatch.setattr(video.subprocess, "run", fake_run)
    monkeypatch.setattr(video, "probe_duration", lambda p: 88.5)

    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"x")
    dur = video.assemble_video_from_clips(
        scene_clips=[{"clip_path": str(clip), "duration": 6.0}],
        audio_path=str(tmp_path / "a.mp3"),
        output_path=str(tmp_path / "final.mp4"),
        srt_path=None,
        width=1080, height=1920,
    )
    assert "-shortest" in captured["cmd"]
    assert dur == 88.5


def test_static_provider_respects_dim_override():
    prov = StaticImageProvider(width=720, height=1280)
    assert prov._WIDTH == 720
    assert prov._HEIGHT == 1280
