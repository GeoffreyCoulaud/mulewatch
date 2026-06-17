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
  amuled **partage le réseau de gluetun** : tant que le tunnel est down, amuled n'a aucune sortie. Si
  le tunnel ne monte pas, corrigez le VPN (clé WireGuard, fournisseur, `SERVER_COUNTRIES`) puis
  relancez ; une fois gluetun « up », redémarrez amuled : `docker compose restart amuled`.
- **Autre cause : image amuled dérivée du pin.** Seules les versions **≥ 3.0.0** font l'auto-amorçage.
  Une image `latest` ou `2.3.3-*` casse l'amorçage du premier run **sans erreur évidente**. Gardez
  `ngosang/amule:3.0.0-1` dans `compose.yaml`.

### Le statut « Low-ID » apparaît dans les logs

- **Ce n'est pas une panne.** Low-ID est l'**état normal** par défaut : recherche, catalogage et
  téléchargement fonctionnent ; seule la joignabilité est sous-optimale (moins de sources directes).
- **Pour passer en High-ID** (optionnel), voir « High-ID (optionnel) » dans le
  [runbook d'administration](runbook-administration.md).

---

## Mode full (téléchargement + vérification)

### Le crawler redémarre en boucle au démarrage (mode full)

- **Cause.** En full, le crawler **refuse de démarrer** si le verifier ne répond pas (pas de
  téléchargement sans vérification) ; son `restart: unless-stopped` le relance tant que le verifier
  n'est pas sain.
- **Solution.** Démarrez le verifier d'abord, puis le reste :
  ```bash
  docker compose --profile full up -d verifier
  docker compose --profile full up -d
  ```

### Un fichier manifestement sain ressort `suspicious`

Trois causes possibles, de la plus probable à la moins :

1. **La base clamav n'est pas encore synchronisée.** Au premier démarrage en full, le sidecar
   `freshclam` télécharge ~300–500 Mo (quelques minutes) ; tant qu'elle manque, clamav rend
   `suspicious` par défaut (jamais `clean` sans base). **C'est transitoire** — attendez la fin de la
   première synchro, le fichier sera re-scanné.
2. **Le scan se fait tuer faute de mémoire.** `clamscan` charge toute la base en RAM ; si les limites
   sont trop basses, l'OOM-killer tue le scan avant la fin → `suspicious`. Augmentez
   `RLIMIT_AS_BYTES_CLAMAV` / `RLIMIT_CPU_S_CLAMAV` et le `mem_limit` du verifier (voir
   [runbook d'administration](runbook-administration.md), « Analyse antivirus (clamav) »).
3. **Accroc de droits sur la quarantaine** (voir « Droits cross-user sur la quarantaine » plus bas).

### Le fichier fini n'est pas récupéré (reste dans l'IncomingDir, non catalogué)

- **Cause.** Une des contraintes du mode full n'est pas respectée : IncomingDir d'amuled ≠ dossier de
  quarantaine, volume sur un FS non-Linux, catégories amuled actives, ou bibliothèque partagée
  pré-existante trop grosse.
- **Solution.** Revoyez l'encadré « Contraintes du mode full » du
  [runbook de déploiement](runbook-deployment.md) (les 4 conditions).

---

## High-ID / port-sync

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
  `compose.yaml`). Sous un autre nom, le restart fait **404** et le port-sync ne fait rien.
- **Fournisseur sans port forwarding.** Le High-ID exige un provider à port forwarding
  (Proton/PIA/PrivateVPN/PerfectPrivacy) et `VPN_PORT_FORWARDING: "on"`.

---

## Stockage & droits

### Volume `/data` déjà peuplé : permission refusée

- **Cause.** Le crawler tourne en `user: 999`. Les images pré-créent `/data/{catalog,local,quarantine}`
  en `nonroot`, donc un volume nommé **vide** hérite de la bonne propriété. Mais un volume **déjà
  peuplé** (root-owned) garde ses droits.
- **Solution.** Corrigez la propriété à la main :
  ```bash
  docker run --rm -v emule-indexer_catalog-db:/d alpine chown -R 999:999 /d
  ```

### Droits cross-user sur la quarantaine

- **Cause.** `amuled` est une image **tierce** lancée avec **son propre user** (on ne lui impose pas
  notre durcissement). Le volume `quarantine` est écrit à la fois par amuled (fichiers finis) et par
  le crawler (déplacement atomique) ; un accroc de droits cross-user peut survenir au tout premier
  vrai téléchargement.
- **Solution.** À surveiller au premier téléchargement réel ; si un déplacement échoue pour cause de
  droits, alignez la propriété du volume `quarantine`.

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
