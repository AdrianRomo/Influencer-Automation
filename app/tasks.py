import os
import tempfile
import feedparser
import logging

from datetime import datetime, timedelta
from celery import Celery
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from mutagen.mp3 import MP3  # pip install mutagen

from app.db import SessionLocal, engine
from app.models import Base, Source, Article, AudioAsset, VoiceCalibration
from app.extract import extract_article_text
from app.summarize import make_tts_bundle, rewrite_to_target_words  # add helper in summarize.py
from app.tts import synthesize

logger = logging.getLogger(__name__)

celery_app = Celery(
    "mvp",
    broker=os.environ["CELERY_BROKER_URL"],
    backend=os.environ["CELERY_RESULT_BACKEND"],
)

Base.metadata.create_all(bind=engine)

TARGET_SECONDS = int(os.getenv("TTS_TARGET_SECONDS", "180"))
TOLERANCE_SECONDS = int(os.getenv("TTS_TOLERANCE_SECONDS", "30"))  # +/-30s
WAY_OFF_SECONDS = int(os.getenv("TTS_WAY_OFF_SECONDS", "15"))      # only retry if >15s outside window
CAL_ALPHA = float(os.getenv("TTS_CAL_ALPHA", "0.3"))              # EMA
MAX_TTS_ATTEMPTS = int(os.getenv("TTS_MAX_ATTEMPTS", "2"))        # keep it 2 to limit charges
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
    # avoid float equality issues in composite PK
    return round(speed, 2)


@celery_app.task(name="generate_latest_for_source")
def generate_latest_for_source(
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

        feed = feedparser.parse(src.rss_url)
        if not feed.entries:
            raise RuntimeError("No RSS entries found")

        lookback_days = int(os.getenv("RSS_LOOKBACK_DAYS", "7"))
        cutoff = datetime.utcnow() - timedelta(days=lookback_days)

        def _dt(e):
            return _parse_dt(e)  # your existing helper

        # prefer items inside window
        candidates = [e for e in feed.entries if _dt(e) and _dt(e) >= cutoff]
        entry = candidates[0] if candidates else feed.entries[0]
        title = (entry.get("title") or "").strip() or "Untitled"
        url = (entry.get("link") or "").strip()
        if not url:
            raise RuntimeError("RSS entry has no link/url")
        fallback = (entry.get("summary") or entry.get("description") or "").strip()
        published_at = _parse_dt(entry)

        logger.info("Selected RSS entry: title=%r published_at=%s url=%s", title, published_at, url)

        # upsert article
        article = Article(source_id=src.id, title=title, url=url, published_at=published_at)
        db.add(article)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            article = db.execute(
                select(Article).where(Article.source_id == src.id, Article.url == url)
            ).scalar_one()

        # extract
        raw = extract_article_text(url, fallback_text=fallback)
        if not raw:
            raw = fallback or title

        # choose voice/model EARLY so we can pick the right calibration
        used_voice_id = voice_id or os.getenv("ELEVENLABS_VOICE_ID")
        if not used_voice_id:
            raise RuntimeError("Missing ELEVENLABS_VOICE_ID")
        model_id = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
        output_format = os.getenv("ELEVENLABS_OUTPUT_FORMAT", "mp3_44100_128")
        speed = float(os.getenv("ELEVENLABS_SPEED", "1.0"))
        speed = _speed_key(speed)

        # fetch calibration (default WPM if no samples yet)
        cal = db.get(VoiceCalibration, (used_voice_id, model_id, speed))
        wpm = cal.wpm_estimate if cal else 140.0  # Spanish baseline

        target_words = _words_for_seconds(target_seconds, wpm)
        tol_words = _words_for_seconds(TOLERANCE_SECONDS, wpm)

        # summarize + storyboard (Spanish output is enforced by summarize.py env TTS_OUTPUT_LANGUAGE)
        bundle = make_tts_bundle(
            title=title,
            body=raw,
            language_hint=src.language_hint,
            target_seconds=target_seconds,
            n_scenes=n_scenes,
            target_words=target_words,
            tol_words=tol_words,
        )

        script = bundle["script"]
        scenes = bundle.get("scenes") or []
        word_count = bundle.get("word_count")

        logger.info("Final script words=%s preview=%r", word_count, script[:400])

        # store article artifacts
        article.raw_text = raw
        article.tts_script = script
        article.script_language = os.getenv("TTS_OUTPUT_LANGUAGE", "es-MX")
        article.summary_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        if hasattr(article, "storyboard_json"):
            article.storyboard_json = {"scenes": scenes}
        db.commit()

        final_path = os.path.join(audio_dir, f"{article.id}_{used_voice_id}.mp3")

        duration = None
        last_error = None

        accept_min = MIN_SECONDS - WAY_OFF_SECONDS
        accept_max = MAX_SECONDS + WAY_OFF_SECONDS

        for attempt in range(1, MAX_TTS_ATTEMPTS + 1):
            try:
                audio_bytes = synthesize(script, voice_id=used_voice_id,
                                         model_id=model_id, output_format=output_format)

                # write safely then move into place
                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3", dir=audio_dir) as tmp:
                    tmp.write(audio_bytes)
                    tmp_path = tmp.name

                duration = _mp3_duration_seconds(tmp_path)

                # Accept if within window -> atomic rename works now (same filesystem)
                if accept_min <= duration <= accept_max:
                    os.replace(tmp_path, final_path)
                    break

                # accept if within soft window
                if accept_min <= duration <= accept_max:
                    os.replace(tmp_path, final_path)
                    break

                # if this was the last attempt, keep tmp for debugging or delete it and fall through
                if attempt >= MAX_TTS_ATTEMPTS:
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass
                    break

                # rewrite for next attempt
                wc = word_count or len(script.split())
                desired = target_seconds
                if duration < MIN_SECONDS:
                    desired = MIN_SECONDS
                elif duration > MAX_SECONDS:
                    desired = MAX_SECONDS

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

        accept_min = MIN_SECONDS - WAY_OFF_SECONDS
        accept_max = MAX_SECONDS + WAY_OFF_SECONDS
        if duration is None or not (accept_min <= duration <= accept_max):
            # mark failure or at least surface the error
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

        # DB record for audio
        audio = AudioAsset(
            article_id=article.id,
            voice_id=used_voice_id,
            model_id=model_id,
            output_format=output_format,
            file_path=final_path,
            tts_provider="elevenlabs",
        )

        # If you added these fields in models.py, fill them:
        if hasattr(audio, "target_seconds"):
            audio.target_seconds = target_seconds
        if hasattr(audio, "estimated_seconds"):
            audio.estimated_seconds = duration
        if hasattr(audio, "word_count"):
            audio.word_count = word_count
        if hasattr(audio, "status"):
            audio.status = "ready"

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
            "scenes": scenes,  # helpful for next step (images/video)
        }
