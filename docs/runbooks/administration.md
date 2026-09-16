# Runbook d'administration : mulewatch

Ce guide s'adresse à qui **exploite et règle** un nœud déjà monté. Pour *monter* la stack, commencez
par le **[Runbook de déploiement](deployment.md)** ; pour résoudre un problème concret, le
**[Runbook de dépannage](troubleshooting.md)**. On trouve ici le cycle de vie du nœud, le
High-ID (optionnel), les métriques, le durcissement conteneur, les outils de
catalogue et les limites connues. Le sujet du catalogue reste **le fichier, jamais la personne**.

---

## Cycle de vie & données

Un nœud est désormais **un seul conteneur** (service compose `mulewatch`) qui fait tourner **trois
processus** sous le superviseur s6 : `amuled`, `amuleweb` et `mulewatch` — le crawler, qui sert
aussi la webui en intra-processus, sur un thread dédié. PID 1 est l'entrypoint : il crée
l'utilisateur `amule` à partir de `PUID`/`PGID`, prend possession des points de montage, écrit un
`amule.conf` **seulement s'il n'y en a pas**, puis passe la main à `s6-svscan`. Chaque service
abandonne ensuite ses privilèges avec `setpriv`.

Quatre variables d'environnement sont **strictement obligatoires** : `PUID`, `PGID`,
`AMULE_EC_PASSWORD` et `WEBUI_PWD`. Le one-shot de démarrage sort en 1 et le conteneur meurt si
l'une d'elles manque ; les fichiers compose portent des gardes `:?`, de sorte que vous obtenez un
échec clair de `compose up` au lieu d'un conteneur qui démarre et s'arrête aussitôt.

- **Persistance.** Tout est en **montages bind relatifs** dans votre dossier de travail : il n'y a
  **plus aucun volume Docker nommé**. Le catalogue et l'état local sont dans `data/`
  (`catalog.db`, `local.db`), la config d'amuled dans `amule/`, les fichiers téléchargés dans
  `downloads/incoming` et `downloads/temp`. `docker compose down` n'y touche pas, et il n'y a plus
  de `-v` capable d'effacer le catalogue par accident : pour l'effacer, supprimez `data/` à la main.
  Corollaire voulu : `sqlite3 data/catalog.db` marche directement depuis l'hôte — c'est le rôle de
  `PUID`/`PGID`, qui gardent ces dossiers lisibles sans `sudo`.
- **Arrêter le nœud** : `docker compose down` (stack par défaut) ou
  `docker compose -f gluetun.compose.yml down` (stack VPN).
- **Mettre à jour** : re-tirez l'image puis relancez (ajoutez `-f gluetun.compose.yml` aux deux
  commandes pour la stack VPN) :
  ```bash
  docker compose pull
  docker compose up -d
  ```
- **Redémarrage de la machine hôte.** Le conteneur a `restart: unless-stopped` : il revient seul au
  boot de l'hôte (Docker doit démarrer en service système). **Aucune commande à relancer.**
  Vérifiez après reboot : `docker compose ps`. Si le conteneur est en `Restarting` ou en `Exited`,
  voir « Diagnostic après panne » ci-dessous.
