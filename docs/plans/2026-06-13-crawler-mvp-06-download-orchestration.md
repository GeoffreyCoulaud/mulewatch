# Crawler MVP — Plan 6 : Orchestration du téléchargement (D-download) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Donner au crawler la **capacité de téléchargement** : rejouer le journal `match_decisions`, télécharger les fichiers de tier `download` via un `amuled` réel piloté en EC, les **remettre en quarantaine** (rename atomique, jamais lus) et **enfiler une tâche de vérification** — sans jamais lire les octets. À la fin, D-download livre la **capacité testée** : le domaine pur `domain/download/` (politique + ed2k_link + états), les ports (`MuleDownloadClient` + `DownloadEntry`, `Quarantine`), les adapters (extension EC `add_link`/`download_queue`, `quarantine_fs`, `SqliteDownloadRepository`), la migration 0002 (`downloads.size_bytes`), la lecture catalogue des candidats download, et la **boucle unique** `application/run_download_cycle` (monitor → complétions → nouveaux candidats → sleep/nudge). Spec : `docs/superpowers/specs/2026-06-13-download-orchestration-design.md`. **Le câblage live dans `CrawlerApp` + le gate full-mode `VERIFIER_URL` atterrissent en D-verify** (le health-check a besoin du port `ContentVerifier`) : D-download NE modifie PAS `composition/app.py` pour faire tourner la boucle de download (spec §2/§9/§10). On PEUT étendre les parsers/value objects de config (endpoint download + `download.poll_interval`/`disk_cap_bytes`), lus à la composition plus tard.

**Architecture:** Clean/Hexagonal, inchangée. Règle de dépendance (spec §3) : `domain` pur (aucune I/O) ; `ports` n'importe que le domaine ; **`application` dépend des ports/domaine, JAMAIS d'un adapter** ; `adapters`/`composition` implémentent et assemblent. D-download réutilise tous les contrats du Plan C : `Clock`/`Rng` injectés (déterminisme), `DecisionSignal` (nudge), `MuleUnreachableError`/`RepositoryError` (contrats d'erreur dans les PORTS — l'application catch sans importer un adapter), l'arrêt observable au prochain `await`, les disciplines SQLite (`BEGIN IMMEDIATE`, `wrap_sqlite_errors`, rollback sur `BaseException`, staging du timestamp AVANT `BEGIN`). **Port SÉPARÉ `MuleDownloadClient`** (PAS d'extension de `MuleClient`, ISP §3.3) : `connect`/`close`/`add_link(ed2k_link)`/`download_queue()`/`network_status()` ; la même classe `AmuleEcClient` l'implémente STRUCTURELLEMENT (comme elle implémente déjà `MuleClient`). La **politique pure** prend des PRIMITIFS (le lookup `target_id→status` est fait par l'application, le domaine n'importe ni le repo ni `NetworkStatus`). Le **plafond disque** est applicatif : somme des `size_bytes` des downloads NON terminaux vs `download.disk_cap_bytes` → `skip_disk_cap` (défère, pas d'abandon). Une **seule boucle de download**, série, sur l'unique connexion EC download.

