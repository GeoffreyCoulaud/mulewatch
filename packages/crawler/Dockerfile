# syntax=docker/dockerfile:1
# --- Builder : installe emule-indexer (membre du workspace VIRTUEL) via uv ---
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=packages/crawler/pyproject.toml,target=packages/crawler/pyproject.toml \
    --mount=type=bind,source=packages/verifier/pyproject.toml,target=packages/verifier/pyproject.toml \
    uv sync --locked --no-install-workspace --package emule-indexer

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --package emule-indexer

# --- Runtime : python slim, non-root, venv copie ---
FROM python:3.12-slim-bookworm

# User non-root (999:999) — composera avec le durcissement compose (user:, read_only).
RUN groupadd --system --gid 999 nonroot \
    && useradd --system --gid 999 --uid 999 --create-home nonroot

# Points de montage des volumes nommes (catalog-db/local-db/quarantine) : les creer possedes
# par nonroot pour qu'un volume nomme VIDE herite de la propriete 999:999 au premier mount
# (le crawler tourne en read_only + non-root et doit pouvoir creer ses bases SQLite).
RUN mkdir -p /data/catalog /data/local /data/quarantine \
    && chown -R nonroot:nonroot /data

COPY --from=builder --chown=nonroot:nonroot /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH"
USER nonroot
WORKDIR /app
ENTRYPOINT ["python", "-m", "emule_indexer"]
