# syntax=docker/dockerfile:1

# Single source of truth for the runtime apk package list. The apk-plan probe AND the runtime
# install below both reference $APK_PACKAGES, so they cannot drift out of sync: it is one
# variable, not two lists to keep aligned. A shared cross-stage ARG must be declared before the
# first FROM (Docker rule), then re-declared without a value in each stage that uses it.
ARG APK_PACKAGES="ffmpeg clamav libseccomp"

# Product version, computed from the git tag on the CI runner and injected here. Shared across
# stages like APK_PACKAGES: declared before the first FROM, re-declared where used. The build
# context has no .git (.dockerignore), so hatch-vcs/setuptools-scm reads
# SETUPTOOLS_SCM_PRETEND_VERSION when building the member wheel; the same value stamps the OCI
# version label on the runtime stage.
ARG VERSION=0.0.0

# --- Builder : installe download-verifier (membre du workspace VIRTUEL) via uv ---
FROM ghcr.io/astral-sh/uv:python3.14-alpine@sha256:e852e37cfaffb287f2d78de0d4f27e28bf0722ccbf0d6948dbdf19a0b4de7bc7 AS builder
ARG VERSION

ENV SETUPTOOLS_SCM_PRETEND_VERSION=${VERSION} \
    UV_COMPILE_BYTECODE=1 \
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
    uv sync --frozen --no-install-workspace --package download-verifier

# Sources + sync du SEUL membre verifier, non-editable (venv autonome, sans les sources).
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --package download-verifier

# --- apk-plan: fingerprint the LIVE apk resolution of $APK_PACKAGES WITHOUT installing anything.
#     APK_PROBE_NONCE is passed unique per build (CI: the run id) so this cheap stage always
#     re-runs and re-resolves the live Alpine index; --simulate --no-cache fetches only the index,
#     no packages. The /apk-plan.sha256 it writes then gates the real `apk add` below: that
#     expensive layer is re-run ONLY when the resolved version set changed (an upstream security
#     fix, a new transitive dep, ...). This replaces the old manual `clamav>=X` cache-bust floor:
#     freshness is now automatic, no per-CVE version bump to maintain. pipefail makes the build
#     fail loudly if a package vanishes or apk's output format drifts, rather than silently
#     freezing the fingerprint (which would resurrect the stale-cache bug, invisibly).
#     This base digest MUST stay in sync with the runtime base below: both resolve $APK_PACKAGES
#     against the same Alpine release, so the fingerprint reflects what the runtime installs. (It
#     is a literal, not a shared ARG/stage alias, because the vex_guards source scan checks the
#     last FROM textually for "alpine" and any indirection would defeat that guard.)
FROM python:3.14-alpine@sha256:26730869004e2b9c4b9ad09cab8625e81d256d1ce97e72df5520e806b1709f92 AS apk-plan
ARG APK_PACKAGES
ARG APK_PROBE_NONCE
RUN set -eo pipefail; \
    apk add --no-cache --simulate $APK_PACKAGES 2>&1 \
      | grep -i installing | sort | sha256sum > /apk-plan.sha256

# --- Runtime : ffmpeg (ffprobe) + clamav (clamscan) + libseccomp, non-root, venv copie ---
# Base digest kept in sync with the apk-plan probe above (same Alpine release, see that note).
FROM python:3.14-alpine@sha256:26730869004e2b9c4b9ad09cab8625e81d256d1ce97e72df5520e806b1709f92
ARG APK_PACKAGES
ARG VERSION
LABEL org.opencontainers.image.version=${VERSION}

# ffmpeg provides ffprobe (D-analysis); clamav provides clamscan (opt-in signature check);
# libseccomp provides libseccomp.so.2, loaded at runtime by pyseccomp (seccomp-bpf kernel ring).
# freshclam is NOT installed (apk adds no "recommends"): the signature database comes from a
# RO volume populated by the freshclam sidecar, so the verifier image stays network-free (internal).
# We purge /var/lib/clamav (the empty DB apk drops in): the real DB is mounted at /clamav-db.
# COPYing apk-plan's fingerprint right before this RUN is what busts the layer when the resolved
# versions change, so a plain rebuild picks up upstream fixes; we drop the fingerprint file after.
COPY --from=apk-plan /apk-plan.sha256 /apk-plan.sha256
RUN apk add --no-cache $APK_PACKAGES \
    && rm -rf /var/lib/clamav/* /apk-plan.sha256

# User non-root (999:999): composera avec le durcissement compose (user:, read_only).
# Busybox (Alpine) : addgroup/adduser au lieu de groupadd/useradd (-h reproduit --create-home).
# Alpine occupe deja GID 999 (groupe `ping`, inutile ici) → on le retire pour liberer 999:999.
RUN delgroup ping 2>/dev/null; addgroup -S -g 999 nonroot \
    && adduser -S -u 999 -G nonroot -h /home/nonroot nonroot

COPY --from=builder --chown=nonroot:nonroot /app/.venv /app/.venv

# Environment shim (musl): a sitecustomize.py on PYTHONPATH makes ctypes.util.find_library("seccomp")
# resolve libseccomp.so.2 on Alpine, so pyseccomp loads in the confined child. Kept out of the app
# code on purpose: see the file's docstring and download_verifier/confine.py.
COPY packages/verifier/docker/sitecustomize.py /opt/shims/sitecustomize.py

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/opt/shims"
USER nonroot
WORKDIR /app
ENTRYPOINT ["python", "-m", "download_verifier"]
