# D-analysis (vrai verifier) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remplacer le verifier NO-OP par un **vrai analyseur**, ENTIÈREMENT dans `packages/verifier`, **sans changer le contrat de fil** (`{verdict, real_meta, checks}`) ni toucher au crawler. À chaque `POST /verify`, le service spawne un **enfant d'analyse jetable** (`python -m download_verifier.analysis_child <hash>`) qui ouvre `quarantine/<hash>` en lecture seule, exécute les checks activés (`type_sniff` via puremagic + `ffprobe` binaire), agrège leur **worst-status** en un verdict (`clean < suspicious < malicious`), remplit `real_meta` (durée/bitrate/codec — le trou qu'EC ne comble jamais), imprime un JSON sur stdout et meurt. Le service parse cet égress **défensivement** (borné, schéma strict, enum) et répond `{verdict, real_meta, checks}`. Le NO-OP de `check.verify_file` ne bascule sur le vrai pipeline **qu'en avant-dernière tâche**, pour que l'e2e `verify_integration` (côté crawler) reste vert tout du long. Spec : `docs/superpowers/specs/2026-06-14-analysis-design.md`.

**Architecture:** Clean/Hexagonal, **dans le seul `packages/verifier`**. Le verifier a deux faces : **le service** (process parent : HTTP, spawn, parse d'égress) et **l'enfant d'analyse** (process jetable : lit les octets, exécute les checks, imprime un JSON, meurt). La **logique pure et testable** (`pipeline`, `checks/`, `egress`) est isolée du **spawn réel** (`spawn`, l'appel `subprocess.run` réel de l'enfant, l'appel `subprocess.run` réel de `ffprobe`). Le seul code à effet de bord système (l'appel subprocess nu, `preexec_fn=_confine`, `if __name__ == "__main__":`) porte `# pragma: no cover` et est couvert par le marqueur d'intégration `analysis_integration` (spawn réel + vrai ffprobe, désélectionné par défaut, exclu de la coverage). Toute la logique de décision (mapping stdout/timeout/exitcode → verdict, lecture bornée à `egress_cap`, cycle de vie du tmpdir, agrégation, parsing défensif) est unit-testée avec des **runners injectables** (`FfprobeRunner`, `ChildRunner`). **Le crawler est INTOUCHÉ** : `record_verification` stocke déjà `verdict` (string) + `real_meta`/`checks` (JSON) sans interpréter ; aucune logique crawler ne branche sur la valeur du verdict ; `expected` reste minimal (DA2). La frontière de paquet est stable : `download_verifier` n'importe JAMAIS `emule_indexer` (et inversement) — seuls le test de contrat + l'e2e la traversent, côté test.

**Tech Stack:** Python ≥ 3.12 (`subprocess`, `resource`, `os`, `tempfile`, `shutil`, `json`, `sys` stdlib). **Nouvelle dépendance paquet** : `puremagic>=1.28` (pip, pur-Python) dans `packages/verifier/pyproject.toml`. `ffprobe` = **binaire système** (PAS une dépendance pip ; requis pour le marqueur d'intégration en dev + l'e2e après bascule ; image du Plan F). Stack existante du verifier inchangée (`starlette`/`uvicorn`). `ruff` (E/F/I/UP/B/SIM, line 100), `mypy --strict` (src + tests), `pytest` + `pytest-asyncio` (mode `strict`) + `pytest-cov` (gate **100 % branch par paquet**). Déterminisme TOTAL en unitaire : `FfprobeRunner` et `ChildRunner` injectables ; aucun subprocess réel dans le run par défaut ; `tmp_path` pour les fichiers ; le subprocess/`preexec_fn` réel derrière `# pragma: no cover` + `analysis_integration`.

> **Référence spec :** `docs/superpowers/specs/2026-06-14-analysis-design.md` — §1 (but/périmètre), §2 (décisions DA1..DA10), §3 (structure paquet + modèle deux process), §4 (enfant & confinement), §5 (checks/`real_meta`/agrégation), §6 (flux & mapping verdict), §7 (erreurs), §8 (config), §9 (tests), §10 (modèle/contrat de fil — INCHANGÉS), §11 (hors-périmètre), §12 (risques). Plan précédent de référence (style/densité/format) : `docs/superpowers/plans/2026-06-13-crawler-mvp-07-verification-pipeline.md`. Handoff : `docs/handoffs/2026-06-14 - handoff - verification pipeline.md` (§3 = contrats stables, §4 = pièges appris).

> **HORS PÉRIMÈTRE (spec §1/§11 — RIEN de tout ceci ici) :** **Ring noyau** (`net=none` namespace, non-root, seccomp, bwrap/nsjail/gVisor, montages RO, vrai tmpfs) → **Plan F (packaging)** ; ici l'isolation vient du process + rlimits + de l'absence de réseau dans le code. **clamav** (source `malicious` par signatures) → **follow-up OBLIGATOIRE après Plan F** (tension `freshclam` egress vs `internal: true`) ; un créneau est réservé dans le registre mais NON implémenté. **Dédup** des lignes `file_verifications` dupliquées (artefact at-least-once) → future surface de lecture/export (inexistante). **Alerte** `malicious` → Plan E. **Promotion** humaine → hors-scope permanent. **Windows** non supporté (`preexec_fn`/`resource`/`setsid` Linux). **Aucune modif crawler** (ni port, ni boucle, ni config, ni DTO).

---

## Décisions verrouillées (spec §2 — DA1..DA10, ne PAS relitiger)

> **DA1 — Confinement portable maintenant, ring noyau au Plan F.** D-analysis livre l'enfant jetable « léger » : subprocess re-exec + rlimits (`resource.setrlimit`) + timeout-kill du groupe (`os.setsid`/`os.killpg`) + cwd tmpdir jetable + fichier RO + code sans réseau. Le `net=none`/non-root/seccomp/bwrap/gVisor arrive avec le container Plan F.

> **DA2 — Verdict = axe SÛRETÉ/cohérence**, pas identité. `real_meta` = enrichissement honnête. `expected` reste **minimal et non décisif** → le verifier demeure stateless, sans dépendance au domaine crawler → **aucune modif crawler**. (L'enfant reçoit l'`expected` côté service mais le pipeline NE l'exploite pas pour décider en D-analysis ; il le passe inerte — l'exploitation réelle est un follow-up.)

> **DA3 — Checks = `type_sniff` (puremagic) + `ffprobe` (binaire).** Registre branchable (activable par config) + agrégation worst-status. `clamav` = créneau réservé NON implémenté.

> **DA4 — clamav = follow-up OBLIGATOIRE après Plan F.** Pas dans ce plan. Le registre laisse la place ; aucune fonction `clamav` n'est écrite.

> **DA5 — Un enfant d'analyse Python unique par fichier** (re-exec subprocess, PAS `os.fork`). Tous les octets hostiles sont lus DANS l'enfant jetable, jamais dans le process service. Runners injectés pour les tests (DA9).

> **DA6 — Mapping des échecs déterministe, toujours en 200.** Un fichier qui résiste à l'analyse n'est PAS une panne de service. Fichier absent/non-régulier → `error`. Enfant exit ≠ 0 / timeout / OOM-killé / égress illisible ou hors-schéma → `suspicious` (signal de poison). Enfant OK + égress valide → worst-status des checks. Le verifier ne lève JAMAIS vers le client pour un fichier problématique (le `VerifierUnavailableError` est côté crawler, HORS de ce plan). Table en Task 7/8.

> **DA7 — `type_sniff` = détection de danger ABSOLU** (pas de comparaison à l'extension déclarée, hostile). Conteneur média → `clean` ; **exécutable/script (ELF, PE/MZ, Mach-O, shebang `#!`) → `malicious`** ; archive (zip/rar/7z) → `suspicious` ; inconnu/non concluant → `clean` (ffprobe tranche). `malicious` est atteignable dès D-analysis. `sniffed_type` est mis dans `real_meta` dans tous les cas.

> **DA8 — Enfant le plus vierge possible.** `close_fds=True` (le parent n'OUVRE jamais le fichier — `is_file()` métadonnée seulement ; c'est l'enfant qui ouvre RO → aucun fd passé) ; **environnement explicite minimal** (on n'hérite PAS de `os.environ` — secrets/VPN ; on ne passe que `QUARANTINE_DIR`, `enabled_checks`, `ffprobe_path` absolu, au plus un `PATH` minimal). Entrée = argv (hash) + env minimal ; sortie = stdout/exit. **Revalidation du hash canonique dans l'enfant** (défense en profondeur anti-traversal).

> **DA9 — 100 % branch via runners injectés ; subprocess réel derrière un marqueur.** Toute la logique pure (sniff, parse ffprobe → `real_meta`, agrégation, parse égress, mapping) est unit-testée sans subprocess. Le seul `subprocess.run` réel (impl prod du `ChildRunner` + de la PROD `FfprobeRunner`) + `preexec_fn=_confine` + l'appel `os.killpg`/`os.setsid`/`setrlimit` réels sont `# pragma: no cover`, couverts par `analysis_integration`.

> **DA10 — Défauts config raffinables, flags ffprobe figés au plan.** `timeout_s` 30 ; `rlimit_cpu_s` ~20 ; `rlimit_as_bytes` ~512 Mio ; `rlimit_nproc`/`rlimit_nofile` modestes ; `rlimit_fsize_bytes` borné ; `egress_cap_bytes` 65536 ; `header_bytes` 4096 ; tous overridables par env. **Flags ffprobe FIGÉS (context7/doc ffprobe, re-confirmés) :** `[cfg.ffprobe_path, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]`. **API puremagic FIGÉE (context7) :** `puremagic.from_string(data: bytes, mime: bool = False, filename: str | None = None) -> str` (lève `puremagic.PureError` si rien ne matche) ; `puremagic.magic_string(data: bytes, ...) -> list` (matches avec `.extension`/`.mime_type`/`.confidence` ; peut être vide). On lit SEULEMENT `cfg.header_bytes` octets du fichier (PAS tout le fichier — contenu hostile potentiellement énorme).

> **Le gate (par paquet, VERBATIM dans chaque step « Vérifier ») :**
> ```bash
> ( cd packages/verifier && uv run pytest -q )          # verifier tests, 100 % branch
> ( cd packages/crawler  && uv run pytest -q )          # crawler tests, 100 % branch (INCHANGÉ)
> uv run ruff check .
> uv run ruff format --check .
> uv run mypy
> uv run sqlfluff lint packages/crawler/src
> ```
> **Run focalisé (coverage off)** : `( cd packages/verifier && uv run pytest tests/<fichier>.py::<test> --no-cov -q )`. **Run dédié de l'intégration** : `( cd packages/verifier && uv run pytest -m analysis_integration --no-cov -q )`. Laisser ruff trancher l'ordre des imports (`uv run ruff check . --fix && uv run ruff format .`) AVANT le gate. **Chaque** `git commit` se termine par le trailer HEREDOC `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

> **Note ordonnancement & convention de run :** chaque tâche = test(s) qui échoue(nt) → run/échec attendu → impl minimale → run/pass → **gate** → commit conventionnel. La bascule de `check.verify_file` (du stat NO-OP au vrai pipeline) est en **Task 8** (avant l'intégration Task 9) : jusque-là, le verifier reste NO-OP et l'e2e `verify_integration` côté crawler reste vert SANS modification. **`app.py` n'est JAMAIS modifié** (la couture est `check.verify_file`, dont la signature ne change pas).

> **Note couverture (gate 100 % branch — points chauds, anticipés) :**
> - **Runners injectables** : protocoles `FfprobeRunner`/`ChildRunner` stub sur UNE ligne (`def __call__(...) -> ...: ...` couvert par le `def`). En PROD, l'impl de chaque runner fait le `subprocess.run` réel → la ligne d'appel subprocess porte `# pragma: no cover` (testée par `analysis_integration`).
> - **`type_sniff`** : chaque classe de la table DA7 (conteneur média / exécutable-script / archive / inconnu) a son test sur des octets canoniques + le cas `PureError` (octets inconnus → `clean`). Les DEUX branches de chaque conditionnel exercées.
> - **`ffprobe`** : runner injecté → JSON média valide (`clean` + `real_meta`) / exit ≠ 0 (`suspicious`) / JSON malformé (`suspicious`) / `streams` vide ou absent (`suspicious`) / streams sans audio-vidéo (`suspicious`) ; parsing défensif des strings numériques (`duration`/`bit_rate`/`size` parsables / non parsables / absentes) → champ omis vs présent.
> - **`pipeline`** : checks factices → tous `clean` / un `suspicious` / un `malicious` (worst-status) ; fusion `real_meta` ; trace `checks` ; sélection `enabled_checks` (check activé / désactivé).
> - **`egress`** : JSON valide / dépassant `egress_cap` / non-JSON / hors-schéma (clé manquante, mauvais type) / enum `verdict` invalide → mapping.
> - **`spawn`** : `ChildRunner` injecté → stdout OK / `subprocess.TimeoutExpired` / exit ≠ 0 / stdout surdimensionné ; cycle de vie tmpdir (créé / supprimé même en cas d'exception) ; env minimal NE contient PAS de var parasite ; argv correct.
> - **`analysis_child.main`** : hash valide → JSON imprimé + exit 0 ; hash non canonique → exit ≠ 0 ; fichier absent → JSON `error`-ish (DA6 le mappe parent-side, mais ici l'enfant produit un égress valide) ; `if __name__` sous pragma.
> - **`check.verify_file` (Task 8)** : `is_file()` False → `("error", {}, [])` ; True → `spawn.run_analysis` → `egress` → mapping. Les anciens tests NO-OP (`unverified`) deviennent caducs et sont REMPLACÉS par des tests du nouveau comportement (runner injecté), en conservant les cas `error` (absent/répertoire).
> - **`config`** : défauts / overrides env / valeur invalide (entier non parsable, `enabled_checks` vide) → `ConfigError`.

---

## File Structure

```
packages/verifier/
├── pyproject.toml                               # Modify : + dependency puremagic>=1.28 ; + marker analysis_integration (markers=[...] + addopts -m "not analysis_integration")
└── src/download_verifier/
    ├── __init__.py                              # INCHANGÉ (vide)
    ├── __main__.py                              # INCHANGÉ (uvicorn entry)
    ├── app.py                                   # INCHANGÉ (Starlette ; appelle check.verify_file)
    ├── check.py                                 # Modify (EN DERNIER, Task 8) : verify_file = is_file → spawn.run_analysis → egress.parse
    ├── config.py                                # Create (Task 1) : AnalysisConfig (frozen) + from_env()
    ├── pipeline.py                              # Create (Task 4, pur) : run(header, path, ffprobe_runner, cfg) → (verdict, real_meta, checks)
    ├── egress.py                                # Create (Task 5, parent) : parse(stdout, returncode, timed_out, cfg) → (verdict, real_meta, checks)
    ├── spawn.py                                 # Create (Task 6, parent) : run_analysis(path, cfg, runner) → (verdict, real_meta, checks)
    ├── analysis_child.py                        # Create (Task 7, enfant) : main(argv, *, ffprobe_runner=...) ; if __name__ sous pragma
    └── checks/
        ├── __init__.py                          # Create (Task 1, vide)
        ├── base.py                              # Create (Task 1) : Status (Literal), CheckOutcome (frozen), worst_status(), STATUS_RANK
        ├── type_sniff.py                        # Create (Task 2) : sniff(header, *, header_bytes) -> CheckOutcome
        └── ffprobe.py                           # Create (Task 3) : FfprobeRunner (Protocol), ProdFfprobeRunner, probe(path, runner, cfg) -> CheckOutcome
└── tests/                                       # PAS de tests/__init__.py (évite la collision de module au mypy racine — handoff §4)
    ├── test_package.py                          # INCHANGÉ
    ├── test_check.py                            # Modify (Task 8) : remplace les tests NO-OP par le comportement réel (runner injecté) ; conserve error
    ├── test_app.py                              # INCHANGÉ
    ├── test_main.py                             # INCHANGÉ
    ├── test_config.py                           # Create (Task 1)
    ├── test_checks_base.py                      # Create (Task 1)
    ├── test_type_sniff.py                       # Create (Task 2)
    ├── test_ffprobe.py                          # Create (Task 3)
    ├── test_pipeline.py                         # Create (Task 4)
    ├── test_egress.py                           # Create (Task 5)
    ├── test_spawn.py                            # Create (Task 6)
    ├── test_analysis_child.py                   # Create (Task 7)
    └── test_analysis_integration.py            # Create (Task 9) : pytestmark = analysis_integration (spawn réel + vrai ffprobe ; hors coverage)
```

> **Carte de dépendance (cohérence des signatures, vérifiée à l'écriture du plan) :**
> - `config.AnalysisConfig` (frozen) : `enabled_checks: tuple[str, ...]`, `ffprobe_path: str`, `timeout_s: float`, `rlimit_cpu_s: int`, `rlimit_as_bytes: int`, `rlimit_nproc: int`, `rlimit_nofile: int`, `rlimit_fsize_bytes: int`, `egress_cap_bytes: int`, `header_bytes: int`, `quarantine_dir: str`. `from_env(env: Mapping[str, str]) -> AnalysisConfig`.
> - `checks/base.py` : `Status = Literal["clean", "suspicious", "malicious"]` ; `STATUS_RANK: dict[Status, int]` ; `CheckOutcome(name: str, status: Status, meta: Mapping[str, object])` (frozen) ; `worst_status(statuses: Iterable[Status]) -> Status`.
> - `checks/type_sniff.py` : `sniff(header: bytes) -> CheckOutcome` (name=`"type_sniff"`).
> - `checks/ffprobe.py` : `FfprobeRunner` (Protocol : `def __call__(self, argv: Sequence[str]) -> tuple[int, bytes]: ...`) ; `ProdFfprobeRunner` (impl `subprocess.run`, `# pragma: no cover`) ; `probe(path: Path, runner: FfprobeRunner, cfg: AnalysisConfig) -> CheckOutcome` (name=`"ffprobe"`).
> - `pipeline.py` : `run(header: bytes, path: Path, ffprobe_runner: FfprobeRunner, cfg: AnalysisConfig) -> tuple[str, dict[str, object], list[dict[str, object]]]` (verdict, real_meta, checks).
> - `egress.py` : `parse(stdout: bytes, returncode: int, timed_out: bool, cfg: AnalysisConfig) -> tuple[str, dict[str, object], list[object]]`.
> - `spawn.py` : `ChildRunner` (Protocol : `def __call__(self, argv: Sequence[str], *, cwd: str, env: Mapping[str, str], timeout: float) -> tuple[int, bytes, bool]: ...`) ; `ProdChildRunner` (impl `subprocess.run`, `# pragma: no cover`) ; `run_analysis(ed2k_hash: str, cfg: AnalysisConfig, runner: ChildRunner) -> tuple[str, dict[str, object], list[object]]`.
> - `analysis_child.py` : `main(argv: Sequence[str], *, ffprobe_runner: FfprobeRunner | None = None, cfg: AnalysisConfig | None = None) -> int`.
> - `check.verify_file(quarantine_path: Path, expected: Mapping[str, object]) -> tuple[str, dict[str, object], list[object]]` (Task 8, signature INCHANGÉE).

---

(Les tâches numérotées suivent. Chaque tâche est autonome : write failing test → run fail → impl complète → run pass → gate → commit.)

---

## Task 1 : Scaffolding — dépendance `puremagic`, `config.py`, `checks/base.py`, marqueur `analysis_integration`

**Files :**
- Modify: `packages/verifier/pyproject.toml`
- Create: `packages/verifier/src/download_verifier/config.py`
- Create: `packages/verifier/src/download_verifier/checks/__init__.py`
- Create: `packages/verifier/src/download_verifier/checks/base.py`
- Create: `packages/verifier/tests/test_config.py`
- Create: `packages/verifier/tests/test_checks_base.py`

> Spec §3/§5/§8 + DA10. On pose les fondations PURES (config + modèle de check + agrégation worst-status) et on enregistre le marqueur d'intégration (déclaré sans test = inoffensif avec `--strict-markers`, l'addopts du verifier doit le désélectionner par défaut). Aucune logique de spawn/check ici. Gate vert (verifier + crawler intouché).

- [ ] **Step 0 : RE-confirmer l'API puremagic via context7 (avant d'ajouter la dep)**

`puremagic` (≥1.28) expose `from_string(data: bytes, mime: bool = False, filename: str | None = None) -> str` (lève `puremagic.PureError` si rien ne matche ; import : `from puremagic import PureError`) et `magic_string(data: bytes, filename: str | None = None) -> list` (matches avec `.extension`/`.mime_type`/`.confidence` ; liste potentiellement vide). Re-confirmer via context7 (`/cdgriffith/puremagic`) que ces signatures sont stables sur la version résolue par `uv lock`. (Vérifié à l'écriture du plan : stable.) Si une signature a bougé, adapter Task 2 en conséquence (ne PAS deviner).

- [ ] **Step 1 : Ajouter `puremagic` + le marqueur `analysis_integration` à `pyproject.toml`**

**Lire d'abord** `packages/verifier/pyproject.toml`. Ajouter `puremagic>=1.28` aux `dependencies`, et enregistrer le marqueur `analysis_integration` (miroir EXACT du motif du crawler : `markers=[...]` + `addopts ... -m "not analysis_integration"`). Le bloc `[project]` devient :
```toml
[project]
name = "download-verifier"
version = "0.0.0"
description = "Service de vérification des fichiers en quarantaine — analyseur confiné (déployable séparé)"
requires-python = ">=3.12"
dependencies = [
    "starlette>=1.3",
    "uvicorn>=0.30",
    "puremagic>=1.28",
]
```
et le bloc `[tool.pytest.ini_options]` devient :
```toml
[tool.pytest.ini_options]
addopts = '--cov=download_verifier --cov-report=term-missing --cov-fail-under=100 --strict-markers -m "not analysis_integration"'
testpaths = ["tests"]
markers = [
    "analysis_integration: spawn réel de l'enfant + vrai ffprobe sur de vrais échantillons (ffmpeg/ffprobe requis) — déselectionnés par défaut ; run dédié : cd packages/verifier && uv run pytest -m analysis_integration --no-cov",
]
asyncio_mode = "strict"
asyncio_default_fixture_loop_scope = "function"
```
(Le reste du fichier — `[build-system]`, `[tool.hatch...]`, `[tool.coverage...]` — INCHANGÉ.)

- [ ] **Step 2 : régénérer le lockfile + sync**

```bash
uv lock
uv sync --dev
```
Expected : `uv.lock` régénéré (un seul, racine ; `puremagic` résolu) ; `uv sync` l'installe dans le venv racine partagé.

- [ ] **Step 3 : Écrire les tests qui échouent — `config.py` + `checks/base.py`**

`packages/verifier/tests/test_config.py` :
```python
import pytest

from download_verifier.config import AnalysisConfig


def test_from_env_uses_defaults_when_empty() -> None:
    cfg = AnalysisConfig.from_env({})
    assert cfg.enabled_checks == ("type_sniff", "ffprobe")
    assert cfg.ffprobe_path == "ffprobe"
    assert cfg.timeout_s == 30.0
    assert cfg.rlimit_cpu_s == 20
    assert cfg.rlimit_as_bytes == 512 * 1024 * 1024
    assert cfg.egress_cap_bytes == 65536
    assert cfg.header_bytes == 4096
    assert cfg.quarantine_dir == "/quarantine"


def test_from_env_overrides_each_field() -> None:
    cfg = AnalysisConfig.from_env(
        {
            "ENABLED_CHECKS": "type_sniff",
            "FFPROBE_PATH": "/usr/bin/ffprobe",
            "ANALYSIS_TIMEOUT_S": "12.5",
            "RLIMIT_CPU_S": "9",
            "RLIMIT_AS_BYTES": "1048576",
            "RLIMIT_NPROC": "7",
            "RLIMIT_NOFILE": "33",
            "RLIMIT_FSIZE_BYTES": "2048",
            "EGRESS_CAP_BYTES": "4096",
            "HEADER_BYTES": "512",
            "QUARANTINE_DIR": "/data/quarantine",
        }
    )
    assert cfg.enabled_checks == ("type_sniff",)
    assert cfg.ffprobe_path == "/usr/bin/ffprobe"
    assert cfg.timeout_s == 12.5
    assert cfg.rlimit_cpu_s == 9
    assert cfg.rlimit_as_bytes == 1048576
    assert cfg.rlimit_nproc == 7
    assert cfg.rlimit_nofile == 33
    assert cfg.rlimit_fsize_bytes == 2048
    assert cfg.egress_cap_bytes == 4096
    assert cfg.header_bytes == 512
    assert cfg.quarantine_dir == "/data/quarantine"


def test_enabled_checks_splits_and_strips() -> None:
    cfg = AnalysisConfig.from_env({"ENABLED_CHECKS": " type_sniff , ffprobe "})
    assert cfg.enabled_checks == ("type_sniff", "ffprobe")


def test_from_env_rejects_empty_enabled_checks() -> None:
    with pytest.raises(ValueError):
        AnalysisConfig.from_env({"ENABLED_CHECKS": "  ,  "})


def test_from_env_rejects_unparsable_int() -> None:
    with pytest.raises(ValueError):
        AnalysisConfig.from_env({"RLIMIT_CPU_S": "not-an-int"})


def test_from_env_rejects_unparsable_float() -> None:
    with pytest.raises(ValueError):
        AnalysisConfig.from_env({"ANALYSIS_TIMEOUT_S": "soon"})


def test_config_is_frozen() -> None:
    cfg = AnalysisConfig.from_env({})
    with pytest.raises(AttributeError):
        cfg.timeout_s = 1.0  # type: ignore[misc]
```

`packages/verifier/tests/test_checks_base.py` :
```python
import dataclasses

import pytest

from download_verifier.checks.base import (
    STATUS_RANK,
    CheckOutcome,
    worst_status,
)


def test_status_rank_orders_clean_below_suspicious_below_malicious() -> None:
    assert STATUS_RANK["clean"] < STATUS_RANK["suspicious"] < STATUS_RANK["malicious"]


def test_check_outcome_is_frozen() -> None:
    outcome = CheckOutcome(name="type_sniff", status="clean", meta={})
    with pytest.raises(dataclasses.FrozenInstanceError):
        outcome.status = "malicious"  # type: ignore[misc]


def test_check_outcome_carries_name_status_meta() -> None:
    outcome = CheckOutcome(name="ffprobe", status="suspicious", meta={"container": "mkv"})
    assert outcome.name == "ffprobe"
    assert outcome.status == "suspicious"
    assert outcome.meta == {"container": "mkv"}


def test_worst_status_all_clean_is_clean() -> None:
    assert worst_status(["clean", "clean"]) == "clean"


def test_worst_status_picks_suspicious_over_clean() -> None:
    assert worst_status(["clean", "suspicious", "clean"]) == "suspicious"


def test_worst_status_picks_malicious_over_all() -> None:
    assert worst_status(["clean", "suspicious", "malicious"]) == "malicious"


def test_worst_status_empty_is_clean() -> None:
    # aucun check exécuté → rien de dangereux constaté → clean (le mapping service-level
    # traite séparément l'enfant en échec ; ici c'est l'agrégation pure d'une liste vide).
    assert worst_status([]) == "clean"
```

- [ ] **Step 4 : Lancer pour vérifier l'échec**

Run : `( cd packages/verifier && uv run pytest tests/test_config.py tests/test_checks_base.py -q --no-cov )`
Expected : FAIL — `ModuleNotFoundError: No module named 'download_verifier.config'` (puis `download_verifier.checks`).

- [ ] **Step 5 : Écrire `config.py`**

`packages/verifier/src/download_verifier/config.py` :
```python
"""Config de l'analyseur (spec analysis §8 — DA10).

``AnalysisConfig`` (frozen) lue depuis l'environnement par ``from_env`` : checks activés,
chemin ffprobe, timeout, rlimits, cap d'égress, taille d'en-tête sniffée, dossier de
quarantaine. Le PARENT (service) l'utilise pour les rlimits/timeout/env minimal du spawn ;
l'ENFANT relit la part « checks » (``enabled_checks``/``ffprobe_path``/``header_bytes``) depuis
l'env minimal que le parent lui passe. Défauts raffinables ; valeurs invalides → ``ValueError``
(fail-fast au démarrage du service). Côté crawler : aucune config nouvelle.
"""

from collections.abc import Mapping
from dataclasses import dataclass

_DEFAULT_ENABLED = ("type_sniff", "ffprobe")
_DEFAULT_QUARANTINE = "/quarantine"


@dataclass(frozen=True, slots=True)
class AnalysisConfig:
    """Paramètres figés de l'analyseur (un seul objet partagé parent/enfant via l'env)."""

    enabled_checks: tuple[str, ...]
    ffprobe_path: str
    timeout_s: float
    rlimit_cpu_s: int
    rlimit_as_bytes: int
    rlimit_nproc: int
    rlimit_nofile: int
    rlimit_fsize_bytes: int
    egress_cap_bytes: int
    header_bytes: int
    quarantine_dir: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "AnalysisConfig":
        """Construit la config depuis ``env``. Valeur non parsable / liste vide → ``ValueError``."""
        return cls(
            enabled_checks=_parse_checks(env.get("ENABLED_CHECKS")),
            ffprobe_path=env.get("FFPROBE_PATH", "ffprobe"),
            timeout_s=_parse_float(env.get("ANALYSIS_TIMEOUT_S"), 30.0),
            rlimit_cpu_s=_parse_int(env.get("RLIMIT_CPU_S"), 20),
            rlimit_as_bytes=_parse_int(env.get("RLIMIT_AS_BYTES"), 512 * 1024 * 1024),
            rlimit_nproc=_parse_int(env.get("RLIMIT_NPROC"), 64),
            rlimit_nofile=_parse_int(env.get("RLIMIT_NOFILE"), 64),
            rlimit_fsize_bytes=_parse_int(env.get("RLIMIT_FSIZE_BYTES"), 16 * 1024 * 1024),
            egress_cap_bytes=_parse_int(env.get("EGRESS_CAP_BYTES"), 65536),
            header_bytes=_parse_int(env.get("HEADER_BYTES"), 4096),
            quarantine_dir=env.get("QUARANTINE_DIR", _DEFAULT_QUARANTINE),
        )


def _parse_checks(raw: str | None) -> tuple[str, ...]:
    if raw is None:
        return _DEFAULT_ENABLED
    checks = tuple(item.strip() for item in raw.split(",") if item.strip())
    if not checks:
        raise ValueError("ENABLED_CHECKS ne doit pas être vide")
    return checks


def _parse_int(raw: str | None, default: int) -> int:
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"entier attendu, reçu {raw!r}") from exc


def _parse_float(raw: str | None, default: float) -> float:
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"flottant attendu, reçu {raw!r}") from exc
```

- [ ] **Step 6 : Écrire `checks/__init__.py` (vide) + `checks/base.py`**

`packages/verifier/src/download_verifier/checks/__init__.py` : fichier VIDE.

`packages/verifier/src/download_verifier/checks/base.py` :
```python
"""Modèle de check & agrégation worst-status (spec analysis §5).

Chaque check rend un ``CheckOutcome(name, status, meta)`` avec ``status`` dans
``clean < suspicious < malicious``. Le verdict du fichier = worst-status sur la liste des
statuts. ``error`` n'est PAS un statut de check (c'est un résultat service-level, §6) — il
n'apparaît jamais ici.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

Status = Literal["clean", "suspicious", "malicious"]

# Ordre de gravité : un check plus grave écrase un check moins grave (worst-status).
STATUS_RANK: dict[Status, int] = {"clean": 0, "suspicious": 1, "malicious": 2}
_RANK_TO_STATUS: dict[int, Status] = {rank: status for status, rank in STATUS_RANK.items()}


@dataclass(frozen=True, slots=True)
class CheckOutcome:
    """Résultat d'un check : son nom, son verdict de gravité, et son apport à ``real_meta``."""

    name: str
    status: Status
    meta: Mapping[str, object]


def worst_status(statuses: Iterable[Status]) -> Status:
    """Statut le plus grave de ``statuses`` ; liste vide → ``clean`` (rien de dangereux vu)."""
    return _RANK_TO_STATUS[max((STATUS_RANK[status] for status in statuses), default=0)]
```

- [ ] **Step 7 : Vérifier puis gate**

Run : `( cd packages/verifier && uv run pytest tests/test_config.py tests/test_checks_base.py -q --no-cov )` → PASS.
Run : gate complet (verifier 100 % branch + crawler INCHANGÉ + ruff/format/mypy/sqlfluff).

> **Note couverture :** `_parse_checks` (None→défaut / non-vide→tuple / vide→ValueError) ; `_parse_int` (None→défaut / parsable / non parsable→ValueError) ; `_parse_float` (idem) ; `worst_status` (vide→clean / non-vide→max) ; `CheckOutcome` frozen. Chaque branche testée ci-dessus.

- [ ] **Step 8 : Commit**

```bash
git add packages/verifier/pyproject.toml packages/verifier/src/download_verifier/config.py packages/verifier/src/download_verifier/checks/__init__.py packages/verifier/src/download_verifier/checks/base.py packages/verifier/tests/test_config.py packages/verifier/tests/test_checks_base.py uv.lock
git commit -m "$(cat <<'EOF'
feat(verifier): scaffolding analyse — AnalysisConfig + CheckOutcome/worst_status + dep puremagic + marqueur analysis_integration

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2 : `checks/type_sniff.py` (puremagic — danger absolu, DA7)

**Files :**
- Create: `packages/verifier/src/download_verifier/checks/type_sniff.py`
- Create: `packages/verifier/tests/test_type_sniff.py`

> Spec §5 + DA7. `sniff(header: bytes) -> CheckOutcome` : sniffe les octets d'en-tête (le PARENT/ENFANT lui passe déjà au plus `header_bytes` octets — lecture bornée), classe le danger ABSOLU. Conteneur média → `clean` ; exécutable/script (ELF, PE/MZ, Mach-O, shebang `#!`) → `malicious` ; archive (zip/rar/7z) → `suspicious` ; inconnu / `PureError` → `clean` (ffprobe tranchera). `sniffed_type` (le mime détecté, ou `None`) est mis dans `meta` dans TOUS les cas. La classification s'appuie sur le MIME/extension de puremagic + une garde explicite sur les magiques d'exécutables (puremagic peut ne pas tous les couvrir — le shebang notamment).

- [ ] **Step 1 : Écrire le test qui échoue**

`packages/verifier/tests/test_type_sniff.py` :
```python
from download_verifier.checks.type_sniff import sniff


def test_elf_binary_is_malicious() -> None:
    outcome = sniff(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 64)
    assert outcome.name == "type_sniff"
    assert outcome.status == "malicious"


def test_pe_mz_executable_is_malicious() -> None:
    outcome = sniff(b"MZ\x90\x00" + b"\x00" * 64)
    assert outcome.status == "malicious"


def test_macho_executable_is_malicious() -> None:
    # Mach-O 64-bit little-endian magic 0xCFFAEDFE.
    outcome = sniff(b"\xcf\xfa\xed\xfe" + b"\x00" * 64)
    assert outcome.status == "malicious"


def test_shebang_script_is_malicious() -> None:
    outcome = sniff(b"#!/bin/sh\necho pwned\n")
    assert outcome.status == "malicious"


def test_zip_archive_is_suspicious() -> None:
    outcome = sniff(b"PK\x03\x04" + b"\x00" * 64)
    assert outcome.status == "suspicious"


def test_rar_archive_is_suspicious() -> None:
    outcome = sniff(b"Rar!\x1a\x07\x00" + b"\x00" * 64)
    assert outcome.status == "suspicious"


def test_7z_archive_is_suspicious() -> None:
    outcome = sniff(b"7z\xbc\xaf\x27\x1c" + b"\x00" * 64)
    assert outcome.status == "suspicious"


def test_matroska_container_is_clean() -> None:
    outcome = sniff(b"\x1a\x45\xdf\xa3" + b"\x00" * 64)
    assert outcome.status == "clean"


def test_avi_container_is_clean() -> None:
    outcome = sniff(b"RIFF\x00\x00\x00\x00AVI LIST" + b"\x00" * 32)
    assert outcome.status == "clean"


def test_mp4_container_is_clean() -> None:
    outcome = sniff(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 32)
    assert outcome.status == "clean"


def test_plain_text_is_clean() -> None:
    outcome = sniff(b"juste du texte lambda, pas un media\n")
    assert outcome.status == "clean"


def test_unknown_bytes_are_clean_via_pure_error() -> None:
    # octets non concluants : puremagic lève PureError → clean (ffprobe tranchera).
    outcome = sniff(b"\x00\x01\x02")
    assert outcome.status == "clean"
    assert outcome.meta["sniffed_type"] is None


def test_meta_carries_sniffed_type_when_known() -> None:
    outcome = sniff(b"\x1a\x45\xdf\xa3" + b"\x00" * 64)
    assert isinstance(outcome.meta["sniffed_type"], str)
```

- [ ] **Step 2 : Lancer pour vérifier l'échec**

Run : `( cd packages/verifier && uv run pytest tests/test_type_sniff.py -q --no-cov )`
Expected : FAIL — `ModuleNotFoundError: No module named 'download_verifier.checks.type_sniff'`.

- [ ] **Step 3 : Écrire l'implémentation**

`packages/verifier/src/download_verifier/checks/type_sniff.py` :
```python
"""Check ``type_sniff`` (spec analysis §5 — DA7) : détection de DANGER ABSOLU.

On sniffe les premiers octets (le caller passe déjà au plus ``header_bytes``) sans jamais
comparer à l'extension déclarée (le nom eD2k est hostile). Classement :
- conteneur média connu → ``clean`` ;
- exécutable / script (ELF, PE/MZ, Mach-O, shebang ``#!``) → ``malicious`` (une vidéo qui est
  en fait un binaire est une tromperie délibérée) ;
- archive (zip/rar/7z…) → ``suspicious`` (plausible, mais pas une vidéo) ;
- inconnu / non concluant → ``clean`` (ffprobe tranchera).
``sniffed_type`` (le mime détecté ou ``None``) va dans ``meta`` dans tous les cas.
"""

import puremagic
from puremagic import PureError

from download_verifier.checks.base import CheckOutcome, Status

# Magiques d'exécutables/scripts (defense-in-depth : on ne dépend pas du seul mime puremagic,
# le shebang en particulier n'a pas de magique binaire fiable).
_EXECUTABLE_MAGICS: tuple[bytes, ...] = (
    b"\x7fELF",  # ELF (Linux/BSD)
    b"MZ",  # PE/COFF (Windows .exe/.dll)
    b"\xfe\xed\xfa\xce",  # Mach-O 32-bit big-endian
    b"\xfe\xed\xfa\xcf",  # Mach-O 64-bit big-endian
    b"\xce\xfa\xed\xfe",  # Mach-O 32-bit little-endian
    b"\xcf\xfa\xed\xfe",  # Mach-O 64-bit little-endian
    b"\xca\xfe\xba\xbe",  # Mach-O universal (fat) binary
    b"#!",  # shebang (script)
)

# Mimes/extensions d'archives (plausible mais pas un média).
_ARCHIVE_MARKERS: tuple[str, ...] = (
    "zip",
    "x-rar",
    "rar",
    "x-7z",
    "7z",
    "x-tar",
    "gzip",
    "x-bzip",
    "x-xz",
)

# Mimes de conteneurs/flux média.
_MEDIA_PREFIXES: tuple[str, ...] = ("video/", "audio/")
_MEDIA_MARKERS: tuple[str, ...] = ("matroska", "mp4", "quicktime", "mpeg", "ogg", "webm", "x-msvideo")


def sniff(header: bytes) -> CheckOutcome:
    """Sniffe ``header`` et classe son danger absolu (spec §5/DA7)."""
    if _looks_executable(header):
        return CheckOutcome(name="type_sniff", status="malicious", meta={"sniffed_type": None})
    try:
        mime = puremagic.from_string(header, mime=True)
    except PureError:
        return CheckOutcome(name="type_sniff", status="clean", meta={"sniffed_type": None})
    status = _classify(mime)
    return CheckOutcome(name="type_sniff", status=status, meta={"sniffed_type": mime})


def _looks_executable(header: bytes) -> bool:
    return header.startswith(_EXECUTABLE_MAGICS)


def _classify(mime: str) -> Status:
    lowered = mime.lower()
    if lowered.startswith(_MEDIA_PREFIXES) or any(m in lowered for m in _MEDIA_MARKERS):
        return "clean"
    if any(marker in lowered for marker in _ARCHIVE_MARKERS):
        return "suspicious"
    if "application/x-" in lowered and "executable" in lowered:
        return "malicious"
    return "clean"
```

> **Note d'implémentation :** `bytes.startswith(tuple_of_bytes)` accepte un tuple → couvre tous les magiques d'exécutables en un test. Le shebang `#!` est dans `_EXECUTABLE_MAGICS`, donc capté AVANT puremagic (qui ne le détecte pas comme « exécutable »). La branche `"application/x-…executable"` capte un exécutable que puremagic identifierait par mime mais dont la magique n'est pas dans `_EXECUTABLE_MAGICS` (defense-in-depth ; les tests ELF/MZ/Mach-O passent déjà par `_looks_executable`, mais cette branche reste atteignable — voir Note couverture).

- [ ] **Step 4 : Vérifier puis gate**

Run : `( cd packages/verifier && uv run pytest tests/test_type_sniff.py -q --no-cov )` → PASS.
Run : gate complet.

> **Note couverture (CHAUDE) :** `_looks_executable` True (ELF/MZ/Mach-O/shebang) / False (média/archive/texte/inconnu). `puremagic.from_string` lève `PureError` (`test_unknown_bytes_are_clean_via_pure_error`) / rend un mime (les autres). `_classify` : média (préfixe `video/`/`audio/` OU marqueur) → clean ; archive → suspicious ; `application/x-…executable` → malicious ; sinon → clean (texte). **Risque** : la branche `"application/x-…executable"` de `_classify` n'est PAS atteinte par les magiques ELF/MZ (captés par `_looks_executable` avant). **Si la coverage la signale manquante**, AJOUTER un test qui force puremagic à rendre un tel mime sans magique listée — concrètement, vérifier d'abord sur la version installée quel échantillon d'octets puremagic mappe vers un mime `executable` non listé ; si AUCUN, RETIRER cette branche (elle serait morte) et documenter que les exécutables passent tous par `_looks_executable`. **Trancher au moment de l'impl en exécutant `python -c "import puremagic; print(puremagic.from_string(<octets>, mime=True))"` sur quelques en-têtes** — ne PAS laisser une branche morte. (Le test `test_plain_text_is_clean` couvre déjà le `return "clean"` final.)

- [ ] **Step 5 : Commit**

```bash
git add packages/verifier/src/download_verifier/checks/type_sniff.py packages/verifier/tests/test_type_sniff.py
git commit -m "$(cat <<'EOF'
feat(verifier): check type_sniff (puremagic) — danger absolu (exécutable→malicious, archive→suspicious, média→clean)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3 : `checks/ffprobe.py` (Runner injecté → `real_meta` + status)

**Files :**
- Create: `packages/verifier/src/download_verifier/checks/ffprobe.py`
- Create: `packages/verifier/tests/test_ffprobe.py`

> Spec §5 + DA10. `FfprobeRunner` (Protocol injectable : `__call__(argv) -> (returncode, stdout)`). `ProdFfprobeRunner` fait le `subprocess.run` réel (`# pragma: no cover`, testé par `analysis_integration`). `probe(path, runner, cfg) -> CheckOutcome` : invoque ffprobe (flags figés), parse le JSON DÉFENSIVEMENT (`.get(...)`, jamais d'accès direct ; `duration`/`bit_rate`/`size`/`sample_rate`/`avg_frame_rate` sont des STRINGS ; `width`/`height`/`channels`/`nb_streams` des ints). Status : exit ≠ 0 OU JSON vide/illisible OU `streams` vide/absent OU aucun stream audio/vidéo → `suspicious` ; sinon → `clean` + `real_meta`. Champs absents/non parsables → OMIS de `real_meta`.

- [ ] **Step 1 : Écrire le test qui échoue**

`packages/verifier/tests/test_ffprobe.py` :
```python
import json
from collections.abc import Sequence
from pathlib import Path

from download_verifier.checks.ffprobe import FfprobeRunner, probe
from download_verifier.config import AnalysisConfig

_CFG = AnalysisConfig.from_env({})

_VALID = {
    "streams": [
        {
            "index": 0,
            "codec_type": "video",
            "codec_name": "h264",
            "width": 720,
            "height": 576,
            "avg_frame_rate": "25/1",
            "tags": {"language": "fre"},
        },
        {
            "index": 1,
            "codec_type": "audio",
            "codec_name": "aac",
            "channels": 2,
            "sample_rate": "48000",
            "tags": {"language": "fre"},
        },
    ],
    "format": {
        "filename": "x",
        "nb_streams": 2,
        "format_name": "matroska,webm",
        "duration": "1294.500000",
        "size": "242884608",
        "bit_rate": "1500000",
        "tags": {"title": "t"},
    },
}


class _StubRunner:
    """FfprobeRunner injecté : rend un (returncode, stdout) canné, capture l'argv."""

    def __init__(self, returncode: int, stdout: bytes) -> None:
        self._returncode = returncode
        self._stdout = stdout
        self.calls: list[Sequence[str]] = []

    def __call__(self, argv: Sequence[str]) -> tuple[int, bytes]:
        self.calls.append(argv)
        return self._returncode, self._stdout


def _run(runner: FfprobeRunner, path: Path = Path("/quarantine/abc")) -> object:
    return probe(path, runner, _CFG)


def test_valid_media_is_clean_with_real_meta() -> None:
    runner = _StubRunner(0, json.dumps(_VALID).encode())
    outcome = probe(Path("/q/f"), runner, _CFG)
    assert outcome.name == "ffprobe"
    assert outcome.status == "clean"
    assert outcome.meta["container"] == "matroska,webm"
    assert outcome.meta["duration_s"] == 1294.5
    assert outcome.meta["bit_rate"] == 1500000
    assert outcome.meta["size_bytes"] == 242884608
    assert outcome.meta["video"] == {
        "codec": "h264",
        "width": 720,
        "height": 576,
        "frame_rate": "25/1",
    }
    assert outcome.meta["audio"] == [
        {"codec": "aac", "channels": 2, "sample_rate": 48000, "language": "fre"}
    ]


def test_argv_uses_frozen_flags_and_path() -> None:
    runner = _StubRunner(0, json.dumps(_VALID).encode())
    probe(Path("/quarantine/abc"), runner, _CFG)
    assert runner.calls[0] == [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "/quarantine/abc",
    ]


def test_nonzero_exit_is_suspicious() -> None:
    outcome = probe(Path("/q/f"), _StubRunner(1, b""), _CFG)
    assert outcome.status == "suspicious"


def test_malformed_json_is_suspicious() -> None:
    outcome = probe(Path("/q/f"), _StubRunner(0, b"{not json"), _CFG)
    assert outcome.status == "suspicious"


def test_empty_stdout_is_suspicious() -> None:
    outcome = probe(Path("/q/f"), _StubRunner(0, b""), _CFG)
    assert outcome.status == "suspicious"


def test_no_streams_key_is_suspicious() -> None:
    outcome = probe(Path("/q/f"), _StubRunner(0, json.dumps({"format": {}}).encode()), _CFG)
    assert outcome.status == "suspicious"


def test_empty_streams_is_suspicious() -> None:
    outcome = probe(Path("/q/f"), _StubRunner(0, json.dumps({"streams": []}).encode()), _CFG)
    assert outcome.status == "suspicious"


def test_streams_without_audio_or_video_is_suspicious() -> None:
    payload = {"streams": [{"codec_type": "subtitle", "codec_name": "srt"}], "format": {}}
    outcome = probe(Path("/q/f"), _StubRunner(0, json.dumps(payload).encode()), _CFG)
    assert outcome.status == "suspicious"


def test_video_only_is_clean() -> None:
    payload = {
        "streams": [{"codec_type": "video", "codec_name": "h264", "width": 320, "height": 240}],
        "format": {"format_name": "mp4"},
    }
    outcome = probe(Path("/q/f"), _StubRunner(0, json.dumps(payload).encode()), _CFG)
    assert outcome.status == "clean"
    assert outcome.meta["video"] == {"codec": "h264", "width": 320, "height": 240}
    assert "audio" not in outcome.meta


def test_audio_only_is_clean() -> None:
    payload = {
        "streams": [{"codec_type": "audio", "codec_name": "mp3", "channels": 2}],
        "format": {"format_name": "mp3"},
    }
    outcome = probe(Path("/q/f"), _StubRunner(0, json.dumps(payload).encode()), _CFG)
    assert outcome.status == "clean"
    assert outcome.meta["audio"] == [{"codec": "mp3", "channels": 2}]
    assert "video" not in outcome.meta


def test_unparsable_numeric_strings_are_omitted() -> None:
    payload = {
        "streams": [{"codec_type": "video", "codec_name": "h264"}],
        "format": {"format_name": "mkv", "duration": "N/A", "bit_rate": "", "size": "oops"},
    }
    outcome = probe(Path("/q/f"), _StubRunner(0, json.dumps(payload).encode()), _CFG)
    assert outcome.status == "clean"
    assert "duration_s" not in outcome.meta
    assert "bit_rate" not in outcome.meta
    assert "size_bytes" not in outcome.meta


def test_missing_format_keys_are_omitted() -> None:
    payload = {"streams": [{"codec_type": "video", "codec_name": "h264"}]}
    outcome = probe(Path("/q/f"), _StubRunner(0, json.dumps(payload).encode()), _CFG)
    assert outcome.status == "clean"
    assert "container" not in outcome.meta
    assert outcome.meta["video"] == {"codec": "h264"}


def test_non_object_json_is_suspicious() -> None:
    # JSON valide mais pas un objet (liste) → illisible → suspicious.
    outcome = probe(Path("/q/f"), _StubRunner(0, b"[1,2,3]"), _CFG)
    assert outcome.status == "suspicious"
```

- [ ] **Step 2 : Lancer pour vérifier l'échec**

Run : `( cd packages/verifier && uv run pytest tests/test_ffprobe.py -q --no-cov )`
Expected : FAIL — `ModuleNotFoundError: No module named 'download_verifier.checks.ffprobe'`.

- [ ] **Step 3 : Écrire l'implémentation**

`packages/verifier/src/download_verifier/checks/ffprobe.py` :
```python
"""Check ``ffprobe`` (spec analysis §5 — DA10) : cœur de ``real_meta``.

``probe`` invoque ffprobe via un ``FfprobeRunner`` INJECTABLE (prod = subprocess réel ; tests =
JSON canné) avec des flags FIGÉS, parse le JSON DÉFENSIVEMENT (``.get(...)`` partout ; les champs
numériques de ``format`` sont des STRINGS chez ffprobe → parse en float/int dans un try/except,
champ omis s'il manque/n'est pas parsable). Status : exit ≠ 0, JSON vide/illisible/non-objet,
``streams`` vide/absent, ou aucun flux audio/vidéo → ``suspicious`` (prétend être un média, n'en
est pas un) ; sinon ``clean`` + ``real_meta``. ``ffprobe`` tourne en petit-fils sous les
rlimits/timeout/groupe de l'enfant (spec §4/§12) — un ffprobe qui boucle est tué et donne
``suspicious``.
"""

import json
import subprocess  # noqa: S404 — appel maîtrisé, argv en liste, pas de shell ; ligne réelle pragma
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from download_verifier.checks.base import CheckOutcome
from download_verifier.config import AnalysisConfig

_MEDIA_STREAM_TYPES = frozenset({"video", "audio"})


class FfprobeRunner(Protocol):
    """Exécute ffprobe et rend ``(returncode, stdout)``. Injecté pour les tests."""

    def __call__(self, argv: Sequence[str]) -> tuple[int, bytes]: ...


class ProdFfprobeRunner:
    """``FfprobeRunner`` de PROD : vrai ``subprocess.run`` (couvert par analysis_integration)."""

    def __init__(self, timeout_s: float) -> None:
        self._timeout_s = timeout_s

    def __call__(self, argv: Sequence[str]) -> tuple[int, bytes]:  # pragma: no cover
        completed = subprocess.run(  # noqa: S603
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=self._timeout_s,
            check=False,
        )
        return completed.returncode, completed.stdout


def probe(path: Path, runner: FfprobeRunner, cfg: AnalysisConfig) -> CheckOutcome:
    """Sonde ``path`` via ``runner`` ; rend ``CheckOutcome`` (status + ``real_meta``)."""
    argv = [
        cfg.ffprobe_path,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    returncode, stdout = runner(argv)
    if returncode != 0:
        return _suspicious()
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return _suspicious()
    if not isinstance(payload, dict):
        return _suspicious()
    streams = payload.get("streams")
    if not isinstance(streams, list) or not streams:
        return _suspicious()
    typed = [s for s in streams if isinstance(s, dict)]
    if not any(s.get("codec_type") in _MEDIA_STREAM_TYPES for s in typed):
        return _suspicious()
    return CheckOutcome(name="ffprobe", status="clean", meta=_build_meta(payload, typed))


def _suspicious() -> CheckOutcome:
    return CheckOutcome(name="ffprobe", status="suspicious", meta={})


def _build_meta(payload: dict[str, object], streams: list[dict[str, object]]) -> dict[str, object]:
    meta: dict[str, object] = {}
    fmt = payload.get("format")
    if isinstance(fmt, dict):
        _put(meta, "container", _as_str(fmt.get("format_name")))
        _put(meta, "duration_s", _as_float(fmt.get("duration")))
        _put(meta, "bit_rate", _as_int(fmt.get("bit_rate")))
        _put(meta, "size_bytes", _as_int(fmt.get("size")))
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is not None:
        _put(meta, "video", _video_meta(video))
    audios = [_audio_meta(s) for s in streams if s.get("codec_type") == "audio"]
    if audios:
        meta["audio"] = audios
    return meta


def _video_meta(stream: dict[str, object]) -> dict[str, object]:
    out: dict[str, object] = {}
    _put(out, "codec", _as_str(stream.get("codec_name")))
    _put(out, "width", _as_plain_int(stream.get("width")))
    _put(out, "height", _as_plain_int(stream.get("height")))
    _put(out, "frame_rate", _as_str(stream.get("avg_frame_rate")))
    return out


def _audio_meta(stream: dict[str, object]) -> dict[str, object]:
    out: dict[str, object] = {}
    _put(out, "codec", _as_str(stream.get("codec_name")))
    _put(out, "channels", _as_plain_int(stream.get("channels")))
    _put(out, "sample_rate", _as_int(stream.get("sample_rate")))
    tags = stream.get("tags")
    if isinstance(tags, dict):
        _put(out, "language", _as_str(tags.get("language")))
    return out


def _put(meta: dict[str, object], key: str, value: object | None) -> None:
    if value is not None:
        meta[key] = value


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _as_plain_int(value: object) -> int | None:
    # ffprobe rend déjà ces champs (width/height/channels) comme des ints JSON.
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _as_int(value: object) -> int | None:
    # ffprobe rend duration/bit_rate/size/sample_rate comme des STRINGS → parse défensif.
    if not isinstance(value, str):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _as_float(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return float(value)
    except ValueError:
        return None
```

> **Note d'implémentation :** `# noqa: S404`/`S603` ne servent que si la config ruff activait `flake8-bandit` (S) — la sélection du projet est `E,F,I,UP,B,SIM`, donc ces `noqa` sont INUTILES et seraient signalés par ruff (`RUF100` n'est pas dans la sélection mais `--strict` n'aime pas les noqa morts). **RETIRER les `# noqa: S404`/`S603`** (laissés en commentaire d'intention ci-dessus pour mémoire) : la sélection ne contient pas `S`. Garder uniquement le `# pragma: no cover` sur la ligne `subprocess.run`.

- [ ] **Step 4 : Vérifier puis gate**

Run : `( cd packages/verifier && uv run pytest tests/test_ffprobe.py -q --no-cov )` → PASS.
Run : gate complet.

> **Note couverture (CHAUDE) :** `returncode != 0` (True/False) ; `json.loads` lève (True/False) ; `isinstance(payload, dict)` (True/False) ; `streams` non-liste/vide vs liste non-vide ; `any(codec_type in media)` (True/False) ; `_build_meta` : `format` dict / absent ; `video` présent / absent ; `audios` non-vide / vide ; `_as_str`/`_as_int`/`_as_float`/`_as_plain_int` chacun avec valeur du bon type / mauvais type / non parsable ; `tags` dict / absent. **Le `ProdFfprobeRunner.__call__` est `# pragma: no cover`** (testé par `analysis_integration`, Task 9). Le constructeur `ProdFfprobeRunner.__init__` n'est PAS pragma → AJOUTER `test_prod_ffprobe_runner_constructs` : `ProdFfprobeRunner(30.0)` (instancie sans appeler `__call__`) pour couvrir le `def __init__`. Le `def __call__` (signature) est couvert par le `def` ; seul son CORPS est pragma.

- [ ] **Step 5 : Commit**

```bash
git add packages/verifier/src/download_verifier/checks/ffprobe.py packages/verifier/tests/test_ffprobe.py
git commit -m "$(cat <<'EOF'
feat(verifier): check ffprobe (runner injecté) — real_meta défensif (strings→float/int), status clean/suspicious

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4 : `pipeline.py` (pur — exécute les checks activés, agrège worst-status, fusionne `real_meta`)

**Files :**
- Create: `packages/verifier/src/download_verifier/pipeline.py`
- Create: `packages/verifier/tests/test_pipeline.py`

> Spec §5. `run(header, path, ffprobe_runner, cfg)` : exécute les checks dans `cfg.enabled_checks` (registre `{name: callable}`), agrège leur worst-status en verdict, fusionne leurs `meta` en `real_meta`, et renvoie la trace `checks` (`[{name, status, meta}]`). Un nom de check inconnu dans `enabled_checks` est ignoré (le registre est la source de vérité ; `clamav` n'a pas d'entrée → ignoré). `type_sniff` reçoit `header` ; `ffprobe` reçoit `path` + `ffprobe_runner` + `cfg`.

- [ ] **Step 1 : Écrire le test qui échoue**

`packages/verifier/tests/test_pipeline.py` :
```python
import json
from collections.abc import Sequence
from pathlib import Path

from download_verifier import pipeline
from download_verifier.config import AnalysisConfig

_BASE = AnalysisConfig.from_env({})


class _StubFfprobe:
    def __init__(self, returncode: int, stdout: bytes) -> None:
        self._rc = returncode
        self._out = stdout

    def __call__(self, argv: Sequence[str]) -> tuple[int, bytes]:
        return self._rc, self._out


_VALID_MEDIA = json.dumps(
    {
        "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1, "height": 1}],
        "format": {"format_name": "mp4"},
    }
).encode()


def _cfg(checks: tuple[str, ...]) -> AnalysisConfig:
    return AnalysisConfig.from_env({"ENABLED_CHECKS": ",".join(checks)})


def test_clean_media_aggregates_to_clean() -> None:
    verdict, real_meta, checks = pipeline.run(
        b"\x1a\x45\xdf\xa3" + b"\x00" * 64, Path("/q/f"), _StubFfprobe(0, _VALID_MEDIA), _BASE
    )
    assert verdict == "clean"
    assert real_meta["container"] == "mp4"
    assert real_meta["sniffed_type"] is not None
    assert [c["name"] for c in checks] == ["type_sniff", "ffprobe"]
    assert {c["status"] for c in checks} == {"clean"}


def test_executable_header_makes_verdict_malicious() -> None:
    verdict, _real_meta, checks = pipeline.run(
        b"\x7fELF" + b"\x00" * 64, Path("/q/f"), _StubFfprobe(0, _VALID_MEDIA), _BASE
    )
    assert verdict == "malicious"  # type_sniff malicious écrase ffprobe clean
    statuses = {c["name"]: c["status"] for c in checks}
    assert statuses["type_sniff"] == "malicious"


def test_non_media_makes_verdict_suspicious() -> None:
    # en-tête texte (type_sniff clean) + ffprobe échoue (suspicious) → worst = suspicious.
    verdict, _real_meta, _checks = pipeline.run(
        b"plain text not a media\n", Path("/q/f"), _StubFfprobe(1, b""), _BASE
    )
    assert verdict == "suspicious"


def test_enabled_checks_selects_only_type_sniff() -> None:
    verdict, real_meta, checks = pipeline.run(
        b"\x1a\x45\xdf\xa3" + b"\x00" * 64,
        Path("/q/f"),
        _StubFfprobe(1, b""),  # ffprobe échouerait, mais il est DÉSACTIVÉ
        _cfg(("type_sniff",)),
    )
    assert verdict == "clean"
    assert [c["name"] for c in checks] == ["type_sniff"]
    assert "container" not in real_meta  # ffprobe n'a pas tourné


def test_enabled_checks_selects_only_ffprobe() -> None:
    verdict, real_meta, checks = pipeline.run(
        b"\x7fELF" + b"\x00" * 64,  # serait malicious, mais type_sniff est DÉSACTIVÉ
        Path("/q/f"),
        _StubFfprobe(0, _VALID_MEDIA),
        _cfg(("ffprobe",)),
    )
    assert verdict == "clean"
    assert [c["name"] for c in checks] == ["ffprobe"]
    assert "sniffed_type" not in real_meta


def test_unknown_check_name_is_ignored() -> None:
    verdict, _real_meta, checks = pipeline.run(
        b"\x1a\x45\xdf\xa3" + b"\x00" * 64,
        Path("/q/f"),
        _StubFfprobe(0, _VALID_MEDIA),
        _cfg(("type_sniff", "clamav", "ffprobe")),  # clamav non implémenté → ignoré
    )
    assert verdict == "clean"
    assert [c["name"] for c in checks] == ["type_sniff", "ffprobe"]
```

- [ ] **Step 2 : Lancer pour vérifier l'échec**

Run : `( cd packages/verifier && uv run pytest tests/test_pipeline.py -q --no-cov )`
Expected : FAIL — `ModuleNotFoundError: No module named 'download_verifier.pipeline'`.

- [ ] **Step 3 : Écrire l'implémentation**

`packages/verifier/src/download_verifier/pipeline.py` :
```python
"""Pipeline d'analyse PUR (spec analysis §5) : exécute les checks activés, agrège.

``run`` exécute les checks listés dans ``cfg.enabled_checks`` (dans cet ordre, en filtrant ceux
absents du registre — un ``clamav`` non implémenté est simplement ignoré, DA4), agrège leur
worst-status en un verdict (``clean < suspicious < malicious``), fusionne leurs ``meta`` en un
``real_meta``, et renvoie la trace ``checks`` (``[{name, status, meta}]``). Pur : aucun I/O ici —
``type_sniff`` reçoit l'en-tête déjà lu, ``ffprobe`` reçoit le chemin + son runner injecté.
"""

from pathlib import Path

from download_verifier.checks import ffprobe as ffprobe_check
from download_verifier.checks import type_sniff as type_sniff_check
from download_verifier.checks.base import CheckOutcome, worst_status
from download_verifier.checks.ffprobe import FfprobeRunner
from download_verifier.config import AnalysisConfig


def run(
    header: bytes, path: Path, ffprobe_runner: FfprobeRunner, cfg: AnalysisConfig
) -> tuple[str, dict[str, object], list[dict[str, object]]]:
    """Exécute les checks activés ; rend ``(verdict, real_meta, checks)``."""
    outcomes: list[CheckOutcome] = []
    for name in cfg.enabled_checks:
        if name == "type_sniff":
            outcomes.append(type_sniff_check.sniff(header))
        elif name == "ffprobe":
            outcomes.append(ffprobe_check.probe(path, ffprobe_runner, cfg))
        # tout autre nom (clamav non implémenté, faute de frappe) est ignoré (DA4).
    verdict = worst_status([outcome.status for outcome in outcomes])
    real_meta: dict[str, object] = {}
    for outcome in outcomes:
        real_meta.update(outcome.meta)
    checks = [
        {"name": outcome.name, "status": outcome.status, "meta": dict(outcome.meta)}
        for outcome in outcomes
    ]
    return verdict, real_meta, checks
```

- [ ] **Step 4 : Vérifier puis gate**

Run : `( cd packages/verifier && uv run pytest tests/test_pipeline.py -q --no-cov )` → PASS.
Run : gate complet.

> **Note couverture :** boucle `for name` : branche `type_sniff` / branche `ffprobe` / branche ignorée (nom inconnu — `test_unknown_check_name_is_ignored`). `worst_status` (déjà testé en base). Fusion `real_meta` + trace `checks`. Sélection `enabled_checks` (les deux tests `selects_only_…`). Pas de branche cachée.

- [ ] **Step 5 : Commit**

```bash
git add packages/verifier/src/download_verifier/pipeline.py packages/verifier/tests/test_pipeline.py
git commit -m "$(cat <<'EOF'
feat(verifier): pipeline pur — exécute les checks activés, agrège worst-status, fusionne real_meta

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5 : `egress.py` (parent — parse DÉFENSIF du stdout enfant → mapping, DA6)

**Files :**
- Create: `packages/verifier/src/download_verifier/egress.py`
- Create: `packages/verifier/tests/test_egress.py`

> Spec §4/§6 + DA6. `parse(stdout, returncode, timed_out, cfg)` mappe l'issue de l'enfant en `(verdict, real_meta, checks)`, TOUJOURS de manière déterministe (jamais d'exception remontée) : `timed_out` → `suspicious` ; `returncode != 0` → `suspicious` ; stdout > `egress_cap_bytes` → `suspicious` (illisible) ; non-JSON → `suspicious` ; hors-schéma (pas un objet, `verdict` manquant/non-str/hors-enum, `real_meta` non-objet, `checks` non-liste) → `suspicious` ; bien formé → `(verdict, real_meta, checks)` de l'enfant.

- [ ] **Step 1 : Écrire le test qui échoue**

`packages/verifier/tests/test_egress.py` :
```python
import json

from download_verifier import egress
from download_verifier.config import AnalysisConfig

_CFG = AnalysisConfig.from_env({"EGRESS_CAP_BYTES": "256"})


def _valid(verdict: str = "clean") -> bytes:
    return json.dumps(
        {"verdict": verdict, "real_meta": {"container": "mp4"}, "checks": [{"name": "ffprobe"}]}
    ).encode()


def test_valid_egress_is_passed_through() -> None:
    verdict, real_meta, checks = egress.parse(_valid("clean"), 0, False, _CFG)
    assert verdict == "clean"
    assert real_meta == {"container": "mp4"}
    assert checks == [{"name": "ffprobe"}]


def test_each_enum_verdict_passes_through() -> None:
    for value in ("clean", "suspicious", "malicious"):
        assert egress.parse(_valid(value), 0, False, _CFG)[0] == value


def test_timed_out_is_suspicious() -> None:
    assert egress.parse(_valid(), 0, True, _CFG) == ("suspicious", {}, [])


def test_nonzero_returncode_is_suspicious() -> None:
    assert egress.parse(_valid(), 1, False, _CFG) == ("suspicious", {}, [])


def test_oversized_stdout_is_suspicious() -> None:
    huge = json.dumps({"verdict": "clean", "real_meta": {"p": "x" * 4096}, "checks": []}).encode()
    assert egress.parse(huge, 0, False, _CFG) == ("suspicious", {}, [])


def test_non_json_is_suspicious() -> None:
    assert egress.parse(b"{not json", 0, False, _CFG) == ("suspicious", {}, [])


def test_empty_stdout_is_suspicious() -> None:
    assert egress.parse(b"", 0, False, _CFG) == ("suspicious", {}, [])


def test_non_object_payload_is_suspicious() -> None:
    assert egress.parse(b"[1,2,3]", 0, False, _CFG) == ("suspicious", {}, [])


def test_missing_verdict_is_suspicious() -> None:
    payload = json.dumps({"real_meta": {}, "checks": []}).encode()
    assert egress.parse(payload, 0, False, _CFG) == ("suspicious", {}, [])


def test_non_string_verdict_is_suspicious() -> None:
    payload = json.dumps({"verdict": 1, "real_meta": {}, "checks": []}).encode()
    assert egress.parse(payload, 0, False, _CFG) == ("suspicious", {}, [])


def test_out_of_enum_verdict_is_suspicious() -> None:
    payload = json.dumps({"verdict": "error", "real_meta": {}, "checks": []}).encode()
    assert egress.parse(payload, 0, False, _CFG) == ("suspicious", {}, [])


def test_non_object_real_meta_is_suspicious() -> None:
    payload = json.dumps({"verdict": "clean", "real_meta": [], "checks": []}).encode()
    assert egress.parse(payload, 0, False, _CFG) == ("suspicious", {}, [])


def test_non_list_checks_is_suspicious() -> None:
    payload = json.dumps({"verdict": "clean", "real_meta": {}, "checks": {}}).encode()
    assert egress.parse(payload, 0, False, _CFG) == ("suspicious", {}, [])
```

- [ ] **Step 2 : Lancer pour vérifier l'échec**

Run : `( cd packages/verifier && uv run pytest tests/test_egress.py -q --no-cov )`
Expected : FAIL — `ModuleNotFoundError: No module named 'download_verifier.egress'`.

- [ ] **Step 3 : Écrire l'implémentation**

`packages/verifier/src/download_verifier/egress.py` :
```python
"""Contrat d'égress de l'enfant (spec analysis §4/§6 — DA6) : parse DÉFENSIF côté parent.

``parse`` mappe l'issue de l'enfant en ``(verdict, real_meta, checks)`` de façon TOUJOURS
déterministe (jamais d'exception remontée — le service répond 200, §6). Un enfant qui timeout,
sort en erreur, dépasse le cap d'octets, ou rend un égress illisible/hors-schéma est un signal de
POISON → ``suspicious``. Schéma strict : objet ``{verdict ∈ {clean,suspicious,malicious}: str,
real_meta: obj, checks: list}``. Tout écart → ``suspicious``.
"""

import json

from download_verifier.checks.base import STATUS_RANK
from download_verifier.config import AnalysisConfig

_POISON: tuple[str, dict[str, object], list[object]] = ("suspicious", {}, [])
_VALID_VERDICTS = frozenset(STATUS_RANK)


def parse(
    stdout: bytes, returncode: int, timed_out: bool, cfg: AnalysisConfig
) -> tuple[str, dict[str, object], list[object]]:
    """Mappe l'égress enfant en ``(verdict, real_meta, checks)`` (jamais d'exception)."""
    if timed_out or returncode != 0 or len(stdout) > cfg.egress_cap_bytes:
        return _POISON
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return _POISON
    if not isinstance(payload, dict):
        return _POISON
    verdict = payload.get("verdict")
    real_meta = payload.get("real_meta")
    checks = payload.get("checks")
    if not isinstance(verdict, str) or verdict not in _VALID_VERDICTS:
        return _POISON
    if not isinstance(real_meta, dict) or not isinstance(checks, list):
        return _POISON
    return verdict, real_meta, checks
```

> **Note d'implémentation :** `RecursionError` est attrapé comme dans `app.py` (handoff §4 : un JSON profondément imbriqué SOUS le cap d'octets lève `RecursionError`, un `RuntimeError`). Test optionnel `test_deeply_nested_json_is_suspicious` (cf. Note couverture) pour exercer cette branche.

- [ ] **Step 4 : Vérifier puis gate**

Run : `( cd packages/verifier && uv run pytest tests/test_egress.py -q --no-cov )` → PASS.
Run : gate complet.

> **Note couverture (CHAUDE) :** la condition `timed_out or returncode != 0 or len > cap` est un OR court-circuité → chaque opérande doit être exercé True ET False isolément : `test_timed_out…` (timed_out True) ; `test_nonzero_returncode…` (returncode True, timed_out False) ; `test_oversized…` (len True, les deux autres False) ; `test_valid…` (les trois False). `json.loads` lève (`test_non_json…`/`test_empty…`) / OK. `isinstance(payload, dict)` (`test_non_object…`) / dict. `verdict` non-str (`test_non_string…`) / hors-enum (`test_out_of_enum…`) / valide. `real_meta` non-dict (`test_non_object_real_meta…`) / `checks` non-list (`test_non_list_checks…`) / les deux OK. **`RecursionError`** : si la coverage signale cette branche du `except` non couverte (improbable car groupée avec `ValueError`), AJOUTER `test_deeply_nested_json_is_suspicious` (`(b"[" * 20000) + b"1" + (b"]" * 20000)` mais `EGRESS_CAP_BYTES` doit alors être ≥ 40 Kio — utiliser un `AnalysisConfig.from_env({})` à cap 64 Kio pour ce test précis). Sinon, comme `app.py` groupe déjà les trois exceptions dans un seul `except`, la branche est couverte par n'importe quel `json.loads` qui lève.

- [ ] **Step 5 : Commit**

```bash
git add packages/verifier/src/download_verifier/egress.py packages/verifier/tests/test_egress.py
git commit -m "$(cat <<'EOF'
feat(verifier): egress.parse défensif — timeout/exit≠0/oversize/illisible/hors-schéma → suspicious (DA6)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6 : `spawn.py` (parent — `ChildRunner` injecté, tmpdir jetable, env minimal → mapping)

**Files :**
- Create: `packages/verifier/src/download_verifier/spawn.py`
- Create: `packages/verifier/tests/test_spawn.py`

> Spec §4 + DA5/DA8/DA9. `ChildRunner` (Protocol injectable : `__call__(argv, *, cwd, env, timeout) -> (returncode, stdout, timed_out)`). `ProdChildRunner` fait le vrai `subprocess.run` (`# pragma: no cover` : argv re-exec enfant, `preexec_fn=_confine` = rlimits + setsid, `close_fds=True`, `stdin=DEVNULL`/`stdout=PIPE`/`stderr=DEVNULL`, `timeout`, `env` minimal ; sur `TimeoutExpired` → `killpg` + `(0, b"", True)`). `run_analysis(ed2k_hash, cfg, runner)` : crée un `tempfile.mkdtemp()` jetable (supprimé en `finally`), construit l'argv `[sys.executable, "-m", "download_verifier.analysis_child", ed2k_hash]` + l'env minimal `_minimal_env(cfg)` (PAS `os.environ`), appelle le runner, et délègue à `egress.parse`.

- [ ] **Step 1 : Écrire le test qui échoue**

`packages/verifier/tests/test_spawn.py` :
```python
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from download_verifier import spawn
from download_verifier.config import AnalysisConfig
from download_verifier.spawn import ProdChildRunner, run_analysis

_CFG = AnalysisConfig.from_env({"QUARANTINE_DIR": "/quar", "ENABLED_CHECKS": "type_sniff,ffprobe"})
_HASH = "a" * 32
_VALID_EGRESS = b'{"verdict": "clean", "real_meta": {"container": "mp4"}, "checks": []}'


class _RecordingRunner:
    """ChildRunner injecté : capture argv/cwd/env/timeout, rend un (rc, stdout, timed_out) canné."""

    def __init__(self, returncode: int, stdout: bytes, timed_out: bool) -> None:
        self._result = (returncode, stdout, timed_out)
        self.argv: Sequence[str] = ()
        self.cwd = ""
        self.env: Mapping[str, str] = {}
        self.timeout = 0.0
        self.cwd_existed_during_call = False

    def __call__(
        self, argv: Sequence[str], *, cwd: str, env: Mapping[str, str], timeout: float
    ) -> tuple[int, bytes, bool]:
        self.argv = list(argv)
        self.cwd = cwd
        self.env = dict(env)
        self.timeout = timeout
        self.cwd_existed_during_call = Path(cwd).is_dir()
        return self._result


def test_valid_child_output_is_parsed() -> None:
    runner = _RecordingRunner(0, _VALID_EGRESS, False)
    verdict, real_meta, checks = run_analysis(_HASH, _CFG, runner)
    assert verdict == "clean"
    assert real_meta == {"container": "mp4"}
    assert checks == []


def test_timed_out_child_is_suspicious() -> None:
    assert run_analysis(_HASH, _CFG, _RecordingRunner(0, b"", True)) == ("suspicious", {}, [])


def test_nonzero_exit_child_is_suspicious() -> None:
    assert run_analysis(_HASH, _CFG, _RecordingRunner(1, b"", False)) == ("suspicious", {}, [])


def test_oversized_child_output_is_suspicious() -> None:
    huge = b'{"verdict":"clean","real_meta":{},"checks":[]}' + b" " * (_CFG.egress_cap_bytes + 1)
    assert run_analysis(_HASH, _CFG, _RecordingRunner(0, huge, False)) == ("suspicious", {}, [])


def test_argv_targets_the_child_module_with_hash() -> None:
    runner = _RecordingRunner(0, _VALID_EGRESS, False)
    run_analysis(_HASH, _CFG, runner)
    assert runner.argv == [sys.executable, "-m", "download_verifier.analysis_child", _HASH]


def test_timeout_is_passed_from_config() -> None:
    runner = _RecordingRunner(0, _VALID_EGRESS, False)
    run_analysis(_HASH, _CFG, runner)
    assert runner.timeout == _CFG.timeout_s


def test_cwd_is_a_real_temp_dir_during_call_and_removed_after() -> None:
    runner = _RecordingRunner(0, _VALID_EGRESS, False)
    run_analysis(_HASH, _CFG, runner)
    assert runner.cwd_existed_during_call is True
    assert not Path(runner.cwd).exists()  # supprimé en finally


def test_temp_dir_is_removed_even_when_runner_raises() -> None:
    captured: list[str] = []

    class _BoomRunner:
        def __call__(
            self, argv: Sequence[str], *, cwd: str, env: Mapping[str, str], timeout: float
        ) -> tuple[int, bytes, bool]:
            captured.append(cwd)
            raise RuntimeError("boom")

    try:
        run_analysis(_HASH, _CFG, _BoomRunner())
    except RuntimeError:
        pass
    assert captured  # le runner a bien été appelé
    assert not Path(captured[0]).exists()  # tmpdir nettoyé malgré l'exception


def test_minimal_env_contains_only_whitelisted_vars() -> None:
    runner = _RecordingRunner(0, _VALID_EGRESS, False)
    run_analysis(_HASH, _CFG, runner)
    assert runner.env["QUARANTINE_DIR"] == "/quar"
    assert runner.env["ENABLED_CHECKS"] == "type_sniff,ffprobe"
    assert runner.env["FFPROBE_PATH"] == _CFG.ffprobe_path
    assert runner.env["HEADER_BYTES"] == str(_CFG.header_bytes)
    assert runner.env["ANALYSIS_TIMEOUT_S"] == str(_CFG.timeout_s)
    assert set(runner.env) == {
        "QUARANTINE_DIR",
        "ENABLED_CHECKS",
        "FFPROBE_PATH",
        "HEADER_BYTES",
        "ANALYSIS_TIMEOUT_S",
        "PATH",
    }


def test_minimal_env_does_not_leak_parent_environ(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("SECRET_VPN_TOKEN", "do-not-leak")
    runner = _RecordingRunner(0, _VALID_EGRESS, False)
    run_analysis(_HASH, _CFG, runner)
    assert "SECRET_VPN_TOKEN" not in runner.env


def test_prod_child_runner_constructs() -> None:
    # le constructeur n'est pas pragma ; __call__ (vrai subprocess) l'est.
    assert isinstance(ProdChildRunner(_CFG), ProdChildRunner)
```

> **Note typage du test :** `monkeypatch` est typé `pytest.MonkeyPatch` ; le `# type: ignore[no-untyped-def]` ci-dessus est un raccourci — PRÉFÉRER `def test_…(monkeypatch: pytest.MonkeyPatch) -> None:` (import `pytest`) et retirer le ignore. (Conserver le `-> None` sur toutes les fonctions de test, mypy strict.)

- [ ] **Step 2 : Lancer pour vérifier l'échec**

Run : `( cd packages/verifier && uv run pytest tests/test_spawn.py -q --no-cov )`
Expected : FAIL — `ModuleNotFoundError: No module named 'download_verifier.spawn'`.

- [ ] **Step 3 : Écrire l'implémentation**

`packages/verifier/src/download_verifier/spawn.py` :
```python
"""Spawn de l'enfant d'analyse (spec analysis §4 — DA5/DA8/DA9), côté PARENT.

``run_analysis`` re-exec un enfant Python jetable par fichier (PAS ``os.fork``) : argv minimal,
cwd ``tempfile.mkdtemp()`` jetable (supprimé en ``finally`` même en cas d'exception), env EXPLICITE
minimal (on n'hérite PAS de ``os.environ`` — secrets/VPN ; on ne passe que QUARANTINE_DIR + la
config des checks + un PATH minimal). Le ``ChildRunner`` est INJECTABLE : l'impl PROD fait le vrai
``subprocess.run`` (``close_fds=True``, ``preexec_fn=_confine`` = rlimits + setsid, timeout-kill du
groupe via ``killpg``) — ces lignes système sont ``# pragma: no cover`` (couvertes par
analysis_integration). Le mapping de l'issue (stdout/timeout/exit) est délégué à ``egress.parse``
(défensif, DA6). Le parent ne lit JAMAIS d'octets du fichier (DA8).
"""

import os
import resource
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from typing import Protocol

from download_verifier import egress
from download_verifier.config import AnalysisConfig

_CHILD_MODULE = "download_verifier.analysis_child"
_MINIMAL_PATH = "/usr/local/bin:/usr/bin:/bin"


class ChildRunner(Protocol):
    """Exécute l'enfant et rend ``(returncode, stdout, timed_out)``. Injecté pour les tests."""

    def __call__(
        self, argv: Sequence[str], *, cwd: str, env: Mapping[str, str], timeout: float
    ) -> tuple[int, bytes, bool]: ...


class ProdChildRunner:
    """``ChildRunner`` de PROD : vrai subprocess confiné (couvert par analysis_integration)."""

    def __init__(self, cfg: AnalysisConfig) -> None:
        self._cfg = cfg

    def __call__(
        self, argv: Sequence[str], *, cwd: str, env: Mapping[str, str], timeout: float
    ) -> tuple[int, bytes, bool]:  # pragma: no cover
        try:
            completed = subprocess.run(
                list(argv),
                cwd=cwd,
                env=dict(env),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                close_fds=True,
                preexec_fn=self._confine,
                check=False,
            )
        except subprocess.TimeoutExpired as expired:
            return 0, expired.stdout or b"", True
        return completed.returncode, completed.stdout, False

    def _confine(self) -> None:  # pragma: no cover
        os.setsid()  # groupe de process dédié → on tue l'enfant ET son petit-fils ffprobe
        cfg = self._cfg
        resource.setrlimit(resource.RLIMIT_CPU, (cfg.rlimit_cpu_s, cfg.rlimit_cpu_s))
        resource.setrlimit(resource.RLIMIT_AS, (cfg.rlimit_as_bytes, cfg.rlimit_as_bytes))
        resource.setrlimit(resource.RLIMIT_FSIZE, (cfg.rlimit_fsize_bytes, cfg.rlimit_fsize_bytes))
        resource.setrlimit(resource.RLIMIT_NPROC, (cfg.rlimit_nproc, cfg.rlimit_nproc))
        resource.setrlimit(resource.RLIMIT_NOFILE, (cfg.rlimit_nofile, cfg.rlimit_nofile))


def run_analysis(
    ed2k_hash: str, cfg: AnalysisConfig, runner: ChildRunner
) -> tuple[str, dict[str, object], list[object]]:
    """Spawne l'enfant pour ``ed2k_hash`` ; rend ``(verdict, real_meta, checks)`` (DA6)."""
    argv = [sys.executable, "-m", _CHILD_MODULE, ed2k_hash]
    scratch = tempfile.mkdtemp(prefix="analysis-")
    try:
        returncode, stdout, timed_out = runner(
            argv, cwd=scratch, env=_minimal_env(cfg), timeout=cfg.timeout_s
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return egress.parse(stdout, returncode, timed_out, cfg)


def _minimal_env(cfg: AnalysisConfig) -> dict[str, str]:
    """Env EXPLICITE minimal pour l'enfant (DA8) — ne fuit JAMAIS ``os.environ``."""
    return {
        "QUARANTINE_DIR": cfg.quarantine_dir,
        "ENABLED_CHECKS": ",".join(cfg.enabled_checks),
        "FFPROBE_PATH": cfg.ffprobe_path,
        "HEADER_BYTES": str(cfg.header_bytes),
        "ANALYSIS_TIMEOUT_S": str(cfg.timeout_s),
        "PATH": _MINIMAL_PATH,
    }
```

> **Note rlimit :** `resource.RLIMIT_NPROC` n'existe pas sur toutes les plateformes mais EST présent sur Linux (cible du verifier, spec §11). Comme `_confine` est `# pragma: no cover` (Linux-only, testé par analysis_integration), aucune branche conditionnelle n'est requise ici. **Note ruff :** `preexec_fn` + `subprocess.run` ne déclenchent `S602/S603/B603` que si `S`/bandit est sélectionné — il ne l'est pas (`E,F,I,UP,B,SIM`). `B` (flake8-bugbear) ne signale pas `subprocess`. Aucun `noqa` requis.

- [ ] **Step 4 : Vérifier puis gate**

Run : `( cd packages/verifier && uv run pytest tests/test_spawn.py -q --no-cov )` → PASS.
Run : gate complet.

> **Note couverture (CHAUDE) :** `run_analysis` — chemin nominal (runner rend stdout → `egress.parse`) ; `finally` exécuté en succès (`test_cwd…removed_after`) ET sur exception du runner (`test_temp_dir…runner_raises`, qui relève l'exception après nettoyage). `_minimal_env` couvert par les deux tests d'env. `ProdChildRunner.__init__` couvert par `test_prod_child_runner_constructs`. Le `def __call__`/`def _confine` (signatures) sont couverts par le `def` ; leurs CORPS sont `# pragma: no cover`. **Vérifier qu'aucune ligne de `_confine`/`__call__` ne fuit hors du pragma** (le pragma couvre tout le corps de la fonction quand placé sur la ligne `def …:` — mais ici il est sur la dernière ligne de signature ; s'assurer que coverage l'applique au bloc ; sinon, déplacer `# pragma: no cover` sur la ligne `def`).

- [ ] **Step 5 : Commit**

```bash
git add packages/verifier/src/download_verifier/spawn.py packages/verifier/tests/test_spawn.py
git commit -m "$(cat <<'EOF'
feat(verifier): spawn.run_analysis — enfant jetable (ChildRunner injecté, tmpdir, env minimal) → egress

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7 : `analysis_child.py` (enfant — revalide le hash, ouvre RO, `pipeline.run`, imprime JSON)

**Files :**
- Create: `packages/verifier/src/download_verifier/analysis_child.py`
- Create: `packages/verifier/tests/test_analysis_child.py`

> Spec §4 + DA8. `main(argv, *, ffprobe_runner=None, cfg=None) -> int` : revalide le hash canonique (32 hex — défense en profondeur anti-traversal), lit `cfg.header_bytes` octets du fichier RO (pas tout le fichier — DA10), appelle `pipeline.run`, imprime `json.dumps({"verdict","real_meta","checks"})` sur stdout, rend `0`. Hash non canonique → rend `2` (sans rien imprimer d'utile). Fichier absent/illisible → l'enfant imprime un égress VALIDE avec `verdict="suspicious"` (l'enfant a été spawné parce que le parent a vu `is_file()` True ; une disparition entre-temps est un poison → suspicious, cohérent DA6). `if __name__ == "__main__":` sous `# pragma: no cover` (appelle `sys.exit(main(sys.argv[1:], ffprobe_runner=ProdFfprobeRunner(...), cfg=AnalysisConfig.from_env(os.environ)))`).

- [ ] **Step 1 : Écrire le test qui échoue**

`packages/verifier/tests/test_analysis_child.py` :
```python
import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from download_verifier.analysis_child import main
from download_verifier.config import AnalysisConfig

_HASH = "a" * 32

_VALID_MEDIA = json.dumps(
    {
        "streams": [{"codec_type": "video", "codec_name": "h264", "width": 2, "height": 2}],
        "format": {"format_name": "mp4"},
    }
).encode()


class _StubFfprobe:
    def __init__(self, returncode: int, stdout: bytes) -> None:
        self._rc = returncode
        self._out = stdout

    def __call__(self, argv: Sequence[str]) -> tuple[int, bytes]:
        return self._rc, self._out


def _cfg(tmp_path: Path) -> AnalysisConfig:
    return AnalysisConfig.from_env({"QUARANTINE_DIR": str(tmp_path), "HEADER_BYTES": "4096"})


def test_valid_file_prints_clean_egress(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / _HASH).write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 64)
    code = main([_HASH], ffprobe_runner=_StubFfprobe(0, _VALID_MEDIA), cfg=_cfg(tmp_path))
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "clean"
    assert payload["real_meta"]["container"] == "mp4"
    assert [c["name"] for c in payload["checks"]] == ["type_sniff", "ffprobe"]


def test_executable_file_prints_malicious_egress(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / _HASH).write_bytes(b"\x7fELF" + b"\x00" * 64)
    code = main([_HASH], ffprobe_runner=_StubFfprobe(0, _VALID_MEDIA), cfg=_cfg(tmp_path))
    assert code == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "malicious"


def test_non_canonical_hash_exits_nonzero_without_egress(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["../etc/passwd"], ffprobe_runner=_StubFfprobe(0, b""), cfg=_cfg(tmp_path))
    assert code == 2
    assert capsys.readouterr().out == ""


def test_missing_argv_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main([], ffprobe_runner=_StubFfprobe(0, b""), cfg=_cfg(tmp_path))
    assert code == 2


def test_vanished_file_prints_suspicious_egress(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # hash canonique mais le fichier n'existe pas (disparu entre is_file et le spawn) → suspicious.
    code = main([_HASH], ffprobe_runner=_StubFfprobe(0, _VALID_MEDIA), cfg=_cfg(tmp_path))
    assert code == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "suspicious"


def test_only_header_bytes_are_read(tmp_path: Path) -> None:
    # un fichier énorme : l'enfant ne doit lire que header_bytes (pas tout le fichier).
    big = tmp_path / _HASH
    big.write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * (10 * 1024 * 1024))
    cfg = AnalysisConfig.from_env({"QUARANTINE_DIR": str(tmp_path), "HEADER_BYTES": "8"})
    code = main([_HASH], ffprobe_runner=_StubFfprobe(0, _VALID_MEDIA), cfg=cfg)
    assert code == 0  # n'a lu que 8 octets pour le sniff (le test prouve l'absence de crash mémoire)
```

- [ ] **Step 2 : Lancer pour vérifier l'échec**

Run : `( cd packages/verifier && uv run pytest tests/test_analysis_child.py -q --no-cov )`
Expected : FAIL — `ModuleNotFoundError: No module named 'download_verifier.analysis_child'`.

- [ ] **Step 3 : Écrire l'implémentation**

`packages/verifier/src/download_verifier/analysis_child.py` :
```python
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
```

> **Note d'implémentation :** la branche `cfg is None` / `ffprobe_runner is None` (défauts PROD lisant `os.environ` + `ProdFfprobeRunner`) DOIT être couverte (mypy strict + 100 % branch). Les tests fournissent TOUJOURS `cfg=`/`ffprobe_runner=` (branche `is not None`). **AJOUTER deux tests** pour les branches par défaut : `test_main_defaults_cfg_from_env` (appel `main([_HASH])` avec `monkeypatch.setenv("QUARANTINE_DIR", str(tmp_path))` + un fichier valide ; le `ProdFfprobeRunner` par défaut lancera un VRAI ffprobe → résultat dépendant de la plateforme, donc PRÉFÉRER : appeler `main([_HASH], cfg=_cfg(tmp_path))` SANS `ffprobe_runner` pour couvrir la branche `ffprobe_runner is None` → `ProdFfprobeRunner`, MAIS alors le vrai ffprobe tourne). **Trancher au moment de l'impl** : pour éviter un vrai ffprobe dans le run unitaire, couvrir la branche par défaut de `cfg` via `monkeypatch.setattr` sur `AnalysisConfig.from_env` (retournant `_cfg(tmp_path)`) et la branche par défaut de `ffprobe_runner` via `monkeypatch.setattr` sur `ProdFfprobeRunner` (retournant un `_StubFfprobe`). Cf. Step 3bis ci-dessous.

- [ ] **Step 3bis : Tests des branches par défaut (cfg/ffprobe_runner None) — sans vrai ffprobe**

Ajouter à `packages/verifier/tests/test_analysis_child.py` :
```python
def test_main_defaults_cfg_and_runner_without_real_ffprobe(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # couvre les branches `cfg is None` et `ffprobe_runner is None` SANS lancer un vrai ffprobe :
    # on monkeypatch from_env (→ cfg de test) et ProdFfprobeRunner (→ stub).
    import download_verifier.analysis_child as child

    (tmp_path / _HASH).write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 64)
    monkeypatch.setattr(child.AnalysisConfig, "from_env", classmethod(lambda cls, env: _cfg(tmp_path)))
    monkeypatch.setattr(child, "ProdFfprobeRunner", lambda timeout_s: _StubFfprobe(0, _VALID_MEDIA))
    code = child.main([_HASH])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "clean"
```

- [ ] **Step 4 : Vérifier puis gate**

Run : `( cd packages/verifier && uv run pytest tests/test_analysis_child.py -q --no-cov )` → PASS.
Run : gate complet.

> **Note couverture (CHAUDE) :** `cfg is None` True (Step 3bis) / False (les autres) ; `ffprobe_runner is None` True (Step 3bis) / False (les autres) ; `len(argv) != 1 or fullmatch is None` → argv vide (`test_missing_argv`) / hash non canonique (`test_non_canonical_hash`) / valide (les autres) ; `path.open` lève `OSError` (`test_vanished_file`) / OK (les autres). `if __name__ == "__main__":` sous `# pragma: no cover`. La lecture bornée `handle.read(header_bytes)` couverte par `test_only_header_bytes_are_read`.

- [ ] **Step 5 : Commit**

```bash
git add packages/verifier/src/download_verifier/analysis_child.py packages/verifier/tests/test_analysis_child.py
git commit -m "$(cat <<'EOF'
feat(verifier): analysis_child — revalide le hash, lit header RO borné, pipeline.run, imprime l'égress JSON

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8 : BASCULE — `check.verify_file` du NO-OP au vrai pipeline (+ MAJ e2e crawler)

**Files :**
- Modify: `packages/verifier/src/download_verifier/check.py`
- Modify: `packages/verifier/tests/test_check.py` (remplace les tests NO-OP)
- Modify: `packages/crawler/tests/integration/test_verify_loop.py` (le verdict attendu change)

> Spec §3/§6 + DA6. `check.verify_file(quarantine_path, expected)` (signature INCHANGÉE — couture stable que `app.py` appelle déjà) bascule : `is_file()` False → `("error", {}, [])` (métadonnée seulement, AUCUN octet lu côté parent) ; True → `spawn.run_analysis(<hash>, cfg, runner)` (qui spawne l'enfant → `egress.parse`). Le `<hash>` est le `.name` du chemin de quarantaine. `app.py` reste **INTOUCHÉ**. Les anciens tests NO-OP (`unverified`) deviennent caducs → REMPLACÉS par des tests du comportement réel (avec `ChildRunner`/`cfg` injectés via paramètres optionnels défaut-PROD) ; on CONSERVE les cas `error` (absent/répertoire). L'e2e `verify_integration` côté crawler pré-place un fichier de 3 octets : après bascule, son verdict passe de `unverified` à `suspicious` (octets inconnus → type_sniff `clean` ; ffprobe sur 3 octets → exit ≠ 0 → `suspicious` ; worst = `suspicious`) — l'assertion de l'e2e doit être mise à jour.

- [ ] **Step 1 : Réécrire `test_check.py` (le comportement change)**

**Lire d'abord** `packages/verifier/tests/test_check.py` (tests NO-OP existants). Le REMPLACER INTÉGRALEMENT par :
```python
from collections.abc import Mapping, Sequence
from pathlib import Path

from download_verifier.check import verify_file
from download_verifier.config import AnalysisConfig

_HASH = "a" * 32
_CLEAN_EGRESS = b'{"verdict": "clean", "real_meta": {"container": "mp4"}, "checks": []}'


class _StubChildRunner:
    """ChildRunner injecté : rend un (rc, stdout, timed_out) canné, capture le hash de l'argv."""

    def __init__(self, returncode: int, stdout: bytes, timed_out: bool) -> None:
        self._result = (returncode, stdout, timed_out)
        self.seen_hash: str | None = None

    def __call__(
        self, argv: Sequence[str], *, cwd: str, env: Mapping[str, str], timeout: float
    ) -> tuple[int, bytes, bool]:
        self.seen_hash = argv[-1]
        return self._result


def _cfg(tmp_path: Path) -> AnalysisConfig:
    return AnalysisConfig.from_env({"QUARANTINE_DIR": str(tmp_path)})


def test_missing_file_is_error_without_spawn(tmp_path: Path) -> None:
    runner = _StubChildRunner(0, _CLEAN_EGRESS, False)
    verdict, real_meta, checks = verify_file(
        tmp_path / "absent", {}, cfg=_cfg(tmp_path), runner=runner
    )
    assert (verdict, real_meta, checks) == ("error", {}, [])
    assert runner.seen_hash is None  # l'enfant n'est PAS spawné pour un fichier absent


def test_directory_is_error_without_spawn(tmp_path: Path) -> None:
    directory = tmp_path / "dir"
    directory.mkdir()
    runner = _StubChildRunner(0, _CLEAN_EGRESS, False)
    assert verify_file(directory, {}, cfg=_cfg(tmp_path), runner=runner)[0] == "error"
    assert runner.seen_hash is None


def test_existing_file_runs_pipeline_and_returns_verdict(tmp_path: Path) -> None:
    target = tmp_path / _HASH
    target.write_bytes(b"x")  # le parent ne lit JAMAIS ces octets (l'enfant si — stubé ici)
    runner = _StubChildRunner(0, _CLEAN_EGRESS, False)
    verdict, real_meta, checks = verify_file(target, {}, cfg=_cfg(tmp_path), runner=runner)
    assert verdict == "clean"
    assert real_meta == {"container": "mp4"}
    assert checks == []
    assert runner.seen_hash == _HASH  # l'enfant a été spawné avec le bon hash


def test_child_failure_maps_to_suspicious(tmp_path: Path) -> None:
    target = tmp_path / _HASH
    target.write_bytes(b"x")
    runner = _StubChildRunner(1, b"", False)
    assert verify_file(target, {}, cfg=_cfg(tmp_path), runner=runner)[0] == "suspicious"


def test_default_cfg_and_runner_are_prod(tmp_path: Path) -> None:
    # appel SANS cfg/runner pour couvrir les défauts PROD : fichier absent → error (pas de spawn).
    assert verify_file(tmp_path / "absent", {})[0] == "error"
```

> **Note :** le dernier test couvre les défauts PROD (`cfg`/`runner` None) SANS spawner un vrai enfant — il vise le chemin `is_file()` False qui retourne AVANT toute construction de runner. Cela couvre les deux branches `is None` sans subprocess réel.

- [ ] **Step 2 : Lancer pour vérifier l'échec**

Run : `( cd packages/verifier && uv run pytest tests/test_check.py -q --no-cov )`
Expected : FAIL — `verify_file` ne prend pas encore `cfg=`/`runner=` (TypeError) et fait encore le NO-OP (`unverified` au lieu de `clean`/`suspicious`).

- [ ] **Step 3 : Réécrire `check.py` (bascule)**

**Lire d'abord** `packages/verifier/src/download_verifier/check.py` (NO-OP). Le REMPLACER INTÉGRALEMENT par :
```python
"""Couture service-side de l'analyse (spec analysis §3/§6 — DA6).

``verify_file`` est la couture STABLE que ``app.py`` appelle (signature inchangée) : elle vérifie
l'EXISTENCE du fichier en quarantaine (``is_file`` — métadonnée seulement, le parent ne lit JAMAIS
les octets, DA8), puis spawne l'enfant d'analyse jetable (``spawn.run_analysis``) qui exécute les
checks et imprime un égress parsé défensivement (``egress.parse``). Mapping (DA6, toujours 200) :
fichier absent / non-régulier → ``("error", {}, [])`` ; sinon le verdict réel de l'enfant
(``clean``/``suspicious``/``malicious``, ou ``suspicious`` si l'enfant timeout/crashe/égresse mal).

``cfg``/``runner`` sont injectables (tests) ; les défauts sont la config d'env + le ``ProdChildRunner``
réel. ``expected`` reste minimal et non décisif (DA2 ; le pipeline ne l'exploite pas en D-analysis).
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
    if not quarantine_path.is_file():
        return _VERDICT_ERROR, {}, []
    config = cfg if cfg is not None else AnalysisConfig.from_env(os.environ)
    child_runner = runner if runner is not None else ProdChildRunner(config)
    return spawn.run_analysis(quarantine_path.name, config, child_runner)
```

> **`app.py` n'est PAS modifié** : il appelle toujours `verify_file(_quarantine_dir(request) / ed2k_hash, expected)`. Les nouveaux paramètres `cfg`/`runner` sont keyword-only optionnels → l'appel existant reste valide (défauts PROD). **Vérifier** : `grep -n "verify_file" packages/verifier/src/download_verifier/app.py` → l'appel est inchangé.

- [ ] **Step 4 : Mettre à jour l'e2e `verify_integration` côté crawler**

**Lire d'abord** `packages/crawler/tests/integration/test_verify_loop.py`. Le verdict attendu change : un fichier de 3 octets (`b"\x00\x01\x02"`) pré-placé donne désormais `suspicious` (type_sniff `clean` sur octets inconnus + ffprobe `suspicious` car ffprobe échoue sur 3 octets → worst = `suspicious`). C'est DÉTERMINISTE quel que soit l'état de ffprobe (présent → échoue sur 3 octets ; absent → l'enfant lance `ProdFfprobeRunner` qui ne trouve pas le binaire → returncode ≠ 0 → suspicious). Mettre à jour :
- le docstring du module : remplacer « produit une ligne `file_verifications` `unverified` » par « produit une ligne `file_verifications` `suspicious` (l'analyseur réel : 3 octets ne sont pas un média) » ;
- le nom du test `test_verify_loop_produces_unverified_row` → `test_verify_loop_produces_suspicious_row` ;
- l'assertion `assert row == (_A, "unverified")` → `assert row == (_A, "suspicious")`.

Concrètement, les deux lignes à changer :
```python
    row = catalog.execute(
        "SELECT ed2k_hash, verdict FROM file_verifications WHERE ed2k_hash = ?", (_A,)
    ).fetchone()
    assert row == (_A, "suspicious")
    # la tâche est complétée (plus claimable).
    assert local_repo.claim_verification() is None
```

> **Pourquoi le verdict est déterministe sans dépendre de ffprobe :** l'e2e tourne le verifier IN-PROCESS via `ASGITransport` → `check.verify_file` (défauts PROD) → `spawn.run_analysis` spawne un VRAI enfant (`python -m download_verifier.analysis_child`) qui lit les 3 octets, sniffe (inconnu → clean) et lance ffprobe via `ProdFfprobeRunner`. Que ffprobe existe (échoue sur 3 octets, exit ≠ 0) ou pas (binaire introuvable → exception dans `ProdFfprobeRunner` → l'enfant ne l'attrape PAS → exit ≠ 0 → le parent mappe via egress → suspicious), le résultat est `suspicious`. **Note** : ce test devient donc dépendant d'un environnement où le spawn d'un subprocess Python fonctionne (toujours vrai en dev/CI). Le marqueur reste `verify_integration` (sans Docker), désélectionné par défaut, hors coverage — son contrat (chaîne DTO↔réponse + écriture durable) tient ; seul le verdict change. **Vérifier que `analysis_integration` (verifier) couvre le cas média VALIDE** (Task 9), que `verify_integration` (crawler) ne couvre pas.

> **Robustesse alternative (si on veut un verdict média réel dans l'e2e crawler) :** pré-placer un VRAI petit média (généré par ffmpeg) au lieu de 3 octets pour obtenir `clean`. NON retenu ici : cela ajouterait une dépendance ffmpeg à `verify_integration` (qui n'en avait pas) ; `suspicious` sur 3 octets est déterministe et sans dépendance. Le cas `clean` réel est couvert par `analysis_integration` (Task 9).

- [ ] **Step 5 : Vérifier puis gate (les DEUX paquets + les DEUX e2e)**

Run : `( cd packages/verifier && uv run pytest tests/test_check.py -q --no-cov )` → PASS.
Run : gate complet :
```bash
( cd packages/verifier && uv run pytest -q )          # 100 % branch
( cd packages/crawler  && uv run pytest -q )          # 100 % branch (INCHANGÉ — l'e2e est déselectionné par défaut)
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run sqlfluff lint packages/crawler/src
```
Run (e2e crawler dédié — fait foi, le verdict a changé) :
```bash
( cd packages/crawler && uv run pytest -m verify_integration --no-cov -q )
```
Expected : `1 passed` (la boucle produit la ligne `suspicious` contre le vrai service in-process, qui spawne le vrai enfant).

> **CRITIQUE :** confirmer que `app.py` est INCHANGÉ (`git diff --stat packages/verifier/src/download_verifier/app.py` → vide) et que le crawler PROD ne change pas (seul le TEST e2e change). Le contrat de fil `{verdict, real_meta, checks}` tient.

> **Note couverture `check.py` :** `is_file()` False (`test_missing_file…`/`test_directory…`/`test_default…`) / True (`test_existing_file…`/`test_child_failure…`) ; `cfg is None` True (`test_default…`, via le chemin error) / False (les tests injectés) ; `runner is None` True (`test_default…`) / False (injectés). Le `test_default_cfg_and_runner_are_prod` couvre les deux branches `is None` SANS spawner (retour `error` avant construction du runner). **Attention** : dans `test_default…`, le chemin `is_file()` False retourne AVANT `config = …`/`child_runner = …`, donc les lignes `cfg is None`/`runner is None` ne sont PAS exécutées par ce test. **Il faut donc un test qui exerce les défauts AVEC un fichier présent** — mais cela spawnerait un vrai enfant. **Trancher** : ajouter `test_default_runner_spawns_real_child(tmp_path)` qui crée un vrai fichier de 3 octets et appelle `verify_file(target, {})` SANS `cfg`/`runner` → attend `suspicious` (vrai enfant, comme l'e2e) ; OU marquer ce test `analysis_integration` (préférable — il spawne un subprocess réel). **Décision retenue : déplacer la couverture des défauts `cfg/runner is None` AVEC fichier présent dans Task 9 (`analysis_integration`)** et ici n'exiger que le chemin `error` pour les défauts. Conséquence : les lignes `config = cfg if cfg is not None else …` et `child_runner = runner if runner is not None else …` ne sont atteintes QUE par les tests injectés (branche `is not None`) → la branche `is None` de chacune ne serait PAS couverte en unitaire. **Pour préserver 100 % branch sans subprocess**, structurer `check.py` pour que les défauts soient résolus AVANT le `is_file()` :
> ```python
>     config = cfg if cfg is not None else AnalysisConfig.from_env(os.environ)
>     child_runner = runner if runner is not None else ProdChildRunner(config)
>     if not quarantine_path.is_file():
>         return _VERDICT_ERROR, {}, []
>     return spawn.run_analysis(quarantine_path.name, config, child_runner)
> ```
> Ainsi `test_default_cfg_and_runner_are_prod` (fichier absent, défauts) exécute les deux lignes `is None`-True puis retourne `error` — **les deux branches par défaut sont couvertes sans spawn**. **Adopter cette structure dans le Step 3** (défauts résolus en tête). Construire `ProdChildRunner(config)` est inoffensif (son `__init__` ne lance aucun subprocess). Mettre à jour le test `test_missing_file_is_error_without_spawn` : `runner.seen_hash is None` reste vrai (le runner injecté n'est pas appelé car `is_file()` False).

- [ ] **Step 6 : Commit**

```bash
git add packages/verifier/src/download_verifier/check.py packages/verifier/tests/test_check.py packages/crawler/tests/integration/test_verify_loop.py
git commit -m "$(cat <<'EOF'
feat(verifier): BASCULE check.verify_file vers le vrai pipeline (is_file → spawn → egress) ; e2e verify_integration → suspicious

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9 : Test d'intégration `analysis_integration` (spawn réel + vrai ffprobe)

**Files :**
- Create: `packages/verifier/tests/test_analysis_integration.py` (fichier PLAT — PAS de sous-dossier, PAS de `tests/__init__.py`)

> Spec §9 + DA9. Le seul test qui exerce le subprocess RÉEL (l'enfant re-exec + le vrai ffprobe + `_confine` rlimits/setsid) — il prouve `# pragma: no cover` du `ProdChildRunner`/`ProdFfprobeRunner`/`_confine` pour de vrai. `pytestmark = pytest.mark.analysis_integration` → désélectionné par défaut, exclu de la coverage (run dédié). Couvre : un VRAI petit média (généré par ffmpeg → `clean` + `real_meta` rempli) ; un ELF/binaire (`malicious`) ; un script shebang (`malicious`) ; un texte (`suspicious` — pas un média) ; un cas timeout (enfant qui dépasse `timeout_s`) ; un cas égress surdimensionné. **Dépendance : `ffmpeg`/`ffprobe` présents.**

- [ ] **Step 1 : Écrire le test d'intégration**

`packages/verifier/tests/test_analysis_integration.py` :
```python
"""Intégration D-analysis : spawn RÉEL de l'enfant + VRAI ffprobe (spec analysis §9 — DA9).

Run dédié : ( cd packages/verifier && uv run pytest -m analysis_integration --no-cov )
Dépendance : ffmpeg/ffprobe présents dans le PATH (image Plan F en prod ; dev = paquet système).
Prouve POUR DE VRAI le confinement (ProdChildRunner : re-exec, rlimits/setsid via _confine,
timeout-kill du groupe, env minimal, close_fds) + ProdFfprobeRunner (vrai ffprobe) — tout le code
sous # pragma: no cover. Désélectionné par défaut, exclu de la coverage.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from download_verifier.check import verify_file
from download_verifier.config import AnalysisConfig
from download_verifier.spawn import ProdChildRunner

pytestmark = pytest.mark.analysis_integration

_HASH = "a" * 32

_FFMPEG = shutil.which("ffmpeg")
_FFPROBE = shutil.which("ffprobe")
_NEEDS_FFMPEG = pytest.mark.skipif(
    _FFMPEG is None or _FFPROBE is None, reason="ffmpeg/ffprobe requis pour l'intégration D-analysis"
)


def _cfg(quarantine: Path, **overrides: object) -> AnalysisConfig:
    env = {"QUARANTINE_DIR": str(quarantine), "FFPROBE_PATH": _FFPROBE or "ffprobe"}
    env.update({key: str(value) for key, value in overrides.items()})
    return AnalysisConfig.from_env(env)


def _verify(quarantine: Path, cfg: AnalysisConfig) -> tuple[str, dict[str, object], list[object]]:
    return verify_file(quarantine / _HASH, {}, cfg=cfg, runner=ProdChildRunner(cfg))


@_NEEDS_FFMPEG
def test_real_small_media_is_clean_with_real_meta(tmp_path: Path) -> None:
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    target = quarantine / _HASH
    # génère un vrai média minuscule (1 s de couleur unie + un ton audio) en mkv.
    subprocess.run(
        [
            _FFMPEG,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x64:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-shortest",
            str(target),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    verdict, real_meta, checks = _verify(quarantine, _cfg(quarantine))
    assert verdict == "clean"
    assert real_meta.get("video") is not None
    assert real_meta.get("container") is not None
    assert {c["name"] for c in checks} == {"type_sniff", "ffprobe"}


@_NEEDS_FFMPEG
def test_real_executable_is_malicious(tmp_path: Path) -> None:
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    (quarantine / _HASH).write_bytes(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 256)
    assert _verify(quarantine, _cfg(quarantine))[0] == "malicious"


@_NEEDS_FFMPEG
def test_real_shebang_script_is_malicious(tmp_path: Path) -> None:
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    (quarantine / _HASH).write_bytes(b"#!/bin/sh\necho hello\n")
    assert _verify(quarantine, _cfg(quarantine))[0] == "malicious"


@_NEEDS_FFMPEG
def test_real_plain_text_is_suspicious(tmp_path: Path) -> None:
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    (quarantine / _HASH).write_bytes(b"ceci n'est pas un media\n" * 16)
    assert _verify(quarantine, _cfg(quarantine))[0] == "suspicious"


@_NEEDS_FFMPEG
def test_real_oversized_egress_is_suspicious(tmp_path: Path) -> None:
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    (quarantine / _HASH).write_bytes(b"plain text\n")
    # cap d'égress minuscule → l'égress (même suspicious) dépasse → suspicious (poison).
    assert _verify(quarantine, _cfg(quarantine, EGRESS_CAP_BYTES=1))[0] == "suspicious"


@_NEEDS_FFMPEG
def test_real_timeout_is_suspicious(tmp_path: Path) -> None:
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    (quarantine / _HASH).write_bytes(b"plain text\n")
    # timeout ~0 → l'enfant est tué (killpg) avant de finir → suspicious.
    assert _verify(quarantine, _cfg(quarantine, ANALYSIS_TIMEOUT_S=0.001))[0] == "suspicious"


@_NEEDS_FFMPEG
def test_real_missing_file_is_error(tmp_path: Path) -> None:
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()  # pas de fichier _HASH
    assert _verify(quarantine, _cfg(quarantine))[0] == "error"
```

- [ ] **Step 2 : Vérifier (collection par défaut → déselectionné ; run dédié → fait foi)**

Run (collection, run par défaut → déselectionné, ne compte pas dans la coverage) :
```bash
( cd packages/verifier && uv run pytest -q )
```
Expected : `… passed, 7 deselected` (les 7 tests `analysis_integration`), **100.00 % branch** sur `download_verifier`.
Run (dédié, fait foi — ffmpeg/ffprobe requis) :
```bash
( cd packages/verifier && uv run pytest -m analysis_integration --no-cov -q )
```
Expected : `7 passed` (ou `skipped` si ffmpeg absent — mais en dev/CI il est présent : `which ffprobe` → `/usr/bin/ffprobe`, n8.x). Ce run exerce le subprocess RÉEL, `_confine`, le timeout-kill.

> **Note :** ce fichier est PLAT dans `tests/` (PAS de sous-dossier `tests/integration/`, PAS de `tests/__init__.py`) — l'absence de `tests/__init__.py` côté verifier évite la collision de module `tests` au mypy racine (handoff §4). `pytestmark = pytest.mark.analysis_integration` + l'`addopts ... -m "not analysis_integration"` (Task 1) → désélectionné par défaut, hors coverage (`--no-cov` au run dédié). Le `skipif(ffmpeg absent)` rend le run dédié robuste hors-CI.

- [ ] **Step 3 : Commit**

```bash
git add packages/verifier/tests/test_analysis_integration.py
git commit -m "$(cat <<'EOF'
test(integration): analysis_integration — spawn réel + vrai ffprobe (média clean / exécutable malicious / timeout suspicious)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review : couverture de la spec (section/décision → tâche)

| Spec analysis | Couvert par |
|---|---|
| §1 But : vrai analyseur dans `packages/verifier`, sans changer le contrat de fil ni le crawler | Tasks 1-9 (tout dans le verifier) ; Task 8 (bascule, contrat de fil + crawler intouchés) |
| §1 Périmètre : enfant + confinement portable, registre branchable + worst-status, type_sniff + ffprobe, config, marqueur `analysis_integration` | T6 (spawn/confinement), T4 (registre/agrégation), T2 (type_sniff), T3 (ffprobe), T1 (config + marqueur), T9 (intégration) |
| §1 Hors périmètre : ring noyau (Plan F), clamav (follow-up), dédup, alerte, Windows | Header HORS PÉRIMÈTRE ; T4 (clamav = créneau ignoré) ; aucune ligne écrite pour ces points |
| §2 DA1 — confinement portable maintenant, ring noyau au Plan F | T6 (`_confine` rlimits/setsid ; pas de namespace) |
| §2 DA2 — verdict = sûreté/cohérence ; `expected` minimal non décisif → aucune modif crawler | T8 (`expected` passé inerte) ; aucun fichier crawler PROD modifié |
| §2 DA3 — checks = type_sniff + ffprobe ; registre branchable + worst-status ; clamav réservé | T2, T3, T4 (registre `enabled_checks`, clamav ignoré) |
| §2 DA4 — clamav follow-up post-Plan F | T4 (nom inconnu ignoré, aucune impl clamav) |
| §2 DA5 — un enfant Python unique par fichier (re-exec, pas fork) | T6 (`run_analysis` re-exec via `sys.executable -m`), T7 (l'enfant) |
| §2 DA6 — mapping déterministe, toujours 200 (absent→error ; crash/timeout/illisible→suspicious ; OK→worst) | T5 (egress), T8 (check : absent→error sinon spawn→egress) |
| §2 DA7 — type_sniff = danger absolu (exécutable→malicious, archive→suspicious, média→clean, inconnu→clean) | T2 (table complète + PureError) |
| §2 DA8 — enfant vierge : close_fds, env explicite minimal (pas os.environ), revalidation hash | T6 (`_minimal_env`, `close_fds`), T7 (revalidation hash, lecture RO bornée) |
| §2 DA9 — 100 % branch via runners injectés ; subprocess réel derrière marqueur | T3/T6 (`FfprobeRunner`/`ChildRunner` injectables ; `# pragma: no cover` sur le subprocess réel + `_confine`) ; T9 (`analysis_integration`) |
| §2 DA10 — défauts config ; flags ffprobe + API puremagic figés | T1 (config + défauts) ; T3 (flags ffprobe figés) ; T2 (API puremagic figée, Step 0) |
| §3 Structure paquet + couture `check.verify_file` stable + crawler intouché | File Structure + T8 (couture, app.py inchangé) |
| §4 Enfant & confinement (argv/env/tmpdir/timeout/rlimits/égress borné) | T6 (spawn), T7 (enfant), T5 (égress borné + schéma) |
| §5 Checks, real_meta, agrégation (worst-status, fusion meta, trace checks) | T2, T3 (real_meta), T4 (agrégation/fusion/trace) |
| §6 Flux & mapping verdict (table absent/crash/OK) | T5 (egress), T8 (check) |
| §7 Erreurs (octets dans l'enfant seul ; défense en profondeur ; service ne lève jamais ; rlimit/timeout→suspicious) | T6 (parent ne lit pas d'octets), T7 (enfant), T5 (jamais d'exception), T9 (timeout réel) |
| §8 Config verifier (`AnalysisConfig` + from_env ; dep puremagic ; ffprobe système) | T1 (config + dep) ; T9 (ffprobe système) |
| §9 Tests (unitaire 100 % branch sans subprocess + `analysis_integration`) | T2-T8 (unitaires) ; T9 (intégration) |
| §10 Modèle de données & contrat de fil INCHANGÉS | T8 (contrat de fil identique ; aucune migration ; e2e prouve la chaîne) |
| §11 Hors-périmètre/reporté | Header + T4 (clamav réservé) |
| §12 Risques (ffprobe = parseur hostile sous rlimits ; preexec_fn POSIX-only ; flags figés) | T3 (ffprobe en petit-fils confiné), T6 (`_confine` Linux), T9 (timeout-kill réel) |

**Self-review — résultats :**

1. **Couverture spec §1–§12 / DA1–DA10** : chaque section et chaque décision est mappée à au moins une tâche (table ci-dessus). Aucune lacune. clamav reste volontairement un créneau ignoré (DA4, hors périmètre).

2. **Placeholder scan** : AUCUN « TBD », « add error handling », « similar to », step sans code. Le code de chaque fichier source ET de chaque test est complet et copiable. Les seuls renvois sont (a) des **Notes couverture** qui anticipent des points chauds et donnent la marche à suivre exacte (ex. brancher/retirer une branche morte de `_classify` après vérification empirique du mime puremagic ; déplacer la couverture des défauts `cfg/runner` dans la structure de `check.py`), (b) des consignes de RE-confirmation context7 (Step 0 de T1/T2). Ce sont des garde-fous d'exécution, pas du code laissé en blanc.

3. **Cohérence des types/signatures (vérifiée transversalement)** :
   - `AnalysisConfig` (T1) : champs `enabled_checks`/`ffprobe_path`/`timeout_s`/rlimits/`egress_cap_bytes`/`header_bytes`/`quarantine_dir` ; consommée par `ffprobe.probe` (T3), `pipeline.run` (T4), `egress.parse` (T5), `spawn.run_analysis`/`_minimal_env`/`ProdChildRunner` (T6), `analysis_child.main` (T7), `check.verify_file` (T8). ✔
   - `CheckOutcome(name, status: Status, meta: Mapping)` + `worst_status` + `STATUS_RANK` (T1) : produits par `type_sniff.sniff` (T2) et `ffprobe.probe` (T3), agrégés par `pipeline.run` (T4) ; `STATUS_RANK` réutilisé par `egress` pour l'enum (T5). ✔
   - `FfprobeRunner` (Protocol `__call__(argv) -> (int, bytes)`, T3) : injecté dans `probe` (T3), `pipeline.run` (T4), `analysis_child.main` (T7) ; `ProdFfprobeRunner` (T3) = défaut PROD de l'enfant (T7) + intégration (T9). ✔
   - `ChildRunner` (Protocol `__call__(argv, *, cwd, env, timeout) -> (int, bytes, bool)`, T6) : injecté dans `run_analysis` (T6) et `check.verify_file` (T8) ; `ProdChildRunner` (T6) = défaut PROD de `check` (T8) + intégration (T9). ✔
   - `egress.parse(stdout, returncode, timed_out, cfg) -> (verdict, real_meta, checks)` (T5) : appelée par `run_analysis` (T6). ✔
   - `pipeline.run(header, path, ffprobe_runner, cfg) -> (verdict, real_meta, checks)` (T4) : appelée par `analysis_child.main` (T7). ✔
   - `check.verify_file(quarantine_path, expected, *, cfg=None, runner=None)` (T8) : signature positionnelle INCHANGÉE (les nouveaux params sont keyword-only optionnels) → `app.py` intouché. ✔
   - Contrat de fil `{verdict, real_meta, checks}` : `analysis_child` l'imprime (T7) → `egress.parse` le lit (T5) → `check.verify_file` le rend (T8) → `app.py` le sérialise (inchangé) → DTO crawler `VerificationResult` (intouché). ✔

4. **APIs externes figées (context7)** : puremagic `from_string(data, mime=True)`/`PureError` (T2, re-confirmé Step 0) ; ffprobe `[path, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", file]` + champs STRING (`duration`/`bit_rate`/`size`/`sample_rate`/`avg_frame_rate`) parsés défensivement, champs INT (`width`/`height`/`channels`/`nb_streams`) (T3). ✔

5. **`app.py` JAMAIS modifié + bascule en DERNIER** : `app.py` n'apparaît dans AUCUNE tâche en « Modify » ; la bascule de `check.verify_file` est en **Task 8** (avant-dernière), l'e2e `verify_integration` reste vert (NO-OP) jusque-là ; Task 8 met à jour son assertion (`unverified`→`suspicious`). `analysis_integration` (Task 9) couvre le cas média `clean` réel. ✔

6. **Risques de couverture 100 % branch anticipés (à surveiller en exécution)** :
   - **`_classify` branche `application/x-…executable` (T2)** : potentiellement morte (les exécutables passent par `_looks_executable` avant). **Action** : vérifier empiriquement le mime puremagic d'un échantillon ; si aucun n'atteint cette branche, la RETIRER (sinon branche morte → < 100 %). La Note couverture T2 le détaille.
   - **Défauts `cfg`/`runner is None` de `check.verify_file` (T8)** : couverts SANS subprocess en résolvant les défauts AVANT le `is_file()` (structure imposée dans la Note couverture T8) → `test_default_cfg_and_runner_are_prod` (fichier absent) exerce les deux branches `is None`-True.
   - **Défauts `cfg`/`ffprobe_runner is None` de `analysis_child.main` (T7)** : couverts par `test_main_defaults_cfg_and_runner_without_real_ffprobe` (monkeypatch de `from_env` + `ProdFfprobeRunner`) → pas de vrai ffprobe en unitaire.
   - **`# pragma: no cover`** : `ProdFfprobeRunner.__call__` (corps), `ProdChildRunner.__call__`/`_confine` (corps), `if __name__ == "__main__":` (T7). Les constructeurs `__init__` NE sont PAS pragma → testés (`test_prod_ffprobe_runner_constructs`, `test_prod_child_runner_constructs`). **Vérifier que le `# pragma: no cover` couvre bien tout le corps de chaque fonction subprocess** (le placer sur la ligne `def …:` si coverage ne l'applique pas à la dernière ligne de signature multi-lignes).
   - **OR court-circuité dans `egress.parse` (T5)** : chaque opérande (`timed_out` / `returncode != 0` / `len > cap`) testé True isolément + tous False (branch coverage exige les deux sorties de chaque condition).

**Nombre de tâches : 9** (1 scaffolding config+base+dep+marqueur ; 2 type_sniff ; 3 ffprobe ; 4 pipeline ; 5 egress ; 6 spawn ; 7 analysis_child ; 8 BASCULE check.verify_file + MAJ e2e ; 9 analysis_integration).
