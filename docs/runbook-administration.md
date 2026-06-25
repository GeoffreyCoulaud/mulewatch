# Runbook d'administration — emule-indexer

Ce guide s'adresse à qui **exploite et règle** un nœud déjà monté. Pour *monter* la stack, commencez
par le **[Runbook de déploiement](runbook-deployment.md)** ; pour résoudre un problème concret, le
**[Runbook de dépannage](runbook-troubleshooting.md)**. On trouve ici le cycle de vie du nœud, le
High-ID (optionnel), l'analyse antivirus, les métriques, le durcissement noyau, les outils de
catalogue et les limites connues. Le sujet du catalogue reste **le fichier, jamais la personne**.

---

## Cycle de vie & données

- **Persistance.** Le catalogue et l'état vivent dans des **volumes Docker nommés** (`catalog-db`,
  `local-db`, `quarantine`, `amule-state`, et `clamav-db` en mode download). Ils **persistent** à la
  recréation des conteneurs — ne lancez `docker compose down` **avec `-v`** que si vous voulez
  réellement **effacer** le catalogue.
- **Arrêter le nœud** : `docker compose -f examples/<fichier> --profile <observer|download> down`.
- **Mettre à jour** : re-tirez les images puis relancez :
  ```bash
  docker compose -f examples/<fichier> --profile download pull
  docker compose -f examples/<fichier> --profile download up -d
  ```
- **Redémarrage de la machine hôte.** Les conteneurs ont `restart: unless-stopped` — ils reviennent
  seuls au boot de l'hôte (Docker doit démarrer en service système). **Aucune commande à relancer.**
  Vérifiez après reboot : `docker compose -f examples/<fichier> ps`. Si un service est en `Exited`
  alors que les autres sont `Up`, voir « Diagnostic après panne » ci-dessous.

### Diagnostic après panne

Si le nœud tourne mais ne semble plus catalogue / télécharge plus rien :

| Symptôme | Premier check | Action |
|---|---|---|
| Le crawler tourne mais aucune nouvelle observation depuis > 1 h | `docker compose logs crawler --tail 100` | Cherchez « EC unavailable », « no servers » ou « cycle » récent. Si pas de cycle, amuled est probablement déconnecté du réseau (voir [runbook-troubleshooting](runbook-troubleshooting.md)). |
| Téléchargements bloqués en QUEUED | `docker compose logs crawler \| grep -i download` | Vérifier que amuled est en High-ID **ou** qu'il a des sources (sources directes nécessaires en Low-ID). |
| Tous les fichiers ressortent `suspicious` | `docker compose logs verifier --tail 100` | Voir clamav rlimits ci-dessous — probable manque de RAM pour le scan. |
| Le verifier crash périodiquement (logs `Killed`) | `docker stats verifier` | RAM insuffisante. Augmentez `mem_limit` ou désactivez clamav. |
| Un volume Docker se remplit | `docker system df -v` | Catalogue trop gros (voir Compaction) ou quarantaine accumulée. |

Pour les symptômes inconnus, voir le [runbook de dépannage](runbook-troubleshooting.md).

### Planification disque

Ordres de grandeur **indicatifs** (à ajuster selon votre trafic eMule réel et la cardinalité de
vos cibles) :

- **`catalog-db`** : croissance lente, **~1 à 6 Go/an** sans compaction (chiffre estimé sur le trafic
  eMule 2026 ; ré-évaluer si vous activez un grand nombre de cibles). La compaction (cf. Outils de
  catalogue) ramène l'historique au-delà de 90 jours à un rollup journalier — taux de compression
  élevé.
- **`quarantine`** : taille des fichiers en cours de vérification (transitoire) + ceux remis à
  l'opérateur (variable, dépend de votre politique de purge).
- **`clamav-db`** : ~300-500 Mo (base de signatures, mise à jour quotidienne).
- **`amule-state`** : qq Mo (server.met, nodes.dat, prefs).

Si votre VPS / NAS approche de saturation, lancez `docker system df -v` pour identifier le volume
fautif, puis `python -m emule_indexer.compact` (cf. Outils de catalogue) ou purgez la quarantaine.

---

## High-ID (optionnel) — devenir joignable

Par défaut, un nœud est en **Low-ID** : il fonctionne très bien ainsi (recherche, catalogage,
téléchargement), il est juste sous-optimal côté sources. Le **High-ID** rend la machine **joignable**
depuis l'extérieur (plus de sources directes) ; c'est **facultatif**. Pour être joignable, il faut
qu'un **port entrant** atteigne amuled — deux routes, selon que vous gardez ou non le VPN devant le
trafic P2P.

### Route A (recommandée) — derrière le VPN, via port forwarding

