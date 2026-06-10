#!/usr/bin/env bash
# Setup dev : active les hooks Git du repo + installe l'environnement.
set -euo pipefail

git config core.hooksPath .githooks
uv sync --dev
echo "Environnement dev prêt. Hooks Git activés (core.hooksPath=.githooks)."
