# Crawler MVP — Plan 5 : Orchestration des recherches (`v0.7.0-orchestration`) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Brancher le moteur + l'adapter EC + le modèle de données en une **boucle de crawl** déterministe et résiliente (spec orchestration §1). À la fin, `python -m emule_indexer` est un crawler qui tourne : pointé sur un `amuled` réel, il génère des mots-clés depuis `targets.yaml`, lance les recherches via EC (pool de travailleurs, un par instance), persiste les observations, les évalue avec le moteur, persiste les décisions (anti-redondance par changement de verdict), nudge un hub in-process, recommence — en continu, avec backoff par (instance, canal), arrêt **observable et borné**. Ce plan introduit les **deux couches manquantes** de l'architecture cible : `application/` (use-cases async) et `composition/` (assemblage + point d'entrée), plus le domaine pur `domain/search/` (keywords/cycle/backoff/coverage) et trois ports (`Clock`/`Rng`, `DecisionSignal`, `SchedulerStateRepository`). Spec : `docs/superpowers/specs/2026-06-12-orchestration-design.md`. Périmètre : **observe, catalogue, décide, boucle — rien d'autre** (NO download/notify = plans D/E ; NO server.met/nodes.dat = plan F ; logging stdlib seul).

**Architecture:** Clean/Hexagonal, inchangée. Règle de dépendance (spec §4) : `domain` pur ; `ports` n'importe que le domaine ; **`application` dépend des ports/domaine, JAMAIS d'un adapter** ; `adapters`/`composition` implémentent et assemblent. Conséquence forte vérifiée à l'écriture : pour que l'application catch des exceptions sans importer d'adapter, le **contrat d'erreur** vit dans les PORTS — `MuleUnreachableError`/`MuleSearchFailedError` (`ports/mule_client.py`) et `RepositoryError` (`ports/repository_errors.py`) ; les exceptions d'adapter (`EcConnectError`…, `PersistenceError`) en **HÉRITENT** (dépendance adapter→port, licite). Le **port `Rng`** (hasard injectable : `shuffled` + `jitter`) est défini dans le domaine (`domain/search/cycle.py`, là où il est consommé — le domaine n'importe jamais un port) et **ré-exporté** par `ports/clock.py`. Le **backoff par (instance, canal)** est exponentiel + jitter, MÉMORISÉ dans un `BackoffRegistry` **partagé** (une instance pour tous les travailleurs + le cycle) et **PERSISTÉ** dans `scheduler_state` en fin de cycle (survit au redémarrage, spec §3/§7). Les repos sont **synchrones, appelés directement** (spec §3 : sub-ms, pas de `to_thread` en MVP — documenté, réversible) ; le seul vrai parallélisme est l'I/O réseau EC (`await`). La composition root possède et ferme le pool de clients + les 2 connexions via `AsyncExitStack`, **APRÈS** l'unwind du `TaskGroup`.

**Tech Stack:** Python ≥ 3.12 (`asyncio.TaskGroup`/`asyncio.timeout`/`asyncio.Queue`/`loop.add_signal_handler`/`contextlib.AsyncExitStack`), `sqlite3` stdlib, `uv`, `ruff` (E/F/I/UP/B/SIM, line 100), `mypy --strict` (src + tests), `pytest` + `pytest-asyncio` (mode `strict`, tests async annotés `@pytest.mark.asyncio`) + `pytest-cov` (gate **100 % branch**), `sqlfluff`. **Aucune nouvelle dépendance.** Déterminisme TOTAL : `Clock`/`Rng`/`sleep` injectables (faux avançables, zéro attente réelle), seed du shuffle = `node_id`+index de cycle. Tests : faux `MuleClient` scripté + pannes injectables ; **vrais** repos SQLite sur `tmp_path` (spec §8) ; faux hub de nudge capturant les sujets.

> **Référence spec :** `docs/superpowers/specs/2026-06-12-orchestration-design.md` — §2 (périmètre), §3 (décisions verrouillées), §4 (architecture + cycle), §5 (config), §6 (arrêt observable & borné), §7 (résilience), §8 (tests), §9 (livrables/DoD), §10 (questions laissées à CE plan). Spec MVP `2026-06-10-crawler-mvp-design.md` : §4 (couches), §6 (orchestration), §13 (observabilité/coverage), §14 (résilience). Handoffs : `2026-06-12 - handoff - modèle de données complet.md` (§4 contrats repos, §5 pièges) et `2026-06-11 - handoff - adapter EC complet.md` (client async FCFS, « le client signale, le plan C décide »).