gluetun sait demander un **port forwarding** à votre fournisseur VPN : le port joignable est celui du
VPN, **tout le trafic reste derrière le tunnel**. gluetun ne l'implémente que pour **4 fournisseurs** :
**ProtonVPN, PIA, PrivateVPN, PerfectPrivacy**. Une **boucle port-sync** lit ce port forwardé et
l'applique à amuled automatiquement, puis redémarre amuled pour qu'il écoute dessus.

Elle est **opt-in** et exige **trois réglages solidaires** :

1. un fournisseur à port forwarding (ci-dessus) + `VPN_PORT_FORWARDING: "on"` dans `.env` ;
2. le service **`docker-proxy`** (profil download, stack B / `examples/gluetun.yaml`), qui redémarre
   amuled de façon confinée (le crawler ne voit jamais le socket Docker) : renseignez `DOCKER_GID`
   dans `.env` (GID du groupe `docker` de l'hôte, via `getent group docker`). **Exige un Docker
   rootful natif** ; **Docker Desktop et le mode rootless ne fonctionnent pas** tels quels (voir le
   [runbook de dépannage](runbook-troubleshooting.md) si le port-sync reste inopérant) ;
3. dans `config/crawler/download.yaml` : décommentez le bloc `port_sync` (champs
   `gluetun_control_url: "http://gluetun:8000"` et `restarter_url: "http://docker-proxy:2375"`) ; la
   section `port_sync` de `config/crawler/crawler.yaml` contient les valeurs par défaut (réglage fin
   optionnel).

Les **trois** doivent être présents : si un seul manque, le crawler **refuse de démarrer**
(fail-fast). Absents → la boucle reste OFF et Low-ID est l'état normal. Une fois actif, surveillez
les events `port-sync` / `High-ID retrouvé` dans les logs et les métriques `emule_port_*`.

### Route B — ouvrir un port vous-même

Si votre fournisseur ne fait pas de port forwarding, le High-ID reste atteignable en
**ouvrant/redirigeant un port** sur votre box/routeur vers le nœud, pour que les pairs joignent amuled
directement. C'est une option **parfaitement viable** ; le choix relève surtout de votre **tolérance
au risque**.

> #### À savoir
> - **Légalité.** Partager une œuvre sous droit d'auteur est illégal — c'est vrai dès qu'on fait
>   tourner un nœud, route B ou non. Ce qui distingue cet usage, ce sont les **circonstances** : eMule
>   est, en 2026, un réseau de niche largement désuet, et la cible — des **médias perdus**,
>   introuvables ailleurs et aux ayants droit inactifs ou introuvables — ne mobilise, en pratique,
>   personne. Le risque concret reste donc **très faible**.
> - **Surface d'attaque.** Un port entrant ouvert, c'est un point d'entrée de plus sur votre réseau
>   domestique : redirigez **précisément** ce port (pas une plage) et gardez la machine à jour.
>
> La **route A** garde tout derrière le VPN sans ouvrir de port chez vous ; le **Low-ID**, lui,
> convient déjà très bien.

---

## Analyse antivirus (clamav) — provisioning & réglage

En **mode download**, le verifier ajoute une 3ᵉ source de verdict : un scan **par signatures**
(`clamscan`) qui rend un fichier `malicious` sur match d'une base virale. C'est **activé par défaut
dans le profil download** (`ENABLED_CHECKS: type_sniff,ffprobe,clamav` dans `bricks/compose.core.yaml`).

**Comment la base arrive (sans casser l'isolement réseau du verifier).** Le verifier n'a **aucune
sortie Internet** (réseau `internal: true`) — il ne peut donc pas mettre à jour la base lui-même. Un
**sidecar `freshclam`** (service séparé sur le réseau `egress`) télécharge et tient à jour la base
dans un **volume partagé `clamav-db`** ; le verifier le **lit en lecture seule**. L'isolement du
verifier est préservé.

- Au démarrage en mode download, `freshclam` fait sa **première synchronisation** (~300–500 Mo) — cela prend
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

## Métriques Prometheus

Le crawler et le verifier exposent des métriques Prometheus.

- **crawler** — sur un port HTTP dédié (`observability.metrics.port` dans `config/crawler/crawler.yaml`),
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

## Durcissement noyau (gVisor)

```bash
CONTAINER_RUNTIME=runsc docker compose -f examples/<fichier> --profile <observer|download> up -d
```

Nécessite le runtime gVisor `runsc` enregistré sur l'hôte (**Linux uniquement**). Sans gVisor,
omettez simplement le préfixe `CONTAINER_RUNTIME=runsc` : la base est déjà durcie (non-root,
capabilities retirées, rootfs en lecture seule, et le verifier sans aucune sortie Internet). gVisor
**est** l'anneau noyau du projet — un noyau en espace utilisateur qui virtualise réseau + FS au
niveau syscall ; il reste **opt-in** car disponible seulement sur les hôtes qui l'ont enregistré.
La posture complète est en « Limites connues » plus bas.

---

## Outils de catalogue

Tous ces outils sont **opérateurs et ponctuels** (pas de boucle, jamais déclenchés par le crawler) et
**ne mutent jamais une base en place** : ils lisent une source et écrivent un fichier neuf.

- **Validation de config** : `uv run python -m emule_indexer validate-config` charge + valide les 4
  configs et sort en erreur (code ≠ 0) si l'une est invalide, **sans rien démarrer**. À lancer avant
  un déploiement.
- **Fusion de catalogues** : `uv run python -m emule_indexer.merge --output catalog-merged.db
  source-a.db source-b.db …` consolide N `catalog.db` (un par chercheur/campagne) en un seul,
  **idempotent** (re-merger est un no-op) et safe-by-default (pas d'écrasement sans `--force` ;
  `--into <source>` pour fusionner dans une source existante).
- **Compaction du catalogue** : `uv run python -m emule_indexer.compact catalog.db -o
  catalog-compact.db [--keep-recent-days 90]` réduit la **seule** table qui croît sans borne,
  `file_observations` (une ligne par fichier observé à chaque cycle). Le brut des `--keep-recent-days`
  derniers jours (90 par défaut) est conservé tel quel ; au-delà, les observations sont **résumées en
  un rollup journalier** node-agnostique dans `file_observation_ranges` (une ligne par fichier et par
  **jour UTC** : ensemble des noms vus, ensemble des nœuds, min/max/somme de la disponibilité, plage
  temporelle ; la moyenne se dérive de somme/compte). À lancer **crawler arrêté** ; il **reconstruit
  vers une sortie neuve** (la sortie ne doit pas exister), puis l'opérateur permute. Coupure **alignée
  sur le jour UTC** : un jour ne serait-ce que partiellement dans la fenêtre reste intégralement brut
  (granularité au jour, pas 24 h glissantes). Ordre recommandé : **fusionner d'abord, compacter
  ensuite** (la compaction voit alors tous les nœuds et produit une seule ligne par fichier/jour).
  Conséquence assumée : un fichier **non vu depuis plus de `--keep-recent-days`** n'a plus
  d'observation brute, donc `last_observation` (chemin « nom frais » du download) le rend introuvable
  — sans incidence en pratique (un tel fichier a quasi sûrement quitté le réseau ; les fichiers
  vivants sont ré-observés en continu). Volume au jour : ~1–6 Go/an pour une cardinalité réaliste,
  très en deçà d'un budget de 50 Go/an.

Pour valider/tester en profondeur (suites d'intégration, smoke, CI), voir le
[guide des tests](testing-guide.md).

---

## WebUI (consultation du catalogue)

La WebUI est une interface de **lecture seule** qui expose le catalogue SQLite via un serveur HTTP
Starlette/Jinja2. Elle n'a **aucune authentification** — l'auth/TLS sont délégués au reverse proxy
amont (nginx, Caddy, Traefik, etc.) que vous mettez devant. Elle ne modifie aucune donnée et n'a
accès à aucun réseau applicatif (elle monte uniquement les volumes de bases de données en lecture).

### Lancer la WebUI

```bash
# Mode observer (catalogue seul) :
docker compose -f examples/<fichier> --profile observer up -d webui

# Mode download (catalogue + téléchargements) :
docker compose -f examples/<fichier> --profile download up -d webui
```

### Routes disponibles

| Route | Description |
|---|---|
| `/` | Tableau de bord — couverture par cible (épisodes trouvés/manquants) |
| `/files` | Liste paginée des fichiers ; filtres `?target=`, `?tier=`, `?verdict=`, `?q=` |
| `/files/{ed2k_hash}` | Détail d'un fichier (observations, décisions, vérifications, explication du matching) |
| `/targets/{target_id}` | Fichiers d'une cible (alias de `/files?target=`) |
| `/node` | État du nœud CRAWLER : `node_id` + entrées du `scheduler_state` (last_full_cycle_at, etc.). N'expose PAS l'état réseau amuled (l'EC n'est pas joignable depuis le webui). |
| `/health` | Healthcheck JSON — répond `{"status": "ok"}` si le service est opérationnel |

### Variables d'environnement

| Variable | Valeur par défaut | Rôle |
|---|---|---|
| `CATALOG_DB` | `/data/catalog/catalog.db` | Chemin vers la base catalogue |
| `LOCAL_DB` | `/data/local/local.db` | Chemin vers la base état local |
| `TARGETS_CONFIG` | `/app/config/targets.yaml` | Config cibles (montée depuis `./config/crawler/targets.yaml`) |
| `MATCHER_CONFIG` | `/app/config/matcher.yaml` | Config matcher (montée depuis `./config/crawler/matcher.yaml`) |
| `WEBUI_HOST` | `127.0.0.1` | Adresse d'écoute (loopback par défaut ; le binding sur l'interface du host se règle au niveau du compose, pas de l'app). |
| `WEBUI_PORT` | `8080` | Port d'écoute (exposé via `${WEBUI_PORT:-8080}:8080`) |

### Exposition derrière un reverse proxy

La WebUI n'a ni TLS ni authentification — mettez un reverse proxy devant si elle est accessible
sur le réseau. Exemple minimal avec Caddy :

```caddyfile
webui.example.com {
    basicauth /* {
        alice $2a$14$...  # bcrypt généré par caddy hash-password
    }
    reverse_proxy webui:8080
}
```

> **Point empirique #1 (à valider au premier déploiement)** : le montage `:ro` des bases SQLite en
> mode WAL vivant (le crawler écrit simultanément) peut échouer — SQLite en mode WAL crée des
> fichiers `-shm` et `-wal` dont les accès `mmap` peuvent être refusés par le noyau quand le FS est
> monté `mode=ro`. Si la WebUI ne démarre pas ou retourne des erreurs SQLite, retirez le `:ro` du
> montage des volumes `catalog-db` et `local-db` dans votre fichier `examples/*.yaml` (le montage
> devient RW, mais `open_ro` applicatif (`PRAGMA query_only=ON`) maintient la garantie lecture seule
> côté application). Documentez le verdict dans
> [`docs/reference/`](reference/2026-06-22-webui-wal-readonly.md) après validation homelab.

---

## Limites connues / follow-ups

- **Ring noyau — posture ACTÉE (2026-06-17)** : le ring noyau par-enfant « étendu » (`net=none`,
  bwrap/montages RO réels, tmpfs dédié) est un **non-objectif assumé**, pas un manque. Chacun exige
  `CAP_SYS_ADMIN` (régression du `cap_drop: ALL`) ou des user namespaces non privilégiés (non
  portables : dépendants d'un sysctl hôte, en conflit avec le seccomp par défaut de Docker, et
  `bwrap` sous gVisor est fragile), pour un gain **marginal** face aux anneaux déjà en place (le
  seccomp par-enfant EPERM-deny déjà les sockets ; le réseau du verifier n'a **aucun egress** via
  `internal: true` ; le rootfs est `read_only`). **gVisor (`runsc`) EST l'anneau noyau** — noyau en
  espace utilisateur qui virtualise réseau + FS au niveau syscall — fourni en **opt-in**
  (knob `CONTAINER_RUNTIME=runsc`, voir « Durcissement noyau » plus haut). Plancher portable universel =
  conteneur durci (`cap_drop: ALL`, `no-new-privileges`, `read_only`, `internal`) + seccomp
  par-enfant + rlimits, sur **n'importe quel** hôte Docker ; gVisor en bolt-on pour les hôtes qui
  supportent `runsc`. (Passer le seccomp par-enfant de blocklist à allowlist a été **écarté** : trop
  fragile, risque de faux positifs sur un média sain.)
- **port-sync — validation réelle** : la boucle est construite ; sa validation **bout-en-bout**
  (port-check High-ID réel derrière le VPN) se fait via un déploiement réel.
- **DV10 (download → quarantaine) — CONFIRMÉ par lecture de la source amont d'amuled**
  (cf. [`docs/reference/2026-06-17-amuled-completion-behavior.md`](reference/2026-06-17-amuled-completion-behavior.md)).
  À la complétion, amuled déplace le fichier vers son **IncomingDir** ; le statut ne passe complet
  qu'**après** le déplacement (pas de race). Le crawler détecte la complétion par la **présence du
  fichier dans les partagés EC** (signal positif, auto-partagé par amuled à la complétion) et promeut
  au **vrai nom on-disk** rapporté par amuled — la collision de nom (`nom(0).ext`) est donc gérée par
  construction. Les **contraintes de déploiement** qui en découlent (IncomingDir = quarantaine, FS
  Linux, pas de catégories, amuled dédié) sont décrites dans le
  [runbook de déploiement](runbook-deployment.md) (mode download). *(La suite e2e « transfert réel » qui
  aurait validé la chaîne complète a été abandonnée — voir le guide des tests ; le décodage
  `shared_files()` contre un vrai amuled est, lui, couvert par `download_integration`.)*
- **WebUI — montage WAL `:ro` inter-conteneurs** : point empirique ouvert, à valider au premier
  déploiement réel (voir section « WebUI » plus haut et
  [`docs/reference/2026-06-22-webui-wal-readonly.md`](reference/2026-06-22-webui-wal-readonly.md)).
- **Hub central / rétention** : non planifiés à ce stade.
