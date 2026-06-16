# Guide des tests — emule-indexer

Ce guide décrit **comment lancer les tests d'intégration** (les suites « lourdes » désélectionnées
par défaut), leurs **prérequis exacts** et **ce qu'on doit attendre** en sortie. Il complète le
[runbook de déploiement](runbook-deployment.md) : le runbook explique comment faire tourner la
stack, ce guide-ci explique comment la **valider**.

Public visé : opérateur / dev / CI. Tout ce qui suit est **extrait du code réel** (fichiers de test,
`pyproject.toml`, fichiers compose). Quand un prérequis n'est pas vérifiable dans le code, c'est noté
« à confirmer ».

---

## 1. Vue d'ensemble — la pyramide de tests

Le projet a **deux niveaux** :

1. **Le gate unitaire** (lancé par défaut, **100 % de couverture de branches** imposée). C'est ce
   que vérifient le hook pre-push et la CI. Le gate est **par paquet** :

   ```bash
   ( cd packages/crawler  && uv run pytest -q )    # tests crawler, 100 % branch
   ( cd packages/verifier && uv run pytest -q )    # tests verifier, 100 % branch
   ```

   > Le gate est **par paquet** : un `uv run pytest` nu depuis la racine **n'est pas** le gate
   > (la racine n'a pas de config pytest ; un `conftest.py` racine neutralise toute collecte →
   > `exit 5`). Lancez toujours depuis `packages/<pkg>`.

   Les `addopts` de chaque paquet **désélectionnent** tous les marqueurs d'intégration
   (`-m "not ec_integration and not …"`), donc le gate ne les exécute jamais — ils sont aussi exclus
   de la mesure de couverture.

2. **Les suites d'intégration** (désélectionnées par défaut, lancées **à la demande**). Chacune
   porte un **marqueur** pytest. On les lance une par une avec `--no-cov` (sinon le seuil de 100 %
   appliqué au paquet entier fait « échouer » un run focalisé, même quand les tests passent) :

   ```bash
   ( cd packages/<pkg> && uv run pytest -m <marqueur> --no-cov )
   ```

   Ces suites exigent des ressources externes (Docker, ffmpeg, clamav…). **Elles ne tournent pas
   dans un sandbox sans accès réseau/Docker complet** : lancez-les sur une vraie machine.

---

## 2. Tableau récapitulatif des marqueurs

| Marqueur | Paquet | Ce que ça valide | Docker ? | Autres prérequis | Commande |
|---|---|---|---|---|---|
| `verify_integration` | crawler | Boucle de vérification ↔ vrai service verifier (in-process via ASGI) | Non | Aucun (les 2 paquets installés via `uv sync --dev`) | `( cd packages/crawler && uv run pytest -m verify_integration --no-cov )` |
| `analysis_integration` | verifier | Spawn réel de l'enfant confiné + vrai ffprobe (+ clamav/seccomp si dispo) | Non | **ffmpeg/ffprobe** (obligatoire) ; clamscan+base et seccomp/libseccomp (optionnels, sinon skip) | `( cd packages/verifier && uv run pytest -m analysis_integration --no-cov )` |
| `ec_integration` | crawler | Adaptateur EC (auth, statut réseau, cycle de recherche, get/set port) ↔ amuled réel | **Oui** (testcontainers) | Image `ngosang/amule:3.0.0-1` | `( cd packages/crawler && uv run pytest -m ec_integration --no-cov )` |
| `download_integration` | crawler | Mécaniques EC du download (`add_link` → file de download) ↔ amuled réel | **Oui** (testcontainers) | Image `ngosang/amule:3.0.0-1` | `( cd packages/crawler && uv run pytest -m download_integration --no-cov )` |
| `orchestration_integration` | crawler | Boucle de crawl complète (un cycle + arrêt borné) ↔ amuled réel | **Oui** (testcontainers) | Image `ngosang/amule:3.0.0-1` | `( cd packages/crawler && uv run pytest -m orchestration_integration --no-cov )` |
| `compose_integration` | crawler | Smoke e2e de la stack docker compose assemblée (sans VPN) — câblage seul | **Oui** (compose v2) | docker compose v2 ; build des 2 images | `( cd packages/crawler && uv run pytest -m compose_integration --no-cov )` |
| `e2e_integration` | crawler | Download → quarantaine → verify **RÉEL** de bout en bout (octets transférés) | **Oui** (compose v2) | docker compose v2 ; **`vendor/ed2kd` matérialisé** (gitignoré) ; image ed2kd buildable | `( cd packages/crawler && uv run pytest -m e2e_integration --no-cov )` |

