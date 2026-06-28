# Spec — emule-indexer : WebUI (consultation du catalogue, lecture seule)

> **Sous-projet** : la « petite UI de consultation du catalogue / état du crawler » du backlog
> (handoff `2026-06-15 - handoff - post-E checkpoint (backlog).md` §3.3 / §4a — basse prio, **pas
> abandonnée** ; MVP `2026-06-10-crawler-mvp-design.md` §2 la classait « UI web d'admin dépriorisée »).
> Un **3ᵉ service** déployable à côté du crawler et du verifier, qui **lit** les bases et les rend
> consultables par un humain — il ne *remplace pas* l'observabilité opérationnelle (Prometheus +
> Grafana + apprise) ; il comble le seul trou : **explorer le contenu du catalogue** sans sortir le
> `sqlite3` CLI.
>
> **Tâche 1 = une extraction.** Pour recalculer l'explication d'un match côté UI sans coupler le
> webui à l'app crawler, on **extrait d'abord le moteur de matching** (domaine pur) dans un **paquet
> partagé** `catalog_matching` consommé par le crawler ET le webui. Mécanique, sans changement de
> comportement, gate-vert comme critère de fin — exactement le patron de la restructuration workspace
> de D-verify (`2026-06-13-verification-pipeline-design.md` §3).
>
> Réfs : modèle de données `2026-06-11-data-model-design.md` (tables/append-only/WAL/`node_id`) ;
> observabilité `2026-06-15-observability-design.md` (ce qui est DÉJÀ couvert — à ne pas refaire) ;
> packaging `2026-06-14-packaging-design.md` (images Docker, compose, profils, durcissement) ;
> verification-pipeline `2026-06-13-verification-pipeline-design.md` (patron de service Starlette,
> frontière de paquet, restructuration workspace). MVP §1 (le sujet = le fichier), §3 (observer/full).
> Architecture du moteur de matching : voir `CLAUDE.md` (section « Architecture — le moteur de matching »).

---

## 1. But & périmètre

**But** : offrir une **interface web en lecture seule** qui rend le **contenu du catalogue** d'un
nœud consultable par un humain — vue par épisode cible (y compris les épisodes **toujours
introuvables**), explorateur de fichiers filtrable, détail d'un fichier (observations, **explication
de match recalculée**, verdict de vérification, lien eD2k reconstruit) et un résumé **concret** de
l'état du nœud. Service **séparé**, **sans écriture**, **sans auth** (déléguée à un reverse proxy),
**poll simple**. Pré-requis livré par ce même sous-projet : l'**extraction du moteur de matching** en
paquet partagé.

**Dans le périmètre** :
- **Tâche 1 — extraction du moteur de matching** (cf. §3) dans `packages/matching` (paquet
  `catalog_matching`, dist `catalog-matching`) : déplacement **mécanique** du domaine pur depuis
  `emule_indexer.domain.matching`, le crawler le consomme désormais comme dépendance. Gate-vert.
- **Nouveau paquet `catalog_webui`** (`packages/webui`, dist `catalog-webui`) : app **Starlette** +
  templates **Jinja2 sans logique** (SSR), entrée **uvicorn** (`python -m catalog_webui`).
- **Read model SQLite read-only** sur `catalog.db` et `local.db` (`PRAGMA query_only`, jamais via les
  repos du crawler).
- **Énumération des cibles** depuis `config/targets.yaml` (RO) pour afficher AUSSI les cibles à zéro
  observation, **et** pour reconstruire le moteur côté UI (recalcul de l'explication).
- **Pages SSR** (§5), **CSS vendoré** servi par Starlette (pas de CDN — le service est sans egress).
- **Garde « templates sans logique »** : un check (pre-push hook + CI) qui rejette toute logique dans
  les templates Jinja (cf. §9).
- **Packaging** : `packages/webui/Dockerfile` (multi-stage uv), service `webui` dans le compose
  (monte `catalog-db`/`local-db`/`config` en **RO**), profils **observer** ET **download**, derrière
  reverse proxy. Healthcheck `GET /health`.

**Hors périmètre** (voir §15) :
- **Toute écriture / action** (re-vérifier, re-télécharger, traiter un `dead_letter`, éditer des
  règles) — exige une médiation du writer unique + de l'auth. **Différé.**
- **Toute face « ops » / graphes** : Prometheus + **Grafana restent** l'outil opérationnel ; le webui
  ne parle pas à Prometheus (W-D5).
- **Flux d'activité / tail de logs bruts** — déféré (handler fichier crawler ou socket Docker, hors
  posture). On s'en tient à l'**état concret** lu des DB.
