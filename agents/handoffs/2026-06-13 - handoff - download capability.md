# Handoff — emule-indexer (capacité de téléchargement, D-download)

> **But** : reprendre naturellement après **D-download** (la 1re moitié du « Plan D »
> auto-download + verifier). Lis d'abord le handoff précédent
> (`2026-06-13 - handoff - orchestration complète.md`, le crawl loop) : celui-ci s'appuie
> dessus et ne le répète pas. Le « Plan D » se compose de **D-download (ici)** + **D-verify
> (suivant)** ; le **tag de jalon n'est PAS posé** — il arrivera à la fin de D-verify.
>
> **Dernière mise à jour** : 2026-06-13, après la fusion de D-download sur `main` (non taggé).
> C'est le handoff le plus récent (postérieur à `… - orchestration complète.md`).

---

## 1. TL;DR

- **Ce qui est fait** : le crawler **sait télécharger** (capacité LIVRÉE et TESTÉE, mais **pas
  encore câblée** dans `CrawlerApp`). La boucle `run_download_cycle`/`download_loop` rejoue le
  journal `match_decisions` (verdict le plus récent = tier `download`), télécharge via EC
  (`add_link` + `download_queue`), réconcilie l'état, **promeut en quarantaine** (rename
  atomique `os.replace`, **jamais lu, jamais +x**) et **enfile une tâche de vérification**.
  Politique PURE : status-gate (cible `complete` → skip) + dédup + **plafond disque applicatif**
  (somme des `size_bytes` actifs ; au-dessus → on diffère, jamais d'abandon).
- **Trois sous-paquets/couches** : `domain/download/` (PUR : `states`/`policy`/`ed2k_link`),
  ports (`MuleDownloadClient`+`DownloadEntry`, `Quarantine`), adapters (EC `add_link`/
  `download_queue` + opcodes, `quarantine_fs`, `SqliteDownloadRepository` + migration
  `local/0002`), lecture catalogue (`download_decisions`/`last_observation`), config optionnelle
  (`DownloadConfig` + endpoint/dirs), application (`run_download_cycle`/`download_loop`).
- **Gate** : **614 tests, 100 % branch** ; ruff/format/mypy/sqlfluff verts. **e2e opt-in**
  (`download_integration`) **VERT contre un `amuled` réel** (Docker) — c'est ce qui fait foi :
  un lien de taille réelle est accepté et **apparaît dans `download_queue`** (hash décodé,
  `size_done < size_full`).
- **PAS de tag** (D-verify clôt le jalon). **`composition/app.py` INCHANGÉ** : le câblage live
  + le gate full-mode `VERIFIER_URL` sont D-verify (ils ont besoin du port `ContentVerifier`).
- **Prochaine étape** : **D-verify** (verifier trivial NO-OP d'abord, boucle de vérification,
  câblage live des DEUX boucles, gate full-mode). **Brainstormer d'abord**, comme toujours. La
  spec existe déjà : `docs/superpowers/specs/2026-06-13-verification-pipeline-design.md`.

## 2. État vérifiable

- Spec : `docs/superpowers/specs/2026-06-13-download-orchestration-design.md`. Plan exécuté :
  `docs/superpowers/plans/2026-06-13-crawler-mvp-06-download-orchestration.md` (13 tâches).
- Gate **5 checks** inchangé + **un 3e e2e opt-in** : `uv run pytest -m download_integration
  --no-cov` (Docker ; `ngosang/amule:3.0.0-1` ; déselectionné par défaut, HORS coverage — comme
  `ec_integration`/`orchestration_integration`). Au run par défaut : **6 deselected** (4 ec + 1
  orchestration + 1 download).
- Opcodes/tags EC download validés empiriquement :
  `agents/reference/2026-06-13-ec-download-opcodes.md` (SOURCE / EMPIRIQUE / PENDING).
- **AUCUN tag** : `git tag --list | grep -E "download|0\.8"` → vide (attendu).

## 3. Contrats que D-verify doit respecter / brancher

1. **Câblage live dans `CrawlerApp`** (le gros morceau de D-verify) : monter une **2e connexion
   EC** (`download_endpoint`, sa propre `AmuleEcClient` — peut viser un daemon dédié ou partagé),
   un `SqliteDownloadRepository`, un `FilesystemQuarantine(quarantine_dir)`, résoudre
   `staging_path_for` sur le **vrai layout amuled** (DÉCISION D2 — c'est le point ouvert, voir §5),
   et lancer `download_loop(DownloadLoopDeps(...))` dans le `TaskGroup` de `CrawlerApp` (à côté du
   crawl loop). L'arrêt observable existant annule la tâche au prochain `await` (déjà testé, §4).
   Le gate full-mode (`VERIFIER_URL` présent + health-check fail-fast) a besoin du port
   `ContentVerifier` — il atterrit donc en D-verify, pas avant.
2. **Nudge** (DÉCISION D13) : la boucle s'abonne au sujet FIXE `DOWNLOAD_NUDGE_SUBJECT =
   "download"`. Pour que le nudge réveille la boucle au changement de verdict, **brancher un
   `signal("download")` côté producteur** (le pipeline `record_observations` du Plan C signale
   déjà le hub avec le `ed2k_hash` ; ajouter un `signal("download")` quand une décision tier=
   download est écrite). Sinon le **poll de repli** (`download.poll_interval_seconds`) suffit — un
   nudge perdu est inoffensif (même contrat best-effort que le Plan C).
3. **File de vérification** : `run_download_cycle` est désormais le **PRODUCTEUR**
   (`enqueue_verification(hash)` après quarantaine réussie, idempotent via l'index unique
   partiel). D-verify est le **CONSOMMATEUR** (`claim`/`complete`/`fail`/`reclaim` du repo local,
   déjà construit dans le modèle de données).
4. **`staging_path_for` / chemin staging réel** : DÉCISION D2 — EC n'expose PAS le chemin du
   fichier complété. `DownloadEntry` ne porte que `(ed2k_hash, size_done, size_full)`. La boucle
   reçoit une fonction injectée `staging_path_for(entry) -> Path` ; D-verify doit la dériver du
   `staging_dir` configuré + la **convention de nom amuled** (à VALIDER au homelab via
   `tools/download_probe.py` contre un vrai fichier à sources — voir la section PENDING du
   rapport d'opcodes). `staging` et `quarantine` DOIVENT être sur le **même système de fichiers**
   (sinon `os.replace` lève — contrainte de déploiement à câbler/valider).
5. **Validation config au montage** : le parser rend `download`/`download_endpoint`/`staging_dir`/
   `quarantine_dir` **optionnels** (DÉCISION D11 — pour ne pas casser le crawler search-only).
   L'enforcement est **uni-directionnel** : un `download_endpoint` présent EXIGE les deux dirs ;
   mais des dirs présents SANS endpoint sont **silencieusement ignorés**. D-verify, à la
   composition, doit **vérifier que l'ensemble complet** (`download` + `download_endpoint` +
   `staging_dir` + `quarantine_dir`) est présent **avant d'activer la boucle** (fail-fast).

## 4. Pièges appris (CE jalon — le plus gros était INVISIBLE au gate, comme à chaque jalon)

- **CRITIQUE, attrapé par la revue HOLISTIQUE contre un `amuled` RÉEL avec un fichier de taille
  réelle.** Le hash d'un partfile **n'est PAS la valeur propre du tag `EC_TAG_PARTFILE`** (cette
  valeur propre est un **UINT8** d'index/statut, p.ex. `0x0d`) — il est dans le **tag enfant
  `EC_TAG_PARTFILE_HASH` (0x031E, HASH16, 16 octets)**. La DÉCISION D1 du plan avait grounded le
  hash « en valeur propre » (par analogie fausse) ; `_map_partfile` lisait donc le mauvais
  endroit → `download_queue()` renvoyait `()` pour **tout** vrai partfile → aucun monitoring,
  aucune complétion, aucune quarantaine, aucune vérif enfilée, ET re-`add_link` à l'infini. **Le
  gate était VERT** parce que les fixtures unitaires ET l'e2e utilisaient le **MD4 du fichier
  VIDE** (`31d6cfe0…`), un cas dégénéré qu'amuled ne liste jamais comme partfile actif. Correctif :
  lire le hash via `entry.find(EC_TAG_PARTFILE_HASH)` ; fixtures + e2e refaits avec un hash/taille
  RÉELS (l'e2e affirme maintenant que le hash APPARAÎT dans la file). **Leçon (reconduite) :
  l'e2e contre un `amuled` réel FAIT FOI — et il faut un input NON dégénéré (taille + hash
  réels), sinon le daemon court-circuite le chemin qu'on croit tester.** C'est la 3e fois qu'un
  filet « réel » (e2e / revue holistique) attrape un bug que 100 % branch + mypy strict ne
  voient pas.
- **`add_link` qui renvoie `EC_OP_FAILED` ne doit pas tuer la boucle.** `EcFailureError` est un
  `MuleSearchFailedError` (PAS un `MuleUnreachableError`) ; il échappait au `try` de
  `run_download_cycle` → crash de la tâche de download. Correctif : `_add_links` attrape
  `MuleSearchFailedError` par lien (→ marque le hash `failed` + log + continue) ; seul
  `MuleUnreachableError` (flux mort) remonte et fait sauter l'itération entière. Un `failed` est
  terminal (sort du plafond), reste dédupé (`is_downloaded` = n'importe quelle ligne) et n'est
  jamais ré-émis.
- **Plus petits, vérifiés bons** : le crawler ne lit JAMAIS les octets (aucun `open`/`read*` dans
  les chemins de download ; `promote` = `os.replace` seul) ; ordre écriture-avant-réseau
  (`record_queued` avant `add_link` ; `_add_links` relit un `active_states()` FRAIS car
  `_queue_new_candidates` a écrit de nouvelles lignes ce cycle) ; plafond intra-cycle recalculé
  en mémoire ; `DownloadEntry.is_complete` garde `size_full > 0` (jamais promouvoir un vide) ;
  `_target_status` rend `complete` (conservateur) pour une cible disparue de la config ;
  `download_loop` annulable au prochain `await` (le `suppress(CancelledError)` du teardown ne
  mange PAS l'annulation externe — vérifié empiriquement) ; `is_terminal` (3 terminaux) ≡ le SQL
  `_COMMITTED_BYTES` (même triplet, commentaire « keep in sync »).

## 5. Notes reportées (NON bloquantes — à trancher/faire en D-verify ou plus tard)

- **I1 — re-émission `add_link` (résolu BÉNIN).** `_add_links` ré-émet `add_link` pour tout hash
  `QUEUED` à chaque cycle. Empiriquement (revue holistique), un lien de **taille réelle** crée un
  partfile qui **apparaît** dans `download_queue` (`size_done=0 < size_full`) → `_monitor` le
  passe `DOWNLOADING` au cycle suivant → la ré-émission **s'arrête** (≈1-2 envois). C'est donc
  borné et bénin. Amélioration possible (pas nécessaire) : un flag `SENT` (ou état
  `queued-unsent` vs `queued-sent`) pour n'émettre que les liens non confirmés — à faire SI on
  observe au homelab un cas où un partfile sourceless n'apparaît jamais dans la file.
- **I2 — granularité d'erreur (plan-D9-conforme).** `run_download_cycle` enveloppe tout le corps
  dans un seul `try/except RepositoryError` → une `RepositoryError` dans `_handle_completions`
  saute le reste de l'itération (candidats non enfilés ce cycle, retry au suivant). La DÉCISION
  D9 sanctionne « continue si possible, sinon skip ». Risque (théorique) de famine si une SEULE
  ligne échoue en `set_state` à chaque cycle. Amélioration possible en D-verify : résilience
  par-étape (try/except autour de `_handle_completions` et `_queue_new_candidates`
  séparément).
- **T12 — couverture d'arrêt en intégration.** `download_loop` a un test unitaire d'annulation
  mid-wait (le vrai déclencheur prod). Quand D-verify câble la boucle dans le `TaskGroup`,
  ajouter une couverture d'intégration : pas de tâche en fuite (`asyncio.all_tasks`) après arrêt,
  et le guard `if not task.done()` de `_sleep_or_nudge` (test de mutation).
- **Nits doc Mineurs** : `download_policy` rend `SKIP_COMPLETE` pour `tier != "download"`
  (garde conservatrice documentée D5 ; un verdict dédié `SKIP_NOT_CANDIDATE` serait plus propre
  pour une métrique future) ; le commentaire « no-traversal » de `quarantine_fs.py` attribue la
  canonicité du hash à une contrainte CHECK — elle vit en réalité sur `files`/`match_decisions`
  (catalog.db) via FK, pas sur `downloads` (le chemin de données reste sûr : `.hex()` d'un
  HASH16 + `_CANONICAL_HASH_RE`).

## 6. Méthode (bilan du jalon)

Subagent-driven (implémenteur frais/tâche) + revue spec puis revue qualité adversariale (opus
sur les tâches substantielles : EC adapter, repo SQLite, lecture catalogue, boucle) + **revue
holistique finale contre `amuled` réel**. La revue holistique a, encore une fois, attrapé LE bug
critique du jalon (hash partfile mal décodé) que le gate 100 % branch ne pouvait pas voir — les
fixtures unitaires encodaient la même hypothèse fausse que le code, et l'e2e utilisait un input
dégénéré. **Garde les deux filets : l'e2e réel et la revue holistique sont rentables à chaque
jalon — et l'e2e doit utiliser un input réaliste.**

## 7. Prochaine étape

**D-verify** : pipeline de vérification NO-OP d'abord (workspace uv : racine propre +
`packages/crawler` + `packages/verifier` ; verifier Starlette trivial qui rend `unverified` ;
port `ContentVerifier` + `HttpContentVerifier` httpx ; `run_verification_cycle` consommateur de
la file ; câblage live des DEUX boucles dans `CrawlerApp` + gate full-mode `VERIFIER_URL`). Spec
déjà écrite : `docs/superpowers/specs/2026-06-13-verification-pipeline-design.md`. **Brainstormer
d'abord** (le plan D-verify dépend des interfaces réelles que D-download vient de produire :
`MuleDownloadClient`, `Quarantine`, `SqliteDownloadRepository`, `download_loop`/`DownloadLoopDeps`,
les contrats §3 ci-dessus). Le **tag** (`v0.8.0-download` / `-auto-download`) se pose à la fin de
D-verify, pas avant.
