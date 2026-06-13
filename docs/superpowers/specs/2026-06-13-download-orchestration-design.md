# Spec — emule-indexer : D-download (orchestration du téléchargement)

> **Sous-projet** : premier des deux issus de la scission de « Plan D » (auto-download +
> verifier). Ici : **D-download** — la capacité de téléchargement côté crawler, qui rejoue
> le journal `match_decisions`, télécharge via EC les fichiers de tier `download`, et les
> remet en quarantaine + enfile une tâche de vérification. **D-verify** (le verifier +
> la boucle de vérification + le gate mode full) est le sous-projet suivant, brainstormé à
> la suite.
>
> Réfs : MVP design `2026-06-10-crawler-mvp-design.md` §3 (modes), §9 (auto-download),
> §10 (sécurité/quarantaine), §11/§12 (modèle de données). Orchestration `2026-06-12-
> orchestration-design.md` (les patterns réutilisés : data-coupling + nudge, worker/loop,
> arrêt observable, déterminisme). Handoffs data-model et orchestration.

---

## 1. But & périmètre

**But** : à partir des décisions `tier=download` déjà produites par le moteur (Plan C,
journal `match_decisions`), télécharger les fichiers correspondants via un `amuled` réel
piloté en EC, puis les **remettre en quarantaine** et **enfiler une tâche de vérification**
— sans jamais lire les octets. C'est le producteur de la file de vérification que D-verify
consommera.

**Dans le périmètre** :
- Extension du client EC avec les **opérations de téléchargement** (ajout de lien ed2k,
  lecture de la file/statut de download, chemin du fichier) — opcodes figés par un **probe
  empirique** contre un amuled réel au début du plan (méthode du Plan B).
- La **boucle de download** (`run_download_cycle`) : monitor → complétions → nouveaux
  candidats → sleep/nudge ; réconciliation au redémarrage.
- La **politique auto-DL pure** (domaine) : `tier=download` ∧ `status∈{lost,partial,poor}`
  ∧ dédup par hash ∧ **plafond disque applicatif** (back-pressure gracieux).
- Le **port `Quarantine`** + adapter FS (rename atomique `staging→quarantine/<hash>`).
- Le **repo `downloads`** (local.db) + **migration 0002** ajoutant `size_bytes`.

**Hors périmètre (→ D-verify ou plus tard)** :
- Le **service verifier**, le port `ContentVerifier`, la **boucle de vérification**, et le
  **câblage live dans `CrawlerApp` + le gate mode full `VERIFIER_URL`/health-check** →
  **D-verify** (le gate a besoin du port `ContentVerifier` pour pinger le verifier).