- **Catalogue agrégé / public (hub)** : suppose la fusion multi-nœuds ; palier ultérieur, réutilisera
  ce moteur SSR sur un `merged-catalog.db`.
- **Auth / TLS** dans le service : reverse proxy.

## 2. Décisions verrouillées (issues du brainstorm)

1. **W-D1 — Extraction du moteur de matching en paquet partagé, en 1ʳᵉ tâche.** Le domaine pur du
   matching sort dans `catalog_matching` (cf. §3) ; crawler **et** webui en dépendent. Mécanique,
   **sans changement de comportement**, gate-vert (100 % branch, tests déplacés) = critère de fin.
2. **W-D2 — Lecture seule stricte.** Le webui **n'écrit jamais** ; chaque connexion SQLite est ouverte
   en `PRAGMA query_only=ON`. Le **crawler reste l'unique writer de record** des deux bases ; le WAL
   rend les lectures concurrentes sûres (modèle de données §3). Aucune action mutante en v1 (§15).
3. **W-D3 — Paquet `catalog_webui` étanche.** Il importe `catalog_matching` (lib pure partagée) mais
   **jamais `emule_indexer` ni `download_verifier`**. Il lit les DB par **SQL direct** (pas via les
   repos du crawler) → couplage de **schéma** seulement (assumé, gardé en phase par les tests).
4. **W-D4 — Sans auth, sans TLS, poll simple.** Bind host/port configurable ; exposition/authn/TLS
   **délégués à un reverse proxy** (runbook). Pas de websocket/SSE : chaque requête lit l'état courant.
5. **W-D5 — Pas d'ops, pas de graphes.** L'opérationnel reste **Prometheus + Grafana** + **apprise**.
   Le webui montre du **contenu** (catalogue) et de l'**état concret** (files/ordonnancement), pas des
   séries temporelles. (Arbitrage tranché : une face Grafana-like reconstruirait un second Grafana.)
6. **W-D6 — Cibles lues depuis `config/targets.yaml` (RO).** Pour les épisodes **à zéro find** et pour
   reconstruire le moteur côté UI. Le webui charge le YAML (pyyaml) puis appelle les fonctions de
   parsing **de `catalog_matching`** — pas de réimplémentation du parsing/validation.
7. **W-D7 — Décision de match : recalcul de l'`Explanation`.** Liste/explorateur → champs **stockés**
   (`target_id`, `rule_name`, `tier`) ; **fiche détail** → `Explanation` **recalculée** via le moteur
   partagé (quels tokens/règles matchent). Reconstruit un `FileCandidate` depuis la dernière
   observation. **Nuance honnête** : le recalcul reflète la config **actuelle**, pas forcément celle
   de la décision historique — c'est « pourquoi ça matche maintenant », à afficher comme tel.
8. **W-D8 — Rendu : Jinja2 *sans logique*, garde automatisée.** Templates strictement présentationnels
   (itération + interpolation + composition) ; **toute** dérivation/branche vit dans les view-models
   typés. Un **check pre-push + CI** (par tokens ou AST, cf. §9) rejette `if`/`set`/`macro`/
   expressions calculées. **CSS vendoré** (servi par Starlette `StaticFiles`, pas de CDN). **Zéro JS
   custom** (filtres/recherche/pagination côté serveur via query-params).
9. **W-D9 — Minimisation / le sujet = le fichier.** Jamais d'IP ni de `user_hash` brut ; uniquement des
   **compteurs** de sources. `source_observations` (dormante) **non exposée**. `raw_meta` repliable,
   sur demande. Pas d'export en masse. (MVP §1 / §10.1.)
10. **W-D10 — Stack & deps.** `catalog_matching` : `google-re2` + `rapidfuzz` (pur, aucune I/O, pas de
    pyyaml). `catalog_webui` : `starlette` + `uvicorn` + `jinja2` + `pyyaml` + `catalog-matching`
    (workspace) ; **pas** de `httpx`. Versions/idiomes exacts (Jinja2 sur Starlette, `StaticFiles`,
    uv-in-Docker, garde templates par tokens ou AST) figés via **context7** au plan.

