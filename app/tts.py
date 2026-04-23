import os
import time
from typing import TYPE_CHECKING, Optional

from elevenlabs import VoiceSettings
from elevenlabs.client import ElevenLabs

if TYPE_CHECKING:
    from app.usage import UsageCollector

# Module-level client — used when no per-user key is supplied
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
_client: ElevenLabs | None = ElevenLabs(api_key=ELEVENLABS_API_KEY) if ELEVENLABS_API_KEY else None

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
    api_key: Optional[str] = None,
    voice_settings: Optional[VoiceSettings] = None,
    language_code: Optional[str] = DEFAULT_LANGUAGE_CODE,
    retries: int = 3,
    model_id: str = MODEL_ID,
    output_format: str = OUTPUT_FORMAT,
    collector: "UsageCollector | None" = None,
    operation: str = "tts",
) -> bytes:
    if not text or not text.strip():
        raise ValueError("Empty text")
    if len(text) > MAX_CHARS:
        raise ValueError(f"Text too long for one request: {len(text)} chars (max {MAX_CHARS})")

    vid = voice_id or DEFAULT_VOICE_ID
    if not vid:
        raise RuntimeError("ELEVENLABS_VOICE_ID is not set and no voice_id was provided")

    vs = voice_settings or _default_voice_settings()

    effective_key = api_key or ELEVENLABS_API_KEY
    if not effective_key:
        raise RuntimeError("No ElevenLabs API key — set ELEVENLABS_API_KEY or configure user API keys")
    active_client = ElevenLabs(api_key=effective_key) if api_key else (_client or ElevenLabs(api_key=effective_key))

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            from app.circuit_breakers import elevenlabs_breaker  # lazy import — no circular deps
            # convert returns an iterator of bytes in the SDK examples.
            audio_stream = elevenlabs_breaker.call(
                active_client.text_to_speech.convert,
                voice_id=vid,
                model_id=model_id,
                output_format=output_format,
                text=text,
                voice_settings=vs,
                language_code=language_code,
            )

            chunks = []
            for chunk in audio_stream:
                if isinstance(chunk, (bytes, bytearray)) and chunk:
                    chunks.append(bytes(chunk))
            audio_bytes = b"".join(chunks)

            if collector is not None:
                try:
                    from app.pricing import estimate_elevenlabs_cost, get_elevenlabs_pricing_snapshot
                    char_count = len(text)
                    cost = estimate_elevenlabs_cost(model_id=model_id, character_count=char_count)
                    collector.record(
                        provider="elevenlabs",
                        operation=operation,
                        model=model_id,
                        character_count=char_count,
                        estimated_cost_usd=cost,
                        pricing_snapshot=get_elevenlabs_pricing_snapshot(model_id),
                        metadata={"voice_id": vid, "output_format": output_format},
                    )
                except Exception:
                    pass  # never block generation on tracking failures

            return audio_bytes

        except Exception as e:
            last_err = e
            if attempt == retries - 1:
                raise
            time.sleep(0.8 * (2 ** attempt))  # simple backoff

    # unreachable, but keeps type-checkers happy
    raise last_err or RuntimeError("TTS failed")
