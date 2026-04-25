#!/usr/bin/env python3
"""Seed a dev database with sample users, articles, and generated packages.

Runs idempotently — re-invoking the script updates existing rows instead of
duplicating them. Designed for local development so new contributors can spin
up the UI and see a populated history without burning real API credits.

Usage::

    python scripts/seed_dev.py            # seed default fixtures
    python scripts/seed_dev.py --fresh    # wipe seeded rows first
    python scripts/seed_dev.py --user you@example.com  # own seeded rows

Environment: needs the same DATABASE_URL + SECRET_KEY as the API.
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

# Ensure the project root is on sys.path when invoked directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.auth import hash_password
from app.db import SessionLocal, engine
from app.models import Article, AudioAsset, Base, Source, User

# Marker in article URL prefix so --fresh only nukes seeded rows.
SEED_PREFIX = "https://dev-seed.local/"

_SAMPLE_SCRIPTS = [
    {
        "title": "FDA aprueba nuevo tratamiento contra la migraña",
        "source_id": "fda_news",
        "language": "es-MX",
        "tts_script": (
            "La Administración de Alimentos y Medicamentos, FDA, aprobó un "
            "nuevo tratamiento inyectable para adultos con migraña crónica. "
            "El medicamento actúa bloqueando una proteína llamada CGRP, "
            "responsable de desencadenar los episodios. Los ensayos clínicos "
            "mostraron una reducción del cincuenta por ciento en la "
            "frecuencia de los ataques. Como siempre, esta información no "
            "reemplaza la consulta con un profesional de la salud."
        ),
        "storyboard_json": {
            "scenes": [
                {
                    "scene_number": 1,
                    "narration": "La FDA aprobó un nuevo tratamiento contra la migraña.",
                    "visual_prompt": "Close-up of a branded medication bottle on a clean white surface",
                    "on_screen_text": "FDA approval",
                    "asset_type": "title-card",
                    "transition": "cut",
                    "start_time_estimate": 0.0,
                    "duration_estimate": 6.0,
                },
                {
                    "scene_number": 2,
                    "narration": "Bloquea una proteína llamada CGRP.",
                    "visual_prompt": "3D molecular animation showing a protein being blocked",
                    "on_screen_text": "CGRP blockade",
                    "asset_type": "b-roll",
                    "transition": "cut",
                    "start_time_estimate": 6.0,
                    "duration_estimate": 8.0,
                },
                {
                    "scene_number": 3,
                    "narration": "Reduce la frecuencia de ataques en un 50%.",
                    "visual_prompt": "Overhead view of a medical chart with declining trend line",
                    "on_screen_text": "-50% attacks",
                    "asset_type": "b-roll",
                    "transition": "cut",
                    "start_time_estimate": 14.0,
                    "duration_estimate": 7.0,
                },
                {
                    "scene_number": 4,
                    "narration": "Consulta siempre a un profesional de la salud.",
                    "visual_prompt": "Doctor and patient talking in a bright clinic",
                    "on_screen_text": "Talk to your doctor",
                    "asset_type": "outro",
                    "transition": "cut",
                    "start_time_estimate": 21.0,
                    "duration_estimate": 5.0,
                },
            ],
            "total_duration_estimate": 26.0,
        },
        "analysis_json": {
            "sentiment": "positive",
            "impact_score": 7,
            "medical_urgency": "informational",
            "key_claims": [
                "FDA approved a new migraine injectable",
                "Blocks CGRP protein",
                "50% reduction in attack frequency",
            ],
            "audience_relevance": "Chronic migraine sufferers and their caregivers.",
        },
    },
    {
        "title": "OMS advierte sobre aumento de infecciones resistentes a antibióticos",
        "source_id": "who_news",
        "language": "es-MX",
        "tts_script": (
            "La Organización Mundial de la Salud lanza una nueva alerta "
            "sobre el aumento de infecciones resistentes a antibióticos. "
            "En los últimos cinco años, algunos patógenos han duplicado su "
            "resistencia a los tratamientos de primera línea. El uso "
            "excesivo y automedicado sigue siendo la principal causa. "
            "La OMS recomienda completar los tratamientos según indicación "
            "médica y reservar los antibióticos para infecciones bacterianas "
            "confirmadas. Esta información es educativa; no sustituye la "
            "consulta profesional."
        ),
        "storyboard_json": {
            "scenes": [
                {
                    "scene_number": 1,
                    "narration": "La OMS alerta sobre la resistencia a antibióticos.",
                    "visual_prompt": "World Health Organization logo against abstract blue background",
                    "on_screen_text": "WHO alert",
                    "asset_type": "title-card",
                    "transition": "cut",
                    "start_time_estimate": 0.0,
                    "duration_estimate": 6.0,
                },
                {
                    "scene_number": 2,
                    "narration": "Algunos patógenos duplicaron su resistencia.",
                    "visual_prompt": "Petri dish with visible bacterial colonies under warm light",
                    "on_screen_text": "2× resistance",
                    "asset_type": "b-roll",
                    "transition": "cut",
                    "start_time_estimate": 6.0,
                    "duration_estimate": 8.0,
                },
                {
                    "scene_number": 3,
                    "narration": "El uso excesivo sigue siendo la principal causa.",
                    "visual_prompt": "Hand reaching for antibiotic pills from a medicine cabinet",
                    "on_screen_text": "Overuse = main cause",
                    "asset_type": "b-roll",
                    "transition": "cut",
                    "start_time_estimate": 14.0,
                    "duration_estimate": 7.0,
                },
                {
                    "scene_number": 4,
                    "narration": "Usa antibióticos sólo cuando sean realmente necesarios.",
                    "visual_prompt": "Doctor writing a prescription in a clean modern clinic",
                    "on_screen_text": "Only when needed",
                    "asset_type": "outro",
                    "transition": "cut",
                    "start_time_estimate": 21.0,
                    "duration_estimate": 5.0,
                },
            ],
            "total_duration_estimate": 26.0,
        },
        "analysis_json": {
            "sentiment": "cautionary",
            "impact_score": 9,
            "medical_urgency": "important",
            "key_claims": [
                "Antibiotic resistance is accelerating worldwide",
                "Some pathogens have doubled their resistance in 5 years",
                "Overuse and self-medication are the main drivers",
            ],
            "audience_relevance": "General public and primary-care patients.",
        },
    },
]


def _upsert_dev_user(db, email: str, password: str) -> User:
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user:
        return user
    user = User(
        id=str(uuid.uuid4()),
        email=email,
        hashed_password=hash_password(password),
    )
    db.add(user)
    db.flush()
    return user


def _upsert_source(db, source_id: str, name: str) -> Source:
    src = db.get(Source, source_id)
    if src:
        return src
    src = Source(
        id=source_id,
        name=name,
        rss_url=f"{SEED_PREFIX}{source_id}.xml",
        language_hint="es",
    )
    db.add(src)
    db.flush()
    return src


def _upsert_article(db, user: User, sample: dict) -> Article:
    source = _upsert_source(db, sample["source_id"], sample["title"][:40])
    url = f"{SEED_PREFIX}articles/{sample['source_id']}"
    article = db.execute(
        select(Article).where(Article.source_id == source.id, Article.url == url)
    ).scalar_one_or_none()
    if not article:
        article = Article(source_id=source.id, url=url)
        db.add(article)
    article.title = sample["title"]
    article.user_id = user.id
    article.language = sample["language"]
    article.tts_script = sample["tts_script"]
    article.script_language = sample["language"]
    article.summary_model = "seed-fixture"
    article.storyboard_json = sample["storyboard_json"]
    article.analysis_json = sample["analysis_json"]
    article.selected_platforms = ["tiktok", "reels"]
    article.animation_prompt = None
    article.published_at = datetime.utcnow() - timedelta(days=1)
    article.raw_text = sample["tts_script"]  # simulate extraction
    db.flush()
    return article


def _purge_seeded(db) -> int:
    """Remove all seeded articles/sources/audios. Returns rows deleted."""
    count = 0
    articles = db.execute(
        select(Article).where(Article.url.like(f"{SEED_PREFIX}%"))
    ).scalars().all()
    for a in articles:
        for audio in list(a.audio_assets):
            db.delete(audio)
            count += 1
        db.delete(a)
        count += 1
    sources = db.execute(
        select(Source).where(Source.rss_url.like(f"{SEED_PREFIX}%"))
    ).scalars().all()
    for s in sources:
        db.delete(s)
        count += 1
    db.flush()
    return count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", default=os.getenv("DEV_SEED_EMAIL", "dev@example.com"))
    parser.add_argument("--password", default=os.getenv("DEV_SEED_PASSWORD", "dev-password-12345"))
    parser.add_argument("--fresh", action="store_true", help="Delete previously seeded rows first")
    args = parser.parse_args()

    # Make sure tables exist when invoked against a clean DB.
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        if args.fresh:
            deleted = _purge_seeded(db)
            print(f"[seed] deleted {deleted} previously seeded rows")

        user = _upsert_dev_user(db, args.user, args.password)
        created = 0
        for sample in _SAMPLE_SCRIPTS:
            _upsert_article(db, user, sample)
            created += 1
        db.commit()

    print(f"[seed] user={args.user} password={args.password}")
    print(f"[seed] upserted {created} sample article(s)")
    print("[seed] log in at the frontend and you'll see these in your history")


if __name__ == "__main__":
    main()
