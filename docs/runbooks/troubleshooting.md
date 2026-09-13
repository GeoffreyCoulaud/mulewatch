# Runbook de dépannage : mulewatch

Chaque fiche suit le même format : **symptôme (ce que vous voyez à l'écran) : cause : solution
(commandes à copier-coller)**. Le runbook est en deux niveaux.

- **Le déploiement bloque (premiers pas)** : juste en dessous. Les blocages du tout premier
  déploiement, dans l'ordre de la [voie royale](deployment.md). Chaque fiche prolonge un **Point de
  contrôle** du guide : ouvrez celle vers laquelle le guide vous renvoie.
- **Diagnostics avancés (opérateurs)** : plus bas. Téléchargement, High-ID/port-sync, stockage &
  droits, récupération après panne. Certaines de ces sections demandent une
  familiarité Linux/Docker et le signalent à leur ouverture.

Pour *monter* un nœud, voir le [runbook de déploiement](deployment.md) ; pour le *régler*, le
[runbook d'administration](administration.md).

---

## Le déploiement bloque (premiers pas)

Ces fiches correspondent aux **Points de contrôle** du guide de déploiement, dans l'ordre. Presque
tout se répare sans expertise : lire un journal, corriger une ligne, relancer une commande.

> **Où lancer ces commandes.** Toutes les commandes ci-dessous se lancent depuis votre **dossier de
> travail** (le dossier qui contient `compose.yaml`, créé à
> [l'étape 3 du guide](deployment.md#3-create-your-working-folder)). Les chemins sont donc
> relatifs : `.env`, `config/...`.

### Docker introuvable ou compose v1

- **Symptôme.** `docker compose version` répond `command not found`, ou affiche une version `1.x`
  du vieil outil `docker-compose` (écrit avec un tiret).
- **Cause.** Docker n'est pas installé, n'est pas démarré, ou votre système ne fournit que l'ancien
  `docker-compose` v1 (fréquent dans les paquets des distributions Linux). mulewatch a besoin de la
  commande moderne `docker compose` (en deux mots, v2).
- **Solution.**
  1. Installez Docker **depuis la documentation officielle**, jamais depuis un tutoriel tiers :
     - Windows ou macOS : Docker Desktop, <https://docs.docker.com/get-started/get-docker/>.
     - Serveur Linux : Docker Engine, <https://docs.docker.com/engine/install/>.
  2. Sous Windows ou macOS, **lancez l'application Docker Desktop** et attendez qu'elle affiche
     qu'elle tourne : seules les commandes qui interrogent le moteur (comme `docker ps` ou
     `docker compose up`) échouent tant qu'il est arrêté ; `docker compose version`, elle, répond
     même moteur éteint (c'est une commande côté client).
  3. Revérifiez :
     ```
     docker compose version
     ```
     Vous devez lire `Docker Compose version v2.x.x` (un numéro plus récent convient aussi). Si vous
     ne voyez toujours qu'un `docker-compose` v1, installez Docker Engine (v2) comme ci-dessus : la
     commande en deux mots est indispensable.
- **Retour au guide.** [Étape 2 : Installer Docker](deployment.md#2-install-docker).

### Docker est installé mais ne répond pas

- **Symptôme.** Une commande qui interroge le moteur (`docker ps`, `docker compose up -d`...)
  s'arrête avec un message de connexion refusée. Sous **Windows** :
  ```
  error during connect: ... pipe/docker_engine ... The system cannot find the file specified
  ```
  Sous **macOS / Linux** :
  ```
  Cannot connect to the Docker daemon
  ```
  En revanche, `docker compose version` répond normalement : c'est une commande côté client, qui ne
  parle pas au moteur.
- **Cause.** Le moteur Docker n'est pas démarré : sous Windows/macOS, l'application Docker Desktop
  n'est pas lancée (ou pas encore prête) ; sur un serveur Linux, le service Docker est arrêté.
- **Solution.**
  1. Sous **Windows ou macOS** : lancez **Docker Desktop** et attendez que son icône passe au vert
     (le moteur est prêt).
  2. Sur un **serveur Linux** : démarrez le service Docker.
     ```
     sudo systemctl start docker
     ```
  3. Re-testez `docker ps` : le succès est un tableau d'en-têtes de colonnes (même vide).
     ```
     docker ps
     ```
- **Retour au guide.** [Étape 2 : Installer Docker](deployment.md#2-install-docker) et
  [étape 5 : Lancer](deployment.md#5-start-it).

### Une valeur change-me est restée dans .env

- **Symptôme.** Au Point de contrôle de l'étape 4, la commande de vérification **affiche une ligne**
  au lieu de ne rien afficher :
  ```
  grep -E 'AMULE_EC_PASSWORD=change-me' .env
  ```
  (elle imprime la ligne fautive). Symptôme possible plus tard : le crawler journalise une erreur
  d'authentification.
- **Cause.** Le mot de passe obligatoire est encore la valeur d'exemple `change-me` : vous avez
  oublié la ligne, ou édité la mauvaise.
- **Solution.**
  1. Rouvrez le fichier (son nom commence par un point ; le plus simple est de l'éditer au
     terminal). Sous **macOS / Linux** :
     ```
     nano .env
     ```
     Sous **Windows** :
     ```
     notepad .env
     ```
     Remplacez la valeur après le `=` sur la ligne signalée : `AMULE_EC_PASSWORD` (au moins 12
     caractères). Dans `nano`, enregistrez avec **Ctrl+O** puis **Entrée**,
     quittez avec **Ctrl+X** ; dans le Bloc-notes, enregistrez avec **Ctrl+S**.
  2. Revérifiez : la commande de contrôle ne doit **plus rien afficher**.
     ```
     grep -E 'AMULE_EC_PASSWORD=change-me' .env
     ```
     Sous **Windows (PowerShell)** :
     ```
     Select-String -Path .env -Pattern 'AMULE_EC_PASSWORD=change-me'
     ```
  3. Relancez la pile **depuis votre dossier de travail** : `up -d` ne recrée que ce qui a changé.
     ```
     docker compose up -d
     ```
- **Ce qui n'est PAS un problème.** Il reste normalement d'autres `change-me` dans le fichier (la
  ligne de commentaire, ou `WIREGUARD_PRIVATE_KEY` réservé au VPN de l'annexe A) : la commande de
  contrôle ci-dessus les **ignore** exprès. Seul `AMULE_EC_PASSWORD` compte pour la voie royale.
- **Retour au guide.** [Étape 4 : votre mot de passe](deployment.md#4-your-password).

### Un conteneur redémarre en boucle

- **Symptôme.** `docker compose ps` montre un service en **`Restarting`** (ou `Exited`) au lieu de
  `Up`.
- **Diagnostic (toujours le même).** Regardez d'abord *quel* service, puis son journal. Depuis votre
  dossier de travail :
  ```
  docker compose ps
  ```
  ```
  docker compose logs <service>
  ```
  (remplacez `<service>` par le nom du conteneur en boucle, par exemple `crawler` ou `amuled`). La
  dernière page du journal dit presque toujours pourquoi. Causes fréquentes :
- **Mot de passe EC absent ou incohérent (`crawler`).** Le journal du crawler se termine par une
  erreur d'authentification (`EcAuthError`, mot de passe EC refusé) et le conteneur redémarre. Sur
  la voie royale, amuled et le crawler partagent la **même** variable `AMULE_EC_PASSWORD` : ce cas
  vient donc d'un `AMULE_EC_PASSWORD` resté vide ou `change-me`, ou d'un mot de passe édité à la main
  dans `config/crawler/crawler.yml`. Corrigez `.env` (voir la fiche
  [« Une valeur change-me est restée dans .env »](#une-valeur-change-me-est-restée-dans-env)), puis
  `docker compose up -d` depuis votre dossier de travail.
- **Journal vide juste après une montée d'image (`crawler`).** Si le crawler boucle en laissant un
  journal **vide** (pas d'erreur, pas de traceback), ce n'est pas une panne applicative : le noyau a
  tué le conteneur, donc rien n'a pu être écrit. À vérifier :
  ```
  docker inspect --format '{{.State.OOMKilled}} {{.State.ExitCode}}' mulewatch-crawler-1
  ```
  `true 137` confirme le manque de mémoire. Cause : au premier démarrage qui suit une montée
  d'image, le crawler applique les migrations SQLite en attente, et une migration qui construit un
  index **trie en mémoire**. Ce tri grossit avec la table indexée (environ 116 octets par ligne) et
  rien ne le plafonne, ni `cache_size` ni la limite du conteneur : sur un gros catalogue le pic peut
  dépasser `mem_limit` (512 Mo par défaut, soit environ 4,5 millions de lignes). Remède : relevez
  temporairement le `mem_limit` du service `crawler` dans `base.compose.yml`, `docker compose up -d`,
  laissez le premier démarrage aller à son terme (le pic est ponctuel : une fois l'index construit il
  est maintenu au fil de l'eau, sans tri), puis remettez la valeur d'origine. À titre de repère, la
  migration 0004 construit son index sur 1,19 million d'observations en environ 5 s pour un pic
  d'environ 150 Mo.
- **Retour au guide.** [Étape 5 : Lancer](deployment.md#5-start-it) et
  [étape 6 : Voir votre nœud](deployment.md#6-see-your-node).

### Le port est déjà pris

- **Symptôme.** Au lancement, `docker compose up -d` s'arrête avec un message du type :
  ```
  Error ... failed to bind host port for 0.0.0.0:8080: address already in use
  ```
  Le mot-clé est **`bind: address already in use`**. Un autre programme occupe déjà ce port sur
  votre machine.
- **Cause.** mulewatch publie des ports sur l'hôte ; l'un d'eux est déjà utilisé (un autre service,
  une ancienne pile...). Le numéro dans le message vous dit lequel :

  | Port par défaut | Variable à changer dans `.env` | Sert à |
  |---|---|---|
  | `8080` | `WEBUI_PORT` | le catalogue web (servi en intra-processus par le service `crawler`) |
  | `4662` | `LISTEN_PORT` | le port eMule (toujours publié ; surtout utile en High-ID, annexe C) |

- **Solution.** Ouvrez `.env`, donnez au port concerné une valeur libre (par exemple
  `WEBUI_PORT=8090`), enregistrez, puis relancez depuis votre dossier de travail :
  ```
  nano .env
  ```
  ```
  docker compose up -d
  ```
  Pensez ensuite à ouvrir la nouvelle adresse dans le navigateur (par exemple
  <http://localhost:8090> au lieu de 8080).
- **Retour au guide.** [Étape 5 : Lancer](deployment.md#5-start-it).

### amuled ne se connecte à rien

- **Symptôme.** Le crawler boucle bien (vous voyez des lignes `cycle ...`) mais reste indéfiniment
  en `effective_coverage=blind`, avec des avertissements d'injoignabilité : amuled n'atteint ni les
  serveurs eD2k ni le réseau Kad, donc aucune source.
- **D'abord, patientez : au premier démarrage c'est normal.** amuled amorce **tout seul** sa liste
  de serveurs eD2k (`server.met`) et de nœuds Kad (`nodes.dat`) via du DNS et du HTTPS sortant, ce
  qui prend **1 à 3 minutes**. Pendant ce temps, `effective_coverage=blind` et quelques
  avertissements sont attendus : cela se résorbe seul. Vous n'avez **aucun serveur à ajouter**.
- **« Low-ID » n'est pas une panne.** Si les logs d'amuled (`docker compose logs amuled`) mentionnent
  Low-ID, c'est l'état **normal** par défaut : recherche, catalogage et téléchargement fonctionnent ;
  seule la joignabilité est sous-optimale. Devenir High-ID est optionnel (annexe C du guide).
- **Si ça dure au-delà de quelques minutes.**
  - **(a) Vérifiez la sortie Internet de la machine** : amuled a besoin du port 443 sortant pour
    l'amorçage.
  - **(b) Si vous avez ajouté un VPN (annexe A)**, c'est presque toujours le tunnel `gluetun` qui
    n'est pas monté : amuled **partage le réseau de gluetun**, donc tant que le tunnel est down,
    amuled n'a aucune sortie. Vérifiez gluetun *avant* amuled (depuis votre dossier de travail,
    avec le `-f gluetun.compose.yml` de l'annexe A) :
    ```
    docker compose -f gluetun.compose.yml logs gluetun
    ```
    Tunnel sain : une ligne `[gluetun] [vpn] connected` et une IP publique VPN
    (`You are running on the public IP address ...`, pas la vôtre). Tunnel cassé : `cannot connect
    to ...` puis `retrying in N seconds`. Corrigez alors le VPN (clé WireGuard,
    `VPN_SERVICE_PROVIDER`, `SERVER_COUNTRIES` dans `.env`), puis, une fois gluetun « connected »,
    redémarrez amuled :
    ```
    docker compose -f gluetun.compose.yml restart amuled
    ```
  - **(c) Image amuled inattendue.** Le projet est testé avec `ngosang/amule:3.0.0-1`. Une image
    `latest` ou `2.3.3-*` casse l'amorçage du premier run **sans erreur évidente**. Ce point est
    détaillé dans la fiche opérateur
    [« amuled ne se connecte à aucun serveur ni réseau »](#amuled-ne-se-connecte-à-aucun-serveur-ni-réseau-image--tunnel).
- **Retour au guide.** [Étape 6 : Voir votre nœud](deployment.md#6-see-your-node).

### La webui reste vide

Deux situations très différentes se cachent derrière « la webui est vide » :

- **La page se charge, mais le tableau est vide.** C'est **normal**, surtout les premières heures.
  Le catalogue se remplit au fil des recherches ; certaines cibles rares (le principe même du lost
  media) peuvent mettre des jours à réapparaître. **Ce n'est pas une panne.** Vérifiez plutôt que le
  nœud *vit*, en regardant les cycles du crawler (depuis votre dossier de travail) :
  ```
  docker compose logs crawler
  ```
  Vous devez y voir des lignes `cycle ...` jusqu'à `cycle 0 done`. Si oui, tout va bien : laissez
  tourner. Si le crawler reste `effective_coverage=blind`, voir
  [« amuled ne se connecte à rien »](#amuled-ne-se-connecte-à-rien).
- **La page ne se charge pas du tout** (connexion refusée, page inaccessible) : là c'est un vrai
  problème. La webui est servie **en intra-processus** par le service `crawler` (il n'y a pas de
  service `webui` séparé) ; vérifiez d'abord que le service `crawler` tourne :
  ```
  docker compose ps
  ```
  S'il n'est pas `Up`, voir [« Un conteneur redémarre en boucle »](#un-conteneur-redémarre-en-boucle).
  S'il est `Up` mais la page reste inaccessible, le port est peut-être remappé ou occupé (voir
  [« Le port est déjà pris »](#le-port-est-déjà-pris)) : confirmez l'adresse, par défaut
  <http://localhost:8080> (sur un serveur distant, remplacez `localhost` par son IP).
- **Retour au guide.** [Étape 6 : Voir votre nœud](deployment.md#6-see-your-node).

---

## Diagnostics avancés (opérateurs)

Les sections qui suivent vont plus loin que le premier déploiement : téléchargement, High-ID,
stockage, récupération. La plupart restent accessibles (lecture de logs, redémarrage de
service) ; **High-ID/port-sync et Stockage & droits exigent une familiarité Linux/Docker** et le
signalent à leur ouverture. Si vous bloquez sur une étape qui dépasse votre confort, l'option de
repli sûre est presque toujours de *repartir d'un volume propre* (voir « Récupération après panne »
plus bas) : vous perdez le catalogue accumulé mais vous redémarrez d'un état connu.

---

## Démarrage & réseau

### amuled ne se connecte à aucun serveur ni réseau (image / tunnel)

- **Cause la plus fréquente.** Au tout premier run, amuled doit amorcer sa liste de serveurs eD2k
  (`server.met`) et de nœuds Kad (`nodes.dat`) en faisant du DNS + HTTPS sortant (443) **à travers le
  VPN**. Si gluetun n'est pas encore monté, ou si la sortie Internet est bloquée à ce moment, rien ne
  s'amorce et amuled reste sans serveurs ni nœuds.
- **Solution.** Vérifiez d'abord l'état du tunnel gluetun, *avant* amuled :
  ```bash
  docker compose logs gluetun     # le tunnel doit être « up » et afficher une IP publique VPN
  ```

  **Ce que vous devez voir (tunnel sain) :**
  ```
  [gluetun] [main] Listening on 0.0.0.0:8000
  [gluetun] [main] You are running on the public IP address W.X.Y.Z   ← IP VPN (pas la vôtre !)
  [gluetun] [vpn] connected
  ```
  **Symptômes d'un tunnel cassé :**
  ```
  [gluetun] [vpn] cannot connect to ...   ← VPN provider/clé refusée
  [gluetun] [main] retrying in N seconds
  ```
  amuled **partage le réseau de gluetun** : tant que le tunnel est down, amuled n'a aucune sortie. Si
  le tunnel ne monte pas, corrigez le VPN (clé WireGuard, fournisseur, `SERVER_COUNTRIES`) puis
  relancez ; une fois gluetun « up », redémarrez amuled : `docker compose restart amuled`.
- **Autre cause : image amuled dérivée du pin.** Le projet est **testé avec `ngosang/amule:3.0.0-1`
  (validé en juin 2026)**. Les versions ≥ 3.0.0 supportent l'auto-amorçage ; une image `latest` ou
  `2.3.3-*` casse l'amorçage du premier run **sans erreur évidente**. Vérifiez l'image utilisée :
  ```bash
  docker compose -f gluetun.compose.yml images amuled
  # Vous devez voir : ngosang/amule:3.0.0-1
  ```
  Si vous voyez `latest` ou `2.3.3-*`, fixez la version dans `base.compose.yml` puis re-pullez.
  *(Si une version 4.x sort dans le futur, ré-évaluer la compatibilité avant migration : ce projet
  n'a été éprouvé qu'avec 3.0.0-1.)*

### Le crawler refuse de démarrer : « environment variable '…' referenced but not set »

- **Symptôme.** `docker compose logs crawler` affiche
  `Invalid config, refusing to start: … : environment variable 'AMULE_EC_PASSWORD' referenced but not set`,
  alors que la variable est bien renseignée dans `.env`.
- **Cause.** Compose ne lit `.env` que pour substituer les `${...}` **dans les fichiers compose**.
  Le crawler, lui, interpole les `${VAR}` de `crawler.yml` depuis **son propre** environnement de
  conteneur. Une variable référencée dans `crawler.yml` doit donc être injectée explicitement dans
  le service `crawler` (bloc `environment:` de `base.compose.yml`), sinon le process ne la
  voit pas. `AMULE_EC_PASSWORD` y est câblé par défaut.
- **Solution.** Si vous ajoutez un **nouveau** `${VAR}` dans `crawler.yml` (typiquement en activant
  une URL de notification `notifications[].url: "discord://${DISCORD_WEBHOOK_ID}/…"`), ajoutez la
  même variable au bloc `environment:` du service `crawler` :
  ```yaml
  # base.compose.yml
  crawler:
    environment:
      AMULE_EC_PASSWORD: ${AMULE_EC_PASSWORD:?}
      DISCORD_WEBHOOK_ID: ${DISCORD_WEBHOOK_ID:?}     # ← nouvelle ligne par secret ajouté
      DISCORD_WEBHOOK_TOKEN: ${DISCORD_WEBHOOK_TOKEN:?}
  ```
  Le mapping est **explicite** (et non `env_file: .env`) pour le moindre privilège : le crawler n'a
  pas à voir la clé WireGuard ni les autres secrets du déploiement.

### Le statut « Low-ID » apparaît dans les logs

- **Ce n'est pas une panne.** Low-ID est l'**état normal** par défaut : recherche, catalogage et
  téléchargement fonctionnent ; seule la joignabilité est sous-optimale (moins de sources directes).
- **Pour passer en High-ID** (optionnel), voir « High-ID (optionnel) » dans le
  [runbook d'administration](administration.md).

---

## Downloads

### A finished file never shows up in `downloads/incoming`

- **What happens normally.** amuled writes a finished file straight into its `IncomingDir`, which the
  compose stacks bind-mount to `./downloads/incoming` in your working folder. The crawler detects the
  completion from amuled's shared-files list (the hash is shared **and** has left the download
  queue), flips the download to `completed` and notifies. It never moves, opens or inspects the
  file.
- **Check the state the crawler sees first.** If the webui still shows the download as `downloading`,
  it simply has not finished: nothing is broken.
- **If the crawler says `completed` but the folder is empty**, amuled put the file somewhere else.
  Two causes, in order:
  1. **An amuled category is redirecting the destination.** In `amule.conf` (or through the GUI), no
     category may carry a non-empty `Path=` that sends the finished file outside `IncomingDir`.
  2. **`IncomingDir` does not point at the bind-mounted path.** The stack binds `./downloads` to
     `/downloads`, the image's own default location, so a fresh node needs no amuled setting at all.
     A node created before 2026-09-13 may carry a hand-edited `amule.conf` pointing elsewhere: the
     image writes that file only when it is absent, so an old value survives every restart. Check it
     from inside the container:
     ```bash
     docker compose exec amuled sh -c 'grep -E "^(Incoming|Temp)Dir" /home/amule/.aMule/amule.conf'
     ```
- **Keep amuled dedicated to the crawler.** `shared_files()` is queried on every download cycle, so
  do not point this amuled at a large pre-existing shared library: completion detection gets slower
  and noisier. Background and sources:
  [`reference/2026-06-17-amuled-completion-behavior.md`](../reference/2026-06-17-amuled-completion-behavior.md)
  (its constraints 1 and 2, about a shared quarantine volume, no longer apply: the quarantine step
  was removed on 2026-09-13).

### A download is stuck, then turns `failed`

- **What the crawler does.** Every download cycle stamps `last_seen_at` for each tracked hash that
  amuled reports, either in its download queue or in its shared files. A download still `queued` or
  `downloading` that amuled has not reported for `download.lost_after_seconds` (24 h by default) is
  marked `failed`, with a log line: `hash=... unseen by amuled for 86400.0s: marked failed`.
- **Why this is safe.** An entry stays in amuled's queue even with **zero sources**: it goes
  dormant, but it does not disappear. So a lost-media download sitting at 0 % for months is never
  at risk. Absence from amuled means the entry was actually removed, or the file finished and was
  moved out of `IncomingDir` before the next poll.
- **`failed` is not final.** amuled remains the authority: if the hash reappears in its queue, the
  crawler puts the download back to `downloading`; if it appears in the shared files, the download
  completes and the notification fires. Check `downloads/incoming` before assuming the file is lost.
- **To retry one by hand**, delete its row: `is_downloaded()` is state-blind, so a `failed` row
  keeps blocking the automatic re-queue on purpose. There is no webui control for this and the SQL
  console is read-only, so it is a manual write on `local.db`. Stop the crawler first (single
  writer by doctrine), then use the `--entrypoint python` recipe from *Outils de diagnostic* below
  against the `local-db` volume:
  ```bash
  docker compose stop crawler
  docker compose run --rm --entrypoint python crawler -c \
    "import sqlite3; db = sqlite3.connect('/data/local/local.db', autocommit=True); \
     db.execute('DELETE FROM downloads WHERE ed2k_hash = ?', ('<hash>',))"
  docker compose start crawler
  ```
  The next cycle re-queues it from the catalogue decision, provided the file still matches a target
  that is not `complete`.
- **If nothing at all is being downloaded**, check the disk floor before suspecting the TTL: a log
  line `candidate hash=... -> skip_disk_cap (skipped/deferred)` means free space minus what amuled
  still has to fetch would fall below `download.min_free_bytes`. `output directory unmeasurable`
  instead means the read-only `./downloads` mount is missing from your compose file.

---

## High-ID / port-sync

> ⚠️ **Prérequis pour ce diagnostic** : connaissance Docker (sockets, groupes Unix). Si vous n'êtes
> pas à l'aise avec ces concepts, le port-sync n'est probablement pas la bonne voie pour vous :
> envisagez la **Route B** (port-forward manuel sur votre box) ou restez en **Low-ID** (qui marche
> très bien). Voir [runbook d'administration § High-ID](administration.md#high-id-optionnel--devenir-joignable).

### Le port-sync reste inopérant (toujours Low-ID alors qu'il est activé)

Plusieurs causes, à vérifier dans cet ordre :

- **`docker-proxy` qui redémarre en boucle (`socket not available … connect: permission denied`).**
  Le proxy doit tourner en **root** pour lire le socket Docker bind-monté (`root:root` sous Docker
  Desktop, `root:docker` sous Docker natif, root est propriétaire dans les deux cas). L'image
  `wollomatic/socket-proxy` est buildée `USER 65534`, donc le compose **doit** poser `user: "0:0"`
  explicitement (`gluetun.compose.yml`) : sans cette ligne, le proxy tourne en `nobody` →
  `permission denied` → boucle. Si vous voyez ce symptôme, vérifiez que `user: "0:0"` est bien
  présent. **Rootless** reste hors de portée (socket sous `$XDG_RUNTIME_DIR`, accès par UID ;
  détails + sources : [`docs/reference/2026-06-17-docker-desktop-rootless-socket.md`](../reference/2026-06-17-docker-desktop-rootless-socket.md)).
- **Conteneur amuled mal nommé.** Le proxy n'autorise QUE `POST .../containers/amuled/restart` : le
  conteneur doit s'appeler **exactement `amuled`** (épinglé via `container_name: amuled` dans
  `gluetun.compose.yml`). Sous un autre nom, le restart fait **404** et le port-sync ne fait rien.
- **Fournisseur sans port forwarding.** Le High-ID exige un provider à port forwarding
  (Proton/PIA/PrivateVPN/PerfectPrivacy) et `VPN_PORT_FORWARDING: "on"`.

### Le port forwarded change toutes les ~60 s (jamais de High-ID stable, ProtonVPN + WireGuard)

- **Symptôme.** Dans les logs `gluetun`, un `port forwarded is <N>` **différent à chaque
  renouvellement** (~toutes les 45-60 s), chaque fois précédé de
  `ERROR [port forwarding] refreshing port mapping … external port requested as X but received Y`.
  Le port-sync ne peut jamais converger : la cible bouge plus vite qu'il ne peut aligner amuled
  (et son `restart_min_interval_seconds` bride le rythme des restarts). Résultat : Low-ID permanent
  **alors même que le port-sync fonctionne**.
- **Cause.** Le renouvellement NAT-PMP (obligatoire côté Proton) transite en UDP dans le tunnel
  **WireGuard** ; sur une clé/config Proton défaillante, la passerelle ne **préserve pas** le
  mapping au renouvellement et réassigne un port neuf. C'est un problème **gluetun ⇄ Proton**, pas
  du crawler (cf. [gluetun#3196](https://github.com/qdm12/gluetun/issues/3196)). `PORT_FORWARD_ONLY`
  seul **ne suffit pas** (vérifié sur le terrain : le churn persiste sur serveurs P2P).
- **Solution : régénérer la clé WireGuard Proton** (dashboard Proton) en cochant les bons réglages,
  ce qui couvre les trois causes racines connues d'un coup :
  1. **Port Forwarding activé** sur la config au moment de la génération.
  2. **Moderate NAT désactivé** : Proton le documente comme **incompatible NAT-PMP** (cause la plus
     fréquente).
  3. **Clé unique à cette instance** : une même clé WireGuard réutilisée par deux clients (autre
     gluetun, autre appareil) fait s'entre-écraser les renouvellements NAT-PMP. Une clé fraîche
     garantit l'unicité.

  Puis remplacer `WIREGUARD_PRIVATE_KEY` dans `.env` et recréer gluetun + amuled
  (`docker compose up -d gluetun amuled`). Garder `PORT_FORWARD_ONLY: "on"` (correct et sain, juste
  pas suffisant seul). Valider en observant `gluetun` : le port doit apparaître **une fois** puis
  rester **silencieux** sur plusieurs cycles (> 5 min), sans `requested X but received Y`.

---

## Stockage & droits

> ⚠️ **Prérequis pour cette section** : Linux + ligne de commande Docker. Les commandes `docker
> volume`, `chown`, UID/GID supposent une familiarité Unix. Si vous bloquez sur un de ces
> diagnostics et n'êtes pas à l'aise, l'option de repli sûre est de **repartir d'un volume vide**
> (perte du catalogue accumulé) : `docker compose down -v` puis `up -d`. Lourd mais simple.

### Volume `/data` déjà peuplé : permission refusée

- **Cause.** Le crawler tourne en `user: 999`. L'image pré-crée `/data/catalog` et `/data/local`
  en `nonroot`, donc un volume nommé **vide** hérite de la bonne propriété. Mais un volume **déjà
  peuplé** (root-owned) garde ses droits.
- **Solution.** Le nom de projet Docker Compose est fixé à `mulewatch` (`name: mulewatch` dans
  `compose.yaml` et `gluetun.compose.yml`), donc le volume s'appelle
  `mulewatch_catalog-db`. Vérifiez avec :
  ```bash
  docker volume ls | grep catalog-db
  # Sortie attendue : local  mulewatch_catalog-db
  ```
  Puis corrigez la propriété :
  ```bash
  docker run --rm -v <nom-du-volume>:/d alpine chown -R 999:999 /d
  # Avec le nom trouvé ci-dessus, par ex. :
  docker run --rm -v mulewatch_catalog-db:/d alpine chown -R 999:999 /d
  ```

### The `downloads/` folder is not writable by amuled

- **Cause.** `amuled` is a **third-party** image run with **its own user**: per the confinement
  decision on record ([AGENTS.md § Confinement posture](../../AGENTS.md), 2026-06-17), we do **not**
  impose our hardening (cap_drop, dedicated user and so on) on it. Accepted residual risk: a
  compromised amuled reaches the bind-mounted output directory. This is a **deliberate non-goal for
  v0.x**, not an oversight (see also
  [administration runbook § Limites connues](administration.md#limites-connues--follow-ups)).

  Operational consequence: `./downloads/incoming` and `./downloads/temp` are created by Docker on
  first `up` if they do not exist, and Docker creates a missing bind-mount source **owned by root**.
  If amuled's user cannot write there, downloads never start or never finish.
- **Symptom.** amuled's log reports a write or permission error on its temp or incoming directory,
  and nothing ever lands in `downloads/`.
- **Fix.** From your working folder, give the folders to the uid amuled runs as (read it from the
  running container first):
  ```bash
  docker compose exec amuled id            # the uid:gid amuled actually runs as
  sudo chown -R <uid>:<gid> downloads/
  ```
  Creating `downloads/incoming` and `downloads/temp` yourself **before** the first `up -d` avoids the
  problem entirely, since they then keep your ownership.

---

## Récupération après panne

Quelques scénarios « j'ai cassé quelque chose, comment je remonte ? » :

### J'ai perdu / je ne me souviens plus de `AMULE_EC_PASSWORD`

- **Symptôme.** Le crawler refuse de se connecter à amuled (`EC auth failed` dans les logs).
- **Solution.** Choisissez un nouveau mot de passe, mettez à jour `AMULE_EC_PASSWORD` dans `.env`
  ET `amules[].password` (et `download.endpoint.password`) dans `config/crawler/crawler.yml`, puis redémarrez :
  ```bash
  docker compose -f gluetun.compose.yml up -d --force-recreate amuled crawler
  ```
  Pas de perte de catalogue (le mot de passe ne protège que le canal EC, pas les données).

### J'ai mal édité `.env` et le compose refuse de démarrer

- **Symptôme.** `docker compose up` retourne une erreur de parsing ou un service `Exited (1)`
  immédiatement.
- **Solution.** Recommencez à partir du modèle : `cp .env.example .env.new`, recopiez vos secrets
  un par un en vérifiant la syntaxe (pas d'espaces autour du `=`, pas de guillemets autour des
  valeurs sauf nécessaire), puis `mv .env.new .env`. Évite d'avoir à débugger un fichier corrompu.

### Where do I find a downloaded file?

- **Answer.** In `downloads/incoming`, inside your working folder: it is a plain folder on your disk,
  not a Docker volume, so `docker compose down -v` does not touch it. Files that are still
  downloading sit in `downloads/temp`.
- **Nothing has inspected that file.** mulewatch never opens a downloaded file: no type check, no
  media probe, no antivirus scan. Check it yourself before opening it.

### Je veux repartir de zéro (catalogue effacé)

- **Solution destructive (irréversible).** Arrêtez tout et supprimez les volumes :
  ```bash
  docker compose -f gluetun.compose.yml down -v
  ```
  Le `-v` est ce qui efface. Sans lui, les volumes (donc le catalogue) sont préservés.
  Sauvegardez d'abord ce que vous tenez à garder.

---

## Outils de diagnostic

### Lancer une commande ponctuelle dans une image

L'image a un entrypoint exec-form `["python","-m","mulewatch"]`. Pour exécuter autre chose, passez
par `--entrypoint` :

```bash
docker run --rm --entrypoint python <image> -c "import rapidfuzz; print('ok')"
```

### Valider la configuration sans rien démarrer

```bash
uv run python -m mulewatch validate-config
```

Charge + valide les 3 configs et sort en erreur (code ≠ 0) si l'une est invalide, **sans rien
démarrer**. À lancer **avant** un déploiement (entre étape 3 et étape 4 du [runbook de déploiement](deployment.md))
ou après une modification de config.
