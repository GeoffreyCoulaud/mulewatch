# Compaction du catalogue par rollup journalier d'observations — design (2026-06-17)

> Spec issue d'une session de brainstorming « un sujet à la fois ». Sujet : rétention /
> compaction des bases append-only en exploitation continue. Modèle retenu : **rollup
> node-agnostique par bucket journalier** (cf. §2 pour le cheminement, §11 pour les décisions).
> Base du plan d'implémentation (writing-plans).

## 1. Contexte et objectif

`emule-indexer` est une **surveillance continue** : `record_observation` est appelé pour
**chaque** fichier observé à **chaque** cycle, et écrit **toujours** une ligne dans
`file_observations` (catalog.db), match ou non (on catalogue tout « en passant »). C'est la
**seule table qui croît avec les cycles** — le seul manque fonctionnel à long terme (disque,
scans `observed_at`, temps de merge).

Toutes les autres tables sont **bornées** : `files`/`sources` (1 ligne/entité) ;
`match_decisions` (anti-redondance sur changement de verdict) ; `file_verifications` (bornée par
les downloads — rares) ; `source_observations` **dormante** (écrite par aucun code) ;
`local.db` entière (1 tâche/download terminé, index `UNIQUE` partiel sur statuts actifs, downloads
bornés par les matchs, tables clé-valeur) → **aucune rétention côté local**.

**Objectif** : un outil de **compaction** qui réduit `file_observations` sans perdre l'information
**utile**, en préservant les invariants du catalogue (append-only par triggers, idempotence du
merge, « on reconstruit, on ne mute pas »).

## 2. Cheminement du modèle (pourquoi node-agnostique + bucket journalier)

Trois faits empiriques (vérifiés dans le code / sur aMule) ont guidé le modèle :

