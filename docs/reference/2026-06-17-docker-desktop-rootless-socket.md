# Docker Desktop / rootless et le socket : pourquoi le port-sync exige un Docker rootful natif (2026-06-17)

> **🔵 Mise à jour 2026-06-29** : le `docker-proxy` tourne désormais en **root**, ce qui rend le port-sync compatible avec **Docker Desktop** (Windows/macOS). Le proxy reste confiné (`cap_drop: ALL`, `read_only`, `no-new-privileges`, allowlist restreinte à `POST /containers/amuled/restart`). `DOCKER_GID` a été supprimé. Ce fichier est conservé pour l'historique de l'analyse.
>
> **🔴 Correctif 2026-07-02** : le passage « en root » du 2026-06-29 avait été fait en **supprimant** la ligne `user:`, sous l'hypothèse que l'utilisateur par défaut de l'image serait root. **C'est faux** : `docker inspect wollomatic/socket-proxy:1.12.2` → `Config.User = 65534:65534` (l'image est buildée `USER 65534`). Retirer `user:` laissait donc le proxy en **nobody** → `connect: permission denied` sur le socket → **boucle de restart**. Le root doit être posé **explicitement** : `user: "0:0"` (rétabli dans `deploy/gluetun.compose.yml`). Avec ce `user: "0:0"`, le port-sync fonctionne aussi bien sur Docker Desktop (socket `root:root`) que sur Docker rootful natif (socket `root:docker`, root étant propriétaire). Le mode **rootless** reste hors de portée (chemin de socket + modèle d'accès par UID différents, cf. corps ci-dessous).
>
> ⚠️ **Observation datée — 2026-06-17.** L'écosystème Docker Desktop évolue rapidement. Cette note
> reflète le comportement observé en mi-2026 ; vérifiez sur votre version courante avant de partir
> du principe que le comportement décrit est encore d'actualité. Le **résumé opérateur** (« Docker
> Desktop / rootless = pas de port-sync, Low-ID forcé ») reste vrai au moment de la rédaction.

