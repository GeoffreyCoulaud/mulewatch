"""Enfant d'analyse jetable (spec analysis §4 — DA5/DA8), côté ENFANT.

``main`` : revalide le hash canonique (défense en profondeur anti-traversal, DA8), lit AU PLUS
``cfg.header_bytes`` octets du fichier RO (PAS tout le fichier — contenu hostile potentiellement
énorme, DA10), exécute ``pipeline.run`` (type_sniff sur l'en-tête + ffprobe sur le chemin), imprime
``json.dumps({"verdict","real_meta","checks"})`` sur stdout, rend 0. Hash non canonique / argv
absent → rend 2 sans égress. Fichier absent/illisible (disparu après le ``is_file`` du parent) →
égress VALIDE ``suspicious`` (poison, cohérent DA6). Aucune stack en égress (best-effort).

Ce module est exécuté par re-exec (``python -m download_verifier.analysis_child <hash>``) ; le
parent (``spawn.py``) le confine (rlimits/setsid/env minimal). En PROD l'``__main__`` lit la config
depuis l'env minimal et utilise le ``ProdFfprobeRunner`` réel.
"""

import json
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from download_verifier import pipeline
from download_verifier.checks.ffprobe import FfprobeRunner, ProdFfprobeRunner
from download_verifier.config import AnalysisConfig

_CANONICAL_HASH_RE = re.compile(r"[0-9a-f]{32}\Z")


def main(
    argv: Sequence[str],
    *,
    ffprobe_runner: FfprobeRunner | None = None,
    cfg: AnalysisConfig | None = None,
) -> int:
    """Analyse ``quarantine/<argv[0]>`` et imprime l'égress JSON ; rend le code de sortie."""
    config = cfg if cfg is not None else AnalysisConfig.from_env(os.environ)
    runner = ffprobe_runner if ffprobe_runner is not None else ProdFfprobeRunner(config.timeout_s)
    if len(argv) != 1 or _CANONICAL_HASH_RE.fullmatch(argv[0]) is None:
        return 2
    path = Path(config.quarantine_dir) / argv[0]
    try:
        with path.open("rb") as handle:
            header = handle.read(config.header_bytes)
    except OSError:
        _emit("suspicious", {}, [])
        return 0
    verdict, real_meta, checks = pipeline.run(header, path, runner, config)
    _emit(verdict, real_meta, checks)
    return 0


def _emit(verdict: str, real_meta: dict[str, object], checks: list[dict[str, object]]) -> None:
    sys.stdout.write(json.dumps({"verdict": verdict, "real_meta": real_meta, "checks": checks}))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
