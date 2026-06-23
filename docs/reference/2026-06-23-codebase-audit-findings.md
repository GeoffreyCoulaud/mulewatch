# Rapport d'audit consolidé — emule-indexer

## Résumé exécutif

**59 findings** issus de 17 perspectives, chacun passé par deux vérificateurs adversariaux (lentille reproduction + lentille faux-positif). Après consolidation des verdicts :

| Statut | Nombre |
|---|---|
| confirmed | 41 |
| disputed | 5 |
| uncertain | 5 |
| likely | 3 |
| refuted | 1 |

Répartition par sévérité **après révision des vérificateurs** (la sévérité d'origine est souvent revue à la baisse) :

| Sévérité (révisée) | Nombre approximatif |
|---|---|
| high | 4 |
| medium | ~18 |
| low | ~24 |
| info | ~13 |

> Note : plusieurs findings notés `high` à l'origine ont été ramenés à `medium`/`low` par au moins un vérificateur (souvent les deux). Les chiffres ci-dessus reflètent le consensus le plus défendable.

### Les 5 points à traiter en priorité

1. **Promotion de download non-idempotente (`logic-download#0`, high, confirmed)** — un échec d'écriture DB *après* l'`os.replace` de promotion bloque définitivement un fichier en quarantaine, jamais vérifié, avec `PromotionFailed` en boucle. C'est une **perte fonctionnelle définitive** sur l'objectif cœur du projet (retrouver les épisodes Keroro). Le test associé (`logic-download#1`) masque le bug parce que le fake ne consomme pas la source.

2. **Validation de config matcher absente sur plusieurs chemins critiques** — une règle `all: []`/`any: []` matche **inconditionnellement** tout fichier en tier=download (`config-validation#0`, confirmed) ; les rlimits/timeout négatifs/nuls du verifier sont acceptés sans fail-fast et **désarment silencieusement le confinement** (`config-validation#3`, confirmed) ; `ENABLED_CHECKS` accepte les noms inconnus → **fail-open total** de l'analyse antivirus sur typo (`config-validation#4` / `test-gaps#1`, confirmed).

3. **Restart d'amuled raté = High-ID perdu définitivement et en silence (`test-gaps#0`, high, confirmed)** — une seule panne transitoire de restart stalle pour toujours la récupération High-ID (le seul but du port-sync) et **éteint l'alerte** OPERATIONS, car la garde compare au port *préférence* (que `set` vient d'écrire) et non au port réellement bound.

4. **Event loop du verifier gelé pendant toute l'analyse** (`sandbox-confinement#0` / `concurrency-async#0`, confirmed) + **désaccord de timeout client 10 s vs analyse 30/120 s** (`concurrency-async#1`, confirmed) — `/health` et `/metrics` deviennent inservables pendant chaque analyse, et les fichiers lents (clamav, gros média) partent systématiquement en dead-letter sans panne réelle.

5. **Worker en backoff qui consomme et jette les tâches de la queue partagée (`logic-search#0`, high, confirmed)** — en déploiement multi-instance, un worker dont l'instance est tombée draine et jette les tâches restantes du cycle, créant un angle mort de couverture pendant que le cycle se déclare complet.

---

## Findings confirmés et probables

### Verifier — confinement, concurrence, analyse

#### [HIGH→medium] Event loop uvicorn gelé pendant toute l'analyse (subprocess synchrone)
- **Statut** : confirmed (les deux vérificateurs révisent high→medium)
- **Perspectives** : `sandbox-confinement#0` + `concurrency-async#0` (**doublon fusionné** — même cause, mêmes fichiers)
- **Fichiers** : `packages/verifier/src/download_verifier/app.py:45,69` ; `spawn.py:60` (`proc.communicate(timeout=...)`) ; `__main__.py:33-37` (uvicorn mono-worker)
- **Description** : `verify_endpoint` est `async def` mais appelle `verify_file()` de façon synchrone et bloquante. Aucun `run_in_threadpool`/`to_thread` (grep vide). L'event loop est gelé jusqu'à `timeout_s` (30 s par défaut).
- **Impact** : `/health` (healthcheck Docker interval 10 s / timeout 3 s) et `/metrics` (scrape Prometheus) ne sont plus servis pendant l'analyse → conteneur flappe en `unhealthy`. **Nuances établies par les vérificateurs** : le consommateur crawler est sériel (pas de `/verify` concurrents) ; le gate `verifier.health()` du crawler est one-shot au démarrage seulement (`composition/app.py:582`), donc pas de cascade fail-fast au runtime ; `depends_on` est nu (pas de `service_healthy`), donc l'`unhealthy` n'a aucune conséquence automatique.
- **Correctif** : `await run_in_threadpool(verify_file, ...)` ou rendre le handler synchrone (`def`) ; garder `/health` et `/metrics` non bloquants. Ajouter un test asservissant `/health` concurrent d'un `/verify` lent.

#### [HIGH→medium] Désaccord de timeout : client httpx 10 s vs analyse 30/120 s → dead-letter de fichiers sains
- **Statut** : confirmed (les deux)
- **Perspective** : `concurrency-async#1`
- **Fichiers** : `composition/app.py:96` (`httpx.Timeout(10.0)`) ; `config.py:56` (`timeout_s=30.0`) ; `verifier_http.py:61-62` ; `run_verification_cycle.py:152-159` ; `local_state_repository.py:61,85` (`max_attempts=3`)
- **Description** : le client crawler a un timeout figé à 10 s, non configurable, non override en prod. Une analyse > 10 s (clamav jusqu'à 120 s CPU) lève `ReadTimeout` → `VerifierUnavailableError` → `fail_verification` → retry → re-timeout → dead-letter après 3 tentatives. Le défaut est autonome (n'exige pas le gel de l'event loop, mais celui-ci l'aggrave en sérialisant).
- **Impact** : fichiers intrinsèquement lents (gros média, scan antivirus) **jamais vérifiés avec succès**, dead-letter systématique, re-analyse coûteuse ×3. Borné : le dead-letter est non destructif (fichier reste en quarantaine), impact limité aux analyses réellement lentes (avec les checks par défaut type_sniff+ffprobe sur 4096 octets d'en-tête, c'est rapide).
- **Correctif** : aligner le timeout client sur `ANALYSIS_TIMEOUT_S` + marge, ou le rendre configurable et dérivé de `verify_config`. A minima forcer la contrainte `timeout_client > timeout_analyse` au montage.

#### [MEDIUM] La config de l'analyseur n'est PAS validée au démarrage du verifier (fail-fast promis mais absent)
- **Statut** : confirmed (les deux)
- **Perspective** : `error-boundary#0`
- **Fichiers** : `config.py:7-8` (docstring promet fail-fast) ; `check.py:34` (résolution paresseuse par requête) ; `app.py:69,92-110` ; `__main__.py:30-37`
- **Description** : `AnalysisConfig.from_env` n'est appelé NULLE PART au boot ; il est résolu paresseusement à chaque requête dans `verify_file`. Une env malformée (`RLIMIT_CPU_S=abc`, `ENABLED_CHECKS` vide, `SECCOMP_ENABLED=maybe`) ne fait pas échouer le démarrage : le `ValueError` remonte hors de `verify_endpoint` (le seul try couvre `json.loads`) → 500 → mappé en `VerifierUnavailableError` transitoire → dead-letter après `max_attempts`. Une erreur de config **statique et déterministe** est traitée comme une panne transitoire. Violation directe de E-D13.
- **Correctif** : construire `AnalysisConfig.from_env(os.environ)` UNE fois au boot (dans `build_app`/`main`), le stocker dans `app.state`, l'injecter à `verify_file`. Le `ValueError` devient un vrai fail-fast au lancement.

#### [MEDIUM] Verifier : rlimits/timeout/header_bytes/egress négatifs ou nuls acceptés sans validation
- **Statut** : confirmed (les deux révisent high→medium)
- **Perspectives** : `config-validation#3` (lié à `error-boundary#0` ci-dessus — même absence de validation au boot)
- **Fichiers** : `config.py:82-97` (`_parse_int`/`_parse_float` sans plancher) ; `config.py:37-70` ; `spawn.py:70-95`
- **Description** : aucune borne minimale. `timeout_s<=0` → `communicate(timeout=)` lève `TimeoutExpired` immédiat → tout en `suspicious`. `egress_cap_bytes<0` → tout stdout `suspicious`. `RLIMIT_AS_BYTES=0` → child ne peut pas exec (`OSError` au Popen parent, non rattrapé → 500). **Correction importante des vérificateurs** : `RLIMIT_CPU_S=-1` ne crashe PAS — `-1 == RLIM_INFINITY`, donc le garde CPU est **désarmé silencieusement** (illimité), ce qui est pire que le crash annoncé.
- **Impact** : config sécurité-critique acceptée sans plancher → confinement silencieusement désarmé OU service cassé au premier fichier, alors que la docstring promet le fail-fast. Déclenchement par mauvaise saisie opérateur (pas attaquant). Effets : soit fail-closed vers `suspicious` (direction sûre), soit rlimit désarmé (vrai affaiblissement).
- **Correctif** : valider dans `from_env` : `timeout_s > 0`, `header_bytes > 0`, `egress_cap_bytes > 0`, tous les `rlimit_* > 0`. `ValueError` actionnable nommant la variable.

#### [MEDIUM] Le timeout wall-clock parent (30 s) borne le scan clamav avant son budget CPU relâché (120 s)
- **Statut** : confirmed (les deux)
- **Perspective** : `sandbox-confinement#1`
- **Fichiers** : `config.py:50` (cpu→120 conditionnel) vs `config.py:56` (`timeout_s=30.0` NON relâché) ; `spawn.py:60` (wall-clock killpg) ; `spawn.py:73` (RLIMIT_CPU) ; `egress.py:27` (timed_out→suspicious)
- **Description** : en mode clamav, `cpu_default` passe à 120 s mais `timeout_s` reste à 30 s. Le wall-clock parent (`communicate(timeout=30)` + killpg) prime toujours sur RLIMIT_CPU. Le déploiement prod (`compose.core.yaml:79-82`) active clamav avec `RLIMIT_CPU_S_CLAMAV=120` mais ne pose JAMAIS `ANALYSIS_TIMEOUT_S`. Le budget CPU de 120 s est donc inatteignable.
- **Impact** : tout scan clamav dont le wall-clock dépasse 30 s (premier scan = chargement base + gros média) est tué et rendu `suspicious` → **faux positifs systématiques sur médias sains lents**. Conservateur (fichier reste en quarantaine, jamais promu/détruit).
- **Correctif** : relâcher aussi `timeout_s` conditionnellement (`ANALYSIS_TIMEOUT_S_CLAMAV` ≥ CPU clamav, ~120-150 s). Réconcilier les deux limites dans une seule décision de config.

#### [MEDIUM→low] `sniff()` crashe sur un fichier de 0 octet : `PureValueError` n'hérite pas de `PureError`
- **Statut** : confirmed (les deux révisent medium→low)
- **Perspective** : `input-trust#0`
- **Fichiers** : `checks/type_sniff.py:102-105` ; `analysis_child.py:57` ; `pipeline.py:32`
- **Description** : reproduit empiriquement (puremagic 2.2.0). `from_string(b"")` lève `PureValueError` (MRO `[PureValueError, ValueError, Exception]`), qui n'hérite PAS de `PureError`. L'`except PureError` ne l'attrape pas. Un fichier de 0 octet en quarantaine fait crasher le child. La branche est non testée (le test PureError utilise `b"\x00\x01\x02"`, non vide), et le 100% branch coverage ne la détecte pas.
- **Impact** : **borné** — le crash est contenu dans le child jetable ; `egress.parse` mappe `returncode != 0` → `suspicious`. Le parent ne crashe pas (c'est exactement la frontière child→parent voulue, donc la prétention « violation E-D13 » est fausse). Le verdict final est `suspicious` dans les deux chemins (ffprobe sur 0 octet aurait aussi donné suspicious). Vrai enjeu : observabilité (`checks=[]`, ffprobe court-circuité) + entrée hostile non testée + divergence vs docstring (« clean »).
- **Correctif** : court-circuiter un header vide avant puremagic (`if not header: return clean`), OU élargir l'`except` à `(PureError, ValueError)`. Ajouter `sniff(b'') == clean`.

#### [LOW] Reap post-timeout fait `proc.communicate()` SANS timeout : un descendant échappé peut hang le worker
- **Statut** : confirmed (les deux) | **Perspective** : `sandbox-confinement#2`
- **Fichiers** : `spawn.py:64,66` ; `confine.py:26-38` (seccomp ne deny pas `setsid`)
- **Description** : au `TimeoutExpired`, `killpg(SIGKILL)` puis `proc.communicate()` sans timeout. Un descendant compromis (post-RCE) qui fait `setsid()` (non bloqué par le blocklist, choix délibéré) et garde le pipe stdout ouvert échappe au killpg → `communicate()` bloque indéfiniment, gelant le worker (cf. event loop synchrone).
- **Impact** : conditionnel à une RCE préalable dans ffprobe/clamscan ; évasion très contrived. gVisor ne mitige pas (sémantique des groupes de process). Code non couvert (`# pragma: no cover`).
- **Correctif** : timeout court sur le reap + `proc.kill()/wait()` en dernier ressort, et/ou fermer `proc.stdout` avant le reap.

#### [LOW] clamscan invoqué sans bornes de décompression explicites
- **Statut** : likely (1 confirmed low, 1 uncertain→info) | **Perspective** : `sandbox-confinement#3`
- **Fichiers** : `checks/clamav.py:49` ; `bricks/compose.core.yaml:101-102` (tmpfs `/tmp` sans `size=`)
- **Description** : argv clamscan sans `--max-scansize/--max-filesize/--max-recursion/--max-files/--max-scantime`. Désaccord : tous les vecteurs (mémoire, CPU, disque tmpfs) sont déjà bornés par RLIMIT_AS=1.5 Gio, RLIMIT_CPU=120 s, wall-clock, RLIMIT_FSIZE=16 Mio, `mem_limit: 2g`. Aucun chemin d'exploitation concret n'est ouvert.
- **Correctif** : passer les bornes explicites + fixer `tmpfs: /tmp:size=...`. À traiter comme durcissement defense-en-profondeur, pas urgence.

#### [INFO] Aucune garde symlink/realpath côté verifier avant ouverture de la quarantaine
- **Statut** : confirmed (les deux, info) | **Perspective** : `sandbox-confinement#4`
- **Fichiers** : `check.py:36` (`is_file()` suit les symlinks) ; `analysis_child.py:54-56` (open sans O_NOFOLLOW) ; la revalidation `_CANONICAL_HASH_RE` ne valide que le NOM
- **Description** : un symlink nommé comme un hash hex valide passerait. Non exploitable dans l'archi actuelle (quarantaine créée par `os.replace` = fichiers réguliers ; verifier monte `:ro`). Résiduel : amuled partage le volume quarantaine en RW (`examples/gluetun.yaml:41`), donc un amuled compromis pourrait y déposer un symlink. Même réussi, le child ne renvoie qu'un verdict JSON (pas de fuite d'octets).
- **Correctif** : `realpath` containment ou `O_NOFOLLOW` + `fstat S_ISREG` après l'open RO, avant le seccomp.

#### [MEDIUM→info] Timeout/crash interne des runners peut faire crasher l'enfant au lieu d'un poison propre
- **Statut** : disputed (1 confirmed info, 1 refuted) | **Perspective** : `error-boundary#3`
- **Voir section « Contestés »**.

#### [MEDIUM] Réponses 400/500 du verifier non comptées
- **Statut** : confirmed (les deux révisent → low) | **Perspective** : `observability#3`
- **Fichiers** : `app.py:50-72` ; `metrics.py:26-29` ; `spawn.py:88` (`mkdtemp` hors try)
- **Description** : `metrics.observe` n'est appelé qu'après un retour normal de `verify_file`. Les retours `_bad_request` (400) et les exceptions remontant en 500 (ex. `mkdtemp` sur FS scratch plein) ne touchent aucune métrique. **Correction** : le cas fichier-absent est déjà compté comme `error` (`check.py:36-37`), donc la portée du 500 est surestimée ; service interne (reseau `internal`).
- **Correctif** : counter `emule_verifier_responses{status}` ; envelopper `verify_file` dans un try/except observant un verdict + counter d'exception.

#### [MEDIUM→low] Timeout/crash du child écrasés en `suspicious` sans métrique distincte
- **Statut** : confirmed (les deux → low) | **Perspective** : `observability#2`
- **Fichiers** : `egress.py:27-28` ; `metrics.py:14-29` ; `app.py:67-72`
- **Description** : `egress.parse` mappe timeout / crash (`returncode != 0`) / egress hors-cap tous vers `suspicious`. Seule métrique : `emule_verifier_requests{verdict}`. En incident de masse, l'opérateur voit une montée de `suspicious` sans cause. L'histogramme de durée atténue partiellement le cas timeout, pas le crash ni l'overflow. La décision `suspicious` reste correcte (le finding ne la conteste pas).
- **Correctif** : counter `emule_verifier_child_outcome{ok,timeout,nonzero_exit,egress_overflow}` alimenté depuis `egress.parse`/`run_analysis`.

#### [MEDIUM→medium] `ENABLED_CHECKS` accepte les noms inconnus → fail-open / désactivation silencieuse
- **Statut** : confirmed (les deux) | **Perspectives** : `config-validation#4` + `test-gaps#1` (**doublon fusionné**)
- **Fichiers** : `config.py:73-79` (`_parse_checks` ne valide pas les noms) ; `pipeline.py:30-37` (DA4 : nom inconnu ignoré) ; `checks/base.py:29-31` (`worst_status([]) == "clean"`)
- **Description** : une typo (`clamv`, `type-sniff`) produit zéro check exécuté → verdict `clean` pour tout fichier, sans erreur ni log. Si TOUS les noms sont mal orthographiés → fail-open total (fichier malveillant catalogué `clean`). Le comportement est verrouillé par `test_unknown_check_name_is_ignored`. DA4 est obsolète puisque clamav est désormais implémenté.
- **Impact** : affaiblissement silencieux de la posture sécurité. Nuance : défaut `(type_sniff, ffprobe)` est sûr ; exige une mauvaise saisie opérateur explicite ; une typo partielle laisse tourner les checks valides.
- **Correctif** : valider chaque nom contre `KNOWN_CHECKS = {type_sniff, ffprobe, clamav}` → `ValueError` fail-fast. Optionnellement refuser un pipeline à zéro check.

#### [LOW] `_parse_bool` rejette `True/TRUE/On` pour `SECCOMP_ENABLED`
- **Statut** : confirmed (1 low, 1 info) | **Perspective** : `config-validation#5`
- **Fichiers** : `config.py:100-107`
- **Description** : seuls les littéraux minuscules exacts sont acceptés. `SECCOMP_ENABLED=True` (casse Python) lève `ValueError`. C'est du fail-fast bruyant (jamais une désactivation silencieuse de seccomp), conforme à E-D13 ; le message echo la valeur fautive mais ne liste pas les littéraux acceptés.
- **Correctif** : `raw.strip().lower()`, accepter `on/off`, message listant les littéraux.

---

### Crawler — boucle download, promotion, search

#### [HIGH] Promotion non-idempotente : un échec d'enqueue/set_state APRÈS l'`os.replace` bloque le fichier pour toujours
- **Statut** : confirmed (les deux, high) | **Perspective** : `logic-download#0`
- **Fichiers** : `run_download_cycle.py:209-219` (try couvre seulement `promote`, `enqueue_verification` et `set_state(QUARANTINED)` HORS du try) ; `quarantine_fs.py:22-32` (`os.replace` consomme la source) ; `run_download_cycle.py:225-238,236,345`
- **Description** : si `enqueue_verification` lève `RepositoryError` (local.db transitoirement KO : SQLITE_BUSY, disque plein) juste après un `promote` réussi, l'état reste COMPLETED, aucune tâche enfilée, source déjà déplacée. Au cycle suivant, le hash est toujours dans shared_files, état COMPLETED (non terminal) → re-`_promote_completion` → `os.replace` sur source disparue → `FileNotFoundError` attrapé → reste COMPLETED, `PromotionFailed` émis. **Boucle infinie**, fichier jamais vérifié, état non récupérable sans intervention manuelle.
- **Impact** : perte définitive d'un fichier pour la vérification (objectif cœur). Le variant `set_state(QUARANTINED)` qui échoue après enqueue réussi est plus bénin (fichier vérifié une fois, état figé + alerte en boucle).
- **Correctif** : rendre la promotion idempotente face à une source déjà consommée — traiter `FileNotFoundError` comme « déjà promu » quand `quarantine_dir/<hash>` existe, et reprendre à l'enqueue + `set_state(QUARANTINED)`. Ou réordonner.

#### [MEDIUM] Le FakeQuarantine de test ne consomme pas la source, masquant la non-idempotence
- **Statut** : confirmed (1 medium, 1 low) | **Perspective** : `logic-download#1` (**lié à `logic-download#0`**)
- **Fichiers** : `tests/application/test_run_download_cycle.py:81-91,678-704`
- **Description** : `FakeQuarantine.promote` ne fait qu'`append` ; il ne modélise pas l'effet de bord d'`os.replace`. Le test `test_completion_repo_failure_does_not_starve_new_candidates` valide « reste COMPLETED → retry au tour suivant » alors que le vrai adapter lèverait `FileNotFoundError` au retry. Le fake donne une **fausse assurance d'idempotence** et laisse passer `logic-download#0`.
- **Correctif** : faire que `FakeQuarantine` lève `FileNotFoundError` sur un second promote du même hash. Ajouter un test multi-cycle « promote OK puis enqueue échoue puis cycle suivant » qui doit aboutir à QUARANTINED + enfilé.

#### [HIGH→medium] Un worker en backoff d'instance consomme et jette les tâches de la queue partagée
- **Statut** : confirmed (les deux révisent high→medium) | **Perspective** : `logic-search#0`
- **Fichiers** : `run_search_cycle.py:107,139` ; `search_worker.py:239,251,268-278`
- **Description** : la queue est partagée, les tâches non affectées par instance. Le chemin « instance en backoff » dans `run_task` est **purement synchrone** (`is_in_backoff` → log → `return`), sans `await`. Quand `await queue.get()` trouve la queue non vide, il retourne synchrone sans céder à l'event loop → le worker en backoff draine toute la queue restante d'un trait pendant que le worker sain est parqué sur un `await` réseau. Reproduit : le worker en backoff jette 19-20/20 tâches. `queue.join()` satisfait, `cycle_index` avance → cycle déclaré complet alors qu'une fraction des mots-clés n'a jamais été recherchée. Viole la spec §14 « PAS DE PERTE ».
- **Impact** : angle mort de couverture. Borné : ne se manifeste qu'avec N≥2 workers, pendant une fenêtre de panne d'instance mi-cycle, auto-récupérant au cycle suivant (reshuffle + backoff expire). Aucune télémétrie de skip.
- **Correctif** : ne pas consommer définitivement une tâche sautée — re-enfiler avant `task_done()` (attention à la terminaison si toutes les instances sont en backoff), ou retirer le worker du pool tant que `retry_after` est futur, ou affecter les tâches par instance. Test multi-worker.

#### [LOW] `_build_expected` qui lève `RepositoryError` laisse la tâche bloquée jusqu'à expiration du lease (15 min)
- **Statut** : disputed (1 confirmed low, 1 refuted) | **Perspective** : `logic-download#3`
- **Voir section « Contestés »**.

#### [LOW] Un échec repo dans `_monitor` masque TOUTES les complétions du cycle
- **Statut** : confirmed (les deux, low) | **Perspective** : `logic-download#2`
- **Fichiers** : `run_download_cycle.py:335-341,225-238`
- **Description** : sur `RepositoryError` dans `_monitor`, `states = {}` est posé puis `_handle_completions` est appelé avec ce dict vide. Chaque hash partagé → `states.get(...) is None` → traité comme « fichier hors crawler » → ignoré. Une complétion réelle est manquée pour ce cycle entier. Le commentaire « isolé par étape » est inexact (l'étape 2 perd son dict d'états). `_add_links` relit `active_states()` (ligne 293), pas `_handle_completions`.
- **Impact** : latence de promotion +1 cycle, pas de perte (retry au cycle suivant, hash reste dans shared_files).
- **Correctif** : relire `active_states()` avant `_handle_completions` au lieu de `states={}`.

#### [LOW] `_handle_completions` : une `RepositoryError` au milieu d'un hash saute les hash suivants du cycle
- **Statut** : confirmed (les deux, low) | **Perspective** : `error-boundary#2`
- **Fichiers** : `run_download_cycle.py:225-238,340-346`
- **Description** : la boucle `for entry in shared` appelle `_promote_completion` sans try/except par hash. Une `RepositoryError` sur le hash N remonte jusqu'au handler de niveau cycle, abandonnant N+1, N+2. L'isolation documentée est PAR ÉTAPE, pas PAR HASH (intention I2). Non couvert (tous les tests utilisent un seul `SharedFileEntry`).
- **Impact** : famine temporaire intra-cycle, rattrapée au cycle suivant (signal persistant). Pas de perte définitive.
- **Correctif** : envelopper l'appel à `_promote_completion` par hash dans un try/except `RepositoryError` (log + continue).

#### [MEDIUM] `run_search_cycle` crash l'app entière sur `RepositoryError` en fin de cycle
- **Statut** : confirmed (1 medium, 1 low) | **Perspective** : `error-boundary#1`
- **Fichiers** : `run_search_cycle.py:156-158` (`write_cycle_state`/`save_channel_backoff` sans try/except) ; `composition/app.py:214-232,452-453`
- **Description** : ces deux écritures de fin de cycle ne sont protégées par aucun try/except. Une `PersistenceError` (disque plein, local.db verrouillé) propage hors de `run_search_cycle` → `_run_loop` → TaskGroup de `_supervise` qui annule TOUTES les boucles sœurs (download/verify/port-sync) et crashe le process. **Asymétrie** : les trois autres boucles documentent « NE LÈVE JAMAIS » et absorbent `RepositoryError` ; `record_observation` absorbe déjà sa `RepositoryError` mi-cycle. Seul l'état de fin de cycle de recherche est fail-loud. La spec dit qu'un kill mi-cycle rejoue le cycle (index n'avance pas) → état récupérable.
- **Impact** : une panne de persistance transitoire fait tomber tout le crawler. État récupérable (append-only, pas de corruption). Pas de test figeant l'intention.
- **Correctif** : envelopper dans try/except `RepositoryError` (log + return sans avancer l'index) pour aligner sur les trois autres boucles ; sinon documenter le fail-loud délibéré.

#### [INFO] La docstring du port `MuleDownloadClient` et `is_complete` contredisent l'invariant « completion = shared-files »
- **Statut** : confirmed (1 info, 1 low) | **Perspective** : `logic-download#4`
- **Fichiers** : `ports/mule_download_client.py:12-15,37-40` ; `run_download_cycle.py:164-169`
- **Description** : la docstring affirme « la complétion se déduit de size_done/size_full (D2) », contredisant `_monitor` (« ne se déduit PLUS des octets »). `is_complete` n'est consommé en prod que par `tools/download_probe.py`. Footgun : une évolution future pourrait re-brancher la complétion sur `is_complete` (inférence octets bannie).
- **Correctif** : corriger la docstring ; documenter `is_complete` comme réservé au probe diagnostic (ou le retirer du DTO).

#### [INFO] `_ERROR_RESULT` singleton partagé avec `real_meta` dict mutable
- **Statut** : uncertain (1 refuted info, 1 uncertain info) | **Perspective** : `logic-download#5`
- **Voir section « Contestés »**.

---

### Crawler — protocole EC

#### [LOW] Un message AUTH_FAIL/FAILED corrompu transforme l'erreur en `EcProtocolError` (mauvaise classification)
- **Statut** : confirmed (les deux, low) | **Perspective** : `protocol-ec#0`
- **Fichiers** : `mule_ec/client.py:57,288,333` ; `codec.py:53-54` ; `errors.py:35,43`
- **Description** : `_failure_message` appelle `tag.string_value()` à cru. Si le tag EC_TAG_STRING est malformé (type ≠ STRING, ou sans NUL final), `string_value()` lève `EcProtocolError` (= `MuleUnreachableError`, flux mort) au lieu d'`EcFailureError` (= `MuleSearchFailedError`, échec applicatif) ou `EcAuthError`. Côté requête : un FAILED applicatif est traité comme coupure réseau (backoff instance vs canal). Côté auth : `EcAuthError` (fail-fast config) est noyé en transitoire silencieux — inversion de contrat. Le pattern tolérant existe ailleurs (`_optional_partfile_int`) mais pas ici.
- **Impact** : précondition rare (amuled émet normalement un STRING bien formé) ; pas de crash/perte/violation d'invariant ; émetteur EC sur loopback/internal.
- **Correctif** : envelopper `string_value()` dans un try/except `EcProtocolError` et retomber sur un libellé sûr (comme la branche `tag is None`), pour préserver la classe d'erreur d'origine.

#### [INFO] `search_and_wait` n'arrête jamais le polling tôt pour une recherche Kad terminée
- **Statut** : confirmed (les deux, info) | **Perspective** : `protocol-ec#1`
- **Fichiers** : `mule_ec/client.py:147` (`> 100 → None`) ; `tools/ec_probe.py:176,217` (`if progress == 100: break`)
- **Description** : `search_progress()` rend `None` dès que la valeur dépasse 100, ce qui écrase les sentinelles Kad-fini (0xfffe) et local-fini (0xffff). L'arrêt anticipé teste `== 100` strictement → jamais court-circuité pour Kad/local → poll jusqu'au budget complet. Affecte aussi le code PROD `search_worker.py:213` (même nature d'impact, pas de perte).
- **Correctif** : exposer un état « terminé » distinct du pourcentage, ou documenter que l'arrêt anticipé ne couvre que le canal global.

---

### Persistance, merge/compact, domaine

#### [MEDIUM→low] merge/compact ne vérifient pas la version de schéma des sources (échec opaque)
- **Statut** : confirmed (les deux révisent → low) | **Perspective** : `data-persistence#0`
- **Fichiers** : `merge/merger.py:182,146` ; `compact/compactor.py:114,26`
- **Description** : la SORTIE est ouverte via `open_catalog` (0001+0002) mais les SOURCES sont ATTACH-ées telles quelles, sans migration ni lecture de `PRAGMA user_version`. Les copies lisent inconditionnellement `src.file_observation_ranges` (migration 0002). Reproduit : une source restée à 0001 → `no such table: src.file_observation_ranges` enveloppé en MergeError/CompactError. Pas de corruption (rollback propre), mais message opaque au lieu d'un diagnostic de version. Risque croissant à chaque migration additive.
- **Correctif** : lire `PRAGMA src.user_version`, comparer à la version attendue, lever une erreur explicite (« source en version N, code en version M : rouvrez avec un crawler à jour »).

#### [LOW] `compactor` : `cutoff_date` dérivé de `clock().date()` sans normalisation UTC
- **Statut** : likely (1 uncertain info, 1 confirmed low) | **Perspective** : `data-persistence#1`
- **Fichiers** : `compact/compactor.py:106`
- **Description** : `.date()` pris directement sur le datetime de l'horloge, sans rejet du naïf ni `astimezone(UTC)`, contrairement à `utc_iso`. Une horloge non-UTC décalerait la coupure d'un jour. En prod (`utc_now` aware UTC) le défaut n'est pas atteignable ; le CLI ne passe jamais `clock`. Fragilité latente, partition reste complémentaire (pas de double-comptage).
- **Correctif** : `cutoff_date = clock().astimezone(UTC).date()...` ou réutiliser un helper imposant aware+UTC.

#### [INFO] Liste des états terminaux dupliquée (enum domaine vs littéral SQL) sans test de synchro
- **Statut** : confirmed (les deux, info) | **Perspectives** : `data-persistence#2` + `test-gaps#5` (**doublon fusionné**)
- **Fichiers** : `download_repository.py:40-43` (SQL `NOT IN ('completed','quarantined','failed')`) ; `domain/download/states.py:17` (`_TERMINAL_STATES`)
- **Description** : `_COMMITTED_BYTES` code en dur les états terminaux, source de vérité = `_TERMINAL_STATES`. Le commentaire dit « DOIVENT rester synchronisés » mais aucun mécanisme/test ne le garantit. Le 100% branch ne capte pas la dérive (littéral SQL opaque). Si un futur état terminal est ajouté, `committed_bytes` sur-compte → blocage indu du quota disque.
- **Correctif** : dériver la clause SQL de `_TERMINAL_STATES` (placeholders paramétrés), ou test `liste SQL == sorted(_TERMINAL_STATES)`.

#### [INFO] `claim_verification` : rollback sur `sqlite3.Error` seulement, asymétrie avec la discipline `BaseException`
- **Statut** : confirmed (les deux, info) | **Perspective** : `data-persistence#3`
- **Fichiers** : `local_state_repository.py:135`
- **Description** : seule méthode transactionnelle du package à n'attraper que `sqlite3.Error` pour le ROLLBACK, alors que 6 autres sites (dont `node_id` dans la même classe, au profil identique) attrapent `BaseException`, discipline documentée et testée (« ne jamais laisser la connexion `in_transaction »). Une exception asynchrone (KeyboardInterrupt/MemoryError) laisserait le repo cassé. Fenêtre très étroite (params calculés avant le BEGIN).
- **Correctif** : aligner sur `except BaseException` (ROLLBACK best-effort `suppress(sqlite3.Error)` puis raise).

#### [LOW→info] `bucketize` repose sur un tri d'entrée non vérifié
- **Statut** : uncertain (1 refuted info, 1 uncertain info) | **Perspective** : `logic-search#1`
- **Voir section « Contestés »**.

#### [LOW→info] `downloads.state` sans contrainte CHECK : un état inattendu lève `ValueError` non enveloppée
- **Statut** : disputed (1 refuted info, 1 confirmed low) | **Perspective** : `test-gaps#3`
- **Voir section « Contestés »**.

---

### Observabilité

#### [HIGH→medium] Label Prometheus `verdict` non borné → cardinalité explosive sur un verifier divergent
- **Statut** : confirmed (les deux révisent high→medium) | **Perspectives** : `observability#0` + `test-gaps#4` (**doublon fusionné**)
- **Fichiers** : `verifier_http.py:78-85` (accepte tout `str`) ; `policy.py:125-134` (label = verdict brut) ; `prometheus_sink.py:59` ; `catalog_repository.py:213` (stocké verbatim)
- **Description** : `_parse` accepte n'importe quel verdict str sans vérifier l'appartenance à `{clean,suspicious,malicious,error}`. Ce verdict devient une valeur de label Prometheus (une time-series par valeur distincte → cardinalité non bornée) ET est inséré verbatim en base. Comportement figé par `test_policy.py` (verdict='bogus' → label 'bogus'). Asymétrie : `policy` borne défensivement la sévérité/audience mais pas le label.
- **Impact** : DoS mémoire progressif (CollectorRegistry + scraper). **Atténuation** : l'egress du verifier légitime borne déjà l'enum (`egress.py:40`) → déclenchement seulement par verifier buggé/désynchronisé/compromis (composant interne déjà confiné), débit sériel. La catégorie « concurrence » est erronée (c'est validation d'entrée).
- **Correctif** : dans `_parse`, si `verdict not in {clean,suspicious,malicious,error}` → retomber sur `_ERROR_RESULT`. Idéalement typer `verdict` en `Literal`/enum côté frontière.

#### [HIGH→medium] Échecs de persistance (`RepositoryError`) jamais instrumentés
- **Statut** : confirmed (les deux révisent high→medium) | **Perspective** : `observability#1`
- **Fichiers** : `record_observations.py:58-64` ; `run_verification_cycle.py:168-191` ; `run_download_cycle.py:335-358` ; `policy.py:56-77`
- **Description** : toutes les `RepositoryError` (DB en panne durable) sont absorbées avec un simple `_logger.error` + continue/return. Aucun `telemetry.emit`, aucun `MetricName` dédié, aucun event `PersistenceFailed`. Un crawler dont la DB est durablement en panne tourne en silence côté métriques/alertes. Contraste avec AllInstancesBlind/VerifierUnavailable/PortMismatchUnresolved qui alertent OPERATIONS edge-triggered. Pas listé dans les hors-périmètre délibérés de la spec.
- **Impact** : angle mort majeur de détection d'incident. Signal indirect existe (stagnation de `emule_observations`/`emule_verifications`) mais ambigu, sans cause ni scope.
- **Correctif** : event `PersistenceFailed(scope)` + counter `emule_persistence_failures{scope}` émis sur chaque branche absorbée, audience OPERATIONS edge-triggered (anti-spam via EdgeState).

#### [MEDIUM] États terminaux d'échec de download (failed, nom dégénéré, plafond disque) non instrumentés
- **Statut** : confirmed (1 low, 1 medium) | **Perspective** : `observability#4`
- **Fichiers** : `run_download_cycle.py:303-307,200-205,261-265` ; `domain/download/policy.py:25` (`SKIP_DISK_CAP`) ; `events.py:112-131`
- **Description** : trois chemins log-only : add_link rejeté → FAILED ; nom dégénéré → skip ; candidat différé/sauté par `download_policy` (notamment plafond disque atteint). Seul `PromotionFailed` émet un event. Un **plafond disque atteint diffère SILENCIEUSEMENT tous les nouveaux candidats** (le crawler ne télécharge plus rien) sans aucun signal Prometheus ni alerte — angle mort de saturation. Le dashboard Grafana ne trace que queued/completed.
- **Correctif** : counter `emule_downloads_failed`, gauge `emule_download_disk_committed_bytes` ou counter `emule_downloads_deferred{reason}` (disk_cap, target_satisfied). Alerte OPERATIONS edge-triggered quand le plafond disque bloque.

#### [LOW] Daemon download injoignable n'émet pas d'`InstanceUnreachable`
- **Statut** : disputed (1 confirmed low, 1 refuted info) | **Perspective** : `observability#5`
- **Voir section « Contestés »**.

#### [LOW→info] Gauge `emule_crawler_up` jamais remise à 0 à l'arrêt propre
- **Statut** : confirmed (les deux → info) | **Perspective** : `observability#6`
- **Fichiers** : `composition/app.py:608-631` ; `policy.py:248-254` ; `events.py:89-91`
- **Description** : `CrawlerStarted` met `CRAWLER_UP=1.0` ; aucun event `CrawlerStopped` n'existe. La gauge reste figée à 1 jusqu'à disparition du scrape. Atténuation forte : le serveur `/metrics` est un thread daemon qui meurt avec le process → la valeur 1 n'est observable que tant que le scrape est possible ; la gauge ne porte donc quasi aucun signal indépendant du `up` synthétique de Prometheus.
- **Correctif** : émettre `CrawlerStopped` (set 0) avant `aclose`, OU documenter la délégation au `up` synthétique et préférer un counter de boot à une gauge.

---

### WebUI — sécurité, code, logique

#### [MEDIUM] Le lien eD2k du webui n'échappe pas le nom de fichier hostile (contrairement au crawler)
- **Statut** : confirmed (1 medium, 1 low) | **Perspective** : `code-smell#1`
- **Fichiers** : `webui/domain/format.py:4` (interpole le filename brut) ; `crawler/domain/download/ed2k_link.py:20` (`quote(...)`)
- **Description** : `format.ed2k_link()` interpole `{filename}` sans encodage dans `ed2k://|file|{filename}|{size}|{hash}|/`. Le crawler résout exactement ce risque via `urllib.parse.quote`. Les noms viennent du réseau eMule (hostile). Un `|` dans le nom décale les champs taille/hash → lien mal cadré, inutilisable ou pointant ailleurs. L'autoescape Jinja2 neutralise le XSS (href), mais pas la corruption du payload eD2k. Test webui ne couvre qu'un nom bénin.
- **Correctif** : aligner `format.ed2k_link` sur `build_ed2k_link` (percent-encoder avec le même jeu sûr). Idéalement partager la construction dans le package matching.

#### [MEDIUM→low] Le rang des tiers est réimplémenté dans le webui, désynchronisé de la source de vérité matching
- **Statut** : confirmed (1 low, 1 medium) | **Perspective** : `code-smell#0`
- **Fichiers** : `webui/domain/coverage.py:8,15` (`{download:3,notify:2,catalog:1}` + `.get(d[1],0)`) ; `matching/config.py:103` (`TIERS`) ; `matching/engine.py:99` (`_TIER_RANK` protégé par test `set(_TIER_RANK)==TIERS`)
- **Description** : le webui réinvente `_TIER_RANK` sans lien avec `TIERS`, avec convention divergente, et range tout tier inconnu à 0 (sous `catalog`). Le webui importe déjà `catalog_matching` → aucun obstacle d'archi. Aucun test de cohérence côté webui. Correct aujourd'hui (set fermé) ; latent : un 4e tier ou un renommage serait silencieusement mal classé → dashboard faux.
- **Correctif** : exposer l'ordre des tiers depuis `catalog_matching` et l'importer dans `coverage.py`. À défaut, test `set(_TIER_RANK) == TIERS` côté webui.

#### [LOW] Filtres vides (`?target=`, `?tier=`, `?verdict=`) masquent silencieusement tous les résultats
- **Statut** : confirmed (les deux, low) | **Perspectives** : `webui-security#0` + `code-smell#4` partiellement liés (pagination) — ici filtres
- **Fichiers** : `webui/composition/app.py:87-90` ; `adapters/catalog_read.py:208-219`
- **Description** : un paramètre présent mais vide renvoie `""` (pas `None`). `list_files` teste `if target is not None`, donc `""` ajoute `dec.target_id = ''` qui ne matche rien → 0 résultat sans message. Cas fréquent avec `<select>` à option vide. `?q=` vide est inoffensif (`LIKE '%%'`). SQL paramétré (pas d'injection).
- **Correctif** : normaliser `param or None` (ou `param.strip() or None`) dans `handle_files`.

#### [LOW] La pagination accepte `page <= 0` → OFFSET négatif
- **Statut** : confirmed (les deux, low) | **Perspectives** : `code-smell#4` + `webui-security#2` (**doublon fusionné**)
- **Fichiers** : `webui/composition/app.py:92` ; `adapters/catalog_read.py:227`
- **Description** : `page = int(page_raw)` n'attrape que `ValueError`, aucune borne inférieure. `?page=0` → OFFSET=-50. SQLite traite OFFSET négatif comme 0 → `page=0` et `page=1` rendent la même page silencieusement. Pas de crash, pas d'injection (paramétré). Correct par chance, pas par intention.
- **Correctif** : `page = max(1, int(page_raw))`.

#### [LOW] Aucune navigation de pagination côté UI alors que `list_files` pagine à 50
- **Statut** : confirmed (les deux, low) | **Perspective** : `webui-security#1`
- **Fichiers** : `adapters/catalog_read.py:226-227` ; `templates/files.html:19-35`
- **Description** : `list_files` applique LIMIT 50 OFFSET et lit `?page`, mais `files.html` n'expose aucun lien suivant/précédent ni indicateur. Au-delà de 50 fichiers, les résultats sont inaccessibles sauf à forger `?page=N`. La spec exige une pagination côté serveur via query-params (donc des liens rendus).
- **Correctif** : précalculer les liens page+/page- dans le handler, les rendre dans `files.html` (discipline view-model).

#### [LOW] MatchingExplainer (webui) passe l'`Any` de `yaml.safe_load` sans valider la racine
- **Statut** : confirmed (1 info, 1 low) | **Perspectives** : `type-safety#0` + `config-validation#6` (**doublon fusionné**)
- **Fichiers** : `webui/adapters/matching_read.py:38-42` ; contraste `targets_read.py:28-33`
- **Description** : `parse_matcher_config(None)` lève `AttributeError` opaque (`'NoneType' has no attribute 'get'`) sur un matcher.yaml vide/non-mapping, au lieu d'un `ConfigError` actionnable comme le fichier-sœur. Atténuation : pour le chemin **targets**, `load_targets` (qui garde) tourne AVANT dans `app.py:45-46`, donc ce sous-chemin n'est pas atteignable ; seul **matcher.yaml** est réellement non gardé (lecture unique, aucune garde ailleurs). Crash au boot, pas request-time, webui read-only.
- **Correctif** : répliquer la garde de `targets_read` (`raw is None` / non-dict → `ConfigError` nommant le chemin) avant `parse_matcher_config`.

#### [LOW] La conversion `FileObservation.to_candidate` est dupliquée à la main dans le webui
- **Statut** : confirmed (les deux, low) | **Perspective** : `code-smell#2`
- **Fichiers** : `webui/adapters/matching_read.py:63` ; `crawler/domain/observation.py:38`
- **Description** : `_BYTES_PER_MIB` + règle d'unités recopiés (frontière de package interdit d'importer `emule_indexer`). Si la conversion change côté crawler, l'explication recalculée du webui divergerait silencieusement de la décision persistée, sans test cross-package.
- **Correctif** : factoriser `candidate_from_metadata(...)` dans `catalog_matching` (où `FileCandidate` vit déjà), importé par les deux packages.

#### [LOW] Duplication quasi-intégrale de `FileRowDisplay` entre `handle_files` et `handle_target`
- **Statut** : confirmed (les deux, low) | **Perspective** : `code-smell#3`
- **Fichiers** : `webui/composition/app.py:107,200`
- **Description** : list-comprehension `FileRowDisplay(...)` byte-pour-byte identique (10 champs). Toute évolution de colonne doit être faite à deux endroits.
- **Correctif** : extraire un helper `_to_display_rows(file_rows)`.

#### [INFO] `request.path_params` (Any) passe directement aux lecteurs sans narrowing en str
- **Statut** : confirmed (les deux, info) | **Perspective** : `type-safety#2`
- **Fichiers** : `webui/composition/app.py:130,189`
- **Description** : `path_params` typé `dict[str, Any]` ; le converseur de chemin par défaut renvoie toujours str au runtime. Pure hygiène de typage.
- **Correctif** : annoter `ed2k_hash: str = request.path_params["ed2k_hash"]`.

#### [INFO] Lectures `sqlite3.Row` → dataclasses domaine via `Any` non contraints, de façon inégale
- **Statut** : confirmed (les deux, info) | **Perspective** : `type-safety#1`
- **Fichiers** : `catalog_repository.py:174,180,188` ; `webui/catalog_read.py:231` ; `webui/local_read.py:72`
- **Description** : incohérence — `download_repository.py` caste explicitement (`int(...)`, `str(...)`) ; `catalog_repository.py` injecte l'`Any` brut. Aucun bug actif (schéma SQL garantit les valeurs), mais frontière DB non vérifiée.
- **Correctif** : discipline unique de frontière (mapper qui caste chaque colonne).

#### [INFO] Aucun en-tête de sécurité (CSP, X-Content-Type-Options, etc.)
- **Statut** : confirmed (les deux, info) | **Perspective** : `webui-security#3`
- **Fichiers** : `webui/composition/app.py:241-251`
- **Description** : aucun middleware d'en-têtes de sécurité. Autoescape Jinja2 neutralise déjà le XSS ; bind 127.0.0.1 par défaut (binaire). Defense-en-profondeur manquante.
- **Correctif** : middleware posant CSP `default-src 'self'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`.

#### [INFO] 404 stylée uniquement pour `/files/{hash}` inconnu, pas pour les routes inexistantes
- **Statut** : confirmed (les deux, info) | **Perspective** : `webui-security#4`
- **Fichiers** : `webui/composition/app.py:136-137,241-251`
- **Description** : `404.html` rendu seulement par `handle_file_detail` ; toute autre URL inexistante → 404 texte brut de Starlette. Pas de fuite (404 neutre).
- **Correctif** : enregistrer un `exception_handler` 404 (et 500) rendant `404.html`.

#### [INFO] Docstring de `compact` mentionne des migrations figées (« 0001+0002 ») alors que `open_catalog` les applique toutes
- **Statut** : confirmed (les deux, info) | **Perspective** : `code-smell#5`
- **Fichiers** : `compact/compactor.py:2`
- **Description** : `_apply_migrations` applique TOUTES les `NNNN_*.sql` découvertes. Exact aujourd'hui mais rotera à la prochaine 0003.
- **Correctif** : « (toutes les migrations embarquées) ».

---

### Réseau, sandbox conteneur, déploiement

#### [MEDIUM→high] Le knob gVisor `CONTAINER_RUNTIME` ne couvre pas amuled (composant le plus exposé)
- **Statut** : confirmed (1 high, 1 medium) | **Perspective** : `deployment-packaging#2`
- **Fichiers** : `examples/sans-vpn-lowid.yaml:10-13` ; `examples/gluetun.yaml:30-34` ; `bricks/compose.core.yaml:19`
- **Description** : le `runtime` est appliqué à crawler/verifier/webui mais PAS à `amuled`, qui parse en continu du trafic eD2k/Kad hostile sur image tierce. **Plus grave que le knob seul** : amuled n'a AUCUN durcissement plancher (pas de cap_drop, read_only, no-new-privileges, user, pids/mem_limit), contredisant l'invariant MVP §10.3 (« amuled sandboxé… s'applique aux DEUX modes ») alors que §10.2 modélise « RCE amuled » comme menace. L'exclusion gVisor seule est documentée (images tierces sur runc), mais l'absence du plancher portable ne l'est pas. En highid, un port entrant est redirigé vers amuled.
- **Correctif** : aligner amuled sur le floor (cap_drop ALL, no-new-privileges, pids/mem_limit) — faisable comme freshclam (image tierce déjà durcie) — et propager `runtime`. Documenter toute exclusion délibérée.

#### [MEDIUM] WebUI : lecture SQLite WAL sur volume `:ro` + rootfs read_only échoue
- **Statut** : confirmed (les deux, reproduit empiriquement) | **Perspective** : `deployment-packaging#1`
- **Fichiers** : `webui/adapters/db.py:15` (`mode=ro`) ; `bricks/compose.core.yaml:174-175` (`:ro`) ; `crawler/.../connection.py:84` (WAL)
- **Description** : un lecteur `mode=ro` d'une base WAL doit créer/mmaper le `-shm` pour prendre son verrou. Sur un répertoire `:ro` sans `-shm` vivant (crawler arrêté/redémarré, checkpoint propre, premier boot), reproduit sous SQLite 3.47.1 : `OperationalError: attempt to write a readonly database`. Avec un writer vivant, ça passe. `/health` renvoie un JSON statique sans toucher la DB → conteneur `healthy` alors que l'UI est cassée (fausse confiance). Le projet connaît le problème (doc de référence) mais le compose livré garde `:ro` et le statut est « question ouverte non validée ».
- **Correctif** : donner au lecteur l'accès écriture au répertoire (retirer `:ro` ; `query_only=ON`+`mode=ro` suffisent à interdire les écritures applicatives), OU chemin de lecture WAL-aware. `immutable=1` n'est PAS correct (writer concurrent vivant).

#### [HIGH→medium] L'image webui n'est jamais construite/publiée par la CI mais le compose la tire de ghcr.io
- **Statut** : disputed (`deployment-packaging#0` : 1 refuted low, 1 confirmed medium) / confirmed (`docs-drift#0` : 1 medium, 1 high) | **Perspectives** : `deployment-packaging#0` + `docs-drift#0` (**doublon fusionné — verdicts mitigés**)
- **Fichiers** : `.github/workflows/images.yml:34-41` (matrice publish = crawler+verifier seulement) ; `bricks/compose.core.yaml:159-164` (image webui ghcr.io, profils observer+download) ; `docs/runbook-deployment.md:149-159`
- **Description** : l'image `emule-indexer-webui` n'est jamais poussée sur GHCR. Le service webui a un bloc `build:`, donc le flux `docker compose ... up -d` **construit webui localement** (pas de `manifest unknown`) — c'est le point de désaccord : un vérificateur réfute l'impact « high / échec au démarrage ». En revanche, le flux `docker compose ... pull` documenté (runbook étape 4) échoue spécifiquement pour webui (`pull` tente les services buildables sauf `--ignore-buildable`). Le commentaire `images.yml:22` dit toujours « 2 images ».
- **Correctif** : ajouter `webui` à la matrice `publish` (image `...-webui`, `packages/webui/Dockerfile`), corriger le commentaire, étendre le smoke si pertinent.

#### [MEDIUM→low] WebUI exposée par défaut sur 0.0.0.0 de l'hôte sans auth
- **Statut** : confirmed (1 medium, 1 low) | **Perspectives** : `security-network#0` + `docs-drift#3` (**doublon fusionné** — exposition + absence d'avertissement runbook)
- **Fichiers** : `bricks/compose.core.yaml:178-179` (`"${WEBUI_PORT:-8080}:8080"` sans IP de bind), `:167` (`WEBUI_HOST: 0.0.0.0`) ; `docs/runbook-deployment.md` (ne mentionne ni webui ni 8080 ni reverse proxy)
- **Description** : le mapping de port non qualifié publie sur 0.0.0.0 de l'hôte. Service dans profils observer+download → démarre dès `--profile observer up -d` (scénario le plus simple). Aucune auth/TLS. Le runbook **de déploiement** (document pour monter la stack) n'avertit jamais ; seul le runbook-administration en parle. Un opérateur sur VPS expose le catalogue complet (noms de fichiers, hashes eD2k, métadonnées) sans le savoir. Blast radius limité par l'invariant « le sujet est le fichier » (pas de PII).
- **Correctif** : binder sur loopback par défaut (`"127.0.0.1:${WEBUI_PORT:-8080}:8080"`) ; ajouter une note au runbook-deployment (étape 5/6) renvoyant vers la section reverse proxy.

#### [LOW] Prometheus et Grafana sans aucun durcissement conteneur
- **Statut** : confirmed (les deux, low) | **Perspective** : `deployment-packaging#3`
- **Fichiers** : `bricks/compose.core.yaml:127-157`
- **Description** : prometheus/grafana sans cap_drop, no-new-privileges, user, read_only, pids/mem_limit, contrairement à crawler/verifier/webui (et même freshclam, image tierce, qui porte cap_drop ALL + no-new-privileges). Grafana publie un port hôte (protégé par mot de passe obligatoire). Écart de cohérence de posture.
- **Correctif** : appliquer au moins `cap_drop: [ALL]` + `no-new-privileges:true` (comme freshclam), user non-root, pids/mem_limit.

#### [LOW] docker-socket-proxy accepte toute source (`-allowfrom=0.0.0.0/0`) sur le réseau `ec` partagé
- **Statut** : confirmed (les deux, low) | **Perspective** : `security-network#1`
- **Fichiers** : `examples/gluetun.yaml:44-64`
- **Description** : le proxy écoute `0.0.0.0:2375` `-allowfrom=0.0.0.0/0` ; seul filtre = méthode+chemin (`-allowPOST=.../containers/amuled/restart`), pas la source. amuled (network_mode service:gluetun, alias sur `ec`) peut atteindre le proxy. Le rate-limit port-sync vit côté crawler, contourné par un appel direct. Pire cas : DoS auto-infligé (amuled se redémarre). Pas d'escalade (socket `:ro` + allowlist d'un seul chemin). Imprécision du finding : le §416 du design place le proxy sur `ec`, pas un réseau dédié.
- **Correctif** : restreindre `-allowfrom` au sous-réseau crawler, ou isoler le proxy sur un réseau dédié crawler↔proxy.

#### [LOW] docker-proxy : `DOCKER_GID` défaut 0 (groupe root)
- **Statut** : confirmed (les deux, low) | **Perspective** : `deployment-packaging#5`
- **Fichiers** : `examples/gluetun.yaml:48-54` (`user: "65534:${DOCKER_GID:-0}"`) ; `.env.example:15` (vide)
- **Description** : sur hôte rootful (socket `660 root:docker`), gid 0 ≠ groupe docker → le proxy ne peut pas lire le socket → restart échoue en 403 silencieux → reste Low-ID (échec absorbé, pas de fail-fast). Le `:-0` masque la config manquante. Cadrage « groupe root » surestimé (gid 0 donne MOINS d'accès, pas une escalade).
- **Correctif** : `${DOCKER_GID:?renseigner le GID du groupe docker}` pour fail-fast.

#### [LOW] gluetun épinglé sur `:latest` (frontière VPN = invariant anonymat)
- **Statut** : confirmed (les deux, low) | **Perspective** : `deployment-packaging#4`
- **Fichiers** : `examples/gluetun.yaml:12` ; `bricks/compose.core.yaml:112` (clamav `:1.4`)
- **Description** : seule image non épinglée alors que toutes les autres le sont. Le port-sync dépend explicitement du COMPORTEMENT d'une version gluetun (auth-by-default v3.40, route `/v1/portforward`) — le design suppose une version épinglée que le compose n'épingle pas. Un `pull` ultérieur peut casser le port-sync ou régresser le kill-switch sans contrôle. clamav `:1.4` (patchs) est défendable.
- **Correctif** : épingler gluetun sur une version (idéalement par digest pour les images réseau-critiques).

#### [INFO] Le crawler PROD a un egress hors-VPN (apprise/DNS) sur le même process que l'EC
- **Statut** : disputed (1 confirmed info, 1 refuted info) | **Perspective** : `security-network#2`
- **Voir section « Contestés »**.

#### [LOW→refuted] clamav-db : coordination propriété/permissions freshclam↔verifier
- **Statut** : refuted (les deux) | **Perspective** : `deployment-packaging#6`
- **Voir section « Écartés »**.

---

### Tests, validation de config, drift docs

#### [HIGH→high] Restart d'amuled raté masque DÉFINITIVEMENT le mismatch de port (High-ID jamais retrouvé)
- **Statut** : confirmed (les deux, high) | **Perspective** : `test-gaps#0`
- **Fichiers** : `port_sync_loop.py:102-107,114-127` ; `mule_ec/client.py:195-215` (set persiste la pref, pas de rebind) ; `tests/.../test_run_port_sync_cycle.py:70-73`
- **Description** : le cycle aligne via `set_listen_port(live)` (persiste la préférence) PUIS `restart()`. Si set réussit mais `restart()` lève `RestarterError`, le cycle alerte et `return` AVANT `record_restart` (rate-limit jamais armé). Au cycle suivant, `current = get_listen_port() == live` (la pref écrite) → la garde `live == current` est satisfaite → `edge.leave(_MISMATCH)` **efface l'alerte**, alors qu'amuled n'a JAMAIS rebindé et écoute toujours l'ancien port (Low-ID). Une seule panne transitoire **stalle PERMANENTEMENT** la récupération High-ID et éteint le signal OPERATIONS. Le test ne le détecte pas car `FakePortPreferences.set_listen_port` n'actualise pas `_current_port`.
- **Impact** : crawler reste Low-ID en silence — défaite du seul but du port-sync.
- **Correctif** : comparer le port forwardé au port RÉELLEMENT bound (via `network_status`/connstate) plutôt qu'à la préférence ; ou tracer un état `restart_pending` jusqu'à confirmation High-ID. Faire que le fake modélise set→get.

#### [HIGH→medium] Règle de matching avec liste d'opérandes vide (`all: []`/`any: []`) → matche TOUS les fichiers
- **Statut** : confirmed (1 high, 1 medium) | **Perspective** : `config-validation#0`
- **Fichiers** : `matching/validation.py:94-107,157-172` ; `combinators.py:24-31`
- **Description** : `_parse_condition` ne vérifie jamais que la liste d'un `all`/`any` est non vide. `AllMatcher([]).matches() == all([]) == True`. Reproduit : `{name:pwn, tier:download, all:[]}` est accepté et matche inconditionnellement tout candidat → `tier=download`. Chaîne aval : `record_observations` agit sur `tier==download`, `run_download_cycle` rejoue `download_decisions()` → auto-download. Viole l'EBNF §8.3 (`operand (',' operand)*` = ≥1) et le fail-fast §8.4. Nuance : `download_policy` impose des gardes (status, disk_cap, file_size), donc pas un téléchargement littéralement inconditionnel — mais les cibles lost/partial passent la plupart des gardes, rayon d'impact large.
- **Correctif** : rejeter une liste vide pour `all`/`any` (`ConfigError`). Garder `all([])/any([])` comme sémantique interne des combinateurs.

#### [MEDIUM] `coverage.min`/`fuzz` hors [0,1] acceptés silencieusement → token coverage perpétuellement faux
- **Statut** : confirmed (1 medium, 1 low) | **Perspective** : `config-validation#2`
- **Fichiers** : `matching/validation.py:133-142,78-91` ; `matchers.py:79-97`
- **Description** : `min`/`fuzz` (fractions logiques [0,1]) sans borne. `{coverage:title, min:5.0, fuzz:99}` accepté. `min>1` → `value() <= 1` toujours → ne matche jamais. `fuzz>1` → `ratio/100 >= fuzz` toujours faux → value=0. Erreur fréquente : `min:60` au lieu de `0.6` rend une règle silencieusement inerte, sans signal au chargement.
- **Correctif** : valider `0.0 <= min <= 1.0` et `0.0 <= fuzz <= 1.0` (pour CoverageDef et overrides TokenRef). `ConfigError` actionnable.

#### [MEDIUM→low] `attr_between` accepte min > max sans avertissement
- **Statut** : likely (1 confirmed low, 1 uncertain low) | **Perspective** : `config-validation#1`
- **Fichiers** : `matching/validation.py:143-153` ; `matchers.py:123-129`
- **Description** : `{attr_between: size_mb, min: 600, max: 30}` accepté → matche toujours False (règle muette pour toujours). **À noter** : la partie « absence de bornes = test de présence » du finding est REFUTÉE — c'est un comportement délibéré, documenté et testé (bornes ouvertes). Seul `min > max` est le vrai trou de fail-fast. Déclenché par erreur de saisie opérateur ; config relue en PR.
- **Correctif** : si min et max non nuls, exiger `min <= max` (`ConfigError` sinon).

#### [MEDIUM] Une cible `{date_alt}` sans `broadcast_date` fait crasher la construction du `MatchingEngine` au démarrage
- **Statut** : confirmed (les deux, reproduit) | **Perspective** : `test-gaps#2`
- **Fichiers** : `matching/validation.py:340-354` (`_PROBE_TARGET` a toujours une date) ; `resolver.py:103-104` ; `interpolation.py:83-89` ; `composition/app.py:527`
- **Description** : la config canonique (`air_date: {regex: '{date_alt}'}`) + une vraie cible lost-media sans date connue (cas naturel, `parse_targets` l'accepte) → `resolve_all` interpole par cible → `InterpolationError`. `validate_config` ne valide les regex que via `_PROBE_TARGET` (toujours daté), donc passe. `InterpolationError` n'hérite PAS de `ConfigError`, non capturée à `app.py:527` → **crash opaque au boot**. Pire : `validate-config` (qui n'instancie jamais MatchingEngine) afficherait « Config valide » — fausse réassurance.
- **Correctif** : à la validation, vérifier que toute cible référencée par une règle utilisant `{date_alt}` possède un `broadcast_date` → `ConfigError` fail-fast. Test d'intégration engine + cible-sans-date.

#### [MEDIUM→low] Le schéma DDL du webui est dupliqué à la main, jamais croisé avec les migrations réelles
- **Statut** : confirmed (les deux, low) | **Perspective** : `testing-quality#0`
- **Fichiers** : `webui/tests/conftest.py:13,63` ; `migrations/catalog/0001_initial.sql`
- **Description** : le conftest recrée les tables par `executescript` copié à la main (frontière de package interdit d'importer `emule_indexer`). Le DDL diverge déjà (omet REFERENCES, triggers append-only, table `file_observation_ranges`). Aucun test ne compare au `.sql` source. Une migration crawler renommant une colonne lue laisserait la suite webui verte sur l'ancien schéma → couverture « fausse » vis-à-vis du contrat. Latent (les colonnes lues existent à l'identique aujourd'hui ; readers utilisent des listes de colonnes explicites).
- **Correctif** : test de contrat chargeant les `.sql` réels (chemin relatif, sans import) dans une base temp, vérifiant que les colonnes SELECTionnées existent ; ou générer le DDL des fixtures depuis les `.sql`.

#### [MEDIUM] merge/compact n'ont aucune assertion de régression sur l'immutabilité de la DB source
- **Statut** : confirmed (1 medium, 1 low) | **Perspective** : `testing-quality#1`
- **Fichiers** : `tests/merge/test_merger.py` ; `tests/compact/test_compactor.py` ; `merger.py:193` ; `compactor.py:116`
- **Description** : invariant « ne mutent jamais une DB en place ». Le code ATTACH la source en lecture-écriture (pas `mode=ro`), et aucun test ne vérifie que le fichier source est intact après l'opération (assertions uniquement sur la SORTIE). Un bug futur (INSERT/UPDATE visant `src.` par erreur) écrirait dans la source sans qu'aucun test n'échoue. **Correction** : `mode=ro` a été délibérément rejeté (spec §8, exigerait de changer `open_catalog`) → seul le test snapshot est le bon correctif, pas le durcissement.
- **Correctif** : test snapshot (hash/count par table) de la source AVANT, revérifié APRÈS.

#### [LOW] `verifier_http` n'impose pas l'enum fermé de verdict → cardinalité de label non bornée
- **Statut** : confirmed (les deux, low) | **Perspective** : `test-gaps#4` (**doublon de `observability#0`, voir section observabilité**)
- *(Fusionné ci-dessus dans `observability#0`.)*

#### [MEDIUM→low] Plusieurs tests d'intégration HTTP du webui n'assertent que le code 200
- **Statut** : likely (1 confirmed low, 1 uncertain info) | **Perspective** : `testing-quality#2`
- **Fichiers** : `tests/test_webui_app.py:308,346,370,393,500`
- **Description** : tests verdict-filtre vide, explanation=None, raccourci /targets, page non numérique = status 200 seul. Test media-fields annonce « explication calculée » mais n'assert que `'S2E062A' in text` (qui vient du bloc decision, indépendant de l'explication). **Désaccord** : le filtrage verdict est déjà couvert par des tests unitaires d'adaptateur ; le fix suggéré (`'22 min'/'192 kbps'`) est IMPOSSIBLE (aucun template ne rend duration/bitrate). La bonne assertion serait la chaîne d'explication / l'absence du hash filtré.
- **Correctif** : ajouter des assertions sur le HTML pertinent (absence du hash dans la table filtrée ; présence de la chaîne d'explication ; même contenu que page=1).

#### [MEDIUM] README racine obsolète : profil `full` inexistant, webui/matching absents, statut périmé
- **Statut** : confirmed (1 medium, 1 low) | **Perspective** : `docs-drift#1`
- **Fichiers** : `README.md:24-25,47-49,60-63`
- **Description** : annonce les profils `observer`/`full` (réels : observer/download/monitoring) ; `--profile full` ne démarre rien. Ne liste que les tests crawler+verifier (matching et webui omis). Statut figé à « fondations posées ». **Correction** : `full` n'a jamais existé côté compose (vestige des specs, pas un renommage). Aucun impact code ; document d'entrée trompeur.
- **Correctif** : `observer`/`download`, ajouter matching+webui aux tests, mettre à jour le bloc Statut.

#### [LOW] CLAUDE.md indique « three packages » alors qu'il y en a quatre (webui)
- **Statut** : confirmed (les deux, low) | **Perspective** : `docs-drift#2`
- **Fichiers** : `CLAUDE.md:9,67`
- **Description** : intro « three packages » et « span all three packages » omettent webui, alors que ruff/mypy listent `packages/webui/`. Incohérence interne (le tableau et Commands citent bien webui). Ligne 28 dit aussi « future webui » alors qu'il existe.
- **Correctif** : « four packages », ajouter webui, corriger « all three » → « all four » et « future webui ».

#### [LOW] runbook-administration décrit `/node` de façon trompeuse (« connexions, état réseau amuled »)
- **Statut** : confirmed (les deux, low/info) | **Perspective** : `docs-drift#4`
- **Fichiers** : `docs/runbook-administration.md:207` ; `webui/composition/app.py:222-235` ; `local_read.py:62-75`
- **Description** : `/node` ne lit que `local.db` (downloads, verification_tasks, scheduler_state, node_runtime). Aucune connexion EC / état réseau. Un opérateur attendrait de la connectivité eD2k/Kad qu'il ne trouvera pas.
- **Correctif** : « État local du nœud : téléchargements, file de vérification, ordonnancement, identité ».

#### [LOW] runbook-administration affiche `WEBUI_HOST` défaut `0.0.0.0` alors que le défaut applicatif est `127.0.0.1`
- **Statut** : confirmed (les deux, low) | **Perspective** : `docs-drift#5`
- **Fichiers** : `docs/runbook-administration.md:218` ; `webui/__main__.py:11,41`
- **Description** : le tableau « Valeur par défaut » donne `0.0.0.0`, mais le défaut du binaire est `127.0.0.1` ; le `0.0.0.0` vient du compose. Confusion de diagnostic hors-compose.
- **Correctif** : indiquer le défaut applicatif réel `127.0.0.1` et préciser que le compose le surcharge.

#### [LOW] testing-guide décrit le gate à 2 paquets alors qu'il en compte 4
- **Statut** : confirmed (les deux, low) | **Perspective** : `docs-drift#6`
- **Fichiers** : `docs/testing-guide.md:28-31,280-282` ; `CLAUDE.md:56-65`
- **Description** : le guide (« extrait du code réel ») présente le gate avec crawler+verifier seulement ; le gate réel (ci.yml, pre-push) lance matching+crawler+verifier+webui + garde templates. Un contributeur croirait valider en sautant matching/webui.
- **Correctif** : mettre à jour §1 et §5 pour les 4 suites + garde templates.

---

## Contestés (disputed / uncertain)

| Finding | Statut | Point de désaccord |
|---|---|---|
| `error-boundary#3` — timeout interne runner peut crasher l'enfant au lieu d'un poison propre | disputed | Vérif 1 confirme (info, faits exacts) ; vérif 2 réfute : c'est un chemin **maîtrisé, voulu, documenté (egress.py, ffprobe.py, clamav.py) et testé** (`test_nonzero_returncode_is_suspicious`), pas un défaut. La seule « amélioration » serait une ligne de docstring déjà redondante. |
| `logic-download#3` — `_build_expected` lève `RepositoryError` → tâche bloquée 15 min | disputed | Vérif 1 confirme (low, chemin atteignable) ; vérif 2 réfute : comportement **délibéré et documenté** (« la task repart par le lease »), et la prémisse « base distincte » est **fausse** (targets et queue partagent la même `local_conn`), donc le fix « fail+retry immédiat » lèverait aussi. |
| `test-gaps#3` — `downloads.state` sans CHECK → `ValueError` non enveloppée crashe la boucle | disputed | Vérif 1 réfute : **aucun writer atteignable** ne produit de valeur invalide (seuls `_INSERT 'queued'` et `set_state(state.value)` typé `DownloadState`) ; le finding concède « impossible avec les writers actuels ». Vérif 2 confirme (low) le mécanisme latent (mutation hors-bande / corruption / migration future). Consensus : durcissement defense-en-profondeur, non atteignable en fonctionnement normal. |
| `security-network#2` — egress crawler hors-VPN (apprise/DNS) | disputed | Les deux vérifs s'accordent sur les faits (info) ; l'un confirme l'exactitude factuelle, l'autre réfute en tant que « défaut » : c'est un **choix de design délibéré et documenté** (le kill-switch P2P reste effectif, seule la corrélation IP↔webhook de notification subsiste, tradeoff assumé). |
| `observability#5` — daemon download injoignable n'émet pas `InstanceUnreachable` | disputed | Vérif 1 confirme (low, asymétrie réelle) ; vérif 2 réfute : la **taxonomie E-D5 range `InstanceUnreachable` sous Recherche uniquement** ; la tolérance silencieuse est spec'ée (§9), et `InstanceUnreachable(instance=...)` est inapplicable (boucle download mono-instance, pollerait le compteur search-keyé). |
| `logic-search#1` — `bucketize` repose sur un tri non vérifié | uncertain | Mécanique exacte mais **aucun chemin atteignable** : l'unique appelant (`_bucketize_old`) alimente via `_SELECT_OLD` se terminant par `ORDER BY ed2k_hash, observed_at, id`, qui garantit la contiguïté. Remarque defense-en-profondeur, pas un bug présent. |
| `data-persistence#1` — `cutoff_date` sans normalisation UTC | uncertain (likely) | Asymétrie de discipline réelle, mais non atteignable via le CLI (défaut `utc_now` aware UTC) ; partition reste complémentaire. Fragilité latente. |
| `logic-download#5` — `_ERROR_RESULT` singleton avec `real_meta` mutable | uncertain | Faits exacts mais mitigé par le typage `Mapping[str, object]` (read-only sous mypy strict) + frozen ; le seul consommateur ne fait que `json.dumps` en lecture. Smell stylistique, pas piège atteignable en l'état. |
| `config-validation#1` — `attr_between` min>max | likely (uncertain) | La sous-affirmation « absence de bornes = bug » est **fausse** (comportement voulu/testé) ; seul `min > max` est un vrai trou de fail-fast (low). |
| `docs-drift#7` — commentaires « 2 paquets »/« 2 images » obsolètes | uncertain | Volet pre-push « 2 paquets » CONFIRMÉ (teste 4) ; volet `images.yml:22` « 2 images » **REFUTÉ** : le build smoke ne construit effectivement que 2 images (amuled n'a pas de bloc `build:`), le commentaire est exact et le fix « 3 images » l'introduirait en erreur. |

---

## Écartés après vérification (refuted)

- **`deployment-packaging#6` — clamav-db coordination propriété/permissions freshclam↔verifier** (refuted par les deux). Vérification empirique sur l'image réelle `clamav/clamav:1.4` : répertoire `2755` (world-traversable), fichiers de base `644` (world-readable), freshclam écrit en `0644` en dur. Le lecteur uid 999 lit la base malgré le décalage d'uid (accès accordé par les bits world-read, pas par alignement d'uid). Le verdict `suspicious` permanent par incompatibilité de droits est non-reproductible.

---

## Couverture & angles morts

**Audité** (statique + reproductions ciblées) :
- Verifier : event loop, timeouts, rlimits/seccomp, parsing config, egress/poison, sniff puremagic (reproduit empiriquement avec puremagic 2.2.0).
- Crawler : boucles search/download/verification, promotion/quarantaine, protocole EC (codec, classification d'erreur, progress Kad), persistance SQLite (merge/compact, transactions), observabilité (events/policy/metrics).
- Matching : validation de config (combinators vides, bornes coverage/attr_between, interpolation date_alt), reproductions de l'engine.
- WebUI : sécurité (échappement eD2k, en-têtes, 404, exposition port), pagination, filtres, typage frontière, schéma de test.
- Packaging/déploiement : matrice CI images, durcissement conteneur, épinglage d'images, socket-proxy, WAL `:ro` (reproduit sous SQLite 3.47.1).
- Drift docs (README, CLAUDE.md, runbooks, testing-guide).

**Non couvert / non validable à la lecture seule** :
- **Comportement vrai matériel** : la plupart des suites d'intégration (Docker/testcontainers/ffmpeg, marqueurs `*_integration`) ne tournent pas dans le sandbox (pas de veth) — explicitement noté pour le WAL `:ro` (`deployment-packaging#1`, « question ouverte »), le timeout clamav 30 s vs 120 s (`sandbox-confinement#1`, non validé contre vrai média), le build Docker webui (`docs-drift#0`/`deployment-packaging#0`, image jamais construite/lancée), et les tests port-sync sur Docker rootful (`test-gaps#0`, non testable hors serveur rootful).
- **Durées empiriques réelles** : les seuils de déclenchement de plusieurs findings (analyses lentes > 10 s/30 s, scans clamav) dépendent du comportement réel d'amuled/ffprobe/clamscan contre du vrai trafic eMule, jamais observé ici.
- **Topologie multi-instance N≥2** : `logic-search#0` ne se manifeste qu'avec plusieurs workers ; le comportement asyncio a été reproduit en isolation mais pas dans un déploiement réel.
- **Scénarios de RCE/compromission** : `sandbox-confinement#2/#4` et l'angle compromission de `observability#0` supposent une RCE préalable non démontrée.

---

## Priorisation recommandée

**P0 — perte de données / sécurité fonctionnelle (à corriger d'abord)** :
1. `logic-download#0` — promotion idempotente (+ `logic-download#1` : corriger le fake pour qu'il révèle le bug). *Perte définitive de fichiers, objectif cœur.*
2. `test-gaps#0` — port-sync : comparer au port réellement bound, pas à la préférence. *High-ID perdu en silence.*
3. `config-validation#0` — rejeter `all: []`/`any: []`. *Auto-download de tout le réseau sur une faute de frappe.*
4. `config-validation#4`/`test-gaps#1` — valider `ENABLED_CHECKS` contre l'enum fermé. *Fail-open antivirus silencieux.*

**P1 — disponibilité / posture sécurité / faux verdicts** :
5. `sandbox-confinement#0`/`concurrency-async#0` + `concurrency-async#1` — sortir l'analyse de l'event loop ET aligner le timeout client. *Fichiers sains en dead-letter, /health gelé.*
6. `error-boundary#0` + `config-validation#3` — fail-fast de la config verifier au boot (résoudre `AnalysisConfig` une fois, valider les planchers). *Config invalide brûle les tâches / désarme le confinement.*
7. `sandbox-confinement#1` — réconcilier `timeout_s` et `RLIMIT_CPU` en mode clamav. *Faux `suspicious` sur médias sains.*
8. `deployment-packaging#2` — durcir amuled (floor + runtime). *Composant le plus exposé non confiné.*
9. `deployment-packaging#1` — WAL `:ro` du webui (retirer `:ro`, garder `query_only`). *UI cassée par intermittence, masquée par /health.*
10. `code-smell#1` — échapper le nom dans le lien eD2k du webui.
11. `test-gaps#2` — valider `{date_alt}` + `broadcast_date` au chargement. *Crash opaque au boot.*

**P2 — observabilité, cohérence config, sécurité par défaut** :
12. `observability#1` — instrumenter les `RepositoryError`. `observability#0` — borner le label `verdict`. `observability#4` — instrumenter le plafond disque.
13. `error-boundary#1` — absorber les `RepositoryError` de fin de cycle search.
14. `config-validation#2` — borner `coverage.min`/`fuzz` à [0,1].
15. `security-network#0`/`docs-drift#3` — binder le webui sur loopback + avertir le runbook-deployment.
16. `deployment-packaging#0`/`docs-drift#0` — publier l'image webui en CI.

**P3 — robustesse defense-en-profondeur, dette de cohérence, drift docs** :
17. Findings `info`/`low` restants : durcissements (`sandbox-confinement#2/#3/#4`, `protocol-ec#0`, `deployment-packaging#3/#4/#5`, `security-network#1`), dé-duplication code (`code-smell#0/#2/#3`, `data-persistence#2`), tests (`testing-quality#0/#1/#2`), drift docs (`docs-drift#1/#2/#4/#5/#6`, `code-smell#5`).
18. Décisions à prendre sur les **contestés** (documenter le choix délibéré ou corriger) : `error-boundary#3`, `logic-download#3`, `observability#5`, `security-network#2`, `test-gaps#3`.
