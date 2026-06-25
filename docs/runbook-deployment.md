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

> **Les stacks A et B exigent un abonnement VPN commercial** supportant **WireGuard** (≈ 2 à 5 €/mois
> typiquement). Voir la [liste à jour des fournisseurs supportés par gluetun](https://github.com/qdm12/gluetun-wiki/tree/main/setup/providers)
> pour choisir. Les VPN gratuits (Mullvad free, Windscribe free) ne sont pas vérifiés par gluetun —
> ils peuvent fonctionner ou non. La stack **B** exige en plus un fournisseur avec **port forwarding**
> (vérifiez ce point chez le fournisseur avant de payer ; tous ne l'offrent pas).

Repérez la ligne qui correspond à **vos contraintes** (colonnes) et à **votre intention** (vie privée /
joignabilité) :

| Stack | Expose son IP domestique | Ouvrir un port sur sa box | Rend High-ID possible | Compatible Docker Desktop (Win/macOS) | Demande un VPN commercial | VPN avec port forwarding |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| **A** · gluetun, Low-ID | Non | Non | Non | Oui | **Oui** | Non |
| **B** · gluetun, High-ID | Non | Non | **Oui** | **Non** | **Oui** | **Oui** |
| **C** · sans VPN, Low-ID | **Oui** | Non | Non | Oui | Non | Non |
| **D** · sans VPN, High-ID | **Oui** | **Oui** | **Oui** | Oui | Non | Non |

Cellules non triviales :

- **Stack B — comment le High-ID est obtenu** : le crawler demande au VPN le port forwardé puis le
  pousse à amuled (« port-sync »). Cette boucle exige **trois pièces qui marchent ensemble** : (1) un
  VPN avec port forwarding **activé** dans `.env`, (2) un service `docker-proxy` qui lit le socket
  Docker, (3) le `port_sync` armé dans `config/crawler/download.yaml` (étape 3). Si **une seule**
  manque, le crawler reste en Low-ID sans erreur visible.
- **Stack B incompatible Docker Desktop** (Win/macOS) : le port-sync a besoin d'un accès direct au
  socket Docker que Docker Desktop n'expose pas correctement. Voir
  `docs/reference/2026-06-17-docker-desktop-rootless-socket.md` (état observé en juin 2026, à
  re-vérifier si Docker Desktop évolue).
- **« Expose IP domestique »** : Non pour A/B (sortie par le VPN), Oui pour C/D (sortie par la
  connexion perso, sauf VPN installé directement sur le système d'exploitation hôte).

Mapping stack → fichier de point d'entrée :

| Stack | Fichier `examples/` | High-ID via |
|---|---|---|
| A | `gluetun.yaml` | — (Low-ID) |
| B | `gluetun.yaml` (+ port-sync armé) | port-sync auto (VPN PF) |
| C | `sans-vpn-lowid.yaml` | — (Low-ID) |
| D | `sans-vpn-highid.yaml` | port statique redirigé |

---

> **⚠️ Mode download — 4 contraintes strictes à respecter** (sous peine d'échec silencieux de la
> chaîne téléchargement → quarantaine → vérification) : volume partagé crawler/amuled, FS Linux,
> pas de catégories amuled, jeu partagé restreint. **Détail et rationale complets dans la référence
> [Complétion d'un download côté amuled](reference/2026-06-17-amuled-completion-behavior.md)
> § Contraintes de déploiement.** Lisez-la avant de remplir `config/crawler/download.yaml`
> (étape 3) si vous montez une stack en mode download.

---

## 2. Prérequis par stack

Les prérequis *contraignants* figurent déjà comme colonnes dans la matrice. Détails complets :

- **A** : abonnement VPN WireGuard (n'importe lequel supporté par gluetun) → clé privée dans `.env` ;
  `/dev/net/tun` disponible sur l'hôte (fourni aussi par Docker Desktop). Aucun port à ouvrir.
- **B** : **hôte Linux Docker rootful** + VPN **avec port forwarding** (cherchez les fournisseurs
  marqués `PORT_FORWARDING: yes` dans la [liste gluetun](https://github.com/qdm12/gluetun-wiki/tree/main/setup/providers)
  — en juin 2026, ProtonVPN, PIA, PrivateVPN et PerfectPrivacy sont éligibles) → clé dans `.env` ;
  armer le bloc `port_sync` dans `config/crawler/download.yaml` (voir étape 3).
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

Selon la stack choisie (§2), les prérequis ci-dessous doivent être réunis **avant** d'éditer le
`.env` (étape 2).

**Toutes stacks :**
- Docker + compose v2 (vérifier : `docker compose version`).
- Une machine qui correspond aux [prérequis matériels minima](#prérequis-matériels-minima) ci-dessus.

**Stacks A et B (avec VPN) :**
- Un **abonnement VPN WireGuard** chez un fournisseur supporté par gluetun → vous aurez besoin de la
  **clé privée WireGuard** que votre fournisseur expose dans son espace client (étape 2).
- Le périphérique virtuel **`/dev/net/tun`** disponible sur l'hôte (c'est un fichier spécial Linux
  utilisé par les VPN ; présent par défaut sur la plupart des distributions et fourni aussi par
  Docker Desktop ; vérifier : `ls -la /dev/net/tun`).
- **Stack B uniquement** : fournisseur VPN avec **port forwarding** activable (déjà cadré §1) + le
  groupe Unix `docker` doit exister sur l'hôte (vérifier : `getent group docker` retourne une ligne).

**Stack D (sans VPN, High-ID statique) :**
- Un **port redirigé sur votre box** (TCP + UDP, par défaut 4662) vers cette machine et autorisé au
  pare-feu de l'hôte (Windows compris). « Redirigé » = règle de NAT/port forwarding dans l'interface
  d'admin de votre box ; demandez à votre fournisseur d'accès si vous ne l'avez jamais fait.

**Optionnel — gVisor (sandbox de noyau) :**
- Le runtime conteneur `runsc` enregistré sur l'hôte (**Linux uniquement** — gVisor n'existe pas sur
  Docker Desktop). Voir § Options orthogonales.

**Vérification rapide avant de continuer :**

```bash
docker compose version                       # toutes stacks : doit afficher v2.x
ls -la /dev/net/tun                          # stacks A/B : doit exister
getent group docker                          # stack B : doit retourner une ligne
docker info | grep -i runtime                # gVisor optionnel : `runsc` doit apparaître
```

Si une commande échoue, traitez-la avant de passer à l'étape 2.

### Étape 2 — Secrets

Copiez le modèle et renseignez vos secrets :

```bash
# === Linux / macOS / WSL2 (bash/zsh) ===
cp .env.example .env
```

```powershell
# === Windows (PowerShell) ===
Copy-Item .env.example .env
```

#### Remplir le `.env`

Le `.env.example` contient des placeholders `change-me` — **ce ne sont pas des valeurs par défaut
sûres**, ce sont des marqueurs « à remplir ». Une valeur `change-me` laissée telle quelle ne
provoque pas d'erreur au lancement mais causera un **échec silencieux plus tard** (le crawler ne
pourra pas s'authentifier au démon amuled, et vous le verrez seulement dans les logs).

Variables à renseigner, par stack :

| Variable | Stacks | Quoi | Où la trouver |
|---|---|---|---|
| `AMULE_EC_PASSWORD` | Toutes | Mot de passe que **vous choisissez** (texte libre, ≥ 12 caractères recommandés). Il protège le canal interne entre le crawler et amuled. | Vous l'inventez. Notez-le, vous le re-saisirez à l'étape 3. |
| `WIREGUARD_PRIVATE_KEY` | A, B | Clé privée WireGuard (chaîne base64 ≈ 44 caractères). | Espace client de votre fournisseur VPN, section WireGuard / Configuration. Chaque fournisseur a sa propre UI — voir la [wiki gluetun](https://github.com/qdm12/gluetun-wiki/tree/main/setup/providers) pour les liens par fournisseur. |
| `SERVER_COUNTRIES` | A, B | Pays de sortie VPN, **nom complet en anglais** (`Switzerland`, `France`, `Germany`). Pas le code ISO. | Liste des pays supportés par votre fournisseur. |
| `VPN_SERVICE_PROVIDER` | A, B | Nom du fournisseur tel qu'attendu par gluetun (`protonvpn`, `pia`, `privatevpn`, `perfectprivacy`, ...). | [Liste gluetun](https://github.com/qdm12/gluetun-wiki/tree/main/setup/providers). |
| `VPN_PORT_FORWARDING` | B | `on` (active la boucle port-sync) ou `off` (Low-ID). | Vous décidez. `off` = Low-ID, suffisant pour cataloguer. |
| `DOCKER_GID` | B | GID numérique du groupe Unix `docker` sur **l'hôte**, utilisé par le service `docker-proxy` pour accéder au socket Docker. | `getent group docker \| cut -d: -f3` |
| `LISTEN_PORT` / `LISTEN_PORT_UDP` | D | Port que vous avez redirigé sur votre box (défaut `4662` TCP + `4672` UDP). | Identique à la redirection NAT de votre box (étape 1). |
| `GRAFANA_PWD` | Toutes, si `--profile monitoring` | Mot de passe que **vous choisissez** pour le compte `admin` de Grafana. | Vous l'inventez. |

Le `.env` est **gitignoré** — il ne sera jamais committé. Gardez-en une copie hors du repo en cas de
perte (la `WIREGUARD_PRIVATE_KEY` notamment, qui est re-générable mais nécessite de revoir votre VPN).

#### Checklist avant de lancer

- [ ] Toutes les variables requises par votre stack sont renseignées (cf. tableau).
- [ ] Aucune valeur ne vaut encore `change-me`.
- [ ] Les commandes de vérification de l'étape 1 passent toutes.

Si l'une de ces lignes n'est pas cochée, ne lancez pas — vous gagnerez du temps à corriger maintenant
plutôt qu'à débugger un échec silencieux.

### Étape 3 — Config crawler

Le dossier `config/crawler/` contient des **fichiers modèles** suffixés `.example.yaml`. Copiez
celui qui correspond à votre mode (observer ou download) sous le nom attendu (sans `.example`) :

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

Dans le fichier copié, renseignez `amules[].password` avec **la même valeur** que
`AMULE_EC_PASSWORD` du `.env` (étape 2). **Pourquoi** : ce mot de passe protège le canal interne
EC ; sans correspondance entre le `.env` (amuled) et `config/crawler/download.yaml` (crawler),
amuled refuse la connexion EC et le crawler ne démarre pas.

> **Stack B uniquement — activer le port-sync.** Dans `config/crawler/download.yaml`, décommentez le
> bloc `port_sync` (champs `gluetun_control_url` et `restarter_url`) pour armer la boucle High-ID
> automatique. Sans cela, la stack B reste en Low-ID (état inoffensif).

> **Mode download — vérifiez les 4 contraintes** signalées avant la §2. Elles vivent dans
> [`reference/2026-06-17-amuled-completion-behavior.md` § Contraintes de déploiement](reference/2026-06-17-amuled-completion-behavior.md#contraintes-de-déploiement-résumé),
> qui explique aussi le **pourquoi** de chacune (assainissement de nom, dédup par collision,
> détection par fichiers partagés). Ne les négligez pas : le mode download échoue **sans message
> clair** si l'une est cassée (les fichiers restent dans l'IncomingDir d'amuled sans être promus).

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
```

```bash
# Stack D, mode download, avec gVisor — ⚠️ Linux uniquement (runsc enregistré sur l'hôte) :
CONTAINER_RUNTIME=runsc docker compose -f examples/sans-vpn-highid.yaml --profile download up -d
```

> ⚠️ **`CONTAINER_RUNTIME=runsc` ne fonctionne que sur Linux** avec gVisor installé. Sur macOS ou
> Windows (Docker Desktop), la commande échoue avec « unknown runtime: runsc ». Si vous n'avez pas
> gVisor, retirez simplement le préfixe `CONTAINER_RUNTIME=runsc` — la stack tourne en `runc`
> (runtime conteneur Docker standard) sans changer de comportement fonctionnel.

#### Premier boot : ce qui est normal (et ce qui ne l'est pas)

Au tout premier démarrage en mode download, **deux comportements inattendus** sont en réalité
nominaux. Ne les traitez pas comme des pannes :

1. **Le crawler redémarre en boucle pendant 1-2 minutes.** En mode download, il vérifie au
   démarrage que le service de vérification (verifier) répond, et **refuse de démarrer** sinon. Son
   `restart: unless-stopped` le relance jusqu'à ce que le verifier soit sain. C'est attendu :
   regardez les logs du verifier (`docker compose logs verifier`), il démarre en quelques dizaines
   de secondes, puis le crawler s'accroche.
2. **Les fichiers ressortent `suspicious` pendant 5 à 20 minutes.** L'analyse antivirus (clamav)
   est active par défaut. Au premier démarrage, clamav télécharge sa **base de signatures**
   (~300-500 Mo, durée dépendante de votre connexion : 5 min en fibre, 20+ min en 4G/ADSL). Tant
   que la base n'est pas chargée, clamav refuse de dire « sain » par défaut défensif → tous les
   fichiers atterrissent en `suspicious`. C'est **transitoire** : une fois `freshclam: […]
   updated ok` visible dans les logs (`docker compose logs verifier | grep freshclam`), les
   prochains fichiers seront verdictés normalement. Détails dans le
   [runbook d'administration](runbook-administration.md).

**Signes que le premier boot s'est bien passé :**

```bash
docker compose -f examples/<fichier> ps         # tous les services en "Up" (pas "Restarting")
docker compose logs amuled | head -50           # voit "Connecting to" puis "Connected to" un serveur
docker compose logs crawler | grep -iE "cycle|search"   # le crawler annonce au moins un cycle
```

Comptez **3 à 5 minutes** avant la première trace d'activité catalogage. Le crawler peut sembler
silencieux au début — c'est normal.

#### En cas d'erreur au déploiement

Si une étape échoue, regardez les logs **avant** de relancer :

| Symptôme | Cause probable | Quoi vérifier |
|---|---|---|
| `docker compose pull` échoue | Image absente / réseau / mauvaise version | Vérifiez votre connexion Internet + que `IMAGE_TAG` (dans `.env`) existe sur GHCR. |
| `docker compose up -d` retourne immédiatement avec un service `Exited (1)` | Erreur de config (mauvais `.env`, mauvais `config/crawler/*.yaml`) | `docker compose logs <service-en-erreur>` (souvent : variable manquante, mot de passe `change-me` oublié, chemin invalide). |
| Le verifier ne démarre jamais | RAM insuffisante avec clamav, ou base clamav non téléchargée | Vérifiez la RAM dispo (`docker stats`) ; si < 2 Go, désactivez clamav (voir runbook administration). |
| Le crawler boucle sur "verifier unreachable" pendant > 5 minutes | Le verifier est down OU `verifier_url` mal configuré dans `config/crawler/download.yaml` | `docker compose logs verifier` (doit montrer un `Listening on :8000`) + comparer avec `verifier_url` côté crawler. |
| Port déjà utilisé (`bind: address already in use`) | Un autre service (autre crawler, autre Grafana) utilise le port | Modifiez `LISTEN_PORT` ou `GRAFANA_PORT` dans `.env`. |

Pour les pannes **après** déploiement (amuled sans serveurs, fichier sain en `suspicious` une fois
la base clamav prête, port-sync inopérant, etc.), voir le
[runbook de dépannage](runbook-troubleshooting.md).

### Étape 6 — Vérifier

```bash
docker compose -f examples/<fichier> ps       # état des conteneurs
docker compose -f examples/<fichier> logs -f crawler   # logs du crawler
```

Voir aussi la sous-section [« Premier boot : ce qui est normal »](#premier-boot--ce-qui-est-normal-et-ce-qui-ne-lest-pas)
ci-dessus pour les signes de succès attendus et les durées typiques.

#### Grafana (si monitoring activé)

Si vous avez activé le monitoring : Grafana sur `http://<hôte>:${GRAFANA_PORT}` (défaut `3000`),
identifiants `admin` / valeur de `GRAFANA_PWD`.

> ⚠️ **Ne pas exposer Grafana directement sur Internet.** Le dashboard donne accès au catalogue
> (noms de fichiers, sources, traces réseau) — données sensibles pour un opérateur. Par défaut
> Grafana publie sur **toutes les interfaces** de l'hôte. En usage homelab (réseau local), restez
> sur `http://<hôte-LAN>:3000`. Pour un accès distant : passer par un **reverse-proxy** (nginx,
> Traefik, Caddy) avec **TLS** et authentification supplémentaire, ou par un **VPN d'accès** type
> WireGuard/Tailscale. Ne publiez jamais le port 3000 nu sur Internet.

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
et de nœuds Kad (`nodes.dat`) pour se connecter — comptez **1 à 3 minutes** après le démarrage,
vous n'avez rien à faire. *(Si après 5 minutes amuled ne se connecte à aucun réseau, voir le
[runbook de dépannage](runbook-troubleshooting.md).)*

En mode download, voir aussi la sous-section [« Premier boot : ce qui est normal »](#premier-boot--ce-qui-est-normal-et-ce-qui-ne-lest-pas)
de l'étape 5 pour les deux comportements transitoires (crawler qui redémarre, clamav qui synchronise).

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
