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

> **Un seul conteneur, trois processus.** Le service compose s'appelle `mulewatch` ; à l'intérieur,
> s6 supervise `amuled`, `amuleweb` et `mulewatch` (le crawler, qui sert la webui dans son propre
> thread). Chaque service s6 redirige sa sortie d'erreur vers la sortie standard, donc
> `docker compose logs mulewatch` montre **les trois journaux entrelacés** : la première question
> d'un diagnostic est toujours « lequel des trois a parlé ? ».

---

## Le déploiement bloque (premiers pas)

Ces fiches correspondent aux **Points de contrôle** du guide de déploiement, dans l'ordre. Presque
tout se répare sans expertise : lire un journal, corriger une ligne, relancer une commande.

> **Où lancer ces commandes.** Toutes les commandes ci-dessous se lancent depuis votre **dossier de
> travail** (le dossier qui contient `compose.yml`, créé à
> [l'étape 3 du guide](deployment.md#3-create-your-working-folder)). Les chemins sont donc
> relatifs : `.env`, `crawler.yml`, `data/`, `amule/`, `downloads/`. Sous la pile VPN, ajoutez
> `-f gluetun.compose.yml` à chaque `docker compose ...`.

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

### A required variable is missing

- **Symptom, form 1 (the usual one).** `docker compose up -d` refuses to start anything and prints
  compose's own error, before a single container is created:
  ```
  error while interpolating services.mulewatch.environment.PUID: required variable "PUID" is not set
  ```
  The same happens for `PGID`, `AMULE_EC_PASSWORD` and `WEBUI_PWD`.
- **Symptom, form 2.** The variable *is* declared but **empty**, or the image is run outside
  compose (a bare `docker run`). The container then exits within a second, and its whole log is one
  line:
  ```
  /usr/local/bin/amule-config.sh: 6: PUID is required
  ```
- **The distinguishing sign: no Python traceback at all.** Nothing Python ever started. If you see
  a traceback, or a `Invalid config, refusing to start:` line, this is not your problem — read
  [« Un conteneur redémarre en boucle »](#un-conteneur-redémarre-en-boucle) instead.
- **Cause.** The four variables are **hard requirements**. The startup one-shot `amule-config.sh`
  runs as PID 1's first action, before any of the three services: it creates the container's
  `amule` user from `PUID`/`PGID`, takes ownership of the bind mounts, and writes the aMule
  password. It exits 1 if any of the four is missing or empty. The compose files add a `:?` guard
  on each one so the failure surfaces as the clear message of form 1 rather than a dead container.
- **Fix.** Fill all four in `.env` (copy `.env.example` if you have not yet):

  | Variable | What it is |
  |---|---|
  | `PUID` / `PGID` | your own uid/gid (`id -u`, `id -g`) — what keeps `data/`, `amule/` and `downloads/` readable from the host without `sudo` |
  | `AMULE_EC_PASSWORD` | the crawler ⇄ amuled password, at least 12 characters, of your choosing |
  | `WEBUI_PWD` | the amuleweb admin password (port 4711) |

  Then relaunch: `docker compose up -d`.
- **Retour au guide.** [Étape 4 : votre mot de passe](deployment.md#4-your-password).

### Une valeur change-me est restée dans .env

- **Symptôme.** Au Point de contrôle de l'étape 4, la commande de vérification **affiche une ligne**
  au lieu de ne rien afficher :
  ```
  grep -E '^(AMULE_EC_PASSWORD|WEBUI_PWD)=change-me' .env
  ```
  (elle imprime la ligne fautive). Symptôme possible plus tard : le crawler journalise une erreur
  d'authentification.
- **Cause.** Un mot de passe obligatoire est encore la valeur d'exemple `change-me` : vous avez
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
     caractères) et `WEBUI_PWD`. Dans `nano`, enregistrez avec **Ctrl+O** puis **Entrée**,
     quittez avec **Ctrl+X** ; dans le Bloc-notes, enregistrez avec **Ctrl+S**.
  2. Revérifiez : la commande de contrôle ne doit **plus rien afficher**.
     ```
     grep -E '^(AMULE_EC_PASSWORD|WEBUI_PWD)=change-me' .env
     ```
     Sous **Windows (PowerShell)** :
     ```
     Select-String -Path .env -Pattern '^(AMULE_EC_PASSWORD|WEBUI_PWD)=change-me'
     ```
  3. Relancez la pile **depuis votre dossier de travail** : `up -d` ne recrée que ce qui a changé.
     ```
     docker compose up -d
     ```
- **Ce qui n'est PAS un problème.** Il reste normalement un autre `change-me` dans le fichier
  (`WIREGUARD_PRIVATE_KEY`, réservé au VPN de l'annexe A) : la commande de contrôle ci-dessus
  l'**ignore** exprès. Seuls `AMULE_EC_PASSWORD` et `WEBUI_PWD` comptent pour la voie royale.
- **Attention si vous changez `AMULE_EC_PASSWORD` sur un nœud déjà lancé** : amuled garde le mot de
  passe de son premier démarrage, voir
  [« J'ai perdu `AMULE_EC_PASSWORD` »](#jai-perdu--je-ne-me-souviens-plus-de-amule_ec_password).
- **Retour au guide.** [Étape 4 : votre mot de passe](deployment.md#4-your-password).

### Un conteneur redémarre en boucle

- **Symptôme.** `docker compose ps` montre le service en **`Restarting`** (ou `Exited`) au lieu de
  `Up`.
- **Diagnostic (toujours le même).** Il n'y a **qu'un seul service**, `mulewatch` (plus `gluetun`
  sous la pile VPN) : la question n'est donc pas « quel conteneur ? » mais **lequel des trois
  processus a échoué**. Depuis votre dossier de travail :
  ```
  docker compose ps
  ```
  ```
  docker compose logs mulewatch
  ```
  Le journal est **entrelacé** : amuled, amuleweb et le crawler y écrivent tous. Repérez d'abord
  qui parle en dernier. Seul un arrêt **non nul** du crawler couche le conteneur (son script
  `finish` demande alors l'arrêt de toute la supervision) ; si amuled ou amuleweb tombe, s6 le
  relance sur place et le conteneur reste `Up` — voir
  [« s6 restarted one process »](#s6-restarted-one-process-and-the-container-stayed-up).
  Causes fréquentes :
- **Configuration invalide (crawler).** Le journal se termine par
  `Invalid config, refusing to start: ...`. Corrigez `crawler.yml`, `targets.yml` ou `matcher.yml`,
  puis `docker compose up -d`. Vous pouvez valider les trois fichiers sans rien démarrer : voir
  [« Valider la configuration sans rien démarrer »](#valider-la-configuration-sans-rien-démarrer).
- **Mot de passe EC absent ou incohérent (crawler).** Le journal se termine par une erreur
  d'authentification (`EcAuthError`, mot de passe EC refusé). Le crawler et amuled partagent la
  **même** variable `AMULE_EC_PASSWORD` : ce cas vient donc d'un `AMULE_EC_PASSWORD` resté
  `change-me`, ou modifié après le premier démarrage sans mettre à jour `amule/amule.conf` (voir
  [« J'ai perdu `AMULE_EC_PASSWORD` »](#jai-perdu--je-ne-me-souviens-plus-de-amule_ec_password)).
- **Variable absente au tout premier instant.** Si le journal tient en une ligne du type
  `PUID is required` et qu'aucun processus Python n'a démarré, voir
  [« A required variable is missing »](#a-required-variable-is-missing).
- **Journal vide juste après une montée d'image.** Si le conteneur boucle en laissant un journal
  **vide** (pas d'erreur, pas de traceback), ce n'est pas une panne applicative : le noyau a tué le
  conteneur, donc rien n'a pu être écrit. À vérifier :
  ```
  docker inspect --format '{{.State.OOMKilled}} {{.State.ExitCode}}' mulewatch-mulewatch-1
  ```
  `true 137` confirme le manque de mémoire. Cause : au premier démarrage qui suit une montée
  d'image, le crawler applique les migrations SQLite en attente, et une migration qui construit un
  index **trie en mémoire**. Ce tri grossit avec la table indexée (environ 116 octets par ligne) et
  rien ne le plafonne, ni `cache_size` ni la limite du conteneur : sur un gros catalogue le pic peut
  dépasser `mem_limit` (2 Go par défaut). Le repère historique de **4,5 millions de lignes** a été
  mesuré à l'époque où `mem_limit` valait 512 Mo ; il n'a pas été re-mesuré à 2 Go. Remède : relevez
  temporairement le `mem_limit` du service `mulewatch` dans `base.compose.yml`,
  `docker compose up -d`, laissez le premier démarrage aller à son terme (le pic est ponctuel : une
  fois l'index construit il est maintenu au fil de l'eau, sans tri), puis remettez la valeur
  d'origine. À titre de repère, la migration 0004 construit son index sur 1,19 million
  d'observations en environ 5 s pour un pic d'environ 150 Mo.
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
  | `8080` | `WEBUI_PORT` | le catalogue web mulewatch, **sans aucune authentification** |
  | `4711` | `AMULEWEB_PORT` | amuleweb, l'interface web d'aMule, protégée par `WEBUI_PWD` |
  | `4662` | `LISTEN_PORT` | le port eMule, TCP + UDP (publié par la **pile directe** seulement ; sous VPN il arrive par le port forwardé de gluetun) |

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
- **« Low-ID » n'est pas une panne.** Si le journal du conteneur
  (`docker compose logs mulewatch`, où amuled écrit aussi) mentionne Low-ID, c'est l'état **normal**
  par défaut : recherche, catalogage et téléchargement fonctionnent ; seule la joignabilité est
  sous-optimale. Devenir High-ID est optionnel (annexe C du guide).
- **Si ça dure au-delà de quelques minutes.**
  - **(a) Vérifiez la sortie Internet de la machine** : amuled a besoin du port 443 sortant pour
    l'amorçage.
  - **(b) Si vous avez ajouté un VPN (annexe A)**, c'est presque toujours le tunnel `gluetun` qui
    n'est pas monté : le conteneur mulewatch **partage le réseau de gluetun**, donc tant que le
    tunnel est down, amuled n'a aucune sortie. Vérifiez gluetun *avant* amuled (depuis votre dossier
    de travail, avec le `-f gluetun.compose.yml` de l'annexe A) :
    ```
    docker compose -f gluetun.compose.yml logs gluetun
    ```
    Tunnel sain : une ligne `[gluetun] [vpn] connected` et une IP publique VPN
    (`You are running on the public IP address ...`, pas la vôtre). Tunnel cassé : `cannot connect
    to ...` puis `retrying in N seconds`. Corrigez alors le VPN (clé WireGuard,
    `VPN_SERVICE_PROVIDER`, `SERVER_COUNTRIES` dans `.env`), puis, une fois gluetun « connected »,
    redémarrez **le seul processus amuled**, sans toucher au reste du conteneur :
    ```
    docker compose -f gluetun.compose.yml exec mulewatch s6-svc -r /etc/services.d/amuled
    ```
  - **(c) Plus de détails** dans la
    [fiche opérateur](#amuled-ne-se-connecte-à-aucun-serveur-ni-réseau-tunnel) du même symptôme.
- **Retour au guide.** [Étape 6 : Voir votre nœud](deployment.md#6-see-your-node).

### La webui reste vide

Deux situations très différentes se cachent derrière « la webui est vide » :

- **La page se charge, mais le tableau est vide.** C'est **normal**, surtout les premières heures.
  Le catalogue se remplit au fil des recherches ; certaines cibles rares (le principe même du lost
  media) peuvent mettre des jours à réapparaître. **Ce n'est pas une panne.** Vérifiez plutôt que le
  nœud *vit*, en regardant les cycles du crawler (depuis votre dossier de travail) :
  ```
  docker compose logs mulewatch
  ```
  Vous devez y voir des lignes `cycle ...` jusqu'à `cycle 0 done` (mêlées aux lignes d'amuled et
  d'amuleweb, qui partagent ce journal). Si oui, tout va bien : laissez tourner. Si le crawler reste
  `effective_coverage=blind`, voir
  [« amuled ne se connecte à rien »](#amuled-ne-se-connecte-à-rien).
- **La page ne se charge pas du tout** (connexion refusée, page inaccessible) : là c'est un vrai
  problème. La webui est servie **en intra-processus** par le crawler (il n'y a pas de service
  `webui` séparé) ; vérifiez d'abord que le conteneur tourne, puis que le crawler lui-même tourne —
  un conteneur `Up (healthy)` ne prouve **pas** que le crawler est vivant, la sonde n'interroge
  qu'amuled :
  ```
  docker compose ps
  ```
  ```
  docker compose exec mulewatch s6-svstat /etc/services.d/mulewatch
  ```
  Si le conteneur n'est pas `Up`, voir
  [« Un conteneur redémarre en boucle »](#un-conteneur-redémarre-en-boucle). Si `s6-svstat` répond
  `down`, relisez le journal (`docker compose logs mulewatch`). Si tout est `up` mais que la page
  reste inaccessible, le port est peut-être remappé ou occupé (voir
  [« Le port est déjà pris »](#le-port-est-déjà-pris)) : confirmez l'adresse, par défaut
  <http://localhost:8080> (sur un serveur distant, remplacez `localhost` par son IP). Vérifiez
  enfin que `webui.enabled` vaut bien `true` dans `crawler.yml`.
- **Retour au guide.** [Étape 6 : Voir votre nœud](deployment.md#6-see-your-node).

---

## Diagnostics avancés (opérateurs)

Les sections qui suivent vont plus loin que le premier déploiement : téléchargement, High-ID,
stockage, récupération. La plupart restent accessibles (lecture de logs, redémarrage de
processus) ; **High-ID/port-sync et Stockage & droits exigent une familiarité Linux/Docker** et le
signalent à leur ouverture. Si vous bloquez sur une étape qui dépasse votre confort, l'option de
repli sûre est presque toujours de *repartir d'un dossier `data/` vide* (voir « Récupération après
panne » plus bas) : vous perdez le catalogue accumulé mais vous redémarrez d'un état connu.

---

## Démarrage & réseau

### amuled ne se connecte à aucun serveur ni réseau (tunnel)

- **Cause la plus fréquente.** Au tout premier run, amuled doit amorcer sa liste de serveurs eD2k
  (`server.met`) et de nœuds Kad (`nodes.dat`) en faisant du DNS + HTTPS sortant (443) **à travers le
  VPN**. Si gluetun n'est pas encore monté, ou si la sortie Internet est bloquée à ce moment, rien ne
  s'amorce et amuled reste sans serveurs ni nœuds.
- **Solution.** Vérifiez d'abord l'état du tunnel gluetun, *avant* amuled :
  ```bash
  docker compose -f gluetun.compose.yml logs gluetun   # le tunnel doit être « up », IP publique VPN
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
  Le conteneur mulewatch **partage le réseau de gluetun** (`network_mode: service:gluetun`) : tant
  que le tunnel est down, amuled n'a aucune sortie. Si le tunnel ne monte pas, corrigez le VPN (clé
  WireGuard, fournisseur, `SERVER_COUNTRIES`) puis relancez ; une fois gluetun « up », redémarrez le
  processus amuled sans coucher le reste :
  ```bash
  docker compose -f gluetun.compose.yml exec mulewatch s6-svc -r /etc/services.d/amuled
  ```
- **Version d'aMule.** Elle n'est plus un paramètre de déploiement : aMule **3.0.1** est compilé
  dans notre propre image depuis un nixpkgs épinglé. Il n'y a plus d'image tierce à vérifier ni à
  épingler ; la version d'aMule suit celle de l'image mulewatch.

### s6 restarted one process and the container stayed up

- **Symptom.** amuled (or amuleweb) reappears in the log — amuled re-announcing itself, reloading
  `server.met` — while `docker compose ps` never left `Up`. Or: you pressed the restart button on
  `/controls` and nothing seems to have happened to the container.
- **Cause. This is normal.** s6 supervises each of the three processes independently and restarts
  one in place when it dies; the container only goes down when the **crawler exits non-zero** (its
  `finish` script then asks s6 to tear the whole supervision tree down, so `restart: unless-stopped`
  gives a visible backoff loop instead of a silent crash loop). A clean crawler exit — which is
  exactly what `/controls`' restart button asks for — brings the crawler back alone, and amuled
  keeps its eD2k and Kad sessions, which is the point.
- **How to confirm.** `s6-svstat` prints the service's uptime in seconds: a small number means it
  was just restarted.
  ```bash
  docker compose exec mulewatch s6-svstat /etc/services.d/mulewatch
  docker compose exec mulewatch s6-svstat /etc/services.d/amuled
  docker compose exec mulewatch s6-svstat /etc/services.d/amuleweb
  ```
- **Consequence to keep in mind.** A container sitting at `Up (healthy)` does **not** prove the
  crawler is running: the healthcheck probes amuled only (see the next entry). When in doubt, ask
  `s6-svstat` about `/etc/services.d/mulewatch`, or look for `cycle ...` lines in the log.

### The healthcheck reads s6-svstat output, not its exit code

- **Symptom.** You write your own probe (a monitoring check, a custom `healthcheck:`) around
  `s6-svstat` and it reports a **stopped amuled as healthy**.
- **Cause.** `s6-svstat` exits **0 even for a down service** — it prints `false` on stdout with
  `-u`. A non-zero exit means something else entirely: s6-supervise itself is not running for that
  service directory.
- **Fix.** Test the printed value, which is what the shipped healthcheck does:
  ```yaml
  test: ["CMD-SHELL", 'test "$$(s6-svstat -u /etc/services.d/amuled)" = true']
  ```
  (the `$$` is compose escaping a literal `$`; inside the container the command is
  `test "$(s6-svstat -u /etc/services.d/amuled)" = true`).
- **What `unhealthy` does and does not mean.** The container turns `unhealthy` only when **amuled**
  is down. A crawler that is down is invisible to the healthcheck **by design**: a crawler crash
  already kills the container, so probing it would be near-tautological, and probing amuled keeps
  working on a node running with `webui.enabled: false`.

### Le crawler refuse de démarrer : « environment variable '…' referenced but not set »

- **Symptôme.** `docker compose logs mulewatch` affiche
  `Invalid config, refusing to start: … : environment variable 'AMULE_EC_PASSWORD' referenced but not set`,
  alors que la variable est bien renseignée dans `.env`.
- **Cause.** Compose ne lit `.env` que pour substituer les `${...}` **dans les fichiers compose**.
  Le crawler, lui, interpole les `${VAR}` de `crawler.yml` depuis **son propre** environnement de
  conteneur. Une variable référencée dans `crawler.yml` doit donc être injectée explicitement dans
  le service `mulewatch` (bloc `environment:` de `base.compose.yml`), sinon le process ne la
  voit pas. `AMULE_EC_PASSWORD` y est câblé par défaut.
- **Solution.** Si vous ajoutez un **nouveau** `${VAR}` dans `crawler.yml` (typiquement en activant
  une URL de notification `notifications[].url: "discord://${DISCORD_WEBHOOK_ID}/…"`), ajoutez la
  même variable au bloc `environment:` du service `mulewatch` :
  ```yaml
  # base.compose.yml
  mulewatch:
    environment:
      PUID: ${PUID:?}
      PGID: ${PGID:?}
      AMULE_EC_PASSWORD: ${AMULE_EC_PASSWORD:?}
      WEBUI_PWD: ${WEBUI_PWD:?}
      DISCORD_WEBHOOK_ID: ${DISCORD_WEBHOOK_ID:?}     # ← nouvelle ligne par secret ajouté
      DISCORD_WEBHOOK_TOKEN: ${DISCORD_WEBHOOK_TOKEN:?}
  ```
  Le mapping est **explicite** (et non `env_file: .env`) pour le moindre privilège : le conteneur
  n'a pas à voir la clé WireGuard ni les autres secrets du déploiement.

### Le statut « Low-ID » apparaît dans les logs

- **Ce n'est pas une panne.** Low-ID est l'**état normal** par défaut : recherche, catalogage et
  téléchargement fonctionnent ; seule la joignabilité est sous-optimale (moins de sources directes).
- **Pour passer en High-ID** (optionnel), voir « High-ID (optionnel) » dans le
  [runbook d'administration](administration.md).

---

## Downloads

### A finished file never shows up in `downloads/incoming`

- **What happens normally.** amuled writes a finished file straight into its `IncomingDir`, which
  the compose stacks reach through the single `./downloads:/downloads` bind mount in your working
  folder. The crawler detects the completion from amuled's shared-files list (the hash is shared
  **and** has left the download queue), flips the download to `completed` and notifies. It never
  moves, opens or inspects the file; on `/downloads` it only ever calls `statvfs` for the free-space
  floor.
- **Check the state the crawler sees first.** If the webui still shows the download as `downloading`,
  it simply has not finished: nothing is broken.
- **If the crawler says `completed` but the folder is empty**, amuled put the file somewhere else.
  Two causes, in order:
  1. **An amuled category is redirecting the destination.** In `amule.conf` (or through amuleweb on
     port 4711), no category may carry a non-empty `Path=` that sends the finished file outside
     `IncomingDir`.
  2. **`IncomingDir` does not point at the bind-mounted path.** The startup one-shot writes
     `IncomingDir=/downloads/incoming` and `TempDir=/downloads/temp` into `amule.conf` — but
     **only when that file is absent**. A node migrated from an older layout carries its own
     `amule.conf`, so an old value survives every restart. `amule.conf` is a plain file in your
     working folder, read it from the host:
     ```bash
     grep -E '^(Incoming|Temp)Dir' amule/amule.conf
     ```
     Fix the two lines, then restart amuled alone:
     ```bash
     docker compose exec mulewatch s6-svc -r /etc/services.d/amuled
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
  console is read-only, so it is a manual write on `local.db`. Stop the **crawler alone** first
  (single writer by doctrine — amuled and amuleweb keep running, so the eD2k/Kad sessions survive),
  write as the container's `amule` user so the SQLite WAL files it creates stay owned by
  `PUID:PGID`, then bring the crawler back:
  ```bash
  docker compose exec mulewatch s6-svc -d /etc/services.d/mulewatch
  docker compose exec --user amule mulewatch python -c \
    "import sqlite3; db = sqlite3.connect('/data/local.db', autocommit=True); \
     db.execute('DELETE FROM downloads WHERE ed2k_hash = ?', ('<hash>',))"
  docker compose exec mulewatch s6-svc -u /etc/services.d/mulewatch
  ```
  The next cycle re-queues it from the catalogue decision, provided the file still matches a target
  that is not `complete`.
- **If nothing at all is being downloaded**, check the disk floor before suspecting the TTL: a log
  line `candidate hash=... -> skip_disk_cap (skipped/deferred)` means free space minus what amuled
  still has to fetch would fall below `download.min_free_bytes`. `output directory unmeasurable`
  instead means the `./downloads:/downloads` mount is missing from your compose file.

---

## High-ID / port-sync

> ⚠️ **Prérequis pour ce diagnostic** : lecture de logs gluetun et notions de port forwarding VPN.
> Si vous n'êtes pas à l'aise avec ces concepts, le port-sync n'est probablement pas la bonne voie
> pour vous : envisagez la **Route B** (port-forward manuel sur votre box) ou restez en **Low-ID**
> (qui marche très bien). Voir
> [runbook d'administration § High-ID](administration.md#high-id-optionnel--devenir-joignable).

### Le port-sync reste inopérant (toujours Low-ID alors qu'il est activé)

Plusieurs causes, à vérifier dans cet ordre :

- **Pile directe au lieu de la pile VPN.** Le port-sync n'a de sens que sous
  `gluetun.compose.yml` : il lit le port forwardé sur le serveur de contrôle de gluetun, à
  `http://localhost:8000`, adresse qui n'existe que parce que le conteneur partage le réseau de
  gluetun. Sous la pile directe, `port_sync.enabled: true` ne peut rien joindre.
- **Fournisseur sans port forwarding.** Le High-ID exige un provider à port forwarding
  (Proton/PIA/PrivateVPN/PerfectPrivacy) et `VPN_PORT_FORWARDING: "on"`.
- **Le redémarrage d'amuled est refusé.** Le port-sync applique le nouveau port en redémarrant le
  processus amuled (`s6-svc -r /etc/services.d/amuled`), ce qui suppose que le crawler ait pu poser
  la permission de groupe sur la FIFO de contrôle d'amuled au démarrage. Si cette étape a échoué,
  le journal du conteneur porte, dès le démarrage, la ligne :
  ```
  mulewatch: /etc/services.d/amuled/supervise/control never appeared; amuled restarts will be refused
  ```
  et, à chaque tentative de port-sync, une erreur `s6-svc exited ...`. Le crawl, lui, continue
  normalement. Remède : redémarrer le conteneur (`docker compose restart mulewatch`) pour rejouer
  la séquence de démarrage.

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

  Puis remplacer `WIREGUARD_PRIVATE_KEY` dans `.env` et recréer les deux services — le conteneur
  mulewatch vit dans le namespace réseau de gluetun, il doit donc être recréé avec lui :
  ```bash
  docker compose -f gluetun.compose.yml up -d --force-recreate
  ```
  Garder `PORT_FORWARD_ONLY: "on"` (correct et sain, juste pas suffisant seul). Valider en observant
  `gluetun` : le port doit apparaître **une fois** puis rester **silencieux** sur plusieurs cycles
  (> 5 min), sans `requested X but received Y`.

---

## Stockage & droits

> ⚠️ **Prérequis pour cette section** : Linux + notions d'UID/GID et de droits de fichiers. Si vous
> bloquez sur un de ces diagnostics et n'êtes pas à l'aise, l'option de repli sûre est de
> **repartir d'un `data/` vide** (perte du catalogue accumulé) : `docker compose down`, puis
> supprimez le dossier `data/` et relancez `docker compose up -d`. Lourd mais simple.

### amuled ne peut pas écrire dans les bind mounts (PUID / PGID)

- **Ce que fait l'image.** Il n'y a **plus aucun volume nommé** : tout est un bind mount relatif
  dans votre dossier de travail (`data/`, `amule/`, `downloads/`, plus les trois `.yml` montés en
  lecture seule). Au démarrage, le one-shot `amule-config.sh` tourne en root, crée l'utilisateur
  `amule` avec `PUID:PGID`, puis donne **les points de montage** (`/home/amule/.aMule`,
  `/downloads/incoming`, `/downloads/temp`) à cet utilisateur. Le crawler fait de même sur `/data`.
- **Ce qu'il ne fait pas : ce `chown` n'est PAS récursif** sur `downloads/` ni sur `amule/`, et
  c'est délibéré : ces dossiers peuvent contenir des centaines de gigaoctets de part files, et leur
  contenu appartient à l'opérateur.
- **Symptôme.** Après un changement de `PUID`/`PGID`, ou après avoir migré un nœud depuis un ancien
  déploiement, amuled journalise une erreur d'écriture ou de permission sur son dossier temp ou
  incoming, ou ne reprend pas ses téléchargements en cours ; ou bien amuled ne relit pas son
  `amule.conf`. Côté hôte, symptôme jumeau : `sqlite3 data/catalog.db` ou un simple `ls downloads/`
  demande `sudo`.
- **Cause.** Les **contenus** existants appartiennent encore à l'ancien uid/gid.
- **Solution.** Alignez `PUID`/`PGID` sur VOTRE utilisateur (c'est leur raison d'être : garder ces
  dossiers lisibles sans `sudo`), puis reprenez la propriété des contenus, une seule fois :
  ```bash
  id -u ; id -g                        # les valeurs à mettre dans PUID / PGID
  sudo chown -R "$(id -u):$(id -g)" data amule downloads
  docker compose up -d
  ```
- **Posture de confinement, pour mémoire.** PID 1 tourne en **root** (il crée l'utilisateur et
  prend possession des points de montage), donc ce service ne porte ni `user:`, ni `read_only:`, ni
  `cap_drop: ALL` : ils ne peuvent pas survivre à ce besoin. Décision documentée et signée
  (spec 2026-09-16 §9), qui inverse la moitié « crawler » de la décision du 2026-06-17. Ce qui reste
  est conservé et relevé pour trois processus : `no-new-privileges:true`, `pids_limit: 512`,
  `mem_limit: 2g`. Risque résiduel accepté : un amuled compromis atteint les bind mounts de sortie.
  Voir [runbook d'administration § Limites connues](administration.md#limites-connues--follow-ups).

---

## Récupération après panne

Quelques scénarios « j'ai cassé quelque chose, comment je remonte ? » :

### J'ai perdu / je ne me souviens plus de `AMULE_EC_PASSWORD`

- **Symptôme.** Le crawler refuse de se connecter à amuled (`EcAuthError` dans les logs), et
  amuleweb (port 4711) refuse lui aussi de joindre amuled.
- **Le piège.** Changer `AMULE_EC_PASSWORD` dans `.env` **ne suffit pas**. Le crawler et amuleweb
  prennent bien la nouvelle valeur au redémarrage, mais **amuled**, lui, lit son mot de passe dans
  `amule/amule.conf`, sous forme de **digest MD5** — et ce fichier n'est écrit par l'image que s'il
  est **absent**. Après votre premier démarrage, il existe : sa valeur survit à tous les
  redémarrages. Les trois processus se désynchronisent alors.
- **Solution.** Choisissez un nouveau mot de passe, mettez-le dans `.env`, puis alignez
  `amule.conf` à la main. Depuis votre dossier de travail :
  ```bash
  printf %s 'mon-nouveau-mot-de-passe' | md5sum | cut -d' ' -f1
  ```
  Reportez le digest obtenu dans la ligne `ECPassword=` de `amule/amule.conf` (section
  `[ExternalConnect]`), puis :
  ```bash
  docker compose up -d --force-recreate
  ```
  Pas de perte de catalogue (le mot de passe ne protège que le canal EC, pas les données).
- **Variante brutale.** Supprimer `amule/amule.conf` le fait régénérer au prochain démarrage, avec
  le mot de passe de `.env` — mais vous perdez tous les autres réglages aMule accumulés dans ce
  fichier (les serveurs eD2k et les nœuds Kad, eux, vivent dans `server.met` / `nodes.dat` et
  survivent).

### J'ai mal édité `.env` et le compose refuse de démarrer

- **Symptôme.** `docker compose up` retourne une erreur de parsing ou un service `Exited (1)`
  immédiatement.
- **Solution.** Recommencez à partir du modèle : `cp .env.example .env.new`, recopiez vos secrets
  un par un en vérifiant la syntaxe (pas d'espaces autour du `=`, pas de guillemets autour des
  valeurs sauf nécessaire), puis `mv .env.new .env`. Évite d'avoir à débugger un fichier corrompu.
  Si l'erreur nomme une variable (`required variable "..." is not set`), voir
  [« A required variable is missing »](#a-required-variable-is-missing).

### Where do I find a downloaded file?

- **Answer.** In `downloads/incoming`, inside your working folder: it is a plain folder on your
  disk, so stopping or removing the container does not touch it. Files that are still downloading
  sit in `downloads/temp`.
- **Nothing has inspected that file.** mulewatch never opens a downloaded file: no type check, no
  media probe, no antivirus scan. Check it yourself before opening it.

### Je veux repartir de zéro (catalogue effacé)

- **Solution destructive (irréversible).** Il n'y a plus de volume Docker à supprimer : les données
  sont des dossiers de votre dossier de travail. Arrêtez la pile, puis effacez ce que vous voulez
  perdre.
  ```bash
  docker compose down
  rm -rf data          # le catalogue + l'état local du nœud
  ```
  Ajoutez `amule/` pour repartir d'un aMule vierge (mot de passe, serveurs, nœuds Kad), et
  `downloads/` pour jeter aussi les fichiers téléchargés — ces deux-là sont indépendants du
  catalogue. `docker compose down -v` n'efface **plus rien** de tout cela. Sauvegardez d'abord ce
  que vous tenez à garder.

---

## Outils de diagnostic

### Controlling one process inside the container

The three processes are supervised by s6 inside the single `mulewatch` container, so they are
controlled per process rather than per compose service. From your working folder (`<svc>` is
`amuled`, `amuleweb` or `mulewatch`):

```bash
docker compose exec mulewatch s6-svstat /etc/services.d/<svc>   # up/down + uptime in seconds
docker compose exec mulewatch s6-svc -r /etc/services.d/<svc>   # restart it
docker compose exec mulewatch s6-svc -d /etc/services.d/<svc>   # stop it
docker compose exec mulewatch s6-svc -u /etc/services.d/<svc>   # start it again
```

Two things to know before using them:

- Stopping `mulewatch` (the crawler) leaves amuled and amuleweb running, which is what you want for
  a maintenance write on the databases. Stopping `amuled` blinds the crawler: it will log EC
  failures and back off until amuled returns.
- A **non-zero** exit of the crawler takes the whole container down on purpose. `s6-svc -d` is a
  clean stop, so it does not.

### Lancer une commande ponctuelle dans une image

L'entrypoint de l'image est `/usr/local/bin/entrypoint.sh` (il prépare la configuration d'aMule
puis passe la main à s6). Pour exécuter autre chose, passez par `--entrypoint` ; le venv est en tête
de `PATH`, donc `python` est celui du crawler :

```bash
docker run --rm --entrypoint python <image> -c "import rapidfuzz; print('ok')"
```

Depuis le dossier de travail, la même chose sur le service déployé (avec ses bind mounts, donc ses
bases) :

```bash
docker compose run --rm --entrypoint python mulewatch -c "print('ok')"
```

⚠️ Un `run` de ce type démarre en **root** et court-circuite la préparation faite par l'entrypoint.
S'il doit **écrire** dans `/data`, préférez un `exec --user amule` sur le conteneur en cours (la
recette de [« A download is stuck »](#a-download-is-stuck-then-turns-failed)) : sinon SQLite laisse
derrière lui des fichiers `-wal`/`-shm` appartenant à root, que le crawler ne pourra plus écrire.

### Valider la configuration sans rien démarrer

```bash
uv run python -m mulewatch validate-config
```

Charge + valide les 3 configs et sort en erreur (code ≠ 0) si l'une est invalide, **sans rien
démarrer**. À lancer **avant** un déploiement (entre étape 3 et étape 4 du [runbook de déploiement](deployment.md))
ou après une modification de config.
