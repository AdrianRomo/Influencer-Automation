FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libxml2-dev \
    libxslt1-dev \
    zlib1g-dev \
  && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

# Force uv to use system python and NEVER download a managed python
ENV UV_PYTHON=/usr/local/bin/python
ENV UV_PYTHON_PREFERENCE=only-system
ENV UV_PYTHON_DOWNLOADS=never

# ✅ Put the project venv OUTSIDE /app so your bind mount doesn't hide it
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

COPY . /app

ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONPATH="/app"
ENV PYTHONUNBUFFERED=1
