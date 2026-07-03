# syntax=docker/dockerfile:1
# --- Builder : installe catalog-webui (membre du workspace VIRTUEL) via uv ---
FROM ghcr.io/astral-sh/uv:python3.14-alpine@sha256:e852e37cfaffb287f2d78de0d4f27e28bf0722ccbf0d6948dbdf19a0b4de7bc7 AS builder

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
    --mount=type=bind,source=packages/matching/pyproject.toml,target=packages/matching/pyproject.toml \
    --mount=type=bind,source=packages/webui/pyproject.toml,target=packages/webui/pyproject.toml \
    uv sync --locked --no-install-workspace --package catalog-webui

# Sources + sync du SEUL membre webui, non-editable (venv autonome, sans les sources).
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --package catalog-webui

# --- Runtime : python alpine, pas de dependance binaire (ni ffmpeg ni clamav), non-root ---
FROM python:3.14-alpine@sha256:26730869004e2b9c4b9ad09cab8625e81d256d1ce97e72df5520e806b1709f92

# User non-root (999:999) — composera avec le durcissement compose (user:, read_only).
# Busybox (Alpine) : addgroup/adduser au lieu de groupadd/useradd (-h reproduit --create-home).
# Alpine occupe deja GID 999 (groupe `ping`, inutile ici) → on le retire pour liberer 999:999.
RUN delgroup ping 2>/dev/null; addgroup -S -g 999 nonroot \
    && adduser -S -u 999 -G nonroot -h /home/nonroot nonroot

COPY --from=builder --chown=nonroot:nonroot /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH"
USER nonroot
WORKDIR /app
ENTRYPOINT ["python", "-m", "catalog_webui"]
