# Spec — emule-indexer : Plan E (observabilité — logs, métriques Prometheus, notifications apprise)

> **Sous-projet** : doter la stack (qui tourne et se déploie depuis Plan F) de ses **trois sorties
> d'observabilité** — logs structurés, métriques Prometheus, notifications apprise — conçues comme
> **trois faces d'une même chose** : *« un fait métier observable s'est produit, raconte-le ».*
> Aucun comportement métier ne change ; le pipeline crawl→download→verify est inchangé.
>
> Réfs : MVP design `2026-06-10-crawler-mvp-design.md` §13 (observabilité : logs + Prometheus +
> apprise), §6-7 (boucles de recherche), §9-10 (download/verify). Spec packaging
> `2026-06-14-packaging-design.md` (compose, réseaux `ec`/`verify-internal`/`egress`, `internal: true`
> du verifier — contraint l'exposition des métriques). Mémoire projet : « Sorties = logs + métriques
> Prometheus + notifications apprise ».

---

## 1. But & périmètre

**But** : instrumenter le **crawler** (`emule_indexer`) et le **verifier** (`download_verifier`) pour
qu'ils émettent logs, métriques et notifications, **sans toucher la logique métier** ni dégrader le
gate (100 % branch des deux paquets, `mypy --strict`, `ruff`). Côté crawler, la machinerie est une
**chaîne hexagonale propre** : *événement de domaine pur* → *politique pure* → *dispatcher adapter*
vers trois sinks. Côté verifier (microservice), une instrumentation **minimale** (pas de taxonomie).

**Dans le périmètre** :
- **Crawler** : taxonomie d'**événements de domaine** (`domain/observability/`), **politique pure**
  `describe(event) → Report` (sévérité / message / métrique / audience), port `Telemetry.emit`, et un
  **dispatcher** (`adapters/observability/`) routant vers 3 sinks (logging stdlib, Prometheus, apprise).
- **Métriques Prometheus** : un catalogue (compteurs / jauges / histogrammes), labels **eD2k vs Kad**
  là où le réseau a un sens. Exposition via `prometheus_client.start_http_server` sur un
  `CollectorRegistry` **dédié et injecté**.
- **Notifications apprise** : routées **par audience sémantique** (`COMMUNITY` / `OPERATIONS`) via les
  **tags** apprise, **pas par sévérité** ; alertes récurrentes **edge-triggered**.
- **Logging** : `logging` stdlib **enrichi** (messages + niveaux révisés), niveau piloté par
  `log_level` (config). Bootstrap `INFO` au démarrage puis **reconfiguration** après lecture du YAML.
- **Configuration TOUT YAML**, verifier compris : section `observability` dans les YAML déjà montés
  côté crawler ; un **mini-loader YAML** ajouté au verifier (frontière de paquet préservée).
- **Verifier** : `log_level` + `metrics` via son YAML, route **`GET /metrics`** (Starlette) avec
  métriques **techniques**, logging stdlib au bon niveau. **Pas** de taxonomie/notifications.

**Hors périmètre** (voir §12) :
- **Serveur Prometheus / Alertmanager / dashboards** — infra homelab, hors repo. On expose `/metrics`,
  on ne scrape pas.
- **Tracing distribué / OpenTelemetry** — non retenu (YAGNI).
- **Persistance de l'état d'alerte** — l'edge-trigger est in-process (cf. E-D8).
- **Isolation des notifications dans une tâche dédiée** — `emit` await la notif (cf. §6 / risques) ;
  bascule en queue asynchrone = follow-up si la latence gêne.
- **Métriques par-instance fines au-delà des labels listés** — YAGNI.

## 2. Décisions verrouillées (issues du brainstorm)

1. **E-D1 — un seul design, trois sorties d'un même fait.** Logs/métriques/notifs sont les trois
   consommateurs d'un **événement** unique émis une fois. Un seul point d'émission par fait.
2. **E-D2 — configuration TOUT YAML (crawler + verifier).** Pas de canal env parallèle. `log_level`
   est dans le YAML ; conséquence assumée : **bootstrap `logging.basicConfig(INFO)`** au tout début,
   puis **`setLevel`** une fois le YAML lu (sinon une erreur de parsing du YAML ne se logge pas au bon
   niveau). Secrets (URLs apprise) dans `config/local.yaml` (déjà gitignored, contient déjà l'EC
   password) ; réglages non-secrets dans `config/crawler.yaml` (versionné).
3. **E-D3 — chaîne en 3 couches, crawler-only.** (a) `domain/observability/events.py` = dataclasses
   gelées, union taguée, **champs métier purs** ; (b) `domain/observability/policy.py` =
   `describe(event) → Report` **match exhaustif** (`assert_never`) — seul endroit qui décide
   sévérité/message/métrique/audience ; (c) `adapters/observability/` = dispatcher I/O. Le domaine
   n'importe ni `logging`, ni `prometheus_client`, ni `apprise`. **Sévérité = enum DOMAINE**
   (`Severity`), traduit en niveau `logging` par l'adapter.
4. **E-D4 — frontière de l'événement.** Un `Event` = **fait métier saillant** (mérite une métrique,
   et/ou une notif, et/ou un log INFO+). Le **bruit de débogage fin** reste du `logger.debug(...)`
   **local**, hors taxonomie. La demande « enrichir le logging » se scinde donc en deux registres :
   événements (saillants, 3 sorties) + logs stdlib locaux (narratif/debug), tous deux pilotés par le
   même `log_level`.