> **À ne pas confondre :** le dossier `packages/crawler/tests/e2e/` (`test_planted.py`, `test_md4.py`,
> `test_ed2k_stub.py`) ne porte **aucun** marqueur d'intégration — ce sont des tests **unitaires** qui
> vérifient le binaire planté et le stub eD2k, et qui tournent **dans le gate par défaut**. Le marqueur
> `e2e_integration` ne concerne **que** `tests/integration/test_e2e.py`.

---

## 3. Une section par marqueur (du plus léger au plus lourd)

### 3.1 `verify_integration` (crawler, **sans Docker**)

**Ce que ça prouve.** La boucle de vérification du crawler (`run_verification_cycle`) parle au **vrai
service verifier** monté **in-process** (pas de Docker) : un fichier est pré-placé en quarantaine, une
tâche est enfilée, et le cycle réel (claim → verify via RPC → record → complete) produit une ligne
`file_verifications` avec verdict `suspicious` (3 octets ne sont pas un média). Cela valide le contrat
de fil DTO↔réponse + l'écriture durable, sans vrai download.

**Prérequis exacts.**
- **Aucun outil externe.** Le service `download_verifier` tourne in-process via
  `httpx.ASGITransport`. Le test importe `download_verifier.app.build_app` et `emule_indexer.*` — les
  deux paquets doivent être installés (`uv sync --dev`, déjà fait pour le gate).
- Aucun `skipif`, aucune variable d'environnement.

**Commande.**
```bash
( cd packages/crawler && uv run pytest -m verify_integration --no-cov )
```

**Attendu.** 1 test passé (`test_verify_loop_produces_suspicious_row`). Pas de skip.

---

### 3.2 `analysis_integration` (verifier, **sans Docker**, mais ffmpeg/clamav/seccomp)

**Ce que ça prouve.** Le côté verifier exécute **pour de vrai** le confinement (re-exec de l'enfant
jetable, rlimits, `setsid`, kill du groupe au timeout, env minimal) et le **vrai ffprobe** sur de
vrais échantillons, plus — si l'environnement le permet — le **vrai clamscan** (3ᵉ source de verdict)
et le **filtre seccomp réel**.

**Trois sous-cas, gérés par des `skipif` :**