- **Migration depuis un nœud 1.x** (deux images, quatre services, volumes nommés) : la procédure,
  manuelle et à faire une seule fois, est dans le
  [runbook de déploiement, annexe E](deployment.md#annexe-e-migrer-un-nœud-1x-vers-la-20). Ne lancez pas
  la 2.0 par-dessus un nœud 1.x sans
  l'avoir suivie : les volumes nommés ne sont pas lus par la nouvelle stack, et le nœud semblera
  avoir perdu son catalogue (les données, elles, sont toujours dans les volumes).

### Redémarrer un processus plutôt que le conteneur

Le cycle de vie a désormais **deux niveaux**. `docker compose up -d`, `restart` et `down` agissent
sur **tout le conteneur** — les trois processus d'un coup. Pour agir sur **un seul** processus,
adressez-vous à s6 dans le conteneur :

```bash
docker compose exec mulewatch s6-svstat /etc/services.d/amuled   # status
docker compose exec mulewatch s6-svc -r /etc/services.d/amuled   # restart
docker compose exec mulewatch s6-svc -d /etc/services.d/amuled   # stop
docker compose exec mulewatch s6-svc -u /etc/services.d/amuled   # start
```

Remplacez `amuled` par `amuleweb` ou `mulewatch` selon le besoin. Il n'y a plus de
`docker compose restart amuled` — il n'existe pas de service compose `amuled`.

Le crawler est le seul service dont le code de sortie est interprété. Son script `finish` s6 le
lit :

| Code de sortie | Ce que fait s6 | Pourquoi |
|---|---|---|
| `0` | redémarre le crawler **seul** | c'est le bouton de redémarrage de `/controls` dans la webui ; amuled garde ses sessions eD2k et Kad |
| tout autre | `s6-svscanctl -t`, qui couche **tout le conteneur** | un plantage — une config invalide avant tout — doit produire une boucle de backoff `restart: unless-stopped` visible, et non un crash-loop silencieux dans un conteneur qui a toujours l'air sain |

amuled et amuleweb sont supervisés normalement : ils plantent, s6 les relance, le backoff EC du
crawler absorbe le trou.

Le healthcheck du conteneur ne sonde qu'amuled, avec
`test "$(s6-svstat -u /etc/services.d/amuled)" = true`. **Piège si vous scriptez autour :**
`s6-svstat` sort en 0 même pour un service arrêté — il imprime `false` ; un code 1 signifie que
`s6-supervise` lui-même ne tourne pas. Testez la valeur imprimée, jamais le code de retour.

### Diagnostic après panne

Si le nœud tourne mais ne semble plus catalogue / télécharge plus rien. Les trois processus
partagent un seul flux de logs (`docker compose logs mulewatch`) ; chaque ligne est préfixée par le
service qui l'a émise.

| Symptôme | Premier check | Action |
|---|---|---|
| Le crawler tourne mais aucune nouvelle observation depuis > 1 h | `docker compose logs mulewatch --tail 100` | Cherchez « EC unavailable », « no servers » ou « cycle » récent. Si pas de cycle, amuled est probablement déconnecté du réseau (voir [runbook-troubleshooting](troubleshooting.md)). |
| Téléchargements bloqués en QUEUED | `docker compose logs mulewatch \| grep -i download` | Vérifier que amuled est en High-ID **ou** qu'il a des sources (sources directes nécessaires en Low-ID). |
| Un téléchargement fini n'apparaît pas dans `downloads/incoming` | `docker compose logs mulewatch --tail 100` | Voir la fiche [« Un fichier terminé n'apparaît jamais »](troubleshooting.md#un-fichier-terminé-napparaît-jamais-dans-downloadsincoming). |
| Un processus est mort sans emporter le conteneur | `docker compose exec mulewatch s6-svstat /etc/services.d/amuled` | `false` = arrêté : relancez-le avec `s6-svc -u` (cf. ci-dessus) et cherchez la cause dans les logs. |
| Le disque se remplit | `docker system df -v` puis `du -sh downloads/ data/` | Catalogue trop gros (voir Compaction) ou fichiers téléchargés accumulés : le crawler refuse de nouveaux téléchargements sous `download.min_free_bytes`, mais ne supprime jamais rien. |

Pour les symptômes inconnus, voir le [runbook de dépannage](troubleshooting.md).

### Planification disque

Ordres de grandeur **indicatifs** (à ajuster selon votre trafic eMule réel et la cardinalité de
vos cibles) :

- **`data/catalog.db`** : croissance lente, **~1 à 6 Go/an** sans compaction (chiffre estimé sur le
  trafic eMule 2026 ; ré-évaluer si vous activez un grand nombre de cibles). La compaction (cf.
  Outils de catalogue) ramène l'historique au-delà de 90 jours à un rollup journalier : taux de
  compression élevé.
- **`downloads/`** : les fichiers téléchargés, qui **s'accumulent sans borne** (rien ne les purge).
  Depuis le 2026-09-13, le crawler **mesure** vraiment le disque : il n'accepte un nouveau candidat
  que si `libre - reste à télécharger - taille du candidat` demeure au-dessus de
  `download.min_free_bytes` (10 Gio par défaut). C'est un **plancher**, pas un plafond : il bloque
  les nouveaux téléchargements quand le disque se tend, il n'efface rien. Le ménage dans
  `downloads/incoming` reste à votre charge. Le crawler ne fait qu'un `statvfs` sur `/downloads` :
  il n'ouvre jamais un fichier téléchargé. C'est aussi pourquoi `./downloads` est monté **en
  entier**, et non par ses deux sous-dossiers : sans cela `/downloads` serait la couche inscriptible
  du conteneur, et la mesure porterait sur le mauvais système de fichiers.
- **`amule/`** : qq Mo (amule.conf, server.met, nodes.dat, prefs).

Si votre VPS / NAS approche de saturation, lancez `du -sh downloads/ data/ amule/`
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

**Comment ça marche.** gluetun sait demander un **port forwarding** à votre fournisseur VPN : le
port joignable est celui du VPN, **tout le trafic reste derrière le tunnel**. Le crawler interroge
le serveur de contrôle de gluetun, et quand le port a changé, il redémarre amuled pour qu'il
écoute sur le nouveau.

Depuis la 2.0, **Docker n'intervient plus du tout** dans cette boucle : le socket Docker, le
service `docker-proxy` et les réseaux dédiés ont disparu. Le crawler et amuled sont deux processus
du même conteneur, donc le redémarrage est un `s6-svc -r /etc/services.d/amuled` local. Et sous la
stack VPN, mulewatch partage la pile réseau de gluetun (`network_mode: service:gluetun`), donc le
serveur de contrôle est sur `localhost`.

C'est aussi plus juste qu'avant : le port n'a jamais été re-bindable à chaud, donc le port-sync a
toujours eu besoin d'un redémarrage de **processus** ; il redémarrait un **conteneur** seulement
parce que le processus était hors de portée.

**Configuration, deux réglages solidaires :**

1. **VPN avec port forwarding** + `VPN_PORT_FORWARDING=on` dans `.env` (cherchez les fournisseurs
   marqués `PORT_FORWARDING: yes` dans la [liste gluetun](https://github.com/qdm12/gluetun-wiki/tree/main/setup/providers)).
2. Dans `crawler.yml` : basculez `port_sync.enabled: true` (le bloc est présent par défaut,
   `gluetun_control_url` pointant déjà sur `http://localhost:8000` ; réglage fin optionnel via les
   autres champs de la section).

Cela n'a de sens que sous `gluetun.compose.yml` : dans la stack par défaut, il n'y a pas de serveur
de contrôle gluetun sur `localhost:8000`, et le port-sync tournera dans le vide — la dégradation
étant tolérée (Low-ID, backoff, alerte de repli sur le canal *operations*), il n'arrêtera pas le
nœud pour autant.

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

## Métriques Prometheus

Le crawler expose un point d'accès Prometheus sur le port réglé par `observability.metrics.port`
dans `crawler.yml` (`9090` par défaut, `enabled: true` par défaut). **Aucun conteneur Prometheus ni
Grafana n'est livré avec la pile** : si vous voulez des tableaux de bord, faites pointer votre
propre Prometheus sur le crawler.

Ce port n'est pas publié sur l'hôte par défaut, et il n'y a plus de réseau interne partagé à
rejoindre — les réseaux `ec` et `egress` ont disparu avec la pile multi-services. Il reste donc une
seule route : **le publier vous-même**, en ajoutant `"9090:9090"` à la liste `ports:` du fichier de
pile que vous utilisez réellement — `compose.yml` sous le service `mulewatch`,
`gluetun.compose.yml` sous le service `gluetun` (mulewatch n'y a pas de réseau propre). Ne l'ajoutez
jamais à `base.compose.yml` : compose fusionne les `ports` de façon additive et ne sait pas retirer
une entrée apportée par un fragment, ce pourquoi le fragment n'en déclare aucune.

Traitez-le comme le port 8080 : **aucune authentification**, donc gardez-le hors de l'Internet
ouvert.

Exemple de `scrape_config` pour votre propre `prometheus.yml` :

```yaml
scrape_configs:
  - job_name: 'mulewatch'
    static_configs:
      - targets: ['node.example.lan:9090']   # l'hôte sur lequel vous avez publié 9090
```

Mettre `observability.metrics.enabled: false` coupe entièrement le point d'accès ; le crawl et la
webui ne sont pas affectés.

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
  temporelle ; la moyenne se dérive de somme/compte). À lancer **crawler arrêté** — soit tout le
  conteneur (`docker compose down`), soit le seul processus crawler
  (`docker compose exec mulewatch s6-svc -d /etc/services.d/mulewatch`), ce qui laisse amuled
  garder ses sessions eD2k / Kad pendant l'opération. Il **reconstruit vers une sortie neuve**
  (la sortie ne doit pas exister), puis l'opérateur permute. Coupure **alignée
  sur le jour UTC** : un jour ne serait-ce que partiellement dans la fenêtre reste intégralement brut
  (granularité au jour, pas 24 h glissantes). Ordre recommandé : **fusionner d'abord, compacter
  ensuite** (la compaction voit alors tous les nœuds et produit une seule ligne par fichier/jour).

  **Quand la lancer ?** Pas avant que `data/catalog.db` devienne gênant, repère pratique :
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

Un nœud publie **deux** surfaces web, et elles n'ont pas la même posture :

| Port | Ce que c'est | Authentification |
|---|---|---|
| **8080** | l'interface de catalogue mulewatch (cette section) | **AUCUNE, D'AUCUNE SORTE** |
| **4711** | amuleweb, l'interface propre à aMule | le mot de passe admin `WEBUI_PWD` |

Le port **8080 n'a aucune authentification, d'aucune sorte**. Quiconque l'atteint obtient le
catalogue, les POST de `/controls` qui modifient l'état et une console SQL en lecture seule.
`WEBUI_PWD` ne protège **que le 4711** — il ne fait rien pour le 8080. Mettez le 8080 derrière un
reverse proxy ou un VPN, ou gardez-le sur un réseau de confiance, et ne le posez pas sur l'Internet
ouvert.

La WebUI est une interface de **lecture seule** servie **en intra-processus** par le crawler (même
image, même conteneur, sur un thread dédié du processus `mulewatch`) et exposant le catalogue SQLite
via un serveur HTTP Starlette/Jinja2. Elle n'a **aucune authentification** : l'auth/TLS sont
délégués au reverse
proxy amont (nginx, Caddy, Traefik, etc.) que vous mettez devant. Elle ne modifie jamais les bases :
elle ouvre ses propres connexions SQLite en lecture seule (`mode=ro` + `PRAGMA query_only=ON`) via
son `ReaderProvider`, jamais une connexion en écriture.

### Lancer la WebUI

Rien de spécial à lancer : la WebUI est servie **en intra-processus** par le processus `mulewatch`,
donc elle démarre et s'arrête **avec lui**, sans service ni profil dédié. N'importe laquelle des
commandes de lancement du [Runbook de déploiement](deployment.md#5-lancer) la met en ligne, que le
téléchargement soit activé ou non.

```bash
# Stack sans VPN
docker compose up -d

# Stack VPN : idem
docker compose -f gluetun.compose.yml up -d
```

Pour la couper sans couper le crawl, mettez `webui.enabled: false` dans `crawler.yml` : cela ferme
toute la surface HTTP 8080 (le crawl, lui, continue). Pour couper le crawl en gardant amuled vivant,
c'est `docker compose exec mulewatch s6-svc -d /etc/services.d/mulewatch`.

### Routes disponibles

| Route | Description |
|---|---|
| `/` | Tableau de bord : couverture par cible (épisodes trouvés/manquants) |
| `/files` | Liste paginée des fichiers ; filtres `?target=`, `?tier=`, `?q=` |
| `/files/{ed2k_hash}` | Détail d'un fichier (observations, décisions, explication du matching) |
| `/targets/{target_id}` | Fichiers d'une cible (alias de `/files?target=`) |
| `/node` | État du nœud CRAWLER : `node_id` + entrées du `scheduler_state` (last_full_cycle_at, etc.). N'expose PAS l'état réseau amuled (l'EC n'est pas joignable depuis le webui). |
| `/controls` | Contrôles d'exécution : forcer une passe de recherche maintenant, mettre en pause / reprendre la surveillance, redémarrer le service (sortie de processus **propre**, code 0 : s6 relance le seul processus `mulewatch`, le conteneur reste debout et amuled garde ses sessions eD2k / Kad). Ce sont des POST qui **modifient l'état**, sans jeton CSRF ni authentification, par conception. |
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
compose (port publié) qui gouverne l'accès, pas une adresse de bind applicative. La seule
variable d'environnement en jeu est `WEBUI_PORT`, et uniquement côté hôte.

| Réglage | Où | Valeur par défaut | Rôle |
|---|---|---|---|
| `catalog_db_path` | `crawler.yml` | `/data/catalog.db` | Base catalogue, lue en lecture seule par la WebUI (= `data/catalog.db` côté hôte) |
| `local_db_path` | `crawler.yml` | `/data/local.db` | Base état local, lue en lecture seule par la WebUI (= `data/local.db` côté hôte) |
| `webui.amule_url` | `crawler.yml` | `http://localhost:4711` | Cible du lien « aMule » dans la navigation. À changer **uniquement** si un reverse proxy est devant le 8080 : c'est le navigateur qui résout cette URL, pas le conteneur. |
| `WEBUI_PORT` | `.env` (env) | `8080` | Port **publié côté hôte** dans le mapping compose `"${WEBUI_PORT:-8080}:8080"` (hôte:conteneur). Ne change PAS le port d'écoute interne. |
| `AMULEWEB_PORT` | `.env` (env) | `4711` | Idem pour amuleweb (`"${AMULEWEB_PORT:-4711}:4711"`), l'autre surface web. |

### Exposition derrière un reverse proxy

La WebUI n'a ni TLS ni authentification : mettez un reverse proxy devant si elle est accessible
sur le réseau. Exemple minimal avec Caddy, en pointant sur le port publié par le nœud :

```caddyfile
webui.example.com {
    basicauth /* {
        alice $2a$14$...  # bcrypt généré par caddy hash-password
    }
    reverse_proxy node.example.lan:8080
}
```

Si vous faites cela, pensez à `webui.amule_url` dans `crawler.yml` : le lien « aMule » de la
navigation est résolu par le navigateur, donc `http://localhost:4711` ne veut plus rien dire pour
un visiteur distant. Pointez-le sur l'hôte réel (ou sur un second `reverse_proxy` : amuleweb, lui,
a bien un mot de passe, `WEBUI_PWD`).

> **Garantie lecture seule de la WebUI.** Servie **en intra-processus**, la WebUI lit les mêmes
> `data/catalog.db` et `data/local.db` que le crawler (montés en **lecture-écriture** pour lui),
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
IMAGE=ghcr.io/geoffreycoulaud/mulewatch:latest
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

> **Deux noms qui ne bougent pas, exprès.** Le fichier de claims reste
> `security/crawler.vex.openvex.json` et les catégories SARIF gardent leur suffixe `-crawler`
> (`grype-crawler`, `vex-image-claims-crawler`, `vex-stale-claims-crawler`) : renommer une
> catégorie Code scanning rend ses findings existants orphelins. Seul le *produit* VEX a suivi le
> renommage de l'image.
>
> **L'ancien paquet GHCR `mulewatch-crawler` ne doit pas être supprimé** : il reste en 1.x et
> c'est le chemin de retour arrière pendant la migration.

---

## Limites connues / follow-ups

- **Migrations : le tri se fait en mémoire, sans plafond (2026-07-16)** : les migrations SQLite
  s'appliquent avec `temp_store=MEMORY` (`connection.py`, restauré juste après). Motif : construire
  un index déborde le tmpfs de 64 Mo de `/tmp` et échoue en `SQLITE_FULL`, ce qui fait boucler le
  crawler au démarrage (constaté sur le node réel avec la migration 0004). Le remède alternatif,
  régler l'espace temporaire, vit dans le compose de l'opérateur : il peut être oublié au moment
  d'une montée d'image, et cet oubli casse le node ; l'image porte donc son propre remède. **Risque
  résiduel accepté** : le trieur en mémoire de SQLite ne se vide jamais et n'est borné ni par
  `cache_size` ni par autre chose que le nombre de lignes (environ 116 octets par ligne). Repère
  mesuré : 1,19 M d'observations donnent un pic d'environ 150 Mo, soit un plafond vers **4,5 M de
  lignes** — chiffre mesuré à l'époque où la limite était `mem_limit: 512m`. **Elle est passée à
  `2g`** depuis le passage à un conteneur unique (trois processus à loger, valeur encore à régler
  sur un nœud réel), donc le plafond réel est plus haut ; il n'a pas été re-mesuré. Une fois la
  limite atteinte, le conteneur est tué par le noyau (exit 137, journal vide) au lieu de
  produire une erreur lisible : voir la fiche
  [« Un conteneur redémarre en boucle »](troubleshooting.md#un-conteneur-redémarre-en-boucle). Ne
  pas « corriger » sans rouvrir la décision. À surveiller : `file_observations` croît sans borne, et
  c'est une **future** migration triant cette table qui pose le risque, pas 0004 (ponctuelle, déjà
  passée).
- **Durcissement conteneur, décisions au dossier (2026-06-17, mise à jour 2026-06-29, réduite
  2026-09-13, INVERSÉE pour le crawler 2026-09-16)** : le bac à sable gVisor (`runsc`) optionnel a
  été abandonné pour cause de YAGNI, et la blocklist seccomp par enfant ainsi que les rlimits ont
  quitté le projet le 2026-09-13, avec l'enfant d'analyse qu'elles confinaient. L'image à conteneur
  unique a ensuite réduit ce que le plancher portable peut tenir. PID 1 doit être root — il crée
  l'utilisateur `amule` à partir de `PUID`/`PGID`, fait le `chown` des bind mounts et écrit
  `amule.conf` — donc **`user:`, `read_only:` et `cap_drop: ALL` ne s'appliquent plus à aucun
  service livré** ; chaque service abandonne ses privilèges avec `setpriv` à la place. C'était
  délibéré, avec l'accord signé de l'opérateur (spec
  [`2026-09-16-single-container-embedded-amule.md`](../specs/2026-09-16-single-container-embedded-amule.md)
  §9) : **le crawler est descendu au niveau de confinement d'amuled, plutôt qu'amuled ne monte à
  celui du crawler.** Ce qui reste, et que les fichiers compose doivent conserver, c'est
  `security_opt: no-new-privileges:true`, `pids_limit: 512` et `mem_limit: 2g` — les deux derniers
  relevés pour trois processus au lieu d'un, et tous deux encore à régler sur un nœud réel. **Pas
  encore validé sur matériel réel : `no-new-privileges` conjugué à `setpriv`.** Plus rien dans le
  crawler ne lance de sous-processus sur une entrée non fiable, et rien n'ouvre jamais un fichier
  téléchargé. L'isolation au niveau noyau au-delà de cela reste **explicitement hors périmètre** :
  `net=none`, bwrap et de vrais namespaces de montage en lecture seule exigent chacun soit
  `CAP_SYS_ADMIN`, soit des user namespaces non privilégiés (non portables : ils dépendent d'un
  sysctl de l'hôte et entrent en conflit avec le profil seccomp par défaut de Docker).
- **amuled n'est plus un conteneur tiers (2026-09-16)** : c'est un processus de notre propre image,
  donc la dérogation du 2026-06-17 qui l'exemptait de notre durcissement n'a plus rien à exempter —
  tout le service partage la posture ci-dessus. Le **risque résiduel est accepté, et il est
  désormais plus large** : la compromission de l'un quelconque des trois processus atteint les bind
  mounts `downloads/incoming` et `downloads/temp`, `data/` (le catalogue) **et** `amule/`. Ne
  « corrigez » pas cela sans rouvrir la décision au dossier.
- **port-sync, validation réelle** : la boucle est construite ; sa validation **bout-en-bout**
  (port-check High-ID réel derrière le VPN) se fait via un déploiement réel. Elle passe désormais
  par un `s6-svc -r` sur amuled dans le même conteneur, sans Docker ni socket : plus simple, mais
  encore jamais éprouvée sur du matériel réel.
- **Redémarrage d'un nœud 1.x : le backoff de recherche persisté repart de zéro, une fois.** Il est
  indexé sur le nom d'instance d'amuled, qui était lu dans le YAML (`amule-1`) et est maintenant une
  constante de code (`amuled`) ; il en va de même des clés de l'état d'ordonnancement. Les anciennes
  lignes restent dans `local.db` sans être relues. Sans conséquence pour une 2.0.0 cassante, mais
  autant ne pas être surpris.
- **Complétion de téléchargement, validation en conditions réelles** : la chaîne est **confirmée
  par la lecture des sources amont d'amuled** (voir
  [`docs/reference/2026-06-17-amuled-completion-behavior.md`](../reference/2026-06-17-amuled-completion-behavior.md))
  et par un **transfert réel sur un nœud de production le 2026-09-11**, mais il n'existe **aucun
  test bout-en-bout sur un transfert réel** (cette suite e2e a été abandonnée, voir le guide des
  tests). Le décodage de `shared_files()` face à un amuled réel, lui, **est** couvert par
  `download_integration`.

  Mécanique : à la complétion, amuled déplace le fichier dans son **IncomingDir** et ne bascule le
  statut en « terminé » qu'ensuite (pas de course). Le crawler détecte la complétion comme
  **« partagé ET absent de la file de téléchargement »** (amuled partage automatiquement un fichier
  terminé, mais il partage aussi les fichiers partiels, donc c'est la file qui les sépare, voir
  CORRECTION 2026-09-11 dans la référence). Il enregistre alors l'état et notifie : depuis le
  2026-09-13, rien ne déplace le fichier, donc l'ancienne promotion depuis la quarantaine et sa
  gestion des collisions de noms ont disparu, et avec elles les contraintes sur un volume de
  quarantaine partagé et sur un système de fichiers Linux. Ce qui tient toujours : **aucune
  catégorie amuled** ne doit rediriger la destination, et amuled doit être **dédié** au crawler avec
  un **petit ensemble partagé**.

  **Conséquence de l'abandon de l'étape de promotion, pas encore mesurée** : les fichiers terminés
  restent désormais indéfiniment dans `IncomingDir`, donc la liste des fichiers partagés d'amuled
  croît sans borne, et elle est lue à chaque cycle de téléchargement. Sur un nœud de longue durée
  comptant beaucoup de téléchargements terminés, attendez-vous à une détection de complétion plus
  lente. Le ménage dans `downloads/incoming` est à la charge de l'opérateur.

- **WebUI (lecture seule)** : **point clos**. La WebUI est désormais servie **en intra-processus**
  par le crawler (plus de conteneur séparé, donc plus de montage inter-conteneurs). La garantie
  lecture seule repose sur `mode=ro` + `PRAGMA query_only=ON` ; l'ancien montage Docker `:ro` WAL
  est caduc. Voir section « WebUI » plus haut et
  [`docs/reference/2026-06-22-webui-wal-readonly.md`](../reference/2026-06-22-webui-wal-readonly.md).
- **Hub central / rétention** : non planifiés à ce stade.
