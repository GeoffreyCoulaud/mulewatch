# Spec — emule-indexer : Orchestration des recherches (Plan C)

> Sous-projet du MVP crawler (voir `2026-06-10-crawler-mvp-design.md`, §4, §6, §13, §14).
> Validé avec Geoffrey le 2026-06-12. Jalon visé : `v0.7.0-orchestration`.
> Éclairé par les jalons `v0.5.0-ec-adapter` (le client EC est mono-recherche FCFS) et
> `v0.6.0-data-model` (repos sync, writer unique, file de tâches).

## 1. Contexte & objectif

Le moteur décide, l'adapter EC observe, le modèle de données se souvient — mais rien ne
les fait **tourner ensemble**. Ce sous-projet construit la **boucle** : générer des
mots-clés depuis `targets.yaml`, lancer les recherches via EC, persister les observations,
les évaluer avec le moteur, persister les décisions, recommencer — en continu, de façon
déterministe et résiliente. À la fin du plan C, `python -m emule_indexer` est un crawler
qui tourne : pointé sur un `amuled` réel, il catalogue Keroro tout seul.

Ce plan introduit les **deux couches manquantes** de l'architecture cible (§4 MVP) :
`application/` (use-cases async) et `composition/` (assemblage + point d'entrée).

## 2. Périmètre

**Dans le scope :**
- Génération de mots-clés depuis les targets (larges + ciblés par segment).
- **Pool de travailleurs de recherche** : un par instance `amuled` configurée ;
  parallélisme natif borné par le nombre d'instances ; dégénère en boucle séquentielle
  à N=1.
- Cycle ordonnancé : statut réseau par instance → `effective_coverage` → mots-clés
  mélangés (seed `node_id`+index de cycle) → fan-out → drain → avance → sommeil jitteré.
- Backoff exponentiel + jitter **par (instance, canal)**, persisté dans `scheduler_state`.
- Pipeline par observation : mapping (déjà fait par le client) → `record_observation` →
  `evaluate` → (si verdict CHANGÉ) `record_decision` → **nudge** (signal in-process).
- Anti-redondance : on n'ré-append une décision que si le verdict change pour ce hash.
- Config en **YAML, deux fichiers** : `crawler.yaml` (politique, versionné) +
  `local.yaml` (endpoints EC + mots de passe + chemins des bases, **gitignoré**).
- Point d'entrée `python -m emule_indexer` (mode observateur), arrêt propre observable.
- Logging de base (stdlib, structuré, corrélé par id de cycle).

**Hors scope (différé) :**
- Consommation des décisions : auto-download (`download`, plan D), notifications
  (`notify`, plan E). Le plan C **observe, catalogue, décide, boucle — rien d'autre**.
  La table `match_decisions` (append-only) EST le journal d'événements ; D/E rejouent.
- Réconciliation des `downloads` avec la file aMule (plan D).
- Provisionnement de `server.met`/`nodes.dat` (déploiement, image Docker / plan F) — le
  plan C **surveille** seulement High ID / état Kad via EC.
- Observabilité riche : Prometheus, apprise, anti-fatigue (plan E). Ici : logs only.
- Packaging Docker/compose, glueforward (plan F).
- Sharding multi-daemon avancé : le pool EST le multi-instance natif ; répartir sur N
  `amuled` est en scope, mais l'orchestration de leur cycle de vie (démarrer/arrêter des
  conteneurs) est au plan F.

## 3. Décisions verrouillées

- **Couplage par la donnée + nudge in-process.** La boucle persiste la décision
  (append-only = fiabilité gratuite : un consommateur absent/crashé/futur rejoue depuis
  la table), PUIS signale un `DecisionSignal` (hub `asyncio.Event` par sujet) pour
  réveiller les consommateurs in-process immédiatement. Le polling de repli (défaut 5 s)
  reste le filet ; un nudge perdu est inoffensif. Le verifier (hors-process, plan D)
  reste sur la file `verification_tasks`.
