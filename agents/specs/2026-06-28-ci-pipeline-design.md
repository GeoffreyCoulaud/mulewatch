# Design — pipeline CI consolidé (2026-06-28)

## Contexte & motivation

CI actuelle, deux workflows :
- `ci.yml` — `on: push, pull_request` → un job `check` : lint (ruff/format/mypy/sqlfluff/templates)
  + les 4 pytest unitaires.
- `images.yml` — `on: push main, tags v*` → job `smoke` (build via compose + `compose_integration`)
  puis job `publish` (matrice build-push multi-arch).

**Trous identifiés :**
1. Le **build d'images et les tests d'intégration ne tournent PAS sur les PR** (seulement au push `main`).
   Une PR peut casser le câblage compose / un Dockerfile sans que rien ne le signale avant le merge.
2. **`webui` n'est jamais buildé en CI** (ni smoke ni publish) ni publié — alors que
   `deploy/compose.base.yaml` tire `ghcr.io/…-webui:latest`. Régression Dockerfile invisible + image
   inexistante au déploiement.
3. Deux workflows qui se chevauchent, sans réutilisation factorisée.

## Objectifs

- Un pipeline **consolidé**, **fail-fast**, qui fait tourner **lint + unit + build + intégration sur
  chaque PR** (jamais de publish sur PR).
- **Réutilisation par composite actions** (pas de reusable workflows : on veut un job unique sur le
  chemin fréquent, pas des jobs séparés facturés à la minute).
- **Publish isolé** (moindre privilège + garantie « jamais sur PR »).
- Combler les trous : **build + publish des 3 images** (crawler, verifier, webui).

Non-objectif (YAGNI) : path filters conditionnels (tout tourne à chaque PR pour l'instant).

## Architecture

### Déclencheurs

```yaml
on:
  pull_request:            # → job pipeline (check + build + integration). PAS de publish.
  push:
    branches: [main]       # → pipeline + publish
    tags: ["v*"]           # → pipeline + publish
```
Un push sur une branche de feature ne déclenche rien seul (la PR couvre) → pas de run en double.

### Job `pipeline` (toutes les triggers) — `permissions: contents: read`

Étapes séquentielles (fail-fast, 1ᵉʳ rouge = stop) :
1. `actions/checkout`
2. `./.github/actions/setup-uv-env` (composite)
3. **lint** : `ruff check .` · `ruff format --check .` · `mypy` · `sqlfluff lint packages/crawler/src`
   · `check_templates`
4. **unit** : `( cd packages/<p> && uv run pytest )` pour matching, crawler, verifier, webui
5. `docker/setup-buildx-action` puis **build** : `./.github/actions/docker-image` avec
   `push=false, platforms=linux/amd64` pour les 3 packages → images locales taguées `ci-<sha>`
   (cache `type=gha` alimenté)
6. **integration** : `pytest -m compose_integration` avec `IMAGE_TAG=ci-<sha>` → la stack smoke
   consomme les images **déjà construites** (pas de rebuild)

### Job `publish` (`needs: pipeline`, `if: push && (main || tag v*)`) — `permissions: contents: read, packages: write`

N'existe **pas** sur les PR (gardé au niveau job). Étapes :
1. `actions/checkout` · `docker/setup-qemu-action` · `docker/setup-buildx-action` ·
   `docker/login-action` (ghcr, `GITHUB_TOKEN`)
2. `./.github/actions/docker-image` avec `push=true, platforms=linux/amd64,linux/arm64` pour les 3
   packages, tags via `docker/metadata-action` — l'amd64 est réutilisé depuis le **cache `type=gha`**
   produit par `pipeline`.

### Composite actions (`.github/actions/`)

| Composite | Rôle | Inputs |
|---|---|---|
| `setup-uv-env` | `astral-sh/setup-uv@v5` (cache on) + `uv sync --dev` | — |
| `docker-image` | build d'**un** package via `docker/build-push-action` ; mono-arch `--load` **ou** multi-arch `--push` selon `push`. Cache `type=gha` (from+to). | `package`, `push` (bool), `platforms`, `tags` |

`docker-image` est la brique clé : appelée 6×/run (3 build + 3 publish). Elle **n'installe pas**
buildx/QEMU — ce sont les jobs qui le font avant l'appel (`pipeline` : buildx ; `publish` : QEMU +
buildx + login ghcr). Le composite se limite à `build-push-action` (paramétré load/push + cache gha).

## Détails & points d'attention

- **Consommation des images pré-buildées** : la stack smoke référence
  `ghcr.io/geoffreycoulaud/emule-indexer-<pkg>:${IMAGE_TAG:-latest}`. Le job `pipeline` build et tague
  ces images en `:ci-<sha>` (amd64, `--load`) et passe `IMAGE_TAG=ci-<sha>` au test. **Conséquence :
  `test_compose_smoke.py` doit honorer `IMAGE_TAG` et ne plus faire `--build`** (le build est
  désormais une étape CI distincte) ; `test_build_succeeds` est reformulé en « les images attendues
  sont présentes / la stack démarre avec » plutôt que « compose build réussit ». webui est buildé mais
  n'entre pas dans la stack smoke (validé à la compilation, pas à l'exécution compose).
- **Nom d'image en minuscules** : ghcr impose le minuscule. La compose code en dur `geoffreycoulaud`
  (minuscule) ; le build CI doit produire le **même** nom (donc minuscule, pas `${{ github.repository }}`
  brut qui garde la casse `GeoffreyCoulaud`). `docker/metadata-action` minusculise pour le publish ;
  pour le tag local consommé par compose, forcer le minuscule.
- **Pas de secret externe** : seul `GITHUB_TOKEN` (intégré) sert au login ghcr.
- **Runners** : tout sur `ubuntu-latest` (Docker + buildx dispo). QEMU uniquement dans `publish`.

## Erreurs & garanties

- **Fail-fast** : chaque étape qui sort non-zéro stoppe le job ; `publish` est conditionné à la
  réussite de `pipeline` (`needs`).
- **Jamais de publish sur PR** : double garantie — (1) le job `publish` a un `if:` qui ne matche que
  `push` sur `main`/tag (sur une PR il ne se matérialise pas) ; (2) seul `publish` détient
  `packages: write`, et les PR de fork ont de toute façon un `GITHUB_TOKEN` read-only.

## Validation

Le pipeline se valide **lui-même sur la PR qui l'introduit** : la trigger `pull_request` lance
`pipeline` (lint + unit + build + integration) — c'est exactement la première preuve que le build et
le smoke Docker passent en CI (ce qui n'avait jamais tourné en PR avant). `publish` ne sera exercé
qu'au premier push `main`/tag après merge.

## Migration

- Réécrire `ci.yml` (job `pipeline` + job `publish`).
- Supprimer `images.yml` (fusionné).
- Ajouter `.github/actions/setup-uv-env/action.yml` et `.github/actions/docker-image/action.yml`.
- Adapter `packages/crawler/tests/integration/test_compose_smoke.py` (honorer `IMAGE_TAG`, retirer
  `--build`, reformuler `test_build_succeeds`).
- Le `--project-directory` du smoke (introduit par la réorg) est conservé tel quel.