> **HORS PÉRIMÈTRE (spec §2 — RIEN de tout ceci n'apparaît ici) :** consommation des décisions (auto-download plan D, notifications plan E) — la table `match_decisions` append-only EST le journal, D/E rejouent ; provisionnement `server.met`/`nodes.dat` (plan F) — le plan C SURVEILLE seulement High ID/Kad via EC ; observabilité riche (Prometheus/apprise/anti-fatigue, plan E) — ici logs stdlib seuls ; packaging Docker/glueforward (plan F). **Le backoff par (instance, canal) EST persisté** dans `scheduler_state` (clé `channel_backoff`, JSON), au même moment que `cycle_index`/`last_full_cycle_at` — il survit à un redémarrage (spec §3/§7, DÉCISION 7).

---

## Vérifications empiriques (faites PENDANT l'écriture du plan — ne PAS re-découvrir)

Tout le code async/SQL ci-dessous a été **exécuté pour de vrai** (venv du projet, Python **3.12.9**, `sqlite3.sqlite_version == 3.47.1`, `pytest-asyncio == 1.4.0`), puis le plan ENTIER a été assemblé dans un bac à sable et le **gate 5 checks** exécuté sur l'état final : **490 passed, 5 deselected, 100.00 % branch ; ruff check + ruff format + mypy (121 fichiers) + sqlfluff verts.**

1. **Worker pool** : N workers async drainant une `asyncio.Queue`, une **sentinelle `None` par worker** + `await queue.join()` avant d'enfiler les sentinelles, sous `asyncio.TaskGroup` — draine correctement (10 items, 3 workers, vérifié).
2. **Annulation** : annuler le `TaskGroup` (lever dans le bloc) → les workers reçoivent `CancelledError` → leur `finally` s'exécute (`finally_ran=True cancelled_seen=True`). **MAIS** — découverte critique — **annuler UN enfant d'un `TaskGroup` (`child.cancel()`) quand le groupe lui-même n'est PAS annulé NE propage AUCUN `CancelledError`** au sortir du `async with` : l'unwind est PROPRE, un `except* CancelledError` serait **du code mort**. (D'où le design de `_supervise` : pas d'`except*`, on logge après le bloc.)
3. **Signaux** : `loop.add_signal_handler(SIGINT, …)` **ne préempte JAMAIS** une fonction sync en plein vol (le handler ne tourne qu'ENTRE callbacks) — vérifié : `os.kill(SIGINT)` au milieu d'une section sync ne livre le signal qu'au prochain `await`. Escalade double-signal (1er → set Event, 2e → `SystemExit`) confirmée.
4. **Déterminisme** : faux `sleep` qui avance un faux `now` SANS attente réelle → deux sleeps totalisant 35,5 s tournent en **0,023 ms** réelles. `random.Random(seed).shuffle` : même seed → même ordre ; seeds différents (`node-A:5` vs `node-B:5`) → divergence. Permutation, pas de mutation de l'entrée.
5. **`last_decision` SQL** : sur une `catalog.db` construite depuis la VRAIE migration `0001_initial.sql`, `SELECT … WHERE ed2k_hash=? ORDER BY decided_at DESC, id DESC LIMIT 1` rend la décision la PLUS RÉCENTE (2 décisions, decided_at croissants → la 2e gagne) ; hash absent → `None`. L'index `idx_match_decisions_ed2k_hash` **existe déjà** (migration v0.6.0) → **aucun nouvel index nécessaire**.
6. **Protocols sous `mypy --strict`** : `Clock` (now/sleep async), `Rng` (shuffled), `DecisionSignal` (signal/wait async), `SchedulerStateRepository`, et un `FakeMuleClient` satisfont structurellement leurs ports (vérifié en plaçant le scratch dans la scope `files=["src","tests"]` du projet — `Success: no issues found`).
7. **`pytest-asyncio` strict** : tests async `@pytest.mark.asyncio`, faux clock/sleep, hub `asyncio.Event` par sujet — tournent en ms, déterministes. Le nudge `await event.wait()` est réveillé par `signal()` (le test EST le consommateur, pas du code mort).
8. **MRO des exceptions de contrat** : `EcConnectError(EcError, MuleUnreachableError)` avec `EcError(MuleClientError)` et `MuleUnreachableError(MuleClientError)` — diamant propre, MRO résout ; `issubclass(EcFailureError, MuleSearchFailedError)` et `not issubclass(EcFailureError, MuleUnreachableError)` confirmés.
9. **`AsyncExitStack` + `asyncio.timeout`** : les ressources se ferment en LIFO APRÈS l'unwind du `TaskGroup` (workers `finally` AVANT tout `close:` — vérifié). `asyncio.timeout(d)` autour d'une fermeture qui traîne → `TimeoutError` (arrêt borné). Une `aclose()` interrompue par le timeout laisse les callbacks RESTANTS rejouables par un second `aclose()` best-effort.
10. **Backoff PERSISTÉ dans `scheduler_state`** (spec §3/§7) : la map ``{ "amule-1:kad": {attempts, retry_after}, "amule-1": {...} }`` se sérialise en JSON sous UNE clé KV (`channel_backoff`) de `scheduler_state` (table KV, NON append-only → UPSERT licite). Round-trip vérifié sur une base construite depuis la VRAIE migration `0001_initial.sql` : save → instance de repo NEUVE (simule un redémarrage) → load → map IDENTIQUE ; clé absente → `{}` ; overwrite par snapshot vide → `{}`. `retry_after` est un ISO-8601 UTC à largeur fixe → comparaison `now < retry_after` lexicographique == chronologique.
11. **Round-trip de backoff bout-en-bout (à travers `BackoffRegistry` + repo réel)** : `record_failure("amule-1:kad")` (jitter 0 → délai NOMINAL 2.0s, `retry_after = now + 2.0s`) → `save_channel_backoff(snapshot)` → REDÉMARRAGE (repo neuf + registre neuf, ZÉRO état mémoire) → `load_from(repo.load_channel_backoff())` → `is_in_backoff` rend `True` (skip) ; après avance de 3.0s du faux clock → `False` (re-armé/expiré). **La persistance survit au redémarrage, empiriquement.**
12. **Jitter via le port `Rng`** : `SeededRng.jitter(span)` ∈ `[0, span)` (vérifié sur 20 tirages), reproductible pour un `jitter_seed` donné, `0.0` si `span <= 0`. Le faux `Rng` de test rend un jitter CONSTANT (déterminisme : assertions exactes sur le délai).

---

## File Structure & décisions verrouillées

```
src/emule_indexer/
├── domain/search/                         # PUR (nouveau sous-paquet)
│   ├── __init__.py                        # Create (vide)
│   ├── keywords.py                        # Create : generate_keywords → SearchKeyword
│   ├── cycle.py                           # Create : Rng (Protocol), cycle_seed, shuffle_for_cycle
│   ├── backoff.py                         # Create : backoff_delay (math pure)
│   └── coverage.py                        # Create : Coverage (enum), effective_coverage(bools)
├── domain/matching/engine.py              # Modify : + DecisionRecord, + to_record
├── ports/
│   ├── clock.py                           # Create : Clock (Protocol) + ré-export Rng
│   ├── decision_signal.py                 # Create : DecisionSignal (Protocol async)
│   ├── repository_errors.py               # Create : RepositoryError (contrat)
│   ├── scheduler_state_repository.py      # Create : SchedulerStateRepository (Protocol sync)
│   ├── mule_client.py                     # Modify : + MuleClientError/Unreachable/SearchFailed
│   └── catalog_repository.py              # Modify : + last_decision(hash) -> DecisionRecord|None
├── application/                           # NOUVELLE COUCHE (async, dépend des ports)
│   ├── __init__.py                        # Create (vide)
│   ├── record_observations.py             # Create : pipeline par obs (record→eval→decide→nudge)
│   ├── search_worker.py                   # Create : SearchWorker, WorkerDeps, WorkerPolicy, BackoffRegistry, SearchTask
│   └── run_search_cycle.py                # Create : un cycle (statut→coverage→keywords→fan-out→drain→avance)
├── adapters/
│   ├── clock_asyncio.py                   # Create : AsyncioClock + SeededRng
│   ├── decision_signal_asyncio.py         # Create : AsyncioDecisionSignal (Event par sujet)
│   ├── config/crawler_config.py           # Create : parse_crawler_config → CrawlerConfig (gelé, fail-fast)
│   ├── config/local_config.py             # Create : parse_local_config → LocalConfig (gelé, fail-fast)
│   ├── mule_ec/errors.py                  # Modify : EcError hérite du contrat de port
│   └── persistence_sqlite/
│       ├── errors.py                      # Modify : PersistenceError hérite de RepositoryError
│       ├── catalog_repository.py          # Modify : + last_decision
│       └── scheduler_state_repository.py  # Create : SqliteSchedulerStateRepository (KV)
└── composition/                           # NOUVELLE COUCHE — assemblage + entrée
    ├── __init__.py                        # Create (vide)
    ├── app.py                             # Create : CrawlerApp (pool, repos uniques, boucle, arrêt borné)
    └── __main__.py                        # Create : python -m emule_indexer (charge config, run)

config/
├── crawler.yaml                           # Create : politique (versionné)
├── local.example.yaml                     # Create : modèle (versionné) ; local.yaml gitignoré
├── targets.yaml                           # Create : cibles (copie de la fixture canonique)
└── matcher.yaml                           # Create : tokens/règles (copie de la fixture canonique)

tests/                                     # un fichier par unité (voir tâches)
.gitignore                                 # Modify : + config/local.yaml
pyproject.toml                             # Modify : marqueur orchestration_integration
```

> **DÉCISION 1 — Contrat d'erreur dans les PORTS ; les adapters en héritent.**
> Règle de dépendance (spec §4) : l'application ne doit JAMAIS importer un adapter. Or elle doit réagir à « daemon injoignable » vs « recherche échouée » (worker) et à « persistance en échec » (pipeline). Solution propre : `ports/mule_client.py` déclare `MuleClientError` → `MuleUnreachableError`/`MuleSearchFailedError` ; `ports/repository_errors.py` déclare `RepositoryError`. Les exceptions d'adapter HÉRITENT (`EcConnectError`/`EcTimeoutError`/`EcProtocolError` → `MuleUnreachableError` ; `EcFailureError` → `MuleSearchFailedError` ; `EcAuthError` reste hors contrat de boucle = problème de config ; `PersistenceError` → `RepositoryError`). Dépendance adapter→port, licite. L'application catch UNIQUEMENT les classes de port. Vérif empirique 8 (MRO propre).

> **DÉCISION 2 — `last_decision` rend un `DecisionRecord(target_id, rule_name, tier)`, PAS un `MatchDecision`.**
> `match_decisions` ne persiste PAS l'`explanation` (spec data-model). Reconstruire un `MatchDecision` avec une explication vide serait un mensonge. L'anti-redondance (spec §3) ne compare QUE les 3 champs de verdict. On introduit donc un dataclass gelé `DecisionRecord(target_id, rule_name, tier)` dans `domain/matching/engine.py` (à côté de `MatchDecision`) + un helper pur `to_record(MatchDecision) -> DecisionRecord`. `CatalogRepository.last_decision(hash) -> DecisionRecord | None`. La comparaison `last_decision(hash) == to_record(decision)` est un `==` champ par champ de dataclass gelé. **C'est le choix de type laissé ouvert par le prompt — verrouillé ici.**

> **DÉCISION 3 — `Rng` (mélangeur) est un Protocol défini DANS le domaine, ré-exporté par `ports/clock.py`.**
> La spec §4 liste `ports/clock.py: Clock + Rng`. Mais `domain/search/cycle.py` consomme le mélangeur, et le domaine n'importe JAMAIS un port. Le Protocol `Rng` vit donc là où il est consommé (le domaine, exactement comme le Protocol `Matcher` de `combinators.py`), et `ports/clock.py` le **ré-exporte** (`from emule_indexer.domain.search.cycle import Rng`) pour donner aux adapters/composition un point d'import unique « ports du temps ». `Clock` (now aware + sleep async) reste un vrai port. Aucune dépendance domaine→port introduite.

> **DÉCISION 4 — `effective_coverage` prend des BOOLÉENS, pas des `NetworkStatus`.**
> `NetworkStatus` vit dans `ports/mule_client.py` ; le domaine ne peut pas l'importer. `domain/search/coverage.py` reçoit `Sequence[bool]` (« telle instance peut-elle faire aboutir une recherche ? »). C'est l'APPLICATION (`run_search_cycle._is_search_capable`) qui traduit chaque `NetworkStatus` en booléen (HighID eD2k OU Kad CONNECTED) avant d'appeler le domaine pur. `any(())` vaut `False` → liste vide tombe sur `BLIND`.

> **DÉCISION 5 — Paramètres de politique injectés en PRIMITIFS dans l'application.**
> `CrawlerConfig`/`BackoffConfig` (value objects) vivent dans l'adapter `adapters/config/` (spec §4). L'application ne les importe PAS (ce serait application→adapter). `SearchWorker` reçoit un `WorkerPolicy` (dataclass gelé d'application, primitifs : backoff base/cap/factor + poll budget/interval) ; la composition root DÉBALLE `CrawlerConfig` en `WorkerPolicy` (`_build_policy`). `run_search_cycle` reçoit l'`Rng`/`Clock`/`SchedulerStateRepository` directement.

> **DÉCISION 6 — Arrêt : `_supervise` SANS `except*` (vérif empirique 2).**
> Annuler `loop_task` (un enfant du `TaskGroup`) ne propage AUCUN `CancelledError` au sortir du `async with`. Le `_human("Travailleurs arrêtés.")` est donc APRÈS le bloc, pas dans un `except*` (qui serait du code mort, non couvrable). Une VRAIE exception d'un travailleur, elle, propagerait en `ExceptionGroup` — on ne la masque pas. La phase d'arrêt (unwind + fermeture LIFO du stack) est sous UN `asyncio.timeout(shutdown_deadline_seconds)` ; un dépassement → `TimeoutError` ; le `finally` tente un `aclose()` best-effort (`suppress(BaseException)`) pour ne pas re-bloquer.

> **DÉCISION 7 — `scheduler_state` : KV (`cycle_index`, `last_full_cycle_at`, `channel_backoff`), UPSERT atomique ; le BACKOFF est PERSISTÉ.**
> Le port `SchedulerStateRepository` (sync) persiste l'INDEX de cycle (n'avance qu'en FIN de cycle → un kill au milieu rejoue, spec §7), l'horodatage du dernier cycle complet, ET le **backoff par (instance, canal)** (spec §3/§7 : il doit survivre à un redémarrage). Tout est KV dans la table `scheduler_state` (existante, v0.6.0, NON append-only → UPSERT licite). `write_cycle_state(index, datetime)` (datetime aware, l'adapter formate via `utc_iso`) fait UN `BEGIN IMMEDIATE` + 2 UPSERT + `COMMIT`. Le backoff est sérialisé en **JSON** sous la clé `channel_backoff` (`{ "amule-1:kad": {attempts, retry_after}, "amule-1": {...} }`) ; `load_channel_backoff()` rend `{}` si absent, reconstruit des `ChannelBackoff(attempts, retry_after)` ; `save_channel_backoff(map)` REMPLACE entièrement la map (snapshot du registre) sous `BEGIN IMMEDIATE`. **Persistance EN FIN DE CYCLE, au même moment que `cycle_index`** (DÉCISION 7bis) : un kill à mi-cycle ne fait avancer NI l'index NI le backoff → le cycle rejoue ET re-arme le backoff depuis l'état du cycle précédent (cohérence : l'index non plus n'avance pas à mi-cycle, spec §7). `retry_after` est un ISO-8601 UTC à largeur fixe (comparaison `now < retry_after` lexicographique == chronologique) ; le jitter est tiré du port `Rng` (déterministe en test). Le registre `BackoffRegistry` est **PARTAGÉ** (une instance pour tous les travailleurs + le cycle), muté entre deux `await` sur l'event loop mono-thread (writer unique → aucun verrou, spec §3). « Skip jusqu'à `retry_after` » remplace l'ancien « sleep du délai » : un canal en backoff est SAUTÉ (l'event loop reste libre), pas attendu.

> **DÉCISION 8 — `generate_keywords` : large + ciblés, ordonné et DÉDUPLIQUÉ (premier vu gagne).**
> Mot-clé large `keroro` d'abord (origin `"broad"`), puis par cible : l'identifiant de segment `062a` (numéro zéro-paddé sur 3 + lettre minuscule, comme `N°062A` des fichiers source ; le `°`/`n` est laissé de côté car les serveurs eD2k tokenisent sur les non-alphanumériques) puis les tokens du titre (longueur ≥ 2 ; un token d'1 caractère est couvert par le large). Déduplication par `text` (premier vu gagne). `SearchKeyword(text, origin)` gelé/hashable. Déterministe → le shuffle seedé part d'un ordre stable.

> **DÉCISION 9 — Le test du nudge AWAIT le signal (le test EST le consommateur).**
> Le hub `DecisionSignal` n'a pas encore de consommateur de prod (plans D/E). Pour qu'il ne soit pas du code mort, un test `await hub.wait(subject)` dans une task, vérifie qu'elle ne se résout pas, déclenche `record_observation` (qui `signal`e post-commit), puis `await wait_for(waiter)`. **Ce test EST le consommateur — pas du churn pour les reviewers.**

> **Note couverture (gate 100 % branch — points chauds) :** stubs de Protocol **une ligne** (`def m(...) -> T: ...`). Cas exercés des DEUX côtés : `backoff_delay` (attempt ≤ 1 / > 1, cap atteint / non) ; `BackoffRegistry` (record/grow/reset, clés indépendantes, reset clé inconnue, `is_in_backoff` clé connue future/passée + clé inconnue, jitter étend le délai, snapshot↔load round-trip) ; `SeededRng.jitter` (dans span / reproductible / span ≤ 0 → 0) ; `effective_coverage` (vide/aucun/tous/mixte) ; `generate_keywords` (token court écarté, doublon, cibles vides) ; `record_observation` (écarté/nouveau/inchangé/changé/`RepositoryError` absorbée) ; `SearchWorker` (connect ok/échec→backoff instance, instance en backoff → skip sans connecter, canal en backoff → skip, backoff expiré → re-run, déjà connecté, search ok/`SearchFailed`→backoff canal/`Unreachable`→down, poll : break immédiat / boucle puis budget / boucle puis break / progress None, fetch multi-obs dont une écartée) ; `run_search_cycle` (1 instance / 2 workers / une aveugle / log blind / backoff persisté en fin de cycle) ; `app.py` (cycle propre / shutdown pendant sleep / annulation en vol / 2e signal / délai dépassé / node_id override / default_client_factory) ; `__main__` (run propre / config invalide / fichier absent / défauts) ; config parsers (chaque branche fail-fast, dont `jitter_ratio` 0/négatif) ; `last_decision` (None / le plus récent / hash absent) ; `scheduler_state` (lecture 0 / round-trip / overwrite / naïf refusé / panne atomique ; backoff vide / round-trip via repo neuf / overwrite / panne atomique).

> **Note typage (`mypy --strict` sur src ET tests) :** tous les tests `-> None`, params typés, async annotés `@pytest.mark.asyncio`. Le `client_factory` injecté en test est typé `object` dans les helpers (les fakes le satisfont structurellement) avec un unique `# type: ignore[arg-type]` au point d'assemblage `CrawlerApp(...)`. Le faux `MuleClient` satisfait STRUCTURELLEMENT le port (aucun héritage). `coro.close()` dans le faux `asyncio.run` porte `# type: ignore[attr-defined]`. Le `matcher_config` fixture est typé `MatcherConfig` (pas `object`) → zéro ignore sur les appels du moteur.

> **Note ordonnancement & convention de run :** chaque tâche = test(s) qui échoue(nt) → run/échec attendu → impl minimale → run/pass → **gate 5 checks** → commit conventionnel. Runs focalisés en `--no-cov`. **L'éditeur (ruff/format) peut regrouper les imports `from tests.…` comme first-party** — laisser ruff trancher (`uv run ruff check . --fix && uv run ruff format .`) avant le gate. Le gate complet : `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run sqlfluff lint src`.

---

## Task 1: Domaine search — `generate_keywords`

**Files:**
- Create: `src/emule_indexer/domain/search/__init__.py` (vide)
- Create: `src/emule_indexer/domain/search/keywords.py`
- Create: `tests/domain/search/__init__.py` (vide)
- Create: `tests/domain/search/test_keywords.py`

- [ ] **Step 1: Créer les `__init__.py` vides**

`src/emule_indexer/domain/search/__init__.py` : fichier VIDE.
`tests/domain/search/__init__.py` : fichier VIDE.

- [ ] **Step 2: Écrire le test qui échoue**

`tests/domain/search/test_keywords.py` :
```python
import datetime

from emule_indexer.domain.matching.models import TargetSegment
from emule_indexer.domain.search.keywords import SearchKeyword, generate_keywords

_S2E062A = TargetSegment(
    season=2,
    number=62,
    segment="A",
    title="Les demoiselles cambrioleuses",
    broadcast_date=datetime.date(2008, 9, 21),
)
_S2E062B = TargetSegment(season=2, number=62, segment="B", title="Le grand combat sous-marin")


def test_broad_keyword_is_first_and_tagged_broad() -> None:
    keywords = generate_keywords([_S2E062A])
    assert keywords[0] == SearchKeyword(text="keroro", origin="broad")


def test_segment_id_keyword_is_zero_padded_and_lowercased() -> None:
    keywords = generate_keywords([_S2E062A])
    texts = [kw.text for kw in keywords]
    assert "062a" in texts
    segment_kw = next(kw for kw in keywords if kw.text == "062a")
    assert segment_kw.origin == "S2E062A"


def test_title_tokens_are_generated_and_tagged_with_target_id() -> None:
    keywords = generate_keywords([_S2E062A])
    texts = [kw.text for kw in keywords]
    assert "demoiselles" in texts
    assert "cambrioleuses" in texts
    token = next(kw for kw in keywords if kw.text == "demoiselles")
    assert token.origin == "S2E062A"


def test_short_title_tokens_are_dropped() -> None:
    # "le" (len 2) reste, mais un token d'un seul caractère est écarté ; on force le cas
    # avec un titre contenant un mot d'une lettre.
    target = TargetSegment(season=2, number=1, segment="A", title="a b cd")
    texts = [kw.text for kw in generate_keywords([target])]
    assert "a" not in texts  # 1 caractère : écarté
    assert "b" not in texts
    assert "cd" in texts  # 2 caractères : gardé


def test_duplicate_tokens_across_targets_appear_once_first_seen_wins() -> None:
    shared = TargetSegment(season=2, number=2, segment="A", title="combat secret")
    other = TargetSegment(season=2, number=3, segment="A", title="combat final")
    keywords = generate_keywords([shared, other])
    combats = [kw for kw in keywords if kw.text == "combat"]
    assert len(combats) == 1
    assert combats[0].origin == "S2E002A"  # premier vu gagne


def test_empty_targets_yields_only_the_broad_keyword() -> None:
    keywords = generate_keywords([])
    assert keywords == (SearchKeyword(text="keroro", origin="broad"),)


def test_keyword_is_frozen_and_hashable() -> None:
    keyword = SearchKeyword(text="keroro", origin="broad")
    assert {keyword, keyword} == {keyword}
    assert hash(keyword) == hash(SearchKeyword(text="keroro", origin="broad"))


def test_two_segments_produce_distinct_segment_ids() -> None:
    texts = [kw.text for kw in generate_keywords([_S2E062A, _S2E062B])]
    assert "062a" in texts
    assert "062b" in texts
```

- [ ] **Step 3: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/domain/search/test_keywords.py -q --no-cov`
Expected: FAIL (collection) — `ModuleNotFoundError: No module named 'emule_indexer.domain.search'`.

- [ ] **Step 4: Écrire l'implémentation**

`src/emule_indexer/domain/search/keywords.py` :
```python
"""Génération des mots-clés de recherche depuis les cibles (PUR, spec orchestration §4).

Domaine PUR : aucune I/O. Deux familles de mots-clés (spec MVP §6) : un mot-clé LARGE
(``keroro``) qui ratisse tout pour le catalogue, et des mots-clés CIBLÉS par segment
(``062a``, tokens du titre) pour la précision. ``generate_keywords`` est déterministe :
même table de cibles → même tuple, ORDONNÉ et DÉDUPLIQUÉ (premier vu gagne), pour que le
shuffle seedé du cycle (``cycle.py``) parte d'un ordre stable.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from emule_indexer.domain.matching.models import TargetSegment
from emule_indexer.domain.normalization import tokenize

# Mot-clé large : la franchise. Ratisse tout pour le catalogue (spec MVP §6).
_BROAD_KEYWORD = "keroro"

# Tokens trop courts/communs pour cibler (le large les couvre déjà) : on ne génère pas
# un mot-clé d'un seul caractère ou d'un mot vide. La barre est volontairement basse.
_MIN_TARGETED_TOKEN_LENGTH = 2


@dataclass(frozen=True)
class SearchKeyword:
    """Un mot-clé à rechercher + sa provenance (``broad`` ou ``target_id``).

    ``text`` est le mot-clé envoyé à EC (déjà normalisé). ``origin`` documente d'où il
    vient (``"broad"`` pour le filet large, sinon le ``target_id`` du segment) : utile au
    logging structuré (§13 MVP) et à un futur scoring. GELÉ et hashable → déduplication
    par ``text`` triviale.
    """

    text: str
    origin: str


def _segment_id_keyword(target: TargetSegment) -> str:
    """Mot-clé d'identifiant de segment, ex. ``062a`` (numéro zéro-paddé sur 3 + lettre
    minuscule, comme les noms de fichiers source ``N°062A``, spec §7). Le ``°``/``n`` est
    laissé de côté : les serveurs eD2k tokenisent sur les non-alphanumériques, donc
    ``062a`` est le token précis qui distingue le segment."""
    return f"{target.number:03d}{target.segment.lower()}"


def generate_keywords(targets: Sequence[TargetSegment]) -> tuple[SearchKeyword, ...]:
    """Construit la liste ORDONNÉE et DÉDUPLIQUÉE des mots-clés (spec MVP §6).

    Ordre : le mot-clé LARGE d'abord, puis, par cible (dans l'ordre des cibles), son
    identifiant de segment puis les tokens significatifs de son titre. Déduplication par
    ``text`` (premier vu gagne) : deux titres partageant un mot ne le recherchent qu'une
    fois. Un token de longueur ``< 2`` est ignoré (le filet large le couvre déjà).
    """
    seen: set[str] = set()
    keywords: list[SearchKeyword] = []

    def add(text: str, origin: str) -> None:
        if text and text not in seen:
            seen.add(text)
            keywords.append(SearchKeyword(text=text, origin=origin))

    add(_BROAD_KEYWORD, "broad")
    for target in targets:
        add(_segment_id_keyword(target), target.target_id)
        for token in tokenize(target.title):
            if len(token) >= _MIN_TARGETED_TOKEN_LENGTH:
                add(token, target.target_id)
    return tuple(keywords)
```

- [ ] **Step 5: Vérifier le passage puis le gate complet**

Run: `uv run pytest tests/domain/search/test_keywords.py -q --no-cov`
Expected: PASS — 8 tests.

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run sqlfluff lint src`
Expected: tout vert, coverage 100 %.

- [ ] **Step 6: Commit**

```bash
git add src/emule_indexer/domain/search tests/domain/search
git commit -m "feat(domain): génération des mots-clés de recherche (larges + ciblés, dédup)"
```

---

## Task 2: Domaine search — `cycle.py` (Rng Protocol + shuffle seedé)

**Files:**
- Create: `src/emule_indexer/domain/search/cycle.py`
- Create: `tests/domain/search/test_cycle.py`

> Le Protocol `Rng` vit ICI (le domaine, là où il est consommé — DÉCISION 3) et sera ré-exporté par `ports/clock.py` (Task 7). `shuffle_for_cycle` ne fait QUE dériver le seed (`node_id`+index) et déléguer au `Rng` ; il ne mute jamais la séquence de l'appelant (passe par un tuple).

- [ ] **Step 1: Écrire le test qui échoue**

`tests/domain/search/test_cycle.py` :
```python
from emule_indexer.domain.search.cycle import Rng, cycle_seed, shuffle_for_cycle


class _ReverseRng:
    """Faux Rng déterministe : rend les items inversés, ignore le seed (satisfait Rng)."""

    def __init__(self) -> None:
        self.seen_seeds: list[str] = []

    def shuffled(self, items: tuple[str, ...], seed: str) -> tuple[str, ...]:
        self.seen_seeds.append(seed)
        return tuple(reversed(items))

    def jitter(self, span: float) -> float:
        return 0.0


def test_protocol_is_satisfied_structurally() -> None:
    rng: Rng = _ReverseRng()
    assert rng.shuffled(("a", "b"), "seed") == ("b", "a")
    assert rng.jitter(5.0) == 0.0


def test_cycle_seed_combines_node_id_and_index() -> None:
    assert cycle_seed("node-A", 5) == "node-A:5"


def test_shuffle_for_cycle_passes_the_derived_seed_to_the_rng() -> None:
    rng = _ReverseRng()
    shuffle_for_cycle(["x", "y", "z"], rng, "node-A", 7)
    assert rng.seen_seeds == ["node-A:7"]


def test_shuffle_for_cycle_returns_the_rng_permutation() -> None:
    rng = _ReverseRng()
    assert shuffle_for_cycle(["a", "b", "c"], rng, "n", 0) == ("c", "b", "a")


def test_shuffle_for_cycle_does_not_mutate_the_input() -> None:
    rng = _ReverseRng()
    items = ["a", "b", "c"]
    shuffle_for_cycle(items, rng, "n", 0)
    assert items == ["a", "b", "c"]  # le tuple interne protège la séquence de l'appelant
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/domain/search/test_cycle.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'emule_indexer.domain.search.cycle'`.

- [ ] **Step 3: Écrire l'implémentation**

`src/emule_indexer/domain/search/cycle.py` :
```python
"""Ordre de parcours d'un cycle, seedé par nœud (PUR, spec orchestration §3/§4).

Domaine PUR : aucune I/O, aucune horloge, aucun ``random`` GLOBAL. Le shuffle est confié
à un port ``Rng`` injecté (``ports/clock.py``) pour rester déterministe et testable ; ce
module ne fait QUE construire le SEED (``node_id`` + index de cycle) et appliquer le
mélange. Propriété recherchée (spec MVP §6) : deux nœuds DIFFÉRENTS divergent (angles
morts temporels supprimés), un même nœud au même cycle REJOUE le même ordre.
"""

from collections.abc import Sequence
from typing import Protocol


class Rng(Protocol):
    """Port du hasard injectable. ``shuffled`` rend une PERMUTATION de ``items`` dérivée
    UNIQUEMENT du ``seed`` (même seed → même ordre). ``jitter`` rend un flottant dans
    ``[0, span)`` (anti-thundering-herd du backoff, spec §3 « + jitter »). Implémenté côté
    adapter par ``random.Random`` (``adapters/clock_asyncio.py``) ; remplacé en test par un
    faux DÉTERMINISTE (zéro flakiness)."""

    def shuffled(self, items: tuple[str, ...], seed: str) -> tuple[str, ...]: ...

    def jitter(self, span: float) -> float: ...


def cycle_seed(node_id: str, cycle_index: int) -> str:
    """Seed du cycle : ``node_id`` + index, séparés par ``:`` (spec §3).

    Le ``node_id`` fait diverger les nœuds ; l'``cycle_index`` fait varier l'ordre d'un
    cycle au suivant SUR le même nœud (sinon l'ordre serait figé à vie).
    """
    return f"{node_id}:{cycle_index}"


def shuffle_for_cycle(
    items: Sequence[str], rng: Rng, node_id: str, cycle_index: int
) -> tuple[str, ...]:
    """Permutation déterministe de ``items`` pour ce ``(node_id, cycle_index)`` (spec §4).

    L'ordre d'entrée est sans importance pour le résultat (le seed le détermine
    entièrement) ; on passe par un tuple pour ne JAMAIS muter la séquence de l'appelant.
    """
    return rng.shuffled(tuple(items), cycle_seed(node_id, cycle_index))
```

- [ ] **Step 4: Vérifier puis gate**

Run: `uv run pytest tests/domain/search/test_cycle.py -q --no-cov`  → PASS (5 tests).
Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run sqlfluff lint src` → tout vert, 100 %.

- [ ] **Step 5: Commit**

```bash
git add src/emule_indexer/domain/search/cycle.py tests/domain/search/test_cycle.py
git commit -m "feat(domain): ordre de cycle seedé (Rng Protocol + shuffle_for_cycle)"
```

---

## Task 3: Domaine search — `backoff.py`

**Files:**
- Create: `src/emule_indexer/domain/search/backoff.py`
- Create: `tests/domain/search/test_backoff.py`

- [ ] **Step 1: Écrire le test qui échoue**

`tests/domain/search/test_backoff.py` :
```python
from emule_indexer.domain.search.backoff import backoff_delay


def test_first_attempt_is_the_base_delay() -> None:
    assert backoff_delay(1, base=2.0, cap=60.0, factor=2.0) == 2.0


def test_delay_grows_exponentially_by_factor() -> None:
    assert backoff_delay(2, base=2.0, cap=60.0, factor=2.0) == 4.0
    assert backoff_delay(3, base=2.0, cap=60.0, factor=2.0) == 8.0
    assert backoff_delay(4, base=2.0, cap=60.0, factor=2.0) == 16.0


def test_delay_is_capped() -> None:
    assert backoff_delay(10, base=2.0, cap=30.0, factor=2.0) == 30.0


def test_base_above_cap_is_also_capped_on_first_attempt() -> None:
    # base > cap : même la première tentative est plafonnée (config pathologique mais sûre).
    assert backoff_delay(1, base=100.0, cap=30.0, factor=2.0) == 30.0


def test_attempt_zero_or_negative_is_treated_as_the_first() -> None:
    assert backoff_delay(0, base=2.0, cap=60.0, factor=2.0) == 2.0
    assert backoff_delay(-5, base=2.0, cap=60.0, factor=2.0) == 2.0
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/domain/search/test_backoff.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Écrire l'implémentation**

`src/emule_indexer/domain/search/backoff.py` :
```python
"""Backoff exponentiel plafonné, math PURE (spec orchestration §3/§4 ; spec MVP §6/§14).

Domaine PUR : aucune I/O, aucun ``random`` global, aucune horloge. ``backoff_delay``
calcule le délai NOMINAL (exponentiel borné par ``cap``) ; le JITTER est appliqué par
l'appelant (il a besoin du port ``Rng``/d'un tirage) — séparer le calcul déterministe du
tirage garde ce module trivialement testable et le jitter rejouable. Utilisé par
``application/search_worker.py`` pour le backoff PAR (instance, canal) (spec §3).
"""


def backoff_delay(attempt: int, *, base: float, cap: float, factor: float) -> float:
    """Délai de backoff pour la ``attempt``-ième tentative consécutive en échec (≥ 1).

    ``attempt = 1`` → ``base`` ; chaque échec supplémentaire multiplie par ``factor`` ;
    le résultat est plafonné à ``cap`` (spec MVP §6 : « backoff exponentiel »). Un
    ``attempt`` à 0 ou négatif est traité comme la première tentative (``base``) — un
    appelant ne doit jamais demander un délai pour « zéro échec », mais on ne crashe pas
    sur une entrée hors-borne (résilience, spec §14).
    """
    if attempt <= 1:
        return min(base, cap)
    return min(base * factor ** (attempt - 1), cap)
```

- [ ] **Step 4: Vérifier puis gate**

Run: `uv run pytest tests/domain/search/test_backoff.py -q --no-cov` → PASS (5 tests).
Run: gate complet → tout vert, 100 %.

- [ ] **Step 5: Commit**

```bash
git add src/emule_indexer/domain/search/backoff.py tests/domain/search/test_backoff.py
git commit -m "feat(domain): backoff exponentiel plafonné (math pure)"
```

---

## Task 4: Domaine search — `coverage.py`

**Files:**
- Create: `src/emule_indexer/domain/search/coverage.py`
- Create: `tests/domain/search/test_coverage.py`

> DÉCISION 4 : `effective_coverage` prend des BOOLÉENS (« telle instance peut faire aboutir une recherche »), PAS des `NetworkStatus` (qui vit dans un port). La traduction `NetworkStatus → bool` est faite par l'application (Task 13).

- [ ] **Step 1: Écrire le test qui échoue**

`tests/domain/search/test_coverage.py` :
```python
from emule_indexer.domain.search.coverage import Coverage, effective_coverage


def test_no_instances_is_blind() -> None:
    assert effective_coverage([]) is Coverage.BLIND


def test_all_incapable_is_blind() -> None:
    assert effective_coverage([False, False]) is Coverage.BLIND


def test_all_capable_is_healthy() -> None:
    assert effective_coverage([True, True, True]) is Coverage.HEALTHY


def test_single_capable_is_healthy() -> None:
    assert effective_coverage([True]) is Coverage.HEALTHY


def test_mixed_is_degraded() -> None:
    assert effective_coverage([True, False]) is Coverage.DEGRADED


def test_coverage_is_a_closed_enum() -> None:
    assert set(Coverage) == {Coverage.HEALTHY, Coverage.DEGRADED, Coverage.BLIND}
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/domain/search/test_coverage.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Écrire l'implémentation**

`src/emule_indexer/domain/search/coverage.py` :
```python
"""Couverture EFFECTIVE du réseau, dérivée des statuts (PUR, spec orchestration §7 ; MVP §13).

Domaine PUR : reçoit des faits BOOLÉENS déjà observés (« telle instance peut-elle faire
aboutir une recherche ? ») et rend un signal agrégé. « Le process vit » ≠ « on peut
trouver maintenant » (spec MVP §13) : ``effective_coverage`` répond à la seconde question.

Le domaine NE connaît PAS ``NetworkStatus`` (qui vit dans ``ports`` — règle de dépendance
``ports ← application → domain`` : le domaine n'importe jamais un port). C'est
l'APPLICATION (``run_search_cycle``) qui traduit chaque ``NetworkStatus`` en booléen
« search-capable » (HighID eD2k OU Kad CONNECTED) avant d'appeler cette fonction pure.
"""

from collections.abc import Sequence
from enum import StrEnum


class Coverage(StrEnum):
    """Signal agrégé de couverture (spec MVP §13). Enum fermé."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    BLIND = "blind"


def effective_coverage(search_capable: Sequence[bool]) -> Coverage:
    """Agrège la capacité de recherche par instance en un signal (spec MVP §13).

    Aucune instance (liste vide) OU aucune capable → ``BLIND`` (on ne peut rien trouver,
    loggé fort par l'appelant, spec §7). Toutes capables → ``HEALTHY``. Mélange →
    ``DEGRADED`` (certaines instances aveugles). ``any(())`` vaut ``False`` → la liste
    vide tombe bien sur ``BLIND``.
    """
    if not any(search_capable):
        return Coverage.BLIND
    if all(search_capable):
        return Coverage.HEALTHY
    return Coverage.DEGRADED
```

- [ ] **Step 4: Vérifier puis gate**

Run: `uv run pytest tests/domain/search/test_coverage.py -q --no-cov` → PASS (6 tests).
Run: gate complet → tout vert, 100 %.

- [ ] **Step 5: Commit**

```bash
git add src/emule_indexer/domain/search/coverage.py tests/domain/search/test_coverage.py
git commit -m "feat(domain): effective_coverage (healthy/degraded/blind depuis des booléens)"
```

---

## Task 5: Domaine moteur — `DecisionRecord` + `to_record`

**Files:**
- Modify: `src/emule_indexer/domain/matching/engine.py`
- Create: `tests/domain/matching/test_decision_record.py`

> DÉCISION 2 : la forme COMPARABLE d'une décision (les 3 colonnes persistées, sans `explanation`) pour l'anti-redondance. Ajout PUR au moteur (pas de nouveau fichier).

- [ ] **Step 1: Écrire le test qui échoue**

`tests/domain/matching/test_decision_record.py` :
```python
from emule_indexer.domain.matching.engine import (
    DecisionRecord,
    Explanation,
    MatchDecision,
    to_record,
)


def _decision(tier: str = "download") -> MatchDecision:
    return MatchDecision(
        target_id="S2E062A",
        rule_name="id_segment_exact",
        tier=tier,
        explanation=Explanation(
            target_id="S2E062A",
            rules_fired=("id_segment_exact",),
            tokens_matched=(),
            coverage_values=(),
        ),
    )


def test_to_record_projects_the_three_comparable_fields() -> None:
    record = to_record(_decision())
    assert record == DecisionRecord(
        target_id="S2E062A", rule_name="id_segment_exact", tier="download"
    )


def test_decision_record_is_frozen_and_equal_by_value() -> None:
    a = DecisionRecord(target_id="S2E062A", rule_name="r", tier="catalog")
    b = DecisionRecord(target_id="S2E062A", rule_name="r", tier="catalog")
    assert a == b
    assert hash(a) == hash(b)


def test_records_differ_when_any_field_differs() -> None:
    base = to_record(_decision(tier="download"))
    assert base != to_record(_decision(tier="notify"))
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/domain/matching/test_decision_record.py -q --no-cov`
Expected: FAIL — `ImportError: cannot import name 'DecisionRecord'`.

- [ ] **Step 3: Modifier `engine.py`**

Dans `src/emule_indexer/domain/matching/engine.py`, juste APRÈS la définition de la dataclass `MatchDecision` (le bloc `class MatchDecision: … explanation: Explanation`) et AVANT le commentaire `# Rang des paliers …`, insérer :
```python
@dataclass(frozen=True)
class DecisionRecord:
    """Les 3 colonnes COMPARABLES d'une décision persistée, sans l'explicabilité runtime.

    C'est exactement ce que ``match_decisions`` stocke (§11) — ``target_id``/``rule_name``/
    ``tier`` — relu pour l'anti-redondance (spec orchestration §3 : ne ré-``record_decision``
    que si le verdict CHANGE). Volontairement distinct de :class:`MatchDecision` : la lecture
    ne peut pas reconstruire l'``explanation`` (non persistée), et deux ``DecisionRecord``
    s'égalent ssi leurs trois champs s'égalent (dataclass gelé → ``==`` champ par champ).
    """

    target_id: str
    rule_name: str
    tier: str


def to_record(decision: MatchDecision) -> DecisionRecord:
    """Projette une :class:`MatchDecision` (qui vient de tomber) sur sa forme comparable.

    Permet à l'application de comparer le verdict FRAIS au dernier ``DecisionRecord`` connu
    sans manipuler l'``explanation`` (spec orchestration §3, anti-redondance).
    """
    return DecisionRecord(
        target_id=decision.target_id, rule_name=decision.rule_name, tier=decision.tier
    )
```

- [ ] **Step 4: Vérifier puis gate**

Run: `uv run pytest tests/domain/matching/test_decision_record.py -q --no-cov` → PASS (3 tests).
Run: gate complet → tout vert, 100 %.

- [ ] **Step 5: Commit**

```bash
git add src/emule_indexer/domain/matching/engine.py tests/domain/matching/test_decision_record.py
git commit -m "feat(domain): DecisionRecord + to_record (forme comparable pour l'anti-redondance)"
```

---

## Task 6: Ports — `clock.py`, `decision_signal.py`, `repository_errors.py`, `scheduler_state_repository.py`

**Files:**
- Create: `src/emule_indexer/ports/clock.py`
- Create: `src/emule_indexer/ports/decision_signal.py`
- Create: `src/emule_indexer/ports/repository_errors.py`
- Create: `src/emule_indexer/ports/scheduler_state_repository.py`
- Create: `tests/ports/test_clock.py`
- Create: `tests/ports/test_decision_signal.py`
- Create: `tests/ports/test_repository_errors.py`
- Create: `tests/ports/test_scheduler_state_repository.py`

> Quatre ports en une tâche (chacun trivial, stubs une ligne couverts par le `def`). `Rng` est RÉ-EXPORTÉ depuis `domain/search/cycle.py` (DÉCISION 3) ; `Clock` porte `now()` aware + `sleep` async. `SchedulerStateRepository` porte `read_cycle_index`/`write_cycle_state(datetime)` + `load_channel_backoff`/`save_channel_backoff` (backoff PERSISTÉ, DÉCISION 7) + le DTO gelé `ChannelBackoff(attempts, retry_after)`.

- [ ] **Step 1: Écrire les tests qui échouent**

`tests/ports/test_clock.py` :
```python
from datetime import UTC, datetime

import pytest

from emule_indexer.ports.clock import Clock, Rng


class _StubClock:
    """Satisfait Clock structurellement (sans l'importer)."""

    def now(self) -> datetime:
        return datetime(2026, 6, 12, tzinfo=UTC)

    async def sleep(self, seconds: float) -> None:
        return None


class _StubRng:
    def shuffled(self, items: tuple[str, ...], seed: str) -> tuple[str, ...]:
        return items

    def jitter(self, span: float) -> float:
        return 0.0


def test_clock_protocol_is_satisfied_structurally() -> None:
    clock: Clock = _StubClock()
    assert clock.now() == datetime(2026, 6, 12, tzinfo=UTC)


@pytest.mark.asyncio
async def test_clock_sleep_is_awaitable() -> None:
    clock: Clock = _StubClock()
    await clock.sleep(1.0)  # ne lève pas ; rend None (contrat)


def test_rng_is_reexported_from_the_domain() -> None:
    rng: Rng = _StubRng()
    assert rng.shuffled(("a", "b"), "seed") == ("a", "b")
    assert rng.jitter(5.0) == 0.0
```

`tests/ports/test_decision_signal.py` :
```python
from emule_indexer.ports.decision_signal import DecisionSignal


class _StubSignal:
    """Satisfait DecisionSignal structurellement (sans l'importer)."""

    def __init__(self) -> None:
        self.signalled: list[str] = []

    def signal(self, subject: str) -> None:
        self.signalled.append(subject)

    async def wait(self, subject: str) -> None:
        return None


def test_protocol_is_satisfied_structurally() -> None:
    hub: DecisionSignal = _StubSignal()
    hub.signal("S2E062A")
    assert isinstance(hub, _StubSignal)
    assert hub.signalled == ["S2E062A"]
```

`tests/ports/test_repository_errors.py` :
```python
from emule_indexer.ports.repository_errors import RepositoryError


def test_repository_error_is_an_exception() -> None:
    assert issubclass(RepositoryError, Exception)
    error = RepositoryError("boum")
    assert str(error) == "boum"
```

`tests/ports/test_scheduler_state_repository.py` :
```python
import dataclasses
from datetime import UTC, datetime

import pytest

from emule_indexer.ports.scheduler_state_repository import (
    ChannelBackoff,
    SchedulerStateRepository,
)


class _StubRepository:
    """Satisfait SchedulerStateRepository structurellement (sans l'importer)."""

    def __init__(self) -> None:
        self.index = 0
        self.writes: list[tuple[int, datetime]] = []
        self.backoff: dict[str, ChannelBackoff] = {}

    def read_cycle_index(self) -> int:
        return self.index

    def write_cycle_state(self, cycle_index: int, last_full_cycle_at: datetime) -> None:
        self.writes.append((cycle_index, last_full_cycle_at))

    def load_channel_backoff(self) -> dict[str, ChannelBackoff]:
        return dict(self.backoff)

    def save_channel_backoff(self, backoff: dict[str, ChannelBackoff]) -> None:
        self.backoff = dict(backoff)


def test_channel_backoff_is_frozen_and_holds_fields() -> None:
    state = ChannelBackoff(attempts=2, retry_after="2026-06-12T10:05:00.000000+00:00")
    assert state.attempts == 2
    assert state.retry_after == "2026-06-12T10:05:00.000000+00:00"
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.attempts = 3  # type: ignore[misc]


def test_protocol_is_satisfied_structurally() -> None:
    repository: SchedulerStateRepository = _StubRepository()
    assert repository.read_cycle_index() == 0
    moment = datetime(2026, 6, 12, tzinfo=UTC)
    repository.write_cycle_state(3, moment)
    assert repository.load_channel_backoff() == {}
    state = {
        "amule-1:kad": ChannelBackoff(attempts=1, retry_after="2026-06-12T10:00:00.000000+00:00")
    }
    repository.save_channel_backoff(state)
    assert isinstance(repository, _StubRepository)
    assert repository.writes == [(3, moment)]
    assert repository.load_channel_backoff() == state
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/ports -q --no-cov`
Expected: FAIL (collection) — `ModuleNotFoundError: No module named 'emule_indexer.ports.clock'`.

- [ ] **Step 3: Écrire les quatre ports**

`src/emule_indexer/ports/clock.py` :
```python
"""Ports ``Clock`` et ``Rng`` : le temps et le hasard, injectables (spec orchestration §3).

Déterminisme TOTAL (spec §3) : l'application ne lit jamais l'horloge système ni un
``random`` global directement — elle passe par ces ports, que les tests remplacent par des
fausses implémentations avançables/seedées (zéro flakiness, tout cycle rejouable).

``Clock`` porte un ``now()`` AWARE (UTC) ET un ``sleep`` ASYNC (le cycle dort entre deux
itérations) : les deux faces du temps dont l'orchestration a besoin. Le ``sleep`` est sur
le port pour qu'un faux puisse l'avancer SANS attente réelle.

``Rng`` est le mélangeur déterministe consommé par ``domain/search/cycle.py`` ; il est
RÉ-EXPORTÉ ici depuis le domaine (la définition canonique du Protocol vit dans le domaine,
là où il est consommé — règle de dépendance : le domaine n'importe jamais un port). Ce
ré-export donne aux adapters/composition un point d'import unique « les ports du temps ».
"""

from datetime import datetime
from typing import Protocol

from emule_indexer.domain.search.cycle import Rng

__all__ = ["Clock", "Rng"]


class Clock(Protocol):
    """Le temps, injectable : ``now()`` aware (UTC) + ``sleep`` async (spec §3).

    Implémenté côté adapter par ``datetime.now(UTC)`` + ``asyncio.sleep`` ; remplacé en
    test par une fausse horloge avançable (le ``sleep`` avance le ``now`` sans attente).
    """

    def now(self) -> datetime: ...

    async def sleep(self, seconds: float) -> None: ...
```

`src/emule_indexer/ports/decision_signal.py` :
```python
"""Port ``DecisionSignal`` : le hub de nudge in-process (spec orchestration §3).

Couplage par la donnée + nudge (spec §3) : la boucle persiste la décision (append-only =
fiabilité gratuite, un consommateur absent rejoue depuis la table) PUIS ``signal``e le hub
pour réveiller un consommateur in-process immédiatement. Le polling de repli reste le
filet ; un nudge perdu est inoffensif. Le « sujet » est l'identité de ce qui a changé (en
plan C : le ``ed2k_hash`` dont le verdict a changé) — un consommateur futur (plan D/E)
``await wait(subject)``.

Protocol ASYNC. ``signal`` est synchrone (appelé depuis le pipeline sync post-commit, ne
doit jamais bloquer) ; ``wait`` est async (le consommateur s'endort dessus). Implémenté
côté adapter par un ``asyncio.Event`` par sujet (``adapters/decision_signal_asyncio.py``).
"""

from typing import Protocol


class DecisionSignal(Protocol):
    """Hub de réveil in-process (spec §3). ``signal`` réveille tout ``wait`` du même sujet."""

    def signal(self, subject: str) -> None: ...

    async def wait(self, subject: str) -> None: ...
```

`src/emule_indexer/ports/repository_errors.py` :
```python
"""Contrat d'erreur des repositories (spec orchestration §4/§7).

Couche PORTS : le CONTRAT d'erreur que l'application catch (« une ``RepositoryError`` sur
une obs est loggée, le cycle continue », spec §7) vit au niveau du port, JAMAIS d'un
adapter — sinon l'application dépendrait d'un adapter (règle de dépendance §4). L'adapter
SQLite fait hériter sa ``PersistenceError`` de ``RepositoryError`` (dépendance adapter→port,
licite). L'application ne connaît que ``RepositoryError``.
"""


class RepositoryError(Exception):
    """Échec de persistance signalé par un repository (l'adapter signale, il ne décide pas)."""
```

`src/emule_indexer/ports/scheduler_state_repository.py` :
```python
"""Port ``SchedulerStateRepository`` : l'état durable de l'ordonnanceur (spec §4/§7).

Couche PORTS, Protocol SYNCHRONE (même principe que ``LocalStateRepository`` : sub-ms,
pas de ``to_thread`` en MVP). Persiste ce que la reprise après crash relit (spec §7) :
l'INDEX de cycle (n'avance qu'en FIN de cycle → un kill au milieu rejoue les mots-clés
restants), l'horodatage du dernier cycle complet, ET le BACKOFF par (instance, canal)
(spec §3/§7 : il doit survivre à un redémarrage). Tout est stocké en KV dans la table
``scheduler_state`` de ``local.db`` (jamais fusionné, invariant §11).

Le backoff est sérialisé en JSON sous UNE clé (``channel_backoff``) : une map
``{ "amule-1:kad": {attempts, retry_after}, "amule-1": {...} }`` — la clé est soit
``instance:canal`` (échec d'un canal), soit ``instance`` seule (reconnexion). ``retry_after``
est un ISO-8601 UTC à largeur fixe (comparaison lexicographique == chronologique).
``read_cycle_index`` rend ``0`` si jamais écrit (premier démarrage) ; ``load_channel_backoff``
rend un dict vide.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class ChannelBackoff:
    """État de backoff d'une clé (instance, ou instance:canal) : compteur + échéance.

    ``attempts`` = nombre d'échecs CONSÉCUTIFS (sert au calcul exponentiel). ``retry_after``
    = ISO-8601 UTC à largeur fixe : tant que ``now < retry_after``, la clé est SAUTÉE. Gelé
    et JSON-friendly (deux champs scalaires) → sérialisation triviale.
    """

    attempts: int
    retry_after: str


class SchedulerStateRepository(Protocol):
    """Contrat sync de l'état d'ordonnancement (index de cycle + dernier cycle + backoff).

    ``write_cycle_state`` reçoit un ``datetime`` AWARE (l'application passe ``clock.now()``,
    qui ne dépend d'aucun adapter) ; le formatage ISO-8601 est interne à l'adapter SQLite.
    ``save_channel_backoff`` remplace ENTIÈREMENT la map persistée (snapshot du registre).
    """

    def read_cycle_index(self) -> int: ...

    def write_cycle_state(self, cycle_index: int, last_full_cycle_at: datetime) -> None: ...

    def load_channel_backoff(self) -> dict[str, ChannelBackoff]: ...

    def save_channel_backoff(self, backoff: dict[str, ChannelBackoff]) -> None: ...
```

- [ ] **Step 4: Vérifier puis gate**

Run: `uv run pytest tests/ports -q --no-cov` → PASS (les nouveaux + existants).
Run: gate complet → tout vert, 100 %.

- [ ] **Step 5: Commit**

```bash
git add src/emule_indexer/ports/clock.py src/emule_indexer/ports/decision_signal.py src/emule_indexer/ports/repository_errors.py src/emule_indexer/ports/scheduler_state_repository.py tests/ports/test_clock.py tests/ports/test_decision_signal.py tests/ports/test_repository_errors.py tests/ports/test_scheduler_state_repository.py
git commit -m "feat(ports): Clock/Rng, DecisionSignal, RepositoryError, SchedulerStateRepository"
```

---

## Task 7: Contrat d'erreur du client — port + héritage EC

**Files:**
- Modify: `src/emule_indexer/ports/mule_client.py`
- Modify: `src/emule_indexer/adapters/mule_ec/errors.py`
- Create: `tests/ports/test_mule_client_errors.py`

> DÉCISION 1. Le port déclare `MuleClientError → MuleUnreachableError / MuleSearchFailedError` ; les `EcError` en HÉRITENT (adapter→port). Vérif empirique 8 : MRO diamant propre.

- [ ] **Step 1: Écrire le test qui échoue**

`tests/ports/test_mule_client_errors.py` :
```python
from emule_indexer.adapters.mule_ec.errors import (
    EcAuthError,
    EcConnectError,
    EcFailureError,
    EcProtocolError,
    EcTimeoutError,
)
from emule_indexer.ports.mule_client import (
    MuleClientError,
    MuleSearchFailedError,
    MuleUnreachableError,
)


def test_unreachable_and_search_failed_are_mule_client_errors() -> None:
    assert issubclass(MuleUnreachableError, MuleClientError)
    assert issubclass(MuleSearchFailedError, MuleClientError)


def test_transport_failures_are_unreachable() -> None:
    for cls in (EcConnectError, EcTimeoutError, EcProtocolError):
        assert issubclass(cls, MuleUnreachableError)


def test_application_failure_is_search_failed_not_unreachable() -> None:
    assert issubclass(EcFailureError, MuleSearchFailedError)
    assert not issubclass(EcFailureError, MuleUnreachableError)


def test_auth_error_is_not_a_loop_error() -> None:
    # L'échec d'auth est un problème de config (fail-fast au démarrage), pas un cas de boucle.
    assert issubclass(EcAuthError, MuleClientError)
    assert not issubclass(EcAuthError, MuleUnreachableError)
    assert not issubclass(EcAuthError, MuleSearchFailedError)
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/ports/test_mule_client_errors.py -q --no-cov`
Expected: FAIL — `ImportError: cannot import name 'MuleClientError'`.

- [ ] **Step 3: Modifier le port `mule_client.py`**

Dans `src/emule_indexer/ports/mule_client.py`, étendre le docstring du module (ajouter le paragraphe sur le contrat d'erreur) et insérer les 3 classes APRÈS l'import `from emule_indexer.domain.observation import FileObservation` (avant `class SearchChannel`). Remplacer le bloc d'en-tête + import par :
```python
"""Port ``MuleClient`` : ce que le crawler attend d'un client eMule (cf. spec EC-adapter §4).

Le port n'importe QUE le domaine. Les stubs du Protocol tiennent sur UNE ligne (le ``def``
s'exécute à la création de la classe : couvert). La convenance ``search_and_wait`` (poll +
timeout) vit dans l'outil probe, PAS ici : le polling appartient à l'appelant (spec §3).

Le port déclare aussi le CONTRAT d'ERREUR du client (spec orchestration §7, « le client
signale, le plan C décide ») : ``MuleUnreachableError`` (flux mort → reconnexion par
l'appelant) vs ``MuleSearchFailedError`` (échec applicatif d'un canal → backoff). L'adapter
EC fait inhériter ses ``EcError`` de ces classes (dépendance adapter→port, licite), de
sorte que l'APPLICATION ne dépende JAMAIS d'un adapter (règle de dépendance §4).
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from emule_indexer.domain.observation import FileObservation


class MuleClientError(Exception):
    """Base du contrat d'erreur du client eMule (spec orchestration §7)."""


class MuleUnreachableError(MuleClientError):
    """Le daemon est injoignable ou le flux est mort → reconnexion par l'appelant (§7)."""


class MuleSearchFailedError(MuleClientError):
    """Échec applicatif d'une recherche signalé par le daemon → backoff de canal (§7)."""
```
(Le reste du fichier — `SearchChannel`, `KadStatus`, `NetworkStatus`, `MuleClient` — est INCHANGÉ.)

- [ ] **Step 4: Modifier l'adapter `mule_ec/errors.py` (héritage du contrat)**

Remplacer TOUT le contenu de `src/emule_indexer/adapters/mule_ec/errors.py` par :
```python
"""Hiérarchie d'erreurs de l'adapter EC (cf. spec EC-adapter §6 ; orchestration §7).

L'adapter SIGNALE, il ne décide pas : pas de retry caché, pas de crash silencieux. Cette
hiérarchie permet à l'appelant (plan C) de distinguer « amuled est down » (EcConnectError)
de « ma config est fausse » (EcAuthError), une trame illisible (EcProtocolError) d'un échec
applicatif proprement signalé par le daemon (EcFailureError).

Le CONTRAT d'erreur consommé par l'application vit dans le PORT (``ports/mule_client.py`` :
``MuleUnreachableError``/``MuleSearchFailedError``) ; les classes EC ci-dessous en HÉRITENT
(dépendance adapter→port, licite) pour que l'application ne dépende JAMAIS de cet adapter
(règle de dépendance, spec orchestration §4). Le mapping : flux mort (connexion/timeout/
trame illisible) → ``MuleUnreachableError`` ; ``EC_OP_FAILED`` → ``MuleSearchFailedError`` ;
l'échec d'AUTH reste hors contrat de boucle (problème de config, fail-fast au démarrage).
"""

from emule_indexer.ports.mule_client import (
    MuleClientError,
    MuleSearchFailedError,
    MuleUnreachableError,
)


class EcError(MuleClientError):
    """Base de toutes les erreurs de l'adapter EC (sous le contrat de port)."""


class EcConnectError(EcError, MuleUnreachableError):
    """TCP refusé, connexion perdue, ou opération sans connexion établie."""


class EcAuthError(EcError):
    """Authentification refusée (mot de passe ou version de protocole) — pas un cas de boucle."""


class EcProtocolError(EcError, MuleUnreachableError):
    """Trame malformée ou réponse inattendue (l'entrée réseau est non fiable) → flux mort."""


class EcTimeoutError(EcError, MuleUnreachableError):
    """Délai dépassé (lecture réseau ou établissement de connexion) → flux mort."""


class EcFailureError(EcError, MuleSearchFailedError):
    """Échec applicatif signalé par le daemon (EC_OP_FAILED) ; porte son message."""
```

- [ ] **Step 5: Vérifier puis gate**

Run: `uv run pytest tests/ports/test_mule_client_errors.py -q --no-cov` → PASS (4 tests).
Run: gate complet → tout vert (les tests EC existants restent verts : l'héritage n'altère pas le comportement de `raise`/`except EcError`). 100 %.

- [ ] **Step 6: Commit**

```bash
git add src/emule_indexer/ports/mule_client.py src/emule_indexer/adapters/mule_ec/errors.py tests/ports/test_mule_client_errors.py
git commit -m "feat(ports,adapters): contrat d'erreur du client dans le port, EC errors en héritent"
```

---

## Task 8: `last_decision` — port, adapter, `PersistenceError → RepositoryError`

**Files:**
- Modify: `src/emule_indexer/ports/catalog_repository.py`
- Modify: `src/emule_indexer/adapters/persistence_sqlite/errors.py`
- Modify: `src/emule_indexer/adapters/persistence_sqlite/catalog_repository.py`
- Modify: `tests/ports/test_catalog_repository.py`
- Create: `tests/adapters/persistence_sqlite/test_catalog_last_decision.py`

> DÉCISION 2 (rend un `DecisionRecord`). Vérif empirique 5 : SQL `ORDER BY decided_at DESC, id DESC LIMIT 1`, index existant. `PersistenceError` hérite de `RepositoryError` (contrat de port). Le stub du test de port EXISTANT doit gagner `last_decision` (le Protocol l'exige désormais).

- [ ] **Step 1: Étendre les tests (qui échouent)**

Dans `tests/ports/test_catalog_repository.py` — remplacer la ligne d'import du moteur et ajouter `last_decision` au stub + un assert. Remplacer :
```python
from emule_indexer.domain.matching.engine import Explanation, MatchDecision
```
par :
```python
from emule_indexer.domain.matching.engine import (
    DecisionRecord,
    Explanation,
    MatchDecision,
)
```
Ajouter dans la classe `_StubRepository` (après `record_decision`) :
```python
    def last_decision(self, ed2k_hash: str) -> DecisionRecord | None:
        return None
```
Ajouter dans `test_protocol_is_satisfied_structurally`, juste avant `assert stub.observations == …` :
```python
    assert repository.last_decision(observation.ed2k_hash) is None
```

`tests/adapters/persistence_sqlite/test_catalog_last_decision.py` (NOUVEAU) :
```python
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from emule_indexer.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from emule_indexer.adapters.persistence_sqlite.connection import open_catalog
from emule_indexer.domain.matching.engine import (
    DecisionRecord,
    Explanation,
    MatchDecision,
)
from emule_indexer.domain.observation import FileObservation

_HASH = "31d6cfe0d16ae931b73c59d7e0c089c0"
_NODE = "11111111-2222-3333-4444-555555555555"


class _AdvancingClock:
    """Horloge fausse qui avance d'1 min par lecture (pour ordonner decided_at)."""

    def __init__(self) -> None:
        self._now = datetime(2026, 6, 12, 10, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        moment = self._now
        self._now += timedelta(minutes=1)
        return moment


def _observation() -> FileObservation:
    return FileObservation(
        ed2k_hash=_HASH,
        filename="Keroro 062A.avi",
        size_bytes=100,
        source_count=1,
        complete_source_count=0,
        keyword="keroro",
    )


def _decision(rule_name: str, tier: str) -> MatchDecision:
    return MatchDecision(
        target_id="S2E062A",
        rule_name=rule_name,
        tier=tier,
        explanation=Explanation(
            target_id="S2E062A", rules_fired=(rule_name,), tokens_matched=(), coverage_values=()
        ),
    )


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    catalog = open_catalog(tmp_path / "catalog.db")
    yield catalog
    catalog.close()


def test_last_decision_is_none_when_never_decided(connection: sqlite3.Connection) -> None:
    repository = SqliteCatalogRepository(connection, _NODE)
    repository.record_observation(_observation())
    assert repository.last_decision(_HASH) is None


def test_last_decision_returns_the_most_recent_record(connection: sqlite3.Connection) -> None:
    repository = SqliteCatalogRepository(connection, _NODE, clock=_AdvancingClock())
    repository.record_observation(_observation())
    repository.record_decision(_HASH, _decision("keroro_large", "catalog"))
    repository.record_decision(_HASH, _decision("id_segment_exact", "download"))
    assert repository.last_decision(_HASH) == DecisionRecord(
        target_id="S2E062A", rule_name="id_segment_exact", tier="download"
    )


def test_last_decision_for_unknown_hash_is_none(connection: sqlite3.Connection) -> None:
    repository = SqliteCatalogRepository(connection, _NODE)
    assert repository.last_decision("f" * 32) is None
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/adapters/persistence_sqlite/test_catalog_last_decision.py -q --no-cov`
Expected: FAIL — `AttributeError: 'SqliteCatalogRepository' object has no attribute 'last_decision'`.

- [ ] **Step 3: Modifier le port `catalog_repository.py`**

Remplacer le bloc import + classe `CatalogRepository` par :
```python
from typing import Protocol

from emule_indexer.domain.matching.engine import DecisionRecord, MatchDecision
from emule_indexer.domain.observation import FileObservation


class CatalogRepository(Protocol):
    """Contrat sync d'écriture du catalogue (append-only ; l'adapter signale, il ne décide pas).

    ``last_decision`` est une LECTURE (anti-redondance, spec orchestration §3) : le dernier
    verdict CONNU pour un hash, ou ``None`` si jamais décidé. Elle rend un
    :class:`DecisionRecord` (les 3 colonnes comparables ``target_id``/``rule_name``/``tier``)
    et NON un :class:`MatchDecision` : ``explanation`` n'est PAS persisté (spec data-model),
    le fabriquer vide serait un mensonge — la comparaison de verdict n'a besoin que de ces
    trois champs.
    """

    def record_observation(self, observation: FileObservation) -> None: ...

    def record_decision(self, ed2k_hash: str, decision: MatchDecision) -> None: ...

    def last_decision(self, ed2k_hash: str) -> DecisionRecord | None: ...
```

- [ ] **Step 4: Modifier `persistence_sqlite/errors.py` (PersistenceError hérite de RepositoryError)**

Remplacer le bloc en-tête + import + classe `PersistenceError` par :
```python
"""Hiérarchie d'erreurs de l'adapter persistence (spec data-model §7 ; orchestration §7).

L'adapter SIGNALE, il ne décide pas (même philosophie que l'adapter EC) : toute
``sqlite3.Error`` inattendue sort enveloppée en ``PersistenceError``, jamais nue.
Un trigger append-only qui se déclenche est un BUG du code appelant, pas un cas
métier → la même ``PersistenceError``. ``wrap_sqlite_errors`` est l'enveloppe
UNIQUE partagée par la connexion et les deux repositories (cause chaînée gardée).

``PersistenceError`` HÉRITE du contrat de port ``RepositoryError`` (``ports/
repository_errors.py``) : l'application catch ``RepositoryError`` (spec orchestration §7,
« une obs en échec est loggée, le cycle continue »), jamais cette classe d'adapter — règle
de dépendance §4. Dépendance adapter→port, licite.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from emule_indexer.ports.repository_errors import RepositoryError


class PersistenceError(RepositoryError):
    """Base de toutes les erreurs de l'adapter persistence (sous le contrat de port)."""
```
(Le reste — `MigrationError`, `wrap_sqlite_errors` — est INCHANGÉ.)

- [ ] **Step 5: Modifier l'adapter `catalog_repository.py` (ajouter `last_decision`)**

Dans `src/emule_indexer/adapters/persistence_sqlite/catalog_repository.py` :

(a) remplacer l'import du moteur :
```python
from emule_indexer.domain.matching.engine import MatchDecision
```
par :
```python
from emule_indexer.domain.matching.engine import DecisionRecord, MatchDecision
```

(b) après la constante `_INSERT_DECISION = """…"""`, ajouter :
```python
# Dernier verdict connu pour un hash (anti-redondance, spec orchestration §3). Tri par
# (decided_at, id) DÉCROISSANT : decided_at à largeur fixe rend l'ordre lexicographique
# chronologique ; id départage deux décisions de la même microseconde (l'INSERT le plus
# récent a l'id le plus grand). L'index idx_match_decisions_ed2k_hash sert le filtre.
_SELECT_LAST_DECISION = """
SELECT target_id, rule_name, tier FROM match_decisions
WHERE ed2k_hash = ?
ORDER BY decided_at DESC, id DESC
LIMIT 1
"""
```

(c) à la FIN de la classe `SqliteCatalogRepository` (après `record_decision`), ajouter la méthode :
```python
    def last_decision(self, ed2k_hash: str) -> DecisionRecord | None:
        """Dernier verdict connu pour ce hash, ou ``None`` (jamais décidé) — LECTURE.

        Anti-redondance (spec orchestration §3) : l'application compare ce
        ``DecisionRecord`` au verdict frais et ne ré-``record_decision`` que s'il diffère.
        Le hash n'est PAS validé canonique ici : c'est une lecture inoffensive (un hash
        non canonique ne matche simplement rien → ``None``).
        """
        with wrap_sqlite_errors():
            row = self._connection.execute(_SELECT_LAST_DECISION, (ed2k_hash,)).fetchone()
        if row is None:
            return None
        return DecisionRecord(target_id=row[0], rule_name=row[1], tier=row[2])
```

- [ ] **Step 6: Vérifier puis gate**

Run: `uv run pytest tests/adapters/persistence_sqlite/test_catalog_last_decision.py tests/ports/test_catalog_repository.py -q --no-cov` → PASS.
Run: gate complet → tout vert, 100 %.

- [ ] **Step 7: Commit**

```bash
git add src/emule_indexer/ports/catalog_repository.py src/emule_indexer/adapters/persistence_sqlite/errors.py src/emule_indexer/adapters/persistence_sqlite/catalog_repository.py tests/ports/test_catalog_repository.py tests/adapters/persistence_sqlite/test_catalog_last_decision.py
git commit -m "feat(adapters): CatalogRepository.last_decision (anti-redondance) + PersistenceError sous RepositoryError"
```

---

## Task 9: Adapter — `SqliteSchedulerStateRepository`

**Files:**
- Create: `src/emule_indexer/adapters/persistence_sqlite/scheduler_state_repository.py`
- Create: `tests/adapters/persistence_sqlite/test_scheduler_state_repository.py`

> DÉCISION 7 : KV (`cycle_index`, `last_full_cycle_at`, **`channel_backoff`**) sur la table `scheduler_state` (existante, NON append-only → UPSERT licite), UPSERT atomique sous `BEGIN IMMEDIATE`. Le backoff est PERSISTÉ (survie au redémarrage). Vrai `local.db` sur `tmp_path` (spec §8) ; panne injectée par trigger de TEST ; round-trip backoff testé à travers une INSTANCE DE REPO NEUVE (simule un redémarrage).

- [ ] **Step 1: Écrire les tests qui échouent**

`tests/adapters/persistence_sqlite/test_scheduler_state_repository.py` :
```python
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from emule_indexer.adapters.persistence_sqlite.connection import open_local
from emule_indexer.adapters.persistence_sqlite.errors import PersistenceError
from emule_indexer.adapters.persistence_sqlite.scheduler_state_repository import (
    SqliteSchedulerStateRepository,
)
from emule_indexer.ports.scheduler_state_repository import ChannelBackoff

_MOMENT = datetime(2026, 6, 12, 9, 30, 0, tzinfo=UTC)
_MOMENT_ISO = "2026-06-12T09:30:00.000000+00:00"
_BACKOFF = {
    "amule-1:kad": ChannelBackoff(attempts=2, retry_after="2026-06-12T10:05:00.000000+00:00"),
    "amule-1": ChannelBackoff(attempts=1, retry_after="2026-06-12T10:02:00.000000+00:00"),
}


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    local = open_local(tmp_path / "local.db")
    yield local
    local.close()


@pytest.fixture
def repository(connection: sqlite3.Connection) -> SqliteSchedulerStateRepository:
    return SqliteSchedulerStateRepository(connection)


def test_read_cycle_index_is_zero_on_a_fresh_database(
    repository: SqliteSchedulerStateRepository,
) -> None:
    assert repository.read_cycle_index() == 0


def test_write_then_read_cycle_index_round_trips(
    repository: SqliteSchedulerStateRepository, connection: sqlite3.Connection
) -> None:
    repository.write_cycle_state(5, _MOMENT)
    assert repository.read_cycle_index() == 5
    stamped = connection.execute(
        "SELECT value FROM scheduler_state WHERE key = 'last_full_cycle_at'"
    ).fetchone()[0]
    assert stamped == _MOMENT_ISO


def test_write_overwrites_previous_state(
    repository: SqliteSchedulerStateRepository,
) -> None:
    repository.write_cycle_state(1, _MOMENT)
    repository.write_cycle_state(2, _MOMENT)
    assert repository.read_cycle_index() == 2


def test_write_with_naive_datetime_is_refused(
    repository: SqliteSchedulerStateRepository,
) -> None:
    # utc_iso REFUSE un datetime naïf (contrat de Clock) — la ValueError remonte.
    with pytest.raises(ValueError, match="aware"):
        repository.write_cycle_state(1, datetime(2026, 6, 12, 9, 30, 0))


def test_write_is_atomic_on_injected_failure(
    repository: SqliteSchedulerStateRepository, connection: sqlite3.Connection
) -> None:
    # Trigger de TEST : fait échouer l'écriture de la 2e clé → la 1re est défaite (atomicité).
    connection.execute(
        "CREATE TRIGGER boom BEFORE INSERT ON scheduler_state"
        " WHEN NEW.key = 'last_full_cycle_at'"
        " BEGIN SELECT RAISE(ABORT, 'panne injectée'); END"
    )
    with pytest.raises(PersistenceError, match="panne injectée"):
        repository.write_cycle_state(7, _MOMENT)
    assert repository.read_cycle_index() == 0  # cycle_index aussi défait


def test_load_channel_backoff_is_empty_on_a_fresh_database(
    repository: SqliteSchedulerStateRepository,
) -> None:
    assert repository.load_channel_backoff() == {}


def test_channel_backoff_round_trips_through_a_new_repo_instance(
    repository: SqliteSchedulerStateRepository, connection: sqlite3.Connection
) -> None:
    # Sauvegarde → NOUVELLE instance de repo (même base) → recharge → identique : c'est la
    # SURVIE AU REDÉMARRAGE (spec §3/§7). Une nouvelle instance n'a aucun état en mémoire.
    repository.save_channel_backoff(_BACKOFF)
    reborn = SqliteSchedulerStateRepository(connection)
    assert reborn.load_channel_backoff() == _BACKOFF


def test_save_channel_backoff_replaces_the_whole_map(
    repository: SqliteSchedulerStateRepository,
) -> None:
    repository.save_channel_backoff(_BACKOFF)
    repository.save_channel_backoff({})  # snapshot vide → remplace tout
    assert repository.load_channel_backoff() == {}


def test_save_channel_backoff_is_atomic_on_injected_failure(
    repository: SqliteSchedulerStateRepository, connection: sqlite3.Connection
) -> None:
    connection.execute(
        "CREATE TRIGGER boom BEFORE INSERT ON scheduler_state"
        " WHEN NEW.key = 'channel_backoff'"
        " BEGIN SELECT RAISE(ABORT, 'panne injectée'); END"
    )
    with pytest.raises(PersistenceError, match="panne injectée"):
        repository.save_channel_backoff(_BACKOFF)
    assert repository.load_channel_backoff() == {}
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/adapters/persistence_sqlite/test_scheduler_state_repository.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: …scheduler_state_repository`.

- [ ] **Step 3: Écrire l'implémentation**

`src/emule_indexer/adapters/persistence_sqlite/scheduler_state_repository.py` :
```python
"""``SqliteSchedulerStateRepository`` : état d'ordonnancement en KV (spec orchestration §4/§7).

Implémente STRUCTURELLEMENT le port ``SchedulerStateRepository``. Stocke trois clés dans la
table ``scheduler_state`` de ``local.db`` : ``cycle_index`` (entier sérialisé en TEXT),
``last_full_cycle_at`` (ISO-8601 UTC) et ``channel_backoff`` (map JSON des
:class:`ChannelBackoff` par clé instance/instance:canal). ``write_cycle_state`` fait UN
UPSERT atomique de l'index + horodatage sous ``BEGIN IMMEDIATE`` (l'index n'avance qu'en FIN
de cycle, spec §7 : atomicité = un crash laisse l'ancien index, donc rejoue ce cycle).
``save_channel_backoff`` remplace ENTIÈREMENT la map (snapshot du registre, écrit au même
moment que ``write_cycle_state`` — voir ``run_search_cycle``). ``read_cycle_index`` rend
``0`` si la clé est absente ; ``load_channel_backoff`` rend un dict vide.

``scheduler_state`` n'est PAS append-only (état mutable, pas le catalogue) : pas de
triggers — l'UPSERT ``ON CONFLICT … DO UPDATE`` est licite.
"""

import json
import sqlite3
from contextlib import suppress
from datetime import datetime
from typing import Any

from emule_indexer.adapters.persistence_sqlite.connection import utc_iso
from emule_indexer.adapters.persistence_sqlite.errors import wrap_sqlite_errors
from emule_indexer.ports.scheduler_state_repository import ChannelBackoff

_SELECT_CYCLE_INDEX = "SELECT value FROM scheduler_state WHERE key = 'cycle_index'"

_SELECT_BACKOFF = "SELECT value FROM scheduler_state WHERE key = 'channel_backoff'"

_UPSERT = """
INSERT INTO scheduler_state (key, value) VALUES (?, ?)
ON CONFLICT (key) DO UPDATE SET value = excluded.value
"""


class SqliteSchedulerStateRepository:
    """Implémentation SQLite du port ``SchedulerStateRepository`` (satisfaction STRUCTURELLE)."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def read_cycle_index(self) -> int:
        """Index du prochain cycle, ``0`` si jamais écrit (premier démarrage)."""
        with wrap_sqlite_errors():
            row = self._connection.execute(_SELECT_CYCLE_INDEX).fetchone()
        return 0 if row is None else int(row[0])

    def write_cycle_state(self, cycle_index: int, last_full_cycle_at: datetime) -> None:
        """UPSERT atomique de l'index + horodatage (FIN de cycle, spec §7).

        ``last_full_cycle_at`` est un ``datetime`` aware ; ``utc_iso`` le formate (et REFUSE
        un naïf, contrat de ``Clock``).
        """
        stamped = utc_iso(last_full_cycle_at)
        with wrap_sqlite_errors():
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(_UPSERT, ("cycle_index", str(cycle_index)))
                self._connection.execute(_UPSERT, ("last_full_cycle_at", stamped))
                self._connection.execute("COMMIT")
            except BaseException:
                with suppress(sqlite3.Error):
                    self._connection.execute("ROLLBACK")
                raise

    def load_channel_backoff(self) -> dict[str, ChannelBackoff]:
        """Relit la map de backoff persistée, ``{}`` si jamais écrite (premier démarrage).

        Chaque entrée JSON ``{"attempts": int, "retry_after": str}`` est reconstruite en
        :class:`ChannelBackoff`. Lecture inoffensive : aucune transaction explicite.
        """
        with wrap_sqlite_errors():
            row = self._connection.execute(_SELECT_BACKOFF).fetchone()
        if row is None:
            return {}
        raw: dict[str, dict[str, Any]] = json.loads(row[0])
        return {
            key: ChannelBackoff(
                attempts=int(entry["attempts"]), retry_after=str(entry["retry_after"])
            )
            for key, entry in raw.items()
        }

    def save_channel_backoff(self, backoff: dict[str, ChannelBackoff]) -> None:
        """Remplace ENTIÈREMENT la map persistée (snapshot du registre, FIN de cycle).

        Sérialisé en JSON trié (``sort_keys`` → diff stable, déterminisme). UPSERT atomique
        sous ``BEGIN IMMEDIATE`` (même discipline que ``write_cycle_state``).
        """
        blob = json.dumps(
            {
                key: {"attempts": state.attempts, "retry_after": state.retry_after}
                for key, state in backoff.items()
            },
            sort_keys=True,
        )
        with wrap_sqlite_errors():
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(_UPSERT, ("channel_backoff", blob))
                self._connection.execute("COMMIT")
            except BaseException:
                with suppress(sqlite3.Error):
                    self._connection.execute("ROLLBACK")
                raise
```

- [ ] **Step 4: Vérifier puis gate**

Run: `uv run pytest tests/adapters/persistence_sqlite/test_scheduler_state_repository.py -q --no-cov` → PASS (9 tests).
Run: gate complet → tout vert, 100 %.

- [ ] **Step 5: Commit**

```bash
git add src/emule_indexer/adapters/persistence_sqlite/scheduler_state_repository.py tests/adapters/persistence_sqlite/test_scheduler_state_repository.py
git commit -m "feat(adapters): SqliteSchedulerStateRepository (cycle_index + last_full_cycle_at + channel_backoff persisté)"
```

---

## Task 10: Adapters — `AsyncioClock`/`SeededRng` + `AsyncioDecisionSignal`

**Files:**
- Create: `src/emule_indexer/adapters/clock_asyncio.py`
- Create: `src/emule_indexer/adapters/decision_signal_asyncio.py`
- Create: `tests/adapters/test_clock_asyncio.py`
- Create: `tests/adapters/test_decision_signal_asyncio.py`

> Implémentations RÉELLES des ports `Clock`/`Rng`/`DecisionSignal` (satisfaction structurelle). `AsyncioDecisionSignal` : un `asyncio.Event` par sujet ; un `signal` sans waiter laisse l'événement armé (un nudge perdu est inoffensif, le `wait` suivant le consomme aussitôt).

- [ ] **Step 1: Écrire les tests qui échouent**

`tests/adapters/test_clock_asyncio.py` :
```python
from datetime import UTC

import pytest

from emule_indexer.adapters.clock_asyncio import AsyncioClock, SeededRng


def test_asyncio_clock_now_is_aware_utc() -> None:
    assert AsyncioClock().now().tzinfo == UTC


@pytest.mark.asyncio
async def test_asyncio_clock_sleep_zero_returns() -> None:
    await AsyncioClock().sleep(0.0)  # ne lève pas ; pas d'attente notable


def test_seeded_rng_same_seed_same_order() -> None:
    items = ("a", "b", "c", "d", "e")
    assert SeededRng().shuffled(items, "node-A:5") == SeededRng().shuffled(items, "node-A:5")


def test_seeded_rng_different_seed_diverges() -> None:
    items = ("a", "b", "c", "d", "e")
    assert SeededRng().shuffled(items, "node-A:5") != SeededRng().shuffled(items, "node-B:5")


def test_seeded_rng_is_a_permutation() -> None:
    items = ("a", "b", "c", "d")
    assert sorted(SeededRng().shuffled(items, "seed")) == sorted(items)


def test_seeded_rng_does_not_mutate_input() -> None:
    items = ("a", "b", "c")
    SeededRng().shuffled(items, "seed")
    assert items == ("a", "b", "c")


def test_seeded_rng_jitter_is_within_span() -> None:
    rng = SeededRng(jitter_seed=42)
    for _ in range(20):
        value = rng.jitter(10.0)
        assert 0.0 <= value < 10.0


def test_seeded_rng_jitter_is_reproducible_for_a_seed() -> None:
    a = [SeededRng(jitter_seed=7).jitter(5.0) for _ in range(3)]
    b = [SeededRng(jitter_seed=7).jitter(5.0) for _ in range(3)]
    assert a == b  # même jitter_seed → même suite de tirages


def test_seeded_rng_jitter_zero_or_negative_span_is_zero() -> None:
    rng = SeededRng(jitter_seed=1)
    assert rng.jitter(0.0) == 0.0
    assert rng.jitter(-3.0) == 0.0
```

`tests/adapters/test_decision_signal_asyncio.py` :
```python
import asyncio

import pytest

from emule_indexer.adapters.decision_signal_asyncio import AsyncioDecisionSignal


@pytest.mark.asyncio
async def test_signal_wakes_a_waiter() -> None:
    hub = AsyncioDecisionSignal()
    waiter = asyncio.create_task(hub.wait("S2E062A"))
    await asyncio.sleep(0)
    assert not waiter.done()
    hub.signal("S2E062A")
    await asyncio.wait_for(waiter, timeout=1.0)
    assert waiter.done()


@pytest.mark.asyncio
async def test_signal_before_wait_is_not_lost() -> None:
    # Un nudge émis SANS waiter laisse l'événement armé : le wait suivant repart aussitôt.
    hub = AsyncioDecisionSignal()
    hub.signal("S2E062A")
    await asyncio.wait_for(hub.wait("S2E062A"), timeout=1.0)  # ne bloque pas


@pytest.mark.asyncio
async def test_wait_rearms_for_the_next_signal() -> None:
    hub = AsyncioDecisionSignal()
    hub.signal("h")
    await asyncio.wait_for(hub.wait("h"), timeout=1.0)
    # Re-dort : plus de signal en attente → le wait suivant ne se résout pas tout seul.
    second = asyncio.create_task(hub.wait("h"))
    await asyncio.sleep(0)
    assert not second.done()
    hub.signal("h")
    await asyncio.wait_for(second, timeout=1.0)


@pytest.mark.asyncio
async def test_subjects_are_independent() -> None:
    hub = AsyncioDecisionSignal()
    waiter_a = asyncio.create_task(hub.wait("a"))
    await asyncio.sleep(0)
    hub.signal("b")  # autre sujet : ne réveille pas a
    await asyncio.sleep(0)
    assert not waiter_a.done()
    hub.signal("a")
    await asyncio.wait_for(waiter_a, timeout=1.0)
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/adapters/test_clock_asyncio.py tests/adapters/test_decision_signal_asyncio.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: …clock_asyncio`.

- [ ] **Step 3: Écrire les adapters**

`src/emule_indexer/adapters/clock_asyncio.py` :
```python
"""Adapters réels du temps et du hasard (spec orchestration §4).

``AsyncioClock`` : ``now()`` = ``datetime.now(UTC)`` (aware), ``sleep`` = ``asyncio.sleep``
(le vrai sommeil de l'event loop). ``SeededRng`` : mélange déterministe via
``random.Random(seed)`` (un seed → un ordre, vérifié) — c'est l'implémentation du port
``Rng`` consommé par ``domain/search/cycle.py``. Ces deux adapters sont remplacés en test
par des faux avançables/scriptés (déterminisme total, spec §3).
"""

import asyncio
import random
from datetime import UTC, datetime


class AsyncioClock:
    """``Clock`` réel (satisfaction STRUCTURELLE du port)."""

    def now(self) -> datetime:
        """Instant courant, AWARE en UTC (contrat de ``Clock``)."""
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        """Sommeil réel de l'event loop (annulable au point d'``await``, spec §6)."""
        await asyncio.sleep(seconds)


class SeededRng:
    """``Rng`` réel (satisfaction STRUCTURELLE du port).

    ``shuffled`` : permutation déterministe par ``random.Random(seed)`` (une instance neuve
    par appel, seedée par ``seed`` → deux appels de même seed rendent le même ordre).
    ``jitter`` : tirage RÉEL dans ``[0, span)`` via une instance ``random.Random`` propre,
    seedée à la construction (``jitter_seed``, défaut entropie système) — le jitter du
    backoff casse le thundering-herd entre nœuds/canaux."""

    def __init__(self, *, jitter_seed: int | str | None = None) -> None:
        self._jitter = random.Random(jitter_seed)

    def shuffled(self, items: tuple[str, ...], seed: str) -> tuple[str, ...]:
        ordered = list(items)
        random.Random(seed).shuffle(ordered)
        return tuple(ordered)

    def jitter(self, span: float) -> float:
        """Flottant dans ``[0, span)`` (``[0.0]`` si ``span <= 0``)."""
        if span <= 0:
            return 0.0
        return self._jitter.uniform(0.0, span)
```

`src/emule_indexer/adapters/decision_signal_asyncio.py` :
```python
"""Adapter réel du hub de nudge : un ``asyncio.Event`` par sujet (spec orchestration §3/§4).

Implémente STRUCTURELLEMENT le port ``DecisionSignal``. ``signal(subject)`` réveille tout
``wait(subject)`` en cours puis re-arme l'événement (``set`` suivi du ``clear`` par le
waiter réveillé) : un consommateur qui dort sur le sujet repart immédiatement, puis se
rendort sur le prochain nudge. Un ``signal`` SANS waiter est inoffensif (l'événement reste
armé jusqu'au prochain ``wait``, qui le consomme aussitôt) — cohérent avec « un nudge perdu
est inoffensif, le polling de repli est le filet » (spec §3).

Mono-thread/event-loop : tous les accès passent par l'event loop (les repos sont appelés
sync sur cette même boucle, aucune course). Pas de verrou nécessaire.
"""

import asyncio


class AsyncioDecisionSignal:
    """Hub de nudge in-process (un ``asyncio.Event`` par sujet, créé à la demande)."""

    def __init__(self) -> None:
        self._events: dict[str, asyncio.Event] = {}

    def _event(self, subject: str) -> asyncio.Event:
        return self._events.setdefault(subject, asyncio.Event())

    def signal(self, subject: str) -> None:
        """Réveille les ``wait`` du sujet (synchrone : appelé post-commit, ne bloque pas)."""
        self._event(subject).set()

    async def wait(self, subject: str) -> None:
        """Dort jusqu'au prochain ``signal`` du sujet, puis re-arme pour le suivant."""
        event = self._event(subject)
        await event.wait()
        event.clear()
```

- [ ] **Step 4: Vérifier puis gate**

Run: `uv run pytest tests/adapters/test_clock_asyncio.py tests/adapters/test_decision_signal_asyncio.py -q --no-cov` → PASS (6 + 4 tests).
Run: gate complet → tout vert, 100 %.

- [ ] **Step 5: Commit**

```bash
git add src/emule_indexer/adapters/clock_asyncio.py src/emule_indexer/adapters/decision_signal_asyncio.py tests/adapters/test_clock_asyncio.py tests/adapters/test_decision_signal_asyncio.py
git commit -m "feat(adapters): AsyncioClock/SeededRng + AsyncioDecisionSignal (hub de nudge)"
```

---

## Task 11: Adapters config — `parse_crawler_config` / `parse_local_config`

**Files:**
- Create: `src/emule_indexer/adapters/config/crawler_config.py`
- Create: `src/emule_indexer/adapters/config/local_config.py`
- Create: `tests/adapters/config/test_crawler_config.py`
- Create: `tests/adapters/config/test_local_config.py`

> Spec §5 : `crawler.yaml` (politique) + `local.yaml` (machine+secret). Dataclasses GELÉES, validation FAIL-FAST (`ConfigError` → refus de démarrer, spec §14). `local_config` réutilise `ConfigError` de `crawler_config`. Aucune variable d'environnement (spec §3).

- [ ] **Step 1: Écrire les tests qui échouent**

`tests/adapters/config/test_crawler_config.py` :
```python
from typing import Any

import pytest

from emule_indexer.adapters.config.crawler_config import (
    BackoffConfig,
    ConfigError,
    CrawlerConfig,
    parse_crawler_config,
)


def _valid_raw() -> dict[str, Any]:
    return {
        "cycle_interval_seconds": 300.0,
        "search_poll_budget_seconds": 30.0,
        "search_poll_interval_seconds": 5.0,
        "keyword_pause_min_seconds": 1.0,
        "keyword_pause_max_seconds": 4.0,
        "backoff": {
            "base_seconds": 2.0,
            "cap_seconds": 300.0,
            "factor": 2.0,
            "jitter_ratio": 0.3,
        },
        "decision_poll_interval_seconds": 5.0,
        "shutdown_deadline_seconds": 10.0,
    }


def test_parses_a_valid_config() -> None:
    config = parse_crawler_config(_valid_raw())
    assert config == CrawlerConfig(
        cycle_interval_seconds=300.0,
        search_poll_budget_seconds=30.0,
        search_poll_interval_seconds=5.0,
        keyword_pause_min_seconds=1.0,
        keyword_pause_max_seconds=4.0,
        backoff=BackoffConfig(base_seconds=2.0, cap_seconds=300.0, factor=2.0, jitter_ratio=0.3),
        decision_poll_interval_seconds=5.0,
        shutdown_deadline_seconds=10.0,
    )


def test_jitter_ratio_zero_is_accepted() -> None:
    raw = _valid_raw()
    raw["backoff"]["jitter_ratio"] = 0.0  # 0 = aucun jitter (≥ 0 autorisé)
    assert parse_crawler_config(raw).backoff.jitter_ratio == 0.0


def test_negative_jitter_ratio_is_fatal() -> None:
    raw = _valid_raw()
    raw["backoff"]["jitter_ratio"] = -0.1
    with pytest.raises(ConfigError, match="≥ 0 attendu"):
        parse_crawler_config(raw)


def test_missing_key_is_fatal() -> None:
    raw = _valid_raw()
    del raw["cycle_interval_seconds"]
    with pytest.raises(ConfigError, match="cycle_interval_seconds"):
        parse_crawler_config(raw)


def test_non_numeric_value_is_fatal() -> None:
    raw = _valid_raw()
    raw["cycle_interval_seconds"] = "souvent"
    with pytest.raises(ConfigError, match="nombre attendu"):
        parse_crawler_config(raw)


def test_bool_is_not_accepted_as_a_number() -> None:
    raw = _valid_raw()
    raw["cycle_interval_seconds"] = True
    with pytest.raises(ConfigError, match="nombre attendu"):
        parse_crawler_config(raw)


def test_non_positive_value_is_fatal() -> None:
    raw = _valid_raw()
    raw["search_poll_budget_seconds"] = 0
    with pytest.raises(ConfigError, match="strictement positif"):
        parse_crawler_config(raw)


def test_backoff_section_must_be_a_mapping() -> None:
    raw = _valid_raw()
    raw["backoff"] = [1, 2, 3]
    with pytest.raises(ConfigError, match="section 'backoff'"):
        parse_crawler_config(raw)


def test_backoff_factor_below_one_is_fatal() -> None:
    raw = _valid_raw()
    raw["backoff"]["factor"] = 0.5
    with pytest.raises(ConfigError, match="factor doit être ≥ 1"):
        parse_crawler_config(raw)


def test_backoff_cap_below_base_is_fatal() -> None:
    raw = _valid_raw()
    raw["backoff"]["cap_seconds"] = 1.0
    raw["backoff"]["base_seconds"] = 10.0
    with pytest.raises(ConfigError, match="plafond sous le plancher"):
        parse_crawler_config(raw)


def test_keyword_pause_max_below_min_is_fatal() -> None:
    raw = _valid_raw()
    raw["keyword_pause_min_seconds"] = 5.0
    raw["keyword_pause_max_seconds"] = 1.0
    with pytest.raises(ConfigError, match="intervalle vide"):
        parse_crawler_config(raw)
```

`tests/adapters/config/test_local_config.py` :
```python
from typing import Any

import pytest

from emule_indexer.adapters.config.crawler_config import ConfigError
from emule_indexer.adapters.config.local_config import (
    AmuleEndpoint,
    LocalConfig,
    parse_local_config,
)


def _valid_raw() -> dict[str, Any]:
    return {
        "amules": [
            {"name": "amule-1", "host": "gluetun", "port": 4712, "password": "secret"},
        ],
        "catalog_db_path": "/data/catalog.db",
        "local_db_path": "/data/local.db",
    }


def test_parses_a_valid_config_without_node_id() -> None:
    config = parse_local_config(_valid_raw())
    assert config == LocalConfig(
        amules=(AmuleEndpoint(name="amule-1", host="gluetun", port=4712, password="secret"),),
        catalog_db_path="/data/catalog.db",
        local_db_path="/data/local.db",
        node_id=None,
    )


def test_node_id_override_is_kept() -> None:
    raw = _valid_raw()
    raw["node_id"] = "fixed-node"
    assert parse_local_config(raw).node_id == "fixed-node"


def test_multiple_instances_are_parsed_in_order() -> None:
    raw = _valid_raw()
    raw["amules"].append({"name": "amule-2", "host": "h2", "port": 4713, "password": "p2"})
    config = parse_local_config(raw)
    assert [a.name for a in config.amules] == ["amule-1", "amule-2"]


def test_empty_amules_is_fatal() -> None:
    raw = _valid_raw()
    raw["amules"] = []
    with pytest.raises(ConfigError, match="≥ 1 instance"):
        parse_local_config(raw)


def test_amules_not_a_list_is_fatal() -> None:
    raw = _valid_raw()
    raw["amules"] = {"name": "x"}
    with pytest.raises(ConfigError, match="liste NON VIDE"):
        parse_local_config(raw)


def test_instance_must_be_a_mapping() -> None:
    raw = _valid_raw()
    raw["amules"] = ["pas-un-mapping"]
    with pytest.raises(ConfigError, match="mapping attendu"):
        parse_local_config(raw)


def test_duplicate_instance_name_is_fatal() -> None:
    raw = _valid_raw()
    raw["amules"].append({"name": "amule-1", "host": "h2", "port": 4713, "password": "p2"})
    with pytest.raises(ConfigError, match="nom d'instance en double"):
        parse_local_config(raw)


def test_missing_string_field_is_fatal() -> None:
    raw = _valid_raw()
    del raw["amules"][0]["host"]
    with pytest.raises(ConfigError, match="'host' manquante"):
        parse_local_config(raw)


def test_empty_string_field_is_fatal() -> None:
    raw = _valid_raw()
    raw["amules"][0]["password"] = ""
    with pytest.raises(ConfigError, match="chaîne non vide"):
        parse_local_config(raw)


def test_missing_port_is_fatal() -> None:
    raw = _valid_raw()
    del raw["amules"][0]["port"]
    with pytest.raises(ConfigError, match="'port' manquante"):
        parse_local_config(raw)


def test_out_of_range_port_is_fatal() -> None:
    raw = _valid_raw()
    raw["amules"][0]["port"] = 70000
    with pytest.raises(ConfigError, match="1..65535"):
        parse_local_config(raw)


def test_bool_port_is_rejected() -> None:
    raw = _valid_raw()
    raw["amules"][0]["port"] = True
    with pytest.raises(ConfigError, match="1..65535"):
        parse_local_config(raw)


def test_missing_db_path_is_fatal() -> None:
    raw = _valid_raw()
    del raw["catalog_db_path"]
    with pytest.raises(ConfigError, match="catalog_db_path"):
        parse_local_config(raw)


def test_empty_node_id_string_is_fatal() -> None:
    raw = _valid_raw()
    raw["node_id"] = ""
    with pytest.raises(ConfigError, match="node_id"):
        parse_local_config(raw)
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/adapters/config/test_crawler_config.py tests/adapters/config/test_local_config.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: …crawler_config`.

- [ ] **Step 3: Écrire `crawler_config.py`**

`src/emule_indexer/adapters/config/crawler_config.py` :
```python
"""Config de POLITIQUE du crawler (``crawler.yaml``, versionné — spec orchestration §5).

Cadences, budgets de polling, jitter, backoff, filet du nudge, délai d'arrêt. Parsé depuis
le dict YAML déjà chargé par ``load_yaml`` (l'I/O est dans ``yaml_loader``) en une
dataclass GELÉE, avec validation FAIL-FAST (bornes cohérentes → ``ConfigError``, refus de
démarrer, spec §5/§14). Aucune variable d'environnement (spec §3).
"""

from dataclasses import dataclass
from typing import Any


class ConfigError(Exception):
    """Config invalide → refus de démarrer (fail-fast, spec §5/§14)."""


@dataclass(frozen=True)
class BackoffConfig:
    """Backoff exponentiel + jitter par (instance, canal) (spec §3/§5).

    ``jitter_ratio`` : fraction du délai nominal tirée en jitter additionnel
    (anti-thundering-herd) — 0 = aucun jitter, 0.3 = jusqu'à +30 %.
    """

    base_seconds: float
    cap_seconds: float
    factor: float
    jitter_ratio: float


@dataclass(frozen=True)
class CrawlerConfig:
    """Politique du crawler (spec §5). Toutes les durées en SECONDES.

    ``cycle_interval_seconds`` : cadence visée d'un cycle complet. ``search_poll_budget_seconds``
    : temps max d'attente des résultats d'une recherche avant ``fetch``+passage au suivant.
    ``search_poll_interval_seconds`` : pas de polling de la progression. ``keyword_pause`` :
    bornes (min/max) du jitter inter-mots-clés. ``decision_poll_interval_seconds`` : filet
    du nudge (un consommateur futur re-vérifie la table). ``shutdown_deadline_seconds`` :
    borne dure de l'arrêt propre (dépassée → on force, spec §6).
    """

    cycle_interval_seconds: float
    search_poll_budget_seconds: float
    search_poll_interval_seconds: float
    keyword_pause_min_seconds: float
    keyword_pause_max_seconds: float
    backoff: BackoffConfig
    decision_poll_interval_seconds: float
    shutdown_deadline_seconds: float


def _require_mapping(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{what} : mapping attendu, obtenu {type(value).__name__}")
    return value


def _number(mapping: dict[str, Any], key: str, what: str) -> float:
    if key not in mapping:
        raise ConfigError(f"{what} : clé {key!r} manquante")
    value = mapping[key]
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ConfigError(f"{what}.{key} : nombre attendu, obtenu {value!r}")
    return float(value)


def _positive(mapping: dict[str, Any], key: str, what: str) -> float:
    number = _number(mapping, key, what)
    if number <= 0:
        raise ConfigError(f"{what}.{key} : strictement positif attendu, obtenu {number}")
    return number


def _non_negative(mapping: dict[str, Any], key: str, what: str) -> float:
    number = _number(mapping, key, what)
    if number < 0:
        raise ConfigError(f"{what}.{key} : ≥ 0 attendu, obtenu {number}")
    return number


def parse_crawler_config(raw: dict[str, Any]) -> CrawlerConfig:
    """Construit un ``CrawlerConfig`` validé depuis le dict YAML parsé (fail-fast §5/§14)."""
    backoff_raw = _require_mapping(raw.get("backoff", {}), "section 'backoff'")
    factor = _positive(backoff_raw, "factor", "backoff")
    if factor < 1:
        raise ConfigError(f"backoff.factor doit être ≥ 1 (croissance), obtenu {factor}")
    backoff = BackoffConfig(
        base_seconds=_positive(backoff_raw, "base_seconds", "backoff"),
        cap_seconds=_positive(backoff_raw, "cap_seconds", "backoff"),
        factor=factor,
        jitter_ratio=_non_negative(backoff_raw, "jitter_ratio", "backoff"),
    )
    if backoff.cap_seconds < backoff.base_seconds:
        raise ConfigError(
            f"backoff.cap_seconds ({backoff.cap_seconds}) < base_seconds "
            f"({backoff.base_seconds}) : plafond sous le plancher"
        )
    pause_min = _positive(raw, "keyword_pause_min_seconds", "crawler")
    pause_max = _positive(raw, "keyword_pause_max_seconds", "crawler")
    if pause_max < pause_min:
        raise ConfigError(
            f"keyword_pause_max_seconds ({pause_max}) < min ({pause_min}) : intervalle vide"
        )
    return CrawlerConfig(
        cycle_interval_seconds=_positive(raw, "cycle_interval_seconds", "crawler"),
        search_poll_budget_seconds=_positive(raw, "search_poll_budget_seconds", "crawler"),
        search_poll_interval_seconds=_positive(raw, "search_poll_interval_seconds", "crawler"),
        keyword_pause_min_seconds=pause_min,
        keyword_pause_max_seconds=pause_max,
        backoff=backoff,
        decision_poll_interval_seconds=_positive(raw, "decision_poll_interval_seconds", "crawler"),
        shutdown_deadline_seconds=_positive(raw, "shutdown_deadline_seconds", "crawler"),
    )
```

- [ ] **Step 4: Écrire `local_config.py`**

`src/emule_indexer/adapters/config/local_config.py` :
```python
"""Config LOCALE (machine + secret) du crawler (``local.yaml``, gitignoré — spec §5).

Endpoints EC + mots de passe + chemins des bases + override optionnel du ``node_id``.
Parsé depuis le dict YAML chargé par ``load_yaml`` en dataclasses GELÉES, validation
FAIL-FAST (≥ 1 instance, champs présents → ``ConfigError`` sinon, spec §5/§14). Aucune
variable d'environnement (spec §3). ``local.example.yaml`` est versionné comme modèle ;
``local.yaml`` ne l'est jamais (``.gitignore``).
"""

from dataclasses import dataclass
from typing import Any

from emule_indexer.adapters.config.crawler_config import ConfigError


@dataclass(frozen=True)
class AmuleEndpoint:
    """Un daemon ``amuled`` joignable par EC (spec §5). ``name`` est l'étiquette d'instance
    (logging, clé de backoff/scheduler_state) ; UNIQUE par config."""

    name: str
    host: str
    port: int
    password: str


@dataclass(frozen=True)
class LocalConfig:
    """Config machine-spécifique (spec §5). ``node_id`` ``None`` = celui de ``local.db``."""

    amules: tuple[AmuleEndpoint, ...]
    catalog_db_path: str
    local_db_path: str
    node_id: str | None


def _require_mapping(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{what} : mapping attendu, obtenu {type(value).__name__}")
    return value


def _require_str(mapping: dict[str, Any], key: str, what: str) -> str:
    if key not in mapping:
        raise ConfigError(f"{what} : clé {key!r} manquante")
    value = mapping[key]
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{what}.{key} : chaîne non vide attendue, obtenu {value!r}")
    return value


def _require_port(mapping: dict[str, Any], what: str) -> int:
    if "port" not in mapping:
        raise ConfigError(f"{what} : clé 'port' manquante")
    value = mapping["port"]
    if not isinstance(value, int) or isinstance(value, bool) or not (0 < value < 65536):
        raise ConfigError(f"{what}.port : entier 1..65535 attendu, obtenu {value!r}")
    return value


def parse_local_config(raw: dict[str, Any]) -> LocalConfig:
    """Construit un ``LocalConfig`` validé depuis le dict YAML parsé (fail-fast §5/§14)."""
    amules_raw = raw.get("amules")
    if not isinstance(amules_raw, list) or not amules_raw:
        raise ConfigError("section 'amules' : liste NON VIDE attendue (≥ 1 instance, spec §5)")
    endpoints: list[AmuleEndpoint] = []
    seen_names: set[str] = set()
    for index, entry in enumerate(amules_raw):
        what = f"amules[{index}]"
        mapping = _require_mapping(entry, what)
        name = _require_str(mapping, "name", what)
        if name in seen_names:
            raise ConfigError(f"nom d'instance en double : {name!r} (doit être unique, spec §5)")
        seen_names.add(name)
        endpoints.append(
            AmuleEndpoint(
                name=name,
                host=_require_str(mapping, "host", what),
                port=_require_port(mapping, what),
                password=_require_str(mapping, "password", what),
            )
        )
    node_id_raw = raw.get("node_id")
    if node_id_raw is not None and (not isinstance(node_id_raw, str) or not node_id_raw):
        raise ConfigError(f"node_id : chaîne non vide ou absent attendu, obtenu {node_id_raw!r}")
    return LocalConfig(
        amules=tuple(endpoints),
        catalog_db_path=_require_str(raw, "catalog_db_path", "local"),
        local_db_path=_require_str(raw, "local_db_path", "local"),
        node_id=node_id_raw,
    )
```

- [ ] **Step 5: Vérifier puis gate**

Run: `uv run pytest tests/adapters/config -q --no-cov` → PASS (les nouveaux + `test_yaml_loader` existant).
Run: gate complet → tout vert, 100 %.

- [ ] **Step 6: Commit**

```bash
git add src/emule_indexer/adapters/config/crawler_config.py src/emule_indexer/adapters/config/local_config.py tests/adapters/config/test_crawler_config.py tests/adapters/config/test_local_config.py
git commit -m "feat(adapters): parse_crawler_config / parse_local_config (gelés, fail-fast)"
```

---

## Task 12: Application — `record_observations` (+ fakes & conftest partagés)

**Files:**
- Create: `src/emule_indexer/application/__init__.py` (vide)
- Create: `src/emule_indexer/application/record_observations.py`
- Create: `tests/application/__init__.py` (vide)
- Create: `tests/application/fakes.py` (helpers PARTAGÉS pour les Tasks 12-14)
- Create: `tests/application/conftest.py` (fixtures `engine`/`catalog` PARTAGÉES)
- Create: `tests/application/test_record_observations.py`

> Pipeline par obs (spec §4) : `record_observation` TOUJOURS → `evaluate` → si verdict CHANGÉ (`last_decision != to_record(decision)`) → `record_decision` + `signal`. `RepositoryError` ABSORBÉE (log + `False`, le cycle continue, spec §7). `fakes.py`/`conftest.py` servent aussi aux Tasks 13-14. DÉCISION 9 : un test `await` le nudge (le test EST le consommateur).

- [ ] **Step 1: Créer les `__init__.py` vides** (`src/emule_indexer/application/__init__.py`, `tests/application/__init__.py`).

- [ ] **Step 2: Écrire les fakes & le conftest PARTAGÉS**

`tests/application/fakes.py` :
```python
"""Faux objets déterministes pour les tests de la couche application (spec §8).

``FakeMuleClient`` : résultats SCRIPTÉS par appel de ``fetch_results``, pannes injectables
(``MuleUnreachableError``/``MuleSearchFailedError``) à ``connect``/``start_search``.
``FakeClock`` : horloge avançable (``advance`` sans I/O) + ``sleep`` qui avance SANS attente
réelle (déterminisme). ``FakeRng`` : shuffle identité + jitter FIXE (déterminisme).
``RecordingSignal`` : capture les sujets nudgés. Les repos sont les VRAIS repos SQLite
(spec §8 : « vrais repos sur tmp_path ») — pas de faux ici.
"""

import asyncio
from datetime import UTC, datetime, timedelta

from emule_indexer.domain.observation import FileObservation
from emule_indexer.ports.mule_client import (
    KadStatus,
    MuleSearchFailedError,
    MuleUnreachableError,
    NetworkStatus,
    SearchChannel,
)


class FakeClock:
    """Horloge fausse avançable + sleep instantané (avance le now, déterministe)."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 6, 12, tzinfo=UTC)
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        """Avance l'horloge SANS dormir (pour faire passer un ``retry_after`` en test)."""
        self._now += timedelta(seconds=seconds)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._now += timedelta(seconds=seconds)
        await asyncio.sleep(0)  # cède la main sans attente réelle


class FakeRng:
    """Rng faux DÉTERMINISTE : shuffle identité + jitter constant (``jitter_value``).

    Le shuffle conserve l'ordre (pas de dépendance au seed dans les tests). ``jitter`` rend
    toujours ``jitter_value`` (0.0 par défaut → backoff = délai NOMINAL exact, assertions
    exactes possibles)."""

    def __init__(self, *, jitter_value: float = 0.0) -> None:
        self._jitter_value = jitter_value
        self.jitter_spans: list[float] = []

    def shuffled(self, items: tuple[str, ...], seed: str) -> tuple[str, ...]:
        return items

    def jitter(self, span: float) -> float:
        self.jitter_spans.append(span)
        return self._jitter_value


class RecordingSignal:
    """Hub de nudge qui ENREGISTRE les sujets signalés (le test inspecte/await)."""

    def __init__(self) -> None:
        self.signalled: list[str] = []
        self._events: dict[str, asyncio.Event] = {}

    def signal(self, subject: str) -> None:
        self.signalled.append(subject)
        self._events.setdefault(subject, asyncio.Event()).set()

    async def wait(self, subject: str) -> None:
        event = self._events.setdefault(subject, asyncio.Event())
        await event.wait()
        event.clear()


class FakeMuleClient:
    """Client EC scripté (satisfait MuleClient structurellement, spec §8).

    ``results`` : liste de tuples d'observations, un par appel de ``fetch_results``
    (épuisée → tuple vide). ``connect_failures`` : exceptions à lever aux N premiers
    ``connect`` (puis succès). ``search_failures`` : exceptions à lever aux N premiers
    ``start_search`` (puis succès). ``status`` : le ``NetworkStatus`` renvoyé.
    """

    def __init__(
        self,
        *,
        results: list[tuple[FileObservation, ...]] | None = None,
        connect_failures: list[Exception] | None = None,
        search_failures: list[Exception] | None = None,
        status: NetworkStatus | None = None,
    ) -> None:
        self._results = list(results or [])
        self._connect_failures = list(connect_failures or [])
        self._search_failures = list(search_failures or [])
        self._status = status or NetworkStatus(
            ed2k_id=1, ed2k_high=True, kad_status=KadStatus.CONNECTED
        )
        self.connect_calls = 0
        self.close_calls = 0
        self.searches: list[tuple[str, SearchChannel]] = []
        self.fetch_calls = 0

    async def connect(self) -> None:
        self.connect_calls += 1
        if self._connect_failures:
            raise self._connect_failures.pop(0)

    async def close(self) -> None:
        self.close_calls += 1

    async def start_search(self, keyword: str, channel: SearchChannel) -> None:
        self.searches.append((keyword, channel))
        if self._search_failures:
            raise self._search_failures.pop(0)

    async def fetch_results(self) -> tuple[FileObservation, ...]:
        self.fetch_calls += 1
        if not self._results:
            return ()
        return self._results.pop(0)

    async def stop_search(self) -> None:
        return None

    async def search_progress(self) -> int | None:
        return 100  # « terminé » : le polling s'arrête tout de suite (déterminisme)

    async def network_status(self) -> NetworkStatus:
        return self._status


def make_unreachable(message: str = "down") -> MuleUnreachableError:
    return MuleUnreachableError(message)


def make_search_failed(message: str = "EC_OP_FAILED") -> MuleSearchFailedError:
    return MuleSearchFailedError(message)
```

`tests/application/conftest.py` :
```python
"""Fixtures partagées des tests application : moteur réel + repos SQLite réels (spec §8)."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from emule_indexer.adapters.config.yaml_loader import load_yaml
from emule_indexer.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from emule_indexer.adapters.persistence_sqlite.connection import open_catalog
from emule_indexer.domain.matching.engine import MatchingEngine
from emule_indexer.domain.matching.validation import parse_matcher_config, parse_targets

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_NODE = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def engine() -> MatchingEngine:
    """Moteur RÉEL sur la config/targets canoniques (corpus golden, fixtures partagées)."""
    config = parse_matcher_config(load_yaml(_FIXTURES / "canonical_config.yaml"))
    targets = parse_targets(load_yaml(_FIXTURES / "canonical_targets.yaml"))
    return MatchingEngine(config, targets)


@pytest.fixture
def catalog_connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_catalog(tmp_path / "catalog.db")
    yield connection
    connection.close()


@pytest.fixture
def catalog(catalog_connection: sqlite3.Connection) -> SqliteCatalogRepository:
    return SqliteCatalogRepository(catalog_connection, _NODE)
```

- [ ] **Step 3: Écrire le test qui échoue**

`tests/application/test_record_observations.py` :
```python
import asyncio
import sqlite3

import pytest

from emule_indexer.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from emule_indexer.application.record_observations import record_observation
from emule_indexer.domain.matching.engine import MatchingEngine
from emule_indexer.domain.observation import FileObservation
from tests.application.fakes import RecordingSignal

_HASH_DL = "31d6cfe0d16ae931b73c59d7e0c089c0"
_HASH_CAT = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_HASH_DISCARD = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
_DL_NAME = "Keroro N°062A Les demoiselles cambrioleuses.avi"


def _obs(ed2k_hash: str, filename: str) -> FileObservation:
    return FileObservation(
        ed2k_hash=ed2k_hash,
        filename=filename,
        size_bytes=234_000_000,
        source_count=3,
        complete_source_count=1,
        keyword="keroro",
    )


def test_observation_is_always_recorded_even_when_discarded(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    signal = RecordingSignal()
    changed = record_observation(
        _obs(_HASH_DISCARD, "random.txt"), catalog=catalog, engine=engine, signal=signal
    )
    assert changed is False
    assert catalog_connection.execute("SELECT count(*) FROM file_observations").fetchone()[0] == 1
    assert catalog_connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0] == 0
    assert signal.signalled == []


def test_new_verdict_is_persisted_and_nudged(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    signal = RecordingSignal()
    changed = record_observation(
        _obs(_HASH_DL, _DL_NAME), catalog=catalog, engine=engine, signal=signal
    )
    assert changed is True
    assert catalog_connection.execute("SELECT tier FROM match_decisions").fetchone() == (
        "download",
    )
    assert signal.signalled == [_HASH_DL]


def test_unchanged_verdict_is_not_reappended_or_nudged(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    signal = RecordingSignal()
    observation = _obs(_HASH_CAT, "keroro something.avi")
    assert record_observation(observation, catalog=catalog, engine=engine, signal=signal) is True
    # Deuxième observation du MÊME fichier : même verdict catalog → pas de ré-append.
    assert record_observation(observation, catalog=catalog, engine=engine, signal=signal) is False
    assert catalog_connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0] == 1
    # Mais l'observation, elle, est re-persistée (re-observation périodique = le but).
    assert catalog_connection.execute("SELECT count(*) FROM file_observations").fetchone()[0] == 2
    assert signal.signalled == [_HASH_CAT]  # une seule fois


def test_changed_verdict_is_reappended_and_nudged_again(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    signal = RecordingSignal()
    record_observation(
        _obs(_HASH_DL, "keroro something.avi"), catalog=catalog, engine=engine, signal=signal
    )
    # 2e vue du MÊME hash, nom DOWNLOAD → verdict change → ré-append + nudge.
    changed = record_observation(
        _obs(_HASH_DL, _DL_NAME), catalog=catalog, engine=engine, signal=signal
    )
    assert changed is True
    tiers = [
        row[0]
        for row in catalog_connection.execute(
            "SELECT tier FROM match_decisions ORDER BY id"
        ).fetchall()
    ]
    assert tiers == ["catalog", "download"]
    assert signal.signalled == [_HASH_DL, _HASH_DL]


def test_persistence_error_is_absorbed_and_cycle_continues(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    # Trigger de TEST : fait échouer l'INSERT d'observation → RepositoryError absorbée.
    catalog_connection.execute(
        "CREATE TRIGGER boom BEFORE INSERT ON file_observations"
        " BEGIN SELECT RAISE(ABORT, 'panne injectée'); END"
    )
    signal = RecordingSignal()
    changed = record_observation(
        _obs(_HASH_DL, _DL_NAME), catalog=catalog, engine=engine, signal=signal
    )
    assert changed is False  # absorbée, le cycle continue
    assert signal.signalled == []


@pytest.mark.asyncio
async def test_signal_consumer_awaits_the_nudge(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    # Le hub EST consommé par un await (pas du code mort, DÉCISION 9) : un consommateur dort
    # sur le sujet et est réveillé par le nudge post-commit.
    signal = RecordingSignal()
    waiter = asyncio.create_task(signal.wait(_HASH_DL))
    await asyncio.sleep(0)
    assert not waiter.done()
    record_observation(_obs(_HASH_DL, _DL_NAME), catalog=catalog, engine=engine, signal=signal)
    await asyncio.wait_for(waiter, timeout=1.0)
    assert waiter.done()
```

- [ ] **Step 4: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/application/test_record_observations.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: …record_observations`.

- [ ] **Step 5: Écrire l'implémentation**

`src/emule_indexer/application/record_observations.py` :
```python
"""Pipeline par observation : record → evaluate → (si verdict changé) decide + nudge.

Couche APPLICATION (spec orchestration §4) : orchestre des PORTS (sync repos + moteur pur
+ hub de nudge async), ne fait aucune I/O elle-même. Pour CHAQUE observation (spec §4) :

1. ``record_observation`` TOUJOURS (la re-observation périodique est le but, spec §3/§6).
2. ``evaluate`` via le moteur pur ; ``None`` (fichier écarté) → on s'arrête là.
3. Anti-redondance (spec §3) : on lit le dernier verdict connu (``last_decision``) ; on ne
   ``record_decision`` (et ne ``signal`` le hub) QUE si le verdict CHANGE (nouveau hash, ou
   ``DecisionRecord`` différent). Verdict identique → ni ré-append ni nudge.

Les repos sont SYNCHRONES, appelés DIRECTEMENT (spec §3 : sub-ms, pas de ``to_thread`` en
MVP ; conséquence assumée : les écritures DB sont sérialisées de facto sur l'event loop).
Une ``RepositoryError`` (contrat de PORT, jamais un adapter) sur UNE observation est
LOGGÉE et ABSORBÉE ici : la fonction rend ``False`` et le cycle continue (spec §7) — une
seule obs corrompue/en échec ne fait pas tomber tout le balayage, mais l'échec reste
VISIBLE (log niveau ``error``, pour qu'un échec persistant se remarque).
"""

import logging

from emule_indexer.domain.matching.engine import MatchingEngine, to_record
from emule_indexer.domain.observation import FileObservation
from emule_indexer.ports.catalog_repository import CatalogRepository
from emule_indexer.ports.decision_signal import DecisionSignal
from emule_indexer.ports.repository_errors import RepositoryError

_logger = logging.getLogger("emule_indexer.application.record_observations")


def record_observation(
    observation: FileObservation,
    *,
    catalog: CatalogRepository,
    engine: MatchingEngine,
    signal: DecisionSignal,
) -> bool:
    """Traite UNE observation (spec §4). Rend ``True`` ssi un NOUVEAU verdict a été persisté.

    Le booléen sert au logging/aux compteurs de cycle (combien de verdicts ont changé).
    ``record_observation`` est toujours appelé d'abord (ordre d'écriture catalogue : la
    décision exige que l'observation existe, FK — handoff data-model §4). Une
    ``RepositoryError`` est absorbée (log + ``False``), le cycle continue (spec §7).
    """
    try:
        catalog.record_observation(observation)
        decision = engine.evaluate(observation.to_candidate())
        if decision is None:
            return False
        fresh = to_record(decision)
        if catalog.last_decision(observation.ed2k_hash) == fresh:
            # Verdict INCHANGÉ : ni ré-append (anti-redondance §3) ni nudge.
            return False
        catalog.record_decision(observation.ed2k_hash, decision)
    except RepositoryError as error:
        _logger.error(
            "persistance échouée sur hash=%s (%s) — observation ignorée, cycle continue",
            observation.ed2k_hash,
            error,
        )
        return False
    _logger.info(
        "verdict changé hash=%s target=%s tier=%s règle=%s",
        observation.ed2k_hash,
        decision.target_id,
        decision.tier,
        decision.rule_name,
    )
    signal.signal(observation.ed2k_hash)
    return True
```

- [ ] **Step 6: Vérifier puis gate**

Run: `uv run pytest tests/application/test_record_observations.py -q --no-cov` → PASS (6 tests).
Run: gate complet → tout vert, 100 %.

- [ ] **Step 7: Commit**

```bash
git add src/emule_indexer/application tests/application/__init__.py tests/application/fakes.py tests/application/conftest.py tests/application/test_record_observations.py
git commit -m "feat(application): pipeline record_observations (record→eval→decide→nudge, anti-redondance)"
```

---

## Task 13: Application — `SearchWorker` (+ BackoffRegistry, WorkerPolicy, WorkerDeps, SearchTask)

**Files:**
- Create: `src/emule_indexer/application/search_worker.py`
- Create: `tests/application/test_search_worker.py`

> Un travailleur par instance (spec §3). Par item : **consulte le backoff** (SAUTE l'item si l'instance OU le canal est en backoff jusqu'à `retry_after`) → connexion (reconnexion si down) → `start_search` → polling borné → `fetch_results` → pipeline par obs. `MuleUnreachableError` → instance down + backoff PAR INSTANCE ; `MuleSearchFailedError` → backoff PAR (instance, canal). Le `BackoffRegistry` est **PARTAGÉ** (injecté via `WorkerDeps.backoff`), construit avec `(policy, clock, rng)` ; il calcule `retry_after = clock.now() + backoff_delay + jitter` (jitter via le port `Rng`), expose `is_in_backoff`/`record_failure`/`reset` + `snapshot`/`load_from` pour la persistance. « Skip jusqu'à `retry_after` » remplace l'ancien « sleep du délai » (l'event loop reste libre). `WorkerPolicy` = primitifs (DÉCISION 5), dont `backoff_jitter_ratio`. Catch UNIQUEMENT des exceptions de PORT. Tests : `FakeRng(jitter_value)` déterministe + `FakeClock.advance()` (fait passer `retry_after` sans dormir).

- [ ] **Step 1: Écrire le test qui échoue**

`tests/application/test_search_worker.py` :
```python
import sqlite3

import pytest

from emule_indexer.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from emule_indexer.application.search_worker import (
    BackoffRegistry,
    SearchTask,
    SearchWorker,
    WorkerDeps,
    WorkerPolicy,
)
from emule_indexer.domain.matching.engine import MatchingEngine
from emule_indexer.domain.observation import FileObservation
from emule_indexer.ports.mule_client import SearchChannel
from emule_indexer.ports.scheduler_state_repository import ChannelBackoff
from tests.application.fakes import (
    FakeClock,
    FakeMuleClient,
    FakeRng,
    RecordingSignal,
    make_search_failed,
    make_unreachable,
)

_HASH = "31d6cfe0d16ae931b73c59d7e0c089c0"
_DL_NAME = "Keroro N°062A Les demoiselles cambrioleuses.avi"

# jitter_ratio 0.0 + FakeRng(jitter_value=0.0) → backoff = délai NOMINAL exact (assertions nettes).
_POLICY = WorkerPolicy(
    backoff_base_seconds=2.0,
    backoff_cap_seconds=60.0,
    backoff_factor=2.0,
    backoff_jitter_ratio=0.0,
    poll_budget_seconds=10.0,
    poll_interval_seconds=5.0,
)


def _obs() -> FileObservation:
    return FileObservation(
        ed2k_hash=_HASH,
        filename=_DL_NAME,
        size_bytes=234_000_000,
        source_count=3,
        complete_source_count=1,
        keyword="keroro",
    )


def _registry(clock: FakeClock, rng: FakeRng | None = None) -> BackoffRegistry:
    return BackoffRegistry(_POLICY, clock, rng or FakeRng())


def _deps(
    catalog: SqliteCatalogRepository,
    engine: MatchingEngine,
    clock: FakeClock,
    backoff: BackoffRegistry,
) -> WorkerDeps:
    return WorkerDeps(
        catalog=catalog,
        engine=engine,
        signal=RecordingSignal(),
        clock=clock,
        policy=_POLICY,
        backoff=backoff,
    )


# --- BackoffRegistry (logique, déterministe via clock/rng faux) ---


def test_backoff_registry_grows_then_resets() -> None:
    clock = FakeClock()
    registry = _registry(clock)
    assert registry.record_failure("k") == 2.0  # 1re tentative = base
    assert registry.record_failure("k") == 4.0  # × factor
    registry.reset("k")
    assert registry.record_failure("k") == 2.0  # repart à la base


def test_backoff_registry_keys_are_independent() -> None:
    registry = _registry(FakeClock())
    assert registry.record_failure("a") == 2.0
    assert registry.record_failure("b") == 2.0  # 'b' n'a pas hérité du compteur de 'a'


def test_backoff_registry_reset_unknown_key_is_a_noop() -> None:
    _registry(FakeClock()).reset("jamais-vu")  # ne lève pas


def test_backoff_registry_sets_retry_after_in_the_future() -> None:
    clock = FakeClock()
    registry = _registry(clock)
    registry.record_failure("amule-1:kad")
    assert registry.is_in_backoff("amule-1:kad") is True
    clock.advance(1.9)  # encore avant retry_after (2.0s)
    assert registry.is_in_backoff("amule-1:kad") is True
    clock.advance(0.2)  # désormais après retry_after
    assert registry.is_in_backoff("amule-1:kad") is False


def test_backoff_registry_unknown_key_is_not_in_backoff() -> None:
    assert _registry(FakeClock()).is_in_backoff("inconnu") is False


def test_backoff_registry_jitter_extends_the_delay() -> None:
    clock = FakeClock()
    policy = WorkerPolicy(
        backoff_base_seconds=2.0,
        backoff_cap_seconds=60.0,
        backoff_factor=2.0,
        backoff_jitter_ratio=0.5,  # jitter dans [0, 0.5 * délai)
        poll_budget_seconds=10.0,
        poll_interval_seconds=5.0,
    )
    rng = FakeRng(jitter_value=1.0)  # jitter constant de 1.0s
    registry = BackoffRegistry(policy, clock, rng)
    delay = registry.record_failure("k")
    assert delay == 3.0  # base 2.0 + jitter 1.0
    assert rng.jitter_spans == [1.0]  # span = jitter_ratio (0.5) * délai (2.0)


def test_backoff_registry_snapshot_and_load_round_trip() -> None:
    clock = FakeClock()
    registry = _registry(clock)
    registry.record_failure("amule-1:kad")
    snapshot = registry.snapshot()
    assert "amule-1:kad" in snapshot
    assert isinstance(snapshot["amule-1:kad"], ChannelBackoff)
    # Recharge dans un registre NEUF (simule un redémarrage) → même skip appliqué.
    reborn = _registry(clock)
    assert reborn.is_in_backoff("amule-1:kad") is False  # vide avant load
    reborn.load_from(snapshot)
    assert reborn.is_in_backoff("amule-1:kad") is True


# --- SearchWorker ---


@pytest.mark.asyncio
async def test_successful_task_records_observation(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    clock = FakeClock()
    client = FakeMuleClient(results=[(_obs(),)])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, clock, _registry(clock)))
    await worker.run_task(SearchTask(keyword="keroro", channel=SearchChannel.GLOBAL))
    assert client.searches == [("keroro", SearchChannel.GLOBAL)]
    assert catalog_connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_multiple_observations_some_unchanged_are_all_processed(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    clock = FakeClock()
    discarded = FileObservation(
        ed2k_hash="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        filename="random.txt",  # écarté par le moteur → record_observation rend False
        size_bytes=10,
        source_count=1,
        complete_source_count=0,
        keyword="keroro",
    )
    # Deux observations dans le même relevé : la 1re est écartée (False → loop back), la 2e
    # change un verdict. Couvre l'arête « if False → observation suivante ».
    client = FakeMuleClient(results=[(discarded, _obs())])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, clock, _registry(clock)))
    await worker.run_task(SearchTask(keyword="keroro", channel=SearchChannel.GLOBAL))
    assert catalog_connection.execute("SELECT count(*) FROM file_observations").fetchone()[0] == 2
    assert catalog_connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_connect_failure_arms_instance_backoff_and_skips_the_item(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    clock = FakeClock()
    registry = _registry(clock)
    client = FakeMuleClient(connect_failures=[make_unreachable()])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, clock, registry))
    await worker.run_task(SearchTask(keyword="keroro", channel=SearchChannel.GLOBAL))
    assert client.searches == []  # item abandonné, jamais recherché
    assert registry.is_in_backoff("amule-1") is True  # instance en backoff (skip jusqu'à retry)
    assert clock.sleeps == []  # plus de sleep du backoff : on SKIP, on n'attend pas


@pytest.mark.asyncio
async def test_instance_in_backoff_skips_without_connecting(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    clock = FakeClock()
    registry = _registry(clock)
    registry.record_failure("amule-1")  # instance déjà en backoff
    client = FakeMuleClient(results=[(_obs(),)])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, clock, registry))
    await worker.run_task(SearchTask(keyword="keroro", channel=SearchChannel.GLOBAL))
    assert client.connect_calls == 0  # ni connexion ni recherche : sauté
    assert client.searches == []


@pytest.mark.asyncio
async def test_channel_in_backoff_skips_that_item(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    clock = FakeClock()
    registry = _registry(clock)
    registry.record_failure("amule-1:kad")  # canal kad en backoff
    client = FakeMuleClient(results=[(_obs(),), (_obs(),)])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, clock, registry))
    await worker.run_task(SearchTask(keyword="k", channel=SearchChannel.KAD))  # sauté
    assert client.searches == []
    await worker.run_task(SearchTask(keyword="k", channel=SearchChannel.GLOBAL))  # autre canal OK
    assert client.searches == [("k", SearchChannel.GLOBAL)]


@pytest.mark.asyncio
async def test_backoff_expires_and_item_runs_again(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    clock = FakeClock()
    registry = _registry(clock)
    registry.record_failure("amule-1:kad")  # retry_after = +2.0s
    client = FakeMuleClient(results=[(_obs(),)])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, clock, registry))
    clock.advance(3.0)  # retry_after dépassé → le canal n'est plus en backoff
    await worker.run_task(SearchTask(keyword="k", channel=SearchChannel.KAD))
    assert client.searches == [("k", SearchChannel.KAD)]


@pytest.mark.asyncio
async def test_already_connected_does_not_reconnect(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    clock = FakeClock()
    client = FakeMuleClient(results=[(), ()])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, clock, _registry(clock)))
    await worker.run_task(SearchTask(keyword="k1", channel=SearchChannel.GLOBAL))
    await worker.run_task(SearchTask(keyword="k2", channel=SearchChannel.GLOBAL))
    assert client.connect_calls == 1  # connecté une seule fois pour deux tâches


