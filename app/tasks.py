import os
import tempfile
import feedparser
import logging

from datetime import datetime, timedelta
from celery import Celery
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from mutagen.mp3 import MP3

from app.db import SessionLocal, engine
from app.models import Base, Source, Article, AudioAsset, VoiceCalibration, ImageAsset, VideoAsset
from app.extract import extract_article_text
from app.summarize import make_tts_bundle, rewrite_to_target_words
from app.tts import synthesize

logger = logging.getLogger(__name__)

celery_app = Celery(
    "mvp",
    broker=os.environ["CELERY_BROKER_URL"],
    backend=os.environ["CELERY_RESULT_BACKEND"],
)
celery_app.conf.result_expires = 86400  # purge task results after 24 h

# Scheduled generation — disabled unless ENABLE_SCHEDULED_GENERATION=true
if os.getenv("ENABLE_SCHEDULED_GENERATION", "false").lower() == "true":
    from celery.schedules import crontab
    celery_app.conf.beat_schedule = {
        "daily-content-generation": {
            "task": "generate_all_sources_daily",
            "schedule": crontab(
                hour=int(os.getenv("BEAT_GENERATION_HOUR", "6")),
                minute=0,
            ),
        }
    }

Base.metadata.create_all(bind=engine)

TARGET_SECONDS = int(os.getenv("TTS_TARGET_SECONDS", "180"))
TOLERANCE_SECONDS = int(os.getenv("TTS_TOLERANCE_SECONDS", "30"))
WAY_OFF_SECONDS = int(os.getenv("TTS_WAY_OFF_SECONDS", "15"))
CAL_ALPHA = float(os.getenv("TTS_CAL_ALPHA", "0.3"))
MAX_TTS_ATTEMPTS = int(os.getenv("TTS_MAX_ATTEMPTS", "2"))
MIN_SECONDS = int(os.getenv("TTS_DURATION_MIN_SECONDS", "150"))
MAX_SECONDS = int(os.getenv("TTS_DURATION_MAX_SECONDS", "210"))


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


@celery_app.task(name="generate_all_sources_daily")
def generate_all_sources_daily() -> dict:
    """Dispatch audio generation for every source. Triggered by Celery Beat."""
    target_seconds = int(os.getenv("TTS_TARGET_SECONDS", "180"))
    n_scenes = int(os.getenv("STORYBOARD_SCENES", "8"))

    with SessionLocal() as db:
        sources = db.execute(select(Source)).scalars().all()

    dispatched = 0
    for src in sources:
        try:
            celery_app.send_task(
                "generate_latest_for_source",
                kwargs={"source_id": src.id, "target_seconds": target_seconds, "n_scenes": n_scenes},
            )
            dispatched += 1
        except Exception as exc:
            logger.error("Failed to queue source %s: %s", src.id, exc)

    logger.info("Beat: dispatched %d generation tasks", dispatched)
    return {"dispatched": dispatched, "total_sources": len(sources)}


