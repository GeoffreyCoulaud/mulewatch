"""Couture service-side de l'analyse (spec analysis §3/§6 — DA6).

``verify_file`` est la couture STABLE que ``app.py`` appelle (signature inchangée) : elle vérifie
l'EXISTENCE du fichier en quarantaine (``is_file`` — métadonnée seulement, le parent ne lit JAMAIS
les octets, DA8), puis spawne l'enfant d'analyse jetable (``spawn.run_analysis``) qui exécute les
checks et imprime un égress parsé défensivement (``egress.parse``). Mapping (DA6, toujours 200) :
fichier absent / non-régulier → ``("error", {}, [])`` ; sinon le verdict réel de l'enfant
(``clean``/``suspicious``/``malicious``, ou ``suspicious`` si l'enfant timeout/crashe/égresse mal).

``cfg``/``runner`` sont injectables (tests) ; les défauts sont la config d'env + le
``ProdChildRunner`` réel. ``expected`` reste minimal et non décisif (DA2 ; le pipeline ne
l'exploite pas en D-analysis).
"""

import os
from collections.abc import Mapping
from pathlib import Path

from download_verifier import spawn
from download_verifier.config import AnalysisConfig
from download_verifier.spawn import ChildRunner, ProdChildRunner

_VERDICT_ERROR = "error"


def verify_file(
    quarantine_path: Path,
    expected: Mapping[str, object],
    *,
    cfg: AnalysisConfig | None = None,
    runner: ChildRunner | None = None,
) -> tuple[str, dict[str, object], list[object]]:
    """Vérifie un fichier en quarantaine. Rend ``(verdict, real_meta, checks)`` (DA6)."""
    config = cfg if cfg is not None else AnalysisConfig.from_env(os.environ)
    child_runner = runner if runner is not None else ProdChildRunner(config)
    if not quarantine_path.is_file():
        return _VERDICT_ERROR, {}, []
    return spawn.run_analysis(quarantine_path.name, config, child_runner)
