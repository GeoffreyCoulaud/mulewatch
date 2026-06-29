# 2026-06-29 — handoff — compatibilité Docker Desktop + retrait gVisor

## État du projet

Le milestone `v0.18.0-docker-desktop` vient d'être mergé (PR #2). Toutes les stacks de déploiement sont désormais compatibles Docker Desktop (Windows, macOS, Linux).

## Ce qui vient d'être fait

### Compatibilité Docker Desktop
- Le `docker-proxy` (port-sync) tourne en **root** au lieu de `nobody:docker-gid`. Le proxy est confiné (`cap_drop: ALL`, `read_only`, `no-new-privileges`, allowlist `POST /containers/amuled/restart` uniquement) — root n'ajoute aucun risque.
- `DOCKER_GID` supprimé du `.env.example`, de `gluetun.yaml`, des runbooks, et du test smoke.
- **Toutes les stacks (A/B/C/D) sont compatibles Docker Desktop.** La Stack B (High-ID auto avec gluetun + port-sync) fonctionne désormais sur Windows/macOS.

### Retrait de gVisor
- `runtime: ${CONTAINER_RUNTIME:-runc}` supprimé de `compose.base.yaml` (3 occurrences : crawler, verifier, webui).
- `CONTAINER_RUNTIME` supprimé de `.env.example`.
- Section « Durcissement noyau (gVisor) » supprimée de `administration.md`.
- La baseline portable (`cap_drop: ALL`, `read_only`, `no-new-privileges`, `internal`, seccomp blocklist, rlimits) est inchangée et suffisante.
- Notes de dépréciation ajoutées sur les specs concernées (`ring-noyau-design.md`, `deploiement-exemples-design.md`).
- Commentaires `spawn.py` mis à jour (gVisor retiré, cgroups seul mentionné).

## Pièges appris

- **Ne pas confondre "bloquant Docker Desktop" et "bloquant gluetun".** gluetun fonctionne sous Docker Desktop (le wiki officiel le benchmark sur WSL2). Le seul bloquant était le `docker-proxy` et ses permissions socket.
- **Le socket Docker sous Docker Desktop est `root:root` dans le conteneur** (depuis ~v4.19). Un accès par GID `docker` ne fonctionne pas ; seul root passe. Le fix le plus simple (proxy root confiné) est aussi le plus robuste.
- **Podman n'est pas la solution pour Windows** dans notre cas : le mode rootless ne supporte pas `NET_ADMIN`/TUN (limitation noyau), et Podman Desktop sur Windows a les mêmes prérequis WSL2 que Docker Desktop sans simplifier l'installation. Sources : [`rootless.md` officiel Podman](https://github.com/containers/podman/blob/main/rootless.md), [discussion #25044](https://github.com/containers/podman/discussions/25044), [gluetun wiki TUN errors](https://deepwiki.com/qdm12/gluetun-wiki/7.1-tun-device-errors).

## Ce qui n'est PAS validé contre du vrai matériel

- **Stack B (port-sync) sous Docker Desktop Windows réel** : le fix est fondé sur l'analyse du bug Docker Desktop (`docker/for-win#13447`) et les retours communautaires, mais n'a pas été testé sur une machine Windows physique avec Docker Desktop + ProtonVPN. La logique est solide (root peut accéder au socket `root:root`), mais une confirmation empirique serait souhaitable.
- **Stack B sous Docker Desktop macOS** : idem, non testé physiquement.
- **Toutes les stacks sous Docker Desktop Linux (`desktop-linux`)** : Geoffrey a observé que le proxy ne démarrait pas en juin 2026 ; le fix (proxy root) devrait résoudre, mais n'a pas été re-vérifié sur cette plateforme.

## Prochaine étape suggérée

La prochaine priorité naturelle est de **tester empiriquement la Stack B sur Docker Desktop Windows** avec un VPN ProtonVPN (port forwarding activé) pour valider que le port-sync fonctionne de bout en bout. Si ça passe, mettre à jour la note `docs/reference/2026-06-17-docker-desktop-rootless-socket.md` avec le résultat.

En parallèle, la question de l'empaquetage "simple" pour utilisateurs Windows (discutée en session) reste ouverte : même si Docker Desktop est compatible, l'installation reste lourde (Docker Desktop + WSL2 + config .env + choix de stack). Un wrapper qui automatise tout ça (genre un `.exe` qui fait le setup) pourrait être le prochain chantier.