@pytest.mark.asyncio
async def test_search_failure_arms_channel_backoff(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    clock = FakeClock()
    registry = _registry(clock)
    client = FakeMuleClient(search_failures=[make_search_failed()])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, clock, registry))
    await worker.run_task(SearchTask(keyword="keroro", channel=SearchChannel.GLOBAL))
    assert registry.is_in_backoff("amule-1:global") is True  # canal en backoff
    assert registry.is_in_backoff("amule-1") is False  # mais pas l'instance entière
    assert client.fetch_calls == 0  # pas de fetch après l'échec de start_search


@pytest.mark.asyncio
async def test_transport_failure_marks_instance_down(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    clock = FakeClock()
    registry = _registry(clock)
    # start_search lève une panne de transport (flux mort) → instance down + backoff instance.
    client = FakeMuleClient(search_failures=[make_unreachable()], results=[(_obs(),)])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, clock, registry))
    await worker.run_task(SearchTask(keyword="k1", channel=SearchChannel.GLOBAL))
    assert registry.is_in_backoff("amule-1") is True
    # Après expiration du backoff, la tâche suivante FORCE une reconnexion (down marqué).
    clock.advance(3.0)
    await worker.run_task(SearchTask(keyword="k2", channel=SearchChannel.GLOBAL))
    assert client.connect_calls == 2


