# Handoff — emule-indexer (orchestration des recherches)

> **But** : reprendre naturellement après le jalon `v0.7.0-orchestration`. Lis aussi les
> handoffs précédents (moteur de matching, adapter EC, modèle de données) ; celui-ci
> couvre le **Plan C** — la boucle de crawl qui relie tout.
>
> **Dernière mise à jour** : 2026-06-13, après le tag `v0.7.0-orchestration`.

---

## 1. TL;DR

- **Ce qui est fait** : le **crawler tourne**. `python -m emule_indexer` charge la config,
  assemble un pool de travailleurs (un par instance `amuled`), et boucle : statut réseau →
  coverage agrégé → mots-clés (larges + ciblés) mélangés de façon seedée → fan-out des
  recherches EC → persistance des observations → évaluation par le moteur → persistance des
  décisions (**anti-redondance** : on n'écrit/nudge que si le verdict CHANGE) → nudge d'un
  hub in-process → cycle suivant. **Backoff par (instance, canal)** exponentiel + jitter,
  **PERSISTÉ** dans `scheduler_state` (survit au redémarrage). **Arrêt observable & borné**
  (SIGINT/SIGTERM, messages humains, fermeture LIFO sous délai).
- **Trois couches nouvelles** : `domain/search/` (PUR), `application/` (use-cases async),
  `composition/` (assemblage + entrée). Déterminisme TOTAL (`Clock`/`Rng`/`sleep` injectés).
- **Gate** : **502 tests, 100 % branch** ; ruff/format/mypy/sqlfluff verts. **Test e2e
  opt-in** (marqueur `orchestration_integration`) **VERT contre un `amuled` réel** (Docker
  testcontainers) — c'est ce qui fait foi.
- **Prochaine étape recommandée** : **Plan D (auto-download + verifier)** — tout le câblage
  est prêt : `match_decisions` est le journal que D rejoue, la **file de vérification**
  (modèle de données) attend un producteur, le hub de nudge (`DecisionSignal`) réveille un
  consommateur in-process. Ou **Plan E (observabilité)** si tu veux Prometheus/apprise
  avant le téléchargement. Brainstormer d'abord, comme toujours.

## 2. État vérifiable

- Tag annoté **`v0.7.0-orchestration`** (non poussé). Gate **5 checks** inchangé +
  **un 6e filet, l'e2e opt-in** : `uv run pytest -m orchestration_integration --no-cov`
  (Docker requis ; `ngosang/amule:3.0.0-1` ; ~4 s ; déselectionné du run par défaut, NE
  compte PAS dans le coverage — comme `ec_integration`).
- Spec : `docs/superpowers/specs/2026-06-12-orchestration-design.md`. Plan exécuté :
  `docs/superpowers/plans/2026-06-12-crawler-mvp-05-orchestration.md` (18 tâches).
- Deux marqueurs d'intégration enregistrés (`--strict-markers`) : `ec_integration` (4) +
  `orchestration_integration` (1) → **5 deselected** au run par défaut.

## 3. Ce qui existe maintenant

```
src/emule_indexer/
├── domain/search/                     # PUR (nouveau sous-paquet)
│   ├── keywords.py        # generate_keywords → SearchKeyword (larges + ciblés, dédup)
│   ├── cycle.py           # Rng (Protocol, défini ICI), cycle_seed, shuffle_for_cycle
│   ├── backoff.py         # backoff_delay (math pure, exponentiel plafonné)
│   └── coverage.py        # Coverage (enum), effective_coverage(Sequence[bool])
├── domain/matching/engine.py          # + DecisionRecord(target_id,rule_name,tier), to_record
├── ports/
│   ├── clock.py           # Clock (now aware + sleep async) ; ré-exporte Rng (DÉCISION 3)
│   ├── decision_signal.py # DecisionSignal (signal sync / wait async) — le hub de nudge
│   ├── repository_errors.py        # RepositoryError (contrat ; PersistenceError en hérite)
│   ├── scheduler_state_repository.py  # SchedulerStateRepository + ChannelBackoff(attempts,retry_after)
│   ├── mule_client.py     # + MuleClientError → MuleUnreachableError / MuleSearchFailedError
│   └── catalog_repository.py          # + last_decision(hash) -> DecisionRecord | None
├── application/                       # NOUVELLE COUCHE (async ; ports + domaine SEULEMENT)
│   ├── record_observations.py  # record→eval→anti-redondance→record_decision→nudge (RepositoryError absorbée)
│   ├── search_worker.py        # SearchWorker, BackoffRegistry (PARTAGÉ), WorkerPolicy, WorkerDeps, SearchTask
│   └── run_search_cycle.py     # un cycle : coverage→keywords→fan-out→drain (keyword_pause)→persiste index+backoff
├── adapters/
│   ├── clock_asyncio.py        # AsyncioClock + SeededRng (le SEUL datetime.now/random/asyncio.sleep)
│   ├── decision_signal_asyncio.py  # AsyncioDecisionSignal (un asyncio.Event par sujet)
│   ├── config/{crawler_config,local_config}.py  # parse_* gelés, fail-fast (bool ≠ nombre garé)
│   ├── mule_ec/{errors,client}.py   # EC errors héritent du contrat ; connect() rendu IDEMPOTENT
│   └── persistence_sqlite/{scheduler_state_repository,catalog_repository,errors}.py
└── composition/                       # NOUVELLE COUCHE — la SEULE à câbler adapters + application
    ├── app.py             # CrawlerApp (pool, repos UNIQUES partagés, boucle, arrêt borné)
    └── __main__.py        # python -m emule_indexer (charge 4 yaml, fail-fast, run)

config/  crawler.yaml (politique) · local.example.yaml (modèle ; local.yaml GITIGNORÉ)
         targets.yaml + matcher.yaml (copies des fixtures canoniques)
```

## 4. Contrats que les plans D/E/F doivent respecter

- **`match_decisions` EST le journal.** Le plan C écrit une décision UNIQUEMENT au changement
  de verdict (anti-redondance, `last_decision(hash) != to_record(decision)`). Un consommateur
  (auto-download D, notif E) **rejoue** la table — il ne s'abonne pas à un flux éphémère. Le
  **nudge** (`DecisionSignal`) est une OPTIMISATION best-effort de réactivité ; un nudge perdu
  est inoffensif (le polling de repli est le filet). Sujet du nudge = `ed2k_hash`.
- **`decision_poll_interval`** (config) est PARSÉ mais INERTE en plan C — c'est le knob du
  polling de repli pour le consommateur IN-PROCESS de D/E. Forward-compat par design.
- **File de vérification** (modèle de données, déjà là) : `enqueue/claim/complete/fail/reclaim`
  attend un producteur (le verifier D) ; un crash-loop pur ping-pong reclaim→claim sans
  dead-letter (voulu — dead_letter appartient à `fail`).
- **`scheduler_state`** (KV) : `cycle_index` (n'avance qu'en FIN de cycle → kill à mi-cycle
  rejoue), `last_full_cycle_at`, `channel_backoff` (JSON). UPSERT atomique. Si D/E ajoutent
  de l'état de scheduling, même table KV.
- **Config two-file** : `crawler.yaml` (politique, versionné) + `local.yaml` (secrets/instances,
  GITIGNORÉ ; `local.example.yaml` est le modèle). D/E étendent `crawler.yaml`.
- **Contrat d'erreur dans les PORTS** (DÉCISION 1) : l'application catch `MuleUnreachableError`/
  `MuleSearchFailedError`/`RepositoryError` SANS importer un adapter. Tout nouvel use-case suit
  la même règle. `EcAuthError` est HORS contrat de boucle (problème de config → fail-fast).
- **Déterminisme** : tout temps/hasard/sleep passe par `Clock`/`Rng` injectés. Aucun
  `datetime.now`/`random`/`asyncio.sleep` direct dans la logique métier (seuls les adapters
  `AsyncioClock`/`SeededRng` les portent). Les tests utilisent de faux Clock/Rng → zéro flaky.

## 5. Pièges appris (CE jalon — les deux étaient des bugs transverses INVISIBLES en unitaire)

- **Un fake peut masquer une précondition de l'adapter réel.** `FakeMuleClient.network_status()`
  n'avait pas de garde de transport, alors que le vrai `AmuleEcClient` exige `connect()` d'abord.
  Résultat : le cycle lisait le statut sur des clients **jamais connectés** → crash en prod, vert
  en unitaire. **Attrapé SEULEMENT par l'e2e contre un `amuled` réel.** Correctif : connexion au
  démarrage (composition root) + `_aggregate_coverage` TOLÈRE `MuleUnreachableError` (instance
  injoignable = non search-capable → le signal BLIND devient atteignable en prod, pas un crash) +
  `connect()` rendu **idempotent** (clé sur l'existence du transport ; re-handshake après un drop).
  **Leçon : l'e2e opt-in FAIT FOI — les fakes doivent modéliser les préconditions, ou l'e2e les
  expose.**
- **`asyncio.timeout` surveille le `loop.time()` RÉEL, aveugle au faux Clock.** Le délai d'arrêt
  enveloppait toute la boucle indéfinie → le crawler levait `TimeoutError` après
  `shutdown_deadline_seconds` (10 s par défaut) de marche NORMALE, sans aucun signal. Invisible
  car tous les tests utilisent `FakeClock.sleep` (zéro temps réel) et signalent en quelques ms.
  **Attrapé par la revue HOLISTIQUE.** Correctif : `asyncio.timeout(None)` (désarmé) + `reschedule`
  ARMÉ seulement à la demande d'arrêt → seul le teardown (unwind du TaskGroup + `aclose`) est borné,
  la marche steady-state est libre. **Leçon : tout mécanisme sur le temps RÉEL exige un test qui
  LAISSE le temps réel dépasser le seuil (un faux Clock ne le déclenchera jamais).**
- **Plus petit** : `effective_coverage` reçoit des booléens (le domaine ne connaît pas
  `NetworkStatus`) ; l'ordre des gardes compte (`not any` avant `all`, sinon `all([]) == True`
  rend une liste vide HEALTHY au lieu de BLIND). `keyword_pause` est « between not after »
  (N items → N−1 pauses) — anti-ban eD2k. Le `BackoffRegistry` est PARTAGÉ (une instance,
  writer unique sur l'event loop mono-thread → aucun verrou).

## 6. Méthode (bilan du jalon)

Subagent-driven (implémenteur frais/tâche) + revue spec puis revue qualité adversariale
(opus sur les tâches substantielles) + **revue holistique finale** + **e2e contre Docker
réel**. Chacun des deux derniers filets a attrapé UN bug critique que le gate par tâche (100 %
branch, mypy strict) ne pouvait pas voir — l'un parce que les fakes masquaient une précondition
réelle, l'autre parce que `asyncio.timeout` est sur le temps réel que les faux Clock n'avancent
pas. **Garde les deux : l'e2e réel et la revue holistique sont rentables à chaque jalon.** Les
revues par tâche ont aussi épinglé des invariants que le coverage ne voit pas (l'arête
`PersistenceError → RepositoryError`, la reconnexion-après-drop de `connect()`).
