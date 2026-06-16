"""Check ``clamav`` (spec clamav §3) : 3ᵉ source de verdict, par SIGNATURES virales.

``scan`` invoque ``clamscan`` via un ``ClamavRunner`` INJECTABLE (prod = subprocess réel ; tests =
``(rc, stdout)`` canné) avec des flags FIGÉS. ``clamscan`` encode son verdict dans son CODE DE
SORTIE : ``0`` → aucun virus (``clean``), ``1`` → virus trouvé (``malicious``), ``≥2`` → erreur
(base absente/corrompue, I/O…) → ``suspicious`` (défensif : on ne peut pas affirmer « sûr » sans
base, on ne jette pas le fichier non plus). Sur un match, ``_parse_signature`` extrait AU MIEUX le
nom de la signature pour ``meta`` (purement informatif — le verdict ``malicious`` est inchangé).
``clamav`` tourne dans l'enfant confiné comme ``ffprobe`` (base RO locale + fichier local, pas de
réseau) ; un ``clamscan`` qui boucle/excède les rlimits est tué par le parent et donne
``suspicious`` via l'égress. ``error`` n'est JAMAIS un statut de check (réservé service-level).
"""

import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from download_verifier.checks.base import CheckOutcome
from download_verifier.config import AnalysisConfig


class ClamavRunner(Protocol):
    """Exécute clamscan et rend ``(returncode, stdout)``. Injecté pour les tests."""

    def __call__(self, argv: Sequence[str]) -> tuple[int, bytes]: ...


class ProdClamavRunner:
    """``ClamavRunner`` de PROD : vrai ``subprocess.run`` (couvert par analysis_integration)."""

    def __init__(self, timeout_s: float) -> None:
        self._timeout_s = timeout_s

    def __call__(self, argv: Sequence[str]) -> tuple[int, bytes]:  # pragma: no cover
        completed = subprocess.run(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=self._timeout_s,
            check=False,
        )
        return completed.returncode, completed.stdout


def scan(path: Path, runner: ClamavRunner, cfg: AnalysisConfig) -> CheckOutcome:
    """Scanne ``path`` via ``runner`` ; rend ``CheckOutcome`` (status + meta)."""
    argv = [
        cfg.clamscan_path,
        "--no-summary",
        "--stdout",
        "--database",
        cfg.clamav_db_dir,
        str(path),
    ]
    returncode, stdout = runner(argv)
    if returncode == 0:
        return CheckOutcome(name="clamav", status="clean", meta={})
    if returncode == 1:
        signature = _parse_signature(stdout)
        meta: dict[str, object] = {}
        if signature is not None:
            meta["clamav_signature"] = signature
        return CheckOutcome(name="clamav", status="malicious", meta=meta)
    # rc >= 2 (ou tout autre) : erreur clamscan (base absente/corrompue, I/O…) → défensif.
    return CheckOutcome(name="clamav", status="suspicious", meta={})


def _parse_signature(stdout: bytes) -> str | None:
    """Extrait AU MIEUX le nom de signature d'une ligne ``<file>: <sig> FOUND`` ; sinon ``None``."""
    for line in stdout.decode("utf-8", "replace").splitlines():
        if line.endswith(" FOUND") and ": " in line:
            return line.rsplit(": ", 1)[1].removesuffix(" FOUND").strip() or None
    return None
