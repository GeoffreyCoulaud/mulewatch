# Crawler MVP — Plan 4 : Modèle de données (`v0.6.0-data-model`) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construire la **mémoire** du crawler : les deux bases SQLite du spec MVP §11/§12 (`catalog.db` append-only/adressé par contenu/prêt à fusionner, `local.db` opérationnel/jamais fusionné), avec le **schéma COMPLET des dix tables** migré et versionné (`PRAGMA user_version`, scripts `.sql` embarqués dans le paquet et lus via `importlib.resources`), l'**append-only IMPOSÉ PAR LA BASE** (triggers `BEFORE UPDATE`/`BEFORE DELETE` → `RAISE(ABORT)` sur chaque table du catalogue), les **ports** `CatalogRepository`/`LocalStateRepository` (+ DTO gelé `ClaimedTask`) et les **repositories partiels** qui ont un producteur aujourd'hui : `SqliteCatalogRepository` (`record_observation`, `record_decision` — `FileObservation` et `MatchDecision` deviennent des lignes durables, stampées `observed_at`/`decided_at`/`node_id` par l'adapter) et `SqliteLocalStateRepository` (`node_id` + la **file de tâches complète** de §12 : enqueue idempotent, claim atomique FIFO sous `BEGIN IMMEDIATE` + `RETURNING`, lease/reclaim, retries bornés → dead-letter). **sqlfluff** rejoint le gate (pre-push + CI + CLAUDE.md : 4 → 5 checks). Spec : `docs/superpowers/specs/2026-06-11-data-model-design.md`. Le schéma intègre le verdict du rapport `docs/reference/2026-06-11-ec-field-richness.md` (EC n'expose AUCUNE métadonnée média → colonnes `media_*` nullables, `raw_meta` indispensable) et les deux déviations ASSUMÉES vs §11 MVP (spec §5) : `file_observations.size_bytes` (la taille OBSERVÉE est une donnée d'observation) et `bitrate` → `bitrate_kbps`.

