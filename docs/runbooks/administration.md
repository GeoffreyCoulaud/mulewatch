# Runbook d'administration : mulewatch

Ce guide s'adresse à qui **exploite et règle** un nœud déjà monté. Pour *monter* la stack, commencez
par le **[Runbook de déploiement](deployment.md)** ; pour résoudre un problème concret, le
**[Runbook de dépannage](troubleshooting.md)**. On trouve ici le cycle de vie du nœud, le
High-ID (optionnel), les métriques, le durcissement conteneur, les outils de
catalogue et les limites connues. Le sujet du catalogue reste **le fichier, jamais la personne**.

---

## Cycle de vie & données

- **Persistance.** Le catalogue et l'état vivent dans des **volumes Docker nommés** (`catalog-db`,
  `local-db`, `amule-state`). Ils **persistent** à la
  recréation des conteneurs : ne lancez `docker compose down` **avec `-v`** que si vous voulez
  réellement **effacer** le catalogue. Les fichiers téléchargés, eux, ne sont pas dans un volume :
  ils vivent dans `downloads/incoming` et `downloads/temp` de votre dossier de travail (montages
  bind), que `-v` ne touche pas.
- **Arrêter le nœud** : `docker compose -f gluetun.compose.yml down`
  (remplacez par `docker compose down` si vous utilisez la stack sans VPN conteneur,
  celle par défaut).
- **Mettre à jour** : re-tirez les images puis relancez :
  ```bash
  docker compose -f gluetun.compose.yml pull
  docker compose -f gluetun.compose.yml up -d
  ```
- **Redémarrage de la machine hôte.** Les conteneurs ont `restart: unless-stopped` : ils reviennent
  seuls au boot de l'hôte (Docker doit démarrer en service système). **Aucune commande à relancer.**
  Vérifiez après reboot : `docker compose -f gluetun.compose.yml ps`. Si un service est en `Exited`
  alors que les autres sont `Up`, voir « Diagnostic après panne » ci-dessous.
- **Migration depuis une version antérieure à ce changement.** Le nom de projet Compose est
  désormais fixé à `mulewatch` dans `compose.yaml`, quel que soit le nom de votre dossier de travail
  (historiquement, il dérivait du nom du dossier, en général `deploy` du temps où l'on travaillait
  directement dans le dossier `deploy` du dépôt). Un simple `docker compose up -d` créera donc de
  NOUVEAUX volumes vides, et le nœud semblera avoir perdu son catalogue. Les données existantes sont
  toujours là, dans les anciens volumes (listez-les avec `docker volume ls`, préfixe `deploy_`).
  Avant de relancer, copiez-les vers les nouveaux noms :
  ```bash
  docker run --rm -v deploy_catalog-db:/src -v mulewatch_catalog-db:/dst alpine sh -c "cp -a /src/. /dst/"
  docker run --rm -v deploy_local-db:/src -v mulewatch_local-db:/dst alpine sh -c "cp -a /src/. /dst/"
  docker run --rm -v deploy_amule-state:/src -v mulewatch_amule-state:/dst alpine sh -c "cp -a /src/. /dst/"
  ```
  Les volumes `deploy_quarantine`, `deploy_clamav-db`, `deploy_prometheus-data` et
  `deploy_grafana-data`, s'ils existent encore, appartiennent à des services retirés le
  2026-09-13 : rien ne les relit, vous pouvez les supprimer une fois la migration vérifiée.
  Vérifiez
  ensuite avec `docker run --rm -v mulewatch_catalog-db:/d alpine ls -la /d` que le fichier
  `catalog.db` est bien présent avant de supprimer les anciens volumes `deploy_*`.

### Diagnostic après panne

Si le nœud tourne mais ne semble plus catalogue / télécharge plus rien :

