# Handoff — emule-indexer (audit multi-agents de toute la codebase + 1ᵉʳ lot de corrections)

> **Pas de jalon taggé** : session d'**audit + bugfix**, pas de livraison de feature. Fait suite à
> `v0.16.0-webui`. Aucun tag posé (à décider : un `v0.16.1-audit-fixes` patch annoté serait légitime).
> **Rapport d'audit complet** : `agents/reference/2026-06-23-codebase-audit-findings.md` (entrée de référence).

## 1. TL;DR — où on en est

Audit **multi-perspectives de toute la codebase** (4 paquets + packaging/CI) mené via un workflow
multi-agents : **19 perspectives** de découverte (sécurité, confinement, logique, concurrence, codec
EC, persistance, frontière E-D13, types, config, tests, packaging, observabilité, docs-drift…) →
**vérification adversariale à 2 lentilles** par finding (reproduction + faux-positif) → synthèse.
**81 findings** collectés, **67 confirmés**. Le rapport est dans `agents/reference/`.

**1ᵉʳ lot de corrections : 11 commits**, tous en **TDD strict** (RED constaté avant chaque GREEN),
**100 % branch coverage** maintenu sur les 3 paquets touchés, `ruff`/`format`/`mypy --strict` verts à
chaque commit. Tous les findings **high** validés sont traités **sauf un** (`logic-search#0`, oublié
du cadrage — voir §6).

## 2. État vérifiable

```bash
( cd packages/matching && uv run pytest -q )   # 178 passed, 100% branch
( cd packages/crawler  && uv run pytest -q )   # 734 passed (+23 deselect), 100% branch
( cd packages/verifier && uv run pytest -q )   # 152 passed (+8 deselect), 100% branch
( cd packages/webui    && uv run pytest -q )   # 93 passed (intact, non touché)
uv run ruff check . && uv run ruff format --check . && uv run mypy   # verts (272 fichiers)
git log --oneline 15e0a0d..HEAD          # 11 fix(...) + le commit du rapport d'audit
```

## 3. Ce qui a été corrigé (11 commits, du plus ancien au plus récent)

| Commit | Finding | Résumé |
|---|---|---|
| `2b932e2` | logic-download#0/#1 (**high**) | `FilesystemQuarantine.promote` idempotent : `os.replace` consomme la source → un échec post-promote (enqueue/set_state) bloquait le fichier en `completed` pour toujours (`PromotionFailed` en boucle). Distingue « jamais promu » (lève) de « déjà promu » (no-op). Fake corrigé pour modéliser la consommation. |
| `6ab6250` | config-validation#0 (**high**) | Rejet de `all: []` / `any: []` (matchait TOUT en `tier=download`). |
| `f548e8a` | config-validation#2 | `coverage min`/`fuzz` bornés à `[0,1]` (hors borne → règle silencieusement inerte). |
| `82b2761` | config-validation#1 | Rejet de `attr_between` avec `min > max` (plage vide). |
| `eaa99dc` | test-gaps#2 | Cible sans `broadcast_date` + regex `{date_alt}` → crash au boot. Décision : **ignorer la règle datée pour cette cible** (`MissingDateError` → `AnyMatcher(())`). |
| `5cb2877` | config-validation#4 | `ENABLED_CHECKS` validé contre `KNOWN_CHECKS` (typo → fail-open antivirus). |
| `798f36d` | config-validation#3 | Planchers `> 0` sur timeout/rlimits/egress/header (`RLIMIT_*=-1` = `RLIM_INFINITY` désarmait la garde). |
| `b029888` | error-boundary#0 | Config verifier résolue/validée **une fois au boot** (`build_app(config)`, fail-fast à l'import) au lieu d'une résolution paresseuse par requête (→ 500 transitoire → dead-letter). |
| `09bcd56` | test-gaps#0 (**high**) | Port-sync : n'efface l'alerte mismatch que sur **High-ID confirmé** (`ed2k_high`), pas sur l'égalité de préférence (qui masquait un restart raté). |
| `f993b93` | sandbox#0 / concurrency#0 (**high**) | `verify_file` via `run_in_threadpool` : l'event loop reste libre pour `/health` + `/metrics` pendant l'analyse. |
| `03b0978` | concurrency#1 + sandbox#1 | Timeouts réconciliés : verifier `timeout_s` suit le budget CPU clamav (défaut 150 s) ; client crawler configurable (`VerifyConfig.client_timeout_seconds`, défaut 180 s, connect court). |

## 4. Décisions de conception prises avec Geoffrey (ne pas re-litiger)

