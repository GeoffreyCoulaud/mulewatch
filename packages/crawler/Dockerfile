# syntax=docker/dockerfile:1
# --- Builder : installe mulewatch (membre du workspace VIRTUEL) via uv ---
FROM ghcr.io/astral-sh/uv:python3.14-alpine@sha256:e852e37cfaffb287f2d78de0d4f27e28bf0722ccbf0d6948dbdf19a0b4de7bc7 AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Deps layer: install straight from uv.lock, independent of the sources. --frozen skips lock
# revalidation, so it needs only uv.lock + the root pyproject.toml, not each member's
# pyproject.toml (the vex_guards tooling package is likewise never installed). The --locked
# sync below, after COPY . /app, revalidates the lock against every workspace member.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-workspace --package mulewatch

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --package mulewatch

# --- Runtime : python alpine, non-root, venv copie ---
FROM python:3.14-alpine@sha256:26730869004e2b9c4b9ad09cab8625e81d256d1ce97e72df5520e806b1709f92

# User non-root (999:999) — composera avec le durcissement compose (user:, read_only).
# Busybox (Alpine) : addgroup/adduser au lieu de groupadd/useradd (-h reproduit --create-home).
# Alpine occupe deja GID 999 (groupe `ping`, inutile ici) → on le retire pour liberer 999:999.
RUN delgroup ping 2>/dev/null; addgroup -S -g 999 nonroot \
    && adduser -S -u 999 -G nonroot -h /home/nonroot nonroot

# Points de montage des volumes nommes (catalog-db/local-db/quarantine) : les creer possedes
# par nonroot pour qu'un volume nomme VIDE herite de la propriete 999:999 au premier mount
# (le crawler tourne en read_only + non-root et doit pouvoir creer ses bases SQLite).
RUN mkdir -p /data/catalog /data/local /data/quarantine \
    && chown -R nonroot:nonroot /data

COPY --from=builder --chown=nonroot:nonroot /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH"
USER nonroot
WORKDIR /app
ENTRYPOINT ["python", "-m", "mulewatch"]
