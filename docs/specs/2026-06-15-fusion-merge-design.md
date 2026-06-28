# Fusion / merge — design (script standalone, N `catalog.db` → 1 fichier neuf, idempotent)

> **Nature** : design doc **structurant**, décisions **figées** (issu du co-design de la passe
> post-Plan E, cf. `docs/superpowers/specs/2026-06-15-backlog-parallelization-design.md` §5/§8).
> Exécutable par un implémenteur frais, en TDD strict. **Ne re-designe pas** : si l'implémentation
> révèle une contradiction avec le code réel, ne tranche pas seul — note-la sous
> « ## Risques / à confirmer » et signale-la dans le rapport de complétion.
>
> **Worktree** : `WT-fusion`. **Vérif** : **Auto** (100 % SQLite temporaire, **zéro Docker**).
> **Fichiers réservés** : ce script a **son propre point d'entrée** — il ne touche **pas**
> `composition/app.py`, ni le module CLI du crawler, ni `compose*.yaml`, ni `uv.lock`/`pyproject`
> (toute dépendance ajoutée — il n'y en a aucune attendue — est *déclarée* dans le rapport, pas
> lockée par l'agent). Il ne modifie **aucun** schéma SQL existant.

---

## 1. Contexte — vision réseau décentralisé de chercheurs

`emule-indexer` est conçu pour qu'un **réseau de chercheurs** (lost-media du doublage français de
*Keroro mission Titar*) fasse tourner **chacun son propre nœud** (un crawler + un `amuled` derrière
son propre VPN). Chaque nœud accumule, dans son `catalog.db` **append-only** local, l'histoire
complète de ce qu'il a observé sur le réseau eMule : fichiers vus, sources, décisions de matching,
verdicts de vérification — chaque ligne stampée d'un `node_id` (qui *a* observé) et d'un timestamp
(*quand*). La contrainte fondatrice tient : **le sujet du catalogue est le fichier, jamais la
personne** ; `node_id` identifie un *nœud du réseau de recherche*, pas un pair eMule deanonymisé.

Pour mettre en commun les trouvailles, il faut **fusionner** N catalogues hétérogènes (un par
chercheur, ou un par campagne) en **un seul catalogue consolidé**. C'est l'objet de ce script. Trois
propriétés non négociables découlent du modèle de données :

1. **Append-only imposé par la base** : `catalog.db` porte, sur **chaque** table, un trigger
   `BEFORE UPDATE` **et** un `BEFORE DELETE` → `RAISE(ABORT, '<table> est append-only')`
   (`migrations/catalog/0001_initial.sql:89-159`). Le merge **ne doit jamais déclencher** ces
   triggers : pas d'`UPDATE`, pas de `DELETE`, et **surtout pas d'`INSERT OR REPLACE`** (qui est un
   `DELETE` + `INSERT` et heurte le trigger delete — détaillé en §4.1).
2. **Identité de contenu canonique** : un fichier est identifié par son `ed2k_hash` (32 hex
   minuscules, PK de `files`) ; une source par son `user_hash` (PK de `sources`). Ces identités sont
   **globales** (le même fichier vu par deux nœuds a le même `ed2k_hash`) → la dédup d'identité est
   triviale.
3. **Journaux à `id` LOCAL** : les quatre journaux (`file_observations`, `source_observations`,
   `match_decisions`, `file_verifications`) ont un `id INTEGER PRIMARY KEY` **autoincrément, local au
   nœud** → il **collisionne** entre nœuds (deux nœuds ont chacun une ligne `id=1` sans rapport).
   L'`id` n'a **aucun sens global** : le merge le **laisse tomber** et dédupe par **clé naturelle**
   (toutes les colonnes *sauf* `id`) — détaillé en §4.2.

**Cette fusion résout aussi l'item différé `file_verifications` dedup** (`CLAUDE.md`,
backlog §8 du plan d'orchestration) : la dédup par clé naturelle d'un journal append-only est
*exactement* la déduplication des doublons « at-least-once » que le verifier peut produire (un
`record`/`complete` à cheval sur deux DB laisse une ligne `file_verifications` en double). Re-merger
N fois est un no-op → le doublon disparaît au passage. Ce n'est plus un item séparé.

---

## 2. Décision figée — script **standalone**, pas une sous-commande

Le merge est un **outil opérateur ponctuel** (on consolide quand on veut mettre en commun), pas une
boucle de service. Il a donc **son propre point d'entrée**, totalement disjoint de l'app crawler :

- **Nom du module** : `emule_indexer.merge` (un sous-paquet du paquet crawler), avec
  `emule_indexer/merge/__main__.py` → **invocation** :
  ```bash
  uv run python -m emule_indexer.merge --output catalog-merged.db source-a.db source-b.db source-c.db
  ```