5. **E-D5 — taxonomie figée** (cf. §4) : couvre recherche, observations/décisions, download,
   vérification, jauges échantillonnées, cycle de vie.
6. **E-D6 — labels réseau eD2k/Kad.** Dérivés de `SearchChannel` (`GLOBAL`→`ed2k`, `KAD`→`kad`),
   connus **par construction** (la recherche est lancée par canal). Label `network` **uniquement** sur
   ce qui a un sens réseau (cf. §5) ; **pas** sur les cycles globaux, l'injoignabilité d'instance, ni
   download/vérification.
7. **E-D7 — notifications par audience, pas par sévérité.** `Audience` = enum domaine
   `COMMUNITY` (la communauté Discord du lost media) / `OPERATIONS` (l'admin homelab). Un événement
   peut viser **plusieurs audiences** : `Report.audiences: frozenset[Audience]` (vide = aucune notif) ;
   le dispatcher route chaque audience via les **tags apprise**. Exemple canonique : `malicious`
   (WARNING) → `{OPERATIONS}` ; `clean` (INFO) → `{COMMUNITY}` ; `CrawlerStarted` →
   `{COMMUNITY, OPERATIONS}` — **le consommateur décide, pas la gravité**. **Toute notification inclut
   l'ID d'instance** (le `node_id` du nœud), ajouté par l'adapter (pas par la policy) : indispensable
   côté COMMUNITY (réseau **distribué** de chercheurs — savoir *quel* nœud a trouvé quoi), utile aussi
   en OPERATIONS.
8. **E-D8 — alertes récurrentes edge-triggered, état porté par l'événement (raffinement).** Les faits
   de panne récurrents portent un champ **`first_occurrence: bool`** calculé **dans l'application**
   (qui détient l'historique d'état inter-itérations). La policy mappe l'**audience sur ce champ**
   (notif seulement à la transition) ; la **métrique s'incrémente à chaque occurrence** (Prometheus
   veut l'état brut). Conséquence : **aucun état caché dans l'adapter**, la policy reste pure. (Évolue
   le « petit état dans le dispatcher » évoqué au brainstorm — plus propre, même effet anti-spam.)
   Événements de rétablissement (`*Recovered`) optionnels, même mécanique.
9. **E-D9 — exposition.** Crawler : `prometheus_client.start_http_server(port, registry=<dédié>)`
   (thread daemon, zéro dep web ajoutée) ; `CollectorRegistry` **injecté** dans le sink ET le serveur
   → tests sur registre jetable (`get_sample_value`), seul le bind est `# pragma: no cover`. Verifier :
   route **`GET /metrics`** (`generate_latest` sur son registre).
10. **E-D10 — verifier minimal.** Pas d'events/policy/dispatcher. Mini-loader YAML (`log_level` +
    `metrics`), `/metrics` technique, logging stdlib. N'importe **rien** de `emule_indexer`.
