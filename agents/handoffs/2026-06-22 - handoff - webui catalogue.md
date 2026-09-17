# Handoff — emule-indexer (WebUI de consultation du catalogue + extraction du moteur de matching)

> **Jalon** : `v0.16.0-webui` (annoté, **non poussé**). Fait suite à `v0.15.0-deploy-examples`.
> Spec : `docs/superpowers/specs/2026-06-22-webui-catalogue-design.md` (décisions `W-D1..W-D10`).
> Plan : `docs/superpowers/plans/2026-06-22-webui-catalogue.md` (13 tâches, exécution subagent-driven).

## 1. TL;DR — où on en est

Le projet a désormais **4 paquets** (le workspace en avait 2). Deux livraisons dans ce jalon :

1. **`catalog_matching`** (`packages/matching/`, dist `catalog-matching`) — le **moteur de matching extrait** du crawler (`emule_indexer.domain.matching` → paquet partagé pur). Déplacement **mécanique**, comportement inchangé ; le crawler le consomme comme dépendance. C'était la 1ʳᵉ tâche, prérequis du recalcul d'explication côté UI. Ajout : `MatchingEngine.explain(candidate, target_id) -> Explanation | None`.
2. **`catalog_webui`** (`packages/webui/`, dist `catalog-webui`) — un **3ᵉ service** déployable, **lecture seule**, qui rend le **contenu du catalogue** d'un nœud consultable par un humain (le seul trou que ni Prometheus/Grafana ni apprise ne comblaient).

