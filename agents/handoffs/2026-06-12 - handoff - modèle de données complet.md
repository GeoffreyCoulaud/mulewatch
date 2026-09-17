# Handoff — emule-indexer (modèle de données)

> **But** : reprendre naturellement après le jalon `v0.6.0-data-model`. Lis aussi les deux
> handoffs précédents (moteur de matching, adapter EC) ; celui-ci couvre le **Plan A**.
>
> **Dernière mise à jour** : 2026-06-12, après le tag `v0.6.0-data-model`.

---

## 1. TL;DR

- **Ce qui est fait** : la **mémoire** du crawler — les deux bases SQLite du spec MVP
  §11/§12 (schéma complet, dix tables), migrations versionnées (`user_version`,
  fichiers `.sql` embarqués, lintés **sqlfluff** — le gate est passé à **5 checks**),
  et les repositories pour ce qui a un producteur : `record_observation` /
  `record_decision` (catalog), `node_id` + **file de tâches complète** (claim atomique
  FIFO, lease, retries bornés → dead-letter) (local). **359 tests, 100 % branch ;
  intégration EC toujours verte (4/4).**
- **Append-only imposé par la base** : triggers `BEFORE UPDATE/DELETE → RAISE(ABORT)`
  sur les six tables du catalogue, + `PRAGMA recursive_triggers=ON` par connexion
  (sans quoi `INSERT OR REPLACE` les traverse — découverte de revue).
- **Prochaine étape recommandée** : **Plan C (orchestration des recherches)** — tout
  existe pour brancher moteur + EC + DB en boucle ; ou Plan E (observabilité) si tu
  veux des métriques avant la boucle. Brainstormer d'abord, comme toujours.

## 2. État vérifiable

- Tag annoté **`v0.6.0-data-model`** (non poussé). Gate **5 checks** : pytest (100 %
  branch) + ruff check + ruff format + mypy + **sqlfluff lint src** — pre-push et CI à jour.
- Spec : `docs/superpowers/specs/2026-06-11-data-model-design.md` (amendée en cours de
  route : `recursive_triggers`, CHECK canon `ed2k_hash`). Plan exécuté :
  `docs/superpowers/plans/2026-06-11-crawler-mvp-04-data-model.md` (12 tâches).

## 3. Ce qui existe maintenant

```
src/emule_indexer/
├── ports/
│   ├── catalog_repository.py       # record_observation(FileObservation), record_decision(hash, MatchDecision)
│   └── local_state_repository.py   # node_id(), enqueue/claim/complete/fail/reclaim + ClaimedTask
└── adapters/persistence_sqlite/
    ├── errors.py                   # PersistenceError → MigrationError ; wrap_sqlite_errors
    ├── connection.py               # open_catalog/open_local : PRAGMA (WAL, foreign_keys,
    │                               #   recursive_triggers), runner durci (ordre strict,
    │                               #   garde anti-COMMIT parasite, pas de fuite), Clock/utc_iso
    ├── catalog_repository.py       # transaction unique, canon hash validé AVANT, rollback BaseException
    ├── local_state_repository.py   # file complète ; mêmes disciplines
    └── migrations/{catalog,local}/0001_initial.sql   # 10 tables, 12 triggers, CHECKs, index partiels
```

## 4. Contrats que les plans C/D/E doivent respecter

- **Ordre d'écriture catalogue** : `record_decision` exige que l'observation existe déjà
  (FK) — toujours `record_observation` d'abord.
- **`node_id`** : lu depuis `local.db` (`SqliteLocalStateRepository.node_id()`), injecté
  au constructeur du repo catalogue — la plomberie est à la charge du plan C.
- **Hash canonique** : hex minuscule 32 partout (CHECK en base + validation Python
  pré-transaction côté catalogue ; la file locale ne valide PAS — données opérationnelles).
- **File** : `attempts` compté AU CLAIM ; `complete`/`fail` STRICTS (tâche non-in_progress
  → `PersistenceError` = bug de l'appelant) ; défauts lease 15 min / max_attempts 3.
  **Un crash-loop pur (worker qui meurt sans `fail`) ping-pong reclaim→claim sans jamais
  dead-letter** — c'est voulu (dead_letter appartient à `fail`) ; le worker du plan D peut
  dead-letter au claim via `ClaimedTask.attempts` s'il le souhaite.
- **Re-enqueue après dead_letter possible** (l'index partiel libère le slot) : l'appelant
  du plan C/E doit consulter l'historique dead-letter pour qu'un poison ré-observé ne
  recycle pas indéfiniment (alerte plan E).
- **Timestamps** : ISO-8601 UTC largeur fixe (`utc_iso` — REFUSE les datetimes naïfs) ;
  comparaisons lexicographiques = chronologiques. **Horloge injectable partout.**
- **Fusion (différée)** : dédup = égalité ligne entière hors `id` local ; seul
  `catalog.db` traverse la frontière du nœud.

## 5. Pièges appris (revues de ce jalon — tous vérifiés empiriquement)

- **`INSERT OR IGNORE` avale les violations de CHECK** (pas seulement UNIQUE) — d'où la
  validation Python du canon AVANT la transaction côté catalogue.
- **`INSERT OR REPLACE` traverse les triggers** si `recursive_triggers` est OFF (défaut
  SQLite) — nos connexions le mettent ON ; un outil tiers sur défauts peut passer outre.
- **Rollback sur `BaseException`**, pas seulement `sqlite3.Error` : une exception non-sqlite
  en pleine transaction (binding Unicode, horloge défectueuse) laisse sinon la connexion
  coincée `in_transaction` avec des erreurs trompeuses ensuite. Et : staging (json/horloge)
  AVANT le `BEGIN`.
- **Runner** : versions strictement croissantes imposées (doublon/désordre = sauts
  silencieux sinon) ; garde `in_transaction` avant de stamper (un `COMMIT` parasite dans
  un script défait l'enveloppe) ; une migration rebuild **supprime les triggers** — les
  recréer (commentaire durable dans le DDL).
- **`executescript` commite implicitement** toute transaction pendante avant d'exécuter —
  sous `autocommit=True`, l'enveloppe éclatée `BEGIN`/`executescript`/garde/`COMMIT` tient.
- `sqlfluff` : dialecte sqlite OK (triggers + index partiels parsés) ; `LogMessageWaitStrategy`
  (testcontainers) vit dans `core.wait_strategies`, pas `waiting_utils`.

## 6. Méthode (bilan du jalon)

Subagent-driven + revues adversariales (modèle fort) : encore une fois rentable — canon
de hash neutralisé par `OR IGNORE`, traversée des triggers par REPLACE, trois failles du
runner, transaction empoisonnée : **aucune n'était visible dans le plan pourtant validé
empiriquement de bout en bout.** La revue holistique finale n'a trouvé qu'un mineur
(hygiène de transaction de `node_id`), corrigé avant le tag.