- **Pas de `[project.scripts]`** : le projet n'en déclare aucun aujourd'hui (les points d'entrée
  existants sont `python -m emule_indexer` et `python -m emule_indexer.tools.ec_probe`). On **suit
  cette convention** — `python -m emule_indexer.merge`, alignée sur `tools/ec_probe.py`. *(Si
  l'opérateur veut un console-script `emule-merge` plus tard, c'est un delta `[project.scripts]`
  trivial à l'intégration — hors-scope de cette tâche, qui ne touche pas `pyproject`.)*
- **Le script n'importe RIEN de l'app** : pas de `composition/`, pas d'`application/`, pas de
  `CrawlerApp`. Il importe **uniquement** l'adapter persistence (`connection.open_catalog` pour créer
  le schéma de sortie via migration ; voir §5) et la stdlib (`sqlite3`, `argparse`, `pathlib`,
  `logging`, `sys`). Le crawler PROD reste **intouché**.

**Placement hexagonal.** Le merge est de l'**I/O pur** : `ATTACH`/`INSERT … SELECT` sur des
connexions SQLite (§3). Il n'y a **pas de domaine** à extraire (aucune règle métier — l'idempotence
et l'ordre FK sont des propriétés *du SQL*, exprimées en SQL). Le sous-paquet est donc un **adapter**
+ son point d'entrée CLI. Structure :

```
packages/crawler/src/emule_indexer/merge/
  __init__.py
  __main__.py        # argparse, safe-by-default, logging ; appelle merge_catalogs() ; rend int
  merger.py          # merge_catalogs(output, sources, *, force, into) → l'I/O (ATTACH + INSERT…SELECT)
  errors.py          # MergeError (ValueError-like ; messages clairs pour le CLI)
```
*(Réutiliser `connection.open_catalog` du paquet persistence implique une dépendance
`merge → adapters.persistence_sqlite`. C'est licite : `merge` est lui-même un adapter / un outil, il
peut dépendre d'un autre adapter. Il ne dépend **pas** du domaine ni de l'application.)*

---

## 3. Mécanisme — `ATTACH DATABASE` + `INSERT … SELECT`

Le cœur du merge : pour chaque source, on l'**attache** à la connexion de sortie sous un alias
(`ATTACH DATABASE '<source>' AS src`), puis on **copie table par table** avec `INSERT … SELECT`
depuis `src.<table>` vers la table principale (`main.<table>`), avec la stratégie d'idempotence de
§4. Puis on **détache** (`DETACH DATABASE src`) avant la source suivante.

Contraintes de connexion (toutes posées par `open_catalog`, cf. `connection.py:83-90`) :

- **`PRAGMA recursive_triggers=ON`** — **OBLIGATOIRE**. Sans lui, `INSERT OR REPLACE` traverserait les
  triggers append-only (note de la migration, `0001_initial.sql:4-6`). On n'utilise *pas* `OR REPLACE`
  (§4.1), mais **on garde ce pragma par sûreté** : `open_catalog` le pose déjà sur la connexion de
  sortie, donc un `OR REPLACE` accidentel *échouerait bruyamment* (ABORT) au lieu de corrompre
  l'append-only en silence. C'est un filet, pas un mécanisme — l'algo lui-même n'émet jamais d'`UPDATE`/`DELETE`/`REPLACE`.
- **`PRAGMA foreign_keys=ON`** — posé par `open_catalog`. Impose l'**ordre FK** (§4.3) : insérer une
  observation dont l'`ed2k_hash` n'est pas (encore) dans `files` lève une erreur FK. On insère donc
  les tables d'identité **avant** les journaux.
- **`journal_mode=WAL`** — posé et **vérifié** par `open_catalog` (refus net si la base ne le porte
  pas, d'où l'interdiction de `:memory:` — les tests utilisent des **fichiers réels**, cf.
  `connection.py:84-88`). Aligné sur la doctrine du repo (spec data-model §8).
- **`autocommit=True` (transactions explicites)** : `open_catalog` ouvre en autocommit réel
  (`connection.py:70`). Le merger **enveloppe toute la copie d'une source dans UNE transaction
  explicite** (`BEGIN` … `COMMIT`, `ROLLBACK` best-effort sur erreur — même discipline que
  `catalog_repository.record_observation`, `catalog_repository.py:111-139`) : une source à moitié
  copiée ne doit pas laisser la base de sortie incohérente. (`ATTACH`/`DETACH` se font **hors**
  transaction : SQLite refuse d'`ATTACH` à l'intérieur d'une transaction ouverte.)

**`ATTACH` est-il toujours en lecture seule sur la source ?** On n'écrit jamais dans `src.*` (que des
`SELECT`). Pour blinder, on **peut** attacher en lecture seule via l'URI
`ATTACH DATABASE 'file:<source>?mode=ro' AS src` (nécessite `sqlite3.connect(..., uri=True)` sur la
connexion de sortie — **à confirmer** : `open_catalog` n'active pas `uri=True` aujourd'hui ; voir
« Risques / à confirmer »). À défaut, l'attache normale suffit : aucune écriture n'est émise vers la
source.

