# Runbook d'administration : mulewatch

Ce guide s'adresse à qui **exploite et règle** un nœud déjà monté. Pour *monter* la stack, commencez
par le **[Runbook de déploiement](deployment.md)** ; pour résoudre un problème concret, le
**[Runbook de dépannage](troubleshooting.md)**. On trouve ici le cycle de vie du nœud, le
High-ID (optionnel), l'analyse antivirus, les métriques, le durcissement noyau, les outils de
catalogue et les limites connues. Le sujet du catalogue reste **le fichier, jamais la personne**.

---

## Cycle de vie & données

- **Persistance.** Le catalogue et l'état vivent dans des **volumes Docker nommés** (`catalog-db`,
  `local-db`, `quarantine`, `amule-state`, et `clamav-db` en mode download). Ils **persistent** à la
  recréation des conteneurs : ne lancez `docker compose down` **avec `-v`** que si vous voulez
  réellement **effacer** le catalogue.
- **Arrêter le nœud** : `docker compose -f gluetun.compose.yml down`
  (remplacez par `docker compose down` si vous utilisez la stack sans VPN conteneur,
  celle par défaut).
- **Mettre à jour** : re-tirez les images puis relancez :
  ```bash
  docker compose -f gluetun.compose.yml --profile download pull
  docker compose -f gluetun.compose.yml --profile download up -d
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
  docker run --rm -v deploy_quarantine:/src -v mulewatch_quarantine:/dst alpine sh -c "cp -a /src/. /dst/"
  docker run --rm -v deploy_amule-state:/src -v mulewatch_amule-state:/dst alpine sh -c "cp -a /src/. /dst/"
  ```
  Ajoutez `deploy_clamav-db` (si vous utilisiez le mode download) avec le même patron. Les volumes
  `deploy_prometheus-data` et `deploy_grafana-data` (historique de métriques, régénérable) ne sont
  pas critiques : vous pouvez les laisser derrière ou les copier avec la même recette. Vérifiez
  ensuite avec `docker run --rm -v mulewatch_catalog-db:/d alpine ls -la /d` que le fichier
  `catalog.db` est bien présent avant de supprimer les anciens volumes `deploy_*`.

### Diagnostic après panne

Si le nœud tourne mais ne semble plus catalogue / télécharge plus rien :

| Symptôme | Premier check | Action |
|---|---|---|
| Le crawler tourne mais aucune nouvelle observation depuis > 1 h | `docker compose logs crawler --tail 100` | Cherchez « EC unavailable », « no servers » ou « cycle » récent. Si pas de cycle, amuled est probablement déconnecté du réseau (voir [runbook-troubleshooting](troubleshooting.md)). |
| Téléchargements bloqués en QUEUED | `docker compose logs crawler \| grep -i download` | Vérifier que amuled est en High-ID **ou** qu'il a des sources (sources directes nécessaires en Low-ID). |
| Tous les fichiers ressortent `suspicious` | `docker compose logs verifier --tail 100` | Voir clamav rlimits ci-dessous : probable manque de RAM pour le scan. |
| Le verifier crash périodiquement (logs `Killed`) | `docker stats verifier` | RAM insuffisante. Augmentez `mem_limit` ou désactivez clamav. |
| Un volume Docker se remplit | `docker system df -v` | Catalogue trop gros (voir Compaction) ou quarantaine accumulée. |

Pour les symptômes inconnus, voir le [runbook de dépannage](troubleshooting.md).

### Planification disque

Ordres de grandeur **indicatifs** (à ajuster selon votre trafic eMule réel et la cardinalité de
vos cibles) :

- **`catalog-db`** : croissance lente, **~1 à 6 Go/an** sans compaction (chiffre estimé sur le trafic
  eMule 2026 ; ré-évaluer si vous activez un grand nombre de cibles). La compaction (cf. Outils de
  catalogue) ramène l'historique au-delà de 90 jours à un rollup journalier : taux de compression
  élevé.
- **`quarantine`** : taille des fichiers en cours de vérification (transitoire) + ceux remis à
  l'opérateur (variable, dépend de votre politique de purge).
- **`clamav-db`** : ~300-500 Mo (base de signatures, mise à jour quotidienne).
- **`amule-state`** : qq Mo (server.met, nodes.dat, prefs).

