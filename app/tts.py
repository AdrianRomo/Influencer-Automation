import os
import time
from typing import Optional

from elevenlabs import VoiceSettings
from elevenlabs.client import ElevenLabs

# One client per worker process (Celery-friendly)
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
if not ELEVENLABS_API_KEY:
    raise RuntimeError("ELEVENLABS_API_KEY is not set")

_client = ElevenLabs(api_key=ELEVENLABS_API_KEY)

# Multilingual v2 supports Spanish; language_code accepts 'es' among others.
DEFAULT_LANGUAGE_CODE = os.getenv("ELEVENLABS_LANGUAGE_CODE", "es")

MODEL_ID = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
OUTPUT_FORMAT = os.getenv("ELEVENLABS_OUTPUT_FORMAT", "mp3_44100_128")
DEFAULT_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID")

# Keep a safety cap (your ~3 min scripts should be well below this anyway)
MAX_CHARS = int(os.getenv("ELEVENLABS_MAX_CHARS", "9000"))

def _default_voice_settings() -> VoiceSettings:
    # These map to ElevenLabs voice settings shown in their SDK examples.
    return VoiceSettings(
        stability=float(os.getenv("ELEVENLABS_STABILITY", "0.45")),
        similarity_boost=float(os.getenv("ELEVENLABS_SIMILARITY_BOOST", "0.85")),
        style=float(os.getenv("ELEVENLABS_STYLE", "0.15")),
        use_speaker_boost=os.getenv("ELEVENLABS_SPEAKER_BOOST", "true").lower() in ("1", "true", "yes"),
        speed=float(os.getenv("ELEVENLABS_SPEED", "1.0")),  # keep fixed if you enforce exact duration later
    )

def synthesize(
    text: str,
    voice_id: Optional[str] = None,
    *,
    voice_settings: Optional[VoiceSettings] = None,
    language_code: Optional[str] = DEFAULT_LANGUAGE_CODE,
    retries: int = 3,
    model_id: str = MODEL_ID,
    output_format: str = OUTPUT_FORMAT,
) -> bytes:
    if not text or not text.strip():
        raise ValueError("Empty text")
    if len(text) > MAX_CHARS:
        raise ValueError(f"Text too long for one request: {len(text)} chars (max {MAX_CHARS})")

    vid = voice_id or DEFAULT_VOICE_ID
    if not vid:
        raise RuntimeError("ELEVENLABS_VOICE_ID is not set and no voice_id was provided")

    vs = voice_settings or _default_voice_settings()

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            # convert returns an iterator of bytes in the SDK examples.
            audio_stream = _client.text_to_speech.convert(
                voice_id=vid,
                model_id=model_id,
                output_format=output_format,
                text=text,
                voice_settings=vs,
                language_code=language_code,  # optional, but helpful for Spanish normalization
            )

            chunks = []
            for chunk in audio_stream:
                if isinstance(chunk, (bytes, bytearray)) and chunk:
                    chunks.append(bytes(chunk))
            return b"".join(chunks)

        except Exception as e:
            last_err = e
            if attempt == retries - 1:
                raise
            time.sleep(0.8 * (2 ** attempt))  # simple backoff

    # unreachable, but keeps type-checkers happy
    raise last_err or RuntimeError("TTS failed")