## 3. Tâche 1 — extraction du moteur de matching (`catalog_matching`)

Le moteur est déjà du **domaine pur** (aucune I/O ; `CLAUDE.md` le détaille). On le déplace tel quel.

```
packages/matching/                      # dist catalog-matching ; deps google-re2, rapidfuzz
└── src/catalog_matching/
    ├── normalization.py  models.py  matchers.py  interpolation.py
    ├── combinators.py    config.py   validation.py  resolver.py  engine.py
    └── explain.py                     # NEW (pur) : explain(candidate, target_id) -> Explanation
```

- **Déplacement `git mv`** des 9 modules + de leurs tests (`packages/crawler/tests/domain/matching/`
  → `packages/matching/tests/`). Aucune logique modifiée.
- Le crawler **dépend** de `catalog-matching` ; `adapters/config/yaml_loader.py` (l'I/O, **reste** au
  crawler) importe `catalog_matching` (`parse_matcher_config`/`parse_targets`/`MatchingEngine`). Les
  imports internes `emule_indexer.domain.matching.*` deviennent `catalog_matching.*`.
- `google-re2`/`rapidfuzz` quittent les deps **directes** du crawler (transitives via
  `catalog-matching`). L'override mypy `re2` (racine, span tous les paquets) reste valable.
- **Nouveau (pur)** : `explain.py` — un helper qui rend l'`Explanation` d'un `(candidate, target_id)`
  donné (le webui veut l'explication de la cible **stockée**, pas seulement de la cible gagnante de
  `evaluate`). Testé 100 % branch.
- **Critère de fin** : gate **vert sur les 3 paquets** (crawler, verifier, matching), 100 % branch
  partout, `mypy`/`ruff`/`sqlfluff` adaptés (le moteur n'a pas de SQL → pas de sqlfluff). Comportement
  inchangé : les tests de matching (déplacés) passent à l'identique.

## 4. Architecture — paquet `catalog_webui` (Clean léger)

Domaine pur (view-models + dérivations testables sans I/O), adapters pour le SQLite RO / le YAML / le
rendu, composition qui câble dans l'app Starlette.

```
packages/webui/                         # dist catalog-webui
└── src/catalog_webui/
    ├── __main__.py                     # uvicorn (python -m catalog_webui) ; lit l'env (§8)
    ├── domain/
    │   ├── views.py                    # DTOs gelés : TargetCoverage, FileRow, FileDetail, NodeState
    │   ├── coverage.py                 # pur : décisions d'une cible -> found/partial/none + meilleur tier
    │   └── format.py                   # pur : lien ed2k(hash,name,size), hash court, repli raw_meta
    ├── adapters/
    │   ├── catalog_read.py             # SQLite RO sur catalog.db -> view-models (requêtes neuves, §6)
    │   ├── local_read.py               # SQLite RO sur local.db -> NodeState
    │   ├── matching_read.py            # charge config (pyyaml) -> catalog_matching.MatchingEngine + explain (§7)
    │   ├── static/                     # CSS vendoré (servi par StaticFiles)
    │   └── templates/                  # Jinja2 SANS logique (base + dashboard/files/file_detail/node)
    └── composition/
        └── app.py                      # build_app() : ouvre RO, injecte dans app.state, routes, /static
```

Le **domaine** ne connaît ni SQLite, ni Jinja, ni le moteur : `coverage.py`/`format.py` sont des
fonctions pures. Les **adapters** font tout l'I/O (lecture seule) et la construction du moteur.

## 5. Pages & routes (SSR, GET uniquement)

| Route | Rend |
|---|---|
| `GET /` | **Tableau de bord** : couverture **par cible** (toutes les cibles de `targets.yaml`, y compris à zéro find) — statut found/partial/none, nb de fichiers, meilleur tier, sources max, dernière obs ; + encart **état du nœud**. |
| `GET /files` | **Explorateur** : table paginée, **filtres serveur** `?target=&tier=&verdict=&q=&page=`. Colonnes : hash court, nom (dernière obs), taille, sources, dernier verdict, dernière vue. Décision = champs **stockés** (W-D7). |
| `GET /files/{ed2k_hash}` | **Détail** : timeline d'observations, **explication de match recalculée** (W-D7), verdict + `checks` + `real_meta`, **lien eD2k** reconstruit, `raw_meta` repliable. |
| `GET /targets/{target_id}` | Raccourci de `/files?target=…`. |
| `GET /node` | **État du nœud / ordonnancement** (`local.db`) : downloads par état, file de vérif (pending/in_progress/dead_letter + `attempts`), `scheduler_state` (prochain cycle, `last_full_cycle_at`, backoff par canal), `node_id`/`created_at`. État **concret**, pas de séries temporelles. |
| `GET /health` | 200 (healthcheck compose). |
| `GET /static/...` | CSS vendoré (`StaticFiles`). |

En **observer**, `/node` et les verdicts sont quasi vides (pas de download/vérif) — attendu, géré
(sections vides). Le catalogue est peuplé dans les deux modes.

## 6. Read model & accès SQLite read-only

Les requêtes utiles **n'existent pas** dans les repos (orientés écriture). On écrit un read model
dédié en SQL paramétré (sqlfluff-lint comme le crawler) :

- **Couverture par cible** : agrégat sur `match_decisions` (group by `target_id`, meilleur tier, nb de
  fichiers distincts) ; complété par l'énumération `targets.yaml` (W-D6) pour les cibles sans décision.
- **Explorateur** : `files` ⨝ dernière `file_observations` (fenêtre `ROW_NUMBER() … ORDER BY
  observed_at DESC`) ⨝ dernière décision ⨝ dernier verdict, `WHERE` paramétrés, `LIMIT/OFFSET`.
- **Détail** : toutes les `file_observations` d'un hash, la dernière décision, tous les
  `file_verifications` du hash.
- **État nœud** : `SELECT` directs sur `downloads`, `verification_tasks`, `scheduler_state`,
  `node_runtime`.

**Ouverture (W-D2)** : URI `file:…?mode=ro` **et** `PRAGMA query_only=ON` (double garde). Voir §16 sur
la lecture d'une base **WAL vivante** inter-process (point empirique #1 : montage RO strict vs
RW + `query_only`). Connexion par requête (ou pool RO) ; pas de transaction explicite.