11. **E-D11 — dépendances.** crawler : `prometheus-client`, `apprise`. verifier : `prometheus-client`,
    `pyyaml`. (Pas d'`apprise` côté verifier : pas d'événement métier + `internal: true` = pas d'egress.)
12. **E-D12 — zéro régression métier.** Les boucles `application/` gagnent des appels `await
    telemetry.emit(...)` ; aucune décision de crawl/download/verify ne change. 100 % branch maintenu.
13. **E-D13 — notifications à timeout borné, pas de queue.** `emit` reste async ; chaque notif est
    `await asyncio.wait_for(..., timeout=T)` + absorption (`TimeoutError`/exception → warning, crawl
    poursuit). `T` réglable (défaut ~5 s). **Pas** de worker/queue de notification : les événements
    notifiables sont **rares par conception** (trouvailles + transitions edge-triggered), le chemin
    chaud (`ObservationRecorded`) n'est jamais notifiable → l'attente bornée ne mord quasiment jamais.
    La queue fire-and-forget est un **follow-up** si les notifs deviennent fréquentes (YAGNI).

## 3. Architecture — les trois couches (crawler)

```
application/ (async)                  domain/observability/ (pur)         adapters/observability/ (I/O)
  search_worker / run_*_cycle  ──emit(Event)──▶  events.py (union taguée)
                                                 policy.describe(Event)──▶  ObservabilityDispatcher
                                                   → Report(severity,            ├─ logging stdlib  (Severity→level)
                                                      message, metric,           ├─ PrometheusSink  (MetricInstruction)
                                                      audience)                  └─ AppriseNotifier (audience→tag)  [async]
```

Modules :
- **`domain/observability/events.py`** — `type Event = … | … `, une dataclass gelée par fait (champs
  métier seulement). Inclut le champ `first_occurrence` sur les faits edge-triggered (E-D8).
- **`domain/observability/policy.py`** — `class Severity(Enum)` (`DEBUG/INFO/WARNING/ERROR`),
  `class Audience(Enum)` (`COMMUNITY/OPERATIONS`), `class MetricName(StrEnum)`,
  `@dataclass(frozen=True) MetricInstruction(name: MetricName, kind: Literal["inc","set","observe"], labels, value: float)`,
  `@dataclass(frozen=True) Report(severity, message, metrics: tuple[MetricInstruction, ...], audiences: frozenset[Audience])`
  (`metrics` = 0..N — un événement peut alimenter **plusieurs** métriques, ex. `SearchCycleCompleted`
  = compteur + histogramme ; `audiences` vide = aucune notif), et `def describe(event: Event) -> Report`
  — **match exhaustif** (`case _: assert_never(event)`).
- **`ports/telemetry.py`** — `class Telemetry(Protocol): async def emit(self, event: Event) -> None`.
  Ports des sinks : `MetricsSink.apply(MetricInstruction) -> None` (sync),
  `Notifier.notify(audience: Audience, body: str, severity: Severity) -> None` (async). Le **corps**
  = `Report.message` ; le **titre** + le `NotifyType` apprise sont **dérivés de la sévérité/audience
  par l'adapter** (la policy ne rend qu'un seul texte, pas un title/body séparés).
- **`adapters/observability/dispatcher.py`** — `ObservabilityDispatcher(Telemetry)` : `emit` (async)
  appelle `describe`, **toujours** logge (`logger.log(_LEVELS[r.severity], r.message)`) et applique
  **chaque** `MetricInstruction` de `r.metrics`, **puis** pour chaque audience de `r.audiences` :
  `await asyncio.wait_for(notifier.notify(...), timeout=T)` dans un `try/except` qui **absorbe
  `TimeoutError` + toute exception** (warning loggé). Un canal mort/lent/qui hang coûte **au pire `T`**
  et ne casse jamais le crawl (E-D13).