> Le `docker-proxy` du port-sync (High-ID) tourne **non-root** et lit le socket Docker par **accès
> groupe**. Ce modèle n'existe que sur un **Docker rootful natif (Linux)**. Cette note valide, par des
> sources fiables, pourquoi il ne marche **ni** sous Docker Desktop **ni** en rootless — claim faite
> dans `runbook-administration.md` (« High-ID › Route A »), `runbook-troubleshooting.md` (« Le
> port-sync reste inopérant ») et le handoff du 2026-06-17 (§4). Origine : **observé localement** par
> Geoffrey (le proxy n'a jamais démarré sous Docker Desktop ; code de refus exact inconnu) **+ inféré**
> pour le mécanisme et le cas rootless — ici **confirmé en ligne**.

---

## Convention de fiabilité

- **SOURCE** — fait établi par une doc officielle Docker ou une issue acknowledged.
- **OBSERVÉ** — constat empirique sur la machine de Geoffrey (Docker Desktop `desktop-linux`).
- **NÔTRE** — conséquence pour notre `docker-proxy` / `bricks/compose.core.yaml`.

---

## Verdict en une ligne

Le `docker-proxy` (`user: 65534:${DOCKER_GID}`, monte `/var/run/docker.sock`, lit par **accès
groupe** `660 root:docker`) suppose un **Docker rootful natif**. Sous **Docker Desktop**, le socket
bind-monté est ré-exposé **`root:root`** dans le conteneur → le GID `docker` de l'hôte n'y donne aucun
accès. En **rootless**, le socket n'est pas à `/var/run/docker.sock` (mais sous `$XDG_RUNTIME_DIR`) et
l'accès passe par l'**UID**, pas un groupe. Dans les deux cas → **port-sync inopérant**.

---

## Le modèle qui marche : Docker rootful natif (Linux) — SOURCE

Socket `/var/run/docker.sock` = `srw-rw---- root:docker` (mode 660). Un process membre du groupe
`docker` (ou portant son GID) y accède. Notre proxy `65534:${DOCKER_GID}` (nobody + GID `docker` de
l'hôte) colle exactement à ce modèle.

> « Docker uses a socket that's only accessible to root and users in the docker group. »

---

## Docker Desktop (Windows/WSL2 et `desktop-linux`) — SOURCE

Docker Desktop fait tourner un **VM Linux** et **re-monte** le socket dans le conteneur (ce n'est pas
un passthrough : l'inode diffère). Depuis **Docker Desktop ~v2.48 / l'ère 4.19**, le socket
bind-monté apparaît **`root:root`** dans le conteneur (auparavant `root:docker`) → un user non-root
portant le GID `docker` de l'hôte obtient **`permission denied`** ; seul `root` (GID 0) accède.

- Issue [`docker/for-win#13447`](https://github.com/docker/for-win/issues/13447) — **ouverte /
  `status/acknowledged`** (Windows 11, Docker Desktop 4.19, backend WSL2) : « non-root users inside
  containers can no longer access a bind-mounted Docker socket … permission denied » ; socket vu
  `srwxr-xr-x 1 root root` ; root fonctionne, le non-root non.
- Fil [Docker Community Forums — docker.sock bind mount not preserving host ownership](https://forums.docker.com/t/docker-sock-bind-mount-not-preserving-host-ownership/140786) :
  « since v2.48 and v2.49 the ownership of docker.sock (inside the container) changes from root:group
  to root:root » ; « I now either need to run the containers as root or chown docker.sock back to
  root:docker on entry » ; inode différent → « Docker Desktop changed how the socket is mounted ».

---

## Rootless Docker — SOURCE

[Doc officielle « Rootless mode »](https://docs.docker.com/engine/security/rootless/) : le socket du
démon vit sous **`$XDG_RUNTIME_DIR/docker.sock`** (ex. `/run/user/1000/docker.sock`), **pas**
`/var/run/docker.sock` ; l'accès est lié à l'**UID** propriétaire — **aucun groupe `docker`
système**. `DOCKER_HOST=unix:///run/user/<uid>/docker.sock`.

Donc notre montage `/var/run/docker.sock` + accès par GID `docker` ne s'applique pas tel quel : il
faudrait monter le socket sous `$XDG_RUNTIME_DIR` et faire tourner le proxy avec l'**UID** du user
(pas un GID de groupe). La conclusion « pas tel quel » est correcte, **mais pour une raison
différente** de Docker Desktop (chemin + modèle d'accès, pas un re-mount `root:root`).

---

## NÔTRE — conséquence pour le déploiement

- Le `docker-proxy` de `bricks/compose.core.yaml` (`user: 65534:${DOCKER_GID}`, `-socketpath=/var/run/docker.sock`,
  mount `/var/run/docker.sock:ro`) suppose le **modèle rootful natif**.
- ⟹ le **port-sync (High-ID) ne fonctionne que sur un Docker rootful natif (Linux)**. Documenté en
  conséquence dans les runbooks (admin « Route A », dépannage « port-sync inopérant »).
- En **observer**, et en **download sans port-sync**, **aucun socket Docker n'est touché** → Docker
  Desktop convient pour ces cas (seul le High-ID automatique est hors de portée).

---

## OBSERVÉ (machine de Geoffrey, 2026-06-17)

Sous Docker Desktop (`Context: desktop-linux`), le `docker-proxy` **n'a jamais démarré** ; le test
`compose_integration` de l'allowlist a été abandonné (le code de refus exact de `wollomatic` reste
inconnu — le proxy n'a jamais tourné). Cohérent avec le re-mount `root:root` ci-dessus.

---

## Limites de cette validation (à savoir pour ne pas sur-affirmer)

- Les sources directes portent sur Docker Desktop **Windows/WSL2** et sur le **comportement général**
  de Docker Desktop (re-mount `root:root`). Pas de source isolant **`desktop-linux`** en propre, mais
  le re-mount par VM est un trait commun à Docker Desktop (Linux inclus) → l'extrapolation est
  raisonnable, et cohérente avec l'OBSERVÉ.
- Le **numéro de version exact** du passage `root:docker → root:root` vient d'un fil forum, pas d'un
  changelog officiel : le traiter comme « ère ~v2.48 / Docker Desktop 4.19 », pas comme un pin.
