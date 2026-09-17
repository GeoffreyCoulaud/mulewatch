# Le déploiement bloque

Chaque fiche suit le même format : **symptôme**, cause, solution à copier-coller. Elles reprennent
les Points de contrôle du [guide de déploiement](install.md), dans l'ordre, et presque tout se
répare sans expertise : lire un journal, corriger une ligne, relancer une commande. Pour le
téléchargement, le High-ID, le stockage et la récupération après panne, voir
[Diagnostics avancés](troubleshooting.md).

> **Où lancer ces commandes.** Depuis votre dossier de travail, celui qui contient `compose.yml`
> (créé à [l'étape 3 du guide](install.md#3-créer-votre-dossier-de-travail)). Les chemins sont donc
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
  2. Sous Windows ou macOS, lancez l'application Docker Desktop et attendez qu'elle affiche qu'elle
     tourne. Seules les commandes qui interrogent le moteur (`docker ps`, `docker compose up`)
     échouent tant qu'il est arrêté ; `docker compose version` répond même moteur éteint, car c'est
     une commande côté client.
  3. Revérifiez :
     ```
     docker compose version
     ```
     Vous devez lire `Docker Compose version v2.x.x` (un numéro plus récent convient aussi). Si vous
     ne voyez toujours qu'un `docker-compose` v1, installez Docker Engine (v2) comme ci-dessus : la
     commande en deux mots est indispensable.

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
  `docker compose version`, elle, répond normalement : c'est une commande côté client, qui ne parle
  pas au moteur.
- **Cause.** Le moteur Docker n'est pas démarré. Sous Windows et macOS, l'application Docker Desktop
  n'est pas lancée ou pas encore prête ; sur un serveur Linux, le service Docker est arrêté.
- **Solution.**
  1. Sous **Windows ou macOS** : lancez Docker Desktop et attendez que son icône passe au vert, ce
     qui signale que le moteur est prêt.
  2. Sur un **serveur Linux** : démarrez le service Docker.
     ```
     sudo systemctl start docker
     ```
  3. Re-testez `docker ps`. Le succès est un tableau d'en-têtes de colonnes, même vide.
     ```
     docker ps
     ```

### Une variable obligatoire manque

- **Symptôme, forme 1 (la plus courante).** `docker compose up -d` refuse de démarrer quoi que ce
  soit et affiche l'erreur propre à compose, avant même qu'un seul conteneur soit créé :
  ```
  error while interpolating services.mulewatch.environment.PUID: required variable "PUID" is not set
  ```
  Il en va de même pour `PGID`, `AMULE_EC_PASSWORD` et `WEBUI_PWD`.
- **Symptôme, forme 2.** La variable est déclarée mais vide, ou l'image est lancée hors compose (un
  `docker run` nu). Le conteneur sort alors en moins d'une seconde, et tout son journal tient en une
  ligne :
  ```
  PUID is required
  ```
- **Le signe distinctif : aucune traceback Python.** Rien de Python n'a jamais démarré. Si vous
  voyez une traceback, ou une ligne `Invalid config, refusing to start:`, ce n'est pas votre cas :
  lisez plutôt [« Un conteneur redémarre en boucle »](#un-conteneur-redémarre-en-boucle).
- **Cause.** Les quatre variables sont strictement obligatoires. Le one-shot de démarrage
  `amule-config.py` s'exécute comme première action de PID 1, avant les trois services : il crée
  l'utilisateur `amule` du conteneur à partir de `PUID`/`PGID`, prend possession des bind mounts et
  écrit le mot de passe aMule. Il sort en 1 si l'une des quatre manque ou est vide. Les fichiers
  compose ajoutent une garde `:?` sur chacune, pour que l'échec se manifeste par le message clair de
  la forme 1 plutôt que par un conteneur mort.
- **Solution.** Renseignez les quatre dans `.env` (copiez `.env.example` si ce n'est pas déjà
  fait) :

  | Variable | Ce que c'est |
  |---|---|
  | `PUID` / `PGID` | vos propres uid/gid (`id -u`, `id -g`), ce qui garde `data/`, `amule/` et `downloads/` lisibles depuis l'hôte sans `sudo` |
  | `AMULE_EC_PASSWORD` | le mot de passe crawler ⇄ amuled, au moins 12 caractères, de votre choix |
  | `WEBUI_PWD` | le mot de passe admin d'amuleweb (port 4711) |

  Puis relancez : `docker compose up -d`.

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
  1. Rouvrez le fichier. Son nom commence par un point ; le plus simple est de l'éditer au terminal.
     Sous **macOS / Linux** :
     ```
     nano .env
     ```
     Sous **Windows** :
     ```
     notepad .env
     ```
     Remplacez la valeur après le `=` sur la ligne signalée : `AMULE_EC_PASSWORD` (au moins 12
     caractères) et `WEBUI_PWD`. Dans `nano`, enregistrez avec **Ctrl+O** puis Entrée, et quittez
     avec Ctrl+X ; dans le Bloc-notes, enregistrez avec Ctrl+S.
  2. Revérifiez : la commande de contrôle ne doit **plus rien afficher**.
     ```
     grep -E '^(AMULE_EC_PASSWORD|WEBUI_PWD)=change-me' .env
     ```
     Sous **Windows (PowerShell)** :
     ```
     Select-String -Path .env -Pattern '^(AMULE_EC_PASSWORD|WEBUI_PWD)=change-me'
     ```
  3. Relancez la pile depuis votre dossier de travail. `up -d` ne recrée que ce qui a changé.
     ```
     docker compose up -d
     ```
- **Ce qui n'est PAS un problème.** Il reste normalement un autre `change-me` dans le fichier
  (`WIREGUARD_PRIVATE_KEY`, réservé au VPN) : la commande de contrôle ci-dessus
  l'ignore exprès. Seuls `AMULE_EC_PASSWORD` et `WEBUI_PWD` comptent pour la voie royale.
- **Attention si vous changez `AMULE_EC_PASSWORD` sur un nœud déjà lancé.** amuled garde le mot de
  passe de son premier démarrage, voir
  [« J'ai perdu `AMULE_EC_PASSWORD` »](troubleshooting.md#jai-perdu--je-ne-me-souviens-plus-de-amule_ec_password).

### Un conteneur redémarre en boucle

- **Symptôme.** `docker compose ps` montre le service en **`Restarting`** (ou `Exited`) au lieu de
  `Up`.
- **Diagnostic (toujours le même).** Il n'y a qu'un seul service, `mulewatch` (plus `gluetun` sous
  la pile VPN). La question n'est donc pas « quel conteneur ? » mais **lequel des trois processus a
  échoué**. Depuis votre dossier de travail :
  ```
  docker compose ps
  ```
  ```
  docker compose logs mulewatch
  ```
  Le journal est entrelacé : amuled, amuleweb et le crawler y écrivent tous. Repérez d'abord qui
  parle en dernier. Seul un arrêt non nul du crawler couche le conteneur, car son script `finish`
  demande alors l'arrêt de toute la supervision. Si amuled ou amuleweb tombe, s6 le relance sur
  place et le conteneur reste `Up` : voir
  [« s6 a redémarré un processus »](troubleshooting.md#s6-a-redémarré-un-processus-et-le-conteneur-est-resté-debout).
  Causes fréquentes :
- **Configuration invalide (crawler).** Le journal se termine par
  `Invalid config, refusing to start: ...`. Corrigez `crawler.yml`, `targets.yml` ou `matcher.yml`,
  puis `docker compose up -d`. Vous pouvez valider les trois fichiers sans rien démarrer : voir
  [« Valider la configuration sans rien démarrer »](troubleshooting.md#valider-la-configuration-sans-rien-démarrer).
- **Mot de passe EC absent ou incohérent (crawler).** Le journal se termine par une erreur
  d'authentification (`EcAuthError`, mot de passe EC refusé). Le crawler et amuled partagent la même
  variable `AMULE_EC_PASSWORD` : ce cas vient donc d'un `AMULE_EC_PASSWORD` resté `change-me`, ou
  modifié après le premier démarrage sans mettre à jour `amule/amule.conf` (voir
  [« J'ai perdu `AMULE_EC_PASSWORD` »](troubleshooting.md#jai-perdu--je-ne-me-souviens-plus-de-amule_ec_password)).
- **Variable absente au tout premier instant.** Si le journal tient en une ligne du type
  `PUID is required` et qu'aucun processus Python n'a démarré, voir
  [« Une variable obligatoire manque »](#une-variable-obligatoire-manque).
- **Journal vide juste après une montée d'image.** Si le conteneur boucle en laissant un journal
  vide, sans erreur ni traceback, ce n'est pas une panne applicative : le noyau a tué le conteneur,
  donc rien n'a pu être écrit. À vérifier :
  ```
  docker inspect --format '{{.State.OOMKilled}} {{.State.ExitCode}}' mulewatch-mulewatch-1
  ```
  `true 137` confirme le manque de mémoire. Au premier démarrage qui suit une montée d'image, le
  crawler applique les migrations SQLite en attente, et une migration qui construit un index trie en
  mémoire. Ce tri grossit avec la table indexée (environ 116 octets par ligne) et rien ne le
  plafonne, ni `cache_size` ni la limite du conteneur : sur un gros catalogue le pic peut dépasser
  `mem_limit` (2 Go par défaut). Le repère historique de 4,5 millions de lignes a été mesuré à
  l'époque où `mem_limit` valait 512 Mo ; il n'a pas été re-mesuré à 2 Go. Remède : relevez
  temporairement le `mem_limit` du service `mulewatch` dans `base.compose.yml`, faites
  `docker compose up -d`, laissez le premier démarrage aller à son terme, puis remettez la valeur
  d'origine. Le pic est ponctuel : une fois l'index construit, il est maintenu au fil de l'eau, sans
  tri. À titre de repère, la migration 0004 construit son index sur 1,19 million d'observations en
  environ 5 s pour un pic d'environ 150 Mo.

### Le port est déjà pris

- **Symptôme.** Au lancement, `docker compose up -d` s'arrête avec un message du type :
  ```
  Error ... failed to bind host port for 0.0.0.0:8080: address already in use
  ```
  Le mot-clé est **`bind: address already in use`**. Un autre programme occupe déjà ce port sur
  votre machine.
- **Cause.** mulewatch publie des ports sur l'hôte, et l'un d'eux est déjà utilisé (un autre
  service, une ancienne pile...). Le numéro dans le message vous dit lequel :

  | Port par défaut | Sert à |
  |---|---|
  | `8080` | le catalogue web mulewatch, **sans aucune authentification** |
  | `4711` | amuleweb, l'interface web d'aMule, protégée par `WEBUI_PWD` |
  | `4662` | le port eMule, TCP + UDP (publié par la **pile directe** seulement ; sous VPN il arrive par le port forwardé de gluetun) |

- **Solution.** Les ports ne sont pas des variables : ils sont écrits en clair dans la section
  `ports:` du fichier de votre pile (`compose.yml`, ou `gluetun.compose.yml` où le service `gluetun`
  publie les deux ports web). Ouvrez ce fichier et donnez au port concerné une valeur libre **du
  côté gauche**, celui de l'hôte, par exemple `"8090:8080"`. Enregistrez, puis relancez depuis votre
  dossier de travail :
  ```
  nano compose.yml
  ```
  ```
  docker compose up -d
  ```
  Pensez ensuite à ouvrir la nouvelle adresse dans le navigateur, par exemple
  <http://localhost:8090> au lieu de 8080.

### amuled ne se connecte à rien

- **Symptôme.** Le crawler boucle bien (vous voyez des lignes `cycle ...`) mais reste indéfiniment
  en `effective_coverage=blind`, avec des avertissements d'injoignabilité. amuled n'atteint ni les
  serveurs eD2k ni le réseau Kad, donc aucune source.
- **D'abord, patientez : au premier démarrage c'est normal.** amuled amorce tout seul sa liste de
  serveurs eD2k (`server.met`) et de nœuds Kad (`nodes.dat`) via du DNS et du HTTPS sortant, ce qui
  prend 1 à 3 minutes. Pendant ce temps, `effective_coverage=blind` et quelques avertissements sont
  attendus : cela se résorbe seul. Vous n'avez aucun serveur à ajouter.
- **« Low-ID » n'est pas une panne.** Si le journal du conteneur
  (`docker compose logs mulewatch`, où amuled écrit aussi) mentionne Low-ID, c'est l'état normal par
  défaut : recherche, catalogage et téléchargement fonctionnent, seule la joignabilité est
  sous-optimale. Devenir High-ID est optionnel, voir [Devenir High-ID](high-id.md).
- **Si ça dure au-delà de quelques minutes.**
  - **(a) Vérifiez la sortie Internet de la machine.** amuled a besoin du port 443 sortant pour
    l'amorçage.
  - **(b) Si vous avez ajouté un VPN**, c'est presque toujours le tunnel `gluetun` qui
    n'est pas monté. Le conteneur mulewatch partage le réseau de gluetun, donc tant que le tunnel
    est down, amuled n'a aucune sortie. Vérifiez gluetun avant amuled, depuis votre dossier de
    travail, avec le `-f gluetun.compose.yml` de la pile VPN :
    ```
    docker compose -f gluetun.compose.yml logs gluetun
    ```
    Tunnel sain : une ligne `[gluetun] [vpn] connected` et une IP publique VPN
    (`You are running on the public IP address ...`, pas la vôtre). Tunnel cassé : `cannot connect
    to ...` puis `retrying in N seconds`. Corrigez alors le VPN (clé WireGuard,
    `VPN_SERVICE_PROVIDER`, `SERVER_COUNTRIES` dans `.env`), puis, une fois gluetun « connected »,
    redémarrez le seul processus amuled, sans toucher au reste du conteneur :
    ```
    docker compose -f gluetun.compose.yml exec mulewatch s6-svc -r /etc/services.d/amuled
    ```
  - **(c) Plus de détails** dans la
    [fiche opérateur](troubleshooting.md#amuled-ne-se-connecte-à-aucun-serveur-ni-réseau-tunnel) du même symptôme.

### La webui reste vide

Deux situations très différentes se cachent derrière « la webui est vide » :

- **La page se charge, mais le tableau est vide.** C'est normal, surtout les premières heures. Le
  catalogue se remplit au fil des recherches ; certaines cibles rares (le principe même du lost
  media) peuvent mettre des jours à réapparaître. Vérifiez plutôt que le nœud vit, en regardant les
  cycles du crawler depuis votre dossier de travail :
  ```
  docker compose logs mulewatch
  ```
  Vous devez y voir des lignes `cycle ...` jusqu'à `cycle 0 done`, mêlées aux lignes d'amuled et
  d'amuleweb, qui partagent ce journal. Si oui, laissez tourner. Si le crawler reste
  `effective_coverage=blind`, voir
  [« amuled ne se connecte à rien »](#amuled-ne-se-connecte-à-rien).
- **La page ne se charge pas du tout** (connexion refusée, page inaccessible). Là c'est un vrai
  problème. La webui est servie en intra-processus par le crawler, il n'y a pas de service `webui`
  séparé. Vérifiez d'abord que le conteneur tourne, puis que le crawler lui-même tourne : un
  conteneur `Up (healthy)` ne prouve pas que le crawler est vivant, la sonde n'interroge qu'amuled.
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

---
