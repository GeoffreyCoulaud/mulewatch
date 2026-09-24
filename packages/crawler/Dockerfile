# syntax=docker/dockerfile:1

# --- aMule stage: build aMule 3.1.0 from a pinned nixpkgs, export its runtime closure ---
FROM nixos/nix:2.35.2@sha256:7a007c766426c1877758ddc5cb87a965ac131fc78c582ce0083d922d51ae945c AS amule

COPY packages/crawler/docker/amule.nix /build/amule.nix

# Only the store paths are exported: nix itself never reaches the final image. Syft reads the
# store path names, so the SBOM still lists every component of the closure with its version.
# The compile happens once per nixpkgs bump; everything else comes from the binary cache.
RUN nix-build /build/amule.nix -o /amule \
    && mkdir -p /closure/nix/store /closure/bin \
    && nix-store -qR "$(readlink -f /amule)" | xargs -r cp -a -t /closure/nix/store \
    && for binary in amuled amuleapi ed2k; do \
         ln -s "$(readlink -f /amule)/bin/$binary" "/closure/bin/$binary"; \
       done

# Keep the stage: Dependabot only reads FROM lines, so inlining it as a COPY --from=<image>
# would freeze this digest with no update path.
FROM ghcr.io/astral-sh/uv:0.12.18@sha256:3adc3706091ce7c2fe595e669628caedd6d951551b92b258b7e7dbe06d9440bc AS uv

# --- Python stage: build /app/.venv against Debian's python3, the same one the runtime holds ---
FROM debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a AS python

# Product version, computed from the git tag on the CI runner and injected here. The build
# context has no .git (.dockerignore excludes it), so hatch-vcs/setuptools-scm reads
# SETUPTOOLS_SCM_PRETEND_VERSION instead when the member wheel is built below. The same value
# stamps org.opencontainers.image.version on the runtime stage.
ARG VERSION=0.0.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${VERSION} \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON=/usr/bin/python3 \
    UV_PYTHON_DOWNLOADS=0

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /usr/local/bin/uv

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

# --- Runtime: one image, two supervised services (amuled itself starts amuleapi) ---
FROM debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a

# ARG after FROM only lives in the stage that declares it, hence the re-declaration.
ARG VERSION=0.0.0
LABEL org.opencontainers.image.version=${VERSION}

# s6 comes from Debian, not from the s6-overlay tarball, so dpkg knows about it and the
# security feed covers it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 s6 \
    && rm -rf /var/lib/apt/lists/*

# The aMule closure carries its own glibc, so it runs on Debian untouched.
COPY --from=amule /closure/nix/store/ /nix/store/
COPY --from=amule /closure/bin/ /usr/local/bin/

COPY --from=python /app/.venv /app/.venv
COPY packages/crawler/docker/entrypoint.sh packages/crawler/docker/amule-config.py /usr/local/bin/
COPY packages/crawler/docker/services.d/ /etc/services.d/

ENV PATH="/app/.venv/bin:$PATH"
WORKDIR /app
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
