# Influencer Automation (News → Script → Audio → Captions)

End-to-end pipeline to **ingest news/article sources (RSS/feeds)**, store them, rank “top stories”, and generate **ready-to-publish narration assets** (script + TTS audio + subtitles/captions). Built for a **FastAPI + Celery + SQLAlchemy** stack and designed to run locally via **Docker Compose** with hot-reload for development.

> Primary use case (current focus): **medicine/health-related news** (but sources are configurable).

---

## What this project does

1. **Ingest** sources (RSS/Atom or other feed URLs)
2. **Extract & normalize** articles (title, url, published date, content/summary, etc.)
3. **Rank / query “top stories”** over the last N hours
4. **Generate narration assets**
   - Clean script (short-form or long-form)
   - Optional translation (e.g., EN ↔ ES-MX)
   - Text-to-Speech audio output
   - Subtitles/captions (SRT/VTT) from the final script
5. Expose everything via a **REST API** and run heavy work asynchronously via **Celery**

---

## Key features

- ✅ **FastAPI** REST API
- ✅ **SQLAlchemy** models for `sources` and `articles` (extensible)
- ✅ **Celery** background tasks for ingestion and media generation
- ✅ **Docker Compose** for local dev + production-like environment
- ✅ **Top stories endpoint**: `GET /articles/top?hours=24`
- ✅ Designed for **automation** (periodic ingestion via Celery Beat / cron-like schedules)
- ✅ Works for multi-language workflows (e.g., Spanish MX + English)

---

## Architecture

- **API service** (FastAPI)
  - CRUD for sources
  - Query endpoints for articles
  - Triggers Celery tasks
- **Worker service** (Celery)
  - Ingest all sources
  - Parse feeds, fetch/clean article content
  - Generate script, translation, TTS, captions
- **Scheduler** (Celery Beat)
  - Runs ingestion every X minutes
- **Database** (PostgreSQL)
- **Broker/Backend** (Redis)

---

## Tech stack

- Python 3.11+ (recommended)
- FastAPI + Uvicorn
- Celery + Redis
- PostgreSQL
- SQLAlchemy (ORM) + Alembic (migrations)
- Feed parsing + article extraction (implementation-dependent)
- TTS provider (OpenAI / cloud TTS / other)

---

## Repo layout (typical)

> Your exact structure may differ, but the project is usually organized like this:

```
.
├── app/
│   ├── api/                # FastAPI routers/controllers
│   ├── core/               # config, logging, settings
│   ├── db/                 # SQLAlchemy session, base, migrations hooks
│   ├── models/             # Source, Article, (and later: Asset, Script, Job)
│   ├── services/           # feed ingestion, extraction, ranking, TTS, captions
│   ├── tasks/              # Celery tasks
│   └── main.py             # FastAPI entrypoint
├── migrations/             # Alembic migrations (if enabled)
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml / requirements.txt
└── README.md
```

---

## Quickstart (Docker)

### 1) Prerequisites
- Docker + Docker Compose

### 2) Configure environment variables
Create a `.env` file at the project root:

```bash
# Core
ENV=dev
LOG_LEVEL=INFO

# Database
POSTGRES_DB=influencer_automation
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
DATABASE_URL=postgresql+psycopg2://postgres:postgres@db:5432/influencer_automation

# Redis (Celery broker/result backend)
REDIS_URL=redis://redis:6379/0

# Security
API_KEY=dev-key-change-me  # (optional if you add auth middleware)

# Providers (examples; adapt to your implementation)
OPENAI_API_KEY=your_key_here
TTS_PROVIDER=openai         # openai|google|azure|elevenlabs|local
TRANSLATION_PROVIDER=openai # openai|deepl|google|none

# Pipeline knobs
DEFAULT_LANGUAGE=en
TARGET_LANGUAGE=es-MX
TOP_HOURS_DEFAULT=24
```

### 3) Run the stack
```bash
docker compose up --build
```

What starts:
- `api` (FastAPI)
- `worker` (Celery)
- `beat` (scheduler, optional)
- `db` (Postgres)
- `redis` (Redis)

### 4) Apply migrations (if using Alembic)
In a new terminal:
```bash
docker compose exec api alembic upgrade head
```

---

## Development workflow (hot reload)

This project is intended to support rapid iteration:
- FastAPI runs with `--reload`
- Source code is bind-mounted into containers (so changes are picked up automatically)

Typical dev commands:
```bash
docker compose up --build
# then edit code locally; the API reloads automatically
```

