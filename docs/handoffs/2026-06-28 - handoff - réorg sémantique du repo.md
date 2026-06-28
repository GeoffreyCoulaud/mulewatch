# Handoff — réorganisation sémantique du repo (2026-06-28)

## Ce qui a été fait

Réorganisation **purement structurelle** (aucun changement de comportement applicatif) pour une
arborescence où chaque chose est à sa place évidente. Déclencheur : la pollution de la racine
(`bricks/` opaque, fichiers compose/config dispersés brisant la localité). Branche/worktree
`refactor/semantic-repo-layout`.

### Nouvelle arborescence

```
deploy/                         ← tout le nécessaire pour faire tourner une vraie stack
  .env.example  → NON : reste à la RACINE (convention + auto-chargé depuis PWD, cf. pièges)
  compose.base.yaml             (ex-bricks/compose.core.yaml)
  examples/{gluetun,sans-vpn-highid,sans-vpn-lowid}.yaml
  config/{crawler/*, verifier*.yaml, prometheus.yml, grafana/**}   (ex-config/ racine)
tests/smoke/
  compose.yaml                  (ex-compose.smoke.yaml racine) — rejoint SES configs
  {crawler,targets,matcher,local.*}.yaml
docs/                           ← aplati, noms transparents
  plans/  specs/  handoffs/  reference/   (ex-docs/superpowers/{plans,specs} ; handoffs/reference inchangés)
  runbooks/{deployment,administration,troubleshooting}.md   (ex-docs/runbook-*.md, préfixe retiré)
  README.md  legal-and-privacy.md  testing-guide.md
```

`bricks/`, `examples/` (racine), `config/` (racine), `docs/superpowers/` : **supprimés** (vides après
déplacement). `.superpowers/sdd/` : **détracké** (`task-9-report.md` était suivi par accident) **et
supprimé du disque** (décision opérateur : c'était du scratch d'outillage, pas un artefact à garder).

### Règle frozen/living appliquée aux chemins

Seules les **références fonctionnelles** (code, compose, CI, `.gitignore`/`.gitattributes`) et les
**docs vivantes orientées lecteur courant** (`CLAUDE.md`, les 2 README, runbooks, `legal-and-privacy`,
`testing-guide`, `.env.example`) ont eu leurs chemins corrigés. Les **documents datés**
(`handoffs/`, `reference/`, `plans/`, `specs/`) gardent leurs chemins **d'époque** : ce sont des
registres, pas des docs à réécrire. → un plan de juin qui dit « voir `bricks/…` » décrit l'état d'alors.

## Mécanique compose (le point délicat — recâblé soigneusement)

Docker Compose résout les chemins relatifs **contre le project-directory** (défaut = dossier du 1er
`-f`), surchargé par `--project-directory` (confirmé via docs Docker à jour). Conséquences :

- **examples** : `include: ../compose.base.yaml` + `project_directory: ..` (= `deploy/`, résolu depuis
  `deploy/examples/`). Inchangé en texte sauf le `path`.
- **compose.base.yaml** : `context: ..` (= repo root, pour que les Dockerfiles voient `packages/`) ;
  bind-mounts `./config/...` (= `deploy/config/...`). Marche aussi en standalone
  (`docker compose -f deploy/compose.base.yaml config`, project-dir = `deploy/`).
- **tests/smoke/compose.yaml** : project-dir **épinglé au repo root** via `--project-directory`
  (dans `_run` du test ET la commande CI). Donc `context: .`, binds `./tests/smoke/*.yaml`, verifier
  réel `./deploy/config/verifier.yaml` — tout relatif au repo root, déterministe quelle que soit la
  version de compose.

Défauts argparse (`__main__.py`) repointés `config/*.yaml` → `deploy/config/crawler/*.yaml` (+ les 6
assertions de `test_main.py` et le `_CONFIG` de chargement réel). Ces défauts ne servent qu'au run
local nu ; les déploiements passent les `--crawler …` explicitement.

## État de validation

- **Gate unitaire complet : VERT** (lancé dans le sandbox) — matching 183, crawler 736 (+23
  désélectionnés), verifier 176 (+8), webui 97 ; ruff/format/mypy/sqlfluff/templates OK.
- **NON validé (Docker indisponible dans le sandbox)** — à lancer sur ta machine :

  ```bash
  # 1. Rendu des 3 stacks d'exemple (config sans daemon) + base standalone
  GRAFANA_PWD=x docker compose -f deploy/compose.base.yaml config >/dev/null && echo base OK
  for e in gluetun sans-vpn-lowid sans-vpn-highid; do
    WIREGUARD_PRIVATE_KEY=x AMULE_EC_PASSWORD=x GRAFANA_PWD=x SERVER_COUNTRIES= DOCKER_GID=0 \
      docker compose -f deploy/examples/$e.yaml --profile download --profile monitoring config >/dev/null \
      && echo "$e OK"
  done
  # 2. Smoke stack (rendu + bind sources résolus contre le repo root via --project-directory)
  AMULE_EC_PASSWORD=x docker compose --project-directory . -f tests/smoke/compose.yaml \
    --profile download config >/dev/null && echo smoke-config OK
  # 3. La suite d'intégration compose (build + up + le test_entrypoint_config_renders des examples)
  ( cd packages/crawler && uv run pytest -m compose_integration --no-cov -q )
  ```

  Si le smoke échoue sur la résolution de chemins : le `--project-directory .` est la clé ; vérifier
  qu'il est bien présent dans `_run` (`test_compose_smoke.py`) et la commande CI (`images.yml`).

## Pièges appris

- `.env` est auto-chargé par compose depuis **PWD** (le dossier d'où tu lances la commande), pas
  depuis le dossier du compose. D'où `.env.example` **gardé à la racine** (et non sous `deploy/`) :
  avec la commande documentée (`docker compose -f deploy/examples/… up` depuis la racine), compose
  cherche `.env` à la racine. Le mettre dans `deploy/` aurait été un piège silencieux.
- `runbook-administration.md` § env webui cite le **texte littéral** du bind-mount `./config/crawler/
  targets.yaml` (qui reste `./config/...` car project-dir = `deploy/`) : ces 2 mentions ont été
  **laissées intactes** (elles décrivent le fichier compose, pas un chemin repo).

## Prochaine étape

Lancer les validations Docker ci-dessus. Si vertes → la réorg est complète. Sinon, les corrections
sont localisées (chemins compose / `--project-directory`).
