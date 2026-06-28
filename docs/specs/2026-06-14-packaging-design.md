# Spec — emule-indexer : Plan F (packaging — 2 images Docker, compose, smoke test e2e)

> **Sous-projet** : le packaging qui transforme le code en un service déployable et permet — enfin —
> un **test de bout en bout** de la stack assemblée (jamais exercée comme un tout jusqu'ici, faute de
> conteneurs). Prérequis : D-download + D-verify + D-analysis (le pipeline complet existe et tourne).
>
> Réfs : MVP design `2026-06-10-crawler-mvp-design.md` §4-5 (topologie réseau : gluetun/amuled,
> réseaux `ec`/`verify-internal`/`egress`), §10.3-10.6 (durcissement/confinement), §15 (2 images,
> compose profils `observer`/`full`), §16 (e2e = compose contrôlé). Handoffs D-verify et D-analysis
> (`docs/handoffs/`). Spec D-analysis `2026-06-14-analysis-design.md` (DA1 a reporté ICI le « ring
> noyau » du confinement).

---

## 1. But & périmètre

**But** : empaqueter le crawler et le verifier en **deux images Docker**, les câbler dans un
`docker compose` (profils `observer`/`full`) fidèle à la topologie MVP (gluetun/amuled, réseaux
isolés, verifier sans Internet), avec un **durcissement niveau conteneur**, et livrer un **smoke test
e2e automatisé** qui monte la stack assemblée (sans VPN) et vérifie le câblage. Objectif explicite :
débloquer le test de bout en bout que les jalons précédents ne pouvaient pas faire hors Docker.

**Dans le périmètre** :
- **2 Dockerfiles multi-stage (uv)** : `packages/crawler/Dockerfile`, `packages/verifier/Dockerfile`.
- **`compose.yaml`** (topologie prod, profils `observer`/`full`, réseaux/volumes) + **`compose.smoke.yaml`**
  (override sans VPN pour le smoke) + **`compose.hardening.yml`** (override opt-in du ring noyau).
- **Durcissement conteneur** (non-root, `cap_drop: ALL`, `no-new-privileges`, rootfs `read_only` +
  tmpfs, seccomp défaut, limites pids/mémoire ; verifier seul sur `internal: true`).
- **Smoke test e2e** : marqueur pytest `compose_integration` (Docker requis, désélectionné, hors
  coverage) montant verifier+crawler+amuled (sans gluetun) et assertant le câblage.
- **CI build + publication GHCR multi-arch** (`linux/amd64` + `linux/arm64`) : workflow
  `.github/workflows/images.yml` (smoke gate → publish), dormant jusqu'au push.
- **Runbook** de déploiement (`docs/runbook-deployment.md`) : prérequis, `.env`/`local.yaml`,
  observer/full, override durcissement, validation homelab manuelle.

**Hors périmètre** (voir §10) :
- **Port-sync / High-ID** (lire le port forwarded de gluetun → le poser sur amuled via EC). On
  **abandonne glueforward** ; le crawler s'en chargera dans un **follow-up dédié** (inconnu empirique :
  l'EC d'aMule règle-t-il le port à chaud ?). Le profil `full` tourne en **Low-ID** d'ici là.
- **Sous-commandes CLI** (`merge`/`rebuild-local`/`validate-config`) — différées.
- **clamav** — follow-up APRÈS Plan F (déjà acté en D-analysis : tension `freshclam` egress vs
  `internal: true`).
- **Ring noyau OBLIGATOIRE** (gVisor/bwrap) — livré en **override opt-in documenté**, jamais requis.
- **e2e contre un serveur eD2k local + fichier planté** (MVP §16 option lourde) — non retenu.

## 2. Décisions verrouillées (issues du brainstorm)

1. **F-D1 — e2e = smoke test compose automatisé (A).** Marqueur pytest `compose_integration` (Docker
   requis, désélectionné par défaut, `--no-cov`), montant la stack **assemblée sans gluetun**
   (verifier+crawler+amuled). Asserte le **câblage** (EC joignable, `/health` verifier via
   `internal:true`, observer démarre sans verifier, full fail-fast sans verifier) — **aucun
   téléchargement réel**. Les formes (B) e2e-ed2k-local et (C) validation homelab manuelle ne sont pas
   automatisées (C = runbook).
