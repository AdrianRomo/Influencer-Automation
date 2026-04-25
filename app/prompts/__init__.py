"""Prompt loader.

All LLM system prompts live as ``.md`` files next to this module, so they
are version-controlled, diff-friendly, and can be edited without touching
Python. Prompts may contain ``{placeholder}`` markers that get substituted
when ``get()`` is called.

Usage
-----
    from app.prompts import get

    system = get("script", output_language="es-MX")

The first call reads and caches all prompt files. Unknown names raise
``KeyError``; missing placeholders raise ``KeyError`` from ``str.format``.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


@lru_cache(maxsize=None)
def _read(name: str) -> str:
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise KeyError(f"Unknown prompt: {name!r} (no file at {path})")
    return path.read_text(encoding="utf-8")


def get(name: str, **substitutions: str) -> str:
    """Return a prompt by name, optionally substituting ``{placeholder}`` markers."""
    raw = _read(name)
    if not substitutions:
        return raw
    # Only format strings that actually contain ``{`` to keep JSON snippets safe.
    if "{" not in raw:
        return raw
    return raw.format(**substitutions)


def available() -> list[str]:
    """Enumerate prompt names currently on disk (useful for tests + admin)."""
    return sorted(p.stem for p in _PROMPTS_DIR.glob("*.md"))


def reload() -> None:
    """Clear the read cache — used in tests after writing new prompt files."""
    _read.cache_clear()
