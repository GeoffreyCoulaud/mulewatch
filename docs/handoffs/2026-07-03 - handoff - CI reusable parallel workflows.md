# Handoff — CI en workflows réutilisables + parallélisés (multi-arch natif)

> Branche `refactor/ci-reusable-parallel-workflows`, **rebase & merge dans `main`** (4 commits
> propres : `12929e3` docs → `e5affaa` actions → `af71ca8` validate → `27f2e68` callers).
> Spec + plan structurés (`docs/specs/` + `docs/plans/2026-07-02-ci-reusable-parallel-workflows.md`).
> **Pas de tag** (outillage CI, pas un subsystem versionné). Validé de bout en bout sur GitHub,
> **publish ghcr compris**.

## Point de départ

L'ancienne CI était un `.github/workflows/ci.yml` monolithe : un job `pipeline` séquentiel
(lint → tests → build 3 images amd64 `--load` → smoke) puis un job `publish` multi-arch **sous
QEMU** sur push `main`/tag. Le repo étant devenu public, demande : **scinder en workflows
réutilisables + paralléliser**, puis en cours de route **paralléliser aussi les plateformes**,
**durcir les actions** (pin SHA) et **tester le stack assemblé sur les deux archis**.

## Ce qui a été construit

Trois fichiers de workflow + deux composite actions généralisées.

- **`.github/workflows/validate.yml`** — `on: workflow_call`, `contents: read`. Le pipeline
  réutilisable, en jobs parallèles :
  - `lint` (ruff/mypy/sqlfluff/check_templates) ∥ `test` (matrix des 4 paquets, `fail-fast: false`) ;
  - `build` — **matrix orthogonale `package × arch`** (GitHub calcule le produit 3×2), runner
    **dérivé** de l'arch (`runs-on: ${{ matrix.arch == 'arm64' && 'ubuntu-24.04-arm' || 'ubuntu-latest' }}`),
    `outputs: type=cacheonly` + `write-cache: true` — c'est le seul écrivain du cache gha ;
    gaté derrière `[lint, test]` ;
  - `integration` — **matrix `arch`** (amd64 + arm64, runners natifs) : recharge crawler+verifier
    de la bonne arch **depuis le cache gha chaud** (`--load`) et lance le compose smoke. Prouve
    que le **stack assemblé tourne** sur arm64, pas seulement qu'il compile.
- **`.github/workflows/pr.yml`** — `on: pull_request` → `uses: ./.github/workflows/validate.yml`,
  `concurrency: cancel-in-progress`.
- **`.github/workflows/release.yml`** — `on: push` (`main` + `v*`) → `validate`, puis publie en
  multi-arch via le **pattern docker distribute** : `build-push` (matrix `package × arch`,
  push **par digest** sur runner natif, pas de QEMU) → `merge` (par paquet, `imagetools create`
  le manifest list avec les tags de `metadata-action`). `packages: write` isolé là.
- **`.github/actions/docker-image/action.yml`** — passthrough fin sur `docker/build-push-action` :
  le caller choisit l'export via `outputs` (`cacheonly` / `docker` `--load` / `push-by-digest`),
  expose le `digest`, et **`cache-to` conditionné par `write-cache`** (défaut `false`).
- **`.github/actions/setup-uv-env/action.yml`** — description en anglais.
- **Actions tierces pinnées au SHA de commit** (`@<sha> # vX.Y.Z`), dernières releases node24 :
  checkout v7.0.0, setup-uv v8.2.0, setup-buildx v4.2.0, build-push v7.3.0, login v4.3.0,
  metadata v6.2.0, upload-artifact v7.0.1, download-artifact v8.0.1. → **0 warning Node 20**.

Décisions clés (détaillées dans le spec) : reusable workflow justifié par **deux consommateurs**
(pr + release) ; cache gha unique transport (pas d'artifacts tarball) ; smoke sur les deux archis ;
matrices orthogonales ; pin SHA.

## Pièges appris (les vrais, coûteux)

1. **`astral-sh/setup-uv` ne publie pas de tag majeur mobile `v8`** (seulement `v1`…`v7` + tags
   complets `v8.x.x`). `@v8` → *« unable to find version v8 »*, échec de lint+test. Le **pin SHA
   contourne entièrement** ce problème (on ne dépend plus des tags mobiles). Toujours vérifier
   l'existence du ref (`gh api repos/<r>/git/ref/tags/vN`), pas juste `releases/latest`.
2. **Double `cache-to` sur le même scope gha = course → `failed to solve: not_found`.** L'ancien
   `docker-image` faisait toujours `cache-to` ; quand `integration` réexportait un scope déjà
   écrit par un `build` leg, l'export racait (passait 3 fois, cassait la 4ᵉ). **Correctif** :
   `write-cache` → un seul écrivain par scope (les build legs), les consommateurs en lecture
   (`integration`, `build-push`) font `cache-from` seul. Latent depuis l'intro de l'action.
3. **Runners ARM natifs** `ubuntu-24.04-arm` : gratuits/illimités pour repos publics ; suppriment
   le QEMU. Fallback `ubuntu-22.04-arm`.
4. **pyyaml lit la clé `on:` comme booléen `True`** (YAML 1.1) — n'affecte que les scripts de
   parse locaux, pas GitHub (parseur propre). Détail sans conséquence.
5. **`unknown/unknown` dans les manifests publiés** = attestations provenance de buildx (par
   défaut au `push`). Inoffensif (le pull choisit la bonne plateforme). Désactivable via
   `provenance: false` sur build-push si on veut des index propres — non fait (YAGNI).

## Validé sur GitHub (matériel réel)

- **`validate` complet** sur PR **et** sur `main` : 13 jobs verts, dont les 3 build legs arm64
  natifs **et le compose smoke arm64** (uv sync `--dev` résout des wheels aarch64 pour
  `google-re2`/`rapidfuzz`/`ruff` — pas de compilation).
- **Chemin publish** (`release.yml`) sur `main` : `build-push` (6 legs, push par digest) + `merge`
  (3 manifests) verts. **Confirmé** : `ghcr.io/geoffreycoulaud/emule-indexer-{crawler,verifier,webui}`
  publiées en **OCI image index amd64 + arm64**, tags `main` + `latest` + `sha`.

## Réglé depuis (post-merge)

- **Visibilité ghcr** : packages **publics** (déjà fait par Geoffrey).
- **Dependabot** : **activé** — `.github/dependabot.yml`, **3 écosystèmes** hebdo + PR groupée :
  `github-actions` (bumpe SHA **et** commentaire `# vX.Y.Z` des workflows + composite actions ;
  ignore les refs locales), `docker` (les `FROM` de `packages/*/Dockerfile` — pas les compose),
  `uv` (workspace Python : `pyproject.toml` + `uv.lock` racine). Commits `718d8e9` + `84ee737`.

## PAS encore fait / à décider

- **Branch protection** : si activée, viser les **nouveaux noms de checks** (`validate / lint`,
  `validate / test (crawler)`, `validate / build (crawler, amd64)`, `validate / integration (amd64)`,
  `validate / integration (arm64)`, …).
- Aucune image n'a été **déployée sur un vrai nœud** ici (le smoke valide le câblage du stack, pas
  un run de prod). Voir les runbooks `docs/runbooks/` pour un déploiement réel.

## Prochaine étape suggérée

Régler la branch protection selon l'intention, puis reprendre le fil produit (le lost-media Keroro)
là où le handoff précédent s'était arrêté.
