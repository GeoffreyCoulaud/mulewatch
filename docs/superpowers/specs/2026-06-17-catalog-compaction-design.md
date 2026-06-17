# Compaction du catalogue par rollup d'observations — design (2026-06-17)

> Spec issue d'une session de brainstorming « un sujet à la fois ». Sujet : rétention /
> compaction des bases append-only en exploitation continue. Décisions d'alignement prises
> avec Geoffrey actées en §11. La spec est la base du plan d'implémentation (writing-plans).

## 1. Contexte et objectif

`emule-indexer` est une **surveillance continue** : `record_observation` est appelé pour
**chaque** fichier observé à **chaque** cycle de crawl, et écrit **toujours** une ligne dans
`file_observations` (catalog.db), qu'il y ait match ou non (on catalogue tout « en passant »).
En exploitation sur des mois, c'est **la seule table qui croît avec les cycles** — donc le seul
manque fonctionnel à long terme (disque, scans `observed_at`, temps de merge).

Toutes les autres tables sont **bornées** :
- `files`, `sources` : 1 ligne par entité distincte ;
- `match_decisions` : anti-redondance sur changement de verdict → croissance lente ;
- `file_verifications` : bornée par les downloads (rares — les épisodes Keroro) ;
- `source_observations` : **dormante** (réservée au schéma, écrite par AUCUN code aujourd'hui) ;
- `local.db` (`verification_tasks`, `downloads`, `node_runtime`, `scheduler_state`) : bornée de
  bout en bout (1 tâche par download terminé, index `UNIQUE` partiel sur les statuts actifs ;
  downloads bornés par les matchs ; tables clé-valeur). **Aucune rétention nécessaire côté local.**

**Objectif** : un outil de **compaction** qui réduit `file_observations` sans rien perdre
d'**utile**, en préservant les invariants du catalogue (append-only par triggers, idempotence
du merge, « on reconstruit, on ne mute pas »).

## 2. Principe : anti-redondance rétroactive par **rollup**

Une observation ancienne porte essentiellement : le **nom** vu, et la **disponibilité**
(`source_count`/`complete_source_count`) à l'instant `observed_at`. Le nom change rarement ; la
disponibilité jigote à chaque cycle. La valeur est dans les **plages stables** et leurs
**transitions de nom**, pas dans la répétition.