| Symptôme | Premier check | Action |
|---|---|---|
| Le crawler tourne mais aucune nouvelle observation depuis > 1 h | `docker compose logs crawler --tail 100` | Cherchez « EC unavailable », « no servers » ou « cycle » récent. Si pas de cycle, amuled est probablement déconnecté du réseau (voir [runbook-troubleshooting](troubleshooting.md)). |
| Téléchargements bloqués en QUEUED | `docker compose logs crawler \| grep -i download` | Vérifier que amuled est en High-ID **ou** qu'il a des sources (sources directes nécessaires en Low-ID). |
| Un téléchargement fini n'apparaît pas dans `downloads/incoming` | `docker compose logs amuled --tail 100` | Voir la fiche [« A finished file never shows up »](troubleshooting.md#a-finished-file-never-shows-up-in-downloadsincoming). |
| Le disque se remplit | `docker system df -v` puis `du -sh downloads/` | Catalogue trop gros (voir Compaction) ou fichiers téléchargés accumulés : le plafond disque ne borne que les octets *en vol*, pas le total. |

Pour les symptômes inconnus, voir le [runbook de dépannage](troubleshooting.md).

### Planification disque

Ordres de grandeur **indicatifs** (à ajuster selon votre trafic eMule réel et la cardinalité de
vos cibles) :

- **`catalog-db`** : croissance lente, **~1 à 6 Go/an** sans compaction (chiffre estimé sur le trafic
  eMule 2026 ; ré-évaluer si vous activez un grand nombre de cibles). La compaction (cf. Outils de
  catalogue) ramène l'historique au-delà de 90 jours à un rollup journalier : taux de compression
  élevé.
- **`downloads/`** : les fichiers téléchargés, qui **s'accumulent sans borne** (rien ne les purge).
  Le plafond `download.disk_cap_bytes` est un compte des téléchargements *en cours*, pas une mesure
  du disque : c'est à vous de faire le ménage dans `downloads/incoming`.
- **`amule-state`** : qq Mo (server.met, nodes.dat, prefs).

Si votre VPS / NAS approche de saturation, lancez `docker system df -v` et `du -sh downloads/`
pour identifier le coupable, puis `python -m mulewatch.compact` (cf. Outils de catalogue) ou
faites le ménage dans `downloads/incoming`.

---

## High-ID (optionnel) : devenir joignable

Par défaut, un nœud est en **Low-ID** : il fonctionne très bien ainsi (recherche, catalogage,
téléchargement), il est juste sous-optimal côté sources. Le **High-ID** rend la machine **joignable**
depuis l'extérieur (plus de sources directes) ; c'est **facultatif**. Pour être joignable, il faut
qu'un **port entrant** atteigne amuled : deux routes, selon que vous gardez ou non le VPN devant le
trafic P2P.

### Route A (recommandée) : derrière le VPN, via port forwarding

> ⚠️ **Prérequis Route A** : Docker **rootful** : Docker Desktop (Win/macOS/Linux) **ou** Docker natif.
> Le `docker-proxy` tourne en root (`user: "0:0"`), donc **plus besoin** du groupe Unix `docker`/GID.
> Le mode **rootless** n'est pas supporté (socket sous `$XDG_RUNTIME_DIR`, accès par UID). Si le
> port-sync ne vous tente pas, prenez la **Route B** (port-forward manuel sur votre box) : vous y
> perdez seulement la mise à jour automatique du port si votre VPN rotate, ce qui n'arrive que rarement.

**Comment ça marche.** gluetun sait demander un **port forwarding** à votre fournisseur VPN : le
port joignable est celui du VPN, **tout le trafic reste derrière le tunnel**. Cette boucle
« port-sync » fonctionne en trois maillons solidaires :

```
[gluetun]  ──── obtient le port forwardé du VPN ────►  [docker-proxy]  ──── pousse le redémarrage d'amuled ────►  [amuled]
                                                            ▲                                                          ▲
                                                  lit le socket Docker                                        écoute sur le nouveau port
                                              (socket lu en root, `user: "0:0"`)
```

