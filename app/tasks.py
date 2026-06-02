import os
import tempfile
import feedparser
import logging

from datetime import datetime, timedelta
from celery import Celery
from celery.signals import before_task_publish, task_prerun, task_postrun
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from mutagen.mp3 import MP3

from app.db import SessionLocal
from app.models import (
    Source, Article, AudioAsset, VoiceCalibration, ImageAsset, VideoAsset,
    SceneVideoAsset, Product, Brand, Campaign, AdConcept,
)
from app.extract import extract_article_text
from app.summarize import make_tts_bundle, rewrite_to_target_words, generate_social_captions, _pick_wpm
from app.tts import synthesize
from app.usage import UsageCollector
from app.logging_config import (
    configure_logging,
    get_correlation_id,
    set_correlation_id,
    set_log_context,
    clear_log_context,
)
from app.sentry_init import init_sentry

init_sentry()
configure_logging()
logger = logging.getLogger(__name__)

celery_app = Celery(
    "mvp",
    broker=os.environ["CELERY_BROKER_URL"],
    backend=os.environ["CELERY_RESULT_BACKEND"],
)


def _refund_credits(workspace_id: str | None, amount: int | None, idem: str | None) -> None:
    """Return credits charged at enqueue when a catalog-to-ad task fails.

    Idempotent (keyed on the original charge), so retries never double-refund.
    Never raises — billing failures must not mask the original task error.
    """
    if not workspace_id or not amount:
        return
    try:
        from app.billing import refund
        with SessionLocal() as db:
            refund(db, workspace_id, int(amount), ref_type="refund",
                   idempotency_key=f"refund:{idem}" if idem else None)
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("Credit refund failed for workspace=%s: %s", workspace_id, exc)
celery_app.conf.result_expires = 86400  # purge task results after 24 h


# ── Correlation ID propagation through Celery ──────────────────────────────
# Clients (API handlers) put the correlation ID into task headers via
# before_task_publish; workers unpack it in task_prerun and set the
# contextvar so every log line from the task carries the same trace.

_CORR_HEADER = "x_correlation_id"


@before_task_publish.connect
def _inject_correlation_id(headers=None, **_):
    if headers is None:
        return
    cid = get_correlation_id()
    if cid and _CORR_HEADER not in headers:
        headers[_CORR_HEADER] = cid


_task_start_times: dict[str, float] = {}


@task_prerun.connect
def _restore_correlation_id(task_id=None, task=None, **_):
    import time as _t
    headers = getattr(getattr(task, "request", None), "headers", None) or {}
    cid = headers.get(_CORR_HEADER) or ""
    set_correlation_id(cid or None)
    set_log_context(task_id=task_id or "")
    if task_id:
        _task_start_times[task_id] = _t.time()

# Scheduled generation — disabled unless ENABLE_SCHEDULED_GENERATION=true
if os.getenv("ENABLE_SCHEDULED_GENERATION", "false").lower() == "true":
    from celery.schedules import crontab
    from datetime import timedelta as _td
    _beat_interval_hours = int(os.getenv("BEAT_GENERATION_INTERVAL_HOURS", "0"))
    _beat_hour = int(os.getenv("BEAT_GENERATION_HOUR", "6"))
    celery_app.conf.beat_schedule = {
        "scheduled-content-generation": {
            "task": "generate_all_sources_daily",
            "schedule": (
                _td(hours=_beat_interval_hours)
                if _beat_interval_hours > 0
                else crontab(hour=_beat_hour, minute=0)
            ),
        }
    }

TARGET_SECONDS = int(os.getenv("TTS_TARGET_SECONDS", "180"))
BEAT_SKIP_RECENT_HOURS = int(os.getenv("BEAT_SKIP_RECENT_HOURS", "4"))
TOLERANCE_SECONDS = int(os.getenv("TTS_TOLERANCE_SECONDS", "30"))
WAY_OFF_SECONDS = int(os.getenv("TTS_WAY_OFF_SECONDS", "15"))
CAL_ALPHA = float(os.getenv("TTS_CAL_ALPHA", "0.3"))
MAX_TTS_ATTEMPTS = int(os.getenv("TTS_MAX_ATTEMPTS", "2"))
MIN_SECONDS = int(os.getenv("TTS_DURATION_MIN_SECONDS", "150"))
MAX_SECONDS = int(os.getenv("TTS_DURATION_MAX_SECONDS", "210"))


import hashlib
from contextlib import contextmanager

from app.redis_client import get_redis as _get_redis


@contextmanager
def _article_lock(source_id: str, url: str, ttl: int = 120):
    """Redis NX lock preventing two workers from running expensive work on
    the same (source_id, url) concurrently.  Fails open if Redis is down.
    """
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:20]
    key = f"lock:article:{source_id}:{url_hash}"
    acquired = False
    r = None
    try:
        r = _get_redis()
        acquired = bool(r.set(key, "1", nx=True, ex=ttl))
    except Exception:
        acquired = True  # fail open — allow the task to proceed
    try:
        yield acquired
    finally:
        if acquired and r:
            try:
                r.delete(key)
            except Exception:
                pass


# Task lifecycle signals — decrement the per-user active task counter when
# a task completes (success, failure, or revocation), and clear the
# correlation-ID contextvar so it doesn't leak into the next task.


@task_postrun.connect
def _on_task_postrun(sender=None, task_id=None, kwargs=None, **_extra):
    """Remove the finished task from the per-user active-task set."""
    import time as _t
    user_id = (kwargs or {}).get("user_id")
    if user_id and task_id:
        try:
            r = _get_redis()
            r.srem(f"user_tasks:{user_id}", task_id)
            # Clear the owner key too — capacity pruning uses its presence as
            # the "task still alive" signal.
            r.delete(f"task:{task_id}:owner")
        except Exception:
            pass
    # Record task duration
    try:
        if task_id and task_id in _task_start_times:
            from app.metrics import task_duration_seconds
            duration = _t.time() - _task_start_times.pop(task_id)
            task_name = getattr(sender, "name", "unknown") if sender else "unknown"
            task_duration_seconds.labels(task_name=task_name).observe(duration)
    except Exception:
        pass
    clear_log_context()


def _parse_dt(entry) -> datetime | None:
    for k in ("published_parsed", "updated_parsed"):
        t = getattr(entry, k, None)
        if t:
            return datetime(*t[:6])
    return None


def _mp3_duration_seconds(path: str) -> int:
    return int(round(MP3(path).info.length))


def _words_for_seconds(seconds: int, wpm: float) -> int:
    return int(round(seconds * (wpm / 60.0)))


def _speed_key(speed: float) -> float:
    return round(speed, 2)


def _score_entry(entry, now: datetime) -> float:
    """Score an RSS entry for content richness. Higher score = better pick.

    Combines recency, title quality, and summary length so the pipeline
    prefers substantive recent articles over stale or thin ones.
    """
    score = 0.0
    # Recency (0–5): linear decay over 5 days
    dt = _parse_dt(entry)
    if dt:
        age_days = max((now - dt).total_seconds() / 86_400, 0)
        score += max(0.0, 5.0 - age_days)
    # Title quality (0–3): prefer informative titles in the 30–150 char range
    title = (entry.get("title") or "").strip()
    if 30 <= len(title) <= 150:
        score += 3
    elif len(title) > 10:
        score += 1
    # Summary richness (0–2): longer fallback text = richer source content available
    summary = (entry.get("summary") or entry.get("description") or "").strip()
    if len(summary) > 300:
        score += 2
    elif len(summary) > 80:
        score += 1
    return score


@celery_app.task(name="generate_all_sources_daily")
def generate_all_sources_daily() -> dict:
    """Dispatch audio generation for every source. Triggered by Celery Beat.

    Skips sources that already have an article created within the last
    BEAT_SKIP_RECENT_HOURS hours to avoid redundant generation runs.
    """
    target_seconds = int(os.getenv("TTS_TARGET_SECONDS", "180"))
    n_scenes = int(os.getenv("STORYBOARD_SCENES", "8"))
    cutoff = datetime.utcnow() - timedelta(hours=BEAT_SKIP_RECENT_HOURS)

    with SessionLocal() as db:
        sources = db.execute(select(Source)).scalars().all()
        recently_processed: set[str] = set(
            db.execute(
                select(Article.source_id)
                .where(Article.created_at >= cutoff)
                .distinct()
            ).scalars().all()
        )

    dispatched = 0
    skipped = 0
    for src in sources:
        if src.id in recently_processed:
            logger.info("Beat: skipping source %s — processed within last %dh", src.id, BEAT_SKIP_RECENT_HOURS)
            skipped += 1
            continue
        try:
            celery_app.send_task(
                "generate_latest_for_source",
                kwargs={"source_id": src.id, "target_seconds": target_seconds, "n_scenes": n_scenes},
            )
            dispatched += 1
        except Exception as exc:
            logger.error("Beat: failed to queue source %s: %s", src.id, exc)

    logger.info("Beat: dispatched=%d skipped=%d total=%d", dispatched, skipped, len(sources))
    return {"dispatched": dispatched, "skipped": skipped, "total_sources": len(sources)}


