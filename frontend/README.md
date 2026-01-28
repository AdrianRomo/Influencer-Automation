# Influencer Automation Frontend (Vite + React)

Minimal UI for your FastAPI + Celery MVP.

## Features
- Select RSS **source**
- Set optional **voice_id**, **target_seconds**, **n_scenes**
- Trigger `POST /generate`
- Poll `GET /jobs/{task_id}` until `SUCCESS`/`FAILURE`
- Show **Download MP3** button when ready (uses `result.audio_url` or builds `/audio/{audio_id}`)

## Quick start

```bash
npm install
cp .env.example .env
npm run dev
```

Open http://localhost:5173

## Backend endpoints this UI uses

- `GET  /sources`
- `POST /generate`
- `GET  /jobs/{task_id}`
- `GET  /audio/{audio_id}` (returns the mp3 via `FileResponse`)

Optional (only for convenience link):
- `GET  /articles/{article_id}`

## Docker compose snippet

```yaml
frontend:
  image: node:20-alpine
  working_dir: /app
  volumes:
    - ./frontend:/app
    - frontend_node_modules:/app/node_modules
  command: sh -lc "npm install && npm run dev"
  ports:
    - "5173:5173"
  environment:
    - VITE_API_BASE_URL=http://localhost:8000

volumes:
  frontend_node_modules:
```

> If you're reverse-proxying the API under the same origin, you can omit `VITE_API_BASE_URL`
> and use a Vite proxy instead (see `vite.config.ts`).


## CORS (important for local dev)

If your frontend runs on `http://localhost:5173` and the API on `http://localhost:8000`,
you must enable CORS on the FastAPI side, otherwise the browser will block requests and you may see
an `OPTIONS 405 Method Not Allowed`.

Add this near where you create `app = FastAPI(...)`:

```py
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

