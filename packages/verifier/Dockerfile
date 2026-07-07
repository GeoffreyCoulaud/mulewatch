# syntax=docker/dockerfile:1
# --- Builder : installe download-verifier (membre du workspace VIRTUEL) via uv ---
FROM ghcr.io/astral-sh/uv:python3.14-alpine@sha256:e852e37cfaffb287f2d78de0d4f27e28bf0722ccbf0d6948dbdf19a0b4de7bc7 AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Couche deps : sync SANS le projet/workspace (cache des deps independant des sources).
# The vex_guards tooling package is never installed (it is not named in any uv sync
# --package line). This bind-mount only exists so uv sync --locked can see every
# workspace member's pyproject.toml, which it requires. Dropping it in favor of
# uv sync --frozen is a tracked follow-up.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=packages/crawler/pyproject.toml,target=packages/crawler/pyproject.toml \
    --mount=type=bind,source=packages/verifier/pyproject.toml,target=packages/verifier/pyproject.toml \
    --mount=type=bind,source=packages/matching/pyproject.toml,target=packages/matching/pyproject.toml \
    --mount=type=bind,source=packages/vex_guards/pyproject.toml,target=packages/vex_guards/pyproject.toml \
    uv sync --locked --no-install-workspace --package download-verifier

# Sources + sync du SEUL membre verifier, non-editable (venv autonome, sans les sources).
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --package download-verifier

# --- Runtime : python alpine + ffmpeg (ffprobe) + clamav (clamscan), non-root, venv copie ---
FROM python:3.14-alpine@sha256:26730869004e2b9c4b9ad09cab8625e81d256d1ce97e72df5520e806b1709f92

# ffmpeg fournit ffprobe (D-analysis) ; clamav fournit clamscan (check par signatures, opt-in) ;
# libseccomp fournit libseccomp.so.2 chargée au runtime par pyseccomp (ring noyau seccomp-bpf).
# freshclam N'EST PAS installé (apk n'ajoute pas de "recommends") : la base de signatures vient
# d'un volume RO peuplé par le sidecar freshclam → l'image verifier reste sans accès réseau (internal).
# On purge /var/lib/clamav (base vide déposée par apk) : la base réelle est montée sur /clamav-db.
RUN apk add --no-cache ffmpeg clamav libseccomp \
    && rm -rf /var/lib/clamav/*

# User non-root (999:999) — composera avec le durcissement compose (user:, read_only).
# Busybox (Alpine) : addgroup/adduser au lieu de groupadd/useradd (-h reproduit --create-home).
# Alpine occupe deja GID 999 (groupe `ping`, inutile ici) → on le retire pour liberer 999:999.
RUN delgroup ping 2>/dev/null; addgroup -S -g 999 nonroot \
    && adduser -S -u 999 -G nonroot -h /home/nonroot nonroot

COPY --from=builder --chown=nonroot:nonroot /app/.venv /app/.venv

# Environment shim (musl): a sitecustomize.py on PYTHONPATH makes ctypes.util.find_library("seccomp")
# resolve libseccomp.so.2 on Alpine, so pyseccomp loads in the confined child. Kept out of the app
# code on purpose — see the file's docstring and download_verifier/confine.py.
COPY packages/verifier/docker/sitecustomize.py /opt/shims/sitecustomize.py

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/opt/shims"
USER nonroot
WORKDIR /app
ENTRYPOINT ["python", "-m", "download_verifier"]