@pytest.mark.asyncio
async def test_poll_budget_is_respected_when_progress_never_completes(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    clock = FakeClock()

    class _NeverDone(FakeMuleClient):
        async def search_progress(self) -> int | None:
            return 10  # jamais 100 % → on poll jusqu'au budget

    client = _NeverDone(results=[()])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, clock, _registry(clock)))
    await worker.run_task(SearchTask(keyword="keroro", channel=SearchChannel.GLOBAL))
    # budget 10 / pas 5 → deux pas de polling, puis fetch.
    assert clock.sleeps == [5.0, 5.0]
    assert client.fetch_calls == 1


@pytest.mark.asyncio
async def test_poll_loops_once_then_completes(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    clock = FakeClock()

    class _ThenDone(FakeMuleClient):
        def __init__(self, **kwargs: object) -> None:
            super().__init__(**kwargs)  # type: ignore[arg-type]
            self._calls = 0

        async def search_progress(self) -> int | None:
            self._calls += 1
            return 100 if self._calls >= 2 else 10  # 1er relevé : pas fini ; 2e : fini

    client = _ThenDone(results=[()])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, clock, _registry(clock)))
    await worker.run_task(SearchTask(keyword="keroro", channel=SearchChannel.GLOBAL))
    assert clock.sleeps == [5.0]  # un pas de polling, puis break au 2e relevé


