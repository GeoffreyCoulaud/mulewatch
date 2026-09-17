# Exemples de déploiement composables — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fournir des exemples de déploiement clé en main (`examples/*.yaml`) basés sur une brique commune `include`, un monitoring Prometheus/Grafana par profil, et une doc pas-à-pas, dont des topologies **sans gluetun**.

**Architecture:** Une brique `bricks/compose.core.yaml` (tout sauf amuled & VPN) est `include`-ée par 3 points d'entrée distincts (`gluetun`, `sans-vpn-lowid`, `sans-vpn-highid`). Mode (`observer`/`download`) et `monitoring` sont des **profils** ; gVisor un knob `CONTAINER_RUNTIME`. Config rangée par owner sous `config/`. Spec : `docs/superpowers/specs/2026-06-20-deploiement-exemples-design.md`.

**Tech Stack:** Docker Compose v2.20+ (`include`, profils, ancres YAML), Prometheus, Grafana, images GHCR du projet.

## Global Constraints

- **Livrable config + docs : AUCUN code de prod Python.** Le gate 100 % branch est inchangé. Le seul fichier Python touché est `packages/crawler/tests/integration/test_compose_smoke.py` (marqueur `compose_integration`, **exclu de la couverture**) — il DOIT passer `ruff` + `mypy --strict` (typé `-> None`, params typés).
- **Pas de Docker dans le sandbox.** Les validations `docker compose …` sont exécutées par **Geoffrey via `!`** (cf. blocs « Validation (Geoffrey) » de chaque tâche). L'implémenteur écrit les fichiers et commite ; il ne peut pas exécuter Docker.
- **Compose v2.20+** requis (directive `include`).
- **Conventional commits** (`feat(deploy):`, `docs:`, `test:`, `chore:`). Travailler sur `main`.
- **Le secret EC est inline** dans `config/crawler/<mode>.yaml` (non interpolé par Compose) ET dupliqué via `AMULE_EC_PASSWORD` du `.env` — pattern existant, inchangé.
- **Host EC = `amuled` partout** (alias réseau côté gluetun) → un seul `local.yaml` par mode.
- **Durcissement niveau-conteneur préservé** sur les services bâtis : `user 999:999`, `read_only`, `tmpfs /tmp`, `cap_drop: ALL`, `no-new-privileges`, `pids_limit`, `mem_limit`.
- **`include` est additif** : réseaux/volumes définis UNE seule fois (dans `core`) ; `amuled`/VPN UNIQUEMENT dans les points d'entrée ; jamais de redéfinition de part et d'autre.

---

## File Structure

- `bricks/compose.core.yaml` (créé) — brique commune.
- `examples/{gluetun,sans-vpn-lowid,sans-vpn-highid}.yaml` (créés) — points d'entrée.
- `config/crawler/{crawler,matcher,targets}.yaml` (déplacés depuis `config/`).
- `config/crawler/{observer,download}.example.yaml` (créés, scission de `config/local.example.yaml`).
- `config/verifier.yaml`, `config/verifier.example.yaml` (inchangés, à plat).
- `config/prometheus.yml` (créé).
- `config/grafana/provisioning/{datasources/prometheus.yaml,dashboards/provider.yaml}` + `config/grafana/dashboards/emule-indexer.json` (créés).
- `.env.example` (réécrit), `.gitignore` (modifié), `.gitattributes` (créé).
- `compose.yaml`, `compose.hardening.yml` (supprimés). `compose.smoke.yaml` (réécrit autonome).
- `tests/smoke/local.download.yaml` (renommé depuis `local.full.yaml`).
- `packages/crawler/tests/integration/test_compose_smoke.py` (étendu).
- `docs/runbook-deployment.md` (réécrit) ; `docs/runbook-{administration,troubleshooting}.md`, `CLAUDE.md` (refs mises à jour).

---

### Task 1: Réorganisation `config/` + secrets

**Files:**
- Move: `config/crawler.yaml` → `config/crawler/crawler.yaml` ; `config/matcher.yaml` → `config/crawler/matcher.yaml` ; `config/targets.yaml` → `config/crawler/targets.yaml`
- Create: `config/crawler/observer.example.yaml`, `config/crawler/download.example.yaml`, `.gitattributes`
- Delete: `config/local.example.yaml`
- Modify: `.gitignore`

- [ ] **Step 1: Déplacer les configs crawler (historique préservé)**

```bash
cd /home/geoffrey/Repositories/emule-indexer
mkdir -p config/crawler
git mv config/crawler.yaml config/crawler/crawler.yaml
git mv config/matcher.yaml config/crawler/matcher.yaml
git mv config/targets.yaml config/crawler/targets.yaml
```

- [ ] **Step 2: Créer `config/crawler/observer.example.yaml`**

```yaml
# Config LOCALE crawler — MODÈLE (mode observer : cataloguer seulement).
# Copier en config/crawler/observer.yaml (gitignoré) et renseigner le mot de passe EC.
amules:
  - name: amule-1
    host: amuled                       # service amuled (sans-vpn) OU alias `amuled` sur gluetun
    port: 4712
    password: change-me                # = AMULE_EC_PASSWORD du .env
catalog_db_path: /data/catalog/catalog.db
local_db_path: /data/local/local.db
# Pas de verifier_url => mode observer (aucun download / vérif).
# Notifications apprise (optionnel) :
# observability:
#   notifications:
#     - { url: "discord://WEBHOOK_ID/TOKEN", tag: community }
```

