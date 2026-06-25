# Runbook de dépannage — emule-indexer

Symptômes courants et leur résolution, **quel que soit votre niveau**. Chaque entrée suit le même
format : **symptôme → cause → solution**. Pour *monter* un nœud, voir le
[runbook de déploiement](runbook-deployment.md) ; pour le *régler*, le
[runbook d'administration](runbook-administration.md).

---

## Démarrage & réseau

### amuled ne se connecte à aucun serveur ni réseau (aucune source)

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
  docker compose -f examples/<fichier> images amuled
  # Vous devez voir : ngosang/amule:3.0.0-1
  ```
  Si vous voyez `latest` ou `2.3.3-*`, fixez la version dans votre `examples/*.yaml` puis re-pullez.
  *(Si une version 4.x sort dans le futur, ré-évaluer la compatibilité avant migration — ce projet
  n'a été éprouvé qu'avec 3.0.0-1.)*

### Le statut « Low-ID » apparaît dans les logs

- **Ce n'est pas une panne.** Low-ID est l'**état normal** par défaut : recherche, catalogage et
  téléchargement fonctionnent ; seule la joignabilité est sous-optimale (moins de sources directes).
- **Pour passer en High-ID** (optionnel), voir « High-ID (optionnel) » dans le
  [runbook d'administration](runbook-administration.md).

---

## Mode download (téléchargement + vérification)

### Le crawler redémarre en boucle au démarrage (mode download)

- **Cause.** En mode download, le crawler **refuse de démarrer** si le verifier ne répond pas (pas de
  téléchargement sans vérification) ; son `restart: unless-stopped` le relance tant que le verifier
  n'est pas sain — c'est le comportement attendu.
- **Solution rapide.** Le crawler finit par démarrer dès que le verifier est sain. Pour éviter les
  redémarrages initiaux, démarrez le verifier d'abord, puis le reste :
  ```bash
  docker compose -f examples/<fichier> --profile download up -d verifier
  docker compose -f examples/<fichier> --profile download up -d
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
  3. **`verifier_url` est-il correct côté crawler ?** Ouvrir `config/crawler/download.yaml` et
     vérifier que `verifier_url` pointe sur `http://verifier:8000` (nom de service compose, pas
     `localhost` ni IP). Une mauvaise URL → le crawler ne joint jamais le verifier, peu importe son
     état.

### Un fichier manifestement sain ressort `suspicious`

Trois causes possibles, de la plus probable à la moins :

1. **La base clamav n'est pas encore synchronisée.** Au premier démarrage en mode download, le sidecar
   `freshclam` télécharge ~300–500 Mo (quelques minutes) ; tant qu'elle manque, clamav rend
   `suspicious` par défaut (jamais `clean` sans base). **C'est transitoire** — attendez la fin de la
   première synchro, le fichier sera re-scanné.
2. **Le scan se fait tuer faute de mémoire.** `clamscan` charge toute la base en RAM ; si les limites
   sont trop basses, l'OOM-killer tue le scan avant la fin → `suspicious`. Augmentez
   `RLIMIT_AS_BYTES_CLAMAV` / `RLIMIT_CPU_S_CLAMAV` et le `mem_limit` du verifier (voir
   [runbook d'administration](runbook-administration.md), « Analyse antivirus (clamav) »).
3. **Accroc de droits sur la quarantaine** (voir « Droits cross-user sur la quarantaine » plus bas).

### Le fichier fini n'est pas récupéré (reste dans l'IncomingDir, non catalogué)

- **Cause.** Une des 4 contraintes du mode download n'est pas respectée. Détail et rationale dans
  [`reference/2026-06-17-amuled-completion-behavior.md` § Contraintes de déploiement](reference/2026-06-17-amuled-completion-behavior.md#contraintes-de-déploiement-résumé).
- **Solution — vérifier les 4 contraintes dans l'ordre :**
  1. **IncomingDir d'amuled = dossier quarantaine du crawler ?** Vérifier dans la config amuled
     (`amule.conf` → `IncomingDir=`) ; doit pointer sur le même chemin monté que `staging_dir` /
     `quarantine_dir` du crawler. Le plus souvent : `/data/quarantine` côté amuled et côté crawler
     (même volume Docker `quarantine`).
  2. **Le volume est-il sur un FS Linux ?** `docker inspect emule-indexer_quarantine | grep
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
> très bien). Voir [runbook d'administration § High-ID](runbook-administration.md#high-id-optionnel--devenir-joignable).

### Le port-sync reste inopérant (toujours Low-ID alors qu'il est activé)

Plusieurs causes, à vérifier dans cet ordre :

- **Hôte Docker incompatible.** Le `docker-proxy` qui redémarre amuled tourne non-root et lit
  `/var/run/docker.sock` par accès **groupe** (`660 root:docker`) : il exige donc un **Docker rootful
  natif**. **Docker Desktop** ré-expose le socket en **`root:root`** dans le conteneur → le GID
  `docker` de l'hôte n'y donne aucun accès (`permission denied`). En **rootless**, le socket n'est pas
  à `/var/run/docker.sock` (mais sous `$XDG_RUNTIME_DIR`) et l'accès passe par l'UID, pas un groupe.
  Les deux → port-sync inopérant (détails + sources :
  [`docs/reference/2026-06-17-docker-desktop-rootless-socket.md`](reference/2026-06-17-docker-desktop-rootless-socket.md)).
- **`DOCKER_GID` absent ou faux** dans `.env` : ce doit être le GID du groupe `docker` de l'hôte
  (`getent group docker`).
- **Conteneur amuled mal nommé.** Le proxy n'autorise QUE `POST .../containers/amuled/restart` : le
  conteneur doit s'appeler **exactement `amuled`** (épinglé via `container_name: amuled` dans
  `examples/gluetun.yaml`). Sous un autre nom, le restart fait **404** et le port-sync ne fait rien.
- **Fournisseur sans port forwarding.** Le High-ID exige un provider à port forwarding
  (Proton/PIA/PrivateVPN/PerfectPrivacy) et `VPN_PORT_FORWARDING: "on"`.

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
- **Solution.** Trouvez d'abord le nom exact de votre volume (il dépend du nom du projet Docker
  Compose, par défaut le nom du dossier qui contient `examples/`) :
  ```bash
  docker volume ls | grep catalog-db
  # Exemple de sortie : local  emule-indexer_catalog-db
  ```
  Puis corrigez la propriété :
  ```bash
  docker run --rm -v <nom-du-volume>:/d alpine chown -R 999:999 /d
  # Avec le nom trouvé ci-dessus, par ex. :
  docker run --rm -v emule-indexer_catalog-db:/d alpine chown -R 999:999 /d
  ```

### Droits cross-user sur la quarantaine

- **Cause.** `amuled` est une image **tierce** lancée avec **son propre user** : conformément au
  choix de confinement acté ([CLAUDE.md § Confinement posture](../CLAUDE.md), 2026-06-17), on
  **n'impose pas** notre durcissement (cap_drop, user dédié, etc.) à amuled. Risque résiduel
  assumé : si amuled était compromis, l'attaquant accéderait au volume quarantaine. C'est un
  **non-objectif assumé pour v0.x**, pas un manque non vu (voir aussi
  [runbook d'administration § Limites connues](runbook-administration.md#limites-connues--follow-ups)).

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

## Outils de diagnostic

### Lancer une commande ponctuelle dans une image

Les images ont un entrypoint exec-form `["python","-m","<pkg>"]`. Pour exécuter autre chose, passez
par `--entrypoint` :

```bash
docker run --rm --entrypoint python <image> -c "import re2, rapidfuzz; print('ok')"
```

### Valider la configuration sans rien démarrer

```bash
uv run python -m emule_indexer validate-config
```

Charge + valide les 4 configs et sort en erreur (code ≠ 0) si l'une est invalide, **sans rien
démarrer**. À lancer avant un déploiement ou après une modification de config.