Si votre VPS / NAS approche de saturation, lancez `docker system df -v` pour identifier le volume
fautif, puis `python -m mulewatch.compact` (cf. Outils de catalogue) ou purgez la quarantaine.

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
2. Le service **`docker-proxy`** (profil download, stack `gluetun.compose.yml`), qui redémarre
   amuled de façon confinée (le crawler ne voit jamais le socket Docker directement).
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
>   surtout de votre juridiction. Voir [`docs/legal-and-privacy.md`](legal-and-privacy.md) pour la
>   discussion détaillée (ce que le catalogue stocke et ne stocke pas, ce qu'un VPN protège vraiment,
>   responsabilités de l'opérateur).
> - **Surface d'attaque réseau.** Un port entrant ouvert, c'est un point d'entrée de plus sur votre
>   réseau domestique : redirigez **précisément** ce port (pas une plage) et gardez la machine à jour.
>
> La **route A** garde tout derrière le VPN sans ouvrir de port chez vous ; le **Low-ID**, lui,
> convient déjà très bien si vous voulez juste contribuer au catalogage sans optimiser les sources.

---

## Analyse antivirus (clamav) : provisioning & réglage

En **mode download**, le verifier ajoute une 3ᵉ source de verdict : un scan **par signatures**
(`clamscan`) qui produit un verdict `malicious` quand un fichier correspond à une signature de
virus connue. C'est **activé par défaut** dans le profil download (`ENABLED_CHECKS:
type_sniff,ffprobe,clamav` dans `base.compose.yml`).

> **Ce que clamav fait, et ce qu'il ne fait pas.** Il détecte les virus dont la signature est connue
> dans sa base : c'est un **filet** opportuniste, pas une garantie. Un fichier `clean` selon clamav
> n'est pas certifié inoffensif ; un fichier `malicious` est très probablement infecté. Ne lui faites
> pas porter une promesse qu'il ne tient pas.

**Comment la base arrive (sans casser l'isolement réseau du verifier).** Le verifier n'a **aucune
sortie Internet** (réseau `internal: true`) : il ne peut donc pas mettre à jour la base lui-même. Un
**sidecar `freshclam`** (service séparé sur le réseau `egress`) télécharge et tient à jour la base
dans un **volume partagé `clamav-db`** ; le verifier le **lit en lecture seule**. L'isolement du
verifier est préservé.

### Premier démarrage

Au premier démarrage en mode download, `freshclam` télécharge la base de signatures
(**~300-500 Mo**). Durée typique (en 2026, à ré-évaluer si les bases grossissent) : **3-5 min en
fibre, 10-20 min en ADSL/4G**.

**Comment vérifier que la base est prête :**

```bash
docker compose -f gluetun.compose.yml logs freshclam | grep -iE "updated|main\.cvd"
```

Vous devez voir une ligne du type `freshclam: ClamAV update process started ... main.cvd updated`.

**Pendant la synchro, tous les fichiers ressortent `suspicious`** : c'est défensif (clamav ne dit
jamais `clean` sans base). Une fois la base prête, les **nouveaux** fichiers reçoivent un verdict
normal ; en revanche, **les fichiers déjà passés en `suspicious` ne sont pas re-scannés
automatiquement**. Si vous voulez les re-vérifier, il faut les re-soumettre manuellement (laisser
amuled les re-télécharger, ou utiliser un outil dédié si vous en avez).

L'image du verifier grossit de **~50-80 Mo** (moteur `libclamav` + `clamscan` ; **pas** la base, qui
vit dans le volume : c'est tout l'intérêt du sidecar).

### Mémoire et limites : calibration à valider

> ⚠️ **Hypothèse non validée en prod.** Les valeurs ci-dessous (1,5 Gio d'adressage, 120 s CPU,
> `mem_limit` 2 Gio sur le conteneur verifier) sont une **première calibration homelab**, pas
> validée par un test contre l'image de prod. Si vous observez le symptôme décrit plus bas, c'est
> probablement qu'elles sont sous-dimensionnées pour votre contexte.

`clamscan` charge **toute la base en mémoire** : les rlimits du sous-processus d'analyse sont
**relâchés** quand clamav est actif (≈1,5 Gio d'adressage, 120 s CPU, réglables via
`RLIMIT_AS_BYTES_CLAMAV` / `RLIMIT_CPU_S_CLAMAV`), et le `mem_limit` du conteneur verifier est
relevé à **2 Gio** en conséquence (sinon le cgroup tue le scan avant que le rlimit ne s'applique).

**Symptôme typique d'un sous-dimensionnement** : un fichier **sain** ressort systématiquement
`suspicious`, même longtemps après que `freshclam` ait annoncé la base prête. Cause probable : le
scan est tué par manque de mémoire avant d'avoir pu rendre son verdict. **Procédure d'ajustement** :
doublez les deux valeurs (`RLIMIT_AS_BYTES_CLAMAV` à 3 Gio, `mem_limit` du verifier à 4 Gio),
redémarrez le verifier, retestez. Si le symptôme persiste, doublez encore.

> **Désactiver clamav** : retirez `clamav` de `ENABLED_CHECKS` (le verifier retombe sur
> `type_sniff,ffprobe`) et, si vous voulez, ne lancez pas le sidecar. Le **smoke test** et le profil
> **observer** tournent déjà sans clamav. C'est une option valide sur machine peu RAM (< 4 Go).

---

## Métriques Prometheus

> **Optionnel.** Cette section ne concerne que les opérateurs qui veulent **scraper** les métriques
> du nœud depuis un système de monitoring **externe** (Prometheus + Grafana qu'ils gèrent par
> ailleurs). Si vous voulez juste voir les métriques sur un dashboard sans rien configurer : Grafana
> est déjà inclus par défaut (cf. [runbook de déploiement, annexe D : Régler le monitoring](deployment.md#annexe-d-régler-le-monitoring)),
> ouvrez-le directement, le scrape est déjà configuré et le dashboard est livré clé en main.

Le crawler et le verifier exposent des métriques Prometheus.

- **crawler** : sur un port HTTP dédié (`observability.metrics.port` dans `config/crawler/crawler.yml`),
  accessible depuis le réseau `ec`.
- **verifier** : sur son port de service (par défaut `8000`), route `/metrics`. Comme le verifier est
  sur un réseau **sans sortie Internet**, un Prometheus externe doit **rejoindre ce réseau** (ou vous
  exposez le port sur l'hôte).

Exemple de `scrape_config` (à coller dans votre `prometheus.yml` externe) :

```yaml
scrape_configs:
  - job_name: 'mulewatch-crawler'
    static_configs:
      - targets: ['crawler:9090']   # port configurable
  - job_name: 'mulewatch-verifier'
    static_configs:
      - targets: ['verifier:8000']  # même port que le service (/metrics)
```

---

## Outils de catalogue

Tous ces outils sont **opérateurs et ponctuels** (pas de boucle, jamais déclenchés par le crawler) et
**ne mutent jamais une base en place** : ils lisent une source et écrivent un fichier neuf.

- **Validation de config** : `uv run python -m mulewatch validate-config` charge + valide les 4
  configs et sort en erreur (code ≠ 0) si l'une est invalide, **sans rien démarrer**. À lancer avant
  un déploiement.
- **Fusion de catalogues** : `uv run python -m mulewatch.merge --output catalog-merged.db
  source-a.db source-b.db …` consolide N `catalog.db` (un par chercheur/campagne) en un seul,
  **idempotent** (re-merger est un no-op) et safe-by-default (pas d'écrasement sans `--force` ;
  `--into <source>` pour fusionner dans une source existante). **Cycle de partage entre chercheurs
  documenté dans [docs/README § Collaboration entre chercheurs](README.md#collaboration-entre-chercheurs).**
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
[guide des tests](testing-guide.md).

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
n'écrit une ligne que sur un vrai changement) et tourne dans les deux modes (observer et download).

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
de lancement du [Runbook de déploiement](deployment.md#5-lancer) la met en ligne, observer comme
download.

```bash
# Stack sans VPN, observer (catalogue seul) : la WebUI est servie par le crawler
docker compose up -d

# Stack sans VPN, download (catalogue + téléchargements) : idem
docker compose --profile download up -d
```

### Routes disponibles

| Route | Description |
|---|---|
| `/` | Tableau de bord : couverture par cible (épisodes trouvés/manquants) |
| `/files` | Liste paginée des fichiers ; filtres `?target=`, `?tier=`, `?verdict=`, `?q=` |
| `/files/{ed2k_hash}` | Détail d'un fichier (observations, décisions, vérifications, explication du matching) |
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
> [`reference/2026-06-22-webui-wal-readonly.md`](reference/2026-06-22-webui-wal-readonly.md).*

---

## Vérifier l'authenticité d'une image

Chaque image publiée est signée et attestée par la CI (cosign, keyless OIDC). Avant de
lancer une image tirée de GHCR, on peut vérifier qu'elle vient bien de notre pipeline.

Prérequis : [cosign](https://github.com/sigstore/cosign) installé.

L'identité attendue est le workflow de release du dépôt :

```sh
IMAGE=ghcr.io/geoffreycoulaud/mulewatch-crawler:latest   # ou mulewatch-verifier
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
- **Sandbox noyau, choix actés (2026-06-17, updated 2026-06-29)** : la sandbox optionnelle gVisor
  (`runsc`) a été retirée du projet (YAGNI). Le plancher portable universel est suffisant :
  conteneur durci (`cap_drop: ALL`, `no-new-privileges`, `read_only`, `internal`) + seccomp
  par-enfant + rlimits, sur **n'importe quel** hôte Docker (Linux, Windows, macOS). Plusieurs
  alternatives d'isolation niveau noyau ont été évaluées puis **écartées explicitement** :
  - Isolation par-enfant « étendue » (`net=none`, bwrap/montages RO réels, tmpfs dédié) :
    chacune de ces options exige `CAP_SYS_ADMIN` (qui annulerait le `cap_drop: ALL` du conteneur)
    ou des user namespaces non privilégiés (non portables : dépendants d'un réglage sysctl hôte,
    en conflit avec le seccomp par défaut de Docker). Gain
    **marginal** face aux protections déjà en place (le seccomp par-enfant refuse déjà les sockets ;
    le réseau du verifier n'a **aucune sortie Internet** via `internal: true` ; le rootfs est
    monté en lecture seule).
  - Seccomp en mode « allowlist » (autoriser explicitement une liste fermée d'appels système) :
    **écarté** car trop fragile, risque de faux positifs sur un média sain. Le seccomp par-enfant
    actuel utilise une **blocklist** (refuser explicitement les appels dangereux) : moins strict
    mais plus robuste.
- **port-sync, validation réelle** : la boucle est construite ; sa validation **bout-en-bout**
  (port-check High-ID réel derrière le VPN) se fait via un déploiement réel.
- **DV10 (download → quarantaine)**, Statut : **chaîne complète confirmée par lecture des sources
  amont d'amuled** (cf. [`docs/reference/2026-06-17-amuled-completion-behavior.md`](reference/2026-06-17-amuled-completion-behavior.md)),
  mais **non validée par un test bout-en-bout sur transfert réel** (la suite e2e correspondante a
  été abandonnée : voir le guide des tests). Le décodage `shared_files()` contre un vrai amuled est
  en revanche couvert par `download_integration`. Conséquence : si vous montez un nœud en mode
  download, considérez la chaîne complète comme **fonctionnelle d'après lecture du code** mais
  **non éprouvée en production réelle** ; remontez tout comportement inattendu.

  Mécanique : à la complétion, amuled déplace le fichier vers son **IncomingDir** ; le statut ne
  passe complet qu'**après** le déplacement (pas de race). Le crawler détecte la complétion par la
  **présence du fichier dans les partagés EC** (signal positif, auto-partagé par amuled à la
  complétion) et promeut au **vrai nom on-disk** rapporté par amuled : la collision de nom
  (`nom(0).ext`) est gérée par construction. Les **contraintes de déploiement** qui en découlent
  (IncomingDir = quarantaine, FS Linux, pas de catégories, amuled dédié) sont décrites dans la
  [référence amuled-completion-behavior](reference/2026-06-17-amuled-completion-behavior.md#contraintes-de-déploiement-résumé)
  (source unique) et signalées dans le [runbook de déploiement](deployment.md) (mode download).
- **WebUI (lecture seule)** : **point clos**. La WebUI est désormais servie **en intra-processus**
  par le crawler (plus de conteneur séparé, donc plus de montage inter-conteneurs). La garantie
  lecture seule repose sur `mode=ro` + `PRAGMA query_only=ON` ; l'ancien montage Docker `:ro` WAL
  est caduc. Voir section « WebUI » plus haut et
  [`docs/reference/2026-06-22-webui-wal-readonly.md`](reference/2026-06-22-webui-wal-readonly.md).
- **Hub central / rétention** : non planifiés à ce stade.
