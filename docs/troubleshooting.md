# Diagnostics avancés


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

### s6 a redémarré un processus et le conteneur est resté debout

- **Symptôme.** amuled (ou amuleweb) réapparaît dans le journal — amuled se ré-annonce, recharge
  `server.met` — alors que `docker compose ps` n'a jamais quitté `Up`. Ou bien : vous avez appuyé
  sur le bouton de redémarrage de `/controls` et rien ne semble être arrivé au conteneur.
- **Cause. C'est normal.** s6 supervise chacun des trois processus indépendamment et en relance un
  sur place quand il meurt ; le conteneur ne tombe que lorsque le **crawler sort en code non nul**
  (son script `finish` demande alors à s6 de coucher tout l'arbre de supervision, de sorte que
  `restart: unless-stopped` donne une boucle de backoff visible au lieu d'un crash-loop silencieux).
  Une sortie propre du crawler — exactement ce que demande le bouton de redémarrage de `/controls` —
  ramène le crawler seul, et amuled garde ses sessions eD2k et Kad, ce qui est tout l'intérêt.
- **Comment le confirmer.** `s6-svstat` affiche l'uptime du service en secondes : un petit nombre
  signifie qu'il vient d'être redémarré.
  ```bash
  docker compose exec mulewatch s6-svstat /etc/services.d/mulewatch
  docker compose exec mulewatch s6-svstat /etc/services.d/amuled
  docker compose exec mulewatch s6-svstat /etc/services.d/amuleweb
  ```
- **Conséquence à garder en tête.** Un conteneur en `Up (healthy)` ne prouve **pas** que le crawler
  tourne : le healthcheck n'interroge qu'amuled (voir la fiche suivante). Dans le doute, interrogez
  `s6-svstat` sur `/etc/services.d/mulewatch`, ou cherchez des lignes `cycle ...` dans le journal.

### Le healthcheck lit la sortie de s6-svstat, pas son code de retour

- **Symptôme.** Vous écrivez votre propre sonde (un contrôle de supervision, un `healthcheck:`
  maison) autour de `s6-svstat`, et elle déclare **sain un amuled arrêté**.
- **Cause.** `s6-svstat` sort en **0 même pour un service arrêté** — avec `-u`, il imprime `false`
  sur la sortie standard. Un code de retour non nul signifie tout autre chose : c'est s6-supervise
  lui-même qui ne tourne pas pour ce répertoire de service.
- **Solution.** Testez la valeur imprimée, ce que fait le healthcheck livré :
  ```yaml
  test: ["CMD-SHELL", 'test "$$(s6-svstat -u /etc/services.d/amuled)" = true']
  ```
  (le `$$` est l'échappement compose d'un `$` littéral ; dans le conteneur, la commande est
  `test "$(s6-svstat -u /etc/services.d/amuled)" = true`).
- **Ce que `unhealthy` signifie, et ne signifie pas.** Le conteneur ne passe `unhealthy` que quand
  **amuled** est arrêté. Un crawler arrêté est invisible au healthcheck **par conception** : un
  plantage du crawler couche déjà le conteneur, donc le sonder serait quasi tautologique, et sonder
  amuled continue de fonctionner sur un nœud qui tourne avec `webui.enabled: false`.

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
  [runbook d'administration](operate.md).

---

## Téléchargements

### Un fichier terminé n'apparaît jamais dans `downloads/incoming`

- **Ce qui se passe normalement.** amuled écrit un fichier terminé directement dans son
  `IncomingDir`, que les piles compose atteignent par l'unique bind mount `./downloads:/downloads`
  de votre dossier de travail. Le crawler détecte la complétion depuis la liste des fichiers
  partagés d'amuled (le hash est partagé **et** a quitté la file de téléchargement), passe le
  téléchargement en `completed` et notifie. Il ne déplace, n'ouvre ni n'inspecte jamais le fichier ;
  sur `/downloads`, il ne fait jamais qu'un `statvfs`, pour le plancher d'espace libre.
- **Regardez d'abord l'état que voit le crawler.** Si la webui montre encore le téléchargement en
  `downloading`, c'est qu'il n'est tout simplement pas fini : rien n'est cassé.
- **Si le crawler dit `completed` mais que le dossier est vide**, amuled a posé le fichier ailleurs.
  Deux causes, dans l'ordre :
  1. **Une catégorie amuled redirige la destination.** Dans `amule.conf` (ou via amuleweb sur le
     port 4711), aucune catégorie ne doit porter un `Path=` non vide qui envoie le fichier terminé
     hors d'`IncomingDir`.
  2. **`IncomingDir` ne pointe pas sur le chemin monté en bind.** Le one-shot de démarrage écrit
     `IncomingDir=/downloads/incoming` et `TempDir=/downloads/temp` dans `amule.conf` — mais
     **seulement quand ce fichier est absent**. Un nœud migré depuis une organisation plus ancienne
     porte son propre `amule.conf`, donc une vieille valeur survit à tous les redémarrages.
     `amule.conf` est un simple fichier de votre dossier de travail, lisez-le depuis l'hôte :
     ```bash
     grep -E '^(Incoming|Temp)Dir' amule/amule.conf
     ```
     Corrigez les deux lignes, puis redémarrez amuled seul :
     ```bash
     docker compose exec mulewatch s6-svc -r /etc/services.d/amuled
     ```
- **Gardez amuled dédié au crawler.** `shared_files()` est interrogé à chaque cycle de
  téléchargement : ne pointez donc pas cet amuled sur une grande bibliothèque partagée
  préexistante, la détection de complétion en deviendrait plus lente et plus bruyante. Contexte et
  sources :
  [`reference/2026-06-17-amuled-completion-behavior.md`](https://github.com/GeoffreyCoulaud/mulewatch/blob/main/agents/reference/2026-06-17-amuled-completion-behavior.md)
  (ses contraintes 1 et 2, sur un volume de quarantaine partagé, ne s'appliquent plus : l'étape de
  quarantaine a été retirée le 2026-09-13).

### Un téléchargement est bloqué, puis passe en `failed`

- **Ce que fait le crawler.** Chaque cycle de téléchargement horodate `last_seen_at` pour chaque
  hash suivi qu'amuled rapporte, que ce soit dans sa file de téléchargement ou dans ses fichiers
  partagés. Un téléchargement encore `queued` ou `downloading` qu'amuled n'a plus rapporté depuis
  `download.lost_after_seconds` (24 h par défaut) est marqué `failed`, avec une ligne de journal :
  `hash=... unseen by amuled for 86400.0s: marked failed`.
- **Pourquoi c'est sans danger.** Une entrée reste dans la file d'amuled même avec **zéro source** :
  elle devient dormante, mais elle ne disparaît pas. Un téléchargement de lost media qui stagne à
  0 % pendant des mois n'est donc jamais menacé. Une absence côté amuled signifie que l'entrée a
  réellement été retirée, ou que le fichier a fini et a été déplacé hors d'`IncomingDir` avant
  l'interrogation suivante.
- **`failed` n'est pas définitif.** amuled reste l'autorité : si le hash réapparaît dans sa file, le
  crawler remet le téléchargement en `downloading` ; s'il apparaît dans les fichiers partagés, le
  téléchargement se termine et la notification part. Regardez `downloads/incoming` avant de conclure
  que le fichier est perdu.
- **Pour en réessayer un à la main**, supprimez sa ligne : `is_downloaded()` ignore l'état, donc une
  ligne `failed` continue de bloquer la remise en file automatique, à dessein. Il n'existe pas de
  contrôle webui pour cela et la console SQL est en lecture seule : c'est donc une écriture manuelle
  sur `local.db`. Arrêtez d'abord **le crawler seul** (écrivain unique par doctrine — amuled et
  amuleweb continuent de tourner, donc les sessions eD2k/Kad survivent), écrivez en tant
  qu'utilisateur `amule` du conteneur pour que les fichiers WAL créés par SQLite restent la
  propriété de `PUID:PGID`, puis relancez le crawler :
  ```bash
  docker compose exec mulewatch s6-svc -d /etc/services.d/mulewatch
  docker compose exec --user amule mulewatch python -c \
    "import sqlite3; db = sqlite3.connect('/data/local.db', autocommit=True); \
     db.execute('DELETE FROM downloads WHERE ed2k_hash = ?', ('<hash>',))"
  docker compose exec mulewatch s6-svc -u /etc/services.d/mulewatch
  ```
  Le cycle suivant le remet en file depuis la décision du catalogue, à condition que le fichier
  corresponde toujours à une cible qui n'est pas `complete`.
- **Si rien du tout ne se télécharge**, vérifiez le plancher disque avant de soupçonner le TTL : une
  ligne de journal `candidate hash=... -> skip_disk_cap (skipped/deferred)` signifie que l'espace
  libre, moins ce qu'amuled doit encore récupérer, passerait sous `download.min_free_bytes`. Un
  `output directory unmeasurable` signifie au contraire que le montage `./downloads:/downloads`
  manque dans votre fichier compose.

---

## High-ID / port-sync

> ⚠️ **Prérequis pour ce diagnostic** : lecture de logs gluetun et notions de port forwarding VPN.
> Si vous n'êtes pas à l'aise avec ces concepts, le port-sync n'est probablement pas la bonne voie
> pour vous : envisagez la **Route B** (port-forward manuel sur votre box) ou restez en **Low-ID**
> (qui marche très bien). Voir
> [runbook d'administration § High-ID](high-id.md).

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
  lecture seule). Au démarrage, le one-shot `amule-config.py` tourne en root, crée l'utilisateur
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
  Voir [runbook d'administration § Limites connues](limits.md).

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
  [« Une variable obligatoire manque »](troubleshooting-start.md#une-variable-obligatoire-manque).

### Où trouver un fichier téléchargé ?

- **Réponse.** Dans `downloads/incoming`, à l'intérieur de votre dossier de travail : c'est un
  simple dossier de votre disque, donc arrêter ou supprimer le conteneur n'y touche pas. Les
  fichiers encore en cours de téléchargement sont dans `downloads/temp`.
- **Rien n'a inspecté ce fichier.** mulewatch n'ouvre jamais un fichier téléchargé : pas de contrôle
  de type, pas de sonde média, pas d'analyse antivirus. Vérifiez-le vous-même avant de l'ouvrir.

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

### Piloter un processus dans le conteneur

Les trois processus sont supervisés par s6 dans l'unique conteneur `mulewatch` : ils se pilotent
donc par processus, et non par service compose. Depuis votre dossier de travail (`<svc>` vaut
`amuled`, `amuleweb` ou `mulewatch`) :

```bash
docker compose exec mulewatch s6-svstat /etc/services.d/<svc>   # up/down + uptime in seconds
docker compose exec mulewatch s6-svc -r /etc/services.d/<svc>   # restart it
docker compose exec mulewatch s6-svc -d /etc/services.d/<svc>   # stop it
docker compose exec mulewatch s6-svc -u /etc/services.d/<svc>   # start it again
```

Deux choses à savoir avant de les utiliser :

- Arrêter `mulewatch` (le crawler) laisse amuled et amuleweb en marche, ce qui est bien ce que vous
  voulez pour une écriture de maintenance sur les bases. Arrêter `amuled` rend le crawler aveugle :
  il journalisera des échecs EC et fera du backoff jusqu'au retour d'amuled.
- Une sortie **non nulle** du crawler couche tout le conteneur, à dessein. `s6-svc -d` est un arrêt
  propre, donc il ne le fait pas.

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
recette de [« Un téléchargement est bloqué »](#un-téléchargement-est-bloqué-puis-passe-en-failed)) : sinon SQLite laisse
derrière lui des fichiers `-wal`/`-shm` appartenant à root, que le crawler ne pourra plus écrire.

### Valider la configuration sans rien démarrer

```bash
docker compose exec mulewatch python -m mulewatch validate-config
```

Charge + valide les 3 configs et sort en erreur (code ≠ 0) si l'une est invalide, **sans rien
démarrer**. À lancer **avant** un déploiement (entre étape 3 et étape 4 du [runbook de déploiement](install.md))
ou après une modification de config.
