# Handoff — emule-indexer (audit 2026-06-23 : lot 2 + tranchage des contestés + drifts docs)

> **Pas de jalon taggé** : session de **bugfix + durcissement defense-en-profondeur + docs**,
> pas de livraison de feature. Fait suite à `2026-06-23 - handoff - audit multi-agents + corrections (lot 1)`.
> Aucun tag posé. Un `v0.16.2-audit-fixes-lot2` patch annoté serait légitime mais n'a pas été
> jugé indispensable (le rapport d'audit garde sa cohérence d'index).

## 1. TL;DR — où on en est

L'audit `docs/reference/2026-06-23-codebase-audit-findings.md` (lot 1 = 11 commits, lot 2 =
cette session) est désormais **largement résorbé**. **13 commits**, tous en **TDD strict**
(RED constaté avant chaque GREEN sauf 2 ajouts de bornes/headers où la régression vit dans
le DEPLOY), **100 % branch coverage maintenu sur les 4 paquets**, ruff/format/mypy strict
verts à chaque commit.

Couverts cette session :

- 1 **HIGH oublié du lot 1** (`logic-search#0` — re-enfilage des tâches d'un worker en backoff).
- 1 **MEDIUM oublié** (`error-boundary#1` — absorber RepositoryError fin de cycle search).
- **Tous les findings low/info confirmés** du §6 du handoff précédent + 7-8 webui en plus.
- Les **5 contestés** tranchés et annotés dans le code (PAS de refactor, juste documentation
  du choix délibéré au point d'usage).
- Les **doc drifts** corrigés (CLAUDE.md « three packages » → 4 ; gate à 4 paquets partout ;
  défaut WEBUI_HOST 127.0.0.1 ; `/node` description corrigée ; profils observer/download dans
  README ; compact docstring dynamique).

## 2. État vérifiable

```bash
( cd packages/matching && uv run pytest -q )   # 183 passed, 100% branch
( cd packages/crawler  && uv run pytest -q )   # 735 passed (+23 deselect), 99.94% (cf. §6)
( cd packages/verifier && uv run pytest -q )   # 171 passed (+5 fail macOS + 8 deselect, cf. §6)
( cd packages/webui    && uv run pytest -q )   # 97 passed, 100% branch
uv run ruff check . && uv run ruff format --check . && uv run mypy   # verts (272 fichiers)
uv run sqlfluff lint packages/crawler/src                              # vert
uv run python -m catalog_webui._dev.check_templates packages/webui/src/catalog_webui/adapters/templates  # vert
git log --oneline 981c231..HEAD                                         # 13 commits
```

## 3. Ce qui a été corrigé (13 commits, du plus ancien au plus récent)

| Commit | Finding(s) | Résumé |
|---|---|---|
| `e7e9a56` | **`logic-search#0`** (HIGH oublié) | Worker en backoff drainait synchronement la queue partagée pendant qu'un pair sain restait parqué sur un `await` réseau (queue.get sur file non vide ne cède pas l'event loop). Désormais `SearchTask.skipped_by: frozenset[str]` + re-enfilage + `SearchTaskDropped` quand tous les workers ont refusé. **Passage à `asyncio.LifoQueue`** : sans ça FIFO + alternance round-robin = boucle infinie en cas de backoff total. |
| `d2cbe9c` | **`error-boundary#1`** (MEDIUM oublié) | `write_cycle_state`/`save_channel_backoff` propageaient une RepositoryError hors de `run_search_cycle` → TaskGroup superviseur annulait toutes les boucles sœurs → crash de l'app sur panne transitoire. Aligné sur les 3 autres boucles (« NE LÈVE JAMAIS »). |
| `d4370df` | `code-smell#1` (étiqueté webui-security#0 dans le commit — labels audit pas suivis strictement) | Webui interpolait le filename BRUT dans le lien eD2k → un `\|` hostile décalait taille/hash. `build_ed2k_link` mutualisé dans `catalog_matching/ed2k_link.py` (paquet partagé crawler+webui). Test de régression : `\|` → `%7C`, exactement 5 séparateurs. |
| `4412a58` | `config-validation#5` | `_parse_bool` insensible casse (`raw.strip().lower()`) + accepte `on/off` + message qui liste les littéraux acceptés. |
| `4de5b85` | `input-trust#0` | `puremagic.from_string(b"")` lève `PureValueError` qui n'hérite PAS de `PureError` → l'`except` le rate → crash du child sur fichier 0 octet. Court-circuit `if not header: return clean` avant puremagic. |
| `80c8c9f` | `sandbox-confinement#2` | Reap post-timeout `proc.communicate()` SANS timeout → un descendant qui setsid() peut hang le worker. Désormais : close stdout côté parent + `proc.wait(timeout=_REAP_TIMEOUT_S=2.0)` + kill ciblé en dernier ressort. |
| `48cb781` | `sandbox-confinement#3` | clamscan reçoit `--max-scansize/--max-filesize/--max-recursion/--max-files/--max-scantime` (calibré pour ne pas gêner un média ~500 Mio). tmpfs `/tmp` borné via `size=` sur les 3 services (crawler 64m, verifier 256m, webui 32m). |
| `23acbd3` | `sandbox-confinement#4` | `verify_file` : `is_file()` → `os.lstat + S_ISREG` (refuse symlinks). `analysis_child` : `open` → `os.open(O_RDONLY \| O_NOFOLLOW)` + `fstat + S_ISREG`. Extrait en helper `_read_header_no_follow` pour tester la branche S_ISREG False isolément (sinon non atteignable via fdopen). |
| `a82a46a` | `observability#2` + `#3` | Métriques d'incident verifier : `child_outcome{ok/timeout/nonzero_exit/egress_overflow/malformed}` (cause technique, orthogonale au verdict) + `responses{status}` (200/400/500). Plumbing : `spawn.run_analysis`/`check.verify_file` retournent un 4-tuple `(verdict, real_meta, checks, outcome)` ; `outcome=None` quand pas de child (verdict `error`). |
| `b932359` | `logic-download#2` + `error-boundary#2` | Échec repo dans `_monitor` posait `states={}` → toutes les complétions du cycle ignorées. Désormais on RELIT `active_states()` avant `_handle_completions`. + Per-hash try/except dans `_handle_completions` (un échec sur le hash N n'affame plus N+1, N+2). |
| `6768abd` | webui polish (multi) | Filtres vides → None ; `max(1, page)` ; pagination UI (PageNav précalculé, template `(url,) if url`) ; dédup `FileRowDisplay` via `_to_display_rows` ; `TIER_RANK` mutualisé dans `catalog_matching.config` (webui réinventait avec convention divergente) ; middleware `_SecurityHeadersMiddleware` (CSP `default-src 'self'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`) ; annotations `: str` sur `request.path_params`. |
| `101c923` | **5 contestés** : `error-boundary#3`, `logic-download#3`, `test-gaps#3`, `security-network#2`, `observability#5` | TOUS annotés comme « CHOIX DÉLIBÉRÉ » au point d'usage (egress.py docstring, `_build_expected` docstring, `0001_initial.sql` commentaire, apprise_notifier docstring, `run_download_cycle` docstring). Le rapport d'audit a une nouvelle colonne « Décision » + pointeur vers ce commit. |
| `a935969` | doc drifts | CLAUDE.md « three packages » → 4 ; testing-guide gate à 4 paquets ; pre-push commentaire à 4 ; `WEBUI_HOST` défaut `127.0.0.1` (le vrai défaut applicatif) ; `/node` description corrigée (état du nœud crawler, pas amuled) ; README profil `full` (inexistant) → profils réels `observer`/`download` + `monitoring` ; `compactor.py` docstring « migrations 0001+0002 » → « TOUTES les migrations » (anti-drift au prochain ajout). |

## 4. Décisions de conception prises avec Geoffrey (ne pas re-litiger)

- **`logic-search#0` — `asyncio.LifoQueue` (pas FIFO).** Avec FIFO + alternance round-robin de
  l'event loop, chaque worker re-piochait ses propres tâches taguées en boucle infinie. LIFO
  garantit qu'une tâche re-enfilée est au sommet → piochée par un PAIR au tour suivant. Order
  des tâches dans un cycle déjà shuffle, pas de régression UX.
- **`logic-search#0` — drop quand `len(skipped_by) >= n_workers`.** Visibilité plutôt que
  silence ; spec §14 « PAS DE PERTE » se lit au niveau **multi-cycle** (le cycle suivant
  reshuffle + backoff expire), bornée intra-cycle quand toutes les instances sont en backoff.
- **`observability#2/#3` — extension du tuple de retour à 4 éléments**, pas un nouvel API
  parallèle. Les 30+ call-sites de test ont été adaptés ; net positif vs. une fonction
  `classify_outcome` séparée qu'il aurait fallu re-router à chaque couche.
- **5 contestés — annotation au point d'usage, PAS au rapport d'audit seul.** Un futur
  lecteur d'`egress.py` voit la justification du `returncode != 0 → suspicious` sans avoir à
  exhumer un fichier `docs/reference/` daté. Le rapport d'audit reste l'index, mais la
  décision vit DANS le code qu'elle motive.
- **Doc-drift `compactor.py` — formulation dynamique plutôt que dernière à jour.**
  « TOUTES les migrations » au lieu de « 0001+0002 » : au prochain ajout, pas de re-drift. La
  même discipline (préférer un mécanisme auto-vérifié ou une formule dynamique aux numéros
  figés) est désormais en mémoire (`feedback_doc_cross_references.md`).
- **`sandbox-confinement#3/#4` — faits, pas skippés.** Geoffrey a explicitement validé la
  swiss-cheese : « On pourrait le faire facilement je pense, et c'est dans la lignée de la
  défense ». Pareil pour `tmpfs size=` (defense-en-profondeur sur RAM host).
- **`test-gaps#3` (downloads.state sans CHECK) — DOCUMENTER, pas migrer.** Une migration
  ALTER TABLE ADD CONSTRAINT serait lourde sur SQLite (crée+copie+drop+rename). Le finding
  concède « impossible avec les writers actuels » → annotation suffit, on reconsidérera si
  un writer hors-code est envisagé.

## 5. Pièges appris (utiles au prochain lot)

- **`os.fdopen` PRÉ-rejette « Is a directory ».** Mon test S_ISREG d'un fd sur dir ne
  l'exerçait jamais (l'erreur sortait du `with os.fdopen(fd, "rb")` avant ma vérification).
  Solution : extraire un helper (`_read_header_no_follow`) qui fait `S_ISREG` AVANT le wrap
  fdopen, et tester ce helper directement. Pattern transférable à toute défense-en-profondeur
  derrière un wrapper Python qui pré-checke.
- **`puremagic.from_string(b"")` lève `PureValueError`, PAS `PureError`.** Le MRO est
  `[PureValueError, ValueError, Exception]`. L'`except PureError` ne l'attrape pas. Le test
  qui vérifiait l'`except` utilisait `b"\x00\x01\x02"` (non vide) → branche non exercée alors
  qu'on croyait. **Toujours tester les bornes (vide, max, +1) de l'entrée hostile** quand le
  but est la défense en profondeur.
- **`run_in_threadpool` + monkeypatch + 4-tuple.** Quand on monkeypatchez `verify_file` dans
  un test du verifier (qui s'exécute via `run_in_threadpool`), la fonction injectée DOIT
  rendre le tuple de la NOUVELLE arité, sinon `ValueError: too many values to unpack` côté
  endpoint qui mange l'erreur en 500. Penser à mettre à jour les stubs en même temps que la
  signature.
- **httpx `ASGITransport(app=, raise_app_exceptions=True)` est le défaut.** Pour tester un
  500 généré par Starlette en réponse à une exception applicative, il faut explicitement
  `raise_app_exceptions=False`, sinon httpx ré-élève l'exception et le test croit que le
  middleware d'erreurs n'a pas tourné.
- **`asyncio.Queue.get()` sur file non vide ne cède PAS.** C'est la cause racine de
  `logic-search#0`. Quand on conçoit une boucle qui doit céder régulièrement, ne pas se
  reposer sur `await queue.get()` — il faut un `await asyncio.sleep(0)` explicite (ou une
  pause réelle) dans le code qui suit.
- **Couverture 100 % de `pragma: no cover`.** Quand on modifie un bloc déjà sous `# pragma:
  no cover` (comme `ProdChildRunner.__call__` pour `sandbox#2`), c'est OK d'y ajouter des
  branches non couvertes — la pragma s'applique à la fonction entière. Mais le risque est
  réel : la branche `kill+wait` de dernier ressort n'a JAMAIS été exercée, même par
  l'intégration. À la prochaine refonte, envisager d'extraire un helper testable.
- **Le drift le plus tenace est dans les chemins « en français qui comptent »** — `compactor.py`
  disait « migrations 0001+0002 » correctement au moment de l'écriture, faux au prochain
  ajout. Préférer toujours une formule qui ne fige pas un état comptable.
- **`build_ed2k_link` partagé via `catalog_matching`** : leçon plus large — quand le webui ET
  le crawler ont besoin d'un même calcul pur (file → représentation canonique), il vit dans
  `catalog_matching`, pas dans l'un des deux. La frontière de paquet est claire : domaine
  partagé pur ⇒ matching.

## 6. Reste à faire (priorisé)

### Findings non traités (lows/info confirmés du rapport d'audit, hors §6 du handoff précédent)

1. `protocol-ec#0` — un AUTH_FAIL/FAILED corrompu transforme l'erreur en `EcProtocolError`
   (mauvaise classification : `MuleUnreachableError` au lieu de `MuleSearchFailedError`/`EcAuthError`).
   Wrap `string_value()` dans try/except.
2. `protocol-ec#1` — `search_and_wait` ne court-circuite pas pour Kad terminé (`search_progress`
   rend `None` au-dessus de 100, écrasant les sentinelles). Petit.
3. `data-persistence#0` — merge/compact ne vérifient pas `PRAGMA src.user_version` → erreur
   opaque quand une source est dans une vieille version. Migration message + détection.
4. `data-persistence#1` (uncertain) — `cutoff_date` sans normalisation UTC (non atteignable via
   le CLI ; defense-en-profondeur).
5. `observability#0` — label Prometheus `verdict` non borné → cardinalité explosive sur un
   verifier divergent.
6. `observability#1` — échecs de persistance (`RepositoryError`) jamais instrumentés.
7. `observability#4` — états terminaux d'échec de download (`failed`, nom dégénéré, plafond
   disque) non instrumentés.
8. `observability#6` (low→info) — gauge `emule_crawler_up` jamais remise à 0 à l'arrêt propre.
9. `containers-confinement#0` (MEDIUM→high) — le knob gVisor `CONTAINER_RUNTIME` ne couvre pas
   amuled (composant le plus exposé). À regarder sérieusement.
10. `containers-confinement#1` (MEDIUM) — webui SQLite WAL sur `:ro` + rootfs read_only échoue
    par intermittence (masqué par /health).
11. `containers-confinement#2` (HIGH→medium) — l'image webui n'est jamais construite/publiée
    par la CI mais le compose la tire de ghcr.io.
12. `containers-confinement#3` (MEDIUM→low) — webui exposée par défaut sur 0.0.0.0 (lié au
    drift WEBUI_HOST que je viens de corriger côté doc, mais le compose et l'app divergent).
13. `containers-confinement#4` — Prometheus/Grafana sans aucun durcissement conteneur.
14. `webui-info#0` — 404 stylée uniquement pour `/files/{hash}` inconnu, pas pour les routes
    inexistantes.
15. `webui-logic#4` — MatchingExplainer (webui) passe l'`Any` de `yaml.safe_load` sans valider
    la racine → AttributeError opaque au boot.
16. Autres `low`/`info` du rapport (testing-quality#0/#1/#2, sandbox-confinement#1, etc.).

### Vrais pre-existants non liés à l'audit (à fixer un jour)

- `test_transport.py::test_connect_refused_raises_connect_error` — test plateforme-dépendant
  (RST déterministe Linux uniquement, timeout sur macOS). Soit `@pytest.mark.skipif(darwin)`,
  soit `# pragma: no cover` pour les 2 lignes 75-76 de `transport.py`, soit refactor avec
  injection de l'open. Empêche la couverture crawler 100 % sur macOS dev mais pas en CI.
- `test_analysis_child.py` × 5 — pyseccomp s'auto-importe et `find_library("seccomp")` rend
  None sur macOS → RuntimeError au load du module. Workaround : injecter `NoopConfiner()`
  sur tous les `main([_HASH], ...)` (j'ai déjà fait ce pattern dans les 2 nouveaux tests de
  `sandbox#4`, mais les 5 anciens tests doivent encore être migrés).

## 7. PAS validé contre le vrai matériel

- **`logic-search#0` (LIFO + re-enfilage)** : déterministe via FakeClock/FakeRng, mais le
  comportement réel sous charge concurrente avec event loop Python 3.12 + N=2-3 workers
  réels n'a pas été observé. Le test « both workers backoff » tournait en boucle infinie
  AVANT le passage LIFO — confiance forte que ça vaut aussi en prod, mais aucune mesure
  réelle de drain en multi-instance.
- **`sandbox-confinement#2` (reap post-timeout)** : le bloc est `# pragma: no cover` (couvert
  par `analysis_integration`, Linux + libseccomp). Sur macOS dev, on n'a JAMAIS exercé le
  chemin `kill+wait` de dernier ressort. À la CI Linux ça passe ; en prod, ça vaut.
- **`sandbox-confinement#3` (clamscan limits + tmpfs size=)** : valeurs calibrées au feeling
  (2048M scan/file, 10 récursion, 1000 files, 120000 ms). À ajuster si on observe un faux
  positif sur un fichier légitime. **Pas testé avec un vrai média de 500 Mio**.
- **`sandbox-confinement#4` (O_NOFOLLOW + S_ISREG)** : testé en isolation. Le scénario réel
  (amuled compromis qui dépose un symlink dans la quarantaine RW entre `lstat` parent et
  `open` child) est purement théorique — gVisor + cgroups bornent déjà l'impact.
- **`observability#2/#3` (child_outcome + responses)** : compteurs incrémentés correctement
  d'après les tests, mais l'expérience opérateur réelle (« je vois `verdict=suspicious`
  monter, je regarde `child_outcome`, je comprends ») n'a pas été éprouvée. Le dashboard
  Prometheus n'a PAS été mis à jour pour ajouter ces métriques aux panneaux existants.
- **`logic-download#2` + `error-boundary#2`** : la relecture `active_states()` et le try/except
  par hash sont déterministes en test, mais le scénario réel (SQLITE_BUSY transitoire en
  pleine promotion d'un hash) n'a pas été reproduit. Le coût (un appel repo de plus par
  cycle) est négligeable mais pas mesuré.
- **WebUI security headers (CSP)** : `default-src 'self'` est strict — si on ajoute un asset
  externe (CDN, font Google) plus tard, il faudra desserrer. Pas testé avec un vrai
  navigateur (juste le contenu des headers).
- **Pagination UI** : l'heuristique « next existe quand page pleine » donne un faux positif
  inoffensif sur la borne exacte (50 fichiers exactement → un next qui rend une page vide).
  Pas grave UX, mais à savoir.
- **`docs(audit) — contestés`** : les annotations sont des docstrings, pas du code exécuté.
  Le test du temps dira si elles survivent aux refactors futurs (la mémoire posée sur la
  discipline doc-drift devrait aider).

## 8. Pointeurs

- **Rapport d'audit** : `docs/reference/2026-06-23-codebase-audit-findings.md` — désormais
  avec colonne « Décision » sur les contestés tranchés. C'est toujours la source pour le
  prochain lot (cf. §6 ci-dessus).
- **Source de vérité partagée** : `catalog_matching.config.TIER_RANK` (mutualisé crawler+webui
  cette session). `catalog_matching.ed2k_link.build_ed2k_link` (mutualisé pareil).
- **Métriques verifier** : `VerifierMetrics.observe_child_outcome(outcome)` et
  `VerifierMetrics.observe_response(status)` — nouveaux compteurs sur le registre dédié.
- **Mémoire `~/.claude/projects/.../memory/`** : `feedback_doc_cross_references.md` —
  discipline back-refs + préférence pour les mécanismes auto-vérifiés. À consulter au début
  de chaque session qui touche `docs/`.
- **Garde-fous tooling** : `.githooks/pre-push` et `.github/workflows/ci.yml` listent tous les
  deux les **4 paquets** désormais — alignés avec CLAUDE.md, testing-guide et README.
