# Runbook de déploiement — emule-indexer

Ce guide explique comment **choisir son scénario de déploiement et lancer la stack** en quelques
étapes. Le sujet du catalogue reste **le fichier, jamais la personne**.

> **À qui ça s'adresse.** Ce guide suppose que vous pouvez **ouvrir un terminal et lancer des
> commandes Docker** (`docker compose version` doit afficher une version). Si Docker est nouveau pour
> vous, le tutoriel officiel [Docker get-started](https://docs.docker.com/get-started/) suffit (≈ 1 h)
> avant de revenir ici. L'état par défaut (**Low-ID**) ne demande aucun réglage réseau avancé.

> Une fois le nœud monté : pour l'**exploiter et le régler** (cycle de vie, High-ID, analyse
> antivirus, métriques, durcissement gVisor, outils de catalogue), voir le
> **[Runbook d'administration](runbook-administration.md)** ; en cas de souci, le
> **[Runbook de dépannage](runbook-troubleshooting.md)**.

---

## Glossaire (sigles utilisés ici)

| Terme | Signification |
|-------|---------------|
| **VPN** | Tunnel chiffré qui masque l'IP de la machine. Ici assuré par le conteneur **gluetun**. |
| **eD2k** | Le réseau eDonkey2000 (serveurs centralisés). L'un des deux réseaux que surveille le projet. |
| **Kad** | Réseau Kademlia (décentralisé, sans serveur). Le second réseau surveillé. |
| **EC** | *External Connection* — le protocole par lequel le crawler pilote le client aMule (`amuled`). |
| **Low-ID / High-ID** | Statut de joignabilité sur eD2k. **High-ID** = la machine est joignable depuis l'extérieur (meilleures sources). **Low-ID** = elle ne l'est pas (fonctionne quand même, mais sous-optimal). |
| **quarantine** | Dossier isolé où atterrissent les fichiers téléchargés, **avant** vérification. |
| **GHCR** | GitHub Container Registry — l'endroit où sont publiées les images Docker du projet. |

---

## Prérequis matériels minima

Ordres de grandeur **indicatifs** (non validés en prod — à confirmer au premier déploiement homelab) :

- **RAM** : ≥ 2 Go sans antivirus, ≥ 4 Go avec **clamav activé** (l'antivirus charge en mémoire sa
  base de signatures, ~1,5 Gio). En dessous, le système peut s'arrêter brutalement par manque de
  mémoire pendant l'analyse d'un fichier.
- **Disque** : ≥ 10 Go pour le système + le catalogue (qui grossit lentement, ~Mo/jour). En **mode
  download**, prévoir aussi la place des fichiers téléchargés (variable selon votre cible).
- **CPU** : x86_64 ou ARM64 (les images sont publiées multi-arch).
- **Réseau** : connexion permanente. Trafic typique : Mo/h en mode observer, plus en mode download.

Sur Raspberry Pi, NAS bas de gamme ou VPS micro : vérifiez la RAM avant de lancer. Un déploiement en
manque de mémoire échoue **sans message clair** : le service de vérification s'arrête tout seul, et
les fichiers ressortent étiquetés `suspicious` même quand ils sont sains.

---

## 1. Choisir sa stack — matrice fonctionnalités × scénario

Repérez la ligne qui correspond à **vos contraintes** (colonnes) et à **votre intention** (vie privée /
joignabilité) :

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

Mapping stack → fichier de point d'entrée :

| Stack | Fichier `examples/` | High-ID via |
|---|---|---|
| A | `gluetun.yaml` | — (Low-ID) |
| B | `gluetun.yaml` (+ port-sync armé) | port-sync auto (VPN PF) |
| C | `sans-vpn-lowid.yaml` | — (Low-ID) |
| D | `sans-vpn-highid.yaml` | port statique redirigé |

---

## 2. Prérequis par stack

Les prérequis *contraignants* figurent déjà comme colonnes dans la matrice. Détails complets :

- **A** : abonnement VPN WireGuard (n'importe lequel supporté par gluetun) → clé privée dans `.env` ;
  `/dev/net/tun` disponible sur l'hôte (fourni aussi par Docker Desktop). Aucun port à ouvrir.
- **B** : **hôte Linux Docker rootful** + VPN **avec port forwarding** (ProtonVPN, PIA, PrivateVPN,
  PerfectPrivacy) → clé dans `.env` ; armer le bloc `port_sync` dans
  `config/crawler/download.yaml` (voir étape 3).
- **C** : rien de spécial, le plus simple. ⚠ ton IP domestique est exposée aux pairs (mets un VPN au
  niveau hôte pour l'éviter).
- **D** : rediriger `LISTEN_PORT` (TCP + UDP) sur ta box vers cette machine + autoriser au pare-feu
  (Windows compris) ; régler `LISTEN_PORT` dans `.env`. ⚠ IP domestique exposée.
- **Toutes** : Docker + compose v2 (vérifier : `docker compose version`).

---

## 3. Pas-à-pas (cross-platform — Linux, macOS, Windows)

Les commandes `docker compose …` sont **identiques** sur Linux / macOS / Windows. Seule la copie de
fichiers diffère (`cp` Unix vs `Copy-Item` PowerShell). Sous Windows, un shell **WSL2** rend les
commandes Unix identiques — aucune autre étape n'utilise `chmod`/`chown`/chemins absolus Unix (le
durcissement `user: 999`/`read_only` est porté par compose).

### Étape 1 — Prérequis

Selon la stack choisie (§2) :

- Docker + compose v2 (toutes).
- `/dev/net/tun` disponible sur l'hôte (A, B).
- Port redirigé sur la box + pare-feu ouvert (D).
- Runtime `runsc` enregistré sur l'hôte, si gVisor (Linux uniquement — voir § Options orthogonales).

### Étape 2 — Secrets

Copiez le modèle et renseignez vos secrets :

```bash
# Linux / macOS / WSL2 :
cp .env.example .env
# PowerShell :
Copy-Item .env.example .env
```

Renseignez dans `.env` selon votre stack :

- `AMULE_EC_PASSWORD` — **toujours** ; un mot de passe que vous choisissez (protège le canal EC).
- `WIREGUARD_PRIVATE_KEY` — stack A ou B (clé privée WireGuard de votre fournisseur).
- `SERVER_COUNTRIES` — stack A ou B (pays de sortie VPN, ex. `Switzerland`).
- `LISTEN_PORT` — stack D (port d'écoute publié, défaut `4662`).
- `GRAFANA_PWD` — si vous activez le monitoring.

Le `.env` est **gitignoré** : il ne sera jamais committé.

### Étape 3 — Config crawler

Copiez le modèle correspondant à votre mode et renseignez le mot de passe EC :

```bash
# Linux / macOS / WSL2 — exemple pour le mode observer :
cp config/crawler/observer.example.yaml config/crawler/observer.yaml
# PowerShell :
Copy-Item config/crawler/observer.example.yaml config/crawler/observer.yaml
```

De même pour le mode download :

```bash
# Linux / macOS / WSL2 :
cp config/crawler/download.example.yaml config/crawler/download.yaml
# PowerShell :
Copy-Item config/crawler/download.example.yaml config/crawler/download.yaml
```

Dans le fichier copié, renseignez `amules[].password` avec la valeur de `AMULE_EC_PASSWORD`.

> **Stack B uniquement — activer le port-sync.** Dans `config/crawler/download.yaml`, décommentez le
> bloc `port_sync` (champs `gluetun_control_url` et `restarter_url`) pour armer la boucle High-ID
> automatique. Sans cela, la stack B reste en Low-ID (état inoffensif).

> #### Contraintes du mode download (à respecter pour que la chaîne fonctionne)
> Quatre conditions pour que téléchargement → quarantaine → vérification fonctionne :
>
> 1. `staging_dir` = `quarantine_dir` = l'**IncomingDir d'amuled** (le même volume `/data/quarantine`)
>    — configurez l'IncomingDir d'amuled sur ce dossier, **pas** son TempDir.
> 2. Ce volume sur un **FS Linux** (ext4/overlay…), pas vfat/NTFS/HFS.
> 3. **Pas de catégories** amuled (une catégorie redirigerait le fichier).
> 4. amuled **dédié** au crawler, **jeu partagé restreint** (ne pointez pas une grosse bibliothèque
>    partagée pré-existante).

### Étape 4 — Tirer les images

```bash
docker compose -f examples/<fichier> --profile <observer|download> pull
```

Ou construire localement :

```bash
docker compose -f examples/<fichier> --profile <observer|download> build
```

### Étape 5 — Lancer

```bash
docker compose -f examples/<fichier> --profile <observer|download> [--profile monitoring] up -d
```

Exemples concrets :

```bash
# Stack C, mode observer (le plus simple) :
docker compose -f examples/sans-vpn-lowid.yaml --profile observer up -d

# Stack A, mode download + monitoring :
docker compose -f examples/gluetun.yaml --profile download --profile monitoring up -d

# Stack D, mode download, avec gVisor (Linux + runsc) :
CONTAINER_RUNTIME=runsc docker compose -f examples/sans-vpn-highid.yaml --profile download up -d
```

> En mode download, le crawler **vérifie que le verifier répond** au démarrage et **refuse de
> démarrer** s'il est injoignable. Son `restart: unless-stopped` le relance jusqu'à ce que le
> verifier soit sain — c'est le comportement normal, pas une erreur.

> En mode download, l'**analyse antivirus (clamav)** est active par défaut. Au premier démarrage, sa
> base de signatures se synchronise (quelques minutes) ; les fichiers ressortent `suspicious` en
> attendant — c'est **transitoire**. Détails dans le [runbook d'administration](runbook-administration.md).

### Étape 6 — Vérifier

```bash
docker compose -f examples/<fichier> ps       # état des conteneurs
docker compose -f examples/<fichier> logs -f crawler   # logs du crawler
```

Si vous avez activé le monitoring : Grafana sur `http://<hôte>:${GRAFANA_PORT}` (défaut `3000`),
identifiants `admin` / valeur de `GRAFANA_PWD`.

---

## Options orthogonales (toutes stacks)

Ces trois options sont **indépendantes de la stack** choisie ; elles ne figurent pas comme colonnes
de la matrice car elles seraient cochées pour toutes :

| Option | Mécanisme | Contrainte |
|---|---|---|
| **Mode** | `--profile observer` (crawl seul) ou `--profile download` (+ téléchargement + vérif) | aucune |
| **Monitoring** | `--profile monitoring` (Prometheus + Grafana clé en main) | ajouter `GRAFANA_PWD` dans `.env` |
| **gVisor** | `CONTAINER_RUNTIME=runsc` (prefixer la commande `docker compose`) | Linux uniquement + `runsc` enregistré sur l'hôte |

---

## Premier démarrage : amorçage automatique du réseau

Au **tout premier run**, amuled récupère **automatiquement** sa liste de serveurs eD2k (`server.met`)
et de nœuds Kad (`nodes.dat`) pour se connecter — patientez quelques instants après le démarrage,
vous n'avez rien à faire. *(Si amuled ne se connecte à aucun réseau, voir le
[runbook de dépannage](runbook-troubleshooting.md).)*

---

## Low-ID : c'est normal

Par défaut la stack tourne en **Low-ID**, et **ce n'est pas une panne** :

- La recherche, le catalogage et le téléchargement **fonctionnent**.
- Seule la joignabilité est sous-optimale (moins de sources directes).

Ne traitez donc pas un statut « Low-ID » dans les logs comme une erreur à corriger.

Le **High-ID** (joignabilité optimale, plus de sources) est une optimisation **optionnelle** — son
activation, ses prérequis et ses compromis (y compris l'ouverture d'un port et ses risques) sont
décrits dans le [runbook d'administration](runbook-administration.md).

---

## Pour aller plus loin

- **[Runbook d'administration](runbook-administration.md)** — exploiter et régler un nœud monté :
  cycle de vie (arrêt/mise à jour/persistance), High-ID (optionnel), analyse antivirus (clamav),
  métriques Prometheus, durcissement gVisor, outils de catalogue, limites connues.
- **[Runbook de dépannage](runbook-troubleshooting.md)** — symptômes courants et résolutions (amuled
  ne se connecte pas, fichier sain en `suspicious`, port-sync inopérant, droits de volume…).
- **[Guide des tests](testing-guide.md)** — valider/tester en profondeur (suites d'intégration,
  smoke, CI).