@celery_app.task(name="generate_latest_for_source", bind=True)
def generate_latest_for_source(
    self,
    source_id: str,
    voice_id: str | None = None,
    target_seconds: int = TARGET_SECONDS,
    n_scenes: int = 8,
) -> dict:
    audio_dir = os.getenv("AUDIO_DIR", "/data/audio")
    os.makedirs(audio_dir, exist_ok=True)

    with SessionLocal() as db:
        src = db.get(Source, source_id)
        if not src:
            raise ValueError(f"Unknown source_id: {source_id}")

        self.update_state(state="PROGRESS", meta={"stage": "fetching", "msg": "Fetching latest RSS entry…"})

        feed = feedparser.parse(src.rss_url)
        if not feed.entries:
            raise RuntimeError("No RSS entries found")

        lookback_days = int(os.getenv("RSS_LOOKBACK_DAYS", "7"))
        cutoff = datetime.utcnow() - timedelta(days=lookback_days)

        candidates = [e for e in feed.entries if _parse_dt(e) and _parse_dt(e) >= cutoff]
        entry = candidates[0] if candidates else feed.entries[0]
        title = (entry.get("title") or "").strip() or "Untitled"
        url = (entry.get("link") or "").strip()
        if not url:
            raise RuntimeError("RSS entry has no link/url")
        fallback = (entry.get("summary") or entry.get("description") or "").strip()
        published_at = _parse_dt(entry)

        logger.info("Selected RSS entry: title=%r published_at=%s url=%s", title, published_at, url)

        # Upsert article
        article = Article(source_id=src.id, title=title, url=url, published_at=published_at)
        db.add(article)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            article = db.execute(
                select(Article).where(Article.source_id == src.id, Article.url == url)
            ).scalar_one()

        self.update_state(state="PROGRESS", meta={"stage": "extracting", "msg": "Extracting article content…"})

        raw = extract_article_text(url, fallback_text=fallback)
        if not raw:
            raw = fallback or title

        used_voice_id = voice_id or os.getenv("ELEVENLABS_VOICE_ID")
        if not used_voice_id:
            raise RuntimeError("Missing ELEVENLABS_VOICE_ID")
        model_id = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
        output_format = os.getenv("ELEVENLABS_OUTPUT_FORMAT", "mp3_44100_128")
        speed = _speed_key(float(os.getenv("ELEVENLABS_SPEED", "1.0")))

        cal = db.get(VoiceCalibration, (used_voice_id, model_id, speed))
        wpm = cal.wpm_estimate if cal else 140.0

        target_words = _words_for_seconds(target_seconds, wpm)
        tol_words = _words_for_seconds(TOLERANCE_SECONDS, wpm)

        self.update_state(state="PROGRESS", meta={"stage": "scripting", "msg": "Generating script & storyboard…"})

        bundle = make_tts_bundle(
            title=title,
            body=raw,
            language_hint=src.language_hint,
            target_seconds=target_seconds,
            n_scenes=n_scenes,
            target_words=target_words,
            tol_words=tol_words,
            wpm_estimate=wpm,
        )

        script = bundle["script"]
        scenes = bundle.get("scenes") or []
        word_count = bundle.get("word_count")

        logger.info("Final script words=%s preview=%r", word_count, script[:400])

        article.raw_text = raw
        article.tts_script = script
        article.script_language = os.getenv("TTS_OUTPUT_LANGUAGE", "es-MX")
        article.summary_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        article.storyboard_json = {
            "scenes": scenes,
            "total_duration_estimate": bundle.get("total_duration_estimate", 0),
        }
        db.commit()

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
            try:
                audio_bytes = synthesize(
                    script, voice_id=used_voice_id, model_id=model_id, output_format=output_format
                )
                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3", dir=audio_dir) as tmp:
                    tmp.write(audio_bytes)
                    tmp_path = tmp.name

                duration = _mp3_duration_seconds(tmp_path)

                if accept_min <= duration <= accept_max:
                    os.replace(tmp_path, final_path)
                    break

                if attempt >= MAX_TTS_ATTEMPTS:
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass
                    break

                wc = word_count or len(script.split())
                desired = MIN_SECONDS if duration < MIN_SECONDS else MAX_SECONDS
                target_wc = int(round(wc * (desired / max(duration, 1))))
                script = rewrite_to_target_words(script, target_words=target_wc, tol_words=20)
                word_count = len(script.split())

                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

            except Exception as e:
                last_error = str(e)
                duration = None

        if duration is None or not (accept_min <= duration <= accept_max):
            raise RuntimeError(f"TTS out of range after retries. duration={duration}, error={last_error}")

        article.tts_script = script
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
) -> dict:
    """Generate scene images with DALL-E then assemble an MP4 with FFmpeg.

    Reuses already-generated ImageAssets so re-runs don't re-bill for images.
    Falls back to black placeholder frames when image generation fails.
    """
    from app.image_gen import generate_and_save
    from app.video import assemble_video
    from app.captions import storyboard_to_captions, captions_to_srt
    from app.schemas import Storyboard

    image_dir = os.getenv("IMAGE_DIR", "/data/images")
    video_dir = os.getenv("VIDEO_DIR", "/data/video")
    os.makedirs(image_dir, exist_ok=True)
    os.makedirs(video_dir, exist_ok=True)

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
        )
        db.add(video_record)
        db.commit()

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
                    success = generate_and_save(scene.visual_prompt, img_path)
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

                scene_inputs.append({"image_path": img_path, "duration": max(scene.duration_estimate, 1.0)})

            # ── SRT captions ─────────────────────────────────────────────────
            if burn_subtitles:
                captions = storyboard_to_captions(storyboard)
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
            )

            video_record.file_path = output_path
            video_record.duration_seconds = actual_duration
            video_record.has_subtitles = burn_subtitles and srt_path is not None
            video_record.status = "ready"
            db.commit()

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
            # Clean up orphaned SRT
            if srt_path and os.path.exists(srt_path):
                try:
                    os.remove(srt_path)
                except OSError:
                    pass
            raise