2. **F-D2 — durcissement conteneur bâti+testé ; ring noyau opt-in.** Niveau conteneur (non-root,
   `cap_drop: ALL`, `no-new-privileges`, rootfs `read_only` + tmpfs, seccomp défaut, limites
   pids/mém, verifier sur `internal: true` → pas d'Internet pour l'enfant non plus) = bâti et vérifié
   par le smoke. gVisor (`runtime: runsc`) / bwrap (`net=none` par-enfant) = **`compose.hardening.yml`
   opt-in + runbook**, non requis (dépend du support hôte).
3. **F-D3 — glueforward abandonné ; port-sync = follow-up.** Pas de conteneur glueforward. Profil
   `full` en Low-ID pour l'instant. Le port-sync (gluetun control API → EC `set_listen_port`, repli
   `amule.conf`) est un sous-projet suivant (avec son inconnu empirique).
4. **F-D4 — sous-commandes CLI différées.** Le daemon de crawl reste le seul point d'entrée du crawler.
5. **F-D5 — clamav après Plan F.** (Inchangé depuis D-analysis.)
6. **F-D6 — CI build + GHCR multi-arch.** Workflow `images.yml` : job `smoke` (amd64, build local +
   `compose_integration`) **gate** un job `publish` (buildx multi-arch `amd64+arm64` → GHCR). Triggers :
   push `main` + tags `v*` + `workflow_dispatch` (pas sur PR). `ci.yml` (le gate) inchangé. **Zéro code
   PROD Python ajouté** → le gate 100 % branch reste intact.

## 3. Les deux images Docker

**Build context = racine du dépôt** (un seul `uv.lock` de workspace virtuel). Chaque Dockerfile
n'installe que son paquet + ses dépendances.

`packages/crawler/Dockerfile` (multi-stage) :
- *build* : `python:3.12-slim` + `uv` ; copie `pyproject.toml`/`uv.lock`/`packages/` ;
  `uv sync --frozen --no-dev --package emule-indexer` (venv : rapidfuzz, google-re2, httpx, pyyaml…).
- *runtime* : `python:3.12-slim` ; copie le venv ; **user non-root** ; `ENTRYPOINT
  ["python","-m","emule_indexer"]`. Aucun outil de build au runtime.

`packages/verifier/Dockerfile` (multi-stage) :
- *build* : `uv sync --frozen --no-dev --package download-verifier` (starlette, uvicorn, puremagic).
- *runtime* : `python:3.12-slim` **+ `ffmpeg`** (apt → fournit `ffprobe`, seule dépendance binaire) ;
  user non-root ; `ENTRYPOINT ["python","-m","download_verifier"]` ; expose le port de `/verify`.

Versions exactes des images de base + idiome **uv-in-Docker** (cache de couches, `--no-install-project`
vs `--package`) figés via **context7** au plan. Images publiées :
`ghcr.io/geoffreycoulaud/emule-indexer/{crawler,verifier}`.

## 4. `compose.yaml` — topologie prod (profils `observer`/`full`)

Fidèle à MVP §4-5 :
- **`gluetun`** (`qmcgaw/gluetun`) : VPN + NAT-PMP + control server (port forwarding limité à 4
  providers : ProtonVPN/PIA/PrivateVPN/PerfectPrivacy ; sinon Low-ID / port ouvert) ; `cap_add: NET_ADMIN`
  + `/dev/net/tun` (**seule** exception capabilities) ; secrets via `.env` (gitignored). Expose le port
  EC d'amuled. Profils `observer` + `full`.
- **`amuled`** (`ngosang/docker-amule`) : **`network_mode: "service:gluetun"`** (partage la netns →
  killswitch ; tout le P2P sort par le VPN). Volumes : état amuled (RW) + le volume **`quarantine`**
  (staging+quarantine **même FS** : amuled télécharge dans staging, le crawler `os.replace` vers
  quarantine). Profils `observer` + `full`.
- **`crawler`** (bâti, `image:`+`build:`) : multi-homed `ec` + `verify-internal` + `egress`. Monte
  `./config` en RO (bind-mount), `quarantine`/`catalog-db`/`local-db` en RW (volumes nommés). Profils
  `observer` + `full`. Son **mode (observer/full) et son hôte EC viennent de la config montée**
  (`local.yaml` : `verifier_url` absent → observer, présent (`http://verifier:8000`) → full ; hôte EC
  = `gluetun`). `depends_on` amuled (et verifier en full, `condition: service_healthy`). Le mécanisme
  exact (un `local.yaml` dédié par scénario monté par le compose, ou interpolation d'env si le config
  loader la supporte) est **figé au plan** en lisant le loader réel — **sans ajouter de code PROD**.