@celery_app.task(name="prepare_article", bind=True)
def prepare_article(
    self,
    source_id: str,
    article_url: str,
    article_title: str = "",
    article_summary: str | None = None,
    article_published_at: str | None = None,
    n_scenes: int = 8,
    target_seconds: int = TARGET_SECONDS,
    openai_api_key: str | None = None,
    user_id: str | None = None,
    language: str | None = None,
    selected_platforms: list | None = None,
    animation_prompt: str | None = None,
    voice_id: str | None = None,
    voice_model_id: str | None = None,
    voice_speed: float | None = None,
) -> dict:
    """Extract article content and generate script + storyboard WITHOUT audio synthesis.

    Returns article_id so the frontend can display a script preview before the user
    commits to TTS synthesis.
    """
    with SessionLocal() as db:
        src = db.get(Source, source_id)
        if not src:
            raise ValueError(f"Unknown source_id: {source_id}")

        title = (article_title or "").strip() or "Untitled"
        url = article_url.strip()
        fallback = (article_summary or "").strip()
        published_at = None
        if article_published_at:
            try:
                published_at = datetime.fromisoformat(article_published_at)
            except Exception:
                pass

        eff_language = language or os.getenv("TTS_OUTPUT_LANGUAGE", "es-MX")

        # Per-platform targeting: each selected platform gets its own script
        # sized to min(user_target, platform.max_duration). Platforms that land
        # on the same effective duration share a single script to save tokens.
        eff_platforms = selected_platforms or ["tiktok"]
        from app.platforms import PROFILES as _PROFILES
        platform_targets: dict[str, int] = {}
        for p in eff_platforms:
            prof = _PROFILES.get(p)
            if not prof:
                continue
            platform_targets[p] = min(target_seconds, prof.max_duration)
        if not platform_targets:
            platform_targets = {"tiktok": min(target_seconds, _PROFILES["tiktok"].max_duration)}
        # User-facing summary uses the longest target (canonical script).
        max_platform_target = max(platform_targets.values())

        article = Article(
            source_id=src.id, title=title, url=url, published_at=published_at,
            user_id=user_id, language=eff_language,
            selected_platforms=eff_platforms,
            animation_prompt=animation_prompt,
        )
        db.add(article)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            article = db.execute(
                select(Article).where(Article.source_id == src.id, Article.url == url)
            ).scalar_one()
            if user_id and not article.user_id:
                article.user_id = user_id
            article.language = eff_language
            article.selected_platforms = eff_platforms or article.selected_platforms or ["tiktok"]
            if animation_prompt:
                article.animation_prompt = animation_prompt
            db.commit()

        with _article_lock(source_id, url) as lock_acquired:
            if not lock_acquired:
                logger.info("Article lock not acquired for url=%s — skipping duplicate work", url)
                return {
                    "article_id": article.id,
                    "word_count": len((article.tts_script or "").split()),
                    "estimated_duration_seconds": 0,
                    "skipped": True,
                }

            collector = UsageCollector(article_id=article.id, user_id=user_id)

            self.update_state(state="PROGRESS", meta={"stage": "extracting", "msg": "Extracting article content…"})
            raw = extract_article_text(url, fallback_text=fallback)
            if not raw:
                raw = fallback or title

            # Calibrated WPM: prefer the user's specific voice + model + speed
            # observed history. Fall back to the language default only for a
            # cold-start voice that has never been synthesized.
            resolved_model_id = voice_model_id or os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
            resolved_speed = _speed_key(voice_speed if voice_speed is not None else float(os.getenv("ELEVENLABS_SPEED", "1.0")))
            wpm_estimate: float
            if voice_id:
                cal = db.get(VoiceCalibration, (voice_id, resolved_model_id, resolved_speed))
                wpm_estimate = float(cal.wpm_estimate) if cal else float(_pick_wpm(eff_language))
            else:
                wpm_estimate = float(_pick_wpm(eff_language))

            # One script per unique effective duration (multiple platforms with
            # the same duration share a script).
            unique_targets = sorted(set(platform_targets.values()), reverse=True)
            script_by_target: dict[int, dict] = {}
            for idx, dur in enumerate(unique_targets, start=1):
                target_words = _words_for_seconds(dur, wpm_estimate)
                tol_words = max(10, int(round(target_words * 0.10)))
                self.update_state(
                    state="PROGRESS",
                    meta={
                        "stage": "scripting",
                        "msg": f"Generating script {idx}/{len(unique_targets)} (~{dur}s, ~{target_words} words)…",
                    },
                )
                bundle = make_tts_bundle(
                    title=title,
                    body=raw,
                    language_hint=src.language_hint,
                    target_seconds=dur,
                    n_scenes=n_scenes,
                    target_words=target_words,
                    tol_words=tol_words,
                    wpm_estimate=wpm_estimate,
                    api_key=openai_api_key,
                    collector=collector,
                    output_language=eff_language,
                )
                wc = bundle.get("word_count") or len(bundle["script"].split())
                script_by_target[dur] = {
                    "script": bundle["script"],
                    "word_count": wc,
                    "target_seconds": dur,
                    "estimated_duration_seconds": int(round((wc / max(wpm_estimate, 1)) * 60)),
                    "scenes": bundle.get("scenes") or [],
                    "total_duration_estimate": bundle.get("total_duration_estimate", 0),
                }

            # Pack per-platform map: platform -> script payload (a reference
            # back to the shared script-by-target entry).
            platform_scripts: dict[str, dict] = {}
            for platform_id, dur in platform_targets.items():
                entry = script_by_target[dur]
                platform_scripts[platform_id] = {
                    "script": entry["script"],
                    "word_count": entry["word_count"],
                    "target_seconds": entry["target_seconds"],
                    "estimated_duration_seconds": entry["estimated_duration_seconds"],
                    "wpm_estimate": wpm_estimate,
                    "storyboard": {
                        "scenes": entry["scenes"],
                        "total_duration_estimate": entry["total_duration_estimate"],
                    },
                }

            # Canonical = longest-duration script (backwards-compatible surface).
            primary = script_by_target[max_platform_target]
            script = primary["script"]
            scenes = primary["scenes"]
            word_count = primary["word_count"]

            article.raw_text = raw
            article.tts_script = script
            article.script_language = eff_language
            article.summary_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            article.storyboard_json = {
                "scenes": scenes,
                "total_duration_estimate": primary["total_duration_estimate"],
            }
            article.platform_scripts_json = platform_scripts
            db.commit()

            try:
                from app.analysis import analyze_article as _analyze
                self.update_state(state="PROGRESS", meta={"stage": "analyzing", "msg": "Analyzing content…"})
                article.analysis_json = _analyze(script, api_key=openai_api_key, collector=collector)
                db.commit()
            except Exception as exc:
                logger.warning("Analysis failed (non-fatal): %s", exc)

            try:
                platforms = article.selected_platforms or ["tiktok"]
                self.update_state(state="PROGRESS", meta={"stage": "captions", "msg": "Generating social captions…"})
                article.social_captions_json = generate_social_captions(
                    title=title, script=script, platforms=platforms,
                    output_language=eff_language, api_key=openai_api_key, collector=collector,
                )
                db.commit()
            except Exception as exc:
                logger.warning("Social caption generation failed (non-fatal): %s", exc)

            collector.flush(db)

            estimated_duration = int(round(word_count / (wpm_estimate / 60.0)))
            logger.info("Prepared article id=%s words=%d est=%ds", article.id, word_count, estimated_duration)
            return {
                "article_id": article.id,
                "word_count": word_count,
                "estimated_duration_seconds": estimated_duration,
            }


def _synthesize_one_script(
    task_self,
    *,
    script: str,
    word_count: int,
    target_seconds: int,
    used_voice_id: str,
    model_id: str,
    output_format: str,
    audio_dir: str,
    output_path: str,
    eff_language: str,
    elevenlabs_api_key: str | None,
    openai_api_key: str | None,
    collector: "UsageCollector",
    progress_label: str = "",
) -> tuple[str, float, str, int]:
    """Synthesize one script to one audio file, tolerating up to MAX_TTS_ATTEMPTS.

    Rewrites toward MIN..MAX duration between retries. Returns
    (final_path, duration_seconds, final_script, final_word_count).
    """
    # Relative window expressed as a fraction of the script's *own* target
    # instead of the global MIN/MAX constants, so a 60s script isn't judged
    # against the same bounds as a 180s one.
    tol = max(10.0, target_seconds * 0.15)
    accept_min = target_seconds - tol
    accept_max = target_seconds + tol
    duration: float | None = None
    last_error: str | None = None
    final_path = output_path

    for attempt in range(1, MAX_TTS_ATTEMPTS + 1):
        label = f"{progress_label} " if progress_label else ""
        task_self.update_state(
            state="PROGRESS",
            meta={
                "stage": "synthesizing",
                "msg": f"{label}Synthesizing audio (attempt {attempt}/{MAX_TTS_ATTEMPTS})…",
            },
        )
        tmp_path: str | None = None
        accepted = False
        try:
            audio_bytes = synthesize(
                script, voice_id=used_voice_id, model_id=model_id,
                output_format=output_format, api_key=elevenlabs_api_key,
                collector=collector,
                language_code=eff_language[:2] if eff_language else None,
            )
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3", dir=audio_dir) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name

            duration = _mp3_duration_seconds(tmp_path)

            if accept_min <= duration <= accept_max:
                os.replace(tmp_path, final_path)
                tmp_path = None
                accepted = True
                break

            if attempt >= MAX_TTS_ATTEMPTS:
                # Accept anyway on last attempt to avoid total failure.
                os.replace(tmp_path, final_path)
                tmp_path = None
                accepted = True
                break

            wc = word_count or len(script.split())
            desired = target_seconds
            target_wc = max(30, int(round(wc * (desired / max(duration, 1)))))
            script = rewrite_to_target_words(
                script, target_words=target_wc, tol_words=20,
                api_key=openai_api_key, collector=collector,
            )
            word_count = len(script.split())
        except Exception as e:
            last_error = str(e)
            duration = None
        finally:
            if tmp_path and not accepted:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    if duration is None:
        raise RuntimeError(f"TTS failed after retries. error={last_error}")
    return final_path, float(duration), script, int(word_count)