If you update dependencies:
```bash
docker compose build --no-cache
docker compose up
```

---

## Core concepts

### Sources
A **Source** is typically an RSS/Atom feed URL (or a custom ingest adapter).
Example fields:
- `name`
- `url`
- `enabled`
- `category` (optional)
- `created_at`

### Articles
An **Article** is a normalized record created from feed items.
Example fields:
- `source_id`
- `title`
- `url` (unique)
- `published_at`
- `summary` / `content`
- `language`
- `score` (optional for ranking)
- `created_at`

---

## API endpoints (baseline)

> Your actual routes may vary, but these are the expected “MVP” endpoints.

### Health
```bash
curl http://localhost:8000/health
```

### Sources
Create/list sources:
```bash
curl -X POST http://localhost:8000/sources \
  -H "Content-Type: application/json" \
  -d '{"name":"Example Feed","url":"https://example.com/rss.xml","enabled":true}'
```

```bash
curl http://localhost:8000/sources
```

### Ingestion trigger
Kick off ingestion for all enabled sources:
```bash
curl -X POST http://localhost:8000/ingest
```

### Top stories
Get top articles for the last N hours:
```bash
curl "http://localhost:8000/articles/top?hours=24"
```

---

## Celery tasks (baseline)

Expected task entry points:

- `ingest_all_sources`
  - Fetch enabled sources
  - Parse feed items
  - Upsert articles by URL
  - Optionally queue downstream processing

- Optional downstream tasks (depending on your implementation):
  - `extract_article_content(article_id)`
  - `summarize_article(article_id)`
  - `translate_script(article_id, target_language)`
  - `generate_tts(article_id, voice=..., provider=...)`
  - `generate_captions(article_id, format=srt|vtt)`

Run a one-off task from inside the worker container:
```bash
docker compose exec worker celery -A app.tasks.celery_app call ingest_all_sources
```

---

## Adding new sources

1. Add a source via API (`POST /sources`)
2. Ensure it’s `enabled=true`
3. Run ingestion:
   - via API (`POST /ingest`), or
   - wait for scheduler (Celery Beat) if enabled

---

## Output assets

Depending on how you implement the media pipeline, outputs are usually stored in one of:
- Database (metadata) + local filesystem (files)
- Object storage (S3-compatible) + DB references

Common file outputs:
- `script.txt` (or JSON)
- `narration.mp3` (or wav)
- `captions.srt` / `captions.vtt`

A typical pattern is:
- Store asset records (type, path, duration, language, voice)
- Keep actual binaries on disk or object storage

---

## Configuration notes

### Ranking / “Top stories”
The “top stories” endpoint commonly uses:
- recency (published_at)
- source weights
- keyword/category boosts
- deduplication
- optional engagement signals (if you later add them)

MVP default: **most recent** within the time window, optionally excluding duplicates.

### Medicine/health sources
Medical articles often contain tables and structured data. If extraction quality is an issue:
- Prefer sources with clean HTML/article bodies
- Use publisher APIs when available
- Fallback to feed summaries if full text extraction is noisy

---

## Troubleshooting

### “Worker not processing tasks”
- Confirm Redis is running
- Confirm `REDIS_URL` matches inside containers
- Check logs:
  ```bash
  docker compose logs -f worker
  docker compose logs -f redis
  ```

### “Database connection errors”
- Confirm Postgres container is healthy
- Ensure `DATABASE_URL` points to `db` host (inside Docker network)

### “Duplicate articles”
- Enforce a unique constraint on `articles.url`
- Use upsert logic on ingest

---

## Roadmap (suggested)

- [ ] Article extraction improvements (clean HTML → text)
- [ ] Robust deduplication (canonical URLs + similarity)
- [ ] Script templates (TikTok/Shorts vs long-form)
- [ ] Asset model: scripts, audio, captions, publish jobs
- [ ] Publish automation (YouTube/TikTok APIs)
- [ ] Admin UI (sources management + pipeline monitoring)
- [ ] Observability (Prometheus/Grafana, Sentry)

---

## Legal / content note

This project **does not grant rights** to republish copyrighted content. If you generate narration or derivative content from third-party articles, ensure:
- you have permission,
- you comply with publisher terms,
- and you follow applicable copyright/fair-use rules in your jurisdiction.

---

## Contributing

- Create a feature branch
- Add tests for new logic
- Keep ingestion deterministic and idempotent
- Prefer small, composable Celery tasks

---
