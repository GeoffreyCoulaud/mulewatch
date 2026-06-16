# syntax=docker/dockerfile:1
# --- Builder : installe download-verifier (membre du workspace VIRTUEL) via uv ---
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Couche deps : sync SANS le projet/workspace (cache des deps independant des sources).
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=packages/crawler/pyproject.toml,target=packages/crawler/pyproject.toml \
    --mount=type=bind,source=packages/verifier/pyproject.toml,target=packages/verifier/pyproject.toml \
    uv sync --locked --no-install-workspace --package download-verifier

# Sources + sync du SEUL membre verifier, non-editable (venv autonome, sans les sources).
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --package download-verifier

# --- Runtime : python slim + ffmpeg (ffprobe) + clamav (clamscan), non-root, venv copie ---
FROM python:3.12-slim-bookworm

# ffmpeg fournit ffprobe (D-analysis) ; clamav fournit clamscan (check par signatures, opt-in).
# freshclam N'EST PAS installé (--no-install-recommends l'évite) : la base de signatures vient d'un
# volume RO peuplé par le sidecar freshclam → l'image verifier reste sans accès réseau (internal).
# On purge /var/lib/clamav (base vide déposée par apt) : la base réelle est montée sur /clamav-db.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg clamav \
    && rm -rf /var/lib/clamav/* /var/lib/apt/lists/*

# User non-root (999:999) — composera avec le durcissement compose (user:, read_only).
RUN groupadd --system --gid 999 nonroot \
    && useradd --system --gid 999 --uid 999 --create-home nonroot

COPY --from=builder --chown=nonroot:nonroot /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH"
USER nonroot
WORKDIR /app
ENTRYPOINT ["python", "-m", "download_verifier"]
