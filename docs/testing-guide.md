# Guide des tests — emule-indexer

Ce guide décrit **comment lancer les tests d'intégration** (les suites « lourdes » désélectionnées
par défaut), leurs **prérequis exacts** et **ce qu'on doit attendre** en sortie. Il complète le
[runbook de déploiement](runbooks/deployment.md) : le runbook explique comment faire tourner la
stack, ce guide-ci explique comment la **valider**.

Public visé : **dev local + CI**. Pas pour les opérateurs (qui n'ont pas à lancer les suites de
tests). Tout ce qui suit est **extrait du code réel** (fichiers de test, `pyproject.toml`, fichiers
compose). Quand un prérequis n'est pas vérifiable dans le code, c'est noté « à confirmer ».

> **La suite e2e « transfert réel » a été abandonnée** (et son scaffolding supprimé du dépôt) — voir
> la note dans le handoff / `CLAUDE.md`. Raison : pour qu'un vrai `amuled` signale un download terminé
> il faudrait orchestrer/reverse-engineerer des outils tiers (`amuled`, `ed2kd`), ce qui valide surtout
> du comportement tiers de confiance, pas notre code (même motif que la couche port-forwarding gluetun).
> DV10 reste couvert par les **unit-tests** + une **hypothèse de déploiement** (`staging_dir` =
> l'Incoming d'amuled, à confirmer en prod réelle). Ce guide ne couvre donc plus que **6** marqueurs.

---

## 1. Vue d'ensemble — la pyramide de tests

Le projet a **deux niveaux** :