**Architecture:** Clean/Hexagonal, inchangée. `ports/catalog_repository.py` et `ports/local_state_repository.py` sont des **Protocol SYNCHRONES** satisfaits structurellement (même typage structurel que `MuleClient` — l'adapter ne les importe pas, sauf le DTO `ClaimedTask` défini à côté de son port) ; ils n'importent QUE le domaine. Tout le SQL vit dans `adapters/persistence_sqlite/` : `errors.py` (hiérarchie `PersistenceError` → `MigrationError` + enveloppe unique `wrap_sqlite_errors`), `connection.py` (connexion `autocommit=True`, PRAGMA WAL/FK, runner de migrations, horloge partagée `Clock`/`utc_now`/`utc_iso`), `catalog_repository.py`, `local_state_repository.py`, `migrations/{catalog,local}/0001_initial.sql`. **Le domaine ne change pas d'une ligne** : l'adapter stamppe ce que le domaine ignore (horloge injectable au constructeur, `datetime.now(UTC)` par défaut ; `node_id` fourni/persisté). L'adapter **signale, il ne décide pas** : toute `sqlite3.Error` sort enveloppée, jamais nue ; pas de retry, pas de fallback. Repositories **synchrones** (spec §3) : une écriture locale est sub-milliseconde ; le plan C enveloppera dans `asyncio.to_thread` s'il veut s'isoler, sans toucher cette couche.

**Tech Stack:** Python ≥ 3.12, `sqlite3` **stdlib** (pas d'ORM, pas d'`aiosqlite`, SQL à la main), `uv`, `ruff` (line-length 100), `mypy --strict` (src + tests), `pytest` + `pytest-cov` (gate **100 % branch**). **Nouvelle dépendance dev unique : `sqlfluff`** (dialecte `sqlite`, config `[tool.sqlfluff.core]` dans `pyproject.toml`) — `uv run sqlfluff lint src` devient le **5e check du gate** (pre-push + CI + CLAUDE.md). Tests sur **SQLite réel en fichiers** (`tmp_path`) — JAMAIS `:memory:` (ne porte pas WAL, vérifié) ; horloge fausse avançable (zéro sleep, zéro flakiness) ; pannes injectées par **triggers de TEST** (déterministes, sans mock).

> **Référence spec :** `docs/superpowers/specs/2026-06-11-data-model-design.md` — §2 (périmètre), §3 (décisions verrouillées), §4 (architecture + ports), §5 (schémas + déviations), §6 (mécanique de la file), §7 (erreurs), §8 (stratégie de tests), §9 (livrables), §10 (questions laissées à CE plan : DDL exact, config sqlfluff, détail du runner, forme du claim). Spec MVP : §11 (deux bases), §12 (file de tâches), §14 (fail-fast), §16 (TDD/DB sur SQLite réel).

> **HORS PÉRIMÈTRE (spec §2 — RIEN de tout ceci n'apparaît ici) :** l'outil de fusion multi-chercheurs (§17 MVP ; le schéma est PRÊT à fusionner, la mécanique attendra — dédup à la fusion = égalité ligne entière hors `id` local, documenté, mécanisé plus tard) ; les repos de `sources`/`source_observations`/`downloads`/`file_verifications` (plan D) et la structuration de `scheduler_state` (plan C) — leurs TABLES existent dès maintenant (clés de fusion figées une fois pour toutes), leurs repositories non ; les use-cases applicatifs (plan C) ; compaction/rétention (différé).

---

## Vérifications empiriques (faites PENDANT l'écriture du plan — ne PAS re-découvrir)

Tout le SQL et tous les choix d'API ci-dessous ont été **exécutés pour de vrai** dans le venv du projet (Python **3.12.9**, `sqlite3.sqlite_version == 3.47.1` ≥ 3.35 → `RETURNING` disponible), puis le plan ENTIER a été assemblé dans un bac à sable et le gate complet exécuté : **346 passed, 100.00 % branch, ruff + format + mypy + sqlfluff verts** — y compris l'état intermédiaire après CHAQUE tâche (chaque commit du plan se fait sur un arbre au gate vert, vérifié état par état).

1. **`sqlite3.connect(path, autocommit=True)`** (Python 3.12) : accepté ; autocommit RÉEL, `BEGIN`/`COMMIT`/`ROLLBACK` explicites fonctionnent via `execute()`.
2. **PRAGMA** : `journal_mode=WAL` répond `('wal',)` sur fichier, **`('memory',)` sur `:memory:`** (d'où le refus net, spec §8) ; `foreign_keys=ON` actif par connexion — violation = `sqlite3.IntegrityError: FOREIGN KEY constraint failed`.
3. **Runner** : `executescript("BEGIN;\n<script>\nPRAGMA user_version = N;\nCOMMIT;")` fonctionne en autocommit ; un échec à mi-script laisse `connection.in_transaction == True` ; après `ROLLBACK`, le DDL **et** `user_version` sont rendus (le pragma est transactionnel). Les scripts ne doivent donc contenir AUCUN `BEGIN`/`COMMIT` transactionnel.
4. **Triggers append-only** : `CREATE TRIGGER … BEFORE UPDATE ON … BEGIN SELECT RAISE(ABORT, 'msg'); END;` — la violation surface en **`sqlite3.IntegrityError`** portant exactement `msg` (classe RÉELLE observée ; les tests s'y accrochent).
5. **Enqueue idempotent** : `INSERT … ON CONFLICT (ed2k_hash) WHERE status IN ('pending', 'in_progress') DO NOTHING` — la **cible de conflit explicite avec la clause WHERE de l'index partiel est ACCEPTÉE** par SQLite 3.47.1 ; `cursor.rowcount` vaut 1 (inséré) / 0 (absorbé).
6. **Claim** : `UPDATE … WHERE id = (SELECT id … ORDER BY enqueued_at, id LIMIT 1) RETURNING id, ed2k_hash, attempts` sous `BEGIN IMMEDIATE` : `fetchone()` rend la ligne ou `None` (file vide) ; deux connexions séquentielles prennent des tâches DISTINCTES.
7. **sqlfluff** : `uv add --dev "sqlfluff>=3.0"` résout **4.2.2** ; le dialecte `sqlite` **parse les triggers et l'index partiel sans erreur**. Seules violations sur notre DDL : `LT05` (lignes > 80) et `RF04` (`key`/`value` sont des mots-clés) → config `max_line_length = 100` (parité ruff) + `ignore_words = "key,value"` (noms IMPOSÉS par la spec §5). `sqlfluff lint src` **sort 0 même sans aucun fichier `.sql`** (Task 1 sûre).
8. **`importlib.resources`** : `files("emule_indexer.adapters.persistence_sqlite") / "migrations" / kind` se traverse **SANS `__init__.py` dans les sous-répertoires** de migrations (install editable, layout src) ; le wheel hatchling **embarque les `.sql`** (vérifié par `uv build` + inspection de l'archive). `pathlib.Path` satisfait le Protocol `Traversable` (typeshed) → `_load_scripts` se teste sur `tmp_path`.

---

## File Structure & décisions verrouillées

```
src/emule_indexer/
├── ports/catalog_repository.py        # Create : CatalogRepository (Protocol sync)
├── ports/local_state_repository.py    # Create : LocalStateRepository (Protocol sync)
│                                      #          + ClaimedTask (DTO gelé)
└── adapters/persistence_sqlite/
    ├── __init__.py                    # Create (docstring du sous-paquet)
    ├── errors.py                      # Create : PersistenceError → MigrationError
    │                                  #          + wrap_sqlite_errors (enveloppe unique)
    ├── connection.py                  # Create : open_catalog/open_local (PRAGMA, runner
    │                                  #          user_version) + Clock/utc_now/utc_iso
    ├── catalog_repository.py          # Create : SqliteCatalogRepository
    ├── local_state_repository.py      # Create : SqliteLocalStateRepository (file incluse)
    └── migrations/
        ├── catalog/0001_initial.sql   # Create : 6 tables + 5 index + 12 triggers
        └── local/0001_initial.sql     # Create : 4 tables + index UNIQUE partiel

tests/
├── ports/{test_catalog_repository.py,test_local_state_repository.py}     # Create
└── adapters/persistence_sqlite/{__init__.py,test_connection.py,          # Create
    test_append_only.py,test_catalog_repository.py,
    test_local_state_repository.py}

pyproject.toml          # Modify (Task 1 : dép. sqlfluff + config [tool.sqlfluff.*])
.githooks/pre-push      # Modify (Task 2 : 5e check)
.github/workflows/ci.yml# Modify (Task 2 : 5e check)
CLAUDE.md               # Modify (Task 2 : bloc gate « same four » → « same five », minimal)
```

> **DÉCISION 1 — `autocommit=True` + transactions EXPLICITES partout.**
> La spec (§3) impose des PRAGMA par connexion et des transactions précises (`record_observation` = UNE transaction ; claim sous `BEGIN IMMEDIATE`) mais ne fixe pas le mode de gestion. Le mode legacy d'`sqlite3` (implicit BEGIN différé, `isolation_level`) est un nid à surprises ; le paramètre `autocommit=True` (Python ≥ 3.12) donne l'autocommit RÉEL de SQLite : chaque transaction du code est ÉCRITE (`BEGIN`/`BEGIN IMMEDIATE` … `COMMIT`/`ROLLBACK`), rien d'implicite. Échec en cours de transaction → `ROLLBACK` **best-effort** (`contextlib.suppress(sqlite3.Error)`) puis ré-élévation de l'erreur D'ORIGINE — même raisonnement que le `close()` best-effort du transport EC (handoff §4) : le nettoyage ne doit jamais masquer la panne réelle.

> **DÉCISION 2 — Runner : scripts SANS transaction, enveloppe `BEGIN;\n<script>\nPRAGMA user_version = N;\nCOMMIT;`, découverte testable.**
> Chaque script `NNNN_*.sql` est appliqué dans SA transaction (spec §3) : c'est le RUNNER qui pose `BEGIN`/`COMMIT` autour (les `BEGIN…END` des triggers sont du DDL, pas du contrôle transactionnel — aucun conflit), et `PRAGMA user_version = N` est posé DANS la transaction du script N (transactionnel, vérification empirique n° 3 : un rollback rend DDL **et** version). PRAGMA n'accepte pas de paramètre lié : l'interpolation f-string est sûre car `N` sort d'`int()`. Découverte : `_load_scripts(directory: Traversable)` — l'annuaire est un PARAMÈTRE (testable sur `tmp_path`, vérification n° 8) ; tri lexicographique ; un fichier non-`.sql` est ignoré ; un `.sql` au préfixe non numérique = bug d'empaquetage → `MigrationError` (fail-fast, pas de migration silencieusement sautée). `_apply_migrations(connection, scripts)` est elle aussi directement testable (rollback d'un script forgé qui échoue). `open_catalog`/`open_local` ne sont que la composition publique des deux.

> **DÉCISION 3 — WAL EXIGÉ à l'ouverture : `:memory:` est refusé net.**
> La spec (§3) impose `journal_mode=WAL` ; or `:memory:` ne le porte pas (il répond `memory`, vérification n° 2). Plutôt qu'un WAL silencieusement absent (propriété de durabilité perdue sans bruit), `_configure` vérifie la RÉPONSE du pragma et lève `PersistenceError` si ce n'est pas `wal` — fail-fast §14 MVP, et c'est précisément pourquoi les tests utilisent des fichiers réels (spec §8).

> **DÉCISION 4 — Horloge partagée dans `connection.py` : `type Clock`, `utc_now`, `utc_iso` à microsecondes FIXES.**
> Les deux repositories stamppent des timestamps ISO-8601 UTC TEXT (spec §3) et le claim FIFO trie sur `enqueued_at` : pour que l'ordre LEXICOGRAPHIQUE soit l'ordre CHRONOLOGIQUE, le format doit être à largeur fixe — `isoformat(timespec="microseconds")` écrit TOUJOURS les microsecondes (`2026-06-11T12:00:00.000000+00:00`). `utc_iso` normalise par `astimezone(UTC)` : une horloge injectée en `+02:00` est stockée en UTC, jamais telle quelle (testé). Le contrat de `Clock` : retourner un datetime AWARE. Ces trois symboles vivent dans `connection.py` (le module d'infrastructure partagé de l'adapter) plutôt que dans un module supplémentaire — la liste de fichiers de la spec §4 est fermée, on ne l'étend pas pour 12 lignes.

> **DÉCISION 5 — `wrap_sqlite_errors` : l'enveloppe UNIQUE, dans `errors.py`.**
> « Toute `sqlite3.Error` inattendue est enveloppée, jamais nue hors de l'adapter » (spec §7) : un seul context manager `@contextmanager` fait foi, partagé par la connexion et les deux repositories (cause chaînée `from error` conservée). `MigrationError` levée À L'INTÉRIEUR d'un bloc enveloppé n'est PAS re-enveloppée (ce n'est pas une `sqlite3.Error`) — le runner garde son diagnostic précis. Un trigger append-only qui se déclenche suit le chemin normal de l'enveloppe → `PersistenceError` (spec §7 : bug du code appelant, pas un cas métier).

> **DÉCISION 6 — `SqliteCatalogRepository(connection, node_id, *, clock=utc_now)` ; `aich_hash` = NULL ; `BEGIN` simple.**
> Le `node_id` est un PARAMÈTRE du constructeur : le plan C le lira du `LocalStateRepository` et le passera ici — le repository catalogue n'a aucune raison de connaître `local.db`. `record_observation` insère `aich_hash = NULL` : EC n'expose pas l'AICH sur les résultats de recherche (rapport richesse) ; la colonne existe pour la vérification (plan D). Sa transaction utilise `BEGIN` simple — la spec ne réserve `BEGIN IMMEDIATE` qu'au claim (défense en profondeur) ; writer unique garanti par le déploiement. `record_decision` est UN INSERT seul (autocommit = atomique) ; un hash jamais observé viole la FK → `PersistenceError` (l'adapter signale). Seules les **3 colonnes** de `MatchDecision` sont persistées ; `explanation` n'est JAMAIS une colonne (docstring du moteur, vérifié par un test sur `PRAGMA table_info`).

> **DÉCISION 7 — `node_id()` : `uuid.uuid4()` NON injecté, `created_at` stocké à côté, création transactionnelle.**
> La spec (§3) rend l'horloge injectable, pas l'UUID ; le test vérifie la VRAIE propriété : la valeur se parse en UUID (`uuid.UUID(...)`) et reste STABLE (second appel, nouvelle instance, même base). La création insère `('node_id', …), ('created_at', …)` en UNE transaction `BEGIN IMMEDIATE` (le « , … » de la spec §5 sur `node_runtime` : `created_at` documente la naissance du nœud). Le chemin d'échec est testé en pré-insérant `created_at` (violation de PK → rollback COMPLET, le `node_id` orphelin n'existe pas).

> **DÉCISION 8 — `complete`/`fail` EXIGENT une tâche `in_progress` ; défauts : lease 15 min, `max_attempts` 3.**
> La spec (§6) ne dit pas ce que fait `complete_verification(42)` sur un id inconnu ou déjà `done`. Un worker qui complète une tâche qu'il ne tient pas est un BUG (lease expirée et réclamée, double-complétion) : `rowcount != 1` → `PersistenceError` — silencieusement l'ignorer masquerait une vraie course. `fail_verification` décide `pending` vs `dead_letter` en SQL (`CASE WHEN attempts >= :max_attempts`) dans le MÊME UPDATE filtré `status = 'in_progress'` : atomique, pas de fenêtre lecture/écriture. Défauts du constructeur (spec : « configurable au constructeur », valeurs non fixées) : `lease_duration=timedelta(minutes=15)` (une vérification ffprobe/clamav se compte en secondes ; 15 min absorbe un gros fichier sur disque lent), `max_attempts=3` (au 3e claim qui échoue → poison probable, §12).

> **DÉCISION 9 — Enqueue : cible de conflit EXPLICITE (`ON CONFLICT (ed2k_hash) WHERE …`).**
> Vérification n° 5 : SQLite accepte la cible partielle explicite. On la préfère au `ON CONFLICT DO NOTHING` nu : elle n'absorbe QUE le conflit prévu (une tâche active pour ce hash) — toute autre violation d'intégrité future continuerait d'éclater au lieu d'être avalée. Retour : `cursor.rowcount == 1` (créée) / `0` (absorbée → `False`).

> **DÉCISION 10 — sqlfluff : dialecte `sqlite`, `max_line_length = 100`, `RF04 ignore_words = "key,value"` ; gate = `uv run sqlfluff lint src`.**
> Config vérifiée n° 7. `max_line_length = 100` aligne le SQL sur la règle ruff du projet. `key`/`value` sont les noms de colonnes IMPOSÉS par la spec §5 (`node_runtime`, `scheduler_state`) : on désactive RF04 pour CES DEUX MOTS seulement (pas la règle entière — un futur `order` ou `group` sera encore attrapé). La cible `src` (et non un chemin profond) lintera aussi tout `.sql` futur du paquet.

> **DÉCISION 11 — Pannes injectées par triggers de TEST (jamais de mock).**
> Pour couvrir les branches `except sqlite3.Error` des transactions multi-statements (atomicité de `record_observation`, rollback du claim), les tests créent un trigger `RAISE(ABORT, 'panne injectée')` conditionnel SUR LA VRAIE BASE — panne déterministe, au point exact voulu, sans simulacre d'API. C'est le pendant DB du faux serveur EC du plan 03.

> **DÉCISION 12 — Ordonnancement : SQL d'abord (Task 2), runner ensuite (Task 4) ; chaque commit au gate vert (VÉRIFIÉ état par état).**
> Les `.sql` précèdent leur consommateur : sqlfluff est leur premier filet (lint au commit de la Task 2), et leur smoke-test d'exécution réelle est DANS la Task 2 ; les tests comportementaux (création des dix tables, triggers, index partiel) arrivent avec le runner (Task 4-5) AVANT tout repository. Les repositories sont découpés méthode par méthode (Tasks 6-11) de sorte que CHAQUE état intermédiaire passe le gate 100 % branch — l'assemblage du plan a été rejoué tâche par tâche dans le bac à sable : 288 → 302 → 315 → 320 → 324 → 327 → 335 → 342 → 346 tests, 100.00 % à chaque palier.

> **Note couverture (gate 100 % branch — points chauds par tâche) :** chaque conditionnel exercé des deux côtés. En particulier : `wrap_sqlite_errors` (chemin nominal + enveloppe) ; `_configure` (wal/non-wal) ; `_open` (échec d'ouverture, échec de config/migration → close, succès) ; `_load_scripts` (non-`.sql` ignoré, préfixe non numérique, nominal trié) ; `_apply_migrations` (scripts vides, base plus récente, script déjà appliqué, échec→rollback, succès) ; `record_observation` (succès, panne injectée→rollback) ; `record_decision` (succès, FK violée) ; `node_id` (existant, créé, échec d'insertion→rollback) ; `enqueue` (rowcount 1/0) ; `claim` (ligne/None, panne injectée) ; `complete`/`fail` (rowcount 1/≠1, CASE pending/dead_letter) ; `reclaim` (>0 / 0). Stubs des Protocol : **une ligne** (`def x(...) -> T: ...`, le `def` s'exécute à la création de la classe). `__init__.py` à docstring seule = 0 statement (sans effet sur le gate).

> **Note typage (`mypy --strict` sur src ET tests) :** toutes les fonctions de test `-> None`, paramètres typés. AUCUN nouvel override mypy (sqlfluff n'est jamais importé). `pathlib.Path` passe pour `Traversable` (Protocol typeshed, vérification n° 8). Les `row = cursor.fetchone()` sont `Any` → les affectations vers `ClaimedTask`/`str` passent sans cast ; `str(row[0])` matérialise le `str` de `node_id`. Aucun `cast`, un seul motif `# type: ignore[misc]` (mutation d'un dataclass gelé dans les tests, convention du dépôt). Les tests du runner importent `_load_scripts`/`_apply_migrations` (privés) : assumé — c'est l'unité sous test ; la surface publique est couverte par les tests d'`open_catalog`/`open_local`.

---

## Task 1: Outillage — sqlfluff (dépendance dev + config)

**Files:**
- Modify: `pyproject.toml`

> Le 5e check s'installe AVANT le premier `.sql` (Task 2) ; `sqlfluff lint src` sort 0 sans fichier SQL (vérification n° 7), donc le gate reste cohérent dès maintenant. Le câblage pre-push/CI/CLAUDE.md arrive en Task 2 AVEC les fichiers lintés.

- [ ] **Step 1: Ajouter la dépendance dev**

```bash
uv add --dev "sqlfluff>=3.0"
```

Attendu : résolution de sqlfluff **4.2.2** (version observée à l'écriture du plan) dans `[dependency-groups].dev` + `uv.lock`.

- [ ] **Step 2: Ajouter la config sqlfluff à la FIN de `pyproject.toml`**

```toml

[tool.sqlfluff.core]
dialect = "sqlite"
max_line_length = 100

[tool.sqlfluff.rules.references.keywords]
ignore_words = "key,value"
```

(Justification des deux réglages : DÉCISION 10. La syntaxe `[tool.sqlfluff.rules.<groupe>.<règle>]` est la forme pyproject documentée par sqlfluff — vérifiée sur la doc courante.)

- [ ] **Step 3: Vérifier**

Run: `uv run sqlfluff lint src`
Expected: `All Finished!`, code retour 0 (aucun `.sql` encore — attendu, ne rien « corriger »).

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert — `285 passed, 4 deselected`, coverage 100 % (la config TOML n'introduit aucun code).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: outillage sqlfluff (dialecte sqlite, parité ligne 100)"
```

---

## Task 2: Migrations — les deux schémas v1 + gate à 5 checks

**Files:**
- Create: `src/emule_indexer/adapters/persistence_sqlite/__init__.py`
- Create: `src/emule_indexer/adapters/persistence_sqlite/migrations/catalog/0001_initial.sql`
- Create: `src/emule_indexer/adapters/persistence_sqlite/migrations/local/0001_initial.sql`
- Modify: `.githooks/pre-push`, `.github/workflows/ci.yml`, `CLAUDE.md`

> Les DIX tables des deux bases (spec §5), figées une fois pour toutes : les plans C/D ajouteront leurs repos sur des tables déjà en place, sans migration. Les sous-répertoires de `migrations/` ne portent PAS de `__init__.py` (vérification n° 8 : inutile pour `importlib.resources`, et un répertoire de données n'est pas un paquet). Ces fichiers n'ont pas encore de consommateur Python : leurs « tests » de cette tâche sont sqlfluff + une exécution RÉELLE smoke (les tests comportementaux arrivent Tasks 4-5, avant tout repository).

- [ ] **Step 1: Créer `src/emule_indexer/adapters/persistence_sqlite/__init__.py`**

```python
"""Adapter persistence SQLite : les deux bases du spec MVP §11/§12 (spec data-model §4).

``catalog.db`` (append-only, adressé par contenu, prêt à fusionner) et ``local.db``
(opérationnel, jamais fusionné). ``sqlite3`` stdlib, repositories SYNCHRONES, SQL à la
main (spec §3) ; migrations ``.sql`` embarquées dans ``migrations/`` (lues via
``importlib.resources``), lintées par sqlfluff au gate.
"""
```

- [ ] **Step 2: Créer `migrations/catalog/0001_initial.sql`**

`src/emule_indexer/adapters/persistence_sqlite/migrations/catalog/0001_initial.sql` :
```sql
-- catalog.db — migration 0001 : schéma complet (spec data-model §5 ; spec MVP §11).
-- Append-only IMPOSÉ PAR LA BASE : triggers BEFORE UPDATE / BEFORE DELETE sur CHAQUE
-- table → RAISE(ABORT). Clé contenu = hash eD2k (hex minuscule 32, canon v0.5.0).
-- Timestamps ISO-8601 UTC en TEXT ; raw_meta = JSON liste de paires (ordre + doublons).

CREATE TABLE files (
    ed2k_hash TEXT PRIMARY KEY,
    size_bytes INTEGER NOT NULL,
    aich_hash TEXT
);

CREATE TABLE file_observations (
    id INTEGER PRIMARY KEY,
    ed2k_hash TEXT NOT NULL REFERENCES files (ed2k_hash),
    filename TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    source_count INTEGER NOT NULL,
    complete_source_count INTEGER NOT NULL,
    media_length_sec INTEGER,
    bitrate_kbps INTEGER,
    codec TEXT,
    file_type TEXT,
    raw_meta TEXT NOT NULL,
    keyword TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    node_id TEXT NOT NULL
);

CREATE INDEX idx_file_observations_ed2k_hash ON file_observations (ed2k_hash);
CREATE INDEX idx_file_observations_observed_at ON file_observations (observed_at);

CREATE TABLE sources (
    user_hash TEXT PRIMARY KEY,
    client_name TEXT,
    client_version TEXT
);

CREATE TABLE source_observations (
    id INTEGER PRIMARY KEY,
    user_hash TEXT REFERENCES sources (user_hash),
    ed2k_hash TEXT NOT NULL REFERENCES files (ed2k_hash),
    ip TEXT,
    port INTEGER,
    nickname TEXT,
    client_name TEXT,
    client_version TEXT,
    country TEXT,
    id_type TEXT,
    has_complete_file INTEGER,
    origin TEXT,
    raw_meta TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    node_id TEXT NOT NULL
);

CREATE INDEX idx_source_observations_ed2k_hash ON source_observations (ed2k_hash);
CREATE INDEX idx_source_observations_user_hash ON source_observations (user_hash);

CREATE TABLE match_decisions (
    id INTEGER PRIMARY KEY,
    ed2k_hash TEXT NOT NULL REFERENCES files (ed2k_hash),
    target_id TEXT NOT NULL,
    rule_name TEXT NOT NULL,
    tier TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    node_id TEXT NOT NULL
);

CREATE INDEX idx_match_decisions_ed2k_hash ON match_decisions (ed2k_hash);

CREATE TABLE file_verifications (
    id INTEGER PRIMARY KEY,
    ed2k_hash TEXT NOT NULL REFERENCES files (ed2k_hash),
    verdict TEXT NOT NULL,
    real_meta TEXT,
    checks TEXT,
    verified_at TEXT NOT NULL,
    node_id TEXT NOT NULL
);

CREATE TRIGGER files_no_update
BEFORE UPDATE ON files
BEGIN
    SELECT RAISE(ABORT, 'files est append-only');
END;

CREATE TRIGGER files_no_delete
BEFORE DELETE ON files
BEGIN
    SELECT RAISE(ABORT, 'files est append-only');
END;

CREATE TRIGGER file_observations_no_update
BEFORE UPDATE ON file_observations
BEGIN
    SELECT RAISE(ABORT, 'file_observations est append-only');
END;

CREATE TRIGGER file_observations_no_delete
BEFORE DELETE ON file_observations
BEGIN
    SELECT RAISE(ABORT, 'file_observations est append-only');
END;

CREATE TRIGGER sources_no_update
BEFORE UPDATE ON sources
BEGIN
    SELECT RAISE(ABORT, 'sources est append-only');
END;

CREATE TRIGGER sources_no_delete
BEFORE DELETE ON sources
BEGIN
    SELECT RAISE(ABORT, 'sources est append-only');
END;

CREATE TRIGGER source_observations_no_update
BEFORE UPDATE ON source_observations
BEGIN
    SELECT RAISE(ABORT, 'source_observations est append-only');
END;

CREATE TRIGGER source_observations_no_delete
BEFORE DELETE ON source_observations
BEGIN
    SELECT RAISE(ABORT, 'source_observations est append-only');
END;

CREATE TRIGGER match_decisions_no_update
BEFORE UPDATE ON match_decisions
BEGIN
    SELECT RAISE(ABORT, 'match_decisions est append-only');
END;

CREATE TRIGGER match_decisions_no_delete
BEFORE DELETE ON match_decisions
BEGIN
    SELECT RAISE(ABORT, 'match_decisions est append-only');
END;

CREATE TRIGGER file_verifications_no_update
BEFORE UPDATE ON file_verifications
BEGIN
    SELECT RAISE(ABORT, 'file_verifications est append-only');
END;

CREATE TRIGGER file_verifications_no_delete
BEFORE DELETE ON file_verifications
BEGIN
    SELECT RAISE(ABORT, 'file_verifications est append-only');
END;
```

- [ ] **Step 3: Créer `migrations/local/0001_initial.sql`**

`src/emule_indexer/adapters/persistence_sqlite/migrations/local/0001_initial.sql` :
```sql
-- local.db — migration 0001 : état opérationnel (spec data-model §5 ; spec MVP §12).
-- JAMAIS fusionné : seul catalog.db traverse la frontière du nœud (invariant §11).
-- L'index UNIQUE partiel rend l'enqueue idempotent (au plus UNE tâche active par hash).

CREATE TABLE node_runtime (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE verification_tasks (
    id INTEGER PRIMARY KEY,
    ed2k_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'in_progress', 'done', 'dead_letter')),
    attempts INTEGER NOT NULL DEFAULT 0,
    enqueued_at TEXT NOT NULL,
    claimed_at TEXT,
    lease_until TEXT
);

CREATE UNIQUE INDEX idx_verification_tasks_active_hash
ON verification_tasks (ed2k_hash)
WHERE status IN ('pending', 'in_progress');

CREATE TABLE downloads (
    ed2k_hash TEXT PRIMARY KEY,
    target_id TEXT NOT NULL,
    state TEXT NOT NULL,
    queued_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE scheduler_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

- [ ] **Step 4: Linter puis EXÉCUTER les deux scripts pour de vrai (smoke)**

Run: `uv run sqlfluff lint src`
Expected: `All Finished!`, code retour 0, AUCUNE violation.

Run (exécution réelle des scripts, comme le runner le fera) :
```bash
uv run python - <<'EOF'
import pathlib
import sqlite3
import tempfile

root = pathlib.Path("src/emule_indexer/adapters/persistence_sqlite/migrations")
for kind in ("catalog", "local"):
    db = sqlite3.connect(pathlib.Path(tempfile.mkdtemp()) / f"{kind}.db", autocommit=True)
    script = (root / kind / "0001_initial.sql").read_text(encoding="utf-8")
    db.executescript("BEGIN;\n" + script + "\nCOMMIT;")
    tables = db.execute("SELECT count(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0]
    triggers = db.execute("SELECT count(*) FROM sqlite_master WHERE type = 'trigger'").fetchone()[0]
    print(kind, "tables:", tables, "triggers:", triggers)
    db.close()
EOF
```
Expected (EXACTEMENT) :
```
catalog tables: 6 triggers: 12
local tables: 4 triggers: 0
```

- [ ] **Step 5: Passer le gate de 4 à 5 checks**

`.githooks/pre-push` — contenu COMPLET après modification (la ligne sqlfluff s'insère entre format et mypy) :
```bash
#!/usr/bin/env bash
# Pré-push : refuse le push si un check échoue (mêmes checks que la CI).
set -euo pipefail

command -v uv >/dev/null 2>&1 || { echo "[pre-push] ERROR: 'uv' introuvable. Installe-le : https://docs.astral.sh/uv/"; exit 1; }

echo "[pre-push] ruff check…";          uv run ruff check .
echo "[pre-push] ruff format --check…"; uv run ruff format --check .
echo "[pre-push] sqlfluff lint…";       uv run sqlfluff lint src
echo "[pre-push] mypy…";                uv run mypy
echo "[pre-push] pytest…";              uv run pytest
echo "[pre-push] OK"
```

`.github/workflows/ci.yml` — contenu COMPLET après modification :
```yaml
name: CI

on:
  push:
  pull_request:

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true
      - run: uv sync --dev
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run sqlfluff lint src
      - run: uv run mypy
      - run: uv run pytest
```

`CLAUDE.md` — édition MINIMALE du bloc de commandes (rien d'autre ne change). Remplacer :
```
# The full gate (must be green before any commit; the pre-push hook + CI run the same four):
uv run pytest -q                       # tests + 100% BRANCH coverage gate (fails the run under 100%)
uv run ruff check .
uv run ruff format --check .
uv run mypy
```
par :
```
# The full gate (must be green before any commit; the pre-push hook + CI run the same five):
uv run pytest -q                       # tests + 100% BRANCH coverage gate (fails the run under 100%)
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run sqlfluff lint src               # lint SQL (migrations SQLite embarquées)
```

- [ ] **Step 6: Gate complet (désormais 5 checks) + commit**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run sqlfluff lint src`
Expected: tout vert — `285 passed, 4 deselected`, coverage 100 % (le nouvel `__init__.py` à docstring seule = 0 statement).

```bash
git add src/emule_indexer/adapters/persistence_sqlite .githooks/pre-push .github/workflows/ci.yml CLAUDE.md
git commit -m "feat(adapters): schémas SQLite v1 (catalog/local, append-only par triggers) + sqlfluff au gate"
```

---

## Task 3: Ports — `CatalogRepository`, `LocalStateRepository`, `ClaimedTask`

**Files:**
- Create: `src/emule_indexer/ports/catalog_repository.py`
- Create: `src/emule_indexer/ports/local_state_repository.py`
- Create: `tests/ports/test_catalog_repository.py`
- Create: `tests/ports/test_local_state_repository.py`

- [ ] **Step 1: Écrire les tests qui échouent**

`tests/ports/test_catalog_repository.py` :
```python
from emule_indexer.domain.matching.engine import Explanation, MatchDecision
from emule_indexer.domain.observation import FileObservation
from emule_indexer.ports.catalog_repository import CatalogRepository


class _StubRepository:
    """Implémentation structurelle minimale : satisfait CatalogRepository SANS l'importer."""

    def __init__(self) -> None:
        self.observations: list[FileObservation] = []
        self.decisions: list[tuple[str, MatchDecision]] = []

    def record_observation(self, observation: FileObservation) -> None:
        self.observations.append(observation)

    def record_decision(self, ed2k_hash: str, decision: MatchDecision) -> None:
        self.decisions.append((ed2k_hash, decision))


def test_protocol_is_satisfied_structurally() -> None:
    stub = _StubRepository()
    repository: CatalogRepository = stub  # mypy prouve la satisfaction structurelle
    observation = FileObservation(
        ed2k_hash="31d6cfe0d16ae931b73c59d7e0c089c0",
        filename="Keroro 062A.avi",
        size_bytes=100,
        source_count=1,
        complete_source_count=0,
        keyword="keroro",
    )
    decision = MatchDecision(
        target_id="S2E062A",
        rule_name="exact",
        tier="download",
        explanation=Explanation(
            target_id="S2E062A", rules_fired=("exact",), tokens_matched=(), coverage_values=()
        ),
    )
    repository.record_observation(observation)
    repository.record_decision(observation.ed2k_hash, decision)
    assert stub.observations == [observation]
    assert stub.decisions == [(observation.ed2k_hash, decision)]
```

`tests/ports/test_local_state_repository.py` :
```python
import dataclasses

import pytest

from emule_indexer.ports.local_state_repository import ClaimedTask, LocalStateRepository


def test_claimed_task_is_frozen_and_holds_fields() -> None:
    task = ClaimedTask(task_id=7, ed2k_hash="31d6cfe0d16ae931b73c59d7e0c089c0", attempts=1)
    assert task.task_id == 7
    assert task.ed2k_hash == "31d6cfe0d16ae931b73c59d7e0c089c0"
    assert task.attempts == 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        task.attempts = 2  # type: ignore[misc]


class _StubRepository:
    """Implémentation structurelle minimale : satisfait LocalStateRepository SANS l'importer."""

    def node_id(self) -> str:
        return "00000000-0000-0000-0000-000000000000"

    def enqueue_verification(self, ed2k_hash: str) -> bool:
        return True

    def claim_verification(self) -> ClaimedTask | None:
        return None

    def complete_verification(self, task_id: int) -> None:
        return None

    def fail_verification(self, task_id: int) -> None:
        return None

    def reclaim_expired(self) -> int:
        return 0


def test_protocol_is_satisfied_structurally() -> None:
    repository: LocalStateRepository = _StubRepository()  # mypy prouve la satisfaction
    assert repository.node_id() == "00000000-0000-0000-0000-000000000000"
    assert repository.enqueue_verification("31d6cfe0d16ae931b73c59d7e0c089c0") is True
    assert repository.claim_verification() is None
    repository.complete_verification(1)
    repository.fail_verification(1)
    assert repository.reclaim_expired() == 0
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/ports -q --no-cov`
Expected: FAIL (collection) — `ModuleNotFoundError: No module named 'emule_indexer.ports.catalog_repository'`.

- [ ] **Step 3: Écrire les deux ports**

`src/emule_indexer/ports/catalog_repository.py` :
```python
"""Port ``CatalogRepository`` : la mémoire durable du catalogue (spec data-model §4).

Protocol SYNCHRONE (spec §3 : une écriture locale est sub-milliseconde ; si le plan C
veut s'isoler, il enveloppera dans ``asyncio.to_thread`` sans toucher cette couche).
Le port n'importe QUE le domaine. Les stubs tiennent sur UNE ligne (le ``def`` s'exécute
à la création de la classe : couvert). L'adapter stamppe ``observed_at``/``decided_at``/
``node_id`` — c'est pour ça que ``record_decision`` reçoit le hash À CÔTÉ de la décision
(``MatchDecision`` ne porte pas la clé contenu, par principe : domaine sans colonnes de
persistance).
"""

from typing import Protocol

from emule_indexer.domain.matching.engine import MatchDecision
from emule_indexer.domain.observation import FileObservation


class CatalogRepository(Protocol):
    """Contrat sync d'écriture du catalogue (append-only ; l'adapter signale, il ne décide pas)."""

    def record_observation(self, observation: FileObservation) -> None: ...

    def record_decision(self, ed2k_hash: str, decision: MatchDecision) -> None: ...
```

`src/emule_indexer/ports/local_state_repository.py` :
```python
"""Port ``LocalStateRepository`` : identité du nœud + file de tâches (spec data-model §4/§6).

Protocol SYNCHRONE (même principe que ``CatalogRepository``). ``ClaimedTask`` est le DTO
gelé du claim (spec §4) : ``attempts`` est compté AU CLAIM (spec §6) — le consommateur
(plan D) le verra à 1 dès la première prise. ``local.db`` n'est JAMAIS fusionné : ce port
ne traverse pas la frontière du nœud (invariant MVP §11).
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ClaimedTask:
    """Une tâche de vérification prise par un worker (lease posée, attempts incrémenté)."""

    task_id: int
    ed2k_hash: str
    attempts: int


class LocalStateRepository(Protocol):
    """Contrat sync de l'état local : identité stable + file FIFO idempotente (§12 MVP)."""

    def node_id(self) -> str: ...

    def enqueue_verification(self, ed2k_hash: str) -> bool: ...

    def claim_verification(self) -> ClaimedTask | None: ...

    def complete_verification(self, task_id: int) -> None: ...

    def fail_verification(self, task_id: int) -> None: ...

    def reclaim_expired(self) -> int: ...
```

- [ ] **Step 4: Vérifier le passage puis le gate complet**

Run: `uv run pytest tests/ports -q --no-cov`
Expected: PASS (7 tests : 4 existants `mule_client` + 3 nouveaux).

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run sqlfluff lint src`
Expected: tout vert — `288 passed, 4 deselected`, coverage 100 % (stubs une-ligne couverts par le `def`).

- [ ] **Step 5: Commit**

```bash
git add src/emule_indexer/ports tests/ports
git commit -m "feat(ports): CatalogRepository, LocalStateRepository et ClaimedTask"
```

---

## Task 4: Erreurs + connexion + runner de migrations

**Files:**
- Create: `src/emule_indexer/adapters/persistence_sqlite/errors.py`
- Create: `src/emule_indexer/adapters/persistence_sqlite/connection.py`
- Create: `tests/adapters/persistence_sqlite/__init__.py` (vide)
- Create: `tests/adapters/persistence_sqlite/test_connection.py`

> Le cœur d'infrastructure : DÉCISIONS 1-5. Les tests couvrent le nominal ET l'hostile (base plus récente, script qui échoue, `:memory:`, chemin inouvrables, préfixe de script invalide) — c'est ce qui porte le runner à 100 % branch en un seul commit.

- [ ] **Step 1: Créer `tests/adapters/persistence_sqlite/__init__.py`** (fichier VIDE, convention des paquets de tests du dépôt)

- [ ] **Step 2: Écrire les tests qui échouent**

`tests/adapters/persistence_sqlite/test_connection.py` :
```python
import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from emule_indexer.adapters.persistence_sqlite.connection import (
    _apply_migrations,
    _load_scripts,
    open_catalog,
    open_local,
    utc_iso,
    utc_now,
)
from emule_indexer.adapters.persistence_sqlite.errors import MigrationError, PersistenceError

_CATALOG_TABLES = {
    "files",
    "file_observations",
    "sources",
    "source_observations",
    "match_decisions",
    "file_verifications",
}
_LOCAL_TABLES = {"node_runtime", "verification_tasks", "downloads", "scheduler_state"}


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


def test_open_catalog_creates_the_six_tables_and_versions_the_schema(tmp_path: Path) -> None:
    connection = open_catalog(tmp_path / "catalog.db")
    try:
        assert _table_names(connection) == _CATALOG_TABLES
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    finally:
        connection.close()


def test_open_local_creates_the_four_tables_and_the_partial_unique_index(tmp_path: Path) -> None:
    connection = open_local(tmp_path / "local.db")
    try:
        assert _table_names(connection) == _LOCAL_TABLES
        index_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'idx_verification_tasks_active_hash'"
        ).fetchone()[0]
        assert "WHERE status IN ('pending', 'in_progress')" in index_sql
    finally:
        connection.close()


def test_open_applies_wal_and_foreign_keys_pragmas(tmp_path: Path) -> None:
    connection = open_catalog(tmp_path / "catalog.db")
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        connection.close()


def test_foreign_keys_are_enforced(tmp_path: Path) -> None:
    connection = open_catalog(tmp_path / "catalog.db")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute(
                "INSERT INTO match_decisions"
                " (ed2k_hash, target_id, rule_name, tier, decided_at, node_id)"
                " VALUES ('absent', 't', 'r', 'catalog', 'now', 'n')"
            )
    finally:
        connection.close()


def test_reopen_is_idempotent_and_keeps_data(tmp_path: Path) -> None:
    path = tmp_path / "catalog.db"
    first = open_catalog(path)
    first.execute("INSERT INTO files (ed2k_hash, size_bytes) VALUES ('a' * 32, 1)")
    first.close()
    second = open_catalog(path)  # version 1 déjà appliquée : AUCUN script ne rejoue
    try:
        assert second.execute("PRAGMA user_version").fetchone()[0] == 1
        assert second.execute("SELECT count(*) FROM files").fetchone()[0] == 1
    finally:
        second.close()


def test_in_memory_database_is_refused_because_wal_is_required() -> None:
    # :memory: répond journal_mode='memory' (vérifié empiriquement) -> refus net.
    with pytest.raises(PersistenceError, match="WAL"):
        open_catalog(":memory:")


def test_unopenable_path_raises_persistence_error(tmp_path: Path) -> None:
    with pytest.raises(PersistenceError):
        open_catalog(tmp_path)  # un répertoire n'est pas une base


def test_database_newer_than_the_code_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "catalog.db"
    open_catalog(path).close()
    raw = sqlite3.connect(path, autocommit=True)
    raw.execute("PRAGMA user_version = 99")
    raw.close()
    with pytest.raises(MigrationError, match="99"):
        open_catalog(path)


def test_apply_migrations_with_no_scripts_is_a_noop(tmp_path: Path) -> None:
    connection = sqlite3.connect(tmp_path / "vide.db", autocommit=True)
    try:
        _apply_migrations(connection, ())
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
    finally:
        connection.close()


def test_failed_script_is_rolled_back_and_version_unchanged(tmp_path: Path) -> None:
    connection = sqlite3.connect(tmp_path / "echec.db", autocommit=True)
    bad_script = "CREATE TABLE disparait (x INTEGER);\nINSERT INTO inexistante VALUES (1);"
    try:
        with pytest.raises(MigrationError, match="migration 2"):
            _apply_migrations(
                connection, ((1, "CREATE TABLE survit (x INTEGER);"), (2, bad_script))
            )
        # La migration 1 a SA transaction (appliquée) ; la 2 est ENTIÈREMENT défaite.
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert _table_names(connection) == {"survit"}
    finally:
        connection.close()


def test_load_scripts_orders_by_name_and_skips_non_sql(tmp_path: Path) -> None:
    (tmp_path / "0002_second.sql").write_text("B", encoding="utf-8")
    (tmp_path / "0001_premier.sql").write_text("A", encoding="utf-8")
    (tmp_path / "README.md").write_text("ignoré", encoding="utf-8")
    assert _load_scripts(tmp_path) == ((1, "A"), (2, "B"))


def test_load_scripts_rejects_a_non_numeric_prefix(tmp_path: Path) -> None:
    (tmp_path / "abcd_mauvais.sql").write_text("X", encoding="utf-8")
    with pytest.raises(MigrationError, match="abcd_mauvais.sql"):
        _load_scripts(tmp_path)


def test_utc_iso_is_fixed_width_and_normalizes_to_utc() -> None:
    # Largeur fixe (microsecondes TOUJOURS écrites) => ordre lexicographique == chronologique.
    paris = timezone(timedelta(hours=2))
    moment = datetime(2026, 6, 11, 14, 0, 0, tzinfo=paris)
    assert utc_iso(moment) == "2026-06-11T12:00:00.000000+00:00"


def test_utc_now_returns_an_aware_utc_datetime() -> None:
    now = utc_now()
    assert now.tzinfo == UTC
```

- [ ] **Step 3: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/adapters/persistence_sqlite -q --no-cov`
Expected: FAIL (collection) — `ModuleNotFoundError: No module named 'emule_indexer.adapters.persistence_sqlite.connection'`.

- [ ] **Step 4: Écrire `errors.py`**

`src/emule_indexer/adapters/persistence_sqlite/errors.py` :
```python
"""Hiérarchie d'erreurs de l'adapter persistence (spec data-model §7).

L'adapter SIGNALE, il ne décide pas (même philosophie que l'adapter EC) : toute
``sqlite3.Error`` inattendue sort enveloppée en ``PersistenceError``, jamais nue.
Un trigger append-only qui se déclenche est un BUG du code appelant, pas un cas
métier → la même ``PersistenceError``. ``wrap_sqlite_errors`` est l'enveloppe
UNIQUE partagée par la connexion et les deux repositories (cause chaînée gardée).
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager


class PersistenceError(Exception):
    """Base de toutes les erreurs de l'adapter persistence."""


class MigrationError(PersistenceError):
    """Base plus récente que le code, ou script qui échoue (fail-fast, spec MVP §14)."""


@contextmanager
def wrap_sqlite_errors() -> Iterator[None]:
    """Enveloppe toute ``sqlite3.Error`` en ``PersistenceError`` (cause chaînée)."""
    try:
        yield
    except sqlite3.Error as error:
        raise PersistenceError(str(error)) from error
```

- [ ] **Step 5: Écrire `connection.py`**

`src/emule_indexer/adapters/persistence_sqlite/connection.py` :
```python
"""Connexion SQLite + runner de migrations (spec data-model §3/§4/§7).

Chaque connexion est ouverte en autocommit RÉEL (``autocommit=True``, Python ≥ 3.12) :
les transactions sont EXPLICITES (``BEGIN``/``COMMIT``/``ROLLBACK`` écrits par les
repositories), aucune isolation implicite. PRAGMA d'ouverture (spec §3) :
``journal_mode=WAL`` — EXIGÉ : ``:memory:`` ne le porte pas (il répond ``memory``)
et est donc refusé net ; les tests utilisent des fichiers réels (spec §8) —
et ``foreign_keys=ON``.

Le runner lit les scripts ``NNNN_*.sql`` embarqués dans le paquet (``importlib.
resources``), les applique en ordre croissant CHACUN dans SA transaction (échec →
ROLLBACK best-effort, version inchangée — même esprit que le ``close()`` best-effort
du transport EC), et trace l'état dans ``PRAGMA user_version``. Une base PLUS RÉCENTE
que le code → refus net (``MigrationError``, fail-fast spec MVP §14). Les scripts ne
contiennent AUCUN ``BEGIN``/``COMMIT`` : c'est le runner qui enveloppe.

Ce module porte aussi l'horloge partagée des repositories (``Clock``/``utc_now``/
``utc_iso``) : ISO-8601 UTC en TEXT (spec §3), microsecondes FIXES pour que l'ordre
lexicographique SOIT l'ordre chronologique (le claim FIFO trie sur ``enqueued_at``).
"""

import sqlite3
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

from emule_indexer.adapters.persistence_sqlite.errors import (
    MigrationError,
    PersistenceError,
    wrap_sqlite_errors,
)

type Clock = Callable[[], datetime]

_MIGRATIONS = resources.files("emule_indexer.adapters.persistence_sqlite") / "migrations"


def utc_now() -> datetime:
    """Horloge par défaut des repositories (spec §3 : injectable, ``datetime.now(UTC)``)."""
    return datetime.now(UTC)


def utc_iso(moment: datetime) -> str:
    """ISO-8601 UTC à largeur fixe (microsecondes TOUJOURS écrites), p.ex.
    ``2026-06-11T12:00:00.000000+00:00``. ``moment`` doit être AWARE (contrat de
    ``Clock``) ; un fuseau non-UTC est normalisé, jamais stocké tel quel."""
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def open_catalog(path: Path | str) -> sqlite3.Connection:
    """Ouvre/migre ``catalog.db`` (les triggers append-only font partie du schéma)."""
    return _open(path, _MIGRATIONS / "catalog")


def open_local(path: Path | str) -> sqlite3.Connection:
    """Ouvre/migre ``local.db``."""
    return _open(path, _MIGRATIONS / "local")


def _open(path: Path | str, scripts_dir: Traversable) -> sqlite3.Connection:
    with wrap_sqlite_errors():
        connection = sqlite3.connect(path, autocommit=True)
    try:
        with wrap_sqlite_errors():
            _configure(connection)
            _apply_migrations(connection, _load_scripts(scripts_dir))
    except PersistenceError:
        connection.close()
        raise
    return connection


def _configure(connection: sqlite3.Connection) -> None:
    journal_mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    if journal_mode != "wal":
        raise PersistenceError(
            f"journal_mode={journal_mode!r} : WAL exigé (spec §3) — base fichier uniquement"
        )
    connection.execute("PRAGMA foreign_keys=ON")


def _load_scripts(directory: Traversable) -> tuple[tuple[int, str], ...]:
    """Découverte des migrations : ``NNNN_*.sql`` triés par nom (ordre lexicographique).

    Un fichier non-``.sql`` est ignoré ; un ``.sql`` sans préfixe numérique est un BUG
    d'empaquetage → ``MigrationError`` (fail-fast, pas de migration silencieusement sautée).
    """
    scripts: list[tuple[int, str]] = []
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        if not entry.name.endswith(".sql"):
            continue
        prefix = entry.name.partition("_")[0]
        if not prefix.isdigit():
            raise MigrationError(f"nom de script invalide (attendu NNNN_*.sql) : {entry.name}")
        scripts.append((int(prefix), entry.read_text(encoding="utf-8")))
    return tuple(scripts)


def _apply_migrations(connection: sqlite3.Connection, scripts: tuple[tuple[int, str], ...]) -> None:
    """Applique les scripts de version > ``user_version``, chacun dans SA transaction.

    ``PRAGMA user_version = N`` est posé DANS la transaction du script N (le pragma est
    transactionnel : un ROLLBACK le rend — vérifié empiriquement, SQLite 3.47.1). PRAGMA
    n'accepte pas de paramètre lié : ``version`` vient d'``int()``, l'interpolation est sûre.
    """
    current = int(connection.execute("PRAGMA user_version").fetchone()[0])
    latest = scripts[-1][0] if scripts else 0
    if current > latest:
        raise MigrationError(
            f"base en version {current}, code en version {latest} : "
            "base plus récente que le code, refus de démarrer (spec §3)"
        )
    for version, script in scripts:
        if version <= current:
            continue
        try:
            connection.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;")
        except sqlite3.Error as error:
            with suppress(sqlite3.Error):
                connection.execute("ROLLBACK")
            raise MigrationError(f"migration {version} échouée : {error}") from error
```

- [ ] **Step 6: Vérifier le passage puis le gate complet**

Run: `uv run pytest tests/adapters/persistence_sqlite -q --no-cov`
Expected: PASS — 14 tests.

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run sqlfluff lint src`
Expected: tout vert — `302 passed, 4 deselected`, coverage 100 %.

- [ ] **Step 7: Commit**

```bash
git add src/emule_indexer/adapters/persistence_sqlite tests/adapters/persistence_sqlite
git commit -m "feat(adapters): connexion SQLite + runner de migrations (WAL, user_version, fail-fast)"
```

---

## Task 5: Append-only — la propriété est dans la BASE, prouvons-le

**Files:**
- Create: `tests/adapters/persistence_sqlite/test_append_only.py`

> Tests PURS sur le schéma de la Task 2 (aucun code de prod nouveau) : un `UPDATE`/`DELETE` DIRECT sur chacune des six tables du catalogue — comme le ferait un outil tiers — doit échouer par trigger (spec §3 : « tient face à un outil tiers »). La classe d'exception attendue est la classe RÉELLE observée : `sqlite3.IntegrityError` (vérification n° 4).

- [ ] **Step 1: Écrire les tests**

`tests/adapters/persistence_sqlite/test_append_only.py` :
```python
"""Append-only IMPOSÉ PAR LA BASE (spec data-model §3) : propriété du SCHÉMA, pas du code.

Chaque table de ``catalog.db`` porte un trigger ``BEFORE UPDATE`` et un ``BEFORE DELETE``
→ ``RAISE(ABORT, '<table> est append-only')``. Vérifié ici par UPDATE/DELETE DIRECTS sur
la connexion (comme le ferait un outil tiers) : la violation surface en
``sqlite3.IntegrityError`` (classe RÉELLE observée, SQLite 3.47.1).
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from emule_indexer.adapters.persistence_sqlite.connection import open_catalog

# Une ligne par table (FK respectées : files d'abord) + l'UPDATE qui DOIT échouer.
_SEED = (
    "INSERT INTO files (ed2k_hash, size_bytes) VALUES ('a', 1)",
    "INSERT INTO file_observations (ed2k_hash, filename, size_bytes, source_count,"
    " complete_source_count, raw_meta, keyword, observed_at, node_id)"
    " VALUES ('a', 'f', 1, 0, 0, '[]', 'k', 't', 'n')",
    "INSERT INTO sources (user_hash) VALUES ('u')",
    "INSERT INTO source_observations (user_hash, ed2k_hash, raw_meta, observed_at, node_id)"
    " VALUES ('u', 'a', '[]', 't', 'n')",
    "INSERT INTO match_decisions (ed2k_hash, target_id, rule_name, tier, decided_at, node_id)"
    " VALUES ('a', 'S2E062A', 'r', 'download', 't', 'n')",
    "INSERT INTO file_verifications (ed2k_hash, verdict, verified_at, node_id)"
    " VALUES ('a', 'pending', 't', 'n')",
)

_UPDATES = {
    "files": "UPDATE files SET size_bytes = 2",
    "file_observations": "UPDATE file_observations SET filename = 'autre'",
    "sources": "UPDATE sources SET client_name = 'autre'",
    "source_observations": "UPDATE source_observations SET nickname = 'autre'",
    "match_decisions": "UPDATE match_decisions SET tier = 'notify'",
    "file_verifications": "UPDATE file_verifications SET verdict = 'ok'",
}


@pytest.fixture
def seeded_catalog(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_catalog(tmp_path / "catalog.db")
    for statement in _SEED:
        connection.execute(statement)
    yield connection
    connection.close()


@pytest.mark.parametrize("table", sorted(_UPDATES))
def test_direct_update_is_rejected_by_the_database(
    seeded_catalog: sqlite3.Connection, table: str
) -> None:
    with pytest.raises(sqlite3.IntegrityError, match=f"{table} est append-only"):
        seeded_catalog.execute(_UPDATES[table])


@pytest.mark.parametrize("table", sorted(_UPDATES))
def test_direct_delete_is_rejected_by_the_database(
    seeded_catalog: sqlite3.Connection, table: str
) -> None:
    with pytest.raises(sqlite3.IntegrityError, match=f"{table} est append-only"):
        seeded_catalog.execute(f"DELETE FROM {table}")


def test_insert_remains_allowed_on_every_table(seeded_catalog: sqlite3.Connection) -> None:
    # Append-only = on peut TOUJOURS ajouter (l'INSERT du seed a déjà réussi) ;
    # ici on prouve qu'un DEUXIÈME insert passe aussi (les triggers ne bloquent que U/D).
    seeded_catalog.execute("INSERT INTO files (ed2k_hash, size_bytes) VALUES ('b', 2)")
    assert seeded_catalog.execute("SELECT count(*) FROM files").fetchone()[0] == 2
```

- [ ] **Step 2: Lancer — ces tests doivent passer DIRECTEMENT**

Run: `uv run pytest tests/adapters/persistence_sqlite/test_append_only.py -q --no-cov`
Expected: PASS — 13 tests (2 × 6 paramétrés + 1). Ils valident le SQL de la Task 2, pas du code nouveau : s'ils échouent, c'est le SCHÉMA qu'il faut corriger (un trigger manquant/mal nommé), jamais le test.

- [ ] **Step 3: Gate complet + commit**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run sqlfluff lint src`
Expected: tout vert — `315 passed, 4 deselected`, coverage 100 %.

```bash
git add tests/adapters/persistence_sqlite/test_append_only.py
git commit -m "test: append-only imposé par la base sur les six tables du catalogue"
```

---

## Task 6: `SqliteCatalogRepository.record_observation`

**Files:**
- Create: `src/emule_indexer/adapters/persistence_sqlite/catalog_repository.py` (SANS `record_decision` — il arrive Task 7 avec ses tests, pour que CE commit soit à 100 %)
- Create: `tests/adapters/persistence_sqlite/test_catalog_repository.py`

- [ ] **Step 1: Écrire les tests qui échouent**

`tests/adapters/persistence_sqlite/test_catalog_repository.py` :
```python
import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from emule_indexer.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from emule_indexer.adapters.persistence_sqlite.connection import open_catalog
from emule_indexer.adapters.persistence_sqlite.errors import PersistenceError
from emule_indexer.domain.observation import FileObservation

_HASH = "31d6cfe0d16ae931b73c59d7e0c089c0"
_NODE = "11111111-2222-3333-4444-555555555555"
_FROZEN_NOW = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)
_FROZEN_ISO = "2026-06-11T12:00:00.000000+00:00"


def _frozen_clock() -> datetime:
    return _FROZEN_NOW


def _observation(
    *,
    filename: str = "Keroro 062A.avi",
    size_bytes: int = 234567890,
    media_length_sec: int | None = None,
    bitrate_kbps: int | None = None,
    codec: str | None = None,
    file_type: str | None = None,
) -> FileObservation:
    # média None par défaut (EC n'expose AUCUNE métadonnée média — rapport 2026-06-11) ;
    # raw_meta avec DOUBLON, ordre wire et non-ASCII (les trois propriétés à préserver).
    return FileObservation(
        ed2k_hash=_HASH,
        filename=filename,
        size_bytes=size_bytes,
        source_count=5,
        complete_source_count=2,
        keyword="keroro",
        media_length_sec=media_length_sec,
        bitrate_kbps=bitrate_kbps,
        codec=codec,
        file_type=file_type,
        raw_meta=(("0x0308", "0"), ("0x0308", "0"), ("0x0999", "mystère")),
    )


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    catalog = open_catalog(tmp_path / "catalog.db")
    yield catalog
    catalog.close()


@pytest.fixture
def repository(connection: sqlite3.Connection) -> SqliteCatalogRepository:
    return SqliteCatalogRepository(connection, _NODE, clock=_frozen_clock)


def test_record_observation_round_trip(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    repository.record_observation(_observation())
    file_row = connection.execute("SELECT ed2k_hash, size_bytes, aich_hash FROM files").fetchone()
    assert file_row == (_HASH, 234567890, None)
    row = connection.execute(
        "SELECT ed2k_hash, filename, size_bytes, source_count, complete_source_count,"
        " media_length_sec, bitrate_kbps, codec, file_type, raw_meta, keyword,"
        " observed_at, node_id FROM file_observations"
    ).fetchone()
    assert row == (
        _HASH,
        "Keroro 062A.avi",
        234567890,
        5,
        2,
        None,
        None,
        None,
        None,
        '[["0x0308", "0"], ["0x0308", "0"], ["0x0999", "mystère"]]',
        "keroro",
        _FROZEN_ISO,
        _NODE,
    )


def test_raw_meta_preserves_order_duplicates_and_non_ascii(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    repository.record_observation(_observation())
    stored = connection.execute("SELECT raw_meta FROM file_observations").fetchone()[0]
    assert "mystère" in stored  # ensure_ascii=False : l'accent est stocké TEL QUEL
    assert json.loads(stored) == [["0x0308", "0"], ["0x0308", "0"], ["0x0999", "mystère"]]


def test_record_observation_twice_first_seen_wins_in_files(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    repository.record_observation(_observation())
    # Même hash, taille et nom DIFFÉRENTS (entrée hostile, déviation 1 spec §5).
    repository.record_observation(_observation(filename="leurre.avi", size_bytes=999))
    assert connection.execute("SELECT size_bytes FROM files").fetchall() == [(234567890,)]
    observed_sizes = connection.execute(
        "SELECT size_bytes FROM file_observations ORDER BY id"
    ).fetchall()
    assert observed_sizes == [(234567890,), (999,)]  # l'anomalie reste VISIBLE


def test_record_observation_with_media_metadata_and_default_clock(tmp_path: Path) -> None:
    connection = open_catalog(tmp_path / "catalog.db")
    try:
        repository = SqliteCatalogRepository(connection, _NODE)  # horloge par défaut (utc_now)
        repository.record_observation(
            _observation(media_length_sec=1474, bitrate_kbps=1200, codec="xvid", file_type="Video")
        )
        row = connection.execute(
            "SELECT media_length_sec, bitrate_kbps, codec, file_type, observed_at"
            " FROM file_observations"
        ).fetchone()
        assert row[:4] == (1474, 1200, "xvid", "Video")
        stamped = datetime.fromisoformat(row[4])
        assert stamped.tzinfo == UTC  # l'horloge par défaut stamppe bien de l'UTC aware
    finally:
        connection.close()


def test_record_observation_is_one_transaction(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    # Panne injectée ENTRE les deux INSERT : un trigger de TEST fait échouer le second.
    connection.execute(
        "CREATE TRIGGER boom BEFORE INSERT ON file_observations"
        " WHEN NEW.filename = '__boom__'"
        " BEGIN SELECT RAISE(ABORT, 'panne injectée'); END"
    )
    with pytest.raises(PersistenceError, match="panne injectée"):
        repository.record_observation(_observation(filename="__boom__"))
    # ATOMICITÉ : le INSERT OR IGNORE dans files a été défait avec la transaction.
    assert connection.execute("SELECT count(*) FROM files").fetchone()[0] == 0
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/adapters/persistence_sqlite/test_catalog_repository.py -q --no-cov`
Expected: FAIL (collection) — `ModuleNotFoundError: No module named 'emule_indexer.adapters.persistence_sqlite.catalog_repository'`.

- [ ] **Step 3: Écrire l'implémentation (sans `record_decision`)**

`src/emule_indexer/adapters/persistence_sqlite/catalog_repository.py` :
```python
"""``SqliteCatalogRepository`` : ``FileObservation``/``MatchDecision`` → lignes durables.

L'adapter stamppe ce que le domaine ignore (spec data-model §3) : ``observed_at``/
``decided_at`` (horloge injectable, ``utc_now`` par défaut) et ``node_id`` (fourni au
constructeur — le plan C le lira du ``LocalStateRepository``). ``raw_meta`` est sérialisé
en JSON LISTE de paires (``[["0x0308", "0"], …]``), ordre du fil et doublons préservés,
``ensure_ascii=False``, pas de tri (spec §3). ``record_observation`` fait UNE transaction
(spec §4) : ``INSERT OR IGNORE`` dans ``files`` (première vue gagne) puis ``INSERT`` dans
``file_observations`` — la taille OBSERVÉE est TOUJOURS écrite dans l'observation
(déviation 1, spec §5 : une anomalie de taille ne doit pas devenir invisible).
"""

import json
import sqlite3
from contextlib import suppress

from emule_indexer.adapters.persistence_sqlite.connection import Clock, utc_iso, utc_now
from emule_indexer.adapters.persistence_sqlite.errors import wrap_sqlite_errors
from emule_indexer.domain.observation import FileObservation

_INSERT_FILE = "INSERT OR IGNORE INTO files (ed2k_hash, size_bytes, aich_hash) VALUES (?, ?, NULL)"

_INSERT_OBSERVATION = """
INSERT INTO file_observations (
    ed2k_hash, filename, size_bytes, source_count, complete_source_count,
    media_length_sec, bitrate_kbps, codec, file_type, raw_meta,
    keyword, observed_at, node_id
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


class SqliteCatalogRepository:
    """Implémentation SQLite du port ``CatalogRepository`` (satisfaction STRUCTURELLE)."""

    def __init__(
        self, connection: sqlite3.Connection, node_id: str, *, clock: Clock = utc_now
    ) -> None:
        self._connection = connection
        self._node_id = node_id
        self._clock = clock

    def record_observation(self, observation: FileObservation) -> None:
        """UNE transaction : fichier (première vue gagne) + observation stampée."""
        raw_meta = json.dumps(observation.raw_meta, ensure_ascii=False)
        observed_at = utc_iso(self._clock())
        with wrap_sqlite_errors():
            self._connection.execute("BEGIN")
            try:
                self._connection.execute(
                    _INSERT_FILE, (observation.ed2k_hash, observation.size_bytes)
                )
                self._connection.execute(
                    _INSERT_OBSERVATION,
                    (
                        observation.ed2k_hash,
                        observation.filename,
                        observation.size_bytes,
                        observation.source_count,
                        observation.complete_source_count,
                        observation.media_length_sec,
                        observation.bitrate_kbps,
                        observation.codec,
                        observation.file_type,
                        raw_meta,
                        observation.keyword,
                        observed_at,
                        self._node_id,
                    ),
                )
                self._connection.execute("COMMIT")
            except sqlite3.Error:
                with suppress(sqlite3.Error):
                    self._connection.execute("ROLLBACK")
                raise
```

- [ ] **Step 4: Vérifier le passage puis le gate complet**

Run: `uv run pytest tests/adapters/persistence_sqlite/test_catalog_repository.py -q --no-cov`
Expected: PASS — 5 tests.

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run sqlfluff lint src`
Expected: tout vert — `320 passed, 4 deselected`, coverage 100 %.

- [ ] **Step 5: Commit**

```bash
git add src/emule_indexer/adapters/persistence_sqlite/catalog_repository.py tests/adapters/persistence_sqlite/test_catalog_repository.py
git commit -m "feat(adapters): SqliteCatalogRepository.record_observation (1 transaction, stamp horloge/node)"
```

---

## Task 7: `SqliteCatalogRepository.record_decision` + satisfaction du port

**Files:**
- Modify: `src/emule_indexer/adapters/persistence_sqlite/catalog_repository.py`
- Modify: `tests/adapters/persistence_sqlite/test_catalog_repository.py`

- [ ] **Step 1: Étendre les tests (qui échouent)**

Dans `tests/adapters/persistence_sqlite/test_catalog_repository.py`, remplacer les deux lignes d'import :
```python
from emule_indexer.adapters.persistence_sqlite.errors import PersistenceError
from emule_indexer.domain.observation import FileObservation
```
par :
```python
from emule_indexer.adapters.persistence_sqlite.errors import PersistenceError
from emule_indexer.domain.matching.engine import Explanation, MatchDecision
from emule_indexer.domain.observation import FileObservation
from emule_indexer.ports.catalog_repository import CatalogRepository
```

puis ajouter À LA FIN du fichier :
```python
def _decision() -> MatchDecision:
    return MatchDecision(
        target_id="S2E062A",
        rule_name="exact_062a",
        tier="download",
        explanation=Explanation(
            target_id="S2E062A",
            rules_fired=("exact_062a",),
            tokens_matched=("keroro",),
            coverage_values=(("titre", 0.91),),
        ),
    )


def test_record_decision_round_trip(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    repository.record_observation(_observation())
    repository.record_decision(_HASH, _decision())
    row = connection.execute(
        "SELECT ed2k_hash, target_id, rule_name, tier, decided_at, node_id FROM match_decisions"
    ).fetchone()
    assert row == (_HASH, "S2E062A", "exact_062a", "download", _FROZEN_ISO, _NODE)


def test_explanation_is_never_persisted(
    repository: SqliteCatalogRepository, connection: sqlite3.Connection
) -> None:
    repository.record_observation(_observation())
    repository.record_decision(_HASH, _decision())
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(match_decisions)").fetchall()
    }
    assert columns == {"id", "ed2k_hash", "target_id", "rule_name", "tier", "decided_at", "node_id"}


def test_record_decision_for_unknown_file_raises_persistence_error(
    repository: SqliteCatalogRepository,
) -> None:
    # FK violée (fichier jamais observé) : sqlite3.IntegrityError ENVELOPPÉE, jamais nue.
    with pytest.raises(PersistenceError, match="FOREIGN KEY"):
        repository.record_decision("0" * 32, _decision())


def test_repository_satisfies_the_port_structurally(
    repository: SqliteCatalogRepository,
) -> None:
    port: CatalogRepository = repository  # mypy prouve la satisfaction structurelle
    port.record_observation(_observation())
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/adapters/persistence_sqlite/test_catalog_repository.py -q --no-cov`
Expected: FAIL — `AttributeError: 'SqliteCatalogRepository' object has no attribute 'record_decision'` (4 nouveaux tests ; `test_repository_satisfies_the_port_structurally` échouera aussi côté mypy tant que la méthode manque).

- [ ] **Step 3: Ajouter la méthode**

Dans `src/emule_indexer/adapters/persistence_sqlite/catalog_repository.py`, remplacer la ligne d'import :
```python
from emule_indexer.domain.observation import FileObservation
```
par :
```python
from emule_indexer.domain.matching.engine import MatchDecision
from emule_indexer.domain.observation import FileObservation
```

insérer, juste APRÈS le bloc `_INSERT_OBSERVATION = """…"""` (et sa ligne `"""` de fermeture) :
```python

_INSERT_DECISION = """
INSERT INTO match_decisions (ed2k_hash, target_id, rule_name, tier, decided_at, node_id)
VALUES (?, ?, ?, ?, ?, ?)
"""
```

et ajouter À LA FIN de la classe (après `record_observation`) :
```python

    def record_decision(self, ed2k_hash: str, decision: MatchDecision) -> None:
        """INSERT seul (autocommit) ; fichier inconnu → FK violée → ``PersistenceError``.

        Seules les 3 colonnes de ``MatchDecision`` sont persistées (spec moteur) ;
        ``explanation`` est de l'explicabilité runtime, JAMAIS une colonne.
        """
        with wrap_sqlite_errors():
            self._connection.execute(
                _INSERT_DECISION,
                (
                    ed2k_hash,
                    decision.target_id,
                    decision.rule_name,
                    decision.tier,
                    utc_iso(self._clock()),
                    self._node_id,
                ),
            )
```

- [ ] **Step 4: Vérifier le passage puis le gate complet**

Run: `uv run pytest tests/adapters/persistence_sqlite/test_catalog_repository.py -q --no-cov`
Expected: PASS — 9 tests.

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run sqlfluff lint src`
Expected: tout vert — `324 passed, 4 deselected`, coverage 100 %.

- [ ] **Step 5: Commit**

```bash
git add src/emule_indexer/adapters/persistence_sqlite/catalog_repository.py tests/adapters/persistence_sqlite/test_catalog_repository.py
git commit -m "feat(adapters): SqliteCatalogRepository.record_decision (3 colonnes, jamais l'explanation)"
```

---

## Task 8: `SqliteLocalStateRepository.node_id`

**Files:**
- Create: `src/emule_indexer/adapters/persistence_sqlite/local_state_repository.py` (constructeur complet + `node_id` SEULEMENT — la file arrive Tasks 9-11, méthode par méthode, chaque commit à 100 %)
- Create: `tests/adapters/persistence_sqlite/test_local_state_repository.py`

> Le constructeur porte D'EMBLÉE `lease_duration`/`max_attempts` (DÉCISION 8) : la signature est figée une fois, les Tasks 9-11 n'y retoucheront pas.

- [ ] **Step 1: Écrire les tests qui échouent**

`tests/adapters/persistence_sqlite/test_local_state_repository.py` :
```python
import sqlite3
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from emule_indexer.adapters.persistence_sqlite.connection import open_local
from emule_indexer.adapters.persistence_sqlite.errors import PersistenceError
from emule_indexer.adapters.persistence_sqlite.local_state_repository import (
    SqliteLocalStateRepository,
)

_START = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)
_NODE_ID_QUERY = "SELECT value FROM node_runtime WHERE key = 'node_id'"


class _FakeClock:
    """Horloge injectable AVANÇABLE : zéro sleep, zéro flakiness (spec §8)."""

    def __init__(self) -> None:
        self.now = _START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    local = open_local(tmp_path / "local.db")
    yield local
    local.close()


@pytest.fixture
def clock() -> _FakeClock:
    return _FakeClock()


@pytest.fixture
def repository(connection: sqlite3.Connection, clock: _FakeClock) -> SqliteLocalStateRepository:
    return SqliteLocalStateRepository(connection, clock=clock)


# --- node_id (spec §3) ---------------------------------------------------------------


def test_node_id_is_created_on_first_call_and_stable(
    repository: SqliteLocalStateRepository, connection: sqlite3.Connection
) -> None:
    created = repository.node_id()
    assert uuid.UUID(created)  # un VRAI UUID, vérifiable
    assert repository.node_id() == created  # stable au second appel
    fresh = SqliteLocalStateRepository(connection)  # et pour toute instance future
    assert fresh.node_id() == created


def test_node_id_persists_created_at_alongside(
    repository: SqliteLocalStateRepository, connection: sqlite3.Connection
) -> None:
    repository.node_id()
    row = connection.execute("SELECT value FROM node_runtime WHERE key = 'created_at'").fetchone()
    assert row == ("2026-06-11T12:00:00.000000+00:00",)


def test_node_id_creation_failure_is_wrapped_and_rolled_back(
    repository: SqliteLocalStateRepository, connection: sqlite3.Connection
) -> None:
    # 'created_at' pré-existant -> l'INSERT à deux lignes viole la PK -> rollback complet.
    connection.execute("INSERT INTO node_runtime (key, value) VALUES ('created_at', 'déjà')")
    with pytest.raises(PersistenceError, match="UNIQUE"):
        repository.node_id()
    assert connection.execute(_NODE_ID_QUERY).fetchone() is None
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/adapters/persistence_sqlite/test_local_state_repository.py -q --no-cov`
Expected: FAIL (collection) — `ModuleNotFoundError: No module named 'emule_indexer.adapters.persistence_sqlite.local_state_repository'`.

- [ ] **Step 3: Écrire l'implémentation (node_id seul)**

`src/emule_indexer/adapters/persistence_sqlite/local_state_repository.py` :
```python
"""``SqliteLocalStateRepository`` : identité du nœud + file de tâches (spec §4/§6).

La file (spec MVP §12) : claim atomique FIFO sous ``BEGIN IMMEDIATE`` + ``RETURNING``
(défense en profondeur — le writer unique est garanti par le déploiement, spec §3),
lease configurable au constructeur, retries bornés → ``dead_letter`` (« poison
probable », le plan E en fera une alerte), enqueue idempotent (l'index UNIQUE partiel
sur les statuts actifs absorbe le doublon : ``ON CONFLICT … DO NOTHING``, vérifié
empiriquement avec cible de conflit explicite, SQLite 3.47.1). ``done``/``dead_letter``
restent en table (historique local, reconstructible — spec §6).

``node_id`` (spec §3) : UUID généré au premier appel, persisté dans ``node_runtime``
avec ``created_at``, stable ensuite (seed du scheduler §6 MVP + tag des observations).
"""

import sqlite3
import uuid
from contextlib import suppress
from datetime import timedelta

from emule_indexer.adapters.persistence_sqlite.connection import Clock, utc_iso, utc_now
from emule_indexer.adapters.persistence_sqlite.errors import wrap_sqlite_errors

_SELECT_NODE_ID = "SELECT value FROM node_runtime WHERE key = 'node_id'"

_INSERT_NODE_IDENTITY = """
INSERT INTO node_runtime (key, value)
VALUES ('node_id', ?), ('created_at', ?)
"""


class SqliteLocalStateRepository:
    """Implémentation SQLite du port ``LocalStateRepository`` (satisfaction STRUCTURELLE)."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        clock: Clock = utc_now,
        lease_duration: timedelta = timedelta(minutes=15),
        max_attempts: int = 3,
    ) -> None:
        self._connection = connection
        self._clock = clock
        self._lease_duration = lease_duration
        self._max_attempts = max_attempts

    def node_id(self) -> str:
        """UUID créé (et persisté avec ``created_at``) au premier appel, stable ensuite."""
        with wrap_sqlite_errors():
            row = self._connection.execute(_SELECT_NODE_ID).fetchone()
            if row is not None:
                return str(row[0])
            generated = str(uuid.uuid4())
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(_INSERT_NODE_IDENTITY, (generated, utc_iso(self._clock())))
                self._connection.execute("COMMIT")
            except sqlite3.Error:
                with suppress(sqlite3.Error):
                    self._connection.execute("ROLLBACK")
                raise
        return generated
```

- [ ] **Step 4: Vérifier le passage puis le gate complet**

Run: `uv run pytest tests/adapters/persistence_sqlite/test_local_state_repository.py -q --no-cov`
Expected: PASS — 3 tests.

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run sqlfluff lint src`
Expected: tout vert — `327 passed, 4 deselected`, coverage 100 %.

- [ ] **Step 5: Commit**

```bash
git add src/emule_indexer/adapters/persistence_sqlite/local_state_repository.py tests/adapters/persistence_sqlite/test_local_state_repository.py
git commit -m "feat(adapters): SqliteLocalStateRepository.node_id (UUID persisté au premier appel)"
```

---

## Task 9: File — enqueue idempotent + claim FIFO atomique

**Files:**
- Modify: `src/emule_indexer/adapters/persistence_sqlite/local_state_repository.py`
- Modify: `tests/adapters/persistence_sqlite/test_local_state_repository.py`

> Le cœur de §12 : l'index UNIQUE partiel absorbe le doublon ACTIF (DÉCISION 9) ; le claim est `BEGIN IMMEDIATE` + `UPDATE … RETURNING` sur la plus ancienne `pending` (vérifications n° 5-6). L'atomicité est PROUVÉE par deux connexions réelles sur le même fichier.

- [ ] **Step 1: Étendre les tests (qui échouent)**

Dans `tests/adapters/persistence_sqlite/test_local_state_repository.py`, remplacer le bloc d'import :
```python
from emule_indexer.adapters.persistence_sqlite.local_state_repository import (
    SqliteLocalStateRepository,
)
```
par :
```python
from emule_indexer.adapters.persistence_sqlite.local_state_repository import (
    SqliteLocalStateRepository,
)
from emule_indexer.ports.local_state_repository import ClaimedTask
```

puis ajouter À LA FIN du fichier :
```python
# --- enqueue idempotent (spec §6 : l'index UNIQUE partiel absorbe le doublon actif) ----


def test_enqueue_returns_true_then_false_while_pending(
    repository: SqliteLocalStateRepository,
) -> None:
    assert repository.enqueue_verification("aaaa") is True
    assert repository.enqueue_verification("aaaa") is False  # déjà active -> absorbé


def test_enqueue_is_still_refused_while_in_progress(
    repository: SqliteLocalStateRepository,
) -> None:
    repository.enqueue_verification("aaaa")
    claimed = repository.claim_verification()
    assert claimed is not None
    assert repository.enqueue_verification("aaaa") is False  # in_progress est ACTIF aussi


# --- claim atomique FIFO (spec §6) -----------------------------------------------------


def test_claim_is_fifo_by_enqueue_time(
    repository: SqliteLocalStateRepository, clock: _FakeClock
) -> None:
    repository.enqueue_verification("premier")
    clock.advance(timedelta(seconds=1))
    repository.enqueue_verification("second")
    first = repository.claim_verification()
    second = repository.claim_verification()
    assert first == ClaimedTask(task_id=1, ed2k_hash="premier", attempts=1)
    assert second == ClaimedTask(task_id=2, ed2k_hash="second", attempts=1)


def test_claim_breaks_enqueue_time_ties_by_id(
    repository: SqliteLocalStateRepository,
) -> None:
    # Horloge GELÉE : même enqueued_at -> départage déterministe par id croissant.
    repository.enqueue_verification("a")
    repository.enqueue_verification("b")
    first = repository.claim_verification()
    assert first is not None
    assert first.ed2k_hash == "a"


def test_claim_on_empty_queue_returns_none(repository: SqliteLocalStateRepository) -> None:
    assert repository.claim_verification() is None


def test_claim_stamps_lease_and_marks_in_progress(
    connection: sqlite3.Connection, clock: _FakeClock
) -> None:
    repository = SqliteLocalStateRepository(
        connection, clock=clock, lease_duration=timedelta(minutes=5)
    )
    repository.enqueue_verification("aaaa")
    repository.claim_verification()
    row = connection.execute(
        "SELECT status, claimed_at, lease_until FROM verification_tasks"
    ).fetchone()
    assert row == (
        "in_progress",
        "2026-06-11T12:00:00.000000+00:00",
        "2026-06-11T12:05:00.000000+00:00",  # now + lease_duration (constructeur)
    )


def test_two_connections_claim_distinct_tasks(tmp_path: Path, clock: _FakeClock) -> None:
    # Atomicité PROUVÉE : deux connexions distinctes ne prennent JAMAIS la même tâche.
    path = tmp_path / "local.db"
    first_connection = open_local(path)
    second_connection = open_local(path)
    try:
        producer = SqliteLocalStateRepository(first_connection, clock=clock)
        producer.enqueue_verification("t1")
        clock.advance(timedelta(seconds=1))
        producer.enqueue_verification("t2")
        consumer = SqliteLocalStateRepository(second_connection, clock=clock)
        first = producer.claim_verification()
        second = consumer.claim_verification()
        assert first is not None
        assert second is not None
        assert {first.ed2k_hash, second.ed2k_hash} == {"t1", "t2"}
    finally:
        first_connection.close()
        second_connection.close()


def test_claim_failure_is_wrapped_and_rolled_back(
    repository: SqliteLocalStateRepository, connection: sqlite3.Connection
) -> None:
    repository.enqueue_verification("aaaa")
    connection.execute(
        "CREATE TRIGGER boom BEFORE UPDATE ON verification_tasks"
        " BEGIN SELECT RAISE(ABORT, 'panne injectée'); END"
    )
    with pytest.raises(PersistenceError, match="panne injectée"):
        repository.claim_verification()
    status = connection.execute("SELECT status FROM verification_tasks").fetchone()[0]
    assert status == "pending"  # la transaction du claim a été défaite
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/adapters/persistence_sqlite/test_local_state_repository.py -q --no-cov`
Expected: FAIL — `AttributeError: 'SqliteLocalStateRepository' object has no attribute 'enqueue_verification'` (8 nouveaux tests en échec).

- [ ] **Step 3: Ajouter les deux méthodes**

Dans `src/emule_indexer/adapters/persistence_sqlite/local_state_repository.py`, remplacer la ligne d'import :
```python
from emule_indexer.adapters.persistence_sqlite.errors import wrap_sqlite_errors
```
par :
```python
from emule_indexer.adapters.persistence_sqlite.errors import wrap_sqlite_errors
from emule_indexer.ports.local_state_repository import ClaimedTask
```

insérer, juste APRÈS le bloc `_INSERT_NODE_IDENTITY = """…"""` (et sa ligne `"""` de fermeture) :
```python

_ENQUEUE = """
INSERT INTO verification_tasks (ed2k_hash, status, enqueued_at)
VALUES (?, 'pending', ?)
ON CONFLICT (ed2k_hash) WHERE status IN ('pending', 'in_progress') DO NOTHING
"""

_CLAIM = """
UPDATE verification_tasks
SET
    status = 'in_progress',
    claimed_at = :now,
    lease_until = :lease,
    attempts = attempts + 1
WHERE id = (
    SELECT id FROM verification_tasks
    WHERE status = 'pending'
    ORDER BY enqueued_at, id
    LIMIT 1
)
RETURNING id, ed2k_hash, attempts
"""
```

et ajouter À LA FIN de la classe (après `node_id`) :
```python

    def enqueue_verification(self, ed2k_hash: str) -> bool:
        """``True`` si une tâche a été créée ; ``False`` si une tâche ACTIVE existait déjà."""
        with wrap_sqlite_errors():
            cursor = self._connection.execute(_ENQUEUE, (ed2k_hash, utc_iso(self._clock())))
        return cursor.rowcount == 1

    def claim_verification(self) -> ClaimedTask | None:
        """Claim atomique FIFO (``BEGIN IMMEDIATE`` + ``RETURNING``) ; file vide → ``None``.

        FIFO = ``ORDER BY enqueued_at, id`` (l'ISO UTC à largeur fixe rend l'ordre
        lexicographique chronologique ; ``id`` départage les égalités d'horloge).
        ``attempts`` est compté AU CLAIM (spec §6).
        """
        now = self._clock()
        parameters = {"now": utc_iso(now), "lease": utc_iso(now + self._lease_duration)}
        with wrap_sqlite_errors():
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(_CLAIM, parameters).fetchone()
                self._connection.execute("COMMIT")
            except sqlite3.Error:
                with suppress(sqlite3.Error):
                    self._connection.execute("ROLLBACK")
                raise
        if row is None:
            return None
        return ClaimedTask(task_id=row[0], ed2k_hash=row[1], attempts=row[2])
```

- [ ] **Step 4: Vérifier le passage puis le gate complet**

Run: `uv run pytest tests/adapters/persistence_sqlite/test_local_state_repository.py -q --no-cov`
Expected: PASS — 11 tests.

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run sqlfluff lint src`
Expected: tout vert — `335 passed, 4 deselected`, coverage 100 %.

- [ ] **Step 5: Commit**

```bash
git add src/emule_indexer/adapters/persistence_sqlite/local_state_repository.py tests/adapters/persistence_sqlite/test_local_state_repository.py
git commit -m "feat(adapters): file de vérification — enqueue idempotent + claim FIFO atomique"
```

---

## Task 10: File — complete/fail, retries bornés → dead-letter

**Files:**
- Modify: `src/emule_indexer/adapters/persistence_sqlite/local_state_repository.py`
- Modify: `tests/adapters/persistence_sqlite/test_local_state_repository.py`

- [ ] **Step 1: Étendre les tests (qui échouent)**

Ajouter À LA FIN de `tests/adapters/persistence_sqlite/test_local_state_repository.py` :
```python
# --- complete / fail / dead-letter (spec §6) -------------------------------------------


def test_complete_marks_done_and_keeps_the_row(
    repository: SqliteLocalStateRepository, connection: sqlite3.Connection
) -> None:
    repository.enqueue_verification("aaaa")
    claimed = repository.claim_verification()
    assert claimed is not None
    repository.complete_verification(claimed.task_id)
    # done RESTE en table (historique local, spec §6).
    assert connection.execute("SELECT status FROM verification_tasks").fetchone() == ("done",)


def test_complete_requires_an_in_progress_task(
    repository: SqliteLocalStateRepository,
) -> None:
    with pytest.raises(PersistenceError, match="introuvable"):
        repository.complete_verification(42)  # id inconnu
    repository.enqueue_verification("aaaa")
    claimed = repository.claim_verification()
    assert claimed is not None
    repository.complete_verification(claimed.task_id)
    with pytest.raises(PersistenceError, match="introuvable"):
        repository.complete_verification(claimed.task_id)  # déjà done : bug appelant


def test_fail_below_max_attempts_requeues_as_pending(
    repository: SqliteLocalStateRepository, connection: sqlite3.Connection
) -> None:
    repository.enqueue_verification("aaaa")
    claimed = repository.claim_verification()
    assert claimed is not None
    repository.fail_verification(claimed.task_id)
    row = connection.execute(
        "SELECT status, attempts, claimed_at, lease_until FROM verification_tasks"
    ).fetchone()
    assert row == ("pending", 1, None, None)  # attempts CONSERVÉ, lease nettoyée


def test_fail_at_max_attempts_dead_letters(
    connection: sqlite3.Connection, clock: _FakeClock
) -> None:
    repository = SqliteLocalStateRepository(connection, clock=clock, max_attempts=2)
    repository.enqueue_verification("aaaa")
    first = repository.claim_verification()
    assert first is not None
    assert first.attempts == 1
    repository.fail_verification(first.task_id)  # 1 < 2 -> pending
    second = repository.claim_verification()
    assert second is not None
    assert second.attempts == 2
    repository.fail_verification(second.task_id)  # 2 >= 2 -> dead_letter (poison probable)
    status = connection.execute("SELECT status FROM verification_tasks").fetchone()[0]
    assert status == "dead_letter"
    assert repository.claim_verification() is None  # une dead_letter n'est JAMAIS reprise


def test_default_max_attempts_is_three(repository: SqliteLocalStateRepository) -> None:
    repository.enqueue_verification("aaaa")
    for expected_attempts in (1, 2, 3):
        claimed = repository.claim_verification()
        assert claimed is not None
        assert claimed.attempts == expected_attempts
        repository.fail_verification(claimed.task_id)
    assert repository.claim_verification() is None  # dead_letter au 3e échec (défaut)


def test_fail_requires_an_in_progress_task(repository: SqliteLocalStateRepository) -> None:
    with pytest.raises(PersistenceError, match="introuvable"):
        repository.fail_verification(42)


def test_enqueue_is_allowed_again_once_the_task_is_done(
    repository: SqliteLocalStateRepository,
) -> None:
    repository.enqueue_verification("aaaa")
    claimed = repository.claim_verification()
    assert claimed is not None
    repository.complete_verification(claimed.task_id)
    assert repository.enqueue_verification("aaaa") is True  # done n'est PLUS actif
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/adapters/persistence_sqlite/test_local_state_repository.py -q --no-cov`
Expected: FAIL — `AttributeError: 'SqliteLocalStateRepository' object has no attribute 'complete_verification'` (7 nouveaux tests en échec).

- [ ] **Step 3: Ajouter les deux méthodes**

Dans `src/emule_indexer/adapters/persistence_sqlite/local_state_repository.py`, remplacer la ligne d'import :
```python
from emule_indexer.adapters.persistence_sqlite.errors import wrap_sqlite_errors
```
par :
```python
from emule_indexer.adapters.persistence_sqlite.errors import (
    PersistenceError,
    wrap_sqlite_errors,
)
```

insérer, juste APRÈS le bloc `_CLAIM = """…"""` (et sa ligne `"""` de fermeture) :
```python

_COMPLETE = "UPDATE verification_tasks SET status = 'done' WHERE id = ? AND status = 'in_progress'"

_FAIL = """
UPDATE verification_tasks
SET
    status = CASE WHEN attempts >= :max_attempts THEN 'dead_letter' ELSE 'pending' END,
    claimed_at = NULL,
    lease_until = NULL
WHERE id = :task_id AND status = 'in_progress'
"""
```

et ajouter À LA FIN de la classe (après `claim_verification`) :
```python

    def complete_verification(self, task_id: int) -> None:
        """Marque ``done`` (la ligne RESTE : historique local). Exige une tâche ``in_progress``."""
        with wrap_sqlite_errors():
            cursor = self._connection.execute(_COMPLETE, (task_id,))
        if cursor.rowcount != 1:
            raise PersistenceError(
                f"tâche {task_id} introuvable en in_progress (bug du code appelant)"
            )

    def fail_verification(self, task_id: int) -> None:
        """Repasse en ``pending``, sauf ``attempts >= max_attempts`` → ``dead_letter`` (§12)."""
        with wrap_sqlite_errors():
            cursor = self._connection.execute(
                _FAIL, {"max_attempts": self._max_attempts, "task_id": task_id}
            )
        if cursor.rowcount != 1:
            raise PersistenceError(
                f"tâche {task_id} introuvable en in_progress (bug du code appelant)"
            )
```

- [ ] **Step 4: Vérifier le passage puis le gate complet**

Run: `uv run pytest tests/adapters/persistence_sqlite/test_local_state_repository.py -q --no-cov`
Expected: PASS — 18 tests.

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run sqlfluff lint src`
Expected: tout vert — `342 passed, 4 deselected`, coverage 100 %.

- [ ] **Step 5: Commit**

```bash
git add src/emule_indexer/adapters/persistence_sqlite/local_state_repository.py tests/adapters/persistence_sqlite/test_local_state_repository.py
git commit -m "feat(adapters): file de vérification — complete/fail, retries bornés vers dead_letter"
```

---

## Task 11: File — `reclaim_expired` + satisfaction du port

**Files:**
- Modify: `src/emule_indexer/adapters/persistence_sqlite/local_state_repository.py`
- Modify: `tests/adapters/persistence_sqlite/test_local_state_repository.py`

> La lease expirée se teste à l'HORLOGE AVANCÉE (`clock.advance`), jamais au sleep (spec §8). Le test de satisfaction du port arrive ICI : c'est seulement maintenant que `SqliteLocalStateRepository` porte les six méthodes — mypy prouve la conformité structurelle au moment où elle devient vraie.

- [ ] **Step 1: Étendre les tests (qui échouent)**

Dans `tests/adapters/persistence_sqlite/test_local_state_repository.py`, remplacer la ligne d'import :
```python
from emule_indexer.ports.local_state_repository import ClaimedTask
```
par :
```python
from emule_indexer.ports.local_state_repository import ClaimedTask, LocalStateRepository
```

puis ajouter À LA FIN du fichier :
```python
# --- lease / reclaim (spec §6) ---------------------------------------------------------


def test_reclaim_expired_requeues_only_expired_leases(
    connection: sqlite3.Connection, clock: _FakeClock
) -> None:
    repository = SqliteLocalStateRepository(
        connection, clock=clock, lease_duration=timedelta(minutes=15)
    )
    repository.enqueue_verification("expirée")
    repository.claim_verification()  # lease jusqu'à 12:15
    clock.advance(timedelta(minutes=10))
    repository.enqueue_verification("fraîche")
    repository.claim_verification()  # lease jusqu'à 12:25
    clock.advance(timedelta(minutes=6))  # 12:16 : la 1re a expiré, pas la 2e
    assert repository.reclaim_expired() == 1
    rows = dict(connection.execute("SELECT ed2k_hash, status FROM verification_tasks").fetchall())
    assert rows == {"expirée": "pending", "fraîche": "in_progress"}
    reclaimed = repository.claim_verification()
    assert reclaimed is not None
    assert reclaimed.ed2k_hash == "expirée"
    assert reclaimed.attempts == 2  # attempts compté AU CLAIM, le re-claim compte


def test_reclaim_with_nothing_expired_returns_zero(
    repository: SqliteLocalStateRepository,
) -> None:
    repository.enqueue_verification("aaaa")
    repository.claim_verification()
    assert repository.reclaim_expired() == 0  # lease encore valide : rien à récupérer


def test_reclaim_ignores_done_and_dead_letter(
    connection: sqlite3.Connection, clock: _FakeClock
) -> None:
    repository = SqliteLocalStateRepository(connection, clock=clock, max_attempts=1)
    repository.enqueue_verification("finie")
    done = repository.claim_verification()
    assert done is not None
    repository.complete_verification(done.task_id)
    repository.enqueue_verification("poison")
    poisoned = repository.claim_verification()
    assert poisoned is not None
    repository.fail_verification(poisoned.task_id)  # max_attempts=1 -> dead_letter direct
    clock.advance(timedelta(days=1))  # toutes les leases seraient expirées depuis longtemps
    assert repository.reclaim_expired() == 0


def test_repository_satisfies_the_port_structurally(
    repository: SqliteLocalStateRepository,
) -> None:
    port: LocalStateRepository = repository  # mypy prouve la satisfaction structurelle
    assert port.claim_verification() is None
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/adapters/persistence_sqlite/test_local_state_repository.py -q --no-cov`
Expected: FAIL — `AttributeError: 'SqliteLocalStateRepository' object has no attribute 'reclaim_expired'` (4 nouveaux tests en échec).

- [ ] **Step 3: Ajouter la méthode**

Dans `src/emule_indexer/adapters/persistence_sqlite/local_state_repository.py`, insérer juste APRÈS le bloc `_FAIL = """…"""` (et sa ligne `"""` de fermeture) :
```python

_RECLAIM = """
UPDATE verification_tasks
SET status = 'pending', claimed_at = NULL, lease_until = NULL
WHERE status = 'in_progress' AND lease_until < ?
"""
```

et ajouter À LA FIN de la classe (après `fail_verification`) :
```python

    def reclaim_expired(self) -> int:
        """Repasse en ``pending`` toute ``in_progress`` dont la lease a expiré ; rend le nombre."""
        with wrap_sqlite_errors():
            cursor = self._connection.execute(_RECLAIM, (utc_iso(self._clock()),))
        return cursor.rowcount
```

- [ ] **Step 4: Vérifier le passage puis le gate complet**

Run: `uv run pytest tests/adapters/persistence_sqlite/test_local_state_repository.py -q --no-cov`
Expected: PASS — 22 tests.

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run sqlfluff lint src`
Expected: tout vert — `346 passed, 4 deselected`, coverage 100 %.

- [ ] **Step 5: Commit**

```bash
git add src/emule_indexer/adapters/persistence_sqlite/local_state_repository.py tests/adapters/persistence_sqlite/test_local_state_repository.py
git commit -m "feat(adapters): file de vérification — reclaim des leases expirées ; port satisfait"
```

---

## Task 12: Revue holistique finale + tag `v0.6.0-data-model`

**Files:** (aucun fichier nouveau ; corrections éventuelles issues de la revue, puis tag git)

> La revue finale holistique a attrapé de vrais bugs transverses à chaque plan précédent — la garder (CLAUDE.md). Dérouler la checklist ; toute trouvaille = correction TDD (test d'abord) AVANT le tag.

- [ ] **Step 1: Vérifier la règle de dépendance (Clean Architecture)**

> La liste blanche du grep n° 1 intègre la leçon du plan 03 (handoff §5) : les dépendances PUR-CALCUL du moteur (`re2`, `rapidfuzz`, `re`) sont blanchies — elles font partie du domaine depuis `v0.4.0`, le grep du plan 03 les oubliait.

```bash
# 1. Le domaine reste PUR (stdlib « pure », deps pur-calcul du moteur, et le domaine lui-même) :
grep -rnE "^(import|from) " src/emule_indexer/domain --include="*.py" \
  | grep -vE "from (dataclasses|typing|collections\.abc|enum|rapidfuzz) import|import (datetime|unicodedata|re2|re as _re)|from emule_indexer\.domain"
# Expected: AUCUNE sortie. En particulier : AUCUN sqlite3/json/uuid n'est entré dans domain/ avec ce plan.

# 2. ports/ n'importe QUE le domaine (+ stdlib de typage) :
grep -rnE "^(import|from) " src/emule_indexer/ports --include="*.py" \
  | grep -vE "from (dataclasses|typing|enum) import|from emule_indexer\.domain"
# Expected: AUCUNE sortie.

# 3. Personne ne voit l'adapter persistence hors de lui-même (le plan C câblera par les PORTS) :
grep -rn "persistence_sqlite" src/emule_indexer --include="*.py" \
  | grep -v "adapters/persistence_sqlite/"
# Expected: AUCUNE sortie.

# 4. Le domaine n'importe ni ports ni adapters :
grep -rn "emule_indexer.ports\|emule_indexer.adapters" src/emule_indexer/domain --include="*.py"
# Expected: AUCUNE sortie.

# 5. L'adapter persistence est STRICTEMENT synchrone (spec §3) :
grep -rn "asyncio\|await \|async def" src/emule_indexer/adapters/persistence_sqlite --include="*.py"
# Expected: AUCUNE sortie.
```

- [ ] **Step 2: Checklist de cohérence transverse (lire le code, pas survoler)**

- [ ] Les colonnes de `file_observations` couvrent EXACTEMENT les champs de `FileObservation` (relire `src/emule_indexer/domain/observation.py`) + les 3 colonnes d'adapter (`observed_at`, `node_id`, `id`) — et la taille OBSERVÉE y est bien (déviation 1, spec §5).
- [ ] `match_decisions` porte EXACTEMENT les 3 colonnes de `MatchDecision` (`target_id`, `rule_name`, `tier`) + `ed2k_hash`/`decided_at`/`node_id` — `explanation` n'apparaît NULLE PART (docstring du moteur §11, test `test_explanation_is_never_persisted`).
- [ ] `FileObservation`/`MatchDecision` n'ont toujours NI `observed_at`/`decided_at` NI `node_id` : le domaine n'a pas changé d'une ligne dans ce plan (`git diff v0.5.0-ec-adapter -- src/emule_indexer/domain` doit être VIDE).
- [ ] Tous les timestamps écrits passent par `utc_iso` (grep `isoformat` dans `adapters/persistence_sqlite/` : UNE seule occurrence, dans `connection.py`).
- [ ] `BEGIN IMMEDIATE` n'apparaît qu'au claim et à la création du `node_id` ; `record_observation` utilise `BEGIN` simple (DÉCISION 6).
- [ ] Les scripts de migration ne contiennent AUCUN `COMMIT`/`ROLLBACK` (le runner enveloppe — les `BEGIN…END` de triggers sont du DDL) : `grep -niE "^(commit|rollback)" src/emule_indexer/adapters/persistence_sqlite/migrations -r` ne sort RIEN.
- [ ] Toute `sqlite3.Error` qui sort de l'adapter est enveloppée : chaque méthode publique de repository et `open_*` passe par `wrap_sqlite_errors` (relire les `with` de `catalog_repository.py`/`local_state_repository.py`/`connection.py`).
- [ ] Le wheel embarque les migrations : `uv build` puis `unzip -l dist/emule_indexer-0.0.0-py3-none-any.whl | grep -c '\.sql'` → `2` (puis `rm -rf dist`).
- [ ] Les docstrings citent leur source (`spec §N`, vérification empirique) pour chaque fait non évident (user_version transactionnel, :memory:, cible de conflit partielle).

- [ ] **Step 3: Gate complet + intégration EC (les DEUX, verts, non négociable avant un tag)**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run sqlfluff lint src`
Expected: tout vert ; `346 passed, 4 deselected` ; coverage **100 % branch**.

Run: `uv run pytest -m ec_integration --no-cov -q`
Expected: `4 passed` (Docker requis) — ce plan ne touche pas l'adapter EC, mais le dépôt tague TOUJOURS sur intégration verte (convention v0.5.0).

- [ ] **Step 4: Tag annoté (NON poussé)**

```bash
git tag -a v0.6.0-data-model -m "Modèle de données : schémas catalog/local migrés et lintés, append-only par triggers, SqliteCatalogRepository (observations + décisions) et SqliteLocalStateRepository (node_id + file de vérification complète)"
git tag -n1 | grep v0.6.0
```
Expected: le tag apparaît avec son message. Ne PAS pousser (convention du dépôt).

---

## Self-Review (rempli par l'auteur du plan)

- **Couverture de la spec (`2026-06-11-data-model-design.md`) :**
  - **§2 — schéma COMPLET des dix tables, migré et versionné** → Task 2 (6 tables catalogue + 4 locales, index, triggers ; clés de fusion figées) + Task 4 (runner `user_version`). Les tables SANS repository (sources, source_observations, downloads, file_verifications, scheduler_state) existent et sont testées structurellement (création Task 4, append-only Task 5) — leurs repos sont HORS SCOPE comme exigé. ✓
  - **§2 — repositories partiels (producteurs d'aujourd'hui seulement)** → Tasks 6-7 (`record_observation`/`record_decision`) et 8-11 (`node_id` + file complète enqueue/claim/complete/fail/reclaim). Aucune méthode au-delà des ports spécifiés. ✓
  - **§2 — runner de migrations, PRAGMA, hiérarchie d'erreurs ; outillage sqlfluff** → Tasks 4, 1-2. ✓
  - **§3 — sqlite3 stdlib, sync, SQL à la main** → aucun ORM/aiosqlite nulle part ; grep « strictement synchrone » en Task 12. ✓
  - **§3 — migrations .sql embarquées, importlib.resources, transaction par script, refus base plus récente** → Task 4 (DÉCISION 2 ; tests : idempotence, user_version=99 refusé, rollback version inchangée). ✓
  - **§3 — sqlfluff au gate (pre-push + CI)** → Tasks 1-2 (5 checks pre-push + CI + CLAUDE.md, édition minimale). ✓
  - **§3 — node_id UUID premier démarrage, persisté node_runtime** → Task 8 (DÉCISION 7 ; stabilité inter-instances testée). La surcharge par config est « plus tard » (spec) — non implémentée, comme écrit. ✓
  - **§3 — l'adapter stamppe (horloge injectable, défaut now(UTC)), le domaine ne change pas** → Tasks 6-8 (constructeurs `clock=` ; vérif « domaine intact » en Task 12). ✓
  - **§3 — timestamps ISO-8601 UTC TEXT** → DÉCISION 4 (largeur fixe JUSTIFIÉE par le tri FIFO ; normalisation +02:00 testée). ✓
  - **§3 — raw_meta JSON LISTE de paires, ordre + doublons, ensure_ascii=False** → Task 6 (round-trip avec doublon + accent ; chaîne JSON exacte assertée). ✓
  - **§3 — append-only PAR LA BASE (triggers posés par la migration initiale)** → Task 2 (12 triggers) + Task 5 (UPDATE/DELETE directs sur les SIX tables : la propriété tient face à un outil tiers). ✓
  - **§3 — PRAGMA WAL + foreign_keys par connexion** → Task 4 (testés ; FK violée testée ; `:memory:` refusé — DÉCISION 3). ✓
  - **§3 — writer unique garanti par le déploiement, BEGIN IMMEDIATE au claim en défense** → Task 9 (claim) ; le code ne « vérifie » rien de plus, comme exigé. ✓
  - **§4 — fichiers EXACTS de l'arborescence** → tous créés, AUCUN fichier ajouté hors liste (l'horloge partagée vit dans `connection.py` — DÉCISION 4 — précisément pour ne pas étendre la liste). ✓
  - **§4 — signatures des ports + ClaimedTask(task_id, ed2k_hash, attempts)** → Task 3, signatures identiques à la spec, stubs une-ligne. ✓
  - **§4 — record_observation = UNE transaction (INSERT OR IGNORE files puis INSERT observation)** → Task 6 (atomicité PROUVÉE par panne injectée : rollback des DEUX inserts). ✓
  - **§5 — DDL complet, y compris les 2 déviations (size_bytes observé, bitrate_kbps)** → Task 2 (colonnes au cordeau) ; déviation 1 TESTÉE (Task 6 : l'anomalie de taille reste visible). ✓
  - **§6 — claim atomique FIFO (requête de la spec au mot près), lease constructeur, attempts AU CLAIM, dead-letter, enqueue idempotent ON CONFLICT, done/dead_letter restent en table** → Tasks 9-11 (FIFO + égalité départagée par id ; 2 connexions ; lease exacte ; attempts=2 au re-claim ; CASE atomique ; rows conservées). ✓
  - **§7 — PersistenceError/MigrationError, jamais de sqlite3.Error nue, triggers = PersistenceError, fail-fast à l'ouverture** → Tasks 4-11 (enveloppe unique DÉCISION 5 ; chaque chemin d'erreur testé). ✓
  - **§8 — stratégie de tests, point par point** → fichiers réels tmp_path (partout) ; migrations from-scratch/idempotence/refus/rollback (Task 4) ; append-only + 2×record_observation→1 files/2 observations (Tasks 5-6) ; round-trips complets stampés (Tasks 6-7) ; file : FIFO, 2 connexions, lease à horloge avancée, dead-letter, enqueue False, file vide None (Tasks 9-11) ; sqlfluff vert + gate 5 checks (Tasks 1-2). ✓
  - **§9 — definition of done 1-5** → 1 : Tasks 2+4+5 ; 2 : Task 3 ; 3 : Tasks 6-11 ; 4 : Task 2 ; 5 : Task 12. ✓
  - **§10 — questions laissées au plan** : DDL exact (Task 2), config sqlfluff fine (Task 1, DÉCISION 10), détail du runner (DÉCISION 2), forme du claim + version SQLite vérifiée (3.47.1 ≥ 3.35, vérifications n° 1/6). ✓
- **Scan des placeholders :** aucun « TBD », aucun « similaire à la Task N », aucun « ajouter la gestion d'erreurs ». Chaque step porte le code COMPLET ou une instruction d'insertion EXACTE (ancre citée + contenu intégral) ; chaque run a sa commande et sa sortie attendue (compte de tests inclus) ; chaque tâche se clôt par un commit exact.
- **Séquencement & gate 100 % :** le point de friction classique (un fichier dont une méthode n'est testée qu'à la tâche suivante → commit sous 100 %) est résolu STRUCTURELLEMENT : les repositories sont écrits méthode par méthode (catalog : Tasks 6→7 ; local : Tasks 8→9→10→11), et l'enchaînement ENTIER a été REJOUÉ dans le bac à sable — chaque état intermédiaire passe les 5 checks (288/302/315/320/324/327/335/342/346 tests, 100.00 % branch à chaque palier, ruff/format/mypy/sqlfluff verts). Les instructions d'insertion des Tasks 7/9/10/11 ont été vérifiées par RÉASSEMBLAGE OCTET PAR OCTET (la concaténation des fragments reproduit exactement les fichiers finaux testés).
- **Tout fait SQL/API est vérifié empiriquement** (section « Vérifications empiriques », valeurs observées datées du venv RÉEL du projet) : RETURNING, cible de conflit partielle, user_version transactionnel, classe d'exception des triggers, :memory: sans WAL, sqlfluff 4.2.2 sur NOS fichiers, importlib.resources + wheel hatchling. Aucune valeur « de mémoire ».
- **Décisions prises là où la spec laissait ouvert (à relire en revue) :** DÉCISION 1 (autocommit réel + rollback best-effort), 2 (enveloppe transactionnelle du runner + format NNNN_*.sql + non-.sql ignoré/préfixe invalide fatal), 3 (:memory: refusé), 4 (horloge dans connection.py ; microsecondes fixes ; normalisation astimezone), 6 (node_id paramètre du constructeur catalogue ; aich_hash NULL ; BEGIN simple ; FK violée = PersistenceError), 7 (uuid4 non injecté ; created_at stocké), 8 (complete/fail exigent in_progress sinon PersistenceError ; défauts lease 15 min / max_attempts 3), 9 (cible de conflit explicite), 10 (config sqlfluff : ligne 100, ignore_words key,value ; cible `src`), 11 (pannes par triggers de test), 12 (ordre des tâches ; SQL avant runner).
- **Argument gate coverage :** chaque module nouveau a ses deux côtés de branche listés dans la « Note couverture » d'en-tête et exercés par les tests nommés ; AUCUN pragma nouveau ; les `__init__.py` à docstring seule pèsent 0 statement. Les tests d'intégration EC restent déselectionnés et hors coverage.

### OPEN QUESTIONS FOR THE HUMAN

Aucune bloquante. Trois choix à confirmer en revue si désaccord : (1) **défauts de la file** — lease 15 min / `max_attempts` 3 (DÉCISION 8) : changer deux valeurs par défaut + 2 tests si le plan D préfère autre chose ; (2) **`complete`/`fail` stricts** (PersistenceError sur tâche non `in_progress`) — l'alternative « no-op silencieux » masquerait les courses de lease, mais c'est un choix de contrat ; (3) **`node_id` paramètre du constructeur** de `SqliteCatalogRepository` (et non lu de `local.db` par l'adapter catalogue) — c'est le plan C qui fera la plomberie `local_state.node_id()` → `SqliteCatalogRepository(...)`.