- **`verifier`** (bâti, `image:`+`build:`) : profil `full` **uniquement** ; **seul sur
  `verify-internal`** (`internal: true` → pas d'Internet). Monte `quarantine` en **RO**. Env pur
  (`QUARANTINE_DIR=/quarantine`, rlimits…). **Healthcheck** `GET /health`.
- **réseaux** : `ec` (crawler↔gluetun:ecport), `verify-internal` (**`internal: true`**), `egress`
  (crawler↔Internet : apprise/DNS).
- **volumes nommés** : `quarantine`, `catalog-db`, `local-db`. (`config/` = bind-mount RO, éditable.)

Chaque service bâti déclare `image: ghcr.io/geoffreycoulaud/emule-indexer/<name>` **ET** `build:` →
`docker compose build` (local/smoke) et `docker compose pull` (homelab) marchent tous deux.

## 5. `compose.smoke.yaml` + le smoke test `compose_integration`

**`compose.smoke.yaml` (override sans VPN)** : retire `gluetun` ; `amuled` tourne directement sur le
réseau `ec` (pas de `network_mode: service:gluetun`) ; le crawler reçoit une **config smoke** pointant
son hôte EC sur `amuled` (via le `local.yaml` monté par le compose — pas de nouvelle var d'env PROD,
cf. §4) ; verifier comme en `full`. Pas de persistance (volumes éphémères). → tourne **partout, sans
secrets VPN**.

**Test `compose_integration`** (`packages/crawler/tests/integration/test_compose_smoke.py`, marqueur
enregistré dans le pyproject crawler + désélectionné par défaut + `--no-cov` ; à côté des autres tests
Docker). Shell-out `docker compose -f compose.yaml -f compose.smoke.yaml …` ; `try/finally` avec
`docker compose down -v`. Asserte :
1. **Build + `--profile full up -d --build`** réussit (les 2 images se construisent, la stack monte).
2. **Healthchecks** atteints : verifier `/health` (healthcheck via `python -c urllib`, pas de `curl`
   ajouté) ; crawler **reste Up** ~N s sans crasher.
3. Le **verifier `/health` répond 200**.
4. **Full fail-fast** : crawler en full (config avec `verifier_url`) mais verifier **absent** → le crawler
   **sort en erreur promptement** (health-gate full-mode existant) — assert via `docker inspect` (exit
   ≠ 0).
5. **Observer** : `--profile observer up` → crawler démarre **sans** verifier et reste Up.

Test « ça s'assemble et ça se parle » — **aucun téléchargement réel** (amuled n'a ni serveurs ni VPN ;
seul son serveur EC est sollicité). Timeouts bornés sur l'attente des healthchecks.

## 6. Durcissement (F-D2)

**Niveau conteneur (bâti + smoke-testé)**, sur tous les services bâtis :
`cap_drop: [ALL]`, `security_opt: ["no-new-privileges:true"]`, `user:` non-root, **`read_only: true`**
(rootfs) + `tmpfs:` pour le scratch nécessaire, `pids_limit`, `mem_limit`/`ulimits`, seccomp **par
défaut** (jamais `unconfined`). En particulier :
- **verifier** : `read_only` + **tmpfs sur `/tmp`** (pour le `mkdtemp` de l'enfant d'analyse, qui écrit
  son cwd jetable là) ; `quarantine` en **RO** ; sur `internal: true` (l'enfant n'a donc pas d'Internet
  sans même un `net=none` par-enfant).
- **crawler** : `read_only` + volumes RW (`catalog-db`/`local-db`/`quarantine` pour `os.replace`) +
  tmpfs si besoin.
- exceptions : gluetun (`NET_ADMIN`/tun) ; amuled partage la netns gluetun.

**Ring noyau (`compose.hardening.yml`, opt-in)** : ajoute `runtime: runsc` (gVisor) sur le verifier
et/ou les notes bwrap (`net=none` par-enfant) — **exige un support hôte**, jamais requis pour
démarrer/smoke-tester. C'est la couche reportée de D-analysis (DA1).

## 7. CI — build + publication GHCR multi-arch (F-D6)

Nouveau `.github/workflows/images.yml` (le `ci.yml` existant = gate, **inchangé**). Triggers : push
`main` + tags `v*` + `workflow_dispatch` (**pas** sur PR — lourd). `permissions: { contents: read,
packages: write }`.
- **Job `smoke`** (`ubuntu-latest`, amd64) : checkout, `docker compose build` (les 2 images), exécute
  le marqueur `compose_integration` (build→up→asserts→down). **Gate** la publication — on ne pousse
  jamais une stack cassée.