1. **ffprobe (obligatoire pour l'essentiel des tests).** `skipif` actif si `ffmpeg` **ou** `ffprobe`
   est absent du PATH (`shutil.which`). Ces tests génèrent un petit média réel avec ffmpeg
   (`-f lavfi … -f matroska`) et vérifient les verdicts : média sain → `clean` + `real_meta` non
   vide ; ELF/shebang → `malicious` ; texte → `suspicious` ; egress sur-dimensionné → `suspicious` ;
   timeout (`ANALYSIS_TIMEOUT_S=0.001`) → `suspicious` ; fichier absent → `error`.

2. **clamav (optionnel — skip si non provisionné).** `skipif` actif si `clamscan` est absent **ou**
   s'il n'y a **aucune base de signatures** (au moins un `*.cvd`/`*.cld`) dans `CLAMAV_DB_DIR`
   (défaut `/var/lib/clamav`, surchargeable par la variable d'environnement). Sous-cas :
   - EICAR → `malicious` ;
   - média sain avec les 3 checks actifs → `clean` ;
   - base absente (`CLAMAV_DB_DIR` pointant un dossier vide) → `suspicious` (défensif).

   Pour provisionner la base en local : `freshclam` (paquet clamav) écrit dans `/var/lib/clamav`.

3. **seccomp (tourne dès que faisable — souvent par défaut).** `skipif` actif **seulement** si poser
   un filtre seccomp minimal n'est pas faisable. Le test le détecte en posant un filtre `ALLOW` dans
   un enfant `os.fork()` jetable : il faut **`pyseccomp`** (déjà dans le lock) + **`libseccomp`**
   (présent sur la plupart des Linux) + un **`no_new_privs` posable** (possible pour tout process sur
   lui-même). Donc sur une machine Linux typique, ce test **ne skippe pas** : il tourne et vérifie
   qu'un média sain reste `clean` sous le ring noyau (`SECCOMP_ENABLED=1`, qui est le **défaut**).

**Variables d'environnement reconnues** (toutes lues par `AnalysisConfig.from_env`) :
`ENABLED_CHECKS`, `FFPROBE_PATH`, `CLAMSCAN_PATH`, `CLAMAV_DB_DIR`, `ANALYSIS_TIMEOUT_S`,
`RLIMIT_CPU_S`, `RLIMIT_AS_BYTES`, `RLIMIT_NPROC`, `RLIMIT_NOFILE`, `RLIMIT_FSIZE_BYTES`,
`EGRESS_CAP_BYTES`, `HEADER_BYTES`, `QUARANTINE_DIR`, `SECCOMP_ENABLED`, plus les overrides
conditionnels clamav `RLIMIT_AS_BYTES_CLAMAV` (défaut 1,5 Gio) / `RLIMIT_CPU_S_CLAMAV` (défaut 120 s).

> **`RLIMIT_NPROC` en dev bare-metal.** `RLIMIT_NPROC` est **global par UID** (pas par sous-arbre) :
> le défaut (64) est sain dans l'image Docker (UID dédié peu peuplé) mais bloque `fork()` sur une
> machine de dev où l'UID a déjà beaucoup de processus. Les tests forcent donc `RLIMIT_NPROC=4096`.

> **Validation des rlimits clamav.** Le test `test_real_clean_media_passes_clamav` est un **garde-fou
> de réglage** : si `clamscan` se fait tuer par le rlimit d'adressage/CPU (ou par l'OOM-killer du
> cgroup), un média **sain** ressort `suspicious` et ce test **échoue**. Le signal est alors de
> **relever `RLIMIT_AS_BYTES_CLAMAV` / `RLIMIT_CPU_S_CLAMAV`** (et, en conteneur, le `mem_limit` du
> service verifier — réglé à 2 Gio dans `compose.yaml`, > 1,5 Gio du rlimit AS).

**Commande.**
```bash
( cd packages/verifier && uv run pytest -m analysis_integration --no-cov )
```

**Attendu.** 11 tests au total ; le nombre de passés/skippés dépend de ce qui est provisionné :
- **ffmpeg + `libseccomp` présents (cas Linux courant), sans clamav** → **8 passés** (ffprobe +
  seccomp), **3 skippés** (les 3 cas clamav). *C'est le résultat de référence.*
- **+ `clamscan` et une base de signatures** → **11 passés**, zéro skip.
- **ffmpeg seul, sans `libseccomp` ni clamav** → seuls les tests ffprobe passent ; seccomp **et**
  clamav skippent.

---

### 3.3 `ec_integration` (crawler, **Docker requis**)

**Ce que ça prouve.** L'adaptateur EC parle à un **vrai `amuled`** : la formule de hash d'auth est
validée contre le daemon, l'auth échoue avec un mauvais mot de passe, le statut réseau est décodé, et
le cycle complet recherche → progress → fetch → stop tourne. Le second fichier
(`test_amuled_preferences.py`) valide le **get/set du port d'écoute** (port-sync High-ID) :
`get_listen_port()` lit un port plausible, et le round-trip `set → get` rend la valeur posée.

**Prérequis exacts.**
- **Docker** (les tests utilisent `testcontainers`, qui démarre un conteneur).
- **Image** `ngosang/amule:3.0.0-1` (tirée automatiquement si absente).
- Readiness attendue via le log `listening on 0.0.0.0:4712` (timeout de démarrage : 180 s).
- Aucune variable d'environnement à poser (le mot de passe EC `indexer-ec-test` est interne au test).

> Le conteneur éphémère **n'a pas d'accès réseau eD2k** : une recherche peut renvoyer
> `EC_OP_FAILED` ou des résultats vides. Les tests le **tolèrent explicitement** — c'est le **cycle
> requête/réponse** qui est validé, pas la richesse des résultats.

**Commande.**
```bash
( cd packages/crawler && uv run pytest -m ec_integration --no-cov )
```

**Attendu.** 6 tests passés (4 dans `test_amuled_ec.py` + 2 dans `test_amuled_preferences.py`), aucun
skip. Tolérance interne au `EC_OP_FAILED` (le test passe quand même).

---

### 3.4 `download_integration` (crawler, **Docker requis**)

**Ce que ça prouve.** Les mécaniques EC du download contre un vrai `amuled` : `add_link` est accepté
et le lien apparaît dans `download_queue` avec un statut lisible. C'est le **garde-fou de régression**
du bug de décodage du hash de partfile (le hash vit dans l'enfant `EC_TAG_PARTFILE_HASH 0x031E`, pas
dans la valeur propre du parent) — d'où un hash et une taille réalistes (~700 Mio), **jamais** la MD4
du fichier vide (qu'amuled traite comme instantanément complet et ne liste pas).

**Prérequis exacts.** Identiques à `ec_integration` : **Docker** + image `ngosang/amule:3.0.0-1`,
readiness sur `listening on 0.0.0.0:4712`.

**Commande.**
```bash
( cd packages/crawler && uv run pytest -m download_integration --no-cov )
```

**Attendu.** 1 test passé (`test_add_link_then_appears_in_download_queue`). La complétion réelle n'est
pas atteignable (pas de sources eD2k) : seul le cycle add_link → file → statut est validé.

---

### 3.5 `orchestration_integration` (crawler, **Docker requis**)

**Ce que ça prouve.** Un `CrawlerApp` réel (vrais `AmuleEcClient` + vraies bases SQLite sur
`tmp_path`) tourne **un cycle complet** contre un `amuled` Docker puis **s'arrête proprement** dans la
limite d'un `wait_for` de 120 s. L'assertion clé : l'index de cycle a avancé (`read_cycle_index() >= 1`),
preuve qu'un cycle a vraiment complété.

**Prérequis exacts.** Identiques à `ec_integration` : **Docker** + image `ngosang/amule:3.0.0-1`,
readiness sur `listening on 0.0.0.0:4712`. Le test charge une config de matching depuis
`packages/crawler/tests/fixtures/canonical_config.yaml`.

**Commande.**
```bash
( cd packages/crawler && uv run pytest -m orchestration_integration --no-cov )
```

**Attendu.** 1 test passé (`test_real_loop_runs_one_cycle_and_stops`). Résultats de recherche
possiblement vides : c'est la **boucle** (démarrage → recherche → catalogage → arrêt borné) qui est
validée.

---

### 3.6 `compose_integration` — smoke (crawler, **Docker + compose v2 requis**)

**Ce que ça prouve.** La stack `docker compose` **assemblée** (verifier + crawler + amuled,
**sans gluetun**) démarre et se câble correctement — **aucun octet de contenu n'est téléchargé**.
Quatre scénarios :
1. `docker compose build` réussit (les 2 images se construisent) ;
2. **full** : le verifier devient *healthy* (`/health` → 200) et le crawler reste *Up* ;
3. **observer** : le crawler démarre **sans** verifier et reste *Up* ;
4. **full fail-fast** : crawler en mode full mais verifier **absent** → le crawler health-check le
   verifier au boot, échoue, et **se fige en `exited`** avec un code de sortie ≠ 0.

Le smoke exerce **délibérément** le chemin de persistance réel (vrais volumes nommés
`catalog-db`/`local-db`/`quarantine`, crawler non-root uid 999, rootfs `read_only`) pour attraper toute
régression de permissions (un volume nommé root-owned ferait échouer SQLite : `unable to open database
file`).

**Prérequis exacts.**
- **Docker** + **docker compose v2** (le test pilote `docker compose …` par `subprocess`).
- Les builds tournent **depuis la racine du dépôt** (le test fixe `cwd = repo root`).
- Les variables gluetun sont **stubées** par le test lui-même (`WIREGUARD_PRIVATE_KEY`,
  `AMULE_EC_PASSWORD`, `SERVER_COUNTRIES`) car compose les interpole au parse même si gluetun est
  désactivé — **rien à poser côté opérateur**.
- Fichiers compose utilisés : `compose.yaml` + `compose.smoke.yaml` ; configs sous `deploy/smoke/`.
- Le test n'importe **aucun** module `emule_indexer` (préserve le 100 % branch du paquet).

**Commande.**
```bash
( cd packages/crawler && uv run pytest -m compose_integration --no-cov )
```

**Attendu.** 4 tests passés. Chaque scénario fait son `docker compose down -v` dans un `finally`
(volumes éphémères nettoyés). Prévoir plusieurs minutes (le build + le up sont sous des timeouts de
900 s).

---

### 3.7 `e2e_integration` — download→verify réel (crawler, **Docker + compose v2 requis**)

**Ce que ça prouve.** Contrairement au smoke (câblage seul), cette suite assemble un **vrai serveur
eD2k** (`ed2kd` vendoré) + un **amuled seeder** qui partage le fichier planté en High-ID + l'amuled
leecher du crawler. Le crawler observe, décide `download`, télécharge **les octets réels**, et au
partfile complété déclenche `resolve_staging_path` (DV10, **jamais exercé ailleurs**) → `os.replace`
vers la quarantaine par hash → le verifier analyse → verdict `clean` + `real_meta` non vide. Un
sous-test optionnel valide le port-sync (High-ID après SetPort).

**Prérequis exacts.**
- **Docker** + **docker compose v2** (pilotage par `subprocess`, `cwd = repo root`).
- **`vendor/ed2kd` doit exister.** ⚠️ **`vendor/` est gitignoré** (`/.gitignore: vendor/`) : un
  contributeur fraîchement cloné **ne l'a pas**. Le Dockerfile `deploy/e2e/ed2kd/Dockerfile` fait
  `COPY vendor/ed2kd …` (commit amont `f6c330da`) — sans ce dossier, le build de l'image ed2kd
  échoue. **Décision ouverte** : matérialiser `vendor/ed2kd` (sous-module, script de vendoring, ou
  désigitignorer) avant de pouvoir lancer cette suite hors de la machine de Geoffrey.
- Le binaire planté `deploy/e2e/fixtures/planted.mp4` (commité) et sa constante de hash
  (`7d3ce5e6…`) sont déjà en place et vérifiés par les tests unitaires `tests/e2e/test_planted.py`.
- Variables gluetun **stubées par le test** (rien à poser côté opérateur).
- Fichiers compose : `compose.yaml` + `compose.e2e.yaml` ; configs/fixtures sous `deploy/e2e/`.
- Le sous-test port-sync est gardé par `skipif` : il ne tourne **que si `E2E_PORTSYNC=1`** (sinon
  skip, car la boucle port-sync peut ne pas encore être intégrée).

**Commande.**
```bash
( cd packages/crawler && uv run pytest -m e2e_integration --no-cov )
```
(Avec port-sync : `E2E_PORTSYNC=1 ( cd packages/crawler && uv run pytest -m e2e_integration --no-cov )`.)

**Attendu.** 3 tests « cœur » passés (build, download→verify réel, décision = download) +
`test_portsync_highid_after_setport` **skippé** sauf si `E2E_PORTSYNC=1`. Builds + transferts réels :
prévoir du temps (timeouts internes jusqu'à 1800 s).

---

## 4. Prérequis machine (récapitulatif installable)

Pour pouvoir lancer **toutes** les suites :

- **Docker** + **docker compose v2** (`ec/download/orchestration/compose/e2e_integration`). Les
  suites EC utilisent `testcontainers` (tire l'image `ngosang/amule:3.0.0-1`) ; les suites compose/e2e
  pilotent `docker compose` directement.
- **ffmpeg / ffprobe** (`analysis_integration` — obligatoire pour les tests ffprobe).
- **clamav** : `clamscan` + une base de signatures (`freshclam` peuple `/var/lib/clamav`, ou pointez
  `CLAMAV_DB_DIR` ailleurs) — **optionnel** ; sans base, les tests clamav sont skippés.
- **libseccomp + `pyseccomp`** (déjà dans le lock du paquet verifier) + un `no_new_privs` posable —
  **optionnel** ; sinon les tests seccomp sont skippés.
- Un **`.env`** (copié de `.env.example`) pour les commandes compose **manuelles** : `WIREGUARD_PRIVATE_KEY`,
  `SERVER_COUNTRIES`, `AMULE_EC_PASSWORD`, et `DOCKER_GID` (uniquement si port-sync). Note : les tests
  `compose_integration`/`e2e_integration` **stubent eux-mêmes** ces variables, donc le `.env` n'est pas
  requis pour les lancer.
- Pour `e2e_integration` uniquement : **`vendor/ed2kd` matérialisé** (voir §3.7).

---

## 5. Intégration CI (pistes — pas du code prêt à coller)

Ce qui est **déjà** en CI :
- `.github/workflows/ci.yml` (le **gate**) : ruff + format + sqlfluff + mypy + les deux gates
  `pytest` par paquet (100 % branch). Sur `push` et `pull_request`. **Ne lance aucun marqueur
  d'intégration.**
- `.github/workflows/images.yml` : le job **`smoke`** lance **déjà `compose_integration`** (build
  amd64 + smoke) et **GATE** le job `publish` (buildx multi-arch amd64+arm64 → GHCR). Déclenché sur
  `push main` / tags `v*` / `workflow_dispatch` (**pas** sur PR).

Pistes par marqueur (faisabilité GitHub Actions) :

| Marqueur | Réaliste en GHA ? | Comment |
|---|---|---|
| `verify_integration` | **Oui, facile** | Aucun service externe ; juste `uv sync --dev` puis le run. Le moins coûteux → **à ajouter en premier** (idéalement dans `ci.yml`). |
| `analysis_integration` (ffprobe) | **Oui** | `apt-get install -y ffmpeg` sur le runner, puis le run. Les sous-cas clamav/seccomp se skippent proprement → pas de flakiness. |
| `analysis_integration` (clamav) | **Oui mais plus lent** | `apt-get install -y clamav` + `freshclam` (téléchargement ~300 Mo) avant le run. Coûteux ; envisager un cache de la base. |
| `analysis_integration` (seccomp) | **Oui, probable** | `libseccomp` est généralement présent sur les runners Ubuntu et `no_new_privs` est posable → le test **tourne** (confirmé : il tourne déjà dans le sandbox de dev). S'il n'est pas faisable sur un runner donné, il se **skippe** (jamais d'échec). |
| `ec / download / orchestration_integration` | **Oui** | Docker est disponible sur les runners Ubuntu ; `testcontainers` tire `ngosang/amule:3.0.0-1`. Démarrage du conteneur ~ dizaines de secondes. |
| `compose_integration` | **Oui — déjà fait** | Déjà dans `images.yml` (job `smoke`). |
| `e2e_integration` | **Bloqué tant que `vendor/ed2kd` n'est pas matérialisé** | Une fois `vendor/ed2kd` disponible en CI (sous-module / script de vendoring), le job pourrait builder ed2kd + lancer la suite. Le plus coûteux (build C + transferts réels). |

**Ordre d'ajout raisonnable** (du moins coûteux / plus stable au plus lourd) :
1. `verify_integration` (gratuit, in-process) ;
2. `analysis_integration` côté ffprobe (apt ffmpeg) ;
3. les suites EC (`ec` → `download` → `orchestration`) — Docker, déjà disponible ;
4. `analysis_integration` côté clamav (apt + base, plus lent) ;
5. `analysis_integration` côté seccomp (après avoir confirmé `no_new_privs` en GHA) ;
6. `e2e_integration` (après matérialisation de `vendor/ed2kd`).

---

## 6. Voir aussi

- [Runbook de déploiement](runbook-deployment.md) — sections « Smoke test local (sans VPN) » et
  « Tests e2e (Docker, optionnel) » renvoient vers ce guide pour les prérequis détaillés.
