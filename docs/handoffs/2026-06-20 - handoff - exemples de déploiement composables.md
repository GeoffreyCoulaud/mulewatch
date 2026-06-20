# Handoff — exemples de déploiement composables (v0.15.0) — 2026-06-20

> Point d'entrée pour la prochaine session. Sujet mené brainstorming → spec → plan → exécution
> **subagent-driven** (un implémenteur frais par tâche + revue spec/qualité + revue holistique
> finale). Arbre **propre**, gate **vert**, suite Docker validée. Jalon **`v0.15.0-deploy-examples`**
> (annoté, non poussé) sur `70bd834`.

## 1. Ce qui a été construit

Le sujet « support sans gluetun + compose chercheur clé en main » du backlog (handoff 2026-06-17 §6).
Spec : `docs/superpowers/specs/2026-06-20-deploiement-exemples-design.md` (15 sections). Plan :
`docs/superpowers/plans/2026-06-20-deploiement-exemples.md` (9 tâches).

**Nouvelle topologie de déploiement** — l'ancien `compose.yaml` unique éclaté en :
- **`bricks/compose.core.yaml`** — brique commune `include`-ée par chaque exemple : `crawler-observer`
  /`crawler-download` (2 variantes profilées via ancre YAML `&crawler-common`), `verifier`,
  `freshclam`, `prometheus`, `grafana`, tous les réseaux/volumes. Définit **tout sauf amuled & le VPN**.
- **`examples/{gluetun,sans-vpn-lowid,sans-vpn-highid}.yaml`** — 3 points d'entrée distincts ; chacun
  `include` la brique (syntaxe longue, `project_directory: ..`) et ajoute **seulement** `amuled`
  (+ gluetun/docker-proxy pour la stack gluetun).
- **Mode `observer`/`download` + `monitoring`** = profils Compose. **gVisor** = knob
  `CONTAINER_RUNTIME=runsc` (plus de `compose.hardening.yml`).
- **`config/` rangé par owner** : `config/crawler/{crawler,matcher,targets}.yaml` +
  `config/crawler/{observer,download}.example.yaml` (gitignorés une fois copiés) ; `config/verifier.yaml`,
  `config/prometheus.yml` à plat ; `config/grafana/` (datasource + dashboard provisionnés).
- **Monitoring clé en main** : Prometheus scrape `crawler:9090` + `verifier:8000`, Grafana avec
  dashboard pré-fourni.
- **`compose.smoke.yaml`** réécrit **autonome** (n'override plus l'ancien compose).
- **`test_compose_smoke.py`** étendu : rebase sur le smoke autonome + 9 cas `test_entrypoint_config_renders`.
- **`docs/runbook-deployment.md`** réécrit en pas-à-pas guidé (matrice de choix fonctionnalités × stack,
  prérequis par stack, étapes cross-platform Windows compris) ; admin/dépannage/`CLAUDE.md` alignés
  `full`→`download` + gVisor knob ; `.gitattributes` (LF).

13 commits `5d6505c`→`70bd834` ; détail par tâche dans le plan.

## 2. État courant

- Branche `main`, arbre **propre**. Gate **vert (vérifié)** : crawler **896 passed / 100 % branch**,
  verifier **142 / 100 %**, ruff + format + mypy + sqlfluff OK.
- **`compose_integration` : 13 passed** (machine de Geoffrey) — build des 2 images + smoke
  download/observer/fail-fast + les 9 `docker compose config` des points d'entrée.
- **Validé en vrai (Geoffrey)** : `docker compose config` sur la brique + les 3 points d'entrée
  (include, forward-refs, ancre/merge, `project_directory`) — tous verts (CHECKPOINT A).

## 3. Décisions actées / pièges appris (importants pour la suite)

- **`include` est additif, pas un override** (doc Compose : conflit de noms → warning, pas de merge ;
  chaque fichier inclus doit être cohérent seul). Conséquences en cascade :
  - tout le commun (réseaux/volumes/services) vit **une seule fois** dans `core` ; `amuled`/VPN
    seulement dans les points d'entrée.
  - **gVisor ne peut plus être un override `-f`** (il toucherait un service de `core` inclus → conflit)
    → devenu le knob `CONTAINER_RUNTIME`.
  - **le smoke ne peut plus override `core`** → réécrit autonome.
- **Forward-ref validé** : un service du point d'entrée (`amuled`) peut référencer un réseau/volume
  défini dans la brique incluse (test éclair `docker compose config` OK). Les **ancres + merge YAML**
  (`<<: *anchor`) sont l'idiome Compose supporté (le `<<` fusionne des mappings, pas des listes).
- **Host EC unifié `amuled`** partout (alias réseau sur gluetun côté gluetun) → un seul `local.yaml`
  par mode.
- **La revue holistique finale a rattrapé 2 Critical que les revues par-tâche ont ratés** :
  1. `test_main.py` lisait les configs **déplacées** (`config/{crawler,targets,matcher}.yaml`,
     `config/local.example.yaml`) → **gate crawler silencieusement cassé** (aucune tâche n'avait
     relancé `pytest` crawler après la réorg config).
  2. `.github/workflows/images.yml` référençait `compose.yaml` (supprimé) + `--profile full`.
  **Leçon générale** : quand on déplace/renomme des fichiers du dépôt, grepper **tous** les
  consommateurs — tests unitaires, CI, docs, pas seulement le diff du déplacement.
- **`compose config` n'exige ni daemon ni existence des sources de bind-mount** → idéal pour valider
  l'assemblage en CI/local sans rien lancer.

## 4. Ce qui RESTE à valider (au déploiement réel — pas couvert par le sandbox/Docker Desktop)

1. **Stack B (gluetun High-ID via port-sync)** : exige un **hôte Linux Docker rootful** + un VPN avec
   port forwarding ; Docker Desktop ne peut pas (socket refusé). Seul le `config` statique est validé.
2. **Stack D (High-ID statique)** : joignabilité réelle = redirection du port sur la box + pare-feu ;
   et **`LISTEN_PORT` doit égaler le port d'amuled** (`amule.conf Port=`, défaut 4662 → marche sans
   config ; le changer exige d'éditer amule.conf — documenté, non vérifié empiriquement).
3. **clamav réel** (calibrage `RLIMIT_AS_BYTES_CLAMAV`/`mem_limit`) — toujours en attente (héritage).
4. **DV10** : au 1er vrai download, confirmer que l'Incoming d'amuled = `staging_dir` monté.

## 5. Étape suivante (reste du backlog, cf. triage 2026-06-19)

- **WebUI** d'exploration du catalogue (basse priorité, potentiellement visuel).
- Les **validations machine réelle** ci-dessus, lors d'un premier déploiement de nœud.
- ~~Sans-gluetun + clé en main~~ : **fait** (ce jalon).

## 6. Méthode (à reconduire)

Subagent-driven : implémenteur frais (modèle économique pour la transcription, standard pour le code
/jugement) → revue spec+qualité par tâche → **revue holistique finale sur le modèle le plus capable**
(elle a payé : 2 Critical inter-fichiers). Validations Docker lancées par Geoffrey via `!` (sandbox
sans Docker). Ledger de progression dans `.git/sdd/progress.md`.