Gate intégral **vert** : `catalog_matching` 170 · `crawler` 729 (+23 deselect) · `verifier` 142 (+8) · `catalog_webui` 93 tests — **100 % branch** partout ; `ruff`/`ruff format`/`mypy --strict` (272 fichiers)/`sqlfluff`/**garde templates** verts.

## 2. État vérifiable

```bash
( cd packages/matching && uv run pytest -q )   # 170 passed, 100% branch
( cd packages/crawler  && uv run pytest -q )   # 729 passed, 100% branch
( cd packages/verifier && uv run pytest -q )   # 142 passed, 100% branch
( cd packages/webui    && uv run pytest -q )   # 93 passed, 100% branch
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run sqlfluff lint packages/crawler/src
uv run python -m catalog_webui._dev.check_templates packages/webui/src/catalog_webui/adapters/templates  # 0 violation
git tag --list | grep v0.16
```

## 3. Ce qui a été bâti (catalog_webui)

- **Service Starlette SSR**, entrée `python -m catalog_webui`, config par env : `WEBUI_HOST` (déf `127.0.0.1`), `WEBUI_PORT` (déf `8080`), `CATALOG_DB`, `LOCAL_DB`, `TARGETS_CONFIG`, `MATCHER_CONFIG` (les 4 chemins requis → fail-fast si absents).
- **Routes GET** : `/` (tableau de bord : couverture **par cible**, y compris épisodes à zéro find, via `targets.yaml` + `match_decisions` + `coverage_for`) ; `/files` (explorateur filtré `?target=&tier=&verdict=&q=&page=`, pagination serveur) ; `/files/{ed2k_hash}` (détail : observations, décision stockée, verdicts, lien eD2k, **explication recalculée** via `catalog_matching` contre la config actuelle, `raw_meta` repliable) ; `/targets/{id}` (raccourci) ; `/node` (état concret : downloads, file de vérif, **ordonnancement** `scheduler_state`, identité du nœud) ; `/health` ; `/static`.
- **Architecture** (Clean léger) : `domain/` pur (`views`, `coverage`, `format`) ; `adapters/` (`db.open_ro`, `catalog_read`, `local_read`, `targets_read`, `matching_read`, `templates/`, `static/`) ; `composition/app.build_app(...)`.
- **Packaging** : `packages/webui/Dockerfile` (multi-stage uv, non-root 999, sans binaire) ; service `webui` dans `bricks/compose.core.yaml` (profils **observer** + **download**, bases montées `:ro`, durcissement conteneur, port exposé, healthcheck `/health`, **aucun réseau applicatif**). Runbook : `docs/runbook-administration.md` § WebUI.

## 4. Invariants tenus (ne pas régresser)

- **`W-D2` lecture seule de bout en bout** : toute connexion SQLite passe par `open_ro` (`mode=ro` + `PRAGMA query_only=ON`) ; **aucune écriture** dans `catalog_webui`.
- **`W-D3` frontière** : `catalog_webui` n'importe **que** `catalog_matching` (jamais `emule_indexer`/`download_verifier`) ; il lit les bases par **SQL direct** (couplage de **schéma** assumé, fixtures alignées sur les migrations). Crawler ⟂ verifier inchangé.
- **`W-D8` templates sans logique** : garde par **match de tokens** (`_dev/check_templates.py`, câblée pre-push + CI) ; toute dérivation d'affichage est **précalculée** en Python (view-rows), jamais dans le template. CSS **vendoré** (pas de CDN — le service est sans egress).
- **`W-D9` minimisation** : nulle part d'IP/`user_hash`/`nickname` ; uniquement des **compteurs** de sources. Le seul `node_id` affiché est l'**identité propre** du nœud (dashboard/node) ; le `node_id` par-observation a été retiré des vues.
- **`W-D5`** : Grafana reste l'outil ops (profil `monitoring`) ; le webui ne fait pas de séries temporelles.

## 5. Pièges appris (utiles au prochain chantier)

- **mypy racine unique sur 4 paquets** : `packages/crawler/tests/` est un **paquet** (`__init__.py`, imports `from tests.…`). On ne peut PAS avoir un 2ᵉ paquet `tests` (collision « Duplicate module 'tests' »). → `verifier`, `matching` et `webui` ont des **tests PLATS** (pas d'`__init__.py`), et leurs **noms de fichiers de tests doivent être globalement uniques** (convention `test_webui_*.py` ; `test_config.py` de matching renommé `test_matcher_config.py`). Fixtures partagées du webui = **`conftest.py`** (fixtures `catalog_db`/`local_db`), **pas** d'`from tests.fixtures…`.
- **sqlfluff ne lint que des `.sql`** : le SQL de lecture du webui est en **constantes Python** (comme les requêtes du crawler, elles aussi non lintées) → sqlfluff reste sur `packages/crawler/src` (migrations) ; pointer sqlfluff sur `webui/src` est un no-op (faux signal). Correction du SQL webui garantie par les **tests** (dont le cas « dernier par hash » avec tie-break `ts DESC, id DESC`).
- **hatchling** inclut les fichiers de données (`.html`/`.css`, `.sql`) sous le paquet **par défaut** → le wheel `catalog-webui` embarque bien `templates/` + `static/` (vérifié via `uv build` + `unzip -l`).
- **Dockerfile workspace** : `uv sync --package <X>` exige que **tous** les `pyproject.toml` membres soient bind-montés (résolution du glob `members = ["packages/*"]`), pas seulement le sous-graphe de deps (les Dockerfiles crawler/verifier font déjà ainsi).
- **La revue holistique finale a encore payé** : elle a attrapé 2 régressions spec **invisibles aux tests** (lien nav `/nodes` mort sur toutes les pages ; `scheduler_state` câblé reader→DTO mais jamais rendu). Garder cette revue transverse en fin de plan.

## 6. PAS validé contre le vrai matériel

- **Build Docker réel** : non exécuté ici (pas de daemon). `docker compose -f bricks/compose.core.yaml --profile observer config` passe ; le wheel embarque les assets ; mais l'image n'a pas été construite/lancée.
- **Point empirique #1 — lecture d'une base WAL *vivante* en `:ro` inter-conteneurs** : ouvrir une base WAL en `mode=ro` strict peut échouer (accès/écriture de l'index `-shm`). Repli documenté = **montage RW** du volume (retirer `:ro`) — `PRAGMA query_only=ON` garde la lecture seule applicative, le crawler reste l'unique writer de record. À **trancher au 1ᵉʳ déploiement** et consigner le verdict dans `agents/reference/2026-06-22-webui-wal-readonly.md`.
- **Rendu sur volume réel** : le smoke `compose_integration` n'a pas été étendu au webui (validation homelab manuelle pour l'instant) — piste si on veut un filet automatisé.

## 7. Étape suivante recommandée

1. **Valider l'inconnu #1** (WAL `:ro` vs RW + `query_only`) sur un vrai déploiement, figer le montage dans le compose, consigner dans `agents/reference/`.
2. (Option) **Étendre le smoke** `compose_integration` : le service `webui` monte, `/health` répond, une page se rend sur un volume DB peuplé en RO.
3. **Suites différées de la spec** (`§14`) : actions médiées (re-vérifier / re-télécharger via une table d'intentions consommée par le crawler — respecte le writer unique — + auth) ; **catalogue public/agrégé** sur un `merged-catalog.db` (réutilise le moteur SSR ; dépend de la **fusion multi-nœuds**, toujours au backlog).
4. Reliquats triviaux non bloquants : pas de test dédié `{% endif %}` seul dans la garde (le mécanisme est couvert via `{% endfor %}`).

## 8. Hors-scope verrouillé (rappels)
Pas d'écriture / d'actions en v1 ; pas de face ops/graphes (Grafana reste) ; pas de flux de logs bruts (état dérivé des DB seulement) ; pas d'auth dans le service (reverse proxy).
