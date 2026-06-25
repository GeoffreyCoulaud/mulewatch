"""Couture service-side de l'analyse (spec analysis §3/§6 — DA6).

``verify_file`` est la couture STABLE que ``app.py`` appelle (signature inchangée) : elle vérifie
l'EXISTENCE du fichier en quarantaine (``is_file`` — métadonnée seulement, le parent ne lit JAMAIS
les octets, DA8), puis spawne l'enfant d'analyse jetable (``spawn.run_analysis``) qui exécute les
checks et imprime un égress parsé défensivement (``egress.parse``). Mapping (DA6, toujours 200) :
fichier absent / non-régulier → ``("error", {}, [])`` ; sinon le verdict réel de l'enfant
(``clean``/``suspicious``/``malicious``, ou ``suspicious`` si l'enfant timeout/crashe/égresse mal).

``cfg`` est REQUIS (résolu une fois au boot par ``app.build_app`` et injecté — plus de
résolution paresseuse par requête, cf. error-boundary#0) ; ``runner`` est injectable (tests),
son défaut est le ``ProdChildRunner`` réel. ``expected`` reste minimal et non décisif (DA2 ;
le pipeline ne l'exploite pas en D-analysis).
"""

import os
import stat
from collections.abc import Mapping
from pathlib import Path

from download_verifier import spawn
from download_verifier.config import AnalysisConfig
from download_verifier.spawn import ChildRunner, ProdChildRunner

_VERDICT_ERROR = "error"


def _is_regular_file_no_follow(path: Path) -> bool:
    """``True`` si ``path`` est un fichier RÉGULIER (PAS un symlink, ni un répertoire, ni un FIFO).

    Sandbox-confinement#4 : ``Path.is_file()`` suit les symlinks. Un amuled compromis partageant
    la quarantaine en RW pourrait y déposer un symlink nommé comme un hash hex valide → ``is_file``
    rendrait True et le verifier ouvrirait le symlink en suivant la cible. On bascule sur
    ``os.lstat + S_ISREG`` : un symlink est explicitement REJETÉ (lstat ne suit pas), et tout
    type non régulier (FIFO, socket, périphérique, répertoire) aussi.
    """
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode)


def verify_file(
    quarantine_path: Path,
    expected: Mapping[str, object],
    *,
    cfg: AnalysisConfig,
    runner: ChildRunner | None = None,
) -> tuple[str, dict[str, object], list[object]]:
    """Vérifie un fichier en quarantaine. Rend ``(verdict, real_meta, checks)`` (DA6)."""
    child_runner = runner if runner is not None else ProdChildRunner(cfg)
    if not _is_regular_file_no_follow(quarantine_path):
        return _VERDICT_ERROR, {}, []
    return spawn.run_analysis(quarantine_path.name, cfg, child_runner)