@pytest.mark.asyncio
async def test_poll_stops_when_progress_is_none_but_budget_bounds_it(
    catalog: SqliteCatalogRepository, engine: MatchingEngine
) -> None:
    clock = FakeClock()

    class _NoProgress(FakeMuleClient):
        async def search_progress(self) -> int | None:
            return None  # EC n'expose pas la progression → on poll jusqu'au budget

    client = _NoProgress(results=[()])
    worker = SearchWorker("amule-1", client, _deps(catalog, engine, clock, _registry(clock)))
    await worker.run_task(SearchTask(keyword="keroro", channel=SearchChannel.GLOBAL))
    assert clock.sleeps == [5.0, 5.0]
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/application/test_search_worker.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: …search_worker`.

- [ ] **Step 3: Écrire l'implémentation**

`src/emule_indexer/application/search_worker.py` :
```python
"""Travailleur de recherche : possède 1 ``MuleClient``, draine la queue (spec §4).

Couche APPLICATION. Un travailleur par instance ``amuled`` (spec §3 : N travailleurs = N
connexions EC = parallélisme réel ; dégénère en boucle séquentielle à N=1). Par item
``(keyword, channel)`` tiré de la queue partagée :

  consulte le backoff (SAUTE l'item si l'instance OU le canal est en backoff jusqu'à son
  ``retry_after``) → assure la connexion (reconnexion par instance si down) →
  ``start_search`` → polling borné (budget config) → ``fetch_results`` →
  ``record_observation`` pour CHAQUE obs.

Gestion d'erreurs (spec §7, « le client signale, le plan C décide ») — l'application ne
catch QUE des exceptions de PORT (jamais d'un adapter, règle de dépendance §4) :
- ``MuleUnreachableError`` (flux mort : connexion/timeout/trame illisible côté EC) →
  instance DOWN : on jette le client, BACKOFF de reconnexion PAR INSTANCE (``retry_after``
  posé) ; les autres travailleurs continuent ; l'item est ABANDONNÉ.
- ``MuleSearchFailedError`` (échec applicatif d'un canal) → BACKOFF PAR (instance, canal).
- ``RepositoryError`` sur une obs → loggée et comptée par ``record_observations``.

Le backoff est exponentiel + jitter (spec §3), MÉMORISÉ dans un ``BackoffRegistry`` PARTAGÉ
(une seule instance pour TOUS les travailleurs + le cycle) et PERSISTÉ dans
``scheduler_state`` en fin de cycle (spec §3/§7 : il survit à un redémarrage). « Skip jusqu'à
``retry_after`` » remplace l'ancien « sleep du délai » : un canal en backoff est sauté, pas
attendu — l'event loop reste disponible. Les mutations du registre partagé se font entre
deux ``await`` (event loop mono-thread, writer unique) → aucun verrou nécessaire (spec §3).
Le travailleur ne FERME jamais le client (ownership = composition root, §6).
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from emule_indexer.application.record_observations import record_observation
from emule_indexer.domain.matching.engine import MatchingEngine
from emule_indexer.domain.search.backoff import backoff_delay
from emule_indexer.ports.catalog_repository import CatalogRepository
from emule_indexer.ports.clock import Clock, Rng
from emule_indexer.ports.decision_signal import DecisionSignal
from emule_indexer.ports.mule_client import (
    MuleClient,
    MuleSearchFailedError,
    MuleUnreachableError,
    SearchChannel,
)
from emule_indexer.ports.scheduler_state_repository import ChannelBackoff

_logger = logging.getLogger("emule_indexer.application.search_worker")

_PROGRESS_DONE = 100  # search_progress() à 100 % → on cesse de poller (handoff EC)


def _iso(moment: datetime) -> str:
    """ISO-8601 UTC à largeur fixe (microsecondes TOUJOURS écrites) — mêmes règles que
    ``utc_iso`` de l'adapter (qu'on ne peut pas importer : règle de dépendance §4) pour que
    la comparaison ``now < retry_after`` soit lexicographique == chronologique, et que le
    format PERSISTÉ soit identique à celui des autres timestamps."""
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class SearchTask:
    """Une unité de travail : un mot-clé sur un canal (spec §4)."""

    keyword: str
    channel: SearchChannel


@dataclass(frozen=True)
class WorkerPolicy:
    """Paramètres de politique d'un travailleur, en PRIMITIFS (spec §5 ; injectés par compo).

    ``backoff_jitter_ratio`` : fraction du délai nominal tirée en jitter additionnel
    (anti-thundering-herd, spec §3) — p.ex. 0.3 ⇒ jitter dans ``[0, 0.3 * délai)``.
    """

    backoff_base_seconds: float
    backoff_cap_seconds: float
    backoff_factor: float
    backoff_jitter_ratio: float
    poll_budget_seconds: float
    poll_interval_seconds: float


class BackoffRegistry:
    """Registre de backoff PARTAGÉ par clé (instance, ou « instance:canal »), PERSISTABLE.

    Tient une map ``clé → ChannelBackoff(attempts, retry_after)`` (spec §3/§7). ``retry_after``
    est calculé à l'échec : ``clock.now() + backoff_delay(attempts) + jitter`` (jitter tiré du
    port ``Rng``, déterministe en test) → ISO-8601 UTC à largeur fixe (comparaison
    lexicographique == chronologique). ``is_in_backoff`` saute une clé tant que
    ``now < retry_after``. ``snapshot``/``load_from`` font le pont avec ``scheduler_state``
    (la persistance survit à un redémarrage). Logique déterministe (clock/rng injectés).
    """

    def __init__(self, policy: WorkerPolicy, clock: Clock, rng: Rng) -> None:
        self._policy = policy
        self._clock = clock
        self._rng = rng
        self._states: dict[str, ChannelBackoff] = {}

    def load_from(self, states: dict[str, ChannelBackoff]) -> None:
        """Recharge le registre depuis un snapshot persisté (reprise après crash, spec §7)."""
        self._states = dict(states)

    def snapshot(self) -> dict[str, ChannelBackoff]:
        """Copie de la map courante (à persister en fin de cycle, spec §7)."""
        return dict(self._states)

    def is_in_backoff(self, key: str) -> bool:
        """``True`` si ``key`` a un ``retry_after`` encore dans le FUTUR (à sauter)."""
        state = self._states.get(key)
        if state is None:
            return False
        return _iso(self._clock.now()) < state.retry_after

    def record_failure(self, key: str) -> float:
        """Incrémente ``attempts``, calcule délai+jitter, pose ``retry_after``. Rend le délai.

        Le délai sert au LOG ; la décision opérationnelle est le ``retry_after`` (skip).
        """
        attempts = self._states[key].attempts + 1 if key in self._states else 1
        delay = backoff_delay(
            attempts,
            base=self._policy.backoff_base_seconds,
            cap=self._policy.backoff_cap_seconds,
            factor=self._policy.backoff_factor,
        )
        delay += self._rng.jitter(self._policy.backoff_jitter_ratio * delay)
        retry_after = _iso(self._clock.now() + timedelta(seconds=delay))
        self._states[key] = ChannelBackoff(attempts=attempts, retry_after=retry_after)
        return delay

    def reset(self, key: str) -> None:
        """Efface le backoff d'une clé (succès)."""
        self._states.pop(key, None)


