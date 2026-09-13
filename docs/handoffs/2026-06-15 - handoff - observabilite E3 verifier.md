# Handoff — emule-indexer (Plan E.3 : observabilité du verifier)

> **Obsolete since 2026-09-13.** The subsystem described here left the project's scope.
> See `docs/specs/2026-09-13-scope-reduction-catalog-notify-download.md`.

> Continuation guide. Le plus récent des handoffs = point d'entrée. Lire aussi le précédent
> (`2026-06-15 - handoff - observabilite E2 crawler.md`) pour la chaîne crawler, et la spec
> `docs/superpowers/specs/2026-06-15-observability-design.md` (E-D1→E-D13) pour les détails.
> Le plan exécuté : `docs/superpowers/plans/2026-06-15-observability-e3-verifier.md`.

## 1. TL;DR

**Plan E.3 COMPLET — et avec lui le Plan E tout entier (E.1 + E.2 + E.3).**

Le verifier (`download_verifier`) dispose désormais de son observabilité minimale (E-D10) :
`VerifierMetrics` (counter `/verify` par verdict + histogramme durée), route `GET /metrics`
(Prometheus, registre dédié), instrumentation de `POST /verify` (chrono + log), mini-loader YAML
`obs_config.py` (`log_level` seul — `/metrics` toujours exposé sur le port du service), et
bootstrap logging deux-temps dans `__main__`. **Aucun comportement métier modifié. Frontière
de paquet préservée : zéro import `emule_indexer` dans verifier/src.** Gate vert sur les deux
paquets (verifier 100 % branch, 113 passed ; crawler 100 %, 733 passed) + ruff + mypy --strict.

5 commits :

| SHA | Tâche | Objet |
|-----|-------|-------|
| `afd7a44` | T1 | `VerifierMetrics` (registre dédié, counter par verdict, histogramme) |
| `85c5f73` | T2 | `/metrics` endpoint + instrumentation `/verify` (chrono + log) |
| `852c9ef` | T3 | mini-loader YAML `obs_config.py` (`log_level`, défaut INFO, fail-fast) |
| `95a573e` | T4 | bootstrap logging deux-temps dans `__main__` (`configure_logging`) |
| `05fb7b3` | T5 | config example + montage compose + scrape runbook + alignement spec |

**RECOMMANDATION : poser le jalon `v0.11.0-observability` (annoté, non poussé)** — le Plan E est
complet. Le contrôleur posera le tag après revue finale.

## 2. État vérifiable

Gate PAR PAQUET — six checks verts :

```bash
( cd packages/verifier && uv run pytest -q )   # 113 passed, 7 deselected, 100.00% branch
( cd packages/crawler  && uv run pytest -q )   # 733 passed, 11 deselected, 100.00% branch (inchangé)
uv run ruff check . && uv run ruff format --check . && uv run mypy   # 0 issue
uv run sqlfluff lint packages/crawler/src                             # inchangé
```

Vérifications complémentaires :

```bash
grep -rn "emule_indexer" packages/verifier/src   # seule la docstring obs_config.py (commentaire)
( cd packages/verifier && uv run pytest -m analysis_integration --no-cov )  # 7 passed (ffmpeg dispo)
docker compose config >/dev/null                 # compose.yaml parse + merge valide
```

## 3. Ce qui est livré (par fichier)