## 7. Recalcul de l'explication de match (`matching_read` + `catalog_matching`)

Sur la **fiche détail** uniquement (l'explorateur reste sur les champs stockés, par coût) :

1. `matching_read` charge la config (matcher policy + `targets.yaml`) via pyyaml puis
   `catalog_matching.parse_matcher_config`/`parse_targets` → un `MatchingEngine` (construit une fois,
   mis en cache dans `app.state`).
2. Reconstruit un `FileCandidate` depuis la **dernière observation** (nom = `filename`, taille =
   `size_bytes`, + champs média `media_length_sec`/`bitrate_kbps`/`codec`/`file_type` si présents).
3. `catalog_matching.explain(candidate, target_id_stocké)` → `Explanation` riche (tokens/règles).
4. La page affiche l'`Explanation` **avec la mention** « évaluée contre la configuration actuelle »
   (W-D7, nuance historique).

Le moteur reste pur ; toute l'I/O (chargement YAML) est dans l'adapter. Si la config a changé et que
la cible stockée ne matche plus, on l'affiche honnêtement (pas d'erreur).

## 8. Câblage (composition) & configuration

`build_app()` (testable via `httpx.ASGITransport`) résout les chemins (bases + `targets.yaml` +
matcher policy) depuis l'env, ouvre les accès RO, construit le `MatchingEngine`, instancie les
adapters + `Jinja2Templates` + `StaticFiles`, les pose dans `app.state`, déclare les routes.
`__main__.py` lance uvicorn.

Config par **variables d'env** (patron verifier) : `WEBUI_HOST` (déf. `127.0.0.1`), `WEBUI_PORT`
(déf. `8080`), `CATALOG_DB`, `LOCAL_DB`, `TARGETS_CONFIG`, `MATCHER_CONFIG`. Chemins absents /
illisibles → erreur claire au démarrage (fail-fast).

## 9. Rendu : Jinja2 sans logique + garde automatisée + CSS vendoré (W-D8)