@dataclass
class WorkerDeps:
    """Dépendances partagées d'un travailleur (la composition les assemble une fois).

    ``backoff`` est le registre PARTAGÉ (même instance pour tous les travailleurs + le cycle,
    qui le persiste). Writer unique sur l'event loop → aucune course (spec §3).
    """

    catalog: CatalogRepository
    engine: MatchingEngine
    signal: DecisionSignal
    clock: Clock
    policy: WorkerPolicy
    backoff: "BackoffRegistry"


class SearchWorker:
    """Pilote UN ``amuled`` pour drainer des ``SearchTask`` (spec §3/§4)."""

    def __init__(self, instance_name: str, client: MuleClient, deps: WorkerDeps) -> None:
        self._instance = instance_name
        self._client = client
        self._deps = deps
        self._connected = False

    async def _ensure_connected(self) -> bool:
        """Connecte le client si nécessaire. Rend ``False`` si l'instance reste down."""
        if self._connected:
            return True
        try:
            await self._client.connect()
        except MuleUnreachableError as error:
            delay = self._deps.backoff.record_failure(self._instance)
            _logger.warning(
                "instance %s injoignable (%s) — backoff reconnexion %.1fs",
                self._instance,
                error,
                delay,
            )
            return False
        self._connected = True
        self._deps.backoff.reset(self._instance)
        _logger.info("instance %s connectée", self._instance)
        return True

    async def _poll_then_fetch(self) -> int:
        """Polling borné (budget config) puis ``fetch_results`` → pipeline par obs.

        Rend le nombre de verdicts CHANGÉS (logging). Le polling s'arrête dès 100 % ou au
        budget épuisé ; ``fetch_results`` rend le snapshot cumulatif (handoff EC). Une
        ``RepositoryError`` par obs est ABSORBÉE (loggée + comptée) DANS
        ``record_observation`` → le cycle continue (spec §7), une seule obs corrompue ne
        fait pas tomber tout le balayage.
        """
        waited = 0.0
        while waited < self._deps.policy.poll_budget_seconds:
            progress = await self._client.search_progress()
            if progress is not None and progress >= _PROGRESS_DONE:
                break
            await self._deps.clock.sleep(self._deps.policy.poll_interval_seconds)
            waited += self._deps.policy.poll_interval_seconds
        results = await self._client.fetch_results()
        changed = 0
        for observation in results:
            if record_observation(
                observation,
                catalog=self._deps.catalog,
                engine=self._deps.engine,
                signal=self._deps.signal,
            ):
                changed += 1
        return changed

    async def run_task(self, task: SearchTask) -> None:
        """Exécute UN ``SearchTask`` (spec §4). Ne lève jamais : signale par backoff/log.

        SAUTE l'item si l'instance OU le canal est en backoff (``retry_after`` futur, spec §7).
        """
        channel_key = f"{self._instance}:{task.channel}"
        if self._deps.backoff.is_in_backoff(self._instance):
            _logger.info("instance %s en backoff — item '%s' sauté", self._instance, task.keyword)
            return
        if self._deps.backoff.is_in_backoff(channel_key):
            _logger.info(
                "instance %s canal %s en backoff — item '%s' sauté",
                self._instance,
                task.channel,
                task.keyword,
            )
            return
        if not await self._ensure_connected():
            return
        try:
            await self._client.start_search(task.keyword, task.channel)
            changed = await self._poll_then_fetch()
        except MuleSearchFailedError as error:
            delay = self._deps.backoff.record_failure(channel_key)
            _logger.warning(
                "instance %s canal %s en échec (%s) — backoff %.1fs",
                self._instance,
                task.channel,
                error,
                delay,
            )
            return
        except MuleUnreachableError as error:
            self._connected = False
            delay = self._deps.backoff.record_failure(self._instance)
            _logger.warning(
                "instance %s : flux EC mort (%s) — instance down, backoff %.1fs",
                self._instance,
                error,
                delay,
            )
            return
        self._deps.backoff.reset(channel_key)
        _logger.info(
            "instance %s : '%s'/%s → %d verdict(s) changé(s)",
            self._instance,
            task.keyword,
            task.channel,
            changed,
        )
```

- [ ] **Step 4: Vérifier puis gate**

Run: `uv run pytest tests/application/test_search_worker.py -q --no-cov` → PASS (19 tests).
Run: gate complet → tout vert, 100 %.

- [ ] **Step 5: Commit**

```bash
git add src/emule_indexer/application/search_worker.py tests/application/test_search_worker.py
git commit -m "feat(application): SearchWorker (pool, backoff partagé persistable par instance/canal, skip-based)"
```

---

## Task 14: Application — `run_search_cycle`

**Files:**
- Create: `src/emule_indexer/application/run_search_cycle.py`
- Create: `tests/application/test_run_search_cycle.py`

> Un cycle (spec §4) : statut → `effective_coverage` (DÉCISION 4 : traduit `NetworkStatus`→bool ici) → `generate_keywords` → `shuffle_for_cycle` → fan-out (mot-clé × 2 canaux) dans une `asyncio.Queue` → N travailleurs drainent sous `TaskGroup` (sentinelle `None` par travailleur après `queue.join()`) → EN FIN DE CYCLE : `write_cycle_state(index+1, now)` ET `save_channel_backoff(backoff.snapshot())` — l'index ET le backoff persistés ENSEMBLE (DÉCISION 7). `run_search_cycle` reçoit le `BackoffRegistry` PARTAGÉ. Vérif empiriques 1 (worker pool), 9 (ownership), 10-11 (backoff persisté).

- [ ] **Step 1: Écrire le test qui échoue**

`tests/application/test_run_search_cycle.py` :
```python
import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from emule_indexer.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from emule_indexer.adapters.persistence_sqlite.connection import open_local
from emule_indexer.adapters.persistence_sqlite.scheduler_state_repository import (
    SqliteSchedulerStateRepository,
)
from emule_indexer.application.run_search_cycle import run_search_cycle
from emule_indexer.application.search_worker import (
    BackoffRegistry,
    SearchWorker,
    WorkerDeps,
    WorkerPolicy,
)
from emule_indexer.domain.matching.engine import MatchingEngine
from emule_indexer.domain.matching.models import TargetSegment
from emule_indexer.domain.observation import FileObservation
from emule_indexer.ports.mule_client import KadStatus, NetworkStatus
from tests.application.fakes import FakeClock, FakeMuleClient, FakeRng, RecordingSignal

_HASH = "31d6cfe0d16ae931b73c59d7e0c089c0"
_DL_NAME = "Keroro N°062A Les demoiselles cambrioleuses.avi"
_TARGETS = (TargetSegment(season=2, number=62, segment="A", title="Les demoiselles cambrioleuses"),)

_POLICY = WorkerPolicy(
    backoff_base_seconds=2.0,
    backoff_cap_seconds=60.0,
    backoff_factor=2.0,
    backoff_jitter_ratio=0.0,
    poll_budget_seconds=10.0,
    poll_interval_seconds=5.0,
)


class _NoopRng:
    """Rng identité : conserve l'ordre + jitter nul (déterminisme du test)."""

    def shuffled(self, items: tuple[str, ...], seed: str) -> tuple[str, ...]:
        return items

    def jitter(self, span: float) -> float:
        return 0.0


def _obs() -> FileObservation:
    return FileObservation(
        ed2k_hash=_HASH,
        filename=_DL_NAME,
        size_bytes=234_000_000,
        source_count=3,
        complete_source_count=1,
        keyword="keroro",
    )


@pytest.fixture
def local_connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_local(tmp_path / "local.db")
    yield connection
    connection.close()


def _worker(name: str, client: FakeMuleClient, deps: WorkerDeps) -> SearchWorker:
    return SearchWorker(name, client, deps)


def _deps(
    catalog: SqliteCatalogRepository,
    engine: MatchingEngine,
    clock: FakeClock,
    backoff: BackoffRegistry,
) -> WorkerDeps:
    return WorkerDeps(
        catalog=catalog,
        engine=engine,
        signal=RecordingSignal(),
        clock=clock,
        policy=_POLICY,
        backoff=backoff,
    )