- **test-gaps#0 (port-sync) — approche `ed2k_high`, PAS d'extension EC.** Investigation du source aMule
  (`/vendor`) : le port **réellement bound** n'est **pas exposé** par l'EC (`EC_OP_GET_CONNSTATE` n'a
  pas de champ port ; `EC_TAG_CONN_TCP_PORT` = la *préférence* via `thePrefs::GetPort()` ;
  `CListenSocket` n'expose pas son port). L'exposer exigerait de **patcher + recompiler le daemon aMule**
  (fork à maintenir). Choix retenu : se fier au **High-ID** (signal du but réel, déjà disponible).
- **test-gaps#2 — ignorer la règle datée** pour une cible sans date (vs rejeter la config / patcher l'EBNF).
- **error-boundary#0 — `build_app` prend une `AnalysisConfig` déjà résolue** ; le fail-fast vient de
  l'import module-level (`app = build_app(AnalysisConfig.from_env(os.environ))`, chargé par uvicorn).
- **C en 2 commits** (event loop, puis timeouts) à la demande.

## 5. Pièges appris (utiles au prochain lot)

- **Un fake infidèle masque le bug** — récurrent dans ce lot. `FakeQuarantine` ne modélisait pas la
  consommation de source par `os.replace` (→ logic-download#0 invisible) ; `FakePortPreferences` ne
  modélisait pas `set→get` (→ test-gaps#0 invisible). **Toujours faire correspondre le fake au contrat
  réel de l'adapter** avant d'écrire le test de régression — sinon le 100 % branch ment.
- **Tester `run_in_threadpool` par proxy de thread** : asserter que le travail tourne sur un
  `threading.get_ident()` ≠ celui de l'event loop (déterministe, pas de timing/hang).
- **Frontière de paquet** : `build_app(quarantine_dir)` → `build_app(config)` a cassé le **contract test
  crawler** (`tests/adapters/test_verifier_http.py`) et l'intégration `test_verify_loop.py` — seuls
  endroits autorisés à importer `download_verifier`. Pensé à les adapter.
- **Couverture 100 % branch d'une validation multi-champs** : itérer sur un tuple `(nom, valeur)` avec un
  seul `if value <= 0` (1 branche) plutôt qu'un `if` par champ (N branches à couvrir) — voir
  `_validate_positive` dans `config.py` du verifier.
- **Reprise de workflow après coupure quota** : le cache de reprise n'a **quasi pas mordu** (le journal
  partiel après « session limit » a fait re-tourner l'essentiel ~5,9 M tokens). Si re-coupure : prévoir
  que la reprise re-dépense beaucoup.

## 6. Reste à faire (priorisé)

1. **`logic-search#0` (HIGH, confirmé) — NON TRAITÉ, oublié de mon cadrage.** Le worker en backoff
   **draine et jette** les tâches restantes de la queue partagée en déploiement multi-instance → angle
   mort de couverture pendant que le cycle se déclare complet. C'était le point prioritaire #5 du résumé
   exécutif. **À prendre en premier.**
2. **Findings low/info** : `sandbox-confinement#2` (reap post-timeout sans timeout → hang worker si
   descendant échappé), `#3` (bornes `clamscan`), `#4` (symlink/`O_NOFOLLOW`), `input-trust#0` (`sniff(b'')`
   crashe le child — borné, verdict suspicious), `observability#2/#3` (métriques d'incident
   `child_outcome`/`responses{status}`), `config-validation#5` (`_parse_bool` rejette `True/On`), divers webui.
3. **Contestés** (`disputed`/`uncertain`) à trancher (documenter le choix délibéré OU corriger) :
   `error-boundary#3`, `logic-download#3`, `observability#5`, `security-network#2`, `test-gaps#3`.
   Détail dans la section « Contestés » du rapport.

## 7. PAS validé contre le vrai matériel

- **Port-sync (test-gaps#0)** : le comportement réel exige un **serveur Docker rootful** (le sandbox
  Docker Desktop rootless ne le permet pas) — fix couvert par tests unitaires uniquement.
- **Timeouts clamav (sandbox#1 / concurrency#1)** : l'efficacité des 120/150/180 s demande un **vrai média
  lent** + base de signatures réelle ; non reproductible hors déploiement.
- **Event loop libéré (sandbox#0)** : `run_in_threadpool` validé par proxy de thread, pas par charge réelle
  concurrente `/verify` + `/health`.

## 8. Pointeurs

- **Rapport d'audit** : `agents/reference/2026-06-23-codebase-audit-findings.md` (résumé exécutif, findings
  confirmés/contestés/écartés, couverture & angles morts, priorisation). C'est la source pour le prochain lot.
- Nouveaux paramètres de config introduits : `VerifyConfig.client_timeout_seconds` (crawler, déf 180 ;
  exemple ajouté dans `config/crawler/crawler.yaml`) ; `ANALYSIS_TIMEOUT_S_CLAMAV` (verifier, déf 150) ;
  `KNOWN_CHECKS` (verifier, enum fermé des checks).