- Les **upgrades** (re-DL d'une meilleure version pour `partial`/`poor`) — exigent les
  métadonnées média post-download (vrai verifier) pour comparer bitrate/durée → itération
  ultérieure.
- Le **quota disque infra** (Docker/FS) → **Plan F** (packaging). Ici, seul le plafond
  applicatif.
- Le **vrai download bout-en-bout en CI** (option B, deux amuled) → amélioration future ;
  ici **option A** (mécaniques EC réelles + complétion en unitaire + homelab manuel).

## 2. Décisions verrouillées (issues du brainstorm)

1. **Scission** D-download (ici) / D-verify (suivant), brainstormés à la suite, conçus pour
   le **fonctionnement réel** (pas d'interrupteur jetable).
2. **Endpoint de download = entrée de config à part entière** (host/port/password propres),
   avec sa **propre connexion EC**. Le code se fiche que l'opérateur le fasse pointer sur un
   daemon dédié ou partagé avec une instance de recherche. Mono-instance (décision Plan C).
3. **Une seule boucle de download** (pas deux tâches) → la connexion EC download n'a qu'un
   usager, aucun entrelacement de trames.
4. **Port séparé `MuleDownloadClient`** (pas d'extension de `MuleClient`) — ISP : la
   recherche ne dépend pas des méthodes de download et inversement. Même classe adapter
   peut l'implémenter.
5. **Périmètre politique** : status-gate + dédup + plafond disque applicatif. On **s'appuie
   sur le matcher** pour la pertinence type/taille (déjà dans les règles). Upgrades différés.
6. **Plafond disque applicatif** (somme des `size_bytes` des downloads actifs vs plafond
   config → diffère au-dessus). Pas de plafond de downloads concurrents (YAGNI). Quota infra
   → Plan F.
7. **Jamais d'abandon** d'un download stallé (amuled persiste/reprend aux fenêtres
   d'intermittence, §9). Back-pressure uniquement par le plafond disque.
8. **Migration 0002** : `downloads.size_bytes` (le plafond reste une requête local.db simple,
   table auto-décrite).
9. **Couture d'activation** : D-download livre la **capacité testée** ; le câblage live dans
   `CrawlerApp` + le gate full-mode atterrissent en **D-verify**.
10. **E2e option A** : mécaniques EC réelles contre un amuled réel ; complétion en unitaire ;
    homelab manuel documenté. Option B (deux amuled) = amélioration future.

## 3. Architecture & carte des fichiers

Clean/Hexagonal inchangé. Règle de dépendance : `domain` pur ; `ports` n'importent que le
domaine ; `application` dépend des ports/domaine, jamais d'un adapter ; `composition`
assemble. Le download réutilise : `Clock`/`Rng` injectés, `DecisionSignal` (nudge),
`RepositoryError`/`MuleUnreachableError` (contrats de port), l'arrêt observable du `TaskGroup`.

```
src/emule_indexer/
├── domain/download/                    # PUR (nouveau sous-paquet)
│   ├── policy.py        # download_policy(...) -> DownloadVerdict (enum)
│   ├── ed2k_link.py     # build_ed2k_link(filename, size_bytes, ed2k_hash) -> str (échappement)
│   └── states.py        # DownloadState (queued/downloading/completed/quarantined/failed)
├── ports/
│   ├── mule_download_client.py   # MuleDownloadClient (Protocol async) + DownloadEntry DTO
│   └── quarantine.py             # Quarantine (Protocol) : promote(staging, hash)
├── application/
│   └── run_download_cycle.py     # la boucle unique (monitor→complétions→candidats→sleep/nudge)
├── adapters/
│   ├── mule_ec/…                 # extension download (add_link, download_queue) — opcodes via PROBE
│   ├── quarantine_fs.py          # rename atomique même-FS ; jamais +x, jamais lu
│   └── persistence_sqlite/
│       ├── download_repository.py   # SqliteDownloadRepository (table `downloads`)
│       └── migrations/local/0002_downloads_size_bytes.sql
└── (composition/app.py : NON modifié ici — câblage live = D-verify)

config/crawler.yaml   # + download.poll_interval, download.disk_cap_bytes
config/local.example.yaml  # + download endpoint (host/port/password) + staging/quarantine paths
pyproject.toml        # + marqueur download_integration
```

## 4. Port `MuleDownloadClient` (+ probe empirique)

Protocol async, séparé de `MuleClient`. Surface visée (à figer par le probe) :

```
connect() / close()
add_link(ed2k_link: str) -> None            # EC : ajoute le lien à la file de download
download_queue() -> tuple[DownloadEntry, ...]  # EC : snapshot de la file (hash, état, progrès, chemin?)
network_status() -> NetworkStatus           # réutilisé (HighID requis pour DL en mode full)
```

`DownloadEntry` (DTO de port, frozen) : au minimum `ed2k_hash`, un **état/avancement**
(p. ex. `size_done`/`size_full` ou un flag complet), et si EC l'expose, le **chemin staging**
du (part)fichier. **Probe empirique obligatoire** (début de plan, comme le field-richness du
Plan B) : opcodes exacts d'ajout de lien (`EC_OP_ADD_LINK`/partfile…), de lecture de la file
de download (`EC_OP_GET_DLOAD_QUEUE`/tags partfile : `EC_TAG_PARTFILE_*`), présence du
chemin du fichier complété, et signalement d'erreur/suppression d'un download. Le rapport
s'ajoute à `docs/reference/`. **Le crawler ne lit jamais les octets** : `download_queue`
ne renvoie que des métadonnées EC.

## 5. La boucle `run_download_cycle` (application)

Une **seule tâche**, série, qui à chaque itération :

1. **Monitor** : `download_queue()` → met à jour les états dans `downloads` (réconciliation
   avec la vraie file amuled).
2. **Complétions** : pour chaque hash complet et pas encore `quarantined` →
   `quarantine.promote(staging, hash)` → `local_repo.enqueue_verification(hash)` →
   `set_state(hash, quarantined)`. **Idempotent** : si `promote` échoue, on laisse en
   `completed` et on n'enfile PAS (retry à l'itération suivante) ; un hash déjà `quarantined`
   est sauté.
3. **Nouveaux candidats** : décisions `tier=download` dont le hash n'est pas dans `downloads`
   (latest-decision-per-hash, lecture catalogue + diff local) → pour chacun, `download_policy`
   (status via lookup `target_id→status`, dédup, plafond via `committed_bytes + size`) → si
   `download` : `build_ed2k_link` → `add_link()` → `record_queued(hash, target_id, size_bytes)`.
4. **Sleep/nudge** : attend `download.poll_interval` **OU** le hub `DecisionSignal` (réveil
   sur changement de décision) — data-coupling + nudge du Plan C.

**Redémarrage** : la première itération réconcilie (étape 1) la file réelle amuled (rattrape
les complétions offline), puis le replay (étape 3) saute les hash déjà connus. Writer unique
sur l'event loop mono-thread → aucun verrou. `Clock`/`Rng`/`sleep` injectés → déterminisme,
zéro flaky.

## 6. Politique pure (domaine)

```
download_policy(*, tier, target_status, already_downloaded, committed_bytes, file_size,
                disk_cap) -> DownloadVerdict
```
`DownloadVerdict` ∈ {`download`, `skip_complete`, `skip_dedup`, `skip_disk_cap`} (enum, pas
bool → explicabilité + métrique). Règles : `tier != download` → non-candidat (garde) ;
`target_status == complete` → `skip_complete` ; `already_downloaded` → `skip_dedup` ;
`committed_bytes + file_size > disk_cap` → `skip_disk_cap` (diffère, la décision reste dans
le journal) ; sinon → `download`. Le lookup `target_id → status` est fait par l'application
(depuis les `targets` chargées) et passé en **primitif** au domaine (le domaine n'importe ni
le repo ni `NetworkStatus` — comme `effective_coverage` reçoit des booléens). Toutes branches
testées des deux côtés.

## 7. Persistance — repo `downloads` + migration 0002

Table `downloads` (local.db, **writer unique** = crawler, **non append-only** → UPSERT
licite). Migration **0002** ajoute `size_bytes INTEGER NOT NULL` (le plafond reste une
requête local.db simple). `SqliteDownloadRepository` (sync, mêmes disciplines que les autres
adapters : `BEGIN IMMEDIATE` + `wrap_sqlite_errors` + rollback `BaseException` + staging avant
`BEGIN`) :
- `record_queued(hash, target_id, size_bytes)` — INSERT, dédup-safe (PK = hash).
- `set_state(hash, state)` ; `completed_at` stampé à la complétion (horloge injectée).
- `active_downloads() -> tuple[...]` (pour le monitor + la somme).
- `committed_bytes() -> int` (somme `size_bytes` des états non terminaux).
- `is_downloaded(hash) -> bool` (dédup) ; `reconcile(snapshot)` (aligne sur la file amuled).

## 8. Port `Quarantine` + adapter FS

`Quarantine.promote(staging_path, ed2k_hash) -> None` : rename **atomique même-FS**
`staging/<nom>` → `quarantine/<hash>` (§10.5). L'adapter `quarantine_fs` : jamais `+x`,
jamais ouvert/lu (le crawler ne lit jamais le contenu, §10.3) ; opération de métadonnée
seule. Échec (fichier absent, FS) → exception → la boucle laisse le download en `completed`
et retente (idempotent). Testé avec un vrai `tmp_path`.

## 9. Gestion d'erreurs & résilience (réutilise les contrats Plan C)

- **Daemon download injoignable** (`MuleUnreachableError`) → on **tolère** : skip l'itération,
  retry au tour suivant (amuled persiste les downloads). Pas de crash.
- **amuled signale une erreur/suppression** d'un download → état `failed` + métrique
  (signalement EC à confirmer au probe).
- **Plafond disque atteint** → `skip_disk_cap` : diffère, la décision reste dans le journal,
  retentée quand de la place se libère.
- **`RepositoryError`** → absorbée (log + continue) ; une mauvaise écriture ne tue pas la boucle.
- **Échec `quarantine.promote`** → reste `completed`, **n'enfile pas** la vérif (le fichier
  doit être sûrement en quarantaine d'abord) ; retry idempotent.
- **Jamais d'abandon** d'un download stallé.
- **Arrêt** : tâche du `TaskGroup` ; annulation au prochain `await` (EC poll ou sleep), jamais
  en pleine écriture DB (repos sync) ; observable & borné, comme le Plan C.

## 10. La couture d'activation (→ D-verify)

D-download livre la **capacité** : domain/download, les ports (`MuleDownloadClient`,
`Quarantine`, repo `downloads`), les adapters (extension EC, `quarantine_fs`, sqlite repo),
la migration, et `run_download_cycle`. **Le câblage live dans `CrawlerApp` + le gate mode
full** (`VERIFIER_URL` + health-check, §3) **atterrissent en D-verify** — le health-check a
besoin du port `ContentVerifier` (qui ping le verifier), qui est D-verify. Aucun interrupteur
jetable : on ne peut vraiment pas activer le download sans verifier (§3), et chaque
sous-projet reste entièrement testé. D-download est exercé par ses propres tests ; D-verify
l'allume pour de vrai.

## 11. Stratégie de tests (TDD, 100 % branch)

**Unitaire** : `download_policy` (toutes branches : complete/dedup/disk-cap/download) ;
`build_ed2k_link` (échappement `|`/nom, champs) ; la boucle `run_download_cycle` avec **faux**
`MuleDownloadClient` + **fausse** `Quarantine` + **vrai** repo sqlite sur `tmp_path` (replay,
nudge, complétion→quarantine→enqueue, dédup, plafond/défère, réconciliation au redémarrage,
tolérance injoignable, `RepositoryError` absorbée, échec promote→pas d'enqueue, arrêt) ;
le repo `downloads` (round-trips, états, committed_bytes, migration 0002 appliquée) ;
`quarantine_fs` (rename réel sur `tmp_path`, échec). Faux `Clock`/`Rng` → déterminisme.

**E2e opt-in** (marqueur `download_integration`, Docker, **hors coverage**, désélectionné par
défaut) : le `MuleDownloadClient` contre un **amuled réel** — `add_link` puis voir le hash
apparaître dans `download_queue` et lire son statut via EC. La complétion n'est **pas**
atteignable (pas de sources, option A).

**Homelab manuel documenté** : vrai download → quarantine → enqueue (comme le run homelab
d'`ec_probe`), à consigner dans `docs/reference/`.

## 12. Definition of Done

- domain/download (policy, ed2k_link, states) pur, 100 % branch.
- Port `MuleDownloadClient` + extension EC adapter, **probe documenté** dans `docs/reference/`.
- Port `Quarantine` + `quarantine_fs`.
- Repo `downloads` + **migration 0002** (`size_bytes`), lintée sqlfluff.
- `run_download_cycle` (boucle unique, flux §5), réutilisant nudge/clock/rng/contrats d'erreur.
- Config : `download.poll_interval`/`disk_cap_bytes` (crawler.yaml), endpoint + chemins
  (local.example.yaml).
- Gate 5 checks vert (100 % branch) + e2e `download_integration` vert (mécaniques EC) contre
  un amuled réel.
- **NON inclus** (D-verify) : câblage live `CrawlerApp`, gate full-mode, verifier, boucle de
  vérification.