Si **un seul** maillon est mal configuré, le port-sync est désarmé silencieusement et le nœud reste
en Low-ID : pas d'erreur visible. C'est pourquoi le crawler **refuse de démarrer** (fail-fast)
quand certains réglages combinés sont incohérents.

**Configuration, trois réglages solidaires :**

1. **VPN avec port forwarding** + `VPN_PORT_FORWARDING: "on"` dans `.env` (cherchez les fournisseurs
   marqués `PORT_FORWARDING: yes` dans la [liste gluetun](https://github.com/qdm12/gluetun-wiki/tree/main/setup/providers)).
2. Le service **`docker-proxy`**, présent d'office dans la stack `gluetun.compose.yml`, qui
   redémarre amuled de façon confinée (le crawler ne voit jamais le socket Docker directement).
3. Dans `config/crawler/crawler.yml` : basculez `port_sync.enabled: true` (le bloc est
   présent par défaut avec les URL déjà configurées : `gluetun_control_url` et `restarter_url` ;
   réglage fin optionnel via les autres champs de la section).

Une fois actif, surveillez les events `port-sync` / `High-ID retrouvé` dans les logs et les
métriques `emule_port_*`.

### Route B : ouvrir un port vous-même

Si votre fournisseur ne fait pas de port forwarding, le High-ID reste atteignable en
**ouvrant/redirigeant un port** sur votre box/routeur vers le nœud, pour que les pairs joignent amuled
directement. C'est une option **parfaitement viable** ; le choix relève surtout de votre **tolérance
au risque**.

> #### À savoir
> - **Légalité.** Partager une œuvre sous droit d'auteur est illégal **dans la plupart des
>   juridictions** : c'est vrai dès qu'on fait tourner un nœud, route B ou non. Le risque pratique
>   pour ce projet est **statistiquement faible** (eMule est un réseau de niche en 2026, et la cible,
>   des médias perdus aux ayants droit inactifs, mobilise peu) mais **n'est pas nul** ; il dépend
>   surtout de votre juridiction. Voir [`docs/legal-and-privacy.md`](../legal-and-privacy.md) pour la
>   discussion détaillée (ce que le catalogue stocke et ne stocke pas, ce qu'un VPN protège vraiment,
>   responsabilités de l'opérateur).
> - **Surface d'attaque réseau.** Un port entrant ouvert, c'est un point d'entrée de plus sur votre
>   réseau domestique : redirigez **précisément** ce port (pas une plage) et gardez la machine à jour.
>
> La **route A** garde tout derrière le VPN sans ouvrir de port chez vous ; le **Low-ID**, lui,
> convient déjà très bien si vous voulez juste contribuer au catalogage sans optimiser les sources.

---

## Prometheus metrics

The crawler exposes a Prometheus endpoint on the port set by `observability.metrics.port` in
`config/crawler/crawler.yml` (default `9090`, `enabled: true` by default). **No Prometheus and no
Grafana container ships with the stack**: if you want dashboards, point your own Prometheus at the
crawler.

That port is not published on the host by default. Two ways to reach it:

- attach your Prometheus container to the stack's `ec` network, and scrape `crawler:9090`;
- or publish the port yourself by adding a mapping to the `crawler` service in `base.compose.yml`
  (and treat it like the webui: no auth, so keep it off the open Internet).

Example `scrape_config` for your own `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: 'mulewatch-crawler'
    static_configs:
      - targets: ['crawler:9090']   # the port is configurable
```

Setting `observability.metrics.enabled: false` turns the endpoint off entirely; the crawl and the
webui are unaffected.

---

## Outils de catalogue

Tous ces outils sont **opérateurs et ponctuels** (pas de boucle, jamais déclenchés par le crawler) et
**ne mutent jamais une base en place** : ils lisent une source et écrivent un fichier neuf.

- **Validation de config** : `uv run python -m mulewatch validate-config` charge + valide les 3
  configs et sort en erreur (code ≠ 0) si l'une est invalide, **sans rien démarrer**. À lancer avant
  un déploiement.
- **Fusion de catalogues** : `uv run python -m mulewatch.merge --output catalog-merged.db
  source-a.db source-b.db …` consolide N `catalog.db` (un par chercheur/campagne) en un seul,
  **idempotent** (re-merger est un no-op) et safe-by-default (pas d'écrasement sans `--force` ;
  `--into <source>` pour fusionner dans une source existante). **Cycle de partage entre chercheurs
  documenté dans [docs/README § Collaboration between searchers](../README.md#collaboration-between-searchers).**
- **Compaction du catalogue** : `uv run python -m mulewatch.compact catalog.db -o
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

  **Quand la lancer ?** Pas avant que le volume `catalog-db` devienne gênant, repère pratique :
  **catalog.db ≥ ~5 Go** ou **après ≥ 6 mois d'exploitation continue**, selon ce qui arrive en
  premier. Cadence ensuite : tous les 3 à 6 mois. Inutile en dessous de ces seuils (le coût en
  arrêt de service n'en vaut pas la peine).

  **Conséquence assumée** : un fichier **non vu depuis plus de `--keep-recent-days`** n'a plus
  d'observation brute. Effet visible : `last_observation` (chemin « nom frais » utilisé par le
  download) le rend introuvable : sans incidence en pratique (un tel fichier a quasi sûrement
  quitté le réseau ; les fichiers vivants sont ré-observés en continu). **Si vous voulez garder
  l'historique brut sur 1 an**, passez `--keep-recent-days 365` (au prix d'une compaction moins
  efficace).

  Volume au jour : **~1-6 Go/an pour une cardinalité réaliste en 2026** (chiffre à ré-évaluer
  selon votre trafic et le nombre de cibles), très en deçà d'un budget de 50 Go/an.

Pour valider/tester en profondeur (suites d'intégration, smoke, CI), voir le
[guide des tests](../testing-guide.md).

---

## Ré-évaluation du catalogue au démarrage

À chaque démarrage, le crawler ré-évalue **tout le catalogue** contre le matcher courant
(`matcher.yml` + `targets.yml`), mais **seulement s'ils ont changé** depuis le dernier passage.
Le crawler stocke une empreinte `sha256` des deux fichiers dans `local.db` : si l'empreinte est
identique, la passe est **entièrement sautée** (un simple redémarrage ne coûte rien). Éditer
l'un des deux fichiers (même un commentaire) déclenche une passe au prochain démarrage.

Effets d'un changement de policy :

- **Exclusion (rétractation).** Un fichier que le nouveau matcher n'attrape plus est *rétracté* :
  une ligne sentinelle `tier="retracted"` est ajoutée (le catalogue reste append-only, rien n'est
  supprimé). La WebUI le traite alors comme non identifié (masqué du filtre « matched only ») et il
  sort de la file de téléchargement. On ne « dé-télécharge » jamais : un fichier déjà récupéré reste.
- **Re-classement + action.** Un fichier qui change de palier déclenche l'action du nouveau palier :
  `download` est mis en file **et** notifie le canal *community* ; `notify` notifie le canal
  *operations* (configurez une cible `tag: operations` sous `observability.notifications` dans
  `crawler.yml`). Les paliers `catalog` et `retracted` sont silencieux (log + métrique seulement).

Un gros changement de policy peut donc émettre une rafale de notifications (bornée aux fichiers dont
le palier a réellement changé) : c'est voulu. Le passage est idempotent (la garde anti-redondance
n'écrit une ligne que sur un vrai changement) et tourne que le téléchargement soit activé ou non.

---

## WebUI (consultation du catalogue)

La WebUI est une interface de **lecture seule** servie **en intra-processus** par le crawler (même
image, même service `crawler`, sur un thread dédié) et exposant le catalogue SQLite via un serveur
HTTP Starlette/Jinja2. Elle n'a **aucune authentification** : l'auth/TLS sont délégués au reverse
proxy amont (nginx, Caddy, Traefik, etc.) que vous mettez devant. Elle ne modifie jamais les bases :
elle ouvre ses propres connexions SQLite en lecture seule (`mode=ro` + `PRAGMA query_only=ON`) via
son `ReaderProvider`, jamais une connexion en écriture.

### Lancer la WebUI

Rien de spécial à lancer : la WebUI est servie **en intra-processus** par le service `crawler`, donc
elle démarre et s'arrête **avec lui**, sans service ni profil dédié. N'importe laquelle des commandes
de lancement du [Runbook de déploiement](deployment.md#5-start-it) la met en ligne, que le
téléchargement soit activé ou non.

```bash
# Stack sans VPN : la WebUI est servie par le crawler
docker compose up -d

# Stack VPN : idem
docker compose -f gluetun.compose.yml up -d
```

### Routes disponibles

| Route | Description |
|---|---|
| `/` | Tableau de bord : couverture par cible (épisodes trouvés/manquants) |
| `/files` | Liste paginée des fichiers ; filtres `?target=`, `?tier=`, `?q=` |
| `/files/{ed2k_hash}` | Détail d'un fichier (observations, décisions, explication du matching) |
| `/targets/{target_id}` | Fichiers d'une cible (alias de `/files?target=`) |
| `/node` | État du nœud CRAWLER : `node_id` + entrées du `scheduler_state` (last_full_cycle_at, etc.). N'expose PAS l'état réseau amuled (l'EC n'est pas joignable depuis le webui). |
| `/controls` | Contrôles d'exécution : forcer une passe de recherche maintenant, mettre en pause / reprendre la surveillance, redémarrer le service (sortie de processus propre ; le `restart: unless-stopped` du conteneur le relance). Ce sont des POST qui **modifient l'état**, sans jeton CSRF ni authentification, par conception. |
| `/console` | Console SQL **en lecture seule** : exécute un unique `SELECT` sur `catalog.db` ou `local.db`, affiche le tableau de résultats + le temps d'exécution + le nombre de lignes, export CSV. Toujours active. |
| `/health` | Healthcheck JSON : répond `{"status": "ok"}` si le service est opérationnel |

> **Posture des surfaces `/controls` et `/console`.** `/controls` déclenche des actions qui
> **modifient l'état** (passe forcée, pause/reprise, redémarrage) : pas de jeton CSRF, pas
> d'authentification, par conception. `/console` est **structurellement en lecture seule**
> (`mode=ro` + `query_only`) et bornée contre le DoS (timeout mur, plafond de lignes rendues, une
> seule instruction). Les deux ne sont défendables que **derrière le périmètre de l'opérateur** :
> réseau privé, VPN ou reverse proxy authentifié, **jamais exposées sur Internet**. La
> ré-évaluation et la remise en file n'ont délibérément **pas** été construites (une ré-évaluation
> à chaud est un no-op sans rechargement de config, et la remise en file dépend du réglage des
> décisions de téléchargement, différé).

### Adresse d'écoute et chemins de bases

Servie en intra-processus, la WebUI ne lit **aucune** variable d'environnement dédiée : elle dérive
tout de la config opérateur du crawler (`crawler.yml` + arguments de lancement). L'adresse d'écoute
interne est **figée à `0.0.0.0:8080` dans le code** (non configurable) : c'est l'exposition via
compose (port publié + réseaux) qui gouverne l'accès, pas une adresse de bind applicative. La seule
variable d'environnement en jeu est `WEBUI_PORT`, et uniquement côté hôte.

| Réglage | Où | Valeur par défaut | Rôle |
|---|---|---|---|
| `catalog_db_path` | `crawler.yml` | `/data/catalog/catalog.db` | Base catalogue, lue en lecture seule par la WebUI |
| `local_db_path` | `crawler.yml` | `/data/local/local.db` | Base état local, lue en lecture seule par la WebUI |
| `WEBUI_PORT` | `.env` (env) | `8080` | Port **publié côté hôte** dans le mapping compose `"${WEBUI_PORT:-8080}:8080"` (hôte:conteneur). Ne change PAS le port d'écoute interne. |

### Exposition derrière un reverse proxy

La WebUI n'a ni TLS ni authentification : mettez un reverse proxy devant si elle est accessible
sur le réseau. Exemple minimal avec Caddy :

```caddyfile
webui.example.com {
    basicauth /* {
        alice $2a$14$...  # bcrypt généré par caddy hash-password
    }
    reverse_proxy crawler:8080
}
```

> **Garantie lecture seule de la WebUI.** Servie **en intra-processus**, la WebUI partage les
> volumes `catalog-db` et `local-db` du crawler (montés en **lecture-écriture** pour le crawler),
> mais elle ouvre **ses propres** connexions SQLite en lecture seule via son `ReaderProvider` :
> `mode=ro` **et** `PRAGMA query_only=ON`, jamais une connexion en écriture. Toute tentative
> d'écriture est refusée par SQLite avant même d'atteindre le disque : votre catalogue est protégé
> contre une régression du code WebUI.
>
> *Historique : quand la WebUI était un conteneur séparé, un montage Docker en `:ro` avait été
> essayé puis abandonné (instable avec SQLite en mode WAL : le crawler écrit `-shm` et `-wal` en
> simultané, le noyau peut refuser les `mmap` sur un FS monté `ro`). En intra-processus, ce
> raisonnement de montage est **caduc** (plus de conteneur séparé à monter) ; la garantie repose
> désormais entièrement sur `mode=ro` + `query_only`. Voir
> [`reference/2026-06-22-webui-wal-readonly.md`](../reference/2026-06-22-webui-wal-readonly.md).*

---

## Vérifier l'authenticité d'une image

Chaque image publiée est signée et attestée par la CI (cosign, keyless OIDC). Avant de
lancer une image tirée de GHCR, on peut vérifier qu'elle vient bien de notre pipeline.

Prérequis : [cosign](https://github.com/sigstore/cosign) installé.

L'identité attendue est le workflow de release du dépôt :

```sh
IMAGE=ghcr.io/geoffreycoulaud/mulewatch-crawler:latest
IDENTITY='^https://github.com/GeoffreyCoulaud/mulewatch/.github/workflows/release.yml@refs/'
ISSUER=https://token.actions.githubusercontent.com
```

Vérifier la **signature** de l'image :

```sh
cosign verify \
  --certificate-identity-regexp "$IDENTITY" \
  --certificate-oidc-issuer "$ISSUER" \
  "$IMAGE"
```

Vérifier une **attestation** (SBOM ou VEX ; `--type` parmi `cyclonedx`,
`https://syft.dev/bom`, `openvex`) :

```sh
cosign verify-attestation \
  --type openvex \
  --certificate-identity-regexp "$IDENTITY" \
  --certificate-oidc-issuer "$ISSUER" \
  "$IMAGE"
```

Une commande qui réussit prouve que ce digest a été signé/attesté par notre CI : un digest
substitué (image malveillante) n'aurait pas d'attestation signée par notre identité OIDC.
La signature étant `--recursive`, la vérification fonctionne aussi bien par tag (index) que
par digest d'architecture. Le détail de la chaîne et du triage VEX est dans `SECURITY.md`.

---

## Limites connues / follow-ups

- **Migrations : le tri se fait en mémoire, sans plafond (2026-07-16)** : les migrations SQLite
  s'appliquent avec `temp_store=MEMORY` (`connection.py`, restauré juste après). Motif : construire
  un index déborde le tmpfs de 64 Mo de `/tmp` et échoue en `SQLITE_FULL`, ce qui fait boucler le
  crawler au démarrage (constaté sur le node réel avec la migration 0004). Le remède alternatif,
  agrandir le tmpfs, vit dans le compose de l'opérateur : il peut être oublié au moment d'une montée
  d'image, et cet oubli casse le node ; l'image porte donc son propre remède. **Risque résiduel
  accepté** : le trieur en mémoire de SQLite ne se vide jamais et n'est borné ni par `cache_size` ni
  par autre chose que le nombre de lignes (environ 116 octets par ligne). Repère mesuré : 1,19 M
  d'observations donnent un pic d'environ 150 Mo, soit un plafond vers **4,5 M de lignes** à
  `mem_limit: 512m`. Au-delà, le conteneur est tué par le noyau (exit 137, journal vide) au lieu de
  produire une erreur lisible : voir la fiche
  [« Un conteneur redémarre en boucle »](troubleshooting.md#un-conteneur-redémarre-en-boucle). Ne
  pas « corriger » sans rouvrir la décision. À surveiller : `file_observations` croît sans borne, et
  c'est une **future** migration triant cette table qui pose le risque, pas 0004 (ponctuelle, déjà
  passée).
- **Container hardening, decisions on record (2026-06-17, updated 2026-06-29, narrowed
  2026-09-13)**: the optional gVisor (`runsc`) sandbox was dropped as YAGNI, and the per-child
  seccomp blocklist and rlimits left the project on 2026-09-13 along with the analysis child they
  confined. What remains, and is enough, is the portable floor applied to our own container on
  **any** Docker host (Linux, Windows, macOS): `cap_drop: ALL`, `no-new-privileges`, a `read_only`
  rootfs, a size-capped `tmpfs` for `/tmp`, `pids_limit` and `mem_limit`. Nothing in the crawler
  spawns a subprocess over untrusted input any more, and nothing ever opens a downloaded file.
  Kernel-level isolation beyond that stays **explicitly out of scope**: `net=none`, bwrap and real
  read-only mount namespaces each require either `CAP_SYS_ADMIN` (which would cancel the container's
  own `cap_drop: ALL`) or unprivileged user namespaces (not portable: they depend on a host sysctl
  and conflict with Docker's default seccomp profile).
- **port-sync, validation réelle** : la boucle est construite ; sa validation **bout-en-bout**
  (port-check High-ID réel derrière le VPN) se fait via un déploiement réel.
- **Download completion, real-world validation**: the chain is **confirmed by reading amuled's
  upstream sources** (see
  [`docs/reference/2026-06-17-amuled-completion-behavior.md`](../reference/2026-06-17-amuled-completion-behavior.md))
  and one **real transfer on a production node on 2026-09-11**, but there is **no end-to-end test on
  a real transfer** (that e2e suite was abandoned, see the testing guide). The `shared_files()`
  decoding against a real amuled *is* covered by `download_integration`.

  Mechanics: on completion amuled moves the file into its **IncomingDir** and only then flips the
  status to complete (no race). The crawler detects completion as **"shared AND absent from the
  download queue"** (amuled auto-shares a completed file, but it shares partials too, so the queue is
  what separates them, see CORRECTION 2026-09-11 in the reference). It then records the state and
  notifies: since 2026-09-13 nothing moves the file, so the old quarantine promotion and its
  name-collision handling are gone, and with them the constraints about a shared quarantine volume
  and a Linux filesystem. What still holds: **no amuled category** redirecting the destination, and
  an amuled **dedicated** to the crawler with a **small shared set**.

  **Consequence of dropping the promotion step, not yet measured**: completed files now stay in
  `IncomingDir` forever, so amuled's shared-files list grows without bound, and it is read on every
  download cycle. On a long-lived node with many completed downloads, expect completion detection to
  get slower. Pruning `downloads/incoming` is the operator's job.

- **WebUI (lecture seule)** : **point clos**. La WebUI est désormais servie **en intra-processus**
  par le crawler (plus de conteneur séparé, donc plus de montage inter-conteneurs). La garantie
  lecture seule repose sur `mode=ro` + `PRAGMA query_only=ON` ; l'ancien montage Docker `:ro` WAL
  est caduc. Voir section « WebUI » plus haut et
  [`docs/reference/2026-06-22-webui-wal-readonly.md`](../reference/2026-06-22-webui-wal-readonly.md).
- **Hub central / rétention** : non planifiés à ce stade.