- **`adapters/observability/prometheus_sink.py`** — déclare le catalogue (Counter/Gauge/Histogram)
  sur le `CollectorRegistry` injecté, indexé par `MetricName` ; `apply` dispatche sur `kind`.
- **`adapters/observability/apprise_notifier.py`** — `apprise.Apprise` chargé depuis la config
  (URLs + tags par audience), **paramétré par le `node_id`** (ID d'instance) ; `notify` préfixe le
  corps de l'ID d'instance puis appelle `await apobj.async_notify(body, title, tag=<audience>)`.

## 4. Taxonomie des événements (E-D5)

Notation : **Nom**(champs) — *Sévérité* ; `métrique` ; **audience** (— = aucune).

**Recherche** (`run_search_cycle` / `search_worker`)
- **SearchCycleCompleted**(cycle_index, duration_seconds) — INFO ; `emule_search_cycles_total` inc +
  `emule_search_cycle_duration_seconds` observe ; — . *(Le volume d'observations est porté par
  `emule_observations_total{network}` — pas de `n_observations` à agréger cross-worker.)*
- **SearchExecuted**(network, n_results) — DEBUG ; `emule_searches_total{network}` inc ; —.
- **InstanceUnreachable**(instance) — WARNING ; `emule_mule_unreachable_total{instance}` inc ; —
  (la notif d'ensemble est portée par AllInstancesBlind).
- **SearchFailed**(instance, network) — WARNING ; `emule_search_failures_total{network}` inc ; —.
- **AllInstancesBlind**(first_occurrence) — WARNING ; `emule_search_blind_cycles_total` inc ;
  **OPERATIONS si first_occurrence**.

**Observations & décisions** (`record_observations`)
- **ObservationRecorded**(network) — DEBUG ; `emule_observations_total{network}` inc ; —.
- **DecisionRecorded**(target_id, tier) — INFO ; `emule_decisions_total{tier}` inc ;
  **COMMUNITY si tier == download**.

**Download** (`run_download_cycle`)
- **DownloadQueued**(target_id) — INFO ; `emule_downloads_queued_total` inc ; —.
- **DownloadCompleted**(target_id, ed2k_hash) — INFO ; `emule_downloads_completed_total` inc ;
  **COMMUNITY**.
- **PromotionFailed**(ed2k_hash) — WARNING ; `emule_promotion_failures_total` inc ; —.

**Vérification** (`run_verification_cycle`)
- **VerificationCompleted**(target_id, verdict) — la policy branche sur `verdict` :
  `clean` → INFO / **COMMUNITY** ★ ; `malicious` → WARNING / **OPERATIONS** ;
  `suspicious` → INFO / **OPERATIONS** ; `error` → WARNING / — . Toujours
  `emule_verifications_total{verdict}` inc. (Verdict inconnu → traité comme `error`, branche testée.)
- **VerifierUnavailable**(first_occurrence) — WARNING ; `emule_verifier_unavailable_total` inc ;
  **OPERATIONS si first_occurrence**.

**Jauges échantillonnées** (catégorie `set`)
- **ConnectedInstancesSampled**(network, count) — DEBUG ; `emule_connected_instances{network}` set ;
  émis en tête de cycle (la couverture est déjà agrégée là).
- **VerificationQueueDepthSampled**(count) — DEBUG ; `emule_verification_queue_depth` set ; **requiert
  une lecture `count_pending()` sur le repo de queue** (ajout d'une méthode de lecture simple ;
  cf. §11). Si jugé trop coûteux à l'implémentation → reporté en follow-up (la jauge est la seule à en
  dépendre).

**Cycle de vie** (`CrawlerApp`)
- **CrawlerStarted**(mode) — INFO ; `emule_crawler_up` set=1 ; **{COMMUNITY, OPERATIONS}** — message du
  type *« 🟢 Instance {node_id} en ligne (mode {mode}) »* (la communauté voit un chercheur de plus,
  l'ops voit le mode). L'ID d'instance est de toute façon préfixé par l'adapter (E-D7).

**Métriques process** (CPU/mémoire/GC) — fournies **automatiquement** par `prometheus_client`
(collectors par défaut sur le registre), **pas** d'événement.

## 5. Métriques Prometheus — catalogue & labels (E-D6)

Préfixe de namespace **`emule_`**. Conventions Prometheus respectées (`_total` pour les compteurs,
unité `_seconds`/`_bytes` en suffixe).

| Métrique | Type | Labels |
|---|---|---|
| `emule_search_cycles_total` | counter | — |
| `emule_search_cycle_duration_seconds` | histogram | — |
| `emule_searches_total` | counter | `network` |
| `emule_observations_total` | counter | `network` |
| `emule_search_failures_total` | counter | `network` |
| `emule_mule_unreachable_total` | counter | `instance` |
| `emule_search_blind_cycles_total` | counter | — |
| `emule_decisions_total` | counter | `tier` |
| `emule_downloads_queued_total` | counter | — |
| `emule_downloads_completed_total` | counter | — |
| `emule_promotion_failures_total` | counter | — |
| `emule_verifications_total` | counter | `verdict` |
| `emule_verifier_unavailable_total` | counter | — |
| `emule_connected_instances` | gauge | `network` |
| `emule_verification_queue_depth` | gauge | — |
| `emule_crawler_up` | gauge | — |

`network ∈ {ed2k, kad}` (mappé depuis `SearchChannel`). `tier` = les tiers du moteur de matching.
`verdict ∈ {clean, suspicious, malicious, error}`.

**Verifier** (instrumentation directe, pas d'événements) :
- `emule_verifier_requests_total{verdict}` (inc à chaque `/verify` traité),
- `emule_verifier_analysis_duration_seconds` (histogram),
- collectors process par défaut.

## 6. Notifications apprise (E-D7, E-D8)

- **Config** : une liste d'entrées `{url, tag}` (tag ∈ `community`/`operations`) chargée dans
  `apprise.Apprise.add(url, tag=tag)`. `notify` cible `tag=<audience.value>`.
- **Routage par audience** (cf. §4) : le dispatcher itère sur `Report.audiences` (0, 1 ou 2). Le
  **corps** = `Report.message`, rendu par la policy depuis les champs de l'événement (français lisible
  — ex. *« ✅ Épisode candidat vérifié SAIN : S2E062A »*, *« ⚠️ Verifier injoignable »*). Le **titre**
  + le `NotifyType` apprise (info/success/warning) sont **dérivés de la sévérité/audience par
  l'adapter** (cf. §3).
- **Identité d'instance** : l'`AppriseNotifier` est paramétré par le `node_id` et **préfixe le corps de
  toute notification** (ex. *« [titar-node-1] ✅ … »*) — requis côté COMMUNITY (réseau distribué),
  appliqué aussi en OPERATIONS. Si `node_id` est absent de la config, repli sur un libellé court
  (hostname) — détail au plan.
- **Anti-spam edge-triggered** : porté par `first_occurrence` dans l'événement (E-D8). Le dispatcher
  ne tient aucun état d'alerte.
- **Robustesse (E-D13)** : chaque notif est `await asyncio.wait_for(..., timeout=T)` (défaut ~5 s,
  réglable) avec **échec/timeout absorbé + loggé** ; un canal en panne, lent ou qui hang coûte au pire
  `T` et ne bloque jamais le crawl. Pas de queue (notifs rares par conception — §13).
- **Mode dégradé** : si aucune URL n'est configurée, l'`AppriseNotifier` est un **no-op** (les logs et
  métriques fonctionnent seuls). Pas d'erreur au boot.

## 7. Logging (E-D2, E-D4)

- `logging` stdlib conservé (loggers module-level existants), **enrichi** : messages ajoutés, niveaux
  révisés selon la sévérité des faits. Format inchangé (`asctime level name message`).
- **Bootstrap en deux temps** dans les points d'entrée (`emule_indexer.__main__`,
  `download_verifier.__main__`) : `basicConfig(INFO)` **avant** toute lecture de config (pour que les
  erreurs de chargement se loggent), puis `logging.getLogger().setLevel(<log_level YAML>)` une fois la
  config lue et validée.
- `log_level ∈ {DEBUG, INFO, WARNING, ERROR, CRITICAL}` ; valeur inconnue → `ConfigError` (fail-fast,
  style maison).

## 8. Configuration (tout YAML — E-D2)

**Crawler** — section optionnelle, parsée par `parse_crawler_config` (dataclass gelée + fail-fast) :
```yaml
# config/crawler.yaml (versionné — non secret)
observability:
  log_level: INFO
  metrics:
    enabled: true
    port: 9090
# config/local.yaml (gitignored — secrets, à côté de l'EC password)
observability:
  notifications:
    - { url: "discord://…", tag: community }
    - { url: "discord://…", tag: operations }
```
Nouvelles dataclasses gelées : `ObservabilityConfig(log_level, metrics: MetricsConfig | None,
notifications: tuple[NotificationTarget, ...], notification_timeout_seconds: float = 5.0)`,
`MetricsConfig(enabled, port)`, `NotificationTarget(url, tag: Audience)`. Le `timeout` (non secret)
se règle dans `crawler.yaml` ; les URLs (secrètes) restent dans `local.yaml`. Section absente → observabilité minimale (logs INFO, pas de
serveur métriques, notifier no-op). L'**ID d'instance** des notifications réutilise le `node_id`
**existant** de `local.yaml` (aucun nouveau champ) ; repli hostname si absent.

**Verifier** — nouveau **mini-loader YAML** propre au paquet (n'importe pas `emule_indexer`), monté en
volume comme les YAML du crawler ; expose `log_level` + `metrics: {enabled, port}` ; `AnalysisConfig`
existant (env) **inchangé**.

## 9. Exposition des métriques (E-D9)

- **Crawler** : `start_http_server(port, registry=<CollectorRegistry dédié>)` lancé par `CrawlerApp`
  quand `metrics.enabled`. Thread daemon (meurt avec le process — pas de teardown explicite requis).
  Le `CollectorRegistry` est créé une fois, passé au `PrometheusSink` **et** au serveur.
- **Verifier** : route `GET /metrics` ajoutée à `build_app` (`generate_latest(registry)`,
  `CONTENT_TYPE_LATEST`).
- **Déploiement** (runbook, pas un bloqueur) : le verifier est sur `verify-internal` (`internal:true`)
  et le crawler derrière gluetun — un Prometheus externe doit **rejoindre le réseau de scrape** ou un
  port doit être exposé. Documenté au runbook ; le serveur Prometheus reste hors repo.

## 10. Câblage (composition)

- **`CrawlerApp`** construit le `CollectorRegistry`, le `PrometheusSink`, l'`AppriseNotifier`
  (**paramétré par le `node_id`** de `local_config`), le `ObservabilityDispatcher` (= `Telemetry`),
  démarre le serveur métriques si activé, **injecte `telemetry`** dans les use-cases (`search_worker`,
  `run_search_cycle`, `record_observations`, `run_download_cycle`, `run_verification_cycle`). Émet
  `CrawlerStarted(mode)` au boot.
- **Propagation du `network`** : `search_worker` connaît le `SearchChannel` de sa tâche → le passe à
  `record_observation` pour que `ObservationRecorded(network=…)` soit exact (sans toucher la
  persistance). `SearchExecuted` et `SearchFailed` portent aussi ce canal.
- **État de transition** (E-D8) : `run_verification_cycle` tient `verifier_was_available` ;
  `run_search_cycle` tient `coverage_was_ok` — variables inter-itérations qui alimentent
  `first_occurrence`.
- **`download_verifier.app`** : `build_app` instrumente `/verify` (compteur + histogramme) et ajoute
  `/metrics` ; `__main__` lit le YAML (bootstrap-then-setLevel) avant `uvicorn.run`.

## 11. Tests & discipline du projet

- **TDD strict, 100 % branch des deux paquets** (inchangé). Chaque test `-> None`, params typés,
  `mypy --strict` sur `src` **et** `tests`.
- **`describe` (policy)** : un test par variante d'événement + chaque branche `verdict` /
  `first_occurrence` (l'`assert_never` est `# pragma: no cover`). C'est le cœur testable, **pur**.
- **Dispatcher** : fakes `RecordingMetricsSink` / `RecordingNotifier` + un logger capturé (`caplog`) →
  asserte log + métrique + notif **par audience** (0/1/2 canaux selon `audiences`). **Échec ET timeout**
  de notif absorbés : les deux branches testées (un faux notifier qui lève / qui dépasse `T`).
- **AppriseNotifier** : asserte aussi que le **`node_id` est préfixé** au corps (et le repli hostname).
- **PrometheusSink** : `CollectorRegistry()` jetable, `apply(...)`, relecture via
  `registry.get_sample_value(name, labels)` → 100 % sans socket. `start_http_server` (le bind) =
  `# pragma: no cover`.
- **AppriseNotifier** : `apprise.Apprise` avec un faux service / monkeypatch d'`async_notify` → asserte
  le `tag` ciblé ; no-op si aucune URL.
- **Use-cases** : les fakes `application/` existants gagnent un `RecordingTelemetry`
  (`async def emit`) ; on asserte la **séquence d'événements émis** (y compris `first_occurrence` sur
  les transitions). Repos/engines réels sur `tmp_path`, telemetry/clients fakes (pattern existant).
- **`count_pending()`** (si retenu pour la jauge) : repo réel SQLite, round-trip.
- **Verifier** : `/metrics` testé via `httpx.ASGITransport` (pattern existant) ; instrumentation
  `/verify` assertée sur le registre jetable.
- **Aucun nouveau marqueur d'intégration** : tout est testable en unitaire (pas de Docker, pas de
  réseau réel). Le smoke `compose_integration` existant peut gagner une assertion *« /metrics répond »*
  (optionnel, non bloquant).

## 12. Hors-périmètre / reporté (explicite)

- **Serveur Prometheus, Alertmanager, Grafana** — infra homelab, hors repo.
- **OpenTelemetry / tracing** — non retenu.
- **Queue de notifications asynchrone dédiée** — `emit` await la notif (rare) ; bascule si la latence
  gêne (§13).
- **Persistance de l'état d'alerte edge-trigger** — in-process ; après redémarrage, une panne en cours
  re-notifie une fois (acceptable).
- **`emule_quarantine_bytes`** (usage disque quarantaine) — souhaitable mais nécessite un `stat` du
  volume ; follow-up si besoin de surveiller la place.
- **Routage apprise plus fin que 2 audiences** — extensible (les tags le permettent), non requis.

## 13. Risques & notes

- **Latence des notifications (E-D13)** : `emit` await `wait_for(notify, T)`. Les événements
  notifiables sont **rares** (trouvailles + transitions edge-triggered) et le chemin chaud n'est jamais
  notifiable → l'attente bornée (≤ `T`) ne mord quasiment jamais. Si les notifs devenaient fréquentes,
  basculer en queue fire-and-forget (follow-up, §12).
- **Thread du serveur métriques** : `start_http_server` ouvre un thread daemon hors asyncio ; le
  `CollectorRegistry`/les collectors sont thread-safe. Pas de teardown explicite (daemon).
- **Exhaustivité `network`** : repose sur le fait que la recherche est lancée **par canal**
  (`start_search(keyword, channel)`) — vérifié. Une observation est attribuée au canal de la recherche
  qui l'a produite (un même fichier vu sur les deux réseaux compte une fois par réseau — sémantique
  voulue : activité de découverte par réseau).
- **`first_occurrence`** : exige un état inter-itérations dans deux boucles (verif, search). Petit,
  local, déjà dans l'esprit des registres de backoff existants. Au boot, l'état initial = « sain » →
  la première panne notifie.
- **Verdict inconnu** dans `VerificationCompleted` : la policy a une branche défensive (→ `error`),
  testée, pour ne pas dépendre de la seule discipline du contrat verifier.
- **Duplication crawler/verifier** (lecture YAML + montage Prometheus) : assumée, prix de la frontière
  de paquet (le verifier n'importe rien de `emule_indexer`).
