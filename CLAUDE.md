Here’s a revised `CLAUDE.md` that keeps your current repo context but adds the stronger execution rules and the product direction needed to push Claude Code toward a **full video-generation pipeline**, not just scripts + TTS.

````md
# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

Current state (this is a full short-form VIDEO pipeline, not audio-only):
RSS medical/health feeds → Spanish narration scripts (OpenAI) → storyboard with per-scene timing → ElevenLabs TTS → DALL·E scene images → optional AI image-to-video scene clips (Seedance/Runway) → FFmpeg assembly → SRT/VTT captions → MP4 export. Also includes an ad/catalog flow (brands, products, campaigns, ad concepts), auth, workspaces, billing/credits. Served via REST API + React UI.

Current core flow:
`RSS Feed → Extraction → Script → Storyboard (timed) → TTS → Images → (optional AI clips) → FFmpeg assembly → Captions → MP4`

Product direction (in progress, 2026-06):
Add a final-step **embedded video editor** before render — Shotstack Studio SDK (white-label embed) emitting Edit JSON rendered by the Shotstack API, with FFmpeg (`app/video.py`) kept as a no-cost fallback. Users drag/retime subtitles and clips, and toggle a **tiered "Animate"** per clip: free motion effects (Ken Burns/zoom/pan/transitions) or AI image-to-video via the existing Seedance/Runway providers. See `app/scene_video.py`. Key constraint: Shotstack cloud render fetches assets by URL, so media must be exposed via signed, internet-reachable URLs.

When making product or architecture decisions, prefer changes that move the system toward:
- full video creation, not just narration
- reusable short-form content packaging
- export-ready assets
- stronger automation with minimal manual editing
- publishable outputs for social platforms

## Working Style for Claude Code

When working in this repository, follow these rules:

- Be decisive and execution-focused.
- Do not give generic advice when code changes are needed.
- Prefer implementing the next logical step over writing long speculative plans.
- Preserve the current working pipeline unless the task explicitly requires breaking changes.
- Make smart assumptions from the existing architecture and document them briefly in comments or commit notes.
- When adding new features, design them so they can support future video generation/export.
- Prefer incremental, production-usable changes over placeholder abstractions.
- Keep outputs practical, testable, and easy for the team to run locally with Docker.
- If a requested feature cannot be fully completed in one pass, implement the highest-value foundation for it instead of stopping at pseudocode.

## Current Product Goals

Primary near-term goal:
- Maintain a reliable pipeline for extracting medical/health content and generating high-quality Spanish narration assets.

Secondary expansion goal:
- Extend the system to support full media packaging, including:
  - stronger scene/storyboard generation
  - structured visual prompts
  - subtitle/caption generation
  - thumbnail/cover text generation
  - short-form video assembly
  - downloadable final assets

When introducing new modules, APIs, or schemas, favor naming and structure that can support both:
1. audio-only generation
2. full audiovisual generation

## Commands