def _synthesize_per_platform(
    task_self,
    db,
    article: Article,
    platform_scripts: dict,
    *,
    used_voice_id: str,
    model_id: str,
    output_format: str,
    speed: float,
    eff_language: str,
    audio_dir: str,
    openai_api_key: str | None,
    elevenlabs_api_key: str | None,
    collector: "UsageCollector",
) -> dict:
    """Synthesize one audio per unique script; fan-out AudioAsset rows per platform."""
    # Group platforms by script text so we only hit TTS once per unique text.
    groups: dict[str, list[str]] = {}
    for platform_id, payload in platform_scripts.items():
        txt = (payload or {}).get("script") or ""
        if not txt:
            continue
        groups.setdefault(txt, []).append(platform_id)

    if not groups:
        raise RuntimeError("platform_scripts_json has no usable entries")

    audios: list[dict] = []
    total_groups = len(groups)
    for idx, (script_text, platform_ids) in enumerate(groups.items(), start=1):
        # Target duration = the script's declared target (all platforms in the
        # group share the same target since we grouped by text).
        first_payload = platform_scripts[platform_ids[0]]
        target_seconds = int(first_payload.get("target_seconds") or 180)
        word_count = int(first_payload.get("word_count") or len(script_text.split()))

        joined = "+".join(sorted(platform_ids))
        out_path = os.path.join(audio_dir, f"{article.id}_{used_voice_id}_{joined}.mp3")
        final_path, duration, final_script, final_wc = _synthesize_one_script(
            task_self,
            script=script_text,
            word_count=word_count,
            target_seconds=target_seconds,
            used_voice_id=used_voice_id,
            model_id=model_id,
            output_format=output_format,
            audio_dir=audio_dir,
            output_path=out_path,
            eff_language=eff_language,
            elevenlabs_api_key=elevenlabs_api_key,
            openai_api_key=openai_api_key,
            collector=collector,
            progress_label=f"[{idx}/{total_groups} {joined}]",
        )

        # Update calibration EMA from the observed WPM of this clip.
        try:
            observed_wpm = (final_wc / max(duration, 1)) * 60.0
            cal = db.get(VoiceCalibration, (used_voice_id, model_id, speed))
            if not cal:
                cal = VoiceCalibration(
                    voice_id=used_voice_id, model_id=model_id, speed=speed,
                    wpm_estimate=observed_wpm, samples=1,
                )
                db.add(cal)
            else:
                cal.wpm_estimate = (1 - CAL_ALPHA) * cal.wpm_estimate + CAL_ALPHA * observed_wpm
                cal.samples += 1
        except Exception as exc:
            logger.warning("Voice calibration update failed (non-fatal): %s", exc)

        # Persist the (possibly rewritten) script back into platform_scripts_json
        # so the frontend shows the exact text that matches the audio.
        for pid in platform_ids:
            entry = platform_scripts.get(pid) or {}
            entry["script"] = final_script
            entry["word_count"] = final_wc
            entry["estimated_duration_seconds"] = int(round(duration))
            platform_scripts[pid] = entry

        # One AudioAsset per platform, all pointing at the shared file.
        for pid in platform_ids:
            audio = AudioAsset(
                article_id=article.id,
                voice_id=used_voice_id,
                model_id=model_id,
                output_format=output_format,
                file_path=final_path,
                tts_provider="elevenlabs",
                target_seconds=target_seconds,
                estimated_seconds=int(round(duration)),
                word_count=final_wc,
                platform=pid,
                status="ready",
            )
            db.add(audio)
            db.flush()
            audios.append({
                "audio_id": audio.id,
                "platform": pid,
                "duration_seconds": duration,
                "word_count": final_wc,
                "file_path": final_path,
            })

    # Keep the canonical tts_script in sync with the longest-duration platform.
    longest_platform = max(platform_scripts.items(), key=lambda kv: (kv[1] or {}).get("target_seconds", 0))[0]
    article.tts_script = platform_scripts[longest_platform]["script"]
    article.platform_scripts_json = dict(platform_scripts)  # force SQLAlchemy JSON diff
    db.commit()

    collector.flush(db)

    return {
        "article_id": article.id,
        "audios": audios,
        # Back-compat: primary audio = longest-duration platform
        "audio_id": next((a["audio_id"] for a in audios if a["platform"] == longest_platform), None),
        "duration_seconds": next((a["duration_seconds"] for a in audios if a["platform"] == longest_platform), None),
        "title": article.title,
        "url": article.url,
    }


@celery_app.task(name="generate_latest_for_source", bind=True)
def generate_latest_for_source(
    self,
    source_id: str,
    voice_id: str | None = None,
    target_seconds: int = TARGET_SECONDS,
    n_scenes: int = 8,
    openai_api_key: str | None = None,
    elevenlabs_api_key: str | None = None,
    user_id: str | None = None,
    article_url: str | None = None,
    article_title: str | None = None,
    article_summary: str | None = None,
    article_published_at: str | None = None,
    article_id: str | None = None,
    language: str | None = None,
) -> dict:
    audio_dir = os.getenv("AUDIO_DIR", "/data/audio")
    os.makedirs(audio_dir, exist_ok=True)

    with SessionLocal() as db:
        src = db.get(Source, source_id)
        if not src:
            raise ValueError(f"Unknown source_id: {source_id}")

        # TTS config resolved up-front — needed by both the script+TTS path and the TTS-only path
        used_voice_id = voice_id or os.getenv("ELEVENLABS_VOICE_ID")
        if not used_voice_id:
            raise RuntimeError("Missing ELEVENLABS_VOICE_ID")
        model_id = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
        output_format = os.getenv("ELEVENLABS_OUTPUT_FORMAT", "mp3_44100_128")
        speed = _speed_key(float(os.getenv("ELEVENLABS_SPEED", "1.0")))

        if article_id:
            # ── Shortcut: article already prepared — jump straight to TTS ────
            article = db.get(Article, article_id)
            if not article:
                raise ValueError(f"Article {article_id} not found")
            if not article.tts_script:
                raise RuntimeError("Article has no script — call /articles/prepare first")
            script = article.tts_script
            scenes = (article.storyboard_json or {}).get("scenes") or []
            word_count = len(script.split())
            eff_language = language or article.language or os.getenv("TTS_OUTPUT_LANGUAGE", "es-MX")
            collector = UsageCollector(article_id=article.id, user_id=user_id)
            logger.info("TTS-only run for pre-prepared article id=%s words=%d", article.id, word_count)

            # Per-platform path: synthesize one audio per unique script.
            platform_scripts = article.platform_scripts_json or {}
            if platform_scripts:
                return _synthesize_per_platform(
                    self, db, article, platform_scripts,
                    used_voice_id=used_voice_id,
                    model_id=model_id,
                    output_format=output_format,
                    speed=speed,
                    eff_language=eff_language,
                    audio_dir=audio_dir,
                    openai_api_key=openai_api_key,
                    elevenlabs_api_key=elevenlabs_api_key,
                    collector=collector,
                )
        else:
            # ── Full path: obtain article + extract content + generate script ─
            if article_url:
                title = (article_title or "").strip() or "Untitled"
                url = article_url.strip()
                fallback = (article_summary or "").strip()
                published_at = None
                if article_published_at:
                    try:
                        published_at = datetime.fromisoformat(article_published_at)
                    except Exception:
                        pass
                logger.info("Using pre-selected article: title=%r url=%s", title, url)
            else:
                self.update_state(state="PROGRESS", meta={"stage": "fetching", "msg": "Fetching latest RSS entry…"})
                feed = feedparser.parse(src.rss_url)
                if not feed.entries:
                    raise RuntimeError("No RSS entries found")
                lookback_days = int(os.getenv("RSS_LOOKBACK_DAYS", "7"))
                cutoff = datetime.utcnow() - timedelta(days=lookback_days)
                candidates = [e for e in feed.entries if _parse_dt(e) and _parse_dt(e) >= cutoff]
                now = datetime.utcnow()
                entry = max(candidates, key=lambda e: _score_entry(e, now)) if candidates else feed.entries[0]
                title = (entry.get("title") or "").strip() or "Untitled"
                url = (entry.get("link") or "").strip()
                if not url:
                    raise RuntimeError("RSS entry has no link/url")
                fallback = (entry.get("summary") or entry.get("description") or "").strip()
                published_at = _parse_dt(entry)
                logger.info("Selected RSS entry: title=%r published_at=%s url=%s", title, published_at, url)

            eff_language = language or os.getenv("TTS_OUTPUT_LANGUAGE", "es-MX")
            article = Article(
                source_id=src.id, title=title, url=url, published_at=published_at,
                user_id=user_id, language=eff_language,
            )
            db.add(article)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                article = db.execute(
                    select(Article).where(Article.source_id == src.id, Article.url == url)
                ).scalar_one()
                if user_id and not article.user_id:
                    article.user_id = user_id
                article.language = eff_language
                db.commit()

            self.update_state(state="PROGRESS", meta={"stage": "extracting", "msg": "Extracting article content…"})
            raw = extract_article_text(url, fallback_text=fallback)
            if not raw:
                raw = fallback or title

            cal = db.get(VoiceCalibration, (used_voice_id, model_id, speed))
            wpm = cal.wpm_estimate if cal else 140.0
            target_words = _words_for_seconds(target_seconds, wpm)
            tol_words = _words_for_seconds(TOLERANCE_SECONDS, wpm)

            self.update_state(state="PROGRESS", meta={"stage": "scripting", "msg": "Generating script & storyboard…"})
            collector = UsageCollector(article_id=article.id, user_id=user_id)
            bundle = make_tts_bundle(
                title=title,
                body=raw,
                language_hint=src.language_hint,
                target_seconds=target_seconds,
                n_scenes=n_scenes,
                target_words=target_words,
                tol_words=tol_words,
                wpm_estimate=wpm,
                api_key=openai_api_key,
                collector=collector,
                output_language=eff_language,
            )

            script = bundle["script"]
            scenes = bundle.get("scenes") or []
            word_count = bundle.get("word_count")
            logger.info("Script generated words=%s preview=%r", word_count, script[:400])

            article.raw_text = raw
            article.tts_script = script
            article.script_language = eff_language
            article.summary_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            article.storyboard_json = {
                "scenes": scenes,
                "total_duration_estimate": bundle.get("total_duration_estimate", 0),
            }
            db.commit()

            try:
                platforms = article.selected_platforms or ["tiktok"]
                article.social_captions_json = generate_social_captions(
                    title=title, script=script, platforms=platforms,
                    output_language=eff_language, api_key=openai_api_key, collector=collector,
                )
                db.commit()
            except Exception as exc:
                logger.warning("Social caption generation failed (non-fatal): %s", exc)

        # ── TTS synthesis (common to all paths) ──────────────────────────────
        final_path = os.path.join(audio_dir, f"{article.id}_{used_voice_id}.mp3")
        duration = None
        last_error = None
        accept_min = MIN_SECONDS - WAY_OFF_SECONDS
        accept_max = MAX_SECONDS + WAY_OFF_SECONDS

        for attempt in range(1, MAX_TTS_ATTEMPTS + 1):
            self.update_state(
                state="PROGRESS",
                meta={"stage": "synthesizing", "msg": f"Synthesizing audio (attempt {attempt}/{MAX_TTS_ATTEMPTS})…"},
            )
            tmp_path: str | None = None
            accepted = False
            try:
                audio_bytes = synthesize(
                    script, voice_id=used_voice_id, model_id=model_id,
                    output_format=output_format, api_key=elevenlabs_api_key,
                    collector=collector,
                    language_code=eff_language[:2] if eff_language else None,
                )
                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3", dir=audio_dir) as tmp:
                    tmp.write(audio_bytes)
                    tmp_path = tmp.name

                duration = _mp3_duration_seconds(tmp_path)

                if accept_min <= duration <= accept_max:
                    os.replace(tmp_path, final_path)
                    tmp_path = None  # promoted to final_path — no cleanup needed
                    accepted = True
                    break

                if attempt >= MAX_TTS_ATTEMPTS:
                    break

                wc = word_count or len(script.split())
                desired = MIN_SECONDS if duration < MIN_SECONDS else MAX_SECONDS
                target_wc = int(round(wc * (desired / max(duration, 1))))
                script = rewrite_to_target_words(
                    script, target_words=target_wc, tol_words=20,
                    api_key=openai_api_key, collector=collector,
                )
                word_count = len(script.split())

            except Exception as e:
                last_error = str(e)
                duration = None
            finally:
                # Clean up temp file if it exists and wasn't promoted to final
                if tmp_path and not accepted:
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass

        if duration is None or not (accept_min <= duration <= accept_max):
            raise RuntimeError(f"TTS out of range after retries. duration={duration}, error={last_error}")

        article.tts_script = script

        try:
            from app.analysis import analyze_article as _analyze
            self.update_state(state="PROGRESS", meta={"stage": "analyzing", "msg": "Analyzing content…"})
            article.analysis_json = _analyze(script, api_key=openai_api_key, collector=collector)
        except Exception as exc:
            logger.warning("Analysis failed (non-fatal): %s", exc)

        observed_wpm = (word_count / max(duration, 1)) * 60.0

        cal = db.get(VoiceCalibration, (used_voice_id, model_id, speed))
        if not cal:
            cal = VoiceCalibration(
                voice_id=used_voice_id,
                model_id=model_id,
                speed=speed,
                wpm_estimate=observed_wpm,
                samples=1,
            )
            db.add(cal)
        else:
            cal.wpm_estimate = (1 - CAL_ALPHA) * cal.wpm_estimate + CAL_ALPHA * observed_wpm
            cal.samples += 1

        db.commit()

        audio = AudioAsset(
            article_id=article.id,
            voice_id=used_voice_id,
            model_id=model_id,
            output_format=output_format,
            file_path=final_path,
            tts_provider="elevenlabs",
            target_seconds=target_seconds,
            estimated_seconds=duration,
            word_count=word_count,
            status="ready",
        )
        db.add(audio)
        db.commit()

        collector.flush(db)
        logger.info("Saved audio duration=%ss path=%s", duration, final_path)

        return {
            "article_id": article.id,
            "audio_id": audio.id,
            "audio_path": audio.file_path,
            "duration_seconds": duration,
            "word_count": word_count,
            "title": article.title,
            "url": article.url,
            "scenes": scenes,
        }


