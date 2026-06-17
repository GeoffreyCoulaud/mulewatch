# Design — détection de complétion par la liste des fichiers partagés EC (2026-06-17)

> Suite de DV10. Remplace la détection de complétion **byte-based** (défensive) par un **signal
> positif** : un fichier fini est auto-partagé par amuled, donc visible via `EC_OP_GET_SHARED_FILES`,
> avec son **vrai nom sur disque**. Ça résout aussi **DV10-Q2** (collision de noms `nom(0)`) par
> construction et supprime la contrainte « TempDir/IncomingDir même volume ».
> Faits amont : [`docs/reference/2026-06-17-amuled-completion-behavior.md`](../../reference/2026-06-17-amuled-completion-behavior.md)
> (commit aMule `5938915`).

## 1. Motivation (ce que l'amont nous a appris)

À la complétion, `CPartFile::CompleteFileEnded` (succès) exécute, dans l'ordre et **sur le thread
principal sans yield à EC** : `SetStatus(PS_COMPLETE)` → `knownfiles->SafeAddKFile` →
`sharedfiles->SafeAddKFile(this)` → `downloadqueue->RemoveFile(this, true)`. Conséquences établies :

- **`PS_COMPLETE`(9) est inobservable via `EC_OP_GET_DLOAD_QUEUE`** : le fichier quitte `m_filelist`
  au moment exact où il passe à 9, et la file EC n'inclut pas `m_completedDownloads`
  (`CopyFileList(..., includeCompleted=false)` par défaut). On ne peut donc PAS jauger la complétion
  sur le statut de la file.
- **Notre détection byte-based** (`is_complete = size_full>0 and size_done>=size_full`) se déclenche
  pendant `PS_COMPLETING`(8), c.-à-d. **avant** que le fichier soit en place — d'où un `os.replace`
  qui peut échouer transitoirement (retry idempotent) et, surtout, un **nom deviné** (`resolve_staging_path`
  depuis le catalogue) qui **diverge en cas de collision** : amuled écrit `nom(0).ext`, on cherche
  `nom.ext` → blocage permanent (DV10-Q2).
- **Mais le fichier fini devient un fichier partagé** (`sharedfiles`), exposé par
  `EC_OP_GET_SHARED_FILES` via `CEC_SharedFile_Tag`, qui porte `EC_TAG_PARTFILE_NAME =
  GetFileName().GetPrintable()` = **le vrai nom on-disk** (post-cleanup ET post-dédup) + le hash.

→ La liste des partagés est un **signal de complétion positif, persistant, et porteur du vrai nom**.

## 2. Objectif / non-objectifs

**Objectif (strict nécessaire).** Détecter la complétion d'un téléchargement suivi par sa présence
dans la liste des fichiers partagés EC, et promouvoir en quarantaine en utilisant le **nom réel**
rapporté par amuled (plus le `staging_dir` configuré).

**Non-objectifs (décidés avec Geoffrey).**
- Pas de mapping `PS_ERROR → FAILED` (un download en erreur n'apparaît simplement jamais dans les
  partagés → jamais promu, ce qui est correct ; le signalement explicite est hors périmètre).
- Pas d'usage du chemin complet amuled (`EC_TAG_KNOWNFILE_FILENAME`) : il est dans le namespace de
  montage d'amuled, potentiellement ≠ du nôtre. On utilise le **basename** + notre `staging_dir`.

## 3. Constantes EC (confirmées amont, commit `5938915`)

| Constante | Valeur | Rôle |
|---|---|---|
| `EC_OP_GET_SHARED_FILES` | `0x10` | requête (détail `EC_DETAIL_CMD` = `0x00`) |
| `EC_OP_SHARED_FILES` | `0x22` | réponse : N enfants `EC_TAG_KNOWNFILE` |
| `EC_TAG_KNOWNFILE` | `0x0400` | conteneur par fichier partagé |
| `EC_TAG_PARTFILE_NAME` | `0x0301` (déjà défini) | vrai nom on-disk (`GetFileName`) |
| `EC_TAG_PARTFILE_HASH` | `0x031E` (déjà défini) | hash MD4 (HASH16) pour le match |

`EC_DETAIL_CMD` (0x00) suffit : `CEC_SharedFile_Tag` n'omet name+hash que pour `EC_DETAIL_UPDATE`.
Le tag conteneur `EC_TAG_KNOWNFILE` (0x0400) est le défaut de `CEC_SharedFile_Tag` — **à confirmer
empiriquement par le test d'intégration de décodage** (comme R3/R4 l'ont été).

## 4. Design

### 4.1 Couche EC (adapter `mule_ec`)

- **`codes.py`** : ajouter `EC_OP_GET_SHARED_FILES=0x10`, `EC_OP_SHARED_FILES=0x22`,
  `EC_TAG_KNOWNFILE=0x0400`.
- **DTO** `SharedFileEntry(ed2k_hash: str, name: str)` (frozen) dans `ports/mule_download_client.py`,
  à côté de `DownloadEntry`. Minimal : on n'a besoin que du hash (match) et du nom (promotion).
- **`AmuleEcClient.shared_files() -> tuple[SharedFileEntry, ...]`** (`client.py`) : émet
  `EC_OP_GET_SHARED_FILES` au détail `CMD`, attend `EC_OP_SHARED_FILES`, itère les enfants
  `EC_TAG_KNOWNFILE`, mappe chacun via `_map_shared_file`. **Tolérance aux inconnus** identique à
  `_map_partfile`/`map_search_results` : une entrée sans hash **ou** sans nom exploitable est
  **écartée**, jamais fatale.
- **`_map_shared_file(entry) -> SharedFileEntry | None`** : `find(EC_TAG_PARTFILE_HASH)` (HASH16, 16
  octets → hex) + `find(EC_TAG_PARTFILE_NAME)` (string) ; absence/pourri → `None`.