On **compacte les plages** : pour un fichier, on segmente ses observations en **plages
maximales consécutives de même nom**, et chaque plage devient **une ligne de résumé** portant
`min`/`max`/**moyenne** de la disponibilité + l'étendue temporelle + le compte. C'est le modèle
canonique du downsampling de séries temporelles (cf. recording rules Prometheus / RRD).

Pourquoi un **résumé** (rollup) et pas des lignes brutes représentatives : la **moyenne** n'est
la valeur d'aucune ligne réelle, et toute « ligne la plus proche de la moyenne » **casse
l'idempotence** (la moyenne des représentants gardés ≠ moyenne de la plage → un second passage
choisirait une autre ligne). La seule moyenne *idempotente* est une moyenne **stockée**. Combiner
deux résumés est **associatif** donc stable : `n = n₁+n₂`, `min = min(min₁,min₂)`,
`max = max(max₁,max₂)`, `avg = (n₁·avg₁ + n₂·avg₂) / n`.

`files` / `match_decisions` / `file_verifications` / `sources` sont **recopiées intégralement**.
Seule `file_observations` est compactée. `local.db` n'est **pas** concernée.

## 3. Modèle de données — migration `catalog/0002`

Une **nouvelle table append-only** `file_observation_ranges`. Migration **additive** (CREATE
TABLE + CREATE INDEX + CREATE TRIGGER) — elle ne reconstruit aucune table existante, donc ne
touche pas aux triggers de `0001`.

```sql
-- catalog.db — migration 0002 : table de rollup des observations (compaction).
-- Écrite/lue UNIQUEMENT par l'outil de compaction + le merge. Le crawler l'ignore.
-- Une ligne = un RÉSUMÉ d'une plage maximale de même (ed2k_hash, node_id, filename)
-- consécutive dans le temps. source_count/complete_source_count sont NOT NULL dans
-- file_observations → les agrégats sont toujours définis. La taille canonique vit dans
-- files ; on ne la duplique pas ici. keyword et les métadonnées média sont omises
-- (métadonnée de découverte / quasi toujours NULL ; non pertinentes pour un résumé).

CREATE TABLE file_observation_ranges (
    id INTEGER PRIMARY KEY,
    ed2k_hash TEXT NOT NULL REFERENCES files (ed2k_hash),
    node_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    first_observed_at TEXT NOT NULL,
    last_observed_at TEXT NOT NULL,
    observation_count INTEGER NOT NULL,
    source_count_min INTEGER NOT NULL,
    source_count_max INTEGER NOT NULL,
    source_count_avg REAL NOT NULL,
    complete_source_count_min INTEGER NOT NULL,
    complete_source_count_max INTEGER NOT NULL,
    complete_source_count_avg REAL NOT NULL,
    CHECK (observation_count > 0),
    CHECK (first_observed_at <= last_observed_at)
);

CREATE INDEX idx_file_observation_ranges_ed2k_hash
ON file_observation_ranges (ed2k_hash);

CREATE TRIGGER file_observation_ranges_no_update
BEFORE UPDATE ON file_observation_ranges
BEGIN
    SELECT RAISE(ABORT, 'file_observation_ranges est append-only');
END;

CREATE TRIGGER file_observation_ranges_no_delete
BEFORE DELETE ON file_observation_ranges
BEGIN
    SELECT RAISE(ABORT, 'file_observation_ranges est append-only');
END;
```

Conséquence : **toute** `catalog.db` (même neuve, jamais compactée) porte désormais cette table,
vide. Le crawler ne l'écrit ni ne la lit ; elle n'a d'effet que via l'outil de compaction et le
merge. La SQL est linté par sqlfluff comme les autres migrations.

## 4. Algorithme de compaction (cœur pur, `domain/retention/`)

La segmentation + les agrégats sont **purs** (aucune I/O) → placés en `domain/retention/ranges.py`,
testables exhaustivement. Entrée : **toutes** les observations anciennes (au-delà de la fenêtre),
**triées par `(ed2k_hash, node_id, observed_at, id)`** (un flux unique, l'orchestration §5 fournit
ce tri via SQL). Sortie : les lignes de résumé, dans l'ordre du flux.

```
compact_runs(observations: Sequence[ObservationRow]) -> list[ObservationRange]
```

- `ObservationRow` (frozen) : `ed2k_hash, node_id, filename, source_count,
  complete_source_count, observed_at` (les champs nécessaires, typés).
- `ObservationRange` (frozen) : les **12 colonnes** de la table hormis `id`.

Règles :
1. **Segmentation en un seul passage** : une nouvelle plage commence dès que le triplet
   `(ed2k_hash, node_id, filename)` **diffère** de la ligne précédente. Ce critère unique gère à la
   fois la **partition** (changement de `ed2k_hash` ou `node_id`) et les **runs de nom** (changement
   de `filename` à hash/nœud constants). Une plage = sous-séquence consécutive maximale de triplet
   constant ; `A→B→A` sur un même `(hash, node_id)` ⇒ trois plages (l'oscillation de nom est
   préservée), donc deux plages non adjacentes peuvent partager un nom.
2. **Le `node_id` est dans la clé** car un catalog peut être **mergé** (multi-nœuds) avant
   compaction — on ne fusionne pas des plages de provenances différentes. Dans un catalog mono-nœud,
   `node_id` est constant → c'est de fait par `(hash, filename)`.
3. **Tri** déterministe : `observed_at` est ISO-8601 UTC à largeur fixe, donc l'ordre lexicographique
   EST l'ordre chronologique (cf. `connection.utc_iso`) ; `id` départage les égalités.
4. **Agrégats par plage** : `first/last_observed_at` = bornes de la plage ; `observation_count` =
   longueur ; `*_min`/`*_max` = extrêmes ; `*_avg` = `sum/count` (REAL). `source_count` et
   `complete_source_count` étant NOT NULL, aucun agrégat n'est jamais NULL.
5. Une plage de **longueur 1** produit une ligne de résumé normale (`min=max=avg`,
   `first=last`) — traitement **uniforme**, pas de cas spécial.

## 5. Orchestration SQLite (`compact/compactor.py`)

Calquée sur `merge/merger.py`. **Reconstruction vers une sortie neuve**, jamais de mutation en
place. Signature :

```
compact_catalog(source: Path, output: Path, *, keep_recent_days: int, clock: Clock = utc_now) -> None
```

Déroulé :
1. `cutoff = utc_iso(clock() - timedelta(days=keep_recent_days))` (la coupure « récent » ;
   `clock` injectable → tests déterministes, comme les repositories).
2. `output` ouvert via `open_catalog` (migrations `0001`+`0002` → schéma + triggers).
3. `ATTACH` de `source` (hors transaction), puis DANS une transaction explicite
   (`BEGIN`/`COMMIT`, `ROLLBACK` best-effort) :
   a. Copier **verbatim** (dans l'ordre FK : identités d'abord) : `files`, `sources`,
      `match_decisions`, `file_verifications`, et les `file_observation_ranges` **déjà
      présentes** dans la source (pas de combine — §11).
   b. Copier **verbatim** les `file_observations` **récentes** (`observed_at >= cutoff`).
   c. Pour les `file_observations` **anciennes** (`observed_at < cutoff`) : lire triées par
      `(ed2k_hash, node_id, observed_at, id)`, segmenter en plages via `compact_runs` (pur),
      insérer les lignes de résumé dans `file_observation_ranges`.
4. `COMMIT`, `DETACH` (hors transaction).

Le SQL est en **constantes Python** (cohérent avec `merger.py`/`catalog_repository.py` — pas de
nouveau `.sql` à part les migrations). On n'écrit JAMAIS dans la source (que des SELECT).
Toute `sqlite3.Error` → `CompactError` (fail-fast, message clair), `ROLLBACK` best-effort, la
sortie ne garde aucune compaction partielle.

**Idempotence** : déterministe à `(source, cutoff)` donné. Re-compacter une sortie déjà compactée
avec le **même cutoff** est un no-op sur les données déjà résumées (les `file_observation_ranges`
sont recopiées verbatim ; il ne reste plus d'obs ancienne à résumer). Un cutoff qui a avancé (passe
ultérieure) résume le brut nouvellement vieilli en **nouvelles** plages (append, pas de combine).

## 6. CLI (`compact/__main__.py`)

`python -m emule_indexer.compact`, ergonomie **safe-by-default** alignée sur le merge :

- positionnel `source` : la `catalog.db` à compacter (doit exister → sinon `CompactError`
  fail-fast avant ouverture).
- `--output/-o` (requis) : fichier de sortie **neuf** ; refus s'il existe **sauf `--force`**
  (jamais de truncate ; `--force` autorise un output existant, append idempotent).
- `--keep-recent-days N` (défaut **90**) : fenêtre de récence intouchée. `N >= 0` ; `0` =
  compacter tout l'historique.
- `main(argv) -> int` : `0` = OK ; `2` = erreur d'usage/compaction (message clair sur `stderr`,
  jamais de traceback nu) ; pas de variable d'environnement (doctrine du repo).

La compaction est lancée **crawler arrêté** : `compact source -o neuf`, puis l'opérateur permute
(comme le merge). Documenté au runbook.

## 7. Extension du merge (`merge/merger.py`)

Le merge doit unir la **7ᵉ** table. `file_observation_ranges` est un **journal** (`id` surrogate,
sans sens global) → même motif que les 4 journaux existants : `_copy_journal(...)` avec colonnes
explicites SANS `id` + dédup par clé naturelle complète (`WHERE NOT EXISTS`, comparaisons `IS`
NULL-safe) + `SELECT DISTINCT`. Ajoutée à `_COPY_STATEMENTS` **après** `files` (FK `ed2k_hash`).

Le merge **n'effectue PAS** de combine de stats : il unit des lignes de résumé **distinctes**.
Deux nœuds → `node_id` différent → plages distinctes (jamais à combiner). Même nœud, même plage vue
deux fois (backup re-mergé) → ligne identique → dédupliquée. Idempotence préservée. La
recombinaison associative ne vit que **dans la compaction** (§5), et en v1 elle est différée (§11).

## 8. Neutralité côté lecture (prod)

Le code prod est **inchangé**. `last_observation` (`catalog_repository`) lit le brut le plus
récent (`file_observations` ORDER BY `observed_at` DESC) — toujours présent dans la fenêtre de 90 j.
La requête des candidats au download (`tier='download'`) n'est pas touchée.

**Conséquence assumée** : un fichier dont **toutes** les observations sont plus vieilles que
`keep_recent_days` n'a plus d'obs brute → `last_observation` rend `None` → non téléchargeable par
le chemin « nom frais ». Acceptable : (a) un tel fichier a quasi sûrement quitté le réseau ; (b) le
crawl ré-observe en continu les fichiers vivants, donc tout fichier réellement téléchargeable a une
obs brute récente. Repli possible (`files.size_bytes` + `file_observation_ranges.filename`) en
follow-up **si** ça mord.

## 9. Gestion d'erreurs

- Source absente / `--output` existant sans `--force` / `--keep-recent-days` négatif → `CompactError`
  **avant** toute ouverture (fail-fast, message clair, code `2`).
- Toute `sqlite3.Error` pendant la copie → `ROLLBACK` best-effort + `DETACH` best-effort +
  `CompactError` (la sortie ne garde aucune compaction partielle).
- Source plus récente que le code (`user_version` > dernière migration) → `MigrationError` du
  runner existant (inchangé).

## 10. Tests (TDD strict, 100 % branch, par paquet)

- **`domain/retention/ranges.py` (pur)** — l'essentiel de la couverture, exhaustif et déterministe :
  plage simple ; oscillation `A→B→A` (3 plages) ; plage de longueur 1 ; agrégats min/max/avg (dont
  moyenne non entière → REAL) ; **idempotence** (recompacter une sortie de `compact_runs` re-stabilise) ;
  tri `(observed_at, id)` ; partition multi-`node_id` (ne fusionne pas across nœuds).
- **`compact/compactor.py`** — bases **fichier réelles** (jamais `:memory:`, contrainte WAL) :
  copie verbatim des 5 tables intactes ; fenêtre `keep_recent_days` (récent brut conservé / ancien
  résumé) ; `clock` injecté pour un cutoff fixe ; idempotence (re-run même cutoff = no-op) ;
  `ROLLBACK` sur source corrompue ; triggers append-only actifs sur la sortie.
- **`compact/__main__.py`** — safe-by-default : output neuf OK ; output existant refusé / `--force`
  OK ; source absente → `2` ; `--keep-recent-days` invalide → `2` ; défaut 90.
- **`merge/merger.py`** — round-trip `file_observation_ranges` : union + dédup `IS` NULL-safe +
  re-merge no-op (étend les tests merge existants).
- Pas de marqueur d'intégration : tout est I/O SQLite fichier local (comme les tests du merge).

## 11. Décisions actées / non-objectifs

- **Option A → rollup** (Geoffrey) : `source_count` **hors de la clé de plage** (ne casse pas une
  plage). On garde `min`/`max`/**moyenne** (et complete idem) — donc une table de résumé (la
  moyenne force le rollup, §2).
- **Politique uniforme** (Geoffrey) : pas de rétention selon le tier. Justification renforcée :
  l'info précieuse d'un fichier matché vit dans `match_decisions` / `file_verifications` / la
  quarantaine, **pas** dans `file_observations` — rien ne justifie d'y traiter les matchés à part.
- **`keyword` hors de la clé** et **omis du rollup** : métadonnée de découverte ; l'analyse
  « quel mot-clé est productif » se fait sur le récent (non compacté).
- **`keep_recent_days` = 90** par défaut.
- **Pas de combine en v1** (§5/§7) : chaque passe **append** des plages neuves sans fusionner avec
  des plages adjacentes de même nom issues de passes antérieures. Idempotent et simple. Le combine
  associatif (1 plage unique pour une longue période stable, quel que soit le nombre de passes) est
  un **raffinement v2** explicite.
- **Outil standalone, zéro touche prod** : le crawler n'importe ni ne touche `compact/` ;
  `file_observation_ranges` n'est lue/écrite que par l'outil + le merge → gate 100 % branch préservé.
- **Hors périmètre** : `local.db` (bornée, §1) ; `source_observations` (dormante) ; un repli de
  `last_observation` sur le rollup (follow-up conditionnel, §8) ; le combine v2.