---

## 4. Algorithme de merge — idempotence par table

Le merge est **idempotent (décision A)** : re-merger les mêmes sources dans la même sortie N fois
produit **exactement** le même contenu (re-merge = no-op). Deux stratégies selon la nature de la
table.

### 4.1 Tables d'identité-contenu (`files`, `sources`) → `INSERT OR IGNORE`

`files` (PK `ed2k_hash`) et `sources` (PK `user_hash`) portent une **clé primaire de contenu
globale**. La même entité vue par deux nœuds a la même PK. Stratégie :

```sql
INSERT OR IGNORE INTO files       SELECT * FROM src.files;
INSERT OR IGNORE INTO sources     SELECT * FROM src.sources;
```

- **`INSERT OR IGNORE`** : une ligne dont la PK existe déjà est **silencieusement ignorée** (pas
  d'erreur, pas de modification de la ligne existante). C'est le pattern **déjà utilisé en prod** pour
  `files` (`catalog_repository.py:40` : `INSERT OR IGNORE INTO files …`, « première vue gagne »). Le
  merge l'étend simplement à la copie de masse.
- **JAMAIS `INSERT OR REPLACE`** : `OR REPLACE` sur conflit de PK fait un **`DELETE` de la ligne
  existante puis `INSERT`** de la nouvelle. Le `DELETE` heurte le trigger `*_no_delete`
  → `RAISE(ABORT)` (sous `recursive_triggers=ON`, qui est posé). Donc `OR REPLACE` **échouerait**
  — et même s'il passait, il **détruirait** de l'append-only. Proscrit.

⚠️ **Ride mineure documentée (premier-arrivé-gagne) — voir §6.** `INSERT OR IGNORE` garde la
**première** ligne insérée pour une PK donnée et ignore les suivantes. Si la source A a un fichier
avec `aich_hash = NULL` et la source B le même `ed2k_hash` avec `aich_hash` **renseigné**, l'ordre de
merge décide : si A passe en premier, on garde le `NULL`, et on **ne peut pas** l'enrichir ensuite
(l'`UPDATE` est interdit par le trigger). Perte d'information **mineure** et **acceptée** : la valeur
non-NULL existe toujours dans le `catalog.db` source, et de toute façon l'historique complet vit dans
les **observations** (pas dans la ligne `files` agrégée). On **ne corrige pas** (ce serait un
`UPDATE`, interdit). On **documente**.

### 4.2 Journaux append-only → dédup par clé naturelle (`INSERT … SELECT … WHERE NOT EXISTS`)

Les quatre journaux ont un `id` **local** (collisionne entre nœuds) qu'on **laisse tomber**. On ne
copie **jamais** la colonne `id` : on liste les colonnes explicitement (sans `id`) et la base
réattribue un nouvel `id` autoincrément côté sortie. La dédup se fait sur la **clé naturelle = toutes
les colonnes sauf `id`** via `WHERE NOT EXISTS` :

```sql
-- file_observations : 13 colonnes (toutes sauf id)
INSERT INTO file_observations (
    ed2k_hash, filename, size_bytes, source_count, complete_source_count,
    media_length_sec, bitrate_kbps, codec, file_type, raw_meta,
    keyword, observed_at, node_id
)
SELECT DISTINCT
    ed2k_hash, filename, size_bytes, source_count, complete_source_count,
    media_length_sec, bitrate_kbps, codec, file_type, raw_meta,
    keyword, observed_at, node_id
FROM src.file_observations AS s
WHERE NOT EXISTS (
    SELECT 1 FROM file_observations AS d
    WHERE d.ed2k_hash IS s.ed2k_hash
      AND d.filename IS s.filename
      AND d.size_bytes IS s.size_bytes
      AND d.source_count IS s.source_count
      AND d.complete_source_count IS s.complete_source_count
      AND d.media_length_sec IS s.media_length_sec
      AND d.bitrate_kbps IS s.bitrate_kbps
      AND d.codec IS s.codec
      AND d.file_type IS s.file_type
      AND d.raw_meta IS s.raw_meta
      AND d.keyword IS s.keyword
      AND d.observed_at IS s.observed_at
      AND d.node_id IS s.node_id
);
```

Points clés :

- **`IS` et non `=`** dans le `WHERE NOT EXISTS` : plusieurs colonnes des journaux sont **nullable**
  (`media_length_sec`, `bitrate_kbps`, `codec`, `file_type` dans `file_observations` ;
  `user_hash`, `ip`, `port`, … `has_complete_file`, `origin` dans `source_observations` ;
  `real_meta`, `checks` dans `file_verifications`). En SQL, `NULL = NULL` est `NULL` (faux), donc `=`
  **ne déduperait pas** deux lignes identiques contenant des NULL — elles seraient ré-insérées à
  chaque merge → **non idempotent**. L'opérateur **`IS`** (`IS NOT DISTINCT FROM` de SQLite) traite
  `NULL IS NULL` comme vrai. **Toutes** les comparaisons de la clé naturelle utilisent `IS`.
- **Granularité = clé naturelle complète**. Deux observations *légitimement distinctes* (même fichier,
  même nœud, deux instants → `observed_at` diffère) ont des clés naturelles différentes → **les deux
  sont conservées** (c'est voulu : ce sont deux observations réelles). Seules des lignes **bit-pour-bit
  identiques sur toutes les colonnes hors `id`** sont dédupliquées. C'est ce qui rend le merge à la
  fois **idempotent** (re-merge = no-op) **et** non-destructeur (aucune observation réelle perdue).
- **Re-merge = no-op** : au 2ᵉ merge, chaque ligne source a déjà son jumeau exact en sortie →
  `WHERE NOT EXISTS` est faux partout → 0 insertion. Idempotent par construction.
- **`SELECT DISTINCT` — dédup INTRA-source (corrige une incohérence du design initial).** Le
  `WHERE NOT EXISTS` ne dédupe que contre la **destination** (`main.*`). Deux lignes **bit-pour-bit
  identiques au sein d'UNE même source** ne sont *pas* dédupées par lui en un seul passage : au moment
  du `SELECT`, aucune n'est encore dans `main` → les deux passent le `NOT EXISTS` → les deux sont
  copiées (et un re-merge ne les retire jamais, elles y sont alors toutes deux). On ajoute donc
  **`SELECT DISTINCT`** sur la clé naturelle des journaux : il collapse ces doublons intra-source. C'est
  la **même politique de dédup** que celle déjà énoncée ci-dessus (« seules les lignes bit-pour-bit
  identiques sur la clé naturelle sont des doublons »), simplement appliquée *aussi* à l'intérieur d'une
  source. `DISTINCT` traite `NULL` comme égal à `NULL` (cohérent avec l'`IS`) et ne collapse jamais deux
  observations *légitimement distinctes* (elles diffèrent sur ≥1 colonne → restent deux lignes). **C'est
  ce qui rend la promesse §1 (dédup des doublons at-least-once de `file_verifications` d'un seul
  catalogue, via N=1) effectivement vraie** — sans `DISTINCT` elle ne tenait pas. *(Inutile sur
  `files`/`sources` : un doublon de PK intra-source est impossible, `ed2k_hash`/`user_hash` étant PK dans
  la source aussi ; `INSERT OR IGNORE` suffit.)*

Les **clés naturelles par table** (colonnes hors `id`, tirées de `0001_initial.sql`) :

| Journal | Clé naturelle (toutes colonnes sauf `id`) | Colonnes nullable (→ `IS`) |
|---|---|---|
| `file_observations` | `ed2k_hash, filename, size_bytes, source_count, complete_source_count, media_length_sec, bitrate_kbps, codec, file_type, raw_meta, keyword, observed_at, node_id` | `media_length_sec, bitrate_kbps, codec, file_type` |
| `source_observations` | `user_hash, ed2k_hash, ip, port, nickname, client_name, client_version, country, id_type, has_complete_file, origin, raw_meta, observed_at, node_id` | `user_hash, ip, port, nickname, client_name, client_version, country, id_type, has_complete_file, origin` |
| `match_decisions` | `ed2k_hash, target_id, rule_name, tier, decided_at, node_id` | *(aucune)* |
| `file_verifications` | `ed2k_hash, verdict, real_meta, checks, verified_at, node_id` | `real_meta, checks` |

> **`raw_meta` / `real_meta` / `checks` sont du JSON sérialisé en TEXT** (liste de paires, ordre et
> doublons préservés — `catalog_repository.py:109`). La comparaison `IS` est **textuelle exacte** sur
> la sérialisation. Deux nœuds qui ont observé la *même* `raw_meta` produisent la *même* chaîne JSON
> (sérialisation déterministe : `json.dumps(..., ensure_ascii=False)`, **sans tri**, ordre du fil
> préservé) → dédup correcte. C'est la granularité voulue : si deux sérialisations diffèrent (ordre,
> espaces), ce sont deux observations distinctes conservées — acceptable (pas de fausse fusion).

### 4.3 Ordre FK — identités d'abord, journaux ensuite

Sous `foreign_keys=ON`, les journaux référencent `files`/`sources` :

- `file_observations.ed2k_hash → files.ed2k_hash` (NOT NULL)
- `source_observations.ed2k_hash → files.ed2k_hash` (NOT NULL) **et** `source_observations.user_hash → sources.user_hash` (nullable)
- `match_decisions.ed2k_hash → files.ed2k_hash` (NOT NULL)
- `file_verifications.ed2k_hash → files.ed2k_hash` (NOT NULL)

**Ordre d'insertion par source (impératif)** :

1. `files` (`INSERT OR IGNORE`)
2. `sources` (`INSERT OR IGNORE`)
3. `file_observations` (`WHERE NOT EXISTS`)
4. `source_observations` (`WHERE NOT EXISTS`)
5. `match_decisions` (`WHERE NOT EXISTS`)
6. `file_verifications` (`WHERE NOT EXISTS`)

Insérer un journal **avant** son `files`/`sources` parent violerait la FK → erreur. Comme une source
`catalog.db` est elle-même cohérente (ses journaux référencent ses `files`/`sources`), copier `files`
+ `sources` **d'abord** garantit que toute ligne de journal copiée ensuite trouve son parent (qu'il
vienne d'être inséré ou qu'il préexiste d'un merge antérieur). L'ordre est **par source** : on fait
les 6 tables d'une source dans cette transaction, on commit, on détache, on passe à la suivante.

---

## 5. Création de la base de sortie — via la migration catalog

La base de sortie est **NEUVE** : il faut y poser le **schéma complet + les triggers append-only**
avant tout merge. On **réutilise le runner de migration existant** plutôt que de dupliquer le DDL :

```python
from emule_indexer.adapters.persistence_sqlite.connection import open_catalog

connection = open_catalog(output_path)   # applique migrations/catalog/0001_initial.sql sur une base neuve
```

`open_catalog` (`connection.py:58-60`) :
1. `sqlite3.connect(output_path, autocommit=True)` — crée le fichier s'il n'existe pas.
2. `_configure` — pose `journal_mode=WAL` (+ vérifie), `foreign_keys=ON`, `recursive_triggers=ON`.
3. `_apply_migrations` — applique `0001_initial.sql` (tables + index + **les 12 triggers
   append-only**) dans sa transaction, stampe `PRAGMA user_version`.

→ La base de sortie obtient **exactement** le même schéma + les mêmes garanties append-only que toute
`catalog.db` de prod. **Aucun DDL n'est écrit dans le script de merge** (pas de copier-coller du
schéma — single source of truth = la migration). **Aucun changement de schéma** n'est requis : le
schéma est déjà UNION-safe (PK de contenu globales + journaux à `id` local).

> **Note de robustesse** : `open_catalog` est *idempotent vis-à-vis du schéma* — si la sortie existe
> déjà comme `catalog.db` valide, l'ouvrir n'applique aucune migration (version courante = dernière).
> Mais **la création de la sortie passe par la garde safe-by-default de §6** : on ne crée/ouvre la
> sortie qu'après avoir validé le chemin (neuf, ou `--force`, ou `--into`).

**Les sources, elles, ne sont PAS migrées** : on les `ATTACH` telles quelles (lecture seule logique).
On **ne** les ouvre **pas** via `open_catalog` (pas de migration sur une source ; on n'écrit pas
dedans). On valide juste qu'elles existent (fichier présent) ; une source corrompue ou de schéma
incompatible fera échouer le premier `INSERT … SELECT` → `MergeError` clair (fail-fast). *(On peut
optionnellement vérifier `PRAGMA user_version` de chaque source == version attendue et lever un
`MergeError` explicite si une source est d'une version de schéma plus récente — voir « Risques / à
confirmer ».)*

---

## 6. CLI — safe-by-default, zéro perte accidentelle

**Invocation** :
```bash
uv run python -m emule_indexer.merge --output <out.db> <source1.db> [<source2.db> …]
uv run python -m emule_indexer.merge --output <out.db> --force <sources…>      # autorise écrasement/réutilisation d'un fichier existant
uv run python -m emule_indexer.merge --into <one-of-the-sources.db> <sources…> # dest = une source EXPLICITE
```

**Arguments** (`argparse`) :

| Argument | Type | Rôle |
|---|---|---|
| `sources` (positionnels, `nargs="+"`) | `Path` | 1..N bases `catalog.db` en entrée. ≥1 imposé (`nargs="+"`). |
| `--output, -o` | `Path` | Chemin du fichier de **sortie NEUF**. **Mutuellement exclusif** avec `--into`. |
| `--into` | `Path` | Dest = **une des sources**, désignée explicitement. **Mutuellement exclusif** avec `--output`. |
| `--force` | flag | Autorise l'écriture sur un `--output` qui **existe déjà** (sinon refus). |

**Règles safe-by-default (zéro perte accidentelle)** :

1. **`--output` (mode par défaut, fichier neuf)** :
   - Le chemin de sortie **n'existe pas** → on le crée via `open_catalog`, on merge **toutes** les
     sources dedans. Cas nominal.
   - Le chemin de sortie **existe déjà** → **erreur** (`MergeError`, code de sortie ≠ 0), **SAUF** si
     `--force`. **Jamais d'écrasement implicite.** Avec `--force` : on ouvre l'existant via
     `open_catalog` (s'il est un `catalog.db` valide, schéma déjà là ; le merge **ajoute** dedans de
     façon idempotente) — on **n'efface pas** : append idempotent, pas truncate.
2. **`--into <source>`** : la destination est **une des sources**, et **doit** figurer dans la liste
   des `sources` (sinon `MergeError` : `--into` doit désigner une source listée). On **n'attache pas
   la dest à elle-même** : on merge les **autres** sources dans la dest (la dest est déjà son propre
   contenu ; l'idempotence garantit qu'on n'y duplique rien). `--into` est le **seul** moyen de
   pointer une dest = source, et il est **explicite** (pas de heuristique).
3. **`--output` et `--into` sont mutuellement exclusifs** (groupe `add_mutually_exclusive_group`).
   **Au moins un des deux est requis** (`required=True` sur le groupe) — pas de dest implicite.
4. **`--force` n'a de sens qu'avec `--output`** (`--into` désigne déjà délibérément un fichier
   existant). Si `--force` est passé avec `--into` → soit ignoré silencieusement, soit `MergeError`
   (préférer **`MergeError` clair** : « `--force` n'a pas de sens avec `--into` »).
5. **Une source qui n'existe pas** (fichier absent) → `MergeError` clair **avant** d'ouvrir/créer la
   sortie (fail-fast : on ne crée pas un fichier de sortie pour ensuite échouer).

**Sortie & codes** : `main(argv) -> int` ; `0` = merge OK, `≠0` (p.ex. `2`) = erreur d'usage/merge
(message clair sur `stderr`, jamais de traceback nu — même esprit que `composition/__main__.py:78-86`).
Logging : `logging.basicConfig(level=INFO)` + une ligne récap par source mergée (compteurs
insérés/ignorés) et un total final. **Pas de variable d'environnement** (doctrine du repo, spec §3).

---

## 7. Plan de tests TDD (100 % branch, sans Docker)

**TDD strict** : pour chaque comportement, écrire le test qui **échoue d'abord**, le voir échouer,
puis l'implémentation minimale. Chaque fonction de test est annotée `-> None`, params typés
(`tmp_path: Path`, etc.). `mypy --strict` sur **src ET tests**. **Tout est testable sans Docker** :
on crée N `catalog.db` temporaires (via `open_catalog` + quelques `INSERT` directs, exactement comme
`test_append_only.py` et `test_catalog_repository.py`), on merge, on asserte.

**Helpers de test** (un module/fixture partagé) :
- `make_catalog(tmp_path, name, rows) -> Path` : crée un `catalog.db` réel via `open_catalog`, insère
  des `files`/`sources`/journaux donnés (INSERTs directs ou via `SqliteCatalogRepository` pour les
  observations), ferme, rend le chemin. **Fichier réel** (WAL exige un fichier, pas `:memory:`).
- Helpers de comptage : `count(db, table)`, `rows(db, table_sans_id)` pour asserter contenu et
  cardinalité.

**Cas (chaque conditionnel → ses deux branches) :**

| # | Test | Ce qu'il prouve / branche couverte |
|---|---|---|
| **T1** | `merge_two_distinct_catalogs` | 2 sources, contenus disjoints (fichiers/sources/journaux différents) → sortie = **union** ; cardinalités = somme ; toutes les lignes présentes ; FK satisfaites. Le cas nominal. |
| **T2** | `merge_overlapping_identity_files_or_ignore` | 2 sources partagent un `ed2k_hash` (et un `user_hash`) → **une seule** ligne `files`/`sources` en sortie (`INSERT OR IGNORE`, premier gagne). Couvre la branche « PK déjà présente ». |
| **T3** | `re_merge_is_idempotent` | Merger les mêmes sources **deux fois** dans la même sortie (`--force` au 2ᵉ passage) → contenu **identique** au 1ᵉʳ (compteurs inchangés sur toutes les tables). **Le test central** de l'idempotence (`WHERE NOT EXISTS` ⇒ 0 insertion au re-merge). |
| **T4** | `journal_dedup_identical_rows` | 2 sources contiennent une observation **bit-pour-bit identique** (même `ed2k_hash`, `observed_at`, `node_id`, … — y compris colonnes **NULL**) → **une seule** ligne en sortie. Prouve la dédup `WHERE NOT EXISTS` **avec `IS`** sur des NULL (sans `IS`, deux lignes → test rouge). Une variante avec `observed_at` qui diffère → **deux** lignes conservées (clés naturelles distinctes). |
| **T5** | `journal_drops_local_id` | 2 sources ont chacune une observation distincte `id=1` → sortie a **2 lignes** avec des `id` **réassignés** (1 et 2), pas une collision. Prouve qu'on **ne copie pas** `id`. |
| **T6** | `fk_order_inserts_identity_first` | Une source dont un journal référence un `ed2k_hash`/`user_hash` → merge **réussit** (identités insérées avant journaux). *(Garantie par construction ; ce test verrouille l'ordre — un ordre inversé lèverait une erreur FK.)* |
| **T7** | `output_exists_without_force_errors` | `--output` pointe un fichier **existant**, pas de `--force` → `MergeError`, code ≠ 0, **fichier existant inchangé** (pas d'écrasement). Branche « sortie existe & pas force ». |
| **T8** | `output_exists_with_force_appends` | Même cas **avec** `--force` → merge dans l'existant, idempotent. Branche « sortie existe & force ». |
| **T9** | `into_explicit_dest_is_a_source` | `--into srcA` avec `sources = [srcA, srcB]` → merge srcB **dans** srcA ; srcA contient désormais l'union ; idempotent. Branche `--into`. |
| **T10** | `into_must_be_a_listed_source` | `--into srcX` où `srcX` n'est **pas** dans `sources` → `MergeError` clair. Branche de validation `--into`. |
| **T11** | `output_and_into_mutually_exclusive` | Passer `--output` **et** `--into` → erreur argparse (code 2). Et : **ni l'un ni l'autre** → erreur (groupe `required`). Les deux branches du groupe mutuellement exclusif/requis. |
| **T12** | `force_with_into_is_rejected` | `--force` + `--into` → `MergeError` clair (`--force` n'a pas de sens avec `--into`). |
| **T13** | `missing_source_file_errors_before_output_created` | Une source absente → `MergeError` **avant** création de la sortie (asserter que le fichier de sortie n'a **pas** été créé). Fail-fast. |
| **T14** | `aich_first_wins_documented` | srcA a `files(ed2k=H, aich=NULL)`, srcB a `files(ed2k=H, aich='X')` ; merge dans l'ordre A→B → la ligne `files` garde **`aich=NULL`** (premier gagne, ride §6). Et l'ordre B→A → `aich='X'`. **Documente** le comportement (ce test *fige* la ride, il ne la corrige pas). |
| **T15** | `append_only_triggers_present_on_output` | Après merge, un `UPDATE`/`DELETE` direct sur la sortie est **rejeté** (`<table> est append-only`). Prouve que la sortie a bien le schéma + triggers (créée via `open_catalog`). |
| **T16** | `merger_wraps_source_copy_in_a_transaction` | Une source dont la copie échoue à mi-parcours (p.ex. injection d'une erreur, ou une source au schéma cassé) → `ROLLBACK` : la sortie ne contient **pas** de copie partielle de cette source. Couvre le `except`/`ROLLBACK`. |
| **T17** | `single_source_merge` | `nargs="+"` avec **une seule** source → sortie = copie idempotente de cette source (cas dégénéré N=1, utile comme « normaliseur »/dédup `file_verifications` d'un seul catalogue). |

**Couverture des branches** : chaque `if`/garde du CLI et du merger doit voir ses **deux** côtés
(sortie existe / n'existe pas ; force / pas force ; into / output ; source présente / absente ;
`NOT EXISTS` vrai / faux). La table ci-dessus est conçue pour les couvrir toutes. Le module
`merge/errors.py` (`MergeError`) et chaque branche d'`argparse` (codes 0/2/≠0) sont exercés.

**Gate à faire passer (les 6, depuis la racine et par paquet)** :
```bash
( cd packages/crawler && uv run pytest -q )      # 100 % branch, le module merge inclus dans --cov=emule_indexer
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run sqlfluff lint packages/crawler/src        # tout SQL embarqué dans merger.py doit passer sqlfluff
```

> **sqlfluff** : si le SQL du merger est dans des **constantes Python multi-lignes** (comme
> `catalog_repository.py`), il **n'est pas** lu par `sqlfluff lint` (qui ne lint que les `.sql`).
> Mais le SQL des *migrations* l'est. **Décision figée** : le merger garde son SQL en **constantes
> Python** (cohérent avec `catalog_repository.py`, pas de nouveau `.sql`). On **ne crée pas** de
> fichier de migration (aucun changement de schéma). Donc `sqlfluff` reste vert sans action
> spécifique — mais **vérifier** qu'aucun `.sql` n'est ajouté par mégarde.

---

## 8. Risques / à confirmer

- **`ATTACH` en lecture seule (`mode=ro`)** : nécessite `sqlite3.connect(uri=True)` sur la connexion
  de sortie ; `open_catalog` n'active **pas** `uri=True`. Trois options : (a) ouvrir la connexion de
  sortie soi-même avec `uri=True` puis appliquer la migration via une fonction publique du runner —
  mais `open_catalog` ne prend pas ce paramètre aujourd'hui ; (b) accepter l'`ATTACH` normal (on
  n'écrit jamais dans `src`, donc lecture seule *de facto*) ; (c) un petit ajout `uri`-aware. **Décision
  par défaut figée : (b)** — attache normale, aucune écriture émise vers la source. Si l'implémenteur
  juge (a)/(c) plus sûr **et** que ça ne touche pas le crawler PROD, le noter au rapport. **Ne pas
  modifier `open_catalog` au point de changer son contrat pour le crawler.**
- **Limite du nombre de bases attachées** : SQLite a un plafond d'`ATTACH` simultanés
  (`SQLITE_MAX_ATTACHED`, **10** par défaut). On attache **une source à la fois** (`ATTACH` → copie →
  `DETACH`), donc on reste à **1 base attachée** quel que soit N. Pas de risque même pour N grand. À
  **confirmer** par T1 avec N≥3.
- **Versions de schéma hétérogènes entre sources** : si une source est d'une `user_version` plus
  **récente** que le code (schéma futur), le merge pourrait copier des colonnes inattendues ou
  échouer obscurément. **Mitigation suggérée (à confirmer)** : lire `PRAGMA user_version` de chaque
  source et lever un `MergeError` clair si > version attendue (fail-fast), avant tout `INSERT`.
  Symétriquement, une source plus **ancienne** sans une colonne ajoutée par une migration future
  poserait problème — **hors-scope tant qu'il n'y a qu'une migration catalog** (`0001`). À revisiter
  si une `0002` catalog apparaît.
- **`INSERT … SELECT *` vs colonnes explicites** : pour `files`/`sources` (PK de contenu), `SELECT *`
  est acceptable *tant que l'ordre des colonnes du schéma est stable* (il l'est, single source of
  truth = migration). Pour les **journaux**, on **n'utilise jamais `*`** (il inclurait `id`) : colonnes
  **explicites** sans `id`. **Décision figée** : par cohérence et robustesse au futur, **lister les
  colonnes explicitement partout** (y compris `files`/`sources`) — un `SELECT *` casserait
  silencieusement si une migration future ajoutait une colonne. C'est plus verbeux mais aligné sur la
  discipline du repo (les inserts prod listent toujours les colonnes, `catalog_repository.py:42-58`).
- **`raw_meta`/`real_meta`/`checks` — égalité textuelle** : la dédup compare le **JSON sérialisé** tel
  quel (`IS` textuel). Deux nœuds qui ont vu la même chose produisent la même chaîne (sérialisation
  déterministe, sans tri). Si jamais une divergence de sérialisation apparaissait (versions de Python,
  espaces), deux observations « identiques » seraient conservées toutes les deux — **non-destructeur**
  (jamais de fausse fusion), juste un doublon résiduel. Acceptable ; noté.
- **Performance** : `WHERE NOT EXISTS` corrélé sur la clé naturelle complète peut être lent sur de
  gros journaux (pas d'index sur la clé naturelle). À l'échelle MVP (un réseau de chercheurs, pas un
  data center), **acceptable** ; ne pas ajouter d'index spéculatif (prématuré). Si besoin un jour :
  index couvrant ad hoc **sur la base de sortie** (jamais sur le schéma prod). Noté, non corrigé.
- **`--into` et WAL** : merger *dans* une source `--into` ouvre cette source en écriture via
  `open_catalog` (WAL, migrations no-op si déjà à jour). Vérifier qu'attacher **les autres** sources
  pendant que la dest est ouverte en WAL ne pose pas de souci de verrou (writer unique par doctrine ;
  une seule connexion de sortie ouverte ici → OK). Couvert par T9.