- [ ] **Step 3: Créer `config/crawler/download.example.yaml`**

```yaml
# Config LOCALE crawler — MODÈLE (mode download : télécharger + vérifier).
# Copier en config/crawler/download.yaml (gitignoré) et renseigner le mot de passe EC.
amules:
  - name: amule-1
    host: amuled
    port: 4712
    password: change-me                # = AMULE_EC_PASSWORD du .env
catalog_db_path: /data/catalog/catalog.db
local_db_path: /data/local/local.db
download_endpoint:
  name: amule-dl
  host: amuled
  port: 4712
  password: change-me
staging_dir: /data/quarantine
quarantine_dir: /data/quarantine
verifier_url: http://verifier:8000     # présence => mode download (download + vérif)
# Port-sync (High-ID via gluetun, stack B) — décommenter ET passer VPN_PORT_FORWARDING=on ;
# nécessite aussi la section crawler.port_sync dans config/crawler/crawler.yaml :
# gluetun_control_url: http://gluetun:8000
# restarter_url: http://docker-proxy:2375
# Notifications apprise (optionnel) :
# observability:
#   notifications:
#     - { url: "discord://WEBHOOK_ID/TOKEN", tag: operations }
```

- [ ] **Step 4: Supprimer l'ancien modèle**

```bash
git rm config/local.example.yaml
```

- [ ] **Step 5: Mettre à jour `.gitignore`**

Remplacer la ligne `config/local.yaml` par les deux entrées explicites (les `.example` restent suivis ; pas de wildcard car `config/crawler/` mêle committé et gitignoré) :

```
# Config locale crawler (machine + secret EC) — JAMAIS versionnée. Seuls les *.example.yaml le sont.
config/crawler/observer.yaml
config/crawler/download.yaml
```

- [ ] **Step 6: Créer `.gitattributes` (robustesse CRLF si édition Windows)**

```
# Fins de ligne forcées LF pour les fichiers édités à la main au déploiement (Windows compris).
.env.example   text eol=lf
*.env          text eol=lf
config/**/*.yaml text eol=lf
config/**/*.yml  text eol=lf
config/**/*.json text eol=lf
bricks/*.yaml  text eol=lf
examples/*.yaml text eol=lf
compose*.yaml  text eol=lf
```

- [ ] **Step 7: Commit**

```bash
git add -A config/ .gitignore .gitattributes
git commit -m "chore(deploy): range config/ par owner + scinde local.example en observer/download"
```

---

### Task 2: `.env.example` (secrets + knobs, sans COMPOSE_FILE)

**Files:**
- Modify: `.env.example` (réécriture complète)

- [ ] **Step 1: Réécrire `.env.example`**

```dotenv
# Secrets & réglages de déploiement emule-indexer — COPIER en .env (gitignoré).
# Renseigne SEULEMENT ce qui concerne ta stack (cf. docs/runbook-deployment.md, matrice de choix).
# Aucune orchestration ici : on choisit la stack avec `-f examples/<fichier>.yaml`.

# --- amuled (TOUTES stacks) : mot de passe EC, partagé avec le crawler via config/crawler/<mode>.yaml ---
AMULE_EC_PASSWORD=change-me

# --- gluetun (stacks A/B) ---
WIREGUARD_PRIVATE_KEY=change-me
VPN_SERVICE_PROVIDER=protonvpn
SERVER_COUNTRIES=Switzerland
# B (High-ID via VPN) : passer "on" ET armer le bloc port_sync dans config/crawler/download.yaml
VPN_PORT_FORWARDING=off
# B : GID du groupe `docker` de l'hôte (getent group docker | cut -d: -f3) pour le docker-proxy
DOCKER_GID=

# --- High-ID statique (stack D) : DOIT égaler le port d'amuled (amule.conf Port=, défaut 4662) ---
LISTEN_PORT=4662
LISTEN_PORT_UDP=4672

# --- monitoring (profil monitoring) ---
GRAFANA_PWD=change-me
GRAFANA_PORT=3000

# --- divers ---
IMAGE_TAG=latest
# gVisor (durcissement noyau ; hôte Linux + runsc uniquement) — décommenter pour activer :
# CONTAINER_RUNTIME=runsc
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "chore(deploy): .env.example knobs par stack (sans COMPOSE_FILE)"
```

---

### Task 3: Assets monitoring (Prometheus + Grafana)

**Files:**
- Create: `config/prometheus.yml`
- Create: `config/grafana/provisioning/datasources/prometheus.yaml`
- Create: `config/grafana/provisioning/dashboards/provider.yaml`
- Create: `config/grafana/dashboards/emule-indexer.json`

**Interfaces:**
- Produces: cibles de scrape `crawler:9090` et `verifier:8000` ; datasource Grafana `uid: prometheus` (référencée par le dashboard). Le crawler expose `/metrics` sur 9090 (config `observability.metrics.port`), le verifier sur 8000 (`/metrics`).

- [ ] **Step 1: Créer `config/prometheus.yml`**