- **Multi-instance natif (pool de travailleurs), une instance par `amuled`.** La
  limite « une recherche à la fois » vient d'`amuled` (`EC_OP_SEARCH_START` remplace la
  recherche courante), pas de notre archi. N travailleurs = N connexions EC = N daemons,
  recherches **réellement parallèles** via `asyncio.gather`. Le **téléchargement reste
  mono-instance** (plan D). À N=1, le pool est une boucle séquentielle — zéro complexité
  gratuite en MVP.
- **UN seul mécanisme de config : YAML, deux fichiers.** `crawler.yaml` (politique :
  cadences/jitter/backoff/budgets — versionné, partageable comme `targets.yaml`) et
  `local.yaml` (secret + machine-spécifique — gitignoré, `local.example.yaml` versionné ;
  monté par compose au plan F, compatible Docker secrets car c'est un fichier). Même
  loader `load_yaml` existant, validation fail-fast. Aucune variable d'environnement.
  (`ec_probe` garde son `EC_PROBE_PASSWORD` — outil de dev distinct.)
- **Repos en instances UNIQUES partagées.** La composition root construit UNE
  `SqliteCatalogRepository` et UNE `SqliteLocalStateRepository` (une connexion chacune),
  injectées dans tous les travailleurs et l'ordonnanceur (invariant « writer unique »
  §11). Repos **synchrones, appelés directement** (sub-ms ; pas de `to_thread` en MVP —
  documenté, réversible). Conséquence : les écritures DB sont sérialisées de facto sur
  l'event loop (aucune course), le seul vrai parallélisme est l'I/O réseau (`await` EC).
- **Backoff par (instance, canal)**, exponentiel + jitter, persisté dans
  `scheduler_state` (déviation assumée du §6 « par serveur » : EC ne révèle pas quel
  serveur throttle).
- **Anti-redondance par changement de verdict.** On lit le dernier verdict connu pour le
  hash (`CatalogRepository.last_decision`) et on n'`record_decision` que s'il change.
  Re-`record_observation` à CHAQUE fois (la re-observation périodique est le but, §6).
- **Déterminisme total.** Horloge, RNG, sleep injectables (ports). Le seed du shuffle
  dérive de `node_id`+index de cycle. Zéro flakiness — tout cycle est rejouable en test.
- **Domaine pur.** Le « cerveau » (keywords, shuffle, backoff, coverage) est en domaine
  pur (aucune I/O, aucun import réseau/horloge/logging). L'application orchestre des
  ports ; la composition assemble et logge.
- **Arrêt observable & borné** (voir §6).

## 4. Architecture & composants

```
src/emule_indexer/
├── domain/search/              # PUR (nouveau)
│   ├── keywords.py             #   generate_keywords(targets) → SearchKeyword (larges + ciblés)
│   ├── cycle.py                #   shuffle_for_cycle(items, node_id, cycle_index) — ordre seedé
│   ├── backoff.py              #   backoff_delay(attempt, base, cap) — math pure
│   └── coverage.py             #   effective_coverage(statuses) → Coverage (healthy/degraded/blind)
├── ports/
│   ├── clock.py                #   Clock (now/sleep async) + Rng — injectables
│   └── decision_signal.py      #   DecisionSignal (signal/wait) — hub de nudge in-process
├── application/                # NOUVELLE COUCHE (async, dépend des ports)
│   ├── record_observations.py  #   pipeline par observation : record_obs→evaluate→
│   │                           #     (si verdict changé) record_decision→nudge
│   ├── search_worker.py        #   possède 1 MuleClient, draine la queue, backoff par canal,
│   │                           #     reconnexion par instance
│   └── run_search_cycle.py     #   un cycle : statut→coverage→keywords→fan-out→drain→avance
├── adapters/
│   ├── config/                 #   parse_crawler_config / parse_local_config → gelés, fail-fast
│   ├── clock_asyncio.py        #   Clock réel (asyncio.sleep + datetime UTC) + Rng (random.Random)
│   └── decision_signal_asyncio.py  #   hub réel (asyncio.Event par sujet)
└── composition/                # NOUVELLE COUCHE — assemblage + entrée
    ├── app.py                  #   build pool MuleClient + repos UNIQUES + moteur + scheduler ;
    │                           #     AsyncExitStack ; logging ; arrêt observable
    └── __main__.py             #   python -m emule_indexer : charge config, run, signaux

config/
├── crawler.yaml                #   politique (versionné)
└── local.example.yaml          #   modèle (versionné) ; local.yaml gitignoré
```