1. **aMule agrège les résultats par hash** (`adapters/mule_ec/mapping.py` : une observation par
   `EC_TAG_SEARCHFILE`, donc par hash, avec **un seul nom** — le plus sourcé selon les pairs du
   nœud). Dans un nœud, un hash ⇒ un nom (stable tant que la distribution des sources l'est).
2. **Entre nœuds, le nom canonique diverge** : chaque aMule choisit son nom selon SON point de vue
   réseau → deux nœuds peuvent rapporter des noms différents pour le même hash. Une anti-redondance
   *segmentée sur le nom* fragmenterait donc un flux multi-nœuds (noms qui alternent → zéro
   compaction). eMule a en outre *notoirement* plusieurs noms par hash.
3. **La mission est commune** (Geoffrey) : le sujet est le *fichier*, pas l'observateur. On veut un
   rollup **node-agnostique** qui fusionne les vues de tous les nœuds, en gardant *qui* a contribué.

D'où le modèle : **un rollup par bucket de temps à grille fixe (le jour UTC)**, node-agnostique.
La **grille fixe** est ce qui rend la fusion propre : la clé `(ed2k_hash, jour)` est déterministe
et identique entre nœuds → agrégats **combinables associativement** (somme/min/max, union de sets),
idempotents, sans dépendre d'aucune frontière de plage. Stocker l'**ensemble** des noms répond au
seul reproche qu'on faisait à une fenêtre (« elle perd un changement de nom ») : ici on les garde
tous, on perd seulement *quand dans la journée* — négligeable sur du froid (> 90 j).

`files`/`sources`/`source_observations`/`match_decisions`/`file_verifications` sont **recopiées intégralement** (tout ce qui n'est pas `file_observations` est préservé tel quel — rien n'est perdu à la reconstruction). Seule
`file_observations` est compactée. `local.db` n'est **pas** concernée.

## 3. Modèle de données — migration `catalog/0002`

Nouvelle table **append-only** `file_observation_ranges`. Migration **additive** (CREATE
TABLE/INDEX/TRIGGER) — ne reconstruit aucune table existante, ne touche pas aux triggers de `0001`.

```sql
-- catalog.db — migration 0002 : rollup journalier des observations (compaction).
-- Écrite/lue UNIQUEMENT par l'outil de compaction + le merge. Le crawler l'ignore.
-- Une ligne = UN bucket (ed2k_hash, jour UTC), node-agnostique : agrégat de TOUTES les
-- observations de ce fichier ce jour-là, tous nœuds confondus. source_count et
-- complete_source_count sont NOT NULL dans file_observations → agrégats toujours définis.
-- filenames / node_ids : tableaux JSON CANONIQUES (distincts, triés) → deux ensembles
-- égaux ont un texte identique (dédup merge par égalité de ligne). La taille canonique
-- vit dans files ; non dupliquée. keyword + métadonnées média omis (découverte / NULL).
-- moyenne = source_count_sum / observation_count : exacte et associativement combinable.

CREATE TABLE file_observation_ranges (
    id INTEGER PRIMARY KEY,
    ed2k_hash TEXT NOT NULL REFERENCES files (ed2k_hash),
    bucket TEXT NOT NULL,                       -- jour UTC, "YYYY-MM-DD"
    filenames TEXT NOT NULL,                    -- JSON array trié des noms distincts
    node_ids TEXT NOT NULL,                     -- JSON array trié des nœuds contributeurs
    observation_count INTEGER NOT NULL,
    first_observed_at TEXT NOT NULL,
    last_observed_at TEXT NOT NULL,
    source_count_min INTEGER NOT NULL,
    source_count_max INTEGER NOT NULL,
    source_count_sum INTEGER NOT NULL,
    complete_source_count_min INTEGER NOT NULL,
    complete_source_count_max INTEGER NOT NULL,
    complete_source_count_sum INTEGER NOT NULL,
    CHECK (observation_count > 0),
    CHECK (first_observed_at <= last_observed_at),
    CHECK (LENGTH(bucket) = 10)
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

Conséquence : **toute** `catalog.db` (même neuve) porte cette table, vide. Le crawler ne l'écrit ni
ne la lit ; elle n'a d'effet que via la compaction et le merge. SQL linté par sqlfluff.

## 4. Cœur pur (`domain/retention/buckets.py`)

Le bucketing + les agrégats sont **purs** (aucune I/O) → testables exhaustivement.

```
bucketize(observations: Sequence[ObservationRow]) -> list[ObservationBucket]
```

- `ObservationRow` (frozen) : `ed2k_hash, node_id, filename, source_count,
  complete_source_count, observed_at` (les champs nécessaires, typés).
- `ObservationBucket` (frozen) : les **13 colonnes** de la table hormis `id`.
- Entrée : **toutes** les observations anciennes (au-delà de la fenêtre), triées par
  `(ed2k_hash, observed_at, id)` (le tri vient de l'orchestration §5).

Règles :
1. **Bucket = `(ed2k_hash, jour)`** où `jour` = les **10 premiers caractères** de `observed_at`
   (`"YYYY-MM-DD"`). `observed_at` est ISO-8601 **UTC** à largeur fixe (`connection.utc_iso`) → la
   tranche est le jour UTC, **sans calcul de fuseau** (déterministe, pur).
2. **Agrégats par bucket** : `filenames` = JSON trié des noms distincts ; `node_ids` = JSON trié des
   nœuds distincts ; `observation_count` = nombre d'observations ; `first/last_observed_at` =
   min/max `observed_at` (ordre lexicographique = chronologique) ; `*_min`/`*_max`/`*_sum` sur
   `source_count` et `complete_source_count`. `source_count`/`complete_source_count` étant NOT NULL,
   aucun agrégat n'est jamais NULL. La **moyenne** se dérive `*_sum / observation_count` (jamais
   stockée — exacte et associativement combinable).
3. **Node-agnostique** : on n'utilise PAS `node_id` comme clé ; on agrège les observations de tous
   les nœuds d'un même `(hash, jour)` en **une** ligne, et on garde les nœuds contributeurs dans
   `node_ids`. La moyenne inter-nœuds est **pondérée par la cadence** de chaque nœud (un nœud qui
   observe 2× plus pèse 2×) — caveat documenté ; `min`/`max` et `node_ids` restent nets.

## 5. Orchestration SQLite (`compact/compactor.py`)

Calquée sur `merge/merger.py`. **Reconstruction vers une sortie neuve**, jamais de mutation en place.

```
compact_catalog(source: Path, output: Path, *, keep_recent_days: int, clock: Clock = utc_now) -> None
```

1. `cutoff_date = (clock() - timedelta(days=keep_recent_days)).date().isoformat()` — une **date**
   `"YYYY-MM-DD"`, PAS un instant. La coupure est **alignée sur la frontière de jour UTC** (cf.
   §5bis). `clock` injectable → tests déterministes, comme les repositories.
2. `output` ouvert via `open_catalog` (migrations `0001`+`0002` → schéma + triggers).
3. `ATTACH` de `source` (hors transaction), puis DANS une transaction explicite
   (`BEGIN`/`COMMIT`, `ROLLBACK` best-effort) :
   a. Copier **verbatim** (ordre FK, identités d'abord) : `files`, `sources`, `source_observations`,
      `match_decisions`, `file_verifications`, et les `file_observation_ranges` **déjà présentes**
      dans la source (tout sauf `file_observations` — la compaction étant destructive par
      reconstruction, une table non recopiée serait PERDUE ; on aligne sur le merge).
   b. Copier **verbatim** les `file_observations` **récentes** (`observed_at >= cutoff_date`).
   c. Pour les `file_observations` **anciennes** (`observed_at < cutoff_date`), lues triées par
      `(ed2k_hash, observed_at, id)` : `bucketize` (pur) → insérer les lignes de bucket.
4. `COMMIT`, `DETACH` (hors transaction).

**§5bis — coupure alignée sur le jour (jamais 24 h glissantes).** On ne compacte QUE les jours
**entièrement** plus vieux que la fenêtre : un jour ne serait-ce que partiellement dans la fenêtre
reste **intégralement** brut. `cutoff_date` est une date pure → « ancien » ⟺ `observed_at <
cutoff_date`, et la comparaison lexicographique suffit (`"2026-03-19"` < `"2026-03-19T08:..."`, donc
tout horodatage *du* jour de coupure est `>= cutoff_date` → côté récent). Garantie : chaque bucket
`(hash, jour)` est bâti d'un seul coup à partir de **toutes** les observations du jour → **jamais de
jour scindé, jamais de second bucket** pour un même `(hash, jour)` lors d'une passe ultérieure.

SQL en **constantes Python** (cohérent `merger.py`/`catalog_repository.py` — pas de nouveau `.sql`
hors migrations). On n'écrit **jamais** dans la source (que des SELECT). Toute `sqlite3.Error` →
`CompactError` (fail-fast, message clair), `ROLLBACK`+`DETACH` best-effort, sortie sans compaction
partielle.

**Idempotence** : déterministe à `(source, cutoff_date)` donné. Re-compacter une sortie déjà
compactée au **même `cutoff_date`** est un no-op sur le froid (les `file_observation_ranges` sont
recopiées verbatim ; plus aucune obs ancienne à bucketiser). Un `cutoff_date` avancé (passe
ultérieure) bucketise les jours **nouvellement entièrement vieillis** en **nouvelles** lignes de
bucket — sans recouvrement avec l'existant, puisque la coupure jour (§5bis) garantit qu'un jour
n'est compacté qu'une fois, complet.

## 6. CLI (`compact/__main__.py`)

`python -m emule_indexer.compact`, ergonomie **safe-by-default** alignée sur le merge :

- positionnel `source` : la `catalog.db` à compacter (absente → `CompactError` fail-fast avant
  ouverture).
- `--output/-o` (requis) : fichier de sortie qui **ne doit pas exister** (refus sinon — **pas de
  `--force`, pas d'append** : la compaction est une transformation *source → sortie neuve*
  mono-source ; pour refaire, l'opérateur supprime la sortie). Idempotence = « même `source` + même
  `cutoff_date` → même sortie » (fichier neuf et déterministe à chaque exécution ; aucune dédup à
  l'écriture puisque la sortie est toujours vierge).
- `--keep-recent-days N` (défaut **90**, `N >= 0` ; `0` = compacter tout l'historique).
- `main(argv) -> int` : `0` = OK ; `2` = erreur d'usage/compaction (message clair `stderr`, jamais
  de traceback) ; aucune variable d'environnement.

**Pas automatique** : aucune boucle, aucun déclenchement par le crawler — un outil **opérateur**,
manuel (cron-able par l'opérateur s'il le souhaite), exactement comme le merge. Lancée **crawler
arrêté** : `compact source -o neuf`, puis l'opérateur permute. Documenté au runbook.

## 7. Extension du merge (`merge/merger.py`)

`file_observation_ranges` est unie comme un **7ᵉ journal** : `_copy_journal(...)` avec colonnes
explicites SANS `id` + dédup par clé naturelle complète (`WHERE NOT EXISTS`, `IS` NULL-safe) +
`SELECT DISTINCT`. Ajoutée à `_COPY_STATEMENTS` **après** `files` (FK `ed2k_hash`).

Le merge **n'effectue PAS** de combine d'agrégats : il **unit des lignes distinctes**. Append-only
oblige (un UPDATE serait refusé par trigger), et c'est cohérent. Conséquences :
- `merge`-puis-`compact` (recommandé) : la compaction voit le brut de tous les nœuds → **une** ligne
  node-agnostique par `(hash, jour)`.
- `compact`-puis-`merge` : deux nœuds compactés séparément donnent deux lignes pour le même
  `(hash, jour)` (`node_ids` différents) → **les deux conservées** ; la **combinaison est différée
  à la lecture/export** (filenames/node_ids unionnés, sommes/min/max recombinés — associatif). C'est
  exactement la philosophie déjà actée pour la dédup `file_verifications` « at-least-once ».
Re-merge d'une ligne identique → no-op (dédup `IS`).

## 8. Neutralité côté lecture (prod)

Code prod **inchangé**. `last_observation` (`catalog_repository`) lit le brut le plus récent
(`file_observations` ORDER BY `observed_at` DESC) — toujours présent dans la fenêtre des 90 j. La
requête des candidats au download (`tier='download'`) n'est pas touchée.

**Conséquence assumée** : un fichier dont **toutes** les observations sont plus vieilles que
`keep_recent_days` n'a plus d'obs brute → `last_observation` rend `None` → non téléchargeable par le
chemin « nom frais ». Acceptable : un tel fichier a quasi sûrement quitté le réseau, et le crawl
ré-observe en continu les fichiers vivants. Repli possible (`files` + `file_observation_ranges`) en
follow-up **si** ça mord.

## 9. Volume (validé pour la granularité journalière)

Une ligne de bucket ≈ **~300 o** (typique) à **~600 o** (set de noms volumineux). Le tier froid
croît de `N × 365 × ~300 o`/an où **N = fichiers distincts observés par jour** (un bucket = une ligne
par fichier/jour, indépendant de la fréquence de crawl). Repères : N=10 000 → ~1,2 Go/an ;
N=50 000 → ~6 Go/an ; N=100 000 → ~12 Go/an ; **bascule ~415 000 fichiers/jour → ~50 Go/an**. Pour
la mission Keroro (même en cataloguant large), N ≤ quelques dizaines de milliers → **1–6 Go/an**, très
en deçà du budget de 50 Go/an → **granularité au jour validée**. (Caveat orthogonal : la **fenêtre
récente de 90 j en brut** est un coût *fixe* piloté par la fréquence de crawl et `keep_recent_days`,
sans rapport avec le choix de bucket ; c'est la compaction qui borne la croissance *infinie*.)

## 10. Tests (TDD strict, 100 % branch, par paquet)

- **`domain/retention/buckets.py` (pur)** — l'essentiel, exhaustif et déterministe : un bucket
  d'un seul jour ; deux jours → deux buckets ; tranche de jour UTC (`observed_at` → 10 premiers
  caractères) ; agrégats min/max/sum (somme exacte) ; `filenames`/`node_ids` JSON triés + distincts +
  canoniques (ordre d'entrée indifférent) ; **node-agnostique** (observations de 2 nœuds le même
  jour → 1 bucket, `node_ids` à 2 éléments) ; bucket à une seule observation (`min=max=sum`).
- **`compact/compactor.py`** — bases **fichier réelles** (jamais `:memory:`, contrainte WAL) :
  copie verbatim des 6 tables intactes (dont `source_observations`) ; fenêtre `keep_recent_days` (récent brut conservé / ancien
  bucketisé) ; **coupure alignée jour** (§5bis) : un jour partiellement dans la fenêtre reste
  **intégralement** brut (obs du jour de coupure → côté récent) ; `clock` injecté → cutoff fixe ;
  idempotence (re-run même `cutoff_date` = no-op) ; `ROLLBACK` sur source corrompue ; triggers
  append-only actifs sur la sortie (un UPDATE/DELETE sur un bucket est refusé).
- **`compact/__main__.py`** — safe-by-default : output neuf OK ; output existant refusé → `2` ;
  source absente → `2` ; `--keep-recent-days` négatif → `2` ; défaut 90.
- **`merge/merger.py`** — round-trip `file_observation_ranges` : union + dédup `IS` NULL-safe +
  re-merge no-op (étend les tests merge existants).
- Pas de marqueur d'intégration : tout est I/O SQLite fichier local (comme les tests du merge).

## 11. Décisions actées / non-objectifs

- **Rollup node-agnostique par bucket journalier** (Geoffrey) : la mission est commune, on fusionne
  les vues de tous les nœuds ; `node_id` n'est PAS une clé, mais conservé en `node_ids` (provenance à
  la granularité bucket). La grille fixe (jour) rend la fusion exacte/associative et idempotente.
- **Granularité au jour** (Geoffrey) : validée par le calcul de volume (§9), ~1–6 Go/an pour un N
  réaliste, très sous le budget de 50 Go/an ; on garde la résolution journalière « gratuitement ».
- **Option A → on garde min/max/(moyenne dérivée)** de la disponibilité ; `source_count` n'est pas
  une clé. La moyenne est stockée comme **somme + compte** (exacte, combinable), pas comme REAL.
- **Politique uniforme** (Geoffrey) : pas de rétention selon le tier — l'info précieuse d'un matché
  vit dans `match_decisions`/`file_verifications`/la quarantaine, pas dans `file_observations`.
- **`keyword` omis** (métadonnée de découverte ; analyse de productivité sur le récent non compacté).
- **`keep_recent_days` = 90** par défaut, **coupure alignée sur le jour UTC** (§5bis, Geoffrey) :
  granularité au jour, pas 24 h glissantes ; un jour partiellement dans la fenêtre reste intégralement
  brut → jamais de jour scindé ni de double bucket.
- **Compaction NON automatique** (Geoffrey) : outil opérateur standalone, crawler arrêté, comme le
  merge — pas de boucle ni de déclenchement par le crawler.
- **Merge = union-dedup, pas de combine** (§7) ; la combinaison inter-nœuds des buckets identiques est
  **différée à la lecture/export** (cohérent avec la dédup `file_verifications`). Append-only intact.
- **Outil standalone, zéro touche prod** : le crawler n'importe ni ne touche `compact/` ;
  `file_observation_ranges` n'est lue/écrite que par l'outil + le merge → gate 100 % branch préservé.
- **`source_observations`** (dormante, écrite par aucun code) : non *transformée* mais **recopiée
  verbatim** comme les autres tables intactes — la compaction étant destructive par reconstruction,
  l'omettre la perdrait dès la permutation (parité avec le merge ; trouvé par la revue holistique).
- **Hors périmètre** : `local.db` (bornée) ; un repli de
  `last_observation` sur le rollup (follow-up conditionnel, §8) ; une surface de lecture/export qui
  recombine les buckets multi-nœuds (future, §7) ; la calibration de `keep_recent_days` / fréquence de
  crawl pour borner la fenêtre récente (réglage d'exploitation, §9).