```yaml
# Scrape du crawler (9090) et du verifier (8000) — spec 2026-06-20 §7.
global:
  scrape_interval: 30s
scrape_configs:
  - job_name: crawler
    static_configs:
      - targets: ["crawler:9090"]
  - job_name: verifier
    static_configs:
      - targets: ["verifier:8000"]
```

- [ ] **Step 2: Créer `config/grafana/provisioning/datasources/prometheus.yaml`**

```yaml
apiVersion: 1
datasources:
  - name: Prometheus
    uid: prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
```

- [ ] **Step 3: Créer `config/grafana/provisioning/dashboards/provider.yaml`**

```yaml
apiVersion: 1
providers:
  - name: emule-indexer
    type: file
    options:
      path: /var/lib/grafana/dashboards
```

- [ ] **Step 4: Créer `config/grafana/dashboards/emule-indexer.json` (dashboard de départ, valide)**

```json
{
  "uid": "emule-indexer",
  "title": "emule-indexer",
  "schemaVersion": 39,
  "version": 1,
  "time": { "from": "now-6h", "to": "now" },
  "refresh": "30s",
  "panels": [
    {
      "type": "stat",
      "title": "Crawler up",
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "gridPos": { "h": 4, "w": 6, "x": 0, "y": 0 },
      "targets": [ { "refId": "A", "expr": "emule_crawler_up" } ]
    },
    {
      "type": "stat",
      "title": "Instances aMule connectées",
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "gridPos": { "h": 4, "w": 6, "x": 6, "y": 0 },
      "targets": [ { "refId": "A", "expr": "emule_connected_instances" } ]
    },
    {
      "type": "stat",
      "title": "Profondeur file de vérification",
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "gridPos": { "h": 4, "w": 6, "x": 12, "y": 0 },
      "targets": [ { "refId": "A", "expr": "emule_verification_queue_depth" } ]
    },
    {
      "type": "timeseries",
      "title": "Observations / s",
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "gridPos": { "h": 8, "w": 12, "x": 0, "y": 4 },
      "targets": [ { "refId": "A", "expr": "rate(emule_observations_total[5m])" } ]
    },
    {
      "type": "timeseries",
      "title": "Downloads (queued vs completed) / s",
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "gridPos": { "h": 8, "w": 12, "x": 12, "y": 4 },
      "targets": [
        { "refId": "A", "expr": "rate(emule_downloads_queued_total[5m])" },
        { "refId": "B", "expr": "rate(emule_downloads_completed_total[5m])" }
      ]
    }
  ]
}
```

> Note : les counters Prometheus portent le suffixe `_total` à l'exposition (ex. `emule_observations` → `emule_observations_total`) ; les gauges (`emule_crawler_up`, `emule_connected_instances`, `emule_verification_queue_depth`) n'en portent pas. Dashboard volontairement minimal — extensible via l'UI Grafana (export → remplace ce JSON).

- [ ] **Step 5: Commit**

```bash
git add config/prometheus.yml config/grafana
git commit -m "feat(deploy): assets monitoring Prometheus + Grafana (datasource + dashboard provisionnés)"
```

---

### Task 4: Brique `core` (`bricks/compose.core.yaml`)

**Files:**
- Create: `bricks/compose.core.yaml`