@celery_app.task(name="generate_video_for_article", bind=True, time_limit=900, soft_time_limit=840)
def generate_video_for_article(
    self,
    article_id: str,
    audio_asset_id: str | None = None,
    burn_subtitles: bool = True,
    openai_api_key: str | None = None,
    platform: str = "tiktok",
    animation_prompt: str | None = None,
) -> dict:
    """Generate scene images with DALL-E then assemble an MP4 with FFmpeg.

    Reuses already-generated ImageAssets so re-runs don't re-bill for images.
    Falls back to black placeholder frames when image generation fails.
    """
    from app.image_gen import generate_and_save
    from app.video import assemble_video
    from app.captions import storyboard_to_captions, captions_to_srt
    from app.schemas import Storyboard
    from app.platforms import get_profile as _get_profile

    image_dir = os.getenv("IMAGE_DIR", "/data/images")
    video_dir = os.getenv("VIDEO_DIR", "/data/video")
    os.makedirs(image_dir, exist_ok=True)
    os.makedirs(video_dir, exist_ok=True)

    plat_profile = _get_profile(platform)

    with SessionLocal() as db:
        article = db.get(Article, article_id)
        if not article:
            raise ValueError(f"Unknown article_id: {article_id}")
        if not article.storyboard_json:
            raise RuntimeError("Article has no storyboard — run audio generation first")

        try:
            storyboard = Storyboard(**article.storyboard_json)
        except Exception as exc:
            raise RuntimeError(f"Storyboard format incompatible — regenerate the article: {exc}") from exc

        if audio_asset_id:
            audio = db.get(AudioAsset, audio_asset_id)
            if not audio:
                raise ValueError(f"Unknown audio_asset_id: {audio_asset_id}")
        else:
            # Prefer an audio that was synthesized specifically for THIS platform;
            # fall back to any ready audio when the article was generated before
            # per-platform audio existed.
            audio = db.execute(
                select(AudioAsset)
                .where(
                    AudioAsset.article_id == article_id,
                    AudioAsset.status == "ready",
                    AudioAsset.platform == platform,
                )
                .order_by(AudioAsset.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            if not audio:
                audio = db.execute(
                    select(AudioAsset)
                    .where(AudioAsset.article_id == article_id, AudioAsset.status == "ready")
                    .order_by(AudioAsset.created_at.desc())
                    .limit(1)
                ).scalar_one_or_none()
        if not audio:
            raise RuntimeError("No ready audio asset — run audio generation first")
        if not os.path.exists(audio.file_path):
            raise RuntimeError(f"Audio file missing from disk: {audio.file_path}")

        video_record = VideoAsset(
            article_id=article_id,
            audio_asset_id=audio.id,
            file_path="",
            status="created",
            platform=platform,
            width=plat_profile.width,
            height=plat_profile.height,
        )
        db.add(video_record)
        db.commit()

        collector = UsageCollector(article_id=article_id, user_id=getattr(article, "user_id", None))
        total_scenes = len(storyboard.scenes)
        srt_path: str | None = None  # declared here so except block can clean it up

        try:
            # ── Image generation ─────────────────────────────────────────────
            scene_inputs: list[dict] = []
            for i, scene in enumerate(storyboard.scenes):
                self.update_state(
                    state="PROGRESS",
                    meta={
                        "stage": "imaging",
                        "msg": f"Generating scene images… {i}/{total_scenes}",
                        "progress": i / max(total_scenes, 1),
                    },
                )
                img_path = os.path.join(image_dir, f"{article_id}_scene_{scene.scene_number}.png")

                existing = db.execute(
                    select(ImageAsset).where(
                        ImageAsset.article_id == article_id,
                        ImageAsset.scene_number == scene.scene_number,
                        ImageAsset.status == "ready",
                    )
                ).scalar_one_or_none()

                if existing and os.path.exists(existing.file_path):
                    logger.info("Reusing image for scene %d", scene.scene_number)
                    img_path = existing.file_path
                else:
                    logger.info("Generating image for scene %d: %r", scene.scene_number, scene.visual_prompt[:80])
                    success = generate_and_save(
                        scene.visual_prompt, img_path, api_key=openai_api_key,
                        collector=collector, scene_number=scene.scene_number,
                    )
                    img_record = ImageAsset(
                        article_id=article_id,
                        scene_number=scene.scene_number,
                        visual_prompt=scene.visual_prompt,
                        file_path=img_path,
                        status="ready" if success else "failed",
                        error=None if success else "generation failed, placeholder used",
                    )
                    db.add(img_record)
                    db.commit()

                if not os.path.exists(img_path):
                    raise RuntimeError(
                        f"Scene {scene.scene_number} image missing from disk ({img_path}) — aborting video assembly"
                    )
                scene_inputs.append({"image_path": img_path, "duration": max(scene.duration_estimate, 1.0)})

            # ── SRT captions ─────────────────────────────────────────────────
            if burn_subtitles:
                audio_seconds: float | None = None
                try:
                    audio_seconds = float(_mp3_duration_seconds(audio.file_path))
                except Exception:
                    audio_seconds = None
                captions = storyboard_to_captions(storyboard, actual_audio_duration=audio_seconds)
                srt_content = captions_to_srt(captions)
                srt_path = os.path.join(video_dir, f"{article_id}.srt")
                with open(srt_path, "w", encoding="utf-8") as fh:
                    fh.write(srt_content)

            # ── Video assembly ────────────────────────────────────────────────
            self.update_state(state="PROGRESS", meta={"stage": "rendering", "msg": "Assembling video…"})
            output_path = os.path.join(video_dir, f"{article_id}_{video_record.id}.mp4")
            actual_duration = assemble_video(
                scenes=scene_inputs,
                audio_path=audio.file_path,
                output_path=output_path,
                srt_path=srt_path,
                width=plat_profile.width,
                height=plat_profile.height,
            )

            video_record.file_path = output_path
            video_record.duration_seconds = actual_duration
            video_record.has_subtitles = 1 if (burn_subtitles and srt_path is not None) else 0
            video_record.status = "ready"
            db.commit()

            collector.flush(db, video_asset_id=video_record.id)
            logger.info("Video ready: %s (%.1fs)", output_path, actual_duration)
            return {
                "article_id": article_id,
                "video_id": video_record.id,
                "video_path": output_path,
                "duration_seconds": actual_duration,
                "scene_count": total_scenes,
                "has_subtitles": video_record.has_subtitles,
            }

        except Exception as exc:
            video_record.status = "failed"
            video_record.error = str(exc)[:500]
            db.commit()
            collector.flush(db, video_asset_id=video_record.id)
            raise
        finally:
            # SRT is temporary — subtitles get burned into the MP4, so the file
            # itself is not needed after FFmpeg completes (success or failure).
            if srt_path and os.path.exists(srt_path):
                try:
                    os.remove(srt_path)
                except OSError:
                    pass


@celery_app.task(name="regenerate_script_for_article", bind=True)
def regenerate_script_for_article(
    self,
    article_id: str,
    n_scenes: int = 8,
    openai_api_key: str | None = None,
) -> dict:
    """Regenerate the TTS script and storyboard from the article's existing raw_text."""
    with SessionLocal() as db:
        article = db.get(Article, article_id)
        if not article:
            raise ValueError(f"Unknown article_id: {article_id}")
        if not article.raw_text:
            raise RuntimeError("Article has no raw_text — cannot regenerate script")

        self.update_state(state="PROGRESS", meta={"stage": "scripting", "msg": "Regenerating script & storyboard…"})

        voice_id = os.getenv("ELEVENLABS_VOICE_ID", "")
        model_id = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
        speed = round(float(os.getenv("ELEVENLABS_SPEED", "1.0")), 2)

        cal = db.get(VoiceCalibration, (voice_id, model_id, speed)) if voice_id else None
        wpm = cal.wpm_estimate if cal else 140.0

        target_seconds = int(os.getenv("TTS_TARGET_SECONDS", "180"))
        tol_seconds = int(os.getenv("TTS_TOLERANCE_SECONDS", "30"))
        target_words = _words_for_seconds(target_seconds, wpm)
        tol_words = _words_for_seconds(tol_seconds, wpm)

        collector = UsageCollector(article_id=article_id, user_id=getattr(article, "user_id", None))
        bundle = make_tts_bundle(
            title=article.title,
            body=article.raw_text,
            language_hint=None,
            target_seconds=target_seconds,
            n_scenes=n_scenes,
            target_words=target_words,
            tol_words=tol_words,
            wpm_estimate=wpm,
            api_key=openai_api_key,
            collector=collector,
        )

        article.tts_script = bundle["script"]
        article.storyboard_json = {
            "scenes": bundle.get("scenes") or [],
            "total_duration_estimate": bundle.get("total_duration_estimate", 0),
        }

        try:
            from app.analysis import analyze_article as _analyze
            self.update_state(state="PROGRESS", meta={"stage": "analyzing", "msg": "Analyzing content…"})
            article.analysis_json = _analyze(bundle["script"], api_key=openai_api_key, collector=collector)
        except Exception as exc:
            logger.warning("Analysis failed (non-fatal): %s", exc)

        db.commit()
        collector.flush(db)

        return {
            "article_id": article_id,
            "word_count": bundle.get("word_count"),
            "scene_count": len(bundle.get("scenes") or []),
        }


# ── Catalog-to-ad: product analysis ─────────────────────────────────────────────

@celery_app.task(name="analyze_product", bind=True)
def analyze_product_task(
    self,
    product_id: str,
    openai_api_key: str | None = None,
    bill_workspace_id: str | None = None,
    bill_amount: int = 0,
    bill_idem: str | None = None,
) -> dict:
    """Analyze a single catalog product into ad-strategy material.

    Writes the result to ``product.analysis_json`` (audience, pains, benefits,
    angles, claim_risk). The product analogue of ``regenerate_script_for_article``'s
    analysis step. Never blocks on tracking failures. Refunds credits on failure.
    """
    from app.analyze import analyze_product as _analyze_product

    try:
      with SessionLocal() as db:
        product = db.get(Product, product_id)
        if not product:
            raise ValueError(f"Unknown product_id: {product_id}")

        # Optional brand context shapes tone/audience of the analysis.
        brand_context = None
        if product.brand_id:
            brand = db.get(Brand, product.brand_id)
            if brand:
                brand_context = {
                    "name": brand.name,
                    "tone": brand.tone_json,
                    "target_audience": brand.target_audience_json,
                }

        self.update_state(
            state="PROGRESS",
            meta={"stage": "analyzing", "msg": "Analyzing product…"},
        )

        collector = UsageCollector(
            workspace_id=product.workspace_id,
            product_id=product.id,
        )
        product_data = {
            "title": product.title,
            "description": product.description,
            "price": product.price,
            "currency": product.currency,
            "category": product.category,
            "attributes": product.attributes_json or {},
        }
        analysis = _analyze_product(
            product_data,
            brand_context,
            api_key=openai_api_key,
            collector=collector,
        )

        product.analysis_json = analysis
        product.status = "analyzed"
        db.commit()
        collector.flush(db)

        return {
            "product_id": product_id,
            "claim_risk": analysis.get("claim_risk"),
            "angle_count": len(analysis.get("ad_angles") or []),
        }
    except Exception:
        _refund_credits(bill_workspace_id, bill_amount, bill_idem)
        raise


# ── Catalog-to-ad: ad concept generation ────────────────────────────────────────

def _angle_text(entry) -> str | None:
    """ad_angles entries may be {'angle','rationale'} dicts or plain strings."""
    if isinstance(entry, dict):
        return entry.get("angle")
    return entry or None


@celery_app.task(name="generate_ad_concepts", bind=True)
def generate_ad_concepts(
    self,
    product_id: str,
    campaign_id: str | None = None,
    goal: str | None = None,
    platforms: list | None = None,
    language: str | None = None,
    n_variants: int = 3,
    openai_api_key: str | None = None,
    bill_workspace_id: str | None = None,
    bill_amount: int = 0,
    bill_idem: str | None = None,
) -> dict:
    """Generate N ad creative variants for a product → ``ad_concepts`` rows.

    Each variant: hook, headline, ugc/demo/influencer scripts, per-platform
    captions, CTA, on-screen text, a render-ready storyboard, and a compliance
    review. Auto-analyzes the product first if it has no analysis yet.

    A campaign (if given) supplies goal/platforms and links the concepts.
    Refunds credits on failure.
    """
    from app.analyze import analyze_product as _analyze_product
    from app.ad_concepts import generate_creative_pack, run_compliance

    try:
      with SessionLocal() as db:
        product = db.get(Product, product_id)
        if not product:
            raise ValueError(f"Unknown product_id: {product_id}")

        # Campaign drives goal/platforms when present; task args are the fallback.
        campaign = db.get(Campaign, campaign_id) if campaign_id else None
        eff_goal = (campaign.goal if campaign else None) or goal or "awareness"
        eff_platforms = (campaign.platforms if campaign else None) or platforms or ["tiktok"]
        eff_language = language or "en"
        # Brand context (voice + prohibited words + category).
        brand_context = None
        prohibited_words: list = []
        if product.brand_id:
            brand = db.get(Brand, product.brand_id)
            if brand:
                brand_context = {"name": brand.name, "tone": brand.tone_json}
                prohibited_words = brand.prohibited_words or []
                eff_language = language or brand.default_language or eff_language

        # Ensure analysis exists — generate inline if the product was never analyzed.
        analysis = product.analysis_json
        if not analysis:
            self.update_state(state="PROGRESS", meta={"stage": "analyzing", "msg": "Analyzing product…"})
            pre_collector = UsageCollector(workspace_id=product.workspace_id, product_id=product.id)
            analysis = _analyze_product(
                {
                    "title": product.title, "description": product.description,
                    "price": product.price, "currency": product.currency,
                    "category": product.category, "attributes": product.attributes_json or {},
                },
                brand_context,
                api_key=openai_api_key,
                collector=pre_collector,
            )
            product.analysis_json = analysis
            product.status = "analyzed"
            db.commit()
            pre_collector.flush(db)

        angles = analysis.get("ad_angles") or []
        product_data = {
            "title": product.title, "description": product.description,
            "price": product.price, "currency": product.currency,
            "category": product.category,
        }

        collector = UsageCollector(workspace_id=product.workspace_id, product_id=product.id)
        concept_ids: list[str] = []

        for i in range(max(1, n_variants)):
            angle = _angle_text(angles[i % len(angles)]) if angles else None

            # Create the row first so usage events can carry its id.
            concept = AdConcept(
                workspace_id=product.workspace_id,
                campaign_id=campaign_id,
                product_id=product.id,
                variant_index=i,
                angle=angle,
                status="generating",
            )
            db.add(concept)
            db.flush()
            collector.ad_concept_id = concept.id

            self.update_state(
                state="PROGRESS",
                meta={"stage": "generating", "msg": f"Generating ad variant {i + 1}/{n_variants}…"},
            )

            pack = generate_creative_pack(
                product_data, analysis, brand_context,
                goal=eff_goal, platforms=eff_platforms, language=eff_language,
                angle=angle, api_key=openai_api_key, collector=collector,
            )
            compliance = run_compliance(
                pack, category=product.category, prohibited_words=prohibited_words,
                api_key=openai_api_key, collector=collector,
            )

            scenes = pack.get("storyboard") or []
            concept.angle = pack.get("angle") or angle
            concept.hook = pack.get("hook")
            concept.headline = pack.get("headline")
            concept.script_json = pack.get("scripts") or {}
            concept.captions_json = pack.get("captions") or {}
            concept.cta = pack.get("cta")
            concept.on_screen_text = pack.get("on_screen_text")
            concept.storyboard_json = {
                "scenes": scenes,
                "total_duration_estimate": round(
                    sum(s.get("duration_estimate", 0.0) for s in scenes), 1
                ),
            }
            concept.compliance_json = compliance
            # High-risk concepts are held for review rather than marked ready.
            concept.status = "flagged" if compliance.get("risk") == "high" else "ready"
            concept_ids.append(concept.id)

        db.commit()
        collector.flush(db)

        return {
            "product_id": product_id,
            "campaign_id": campaign_id,
            "concept_ids": concept_ids,
            "count": len(concept_ids),
        }
    except Exception:
        _refund_credits(bill_workspace_id, bill_amount, bill_idem)
        raise


# ── Catalog-to-ad: single-concept regeneration ──────────────────────────────────

@celery_app.task(name="regenerate_ad_concept", bind=True)
def regenerate_ad_concept(
    self,
    concept_id: str,
    angle: str | None = None,
    openai_api_key: str | None = None,
    bill_workspace_id: str | None = None,
    bill_amount: int = 0,
    bill_idem: str | None = None,
) -> dict:
    """Regenerate one existing ad concept in place (fresh creative + compliance).

    Reuses the product's existing analysis. ``angle`` overrides the variant's
    angle; otherwise the concept's current angle is reused. The row id is kept
    so the frontend can update it without reordering. Refunds credits on failure.
    """
    from app.ad_concepts import generate_creative_pack, run_compliance

    try:
      with SessionLocal() as db:
        concept = db.get(AdConcept, concept_id)
        if not concept or concept.deleted_at is not None:
            raise ValueError(f"Unknown concept_id: {concept_id}")

        product = db.get(Product, concept.product_id)
        if not product:
            raise ValueError("Concept's product no longer exists")

        # Brand context + campaign goal/platforms.
        brand_context = None
        prohibited_words: list = []
        eff_language = "en"
        if product.brand_id:
            brand = db.get(Brand, product.brand_id)
            if brand:
                brand_context = {"name": brand.name, "tone": brand.tone_json}
                prohibited_words = brand.prohibited_words or []
                eff_language = brand.default_language or eff_language

        eff_goal = "awareness"
        eff_platforms = ["tiktok"]
        if concept.campaign_id:
            campaign = db.get(Campaign, concept.campaign_id)
            if campaign:
                eff_goal = campaign.goal or eff_goal
                eff_platforms = campaign.platforms or eff_platforms

        analysis = product.analysis_json or {}
        eff_angle = angle or concept.angle

        self.update_state(state="PROGRESS", meta={"stage": "generating", "msg": "Regenerating concept…"})

        collector = UsageCollector(
            workspace_id=concept.workspace_id,
            product_id=product.id,
            ad_concept_id=concept.id,
        )
        pack = generate_creative_pack(
            {
                "title": product.title, "description": product.description,
                "price": product.price, "currency": product.currency,
                "category": product.category,
            },
            analysis, brand_context,
            goal=eff_goal, platforms=eff_platforms, language=eff_language,
            angle=eff_angle, api_key=openai_api_key, collector=collector,
        )
        compliance = run_compliance(
            pack, category=product.category, prohibited_words=prohibited_words,
            api_key=openai_api_key, collector=collector,
        )

        scenes = pack.get("storyboard") or []
        concept.angle = pack.get("angle") or eff_angle
        concept.hook = pack.get("hook")
        concept.headline = pack.get("headline")
        concept.script_json = pack.get("scripts") or {}
        concept.captions_json = pack.get("captions") or {}
        concept.cta = pack.get("cta")
        concept.on_screen_text = pack.get("on_screen_text")
        concept.storyboard_json = {
            "scenes": scenes,
            "total_duration_estimate": round(
                sum(s.get("duration_estimate", 0.0) for s in scenes), 1
            ),
        }
        concept.compliance_json = compliance
        concept.status = "flagged" if compliance.get("risk") == "high" else "ready"
        db.commit()
        collector.flush(db)

        return {
            "concept_id": concept.id,
            "product_id": product.id,
            "status": concept.status,
            "risk": compliance.get("risk"),
        }
    except Exception:
        _refund_credits(bill_workspace_id, bill_amount, bill_idem)
        raise


# ── Catalog-to-ad: video render off an ad concept storyboard ─────────────────────

@celery_app.task(name="generate_ad_concept_video", bind=True, time_limit=900, soft_time_limit=840)
def generate_ad_concept_video(
    self,
    concept_id: str,
    platform: str = "tiktok",
    burn_subtitles: bool = True,
    voice_id: str | None = None,
    openai_api_key: str | None = None,
    elevenlabs_api_key: str | None = None,
    bill_workspace_id: str | None = None,
    bill_amount: int = 0,
    bill_idem: str | None = None,
) -> dict:
    """Render an MP4 from an ad concept's storyboard.

    Reuses the article video pipeline (DALL-E scene images → ElevenLabs voiceover
    → FFmpeg assembly). Voiceover text is the storyboard narration (falls back to
    the UGC script). Assets are tagged with workspace/product/concept ids and have
    no article_id. Re-runs reuse already-generated scene images.
    """
    from app.image_gen import generate_and_save
    from app.video import assemble_video
    from app.tts import synthesize
    from app.captions import storyboard_to_captions, captions_to_srt
    from app.schemas import Storyboard
    from app.platforms import get_profile as _get_profile

    image_dir = os.getenv("IMAGE_DIR", "/data/images")
    video_dir = os.getenv("VIDEO_DIR", "/data/video")
    audio_dir = os.getenv("AUDIO_DIR", "/data/audio")
    for d in (image_dir, video_dir, audio_dir):
        os.makedirs(d, exist_ok=True)

    plat_profile = _get_profile(platform)

    with SessionLocal() as db:
        concept = db.get(AdConcept, concept_id)
        if not concept or concept.deleted_at is not None:
            raise ValueError(f"Unknown concept_id: {concept_id}")
        if not concept.storyboard_json:
            raise RuntimeError("Concept has no storyboard — regenerate the concept first")

        try:
            storyboard = Storyboard(**concept.storyboard_json)
        except Exception as exc:
            raise RuntimeError(f"Storyboard format incompatible — regenerate the concept: {exc}") from exc
        if not storyboard.scenes:
            raise RuntimeError("Storyboard has no scenes")

        # Voiceover text: prefer narration, fall back to the UGC script.
        narration = " ".join(s.narration for s in storyboard.scenes if s.narration).strip()
        if not narration:
            narration = (concept.script_json or {}).get("ugc", "").strip()
        if not narration:
            raise RuntimeError("No narration/script text to voice")

        resolved_voice = voice_id or os.getenv("ELEVENLABS_VOICE_ID")
        model_id = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")

        video_record = VideoAsset(
            article_id=None,
            workspace_id=concept.workspace_id,
            product_id=concept.product_id,
            ad_concept_id=concept.id,
            file_path="",
            status="created",
            platform=platform,
            width=plat_profile.width,
            height=plat_profile.height,
        )
        db.add(video_record)
        db.commit()

        collector = UsageCollector(
            workspace_id=concept.workspace_id,
            product_id=concept.product_id,
            ad_concept_id=concept.id,
        )
        total_scenes = len(storyboard.scenes)
        srt_path: str | None = None

        try:
            # ── Voiceover ─────────────────────────────────────────────────────
            self.update_state(state="PROGRESS", meta={"stage": "voicing", "msg": "Synthesizing voiceover…"})
            audio_record = AudioAsset(
                article_id=None,
                workspace_id=concept.workspace_id,
                product_id=concept.product_id,
                ad_concept_id=concept.id,
                voice_id=resolved_voice or "default",
                model_id=model_id,
                output_format=os.getenv("ELEVENLABS_OUTPUT_FORMAT", "mp3_44100_128"),
                file_path="",
                platform=platform,
                status="created",
            )
            db.add(audio_record)
            db.commit()

            audio_bytes = synthesize(
                narration,
                voice_id=resolved_voice,
                api_key=elevenlabs_api_key,
                model_id=model_id,
                language_code=(concept_language(db, concept) or "en")[:2],
                collector=collector,
            )
            audio_path = os.path.join(audio_dir, f"{concept_id}_{audio_record.id}.mp3")
            with open(audio_path, "wb") as fh:
                fh.write(audio_bytes)
            audio_record.file_path = audio_path
            audio_record.status = "ready"
            video_record.audio_asset_id = audio_record.id
            db.commit()

            # ── Scene images ──────────────────────────────────────────────────
            scene_inputs: list[dict] = []
            for i, scene in enumerate(storyboard.scenes):
                self.update_state(
                    state="PROGRESS",
                    meta={"stage": "imaging", "msg": f"Generating scene images… {i}/{total_scenes}",
                          "progress": i / max(total_scenes, 1)},
                )
                img_path = os.path.join(image_dir, f"{concept_id}_scene_{scene.scene_number}.png")

                existing = db.execute(
                    select(ImageAsset).where(
                        ImageAsset.ad_concept_id == concept_id,
                        ImageAsset.scene_number == scene.scene_number,
                        ImageAsset.status == "ready",
                    )
                ).scalar_one_or_none()

                if existing and os.path.exists(existing.file_path):
                    img_path = existing.file_path
                else:
                    success = generate_and_save(
                        scene.visual_prompt, img_path, api_key=openai_api_key,
                        collector=collector, scene_number=scene.scene_number,
                    )
                    db.add(ImageAsset(
                        article_id=None,
                        workspace_id=concept.workspace_id,
                        product_id=concept.product_id,
                        ad_concept_id=concept.id,
                        scene_number=scene.scene_number,
                        visual_prompt=scene.visual_prompt,
                        file_path=img_path,
                        status="ready" if success else "failed",
                        error=None if success else "generation failed, placeholder used",
                    ))
                    db.commit()

                if not os.path.exists(img_path):
                    raise RuntimeError(f"Scene {scene.scene_number} image missing from disk ({img_path})")
                scene_inputs.append({"image_path": img_path, "duration": max(scene.duration_estimate, 1.0)})

            # ── Captions ──────────────────────────────────────────────────────
            if burn_subtitles:
                try:
                    audio_seconds = float(_mp3_duration_seconds(audio_path))
                except Exception:
                    audio_seconds = None
                captions = storyboard_to_captions(storyboard, actual_audio_duration=audio_seconds)
                srt_path = os.path.join(video_dir, f"{concept_id}.srt")
                with open(srt_path, "w", encoding="utf-8") as fh:
                    fh.write(captions_to_srt(captions))

            # ── Assembly ──────────────────────────────────────────────────────
            self.update_state(state="PROGRESS", meta={"stage": "rendering", "msg": "Assembling video…"})
            output_path = os.path.join(video_dir, f"{concept_id}_{video_record.id}.mp4")
            actual_duration = assemble_video(
                scenes=scene_inputs,
                audio_path=audio_path,
                output_path=output_path,
                srt_path=srt_path,
                width=plat_profile.width,
                height=plat_profile.height,
            )

            video_record.file_path = output_path
            video_record.duration_seconds = actual_duration
            video_record.has_subtitles = 1 if (burn_subtitles and srt_path is not None) else 0
            video_record.status = "ready"
            db.commit()
            collector.flush(db, video_asset_id=video_record.id)

            return {
                "concept_id": concept_id,
                "video_id": video_record.id,
                "duration_seconds": actual_duration,
                "scene_count": total_scenes,
                "has_subtitles": video_record.has_subtitles,
            }

        except Exception as exc:
            video_record.status = "failed"
            video_record.error = str(exc)[:500]
            db.commit()
            collector.flush(db, video_asset_id=video_record.id)
            _refund_credits(bill_workspace_id, bill_amount, bill_idem)
            raise
        finally:
            if srt_path and os.path.exists(srt_path):
                try:
                    os.remove(srt_path)
                except OSError:
                    pass


def concept_language(db, concept) -> str | None:
    """Resolve the output language for a concept from its product's brand."""
    if not concept.product_id:
        return None
    product = db.get(Product, concept.product_id)
    if product and product.brand_id:
        brand = db.get(Brand, product.brand_id)
        if brand:
            return brand.default_language
    return None


# ── Animated video task ────────────────────────────────────────────────────────

@celery_app.task(
    name="generate_animated_video_for_article",
    bind=True,
    time_limit=3600,
    soft_time_limit=3540,
)
def generate_animated_video_for_article(
    self,
    article_id: str,
    audio_asset_id: str | None = None,
    burn_subtitles: bool = True,
    openai_api_key: str | None = None,
    scene_video_provider: str | None = None,
    platform: str = "tiktok",
    animation_prompt: str | None = None,
) -> dict:
    """Generate AI-animated scene clips then assemble a final MP4.

    Flow
    ────
    1. Generate (or reuse) DALL-E images for each scene.
    2. Submit each scene image to the configured image-to-video provider.
    3. Poll until all scene jobs finish (or per-scene timeout).
    4. Download and normalise each clip to its storyboard duration.
    5. Fall back to static FFmpeg clip for any scene that failed.
    6. Assemble: concat clips + attach audio + burn subtitles.

    Falls back gracefully: any single scene failure only affects that scene.
    """
    import time as _time

    from app.image_gen import generate_and_save
    from app.scene_video import get_scene_video_provider, JobState, StaticImageProvider
    from app.video import normalize_clip, assemble_video_from_clips
    from app.captions import storyboard_to_captions, captions_to_srt
    from app.schemas import Storyboard
    from app.platforms import get_profile as _get_profile

    image_dir = os.getenv("IMAGE_DIR", "/data/images")
    video_dir = os.getenv("VIDEO_DIR", "/data/video")
    os.makedirs(image_dir, exist_ok=True)
    os.makedirs(video_dir, exist_ok=True)

    plat_profile = _get_profile(platform)

    # Per-scene timeout for the provider (seconds)
    scene_timeout    = float(os.getenv("SCENE_VIDEO_TIMEOUT_SECONDS", "300"))
    # How long to sleep between poll sweeps
    poll_interval    = float(os.getenv("SCENE_VIDEO_POLL_INTERVAL_SECONDS", "6"))

    provider = get_scene_video_provider(scene_video_provider)
    logger.info("Animated video task starting: article=%s provider=%s", article_id, provider.name)

    with SessionLocal() as db:
        article = db.get(Article, article_id)
        if not article:
            raise ValueError(f"Unknown article_id: {article_id}")
        if not article.storyboard_json:
            raise RuntimeError("Article has no storyboard — run audio generation first")

        try:
            storyboard = Storyboard(**article.storyboard_json)
        except Exception as exc:
            raise RuntimeError(f"Storyboard incompatible — regenerate the article: {exc}") from exc

        if audio_asset_id:
            audio = db.get(AudioAsset, audio_asset_id)
            if not audio:
                raise ValueError(f"Unknown audio_asset_id: {audio_asset_id}")
        else:
            audio = db.execute(
                select(AudioAsset)
                .where(
                    AudioAsset.article_id == article_id,
                    AudioAsset.status == "ready",
                    AudioAsset.platform == platform,
                )
                .order_by(AudioAsset.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            if not audio:
                audio = db.execute(
                    select(AudioAsset)
                    .where(AudioAsset.article_id == article_id, AudioAsset.status == "ready")
                    .order_by(AudioAsset.created_at.desc())
                    .limit(1)
                ).scalar_one_or_none()
        if not audio:
            raise RuntimeError("No ready audio asset — run audio generation first")
        if not os.path.exists(audio.file_path):
            raise RuntimeError(f"Audio file missing: {audio.file_path}")

        video_record = VideoAsset(
            article_id=article_id,
            audio_asset_id=audio.id,
            file_path="",
            status="created",
            render_mode="animated",
            platform=platform,
            width=plat_profile.width,
            height=plat_profile.height,
        )
        db.add(video_record)
        db.commit()

        anim_collector = UsageCollector(article_id=article_id, user_id=getattr(article, "user_id", None))
        total_scenes = len(storyboard.scenes)
        srt_path: str | None = None

        try:
            # ── Step 1: ensure scene images exist ────────────────────────────
            image_map: dict[int, str] = {}  # scene_number → image file path
            for i, scene in enumerate(storyboard.scenes):
                self.update_state(
                    state="PROGRESS",
                    meta={
                        "stage": "imaging",
                        "msg": f"Preparing scene images… {i + 1}/{total_scenes}",
                        "progress": (i / max(total_scenes, 1)) * 0.2,
                    },
                )
                img_path = os.path.join(image_dir, f"{article_id}_scene_{scene.scene_number}.png")
                existing = db.execute(
                    select(ImageAsset).where(
                        ImageAsset.article_id == article_id,
                        ImageAsset.scene_number == scene.scene_number,
                        ImageAsset.status == "ready",
                    )
                ).scalar_one_or_none()

                if existing and os.path.exists(existing.file_path):
                    img_path = existing.file_path
                    logger.info("Reusing image for scene %d", scene.scene_number)
                else:
                    success = generate_and_save(
                        scene.visual_prompt, img_path, api_key=openai_api_key,
                        collector=anim_collector, scene_number=scene.scene_number,
                    )
                    img_rec = ImageAsset(
                        article_id=article_id,
                        scene_number=scene.scene_number,
                        visual_prompt=scene.visual_prompt,
                        file_path=img_path,
                        status="ready" if success else "failed",
                        error=None if success else "generation failed, placeholder used",
                    )
                    db.add(img_rec)
                    db.commit()
                image_map[scene.scene_number] = img_path

            # ── Step 2: submit all scenes to the provider ─────────────────────
            self.update_state(
                state="PROGRESS",
                meta={
                    "stage": "submitting",
                    "msg": f"Submitting {total_scenes} scenes to {provider.name}…",
                    "progress": 0.2,
                },
            )

            # Clean up stale scene video records for this article/provider
            db.execute(
                __import__("sqlalchemy").delete(SceneVideoAsset).where(
                    SceneVideoAsset.article_id == article_id,
                )
            )
            db.commit()

            scene_records: dict[int, SceneVideoAsset] = {}
            for scene in storyboard.scenes:
                img_path   = image_map[scene.scene_number]
                clip_raw   = os.path.join(video_dir, f"{article_id}_scene_{scene.scene_number}_raw.mp4")
                duration_t = max(scene.duration_estimate, 1.0)

                sv = SceneVideoAsset(
                    article_id=article_id,
                    scene_number=scene.scene_number,
                    provider=provider.name,
                    status="pending",
                )
                db.add(sv)
                db.commit()
                scene_records[scene.scene_number] = sv

                eff_prompt = (
                    f"{animation_prompt}. {scene.visual_prompt}"
                    if animation_prompt else scene.visual_prompt
                )
                try:
                    job_id = provider.submit(
                        image_path=img_path,
                        prompt=eff_prompt,
                        duration_hint=duration_t,
                    )
                    sv.provider_job_id = job_id
                    sv.status = "processing"
                    db.commit()
                    logger.info(
                        "Submitted scene %d → provider job %s", scene.scene_number, job_id
                    )
                except Exception as exc:
                    logger.error("Submit failed for scene %d: %s", scene.scene_number, exc)
                    sv.status = "failed"
                    sv.error  = str(exc)[:500]
                    db.commit()

            # ── Step 3: poll until all scenes are done ─────────────────────────
            pending = {
                sn: rec for sn, rec in scene_records.items()
                if rec.status == "processing" and rec.provider_job_id
            }
            deadlines = {sn: _time.monotonic() + scene_timeout for sn in pending}

            while pending:
                self.update_state(
                    state="PROGRESS",
                    meta={
                        "stage": "animating",
                        "msg": f"Animating scenes… {total_scenes - len(pending)}/{total_scenes} done",
                        "progress": 0.2 + 0.6 * (1.0 - len(pending) / max(total_scenes, 1)),
                        "scene_statuses": {
                            sn: scene_records[sn].status
                            for sn in range(1, total_scenes + 1)
                            if sn in scene_records
                        },
                    },
                )
                still_pending: dict[int, SceneVideoAsset] = {}
                for sn, rec in list(pending.items()):
                    if _time.monotonic() > deadlines[sn]:
                        logger.warning("Scene %d timed out, falling back to static", sn)
                        rec.status = "failed"
                        rec.error  = f"Provider timeout after {scene_timeout:.0f}s"
                        db.commit()
                        continue
                    try:
                        job = provider.poll(rec.provider_job_id)
                        if job.state == JobState.READY:
                            # Download raw clip
                            clip_raw = os.path.join(
                                video_dir, f"{article_id}_scene_{sn}_raw.mp4"
                            )
                            provider.download(job, clip_raw)
                            rec.status = "ready"
                            rec.file_path = clip_raw
                            db.commit()
                            logger.info("Scene %d clip ready: %s", sn, clip_raw)
                        elif job.state == JobState.FAILED:
                            rec.status = "failed"
                            rec.error  = job.error or "Provider reported failure"
                            db.commit()
                        else:
                            still_pending[sn] = rec
                    except Exception as exc:
                        logger.error("Poll error for scene %d: %s", sn, exc)
                        still_pending[sn] = rec

                pending = still_pending
                if pending:
                    _time.sleep(poll_interval)

            # ── Step 4: normalise clips (trim/pad) and apply static fallback ──
            self.update_state(
                state="PROGRESS",
                meta={"stage": "normalizing", "msg": "Normalising scene clips…", "progress": 0.82},
            )
            final_clips: list[dict] = []
            static_fallback = StaticImageProvider()

            for scene in storyboard.scenes:
                sn = scene.scene_number
                rec = scene_records.get(sn)
                dur = max(scene.duration_estimate, 1.0)
                clip_norm = os.path.join(video_dir, f"{article_id}_scene_{sn}_norm.mp4")

                if rec and rec.status == "ready" and rec.file_path and os.path.exists(rec.file_path):
                    try:
                        normalize_clip(rec.file_path, clip_norm, dur, width=plat_profile.width, height=plat_profile.height)
                        rec.duration_seconds = dur
                        db.commit()
                        final_clips.append({"clip_path": clip_norm, "duration": dur})
                        continue
                    except Exception as exc:
                        logger.warning("Normalize failed for scene %d: %s — using static fallback", sn, exc)

                # Static fallback for this scene
                img_path = image_map[sn]
                try:
                    static_fallback.generate_sync(
                        image_path=img_path,
                        prompt=scene.visual_prompt,
                        output_path=clip_norm,
                        duration_hint=dur,
                    )
                    if rec:
                        rec.status    = "fallback"
                        rec.file_path = clip_norm
                        rec.duration_seconds = dur
                        db.commit()
                    final_clips.append({"clip_path": clip_norm, "duration": dur})
                except Exception as exc:
                    logger.error("Static fallback also failed for scene %d: %s", sn, exc)
                    raise RuntimeError(f"Cannot produce clip for scene {sn}: {exc}") from exc

            # ── Step 5: SRT captions ───────────────────────────────────────────
            if burn_subtitles:
                from app.captions import storyboard_to_captions, captions_to_srt
                audio_seconds: float | None = None
                try:
                    audio_seconds = float(_mp3_duration_seconds(audio.file_path))
                except Exception:
                    audio_seconds = None
                captions    = storyboard_to_captions(storyboard, actual_audio_duration=audio_seconds)
                srt_content = captions_to_srt(captions)
                srt_path    = os.path.join(video_dir, f"{article_id}.srt")
                with open(srt_path, "w", encoding="utf-8") as fh:
                    fh.write(srt_content)

            # ── Step 6: assemble final video ──────────────────────────────────
            self.update_state(
                state="PROGRESS",
                meta={"stage": "rendering", "msg": "Assembling final animated video…", "progress": 0.9},
            )
            output_path   = os.path.join(video_dir, f"{article_id}_{video_record.id}.mp4")
            actual_dur    = assemble_video_from_clips(
                scene_clips=final_clips,
                audio_path=audio.file_path,
                output_path=output_path,
                srt_path=srt_path,
                width=plat_profile.width,
                height=plat_profile.height,
            )

            fallback_count = sum(
                1 for r in scene_records.values() if r.status in ("fallback", "failed")
            )

            video_record.file_path        = output_path
            video_record.duration_seconds = actual_dur
            video_record.has_subtitles    = 1 if burn_subtitles and srt_path else 0
            video_record.status           = "ready"
            db.commit()

            # Update scene video asset durations for fully animated clips
            for rec in scene_records.values():
                if rec.status == "ready":
                    db.refresh(rec)

            anim_collector.flush(db, video_asset_id=video_record.id)
            logger.info(
                "Animated video ready: %s (%.1fs, %d/%d animated, %d fallback)",
                output_path, actual_dur,
                total_scenes - fallback_count, total_scenes,
                fallback_count,
            )

            return {
                "article_id":      article_id,
                "video_id":        video_record.id,
                "video_path":      output_path,
                "duration_seconds": actual_dur,
                "scene_count":     total_scenes,
                "animated_scenes": total_scenes - fallback_count,
                "fallback_scenes": fallback_count,
                "has_subtitles":   bool(video_record.has_subtitles),
                "render_mode":     "animated",
            }

        except Exception as exc:
            video_record.status = "failed"
            video_record.error  = str(exc)[:500]
            db.commit()
            anim_collector.flush(db, video_asset_id=video_record.id)
            raise
        finally:
            if srt_path and os.path.exists(srt_path):
                try:
                    os.remove(srt_path)
                except OSError:
                    pass


@celery_app.task(name="generate_article_thumbnail", bind=True)
def generate_article_thumbnail(
    self,
    article_id: str,
    prompt: str | None = None,
    openai_api_key: str | None = None,
    user_id: str | None = None,
) -> dict:
    """Generate a cover/thumbnail image for an already-prepared article.

    When ``prompt`` is provided, it overrides the default template-based prompt
    so the user can describe the cover they want.
    """
    from app.image_gen import generate_thumbnail as _gen_thumb
    with SessionLocal() as db:
        article = db.get(Article, article_id)
        if not article:
            raise ValueError(f"Unknown article_id: {article_id}")

        collector = UsageCollector(article_id=article.id, user_id=user_id)
        self.update_state(state="PROGRESS", meta={"stage": "thumbnail", "msg": "Generating thumbnail…"})

        scene_prompt = None
        if not prompt and article.storyboard_json:
            scenes = (article.storyboard_json or {}).get("scenes") or []
            if scenes:
                scene_prompt = scenes[0].get("visual_prompt")

        thumb_bytes = _gen_thumb(
            article.title or "Untitled",
            scene_prompt=scene_prompt,
            custom_prompt=prompt,
            api_key=openai_api_key,
            collector=collector,
        )

        image_dir = os.getenv("IMAGE_DIR", "/data/images")
        os.makedirs(image_dir, exist_ok=True)
        thumb_path = os.path.join(image_dir, f"{article.id}_thumbnail.png")
        with open(thumb_path, "wb") as fh:
            fh.write(thumb_bytes)
        article.thumbnail_path = thumb_path
        db.commit()
        collector.flush(db)

        return {"article_id": article.id, "thumbnail_url": f"/thumbnail/{article.id}"}
