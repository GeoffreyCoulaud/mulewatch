# Runbook de déploiement — emule-indexer

Ce guide explique comment **mettre en route** la stack `docker compose` d'emule-indexer sur une
machine (homelab, serveur). Il vise un public **moyennement technique** : à l'aise avec un terminal
et Docker, sans connaître le détail interne du projet. Le sujet du catalogue reste **le fichier**,
jamais la personne.

Deux profils de déploiement :

- **observer** — recherche + catalogage + notifications. Ne télécharge **rien**.
- **full** — observer + téléchargement automatique + vérification isolée des fichiers reçus.

---

## Glossaire (sigles utilisés ici)

| Terme | Signification |
|-------|---------------|
| **VPN** | Tunnel chiffré qui masque l'IP de la machine. Ici assuré par le conteneur **gluetun**. |
| **eD2k** | Le réseau eDonkey2000 (serveurs centralisés). L'un des deux réseaux que surveille le projet. |
| **Kad** | Réseau Kademlia (décentralisé, sans serveur). Le second réseau surveillé. |
| **EC** | *External Connection* — le protocole par lequel le crawler pilote le client aMule (`amuled`). |
| **Low-ID / High-ID** | Statut de joignabilité sur eD2k. **High-ID** = la machine est joignable depuis l'extérieur (meilleures sources). **Low-ID** = elle ne l'est pas (fonctionne quand même, mais sous-optimal). |
| **port forwarding** | Redirection d'un port entrant à travers le VPN. Nécessaire pour obtenir un High-ID. |
| **quarantine** | Dossier isolé où atterrissent les fichiers téléchargés, **avant** vérification. Le verifier le lit en lecture seule, sans jamais ouvrir le réseau. |
| **GHCR** | GitHub Container Registry — l'endroit où sont publiées les images Docker du projet. |

---

## Prérequis

- **Docker** + **docker compose v2** (vérifier : `docker compose version`).
- Un compte chez un **fournisseur VPN WireGuard** (voir l'encadré ci-dessous), d'où vous tirez une
  **clé privée WireGuard**.
- Le device **`/dev/net/tun`** disponible sur l'hôte (gluetun en a besoin pour monter le tunnel).
- *(Optionnel)* le runtime **gVisor** (`runsc`) si vous voulez le durcissement noyau supplémentaire
  de `compose.hardening.yml`. Sans gVisor, n'utilisez simplement pas ce fichier : la base est déjà
  durcie.

> ### Quel fournisseur VPN ? (Low-ID vs High-ID)
> Le projet **n'exige aucun fournisseur précis**. N'importe quel VPN WireGuard supporté par gluetun
> fait tourner la stack. **Mais** : pour obtenir un **High-ID** (machine joignable, meilleures
> sources), il faut le **port forwarding**, que gluetun n'implémente que pour **4 fournisseurs** :
> **ProtonVPN, PIA, PrivateVPN, PerfectPrivacy**.
>
> - Avec l'un de ces 4 → port forwarding possible (le High-ID s'obtient en activant le port-sync,
>   cf. « Activer le High-ID »).
> - Avec tout autre fournisseur → la stack tourne en **Low-ID** (état normal, pas une erreur), à
>   moins d'ouvrir/rediriger un port vous-même.
>
> Le fournisseur se choisit dans `compose.yaml` (variable `VPN_SERVICE_PROVIDER`) ; les secrets
> vont dans `.env` (voir Setup).

---

## Démarrage rapide (étapes)

### 1. Récupérer le dépôt et préparer les secrets

```bash
cp .env.example .env
```

Renseignez dans `.env` :

- `WIREGUARD_PRIVATE_KEY` — la clé privée WireGuard de votre fournisseur VPN.
- `SERVER_COUNTRIES` — le pays de sortie souhaité (ex. `Switzerland`).
- `AMULE_EC_PASSWORD` — un mot de passe que **vous** choisissez ; il protège le canal EC entre le
  crawler et amuled.

Le `.env` est **gitignoré** : il ne sera jamais committé.

> Si votre fournisseur n'est pas ProtonVPN, ajustez aussi `VPN_SERVICE_PROVIDER` dans `compose.yaml`
> et, selon le fournisseur, les variables WireGuard correspondantes attendues par gluetun.

### 2. Configurer le crawler

```bash
cp config/local.example.yaml config/local.yaml
```

Renseignez dans `config/local.yaml` :

- `amules[].host: gluetun`, `amules[].port: 4712`, `amules[].password:` = la valeur de
  `AMULE_EC_PASSWORD`.
  *(L'hôte EC est `gluetun`, et non `amuled`, parce qu'amuled partage le réseau de gluetun.)*