**Nouveaux (prod)**
- `packages/verifier/src/download_verifier/metrics.py` — `VerifierMetrics` : `CollectorRegistry`
  dédié, `Counter("emule_verifier_requests", ["verdict"])` (SANS `_total` — ajouté à l'exposition),
  `Histogram("emule_verifier_analysis_duration_seconds")`; méthode `observe(verdict, seconds)`.
- `packages/verifier/src/download_verifier/obs_config.py` — `ObsConfigError`, `ObservabilityConfig`
  (frozen dataclass, `log_level: str`), `load_observability(path) → ObservabilityConfig` ; valide
  parmi `{DEBUG,INFO,WARNING,ERROR,CRITICAL}`, défaut `"INFO"`, fail-fast sur niveau inconnu.
  N'importe RIEN de `emule_indexer`.

**Modifiés (prod)**
- `packages/verifier/pyproject.toml` — deps ajoutées : `prometheus-client>=0.21`, `pyyaml>=6.0.3`.
- `packages/verifier/src/download_verifier/app.py` — imports ajoutés (`logging`, `time`,
  `generate_latest`/`CONTENT_TYPE_LATEST`, `Response`, `VerifierMetrics`) ; `_logger` module-level ;
  `metrics_endpoint` (`GET /metrics`, retourne `generate_latest(metrics.registry)`) ; `verify_endpoint`
  récupère `metrics` via `request.app.state.metrics`, chronométre `verify_file`, appelle
  `metrics.observe(verdict, delta)`, logge le résultat ; `build_app` crée `VerifierMetrics()` →
  `state.metrics` et monte la route `/metrics`.
- `packages/verifier/src/download_verifier/__main__.py` — `configure_logging(env: Mapping[str,str])`
  : `basicConfig(format=…)` + `setLevel(INFO)` systématiquement, puis `setLevel(log_level)` si
  `VERIFIER_CONFIG` présent dans l'env ; `main()` appelle `configure_logging(os.environ)` avant
  `uvicorn.run` ; `if __name__ == "__main__":` reste `# pragma: no cover`.

**Nouveaux (tests)**
- `packages/verifier/tests/test_metrics.py` — `test_observe_increments_counter_and_histogram` (3
  observations, assertions sur `_total` et `_count`).
- `packages/verifier/tests/test_obs_config.py` — 5 tests couvrant toutes les branches : nominal,
  section absente, YAML non-dict, section non-dict, niveau invalide.

**Modifiés (tests)**
- `packages/verifier/tests/test_app.py` — 2 tests ajoutés : `test_metrics_endpoint_responds` (GET
  /metrics → 200, text/plain, corps contient la métrique) ; `test_verify_increments_request_counter`
  (monkeypatch `verify_file` → GET /metrics contient `emule_verifier_requests_total{verdict="clean"} 1.0`).
- `packages/verifier/tests/test_main.py` — 2 tests ajoutés : `test_configure_logging_default_info`
  (sans VERIFIER_CONFIG → INFO) ; `test_configure_logging_from_yaml` (YAML WARNING → WARNING).

**Config/compose**
- `config/verifier.example.yaml` — modèle public versionné (`observability.log_level: INFO`).
- `config/verifier.yaml` — copie de l'example, versionnée pour que le smoke démarre sans setup
  manuel.
- `compose.yaml` — service `verifier` : `VERIFIER_CONFIG: /config/verifier.yaml` dans
  `environment` ; `./config/verifier.yaml:/config/verifier.yaml:ro` dans `volumes`.

**Documentation**
- `docs/runbook-deployment.md` — section "Métriques Prometheus (scrape)" : réseaux de scrape,
  exemple `scrape_config` crawler (`ec`) et verifier (`verify-internal`).
- `docs/superpowers/specs/2026-06-15-observability-design.md` — alignement §5/§8/E-D10 : verifier
  n'a PAS de port métriques séparé, YAML porte `log_level` seul.

## 4. Pièges appris / points d'attention

- **GOTCHA prometheus client counter naming.** Le `Counter` se déclare SANS `_total` :
  `Counter("emule_verifier_requests", …)`. Le sample exposé l'aura : `emule_verifier_requests_total`.
  Les tests doivent utiliser `get_sample_value("emule_verifier_requests_total", {"verdict": …})`.
  Ne pas confondre le nom de déclaration et le nom du sample.
- **`basicConfig` idempotent en test.** Si le root logger a déjà des handlers (ce qui arrive
  fréquemment dans les suites de tests qui tournent en série), `basicConfig` devient no-op.
  L'implémentation appelle donc `setLevel(logging.INFO)` **séparément** après `basicConfig` pour
  garantir l'isolation entre tests — c'est plus robuste que `basicConfig(level=INFO)` seul.
- **Branches `isinstance` dans `load_observability`.** Le double garde `isinstance(raw, dict)` +
  `isinstance(section, dict)` génère 4 branches : YAML racine non-dict, section observability
  non-dict, section absente, cas nominal. Toutes doivent être testées pour 100 % branch. Les deux
  cas pathologiques (`null` racine, `observability: null`) nécessitent des tests dédiés.
- **`verify-internal: internal: true` immuable.** Toute modification de compose.yaml doit laisser
  ce drapeau intact — le verifier n'a pas d'egress (pas de clamav non plus, pour la même raison).
- **Montage `config/verifier.yaml` dans compose.** Le fichier est optionnel en prod (l'utilisateur
  le crée depuis `verifier.example.yaml`), mais le bind-mount dans compose.yaml l'attend. Le fichier
  versionné `config/verifier.yaml` (copie de l'example) permet au smoke de démarrer sans setup
  manuel, et peut être modifié librement en prod.

## 5. Architecture — observabilité verifier (E-D10)

```
POST /verify
   ├── start = time.monotonic()
   ├── verdict, real_meta, checks = verify_file(path, expected)
   ├── metrics.observe(verdict, monotonic() - start)   ← VerifierMetrics (state.metrics)
   ├── _logger.info("verify hash=… → verdict=…")
   └── JSONResponse(...)

GET /metrics
   └── generate_latest(metrics.registry)   ← CollectorRegistry dédié (state.metrics.registry)

__main__.configure_logging(env):
   basicConfig(format=...) + setLevel(INFO)
   if VERIFIER_CONFIG in env:
       load_observability(Path(config_path)) → setLevel(log_level)
```

Aucun événement de domaine, aucune notification apprise — le verifier est `internal: true`,
l'instrumentation est technique (compteurs, durée, log_level). Conforme à E-D10.

## 6. Plan E — COMPLET (E.1 + E.2 + E.3)

| Sous-plan | Contenu | Statut |
|-----------|---------|--------|
| E.1 — socle | Taxonomie événements, policy, dispatcher, PrometheusSink, AppriseNotifier, EdgeState | ✅ mergé main |
| E.2 — crawler | 5 use-cases instrumentés, CrawlerStarted, serveur métriques injectable | ✅ mergé main |
| E.3 — verifier | VerifierMetrics, /metrics, obs_config.py, bootstrap logging | ✅ mergé main |

**Jalon recommandé : `v0.11.0-observability`** (annoté, non poussé — à poser par le contrôleur
après revue finale) :
```bash
git tag -a v0.11.0-observability -m "Plan E complet : observabilité (logs+métriques+notifications) crawler+verifier"
```

## 7. Ce qui n'est PAS fait (follow-ups hors Plan E)

- **clamav** — seconde source `malicious` via signatures ; `freshclam` exige un egress en tension
  avec `internal: true` (slot réservé, non implémenté). Suivre après réflexion réseau.
- **Port-sync / High-ID** — lecture du port gluetun forwarded → EC, remplace glueforward abandonné ;
  full mode tourne en Low-ID jusqu'alors.
- **Per-child kernel ring (bwrap)** — isolation namespace `net=none` / seccomp / RO mounts par
  enfant d'analyse (code verifier, pas compose). Déjà déféré depuis Plan F.
- **`file_verifications` dédup** — duplicats at-least-once (record + complete sur deux DBs) déférés
  à la surface d'export/lecture.
- **Serveur Prometheus** — infra homelab, hors repo. On expose `/metrics`, on ne scrape pas.
- **Exposition réseau Prometheus** — le verifier est `internal: true`, scraper requiert soit un
  proxy soit exposer le port sur l'hôte (documenté dans le runbook, non bloquant).

## 8. Prochaine étape recommandée

**Poser le jalon `v0.11.0-observability`** après revue holistique du contrôleur, puis choisir
parmi les follow-ups (§7) selon les priorités. Piste naturelle : **clamav** (frontière réseau à
concevoir) ou **port-sync/High-ID** (amélioration de connectivité eMule).