**Règle de dépendance** (inchangée) : `domain` pur ; `ports` n'importe que le domaine ;
`application` dépend des ports ; `adapters`/`composition` implémentent et assemblent.
Le `CatalogRepository` gagne `last_decision(ed2k_hash) -> MatchDecision | None` (lecture,
pour l'anti-redondance) — extension du port existant.

### Le cycle (séquence)

```
Démarrage : charge configs (fail-fast) → ouvre les 2 bases (migrations vérifiées) →
            lit node_id → construit MatchingEngine (une fois) → connecte chaque MuleClient.
Cycle N :
  1. network_status() de CHAQUE instance → effective_coverage agrégé (loggé).
  2. generate_keywords(targets) → larges + ciblés ; shuffle_for_cycle(seed = node_id, N).
  3. enfile (mot-clé × canal) dans une asyncio.Queue ; saute les (instance,canal) en backoff.
  4. N travailleurs drainent en parallèle ; chacun, par item :
       start_search → polling borné (budget config) → fetch_results → pour chaque obs :
         record_observation (toujours) → evaluate → si verdict changé : record_decision + nudge.
       EcFailureError(canal) → backoff (instance,canal) ; EcConnectError → instance down + reconnexion.
  5. queue drainée → scheduler_state : index=N+1, last_full_cycle_at, backoffs.
  6. sleep jusqu'au prochain cycle (cadence − écoulé, jitter).
```

## 5. Configuration

`crawler.yaml` (politique, défauts raisonnables) :
- `cycle_interval`, `search_poll_budget`, `search_poll_interval`,
- `keyword_pause` (min/max pour le jitter inter-mots-clés),
- `backoff` (base, cap, facteur),
- `decision_poll_interval` (filet du nudge),
- `shutdown_deadline`.

`local.yaml` (machine + secret, gitignoré) :
- `amules: [{name, host, port, password}, …]` (≥ 1),
- `catalog_db_path`, `local_db_path`,
- `node_id` optionnel (override ; sinon celui de `local.db`).

Validation fail-fast au chargement (schéma, ≥ 1 instance, bornes cohérentes) → erreur
claire, refus de démarrer (§14).

## 6. Arrêt — observable & borné

Hérité de l'adapter EC : *le client signale, le plan C décide*. Le plan C porte la
reconnexion, le backoff, et l'arrêt.

- Signaux via `loop.add_signal_handler` (PAS `KeyboardInterrupt` : ce dernier peut
  préempter une fonction sync en plein `record_observation` ; le handler de boucle ne
  s'exécute qu'entre callbacks → ne tombe jamais au milieu d'une écriture).
- **Premier ^C / SIGTERM** : ligne humaine immédiate sur stderr (« Arrêt demandé — fin
  des recherches en vol, fermeture propre… (Ctrl-C à nouveau pour forcer) ») ; annule le
  `TaskGroup` (ordonnanceur + travailleurs). L'annulation atterrit au prochain `await`
  (I/O réseau) — jamais au milieu d'une écriture DB (repos sync). Résultats partiels jetés.
- **Progression rapportée** sur stderr étape par étape (« travailleur amule-1 arrêté »,
  « 2/3 connexions EC fermées », « bases fermées — sortie »).
- **Deuxième ^C** : escalade, « arrêt forcé », sortie immédiate.
- **Délai d'arrêt borné** (config, défaut quelques s) : dépassé → on force. Garantit que
  l'app ne *peut pas* paraître bloquée (le client EC a déjà un timeout par lecture).
- **Ownership** : la composition root (`app.py`) crée et ferme les ressources longue
  durée (pool de clients, **2 repos uniques**), via `AsyncExitStack`, APRÈS l'unwind
  complet du `TaskGroup` — donc plus aucun travailleur ne peut écrire au moment du close.
  Les travailleurs n'ferment rien. Append-only + écriture mono-statement = kill inoffensif.

## 7. Gestion d'erreurs & résilience (§14)

- **Instance EC injoignable / VPN tombé** (killswitch → amuled offline) : travailleur
  marque l'instance down, **backoff de reconnexion par instance** ; les autres continuent ;
  recherches en pause pour cette instance, pas de perte (re-observation au cycle suivant) ;
  jamais de crash.
- **Canal en échec** (`EcFailureError`) : backoff (instance, canal) + jitter ; cycle
  continue.
- **`effective_coverage`** agrégé (healthy/degraded/blind) : aucune instance HighID/Kad
  → `blind`, loggé fort. « Le process vit » ≠ « on peut trouver ».
- **Mauvaise observation** : écartée+comptée par le mapper ; `PersistenceError` sur une
  obs loggée, cycle continue (surfacée pour qu'un échec persistant soit visible).
- **Fail-fast** : config invalide ou base inouvrable au démarrage → refus de démarrer.
- **Reprise après crash** : `scheduler_state` (index, backoffs, last_full_cycle_at) relu ;
  index n'avance qu'en fin de cycle → un kill au milieu rejoue les mots-clés restants.

## 8. Stratégie de tests (TDD, 100 % branch, déterminisme)

- **Domaine pur** : keywords depuis fixtures ; shuffle reproductible (même seed → même
  ordre ; seeds différents → divergence inter-nœuds) ; backoff exponentiel plafonné ;
  coverage sur chaque combinaison de statuts.
- **Application** : `FakeMuleClient` (résultats scriptés, pannes injectables :
  `EcFailureError`/`EcConnectError`/timeout), **vrais** repos SQLite sur `tmp_path`,
  horloge/RNG/sleep injectables. Un cycle entier rejoué en ms : fan-out 2 travailleurs,
  un canal en backoff, une instance qui tombe puis se reconnecte, verdict changé →
  decision+nudge, verdict identique → pas de ré-append.
- **Arrêt** : SIGINT simulé en plein `fetch_results` → travailleurs annulés, repos fermés
  APRÈS, exit 0, aucune écriture à moitié ; double-^C ; dépassement de délai.
- **Nudge** : un test `await` le signal post-commit (le test EST le consommateur) — pas
  de code mort.
- **Bout-en-bout léger** (marqueur séparé, opt-in comme `ec_integration`) : boucle réelle
  contre l'`amuled` testcontainers, un cycle, arrêt propre.

## 9. Livrables & definition of done

1. Domaine search (keywords/cycle/backoff/coverage), ports (clock/rng/signal), application
   (record_observations/search_worker/run_search_cycle), adapters (config/clock/signal),
   composition (app/__main__) — tout testé, gate 5 checks vert.
2. `CatalogRepository.last_decision` (lecture anti-redondance).
3. `config/crawler.yaml` + `config/local.example.yaml` ; `local.yaml` gitignoré.
4. `python -m emule_indexer` démarre, tourne un cycle réel, s'arrête proprement et
   visiblement.
5. Tag annoté `v0.7.0-orchestration` (non poussé).

## 10. Questions laissées au plan d'implémentation

- Forme exacte des dataclasses de config et des défauts chiffrés de `crawler.yaml`.
- Signature précise de `last_decision` et de la requête SQL (dernier `decided_at` par
  hash) ; index nécessaire sur `match_decisions` ?
- Découpage fin du `SearchWorker` (reconnexion, backoff) en unités testables ; forme du
  `TaskGroup`/`AsyncExitStack` dans `app.py`.
- Format du logging structuré (id de cycle) et des lignes humaines d'arrêt.
- Marqueur du test bout-en-bout (`orchestration_integration` ?).