### Docker (recommended)
```bash
docker compose up --build       # Start all services
docker compose logs -f worker   # Watch Celery task execution
docker compose exec db psql -U postgres -d mvp  # DB shell
````

### Local Python (requires Postgres + Redis)

```bash
uv sync
export $(cat .env | xargs)
uvicorn app.main:app --reload --port 8085   # API
celery -A app.tasks.celery_app worker -l INFO  # Worker
```

### Frontend

```bash
cd frontend
npm install
npm run dev    # Vite dev server on :5173
npm run build
```

## Architecture

### Services (docker-compose.yml)

* **api** — FastAPI on port 8085
* **worker** — Celery consumer
* **beat** — Celery Beat scheduler
* **db** — PostgreSQL 16
* **redis** — Celery broker + result backend
* **frontend** — React/Vite on port 5173

### Key Files

| File                 | Responsibility                                                                 |
| -------------------- | ------------------------------------------------------------------------------ |
| `app/main.py`        | FastAPI app, includes routers, startup hook seeds RSS sources                   |
| `app/routers/`       | Route modules: auth, billing, brands, campaigns, catalog, concepts, products, workspaces, media, health |
| `app/tasks.py`       | Celery task orchestration (article + ad-concept generation pipelines)          |
| `app/models.py`      | ORM: Source, Article, AudioAsset, ImageAsset, VideoAsset, SceneVideoAsset, VoiceCalibration, User/Workspace, Brand/Catalog/Product, Campaign/AdConcept, credits |
| `app/summarize.py`   | OpenAI calls: script generation, word-count targeting, storyboarding            |
| `app/tts.py`         | ElevenLabs synthesis wrapper                                                    |
| `app/image_gen.py`   | DALL·E scene image generation                                                   |
| `app/scene_video.py` | Image-to-video provider abstraction: Static, **Seedance**, **Runway** (submit/poll/download) |
| `app/video.py`       | FFmpeg assembly (scene images + audio + subtitles → 9:16 MP4)                   |
| `app/captions.py`    | Storyboard timing → SRT/WebVTT (no LLM calls)                                   |
| `app/export.py`      | Content package export                                                          |
| `app/extract.py`     | Article content extraction via trafilatura with fallbacks                       |
| `app/rss_sources.py` | 30+ pre-configured medical RSS feeds (FDA, NIH, CDC, NLM, etc.)                 |
| `app/db.py`          | SQLAlchemy engine, session factory, FastAPI `get_db()` dependency               |

### Planned Expansion Areas (embedded editor)

* `app/timeline.py` — canonical Edit document + storyboard→Shotstack-JSON adapter
* `app/render_shotstack.py` — submit Edit JSON to Shotstack render API, poll, store VideoAsset
* signed/internet-reachable asset URLs so the Shotstack cloud renderer can fetch images/audio/clips
* `frontend/src` — add a router + `/editor/:articleId` page embedding `@shotstack/shotstack-studio`

## API Endpoints

Implemented (see `app/routers/`):

* `GET /sources`, `POST /generate`, `GET /jobs/{task_id}`
* `GET /audio/{id}`, `GET /image/{id}`, `GET /video/{id}`, `GET /scene-videos/{id}`, `GET /thumbnail/...` (in `routers/media.py`)
* `GET /articles/{id}` — article with script + storyboard JSON
* `POST /generate-video` and the ad/catalog/concept/campaign/workspace/billing routes

Planned (embedded editor):

* `GET/PUT /articles/{id}/timeline` — load/save the editable Edit document
* `POST /articles/{id}/render` + `GET /render/{job}` — render edited timeline (Shotstack; FFmpeg fallback)
* `POST /articles/{id}/clips/{n}/animate` — queue tiered animate (effect or AI image-to-video)
* `GET /articles/{id}/package` — final MP4 + captions + thumbnail + social captions

## Database Schema

Implemented:

* `sources`, `articles` (with `storyboard_json`, `social_captions_json`), `audio_assets`, `image_assets`, `video_assets`, `scene_video_assets`, `voice_calibration`
* `users`, `workspaces`, `workspace_members`, `user_api_keys`, credit ledger (`credit_transactions`), usage events
* `brands`, `catalogs`, `products`, `product_images`, `campaigns`, `ad_concepts`

Planned:

* `edit_timelines` — persisted editable Edit document per article/concept, versioned

Schema changes should be backward-compatible when possible (Alembic migrations under `alembic/`).

## Design Patterns

### Voice Calibration

The system tracks words-per-minute per voice/model/speed combo to predict audio duration before synthesis. Uses EMA (alpha=0.3) to improve estimates over time. This drives the word-count targeting in script generation.

### Duration Targeting Loop (in `tasks.py`)

1. Generate script targeting ~N words (derived from WPM estimate × target seconds)
2. Synthesize audio
3. Check MP3 duration against window (`TTS_DURATION_MIN_SECONDS` to `TTS_DURATION_MAX_SECONDS`)
4. If outside window by more than `TTS_WAY_OFF_SECONDS`, rewrite script and retry
5. Max `TTS_MAX_ATTEMPTS` attempts (default: 2)

### Content Extraction Fallbacks (`extract.py`)

1. Trafilatura direct fetch
2. Manual HTTP fetch + trafilatura parse
3. RSS summary/description as last resort

Minimum quality threshold: 120 words

### Script Generation

* Output is always Spanish (generated in Spanish, not translated)
* TTS-friendly: no citations, URLs, bullet points
* Numbers, drug names, units preserved exactly
* Medical disclaimer appended in Spanish

### Storyboard-First Expansion

When adding features beyond narration, prefer generating structured intermediate artifacts before rendering:

1. script
2. scene breakdown
3. visual prompts
4. subtitle/caption data
5. asset manifest
6. final render/export

This makes the system easier to debug, preview, and regenerate partially.

## Content Quality Rules

When generating scripts, storyboards, subtitles, or visual plans:

* Keep medical claims faithful to the source article.
* Do not introduce unsupported health claims.
* Preserve important numbers, dosages, units, and drug names exactly.
* Favor clarity and correctness over hype.
* For public-facing short-form media, keep tone engaging but medically responsible.
* If summarizing for video, compress safely without overstating certainty.

## Critical Environment Variables

```env
DATABASE_URL=postgresql://...
CELERY_BROKER_URL=redis://...
CELERY_RESULT_BACKEND=redis://...
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_ID=
ELEVENLABS_MODEL_ID=eleven_multilingual_v2
TTS_TARGET_SECONDS=180
TTS_TOLERANCE_SECONDS=30
TTS_MAX_ATTEMPTS=2
TTS_OUTPUT_LANGUAGE=es-MX
STORYBOARD_SCENES=8
AUDIO_DIR=/data/audio
```

Possible future variables:

```env
VIDEO_OUTPUT_DIR=/data/video
IMAGE_OUTPUT_DIR=/data/images
CAPTIONS_OUTPUT_DIR=/data/captions
VIDEO_TARGET_FORMAT=mp4
VIDEO_ASPECT_RATIO=9:16
VIDEO_TARGET_SECONDS=30
ENABLE_VIDEO_RENDERING=false
```

Do not assume future env vars exist unless they have been added to the codebase.

## Storyboarding

Current:
Optional scene generation (`n_scenes=0` disables). Narration text is Spanish (aligned to script); image prompts are English (for DALL-E/Midjourney). Stored as JSON in `article.storyboard_json`.

Preferred evolution:
Storyboards should become structured enough to support:

* shot-by-shot preview
* subtitle timing
* visual prompt generation
* thumbnail selection
* automated or semi-automated video rendering

When modifying storyboard output, prefer machine-friendly JSON with explicit fields such as:

* `scene_number`
* `start_time_estimate`
* `duration_estimate`
* `narration`
* `visual_prompt`
* `on_screen_text`
* `transition`
* `asset_type`
* `notes`

## Frontend Guidance

The frontend should remain simple and operational, but future work should support:

* job submission and status tracking
* script preview
* storyboard preview
* audio playback
* asset download
* future video preview/export flows

When making UI changes:

* prioritize clear task status and downloadable outputs
* keep forms lightweight
* avoid overengineering state management unless complexity truly requires it
* prefer components that can later support video assets without major rewrites

## Implementation Priorities

When choosing among multiple valid improvements, prioritize in this order:

1. reliability of the current pipeline
2. quality of extraction and script generation
3. audio quality and duration accuracy
4. structured storyboarding
5. reusable asset packaging
6. video rendering/export support
7. frontend polish

## Safe Change Principles

* Do not silently remove working functionality.
* Avoid large refactors unless they unlock a clear product need.
* Keep functions small and inspectable in task orchestration paths.
* Prefer explicit schemas over loose dictionaries when outputs become more complex.
* Add comments where non-obvious prompt or media logic is introduced.
* Preserve local-dev friendliness.

## Definition of Good Output

A strong implementation in this repo should produce outputs that are:

* accurate
* deterministic enough for automation
* easy to inspect
* easy to regenerate
* useful without heavy manual cleanup
* compatible with future video/export workflows