- `catalog_db_path: /data/catalog/catalog.db` et `local_db_path: /data/local/local.db`.
- **Mode full uniquement** : décommentez le bloc `download_endpoint`, mettez `staging_dir:
  /data/quarantine` + `quarantine_dir: /data/quarantine`, et `verifier_url: http://verifier:8000`.
  *(C'est la présence de `verifier_url` qui bascule le crawler en mode full.)*

### 3. Récupérer (ou construire) les images

Tirer depuis GHCR (recommandé) :

```bash
docker compose --profile full pull   # --profile requis : tous les services sont profilés
```

Ou construire localement :

```bash
docker compose --profile full build
```

> **Images privées ?** Les packages GHCR sont privés par défaut. Soit vous les rendez publics dans
> les settings GitHub du package, soit vous vous authentifiez avant le pull :
> `docker login ghcr.io -u <user>` (avec un PAT ayant le scope `read:packages`).
>
> Références d'images : `ghcr.io/geoffreycoulaud/emule-indexer-crawler` et
> `ghcr.io/geoffreycoulaud/emule-indexer-verifier`.

### 4. Démarrer

Observer (pas de téléchargement) :

```bash
docker compose --profile observer up -d
```

Full (avec téléchargement + vérification) :

```bash
docker compose --profile full up -d
```

> En full, le crawler **vérifie que le verifier répond** au démarrage et **refuse de démarrer** s'il
> est injoignable (pas de téléchargement sans vérification). Si le verifier n'est pas encore prêt, le
> crawler s'arrête et son `restart: unless-stopped` le relance jusqu'à ce que le verifier soit sain.
> Pour éviter ces redémarrages, démarrez le verifier d'abord :
> ```bash
> docker compose --profile full up -d verifier
> docker compose --profile full up -d
> ```

### 5. Vérifier que ça tourne

```bash
docker compose logs -f crawler                  # suivre les logs du crawler
docker compose exec crawler ls /data            # /data/catalog, /data/local, /data/quarantine
```

Vous devriez voir le cycle s'enchaîner sur le vrai réseau eMule : recherche → (en full)
téléchargement → quarantaine → vérification.

---

## Premier démarrage : amorçage automatique du réseau

Au **tout premier run**, amuled doit récupérer une liste de serveurs eD2k (`server.met`) et une liste
de nœuds Kad (`nodes.dat`) pour pouvoir se connecter. **Bonne nouvelle : c'est automatique.**

- L'image **`ngosang/amule:3.0.0-1`** télécharge silencieusement `server.met` et `nodes.dat` au
  premier démarrage. C'est un comportement amorcé par un correctif amont d'aMule **3.0.0** ; sa
  configuration générée pointe `Ed2kServersUrl` et `KadNodesUrl` vers `https://upd.emule-security.org`
  (et active `ConnectToKad=1`).
- **⚠️ Cet amorçage dépend de l'egress au boot.** amuled doit pouvoir, dès le démarrage, faire du
  **DNS** et du **HTTPS sortant (443)** *à travers le VPN*. Si le VPN n'est pas encore monté, ou si
  l'egress est bloqué au moment du premier run, **rien ne s'amorce** et amuled reste sans serveurs ni
  nœuds. En cas de souci de connexion, vérifiez d'abord que gluetun est bien `up` et que la sortie
  Internet fonctionne avant amuled.

### Dépendance au pin de version d'amuled (ne pas dériver)

`compose.yaml` épingle **`ngosang/amule:3.0.0-1`** — c'est **volontaire et important**.

- **Ne jamais** remplacer par `latest` ni par une variante `2.3.3-*`.
- **Seules les versions ≥ 3.0.0** réalisent l'auto-amorçage de `server.met`/`nodes.dat` décrit
  ci-dessus (le correctif amont est arrivé en aMule 3.0.0).
- Dériver vers une image plus ancienne casse l'amorçage du premier run, sans message d'erreur
  évident.

---

## High-ID, Low-ID : à quoi s'attendre

Par défaut la stack tourne en **Low-ID**. C'est **un état normal, pas une panne** :

- En Low-ID, la recherche, le catalogage et le téléchargement **fonctionnent**.
- La joignabilité reste sous-optimale (moins de sources directes).

Ne traitez donc pas un statut « Low-ID » dans les logs comme une erreur à corriger.

### Activer le High-ID (port-sync, optionnel)

Une **boucle port-sync** sait obtenir un High-ID automatiquement : elle lit le port forwardé
vivant de gluetun, l'applique à amuled par EC (`SetPort`) puis **redémarre amuled** pour qu'il
écoute dessus (le port eD2k n'est pas re-bindable à chaud). Elle est **opt-in** et exige **trois
réglages solidaires** :

1. un **provider VPN à port forwarding** (Proton/PIA/PrivateVPN/PerfectPrivacy — cf. encadré plus
   haut), `VPN_PORT_FORWARDING: "on"` dans `compose.yaml` (déjà le cas) ;
2. le service **`docker-proxy`** (profil full) : un proxy à **surface minimale** qui n'autorise
   QUE `POST .../containers/amuled/restart` (le crawler ne voit jamais le socket Docker). Il monte
   le socket Docker hôte en lecture seule → renseignez `DOCKER_GID` dans `.env` (GID du groupe
   `docker` de l'hôte) ;
3. dans `config/local.yaml` : `gluetun_control_url: "http://gluetun:8000"` +
   `restarter_url: "http://docker-proxy:2375"`, et dans `config/crawler.yaml` la section
   `port_sync` (cadences de poll/rate-limit des restarts).

Les **trois** doivent être présents : si un seul manque, le crawler **refuse de démarrer**
(fail-fast). Absents → la boucle reste OFF et Low-ID est l'état normal. Une fois actif, surveillez
les events `port-sync` / `High-ID retrouvé` dans les logs et les métriques `emule_port_*`.

---

## Analyse antivirus (clamav) — provisioning de la base de signatures

En **mode full**, le verifier ajoute une 3ᵉ source de verdict : un scan **par signatures**
(`clamscan`) qui rend un fichier `malicious` sur match d'une base virale. C'est **activé par défaut
dans le profil full** (`ENABLED_CHECKS: type_sniff,ffprobe,clamav` dans `compose.yaml`).

**Comment la base arrive (sans casser l'isolement réseau du verifier).** Le verifier n'a **aucune
sortie Internet** (réseau `internal: true`) — il ne peut donc pas mettre à jour la base lui-même. Un
**sidecar `freshclam`** (service séparé sur le réseau `egress`) télécharge et tient à jour la base
dans un **volume partagé `clamav-db`** ; le verifier le **lit en lecture seule**. L'isolement du
verifier est préservé.

- Au démarrage en full, `freshclam` fait sa **première synchronisation** (~300–500 Mo) — cela prend
  quelques minutes. **Tant que la base n'est pas là, clamav rend `suspicious`** (défensif, jamais
  `clean` sans base), ce qui peut mettre des fichiers en attente d'un re-scan. C'est transitoire.
- L'image du verifier grossit de **~50–80 Mo** (le moteur `libclamav` + `clamscan` ; **pas** la base,
  qui vit dans le volume — c'est tout l'intérêt du sidecar).
- `clamscan` charge **toute la base en mémoire** : les rlimits du sous-processus d'analyse sont
  **relâchés** quand clamav est actif (≈1,5 Gio d'adressage, 120 s CPU — réglables via
  `RLIMIT_AS_BYTES_CLAMAV` / `RLIMIT_CPU_S_CLAMAV`), et le `mem_limit` du conteneur verifier est
  relevé à **2 Gio** en conséquence (sinon l'OOM-killer du cgroup tuerait le scan avant le rlimit).
  Si un fichier **sain** ressort systématiquement `suspicious`, le scan se fait probablement tuer :
  augmentez ces deux valeurs.

> **Désactiver clamav** : retirez `clamav` de `ENABLED_CHECKS` (le verifier retombe sur
> `type_sniff,ffprobe`) et, si vous voulez, ne lancez pas le sidecar. Le **smoke test** et le profil
> **observer** tournent déjà sans clamav.

---

## Durcissement optionnel (gVisor)

```bash
docker compose -f compose.yaml -f compose.hardening.yml --profile full up -d
```

Nécessite le runtime gVisor `runsc` enregistré sur l'hôte. **Sinon, ne chargez pas ce fichier** : la
base est déjà durcie (non-root, capabilities retirées, rootfs en lecture seule, et le verifier sans
aucune sortie Internet).

---

## Métriques Prometheus (optionnel)

Le crawler et le verifier exposent des métriques Prometheus.

- **crawler** — sur un port HTTP dédié (`observability.metrics.port` dans `config/crawler.yaml`),
  accessible depuis le réseau `ec`.
- **verifier** — sur son port de service (par défaut `8000`), route `/metrics`. Comme le verifier est
  sur un réseau **sans sortie Internet**, un Prometheus externe doit **rejoindre ce réseau** (ou vous
  exposez le port sur l'hôte).

Exemple de `scrape_config` :

```yaml
scrape_configs:
  - job_name: 'emule-indexer-crawler'
    static_configs:
      - targets: ['crawler:9090']   # port configurable
  - job_name: 'emule-indexer-verifier'
    static_configs:
      - targets: ['verifier:8000']  # même port que le service (/metrics)
```

---

## Ce qu'on peut ignorer (détails internes non nécessaires au déploiement)

Ces points sont vrais mais **n'exigent aucune action** pour un déploiement normal. Ils ne sont
documentés ici que pour référence si quelque chose cloche.

- **Construction des images en deux couches uv.** Le dépôt est un *workspace uv* (un seul `uv.lock`,
  deux paquets). Les Dockerfiles construisent en deux étapes (dépendances, puis code) pour le cache.
  Vous n'avez rien à faire : `docker compose build` s'en occupe.
- **Libs système.** Le verifier embarque `ffmpeg` (pour `ffprobe`) ; le crawler n'a aucune lib apt
  supplémentaire. Déjà géré dans les images.
- **Propriété des volumes `/data`.** Le crawler tourne en `user: 999` avec un rootfs en lecture
  seule. Les images **pré-créent** `/data/{catalog,local,quarantine}` en `nonroot` pour qu'un volume
  nommé **vide** hérite de la bonne propriété au premier montage. *À surveiller seulement* si vous
  montez un volume **déjà peuplé** (donc root-owned) : il faudrait alors le `chown` manuellement :
  ```bash
  docker run --rm -v emule-indexer_catalog-db:/d alpine chown -R 999:999 /d
  ```
- **User d'amuled.** `amuled` est une image **tierce** lancée avec **son propre user** ; on ne lui
  impose pas notre durcissement. Le volume `quarantine` est écrit à la fois par amuled (fichiers
  finis) et par le crawler (déplacement atomique) — un éventuel accroc de droits cross-user serait à
  surveiller au tout premier vrai téléchargement.
- **Entrypoint exec-form.** Les images ont un entrypoint `["python","-m","<pkg>"]`. Pour lancer une
  commande ponctuelle dans une image, passez par `--entrypoint` :
  ```bash
  docker run --rm --entrypoint python <image> -c "import re2, rapidfuzz; print('ok')"
  ```

---

## Outils annexes

- **Fusion de catalogues** : `uv run python -m emule_indexer.merge --output catalog-merged.db
  source-a.db source-b.db …` consolide N `catalog.db` (un par chercheur/campagne) en un seul,
  **idempotent** (re-merger est un no-op) et safe-by-default (pas d'écrasement sans `--force` ;
  `--into <source>` pour fusionner dans une source existante). Outil opérateur ponctuel.
- **Validation de config** : `uv run python -m emule_indexer validate-config` charge+valide les 4
  configs et sort en erreur (code ≠ 0) si l'une est invalide, **sans rien démarrer**. À lancer avant
  un déploiement.

Pour valider/tester en profondeur (suites d'intégration, smoke, CI), voir le
[guide des tests](testing-guide.md).

## Limites connues / follow-ups

- **clamav vs durcissement noyau** : clamav (signatures) et le filtre seccomp par-enfant sont
  **construits** (voir plus haut). Restent à venir, pour le ring noyau du sous-processus d'analyse :
  `net=none` (namespace réseau), bwrap/montages RO réels, tmpfs dédié — tous exigent `CAP_SYS_ADMIN`
  ou un user namespace, donc un changement de stratégie de confinement (le ring **container** —
  non-root, `cap_drop`, `read_only`, `internal: true`, gVisor opt-in — est déjà là).
- **port-sync — validation réelle** : la boucle est construite ; sa validation **bout-en-bout**
  (port-check High-ID réel derrière le VPN) se fait via un déploiement réel.
- **DV10 (download → quarantaine) — CONFIRMÉ par lecture de la source amont d'amuled**
  (cf. [`docs/reference/2026-06-17-amuled-completion-behavior.md`](reference/2026-06-17-amuled-completion-behavior.md)).
  À la complétion, amuled déplace le fichier vers son **IncomingDir** ; le statut ne passe complet
  qu'**après** le déplacement (pas de race). **Contraintes de déploiement à respecter :**
  1. `staging_dir` = `quarantine_dir` = l'**IncomingDir** d'amuled (le même volume `/data/quarantine`)
     — configurez l'`IncomingDir` d'amuled = ce dossier, **pas** son TempDir.
  2. Ce volume sur un **FS Linux normal** (ext4/overlay…), pas vfat/NTFS/HFS (sinon amuled assainit
     les caractères spéciaux du nom → divergence).
  3. **Pas de catégories** amuled (une catégorie avec son propre chemin redirigerait le fichier).
  4. Incoming **dédié** au crawler et vidé à chaque cycle → évite les collisions de nom.

  *Limite connue (acceptée)* : si l'Incoming contient déjà un fichier du même nom, amuled écrit
  `nom(0).ext` et notre promotion échoue en boucle (signalée par la métrique `PromotionFailed`, donc
  non silencieuse). *(La suite e2e « transfert réel » qui aurait synthétisé ce transfert a été
  abandonnée — voir le guide des tests.)*
- **WebUI / hub central / rétention** : non planifiés à ce stade.
