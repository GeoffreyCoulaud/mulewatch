# Exemples de déploiement composables (sans gluetun + clé en main) — design (2026-06-20)

> **🔵 Note 2026-06-29 (a)** : la section §9 (gVisor / `CONTAINER_RUNTIME`) est dépréciée — gVisor a été retiré du projet (YAGNI).
>
> **🔵 Note 2026-06-29 (b)** : la structure `deploy/examples/<scénario>.yaml` décrite dans cette
> spec a été **remplacée** par `deploy/{gluetun,direct}.compose.yml` (+ `deploy/base.compose.yml`
> en fragment `include`), et `deploy/config/crawler/` est passé à une config unifiée
> `crawler.yml`. Voir [`2026-06-29-simplification-deploiement-design.md`](2026-06-29-simplification-deploiement-design.md).
> Le corps de cette spec reste à titre de record historique.
>
> Spec issue d'une session de brainstorming « un sujet à la fois ». Sujet : fournir **plusieurs
> exemples de déploiement** — dont des topologies **sans gluetun** — sous forme de **briques
> composables avec points d'entrée distincts**, et un socle **monitoring clé en main**
> (Prometheus + Grafana). Mécanisme retenu : `include` Compose (briques) + profils + variabilisation
> `.env`, **pas** `-f`/`COMPOSE_FILE` (cf. §2 pour le cheminement, §15 pour les décisions). Base du
> plan d'implémentation (writing-plans). C'est un livrable **config + docs** : aucun code de prod
> Python (cf. §14).

## 1. Contexte et objectif

Le déploiement actuel se résume à trois fichiers à la racine :

- `compose.yaml` — base **couplée à gluetun** : `amuled` partage la netns de gluetun
  (`network_mode: service:gluetun`), le crawler vise `gluetun:4712`. Profils `observer` / `full`.
- `compose.hardening.yml` — override `-f` qui ajoute `runtime: runsc` (gVisor) à crawler + verifier.
- `compose.smoke.yaml` — override `-f` CI qui *retire* gluetun (`!override [disabled]`,
  `network_mode: !reset null`) pour le test `compose_integration`.

Manques (handoff 2026-06-17 §6) : **aucune topologie sans VPN-conteneur**, et **rien de clé en
main** (pas de monitoring fourni). On veut combler ça **sans** régresser le durcissement
niveau-conteneur ni les invariants du projet.

**Objectif** : un jeu d'**exemples de déploiement** où chaque scénario est **un point d'entrée
distinct** (`docker compose -f examples/<scénario>.yaml up`), qui réutilise des **briques communes**
via `include`, expose un **monitoring clé en main** activable par profil, et se règle par un `.env` de
**vrais secrets/knobs** (pas d'orchestration cachée).

### 1.1 Scénarios couverts (Geoffrey)

| Point d'entrée | VPN | Joignabilité | Notes |
|---|---|---|---|
| `examples/gluetun.yaml` | gluetun (conteneur) | Low-ID (High-ID via port-sync, opt-in) | **l'existant, nommé** |
| `examples/sans-vpn-lowid.yaml` | aucun (responsabilité hôte) | Low-ID | le plus simple |
| `examples/sans-vpn-highid.yaml` | aucun | High-ID **statique** (port publié + redirigé) | pas de port-sync |

Le cas « hôte déjà sous VPN » est un sous-cas documentaire de `sans-vpn-*` (le tunnel est au niveau
hôte ; les conteneurs sortent normalement), pas un fichier dédié.

## 2. Cheminement (pourquoi `include` + points d'entrée, et pas `-f`/`.env`)

Trois mécanismes Compose étaient en lice pour partager le commun :

1. **`-f` additif + `.env`/`COMPOSE_FILE`** — empiler une base + des overlays, choisir le scénario par
   `COMPOSE_FILE` dans `.env`. **Écarté** (Geoffrey) : pilotage par `.env` = « magie » peu lisible ;
   on ne veut pas deviner quels `-f` empiler.
2. **`extends`** — réutilisation au niveau service. Possible mais perd `depends_on`/`volumes_from`,
   ne porte pas les réseaux/volumes top-level → verbeux et piégeux.
3. **`include`** (retenu) — chaque point d'entrée `include` les briques communes ; **un fichier =
   un scénario, lançable seul**. C'est exactement « briques composables + points d'entrée distincts ».

Les **deux contraintes réelles** d'`include` (doc Compose, réf. `include` / `extension` /
`fragments`) ont façonné le design :

