# Spec — emule-indexer : Modèle de données (Plan A)

> Sous-projet du MVP crawler (voir `2026-06-10-crawler-mvp-design.md`, §11–§12, §14, §16).
> Validé avec Geoffrey le 2026-06-11. Jalon visé : `v0.6.0-data-model`.
> Éclairé par le rapport `docs/reference/2026-06-11-ec-field-richness.md` (EC n'expose
> aucune métadonnée média → colonnes `media_*` nullables, `raw_meta` indispensable).

## 1. Contexte & objectif

Le moteur de matching (`v0.4.0`) décide, l'adapter EC (`v0.5.0`) observe — mais rien ne
persiste. Ce sous-projet construit la **mémoire** du crawler : les deux bases SQLite du
spec MVP §11/§12, leurs migrations, et les repositories pour ce qui a un producteur
aujourd'hui. `FileObservation` et `MatchDecision` deviennent des lignes durables,
fusionnables entre chercheurs.

## 2. Périmètre

**Dans le scope :**
- **Schéma COMPLET des deux bases** (les dix tables §11/§12), migré et versionné — les
  clés de fusion sont figées une fois pour toutes ; les plans C/D ajouteront leurs repos
  sur des tables déjà en place, sans migration.
- **Repositories PARTIELS** (seulement ce qui a un producteur aujourd'hui) :
  - `CatalogRepository` : `record_observation(FileObservation)`,
    `record_decision(ed2k_hash, MatchDecision)` ;
  - `LocalStateRepository` : identité du nœud (`node_id`), et la **file de tâches**
    complète de §12 (enqueue/claim/complete/fail/reclaim) — testable sur SQLite réel
    sans réseau, consommée au plan D.
- Runner de migrations (`PRAGMA user_version`), PRAGMA d'ouverture, hiérarchie d'erreurs.
- Outillage **sqlfluff** (lint SQL au gate).

**Hors scope (différé) :**
- L'**outil de fusion** multi-chercheurs (spec MVP §17 : sous-projet). Le schéma est
  *prêt à fusionner* (clés UNION-safe, `node_id` sur toute observation) ; la mécanique
  de fusion n'est pas écrite. Dédup à la fusion = égalité ligne entière hors `id` local
  (documenté ici, mécanisé plus tard).
- Repos de `sources`/`source_observations`/`downloads`/`file_verifications` (plan D),
  structuration de `scheduler_state` (plan C), use-cases applicatifs (RecordObservations
  etc. — plan C les câblera sur ces ports).
- Compaction/rétention (spec MVP §17 : tout garder en MVP).

## 3. Décisions verrouillées

- **`sqlite3` stdlib, repositories SYNCHRONES, SQL à la main** (pas d'ORM, pas
  d'`aiosqlite`). Une écriture locale est sub-milliseconde ; si le plan C veut s'isoler,
  il enveloppera dans `asyncio.to_thread` sans toucher cette couche.
- **Migrations = fichiers `.sql` numérotés embarqués dans le paquet**, lus via
  `importlib.resources`, appliqués chacun dans sa transaction ; `PRAGMA user_version`
  trace l'état ; une base PLUS RÉCENTE que le code → refus net (`MigrationError`).
  **sqlfluff** (dialecte `sqlite`, config `[tool.sqlfluff.core]` dans `pyproject.toml`)
  linte ces fichiers ; `uv run sqlfluff lint` rejoint le gate (pre-push + CI).
- **`node_id`** : UUID généré au premier démarrage, persisté dans `node_runtime`
  (`local.db`), surchargeable par config plus tard. Stable pour le seed du scheduler
  (§6 MVP) et le tag des observations.
- **L'adapter stamppe ce que le domaine ignore** : `observed_at`/`decided_at` (horloge
  **injectable** au constructeur, `datetime.now(UTC)` par défaut) et `node_id`. Le
  domaine ne change pas (même principe que `MatchDecision` : pas de colonnes de
  persistance côté domaine).
- **Timestamps ISO-8601 UTC en TEXT** (lisibles, triables, fusionnables).
- **`raw_meta` en JSON TEXT = LISTE de paires** `[["0x0308", "0"], …]` — pas un objet :
  l'ordre du fil et les doublons (légitimes depuis le mapping v0.5.0) sont préservés.
  `json.dumps(..., ensure_ascii=False)`, pas de tri.
- **Append-only IMPOSÉ PAR LA BASE** : triggers SQLite `BEFORE UPDATE`/`BEFORE DELETE`
  → `RAISE(ABORT, …)` sur toutes les tables de `catalog.db`, posés par la migration
  initiale. Propriété de la base, pas convention de code — tient face à un outil tiers.
- **PRAGMA d'ouverture** (chaque connexion) : `journal_mode=WAL`, `foreign_keys=ON`,
  `recursive_triggers=ON` (sans quoi `INSERT OR REPLACE` traverse les triggers
  append-only).
- **Writer unique = le crawler** (invariant MVP §11) — le code ne le vérifie pas, le
  déploiement le garantit ; `BEGIN IMMEDIATE` au claim par défense en profondeur.

## 4. Architecture & composants

```
src/emule_indexer/
├── ports/
│   ├── catalog_repository.py      # CatalogRepository (Protocol sync)
│   └── local_state_repository.py  # LocalStateRepository (Protocol sync) + ClaimedTask (DTO gelé)
└── adapters/persistence_sqlite/
    ├── __init__.py
    ├── errors.py                  # PersistenceError → MigrationError
    ├── connection.py              # open_catalog()/open_local() : connexion, PRAGMA,
    │                              #   runner de migrations (user_version)
    ├── catalog_repository.py      # SqliteCatalogRepository
    ├── local_state_repository.py  # SqliteLocalStateRepository (file de tâches incluse)
    └── migrations/
        ├── catalog/0001_initial.sql
        └── local/0001_initial.sql
```

### Ports (sync, satisfaits structurellement comme `MuleClient`)

```python
class CatalogRepository(Protocol):
    def record_observation(self, observation: FileObservation) -> None: ...
    def record_decision(self, ed2k_hash: str, decision: MatchDecision) -> None: ...

class LocalStateRepository(Protocol):
    def node_id(self) -> str: ...                        # créé au premier appel, puis stable
    def enqueue_verification(self, ed2k_hash: str) -> bool: ...   # False = déjà active (idempotent)
    def claim_verification(self) -> ClaimedTask | None: ...       # None = file vide
    def complete_verification(self, task_id: int) -> None: ...
    def fail_verification(self, task_id: int) -> None: ...        # pending ou dead_letter
    def reclaim_expired(self) -> int: ...                         # nb de leases récupérées
```

`ClaimedTask` (gelé, défini à côté du port) : `task_id: int`, `ed2k_hash: str`,
`attempts: int`. `record_observation` fait, dans UNE transaction :
`INSERT OR IGNORE` dans `files` (première vue gagne) puis `INSERT` dans
`file_observations` (stampé `observed_at`/`node_id`).

## 5. Schémas

### `catalog.db` — append-only, adressé par contenu, prêt à fusionner

```sql
files(ed2k_hash TEXT PRIMARY KEY,        -- hex MINUSCULE 32 (canon v0.5.0)
      size_bytes INTEGER NOT NULL,       -- première vue gagne
      aich_hash TEXT)

file_observations(id INTEGER PRIMARY KEY,            -- local, JAMAIS clé de fusion
      ed2k_hash TEXT NOT NULL REFERENCES files,
      filename TEXT NOT NULL,
      size_bytes INTEGER NOT NULL,                   -- taille OBSERVÉE (cf. déviation 1)
      source_count INTEGER NOT NULL,
      complete_source_count INTEGER NOT NULL,
      media_length_sec INTEGER, bitrate_kbps INTEGER, codec TEXT, file_type TEXT,
      raw_meta TEXT NOT NULL,                        -- JSON liste de paires
      keyword TEXT NOT NULL,
      observed_at TEXT NOT NULL, node_id TEXT NOT NULL)
  + INDEX (ed2k_hash), INDEX (observed_at)

sources(user_hash TEXT PRIMARY KEY, client_name TEXT, client_version TEXT)

source_observations(id INTEGER PRIMARY KEY,
      user_hash TEXT REFERENCES sources,             -- nullable (§11)
      ed2k_hash TEXT NOT NULL REFERENCES files,
      ip TEXT, port INTEGER, nickname TEXT, client_name TEXT, client_version TEXT,
      country TEXT, id_type TEXT, has_complete_file INTEGER, origin TEXT,
      raw_meta TEXT NOT NULL,
      observed_at TEXT NOT NULL, node_id TEXT NOT NULL)
  + INDEX (ed2k_hash), INDEX (user_hash)

match_decisions(id INTEGER PRIMARY KEY,
      ed2k_hash TEXT NOT NULL REFERENCES files,
      target_id TEXT NOT NULL, rule_name TEXT NOT NULL, tier TEXT NOT NULL,
      decided_at TEXT NOT NULL, node_id TEXT NOT NULL)
  + INDEX (ed2k_hash)

file_verifications(id INTEGER PRIMARY KEY,
      ed2k_hash TEXT NOT NULL REFERENCES files,
      verdict TEXT NOT NULL, real_meta TEXT, checks TEXT,
      verified_at TEXT NOT NULL, node_id TEXT NOT NULL)

-- Sur CHAQUE table ci-dessus : triggers BEFORE UPDATE et BEFORE DELETE → RAISE(ABORT).
```

La clé de fusion est verrouillée par la base : `files` porte un
`CHECK (LENGTH(ed2k_hash) = 32 AND ed2k_hash NOT GLOB '*[^0-9a-f]*')` (canon hex
minuscule 32 — les FK propagent le canon aux tables filles par égalité textuelle).

**Déviations assumées vs §11 du spec MVP** (toutes deux éclairées par v0.5.0) :
1. **`file_observations.size_bytes` ajouté** (le §11 ne met la taille que dans `files`).
   Deux entrées hostiles peuvent annoncer le même hash avec des tailles différentes ;
   si seule la première vue survit, l'anomalie devient invisible — contraire au
   capture-all. La taille observée est une donnée d'observation.
2. **`bitrate` → `bitrate_kbps`** (unité explicite, aligné sur `FileObservation`).

### `local.db` — opérationnel, jamais fusionné

```sql
node_runtime(key TEXT PRIMARY KEY, value TEXT NOT NULL)     -- node_id, created_at, …

verification_tasks(id INTEGER PRIMARY KEY,
      ed2k_hash TEXT NOT NULL,
      status TEXT NOT NULL CHECK (status IN ('pending','in_progress','done','dead_letter')),
      attempts INTEGER NOT NULL DEFAULT 0,
      enqueued_at TEXT NOT NULL, claimed_at TEXT, lease_until TEXT)
  + UNIQUE INDEX (ed2k_hash) WHERE status IN ('pending','in_progress')  -- enqueue idempotent

downloads(ed2k_hash TEXT PRIMARY KEY, target_id TEXT NOT NULL, state TEXT NOT NULL,
          queued_at TEXT NOT NULL, completed_at TEXT)

scheduler_state(key TEXT PRIMARY KEY, value TEXT NOT NULL)   -- KV ; le plan C structurera
```

## 6. Mécanique de la file (§12 MVP)

- **Claim atomique FIFO** : `BEGIN IMMEDIATE` puis
  `UPDATE verification_tasks SET status='in_progress', claimed_at=:now,
  lease_until=:now+lease, attempts=attempts+1
  WHERE id = (SELECT id FROM verification_tasks WHERE status='pending'
              ORDER BY enqueued_at, id LIMIT 1) RETURNING id, ed2k_hash, attempts`.
- **Lease** : durée configurable au constructeur ; `reclaim_expired()` repasse en
  `pending` toute `in_progress` dont `lease_until < now` (appelé par le futur
  consommateur au démarrage et périodiquement).
- **Retries bornés → dead-letter** : `attempts` compté AU CLAIM ; `fail_verification`
  repasse en `pending`, sauf `attempts >= max_attempts` (constructeur) → `dead_letter`
  (signal « poison probable », §12 — le plan E en fera une alerte).
- **Enqueue idempotent** : l'index partiel UNIQUE absorbe le doublon actif
  (`INSERT … ON CONFLICT DO NOTHING`, retour `False`).
- `done`/`dead_letter` restent en table (historique local ; reconstructible, §12).

## 7. Erreurs

Même philosophie que l'adapter EC : **l'adapter signale, il ne décide pas.**
- `PersistenceError` (base) — toute `sqlite3.Error` inattendue est enveloppée, jamais
  nue hors de l'adapter.
- `MigrationError(PersistenceError)` — base plus récente que le code, script qui
  échoue (rollback de SA transaction, base laissée à la version précédente), fail-fast
  à l'ouverture (§14 MVP : config/état invalide = on refuse de démarrer).
- Les triggers append-only qui se déclenchent = `PersistenceError` (c'est un bug du
  code appelant, pas un cas métier).

## 8. Stratégie de tests (TDD, 100 % branch, SQLite réel)

- Bases sur **fichiers réels** (`tmp_path`) — `:memory:` ne porte pas WAL.
- **Migrations** : création from-scratch ; idempotence de la réouverture ;
  refus d'une base plus récente (`user_version` artificiellement gonflé) ; échec d'un
  script → rollback, version inchangée.
- **Append-only** : un `UPDATE`/`DELETE` direct sur chaque table de catalogue échoue
  (trigger) ; `record_observation` deux fois sur le même hash → 1 ligne `files`,
  2 lignes `file_observations`.
- **Round-trip** : `FileObservation` complet (média None, `raw_meta` avec doublons et
  ordre) et `MatchDecision` relus tels quels ; timestamps/node_id stampés (horloge
  injectable, UUID vérifiable).
- **File** : claim FIFO ; deux connexions/claims concurrents → tâches distinctes
  (atomicité prouvée) ; lease expirée récupérée (horloge avancée, pas de sleep) ;
  dead-letter au max d'attempts ; enqueue idempotent (False) ; file vide → None.
- **sqlfluff** : `uv run sqlfluff lint` vert sur `migrations/` ; ajouté au gate
  (pre-push `.githooks/` + CI) — le gate passe de 4 à 5 checks.

## 9. Livrables & definition of done

1. Les deux schémas complets (dix tables), migrés, lintés sqlfluff, triggers append-only.
2. Ports `CatalogRepository`/`LocalStateRepository` + DTO `ClaimedTask`.
3. `SqliteCatalogRepository` (observations + décisions) et `SqliteLocalStateRepository`
   (node_id + file complète), testés sur SQLite réel, gate 5 checks vert.
4. Gate mis à jour partout (pre-push, CI, CLAUDE.md).
5. Tag annoté `v0.6.0-data-model` (non poussé).

## 10. Questions laissées au plan d'implémentation

- Contenu exact des `.sql` (DDL complet + triggers) et config sqlfluff fine (règles).
- Détail du runner (`connection.py`) : découverte des scripts par
  `importlib.resources`, ordre lexicographique, validation `user_version`.
- Forme exacte des requêtes de claim (`RETURNING` est supporté par SQLite ≥ 3.35 —
  vérifier la version embarquée du Python cible au moment du plan).