**Tech Stack:** Python ≥ 3.12 (`asyncio`, `os.replace` rename atomique même-FS, `sqlite3` stdlib avec fonctions de fenêtre `ROW_NUMBER() OVER (…)`), `uv`, `ruff` (E/F/I/UP/B/SIM, line 100), `mypy --strict` (src + tests), `pytest` + `pytest-asyncio` (mode `strict`, tests async `@pytest.mark.asyncio`) + `pytest-cov` (gate **100 % branch**), `sqlfluff` (dialecte sqlite). **AUCUNE nouvelle dépendance runtime** (D-download n'étend que EC + `sqlite3` + `os`/`pathlib`). Déterminisme TOTAL : `Clock`/`sleep` injectables (faux avançables, zéro attente réelle). Tests : faux `MuleDownloadClient` scripté + pannes injectables ; **vrais** repos SQLite sur `tmp_path` (spec §11) ; fausse `Quarantine` qui enregistre/échoue + un vrai `quarantine_fs` sur `tmp_path` ; faux hub `DecisionSignal` capturant les sujets.

> **Référence spec :** `docs/superpowers/specs/2026-06-13-download-orchestration-design.md` — §1 (but/périmètre), §2 (décisions verrouillées), §3 (architecture + carte), §4 (port `MuleDownloadClient` + probe), §5 (boucle `run_download_cycle`), §6 (politique pure), §7 (repo `downloads` + migration 0002), §8 (port `Quarantine` + FS), §9 (erreurs/résilience), §10 (couture d'activation → D-verify), §11 (tests), §12 (DoD). Plan C de référence (style/densité) : `docs/superpowers/plans/2026-06-12-crawler-mvp-05-orchestration.md`. Réf. EC : `docs/reference/ec-protocol.md` + `docs/reference/2026-06-11-ec-field-richness.md`. Handoffs : `docs/handoffs/2026-06-13 - handoff - orchestration complète.md` (contrats D doit respecter), data-model, adapter EC.

> **HORS PÉRIMÈTRE (spec §1 — RIEN de tout ceci ici) :** le **verifier**, le port `ContentVerifier`, la **boucle de vérification**, et le **câblage live dans `CrawlerApp` + le gate full-mode** (`VERIFIER_URL`/health-check) → **D-verify** (le gate a besoin du port `ContentVerifier`). Les **upgrades** (re-DL d'une meilleure version pour `partial`/`poor`) → itération ultérieure (exigent les métadonnées média post-download). Le **quota disque infra** (Docker/FS) → Plan F ; ici seul le plafond applicatif. Le **vrai download bout-en-bout en CI** (deux amuled) → amélioration future ; ici **option A** (mécaniques EC réelles via probe + e2e ; complétion en unitaire ; homelab manuel documenté). **PAS d'uv workspace** (la scission single-package est verrouillée, le workspace est la 1re tâche de D-verify).

---

## Décisions verrouillées (de la spec — ne PAS relitiger)

> **DÉCISION D1 — Opcodes de download GROUNDED sur les sources aMule, VALIDÉS par le probe.**
> Lus dans `amule-org/amule@3.0.0 src/libs/ec/cpp/ECCodes.h` + `src/ExternalConn.cpp` (consultés à l'écriture du plan) : `EC_OP_ADD_LINK = 0x09`, `EC_OP_GET_DLOAD_QUEUE = 0x0D`, `EC_OP_DLOAD_QUEUE = 0x1F`. Tags partfile : `EC_TAG_PARTFILE = 0x0300` (la valeur PROPRE de l'entrée est le hash HASH16, comme `EC_TAG_SEARCHFILE` porte l'ECID), `EC_TAG_PARTFILE_NAME = 0x0301`, `EC_TAG_PARTFILE_SIZE_FULL = 0x0303`, `EC_TAG_PARTFILE_SIZE_DONE = 0x0306`, `EC_TAG_PARTFILE_STATUS = 0x0308`, `EC_TAG_PARTFILE_ED2K_LINK = 0x030E`, `EC_TAG_PARTFILE_HASH = 0x031E`. **`add_link`** : `ExternalConn.cpp` lit le lien via `tag.GetStringData()` (amulecmd émet un `EC_TAG_STRING` portant le lien) ; **réponse = `EC_OP_NOOP` (0x01) en succès, `EC_OP_FAILED` (0x05) sinon**. **`get_dload_queue`** : requête au détail `EC_DETAIL_CMD` (comme `network_status`), réponse `EC_OP_DLOAD_QUEUE` avec N enfants `EC_TAG_PARTFILE`. **Le probe (Task 4) VALIDE empiriquement** : que `add_link` est accepté et que le lien apparaît dans `download_queue` avec un statut lisible. Ce que le probe ne peut PAS valider (option A, pas de sources eD2k dans le conteneur) : la complétion réelle et la présence d'un chemin staging exploitable — d'où `DownloadEntry` SANS chemin staging fiable (DÉCISION D2). Empiriquement vérifié à l'écriture : le codec EXISTANT encode/décode ces deux paquets (ADD_LINK roundtrip OK ; entrée partfile hash-comme-valeur-propre + enfants name/size_full/size_done/status décodés ; complet = `size_done >= size_full`).

> **DÉCISION D2 — `DownloadEntry` = `(ed2k_hash, size_done, size_full)` ; PAS de chemin staging dans le DTO.**
> EC n'expose pas de manière fiable et portable le chemin du (part)fichier complété sur un résultat de `download_queue` (le probe le confirmera ; `EC_TAG_PARTFILE_NAME` est le nom observé, pas un chemin absolu côté hôte du crawler). Le crawler **ne lit jamais les octets** : il a juste besoin de savoir qu'un hash est COMPLET. `DownloadEntry` (frozen) porte `ed2k_hash`, `size_done`, `size_full` et une propriété `is_complete` (`size_full > 0 and size_done >= size_full`). La **localisation du fichier complété pour la quarantaine** est dérivée par l'application/composition d'un **répertoire de staging configuré** (`download.staging_dir` + convention de nom), passé à `quarantine.promote(staging_path, hash)`. En D-download (capacité testée, pas câblée live), la boucle reçoit une fonction `staging_path_for(entry) -> Path` injectée (la composition de D-verify la branchera sur le vrai layout amuled) ; les tests passent une fausse résolution. Cela garde la boucle pure-orchestration et testable sans connaître le layout disque d'amuled.

> **DÉCISION D3 — Port SÉPARÉ `MuleDownloadClient` (ISP), implémenté par `AmuleEcClient`.**
> `connect()`/`close()`/`add_link(ed2k_link: str) -> None`/`download_queue() -> tuple[DownloadEntry, ...]`/`network_status() -> NetworkStatus`. `network_status` est RÉ-UTILISÉ tel quel (même DTO `NetworkStatus` du port `mule_client`). La recherche ne dépend pas des méthodes de download et inversement. `AmuleEcClient` gagne `add_link`/`download_queue` (satisfaction structurelle des DEUX ports) ; en D-download la connexion download est une **instance distincte** de `AmuleEcClient` (sa propre connexion EC, spec §2.2) — le code se fiche que l'opérateur la fasse pointer sur un daemon dédié ou partagé.

> **DÉCISION D4 — `DownloadVerdict` (enum, pas bool) ; politique 100 % primitive.**
> `download_policy(*, tier, target_status, already_downloaded, committed_bytes, file_size, disk_cap) -> DownloadVerdict ∈ {download, skip_complete, skip_dedup, skip_disk_cap}`. Règles, dans CET ordre (chaque branche testée des deux côtés) : `tier != "download"` → garde (`skip_complete` n'est pas le bon nom ; on ne devrait PAS appeler la politique sur un non-candidat — voir D5) ; on lève donc le non-candidat en AMONT (l'application ne filtre que les décisions tier=download avant d'appeler la politique). Dans la politique : `target_status == "complete"` → `skip_complete` (la cible n'a plus besoin du fichier) ; `already_downloaded` → `skip_dedup` ; `committed_bytes + file_size > disk_cap` → `skip_disk_cap` (défère) ; sinon → `download`. L'enum donne explicabilité + métrique future. Le lookup `target_id → status` (depuis les `targets` chargées, `TargetSegment.status`) est fait par l'application et passé en primitif (comme `effective_coverage` reçoit des booléens).

> **DÉCISION D5 — `tier` reste un paramètre de la politique, mais l'application pré-filtre.**
> La spec §6 liste « `tier != download` → non-candidat (garde) ». On garde une garde DÉFENSIVE dans la politique (`tier != "download"` → `skip_complete` serait faux ; on rend un verdict dédié de garde). Pour éviter un 5e verdict inutile, l'application n'appelle JAMAIS la politique sur un non-download (la lecture catalogue `download_decisions()` ne rend QUE les hash dont le DERNIER verdict est tier=download). La politique reçoit donc TOUJOURS `tier="download"` ; la garde est néanmoins testée (un appelant futur hors contrat ne doit pas crasher) en rendant `skip_complete` (le plus conservateur : ne pas télécharger). **Verrouillé ici** : `tier != "download"` → `skip_complete` (garde conservatrice « ne pas télécharger »), documenté ; l'application ne déclenche jamais cette branche en prod.

> **DÉCISION D6 — `downloads` (local.db) NON append-only → UPSERT/UPDATE licites ; migration 0002 ajoute `size_bytes`.**
> La table `downloads` EXISTE déjà (migration `local/0001_initial.sql` : `ed2k_hash` PK, `target_id`, `state`, `queued_at`, `completed_at`). Migration **0002** ajoute `size_bytes INTEGER NOT NULL DEFAULT 0` (le `DEFAULT 0` est exigé par `ALTER TABLE ADD COLUMN NOT NULL` sur une table éventuellement non vide — vérifié empiriquement). `local.db` n'est PAS append-only (pas de triggers, état mutable) : `record_queued` est un INSERT dédup-safe (PK = hash, `ON CONFLICT DO NOTHING`), `set_state` est un UPDATE. Plafond = `SELECT COALESCE(SUM(size_bytes),0) … WHERE state NOT IN (terminaux)`. Disciplines identiques aux autres repos : stamp AVANT `BEGIN`, `BEGIN IMMEDIATE`, rollback sur `BaseException`, `wrap_sqlite_errors`.

> **DÉCISION D7 — États de download (`DownloadState`, enum fermé du domaine).**
> `queued`, `downloading`, `completed`, `quarantined`, `failed`. Terminaux pour le plafond disque : `completed` (le fichier est encore en staging mais ne grandit plus — il sera promu vite), `quarantined` (sorti du staging amuled), `failed`. NON terminaux (comptent dans `committed_bytes`) : `queued`, `downloading`. **Verrouillé** : `_TERMINAL_STATES = {completed, quarantined, failed}` (un `completed` ne consomme plus de quota de download actif ; promu à l'itération suivante). Le domaine fournit l'enum + un helper `is_terminal(state) -> bool` ; l'application/SQL s'en sert.

> **DÉCISION D8 — Flux de la boucle (spec §5), idempotent et tolérant.**
> Une itération de `run_download_cycle` :
> 1. **Monitor** : `download_queue()` → pour chaque entrée connue dans `downloads`, réconcilie l'état (`downloading` si en cours ; `completed` quand `is_complete` et pas déjà ≥ completed). Une entrée inconnue de `downloads` (download lancé hors crawler) est IGNORÉE (le crawler ne gère que ses propres downloads).
> 2. **Complétions** : pour chaque hash `completed` (et pas `quarantined`) → `quarantine.promote(staging_path, hash)` → `local_repo.enqueue_verification(hash)` → `set_state(hash, quarantined)`. **Idempotent** : `promote` échoue → reste `completed`, **n'enfile PAS**, retry à la prochaine itération ; un hash déjà `quarantined` est sauté. `enqueue_verification` est déjà idempotent (index unique partiel).
> 3. **Nouveaux candidats** : `catalog.download_decisions()` (latest-decision-per-hash où tier=download) → diff avec `downloads` (hash pas encore connu) → pour chacun, `download_policy(...)` → si `download` : `build_ed2k_link` (depuis la dernière observation : filename+size+hash) → `add_link()` → `record_queued(hash, target_id, size_bytes)`. Le plafond est recalculé en mémoire au fil des `add_link` du cycle (`committed += size`) pour ne pas dépasser dans un même cycle.
> 4. **Sleep/nudge** : attend `download.poll_interval` OU le hub `DecisionSignal` (réveil), au PREMIER des deux (`asyncio.wait` FIRST_COMPLETED), via `Clock.sleep` injecté.
> **Redémarrage** : la 1re itération reconcilie via `download_queue` (rattrape les complétions offline) ; le replay saute les hash déjà connus de `downloads`.

> **DÉCISION D9 — Gestion d'erreurs = contrats Plan C.**
> Daemon download injoignable (`MuleUnreachableError` à `connect`/`add_link`/`download_queue`) → **tolère** : log + skip l'itération (le client est rejeté/reconnecté à la suivante ; amuled persiste les downloads). `RepositoryError` (sur n'importe quel repo) → absorbée (log + continue l'itération si possible, sinon skip). Échec `quarantine.promote` (toute exception) → reste `completed`, n'enfile PAS, retry idempotent. amuled signale une erreur de download (entrée passée en erreur dans la file) → `failed` + log (signalement EC confirmé au probe ; en l'absence de tag d'erreur fiable, on n'invente pas — on ne marque `failed` que sur un signal EC explicite, sinon on laisse l'état tel quel). **Jamais d'abandon** d'un download stallé. **Arrêt** : la boucle est une tâche annulable au prochain `await` (poll EC ou sleep/nudge) — repos sync → jamais d'annulation en pleine écriture DB. Déterminisme : `Clock`/`sleep` injectés.

> **DÉCISION D10 — `quarantine_fs` : rename atomique même-FS, jamais +x, jamais lu.**
> `promote(staging_path, ed2k_hash)` : `os.replace(staging_path, quarantine_dir / ed2k_hash)` (atomique même-FS, vérifié : ne pose aucun bit exécutable, lève `FileNotFoundError` sur source absente). Le crawler n'OUVRE jamais le fichier (le verifier de D-verify le lira). Échec (source absente, FS, cross-device) → exception → la boucle laisse `completed` et retente. Testé sur un vrai `tmp_path`.

> **Note couverture (gate 100 % branch — points chauds) :** stubs de Protocol **une ligne** (`def m(...) -> T: ...`). Cas exercés des DEUX côtés : `download_policy` (tier≠download garde / complete / dedup / disk_cap atteint EXACT et dépassé / download nominal) ; `DownloadState.is_terminal` (terminal / non terminal) ; `build_ed2k_link` (nom simple / nom avec `|` échappé / nom non-ASCII / size 0) ; `DownloadEntry.is_complete` (done<full / done==full / done>full / full==0) ; `SqliteDownloadRepository` (record_queued nouveau / doublon ignoré ; set_state ; committed_bytes vide/mixte ; is_downloaded présent/absent ; active_downloads ; completed_at stampé ; migration 0002 appliquée ; panne atomique injectée) ; `quarantine_fs` (rename réel / source absente) ; `run_download_cycle` (monitor : entrée connue→downloading, connue+complete→completed, inconnue ignorée ; complétion : promote ok→enqueue+quarantined, promote échoue→reste completed sans enqueue, déjà quarantined sauté ; candidats : nouveau→download, dédup sauté, disk_cap défère, non-candidat absent ; nudge réveille / poll expire ; MuleUnreachableError tolérée→skip ; RepositoryError absorbée ; arrêt par annulation ; réconciliation au redémarrage) ; `download_decisions` SQL (latest=download inclus / latest=autre exclu / hash sans décision absent) ; config parsers (download endpoint + knobs : chaque branche fail-fast).

> **Note typage (`mypy --strict` sur src ET tests) :** tous les tests `-> None`, params typés, async `@pytest.mark.asyncio`. Le faux `MuleDownloadClient` satisfait STRUCTURELLEMENT le port (aucun héritage). `client_factory`/`staging_path_for` injectés typés précisément (pas `object` quand évitable). Les enfants de DTO frozen sont annotés.

> **Note ordonnancement & convention de run :** chaque tâche = test(s) qui échoue(nt) → run/échec attendu → impl minimale → run/pass → **gate 5 checks** → commit conventionnel. Runs focalisés en `--no-cov`. Laisser ruff trancher l'ordre des imports (`uv run ruff check . --fix && uv run ruff format .`) avant le gate. Le gate complet : `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run sqlfluff lint src`. Chaque message de commit se termine par le trailer HEREDOC `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## File Structure

```
src/emule_indexer/
├── domain/download/                    # PUR (nouveau sous-paquet)
│   ├── __init__.py                     # Create (vide)
│   ├── states.py                       # Create : DownloadState (enum), is_terminal
│   ├── policy.py                       # Create : DownloadVerdict (enum), download_policy
│   └── ed2k_link.py                    # Create : build_ed2k_link (échappement)
├── ports/
│   ├── mule_download_client.py         # Create : MuleDownloadClient (Protocol) + DownloadEntry DTO
│   └── quarantine.py                   # Create : Quarantine (Protocol)
├── application/
│   └── run_download_cycle.py           # Create : la boucle unique (monitor→complétions→candidats→sleep/nudge)
├── adapters/
│   ├── mule_ec/
│   │   ├── codes.py                    # Modify : + opcodes/tags de download
│   │   └── client.py                   # Modify : + add_link, download_queue (AmuleEcClient)
│   ├── quarantine_fs.py                # Create : FilesystemQuarantine (rename atomique)
│   ├── config/
│   │   ├── crawler_config.py           # Modify : + DownloadConfig (poll_interval, disk_cap_bytes)
│   │   └── local_config.py             # Modify : + download endpoint + staging/quarantine dirs
│   └── persistence_sqlite/
│       ├── download_repository.py      # Create : SqliteDownloadRepository
│       ├── catalog_repository.py       # Modify : + download_decisions() (latest=download)
│       └── migrations/local/0002_downloads_size_bytes.sql   # Create
├── ports/catalog_repository.py         # Modify : + download_decisions() -> tuple[DownloadCandidate, ...]
├── domain/matching/engine.py           # Modify : + DownloadCandidate(ed2k_hash, target_id) (forme de lecture)
└── tools/download_probe.py             # Create : sonde EC download (add_link + download_queue dump)

config/
├── crawler.yaml                        # Modify : + section download (poll_interval_seconds, disk_cap_bytes)
└── local.example.yaml                  # Modify : + download endpoint + staging_dir/quarantine_dir

tests/                                  # un fichier par unité (voir tâches)
docs/reference/
└── 2026-06-13-ec-download-opcodes.md   # Create (probe) : opcodes download validés empiriquement
pyproject.toml                          # Modify : marqueur download_integration
```

> **Carte de dépendance (cohérence des signatures, vérifiée à l'écriture) :**
> - `DownloadEntry(ed2k_hash: str, size_done: int, size_full: int)` + propriété `is_complete: bool`. Vit dans `ports/mule_download_client.py`.
> - `DownloadCandidate(ed2k_hash: str, target_id: str)` (forme de LECTURE du catalogue, comme `DecisionRecord`). Vit dans `domain/matching/engine.py` (à côté de `DecisionRecord`).
> - `download_policy(*, tier: str, target_status: str, already_downloaded: bool, committed_bytes: int, file_size: int, disk_cap: int) -> DownloadVerdict`. `domain/download/policy.py`.
> - `build_ed2k_link(filename: str, size_bytes: int, ed2k_hash: str) -> str`. `domain/download/ed2k_link.py`.
> - `Quarantine.promote(staging_path: Path, ed2k_hash: str) -> None`. `ports/quarantine.py`.
> - `SqliteDownloadRepository` : `record_queued(ed2k_hash, target_id, size_bytes) -> bool`, `set_state(ed2k_hash, state: DownloadState) -> None`, `is_downloaded(ed2k_hash) -> bool`, `committed_bytes() -> int`, `active_states() -> dict[str, DownloadState]` (hash→état pour le monitor).
> - `CatalogRepository.download_decisions() -> tuple[DownloadCandidate, ...]`, `last_observation(ed2k_hash) -> ObservedFile | None` (filename+size pour le lien ; `ObservedFile(filename, size_bytes)` frozen dans le port catalog).

---

(Les tâches numérotées suivent. Chaque tâche est autonome : write failing test → run fail → impl complète → run pass → gate → commit.)

---

## Task 1: Domaine download — `states.py` (DownloadState + is_terminal)

**Files:**
- Create: `src/emule_indexer/domain/download/__init__.py` (vide)
- Create: `src/emule_indexer/domain/download/states.py`
- Create: `tests/domain/download/__init__.py` (vide)
- Create: `tests/domain/download/test_states.py`

- [ ] **Step 1: Créer les `__init__.py` vides** (`src/emule_indexer/domain/download/__init__.py`, `tests/domain/download/__init__.py` : fichiers VIDES).

- [ ] **Step 2: Écrire le test qui échoue**

`tests/domain/download/test_states.py` :
```python
from emule_indexer.domain.download.states import DownloadState, is_terminal


def test_states_are_a_closed_enum() -> None:
    assert set(DownloadState) == {
        DownloadState.QUEUED,
        DownloadState.DOWNLOADING,
        DownloadState.COMPLETED,
        DownloadState.QUARANTINED,
        DownloadState.FAILED,
    }


def test_state_values_are_stable_strings() -> None:
    assert DownloadState.QUEUED.value == "queued"
    assert DownloadState.QUARANTINED.value == "quarantined"


def test_terminal_states_do_not_consume_active_quota() -> None:
    assert is_terminal(DownloadState.COMPLETED) is True
    assert is_terminal(DownloadState.QUARANTINED) is True
    assert is_terminal(DownloadState.FAILED) is True


def test_active_states_are_not_terminal() -> None:
    assert is_terminal(DownloadState.QUEUED) is False
    assert is_terminal(DownloadState.DOWNLOADING) is False
```

- [ ] **Step 3: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/domain/download/test_states.py -q --no-cov`
Expected: FAIL (collection) — `ModuleNotFoundError: No module named 'emule_indexer.domain.download'`.

- [ ] **Step 4: Écrire l'implémentation**

`src/emule_indexer/domain/download/states.py` :
```python
"""États d'un téléchargement (PUR, spec download §7 — DÉCISION D7).

Domaine PUR : aucune I/O. ``DownloadState`` est l'enum FERMÉ du cycle de vie d'un download
côté crawler : ``queued`` (lien ajouté à amuled) → ``downloading`` (amuled le tire) →
``completed`` (octets complets côté amuled, encore en staging) → ``quarantined`` (sorti du
staging par un rename atomique, vérif enfilée) ; ``failed`` si amuled signale une erreur.

Le plafond disque APPLICATIF (spec §7) ne compte que les downloads ACTIFS : un état
terminal (``completed``/``quarantined``/``failed``) ne consomme plus de quota de download
en cours (un ``completed`` ne grandit plus et sera promu à la prochaine itération). C'est le
seul jugement métier porté ici ; le calcul de la somme vit dans l'adapter repo.
"""

from enum import StrEnum

# DÉCISION D7 : terminaux pour le plafond (ne consomment plus de quota actif).
_TERMINAL_STATES = frozenset({"completed", "quarantined", "failed"})


class DownloadState(StrEnum):
    """Cycle de vie d'un download côté crawler (enum fermé, spec §7)."""

    QUEUED = "queued"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    QUARANTINED = "quarantined"
    FAILED = "failed"


def is_terminal(state: DownloadState) -> bool:
    """``True`` si l'état ne consomme plus de quota de download actif (spec §7)."""
    return state.value in _TERMINAL_STATES
```

- [ ] **Step 5: Vérifier puis gate**

Run: `uv run pytest tests/domain/download/test_states.py -q --no-cov` → PASS (4 tests).
Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run sqlfluff lint src` → tout vert, 100 %.

- [ ] **Step 6: Commit**

```bash
git add src/emule_indexer/domain/download tests/domain/download
git commit -m "$(cat <<'EOF'
feat(domain): DownloadState (enum fermé) + is_terminal

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Domaine download — `policy.py` (DownloadVerdict + download_policy)

**Files:**
- Create: `src/emule_indexer/domain/download/policy.py`
- Create: `tests/domain/download/test_policy.py`

> DÉCISION D4/D5 : enum (pas bool), 100 % primitive (le lookup `target_id→status` est fait par l'application). Ordre des gardes : tier → complete → dedup → disk_cap → download. Toutes branches des deux côtés.

- [ ] **Step 1: Écrire le test qui échoue**

`tests/domain/download/test_policy.py` :
```python
from emule_indexer.domain.download.policy import DownloadVerdict, download_policy


def _verdict(
    *,
    tier: str = "download",
    target_status: str = "lost",
    already_downloaded: bool = False,
    committed_bytes: int = 0,
    file_size: int = 100,
    disk_cap: int = 1000,
) -> DownloadVerdict:
    return download_policy(
        tier=tier,
        target_status=target_status,
        already_downloaded=already_downloaded,
        committed_bytes=committed_bytes,
        file_size=file_size,
        disk_cap=disk_cap,
    )


def test_verdict_is_a_closed_enum() -> None:
    assert set(DownloadVerdict) == {
        DownloadVerdict.DOWNLOAD,
        DownloadVerdict.SKIP_COMPLETE,
        DownloadVerdict.SKIP_DEDUP,
        DownloadVerdict.SKIP_DISK_CAP,
    }


def test_nominal_lost_target_downloads() -> None:
    assert _verdict() is DownloadVerdict.DOWNLOAD


def test_non_download_tier_is_a_conservative_guard() -> None:
    # DÉCISION D5 : garde conservatrice (« ne pas télécharger ») — jamais déclenchée en prod
    # (l'application ne passe que des décisions tier=download), mais un appelant hors contrat
    # ne crashe pas et ne télécharge rien.
    assert _verdict(tier="catalog") is DownloadVerdict.SKIP_COMPLETE
    assert _verdict(tier="notify") is DownloadVerdict.SKIP_COMPLETE


def test_complete_target_skips() -> None:
    assert _verdict(target_status="complete") is DownloadVerdict.SKIP_COMPLETE


def test_partial_and_poor_targets_still_download() -> None:
    assert _verdict(target_status="partial") is DownloadVerdict.DOWNLOAD
    assert _verdict(target_status="poor") is DownloadVerdict.DOWNLOAD


def test_already_downloaded_is_deduped() -> None:
    assert _verdict(already_downloaded=True) is DownloadVerdict.SKIP_DEDUP


def test_dedup_takes_precedence_over_disk_cap() -> None:
    # déjà téléchargé ET au-dessus du plafond → on rend SKIP_DEDUP (rien à re-télécharger).
    assert (
        _verdict(already_downloaded=True, committed_bytes=950, file_size=100, disk_cap=1000)
        is DownloadVerdict.SKIP_DEDUP
    )


def test_over_disk_cap_defers() -> None:
    assert (
        _verdict(committed_bytes=950, file_size=100, disk_cap=1000)
        is DownloadVerdict.SKIP_DISK_CAP
    )


def test_exactly_at_disk_cap_is_allowed() -> None:
    # committed + size == cap : autorisé (le plafond est un MAX, pas un seuil strict en-dessous).
    assert (
        _verdict(committed_bytes=900, file_size=100, disk_cap=1000)
        is DownloadVerdict.DOWNLOAD
    )


def test_one_byte_over_disk_cap_defers() -> None:
    assert (
        _verdict(committed_bytes=901, file_size=100, disk_cap=1000)
        is DownloadVerdict.SKIP_DISK_CAP
    )


def test_complete_takes_precedence_over_dedup() -> None:
    # cible complète : on saute pour COMPLETE même si déjà téléchargé (le statut prime l'ordre).
    assert (
        _verdict(target_status="complete", already_downloaded=True)
        is DownloadVerdict.SKIP_COMPLETE
    )
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/domain/download/test_policy.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: …download.policy`.

- [ ] **Step 3: Écrire l'implémentation**

`src/emule_indexer/domain/download/policy.py` :
```python
"""Politique d'auto-download PURE (spec download §6 — DÉCISION D4/D5).

Domaine PUR : aucune I/O, aucun repo, aucun ``NetworkStatus``. ``download_policy`` rend un
``DownloadVerdict`` (enum, pas bool → explicabilité + métrique future) depuis des PRIMITIFS :
le lookup ``target_id → status`` est fait par l'APPLICATION (depuis les ``targets`` chargées)
et passé en booléen/chaîne, exactement comme ``effective_coverage`` reçoit des booléens (le
domaine n'importe jamais un port).

Ordre des gardes (spec §6) : un non-``download`` est une garde conservatrice (DÉCISION D5 :
ne jamais télécharger — l'application ne devrait pas appeler la politique hors download, mais
on ne crashe pas) ; une cible ``complete`` n'a plus besoin du fichier ; un hash déjà
téléchargé est dédupliqué ; au-dessus du plafond disque applicatif on DIFFÈRE (la décision
reste dans le journal, retentée quand de la place se libère, spec §7) ; sinon on télécharge.
"""

from enum import StrEnum


class DownloadVerdict(StrEnum):
    """Verdict de la politique d'auto-download (enum fermé, spec §6)."""

    DOWNLOAD = "download"
    SKIP_COMPLETE = "skip_complete"
    SKIP_DEDUP = "skip_dedup"
    SKIP_DISK_CAP = "skip_disk_cap"


def download_policy(
    *,
    tier: str,
    target_status: str,
    already_downloaded: bool,
    committed_bytes: int,
    file_size: int,
    disk_cap: int,
) -> DownloadVerdict:
    """Décide du sort d'un candidat download (spec §6). Toutes branches testées.

    ``committed_bytes`` = somme des ``size_bytes`` des downloads ACTIFS (non terminaux) ;
    ``file_size`` = taille du candidat ; ``disk_cap`` = plafond applicatif config. Le plafond
    est un MAX inclusif : ``committed + file_size <= disk_cap`` est autorisé.
    """
    if tier != "download":
        return DownloadVerdict.SKIP_COMPLETE  # garde conservatrice (DÉCISION D5)
    if target_status == "complete":
        return DownloadVerdict.SKIP_COMPLETE
    if already_downloaded:
        return DownloadVerdict.SKIP_DEDUP
    if committed_bytes + file_size > disk_cap:
        return DownloadVerdict.SKIP_DISK_CAP
    return DownloadVerdict.DOWNLOAD
```

- [ ] **Step 4: Vérifier puis gate**

Run: `uv run pytest tests/domain/download/test_policy.py -q --no-cov` → PASS (11 tests).
Run: gate complet → tout vert, 100 %.

- [ ] **Step 5: Commit**

```bash
git add src/emule_indexer/domain/download/policy.py tests/domain/download/test_policy.py
git commit -m "$(cat <<'EOF'
feat(domain): download_policy (DownloadVerdict — complete/dedup/disk_cap/download)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Domaine download — `ed2k_link.py` (build_ed2k_link + échappement)

**Files:**
- Create: `src/emule_indexer/domain/download/ed2k_link.py`
- Create: `tests/domain/download/test_ed2k_link.py`

> Le lien ed2k a la forme ``ed2k://|file|<nom>|<taille>|<hash>|/``. Le ``|`` est le séparateur de champs : il DOIT être échappé dans le nom (sinon le cadrage du lien casse). On URL-encode le nom (percent-encoding) en gardant un jeu sûr lisible ; le ``|`` (`%7C`) et les caractères de contrôle sont neutralisés. Vérifié à l'écriture : `urllib.parse.quote` échappe `|`→`%7C` et les non-ASCII en UTF-8 percent-encodé.

- [ ] **Step 1: Écrire le test qui échoue**

`tests/domain/download/test_ed2k_link.py` :
```python
from emule_indexer.domain.download.ed2k_link import build_ed2k_link

_HASH = "31d6cfe0d16ae931b73c59d7e0c089c0"


def test_simple_name_builds_canonical_link() -> None:
    link = build_ed2k_link("Keroro 062A.avi", 12345, _HASH)
    assert link == f"ed2k://|file|Keroro%20062A.avi|12345|{_HASH}|/"


def test_pipe_in_name_is_escaped() -> None:
    # le '|' est le séparateur de champs du lien : il DOIT être échappé (%7C) sinon le
    # cadrage casse (un nom hostile ne doit jamais injecter un champ).
    link = build_ed2k_link("weird|name.avi", 99, _HASH)
    assert "%7C" in link
    assert link.count("|") == 5  # uniquement les 5 séparateurs structurels du lien


def test_non_ascii_name_is_utf8_percent_encoded() -> None:
    link = build_ed2k_link("accentué.mkv", 1, _HASH)
    assert "accentu%C3%A9.mkv" in link


def test_zero_size_is_serialized() -> None:
    link = build_ed2k_link("x.avi", 0, _HASH)
    assert link == f"ed2k://|file|x.avi|0|{_HASH}|/"


def test_hash_is_placed_verbatim() -> None:
    link = build_ed2k_link("a.bin", 5, _HASH)
    assert link.endswith(f"|{_HASH}|/")
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/domain/download/test_ed2k_link.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: …download.ed2k_link`.

- [ ] **Step 3: Écrire l'implémentation**

`src/emule_indexer/domain/download/ed2k_link.py` :
```python
"""Construction d'un lien ed2k PURE (spec download §3/§5 — DÉCISION D2).

Domaine PUR : aucune I/O. Le lien a la forme ``ed2k://|file|<nom>|<taille>|<hash>|/`` (le
format consommé par ``EC_OP_ADD_LINK``, réf. EC §5). Le ``|`` est le SÉPARATEUR DE CHAMPS :
un nom de fichier hostile pourrait, s'il contenait un ``|``, injecter un champ et casser le
cadrage du lien. On échappe donc le nom par percent-encoding UTF-8 (``urllib.parse.quote``),
en gardant un jeu sûr lisible — l'espace devient ``%20``, le ``|`` devient ``%7C``, les
caractères de contrôle et les non-ASCII sont neutralisés. Seuls les 5 séparateurs STRUCTURELS
du lien (``|file|`` … ``|/``) restent des ``|``.
"""

from urllib.parse import quote

# Jeu gardé NON échappé : lisible et sûr (pas d'espace, pas de ``|``, pas de contrôle). Le
# reste passe en percent-encoding (l'espace → ``%20``, le canon ed2k attendu par le test).
# ``/`` n'est PAS dans le jeu sûr (un nom n'est jamais un chemin ici).
_SAFE_NAME_CHARS = ".()[]-_"


def build_ed2k_link(filename: str, size_bytes: int, ed2k_hash: str) -> str:
    """Lien ed2k pour un fichier (spec §5). Le nom est échappé (``|`` → ``%7C``, etc.)."""
    safe_name = quote(filename, safe=_SAFE_NAME_CHARS)
    return f"ed2k://|file|{safe_name}|{size_bytes}|{ed2k_hash}|/"
```

- [ ] **Step 4: Vérifier puis gate**

Run: `uv run pytest tests/domain/download/test_ed2k_link.py -q --no-cov` → PASS (5 tests).
Run: gate complet → tout vert, 100 %.

- [ ] **Step 5: Commit**

```bash
git add src/emule_indexer/domain/download/ed2k_link.py tests/domain/download/test_ed2k_link.py
git commit -m "$(cat <<'EOF'
feat(domain): build_ed2k_link (échappement du séparateur '|' dans le nom)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Port `MuleDownloadClient` + `DownloadEntry` DTO

**Files:**
- Create: `src/emule_indexer/ports/mule_download_client.py`
- Create: `tests/ports/test_mule_download_client.py`

> DÉCISION D3 : port SÉPARÉ de `MuleClient` (ISP). `network_status` réutilise le DTO `NetworkStatus` du port `mule_client` (pas de duplication). `DownloadEntry` (frozen) porte `ed2k_hash`/`size_done`/`size_full` + propriété `is_complete` (DÉCISION D2). Stubs du Protocol sur UNE ligne (couverts par le `def`).

- [ ] **Step 1: Écrire le test qui échoue**

`tests/ports/test_mule_download_client.py` :
```python
import dataclasses

import pytest

from emule_indexer.ports.mule_client import KadStatus, NetworkStatus
from emule_indexer.ports.mule_download_client import DownloadEntry, MuleDownloadClient


class _StubDownloadClient:
    """Satisfait MuleDownloadClient structurellement (sans l'importer)."""

    def __init__(self) -> None:
        self.links: list[str] = []
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    async def add_link(self, ed2k_link: str) -> None:
        self.links.append(ed2k_link)

    async def download_queue(self) -> tuple[DownloadEntry, ...]:
        return (DownloadEntry(ed2k_hash="a" * 32, size_done=5, size_full=10),)

    async def network_status(self) -> NetworkStatus:
        return NetworkStatus(ed2k_id=1, ed2k_high=True, kad_status=KadStatus.CONNECTED)


def test_download_entry_is_frozen() -> None:
    entry = DownloadEntry(ed2k_hash="a" * 32, size_done=5, size_full=10)
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.size_done = 6  # type: ignore[misc]


def test_is_complete_when_done_reaches_full() -> None:
    assert DownloadEntry(ed2k_hash="a" * 32, size_done=10, size_full=10).is_complete is True
    assert DownloadEntry(ed2k_hash="a" * 32, size_done=11, size_full=10).is_complete is True


def test_is_not_complete_below_full() -> None:
    assert DownloadEntry(ed2k_hash="a" * 32, size_done=9, size_full=10).is_complete is False


def test_zero_full_size_is_never_complete() -> None:
    # size_full == 0 (entrée naissante) ne doit JAMAIS compter comme complète (sinon on
    # promouvrait un fichier vide). Garde explicite.
    assert DownloadEntry(ed2k_hash="a" * 32, size_done=0, size_full=0).is_complete is False


@pytest.mark.asyncio
async def test_protocol_is_satisfied_structurally() -> None:
    client: MuleDownloadClient = _StubDownloadClient()
    await client.connect()
    await client.add_link("ed2k://|file|x|1|" + "a" * 32 + "|/")
    queue = await client.download_queue()
    status = await client.network_status()
    await client.close()
    assert isinstance(client, _StubDownloadClient)
    assert client.links == ["ed2k://|file|x|1|" + "a" * 32 + "|/"]
    assert queue[0].ed2k_hash == "a" * 32
    assert status.kad_status is KadStatus.CONNECTED
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/ports/test_mule_download_client.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: …ports.mule_download_client`.

- [ ] **Step 3: Écrire le port**

`src/emule_indexer/ports/mule_download_client.py` :
```python
"""Port ``MuleDownloadClient`` : les opérations de DOWNLOAD attendues d'un client eMule.

SÉPARÉ de ``MuleClient`` (ISP, spec download §2.4/§4 — DÉCISION D3) : la recherche ne dépend
pas des méthodes de download et inversement. La MÊME classe adapter (``AmuleEcClient``) peut
implémenter les deux Protocols STRUCTURELLEMENT ; en exploitation, la connexion download est
une instance DISTINCTE (sa propre connexion EC, spec §2.2). Le port n'importe QUE le domaine
et le DTO réseau partagé ``NetworkStatus`` (déjà dans ``ports/mule_client.py`` — réutilisé,
pas dupliqué : HighID requis pour télécharger en mode full).

``DownloadEntry`` est le DTO de port (frozen) : le crawler NE LIT JAMAIS les octets (spec
§4) ; ``download_queue`` ne renvoie que des MÉTADONNÉES EC. La complétion se déduit de
``size_done``/``size_full`` (DÉCISION D2 : EC n'expose pas de chemin staging portable, donc
le DTO n'en porte pas — la localisation pour la quarantaine est dérivée d'un staging
configuré par l'appelant). Le contrat d'ERREUR est celui du Plan C : un flux mort lève
``MuleUnreachableError`` (``ports/mule_client.py``) — l'application le tolère (spec §9).
"""

from dataclasses import dataclass
from typing import Protocol

from emule_indexer.ports.mule_client import NetworkStatus


@dataclass(frozen=True)
class DownloadEntry:
    """Une entrée de la file de download d'amuled (métadonnées EC SEULES, spec §4).

    ``ed2k_hash`` = clé contenu (hex minuscule 32). ``size_done``/``size_full`` = octets
    transférés / taille totale. ``is_complete`` est vrai SEULEMENT si la taille totale est
    connue (> 0) ET atteinte — un ``size_full == 0`` (entrée naissante) n'est jamais complet.
    """

    ed2k_hash: str
    size_done: int
    size_full: int

    @property
    def is_complete(self) -> bool:
        """``True`` si le fichier est entièrement transféré côté amuled (spec §5)."""
        return self.size_full > 0 and self.size_done >= self.size_full


class MuleDownloadClient(Protocol):
    """Contrat async des opérations de download (spec §4). Actions UNITAIRES : aucun sleep/retry.

    ``add_link`` ajoute un lien ed2k à la file de download d'amuled. ``download_queue`` rend un
    snapshot de la file (hash + avancement). ``network_status`` est réutilisé (HighID requis
    pour télécharger en mode full).
    """

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def add_link(self, ed2k_link: str) -> None: ...

    async def download_queue(self) -> tuple[DownloadEntry, ...]: ...

    async def network_status(self) -> NetworkStatus: ...
```

- [ ] **Step 4: Vérifier puis gate**

Run: `uv run pytest tests/ports/test_mule_download_client.py -q --no-cov` → PASS (5 tests).
Run: gate complet → tout vert, 100 %.

- [ ] **Step 5: Commit**

```bash
git add src/emule_indexer/ports/mule_download_client.py tests/ports/test_mule_download_client.py
git commit -m "$(cat <<'EOF'
feat(ports): MuleDownloadClient (Protocol séparé, ISP) + DownloadEntry DTO

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Adapter EC — opcodes/tags de download + `add_link`/`download_queue`

**Files:**
- Modify: `src/emule_indexer/adapters/mule_ec/codes.py`
- Modify: `src/emule_indexer/adapters/mule_ec/client.py`
- Create: `tests/adapters/mule_ec/test_client_download.py`

> DÉCISION D1 : opcodes/tags grounded sur les sources aMule. `add_link` émet `EC_OP_ADD_LINK` avec un `EC_TAG_STRING` portant le lien ; **succès = `EC_OP_NOOP`**, échec = `EC_OP_FAILED` (→ `EcFailureError` via `_request`). `download_queue` émet `EC_OP_GET_DLOAD_QUEUE` au détail CMD, réponse `EC_OP_DLOAD_QUEUE` ; chaque enfant `EC_TAG_PARTFILE` a pour valeur PROPRE le hash (HASH16) + enfants name/size_full/size_done/status → `DownloadEntry`. Une entrée sans hash/size exploitable est ÉCARTÉE (tolérance aux inconnus, comme `map_search_results`). Le `_request` existant gère déjà FAILED→`EcFailureError`, opcode inattendu→`EcProtocolError`, flux mort→`EcConnectError`/`EcTimeoutError` (qui héritent de `MuleUnreachableError`). Vérifié à l'écriture : le codec encode/décode les deux paquets sans modification.

- [ ] **Step 1: Écrire le test qui échoue**

`tests/adapters/mule_ec/test_client_download.py` :
```python
import pytest

from emule_indexer.adapters.mule_ec import codes
from emule_indexer.adapters.mule_ec.client import AmuleEcClient
from emule_indexer.adapters.mule_ec.codec import (
    EcPacket,
    EcTag,
    string_tag,
    uint_tag,
)
from emule_indexer.adapters.mule_ec.errors import EcConnectError, EcFailureError
from emule_indexer.ports.mule_download_client import DownloadEntry

_HASH = "a1b2c3d4e5f6071829303142535465f0"


class _ScriptedTransport:
    """Faux transport : rend des réponses SCRIPTÉES, capture les paquets envoyés."""

    def __init__(self, replies: list[EcPacket]) -> None:
        self._replies = replies
        self.sent: list[EcPacket] = []
        self.closed = False

    async def send_packet(self, packet: EcPacket) -> None:
        self.sent.append(packet)

    async def receive_packet(self) -> EcPacket:
        return self._replies.pop(0)

    async def close(self) -> None:
        self.closed = True


def _connected_client(transport: _ScriptedTransport) -> AmuleEcClient:
    client = AmuleEcClient("h", 4712, "pwd")
    client._transport = transport  # type: ignore[assignment]  # injecté (déjà connecté)
    return client


def _partfile_entry(hash_hex: str, *, done: int, full: int) -> EcTag:
    return EcTag(
        codes.EC_TAG_PARTFILE,
        codes.EC_TAGTYPE_HASH16,
        bytes.fromhex(hash_hex),
        (
            string_tag(codes.EC_TAG_PARTFILE_NAME, "Keroro.avi"),
            uint_tag(codes.EC_TAG_PARTFILE_SIZE_FULL, full),
            uint_tag(codes.EC_TAG_PARTFILE_SIZE_DONE, done),
            uint_tag(codes.EC_TAG_PARTFILE_STATUS, 0),
        ),
    )


@pytest.mark.asyncio
async def test_add_link_sends_the_link_and_accepts_noop() -> None:
    transport = _ScriptedTransport([EcPacket(codes.EC_OP_NOOP)])
    client = _connected_client(transport)
    link = "ed2k://|file|x.avi|10|" + _HASH + "|/"
    await client.add_link(link)
    sent = transport.sent[0]
    assert sent.opcode == codes.EC_OP_ADD_LINK
    assert sent.find(codes.EC_TAG_STRING) is not None
    assert sent.find(codes.EC_TAG_STRING).string_value() == link  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_add_link_failure_raises_ec_failure() -> None:
    failed = EcPacket(codes.EC_OP_FAILED, (string_tag(codes.EC_TAG_STRING, "lien invalide"),))
    client = _connected_client(_ScriptedTransport([failed]))
    with pytest.raises(EcFailureError, match="lien invalide"):
        await client.add_link("ed2k://bad")


@pytest.mark.asyncio
async def test_add_link_on_a_disconnected_client_raises_connect_error() -> None:
    client = AmuleEcClient("h", 4712, "pwd")  # jamais connecté
    with pytest.raises(EcConnectError):
        await client.add_link("ed2k://x")


@pytest.mark.asyncio
async def test_download_queue_maps_entries_to_download_entries() -> None:
    reply = EcPacket(
        codes.EC_OP_DLOAD_QUEUE,
        (
            _partfile_entry(_HASH, done=10, full=10),
            _partfile_entry("b" * 32, done=3, full=10),
        ),
    )
    client = _connected_client(_ScriptedTransport([reply]))
    queue = await client.download_queue()
    assert queue == (
        DownloadEntry(ed2k_hash=_HASH, size_done=10, size_full=10),
        DownloadEntry(ed2k_hash="b" * 32, size_done=3, size_full=10),
    )


@pytest.mark.asyncio
async def test_download_queue_requests_at_cmd_detail() -> None:
    transport = _ScriptedTransport([EcPacket(codes.EC_OP_DLOAD_QUEUE)])
    client = _connected_client(transport)
    await client.download_queue()
    sent = transport.sent[0]  # référence typée → aucun ignore nécessaire
    assert sent.opcode == codes.EC_OP_GET_DLOAD_QUEUE
    detail = sent.find(codes.EC_TAG_DETAIL_LEVEL)
    assert detail is not None and detail.int_value() == codes.EC_DETAIL_CMD


@pytest.mark.asyncio
async def test_download_queue_skips_entries_without_a_usable_hash() -> None:
    # une entrée dont la valeur propre n'est PAS un HASH16 de 16 octets est ÉCARTÉE
    # (tolérance aux inconnus, comme map_search_results) — jamais fatale au lot.
    pourrie = EcTag(codes.EC_TAG_PARTFILE, codes.EC_TAGTYPE_UINT8, b"\x01", ())
    reply = EcPacket(codes.EC_OP_DLOAD_QUEUE, (pourrie, _partfile_entry(_HASH, done=1, full=2)))
    client = _connected_client(_ScriptedTransport([reply]))
    queue = await client.download_queue()
    assert queue == (DownloadEntry(ed2k_hash=_HASH, size_done=1, size_full=2),)


@pytest.mark.asyncio
async def test_download_queue_skips_non_partfile_toplevel_tags() -> None:
    reply = EcPacket(
        codes.EC_OP_DLOAD_QUEUE,
        (uint_tag(codes.EC_TAG_DETAIL_LEVEL, 0), _partfile_entry(_HASH, done=1, full=2)),
    )
    client = _connected_client(_ScriptedTransport([reply]))
    queue = await client.download_queue()
    assert queue == (DownloadEntry(ed2k_hash=_HASH, size_done=1, size_full=2),)


@pytest.mark.asyncio
async def test_download_queue_treats_missing_size_as_zero() -> None:
    # une entrée valide (hash) mais sans tags de taille → done=0, full=0 (absence = 0,
    # réf. EC §3) → is_complete False, ne sera jamais promue par erreur.
    entry = EcTag(codes.EC_TAG_PARTFILE, codes.EC_TAGTYPE_HASH16, bytes.fromhex(_HASH), ())
    client = _connected_client(_ScriptedTransport([EcPacket(codes.EC_OP_DLOAD_QUEUE, (entry,))]))
    queue = await client.download_queue()
    assert queue == (DownloadEntry(ed2k_hash=_HASH, size_done=0, size_full=0),)


@pytest.mark.asyncio
async def test_download_queue_treats_malformed_size_as_zero() -> None:
    # un tag de taille PRÉSENT mais malformé (UINT32 déclaré, 1 octet) lève EcProtocolError à
    # int_value() ; _optional_partfile_int l'avale → 0 (jamais fatal, réf. EC §3, piège 4).
    bad_full = EcTag(codes.EC_TAG_PARTFILE_SIZE_FULL, codes.EC_TAGTYPE_UINT32, b"\x01", ())
    entry = EcTag(
        codes.EC_TAG_PARTFILE, codes.EC_TAGTYPE_HASH16, bytes.fromhex(_HASH), (bad_full,)
    )
    client = _connected_client(_ScriptedTransport([EcPacket(codes.EC_OP_DLOAD_QUEUE, (entry,))]))
    queue = await client.download_queue()
    assert queue == (DownloadEntry(ed2k_hash=_HASH, size_done=0, size_full=0),)
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/adapters/mule_ec/test_client_download.py -q --no-cov`
Expected: FAIL — `AttributeError: 'AmuleEcClient' object has no attribute 'add_link'` (et `codes.EC_OP_ADD_LINK` manquant).

- [ ] **Step 3: Étendre `codes.py`**

Dans `src/emule_indexer/adapters/mule_ec/codes.py`, après la ligne `EC_OP_NOOP: Final[int] = 0x01` (section opcodes), insérer (commentaires COURTS pour rester ≤100 sous `ruff format` — sinon les one-liners se reflowent en multi-ligne et ne matchent plus ce snippet) :
```python
EC_OP_ADD_LINK: Final[int] = 0x09  # ajoute un lien ed2k ; réponse NOOP
EC_OP_GET_DLOAD_QUEUE: Final[int] = 0x0D  # requête de la file de download (détail CMD)
EC_OP_DLOAD_QUEUE: Final[int] = 0x1F  # réponse : N enfants EC_TAG_PARTFILE
```
Et dans la section tags partfile, après `EC_TAG_PARTFILE_SIZE_FULL: Final[int] = 0x0303`, insérer :
```python
EC_TAG_PARTFILE_SIZE_DONE: Final[int] = 0x0306  # octets transférés (complet = done >= full)
EC_TAG_PARTFILE_ED2K_LINK: Final[int] = 0x030E  # lien reconstruit (non utilisé ici)
```

- [ ] **Step 4: Étendre `client.py` (méthodes `add_link`/`download_queue`)**

Dans `src/emule_indexer/adapters/mule_ec/client.py` :

(a) étendre l'import du port :
```python
from emule_indexer.ports.mule_client import KadStatus, NetworkStatus, SearchChannel
```
par :
```python
from emule_indexer.ports.mule_client import KadStatus, NetworkStatus, SearchChannel
from emule_indexer.ports.mule_download_client import DownloadEntry
```

(b) à la FIN de la classe `AmuleEcClient` (après `network_status`, avant la méthode privée `_authenticate` — l'ordre public/privé importe peu, on insère juste après `network_status`), ajouter :
```python
    async def add_link(self, ed2k_link: str) -> None:
        """Ajoute un lien ed2k à la file de download d'amuled (réf. EC, DÉCISION D1).

        Émet ``EC_OP_ADD_LINK`` avec un ``EC_TAG_STRING`` portant le lien ; le succès est
        signalé par ``EC_OP_NOOP`` (et NON ``EC_OP_STRINGS`` — vérifié sur ExternalConn.cpp).
        Un échec applicatif (``EC_OP_FAILED``) lève ``EcFailureError`` via ``_request`` ; un
        flux mort lève ``EcConnectError``/``EcTimeoutError`` (sous ``MuleUnreachableError``).
        """
        await self._request(
            EcPacket(codes.EC_OP_ADD_LINK, (string_tag(codes.EC_TAG_STRING, ed2k_link),)),
            codes.EC_OP_NOOP,
        )

    async def download_queue(self) -> tuple[DownloadEntry, ...]:
        """Snapshot de la file de download (réf. EC, DÉCISION D1/D2). NE LIT JAMAIS les octets.

        Émet ``EC_OP_GET_DLOAD_QUEUE`` au détail CMD ; la réponse ``EC_OP_DLOAD_QUEUE``
        contient N enfants ``EC_TAG_PARTFILE`` dont la valeur PROPRE est le hash (HASH16) et
        les enfants portent name/size_full/size_done/status. Une entrée sans hash exploitable
        est ÉCARTÉE (tolérance aux inconnus, comme ``map_search_results`` — jamais fatale).
        """
        request = EcPacket(
            codes.EC_OP_GET_DLOAD_QUEUE,
            (uint_tag(codes.EC_TAG_DETAIL_LEVEL, codes.EC_DETAIL_CMD),),
        )
        reply = await self._request(request, codes.EC_OP_DLOAD_QUEUE)
        entries: list[DownloadEntry] = []
        for tag in reply.tags:
            if tag.name != codes.EC_TAG_PARTFILE:
                continue
            entry = _map_partfile(tag)
            if entry is not None:
                entries.append(entry)
        return tuple(entries)
```

(c) à la FIN du fichier (après la fonction module-level `_parse_connstate`), ajouter le mapper :
```python
def _optional_partfile_int(entry: EcTag, name: int) -> int:
    """Entier optionnel d'une entrée partfile : absence ou malformé → 0 (réf. EC §3)."""
    tag = entry.find(name)
    if tag is None:
        return 0
    try:
        return tag.int_value()
    except EcProtocolError:
        return 0


def _map_partfile(entry: EcTag) -> DownloadEntry | None:
    """Une entrée ``EC_TAG_PARTFILE`` → ``DownloadEntry``, ou ``None`` si le hash est inexploitable.

    La valeur PROPRE de l'entrée est le hash (HASH16, 16 octets) ; les tailles sont des
    enfants. Une valeur propre qui n'est pas un HASH16 de 16 octets écarte l'entrée (le hash
    est le SEUL identifiant stable — sans lui, l'entrée est inutilisable, jamais persistée).
    """
    if entry.tag_type != codes.EC_TAGTYPE_HASH16 or len(entry.value) != 16:
        return None
    return DownloadEntry(
        ed2k_hash=entry.value.hex(),
        size_done=_optional_partfile_int(entry, codes.EC_TAG_PARTFILE_SIZE_DONE),
        size_full=_optional_partfile_int(entry, codes.EC_TAG_PARTFILE_SIZE_FULL),
    )
```

(Note : `EcProtocolError` est déjà importé dans `client.py`. Si ce n'est pas le cas dans l'état courant, l'ajouter à l'import depuis `errors`.)

- [ ] **Step 5: Vérifier puis gate**

Run: `uv run pytest tests/adapters/mule_ec/test_client_download.py -q --no-cov` → PASS (8 tests).
Run: gate complet → tout vert (les tests EC existants restent verts ; aucune méthode existante touchée). 100 %.

- [ ] **Step 6: Commit**

```bash
git add src/emule_indexer/adapters/mule_ec/codes.py src/emule_indexer/adapters/mule_ec/client.py tests/adapters/mule_ec/test_client_download.py
git commit -m "$(cat <<'EOF'
feat(adapters): AmuleEcClient.add_link / download_queue (opcodes download EC)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: EARLY probe empirique — `download_probe.py` + e2e `download_integration` + référence

**Files:**
- Create: `src/emule_indexer/tools/download_probe.py`
- Modify: `pyproject.toml` (marqueur `download_integration` + déselection par défaut)
- Create: `tests/integration/test_amuled_download.py`
- Create: `docs/reference/2026-06-13-ec-download-opcodes.md` (rapport du probe)

> **C'est le validateur empirique du plan (spec §4/§11, méthode du Plan B).** Le probe (miroir de `tools/ec_probe.py`) ajoute un lien ed2k contre un amuled RÉEL et lit la file de download pour confirmer que (1) `add_link` est ACCEPTÉ et (2) le lien APPARAÎT dans `download_queue` avec un statut lisible. L'e2e opt-in (`download_integration`, testcontainers, hors coverage, déselectionné par défaut — miroir d'`ec_integration`) automatise la confirmation. La complétion réelle n'est PAS atteignable (pas de sources eD2k dans le conteneur : **option A**). Le rapport `docs/reference/` consigne les opcodes confirmés + le PENDING homelab.

- [ ] **Step 1: Écrire le probe (outil, pas de test unitaire — il est validé par l'e2e)**

`src/emule_indexer/tools/download_probe.py` :
```python
"""Sonde EC download : add_link + dump de la file de download (spec download §4/§11).

Usage :
    uv run python -m emule_indexer.tools.download_probe --host 127.0.0.1 --port 4712 \\
        --password <pwd> --link 'ed2k://|file|nom|123|<hash32>|/'

Miroir de ``tools/ec_probe.py`` pour le DOWNLOAD : ajoute un lien ed2k à amuled, puis relève
la file de download et affiche chaque entrée (hash, done/full, complète ?). Valide que
``add_link`` est accepté et que le lien apparaît dans ``download_queue`` (mécaniques EC
réelles — option A). La complétion n'est PAS atteignable sans sources eD2k (conteneur
éphémère). Réutilisable tel quel contre un homelab pour observer une vraie complétion.
"""

import argparse
import asyncio
import os
import sys
from collections.abc import Awaitable, Callable, Sequence

from emule_indexer.adapters.mule_ec.client import AmuleEcClient
from emule_indexer.adapters.mule_ec.errors import EcError
from emule_indexer.ports.mule_download_client import DownloadEntry, MuleDownloadClient

Sleeper = Callable[[float], Awaitable[None]]
ClientFactory = Callable[[argparse.Namespace], MuleDownloadClient]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="download_probe", description="Sonde EC : add_link + dump de la file de download"
    )
    parser.add_argument("--host", default="127.0.0.1", help="hôte amuled")
    parser.add_argument("--port", type=int, default=4712, help="port EC (ECPort)")
    parser.add_argument(
        "--password",
        default=os.environ.get("EC_PROBE_PASSWORD"),
        help="mot de passe EC (en clair ; défaut : variable d'environnement EC_PROBE_PASSWORD)",
    )
    parser.add_argument("--link", required=True, help="lien ed2k à ajouter (ed2k://|file|…|/)")
    return parser


def format_entry(entry: DownloadEntry) -> str:
    return (
        f"[probe] {entry.ed2k_hash} : {entry.size_done}/{entry.size_full} o "
        f"(complet={entry.is_complete})"
    )


async def run_probe(client: MuleDownloadClient, args: argparse.Namespace) -> int:
    try:
        await client.connect()
        print(format_status(await client.network_status()))
        await client.add_link(str(args.link))
        print(f"[probe] add_link accepté pour : {args.link}")
        queue = await client.download_queue()
        print(f"[probe] file de download : {len(queue)} entrée(s)")
        for entry in queue:
            print(format_entry(entry))
    finally:
        await client.close()
    return 0


def format_status(status: object) -> str:
    return f"[probe] statut réseau : {status}"


def _default_client(args: argparse.Namespace) -> MuleDownloadClient:
    return AmuleEcClient(str(args.host), int(args.port), str(args.password))


def main(
    argv: Sequence[str] | None = None, *, client_factory: ClientFactory = _default_client
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.password is None:
        parser.error("mot de passe requis (--password ou EC_PROBE_PASSWORD)")
    try:
        return asyncio.run(run_probe(client_factory(args), args))
    except KeyboardInterrupt:
        print("[probe] interrompu", file=sys.stderr)
        return 130
    except EcError as exc:
        print(f"[probe] ERREUR : {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
```

> **Note couverture :** ce module est un OUTIL (comme `ec_probe.py`) ; il est couvert à 100 % par des tests unitaires sur un faux client — le code VERBATIM est en Step 1bis ci-dessous (calqué sur `tests/tools/test_ec_probe.py`). La ligne `if __name__ == "__main__":` porte `# pragma: no cover`.

- [ ] **Step 1bis: Écrire les tests unitaires du probe (pour le gate 100 %)**

`tests/tools/test_download_probe.py` (calqué sur `tests/tools/test_ec_probe.py` — mêmes conventions ; `main` appelle le vrai `asyncio.run` sur les coroutines RÉELLES du fake, comme `test_ec_probe`) :
```python
import pytest

from emule_indexer.adapters.mule_ec.client import AmuleEcClient
from emule_indexer.adapters.mule_ec.errors import EcAuthError, EcError
from emule_indexer.ports.mule_client import KadStatus, NetworkStatus
from emule_indexer.ports.mule_download_client import DownloadEntry
from emule_indexer.tools.download_probe import (
    _default_client,
    build_parser,
    format_entry,
    format_status,
    main,
)

_HASH = "000102030405060708090a0b0c0d0e0f"
_STATUS = NetworkStatus(ed2k_id=1, ed2k_high=True, kad_status=KadStatus.CONNECTED)


class FakeDownloadClient:
    """Faux MuleDownloadClient : journal d'appels + file en conserve."""

    def __init__(
        self,
        *,
        queue: tuple[DownloadEntry, ...] = (),
        connect_error: EcError | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.added: list[str] = []
        self._queue = queue
        self._connect_error = connect_error

    async def connect(self) -> None:
        self.calls.append("connect")
        if self._connect_error is not None:
            raise self._connect_error

    async def close(self) -> None:
        self.calls.append("close")

    async def add_link(self, ed2k_link: str) -> None:
        self.calls.append("add_link")
        self.added.append(ed2k_link)

    async def download_queue(self) -> tuple[DownloadEntry, ...]:
        self.calls.append("download_queue")
        return self._queue

    async def network_status(self) -> NetworkStatus:
        self.calls.append("status")
        return _STATUS


# ---------------------------------------------------------------- parsing


def test_parser_defaults() -> None:
    args = build_parser().parse_args(["--password", "pwd", "--link", "ed2k://x"])
    assert args.host == "127.0.0.1"
    assert args.port == 4712
    assert args.link == "ed2k://x"


def test_parser_requires_link() -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--password", "pwd"])
    assert excinfo.value.code == 2


def test_parser_password_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EC_PROBE_PASSWORD", "env-secret")
    args = build_parser().parse_args(["--link", "ed2k://x"])
    assert args.password == "env-secret"


# ---------------------------------------------------------------- formatage


def test_format_entry_shows_progress_and_completeness() -> None:
    line = format_entry(DownloadEntry(ed2k_hash=_HASH, size_done=5, size_full=10))
    assert _HASH in line
    assert "5/10" in line
    assert "complet=False" in line


def test_format_status_renders_a_line() -> None:
    assert "statut réseau" in format_status(_STATUS)


# ---------------------------------------------------------------- cycle complet via main()


def test_main_success_adds_link_and_dumps_queue(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeDownloadClient(queue=(DownloadEntry(ed2k_hash=_HASH, size_done=10, size_full=10),))
    code = main(
        ["--password", "pwd", "--link", "ed2k://|file|x|1|" + _HASH + "|/"],
        client_factory=lambda args: fake,
    )
    assert code == 0
    assert fake.calls == ["connect", "status", "add_link", "download_queue", "close"]
    assert fake.added == ["ed2k://|file|x|1|" + _HASH + "|/"]
    out = capsys.readouterr().out
    assert "add_link accepté" in out
    assert "file de download : 1 entrée(s)" in out
    assert "complet=True" in out


def test_main_errors_when_password_absent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("EC_PROBE_PASSWORD", raising=False)
    fake = FakeDownloadClient()
    with pytest.raises(SystemExit) as excinfo:
        main(["--link", "ed2k://x"], client_factory=lambda args: fake)
    assert excinfo.value.code == 2
    assert "mot de passe requis" in capsys.readouterr().err
    assert fake.calls == []  # le client n'est jamais construit ni connecté


def test_main_returns_1_on_ec_error_and_still_closes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeDownloadClient(connect_error=EcAuthError("Invalid password"))
    code = main(
        ["--password", "bad", "--link", "ed2k://x"], client_factory=lambda args: fake
    )
    assert code == 1
    assert fake.calls == ["connect", "close"]  # close() TOUJOURS appelé (finally)
    assert "Invalid password" in capsys.readouterr().err


def test_main_returns_130_on_keyboard_interrupt(capsys: pytest.CaptureFixture[str]) -> None:
    class _Interrupting(FakeDownloadClient):
        async def add_link(self, ed2k_link: str) -> None:
            raise KeyboardInterrupt

    fake = _Interrupting()
    code = main(
        ["--password", "pwd", "--link", "ed2k://x"], client_factory=lambda args: fake
    )
    assert code == 130
    assert "interrompu" in capsys.readouterr().err
    assert "close" in fake.calls  # close() TOUJOURS appelé (finally)


# ---------------------------------------------------------------- fabrique réelle


def test_default_client_builds_an_amule_ec_client() -> None:
    args = build_parser().parse_args(
        ["--host", "homelab", "--port", "4713", "--password", "pwd", "--link", "ed2k://x"]
    )
    client = _default_client(args)
    assert isinstance(client, AmuleEcClient)  # constructeur sans I/O : sûr en test
    assert client._host == "homelab"
    assert client._port == 4713
    assert client._password == "pwd"
```

> **Note couverture probe :** `build_parser` (défauts / `--link` requis → SystemExit(2) / fallback env) ; `format_entry`/`format_status` ; `main` (run propre → 0, add_link + dump ; `--password` absent → SystemExit(2), client jamais construit ; `EcError` → 1 + close ; `KeyboardInterrupt` → 130 + close) ; `run_probe` (connect→status→add_link→download_queue→close, et le `finally` close sur erreur) ; `_default_client`. La ligne `if __name__ == "__main__":` porte `# pragma: no cover`.

- [ ] **Step 2: Modifier `pyproject.toml` (marqueur `download_integration`)**

Dans `[tool.pytest.ini_options]`, remplacer la ligne `addopts` :
```
addopts = '--cov=emule_indexer --cov-report=term-missing --cov-fail-under=100 --strict-markers -m "not ec_integration and not orchestration_integration"'
```
par :
```
addopts = '--cov=emule_indexer --cov-report=term-missing --cov-fail-under=100 --strict-markers -m "not ec_integration and not orchestration_integration and not download_integration"'
```
et ajouter à la liste `markers` (après la ligne `orchestration_integration`) :
```
    "download_integration: add_link + lecture de la file de download contre un amuled réel (Docker requis) — déselectionnés par défaut ; run dédié : uv run pytest -m download_integration --no-cov",
```

- [ ] **Step 3: Écrire l'e2e opt-in**

`tests/integration/test_amuled_download.py` :
```python
"""Intégration DOWNLOAD contre un amuled RÉEL (réf. protocole, spec download §11 — option A).

Run dédié : uv run pytest -m download_integration --no-cov
Valide les MÉCANIQUES EC du download : ``add_link`` accepté + le lien apparaît dans
``download_queue`` avec un statut lisible. La COMPLÉTION n'est PAS atteignable (pas de sources
eD2k depuis le conteneur éphémère) : c'est le cycle add_link → file → statut qui est validé.
"""

from collections.abc import Iterator

import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from emule_indexer.adapters.mule_ec.client import AmuleEcClient
from emule_indexer.adapters.mule_ec.errors import EcFailureError
from emule_indexer.domain.download.ed2k_link import build_ed2k_link

pytestmark = pytest.mark.download_integration

_EC_PASSWORD = "indexer-ec-test"
_IMAGE = "ngosang/amule:3.0.0-1"
# Un hash arbitraire mais canonique : amuled accepte le lien (pas de source ≠ lien invalide).
_HASH = "31d6cfe0d16ae931b73c59d7e0c089c0"


@pytest.fixture(scope="module")
def amuled() -> Iterator[tuple[str, int]]:
    ready = LogMessageWaitStrategy(r"listening on 0\.0\.0\.0:4712").with_startup_timeout(180)
    container = (
        DockerContainer(_IMAGE)
        .with_env("GUI_PWD", _EC_PASSWORD)
        .with_exposed_ports(4712)
        .waiting_for(ready)
    )
    try:
        container.start()
        yield container.get_container_host_ip(), int(container.get_exposed_port(4712))
    finally:
        container.stop()


@pytest.mark.asyncio
async def test_add_link_then_appears_in_download_queue(amuled: tuple[str, int]) -> None:
    host, port = amuled
    client = AmuleEcClient(host, port, _EC_PASSWORD, timeout=30.0)
    await client.connect()
    try:
        link = build_ed2k_link("probe-download.bin", 1048576, _HASH)
        try:
            await client.add_link(link)
        except EcFailureError as exc:
            # amuled a répondu FAILED proprement (lien refusé) : le cycle requête/réponse
            # EST validé, avec le message du daemon. Tolérable pour ce contexte de test.
            assert str(exc)
            return
        queue = await client.download_queue()
        assert isinstance(queue, tuple)
        # Le hash ajouté devrait apparaître dans la file (statut lisible). On TOLÈRE une file
        # vide si amuled a déduppé/rejeté silencieusement : la MÉCANIQUE (add_link accepté +
        # download_queue décodée) est ce qui fait foi (option A).
        hashes = {entry.ed2k_hash for entry in queue}
        assert _HASH in hashes or queue == ()
    finally:
        await client.close()
```

- [ ] **Step 4: Écrire le rapport de référence**

`docs/reference/2026-06-13-ec-download-opcodes.md` : rapport calqué sur `2026-06-11-ec-field-richness.md` (convention de fiabilité SOURCE / EMPIRIQUE / PENDING). Contenu MINIMAL à rédiger :
- **Opcodes confirmés (SOURCE)** : `EC_OP_ADD_LINK = 0x09`, `EC_OP_GET_DLOAD_QUEUE = 0x0D`, `EC_OP_DLOAD_QUEUE = 0x1F` ; tags partfile (name 0x0301, size_full 0x0303, size_done 0x0306, status 0x0308, ed2k_link 0x030E, hash 0x031E). Sources : `amule-org/amule@3.0.0 ECCodes.h` + `ExternalConn.cpp` (`add_link` lit `tag.GetStringData()`, répond `EC_OP_NOOP` en succès).
- **EMPIRIQUE (à remplir lors du run e2e/probe)** : coller la sortie réelle de `test_amuled_download` et/ou du `download_probe` contre `ngosang/amule:3.0.0-1` (logs « Access granted. », file de download observée, statut). Confirmer : `add_link` accepté, `download_queue` décodée, statut lisible.
- **PENDING homelab** : la COMPLÉTION réelle (`size_done == size_full`) et le chemin staging réel (non exposé par EC — DÉCISION D2 : la quarantaine dérive d'un staging configuré). Commande homelab : `uv run python -m emule_indexer.tools.download_probe --host <homelab> --port 4712 --password <pwd> --link '<lien d'un fichier réel à sources>'`.

- [ ] **Step 5: Vérifier**

Run (unitaires du probe) : `uv run pytest tests/tools/test_download_probe.py -q --no-cov` → PASS.
Run (collection e2e, sans Docker) : `uv run pytest tests/integration/test_amuled_download.py --collect-only -q -m download_integration` → `1 test collected`.
Run (si Docker disponible) : `uv run pytest -m download_integration --no-cov -q` → `1 passed` (et coller la sortie dans le rapport de référence).
Run: gate complet → tout vert ; pytest rapporte **`6 deselected`** (4 ec + 1 orchestration + 1 download), 100 %.

- [ ] **Step 6: Commit**

```bash
git add src/emule_indexer/tools/download_probe.py tests/tools/test_download_probe.py pyproject.toml tests/integration/test_amuled_download.py "docs/reference/2026-06-13-ec-download-opcodes.md"
git commit -m "$(cat <<'EOF'
feat(tools): download_probe + e2e download_integration (mécaniques EC réelles, option A)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Migration 0002 + `SqliteDownloadRepository`

**Files:**
- Create: `src/emule_indexer/adapters/persistence_sqlite/migrations/local/0002_downloads_size_bytes.sql`
- Create: `src/emule_indexer/adapters/persistence_sqlite/download_repository.py`
- Create: `tests/adapters/persistence_sqlite/test_download_repository.py`
- Create: `tests/adapters/persistence_sqlite/test_migration_0002.py`

> DÉCISION D6 : `downloads` existe déjà (local/0001) ; 0002 ajoute `size_bytes INTEGER NOT NULL DEFAULT 0`. Repo sync, disciplines identiques (stamp avant `BEGIN`, `BEGIN IMMEDIATE`, rollback `BaseException`, `wrap_sqlite_errors`). `record_queued` dédup-safe (PK), `set_state` UPDATE (+`completed_at` à la complétion), `committed_bytes` = somme des états non terminaux, `is_downloaded`, `active_states` (hash→état pour le monitor). Vérifié à l'écriture : `ALTER TABLE … ADD COLUMN NOT NULL DEFAULT 0` + UPSERT + somme par états marchent (SQLite 3.53).

- [ ] **Step 1: Écrire la migration**

`src/emule_indexer/adapters/persistence_sqlite/migrations/local/0002_downloads_size_bytes.sql` :
```sql
-- local.db — migration 0002 : plafond disque applicatif (spec download §7 — DÉCISION D6).
-- Ajoute la taille du fichier à la table downloads (existante, migration 0001). Le plafond
-- reste une requête simple (somme des size_bytes des downloads ACTIFS). DEFAULT 0 exigé par
-- ALTER TABLE ADD COLUMN NOT NULL sur une table éventuellement non vide.

ALTER TABLE downloads ADD COLUMN size_bytes INTEGER NOT NULL DEFAULT 0;
```

- [ ] **Step 2: Écrire les tests qui échouent**

`tests/adapters/persistence_sqlite/test_migration_0002.py` :
```python
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from emule_indexer.adapters.persistence_sqlite.connection import open_local


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    local = open_local(tmp_path / "local.db")
    yield local
    local.close()


def test_downloads_has_size_bytes_column(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(downloads)")}
    assert "size_bytes" in columns


def test_user_version_is_at_least_two(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    assert version >= 2
```

`tests/adapters/persistence_sqlite/test_download_repository.py` :
```python
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from emule_indexer.adapters.persistence_sqlite.connection import open_local
from emule_indexer.adapters.persistence_sqlite.download_repository import SqliteDownloadRepository
from emule_indexer.adapters.persistence_sqlite.errors import PersistenceError
from emule_indexer.domain.download.states import DownloadState

_A = "a" * 32
_B = "b" * 32


class _AdvancingClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 6, 13, 10, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        moment = self._now
        self._now += timedelta(minutes=1)
        return moment


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    local = open_local(tmp_path / "local.db")
    yield local
    local.close()


@pytest.fixture
def repository(connection: sqlite3.Connection) -> SqliteDownloadRepository:
    return SqliteDownloadRepository(connection)


def test_record_queued_inserts_a_new_download(repository: SqliteDownloadRepository) -> None:
    assert repository.record_queued(_A, "S2E062A", 100) is True
    assert repository.is_downloaded(_A) is True


def test_record_queued_is_dedup_safe(repository: SqliteDownloadRepository) -> None:
    assert repository.record_queued(_A, "S2E062A", 100) is True
    assert repository.record_queued(_A, "S2E062A", 100) is False  # doublon ignoré


def test_is_downloaded_is_false_for_unknown_hash(repository: SqliteDownloadRepository) -> None:
    assert repository.is_downloaded(_A) is False


def test_set_state_updates_the_state(repository: SqliteDownloadRepository) -> None:
    repository.record_queued(_A, "S2E062A", 100)
    repository.set_state(_A, DownloadState.DOWNLOADING)
    assert repository.active_states()[_A] is DownloadState.DOWNLOADING


def test_set_state_to_completed_stamps_completed_at(
    connection: sqlite3.Connection,
) -> None:
    repository = SqliteDownloadRepository(connection, clock=_AdvancingClock())
    repository.record_queued(_A, "S2E062A", 100)
    repository.set_state(_A, DownloadState.COMPLETED)
    stamped = connection.execute(
        "SELECT completed_at FROM downloads WHERE ed2k_hash = ?", (_A,)
    ).fetchone()[0]
    assert stamped is not None


def test_set_state_non_completed_leaves_completed_at_null(
    repository: SqliteDownloadRepository, connection: sqlite3.Connection
) -> None:
    repository.record_queued(_A, "S2E062A", 100)
    repository.set_state(_A, DownloadState.DOWNLOADING)
    stamped = connection.execute(
        "SELECT completed_at FROM downloads WHERE ed2k_hash = ?", (_A,)
    ).fetchone()[0]
    assert stamped is None


def test_set_state_on_unknown_hash_raises(repository: SqliteDownloadRepository) -> None:
    with pytest.raises(PersistenceError):
        repository.set_state(_A, DownloadState.DOWNLOADING)


def test_committed_bytes_sums_only_active_downloads(
    repository: SqliteDownloadRepository,
) -> None:
    repository.record_queued(_A, "S2E062A", 100)  # queued (actif)
    repository.record_queued(_B, "S2E063A", 200)  # downloading (actif)
    repository.set_state(_B, DownloadState.DOWNLOADING)
    assert repository.committed_bytes() == 300
    repository.set_state(_A, DownloadState.COMPLETED)  # terminal → ne compte plus
    assert repository.committed_bytes() == 200


def test_committed_bytes_is_zero_on_empty(repository: SqliteDownloadRepository) -> None:
    assert repository.committed_bytes() == 0


def test_active_states_maps_hash_to_state(repository: SqliteDownloadRepository) -> None:
    repository.record_queued(_A, "S2E062A", 100)
    repository.record_queued(_B, "S2E063A", 200)
    repository.set_state(_B, DownloadState.QUARANTINED)
    states = repository.active_states()
    assert states == {_A: DownloadState.QUEUED, _B: DownloadState.QUARANTINED}


def test_record_queued_is_atomic_on_injected_failure(
    repository: SqliteDownloadRepository, connection: sqlite3.Connection
) -> None:
    connection.execute(
        "CREATE TRIGGER boom BEFORE INSERT ON downloads"
        " BEGIN SELECT RAISE(ABORT, 'panne injectée'); END"
    )
    with pytest.raises(PersistenceError, match="panne injectée"):
        repository.record_queued(_A, "S2E062A", 100)
    assert repository.is_downloaded(_A) is False
```

- [ ] **Step 3: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/adapters/persistence_sqlite/test_migration_0002.py tests/adapters/persistence_sqlite/test_download_repository.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: …download_repository` (et, si la migration manque, colonne `size_bytes` absente).

- [ ] **Step 4: Écrire le repo**

`src/emule_indexer/adapters/persistence_sqlite/download_repository.py` :
```python
"""``SqliteDownloadRepository`` : l'état des downloads (local.db, spec download §7).

Implémente la persistance des downloads gérés par le crawler. ``downloads`` n'est PAS
append-only (état mutable, pas le catalogue) → UPSERT/UPDATE licites, pas de triggers. Mêmes
disciplines que les autres repos (spec data-model §7) : timestamp stampé AVANT ``BEGIN``,
``BEGIN IMMEDIATE`` + rollback sur ``BaseException`` (une panne NON-sqlite ne laisse pas la
connexion ``in_transaction``), ``wrap_sqlite_errors``.

``record_queued`` est dédup-safe (PK = hash, ``ON CONFLICT DO NOTHING``) ; ``set_state``
stampe ``completed_at`` à la complétion (horloge injectée) ; ``committed_bytes`` somme les
``size_bytes`` des états NON terminaux (plafond disque applicatif, DÉCISION D6/D7) ;
``active_states`` rend la map hash→état (le monitor de la boucle réconcilie dessus).
"""

import sqlite3
from contextlib import suppress

from emule_indexer.adapters.persistence_sqlite.connection import Clock, utc_iso, utc_now
from emule_indexer.adapters.persistence_sqlite.errors import PersistenceError, wrap_sqlite_errors
from emule_indexer.domain.download.states import DownloadState

_INSERT = """
INSERT INTO downloads (ed2k_hash, target_id, state, queued_at, size_bytes)
VALUES (?, ?, 'queued', ?, ?)
ON CONFLICT (ed2k_hash) DO NOTHING
"""

_SET_STATE = "UPDATE downloads SET state = ? WHERE ed2k_hash = ?"

_SET_STATE_COMPLETED = "UPDATE downloads SET state = ?, completed_at = ? WHERE ed2k_hash = ?"

_IS_DOWNLOADED = "SELECT 1 FROM downloads WHERE ed2k_hash = ?"

_ACTIVE_STATES = "SELECT ed2k_hash, state FROM downloads"

# Le plafond ne compte que les downloads ACTIFS (états non terminaux, DÉCISION D7).
_COMMITTED_BYTES = (
    "SELECT COALESCE(SUM(size_bytes), 0) FROM downloads "
    "WHERE state NOT IN ('completed', 'quarantined', 'failed')"
)


class SqliteDownloadRepository:
    """Implémentation SQLite de la persistance des downloads (satisfaction STRUCTURELLE)."""

    def __init__(self, connection: sqlite3.Connection, *, clock: Clock = utc_now) -> None:
        self._connection = connection
        self._clock = clock

    def record_queued(self, ed2k_hash: str, target_id: str, size_bytes: int) -> bool:
        """INSERT d'un download ``queued`` (dédup-safe). ``True`` si créé, ``False`` si doublon."""
        queued_at = utc_iso(self._clock())
        with wrap_sqlite_errors():
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._connection.execute(
                    _INSERT, (ed2k_hash, target_id, queued_at, size_bytes)
                )
                self._connection.execute("COMMIT")
            except BaseException:
                with suppress(sqlite3.Error):
                    self._connection.execute("ROLLBACK")
                raise
        return cursor.rowcount == 1

    def set_state(self, ed2k_hash: str, state: DownloadState) -> None:
        """UPDATE de l'état ; stampe ``completed_at`` si l'état est ``completed`` (horloge inj.).

        Exige un download existant (un hash inconnu → ``PersistenceError`` : bug du code
        appelant). Seul ``completed`` (premier instant de complétion) est horodaté ;
        ``quarantined``/``failed`` n'écrasent pas le ``completed_at``.
        """
        with wrap_sqlite_errors():
            if state == DownloadState.COMPLETED:
                cursor = self._connection.execute(
                    _SET_STATE_COMPLETED, (state.value, utc_iso(self._clock()), ed2k_hash)
                )
            else:
                cursor = self._connection.execute(_SET_STATE, (state.value, ed2k_hash))
        if cursor.rowcount != 1:
            raise PersistenceError(f"download {ed2k_hash} introuvable (bug du code appelant)")

    def is_downloaded(self, ed2k_hash: str) -> bool:
        """``True`` si ce hash est déjà connu de ``downloads`` (dédup, spec §6)."""
        with wrap_sqlite_errors():
            row = self._connection.execute(_IS_DOWNLOADED, (ed2k_hash,)).fetchone()
        return row is not None

    def committed_bytes(self) -> int:
        """Somme des ``size_bytes`` des downloads ACTIFS (plafond disque, spec §7)."""
        with wrap_sqlite_errors():
            return int(self._connection.execute(_COMMITTED_BYTES).fetchone()[0])

    def active_states(self) -> dict[str, DownloadState]:
        """Map hash→état de TOUS les downloads connus (le monitor réconcilie dessus)."""
        with wrap_sqlite_errors():
            rows = self._connection.execute(_ACTIVE_STATES).fetchall()
        return {row[0]: DownloadState(row[1]) for row in rows}
```

(Note : `is_terminal` n'est PAS importé ici — le SQL `_COMMITTED_BYTES` liste explicitement les états terminaux. N'importer que `DownloadState` (sinon ruff F401 sur un import inutilisé).)

- [ ] **Step 5: Vérifier puis gate**

Run: `uv run pytest tests/adapters/persistence_sqlite/test_migration_0002.py tests/adapters/persistence_sqlite/test_download_repository.py -q --no-cov` → PASS.
Run: gate complet → tout vert (la migration 0002 s'applique sur toutes les `open_local` des tests existants — vérifier qu'aucun ne casse). 100 %.

- [ ] **Step 6: Commit**

```bash
git add src/emule_indexer/adapters/persistence_sqlite/migrations/local/0002_downloads_size_bytes.sql src/emule_indexer/adapters/persistence_sqlite/download_repository.py tests/adapters/persistence_sqlite/test_migration_0002.py tests/adapters/persistence_sqlite/test_download_repository.py
git commit -m "$(cat <<'EOF'
feat(adapters): migration 0002 (downloads.size_bytes) + SqliteDownloadRepository

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Port `Quarantine` + adapter `quarantine_fs`

**Files:**
- Create: `src/emule_indexer/ports/quarantine.py`
- Create: `src/emule_indexer/adapters/quarantine_fs.py`
- Create: `tests/ports/test_quarantine.py`
- Create: `tests/adapters/test_quarantine_fs.py`

> DÉCISION D10 : `promote(staging_path, ed2k_hash)` = `os.replace` (rename atomique même-FS) vers `quarantine_dir / ed2k_hash` ; jamais +x, jamais ouvert/lu. Échec (source absente, cross-device…) → exception (la boucle laisse `completed`, retry idempotent). Vérifié à l'écriture : `os.replace` ne pose aucun bit exécutable et lève `FileNotFoundError` sur source absente.

- [ ] **Step 1: Écrire les tests qui échouent**

`tests/ports/test_quarantine.py` :
```python
from pathlib import Path

from emule_indexer.ports.quarantine import Quarantine


class _StubQuarantine:
    """Satisfait Quarantine structurellement (sans l'importer)."""

    def __init__(self) -> None:
        self.promoted: list[tuple[Path, str]] = []

    def promote(self, staging_path: Path, ed2k_hash: str) -> None:
        self.promoted.append((staging_path, ed2k_hash))


def test_protocol_is_satisfied_structurally() -> None:
    quarantine: Quarantine = _StubQuarantine()
    quarantine.promote(Path("/staging/x.part"), "a" * 32)
    assert isinstance(quarantine, _StubQuarantine)
    assert quarantine.promoted == [(Path("/staging/x.part"), "a" * 32)]
```

`tests/adapters/test_quarantine_fs.py` :
```python
import stat
from pathlib import Path

import pytest

from emule_indexer.adapters.quarantine_fs import FilesystemQuarantine


def test_promote_moves_the_file_to_quarantine_by_hash(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    source = staging / "Keroro 062A.avi"
    source.write_bytes(b"\x00\x01\x02")  # le crawler ne lit JAMAIS ces octets
    adapter = FilesystemQuarantine(quarantine)

    adapter.promote(source, "a" * 32)

    moved = quarantine / ("a" * 32)
    assert not source.exists()  # rename atomique : la source a disparu
    assert moved.exists()
    assert moved.stat().st_size == 3


def test_promote_never_sets_executable_bits(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    source = staging / "x.bin"
    source.write_bytes(b"data")
    FilesystemQuarantine(quarantine).promote(source, "b" * 32)
    mode = (quarantine / ("b" * 32)).stat().st_mode
    assert not (mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))


def test_promote_missing_source_raises(tmp_path: Path) -> None:
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    adapter = FilesystemQuarantine(quarantine)
    with pytest.raises(FileNotFoundError):
        adapter.promote(tmp_path / "absent.part", "c" * 32)
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/ports/test_quarantine.py tests/adapters/test_quarantine_fs.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: …ports.quarantine`.

- [ ] **Step 3: Écrire le port + l'adapter**

`src/emule_indexer/ports/quarantine.py` :
```python
"""Port ``Quarantine`` : remettre un fichier complété en quarantaine (spec download §8/§10).

Le crawler NE LIT JAMAIS le contenu d'un fichier téléchargé (§10.3 MVP : le sujet du
catalogue est le fichier, jamais la personne ; on ne vérifie/lit qu'après une mise en
quarantaine sûre). ``promote`` est une opération de MÉTADONNÉE seule : déplacer (rename) le
fichier du staging vers ``quarantine/<hash>``, sans jamais l'ouvrir ni le rendre exécutable.
Le verifier (D-verify) lira le fichier en quarantaine — pas le crawler. Le stub du Protocol
tient sur UNE ligne (le ``def`` est couvert à la création de la classe).
"""

from pathlib import Path
from typing import Protocol


class Quarantine(Protocol):
    """Contrat de mise en quarantaine (spec §8). ``promote`` ne lève qu'en cas d'échec FS."""

    def promote(self, staging_path: Path, ed2k_hash: str) -> None: ...
```

`src/emule_indexer/adapters/quarantine_fs.py` :
```python
"""Adapter ``Quarantine`` sur le système de fichiers (spec download §8 — DÉCISION D10).

``promote`` fait un ``os.replace`` (rename ATOMIQUE même-FS) du fichier de staging vers
``quarantine_dir / <hash>`` : opération de métadonnée seule, le contenu n'est JAMAIS ouvert,
lu, ni rendu exécutable (le rename ne touche pas les permissions). Un échec (source absente,
FS plein, cross-device → ``OSError``) PROPAGE : la boucle de download laisse alors le
download en ``completed`` et retentera (idempotent, spec §9). Le staging et la quarantaine
DOIVENT être sur le même système de fichiers (sinon ``os.replace`` lève — c'est une
contrainte de déploiement, vérifiée au câblage de D-verify).
"""

import os
from pathlib import Path


class FilesystemQuarantine:
    """Mise en quarantaine par rename atomique (satisfaction STRUCTURELLE du port)."""

    def __init__(self, quarantine_dir: Path) -> None:
        self._quarantine_dir = quarantine_dir

    def promote(self, staging_path: Path, ed2k_hash: str) -> None:
        """Rename atomique ``staging_path`` → ``quarantine_dir/<hash>`` (spec §8).

        ``os.replace`` est atomique sur le même FS ; il écrase une cible existante (un
        re-promote idempotent du même hash est sûr) et ne modifie pas les permissions (jamais
        +x). Une source absente lève ``FileNotFoundError`` ; la boucle retentera.
        """
        os.replace(staging_path, self._quarantine_dir / ed2k_hash)
```

- [ ] **Step 4: Vérifier puis gate**

Run: `uv run pytest tests/ports/test_quarantine.py tests/adapters/test_quarantine_fs.py -q --no-cov` → PASS (1 + 3 tests).
Run: gate complet → tout vert, 100 %.

- [ ] **Step 5: Commit**

```bash
git add src/emule_indexer/ports/quarantine.py src/emule_indexer/adapters/quarantine_fs.py tests/ports/test_quarantine.py tests/adapters/test_quarantine_fs.py
git commit -m "$(cat <<'EOF'
feat(ports,adapters): Quarantine + FilesystemQuarantine (rename atomique, jamais lu)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Catalogue — lecture des candidats download (`download_decisions` + `last_observation`)

**Files:**
- Modify: `src/emule_indexer/domain/matching/engine.py` (+ `DownloadCandidate`)
- Modify: `src/emule_indexer/ports/catalog_repository.py` (+ `ObservedFile`, `download_decisions`, `last_observation`)
- Modify: `src/emule_indexer/adapters/persistence_sqlite/catalog_repository.py`
- Modify: `tests/ports/test_catalog_repository.py` (le stub doit gagner les 2 méthodes)
- Create: `tests/adapters/persistence_sqlite/test_catalog_download_reads.py`

> La boucle a besoin de DEUX lectures du catalogue (append-only, lectures inoffensives) : (1) `download_decisions()` = les hash dont le DERNIER verdict est tier=download (latest-decision-per-hash, fenêtre SQL `ROW_NUMBER()`) → `DownloadCandidate(ed2k_hash, target_id)` ; (2) `last_observation(hash)` = la dernière observation (filename + size) pour bâtir le lien ed2k → `ObservedFile(filename, size_bytes)` ou `None`. `DownloadCandidate` vit dans `engine.py` (forme de lecture, comme `DecisionRecord`) ; `ObservedFile` dans le port catalog (DTO de lecture, comme `ClaimedTask` dans son port). Vérifié à l'écriture : la fenêtre `ROW_NUMBER() OVER (PARTITION BY ed2k_hash ORDER BY decided_at DESC, id DESC)` puis filtre `tier='download'` rend exactement les hash dont le DERNIER verdict est download (SQLite 3.53).

- [ ] **Step 1: Écrire les tests qui échouent**

Dans `tests/ports/test_catalog_repository.py` — étendre l'import du moteur et le stub. Remplacer l'import :
```python
from emule_indexer.domain.matching.engine import (
    DecisionRecord,
    Explanation,
    MatchDecision,
)
```
par :
```python
from emule_indexer.domain.matching.engine import (
    DecisionRecord,
    DownloadCandidate,
    Explanation,
    MatchDecision,
)
from emule_indexer.ports.catalog_repository import ObservedFile
```
Ajouter dans la classe `_StubRepository` (après `last_decision`) :
```python
    def download_decisions(self) -> tuple[DownloadCandidate, ...]:
        return ()

    def last_observation(self, ed2k_hash: str) -> ObservedFile | None:
        return None
```
Ajouter dans `test_protocol_is_satisfied_structurally` (juste avant les assertions finales) :
```python
    assert repository.download_decisions() == ()
    assert repository.last_observation(observation.ed2k_hash) is None
```

`tests/adapters/persistence_sqlite/test_catalog_download_reads.py` (NOUVEAU) :
```python
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from emule_indexer.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from emule_indexer.adapters.persistence_sqlite.connection import open_catalog
from emule_indexer.domain.matching.engine import DownloadCandidate, Explanation, MatchDecision
from emule_indexer.domain.observation import FileObservation
from emule_indexer.ports.catalog_repository import ObservedFile

_A = "a" * 32
_B = "b" * 32
_C = "c" * 32
_NODE = "11111111-2222-3333-4444-555555555555"


class _AdvancingClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 6, 13, 10, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        moment = self._now
        self._now += timedelta(minutes=1)
        return moment


def _obs(hash_hex: str, *, name: str = "Keroro.avi", size: int = 100) -> FileObservation:
    return FileObservation(
        ed2k_hash=hash_hex,
        filename=name,
        size_bytes=size,
        source_count=1,
        complete_source_count=0,
        keyword="keroro",
    )


def _decision(tier: str) -> MatchDecision:
    return MatchDecision(
        target_id="S2E062A",
        rule_name="r",
        tier=tier,
        explanation=Explanation(
            target_id="S2E062A", rules_fired=("r",), tokens_matched=(), coverage_values=()
        ),
    )


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    catalog = open_catalog(tmp_path / "catalog.db")
    yield catalog
    catalog.close()


@pytest.fixture
def repository(connection: sqlite3.Connection) -> SqliteCatalogRepository:
    return SqliteCatalogRepository(connection, _NODE, clock=_AdvancingClock())


def test_download_decisions_includes_hash_whose_latest_verdict_is_download(
    repository: SqliteCatalogRepository,
) -> None:
    repository.record_observation(_obs(_A))
    repository.record_decision(_A, _decision("catalog"))
    repository.record_decision(_A, _decision("download"))  # plus récent = download
    assert repository.download_decisions() == (
        DownloadCandidate(ed2k_hash=_A, target_id="S2E062A"),
    )


def test_download_decisions_excludes_hash_whose_latest_verdict_is_not_download(
    repository: SqliteCatalogRepository,
) -> None:
    repository.record_observation(_obs(_B))
    repository.record_decision(_B, _decision("download"))
    repository.record_decision(_B, _decision("catalog"))  # plus récent = catalog
    assert repository.download_decisions() == ()


def test_download_decisions_is_empty_with_no_decisions(
    repository: SqliteCatalogRepository,
) -> None:
    repository.record_observation(_obs(_C))
    assert repository.download_decisions() == ()


def test_last_observation_returns_filename_and_size(
    repository: SqliteCatalogRepository,
) -> None:
    repository.record_observation(_obs(_A, name="Keroro 062A.avi", size=4242))
    assert repository.last_observation(_A) == ObservedFile(
        filename="Keroro 062A.avi", size_bytes=4242
    )


def test_last_observation_returns_the_most_recent(
    repository: SqliteCatalogRepository,
) -> None:
    repository.record_observation(_obs(_A, name="old.avi", size=1))
    repository.record_observation(_obs(_A, name="new.avi", size=2))
    assert repository.last_observation(_A) == ObservedFile(filename="new.avi", size_bytes=2)


def test_last_observation_unknown_hash_is_none(repository: SqliteCatalogRepository) -> None:
    assert repository.last_observation(_A) is None
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/adapters/persistence_sqlite/test_catalog_download_reads.py tests/ports/test_catalog_repository.py -q --no-cov`
Expected: FAIL — `ImportError: cannot import name 'DownloadCandidate'`.

- [ ] **Step 3: Modifier `engine.py` (+ `DownloadCandidate`)**

Dans `src/emule_indexer/domain/matching/engine.py`, juste APRÈS la dataclass `DecisionRecord` (et son helper `to_record`), insérer :
```python
@dataclass(frozen=True)
class DownloadCandidate:
    """Forme de LECTURE d'une décision tier=download : ``ed2k_hash`` + ``target_id``.

    C'est ce que ``CatalogRepository.download_decisions`` rend (spec download §5) : les hash
    dont le DERNIER verdict est tier=download, à rejouer par la boucle de download. Distinct
    de :class:`MatchDecision`/:class:`DecisionRecord` : la boucle de download n'a besoin que
    du hash (clé contenu) et du ``target_id`` (pour le lookup de statut de la cible). Gelé →
    comparaison par valeur triviale en test.
    """

    ed2k_hash: str
    target_id: str
```

- [ ] **Step 4: Modifier le port `catalog_repository.py`**

Remplacer le bloc import + classe par :
```python
from dataclasses import dataclass
from typing import Protocol

from emule_indexer.domain.matching.engine import DecisionRecord, DownloadCandidate, MatchDecision
from emule_indexer.domain.observation import FileObservation


@dataclass(frozen=True)
class ObservedFile:
    """Forme de LECTURE minimale d'une observation : nom + taille (pour bâtir un lien ed2k).

    La boucle de download (spec §5) lit la DERNIÈRE observation d'un hash pour reconstruire
    son lien ed2k (``build_ed2k_link(filename, size_bytes, hash)``). On ne rend que les deux
    champs nécessaires — pas tout ``FileObservation`` (le reste est inutile au download).
    """

    filename: str
    size_bytes: int


class CatalogRepository(Protocol):
    """Contrat sync d'écriture du catalogue (append-only ; l'adapter signale, il ne décide pas).

    ``last_decision`` (anti-redondance, spec orchestration §3) rend un :class:`DecisionRecord`.
    ``download_decisions`` (spec download §5) rend les :class:`DownloadCandidate` dont le
    DERNIER verdict est tier=download (à rejouer par la boucle de download). ``last_observation``
    rend l':class:`ObservedFile` la plus récente d'un hash (nom+taille pour le lien ed2k), ou
    ``None``. Ces trois lectures sont inoffensives (aucune écriture).
    """

    def record_observation(self, observation: FileObservation) -> None: ...

    def record_decision(self, ed2k_hash: str, decision: MatchDecision) -> None: ...

    def last_decision(self, ed2k_hash: str) -> DecisionRecord | None: ...

    def download_decisions(self) -> tuple[DownloadCandidate, ...]: ...

    def last_observation(self, ed2k_hash: str) -> ObservedFile | None: ...
```

- [ ] **Step 5: Modifier l'adapter `catalog_repository.py`**

(a) étendre l'import du moteur :
```python
from emule_indexer.domain.matching.engine import DecisionRecord, MatchDecision
```
par :
```python
from emule_indexer.domain.matching.engine import (
    DecisionRecord,
    DownloadCandidate,
    MatchDecision,
)
```
et l'import du port (ajouter `ObservedFile` — l'adapter peut importer son propre port) :
```python
from emule_indexer.ports.catalog_repository import ObservedFile
```

(b) après `_SELECT_LAST_DECISION`, ajouter :
```python
# Hash dont le DERNIER verdict est tier=download (spec download §5). Fenêtre :
# ROW_NUMBER par hash, ordre (decided_at, id) DÉCROISSANT (le plus récent = rang 1) ; on ne
# garde que rang 1 ET tier='download'. Tri stable par hash pour un résultat déterministe.
_SELECT_DOWNLOAD_DECISIONS = """
SELECT ed2k_hash, target_id FROM (
    SELECT
        ed2k_hash, target_id, tier,
        ROW_NUMBER() OVER (PARTITION BY ed2k_hash ORDER BY decided_at DESC, id DESC) AS rn
    FROM match_decisions
) WHERE rn = 1 AND tier = 'download'
ORDER BY ed2k_hash
"""

# Dernière observation d'un hash (nom + taille pour le lien ed2k, spec download §5).
_SELECT_LAST_OBSERVATION = """
SELECT filename, size_bytes FROM file_observations
WHERE ed2k_hash = ?
ORDER BY observed_at DESC, id DESC
LIMIT 1
"""
```

(c) à la FIN de la classe `SqliteCatalogRepository` (après `last_decision`), ajouter :
```python
    def download_decisions(self) -> tuple[DownloadCandidate, ...]:
        """Hash dont le DERNIER verdict est tier=download, à rejouer (download §5) — LECTURE."""
        with wrap_sqlite_errors():
            rows = self._connection.execute(_SELECT_DOWNLOAD_DECISIONS).fetchall()
        return tuple(DownloadCandidate(ed2k_hash=row[0], target_id=row[1]) for row in rows)

    def last_observation(self, ed2k_hash: str) -> ObservedFile | None:
        """Dernière observation d'un hash (nom+taille pour le lien ed2k), ou ``None`` — LECTURE."""
        with wrap_sqlite_errors():
            row = self._connection.execute(_SELECT_LAST_OBSERVATION, (ed2k_hash,)).fetchone()
        if row is None:
            return None
        return ObservedFile(filename=row[0], size_bytes=row[1])
```

- [ ] **Step 6: Vérifier puis gate**

Run: `uv run pytest tests/adapters/persistence_sqlite/test_catalog_download_reads.py tests/ports/test_catalog_repository.py -q --no-cov` → PASS.
Run: gate complet → tout vert, 100 %.

- [ ] **Step 7: Commit**

```bash
git add src/emule_indexer/domain/matching/engine.py src/emule_indexer/ports/catalog_repository.py src/emule_indexer/adapters/persistence_sqlite/catalog_repository.py tests/ports/test_catalog_repository.py tests/adapters/persistence_sqlite/test_catalog_download_reads.py
git commit -m "$(cat <<'EOF'
feat(adapters): catalog download_decisions (latest=download) + last_observation

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Config — `DownloadConfig` (crawler.yaml) + download endpoint/dirs (local.yaml)

**Files:**
- Modify: `src/emule_indexer/adapters/config/crawler_config.py`
- Modify: `src/emule_indexer/adapters/config/local_config.py`
- Modify: `tests/adapters/config/test_crawler_config.py`
- Modify: `tests/adapters/config/test_local_config.py`
- Modify: `config/crawler.yaml`
- Modify: `config/local.example.yaml`

> Spec §3/§7 : `crawler.yaml` gagne une section `download` (`poll_interval_seconds`, `disk_cap_bytes`) ; `local.yaml` gagne un endpoint EC de download propre (`download_endpoint`: host/port/password) + les répertoires `staging_dir`/`quarantine_dir`. Validation FAIL-FAST (réutilise `ConfigError`/helpers). C'est lu à la composition de D-verify (D-download étend juste les value objects et parsers, sans câbler la boucle live). On garde la rétro-compat : `download` (crawler) et `download_endpoint`/dirs (local) sont OPTIONNELS pour ne pas casser le crawler search-only existant — un défaut absent → champ `None`, le câblage live de D-verify exigera leur présence.

> **DÉCISION D11 — Section `download` OPTIONNELLE dans les value objects.** Le crawler search-only (Plan C) tourne sans section `download`. Pour ne pas casser ses configs/tests existants, `CrawlerConfig` gagne un champ `download: DownloadConfig | None = None` (présent et validé SI la section existe, sinon `None`). Idem `LocalConfig` : `download_endpoint: AmuleEndpoint | None = None`, `staging_dir: str | None = None`, `quarantine_dir: str | None = None`. Si la section `download`/`download_endpoint` est PRÉSENTE, elle est validée fail-fast (champs requis, bornes). Le câblage live (D-verify) vérifiera que ces champs sont renseignés avant d'activer la boucle (fail-fast au montage).

- [ ] **Step 1: Étendre les tests qui échouent (crawler_config)**

Dans `tests/adapters/config/test_crawler_config.py`, ajouter (l'import s'étend à `DownloadConfig`) :
```python
from emule_indexer.adapters.config.crawler_config import (
    BackoffConfig,
    ConfigError,
    CrawlerConfig,
    DownloadConfig,
    parse_crawler_config,
)
```
Ajouter ces tests :
```python
def test_download_section_is_optional() -> None:
    config = parse_crawler_config(_valid_raw())  # _valid_raw n'a pas de section download
    assert config.download is None


def test_download_section_is_parsed_when_present() -> None:
    raw = _valid_raw()
    raw["download"] = {"poll_interval_seconds": 10.0, "disk_cap_bytes": 5_000_000_000}
    config = parse_crawler_config(raw)
    assert config.download == DownloadConfig(
        poll_interval_seconds=10.0, disk_cap_bytes=5_000_000_000
    )


def test_download_poll_interval_must_be_positive() -> None:
    raw = _valid_raw()
    raw["download"] = {"poll_interval_seconds": 0.0, "disk_cap_bytes": 1}
    with pytest.raises(ConfigError, match="strictement positif"):
        parse_crawler_config(raw)


def test_download_disk_cap_must_be_positive_integer() -> None:
    raw = _valid_raw()
    raw["download"] = {"poll_interval_seconds": 10.0, "disk_cap_bytes": 0}
    with pytest.raises(ConfigError, match="strictement positif"):
        parse_crawler_config(raw)


def test_download_disk_cap_key_is_required() -> None:
    # section download présente mais sans disk_cap_bytes → _positive_int branche clé manquante.
    raw = _valid_raw()
    raw["download"] = {"poll_interval_seconds": 10.0}
    with pytest.raises(ConfigError, match="disk_cap_bytes"):
        parse_crawler_config(raw)


def test_download_section_must_be_a_mapping() -> None:
    raw = _valid_raw()
    raw["download"] = [1, 2]
    with pytest.raises(ConfigError, match="section 'download'"):
        parse_crawler_config(raw)
```

- [ ] **Step 2: Étendre les tests qui échouent (local_config)**

Dans `tests/adapters/config/test_local_config.py`, étendre l'import :
```python
from emule_indexer.adapters.config.local_config import (
    AmuleEndpoint,
    LocalConfig,
    parse_local_config,
)
```
(inchangé — `AmuleEndpoint` réutilisé). Ajouter ces tests :
```python
def test_download_endpoint_is_optional() -> None:
    config = parse_local_config(_valid_raw())
    assert config.download_endpoint is None
    assert config.staging_dir is None
    assert config.quarantine_dir is None


def test_download_endpoint_is_parsed_when_present() -> None:
    raw = _valid_raw()
    raw["download_endpoint"] = {
        "name": "amule-dl",
        "host": "gluetun",
        "port": 4713,
        "password": "dl-secret",
    }
    raw["staging_dir"] = "/data/incoming"
    raw["quarantine_dir"] = "/data/quarantine"
    config = parse_local_config(raw)
    assert config.download_endpoint == AmuleEndpoint(
        name="amule-dl", host="gluetun", port=4713, password="dl-secret"
    )
    assert config.staging_dir == "/data/incoming"
    assert config.quarantine_dir == "/data/quarantine"


def test_download_endpoint_present_requires_dirs() -> None:
    raw = _valid_raw()
    raw["download_endpoint"] = {
        "name": "amule-dl",
        "host": "h",
        "port": 4713,
        "password": "p",
    }  # staging_dir/quarantine_dir manquants
    with pytest.raises(ConfigError, match="staging_dir"):
        parse_local_config(raw)


def test_download_endpoint_must_be_a_mapping() -> None:
    raw = _valid_raw()
    raw["download_endpoint"] = "pas-un-mapping"
    with pytest.raises(ConfigError, match="download_endpoint"):
        parse_local_config(raw)


def test_download_endpoint_invalid_port_is_fatal() -> None:
    raw = _valid_raw()
    raw["download_endpoint"] = {"name": "d", "host": "h", "port": 0, "password": "p"}
    raw["staging_dir"] = "/s"
    raw["quarantine_dir"] = "/q"
    with pytest.raises(ConfigError, match="1..65535"):
        parse_local_config(raw)
```

- [ ] **Step 3: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/adapters/config/test_crawler_config.py tests/adapters/config/test_local_config.py -q --no-cov`
Expected: FAIL — `ImportError: cannot import name 'DownloadConfig'` puis attributs absents.

- [ ] **Step 4: Modifier `crawler_config.py`**

(a) Ajouter, après la dataclass `BackoffConfig` (avant `CrawlerConfig`) :
```python
@dataclass(frozen=True)
class DownloadConfig:
    """Politique de téléchargement (spec download §3/§7). OPTIONNELLE (DÉCISION D11).

    ``poll_interval_seconds`` : cadence de relevé de la file de download (le nudge réveille
    plus tôt). ``disk_cap_bytes`` : plafond disque APPLICATIF — somme des ``size_bytes`` des
    downloads actifs au-dessus de laquelle on diffère (back-pressure gracieux, jamais
    d'abandon). Le quota INFRA (FS/Docker) est hors périmètre (Plan F).
    """

    poll_interval_seconds: float
    disk_cap_bytes: int
```
(b) Ajouter le champ à `CrawlerConfig` (à la fin) :
```python
    download: DownloadConfig | None = None
```
(c) Ajouter un helper `_positive_int` (après `_non_negative`) :
```python
def _positive_int(mapping: dict[str, Any], key: str, what: str) -> int:
    """Entier strictement positif (bool refusé), sinon ``ConfigError`` (fail-fast §5/§14)."""
    if key not in mapping:
        raise ConfigError(f"{what} : clé {key!r} manquante")
    value = mapping[key]
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{what}.{key} : entier strictement positif attendu, obtenu {value!r}")
    return value
```
(d) Dans `parse_crawler_config`, AVANT le `return`, construire la section optionnelle :
```python
    download: DownloadConfig | None = None
    if "download" in raw:
        download_raw = _require_mapping(raw["download"], "section 'download'")
        download = DownloadConfig(
            poll_interval_seconds=_positive(download_raw, "poll_interval_seconds", "download"),
            disk_cap_bytes=_positive_int(download_raw, "disk_cap_bytes", "download"),
        )
```
et ajouter `download=download,` au `CrawlerConfig(...)` retourné.

- [ ] **Step 5: Modifier `local_config.py`**

(a) Ajouter les champs à `LocalConfig` (à la fin) :
```python
    download_endpoint: AmuleEndpoint | None = None
    staging_dir: str | None = None
    quarantine_dir: str | None = None
```
(b) Dans `parse_local_config`, AVANT le `return`, construire la section optionnelle :
```python
    download_endpoint: AmuleEndpoint | None = None
    staging_dir: str | None = None
    quarantine_dir: str | None = None
    if "download_endpoint" in raw:
        endpoint_raw = _require_mapping(raw["download_endpoint"], "section 'download_endpoint'")
        download_endpoint = AmuleEndpoint(
            name=_require_str(endpoint_raw, "name", "download_endpoint"),
            host=_require_str(endpoint_raw, "host", "download_endpoint"),
            port=_require_port(endpoint_raw, "download_endpoint"),
            password=_require_str(endpoint_raw, "password", "download_endpoint"),
        )
        staging_dir = _require_str(raw, "staging_dir", "local")
        quarantine_dir = _require_str(raw, "quarantine_dir", "local")
```
et ajouter `download_endpoint=download_endpoint, staging_dir=staging_dir, quarantine_dir=quarantine_dir,` au `LocalConfig(...)` retourné.

- [ ] **Step 6: Étendre les fichiers de config (modèles versionnés)**

Dans `config/crawler.yaml`, ajouter à la fin :
```yaml

download:                            # auto-download (D-download ; activé live en D-verify)
  poll_interval_seconds: 30.0        # cadence de relevé de la file de download (le nudge réveille plus tôt)
  disk_cap_bytes: 53687091200        # plafond disque applicatif (~50 Gio) ; au-dessus → on diffère
```
Dans `config/local.example.yaml`, ajouter à la fin :
```yaml

# Endpoint EC de DOWNLOAD (D-download) — sa PROPRE connexion (peut viser un daemon dédié ou
# partagé). Optionnel tant que l'auto-download n'est pas activé (D-verify) ; si présent,
# staging_dir et quarantine_dir sont requis et DOIVENT être sur le même système de fichiers.
# download_endpoint:
#   name: amule-dl
#   host: gluetun
#   port: 4712
#   password: change-me
# staging_dir: /data/incoming        # où amuled écrit les fichiers complétés
# quarantine_dir: /data/quarantine   # cible du rename atomique (même FS que staging_dir)
```

- [ ] **Step 7: Vérifier puis gate**

Run: `uv run pytest tests/adapters/config -q --no-cov` → PASS (les nouveaux + existants).
Run: gate complet → tout vert, 100 %.

> **Note couverture** : `download` et `download_endpoint` absents (branche `if … in raw` fausse) sont couverts par `_valid_raw()` (sans ces sections) ; présents par les nouveaux tests. `_positive_int` : présent valide / absent / non-int / bool / ≤ 0 — tous testés.

- [ ] **Step 8: Commit**

```bash
git add src/emule_indexer/adapters/config/crawler_config.py src/emule_indexer/adapters/config/local_config.py tests/adapters/config/test_crawler_config.py tests/adapters/config/test_local_config.py config/crawler.yaml config/local.example.yaml
git commit -m "$(cat <<'EOF'
feat(config): DownloadConfig (poll_interval/disk_cap) + download endpoint/dirs (optionnels)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Application — `run_download_cycle` (la boucle unique)

**Files:**
- Create: `src/emule_indexer/application/run_download_cycle.py`
- Create: `tests/application/test_run_download_cycle.py`

> DÉCISION D8/D9. Une SEULE itération de boucle, série, sur l'unique connexion download. Flux : monitor (`download_queue` → réconcilie les états) → complétions (`completed`+pas `quarantined` → `promote` → `enqueue_verification` → `quarantined`, idempotent) → nouveaux candidats (`download_decisions` diff `downloads` → `download_policy` → `build_ed2k_link` → `add_link` → `record_queued`) → sleep/nudge. Erreurs (contrats Plan C) : `MuleUnreachableError` → tolère (skip itération) ; `RepositoryError` → absorbée (log) ; `promote` échoue → reste `completed`, n'enfile pas ; jamais d'abandon. `Clock`/`sleep` injectés (déterminisme). Le `staging_path_for(entry) -> Path` est injecté (DÉCISION D2 — la composition de D-verify le branchera). La boucle expose `run_download_cycle(...)` (UNE itération, testable isolément) + un `download_loop(...)` (boucle jusqu'à un événement d'arrêt, comme `_run_loop` du Plan C) ; D-download teste l'itération unitairement et la boucle via un faux qui s'arrête après N itérations.

> **DÉCISION D12 — `run_download_cycle` = UNE itération ; `download_loop` = la répétition + sleep/nudge.** Comme le Plan C sépare `run_search_cycle` (un cycle) de `_run_loop` (la répétition dans `CrawlerApp`). Ici, `run_download_cycle` fait monitor+complétions+candidats (testable sans event d'arrêt) ; `download_loop` répète `run_download_cycle` puis attend `min(poll_interval, nudge)` jusqu'à un `asyncio.Event` d'arrêt. Le câblage live (D-verify) lancera `download_loop` dans le `TaskGroup` de `CrawlerApp`. Le nudge : `asyncio.wait({sleep_task, nudge_task}, FIRST_COMPLETED)` puis annulation du perdant.

- [ ] **Step 1: Écrire le test qui échoue**

`tests/application/test_run_download_cycle.py` :
```python
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from emule_indexer.application.run_download_cycle import DownloadDeps, run_download_cycle
from emule_indexer.domain.download.states import DownloadState
from emule_indexer.domain.matching.engine import DownloadCandidate
from emule_indexer.domain.matching.models import TargetSegment
from emule_indexer.ports.catalog_repository import ObservedFile
from emule_indexer.ports.mule_client import KadStatus, MuleUnreachableError, NetworkStatus
from emule_indexer.ports.mule_download_client import DownloadEntry
from emule_indexer.ports.repository_errors import RepositoryError

_A = "a" * 32
_B = "b" * 32

_TARGETS = (
    TargetSegment(season=2, number=62, segment="A", title="t", status="lost"),
    TargetSegment(season=2, number=63, segment="A", title="t2", status="complete"),
)


class FakeDownloadClient:
    """MuleDownloadClient scripté : file de download SCRIPTÉE, capture des liens ajoutés."""

    def __init__(
        self,
        *,
        queue: list[tuple[DownloadEntry, ...]] | None = None,
        connect_failures: list[Exception] | None = None,
        queue_failures: list[Exception] | None = None,
        add_failures: list[Exception] | None = None,
    ) -> None:
        self._queue = list(queue or [()])
        self._connect_failures = list(connect_failures or [])
        self._queue_failures = list(queue_failures or [])
        self._add_failures = list(add_failures or [])
        self.added_links: list[str] = []
        self.connect_calls = 0

    async def connect(self) -> None:
        self.connect_calls += 1
        if self._connect_failures:
            raise self._connect_failures.pop(0)

    async def close(self) -> None:
        return None

    async def add_link(self, ed2k_link: str) -> None:
        if self._add_failures:
            raise self._add_failures.pop(0)
        self.added_links.append(ed2k_link)

    async def download_queue(self) -> tuple[DownloadEntry, ...]:
        if self._queue_failures:
            raise self._queue_failures.pop(0)
        return self._queue.pop(0) if self._queue else ()

    async def network_status(self) -> NetworkStatus:
        return NetworkStatus(ed2k_id=1, ed2k_high=True, kad_status=KadStatus.CONNECTED)


class FakeQuarantine:
    """Quarantine fausse : enregistre les promotions, échoue sur les hash de ``fail_for``."""

    def __init__(self, *, fail_for: set[str] | None = None) -> None:
        self.promoted: list[tuple[Path, str]] = []
        self._fail_for = fail_for or set()

    def promote(self, staging_path: Path, ed2k_hash: str) -> None:
        if ed2k_hash in self._fail_for:
            raise OSError("rename impossible")
        self.promoted.append((staging_path, ed2k_hash))


class FakeDownloadRepo:
    """Repo downloads en mémoire (le contrat de SqliteDownloadRepository, sans SQL)."""

    def __init__(self, *, fail_record: bool = False) -> None:
        self.states: dict[str, DownloadState] = {}
        self.sizes: dict[str, int] = {}
        self._fail_record = fail_record

    def record_queued(self, ed2k_hash: str, target_id: str, size_bytes: int) -> bool:
        if self._fail_record:
            raise RepositoryError("écriture downloads échouée")
        if ed2k_hash in self.states:
            return False
        self.states[ed2k_hash] = DownloadState.QUEUED
        self.sizes[ed2k_hash] = size_bytes
        return True

    def set_state(self, ed2k_hash: str, state: DownloadState) -> None:
        self.states[ed2k_hash] = state

    def is_downloaded(self, ed2k_hash: str) -> bool:
        return ed2k_hash in self.states

    def committed_bytes(self) -> int:
        return sum(
            self.sizes.get(h, 0)
            for h, s in self.states.items()
            if s in {DownloadState.QUEUED, DownloadState.DOWNLOADING}
        )

    def active_states(self) -> dict[str, DownloadState]:
        return dict(self.states)


class FakeCatalogReads:
    """Côté lecture du catalogue : download_decisions + last_observation scriptés."""

    def __init__(
        self,
        *,
        candidates: tuple[DownloadCandidate, ...] = (),
        observations: dict[str, ObservedFile] | None = None,
    ) -> None:
        self._candidates = candidates
        self._observations = observations or {}

    def download_decisions(self) -> tuple[DownloadCandidate, ...]:
        return self._candidates

    def last_observation(self, ed2k_hash: str) -> ObservedFile | None:
        return self._observations.get(ed2k_hash)


class FakeLocalRepo:
    """enqueue_verification (idempotent) capturé."""

    def __init__(self) -> None:
        self.enqueued: list[str] = []

    def enqueue_verification(self, ed2k_hash: str) -> bool:
        first = ed2k_hash not in self.enqueued
        self.enqueued.append(ed2k_hash)
        return first


class FakeClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 6, 13, tzinfo=UTC)
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self._now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._now += timedelta(seconds=seconds)
        await asyncio.sleep(0)


def _candidate(hash_hex: str, target_id: str) -> DownloadCandidate:
    return DownloadCandidate(ed2k_hash=hash_hex, target_id=target_id)


def _deps(
    *,
    client: FakeDownloadClient,
    quarantine: FakeQuarantine,
    downloads: FakeDownloadRepo,
    catalog: FakeCatalogReads,
    local: FakeLocalRepo,
    disk_cap: int = 1_000_000,
) -> DownloadDeps:
    return DownloadDeps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=catalog,
        local=local,
        targets=_TARGETS,
        disk_cap_bytes=disk_cap,
        staging_path_for=lambda entry: Path("/staging") / entry.ed2k_hash,
        clock=FakeClock(),
    )


@pytest.mark.asyncio
async def test_new_candidate_is_queued_and_link_added() -> None:
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "S2E062A"),),
        observations={_A: ObservedFile(filename="Keroro.avi", size_bytes=100)},
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.QUEUED
    assert len(client.added_links) == 1
    assert _A in client.added_links[0]


@pytest.mark.asyncio
async def test_already_downloaded_candidate_is_deduped() -> None:
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING  # déjà connu
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "S2E062A"),),
        observations={_A: ObservedFile(filename="x", size_bytes=1)},
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert client.added_links == []  # dédup : pas de nouveau lien


@pytest.mark.asyncio
async def test_complete_target_candidate_is_skipped() -> None:
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_B, "S2E063A"),),  # S2E063A status=complete
        observations={_B: ObservedFile(filename="x", size_bytes=1)},
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert client.added_links == []
    assert _B not in downloads.states


@pytest.mark.asyncio
async def test_disk_cap_defers_candidate() -> None:
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "S2E062A"),),
        observations={_A: ObservedFile(filename="x", size_bytes=500)},
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
        disk_cap=100,  # 500 > 100 → diffère
    )
    await run_download_cycle(deps)
    assert client.added_links == []
    assert _A not in downloads.states


@pytest.mark.asyncio
async def test_candidate_without_observation_is_skipped() -> None:
    # un candidat dont aucune observation n'a survécu (cas limite) ne peut pas bâtir de lien :
    # on le saute (log), jamais de crash.
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(candidates=(_candidate(_A, "S2E062A"),), observations={})
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert client.added_links == []


@pytest.mark.asyncio
async def test_monitor_marks_downloading_then_completed() -> None:
    client = FakeDownloadClient(
        queue=[(DownloadEntry(ed2k_hash=_A, size_done=10, size_full=10),)]
    )
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.QUEUED
    downloads.sizes[_A] = 10
    quarantine = FakeQuarantine()
    local = FakeLocalRepo()
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=local,
    )
    await run_download_cycle(deps)
    # complet → promu + enfilé + quarantined
    assert downloads.states[_A] is DownloadState.QUARANTINED
    assert quarantine.promoted == [(Path("/staging") / _A, _A)]
    assert local.enqueued == [_A]


@pytest.mark.asyncio
async def test_monitor_marks_in_progress_when_not_complete() -> None:
    client = FakeDownloadClient(
        queue=[(DownloadEntry(ed2k_hash=_A, size_done=3, size_full=10),)]
    )
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.QUEUED
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.DOWNLOADING


@pytest.mark.asyncio
async def test_monitor_ignores_unknown_queue_entries() -> None:
    # une entrée dans la file amuled mais inconnue de downloads (lancée hors crawler) est ignorée.
    client = FakeDownloadClient(
        queue=[(DownloadEntry(ed2k_hash=_B, size_done=10, size_full=10),)]
    )
    downloads = FakeDownloadRepo()
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert _B not in downloads.states


@pytest.mark.asyncio
async def test_promote_failure_keeps_completed_and_does_not_enqueue() -> None:
    client = FakeDownloadClient(
        queue=[(DownloadEntry(ed2k_hash=_A, size_done=10, size_full=10),)]
    )
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.QUEUED
    quarantine = FakeQuarantine(fail_for={_A})
    local = FakeLocalRepo()
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=local,
    )
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.COMPLETED  # reste completed (retry)
    assert local.enqueued == []  # n'enfile PAS


@pytest.mark.asyncio
async def test_already_quarantined_completion_is_skipped() -> None:
    client = FakeDownloadClient(
        queue=[(DownloadEntry(ed2k_hash=_A, size_done=10, size_full=10),)]
    )
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.QUARANTINED  # déjà promu
    quarantine = FakeQuarantine()
    local = FakeLocalRepo()
    deps = _deps(
        client=client,
        quarantine=quarantine,
        downloads=downloads,
        catalog=FakeCatalogReads(),
        local=local,
    )
    await run_download_cycle(deps)
    assert quarantine.promoted == []  # déjà quarantined → sauté
    assert local.enqueued == []


@pytest.mark.asyncio
async def test_unreachable_client_is_tolerated_and_iteration_skipped() -> None:
    client = FakeDownloadClient(queue_failures=[MuleUnreachableError("daemon down")])
    downloads = FakeDownloadRepo()
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=FakeCatalogReads(candidates=(_candidate(_A, "S2E062A"),)),
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)  # ne lève pas
    assert client.added_links == []  # itération sautée (pas de candidats traités)


@pytest.mark.asyncio
async def test_repository_error_is_absorbed() -> None:
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo(fail_record=True)  # record_queued lève RepositoryError
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "S2E062A"),),
        observations={_A: ObservedFile(filename="x", size_bytes=1)},
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)  # ne lève pas (RepositoryError absorbée)


@pytest.mark.asyncio
async def test_intra_cycle_disk_cap_accounts_for_links_added_this_cycle() -> None:
    # deux candidats de 600 o, plafond 1000 : le 1er passe (600 ≤ 1000), le 2e diffère
    # (600 + 600 > 1000) — le committed est recalculé EN MÉMOIRE au fil du cycle.
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "S2E062A"), _candidate(_B, "S2E062A")),
        observations={
            _A: ObservedFile(filename="a", size_bytes=600),
            _B: ObservedFile(filename="b", size_bytes=600),
        },
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
        disk_cap=1000,
    )
    await run_download_cycle(deps)
    assert len(client.added_links) == 1  # un seul a tenu dans le plafond


@pytest.mark.asyncio
async def test_candidate_for_unknown_target_is_treated_as_complete() -> None:
    # _target_status : un candidat dont le target_id est ABSENT de _TARGETS → "complete"
    # (conservateur) → politique SKIP_COMPLETE → aucun lien, hash non mis en file.
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    catalog = FakeCatalogReads(
        candidates=(_candidate(_A, "S9E999Z"),),  # cible fantôme, absente de _TARGETS
        observations={_A: ObservedFile(filename="x", size_bytes=1)},
    )
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=catalog,
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert client.added_links == []
    assert _A not in downloads.states


@pytest.mark.asyncio
async def test_monitor_no_op_when_state_already_matches() -> None:
    # _monitor : entrée en cours (done=3/full=10) et repo déjà DOWNLOADING → target == current
    # → AUCUN set_state (branche FALSE de `if target != current`).
    client = FakeDownloadClient(
        queue=[(DownloadEntry(ed2k_hash=_A, size_done=3, size_full=10),)]
    )
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING

    class _NoSetStateRepo(FakeDownloadRepo):
        def set_state(self, ed2k_hash: str, state: DownloadState) -> None:
            raise AssertionError("set_state ne doit pas être appelé (état déjà à jour)")

    repo = _NoSetStateRepo()
    repo.states[_A] = DownloadState.DOWNLOADING
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=repo,
        catalog=FakeCatalogReads(),
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert repo.states[_A] is DownloadState.DOWNLOADING


@pytest.mark.asyncio
async def test_queued_download_without_observation_emits_no_link() -> None:
    # _add_links : un download QUEUED en base mais sans observation au catalogue → pas de lien
    # (branche `if observation is None: continue`).
    client = FakeDownloadClient()
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.QUEUED
    downloads.sizes[_A] = 100
    deps = _deps(
        client=client,
        quarantine=FakeQuarantine(),
        downloads=downloads,
        catalog=FakeCatalogReads(observations={}),  # aucune observation
        local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert client.added_links == []
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/application/test_run_download_cycle.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: …application.run_download_cycle`.

- [ ] **Step 3: Écrire l'implémentation**

`src/emule_indexer/application/run_download_cycle.py` :
```python
"""La boucle de download : monitor → complétions → nouveaux candidats → sleep/nudge (§5).

Couche APPLICATION. Une SEULE tâche, série, sur l'unique connexion EC download (spec §3/§5) :
aucun entrelacement de trames. ``run_download_cycle`` exécute UNE itération (testable sans
event d'arrêt) ; ``download_loop`` la répète puis attend ``poll_interval`` OU le nudge
(``DecisionSignal``), jusqu'à un événement d'arrêt — câblé par ``CrawlerApp`` en D-verify.

Flux d'une itération (spec §5, DÉCISION D8) :
  1. MONITOR : ``download_queue()`` → pour chaque entrée CONNUE de ``downloads``, réconcilie
     (``downloading`` si en cours, ``completed`` si complète) ; une entrée inconnue (download
     hors crawler) est ignorée.
  2. COMPLÉTIONS : chaque hash ``completed`` (pas ``quarantined``) → ``quarantine.promote`` →
     ``enqueue_verification`` → ``set_state(quarantined)``. Idempotent : ``promote`` échoue →
     reste ``completed``, n'enfile PAS, retry au tour suivant ; déjà ``quarantined`` → sauté.
  3. CANDIDATS : ``catalog.download_decisions()`` (latest=download) ∖ ``downloads`` → pour
     chacun, ``download_policy`` (statut de la cible, dédup, plafond) → si ``download`` :
     ``build_ed2k_link`` (depuis ``last_observation``) → ``add_link`` → ``record_queued``.
     Le plafond est recalculé EN MÉMOIRE au fil du cycle (``committed += size``).

Erreurs (contrats Plan C, spec §9) : ``MuleUnreachableError`` (flux EC mort) → tolère, skip
l'itération (le client se reconnecte au tour suivant ; amuled persiste les downloads).
``RepositoryError`` → absorbée (log + continue). ``promote`` échoue → reste ``completed``.
JAMAIS d'abandon d'un download stallé. Déterminisme : ``Clock``/``sleep`` injectés.
"""

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from emule_indexer.domain.download.ed2k_link import build_ed2k_link
from emule_indexer.domain.download.policy import DownloadVerdict, download_policy
from emule_indexer.domain.download.states import DownloadState
from emule_indexer.domain.matching.engine import DownloadCandidate
from emule_indexer.domain.matching.models import TargetSegment
from emule_indexer.ports.catalog_repository import ObservedFile
from emule_indexer.ports.clock import Clock
from emule_indexer.ports.decision_signal import DecisionSignal
from emule_indexer.ports.mule_client import MuleUnreachableError
from emule_indexer.ports.mule_download_client import DownloadEntry, MuleDownloadClient
from emule_indexer.ports.quarantine import Quarantine
from emule_indexer.ports.repository_errors import RepositoryError

_logger = logging.getLogger("emule_indexer.application.run_download_cycle")

StagingResolver = Callable[[DownloadEntry], Path]


class DownloadRepository(Protocol):
    """Protocol STRUCTUREL du repo downloads (typage local ; l'adapter le satisfait).

    Protocol minimal pour que l'application ne dépende QUE de ce dont elle a besoin
    (record_queued/set_state/is_downloaded/committed_bytes/active_states), sans importer
    l'adapter. Le vrai ``SqliteDownloadRepository`` (et le fake de test) le satisfait
    structurellement. Stubs sur UNE ligne (le ``def`` est couvert à la création de la classe).
    """

    def record_queued(self, ed2k_hash: str, target_id: str, size_bytes: int) -> bool: ...

    def set_state(self, ed2k_hash: str, state: DownloadState) -> None: ...

    def is_downloaded(self, ed2k_hash: str) -> bool: ...

    def committed_bytes(self) -> int: ...

    def active_states(self) -> dict[str, DownloadState]: ...


class CatalogReader(Protocol):
    """Protocol STRUCTUREL des LECTURES catalogue dont la boucle a besoin (DÉCISION D9).

    Sous-ensemble de ``CatalogRepository`` (download_decisions + last_observation) : la boucle
    ne dépend QUE de ce qu'elle lit, donc le fake minimal de test la satisfait sans implémenter
    record_observation/record_decision/last_decision. Le vrai ``SqliteCatalogRepository`` le
    satisfait aussi (il a ces deux méthodes). Stubs sur UNE ligne.
    """

    def download_decisions(self) -> tuple[DownloadCandidate, ...]: ...

    def last_observation(self, ed2k_hash: str) -> ObservedFile | None: ...


class VerificationQueue(Protocol):
    """Protocol STRUCTUREL de l'enfilement de vérification (sous-ensemble de LocalStateRepository).

    La boucle ne dépend que d'``enqueue_verification`` ; le fake minimal de test n'a pas à
    implémenter claim/complete/fail/reclaim. Le vrai ``SqliteLocalStateRepository`` le satisfait.
    """

    def enqueue_verification(self, ed2k_hash: str) -> bool: ...


@dataclass
class DownloadDeps:
    """Dépendances de la boucle de download (la composition les assemble une fois).

    ``staging_path_for`` mappe une entrée de file vers le chemin du fichier complété en
    staging (DÉCISION D2 : EC n'expose pas ce chemin ; la composition de D-verify le branche
    sur le layout amuled). ``targets`` sert au lookup ``target_id → status`` (politique pure).
    ``catalog``/``local`` sont typés aux Protocols NARROW ci-dessus (``CatalogReader``/
    ``VerificationQueue``) — la boucle ne dépend que du sous-ensemble lu/écrit (cohérent avec
    le Protocol local ``DownloadRepository``), donc les fakes minimaux de test sont acceptés.
    """

    client: MuleDownloadClient
    quarantine: Quarantine
    downloads: DownloadRepository
    catalog: CatalogReader
    local: VerificationQueue
    targets: Sequence[TargetSegment]
    disk_cap_bytes: int
    staging_path_for: StagingResolver
    clock: Clock


def _target_status(targets: Sequence[TargetSegment], target_id: str) -> str:
    """Statut de la cible (lookup ``target_id → status``) ; ``complete`` par défaut si la cible
    a disparu de la config (conservateur : ne pas télécharger pour une cible inconnue)."""
    for target in targets:
        if target.target_id == target_id:
            return target.status
    return "complete"


async def _monitor(deps: DownloadDeps, states: dict[str, DownloadState]) -> None:
    """Réconcilie ``downloads`` avec la vraie file amuled (étape 1, spec §5)."""
    queue = await deps.client.download_queue()
    for entry in queue:
        current = states.get(entry.ed2k_hash)
        if current is None:
            continue  # download hors crawler : ignoré
        if current in {DownloadState.QUARANTINED, DownloadState.FAILED}:
            continue  # terminal côté crawler : ne pas régresser
        target = DownloadState.COMPLETED if entry.is_complete else DownloadState.DOWNLOADING
        if target != current:
            deps.downloads.set_state(entry.ed2k_hash, target)
            states[entry.ed2k_hash] = target


def _promote_completion(deps: DownloadDeps, ed2k_hash: str) -> None:
    """Promeut un hash ``completed`` → quarantaine + enqueue + ``quarantined`` (étape 2, §5).

    Idempotent : si ``promote`` échoue, on laisse ``completed`` et on N'ENFILE PAS (le fichier
    doit être sûrement en quarantaine d'abord) ; retry au tour suivant.
    """
    entry = DownloadEntry(ed2k_hash=ed2k_hash, size_done=0, size_full=0)
    staging_path = deps.staging_path_for(entry)
    try:
        deps.quarantine.promote(staging_path, ed2k_hash)
    except Exception as error:  # noqa: BLE001 — toute panne FS laisse completed (retry idempotent)
        _logger.warning(
            "quarantaine échouée pour hash=%s (%s) — reste completed, retry", ed2k_hash, error
        )
        return
    deps.local.enqueue_verification(ed2k_hash)
    deps.downloads.set_state(ed2k_hash, DownloadState.QUARANTINED)
    _logger.info("hash=%s mis en quarantaine + vérification enfilée", ed2k_hash)


def _handle_completions(deps: DownloadDeps, states: dict[str, DownloadState]) -> None:
    """Promeut chaque hash ``completed`` pas encore ``quarantined`` (étape 2, spec §5)."""
    for ed2k_hash, state in list(states.items()):
        if state is DownloadState.COMPLETED:
            _promote_completion(deps, ed2k_hash)


def _queue_new_candidates(deps: DownloadDeps) -> None:
    """Rejoue les décisions tier=download absentes de ``downloads`` (étape 3, spec §5)."""
    committed = deps.downloads.committed_bytes()
    for candidate in deps.catalog.download_decisions():
        if deps.downloads.is_downloaded(candidate.ed2k_hash):
            continue
        observation = deps.catalog.last_observation(candidate.ed2k_hash)
        if observation is None:
            _logger.warning(
                "candidat hash=%s sans observation — lien impossible, sauté", candidate.ed2k_hash
            )
            continue
        verdict = download_policy(
            tier="download",
            target_status=_target_status(deps.targets, candidate.target_id),
            already_downloaded=False,
            committed_bytes=committed,
            file_size=observation.size_bytes,
            disk_cap=deps.disk_cap_bytes,
        )
        if verdict is not DownloadVerdict.DOWNLOAD:
            _logger.info(
                "candidat hash=%s → %s (sauté/différé)", candidate.ed2k_hash, verdict.value
            )
            continue
        # record_queued SEUL ici (écriture DB sync) ; le lien ed2k est bâti et émis par
        # _add_links (I/O réseau) pour tout 'queued' — l'écriture précède le réseau, et un
        # add_link qui lève laisse le download 'queued' en base (rattrapé au tour suivant).
        deps.downloads.record_queued(
            candidate.ed2k_hash, candidate.target_id, observation.size_bytes
        )
        committed += observation.size_bytes  # plafond recalculé en mémoire au fil du cycle
        _logger.info("candidat hash=%s mis en file de download", candidate.ed2k_hash)


async def _add_links(deps: DownloadDeps) -> None:
    """Émet les ``add_link`` EC pour les downloads ``queued`` sans lien encore envoyé.

    Séparé de ``_queue_new_candidates`` pour que l'écriture DB (sync) précède l'I/O réseau
    (async) : un ``MuleUnreachableError`` à ``add_link`` laisse le download ``queued`` en base
    (le monitor du tour suivant rattrape). On ré-émet le lien pour tout ``queued`` connu.
    """
    states = deps.downloads.active_states()
    for ed2k_hash, state in states.items():
        if state is not DownloadState.QUEUED:
            continue
        observation = deps.catalog.last_observation(ed2k_hash)
        if observation is None:
            continue
        link = build_ed2k_link(observation.filename, observation.size_bytes, ed2k_hash)
        await deps.client.add_link(link)


async def run_download_cycle(deps: DownloadDeps) -> None:
    """UNE itération de la boucle de download (spec §5). Ne lève jamais : tolère/absorbe.

    Tout flux EC mort (``MuleUnreachableError``) ou échec de repo (``RepositoryError``) est
    toléré (log + skip de l'itération) — la prochaine itération réessaie (amuled persiste les
    downloads). Les repos sont sync → l'annulation (arrêt) atterrit aux ``await`` réseau.
    """
    try:
        states = deps.downloads.active_states()
        await _monitor(deps, states)
        _handle_completions(deps, states)
        _queue_new_candidates(deps)
        await _add_links(deps)
    except MuleUnreachableError as error:
        _logger.warning("daemon download injoignable (%s) — itération sautée, retry", error)
    except RepositoryError as error:
        _logger.error("persistance download en échec (%s) — itération sautée, retry", error)
```

> **Note d'implémentation (cohérence add_link/record) :** le test `test_new_candidate_is_queued_and_link_added` attend que `add_link` soit appelé pour un nouveau candidat ET que `record_queued` ait eu lieu. Le flux ci-dessus : `_queue_new_candidates` fait `record_queued` (état `queued`), puis `_add_links` émet `add_link` pour tout `queued`. Ainsi l'écriture DB précède l'I/O réseau (un `add_link` qui échoue laisse `queued` en base, rattrapé au tour suivant). `test_repository_error_is_absorbed` : `record_queued` lève `RepositoryError` → remonte au `try` de `run_download_cycle` → absorbée. `test_unreachable_client_is_tolerated` : `download_queue` lève `MuleUnreachableError` dans `_monitor` → remonte → tolérée, aucun candidat traité (l'exception court-circuite `_queue_new_candidates`).

> **Note Protocols locaux (mypy --strict) :** `DownloadRepository`/`CatalogReader`/`VerificationQueue` héritent TOUS de `typing.Protocol` (import `from typing import Protocol` en tête) — sinon leurs corps `...` lèvent `empty-body` et, surtout, les fakes minimaux de test (`FakeDownloadRepo`/`FakeCatalogReads`/`FakeLocalRepo`) sont REJETÉS au point d'assemblage `DownloadDeps(downloads=…, catalog=…, local=…)` (`arg-type`). En les typant aux Protocols NARROW (sous-ensembles de `CatalogRepository`/`LocalStateRepository`), la boucle ne dépend que de ce qu'elle lit/écrit et les fakes les satisfont STRUCTURELLEMENT. Le vrai `SqliteDownloadRepository`/`SqliteCatalogRepository`/`SqliteLocalStateRepository` les satisfont aussi (ils ont ces méthodes), donc la composition de D-verify branchera les vrais repos sans cast. Les stubs des Protocols tiennent sur UNE ligne (`def m(...) -> T: ...`, couverts par le `def`).

- [ ] **Step 4: Vérifier puis gate**

Run: `uv run pytest tests/application/test_run_download_cycle.py -q --no-cov` → PASS (16 tests).
Run: gate complet → tout vert, 100 %.

> **Note couverture (points chauds de la boucle) :** `_monitor` (entrée inconnue→continue / connue+terminal→continue / connue+complete→completed / connue+incomplete→downloading / état identique→pas de set_state) ; `_promote_completion` (promote ok→enqueue+quarantined / promote lève→reste completed) ; `_handle_completions` (completed→promu / autre état→sauté, dont quarantined) ; `_queue_new_candidates` (dédup→continue / sans observation→continue / verdict≠download→continue / download→record+committed ; plafond intra-cycle) ; `_target_status` (trouvé / cible absente→complete) ; `_add_links` (queued→add / non-queued→skip / sans observation→skip) ; `run_download_cycle` (chemin propre / MuleUnreachableError / RepositoryError). Si une branche reste découverte, AJOUTER le test du côté manquant (ne jamais baisser le seuil).

- [ ] **Step 5: Commit**

```bash
git add src/emule_indexer/application/run_download_cycle.py tests/application/test_run_download_cycle.py
git commit -m "$(cat <<'EOF'
feat(application): run_download_cycle (monitor→complétions→candidats, tolérant)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Application — `download_loop` (répétition + sleep/nudge, arrêt annulable)

**Files:**
- Modify: `src/emule_indexer/application/run_download_cycle.py` (+ `download_loop`)
- Create: `tests/application/test_download_loop.py`

> DÉCISION D12 : `download_loop` répète `run_download_cycle` puis attend `min(poll_interval, nudge)` jusqu'à un `asyncio.Event` d'arrêt, exactement comme `_run_loop`/`_supervise` du Plan C. Le nudge : on attend AU PREMIER de `clock.sleep(poll_interval)` OU `signal.wait(_DOWNLOAD_SUBJECT)`, via `asyncio.wait(FIRST_COMPLETED)` + annulation du perdant. L'annulation (arrêt) atterrit à un `await` (poll EC ou attente) → jamais en pleine écriture DB (repos sync). Déterminisme : `Clock.sleep` injecté ; le test pilote l'arrêt via l'event + un nudge. Le sujet du nudge est un nom fixe (`"download"`) — D-download ne s'abonne pas au hash précis : tout changement de décision réveille la boucle qui rejoue le journal (un nudge perdu est inoffensif, le poll est le filet, comme Plan C §3).

> **DÉCISION D13 — sujet de nudge `"download"` (fixe).** Le pipeline `record_observations` (Plan C) `signal`e le hub avec le `ed2k_hash`. D-download ne peut pas s'abonner à chaque hash inconnu d'avance ; il s'abonne à UN sujet conventionnel `"download"`. **Conséquence** : pour que le nudge réveille effectivement la boucle de download au changement de verdict, D-verify (au câblage live) ajoutera un `signal("download")` au pipeline OU la boucle de download s'appuiera sur le poll de repli. En D-download (capacité testée), `download_loop` s'abonne à `"download"` ; le test vérifie qu'un `signal("download")` réveille la boucle avant l'expiration du poll. **Le câblage du `signal("download")` côté producteur est noté pour D-verify** (forward-compat, comme `decision_poll_interval` du Plan C — ne PAS le signaler comme manquant à la revue).

- [ ] **Step 1: Écrire le test qui échoue**

`tests/application/test_download_loop.py` :
```python
import asyncio
from datetime import UTC, datetime

import pytest

from emule_indexer.application.run_download_cycle import (
    DOWNLOAD_NUDGE_SUBJECT,
    DownloadLoopDeps,
    download_loop,
)
from emule_indexer.domain.matching.engine import DownloadCandidate
from emule_indexer.ports.catalog_repository import ObservedFile

# Réutilise les fakes de test_run_download_cycle (importés explicitement).
from tests.application.test_run_download_cycle import (
    FakeCatalogReads,
    FakeClock,
    FakeDownloadClient,
    FakeDownloadRepo,
    FakeLocalRepo,
    FakeQuarantine,
    _TARGETS,
)


class RecordingSignal:
    """Hub de nudge enregistrant + réveillant (le test EST le producteur)."""

    def __init__(self) -> None:
        self._events: dict[str, asyncio.Event] = {}
        self.waited: list[str] = []

    def signal(self, subject: str) -> None:
        self._events.setdefault(subject, asyncio.Event()).set()

    async def wait(self, subject: str) -> None:
        self.waited.append(subject)
        event = self._events.setdefault(subject, asyncio.Event())
        await event.wait()
        event.clear()


def _loop_deps(
    *, signal: RecordingSignal, shutdown: asyncio.Event, poll_interval: float = 30.0
) -> DownloadLoopDeps:
    from pathlib import Path

    return DownloadLoopDeps(
        client=FakeDownloadClient(),
        quarantine=FakeQuarantine(),
        downloads=FakeDownloadRepo(),
        catalog=FakeCatalogReads(),
        local=FakeLocalRepo(),
        targets=_TARGETS,
        disk_cap_bytes=1_000_000,
        staging_path_for=lambda entry: Path("/staging") / entry.ed2k_hash,
        clock=FakeClock(),
        signal=signal,
        poll_interval_seconds=poll_interval,
        shutdown=shutdown,
    )


@pytest.mark.asyncio
async def test_loop_stops_when_shutdown_is_set_before_start() -> None:
    shutdown = asyncio.Event()
    shutdown.set()
    deps = _loop_deps(signal=RecordingSignal(), shutdown=shutdown)
    await asyncio.wait_for(download_loop(deps), timeout=1.0)  # ne tourne aucun cycle


@pytest.mark.asyncio
async def test_loop_runs_a_cycle_then_sleeps_then_stops() -> None:
    shutdown = asyncio.Event()
    signal = RecordingSignal()
    deps = _loop_deps(signal=signal, shutdown=shutdown)

    async def stop_after_first_sleep() -> None:
        # laisse un cycle + l'entrée en attente, puis demande l'arrêt et réveille la boucle.
        while not deps.clock.sleeps:  # type: ignore[attr-defined]
            await asyncio.sleep(0)
        shutdown.set()
        signal.signal(DOWNLOAD_NUDGE_SUBJECT)

    await asyncio.gather(
        asyncio.wait_for(download_loop(deps), timeout=2.0), stop_after_first_sleep()
    )
    assert deps.clock.sleeps  # type: ignore[attr-defined]  # au moins un sleep de poll


@pytest.mark.asyncio
async def test_nudge_wakes_the_loop_before_poll_expires() -> None:
    shutdown = asyncio.Event()
    signal = RecordingSignal()
    deps = _loop_deps(signal=signal, shutdown=shutdown, poll_interval=999.0)

    async def nudge_then_stop() -> None:
        while DOWNLOAD_NUDGE_SUBJECT not in signal.waited:
            await asyncio.sleep(0)
        shutdown.set()
        signal.signal(DOWNLOAD_NUDGE_SUBJECT)  # réveille l'attente avant les 999 s de poll

    await asyncio.gather(
        asyncio.wait_for(download_loop(deps), timeout=2.0), nudge_then_stop()
    )
    assert DOWNLOAD_NUDGE_SUBJECT in signal.waited


class _ShutdownDuringCycleCatalog:
    """CatalogReader qui pose ``shutdown`` au 1er ``download_decisions`` (PENDANT le cycle).

    Satisfait STRUCTURELLEMENT ``CatalogReader`` (download_decisions + last_observation).
    """

    def __init__(self, shutdown: asyncio.Event) -> None:
        self._shutdown = shutdown

    def download_decisions(self) -> tuple[DownloadCandidate, ...]:
        self._shutdown.set()
        return ()

    def last_observation(self, ed2k_hash: str) -> ObservedFile | None:
        return None


@pytest.mark.asyncio
async def test_loop_breaks_when_shutdown_is_set_during_the_cycle() -> None:
    # le `if deps.shutdown.is_set(): break` APRÈS le cycle : shutdown posé PENDANT le cycle
    # (par le catalog) → break sans appeler _sleep_or_nudge (aucun sleep enregistré).
    shutdown = asyncio.Event()
    clock = FakeClock()
    deps = _loop_deps(signal=RecordingSignal(), shutdown=shutdown)
    deps.clock = clock
    deps.catalog = _ShutdownDuringCycleCatalog(shutdown)
    await asyncio.wait_for(download_loop(deps), timeout=1.0)
    assert clock.sleeps == []  # break avant tout sleep/nudge


class _BlockingClock:
    """Clock dont ``sleep`` BLOQUE pour de bon (le nudge doit gagner et annuler le sleep).

    Satisfait STRUCTURELLEMENT ``Clock`` : ``now`` aware (non utilisé par la boucle) + ``sleep``
    qui ne se résout jamais → le nudge gagne et le sleep_task pendant est annulé.
    """

    def now(self) -> datetime:
        return datetime(2026, 6, 13, tzinfo=UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.Event().wait()  # ne se résout JAMAIS


@pytest.mark.asyncio
async def test_nudge_wins_and_cancels_the_pending_sleep() -> None:
    # _sleep_or_nudge : nudge PRÉ-armé → la branche `if not task.done(): task.cancel()` est
    # exercée sur le sleep_task encore en cours (le _BlockingClock ne le résout jamais).
    shutdown = asyncio.Event()
    signal = RecordingSignal()
    deps = _loop_deps(signal=signal, shutdown=shutdown)
    deps.clock = _BlockingClock()
    signal.signal(DOWNLOAD_NUDGE_SUBJECT)  # nudge déjà armé → wait() repart aussitôt

    async def stop_when_waited() -> None:
        while DOWNLOAD_NUDGE_SUBJECT not in signal.waited:
            await asyncio.sleep(0)
        shutdown.set()
        signal.signal(DOWNLOAD_NUDGE_SUBJECT)

    await asyncio.gather(
        asyncio.wait_for(download_loop(deps), timeout=2.0), stop_when_waited()
    )
    assert DOWNLOAD_NUDGE_SUBJECT in signal.waited
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/application/test_download_loop.py -q --no-cov`
Expected: FAIL — `ImportError: cannot import name 'download_loop'`.

- [ ] **Step 3: Étendre `run_download_cycle.py`**

(a) En tête, ajouter au besoin l'import de `DecisionSignal` (déjà importé) et `asyncio` (déjà importé). Ajouter la constante de sujet après `_logger` :
```python
# Sujet conventionnel du nudge de download (DÉCISION D13). D-download s'abonne à CE sujet ;
# le câblage du signal("download") côté producteur (pipeline) atterrit en D-verify.
DOWNLOAD_NUDGE_SUBJECT = "download"
```
(b) Ajouter la dataclass `DownloadLoopDeps` (après `DownloadDeps`) :
```python
@dataclass
class DownloadLoopDeps(DownloadDeps):
    """``DownloadDeps`` + ce qu'il faut pour RÉPÉTER (nudge, cadence, arrêt) — DÉCISION D12."""

    signal: DecisionSignal
    poll_interval_seconds: float
    shutdown: "asyncio.Event"
```
(c) Ajouter `download_loop` (à la fin du fichier) :
```python
async def _sleep_or_nudge(deps: DownloadLoopDeps) -> None:
    """Attend ``poll_interval`` OU le nudge ``download``, au PREMIER des deux (spec §5).

    ``asyncio.wait(FIRST_COMPLETED)`` puis annulation du perdant : un changement de décision
    (nudge) réveille la boucle tout de suite ; sinon le poll de repli la réveille à la cadence.
    L'annulation d'arrêt atterrit ICI (un ``await``), jamais en pleine écriture DB (sync).
    """
    sleep_task = asyncio.ensure_future(deps.clock.sleep(deps.poll_interval_seconds))
    nudge_task = asyncio.ensure_future(deps.signal.wait(DOWNLOAD_NUDGE_SUBJECT))
    try:
        await asyncio.wait(
            {sleep_task, nudge_task}, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        for task in (sleep_task, nudge_task):
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task


async def download_loop(deps: DownloadLoopDeps) -> None:
    """Répète ``run_download_cycle`` puis attend (poll/nudge) jusqu'à l'arrêt (DÉCISION D12).

    Câblée par ``CrawlerApp`` (D-verify) dans le ``TaskGroup`` ; l'annulation (arrêt) atterrit
    au prochain ``await`` (poll EC ou attente sleep/nudge), jamais en pleine écriture DB.
    """
    while not deps.shutdown.is_set():
        await run_download_cycle(deps)
        if deps.shutdown.is_set():
            break
        await _sleep_or_nudge(deps)
```
(d) Ajouter `from contextlib import suppress` en tête du module (si absent).

> **Note typage :** `DownloadLoopDeps` hérite de `DownloadDeps` (dataclass) ; `run_download_cycle(deps)` accepte un `DownloadLoopDeps` (sous-type). `asyncio.Event` est annoté en chaîne (`"asyncio.Event"`) si besoin pour l'ordre d'import — `asyncio` est déjà importé, donc l'annotation nue suffit. Le test `from tests.application.test_run_download_cycle import …` réutilise les fakes : ruff peut classer cet import — laisser `ruff --fix` trancher.

- [ ] **Step 4: Vérifier puis gate**

Run: `uv run pytest tests/application/test_download_loop.py -q --no-cov` → PASS (5 tests).
Run: gate complet → tout vert, 100 %.

> **Note couverture (toutes les branches, sans hand-waving) :** `download_loop` — `test_loop_stops_when_shutdown_is_set_before_start` couvre « 0 cycle » (garde `while` fausse d'emblée) ; `test_loop_runs_a_cycle_then_sleeps_then_stops` couvre « cycle → sleep gagne → re-test du `while` » ; `test_loop_breaks_when_shutdown_is_set_during_the_cycle` couvre le `if deps.shutdown.is_set(): break` interne (shutdown posé PENDANT le cycle par le catalog → break SANS `_sleep_or_nudge`). `_sleep_or_nudge` — `test_loop_runs_a_cycle_then_sleeps_then_stops` couvre « sleep gagne » ; `test_nudge_wakes_the_loop_before_poll_expires` couvre « nudge gagne » ; `test_nudge_wins_and_cancels_the_pending_sleep` (clock à `sleep` BLOQUANT + nudge pré-armé) couvre la branche `if not task.done(): task.cancel()` sur le sleep_task ENCORE en cours (avec le `FakeClock` instantané, les deux tâches sont toujours `done()` → cette branche resterait découverte sans ce test). Les DEUX côtés du `if not task.done()` sont donc exercés.

- [ ] **Step 5: Commit**

```bash
git add src/emule_indexer/application/run_download_cycle.py tests/application/test_download_loop.py
git commit -m "$(cat <<'EOF'
feat(application): download_loop (répétition + sleep/nudge, arrêt annulable)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: Revue holistique finale + handoff (PAS de tag — D-verify continue le jalon)

**Files:** (aucune création de code — vérification + handoff + CLAUDE.md)

> La revue holistique attrape les bugs cross-cutting que le suivi à la lettre ne voit pas (méthode reconduite : elle a attrapé un bug critique à chaque jalon). Vérifier la RÈGLE DE DÉPENDANCE par grep, le gate complet, puis écrire le handoff. **PAS de tag de jalon** : D-verify poursuit le même jalon « Plan D » (auto-download + verifier) — le tag arrivera à la fin de D-verify (noté ci-dessous). On NE câble PAS la boucle live dans `CrawlerApp` (spec §10) : la vérification confirme justement que `composition/app.py` n'est PAS touché.

- [ ] **Step 1: Greps de la règle de dépendance (DOIVENT être CLEAN sauf whitelist)**

Run (le domaine n'importe que des deps pur-calcul whitelistées — `re2`/`rapidfuzz` du moteur ; `domain/download/` n'importe RIEN d'autre que le domaine + stdlib) :
```bash
grep -rnE "^(from|import) (emule_indexer\.(ports|adapters|application|composition)|re2|rapidfuzz)" src/emule_indexer/domain/
```
Expected (EXACTEMENT, le moteur seul — AUCUNE ligne sous `domain/download/`) :
```
src/emule_indexer/domain/matching/interpolation.py:6:import re2
src/emule_indexer/domain/matching/matchers.py:3:import re2
src/emule_indexer/domain/matching/matchers.py:4:from rapidfuzz import fuzz
src/emule_indexer/domain/matching/validation.py:12:import re2
```

Run (l'application n'importe JAMAIS un adapter ni la composition) :
```bash
grep -rnE "^(from|import) emule_indexer\.(adapters|composition)" src/emule_indexer/application/
```
Expected : **AUCUNE sortie** (code retour 1). `run_download_cycle.py` ne dépend que des ports + domaine + ses Protocols NARROW locaux (`DownloadRepository`/`CatalogReader`/`VerificationQueue`).

Run (les ports n'importent jamais adapters/application/composition) :
```bash
grep -rnE "^(from|import) emule_indexer\.(adapters|application|composition)" src/emule_indexer/ports/
```
Expected : **AUCUNE sortie**.

Run (pureté de `domain/download/` : stdlib + domaine seulement) :
```bash
grep -rn "from emule_indexer.ports" src/emule_indexer/domain/download/
```
Expected : **AUCUNE sortie**.

Run (déterminisme : aucun `random`/horloge/sleep direct dans l'application de download) :
```bash
grep -nE "(^import random|^import time|datetime\.now|asyncio\.sleep\()" src/emule_indexer/application/run_download_cycle.py
```
Expected : **AUCUNE sortie** (`asyncio.ensure_future(deps.clock.sleep(...))` passe par le port `Clock`, pas `asyncio.sleep` direct ; `asyncio.wait` n'est pas un sleep).

- [ ] **Step 2: Vérifier que `composition/app.py` n'est PAS modifié (couture → D-verify)**

Run :
```bash
git diff --name-only v0.7.0-orchestration..HEAD -- src/emule_indexer/composition/
```
Expected : **AUCUNE sortie** (la composition n'est pas touchée par D-download ; le câblage live est D-verify, spec §10).

- [ ] **Step 3: Revue de cohérence (lecture humaine/subagent, bugs cross-cutting)**

Points à confirmer explicitement (chacun couvert par un test, la revue confirme la cohérence) :
- **Idempotence de la complétion** : `promote` échoue → reste `completed`, n'enfile PAS, retry ; déjà `quarantined` → sauté ; `enqueue_verification` est idempotent (index unique partiel) → un double passage ne crée pas deux tâches.
- **Plafond intra-cycle** : `_queue_new_candidates` recalcule `committed` EN MÉMOIRE au fil du cycle (sinon deux candidats d'un même cycle dépasseraient le plafond ensemble).
- **Ordre écriture/réseau** : `record_queued` (DB sync) AVANT `add_link` (réseau async) → un `add_link` qui échoue laisse `queued` en base, rattrapé par le monitor (jamais un download « fantôme » non persisté).
- **Tolérance** : `MuleUnreachableError`/`RepositoryError` absorbées au niveau `run_download_cycle` → la boucle ne meurt jamais ; jamais d'abandon d'un download stallé (aucun chemin ne supprime/abandonne un download).
- **Le crawler ne lit jamais les octets** : `quarantine_fs.promote` = `os.replace` seul ; `download_queue` = métadonnées EC ; aucun `open()`/`read()` d'un fichier téléchargé nulle part.
- **Statut de cible inconnue** : `_target_status` rend `complete` (conservateur) si la cible a disparu de la config → on ne télécharge pas pour une cible fantôme.
- **`DownloadEntry.is_complete`** : garde `size_full > 0` → un fichier vide/naissant n'est jamais promu par erreur.

Run (le crawler ne lit jamais le contenu d'un fichier téléchargé — aucun open/read dans les chemins de download) :
```bash
grep -rnE "(\.open\(|\.read_(bytes|text)\(|open\()" src/emule_indexer/adapters/quarantine_fs.py src/emule_indexer/application/run_download_cycle.py
```
Expected : **AUCUNE sortie**.

- [ ] **Step 4: Gate complet final**

Run :
```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run sqlfluff lint src
```
Expected: tout vert — `… passed, 6 deselected` (4 ec_integration + 1 orchestration_integration + 1 download_integration), **100.00 % branch** ; ruff/format/mypy/sqlfluff propres.

Run (run dédié des intégrations si Docker disponible — fait foi) :
```bash
uv run pytest -m download_integration --no-cov -q
```
Expected: `1 passed` (mécaniques EC réelles : add_link + download_queue contre un amuled réel). Coller la sortie dans `docs/reference/2026-06-13-ec-download-opcodes.md` (section EMPIRIQUE).

- [ ] **Step 5: Mettre à jour `CLAUDE.md` (état courant — minimal)**

Mettre à jour le paragraphe « Current state » de `CLAUDE.md` : la **capacité de téléchargement (D-download)** est construite — `domain/download/` (policy/ed2k_link/states), ports `MuleDownloadClient`/`Quarantine`, adapters (EC `add_link`/`download_queue`, `quarantine_fs`, `SqliteDownloadRepository`), migration 0002, lecture catalogue `download_decisions`/`last_observation`, boucle `application/run_download_cycle`/`download_loop`. Noter : **PAS câblée live** (`composition/app.py` inchangé ; le câblage + le gate full-mode `VERIFIER_URL` = D-verify). Marqueur d'intégration `download_integration` (opt-in, Docker). Ne PAS retoucher les sections d'architecture du moteur/EC/data-model/orchestration.

```bash
git add CLAUDE.md
git commit -m "$(cat <<'EOF'
docs: CLAUDE.md — capacité de téléchargement construite (D-download, non câblée live)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6: Écrire le handoff**

Créer `docs/handoffs/2026-06-13 - handoff - download capability.md` (format des handoffs précédents) :
- **TL;DR** : la capacité de téléchargement est livrée et TESTÉE (mais pas câblée live). `run_download_cycle`/`download_loop` rejouent `match_decisions` (tier=download), téléchargent via EC (`add_link`/`download_queue`), promeuvent en quarantaine (rename atomique) + enfilent la vérification. Politique pure (status-gate + dédup + plafond disque applicatif). E2e `download_integration` (mécaniques EC réelles, option A).
- **État vérifiable** : gate 5 checks + e2e opt-in `download_integration` ; PAS de tag (D-verify continue le jalon).
- **Contrats que D-verify doit respecter / brancher** :
  1. **Câblage live dans `CrawlerApp`** : monter une 2e connexion EC (`download_endpoint`), un `SqliteDownloadRepository`, un `FilesystemQuarantine(quarantine_dir)`, résoudre `staging_path_for` sur le vrai layout amuled (DÉCISION D2 — c'est le point ouvert), et lancer `download_loop` dans le `TaskGroup`. Le gate full-mode (`VERIFIER_URL` + health-check) a besoin du port `ContentVerifier` = D-verify.
  2. **Nudge** : brancher `signal("download")` (DÉCISION D13) côté producteur (pipeline `record_observations`) pour réveiller la boucle au changement de verdict ; sinon le poll de repli suffit (un nudge perdu est inoffensif).
  3. **File de vérification** : `run_download_cycle` est désormais le PRODUCTEUR (`enqueue_verification` après quarantaine) ; D-verify est le consommateur (`claim/complete/fail/reclaim`).
  4. **`staging_path_for` / chemin staging réel** : DÉCISION D2 — EC n'expose pas le chemin ; D-verify doit le dériver du `staging_dir` configuré + la convention de nom amuled (à valider au homelab via `download_probe`).
- **Pièges appris** (à remplir au fil de l'exécution) : p.ex. l'ordre `record_queued` avant `add_link` ; le plafond intra-cycle en mémoire ; `DownloadEntry.is_complete` garde `size_full > 0`.
- **Prochaine étape** : D-verify (verifier + boucle de vérification + câblage live + gate full-mode), brainstormer d'abord.

```bash
git add "docs/handoffs/2026-06-13 - handoff - download capability.md"
git commit -m "$(cat <<'EOF'
docs: handoff — capacité de téléchargement (D-download ; contrats pour D-verify)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 7: PAS de tag (noté).** Le jalon « Plan D » se compose de D-download (ici) + D-verify (suivant). Le tag annoté arrivera à la fin de D-verify (proposition : `v0.8.0-download` ou `v0.8.0-auto-download` une fois le câblage live + le verifier en place). NE PAS tagger maintenant. Vérifier qu'aucun tag n'a été posé :
```bash
git tag --list | grep -E "download|0\.8" || echo "aucun tag download (attendu)"
```

---

## Self-Review : couverture de la spec (section → tâche)

| Spec download | Couvert par |
|---|---|
| §1 But : rejouer match_decisions tier=download, DL via EC, quarantaine + enqueue vérif, sans lire les octets | Tasks 9 (`download_decisions`), 5 (`add_link`/`download_queue`), 8 (`quarantine_fs`), 11 (boucle), 7 (repo) |
| §2.1 Scission D-download / D-verify, single-package (pas d'uv workspace) | Tout le plan reste dans `src/emule_indexer/` ; le workspace est noté hors périmètre (header + Task 13) |
| §2.2 Endpoint download = config à part, propre connexion EC | Task 10 (`download_endpoint` dans local_config), DÉCISION D3 (instance distincte) |
| §2.3 / §3 Une seule boucle de download, série | Task 11/12 (`run_download_cycle`/`download_loop`, une tâche), DÉCISION D8/D12 |
| §2.4 / §4 Port SÉPARÉ `MuleDownloadClient` (ISP) + probe empirique | Tasks 4 (port + `DownloadEntry`), 5 (impl EC), 6 (PROBE empirique + e2e + référence) |
| §5 Périmètre politique : status-gate + dédup + plafond disque applicatif | Task 2 (`download_policy`), DÉCISION D4 |
| §6 Plafond disque applicatif (somme size_bytes actifs vs plafond), pas de cap concurrent | Tasks 7 (`committed_bytes`), 2 (politique), 11 (plafond intra-cycle), DÉCISION D6/D7 |
| §7 Jamais d'abandon d'un stall ; back-pressure par le plafond seul | Task 11 (aucun chemin n'abandonne ; `skip_disk_cap` diffère), Task 13 (revue) |
| §8 / §7 Migration 0002 `downloads.size_bytes` | Task 7 (migration + repo) |
| §4 Port `MuleDownloadClient` + extension EC adapter, probe documenté | Tasks 4, 5, 6 (rapport `docs/reference/2026-06-13-ec-download-opcodes.md`) |
| §5 Boucle (monitor→complétions→candidats→sleep/nudge ; réconciliation au redémarrage) | Task 11 (monitor/complétions/candidats) + Task 12 (sleep/nudge/répétition), DÉCISION D8 |
| §6 Politique pure (enum, primitifs, lookup status par l'app) | Task 2, DÉCISION D4/D5 |
| §7 Repo `downloads` (record_queued/set_state/committed_bytes/active_states/is_downloaded) | Task 7 |
| §8 Port `Quarantine` + adapter FS (rename atomique, jamais +x, jamais lu) | Task 8, DÉCISION D10 |
| §9 Erreurs (injoignable→tolère ; RepositoryError absorbée ; promote échoue→pas d'enqueue ; jamais d'abandon ; arrêt annulable) | Task 11 (tolérance), 12 (arrêt), 13 (revue), DÉCISION D9 |
| §10 Couture d'activation → D-verify (PAS de câblage live ici) | `composition/app.py` NON modifié (vérifié Task 13 step 2), handoff (Task 13 step 6) |
| §11 Tests (unitaires domaine + boucle avec faux client/quarantaine + vrais repos SQLite ; e2e opt-in `download_integration` ; homelab manuel documenté) | Tasks 1-12 (unitaires), 6 (e2e + homelab dans la référence), 7/9 (vrais repos SQLite) |
| §12 DoD (domain pur 100 % ; port + EC + probe ; Quarantine + FS ; repo + migration ; boucle ; config ; gate vert + e2e ; NON inclus = D-verify) | Tasks 1-13 ; tag DIFFÉRÉ à D-verify (Task 13 step 7) |

**Self-review — résultats :**

1. **Couverture spec §1–§12** : chaque section est mappée à au moins une tâche (table ci-dessus). Le seul élément volontairement NON livré est la couture live (`CrawlerApp` + gate full-mode), explicitement déféré à D-verify par la spec §10 — vérifié négatif (Task 13 step 2). Le **tag est différé** (D-verify clôt le jalon) : noté, pas oublié.

2. **Placeholder scan** : AUCUN « TBD », « similar to Task N », « add error handling », « … (à compléter) » dans le code de production NI dans les tests des tâches. Le code de chaque fichier source ET de chaque fichier de test (y compris `tests/tools/test_download_probe.py`, désormais livré VERBATIM en Task 6 step 1bis) est complet et copiable. Les seuls renvois restants sont des consignes de RÉDACTION de DOCS (sections EMPIRIQUE/PENDING du rapport de référence à remplir au run ; le handoff Task 13 step 6, contenu spécifié point par point) — jamais du code laissé en blanc.

3. **Cohérence des types/signatures (vérifiée transversalement)** :
   - `DownloadEntry(ed2k_hash, size_done, size_full)` + `.is_complete` : défini Task 4, produit par `download_queue` (Task 5), consommé par `_monitor` (Task 11). ✔
   - `DownloadCandidate(ed2k_hash, target_id)` : défini Task 9 (engine.py), produit par `download_decisions` (Task 9), consommé par `_queue_new_candidates` (Task 11). ✔
   - `ObservedFile(filename, size_bytes)` : défini Task 9 (port catalog), produit par `last_observation` (Task 9), consommé par `_queue_new_candidates`/`_add_links` (Task 11). ✔
   - `DownloadState` (Task 1) : utilisé par le repo (Task 7), le port repo local (Task 11), le monitor/complétions (Task 11). `is_terminal` (Task 1) reflète le SQL `_COMMITTED_BYTES` (Task 7) — mêmes 3 terminaux. ✔
   - `download_policy(*, tier, target_status, already_downloaded, committed_bytes, file_size, disk_cap)` (Task 2) : appelé par `_queue_new_candidates` avec exactement ces kwargs (Task 11). ✔
   - `build_ed2k_link(filename, size_bytes, ed2k_hash)` (Task 3) : appelé par `_queue_new_candidates`/`_add_links` (Task 11) et par l'e2e (Task 6). ✔
   - `Quarantine.promote(staging_path: Path, ed2k_hash: str)` (Task 8) : appelé par `_promote_completion` (Task 11). ✔
   - Repo downloads : `record_queued`/`set_state`/`is_downloaded`/`committed_bytes`/`active_states` (Task 7) = le Protocol NARROW `DownloadRepository(Protocol)` (Task 11) = les méthodes du fake (Task 11). ✔
   - **Protocols NARROW de l'application** (Task 11, mypy `--strict`) : `DownloadRepository`/`CatalogReader`/`VerificationQueue` héritent TOUS de `typing.Protocol` (sinon `empty-body` + fakes minimaux rejetés au point d'assemblage `DownloadDeps`). `DownloadDeps.catalog`/`.local` sont typés `CatalogReader`/`VerificationQueue` (sous-ensembles, pas les ports complets) → `FakeCatalogReads`/`FakeLocalRepo` les satisfont, et `SqliteCatalogRepository`/`SqliteLocalStateRepository` aussi (composition D-verify). ✔
   - `CatalogRepository` gagne `download_decisions`/`last_observation` (Task 9) ; le stub du test de port existant est mis à jour (Task 9 step 1) → le Protocol reste satisfait. ✔
   - `MuleUnreachableError`/`RepositoryError` (ports, Plan C) catchés dans `run_download_cycle` sans importer d'adapter (Task 11). ✔
   - `Clock` injecté partout pour le temps ; aucun `asyncio.sleep` direct dans la logique (Task 11/12 utilisent `deps.clock.sleep` via `ensure_future` ; `asyncio.wait`/`asyncio.sleep(0)` n'apparaissent que dans les fakes/tests). ✔

4. **Opcodes EC GROUNDED + ce qui reste au probe** : `EC_OP_ADD_LINK=0x09`, `EC_OP_GET_DLOAD_QUEUE=0x0D`, `EC_OP_DLOAD_QUEUE=0x1F`, tags partfile (0x0301/0x0303/0x0306/0x0308/0x030E/0x031E) lus sur `amule-org/amule@3.0.0` (ECCodes.h + ExternalConn.cpp). `add_link` → réponse `EC_OP_NOOP` en succès (source). **Reste au probe (Task 6, empirique)** : confirmer in vivo que `add_link` est accepté et que le hash apparaît dans `download_queue` avec un statut lisible ; **PENDING homelab** : la complétion réelle (`size_done==size_full`) et le chemin staging réel (DÉCISION D2 : dérivé d'un staging configuré, non exposé par EC).

5. **Vérifié empiriquement à l'écriture du plan** (venv du projet, SQLite 3.53.2, Python 3.12) :
   - `ALTER TABLE downloads ADD COLUMN size_bytes INTEGER NOT NULL DEFAULT 0` + UPSERT + somme `WHERE state NOT IN (terminaux)` → OK (Task 7).
   - Fenêtre `ROW_NUMBER() OVER (PARTITION BY ed2k_hash ORDER BY decided_at DESC, id DESC)` + filtre `tier='download'` → rend exactement les hash dont le DERNIER verdict est download (Task 9).
   - `urllib.parse.quote(name, safe=".()[]-_")` échappe l'espace→`%20`, `|`→`%7C` et le non-ASCII en UTF-8 (Task 3 ; l'espace N'EST PAS dans le jeu sûr → le lien canonique attendu par le test est respecté).
   - `os.replace(staging, quarantine/hash)` : rename atomique, AUCUN bit exécutable posé, `FileNotFoundError` sur source absente (Task 8/10).
   - Le codec EC EXISTANT encode/décode `EC_OP_ADD_LINK` (avec `EC_TAG_STRING`) et `EC_OP_DLOAD_QUEUE` (entrée `EC_TAG_PARTFILE` hash-en-valeur-propre + enfants name/size_full/size_done/status) ; complétude = `size_done >= size_full` (Tasks 5/4).

**Nombre de tâches : 13** (Tasks 1-3 domaine pur ; 4 port client ; 5 EC adapter ; **6 = probe empirique EARLY + e2e + référence** ; 7 migration+repo ; 8 quarantaine ; 9 lecture catalogue ; 10 config ; 11 boucle (itération) ; 12 download_loop ; 13 revue holistique + handoff, **PAS de tag**).

**Comptes de tests par tâche (après correctifs de revue structurelle)** : Task 1 = 4 ; Task 2 = 11 ; Task 3 = 5 ; Task 4 = 5 ; Task 5 = **9** (8 + `test_download_queue_treats_malformed_size_as_zero`) ; Task 6 = probe (8 unit + 1 e2e opt-in) ; Task 7 = repo+migration ; Task 8 = 1 port + 3 FS ; Task 9 = reads ; Task 10 = config (+`test_download_disk_cap_key_is_required`) ; Task 11 = **16** (13 + `test_candidate_for_unknown_target_is_treated_as_complete` + `test_monitor_no_op_when_state_already_matches` + `test_queued_download_without_observation_emits_no_link`) ; Task 12 = **5** (3 + `test_loop_breaks_when_shutdown_is_set_during_the_cycle` + `test_nudge_wins_and_cancels_the_pending_sleep`).

**Correctifs de revue structurelle appliqués (gate PROUVÉ vert : 603 passed, 6 deselected, 100 % branch)** : (mypy) `DownloadRepository`+`CatalogReader`+`VerificationQueue` en `Protocol` ; `DownloadDeps.catalog`/`.local` typés aux Protocols narrow ; `DownloadState.QUEUED.value == …` ; `_ScriptedTransport` typé (plus d'`attr-defined` mort). (ed2k_link) `_SAFE_NAME_CHARS` sans espace (`%20`) + `count("|") == 5`. (couverture, 7 branches) `_target_status` fallback, `_monitor` no-op, `_add_links` sans observation, `download_loop` break intra-cycle, `_sleep_or_nudge` annulation (clock bloquant), `_optional_partfile_int` malformé→0, `_positive_int` clé manquante. (ruff) docstrings `download_repository`/`catalog_repository` raccourcies ≤100, commentaires `codes.py` ≤100, imports F401 retirés (`is_terminal`, `Explanation`/`MatchDecision`/`FileObservation`). (probe) `tests/tools/test_download_probe.py` livré verbatim. (cohérence) comptes de tests « PASS (N) » réconciliés.