- *« Each path listed in the `include` section loads as an individual Compose application model. »* →
  **chaque fichier inclus doit être cohérent seul** (tous les réseaux/volumes qu'il référence doivent
  y être définis).
- *« Compose displays a warning if resource names conflict and **doesn't try to merge them**. »* →
  `include` est **additif**, pas un override ; on ne peut pas redéfinir/surcharger un service inclus.

D'où le design : **tout le commun dans UNE brique `core`** (jamais redéfini ailleurs → zéro
conflit), **`amuled`/VPN uniquement dans les points d'entrée** (la seule partie qui varie), et les
axes orthogonaux (`observer`/`download`, `monitoring`) exprimés par **profils**, pas par fichiers.

### 2.1 Validations empiriques (machine de Geoffrey, `docker compose config`)

- **Forward-ref réseau** — un service du point d'entrée (`amuled`) référence un réseau défini dans la
  brique incluse (`ec`) : **`OK forward-ref`** (les ressources incluses sont copiées dans le modèle,
  la validation se fait sur le modèle final). C'est l'hypothèse porteuse du design ; **levée**.
- **Ancres + merge YAML** (`x-… : &a` / `<<: *a`) avec profils : confirmé par la doc (pages
  *Fragments*/*Extension*, idiome recommandé). Caveat : `<<` fusionne des **mappings, pas des
  listes** (pas d'append) → les listes qui diffèrent (`volumes`) sont écrites par service ; les
  listes identiques (`cap_drop`, alias) vivent dans l'ancre. Test éclair fourni au plan.

## 3. Arborescence cible

```
<racine>
├── bricks/
│   └── compose.core.yaml      # crawler-observer + crawler-download + verifier + freshclam
│                              #   + prometheus + grafana
│                              #   réseaux : ec, verify-internal (internal), egress
│                              #   volumes : quarantine, catalog-db, local-db, amule-state,
│                              #             clamav-db, prometheus-data, grafana-data
│                              #   ⇒ COHÉRENT seul ; définit TOUT sauf amuled & le VPN
├── examples/                  # ★ POINTS D'ENTRÉE — 1 fichier = 1 scénario, lançable seul
│   ├── gluetun.yaml           #   include core + gluetun(alias ec→amuled) + amuled(netns) + docker-proxy
│   ├── sans-vpn-lowid.yaml    #   include core + amuled(networks:[ec]) — aucun port
│   └── sans-vpn-highid.yaml   #   include core + amuled(networks:[ec]) + ports ${LISTEN_PORT}
├── .env.example               # vrais secrets/knobs (PAS de COMPOSE_FILE)
└── config/                    # sous-dossier par owner SI plusieurs fichiers ; sinon à plat
    ├── crawler/
    │   ├── crawler.yaml  matcher.yaml  targets.yaml         # canon, committés (pas de secret)
    │   ├── observer.example.yaml  download.example.yaml     # modèles committés (download : + verifier_url ; port-sync commenté)
    │   └── observer.yaml  download.yaml                     # GITIGNORÉS : déploiement + secret EC (copiés du .example)
    ├── grafana/
    │   ├── provisioning/datasources/prometheus.yaml
    │   ├── provisioning/dashboards/provider.yaml
    │   └── dashboards/emule-indexer.json                   # dashboard pré-fourni
    ├── verifier.yaml  verifier.example.yaml                # à plat (un fichier)
    └── prometheus.yml                                      # à plat ; scrape crawler:9090 + verifier:8000
```

Règle de rangement : un **sous-dossier par owner uniquement s'il a plusieurs fichiers** (`crawler/`,
`grafana/`) ; un owner mono-fichier reste **à plat** dans `config/` (`verifier.yaml`,
`prometheus.yml`). Plus de dossier `monitoring/` frère (ce n'était que de la config Prometheus/
Grafana). `compose.yaml`, `compose.hardening.yml` disparaissent (cf. §9, §13) ; `compose.smoke.yaml`
est rebasé (§13).

## 4. La brique `core` (`bricks/compose.core.yaml`)

Cohérente **seule** : elle définit tous les réseaux/volumes ET tous les services qui les utilisent,
**sauf `amuled` et le VPN** (apportés par les points d'entrée). Champs communs du crawler partagés
par **ancre YAML** (`x-crawler-common: &crawler-common`), conformément à l'idiome Compose documenté.

Services :

- **`crawler-observer`** (`profiles: [observer]`) et **`crawler-download`** (`profiles: [download]`)
  — deux variantes, une par mode (§6). Une seule tourne selon le profil.
- **`verifier`**, **`freshclam`** (`profiles: [download]`) — pipeline de vérification (inchangé vs
  actuel : verifier `read_only`, `verify-internal` interne, clamav opt-in `ENABLED_CHECKS`, freshclam
  sur `egress`).
- **`prometheus`**, **`grafana`** (`profiles: [monitoring]`) — monitoring (§7).

Réseaux : `ec` (EC + P2P), `verify-internal` (`internal: true`, le verifier n'a pas d'Internet),
`egress` (freshclam). Volumes : `quarantine`, `catalog-db`, `local-db`, `amule-state`, `clamav-db`,
plus `prometheus-data`, `grafana-data`.

Durcissement niveau-conteneur **préservé** sur les services bâtis (crawler/verifier) : `user 999:999`,
`read_only`, `tmpfs /tmp`, `cap_drop: ALL`, `no-new-privileges`, `pids_limit`, `mem_limit`.

## 5. Les points d'entrée (`examples/*.yaml`)

Chaque point d'entrée `include` la brique en **syntaxe longue** avec `project_directory` = racine
(§12), puis définit **uniquement** la partie réseau qui le distingue :

```yaml
# examples/sans-vpn-highid.yaml (esquisse)
name: emule-indexer
include:
  - path: ../bricks/compose.core.yaml
    project_directory: ..
services:
  amuled:
    image: ngosang/amule:3.0.0-1
    container_name: amuled
    profiles: [observer, download]
    networks: [ec]
    environment:
      GUI_PWD: ${AMULE_EC_PASSWORD:?}
    ports:
      - "${LISTEN_PORT:-4662}:4662/tcp"
      - "${LISTEN_PORT:-4662}:4662/udp"   # cf. §10 (port d'écoute amuled à câbler)
    volumes:
      - amule-state:/home/amule/.aMule
      - quarantine:/data/quarantine
    restart: unless-stopped
```

Différences par scénario :

- **`gluetun.yaml`** : ajoute `gluetun` (cap NET_ADMIN, `/dev/net/tun`, `VPN_*`), `amuled` en
  `network_mode: service:gluetun` (+ `container_name: amuled` pour le restart port-sync), et
  `docker-proxy` (`profiles: [download]`). `gluetun` porte l'**alias réseau `amuled` sur `ec`**
  (§5.1). Pas de `ports:` sur amuled (le port sort par gluetun).
- **`sans-vpn-lowid.yaml`** : `amuled` sur `ec`, **aucun** `ports:`.
- **`sans-vpn-highid.yaml`** : `amuled` sur `ec` + `ports: ${LISTEN_PORT}/tcp+udp` (High-ID statique,
  §10).

### 5.1 Host EC unifié (`amuled` partout)

Pour qu'**un seul** `local.yaml` serve tous les scénarios, le host EC est **toujours `amuled`** :

- sans-vpn : le service s'appelle `amuled` sur `ec` → DNS `amuled` natif.
- gluetun : `amuled` partage la netns de gluetun (aucune présence DNS propre sur `ec`) → on pose
  `networks: { ec: { aliases: [amuled] } }` **sur gluetun** ; `amuled:4712` résout vers l'adresse `ec`
  de gluetun, qui sert l'EC d'amuled (bind `0.0.0.0:4712` dans la netns partagée). Le `container_name:
  amuled` (API Docker, pour le restart port-sync) et l'alias réseau `amuled` vivent dans des espaces
  de noms distincts → pas de conflit (amuled n'étant pas sur `ec`).

## 6. Mode `observer` / `download` par profil

`full` était trompeur (« toute la stack » vs « le mode qui télécharge + vérifie ») → renommé
**`download`**. La paire se lit *observer seul* vs *observer + télécharger*.

Le mode = quel `local.yaml` (présence de `verifier_url`) + quels services. `include` ne pouvant pas
surcharger le crawler de `core`, on exprime le mode par **deux variantes de service** profilées, qui
partagent l'ancre `&crawler-common` et ne diffèrent que par `profiles` + le `local.yaml` monté :

```yaml
x-crawler-common: &crawler-common
  image: ghcr.io/geoffreycoulaud/emule-indexer-crawler:${IMAGE_TAG:-latest}
  user: "999:999"
  read_only: true
  tmpfs: [/tmp]
  cap_drop: [ALL]
  security_opt: ["no-new-privileges:true"]
  pids_limit: 256
  mem_limit: 512m
  runtime: ${CONTAINER_RUNTIME:-runc}          # cf. §9 (gVisor)
  command: ["--local","/app/config/local.yaml","--crawler","/app/config/crawler.yaml",
            "--targets","/app/config/targets.yaml","--matcher","/app/config/matcher.yaml"]
  restart: unless-stopped

services:
  crawler-observer:
    <<: *crawler-common
    profiles: [observer]
    networks:
      ec: { aliases: [crawler] }
      egress: {}                              # notifications apprise (pas de verify-internal en observer)
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
      ec: { aliases: [crawler] }
      verify-internal: {}
      egress: {}
    depends_on: [verifier]                      # intra-core uniquement
    volumes:
      - ./config/crawler/download.yaml:/app/config/local.yaml:ro
      - ./config/crawler/crawler.yaml:/app/config/crawler.yaml:ro
      - ./config/crawler/targets.yaml:/app/config/targets.yaml:ro
      - ./config/crawler/matcher.yaml:/app/config/matcher.yaml:ro
      - catalog-db:/data/catalog
      - local-db:/data/local
      - quarantine:/data/quarantine
```

- **Alias réseau `crawler`** sur les deux variantes → Prometheus scrape `crawler:9090` quelle que
  soit la variante active (même astuce que l'alias `amuled`). Un seul crawler tourne à la fois.
- **`depends_on` intra-`core` seulement** (`crawler-download → verifier`). On **retire** le
  `depends_on: amuled` (ce serait une réf « avant » de `core` *vers* un service du point d'entrée —
  direction **non** validée, contrairement au forward-ref réseau). Ordonnancement assuré par
  `restart: unless-stopped` (déjà le pattern du projet : le crawler réessaie l'EC jusqu'à ce qu'amuled
  réponde, et fail-fast/retry le health-check du verifier).

Lancement :

```bash
docker compose -f examples/sans-vpn-highid.yaml --profile download --profile monitoring up -d
docker compose -f examples/sans-vpn-lowid.yaml  --profile observer up -d
```

## 7. Monitoring clé en main (`profiles: [monitoring]`)

Prometheus + Grafana vivent **dans `core`** (une brique monitoring séparée référencerait
`ec`/`verify-internal` qu'elle ne définit pas → incohérente seule ; cf. §2). Activables à la demande
par le profil `monitoring`.

- **prometheus** : sur `ec` (scrape `crawler:9090`) + `verify-internal` (scrape `verifier:8000`),
  volume `prometheus-data`, monte `./config/prometheus.yml:ro`. En mode `observer` la
  cible `verifier:8000` est *down* (sans gravité — Prometheus tolère).
- **grafana** : sur `ec` (interroge prometheus), publie `${GRAFANA_PORT:-3000}:3000`,
  `GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_PWD:?}`, volume `grafana-data`, monte
  `./config/grafana/provisioning` (datasource Prometheus + provider de dashboards) +
  `./config/grafana/dashboards/emule-indexer.json` en RO → **datasource et dashboard prêts au premier
  démarrage**.

Assets statiques (`config/prometheus.yml`, provisioning + dashboard JSON sous `config/grafana/`),
**une seule fois**, résolus depuis la racine via `project_directory` (§12).

## 8. Variabilisation (`.env`) — secrets et knobs uniquement

`.env` ne contient que de **vrais réglages** (interpolés par Compose), **jamais** de `COMPOSE_FILE` :

| Variable | Rôle | Forme |
|---|---|---|
| `AMULE_EC_PASSWORD` | mot de passe EC (amuled + local.yaml) | `${…:?}` (obligatoire) |
| `WIREGUARD_PRIVATE_KEY` | clé VPN (scénario gluetun) | `${…:?}` côté gluetun |
| `SERVER_COUNTRIES` | pays de sortie VPN | `${…:-}` |
| `LISTEN_PORT` | port d'écoute publié (High-ID statique) | `${…:-4662}` |
| `GRAFANA_PWD` | admin Grafana | `${…:?}` (profil monitoring) |
| `GRAFANA_PORT` | port d'expo Grafana | `${…:-3000}` |
| `IMAGE_TAG` | tag des images GHCR | `${…:-latest}` |
| `CONTAINER_RUNTIME` | runtime des services bâtis (gVisor) | `${…:-runc}` (§9) |

`${VAR:?}` fait **échouer** `docker compose` si un secret manque (filet documenté).

## 9. Durcissement gVisor — knob `CONTAINER_RUNTIME` (remplace `compose.hardening.yml`)

L'override `-f compose.hardening.yml` est **incompatible** avec `include` : les fichiers `-f` sont
fusionnés **avant** que `include` copie `core`, donc un `-f` qui touche `crawler-download`/`verifier`
(définis dans `core`) **entre en conflit** avec la copie d'`include` (warning, pas de merge → résultat
non fiable). On remplace donc l'override par un **knob** : `runtime: ${CONTAINER_RUNTIME:-runc}` sur
les services **bâtis** (crawler-observer, crawler-download, verifier) dans `core`. gVisor opt-in =
`CONTAINER_RUNTIME=runsc` dans `.env` (exige `runsc` enregistré sur l'hôte). `amuled`/`gluetun`
(images tierces) gardent le runtime par défaut, comme aujourd'hui. C'est un **knob de déploiement
unique et nommé** (pas de l'orchestration cachée) → cohérent avec le refus du pilotage par `.env`.

## 10. High-ID

- **sans-vpn (statique)** : `examples/sans-vpn-highid.yaml` publie `${LISTEN_PORT}/tcp+udp` ;
  l'opérateur redirige ce port sur sa box/routeur (ou via un VPN hôte à port forwarding). **Pas de
  port-sync**, pas de `docker-proxy`. À câbler : le **port d'écoute d'amuled** (TCP 4662 / UDP 4672)
  doit égaler `LISTEN_PORT` — selon ce qu'expose l'image `ngosang/amule` (amule.conf monté ou réglage
  EC) ; **point d'implémentation à trancher au plan** (défaut 4662).
- **gluetun (port-sync, opt-in)** : `examples/gluetun.yaml` embarque `docker-proxy` (surface
  restart-amuled-only). High-ID = **armer le port-sync** en décommentant le bloc `port_sync`
  (`gluetun_control_url: http://gluetun:8000`, `restarter_url: http://docker-proxy:2375`) dans
  `config/crawler/download.yaml`. Désarmé par défaut → Low-ID (état normal documenté). Le bloc commenté
  vit dans le `download.yaml` partagé (inerte hors scénario gluetun) — petit compromis assumé du
  `local.yaml` unique (§5.1).

## 11. Documentation pas-à-pas (clé en main)

Le « clé en main » exige un chemin **choisir → lancer**. `docs/runbook-deployment.md` est **réécrit**
autour des blocs ci-dessous ; il renvoie aux runbooks **administration** / **dépannage** (déjà
scindés) sans dupliquer.

### 11.1 Aide au choix — matrice fonctionnalités × stack

Le lecteur part de **ses contraintes** (colonnes) et de **son intention** (vie privée / joignabilité)
et repère la ligne qui colle. **Un flowchart a été écarté** (Geoffrey : une matrice se scanne mieux).

| Stack | Expose son IP domestique | Ouvrir un port sur sa box | Rend High-ID possible | Compatible Docker Desktop (Win/macOS) | Demande un VPN commercial | VPN avec port forwarding |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| **A** · gluetun, Low-ID | Non | Non | Non | Oui | **Oui** | Non |
| **B** · gluetun, High-ID | Non | Non | **Oui** | **Non** | **Oui** | **Oui** |
| **C** · sans VPN, Low-ID | **Oui** | Non | Non | Oui | Non | Non |
| **D** · sans VPN, High-ID | **Oui** | **Oui** | **Oui** | Oui | Non | Non |

Cellules non triviales : **B** — le port est forwardé par le **fournisseur VPN** (NAT-PMP), pas la
box ; **B** incompatible Docker Desktop (socket refusé au port-sync, réf.
`docs/reference/2026-06-17-docker-desktop-rootless-socket.md`) ; **« expose IP domestique »** = Non
pour A/B (sortie par le VPN), Oui pour C/D (sortie par la connexion perso, sauf VPN à l'hôte).

Mapping stack → fichier :

| Stack | Fichier `examples/` | High-ID via |
|---|---|---|
| A | `gluetun.yaml` | — (Low-ID) |
| B | `gluetun.yaml` (+ port-sync armé) | port-sync auto (VPN PF) |
| C | `sans-vpn-lowid.yaml` | — (Low-ID) |
| D | `sans-vpn-highid.yaml` | port statique redirigé |

### 11.2 Prérequis spécifiques par stack

Documentés **au moment du choix** (les prérequis *contraignants* sont déjà des colonnes en 11.1) :

- **A** : abonnement VPN WireGuard (n'importe lequel) → clé privée dans `.env` ; `/dev/net/tun`
  (fourni aussi par Docker Desktop). Aucun port à ouvrir.
- **B** : **hôte Linux Docker rootful** + VPN **avec port forwarding** (Proton/PIA/PrivateVPN/
  PerfectPrivacy) → clé dans `.env` ; armer le bloc `port_sync` dans la config crawler montée.
- **C** : rien de spécial (le plus simple). ⚠ ton IP domestique est exposée aux pairs (mets un VPN au
  niveau hôte pour l'éviter).
- **D** : rediriger `LISTEN_PORT` (TCP + UDP) sur ta box vers cette machine + autoriser au pare-feu
  (Windows compris) ; régler `LISTEN_PORT` dans `.env`. ⚠ IP domestique exposée.
- **Toutes** : Docker + compose v2 ; copier `.env.example` → `.env` et
  `config/crawler/<mode>.example.yaml` → `<mode>.yaml` (mot de passe EC) ; choisir mode / monitoring /
  gVisor (11.4).

### 11.3 Pas-à-pas (cross-platform, Windows compris)

Les commandes `docker compose …` sont **identiques** sur Linux / macOS / Windows. Seule la copie de
fichiers diffère ; **aucune** étape n'utilise `chmod`/`chown`/chemins absolus Unix (le durcissement
`user: 999`/`read_only` est porté par compose). Sous Windows : **PowerShell** (équivalents fournis) ou
un shell **WSL2** (commandes Unix identiques).

1. **Prérequis** (selon la stack, cf. 11.2) : Docker + compose v2 ; + `/dev/net/tun` si gluetun ;
   + port redirigé si **D** ; + `runsc` si gVisor.
2. **Secrets** — `cp .env.example .env` (Linux/macOS/WSL2) ou `Copy-Item .env.example .env`
   (PowerShell), puis renseigner ce qui concerne ta stack (`AMULE_EC_PASSWORD` toujours ;
   `WIREGUARD_PRIVATE_KEY` pour A/B ; `LISTEN_PORT` pour D ; `GRAFANA_PWD` si monitoring).
3. **Config crawler** — copier `config/crawler/<mode>.example.yaml` → `config/crawler/<mode>.yaml`
   (même dualité `cp`/`Copy-Item`), y mettre le mot de passe EC ; pour **B**, décommenter le bloc
   `port_sync`.
4. **Images** — `docker compose -f examples/<fichier> --profile <mode> pull` (ou `build`).
5. **Lancer** — `docker compose -f examples/<fichier> --profile <observer|download> [--profile
   monitoring] up -d` (gVisor : préfixer `CONTAINER_RUNTIME=runsc`, hôte Linux uniquement).
6. **Vérifier** — `docker compose -f examples/<fichier> ps` + logs ; monitoring → Grafana sur
   `http://<hôte>:${GRAFANA_PORT}` (admin / `GRAFANA_PWD`).

### 11.4 Options orthogonales (toutes stacks)

`observer`/`download` (profil), `monitoring` (profil), `gVisor` (`CONTAINER_RUNTIME=runsc`, **Linux +
runsc**). Pas des colonnes de 11.1 (elles seraient ✓ partout).

### 11.5 En-tête des fichiers `examples/*.yaml`

Chaque point d'entrée porte un **commentaire d'en-tête** : lettre + titre de la stack, sa ligne de
matrice en une phrase, la commande de lancement, et un pointeur vers ce runbook → lisible dès
l'ouverture du fichier.

## 12. Résolution des chemins & contraintes `include`

- **`project_directory: ..`** (racine) sur chaque `include` → les chemins relatifs de `core`
  (`build.context: .`, `./config/crawler/…`, `./config/verifier.yaml`, `./config/prometheus.yml`,
  `./config/grafana/…`) se résolvent depuis la **racine**, proprement, alors même que `core` vit dans
  `bricks/`. (Doc : `project_directory` « defines a base path to
  resolve relative paths », défaut = dossier du fichier inclus → on le force à la racine.)
- Les **points d'entrée** n'ont **aucun chemin relatif** (amuled/gluetun/docker-proxy sont
  image-based ; le socket Docker est absolu) → insensibles au répertoire projet (= `examples/`). On
  fixe `name: emule-indexer` pour des noms de conteneurs stables (sinon préfixe `examples-…`).
- **Cohérence-seul + non-merge** (§2) respectées par construction : réseaux/volumes définis **une
  seule fois** (dans `core`) ; `amuled`/VPN **uniquement** dans les points d'entrée ; aucun service
  n'est redéfini de part et d'autre.

## 13. Migration (impacts sur l'existant)

- **`compose.yaml` éclaté** : son commun → `bricks/compose.core.yaml` ; ses bouts gluetun/amuled/
  docker-proxy → `examples/gluetun.yaml`. Plus de `compose.yaml` à la racine (un `docker compose up`
  nu n'a plus de défaut — on lance toujours via un point d'entrée).
- **`compose.hardening.yml` supprimé** → knob `CONTAINER_RUNTIME` (§9).
- **`compose.smoke.yaml` rebasé** : ne peut plus faire `-f compose.yaml …`. Le smoke échange des
  configs et utilise des volumes éphémères (un cas d'**override**, incompatible avec `include`) → il
  reste un stack **autonome** (ou réutilise `core` via un répertoire de config paramétré) ; arbitrage
  au plan, le test `compose_integration` servant de garde-fou.
- **`config/` réorganisé** (Geoffrey) : `git mv` (historique préservé) `config/{crawler,matcher,
  targets}.yaml` → `config/crawler/` ; `config/verifier.yaml` (+ `.example`) **reste à plat** (déjà
  là) ; `config/local.example.yaml` **scindé** en `config/crawler/observer.example.yaml` +
  `download.example.yaml` ; nouveaux `config/prometheus.yml` (à plat) + `config/grafana/` (assets
  monitoring, ex-`monitoring/`). `.gitignore` : remplacer `config/local.yaml` par les entrées
  explicites `config/crawler/observer.yaml` + `config/crawler/download.yaml` (les `.example` restent
  suivis ; pas de wildcard — `crawler/` mélange committé et gitignoré). Le secret EC reste **inline**
  dans ces fichiers (non interpolés par Compose), dupliqué avec `AMULE_EC_PASSWORD` du `.env` —
  pattern existant **inchangé**.
- **Renommage `full` → `download`** propagé : profils, `config/crawler/download.yaml`, fixtures smoke
  (`local.full.yaml` → `local.download.yaml`), runbook (déploiement/administration/dépannage), et
  l'invariant CLAUDE.md « Two run modes : observer / full » → « observer / download ».
- **Runbook réécrit** (`docs/runbook-deployment.md`) selon §11 : matrice de sélection, prérequis par
  stack, pas-à-pas cross-platform (Windows compris), en-têtes des `examples/*.yaml`.
- **`.gitattributes`** forçant **LF** sur `.env*`, les `*.yaml` (compose/config) et le dashboard JSON
  → robustesse CRLF en cas d'édition sous Windows.

## 14. Tests / validation

Livrable **config + docs** : **aucun nouveau code de prod Python** → le gate 100 % branch est
**inchangé** (rien à couvrir ; on n'abaisse rien). La validation est :

- **`docker compose config`** sur **chaque point d'entrée × chaque profil** pertinent (observer /
  download / monitoring / + `CONTAINER_RUNTIME=runsc`) — extension du test `compose_integration`
  existant (marqueur d'intégration, **exclu de la couverture**, lancé par Geoffrey via `!` : pas de
  Docker dans le sandbox). Verrouille : YAML valide, forward-refs résolus, aucune clé/volume/réseau
  manquant, ancres/merge corrects, conflits `include` absents.
- **Smoke** (`compose_integration`) : build des 2 images + assemblage d'au moins un point d'entrée en
  `download` (configs smoke, volumes éphémères) — preuve de câblage de bout en bout.
- **Validations machine réelle** (au déploiement, hors CI) : High-ID statique (port redirigé →
  joignable), DV10 (Incoming amuled = `staging_dir`), et le port-sync gluetun s'il est armé.

## 15. Décisions actées / non-objectifs

- **`include` + points d'entrée distincts** (Geoffrey) : un fichier = un scénario, lançable seul ;
  pas de `-f`/`COMPOSE_FILE` ni de pilotage par `.env`. La duplication assumée se limite au service
  `amuled` (~10-15 lignes × 3).
- **Tout le commun dans `core`** : seule façon de respecter la cohérence-seul + le non-merge
  d'`include`. Le monitoring vit dans `core` (cross-cutting, pas une sous-app disjointe).
- **Mode par profil** (`observer`/`download`) via **deux variantes crawler** (ancre YAML partagée) ;
  **monitoring par profil**. Aucun fichier observer séparé, aucun env de sélection de mode.
- **Renommage `full` → `download`** (Geoffrey) : `full` trompeur.
- **`config/` rangé par owner, sous-dossier seulement si multi-fichiers** (Geoffrey) : `config/crawler/`
  et `config/grafana/` (plusieurs fichiers) ; `config/verifier.yaml` + `config/prometheus.yml` à plat
  (mono-fichier) ; pas de sous-dossier `local/` (les configs de déploiement gitignorées vivent à plat
  dans `crawler/`, marquées par le `.gitignore` + le suffixe `.example`). Fini le dossier
  `monitoring/` frère (ce n'était que de la config Prometheus/Grafana).
- **Host EC unifié `amuled`** (alias réseau côté gluetun) → un seul `local.yaml` par mode.
- **gVisor par knob `CONTAINER_RUNTIME`** : l'override `-f` est incompatible avec `include` (conflit
  pré-`include`) ; le knob est un réglage unique nommé, pas de la magie.
- **`depends_on: amuled` retiré** : forward-ref `core → point d'entrée` non validé ; `restart:
  unless-stopped` (pattern existant) assure l'ordre. `depends_on` reste intra-`core`.
- **Durcissement niveau-conteneur préservé** (cap_drop ALL / no-new-privileges / read_only / tmpfs /
  internal / rlimits) — non régressé par la restructuration.
- **Documentation pas-à-pas = livrable de premier plan** (§11) : matrice de sélection (fonctionnalités
  × stack), prérequis par stack, pas-à-pas. **Flowchart écarté** (Geoffrey : une matrice se scanne
  mieux ; on liste les contraintes en colonnes).
- **Posture Windows clarifiée** (Geoffrey) : « Windows non supporté » = verifier **natif** (Linux-only,
  `preexec_fn`/`resource`/`setsid`) ; le **déploiement sur hôte Windows via Docker Desktop** est
  supporté pour A/C/D (le verifier reste un conteneur Linux), **B exclu** (socket port-sync). Commandes
  du runbook **cross-platform** (`docker compose` identique ; `cp`/`Copy-Item` ; option WSL2).
- **Hors périmètre** : autres providers détaillés un par un (gluetun reste générique ; un exemple
  ProtonVPN suffit) ; un fichier dédié « hôte sous VPN » (sous-cas doc de sans-vpn) ; toucher au code
  Python du crawler/verifier (mode = config, pas code) ; serveur Prometheus/Alertmanager externe et
  dashboards au-delà de celui fourni (infra homelab, déjà acté hors-repo dans la spec observabilité) ;
  rendre le stack composable par `-f` (écarté §2).