- **Job `publish`** (`needs: smoke`) : `docker/setup-qemu-action` + `docker/setup-buildx-action`,
  login GHCR (`GITHUB_TOKEN`), `docker/metadata-action` (tags : version sur tag git, `edge`+sha sur
  `main`, `latest` sur release), `docker/build-push-action` **`platforms: linux/amd64,linux/arm64`**
  pour les deux images → GHCR.

Dormant tant que le dépôt reste full-local (ne tourne qu'au push). Coût marginal : les mêmes
Dockerfiles sont déjà construits localement pour le smoke.

## 8. Runbook (`docs/runbook-deployment.md`)

- Prérequis (Docker + Buildx ; identifiants d'un provider VPN à port forwarding — Proton/PIA/PrivateVPN/PerfectPrivacy — sinon Low-ID). Setup `.env` (secrets VPN) + `config/local.yaml`
  (depuis `local.example.yaml` ; EC + `VERIFIER_URL`).
- Démarrage : `docker compose --profile observer up` / `--profile full up` ; pull depuis GHCR
  (`docker compose pull`) ou build local (`docker compose build`).
- Durcissement opt-in : `docker compose -f compose.yaml -f compose.hardening.yml --profile full up`.
- **Validation homelab manuelle** : monter `full`, suivre les logs, confirmer
  recherche→download→quarantaine→vérif sur le vrai eMule (**Low-ID** pour l'instant ; le **High-ID
  attend le follow-up port-sync**). Où vivent les données (volumes nommés) + inspection
  (`docker volume inspect`, `docker compose exec`).

## 9. Tests & discipline du projet

Plan F **n'ajoute aucun code PROD Python** → le **gate 100 % branch (verifier + crawler) reste
inchangé** (les 6 checks de `ci.yml` ne bougent pas). Les nouveaux artefacts sont des Dockerfiles +
compose + un test d'intégration (`compose_integration`, `--no-cov`, Docker requis, désélectionné par
défaut) + un workflow CI. Le smoke test est le filet automatisé ; la validation homelab réelle reste
manuelle (runbook). (Optionnel, non requis : `hadolint` sur les Dockerfiles.)

## 10. Hors-périmètre / reporté (explicite)

- **Port-sync / High-ID** (remplace glueforward, via EC) → **follow-up dédié** (inconnu : EC règle-t-il
  le port à chaud ? repli `amule.conf`+reload). Low-ID en attendant.
- **clamav** → follow-up APRÈS Plan F.
- **Ring noyau obligatoire** (gVisor/bwrap) → opt-in documenté seulement.
- **Sous-commandes CLI** (`merge`/`rebuild-local`/`validate-config`) → différées.
- **e2e ed2k-local + fichier planté** (MVP §16) → non retenu (le smoke (A) suffit comme filet
  automatisé ; la validation réelle = homelab manuel).

## 11. Risques & notes

- **uv-in-Docker pour un workspace** : installer un seul paquet du workspace dans une image demande le
  bon enchaînement (`--package`, cache des couches deps vs source). À figer via context7/doc uv au plan.
- **`read_only` + besoins d'écriture** : repérer tous les chemins écrits (verifier `/tmp` ; crawler
  DBs/quarantine ; amuled état) et n'ouvrir QUE ceux-là (tmpfs/volumes) — un oubli fait crasher au
  démarrage (sera attrapé par le smoke).
- **amuled non-root / image tierce** : `ngosang/docker-amule` peut imposer son propre user/PUID ; le
  durcissement (non-root, RO) doit composer avec ce que l'image permet — à valider au plan.
- **Smoke en CI** : build de 2 images + montée d'amuled à chaque run `main`/tag — lent mais borné ;
  acceptable (gate de la publication). Multi-arch arm64 via QEMU n'est construit qu'au `publish` (le
  smoke tourne en amd64 natif).
- **GHCR visibilité** : les packages GHCR sont privés par défaut ; les rendre publics (ou documenter
  le `docker login ghcr.io`) pour un `docker compose pull` facile. À noter dans le runbook.
- **Mécanisme de config observer/full/EC-host** : à figer au plan en lisant le config loader réel —
  soit le compose monte un `local.yaml` dédié par scénario (observer/full/smoke), soit le loader
  supporte déjà l'interpolation d'env (`${VERIFIER_URL}`…). **Contrainte dure : aucun code PROD
  Python ajouté** ; si l'interpolation n'existe pas, on passe par des fichiers de config montés (pas
  d'ajout au loader).
