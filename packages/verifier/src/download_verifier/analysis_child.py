"""Enfant d'analyse jetable (spec analysis §4 — DA5/DA8), côté ENFANT.

``main`` : revalide le hash canonique (défense en profondeur anti-traversal, DA8), lit AU PLUS
``cfg.header_bytes`` octets du fichier RO (PAS tout le fichier — contenu hostile potentiellement
énorme, DA10), exécute ``pipeline.run`` (type_sniff sur l'en-tête + ffprobe/clamav sur le chemin),
imprime
``json.dumps({"verdict","real_meta","checks"})`` sur stdout, rend 0. Hash non canonique / argv
absent → rend 2 sans égress. Fichier absent/illisible (disparu après le ``is_file`` du parent) →
égress VALIDE ``suspicious`` (poison, cohérent DA6). Aucune stack en égress (best-effort).

Ce module est exécuté par re-exec (``python -m download_verifier.analysis_child <hash>``) ; le
parent (``spawn.py``) le confine (rlimits/setsid/env minimal). En PROD l'``__main__`` lit la config
depuis l'env minimal et utilise les ``ProdFfprobeRunner``/``ProdClamavRunner`` réels. Le RING NOYAU
(filtre seccomp-bpf, ``confine.py``) est posé APRÈS la lecture de l'en-tête RO et JUSTE AVANT
``pipeline.run`` (le ``Confiner`` est injectable ; défaut = ``ProdConfiner`` si seccomp activé).
"""

import errno
import json
import os
import re
import stat
import sys
from collections.abc import Sequence
from pathlib import Path

from download_verifier import pipeline
from download_verifier.checks.clamav import ClamavRunner, ProdClamavRunner
from download_verifier.checks.ffprobe import FfprobeRunner, ProdFfprobeRunner
from download_verifier.config import AnalysisConfig
from download_verifier.confine import Confiner, NoopConfiner, ProdConfiner

_CANONICAL_HASH_RE = re.compile(r"[0-9a-f]{32}\Z")


def _default_confiner(config: AnalysisConfig) -> Confiner:
    """Sélectionne le ``Confiner`` selon la config — RETOURNE l'instance (sans l'appeler)."""
    return ProdConfiner() if config.seccomp_enabled else NoopConfiner()


def _read_header_no_follow(path: Path, header_bytes: int) -> bytes:
    """Lit ``header_bytes`` octets du fichier en REFUSANT symlink et types non réguliers.

    Sandbox-confinement#4 : ``O_NOFOLLOW`` rejette un symlink (lève ``ELOOP``) ; ``fstat +
    S_ISREG`` rejette tout autre type (dir, FIFO, socket, périphérique) — la vérification se
    fait sur le ``fd`` (pas via le chemin) donc immunisée TOCTOU. Defense-en-profondeur : un
    amuled compromis partageant la quarantaine en RW pourrait y déposer un symlink ENTRE le
    ``S_ISREG`` parent (``check.py``) et l'open ici. Lève ``OSError`` sur tout refus ; appelée
    sous un ``try/except OSError`` qui map à un égress ``suspicious``.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(errno.EINVAL, "fichier de quarantaine non régulier")
        with os.fdopen(fd, "rb", closefd=True) as handle:
            fd = -1  # ownership transférée à fdopen (le ``with`` ferme via __exit__)
            return handle.read(header_bytes)
    finally:
        if fd != -1:
            os.close(fd)


def main(
    argv: Sequence[str],
    *,
    ffprobe_runner: FfprobeRunner | None = None,
    clamav_runner: ClamavRunner | None = None,
    cfg: AnalysisConfig | None = None,
    confiner: Confiner | None = None,
) -> int:
    """Analyse ``quarantine/<argv[0]>`` et imprime l'égress JSON ; rend le code de sortie."""
    config = cfg if cfg is not None else AnalysisConfig.from_env(os.environ)
    runner = ffprobe_runner if ffprobe_runner is not None else ProdFfprobeRunner(config.timeout_s)
    clamav = clamav_runner if clamav_runner is not None else ProdClamavRunner(config.timeout_s)
    confine = confiner if confiner is not None else _default_confiner(config)
    if len(argv) != 1 or _CANONICAL_HASH_RE.fullmatch(argv[0]) is None:
        return 2
    path = Path(config.quarantine_dir) / argv[0]
    try:
        header = _read_header_no_follow(path, config.header_bytes)
    except OSError:
        _emit("suspicious", {}, [])
        return 0
    confine()  # RING NOYAU : pose le filtre seccomp ICI (après lecture RO, avant pipeline.run, §7).
    verdict, real_meta, checks = pipeline.run(header, path, runner, clamav, config)
    _emit(verdict, real_meta, checks)
    return 0


def _emit(verdict: str, real_meta: dict[str, object], checks: list[dict[str, object]]) -> None:
    sys.stdout.write(json.dumps({"verdict": verdict, "real_meta": real_meta, "checks": checks}))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