@pytest.mark.asyncio
async def test_single_instance_cycle_records_and_advances(
    catalog: SqliteCatalogRepository,
    catalog_connection: sqlite3.Connection,
    local_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    clock = FakeClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    client = FakeMuleClient(results=[(_obs(),)])  # le download apparaît sur le 1er fetch
    worker = _worker("amule-1", client, _deps(catalog, engine, clock, backoff))
    scheduler_state = SqliteSchedulerStateRepository(local_connection)
    await run_search_cycle(
        workers=[worker],
        clients=[client],
        targets=_TARGETS,
        rng=_NoopRng(),
        node_id="node-A",
        cycle_index=0,
        scheduler_state=scheduler_state,
        backoff=backoff,
        clock=clock,
    )
    assert catalog_connection.execute("SELECT count(*) FROM match_decisions").fetchone()[0] == 1
    assert scheduler_state.read_cycle_index() == 1  # index = N+1, persisté en fin de cycle


@pytest.mark.asyncio
async def test_two_workers_drain_the_same_queue(
    catalog: SqliteCatalogRepository,
    local_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    clock = FakeClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    client_a = FakeMuleClient()
    client_b = FakeMuleClient()
    deps = _deps(catalog, engine, clock, backoff)
    workers = [_worker("amule-1", client_a, deps), _worker("amule-2", client_b, deps)]
    scheduler_state = SqliteSchedulerStateRepository(local_connection)
    await run_search_cycle(
        workers=workers,
        clients=[client_a, client_b],
        targets=_TARGETS,
        rng=_NoopRng(),
        node_id="node-A",
        cycle_index=3,
        scheduler_state=scheduler_state,
        backoff=backoff,
        clock=clock,
    )
    total_searches = len(client_a.searches) + len(client_b.searches)
    assert total_searches >= 2  # toutes les tâches distribuées entre les deux travailleurs
    assert scheduler_state.read_cycle_index() == 4


@pytest.mark.asyncio
async def test_one_instance_blind_still_runs_others(
    catalog: SqliteCatalogRepository,
    local_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    clock = FakeClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    blind = NetworkStatus(ed2k_id=None, ed2k_high=False, kad_status=KadStatus.OFF)
    healthy = NetworkStatus(ed2k_id=1, ed2k_high=True, kad_status=KadStatus.CONNECTED)
    client_a = FakeMuleClient(status=blind)
    client_b = FakeMuleClient(status=healthy)
    deps = _deps(catalog, engine, clock, backoff)
    workers = [_worker("amule-1", client_a, deps), _worker("amule-2", client_b, deps)]
    scheduler_state = SqliteSchedulerStateRepository(local_connection)
    await run_search_cycle(
        workers=workers,
        clients=[client_a, client_b],
        targets=_TARGETS,
        rng=_NoopRng(),
        node_id="node-A",
        cycle_index=0,
        scheduler_state=scheduler_state,
        backoff=backoff,
        clock=clock,
    )
    assert scheduler_state.read_cycle_index() == 1  # le cycle tourne (DEGRADED), aucune exception


@pytest.mark.asyncio
async def test_cycle_logs_blind_coverage(
    catalog: SqliteCatalogRepository,
    local_connection: sqlite3.Connection,
    engine: MatchingEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = FakeClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    blind = NetworkStatus(ed2k_id=None, ed2k_high=False, kad_status=KadStatus.OFF)
    client = FakeMuleClient(status=blind)
    worker = _worker("amule-1", client, _deps(catalog, engine, clock, backoff))
    scheduler_state = SqliteSchedulerStateRepository(local_connection)
    with caplog.at_level(logging.INFO, logger="emule_indexer.application.run_search_cycle"):
        await run_search_cycle(
            workers=[worker],
            clients=[client],
            targets=_TARGETS,
            rng=_NoopRng(),
            node_id="node-A",
            cycle_index=0,
            scheduler_state=scheduler_state,
            backoff=backoff,
            clock=clock,
        )
    assert "blind" in caplog.text


@pytest.mark.asyncio
async def test_channel_backoff_is_persisted_at_cycle_end(
    catalog: SqliteCatalogRepository,
    local_connection: sqlite3.Connection,
    engine: MatchingEngine,
) -> None:
    # Une recherche échoue (EC_OP_FAILED) → le canal entre en backoff DANS le registre
    # partagé ; le cycle PERSISTE le snapshot en fin de cycle (spec §3/§7). Une nouvelle
    # instance de repo (simulant un redémarrage) relit ce backoff.
    clock = FakeClock()
    backoff = BackoffRegistry(_POLICY, clock, FakeRng())
    from tests.application.fakes import make_search_failed

    client = FakeMuleClient(search_failures=[make_search_failed(), make_search_failed()])
    worker = _worker("amule-1", client, _deps(catalog, engine, clock, backoff))
    scheduler_state = SqliteSchedulerStateRepository(local_connection)
    await run_search_cycle(
        workers=[worker],
        clients=[client],
        targets=_TARGETS,
        rng=_NoopRng(),
        node_id="node-A",
        cycle_index=0,
        scheduler_state=scheduler_state,
        backoff=backoff,
        clock=clock,
    )
    persisted = SqliteSchedulerStateRepository(local_connection).load_channel_backoff()
    # Au moins un canal de amule-1 a un backoff persisté (global et/ou kad selon l'ordre).
    assert any(key.startswith("amule-1:") for key in persisted)
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/application/test_run_search_cycle.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: …run_search_cycle`.

- [ ] **Step 3: Écrire l'implémentation**

`src/emule_indexer/application/run_search_cycle.py` :
```python
"""Un cycle de recherche : statut → coverage → keywords → fan-out → drain → avance (§4).

Couche APPLICATION. ``run_search_cycle`` exécute UN cycle (spec §4) :

  1. ``network_status`` de CHAQUE travailleur → ``effective_coverage`` agrégé (loggé).
  2. ``generate_keywords(targets)`` → larges + ciblés ; ``shuffle_for_cycle`` (seed =
     node_id + index de cycle).
  3. enfile un ``SearchTask`` (mot-clé × canal) dans une ``asyncio.Queue`` partagée.
  4. N travailleurs drainent en parallèle (un par instance) ; sentinelle par travailleur.
  5. queue drainée → ``write_cycle_state`` (index = N+1, last_full_cycle_at) ET
     ``save_channel_backoff`` (snapshot du registre PARTAGÉ) — AU MÊME MOMENT (spec §3/§7).

Le pool dégénère en boucle séquentielle à N=1 (spec §3). Les travailleurs partagent la
queue ; chacun lit jusqu'à la sentinelle ``None``. Le ``TaskGroup`` supervise : une
annulation (arrêt, spec §6) atterrit au prochain ``await`` réseau, jamais en pleine
écriture DB (repos sync). Le backoff (registre partagé, muté par les travailleurs pendant
le cycle) n'est PERSISTÉ qu'en FIN de cycle, exactement comme ``cycle_index`` : un kill au
milieu rejoue le cycle ET re-arme le backoff depuis l'état du cycle précédent (cohérent —
l'index n'avance pas non plus à mi-cycle, spec §7).
"""

import asyncio
import logging
from collections.abc import Sequence

from emule_indexer.application.search_worker import BackoffRegistry, SearchTask, SearchWorker
from emule_indexer.domain.matching.models import TargetSegment
from emule_indexer.domain.search.coverage import effective_coverage
from emule_indexer.domain.search.cycle import Rng, shuffle_for_cycle
from emule_indexer.domain.search.keywords import generate_keywords
from emule_indexer.ports.clock import Clock
from emule_indexer.ports.mule_client import KadStatus, MuleClient, SearchChannel
from emule_indexer.ports.scheduler_state_repository import SchedulerStateRepository

_logger = logging.getLogger("emule_indexer.application.run_search_cycle")

# Les deux canaux balayés à chaque cycle (spec MVP §6 : global serveurs + Kad).
_CHANNELS = (SearchChannel.GLOBAL, SearchChannel.KAD)


def _is_search_capable(*, ed2k_high: bool, kad_status: KadStatus) -> bool:
    """Une instance peut-elle faire ABOUTIR une recherche ? (HighID OU Kad CONNECTED).

    Traduction APPLICATION du ``NetworkStatus`` (port) en booléen pur, avant d'appeler le
    domaine ``effective_coverage`` (qui ne connaît pas ``NetworkStatus`` — règle de
    dépendance, le domaine n'importe jamais un port).
    """
    return ed2k_high or kad_status == KadStatus.CONNECTED


async def _aggregate_coverage(clients: Sequence[MuleClient]) -> None:
    """Relève le statut de chaque client → ``effective_coverage`` agrégé (loggé, spec §7)."""
    capable: list[bool] = []
    for client in clients:
        status = await client.network_status()
        capable.append(_is_search_capable(ed2k_high=status.ed2k_high, kad_status=status.kad_status))
    coverage = effective_coverage(capable)
    _logger.info("effective_coverage=%s (%d instance(s))", coverage, len(capable))


async def _worker_loop(worker: SearchWorker, queue: "asyncio.Queue[SearchTask | None]") -> None:
    """Draine la queue jusqu'à la sentinelle ``None`` (un travailleur)."""
    while True:
        task = await queue.get()
        try:
            if task is None:
                return
            await worker.run_task(task)
        finally:
            queue.task_done()


async def run_search_cycle(
    *,
    workers: Sequence[SearchWorker],
    clients: Sequence[MuleClient],
    targets: Sequence[TargetSegment],
    rng: Rng,
    node_id: str,
    cycle_index: int,
    scheduler_state: SchedulerStateRepository,
    backoff: BackoffRegistry,
    clock: Clock,
) -> None:
    """Exécute UN cycle complet (spec §4) ; persiste l'avance + le backoff EN FIN (spec §7)."""
    await _aggregate_coverage(clients)
    keywords = generate_keywords(targets)
    texts = tuple(keyword.text for keyword in keywords)
    ordered = shuffle_for_cycle(texts, rng, node_id, cycle_index)
    queue: asyncio.Queue[SearchTask | None] = asyncio.Queue()
    for text in ordered:
        for channel in _CHANNELS:
            queue.put_nowait(SearchTask(keyword=text, channel=channel))
    _logger.info(
        "cycle %d : %d mot(s)-clé × %d canaux = %d tâche(s)",
        cycle_index,
        len(ordered),
        len(_CHANNELS),
        queue.qsize(),
    )
    async with asyncio.TaskGroup() as group:
        for worker in workers:
            group.create_task(_worker_loop(worker, queue))
        await queue.join()
        for _ in workers:
            queue.put_nowait(None)
    # FIN de cycle : index ET backoff persistés ENSEMBLE (spec §3/§7).
    scheduler_state.write_cycle_state(cycle_index + 1, clock.now())
    scheduler_state.save_channel_backoff(backoff.snapshot())
    _logger.info("cycle %d terminé", cycle_index)
```

- [ ] **Step 4: Vérifier puis gate**

Run: `uv run pytest tests/application/test_run_search_cycle.py -q --no-cov` → PASS (5 tests).
Run: gate complet → tout vert, 100 %.

- [ ] **Step 5: Commit**

```bash
git add src/emule_indexer/application/run_search_cycle.py tests/application/test_run_search_cycle.py
git commit -m "feat(application): run_search_cycle (fan-out→drain→avance + persistance index/backoff en fin de cycle)"
```

---

## Task 15: Composition — `CrawlerApp` + fichiers de config + `.gitignore`

**Files:**
- Create: `src/emule_indexer/composition/__init__.py` (vide)
- Create: `src/emule_indexer/composition/app.py`
- Create: `config/crawler.yaml`, `config/local.example.yaml`, `config/targets.yaml`, `config/matcher.yaml`
- Modify: `.gitignore`
- Create: `tests/composition/__init__.py` (vide)
- Create: `tests/composition/test_app.py`

> Composition root (DÉCISION 6) : pool de clients + 2 connexions + 3 repos UNIQUES + moteur + **`BackoffRegistry` PARTAGÉ** (construit `(policy, clock, rng)`, **RECHARGÉ** via `scheduler_state.load_channel_backoff()` au démarrage → le backoff survit au redémarrage, DÉCISION 7), sous `AsyncExitStack`. Boucle `_run_loop` (cycles + sleep), `backoff` threadé `_supervise`→`_run_loop`→`run_search_cycle`. Arrêt : `loop.add_signal_handler` (1er → set Event, 2e → `SystemExit`), `_supervise` annule `loop_task` (PAS d'`except*`, vérif empirique 2), fermeture LIFO sous `asyncio.timeout(shutdown_deadline)`, `finally` best-effort `aclose()`. Les `config/targets.yaml`/`config/matcher.yaml` sont des copies des fixtures canoniques (`tests/fixtures/canonical_targets.yaml` / `canonical_config.yaml`).

- [ ] **Step 1: Créer les `__init__.py` vides** (`src/emule_indexer/composition/__init__.py`, `tests/composition/__init__.py`).

- [ ] **Step 2: Créer les fichiers de config**

`config/crawler.yaml` :
```yaml
# Politique du crawler (versionné, partageable — spec orchestration §5).
# Toutes les durées en SECONDES. Défauts raisonnables pour un nœud d'observation.

cycle_interval_seconds: 300.0        # cadence visée d'un cycle complet (5 min)
search_poll_budget_seconds: 30.0     # attente max des résultats d'une recherche
search_poll_interval_seconds: 5.0    # pas de polling de la progression
keyword_pause_min_seconds: 1.0       # jitter inter-mots-clés : borne basse
keyword_pause_max_seconds: 4.0       # jitter inter-mots-clés : borne haute
decision_poll_interval_seconds: 5.0  # filet du nudge (consommateur futur, plans D/E)
shutdown_deadline_seconds: 10.0      # borne dure de l'arrêt propre (spec §6)

backoff:                             # backoff par (instance, canal) — exponentiel + jitter
  base_seconds: 2.0                  #   (persisté dans scheduler_state, survit au redémarrage)
  cap_seconds: 300.0
  factor: 2.0
  jitter_ratio: 0.3                  # jitter additionnel : jusqu'à +30 % du délai nominal
```

`config/local.example.yaml` :
```yaml
# Config LOCALE (machine + secret) — MODÈLE versionné (spec orchestration §5).
# Copier en config/local.yaml (gitignoré) et renseigner les valeurs réelles.

amules:                              # ≥ 1 instance amuled joignable par EC
  - name: amule-1                    # étiquette UNIQUE (logging + clé de backoff)
    host: gluetun                    # hôte EC (le conteneur gluetun expose le port d'amuled)
    port: 4712                       # port EC
    password: change-me              # mot de passe EC (secret)

catalog_db_path: /data/catalog.db    # base catalogue (append-only, partageable)
local_db_path: /data/local.db        # base opérationnelle (jamais fusionnée)

# node_id: optionnel — override l'identité du nœud ; sinon celle de local.db est utilisée.
```

`config/targets.yaml` : copie EXACTE de `tests/fixtures/canonical_targets.yaml` :
```yaml
# Cibles canoniques (cf. spec §7). target_id : S2E062A / S2E062B.
episodes:
  - season: 2
    number: 62
    broadcast_date: 2008-09-21
    status: partial
    segments:
      - { letter: A, title: "Les demoiselles cambrioleuses", aliases: [] }
      - { letter: B, title: "Le grand combat sous-marin" }
```

`config/matcher.yaml` : copie EXACTE de `tests/fixtures/canonical_config.yaml` :
```yaml
# Config matcher canonique (cf. spec §8.3). Sert le corpus golden bout-en-bout.
tokens:
  keroro:       { keyword: keroro }
  titar:        { keyword: titar }
  keroro_titar: { any: [keroro, titar] }
  teletoon:     { regex: "t[eé]l[eé]toon" }
  segment_id:   { regex: "n[°o]?\\s*0*{number}\\s*{segment}" }
  air_date:     { regex: "{date_alt}" }
  title_hit:    { coverage: title, min: 0.6 }
  is_video:     { regex: "\\.(avi|mkv|mp4|mpg|ogm)$" }
rules:
  - { name: id_segment_exact,    tier: download, all: [is_video, segment_id, keroro] }
  - { name: date_teletoon_titre, tier: download, all: [air_date, teletoon, { token: title_hit, min: 0.4 }] }
  - { name: numero_titre,        tier: notify,   all: [segment_id, { token: title_hit, min: 0.5 }] }
  - { name: keroro_large,        tier: catalog,  any: [keroro_titar] }
```

- [ ] **Step 3: Modifier `.gitignore`** — ajouter, après le bloc `*.db*` :
```
# Config locale (machine + secret) — JAMAIS versionnée (spec orchestration §5).
# Seul config/local.example.yaml (modèle) est suivi.
config/local.yaml
```

- [ ] **Step 4: Écrire le test qui échoue**

`tests/composition/test_app.py` :
```python
import asyncio
import datetime
import sqlite3
from pathlib import Path

import pytest

from emule_indexer.adapters.config.crawler_config import BackoffConfig, CrawlerConfig
from emule_indexer.adapters.config.local_config import AmuleEndpoint, LocalConfig
from emule_indexer.adapters.config.yaml_loader import load_yaml
from emule_indexer.composition.app import CrawlerApp, default_client_factory
from emule_indexer.domain.matching.config import MatcherConfig
from emule_indexer.domain.matching.models import TargetSegment
from emule_indexer.domain.matching.validation import parse_matcher_config
from emule_indexer.domain.observation import FileObservation
from emule_indexer.ports.mule_client import NetworkStatus
from tests.application.fakes import FakeClock, FakeMuleClient, RecordingSignal

_TARGETS = (
    TargetSegment(
        season=2,
        number=62,
        segment="A",
        title="Les demoiselles cambrioleuses",
        broadcast_date=datetime.date(2008, 9, 21),
    ),
)
_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_DL_NAME = "Keroro N°062A Les demoiselles cambrioleuses.avi"


class _NoopRng:
    """Rng identité : conserve l'ordre + jitter nul (déterminisme du test)."""

    def shuffled(self, items: tuple[str, ...], seed: str) -> tuple[str, ...]:
        return items

    def jitter(self, span: float) -> float:
        return 0.0


@pytest.fixture
def matcher_config() -> MatcherConfig:
    return parse_matcher_config(load_yaml(_FIXTURES / "canonical_config.yaml"))


def _crawler_config(shutdown_deadline: float = 30.0) -> CrawlerConfig:
    return CrawlerConfig(
        cycle_interval_seconds=300.0,
        search_poll_budget_seconds=10.0,
        search_poll_interval_seconds=5.0,
        keyword_pause_min_seconds=1.0,
        keyword_pause_max_seconds=2.0,
        backoff=BackoffConfig(base_seconds=2.0, cap_seconds=60.0, factor=2.0, jitter_ratio=0.0),
        decision_poll_interval_seconds=5.0,
        shutdown_deadline_seconds=shutdown_deadline,
    )


def _local_config(tmp_path: Path, *, count: int = 1, node_id: str | None = None) -> LocalConfig:
    return LocalConfig(
        amules=tuple(
            AmuleEndpoint(name=f"amule-{i}", host="h", port=4712 + i, password="p")
            for i in range(count)
        ),
        catalog_db_path=str(tmp_path / "catalog.db"),
        local_db_path=str(tmp_path / "local.db"),
        node_id=node_id,
    )


def _make_app(
    tmp_path: Path,
    matcher_config: MatcherConfig,
    *,
    factory: object,
    clock: FakeClock | None = None,
    node_id: str | None = None,
    shutdown_deadline: float = 30.0,
) -> CrawlerApp:
    return CrawlerApp(
        crawler_config=_crawler_config(shutdown_deadline),
        local_config=_local_config(tmp_path, node_id=node_id),
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=clock or FakeClock(),
        rng=_NoopRng(),
        signal_hub=RecordingSignal(),
        client_factory=factory,  # type: ignore[arg-type]
    )


class _ShutdownOnStatusClient(FakeMuleClient):
    """Client qui déclenche l'arrêt de l'app au PREMIER relevé de statut (1 cycle puis stop)."""

    def __init__(
        self,
        app_holder: dict[str, CrawlerApp],
        results: list[tuple[FileObservation, ...]] | None = None,
    ) -> None:
        super().__init__(results=results)
        self._app_holder = app_holder
        self._fired = False

    async def network_status(self) -> NetworkStatus:
        if not self._fired:
            self._fired = True
            self._app_holder["app"]._on_signal()  # simule un SIGINT après le démarrage du cycle
        return await super().network_status()


@pytest.mark.asyncio
async def test_app_runs_one_cycle_then_shuts_down_cleanly(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    created: list[_ShutdownOnStatusClient] = []
    app_holder: dict[str, CrawlerApp] = {}

    def factory(endpoint: AmuleEndpoint) -> _ShutdownOnStatusClient:
        client = _ShutdownOnStatusClient(app_holder)
        created.append(client)
        return client

    app = _make_app(tmp_path, matcher_config, factory=factory)
    app_holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=5.0)
    assert created and created[0].close_calls == 1  # client fermé APRÈS l'unwind
    assert (tmp_path / "catalog.db").exists()
    assert (tmp_path / "local.db").exists()


@pytest.mark.asyncio
async def test_node_id_override_is_used(tmp_path: Path, matcher_config: MatcherConfig) -> None:
    observation = FileObservation(
        ed2k_hash="31d6cfe0d16ae931b73c59d7e0c089c0",
        filename=_DL_NAME,
        size_bytes=234_000_000,
        source_count=3,
        complete_source_count=1,
        keyword="keroro",
    )
    app_holder: dict[str, CrawlerApp] = {}

    def factory(endpoint: AmuleEndpoint) -> _ShutdownOnStatusClient:
        return _ShutdownOnStatusClient(app_holder, results=[(observation,)])

    app = _make_app(tmp_path, matcher_config, factory=factory, node_id="forced-node")
    app_holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=5.0)
    catalog = sqlite3.connect(tmp_path / "catalog.db")
    try:
        rows = catalog.execute("SELECT DISTINCT node_id FROM file_observations").fetchall()
    finally:
        catalog.close()
    assert rows == [("forced-node",)]


@pytest.mark.asyncio
async def test_second_signal_forces_exit(tmp_path: Path, matcher_config: MatcherConfig) -> None:
    app = _make_app(tmp_path, matcher_config, factory=lambda e: FakeMuleClient())
    app._on_signal()  # 1er signal : demande d'arrêt
    with pytest.raises(SystemExit):
        app._on_signal()  # 2e signal : escalade → SystemExit


class _ShutdownOnSleepClock(FakeClock):
    """Horloge dont le ``sleep`` déclenche l'arrêt → la boucle sort PROPREMENT au tour suivant."""

    def __init__(self, app_holder: dict[str, CrawlerApp]) -> None:
        super().__init__()
        self._app_holder = app_holder

    async def sleep(self, seconds: float) -> None:
        await super().sleep(seconds)
        self._app_holder["app"]._shutdown.set()


@pytest.mark.asyncio
async def test_loop_exits_cleanly_when_shutdown_set_during_sleep(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # L'arrêt est posé pendant le sleep inter-cycle : la boucle re-teste sa condition et
    # SORT d'elle-même (sans annulation) → couvre la sortie normale du `while`.
    app_holder: dict[str, CrawlerApp] = {}
    clock = _ShutdownOnSleepClock(app_holder)
    app = _make_app(tmp_path, matcher_config, factory=lambda e: FakeMuleClient(), clock=clock)
    app_holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=5.0)


class _BlockingClient(FakeMuleClient):
    """Client dont ``fetch_results`` BLOQUE : la boucle reste en vol → l'annulation la frappe."""

    async def fetch_results(self) -> tuple[FileObservation, ...]:
        await asyncio.Event().wait()  # ne se résout jamais : bloque jusqu'à annulation
        return ()


@pytest.mark.asyncio
async def test_signal_cancels_an_in_flight_cycle(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    # Un travailleur est BLOQUÉ dans fetch_results ; un SIGINT externe annule le TaskGroup →
    # couvre le chemin d'annulation (unwind propre + ligne « Travailleurs arrêtés »).
    app = _make_app(tmp_path, matcher_config, factory=lambda e: _BlockingClient())
    run_task = asyncio.create_task(app.run())
    for _ in range(20):  # laisse le cycle démarrer et bloquer dans fetch_results
        await asyncio.sleep(0)
    app._on_signal()
    await asyncio.wait_for(run_task, timeout=5.0)


def test_default_client_factory_builds_an_amule_client() -> None:
    from emule_indexer.adapters.mule_ec.client import AmuleEcClient

    endpoint = AmuleEndpoint(name="amule-1", host="gluetun", port=4712, password="secret")
    assert isinstance(default_client_factory(endpoint), AmuleEcClient)


class _SlowCloseClient(_ShutdownOnStatusClient):
    """Client dont ``close`` traîne au-delà du délai d'arrêt → la borne le coupe."""

    async def close(self) -> None:
        await asyncio.sleep(10.0)  # > shutdown_deadline (réel) → TimeoutError


@pytest.mark.asyncio
async def test_shutdown_deadline_forces_exit(tmp_path: Path, matcher_config: MatcherConfig) -> None:
    # Fermeture qui traîne + délai d'arrêt minuscule → la borne lève TimeoutError (spec §6 :
    # l'app ne peut PAS paraître bloquée).
    app_holder: dict[str, CrawlerApp] = {}

    def factory(endpoint: AmuleEndpoint) -> _SlowCloseClient:
        return _SlowCloseClient(app_holder)

    app = _make_app(tmp_path, matcher_config, factory=factory, shutdown_deadline=0.05)
    app_holder["app"] = app
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(app.run(), timeout=5.0)


@pytest.mark.asyncio
async def test_observations_are_catalogued_during_the_cycle(
    tmp_path: Path, matcher_config: MatcherConfig
) -> None:
    observation = FileObservation(
        ed2k_hash="31d6cfe0d16ae931b73c59d7e0c089c0",
        filename=_DL_NAME,
        size_bytes=234_000_000,
        source_count=3,
        complete_source_count=1,
        keyword="keroro",
    )
    app_holder: dict[str, CrawlerApp] = {}

    def factory(endpoint: AmuleEndpoint) -> _ShutdownOnStatusClient:
        return _ShutdownOnStatusClient(app_holder, results=[(observation,)])

    app = _make_app(tmp_path, matcher_config, factory=factory)
    app_holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=5.0)
    catalog = sqlite3.connect(tmp_path / "catalog.db")
    try:
        count = catalog.execute("SELECT count(*) FROM match_decisions").fetchone()[0]
    finally:
        catalog.close()
    assert count == 1
```

- [ ] **Step 5: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/composition/test_app.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: …composition.app`.

- [ ] **Step 6: Écrire `composition/app.py`** (code COMPLET, ne rien abréger)

`src/emule_indexer/composition/app.py` :
```python
"""Composition root : assemble le pool + repos UNIQUES + moteur + boucle (spec §4/§6).

Couche COMPOSITION (la seule autorisée à importer adapters ET application). Construit :
- UNE ``SqliteCatalogRepository`` + UNE ``SqliteLocalStateRepository`` +
  ``SqliteSchedulerStateRepository`` (writer unique, invariant §11), connexions ouvertes
  via ``open_catalog``/``open_local`` (migrations vérifiées au démarrage, fail-fast §14).
- le ``MatchingEngine`` (une fois), le ``node_id`` (override config ou celui de local.db),
- un ``MuleClient`` + ``SearchWorker`` par instance configurée (pool, spec §3).

Boucle (``_run_loop``) : par cycle, ``run_search_cycle`` puis sommeil (cadence − écoulé).
Arrêt OBSERVABLE & BORNÉ (spec §6) : ``loop.add_signal_handler`` (PAS ``KeyboardInterrupt``,
qui préempterait une fonction sync en pleine écriture) ; 1er ^C → ligne humaine stderr +
annulation du ``TaskGroup`` ; 2e ^C → ``SystemExit`` immédiat ; les ressources longue durée
sont fermées par l'``AsyncExitStack`` APRÈS l'unwind complet du ``TaskGroup`` (plus aucun
travailleur ne peut écrire), le tout sous un délai borné (``shutdown_deadline_seconds``).
"""

import asyncio
import logging
import signal
import sys
from collections.abc import Callable, Sequence
from contextlib import AsyncExitStack, suppress

from emule_indexer.adapters.config.crawler_config import CrawlerConfig
from emule_indexer.adapters.config.local_config import AmuleEndpoint, LocalConfig
from emule_indexer.adapters.mule_ec.client import AmuleEcClient
from emule_indexer.adapters.persistence_sqlite.catalog_repository import SqliteCatalogRepository
from emule_indexer.adapters.persistence_sqlite.connection import open_catalog, open_local
from emule_indexer.adapters.persistence_sqlite.local_state_repository import (
    SqliteLocalStateRepository,
)
from emule_indexer.adapters.persistence_sqlite.scheduler_state_repository import (
    SqliteSchedulerStateRepository,
)
from emule_indexer.application.run_search_cycle import run_search_cycle
from emule_indexer.application.search_worker import (
    BackoffRegistry,
    SearchWorker,
    WorkerDeps,
    WorkerPolicy,
)
from emule_indexer.domain.matching.config import MatcherConfig
from emule_indexer.domain.matching.engine import MatchingEngine
from emule_indexer.domain.matching.models import TargetSegment
from emule_indexer.ports.clock import Clock, Rng
from emule_indexer.ports.decision_signal import DecisionSignal
from emule_indexer.ports.mule_client import MuleClient
from emule_indexer.ports.scheduler_state_repository import SchedulerStateRepository

_logger = logging.getLogger("emule_indexer.composition.app")

# Type de la factory de client (injectable en test pour substituer un FakeMuleClient).
ClientFactory = Callable[[AmuleEndpoint], MuleClient]


def _human(message: str) -> None:
    """Ligne humaine d'arrêt sur stderr (spec §6 : progression observable, hors logging)."""
    print(message, file=sys.stderr, flush=True)


def _build_policy(config: CrawlerConfig) -> WorkerPolicy:
    """Déballe la config de politique en primitifs pour l'application (règle de dépendance)."""
    return WorkerPolicy(
        backoff_base_seconds=config.backoff.base_seconds,
        backoff_cap_seconds=config.backoff.cap_seconds,
        backoff_factor=config.backoff.factor,
        backoff_jitter_ratio=config.backoff.jitter_ratio,
        poll_budget_seconds=config.search_poll_budget_seconds,
        poll_interval_seconds=config.search_poll_interval_seconds,
    )


def default_client_factory(endpoint: AmuleEndpoint) -> MuleClient:
    """Un ``AmuleEcClient`` réel par instance (factory par défaut, substituée en test)."""
    return AmuleEcClient(endpoint.host, endpoint.port, endpoint.password)


class CrawlerApp:
    """Assemble et fait tourner le crawler (composition root, spec §4/§6)."""

    def __init__(
        self,
        *,
        crawler_config: CrawlerConfig,
        local_config: LocalConfig,
        targets: Sequence[TargetSegment],
        matcher_config: MatcherConfig,
        clock: Clock,
        rng: Rng,
        signal_hub: DecisionSignal,
        client_factory: ClientFactory = default_client_factory,
    ) -> None:
        self._crawler_config = crawler_config
        self._local_config = local_config
        self._targets = tuple(targets)
        self._matcher_config = matcher_config
        self._clock = clock
        self._rng = rng
        self._signal = signal_hub
        self._client_factory = client_factory
        self._shutdown = asyncio.Event()
        self._signal_count = 0

    def _on_signal(self) -> None:
        """Handler de boucle (ne préempte jamais une fonction sync, spec §6)."""
        self._signal_count += 1
        if self._signal_count == 1:
            _human(
                "Arrêt demandé — fin des recherches en vol, fermeture propre… "
                "(Ctrl-C à nouveau pour forcer)"
            )
            self._shutdown.set()
        else:
            _human("Arrêt forcé.")
            raise SystemExit(1)

    async def _run_loop(
        self,
        *,
        workers: Sequence[SearchWorker],
        clients: Sequence[MuleClient],
        node_id: str,
        scheduler_state: SchedulerStateRepository,
        backoff: BackoffRegistry,
    ) -> None:
        """Boucle de cycles jusqu'à l'événement d'arrêt (annulée par le ``TaskGroup``)."""
        cycle_index = scheduler_state.read_cycle_index()
        while not self._shutdown.is_set():
            started = self._clock.now()
            await run_search_cycle(
                workers=workers,
                clients=clients,
                targets=self._targets,
                rng=self._rng,
                node_id=node_id,
                cycle_index=cycle_index,
                scheduler_state=scheduler_state,
                backoff=backoff,
                clock=self._clock,
            )
            cycle_index += 1
            elapsed = (self._clock.now() - started).total_seconds()
            remaining = max(0.0, self._crawler_config.cycle_interval_seconds - elapsed)
            await self._clock.sleep(remaining)

    async def _supervise(
        self,
        *,
        workers: Sequence[SearchWorker],
        clients: Sequence[MuleClient],
        node_id: str,
        scheduler_state: SchedulerStateRepository,
        backoff: BackoffRegistry,
    ) -> None:
        """Lance la boucle, attend l'arrêt (non borné), annule et unwind le ``TaskGroup``.

        L'attente du signal d'arrêt est libre (le crawler tourne tant qu'on ne l'arrête
        pas). À l'arrêt, ``loop_task.cancel()`` ; l'annulation atterrit au prochain ``await``
        réseau d'un travailleur (jamais en pleine écriture DB, repos sync, spec §6).
        VÉRIFICATION EMPIRIQUE : annuler UN enfant d'un ``TaskGroup`` (le groupe lui-même
        n'étant pas annulé) NE propage PAS de ``CancelledError`` au sortir du ``async with``
        — l'unwind est PROPRE. On affiche donc la progression APRÈS le bloc, sans ``except*``
        (qui serait du code mort). Une vraie exception d'un travailleur, elle, propagerait en
        ``ExceptionGroup`` — on ne la masque pas.
        """
        async with asyncio.TaskGroup() as group:
            loop_task = group.create_task(
                self._run_loop(
                    workers=workers,
                    clients=clients,
                    node_id=node_id,
                    scheduler_state=scheduler_state,
                    backoff=backoff,
                )
            )
            await self._shutdown.wait()
            loop_task.cancel()
        _human("Travailleurs arrêtés.")

    async def run(self) -> None:
        """Point d'entrée async : ouvre les ressources, installe les signaux, boucle (§6).

        Ownership (spec §6) : l'``AsyncExitStack`` possède les ressources longue durée (pool
        de clients + 2 connexions). La PHASE D'ARRÊT — unwind du ``TaskGroup`` PUIS fermeture
        LIFO du stack — est BORNÉE par un unique ``asyncio.timeout`` : l'app ne PEUT pas
        paraître bloquée. Un dépassement lève ``TimeoutError`` (sortie forcée) ; le ``finally``
        tente alors une fermeture best-effort (suppress) pour ne pas re-bloquer indéfiniment.
        """
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, self._on_signal)
        loop.add_signal_handler(signal.SIGTERM, self._on_signal)
        stack = AsyncExitStack()
        try:
            catalog_conn = open_catalog(self._local_config.catalog_db_path)
            stack.callback(catalog_conn.close)
            local_conn = open_local(self._local_config.local_db_path)
            stack.callback(local_conn.close)

            local_repo = SqliteLocalStateRepository(local_conn)
            node_id = self._local_config.node_id or local_repo.node_id()
            catalog_repo = SqliteCatalogRepository(catalog_conn, node_id)
            scheduler_state = SqliteSchedulerStateRepository(local_conn)
            engine = MatchingEngine(self._matcher_config, self._targets)
            # Registre de backoff PARTAGÉ : construit UNE fois, RECHARGÉ depuis scheduler_state
            # (le backoff survit au redémarrage, spec §3/§7), injecté dans TOUS les travailleurs
            # + passé au cycle qui le persiste. Writer unique sur l'event loop → aucune course.
            policy = _build_policy(self._crawler_config)
            backoff = BackoffRegistry(policy, self._clock, self._rng)
            backoff.load_from(scheduler_state.load_channel_backoff())
            deps = WorkerDeps(
                catalog=catalog_repo,
                engine=engine,
                signal=self._signal,
                clock=self._clock,
                policy=policy,
                backoff=backoff,
            )

            clients: list[MuleClient] = []
            workers: list[SearchWorker] = []
            for endpoint in self._local_config.amules:
                client = self._client_factory(endpoint)
                stack.push_async_callback(client.close)
                clients.append(client)
                workers.append(SearchWorker(endpoint.name, client, deps))

            _logger.info("crawler démarré : %d instance(s), node_id=%s", len(clients), node_id)
            async with asyncio.timeout(self._crawler_config.shutdown_deadline_seconds):
                await self._supervise(
                    workers=workers,
                    clients=clients,
                    node_id=node_id,
                    scheduler_state=scheduler_state,
                    backoff=backoff,
                )
                _human(f"{len(clients)} connexion(s) EC en fermeture…")
                await stack.aclose()
                _human("Bases fermées — sortie.")
        finally:
            # Best-effort si l'arrêt borné a échoué (TimeoutError) ou si le setup a levé :
            # ferme ce qui reste SANS jamais re-bloquer (suppress de toute panne/annulation).
            with suppress(BaseException):
                await stack.aclose()
            loop.remove_signal_handler(signal.SIGINT)
            loop.remove_signal_handler(signal.SIGTERM)
```

- [ ] **Step 7: Vérifier puis gate**

Run: `uv run pytest tests/composition/test_app.py -q --no-cov` → PASS (8 tests).
Run: gate complet → tout vert, 100 %.

- [ ] **Step 8: Commit**

```bash
git add src/emule_indexer/composition tests/composition/__init__.py tests/composition/test_app.py config/crawler.yaml config/local.example.yaml config/targets.yaml config/matcher.yaml .gitignore
git commit -m "feat(composition): CrawlerApp (pool, repos uniques, arrêt observable & borné) + config files"
```

---

## Task 16: Composition — `__main__` (point d'entrée `python -m emule_indexer`)

**Files:**
- Create: `src/emule_indexer/composition/__main__.py`
- Create: `tests/composition/test_main.py`

> Charge les 4 configs (fail-fast §5/§14 → code 1 sur config invalide), assemble les adapters RÉELS (`AsyncioClock`/`SeededRng`/`AsyncioDecisionSignal`), `asyncio.run(app.run())`. Chemins en arguments (`--crawler`/`--local`/`--targets`/`--matcher`, défauts `config/*.yaml`).

- [ ] **Step 1: Écrire le test qui échoue**

`tests/composition/test_main.py` :
```python
import argparse
from pathlib import Path

import pytest

from emule_indexer.composition import __main__ as entry
from emule_indexer.composition.app import CrawlerApp

_CONFIG = Path(__file__).resolve().parents[2] / "config"


def _args(**overrides: Path) -> argparse.Namespace:
    base = {
        "crawler": _CONFIG / "crawler.yaml",
        "local": _CONFIG / "local.example.yaml",
        "targets": _CONFIG / "targets.yaml",
        "matcher": _CONFIG / "matcher.yaml",
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_build_app_assembles_a_crawler_app() -> None:
    app = entry.build_app(_args())
    assert isinstance(app, CrawlerApp)


class _SpyApp:
    """Faux app : sa coroutine ``run`` n'est jamais réellement exécutée (asyncio.run faux)."""

    async def run(self) -> None:  # pragma: no cover - jamais await (asyncio.run est faux)
        return None


def test_main_returns_zero_on_clean_run(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(coro: object) -> None:
        coro.close()  # type: ignore[attr-defined]  # ferme la coroutine sans la lancer

    monkeypatch.setattr("emule_indexer.composition.__main__.asyncio.run", fake_run)
    monkeypatch.setattr(entry, "build_app", lambda args: _SpyApp())
    assert entry.main([]) == 0


def test_main_refuses_to_start_on_invalid_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad_local = tmp_path / "local.yaml"
    bad_local.write_text("amules: []\ncatalog_db_path: c\nlocal_db_path: l\n", encoding="utf-8")
    code = entry.main(
        [
            "--crawler",
            str(_CONFIG / "crawler.yaml"),
            "--local",
            str(bad_local),
            "--targets",
            str(_CONFIG / "targets.yaml"),
            "--matcher",
            str(_CONFIG / "matcher.yaml"),
        ]
    )
    assert code == 1
    assert "Config invalide" in capsys.readouterr().err


def test_main_refuses_on_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = entry.main(
        [
            "--crawler",
            str(tmp_path / "absent.yaml"),
            "--local",
            str(_CONFIG / "local.example.yaml"),
        ]
    )
    assert code == 1
    assert "Config invalide" in capsys.readouterr().err


def test_default_args_point_at_config_dir() -> None:
    namespace = entry._parse_args([])
    assert namespace.crawler == Path("config/crawler.yaml")
    assert namespace.local == Path("config/local.yaml")
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/composition/test_main.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: …composition.__main__`.

- [ ] **Step 3: Écrire `__main__.py`**

`src/emule_indexer/composition/__main__.py` :
```python
"""Point d'entrée ``python -m emule_indexer`` : charge la config, monte l'app, tourne (§4).

Mode OBSERVATEUR (spec §2) : observe, catalogue, décide, boucle — rien d'autre (pas de
download/notify : plans D/E). Charge ``crawler.yaml`` + ``local.yaml`` + ``targets.yaml`` +
la config matcher (fail-fast au moindre souci → refus de démarrer, spec §5/§14), assemble
les adapters réels (horloge/RNG/nudge), puis ``asyncio.run(app.run())``. L'arrêt propre &
borné est porté par ``CrawlerApp`` (spec §6).

Les chemins de config sont passés en arguments (``--crawler``/``--local``/``--targets``/
``--matcher``) avec des défauts ``config/*.yaml`` ; aucune variable d'environnement (spec §3).
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from emule_indexer.adapters.clock_asyncio import AsyncioClock, SeededRng
from emule_indexer.adapters.config.crawler_config import ConfigError, parse_crawler_config
from emule_indexer.adapters.config.local_config import parse_local_config
from emule_indexer.adapters.config.yaml_loader import YamlLoadError, load_yaml
from emule_indexer.adapters.decision_signal_asyncio import AsyncioDecisionSignal
from emule_indexer.composition.app import CrawlerApp
from emule_indexer.domain.matching.validation import (
    ConfigError as MatcherConfigError,
)
from emule_indexer.domain.matching.validation import (
    parse_matcher_config,
    parse_targets,
)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="emule_indexer", description="Crawler eMule (observateur)"
    )
    parser.add_argument("--crawler", type=Path, default=Path("config/crawler.yaml"))
    parser.add_argument("--local", type=Path, default=Path("config/local.yaml"))
    parser.add_argument("--targets", type=Path, default=Path("config/targets.yaml"))
    parser.add_argument("--matcher", type=Path, default=Path("config/matcher.yaml"))
    return parser.parse_args(argv)


def build_app(args: argparse.Namespace) -> CrawlerApp:
    """Charge + valide toute la config (fail-fast §5/§14) et assemble la ``CrawlerApp``.

    Toute erreur de config (``YamlLoadError``/``ConfigError``/``MatcherConfigError``) remonte
    telle quelle : ``main`` l'attrape, logge clair, et refuse de démarrer (spec §14).
    """
    crawler_config = parse_crawler_config(load_yaml(args.crawler))
    local_config = parse_local_config(load_yaml(args.local))
    targets = parse_targets(load_yaml(args.targets))
    matcher_config = parse_matcher_config(load_yaml(args.matcher))
    return CrawlerApp(
        crawler_config=crawler_config,
        local_config=local_config,
        targets=targets,
        matcher_config=matcher_config,
        clock=AsyncioClock(),
        rng=SeededRng(),
        signal_hub=AsyncioDecisionSignal(),
    )


def main(argv: list[str] | None = None) -> int:
    """Entrée CLI. Rend un code de sortie (0 = arrêt propre, 1 = config invalide)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        app = build_app(args)
    except (YamlLoadError, ConfigError, MatcherConfigError) as error:
        print(f"Config invalide, refus de démarrer : {error}", file=sys.stderr, flush=True)
        return 1
    asyncio.run(app.run())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
```

> **Note couverture** : la ligne `if __name__ == "__main__":` est marquée `# pragma: no cover` (jamais exécutée sous pytest) ; `_SpyApp.run` aussi (la coroutine est `close()`-ée par le faux `asyncio.run`, jamais awaitée). Le reste est couvert par les 5 tests.

- [ ] **Step 4: Vérifier puis gate**

Run: `uv run pytest tests/composition/test_main.py -q --no-cov` → PASS (5 tests).
Run: gate complet → tout vert, 100 %.

- [ ] **Step 5: Commit**

```bash
git add src/emule_indexer/composition/__main__.py tests/composition/test_main.py
git commit -m "feat(composition): point d'entrée python -m emule_indexer (charge config, fail-fast, run)"
```

---

## Task 17: Test bout-en-bout opt-in + marqueur `orchestration_integration`

**Files:**
- Modify: `pyproject.toml` (marqueur + déselection par défaut)
- Create: `tests/integration/test_crawler_loop.py`

> Spec §8 : « bout-en-bout léger (marqueur séparé, opt-in comme `ec_integration`) : boucle réelle contre l'`amuled` testcontainers, un cycle, arrêt propre. » Déselectionné du run par défaut (Docker requis) ; run dédié `uv run pytest -m orchestration_integration --no-cov`. Le client réel est enveloppé par un décorateur de test qui déclenche l'arrêt après le 1er relevé de statut → un seul cycle.

- [ ] **Step 1: Modifier `pyproject.toml`**

Dans `[tool.pytest.ini_options]`, remplacer la ligne `addopts` :
```
addopts = '--cov=emule_indexer --cov-report=term-missing --cov-fail-under=100 --strict-markers -m "not ec_integration"'
```
par :
```
addopts = '--cov=emule_indexer --cov-report=term-missing --cov-fail-under=100 --strict-markers -m "not ec_integration and not orchestration_integration"'
```
et ajouter à la liste `markers` (après la ligne `ec_integration`) :
```
    "orchestration_integration: boucle de crawl réelle contre un amuled testcontainers (Docker requis) — déselectionnés par défaut ; run dédié : uv run pytest -m orchestration_integration --no-cov",
```

- [ ] **Step 2: Écrire le test d'intégration**

`tests/integration/test_crawler_loop.py` :
```python
"""Bout-en-bout léger : la boucle de crawl RÉELLE contre un amuled testcontainers (spec §8).

Run dédié : uv run pytest -m orchestration_integration --no-cov
Valide qu'un ``CrawlerApp`` réel — vrais ``AmuleEcClient`` + vraies bases SQLite — tourne
UN cycle complet contre un ``amuled`` Docker puis s'arrête PROPREMENT. Les résultats
peuvent être vides (pas d'accès réseau eD2k garanti) : c'est la BOUCLE (démarrage,
recherche, catalogage, arrêt borné) qui est validée, pas la richesse des résultats.
"""

import datetime
from collections.abc import Iterator
from pathlib import Path

import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from emule_indexer.adapters.clock_asyncio import AsyncioClock, SeededRng
from emule_indexer.adapters.config.crawler_config import BackoffConfig, CrawlerConfig
from emule_indexer.adapters.config.local_config import AmuleEndpoint, LocalConfig
from emule_indexer.adapters.config.yaml_loader import load_yaml
from emule_indexer.adapters.decision_signal_asyncio import AsyncioDecisionSignal
from emule_indexer.composition.app import CrawlerApp
from emule_indexer.domain.matching.models import TargetSegment
from emule_indexer.domain.matching.validation import parse_matcher_config
from emule_indexer.ports.mule_client import NetworkStatus

pytestmark = pytest.mark.orchestration_integration

_EC_PASSWORD = "indexer-ec-test"
_IMAGE = "ngosang/amule:3.0.0-1"
_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_TARGETS = (
    TargetSegment(
        season=2,
        number=62,
        segment="A",
        title="Les demoiselles cambrioleuses",
        broadcast_date=datetime.date(2008, 9, 21),
    ),
)


@pytest.fixture(scope="module")
def amuled() -> Iterator[tuple[str, int]]:
    ready = LogMessageWaitStrategy(r"listening on 0\.0\.0\.0:4712").with_startup_timeout(180)
    container = (
        DockerContainer(_IMAGE)
        .with_env("GUI_PWD", _EC_PASSWORD)
        .with_exposed_ports(4712)
        .waiting_for(ready)
    )
    try:
        container.start()
        yield container.get_container_host_ip(), int(container.get_exposed_port(4712))
    finally:
        container.stop()


class _ShutdownAfterFirstStatusClient:
    """Enveloppe un vrai client et déclenche l'arrêt après le 1er relevé de statut (1 cycle)."""

    def __init__(self, inner: object, app_holder: dict[str, CrawlerApp]) -> None:
        self._inner = inner
        self._app_holder = app_holder
        self._fired = False

    async def connect(self) -> None:
        await self._inner.connect()  # type: ignore[attr-defined]

    async def close(self) -> None:
        await self._inner.close()  # type: ignore[attr-defined]

    async def start_search(self, keyword: str, channel: object) -> None:
        await self._inner.start_search(keyword, channel)  # type: ignore[attr-defined]

    async def fetch_results(self) -> tuple:  # type: ignore[type-arg]
        return await self._inner.fetch_results()  # type: ignore[attr-defined,no-any-return]

    async def stop_search(self) -> None:
        await self._inner.stop_search()  # type: ignore[attr-defined]

    async def search_progress(self) -> int | None:
        return await self._inner.search_progress()  # type: ignore[attr-defined,no-any-return]

    async def network_status(self) -> NetworkStatus:
        status = await self._inner.network_status()  # type: ignore[attr-defined]
        if not self._fired:
            self._fired = True
            self._app_holder["app"]._on_signal()
        return status  # type: ignore[no-any-return]


@pytest.mark.asyncio
async def test_real_loop_runs_one_cycle_and_stops(amuled: tuple[str, int], tmp_path: Path) -> None:
    import asyncio

    from emule_indexer.adapters.mule_ec.client import AmuleEcClient

    host, port = amuled
    matcher_config = parse_matcher_config(load_yaml(_FIXTURES / "canonical_config.yaml"))
    crawler_config = CrawlerConfig(
        cycle_interval_seconds=300.0,
        search_poll_budget_seconds=10.0,
        search_poll_interval_seconds=5.0,
        keyword_pause_min_seconds=1.0,
        keyword_pause_max_seconds=2.0,
        backoff=BackoffConfig(base_seconds=2.0, cap_seconds=60.0, factor=2.0, jitter_ratio=0.3),
        decision_poll_interval_seconds=5.0,
        shutdown_deadline_seconds=30.0,
    )
    local_config = LocalConfig(
        amules=(AmuleEndpoint(name="amule-1", host=host, port=port, password=_EC_PASSWORD),),
        catalog_db_path=str(tmp_path / "catalog.db"),
        local_db_path=str(tmp_path / "local.db"),
        node_id=None,
    )
    app_holder: dict[str, CrawlerApp] = {}

    def factory(endpoint: AmuleEndpoint) -> _ShutdownAfterFirstStatusClient:
        inner = AmuleEcClient(endpoint.host, endpoint.port, endpoint.password, timeout=30.0)
        return _ShutdownAfterFirstStatusClient(inner, app_holder)

    app = CrawlerApp(
        crawler_config=crawler_config,
        local_config=local_config,
        targets=_TARGETS,
        matcher_config=matcher_config,
        clock=AsyncioClock(),
        rng=SeededRng(),
        signal_hub=AsyncioDecisionSignal(),
        client_factory=factory,
    )
    app_holder["app"] = app
    await asyncio.wait_for(app.run(), timeout=120.0)
    assert (tmp_path / "catalog.db").exists()
```

- [ ] **Step 3: Vérifier (collection seule sans Docker ; exécution si Docker dispo)**

Run (collection, sans Docker) : `uv run pytest tests/integration/test_crawler_loop.py --collect-only -q -m orchestration_integration`
Expected: `1 test collected` (le test est bien marqué et déselectionné du run par défaut).

Run (si Docker disponible) : `uv run pytest -m orchestration_integration --no-cov -q`
Expected: `1 passed` (boucle réelle, un cycle, arrêt propre).

- [ ] **Step 4: Gate complet (le test d'intégration reste DÉSELECTIONNÉ)**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run sqlfluff lint src`
Expected: tout vert ; pytest rapporte **`5 deselected`** (4 ec_integration + 1 orchestration_integration), 100 %.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests/integration/test_crawler_loop.py
git commit -m "test: boucle de crawl bout-en-bout contre amuled (marqueur orchestration_integration, opt-in)"
```

---

## Task 18: Revue holistique finale + tag `v0.7.0-orchestration`

**Files:** (aucune création — vérification + tag)

> La revue holistique attrape les bugs cross-cutting que le suivi à la lettre ne voit pas (méthode reconduite des jalons précédents). Vérifier la RÈGLE DE DÉPENDANCE par grep, le gate complet, puis tagger.

- [ ] **Step 1: Greps de la règle de dépendance (DOIVENT être CLEAN sauf whitelist)**

Run (le domaine n'importe que des deps pur-calcul whitelistées — `re2`/`rapidfuzz` du moteur ; `domain/search/` n'importe RIEN d'autre que le domaine + stdlib) :
```bash
grep -rnE "^(from|import) (emule_indexer\.(ports|adapters|application|composition)|re2|rapidfuzz)" src/emule_indexer/domain/
```
Expected (EXACTEMENT, le moteur seul) :
```
src/emule_indexer/domain/matching/interpolation.py:6:import re2
src/emule_indexer/domain/matching/matchers.py:3:import re2
src/emule_indexer/domain/matching/matchers.py:4:from rapidfuzz import fuzz
src/emule_indexer/domain/matching/validation.py:12:import re2
```
(AUCUNE ligne sous `domain/search/` — si une apparaît, c'est une violation à corriger.)

Run (l'application n'importe JAMAIS un adapter ni la composition) :
```bash
grep -rnE "^(from|import) emule_indexer\.(adapters|composition)" src/emule_indexer/application/
```
Expected : **AUCUNE sortie** (code retour 1). Si une ligne apparaît, c'est une violation de la règle de dépendance §4 — le plan a justement déplacé le contrat d'erreur dans les ports pour l'éviter.

Run (les ports n'importent jamais adapters/application/composition) :
```bash
grep -rnE "^(from|import) emule_indexer\.(adapters|application|composition)" src/emule_indexer/ports/
```
Expected : **AUCUNE sortie**.

- [ ] **Step 2: Vérifier la pureté de `domain/search/` (uniquement stdlib + domaine)**

Run:
```bash
grep -rn "from emule_indexer.ports" src/emule_indexer/domain/search/
```
Expected : **AUCUNE sortie** (le Protocol `Rng` vit DANS le domaine, ré-exporté par les ports — DÉCISION 3 ; le domaine ne remonte jamais vers un port).

- [ ] **Step 3: Revue de cohérence (lecture humaine/subagent, à la recherche de bugs cross-cutting)**

Points à vérifier explicitement (chacun déjà couvert par un test, mais la revue confirme la cohérence) :
- **Ordre d'écriture catalogue** : `record_observation` AVANT `record_decision` (FK) — `record_observations.py` respecte (la décision est dans le même `try`, après l'observation).
- **Anti-redondance** : la comparaison est `last_decision(hash) == to_record(decision)` — ré-append SEULEMENT si différent ; l'observation est re-persistée à CHAQUE fois.
- **Arrêt** : `loop_task.cancel()` annule un ENFANT du `TaskGroup` → pas d'`except*` (vérif empirique 2) ; la fermeture est sous `asyncio.timeout` ; le `finally` best-effort ne re-bloque pas (`suppress(BaseException)`).
- **Ownership** : les `client.close` et les `conn.close` sont poussés sur le `stack` ; AUCUN travailleur ne ferme quoi que ce soit ; le `stack.aclose()` est APRÈS l'unwind du `TaskGroup`.
- **Déterminisme** : aucun `random`/`datetime.now`/`asyncio.sleep` direct dans `application/` (tout passe par `Clock`/`Rng`).

Run (anti-`random`/horloge directe dans l'application) :
```bash
grep -rnE "(^import random|^import time|datetime\.now|asyncio\.sleep)" src/emule_indexer/application/
```
Expected : **AUCUNE sortie** (le déterminisme passe par les ports injectés).

- [ ] **Step 4: Gate complet final**

Run:
```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run sqlfluff lint src
```
Expected: tout vert — `… passed, 5 deselected`, **100.00 % branch** ; ruff/format/mypy/sqlfluff propres.

- [ ] **Step 5: Mettre à jour `CLAUDE.md` (état courant — minimal)**

Mettre à jour le paragraphe « Current state » de `CLAUDE.md` pour refléter que l'**orchestration** est désormais construite (couches `application/` + `composition/`, `python -m emule_indexer` tourne), et bumper la liste des milestones jusqu'à `v0.7.0-orchestration`. Ne PAS réécrire les sections d'architecture (le moteur/EC/data-model restent valides).

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md — orchestration construite (application + composition, python -m emule_indexer)"
```

- [ ] **Step 6: Écrire le handoff de jalon**

Créer `docs/handoffs/2026-06-12 - handoff - orchestration.md` (continuation guide, format des handoffs précédents) : TL;DR, état vérifiable, contrats que les plans D/E doivent respecter (la table `match_decisions` est le journal à rejouer ; le hub `DecisionSignal` réveille les consommateurs in-process ; `scheduler_state` porte `cycle_index`/`last_full_cycle_at` ; le backoff est en mémoire, à persister en plan ultérieur si besoin), pièges appris (annuler un enfant de `TaskGroup` ne propage pas de `CancelledError` ; `add_signal_handler` ≠ `KeyboardInterrupt` ; le contrat d'erreur dans les ports), prochaine étape recommandée (plan D auto-download, ou plan E observabilité).

```bash
git add "docs/handoffs/2026-06-12 - handoff - orchestration.md"
git commit -m "docs: handoff de jalon (orchestration)"
```

- [ ] **Step 7: Tagger le jalon (annoté, NON poussé)**

```bash
git tag -a v0.7.0-orchestration -m "Orchestration des recherches : boucle de crawl (pool, anti-redondance, arrêt borné)"
git tag --list | grep orchestration
```

---

## Self-Review : couverture de la spec (section → tâche)

| Spec orchestration | Couvert par |
|---|---|
| §2 Scope : keywords larges + ciblés | Task 1 (`generate_keywords`) |
| §2 Pool de travailleurs (1/instance, séquentiel à N=1) | Tasks 13 (`SearchWorker`), 14 (`run_search_cycle`), 15 (`CrawlerApp`) |
| §2/§4 Cycle ordonnancé (statut→coverage→keywords→shuffle→fan-out→drain→avance→sommeil) | Tasks 4 (coverage), 2 (shuffle), 14 (cycle), 15 (sommeil jitteré dans `_run_loop`) |
| §2/§3/§7 Backoff exponentiel+jitter par (instance, canal), **PERSISTÉ** dans `scheduler_state` | Tasks 3 (`backoff_delay` pur), 10 (`SeededRng.jitter`), 13 (`BackoffRegistry` partagé : jitter via `Rng`, skip jusqu'à `retry_after`, `snapshot`/`load_from`), 9 (`load/save_channel_backoff`, JSON KV), 14 (persistance en fin de cycle), 15 (registre partagé construit + rechargé au démarrage) |
| §2/§3 Pipeline par obs (record→eval→si verdict changé decision+nudge) | Task 12 (`record_observations`) |
| §3 Anti-redondance par changement de verdict (`last_decision`) | Tasks 8 (`last_decision`/`DecisionRecord`), 12 (comparaison) |
| §2/§5 Config YAML deux fichiers (`crawler.yaml`/`local.yaml` gitignoré + `local.example.yaml`) | Tasks 11 (parsers), 15 (fichiers + `.gitignore`) |
| §2/§4 Point d'entrée `python -m emule_indexer` | Task 16 (`__main__`) |
| §3 Couplage par la donnée + nudge in-process (`DecisionSignal`) | Tasks 6 (port), 10 (`AsyncioDecisionSignal`), 12 (signal post-commit) |
| §3 Repos uniques partagés, sync, appelés directement (writer unique) | Task 15 (`CrawlerApp` construit UNE instance de chaque) |
| §3 Déterminisme total (Clock/Rng/sleep injectables, seed `node_id`+cycle) | Tasks 2/6/10 + faux avançables (Tasks 12-15) |
| §3 Domaine pur (keywords/cycle/backoff/coverage) | Tasks 1-4 ; grep règle de dépendance Task 18 |
| §6 Arrêt observable & borné (`add_signal_handler`, 1er/2e ^C, progression, deadline, ownership AsyncExitStack après TaskGroup) | Task 15 (`CrawlerApp`) |
| §7 Résilience (instance down→backoff reconnexion ; canal échec→backoff ; coverage blind loggé ; obs en échec loggée+cycle continue ; fail-fast config ; reprise via `scheduler_state`) | Tasks 13 (worker), 14 (coverage log), 12 (RepositoryError absorbée), 11/16 (fail-fast), 9 (cycle_index) |
| §8 Tests (domaine pur ; application : FakeMuleClient + vrais repos SQLite + horloge/RNG/sleep injectables ; arrêt ; nudge awaité ; bout-en-bout opt-in) | toutes les tâches ; Tasks 12 (nudge awaité), 15 (arrêt), 17 (bout-en-bout) |
| §9 DoD : domaine+ports+application+adapters+composition testés ; `last_decision` ; config files ; `python -m emule_indexer` tourne+s'arrête ; tag | Tasks 1-18 |
| §10 questions laissées au plan (forme config + défauts ; signature `last_decision` + SQL + index ; découpage worker ; format logging+lignes d'arrêt ; marqueur bout-en-bout) | DÉCISIONS 2/5/6/7/8 + Tasks 8/11/13/15/17 |

**Décisions VERROUILLÉES (pour revue) :**
- `last_decision` rend un **`DecisionRecord(target_id, rule_name, tier)`** (dataclass gelé du domaine, + helper `to_record`), PAS un `MatchDecision` à `explanation` vide — l'`explanation` n'est pas persistée, la comparaison de verdict n'a besoin que des trois colonnes (DÉCISION 2).
- **Backoff PERSISTÉ** dans `scheduler_state` (clé `channel_backoff`, JSON), au même moment que `cycle_index` en fin de cycle (DÉCISION 7) — survit au redémarrage (spec §3/§7). Forme retenue : registre PARTAGÉ `BackoffRegistry` (skip jusqu'à `retry_after` au lieu de sleep) ; jitter via le port `Rng` (`jitter(span)`) ; `retry_after` ISO-8601 UTC (comparaison lexicographique). Un kill à mi-cycle ne fait avancer NI l'index NI le backoff → le cycle rejoue et re-arme le backoff (cohérent). Round-trip à travers une instance de repo neuve **empiriquement vérifié** (vérif. empiriques 10-11).