**Interfaces:**
- Produces (pour les points d'entrée) : réseaux `ec`, `verify-internal` (`internal: true`), `egress` ; volumes `quarantine`, `catalog-db`, `local-db`, `amule-state`, `clamav-db`, `prometheus-data`, `grafana-data` ; services `crawler-observer` [observer], `crawler-download` [download], `verifier`/`freshclam` [download], `prometheus`/`grafana` [monitoring]. Les points d'entrée AJOUTENT `amuled` (et le VPN) référençant `ec`/`amule-state`/`quarantine`.

- [ ] **Step 1: Créer `bricks/compose.core.yaml`**

```yaml
# Brique COMMUNE (spec 2026-06-20 §4) — incluse par chaque examples/*.yaml.
# Définit TOUT sauf amuled & le VPN. Cohérente seule :
#   GRAFANA_PWD=x docker compose -f bricks/compose.core.yaml config
x-crawler-common: &crawler-common
  image: ghcr.io/geoffreycoulaud/emule-indexer-crawler:${IMAGE_TAG:-latest}
  build:
    context: .
    dockerfile: packages/crawler/Dockerfile
  user: "999:999"
  read_only: true
  tmpfs:
    - /tmp
  cap_drop:
    - ALL
  security_opt:
    - "no-new-privileges:true"
  pids_limit: 256
  mem_limit: 512m
  runtime: ${CONTAINER_RUNTIME:-runc}
  command:
    - "--local"
    - "/app/config/local.yaml"
    - "--crawler"
    - "/app/config/crawler.yaml"
    - "--targets"
    - "/app/config/targets.yaml"
    - "--matcher"
    - "/app/config/matcher.yaml"
  restart: unless-stopped

services:
  crawler-observer:
    <<: *crawler-common
    profiles: [observer]
    networks:
      ec:
        aliases: [crawler]
      egress: {}
    volumes:
      - ./config/crawler/observer.yaml:/app/config/local.yaml:ro
      - ./config/crawler/crawler.yaml:/app/config/crawler.yaml:ro
      - ./config/crawler/targets.yaml:/app/config/targets.yaml:ro
      - ./config/crawler/matcher.yaml:/app/config/matcher.yaml:ro
      - catalog-db:/data/catalog
      - local-db:/data/local
      - quarantine:/data/quarantine

  crawler-download:
    <<: *crawler-common
    profiles: [download]
    networks:
      ec:
        aliases: [crawler]
      verify-internal: {}
      egress: {}
    depends_on:
      - verifier
    volumes:
      - ./config/crawler/download.yaml:/app/config/local.yaml:ro
      - ./config/crawler/crawler.yaml:/app/config/crawler.yaml:ro
      - ./config/crawler/targets.yaml:/app/config/targets.yaml:ro
      - ./config/crawler/matcher.yaml:/app/config/matcher.yaml:ro
      - catalog-db:/data/catalog
      - local-db:/data/local
      - quarantine:/data/quarantine

  verifier:
    image: ghcr.io/geoffreycoulaud/emule-indexer-verifier:${IMAGE_TAG:-latest}
    build:
      context: .
      dockerfile: packages/verifier/Dockerfile
    profiles: [download]
    runtime: ${CONTAINER_RUNTIME:-runc}
    environment:
      QUARANTINE_DIR: /quarantine
      VERIFIER_HOST: 0.0.0.0
      VERIFIER_PORT: "8000"
      VERIFIER_CONFIG: /config/verifier.yaml
      ENABLED_CHECKS: type_sniff,ffprobe,clamav
      CLAMAV_DB_DIR: /clamav-db
      RLIMIT_AS_BYTES_CLAMAV: "1610612736"
      RLIMIT_CPU_S_CLAMAV: "120"
    volumes:
      - quarantine:/quarantine:ro
      - clamav-db:/clamav-db:ro
      - ./config/verifier.yaml:/config/verifier.yaml:ro
    networks:
      - verify-internal
    healthcheck:
      test:
        - "CMD"
        - "python"
        - "-c"
        - "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://localhost:8000/health').status==200 else sys.exit(1)"
      interval: 10s
      timeout: 3s
      retries: 5
      start_period: 10s
    user: "999:999"
    read_only: true
    tmpfs:
      - /tmp
    cap_drop:
      - ALL
    security_opt:
      - "no-new-privileges:true"
    pids_limit: 256
    mem_limit: 2g
    restart: unless-stopped

  freshclam:
    image: clamav/clamav:1.4
    profiles: [download]
    command: ["freshclam", "--daemon", "--foreground", "--checks=2"]
    environment:
      FRESHCLAM_CHECKS: "2"
    volumes:
      - clamav-db:/var/lib/clamav
    networks:
      - egress
    cap_drop:
      - ALL
    security_opt:
      - "no-new-privileges:true"
    restart: unless-stopped

  prometheus:
    image: prom/prometheus:v3.1.0
    profiles: [monitoring]
    command:
      - "--config.file=/etc/prometheus/prometheus.yml"
      - "--storage.tsdb.path=/prometheus"
    volumes:
      - ./config/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus-data:/prometheus
    networks:
      - ec
      - verify-internal
    restart: unless-stopped

  grafana:
    image: grafana/grafana:11.4.0
    profiles: [monitoring]
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_PWD:?}
      GF_USERS_ALLOW_SIGN_UP: "false"
    ports:
      - "${GRAFANA_PORT:-3000}:3000"
    volumes:
      - ./config/grafana/provisioning:/etc/grafana/provisioning:ro
      - ./config/grafana/dashboards:/var/lib/grafana/dashboards:ro
      - grafana-data:/var/lib/grafana
    networks:
      - ec
    depends_on:
      - prometheus
    restart: unless-stopped

networks:
  ec: {}
  verify-internal:
    internal: true
  egress: {}

volumes:
  quarantine: {}
  catalog-db: {}
  local-db: {}
  amule-state: {}
  clamav-db: {}
  prometheus-data: {}
  grafana-data: {}
```

- [ ] **Step 2: Validation (Geoffrey, via `!`) — la brique est cohérente seule**

```bash
GRAFANA_PWD=x docker compose -f bricks/compose.core.yaml config >/dev/null && echo "OK core" || echo "KO core"
```
Expected: `OK core` (services/réseaux/volumes rendus sans erreur).

- [ ] **Step 3: Commit**

```bash
git add bricks/compose.core.yaml
git commit -m "feat(deploy): brique core (crawler x2 profils, verifier, freshclam, monitoring)"
```

---

### Task 5: Points d'entrée sans VPN (`sans-vpn-lowid`, `sans-vpn-highid`)

**Files:**
- Create: `examples/sans-vpn-lowid.yaml`, `examples/sans-vpn-highid.yaml`

**Interfaces:**
- Consumes : `bricks/compose.core.yaml` (réseau `ec`, volumes `amule-state`/`quarantine`).
- Produces : service `amuled` (host EC `amuled:4712`).

- [ ] **Step 1: Créer `examples/sans-vpn-lowid.yaml`**

```yaml
# Stack C — sans VPN, Low-ID (spec 2026-06-20 §5). Le P2P sort par ta connexion : IP domestique
# exposée aux pairs (mets un VPN au niveau hôte pour l'éviter). Aucun port à ouvrir.
# Lancer : docker compose -f examples/sans-vpn-lowid.yaml --profile <observer|download> [--profile monitoring] up -d
# Détails : docs/runbook-deployment.md
name: emule-indexer
include:
  - path: ../bricks/compose.core.yaml
    project_directory: ..
services:
  amuled:
    image: ngosang/amule:3.0.0-1
    profiles: [observer, download]
    networks:
      - ec
    environment:
      GUI_PWD: ${AMULE_EC_PASSWORD:?}
    volumes:
      - amule-state:/home/amule/.aMule
      - quarantine:/data/quarantine
    restart: unless-stopped
```

- [ ] **Step 2: Créer `examples/sans-vpn-highid.yaml`**

```yaml
# Stack D — sans VPN, High-ID statique (spec 2026-06-20 §5, §10). IP domestique exposée.
# Redirige LISTEN_PORT (TCP+UDP) sur ta box vers cette machine + autorise au pare-feu (Windows inclus).
# LISTEN_PORT DOIT égaler le port d'amuled (amule.conf Port=, défaut 4662 → marche sans config).
# Lancer : docker compose -f examples/sans-vpn-highid.yaml --profile <observer|download> [--profile monitoring] up -d
# Détails : docs/runbook-deployment.md
name: emule-indexer
include:
  - path: ../bricks/compose.core.yaml
    project_directory: ..
services:
  amuled:
    image: ngosang/amule:3.0.0-1
    profiles: [observer, download]
    networks:
      - ec
    environment:
      GUI_PWD: ${AMULE_EC_PASSWORD:?}
    ports:
      - "${LISTEN_PORT:-4662}:${LISTEN_PORT:-4662}/tcp"
      - "${LISTEN_PORT_UDP:-4672}:${LISTEN_PORT_UDP:-4672}/udp"
    volumes:
      - amule-state:/home/amule/.aMule
      - quarantine:/data/quarantine
    restart: unless-stopped
```

- [ ] **Step 3: Validation (Geoffrey, via `!`) — forward-ref vers `ec`/volumes de la brique**

```bash
env AMULE_EC_PASSWORD=x GRAFANA_PWD=x \
  docker compose -f examples/sans-vpn-lowid.yaml --profile download --profile monitoring config >/dev/null \
  && echo "OK lowid" || echo "KO lowid"
env AMULE_EC_PASSWORD=x GRAFANA_PWD=x LISTEN_PORT=4662 \
  docker compose -f examples/sans-vpn-highid.yaml --profile download config >/dev/null \
  && echo "OK highid" || echo "KO highid"
```
Expected: `OK lowid` puis `OK highid`.

- [ ] **Step 4: Commit**

```bash
git add examples/sans-vpn-lowid.yaml examples/sans-vpn-highid.yaml
git commit -m "feat(deploy): points d'entrée sans-vpn (Low-ID + High-ID statique)"
```

---

### Task 6: Point d'entrée `gluetun` (A/B)

**Files:**
- Create: `examples/gluetun.yaml`

**Interfaces:**
- Consumes : `bricks/compose.core.yaml`. Produces : `gluetun` (alias réseau `amuled` sur `ec`), `amuled` (netns de gluetun, `container_name: amuled`), `docker-proxy` [download].

- [ ] **Step 1: Créer `examples/gluetun.yaml`**

```yaml
# Stacks A/B — VPN intégré gluetun (spec 2026-06-20 §5, §10). Le P2P sort par le VPN (IP masquée).
# A (Low-ID) : par défaut. B (High-ID) : hôte Linux rootful + VPN avec port forwarding ;
# passer VPN_PORT_FORWARDING=on (.env) ET décommenter le bloc port_sync dans config/crawler/download.yaml.
# Lancer : docker compose -f examples/gluetun.yaml --profile <observer|download> [--profile monitoring] up -d
# Détails : docs/runbook-deployment.md
name: emule-indexer
include:
  - path: ../bricks/compose.core.yaml
    project_directory: ..
services:
  gluetun:
    image: qmcgaw/gluetun:latest
    profiles: [observer, download]
    cap_add:
      - NET_ADMIN
    devices:
      - /dev/net/tun:/dev/net/tun
    environment:
      VPN_SERVICE_PROVIDER: ${VPN_SERVICE_PROVIDER:-protonvpn}
      VPN_TYPE: wireguard
      WIREGUARD_PRIVATE_KEY: ${WIREGUARD_PRIVATE_KEY:?}
      SERVER_COUNTRIES: ${SERVER_COUNTRIES:-}
      VPN_PORT_FORWARDING: ${VPN_PORT_FORWARDING:-off}
      HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE: '{"auth":"none"}'
    networks:
      ec:
        aliases: [amuled]          # le host EC `amuled` résout vers gluetun (amuled partage sa netns)
    restart: unless-stopped

  amuled:
    image: ngosang/amule:3.0.0-1
    container_name: amuled         # FIXE : le restart port-sync cible /containers/amuled/restart
    profiles: [observer, download]
    network_mode: "service:gluetun"
    depends_on:
      - gluetun
    environment:
      GUI_PWD: ${AMULE_EC_PASSWORD:?}
    volumes:
      - amule-state:/home/amule/.aMule
      - quarantine:/data/quarantine
    restart: unless-stopped

  docker-proxy:
    image: wollomatic/socket-proxy:1.12.2
    profiles: [download]
    command:
      - "-loglevel=info"
      - "-allowfrom=0.0.0.0/0"
      - "-listenip=0.0.0.0"
      - "-proxyport=2375"
      - "-socketpath=/var/run/docker.sock"
      - "-allowPOST=/v1\\..{1,2}/containers/amuled/restart"
    user: "65534:${DOCKER_GID:-0}"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    networks:
      - ec
    cap_drop:
      - ALL
    security_opt:
      - "no-new-privileges:true"
    read_only: true
    restart: unless-stopped
```

- [ ] **Step 2: Validation (Geoffrey, via `!`)**

```bash
env AMULE_EC_PASSWORD=x GRAFANA_PWD=x WIREGUARD_PRIVATE_KEY=x SERVER_COUNTRIES= DOCKER_GID=0 \
  docker compose -f examples/gluetun.yaml --profile download --profile monitoring config >/dev/null \
  && echo "OK gluetun" || echo "KO gluetun"
```
Expected: `OK gluetun` (vérifier au passage qu'`amuled` n'a pas de conflit alias/`container_name`).

- [ ] **Step 3: Commit**

```bash
git add examples/gluetun.yaml
git commit -m "feat(deploy): point d'entrée gluetun (Low-ID + High-ID port-sync opt-in)"
```

---

### Task 7: Retrait des anciens compose + smoke autonome

**Files:**
- Delete: `compose.yaml`, `compose.hardening.yml`
- Rewrite: `compose.smoke.yaml` (autonome, plus un override de `compose.yaml`)
- Move: `tests/smoke/local.full.yaml` → `tests/smoke/local.download.yaml`

> Pourquoi autonome : un `-f compose.smoke.yaml` qui surchargerait un service de `core` (inclus) **entre en conflit** avec `include` (qui n'override pas). Le smoke définit donc ses propres `crawler`/`verifier`/`amuled` (test infra, duplication assumée). gVisor n'a plus d'override : c'est le knob `CONTAINER_RUNTIME`.

- [ ] **Step 1: Renommer la fixture smoke (cohérence `full`→`download`)**

Les fixtures `tests/smoke/local.*.yaml` utilisent **déjà** `host: amuled` (et le smoke autonome
expose amuled comme service `amuled` sur `ec`) → aucun changement de host, seulement le renommage.

```bash
git mv tests/smoke/local.full.yaml tests/smoke/local.download.yaml
```

- [ ] **Step 2: Supprimer les anciens fichiers compose**

```bash
git rm compose.yaml compose.hardening.yml
```

- [ ] **Step 3: Réécrire `compose.smoke.yaml` (stack autonome de test)**

```yaml
# Stack SMOKE autonome (test compose_integration) — PAS un déploiement réel : configs tests/smoke/,
# volumes nommés (test des perms), sans gluetun/monitoring/docker-proxy. Profils observer/download.
# Piloté par packages/crawler/tests/integration/test_compose_smoke.py.
services:
  amuled:
    image: ngosang/amule:3.0.0-1
    profiles: [observer, download]
    networks:
      - ec
    environment:
      GUI_PWD: smoke-ec-password
    volumes:
      - amule-state:/home/amule/.aMule
      - quarantine:/data/quarantine
    restart: unless-stopped

  crawler:
    image: ghcr.io/geoffreycoulaud/emule-indexer-crawler:${IMAGE_TAG:-latest}
    build:
      context: .
      dockerfile: packages/crawler/Dockerfile
    profiles: [observer, download]
    user: "999:999"
    read_only: true
    tmpfs:
      - /tmp
    cap_drop:
      - ALL
    security_opt:
      - "no-new-privileges:true"
    networks:
      ec:
        aliases: [crawler]
      verify-internal: {}
      egress: {}
    command:
      - "--local"
      - "/app/config/local.yaml"
      - "--crawler"
      - "/app/config/crawler.yaml"
      - "--targets"
      - "/app/config/targets.yaml"
      - "--matcher"
      - "/app/config/matcher.yaml"
    volumes:
      - ./tests/smoke/local.download.yaml:/app/config/local.yaml:ro
      - ./tests/smoke/crawler.yaml:/app/config/crawler.yaml:ro
      - ./tests/smoke/targets.yaml:/app/config/targets.yaml:ro
      - ./tests/smoke/matcher.yaml:/app/config/matcher.yaml:ro
      - quarantine:/data/quarantine
      - catalog-db:/data/catalog
      - local-db:/data/local
    restart: unless-stopped

  verifier:
    image: ghcr.io/geoffreycoulaud/emule-indexer-verifier:${IMAGE_TAG:-latest}
    build:
      context: .
      dockerfile: packages/verifier/Dockerfile
    profiles: [download]
    environment:
      QUARANTINE_DIR: /quarantine
      VERIFIER_HOST: 0.0.0.0
      VERIFIER_PORT: "8000"
      VERIFIER_CONFIG: /config/verifier.yaml
      ENABLED_CHECKS: type_sniff,ffprobe
    volumes:
      - quarantine:/quarantine:ro
      - ./config/verifier.yaml:/config/verifier.yaml:ro
    networks:
      - verify-internal
    healthcheck:
      test:
        - "CMD"
        - "python"
        - "-c"
        - "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://localhost:8000/health').status==200 else sys.exit(1)"
      interval: 10s
      timeout: 3s
      retries: 5
      start_period: 10s
    user: "999:999"
    read_only: true
    tmpfs:
      - /tmp
    cap_drop:
      - ALL
    security_opt:
      - "no-new-privileges:true"
    restart: unless-stopped

networks:
  ec: {}
  verify-internal:
    internal: true
  egress: {}

volumes:
  quarantine: {}
  catalog-db: {}
  local-db: {}
  amule-state: {}
```

- [ ] **Step 4: Commit**

```bash
git add -A compose.smoke.yaml tests/smoke
git rm --cached compose.yaml compose.hardening.yml 2>/dev/null || true
git commit -m "chore(deploy): retire compose.yaml/hardening, smoke autonome, fixture download"
```

---

### Task 8: Étendre `test_compose_smoke.py`

**Files:**
- Modify: `packages/crawler/tests/integration/test_compose_smoke.py`

**Interfaces:**
- Consumes : `examples/*.yaml`, `compose.smoke.yaml`. Le test reste marqué `compose_integration` (exclu de la couverture) mais DOIT passer `ruff` + `mypy --strict`.

- [ ] **Step 1: Rebaser la base smoke + renommer le profil `full`→`download`**

Dans `test_compose_smoke.py`, remplacer la base sur deux fichiers par le smoke autonome, et `full`→`download` partout :
- `_COMPOSE` supprimé ; `project_files` ne yield plus que `(_SMOKE,)`.
- `_FULL_LOCAL_VOLUMES` → `_DOWNLOAD_LOCAL_VOLUMES`, première entrée `./tests/smoke/local.download.yaml`.
- `_down(...)`, `test_build_succeeds`, `test_full_*` : `--profile full` → `--profile download` ; renommer `test_full_*` → `test_download_*`.

Remplacer :

```python
_COMPOSE = _REPO_ROOT / "compose.yaml"
_SMOKE = _REPO_ROOT / "compose.smoke.yaml"
```

par :

```python
_SMOKE = _REPO_ROOT / "compose.smoke.yaml"
```

et `project_files` :

```python
@pytest.fixture
def project_files() -> Iterator[tuple[Path, ...]]:
    """Fichier compose smoke autonome + tear-down encadrant."""
    base = (_SMOKE,)
    _down(base)
    try:
        yield base
    finally:
        _down(base)
```

et dans `_down`, `test_build_succeeds`, `test_download_*` : remplacer chaque `"full"` par `"download"`.

- [ ] **Step 2: Ajouter la validation `config` des points d'entrée réels**

Ajouter en tête (après les imports) :

```python
_ENTRY_POINTS = ("gluetun", "sans-vpn-lowid", "sans-vpn-highid")
_CONFIG_CASES: tuple[tuple[str, tuple[str, ...]], ...] = tuple(
    (entry, profiles)
    for entry in _ENTRY_POINTS
    for profiles in (("observer",), ("download",), ("download", "monitoring"))
)
_CONFIG_ENV = {
    "WIREGUARD_PRIVATE_KEY": "x",
    "AMULE_EC_PASSWORD": "x",
    "GRAFANA_PWD": "x",
    "SERVER_COUNTRIES": "",
    "DOCKER_GID": "0",
    "LISTEN_PORT": "4662",
    "LISTEN_PORT_UDP": "4672",
}
```

et le test paramétré :

```python
@pytest.mark.parametrize("entry,profiles", _CONFIG_CASES)
def test_entrypoint_config_renders(entry: str, profiles: tuple[str, ...]) -> None:
    """`docker compose -f examples/<entry>.yaml --profile … config` rend sans erreur.

    Verrouille include + forward-refs + ancres/merge + interpolation (pas de daemon requis ;
    les sources de bind-mount n'ont pas besoin d'exister pour `config`).
    """
    profile_flags: list[str] = []
    for profile in profiles:
        profile_flags += ["--profile", profile]
    command = [
        "docker", "compose", "-f", f"examples/{entry}.yaml", *profile_flags, "config",
    ]
    result = subprocess.run(
        command,
        cwd=_REPO_ROOT,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **_CONFIG_ENV},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
```

- [ ] **Step 3: Vérifier ruff + mypy (implémenteur, sans Docker)**

```bash
cd /home/geoffrey/Repositories/emule-indexer
uv run ruff check packages/crawler/tests/integration/test_compose_smoke.py
uv run ruff format --check packages/crawler/tests/integration/test_compose_smoke.py
uv run mypy
```
Expected: aucune erreur.

- [ ] **Step 4: Validation fonctionnelle (Geoffrey, via `!`)**

```bash
( cd packages/crawler && uv run pytest -m compose_integration --no-cov -q )
```
Expected: les `test_entrypoint_config_renders[...]` passent (9 cas) + le smoke build/up `download`/observer/fail-fast.

- [ ] **Step 5: Commit**

```bash
git add packages/crawler/tests/integration/test_compose_smoke.py
git commit -m "test(deploy): valide config des points d'entrée + rebase smoke autonome (download)"
```

---

### Task 9: Documentation pas-à-pas + mises à jour transverses

**Files:**
- Rewrite: `docs/runbook-deployment.md`
- Modify: `docs/runbook-administration.md`, `docs/runbook-troubleshooting.md` (refs `full`→`download`, chemins config, port-sync via gluetun)
- Modify: `CLAUDE.md` (invariant « Two run modes » + carte des compose)
- Modify: en-têtes des `examples/*.yaml` (déjà posés en Tasks 5-6 — vérifier la phrase de matrice)

- [ ] **Step 1: Réécrire `docs/runbook-deployment.md` selon spec §11**

Structure (suivre `docs/superpowers/specs/2026-06-20-deploiement-exemples-design.md` §11) :
1. **Aide au choix** — coller la matrice **verbatim** depuis spec §11.1 (colonnes : Stack, Expose son IP domestique, Ouvrir un port sur sa box, Rend High-ID possible, Compatible Docker Desktop, Demande un VPN commercial, VPN avec port forwarding) + le mapping stack→fichier + les cellules non triviales.
2. **Prérequis par stack** — spec §11.2 (A/B/C/D + « Toutes »).
3. **Pas-à-pas cross-platform** — spec §11.3, étapes 1-6 avec la dualité `cp` / `Copy-Item` et la note WSL2 ; commandes `docker compose -f examples/<fichier> --profile <observer|download> [--profile monitoring] up -d`.
4. **Options orthogonales** — spec §11.4 (mode / monitoring / gVisor `CONTAINER_RUNTIME=runsc`, Linux+runsc).
5. **Renvois** vers `runbook-administration.md` (High-ID avancé, gVisor, clamav, métriques) et `runbook-troubleshooting.md`.

Glossaire conservé (eD2k/Kad/EC/Low-ID/High-ID/quarantine/GHCR). Le « premier démarrage : amorçage server.met/nodes.dat » et « Low-ID c'est normal » de l'ancien runbook sont conservés/adaptés.

- [ ] **Step 2: Mettre à jour administration & dépannage**

Dans `docs/runbook-administration.md` et `docs/runbook-troubleshooting.md` : remplacer les mentions « mode full » par « mode download » et `--profile full` par `--profile download` ; corriger les chemins config (`config/local.yaml` → `config/crawler/<mode>.yaml`, `config/crawler.yaml` → `config/crawler/crawler.yaml`) ; le High-ID gluetun (port-sync) devient un opt-in de la stack `gluetun` ; le durcissement gVisor passe par `CONTAINER_RUNTIME=runsc` (plus `compose.hardening.yml`).

```bash
cd /home/geoffrey/Repositories/emule-indexer
grep -rn "compose.yaml\|compose.hardening\|--profile full\|mode full\|config/local\|config/crawler.yaml\|config/matcher.yaml\|config/targets.yaml" docs/runbook-administration.md docs/runbook-troubleshooting.md docs/README.md
```
Corriger chaque occurrence trouvée.

- [ ] **Step 3: Mettre à jour `CLAUDE.md`**

- Invariant « Two run modes » : `observer (no verifier_url) is crawl-only; full (verifier_url set)` → remplacer `full` par `download`.
- Section « Packaging » de la carte des sous-systèmes : `compose*.yaml` → décrire `bricks/compose.core.yaml` + `examples/*.yaml` + `compose.smoke.yaml` ; retirer la mention `compose.hardening.yml` (→ knob `CONTAINER_RUNTIME`).
- Confinement : « gVisor (`compose.hardening.yml`) » → « gVisor via le knob `CONTAINER_RUNTIME=runsc` ».

```bash
grep -n "full\|compose.hardening\|compose\*.yaml\|observer" CLAUDE.md
```
Corriger les occurrences pertinentes.

- [ ] **Step 4: Vérifier les en-têtes des `examples/*.yaml`**

S'assurer que chaque `examples/*.yaml` porte en tête : lettre + titre de stack, une phrase de la matrice (expose IP / High-ID / Docker Desktop), la commande de lancement, le pointeur `docs/runbook-deployment.md` (posés en Tasks 5-6 ; compléter si besoin).

- [ ] **Step 5: Commit**

```bash
git add docs/ CLAUDE.md examples/
git commit -m "docs(deploy): runbook pas-à-pas (matrice + prérequis par stack), full→download, gVisor knob"
```

---

## Validation finale (Geoffrey, via `!`)

```bash
cd /home/geoffrey/Repositories/emule-indexer
# 1. Gate par paquet inchangé (aucun code Python de prod ajouté)
( cd packages/crawler  && uv run pytest -q )
( cd packages/verifier && uv run pytest -q )
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run sqlfluff lint packages/crawler/src
# 2. Compose : la brique + les 3 points d'entrée + le smoke
( cd packages/crawler && uv run pytest -m compose_integration --no-cov -q )
# 3. Bring-up réel d'un scénario clé en main (ex. C + monitoring), puis Grafana sur :3000
env AMULE_EC_PASSWORD=secret GRAFANA_PWD=secret \
  docker compose -f examples/sans-vpn-lowid.yaml --profile download --profile monitoring up -d
```

Après validation verte, taguer le jalon (annoté, non poussé) : `git tag -a v0.15.0-deploy-examples -m "..."`.