1. **Le gate unitaire** (lancé par défaut, **100 % de couverture de branches** imposée). C'est ce
   que vérifient le hook pre-push et la CI. Le gate est **par paquet** (4 paquets) :

   ```bash
   ( cd packages/matching && uv run pytest -q )    # tests matching, 100 % branch
   ( cd packages/crawler  && uv run pytest -q )    # tests crawler,  100 % branch
   ( cd packages/verifier && uv run pytest -q )    # tests verifier, 100 % branch
   ( cd packages/webui    && uv run pytest -q )    # tests webui,    100 % branch
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

> **La suite e2e « transfert réel » a été abandonnée** — voir l'encadré en intro et la note dans le
> handoff / `CLAUDE.md`. DV10 (`resolve_staging_path`, `os.replace`/promote, boucle download, détection
> de complétion) reste couvert par les **unit-tests** + une **hypothèse de déploiement** (`staging_dir`
> = l'Incoming d'amuled).

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
- Aucun `skipif` ni variable d'environnement requis pour **cette** suite — elle tourne sur tout
  système où le gate tourne. (Les variables d'environnement listées en §4 concernent d'autres
  suites comme `compose_integration`.)

**Commande.**
```bash
( cd packages/crawler && uv run pytest -m verify_integration --no-cov )
```

**Attendu.** 1 test passé (`test_verify_loop_produces_suspicious_row`). Pas de skip.

---

### 3.2 `analysis_integration` (verifier, **sans Docker**, mais ffmpeg/seccomp)

**Ce que ça prouve.** Le côté verifier exécute **pour de vrai** le confinement (re-exec de l'enfant
jetable, rlimits, `setsid`, kill du groupe au timeout, env minimal) et le **vrai ffprobe** sur de
vrais échantillons, plus — si l'environnement le permet — le **filtre seccomp réel**.

> **Pas de test d'intégration clamav.** Le check `clamav` reste du code de prod (opt-in via
> `ENABLED_CHECKS`), couvert à 100 % par des tests unitaires à runner stubbé (`test_clamav.py`,
> `test_pipeline.py`, `test_analysis_child.py`). Les anciens tests `analysis_integration` clamav ont
> été **délibérément retirés** : ils ne prouvaient que le comportement d'une **brique tierce de
> confiance** (`clamscan` reconnaît EICAR / la sémantique de ses codes retour), pas notre code.

**Deux sous-cas, gérés par des `skipif` :**

1. **ffprobe (obligatoire pour l'essentiel des tests).** `skipif` actif si `ffmpeg` **ou** `ffprobe`
   est absent du PATH (`shutil.which`). Ces tests génèrent un petit média réel avec ffmpeg
   (`-f lavfi … -f matroska`) et vérifient les verdicts : média sain → `clean` + `real_meta` non
   vide ; ELF/shebang → `malicious` ; texte → `suspicious` ; egress sur-dimensionné → `suspicious` ;
   timeout (`ANALYSIS_TIMEOUT_S=0.001`) → `suspicious` ; fichier absent → `error`.

2. **seccomp (tourne dès que faisable — souvent par défaut).** `skipif` actif **seulement** si poser
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

> **`RLIMIT_NPROC` en dev bare-metal.** Symptôme : un test d'analyse crashe avec `BlockingIOError`
> ou `Resource temporarily unavailable` au moment du `fork()`. Cause : `RLIMIT_NPROC` est **global
> par UID** (pas par sous-arbre) ; le défaut (64) est sain dans l'image Docker (UID dédié peu
> peuplé) mais bloque `fork()` sur une machine de dev où l'UID a déjà beaucoup de processus. Les
> tests posent automatiquement `RLIMIT_NPROC=4096` pour contourner — c'est un workaround côté
> tests, pas un bug du code.

> **Dimensionnement des rlimits clamav (hypothèse, non testé).** Quand `clamav` est activé,
> `clamscan` mmap toute la base de signatures : on relâche `RLIMIT_AS_BYTES_CLAMAV` à **1,5 Gio** et
> on cale le `mem_limit` du service verifier à **2 Gio** dans `deploy/compose.base.yaml`. C'est un **choix
> optimiste assumé**, pas validé par un test (la calibration n'aurait de sens que contre l'image de
> prod, pas un `clamscan` bare-metal — d'où le retrait des tests d'intégration clamav). Si, en prod,
> un média **sain** ressort `suspicious`, le signal est de **relever `RLIMIT_AS_BYTES_CLAMAV` et le
> `mem_limit`**.

**Commande.**
```bash
( cd packages/verifier && uv run pytest -m analysis_integration --no-cov )
```

**Attendu.** 8 tests au total ; le nombre de passés/skippés dépend de ce qui est provisionné :
- **ffmpeg + `libseccomp` présents (cas Linux courant)** → **8 passés** (7 ffprobe + 1 seccomp),
  zéro skip. *C'est le résultat de référence.*
- **ffmpeg seul, sans `libseccomp`** → **7 passés** (ffprobe) ; le test seccomp **skippe**.

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
2. **download** : le verifier devient *healthy* (`/health` → 200) et le crawler reste *Up* ;
3. **observer** : le crawler démarre **sans** verifier et reste *Up* ;
4. **download fail-fast** : crawler en mode download mais verifier **absent** → le crawler health-check le
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
- Fichiers compose utilisés : `tests/smoke/compose.yaml` (autonome) + overrides temporaires par scénario + `deploy/examples/*.yaml` pour `test_entrypoint_config_renders` ; configs smoke sous `tests/smoke/`.
- Le test n'importe **aucun** module `emule_indexer` (préserve le 100 % branch du paquet).

**Commande.**
```bash
( cd packages/crawler && uv run pytest -m compose_integration --no-cov )
```

**Attendu.** 4 tests de cycle de vie + 9 cas paramétrés `test_entrypoint_config_renders` (3 points d'entrée × 3 combos de profil) = **13 tests passés**. Chaque scénario de cycle de vie fait son `docker compose down -v` dans un `finally` (volumes éphémères nettoyés). Prévoir plusieurs minutes (le build + le up sont sous des timeouts de 900 s).

---

## 4. Prérequis machine (récapitulatif installable)

Pour pouvoir lancer **toutes** les suites :

- **Docker** + **docker compose v2** (`ec/download/orchestration/compose_integration`). Les
  suites EC utilisent `testcontainers` (tire l'image `ngosang/amule:3.0.0-1`) ; la suite compose
  pilote `docker compose` directement.
- **ffmpeg / ffprobe** (`analysis_integration` — obligatoire pour les tests ffprobe). Sans cet
  outil, les tests ffprobe sont skip silencieusement (vous verrez 7 passés au lieu de 8). Installer
  depuis [ffmpeg.org/download](https://ffmpeg.org/download.html) (ou votre gestionnaire de paquets
  préféré). Vérifier : `ffprobe -version`.
- **clamav** : `clamscan` + une base de signatures (`freshclam` peuple `/var/lib/clamav`, ou pointez
  `CLAMAV_DB_DIR` ailleurs) — **optionnel** ; sans base, les tests clamav sont skippés.
- **libseccomp + `pyseccomp`** (déjà dans le lock du paquet verifier) + un `no_new_privs` posable —
  **optionnel** ; sinon les tests seccomp sont skippés.
- Un **`.env`** (copié de `.env.example`) pour les commandes compose **manuelles** : `WIREGUARD_PRIVATE_KEY`,
  `SERVER_COUNTRIES`, `AMULE_EC_PASSWORD`, et `DOCKER_GID` (uniquement si port-sync). Note : le test
  `compose_integration` **stube lui-même** ces variables, donc le `.env` n'est pas requis pour le lancer.

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

**Ordre d'ajout raisonnable** (du moins coûteux / plus stable au plus lourd) :
1. `verify_integration` (gratuit, in-process) ;
2. `analysis_integration` côté ffprobe (apt ffmpeg) ;
3. les suites EC (`ec` → `download` → `orchestration`) — Docker, déjà disponible ;
4. `analysis_integration` côté clamav (apt + base, plus lent) ;
5. `analysis_integration` côté seccomp (après avoir confirmé `no_new_privs` en GHA).

---

## 6. Outils de diagnostic (mesure, dev)

Outils ponctuels destinés au **développeur** (mesure/diagnostic, pas exploitation) :

- **Sonde richesse EC** : `uv run python -m emule_indexer.tools.ec_probe --all-tags …` dumpe **tous**
  les tags bruts d'un résultat de recherche réel (mappés + non mappés) — sert à mesurer le taux de
  remplissage des champs exposés par EC. C'est un outil de **diagnostic** : un déploiement n'en a pas
  besoin (cf. le constat « EC n'expose aucune métadonnée média sur les résultats de recherche »).

---

## 7. Voir aussi

- [Runbook de déploiement](runbooks/deployment.md) — pour **déployer et exploiter** un nœud
  (le runbook renvoie ici pour la validation en profondeur).
- [Index de la doc](README.md) — aiguillage par audience (opérateur / développeur / historique).