- **Templates présentationnels** : autorisés = `{% extends %}`/`{% block %}`/`{% include %}`,
  `{% for %}` (+ `{% else %}` pour l'état vide), interpolation `{{ x }}`/`{{ x.attr }}`. Interdits =
  `{% if %}`/`{% elif %}`, `{% set %}`, `{% macro %}`, et toute **expression calculée** dans `{{ }}`
  (opérateurs, filtres à logique, appels). Toute branche/dérivation vit dans `domain/views.py`
  (typé, testé) — ex. un libellé de statut, une classe CSS, un drapeau « vide » sont **précalculés**.
- **Garde** : un petit check Python qui rejette toute construction interdite. **On retiendra la plus
  simple qui répond au besoin** (figée au plan) : (a) **match de tokens interdits** — repérer les
  balises `{% if %}`/`{% elif %}`/`{% set %}`/`{% macro %}` et les motifs d'expression calculée — le
  plus direct ; ou (b) **walk de l'AST Jinja** (`Environment.parse` → rejet de `nodes.If`/
  `nodes.Assign`/`nodes.Macro`/`nodes.Filter`/`nodes.Call` à logique) — plus robuste aux faux positifs.
  Vit dans `packages/webui` (ou `scripts/`), **testé 100 % branch**, câblé dans le **hook pre-push +
  la CI** (un check de gate de plus, comme `sqlfluff`).
- **CSS vendoré** : une feuille **minimale et sémantique** (style Pico.css/Simple.css) vendorée sous
  `adapters/static/`, servie par `StaticFiles`. **Pas de CDN** (service sans egress ; reproductible,
  offline). Dégradation propre sans CSS. Aucun asset de build, aucun JS.

## 10. Packaging (Docker / compose)

- **`catalog_matching`** : pas d'image (lib pure) — empaquetée **dans** les images crawler et webui
  via `uv sync --package` (le workspace résout la dépendance).
- **`packages/webui/Dockerfile`** (multi-stage uv) : `uv sync --frozen --no-dev --package
  catalog-webui` ; runtime `python:3.12-slim`, **non-root**, `ENTRYPOINT
  ["python","-m","catalog_webui"]`, expose `WEBUI_PORT`. Aucune dépendance binaire.
- **Service `webui`** (compose) : monte **`catalog-db`, `local-db`, `./config` en RO** ; durcissement
  conteneur standard (`cap_drop: ALL`, `no-new-privileges`, `read_only` + tmpfs, non-root) ; **aucun
  réseau** vers amuled/verifier/Internet (il lit des fichiers) — seulement un port au reverse proxy.
  Profils **observer** + **download**. Image `ghcr.io/geoffreycoulaud/emule-indexer/webui`.
- **Reverse proxy** (auth/TLS/expo) : hors service, documenté au runbook.

## 11. Éthique & minimisation (W-D9)

Le sujet du catalogue est **le fichier**. Aucune IP, aucun `user_hash` brut, aucun graphe de pairs ;
uniquement des **compteurs** de sources. `source_observations` (dormante) non exposée. `raw_meta`
seulement sur dépliage. Pas d'export en masse. Append-only intact (W-D2, zéro écriture).

## 12. Tests & discipline du projet

TDD strict, **100 % branch par-paquet** (gate étendu aux 4 paquets : crawler, verifier, **matching**,
**webui**). `mypy --strict` (src + tests), `ruff` (E,F,I,UP,B,SIM, l.100), `sqlfluff` étendu à
`packages/webui/src` (SQL de lecture ; le moteur n'a pas de SQL).

- **`catalog_matching`** : les tests de matching **déplacés** passent à l'identique (extraction
  mécanique) ; `explain.py` testé (les deux côtés des conditionnelles).
- **Domaine webui** (`coverage.py`, `format.py`) : testé directement (statuts de couverture ; lien
  ed2k / hash court / repli raw_meta).
- **Read model** (`catalog_read`/`local_read`/`matching_read`) : contre un **vrai SQLite sur
  `tmp_path`** peuplé par fixtures (mêmes migrations/format que le crawler), `query_only` vérifié,
  cibles à zéro find, filtres/pagination, recalcul d'explication (cible qui matche / qui ne matche
  plus).
- **App Starlette** : via **`httpx.ASGITransport`** in-process (chaque route : peuplé / vide /
  introuvable → 404 ; `/health` ; `/static`). Pas de navigateur, pas de Docker.
- **Garde « templates sans logique »** : check d'AST testé 100 % branch (template conforme accepté /
  chaque construction interdite rejetée).
