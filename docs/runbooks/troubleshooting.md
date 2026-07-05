# Runbook de dépannage : mulewatch

Chaque fiche suit le même format : **symptôme (ce que vous voyez à l'écran) : cause : solution
(commandes à copier-coller)**. Le runbook est en deux niveaux.

- **Le déploiement bloque (premiers pas)** : juste en dessous. Les blocages du tout premier
  déploiement, dans l'ordre de la [voie royale](deployment.md). Chaque fiche prolonge un **Point de
  contrôle** du guide : ouvrez celle vers laquelle le guide vous renvoie.
- **Diagnostics avancés (opérateurs)** : plus bas. Mode download, High-ID/port-sync, stockage &
  droits, verdicts du verifier, récupération après panne. Certaines de ces sections demandent une
  familiarité Linux/Docker et le signalent à leur ouverture.

Pour *monter* un nœud, voir le [runbook de déploiement](deployment.md) ; pour le *régler*, le
[runbook d'administration](administration.md).

---

## Le déploiement bloque (premiers pas)

Ces fiches correspondent aux **Points de contrôle** du guide de déploiement, dans l'ordre. Presque
tout se répare sans expertise : lire un journal, corriger une ligne, relancer une commande.

> **Où lancer ces commandes.** Les commandes `docker compose ...` se lancent depuis le dossier
> `deploy/` (là où vous a menés l'étape 5). Les commandes qui touchent au fichier `.env`
> (`nano deploy/.env`, `grep ... deploy/.env`) sont écrites depuis le dossier du projet
> (`mulewatch-main`) : si vous êtes déjà dans `deploy/`, remplacez `deploy/.env` par `.env`.

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
     qu'elle tourne : `docker compose` ne répond pas tant que le moteur est arrêté.
  3. Revérifiez :
     ```
     docker compose version
     ```
     Vous devez lire `Docker Compose version v2.x.x` (un numéro plus récent convient aussi). Si vous
     ne voyez toujours qu'un `docker-compose` v1, installez Docker Engine (v2) comme ci-dessus : la
     commande en deux mots est indispensable.
- **Retour au guide.** [Étape 2 : Installer Docker](deployment.md#2-installer-docker).

### Une valeur change-me est restée dans .env

- **Symptôme.** Au Point de contrôle de l'étape 4, la commande de vérification **affiche une ligne**
  au lieu de ne rien afficher :
  ```
  grep -E '(AMULE_EC_PASSWORD|GRAFANA_PWD)=change-me' deploy/.env
  ```
  (elle imprime la ligne fautive, par exemple `GRAFANA_PWD=change-me`). Symptôme possible plus tard :
  le crawler journalise une erreur d'authentification, ou Grafana refuse votre mot de passe.
- **Cause.** L'un des deux mots de passe obligatoires est encore la valeur d'exemple `change-me` :
  vous avez oublié une ligne, ou édité la mauvaise.
- **Solution.**
  1. Rouvrez le fichier (c'est un fichier **caché**, le plus sûr est de l'éditer au terminal) :
     ```
     nano deploy/.env
     ```
     Remplacez la valeur après le `=` sur la ligne signalée : `AMULE_EC_PASSWORD` (au moins 12
     caractères) et/ou `GRAFANA_PWD`. Enregistrez avec **Ctrl+O** puis **Entrée**, quittez avec
     **Ctrl+X**.
  2. Revérifiez : la commande de contrôle ne doit **plus rien afficher**.
     ```
     grep -E '(AMULE_EC_PASSWORD|GRAFANA_PWD)=change-me' deploy/.env
     ```
  3. Relancez la pile **depuis le dossier `deploy`** : `up -d` ne recrée que ce qui a changé.
     ```
     cd deploy
     docker compose up -d
     ```
- **Ce qui n'est PAS un problème.** Il reste normalement d'autres `change-me` dans le fichier (la
  ligne de commentaire, ou `WIREGUARD_PRIVATE_KEY` réservé au VPN de l'annexe A) : la commande de
  contrôle ci-dessus les **ignore** exprès. Seuls `AMULE_EC_PASSWORD` et `GRAFANA_PWD` comptent pour
  la voie royale.
- **Retour au guide.** [Étape 4 : Choisir ses deux mots de passe](deployment.md#4-choisir-ses-deux-mots-de-passe).

### Un conteneur redémarre en boucle

- **Symptôme.** `docker compose ps` montre un service en **`Restarting`** (ou `Exited`) au lieu de
  `Up`.
- **Diagnostic (toujours le même).** Regardez d'abord *quel* service, puis son journal. Depuis le
  dossier `deploy` :
  ```
  docker compose ps
  ```
  ```
  docker compose logs <service>
  ```
  (remplacez `<service>` par le nom du conteneur en boucle, par exemple `crawler` ou `grafana`). La
  dernière page du journal dit presque toujours pourquoi. Causes fréquentes :
- **Mot de passe EC absent ou incohérent (`crawler`).** Le journal du crawler se termine par une
  erreur d'authentification (`EcAuthError`, mot de passe EC refusé) et le conteneur redémarre. Sur
  la voie royale, amuled et le crawler partagent la **même** variable `AMULE_EC_PASSWORD` : ce cas
  vient donc d'un `AMULE_EC_PASSWORD` resté vide ou `change-me`, ou d'un mot de passe édité à la main
  dans `deploy/config/crawler/crawler.yml`. Corrigez `.env` (voir la fiche
  [« Une valeur change-me est restée dans .env »](#une-valeur-change-me-est-restée-dans-env)), puis
  `docker compose up -d` depuis `deploy/`.
- **Grafana sans mot de passe (`grafana`).** Si `docker compose up -d` s'arrête tout de suite avec
  `required variable GRAFANA_PWD is missing a value`, c'est que `GRAFANA_PWD` est vide dans `.env` :
  renseignez-le (même fiche que ci-dessus), puis relancez.
- **Crawler en mode download qui attend le verifier (normal).** Si vous avez activé le mode download
  (annexe B), le crawler **redémarre volontairement pendant 1 à 2 minutes** le temps que le
  `verifier` devienne sain : c'est attendu, la boucle se stabilise seule. Si elle dure au-delà de
  5 minutes, voir la fiche opérateur
  [« Le crawler redémarre en boucle au démarrage (mode download) »](#le-crawler-redémarre-en-boucle-au-démarrage-mode-download).
- **Retour au guide.** [Étape 5 : Lancer](deployment.md#5-lancer) et
  [étape 6 : Vérifier que le nœud vit](deployment.md#6-vérifier-que-le-nœud-vit).

### Le port est déjà pris

- **Symptôme.** Au lancement, `docker compose up -d` s'arrête avec un message du type :
  ```
  Error ... failed to bind host port for 0.0.0.0:3000: address already in use
  ```
  Le mot-clé est **`bind: address already in use`**. Un autre programme occupe déjà ce port sur
  votre machine.
- **Cause.** mulewatch publie des ports sur l'hôte ; l'un d'eux est déjà utilisé (un autre service,
  une ancienne pile...). Le numéro dans le message vous dit lequel :

  | Port par défaut | Variable à changer dans `deploy/.env` | Sert à |
  |---|---|---|
  | `8080` | `WEBUI_PORT` | le catalogue web |
  | `3000` | `GRAFANA_PORT` | les tableaux de bord Grafana |
  | `4662` | `LISTEN_PORT` | le port eMule (toujours publié ; surtout utile en High-ID, annexe C) |

- **Solution.** Ouvrez `deploy/.env`, donnez au port concerné une valeur libre (par exemple
  `WEBUI_PORT=8090`), enregistrez, puis relancez depuis `deploy/` :
  ```
  nano deploy/.env
  ```
  ```
  cd deploy
  docker compose up -d
  ```
  Pensez ensuite à ouvrir la nouvelle adresse dans le navigateur (par exemple
  <http://localhost:8090> au lieu de 8080).
- **Retour au guide.** [Étape 5 : Lancer](deployment.md#5-lancer).

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
    amuled n'a aucune sortie. Vérifiez gluetun *avant* amuled (depuis `deploy/`, avec le
    `-f gluetun.compose.yml` de l'annexe A) :
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
- **Retour au guide.** [Étape 6 : Vérifier que le nœud vit](deployment.md#6-vérifier-que-le-nœud-vit).

### La webui reste vide

Deux situations très différentes se cachent derrière « la webui est vide » :

- **La page se charge, mais le tableau est vide.** C'est **normal**, surtout les premières heures.
  Le catalogue se remplit au fil des recherches ; certaines cibles rares (le principe même du lost
  media) peuvent mettre des jours à réapparaître. **Ce n'est pas une panne.** Vérifiez plutôt que le
  nœud *vit*, en regardant les cycles du crawler (depuis `deploy/`) :
  ```
  docker compose logs crawler
  ```
  Vous devez y voir des lignes `cycle ...` jusqu'à `cycle 0 done`. Si oui, tout va bien : laissez
  tourner. Si le crawler reste `effective_coverage=blind`, voir
  [« amuled ne se connecte à rien »](#amuled-ne-se-connecte-à-rien).
- **La page ne se charge pas du tout** (connexion refusée, page inaccessible) : là c'est un vrai
  problème. Vérifiez d'abord que le service `webui` tourne :
  ```
  docker compose ps
  ```
  S'il n'est pas `Up`, voir [« Un conteneur redémarre en boucle »](#un-conteneur-redémarre-en-boucle).
  S'il est `Up` mais la page reste inaccessible, le port est peut-être remappé ou occupé (voir
  [« Le port est déjà pris »](#le-port-est-déjà-pris)) : confirmez l'adresse, par défaut
  <http://localhost:8080> (sur un serveur distant, remplacez `localhost` par son IP).
- **Retour au guide.** [Étape 6 : Vérifier que le nœud vit](deployment.md#6-vérifier-que-le-nœud-vit) et
  [étape 7 : Voir le catalogue](deployment.md#7-voir-le-catalogue).

---

## Diagnostics avancés (opérateurs)

Les sections qui suivent vont plus loin que le premier déploiement : mode download, High-ID,
stockage, verdicts, récupération. La plupart restent accessibles (lecture de logs, redémarrage de
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
  docker compose -f deploy/gluetun.compose.yml images amuled
  # Vous devez voir : ngosang/amule:3.0.0-1
  ```
  Si vous voyez `latest` ou `2.3.3-*`, fixez la version dans `deploy/base.compose.yml` puis re-pullez.
  *(Si une version 4.x sort dans le futur, ré-évaluer la compatibilité avant migration — ce projet
  n'a été éprouvé qu'avec 3.0.0-1.)*

### Le crawler refuse de démarrer : « variable d'environnement '…' référencée mais absente »

- **Symptôme.** `docker compose logs crawler` affiche
  `Config invalide, refus de démarrer : … : variable d'environnement 'AMULE_EC_PASSWORD' référencée mais absente`,
  alors que la variable est bien renseignée dans `.env`.
- **Cause.** Compose ne lit `.env` que pour substituer les `${...}` **dans les fichiers compose**.
  Le crawler, lui, interpole les `${VAR}` de `crawler.yml` depuis **son propre** environnement de
  conteneur. Une variable référencée dans `crawler.yml` doit donc être injectée explicitement dans
  le service `crawler` (bloc `environment:` de `deploy/base.compose.yml`) — sinon le process ne la
  voit pas. `AMULE_EC_PASSWORD` y est câblé par défaut.
- **Solution.** Si vous ajoutez un **nouveau** `${VAR}` dans `crawler.yml` (typiquement en activant
  une URL de notification `notifications[].url: "discord://${DISCORD_WEBHOOK_ID}/…"`), ajoutez la
  même variable au bloc `environment:` du service `crawler` :
  ```yaml
  # deploy/base.compose.yml
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

## Mode download (téléchargement + vérification)

### Le crawler redémarre en boucle au démarrage (mode download)

- **Cause.** En mode download, le crawler **refuse de démarrer** si le verifier ne répond pas (pas de
  téléchargement sans vérification) ; son `restart: unless-stopped` le relance tant que le verifier
  n'est pas sain — c'est le comportement attendu.
- **Solution rapide.** Le crawler finit par démarrer dès que le verifier est sain. Pour éviter les
  redémarrages initiaux, démarrez le verifier d'abord, puis le reste :
  ```bash
  docker compose -f deploy/gluetun.compose.yml --profile download up -d verifier
  docker compose -f deploy/gluetun.compose.yml --profile download up -d
  ```
- **Si la boucle persiste > 5 min**, diagnostic en escalier :
  1. **Le verifier a-t-il démarré proprement ?** `docker compose logs verifier --tail 50` — vous
     devez voir une ligne `Uvicorn running on http://0.0.0.0:8000` (ou similaire). Si vous voyez
     `OOMKilled` ou `Killed`, c'est un manque de mémoire — voir « Un fichier sain ressort suspicious »
     ci-dessous (cause #2 : manque de RAM avec clamav).
  2. **Le verifier est-il joignable depuis le réseau du crawler ?** `docker compose exec crawler
     wget -qO- http://verifier:8000/healthz` (si `wget` n'est pas dispo, `curl` aussi) — doit
     renvoyer un JSON `{"status":"ok"}`. Si `Connection refused`, le verifier est down ; si `Name
     resolution failure`, le service n'est pas sur le même réseau Docker (config compose suspecte).
  3. **L'URL du verifier est-elle correcte ?** Ouvrir `deploy/config/crawler/crawler.yml` et
     vérifier que `download.verifier_url` pointe sur `http://verifier:8000` (nom de service compose,
     pas `localhost` ni IP). Une mauvaise URL → le crawler ne joint jamais le verifier, peu importe
     son état.

### Un fichier manifestement sain ressort `suspicious`

Trois causes possibles, de la plus probable à la moins :

1. **La base clamav n'est pas encore synchronisée.** Au premier démarrage en mode download, le sidecar
   `freshclam` télécharge ~300–500 Mo (quelques minutes) ; tant qu'elle manque, clamav rend
   `suspicious` par défaut (jamais `clean` sans base). **C'est transitoire** — attendez la fin de la
   première synchro, le fichier sera re-scanné.
2. **Le scan se fait tuer faute de mémoire.** `clamscan` charge toute la base en RAM ; si les limites
   sont trop basses, l'OOM-killer tue le scan avant la fin → `suspicious`. Augmentez
   `RLIMIT_AS_BYTES_CLAMAV` / `RLIMIT_CPU_S_CLAMAV` et le `mem_limit` du verifier (voir
   [runbook d'administration](administration.md), « Analyse antivirus (clamav) »).
3. **Accroc de droits sur la quarantaine** (voir « Droits cross-user sur la quarantaine » plus bas).

### Le sidecar `freshclam` redémarre en boucle (`chown … Operation not permitted`)

- **Cause.** `freshclam` utilise l'image **tierce** officielle `clamav/clamav`, dont l'entrypoint
  `/init` tourne en root et exige structurellement plusieurs capabilities (`chown -R` de la base,
  `install` du `/run/clamav`, drop de privilèges vers l'utilisateur `clamav`, écriture du log). Sous
  notre plancher `cap_drop: ALL`, le premier `chown` échoue en EPERM ; l'entrypoint étant en
  `set -e`, le conteneur meurt → `restart: unless-stopped` reboucle. C'est le symptôme des lignes
  `chown: /var/lib/clamav/…: Operation not permitted`.
- **Solution.** On **n'impose pas** `cap_drop: ALL` à `freshclam` (image tierce, même posture
  qu'amuled — cf. [CLAUDE.md § Confinement](../../CLAUDE.md)). Le service garde `no-new-privileges`
  mais **pas** de `cap_drop` (`deploy/base.compose.yml`). Le volume `clamav-db` existant n'a pas
  besoin d'être réinitialisé : le `chown` de l'entrypoint réussira au prochain boot.

### Le fichier fini n'est pas récupéré (reste dans l'IncomingDir, non catalogué)

- **Cause.** Une des 4 contraintes du mode download n'est pas respectée. Détail et rationale dans
  [`reference/2026-06-17-amuled-completion-behavior.md` § Contraintes de déploiement](../reference/2026-06-17-amuled-completion-behavior.md#contraintes-de-déploiement-résumé).
- **Solution — vérifier les 4 contraintes dans l'ordre :**
  1. **IncomingDir d'amuled = dossier quarantaine du crawler ?** Vérifier dans la config amuled
     (`amule.conf` → `IncomingDir=`) ; doit pointer sur le même chemin monté que `staging_dir` /
     `quarantine_dir` du crawler. Le plus souvent : `/data/quarantine` côté amuled et côté crawler
     (même volume Docker `quarantine`).
  2. **Le volume est-il sur un FS Linux ?** `docker inspect mulewatch_quarantine | grep
     Mountpoint` puis `stat -f -c %T <mountpoint>` sur l'hôte → doit être `ext2/ext3` (= ext4),
     `btrfs`, `overlayfs`, etc. Pas `vfat`, `ntfs`, `fuseblk`. Si vous êtes sur Docker Desktop
     macOS, le mapping vers HFS+/APFS échoue.
  3. **Y a-t-il des catégories amuled actives ?** Dans `amule.conf` ou via EC : aucune catégorie
     ne doit avoir un `Path=` non vide qui redirigerait le fichier ailleurs que dans IncomingDir.
  4. **Le jeu partagé d'amuled est-il restreint ?** Il doit contenir uniquement les fichiers
     téléchargés par le crawler (qui les remet à la quarantaine à chaque cycle), pas une grosse
     bibliothèque pré-existante. Sinon `shared_files()` retourne trop de hits et la détection de
     complétion devient lente / instable.

---

## High-ID / port-sync

> ⚠️ **Prérequis pour ce diagnostic** : connaissance Docker (sockets, groupes Unix). Si vous n'êtes
> pas à l'aise avec ces concepts, le port-sync n'est probablement pas la bonne voie pour vous —
> envisagez la **Route B** (port-forward manuel sur votre box) ou restez en **Low-ID** (qui marche
> très bien). Voir [runbook d'administration § High-ID](administration.md#high-id-optionnel--devenir-joignable).

### Le port-sync reste inopérant (toujours Low-ID alors qu'il est activé)

Plusieurs causes, à vérifier dans cet ordre :

- **`docker-proxy` qui redémarre en boucle (`socket not available … connect: permission denied`).**
  Le proxy doit tourner en **root** pour lire le socket Docker bind-monté (`root:root` sous Docker
  Desktop, `root:docker` sous Docker natif — root est propriétaire dans les deux cas). L'image
  `wollomatic/socket-proxy` est buildée `USER 65534`, donc le compose **doit** poser `user: "0:0"`
  explicitement (`deploy/gluetun.compose.yml`) : sans cette ligne, le proxy tourne en `nobody` →
  `permission denied` → boucle. Si vous voyez ce symptôme, vérifiez que `user: "0:0"` est bien
  présent. **Rootless** reste hors de portée (socket sous `$XDG_RUNTIME_DIR`, accès par UID —
  détails + sources : [`docs/reference/2026-06-17-docker-desktop-rootless-socket.md`](../reference/2026-06-17-docker-desktop-rootless-socket.md)).
- **Conteneur amuled mal nommé.** Le proxy n'autorise QUE `POST .../containers/amuled/restart` : le
  conteneur doit s'appeler **exactement `amuled`** (épinglé via `container_name: amuled` dans
  `deploy/gluetun.compose.yml`). Sous un autre nom, le restart fait **404** et le port-sync ne fait rien.
- **Fournisseur sans port forwarding.** Le High-ID exige un provider à port forwarding
  (Proton/PIA/PrivateVPN/PerfectPrivacy) et `VPN_PORT_FORWARDING: "on"`.

### Le port forwarded change toutes les ~60 s (jamais de High-ID stable, ProtonVPN + WireGuard)

- **Symptôme.** Dans les logs `gluetun`, un `port forwarded is <N>` **différent à chaque
  renouvellement** (~toutes les 45–60 s), chaque fois précédé de
  `ERROR [port forwarding] refreshing port mapping … external port requested as X but received Y`.
  Le port-sync ne peut jamais converger : la cible bouge plus vite qu'il ne peut aligner amuled
  (et son `restart_min_interval_seconds` bride le rythme des restarts). Résultat : Low-ID permanent
  **alors même que le port-sync fonctionne**.
- **Cause.** Le renouvellement NAT-PMP (obligatoire côté Proton) transite en UDP dans le tunnel
  **WireGuard** ; sur une clé/config Proton défaillante, la passerelle ne **préserve pas** le
  mapping au renouvellement et réassigne un port neuf. C'est un problème **gluetun ⇄ Proton**, pas
  du crawler (cf. [gluetun#3196](https://github.com/qdm12/gluetun/issues/3196)). `PORT_FORWARD_ONLY`
  seul **ne suffit pas** (vérifié sur le terrain : le churn persiste sur serveurs P2P).
- **Solution — régénérer la clé WireGuard Proton** (dashboard Proton) en cochant les bons réglages,
  ce qui couvre les trois causes racines connues d'un coup :
  1. **Port Forwarding activé** sur la config au moment de la génération.
  2. **Moderate NAT désactivé** — Proton le documente comme **incompatible NAT-PMP** (cause la plus
     fréquente).
  3. **Clé unique à cette instance** — une même clé WireGuard réutilisée par deux clients (autre
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

- **Cause.** Le crawler tourne en `user: 999`. Les images pré-créent `/data/{catalog,local,quarantine}`
  en `nonroot`, donc un volume nommé **vide** hérite de la bonne propriété. Mais un volume **déjà
  peuplé** (root-owned) garde ses droits.
- **Solution.** Le nom de projet Docker Compose est fixé à `mulewatch` (`name: mulewatch` dans
  `deploy/compose.yaml` et `deploy/gluetun.compose.yml`), donc le volume s'appelle
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

### Droits cross-user sur la quarantaine

- **Cause.** `amuled` est une image **tierce** lancée avec **son propre user** : conformément au
  choix de confinement acté ([CLAUDE.md § Confinement posture](../../CLAUDE.md), 2026-06-17), on
  **n'impose pas** notre durcissement (cap_drop, user dédié, etc.) à amuled. Risque résiduel
  assumé : si amuled était compromis, l'attaquant accéderait au volume quarantaine. C'est un
  **non-objectif assumé pour v0.x**, pas un manque non vu (voir aussi
  [runbook d'administration § Limites connues](administration.md#limites-connues--follow-ups)).

  Conséquence opérationnelle : le volume `quarantine` est écrit à la fois par amuled (fichiers
  finis) et par le crawler (déplacement atomique) ; un accroc de droits cross-user peut survenir au
  tout premier vrai téléchargement.
- **Solution.** À surveiller au premier téléchargement réel ; si un déplacement échoue pour cause
  de droits :
  ```bash
  docker volume ls | grep quarantine   # trouver le nom exact du volume
  docker run --rm -v <nom-du-volume>:/q alpine chown -R 999:999 /q
  ```

---

## Comprendre les verdicts du verifier

Quand vous regardez un fichier dans la WebUI ou la base, vous voyez un **verdict** parmi 4 valeurs.
Voici ce que chacun signifie concrètement :

| Verdict | Signification | Que faire ? |
|---|---|---|
| `clean` | Tous les checks activés ont passé (`type_sniff` reconnaît le format, `ffprobe` lit les pistes média, `clamav` ne trouve aucune signature de virus). | Le fichier est probablement sain. Vous pouvez le récupérer depuis la quarantaine. **Ce n'est pas une garantie d'absence de virus** — c'est l'absence de signature connue dans la base clamav. |
| `suspicious` | Au moins un check a échoué ou n'a pas pu se prononcer (ex. base clamav non encore prête, scan tué par manque de mémoire, ffprobe incapable de lire). | Lire la colonne `explanation` du verdict : elle dit lequel des checks a échoué et pourquoi. Causes fréquentes : base clamav pas encore synchronisée (transitoire), manque de mémoire (cf. runbook administration), ou fichier réellement étrange. |
| `malicious` | Clamav a trouvé une signature de virus connue. | **N'extrayez pas le fichier de la quarantaine.** Si vous pensez à un faux positif, vérifiez la signature dans la base clamav et remontez à clamav (pas à ce projet). |
| `unknown` | Le verifier n'a pas pu être interrogé du tout (verifier down, timeout, erreur réseau). | Voir « Le crawler redémarre en boucle » plus haut. |

> Un fichier `clean` n'est pas certifié inoffensif — c'est l'absence de signature dans une base
> donnée. Pour les fichiers à enjeu (binaires exécutables, archives), faites une vérification
> supplémentaire avant d'ouvrir.

---

## Récupération après panne

Quelques scénarios « j'ai cassé quelque chose, comment je remonte ? » :

### J'ai perdu / je ne me souviens plus de `AMULE_EC_PASSWORD`

- **Symptôme.** Le crawler refuse de se connecter à amuled (`EC auth failed` dans les logs).
- **Solution.** Choisissez un nouveau mot de passe, mettez à jour `AMULE_EC_PASSWORD` dans `.env`
  ET `amules[].password` dans `deploy/config/crawler/crawler.yml`, puis redémarrez :
  ```bash
  docker compose -f deploy/gluetun.compose.yml --profile <mode> up -d --force-recreate amuled crawler
  ```
  Pas de perte de catalogue (le mot de passe ne protège que le canal EC, pas les données).

### J'ai mal édité `.env` et le compose refuse de démarrer

- **Symptôme.** `docker compose up` retourne une erreur de parsing ou un service `Exited (1)`
  immédiatement.
- **Solution.** Recommencez à partir du modèle : `cp deploy/.env.example .env.new`, recopiez vos secrets
  un par un en vérifiant la syntaxe (pas d'espaces autour du `=`, pas de guillemets autour des
  valeurs sauf nécessaire), puis `mv .env.new .env`. Évite d'avoir à débugger un fichier corrompu.

### Un fichier est bloqué dans la quarantaine

- **Symptôme.** Le fichier est listé dans la WebUI avec un verdict `suspicious` mais vous savez
  qu'il est sain (et vous voulez le récupérer).
- **Solution.** La quarantaine est un volume Docker (`<projet>_quarantine`). Pour y accéder :
  ```bash
  docker volume ls | grep quarantine                       # nom exact
  docker run --rm -it -v <nom-du-volume>:/q alpine ls /q   # lister
  docker run --rm -v <nom-du-volume>:/q -v "$PWD":/out alpine cp /q/<fichier> /out/
  ```
  Le fichier est copié dans votre dossier courant. Vérifiez-le indépendamment avant de l'ouvrir.

### Je veux repartir de zéro (catalogue effacé)

- **Solution destructive (irréversible).** Arrêtez tout et supprimez les volumes :
  ```bash
  docker compose -f deploy/gluetun.compose.yml --profile <mode> down -v
  ```
  Le `-v` est ce qui efface. Sans lui, les volumes (donc le catalogue) sont préservés.
  Sauvegardez d'abord ce que vous tenez à garder.

---

## Outils de diagnostic

### Lancer une commande ponctuelle dans une image

Les images ont un entrypoint exec-form `["python","-m","<pkg>"]`. Pour exécuter autre chose, passez
par `--entrypoint` :

```bash
docker run --rm --entrypoint python <image> -c "import re2, rapidfuzz; print('ok')"
```

### Valider la configuration sans rien démarrer

```bash
uv run python -m mulewatch validate-config
```

Charge + valide les 4 configs et sort en erreur (code ≠ 0) si l'une est invalide, **sans rien
démarrer**. À lancer **avant** un déploiement (entre étape 3 et étape 4 du [runbook de déploiement](deployment.md))
ou après une modification de config.