- **Port** : ajouter `shared_files()` au Protocol `MuleDownloadClient`.

### 4.2 Détection de complétion (application `run_download_cycle`)

Le cycle reste **monitor → complétions → candidats → sleep/nudge**, mais l'étape complétion change
de source de vérité :

- **Étape 1 `_monitor` (download_queue)** : conservée pour suivre les **téléchargements en cours**
  (`DOWNLOADING`) et ne pas régresser un état terminal. **On retire la transition byte-based vers
  `COMPLETED`** (la complétion vient désormais des partagés). `is_complete`/`size_done`/`size_full`
  restent utiles pour la progression/télémétrie mais ne déclenchent plus la promotion.
- **Étape 2 `_handle_completions` (shared_files)** : appelle `client.shared_files()` ; pour chaque
  `SharedFileEntry` dont le hash est **suivi et non terminal** (`states[hash] ∉ {QUARANTINED,
  FAILED}`), promeut directement avec le **nom réel** : `quarantine.promote(staging_dir /
  safe_basename(entry.name), hash)` → `enqueue_verification` → `set_state(QUARANTINED)`.
  - **Idempotence sans persister le nom** : si `promote` échoue (panne FS), l'état reste
    non-terminal ; au tour suivant le hash est **toujours** dans les partagés (signal persistant) →
    re-détecté, re-promu. Pas besoin de stocker le nom dans la DB.
  - **Share fantôme** après le `os.replace` (amuled garde une entrée partagée pointant un fichier
    déplacé) : inoffensif — l'état est `QUARANTINED` (terminal), donc l'entrée est ignorée aux tours
    suivants.

### 4.3 Suppression de `resolve_staging_path`

`resolve_staging_path` (`composition/app.py`) devinait le nom depuis `catalog.last_observation` —
remplacé par le **nom réel des partagés**. À **supprimer** (fonction + `test_staging_resolver.py` +
le `staging_path_for`/`CatalogReader` qui ne servaient qu'à ça). Le **confinement anti-traversal est
conservé** mais déplacé : `safe_basename(name)` = `Path(name).name`, rejet de `""`/`.`/`..` → l'entrée
est écartée (on ne promeut pas un nom dégénéré ; le hash réapparaîtra au tour suivant). Le nom reste
une **entrée externe** (défense en profondeur), même s'il vient d'amuled.

### 4.4 Docs à mettre à jour (dans le périmètre)

- `docs/reference/2026-06-17-amuled-completion-behavior.md` : ajouter la section « détection par les
  partagés » et requalifier la note Q2 (résolue) + la note « même volume » (caduque).
- `docs/runbook-deployment.md` : retirer la contrainte « même volume » et la limite Q2 ; ajouter la
  reco « amuled **dédié**, jeu partagé restreint » (cf. §6).
- `CLAUDE.md` : mettre à jour la ligne DV10 (complétion via shared-files, Q2 résolu).

## 5. Tests (TDD, 100 % branch)

- **Unitaires (cœur du gate)** :
  - codec/mapping `_map_shared_file` : entrée complète → `SharedFileEntry` ; sans hash → `None` ;
    sans nom → `None` ; hash malformé → `None` ; tag inconnu toléré.
  - `shared_files()` : faux transport, réponse `EC_OP_SHARED_FILES` avec N `EC_TAG_KNOWNFILE` →
    tuple correct ; opcode inattendu → erreur ; réponse vide → tuple vide.
  - `_handle_completions` avec un **faux client** renvoyant des partagés : hash suivi non-terminal →
    promu avec le bon nom + enqueue + `QUARANTINED` ; hash déjà `QUARANTINED` → ignoré ; hash non
    suivi → ignoré ; `promote` qui lève → reste non-terminal (retry) ; nom dégénéré (`..`) → écarté.
- **Intégration `download_integration` (Docker, vrai amuled)** : partager un fichier dans le
  conteneur amuled (le déposer dans son dossier partagé / via `add` EC selon le plus simple) et
  vérifier que `shared_files()` le **décode** (hash + nom) → **confirme empiriquement** le conteneur
  `EC_TAG_KNOWNFILE` (0x0400) et le détail `CMD`. Ne nécessite **pas** de transfert réel.
- **Non testable** (assumé) : la chaîne complète « vrai download → apparition dans les partagés » —
  même raison que l'e2e abandonné (pas de transfert eD2k synthétique). La **logique** de complétion
  est couverte en unit ; le **décodage** EC contre un vrai amuled est couvert en intégration.

## 6. Risques / contraintes de déploiement

- **Coût** : un round-trip EC de plus par cycle (`GET_SHARED_FILES`). À borner : amuled doit rester
  **dédié au crawler** (ne pas pointer une grosse bibliothèque partagée pré-existante). Comme on sort
  les fichiers de l'Incoming à chaque cycle, la liste partagée reste petite.
- **Plus de contrainte « même volume »** (gain) : on n'agit qu'une fois le fichier partagé, donc déjà
  déplacé/en place ; la durée du rename/copie devient indifférente.
- **DV10-Q2 résolu** : le nom vient d'amuled (post-dédup), donc `nom(0).ext` est géré nativement.

## 7. Fichiers touchés (indicatif)

`adapters/mule_ec/codes.py`, `adapters/mule_ec/client.py` (`shared_files` + `_map_shared_file`),
`ports/mule_download_client.py` (`SharedFileEntry` + méthode port), `application/run_download_cycle.py`
(étapes 1/2), `composition/app.py` (suppression `resolve_staging_path` + recâblage promotion) ; tests
unitaires associés + `tests/integration/test_amuled_download.py` (décodage shared_files) ; docs (§4.4).