- **Smoke compose** (`compose_integration`, opt-in, `--no-cov`) : le service `webui` monte, `/health`
  répond, une page se rend sur un volume DB peuplé en RO. (Ajout au smoke existant.)

## 13. Definition of Done

- **Tâche 1** : `catalog_matching` extrait, crawler le consomme, gate **vert sur 3 paquets**
  (comportement inchangé). `explain.py` livré + testé.
- Paquet `catalog_webui` ajouté au workspace ; gate **vert sur 4 paquets** à **100 % branch** ;
  `ruff`/`mypy`/`sqlfluff` étendus ; CI + hook adaptés (dont la garde templates).
- App Starlette (routes §5) + read model RO (§6) + recalcul d'explication (§7) + domaine pur +
  énumération `targets.yaml` + CSS vendoré.
- `Dockerfile` webui + service compose (montages RO, durcissement, profils observer/download) +
  section runbook (reverse proxy, env, où regarder). Image publiable GHCR.
- **NON inclus** (différés, §15) : actions mutantes, face ops/graphes, flux d'activité/logs, hub
  agrégé/public, auth dans le service.

## 14. Suite

- **Actions médiées** (re-vérifier / re-télécharger / `dead_letter`) via une table d'**intentions**
  consommée par le crawler (respecte le writer unique) + auth — si le besoin se confirme.
- **Catalogue agrégé / public** : réutiliser ce moteur SSR sur un **`merged-catalog.db`** (après la
  fusion multi-nœuds), skin public + minimisation renforcée.
- **Dry-run de matching** (test de règles avant PR) : désormais facile (le moteur est un paquet) —
  plutôt comme sous-commande CLI que dans le webui.
- **Flux d'activité / logs** (handler fichier crawler → tail RO) si demandé.

## 15. Hors-périmètre / reporté (explicite)

- **Écritures / actions** → différé (médiation writer unique + auth). v1 = strictement lecture.
- **Ops / graphes / PromQL** → **Grafana reste** (W-D5).
- **Flux d'activité / tail de logs** → déféré (plomberie crawler ou socket Docker hors posture).
- **Hub agrégé / catalogue public** → palier ultérieur (dépend de la fusion multi-nœuds).
- **Auth / TLS dans le service** → reverse proxy (W-D4).
- **`source_observations` exposée / export en masse** → jamais (W-D9).

## 16. Risques & notes

- **Lecture d'une base WAL vivante inter-process — point empirique #1.** `mode=ro` strict peut échouer
  si SQLite ne peut pas accéder/écrire l'index `-shm`. Repli robuste = **montage RW du volume +
  `PRAGMA query_only=ON`** (le webui reste lecteur ; le crawler reste l'unique writer de record).
  Trancher au plan sur la vraie stack ; consigner dans `docs/reference/`. L'invariant « le crawler
  PROD ne lit jamais les octets » n'est pas concerné : le webui ne lit que les **bases**.
- **Extraction (Tâche 1) touche le « joyau ».** Le moteur est le code le plus testé du repo ;
  l'extraction est mécanique mais l'erreur d'import est facile. Filet : gate 100 % branch (tests
  déplacés) + `mypy` sur tout le workspace = le critère de fin attrape toute régression.
- **Fidélité du `FileCandidate` reconstruit (W-D7).** On rejoue depuis la **dernière** observation
  (nom + taille + média) — suffisant pour l'explication ; pas une reconstitution de l'instant
  historique. Affiché comme « config actuelle ».
- **Couplage de schéma (W-D3) et de format `targets.yaml` (W-D6).** Lecture par SQL/pyyaml hors repos
  → fragile si le schéma migre. Mitigation : fixtures alignées sur les migrations ; le parsing
  `targets.yaml` passe par `catalog_matching` (pas de réimplémentation). Le smoke monte un volume réel.
- **Garde « templates sans logique ».** Que ce soit par **tokens** (plus simple) ou par **AST** (plus
  robuste aux faux positifs), le check doit autoriser exactement itération+interpolation+composition
  et rien d'autre ; l'approche et la liste exacte sont figées au plan — testées des deux côtés.
- **Pagination naïve `LIMIT/OFFSET`.** OK au volume Keroro ; curseur sur `observed_at` si ça explose
  (YAGNI).
