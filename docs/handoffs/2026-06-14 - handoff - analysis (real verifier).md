# Handoff — emule-indexer (D-analysis : le VRAI verifier — confinement + checks réels)

> Continuation guide. Le plus récent des handoffs = point d'entrée. Lis aussi le précédent
> (`2026-06-14 - handoff - verification pipeline.md`) pour le contexte D-verify (la plomberie
> full-mode que D-analysis remplit), et la spec `docs/superpowers/specs/2026-06-14-analysis-design.md`.

## 1. TL;DR

Le verifier n'est **plus un NO-OP** : c'est un **analyseur de contenu réel**. À chaque `POST /verify`,
le service spawne un **enfant d'analyse JETABLE par fichier** (re-exec `python -m
download_verifier.analysis_child <hash>`) qui ouvre `quarantine/<hash>` en RO, exécute les checks
activés (`type_sniff` via puremagic + `ffprobe` binaire), agrège leur **worst-status** en verdict
(`clean < suspicious < malicious`), remplit `real_meta` (durée/bitrate/codec/conteneur — le trou
qu'EC ne comble jamais), imprime un JSON sur stdout et meurt. Le service parse cet égress
**défensivement** et répond `{verdict, real_meta, checks}`. **Le contrat de fil est inchangé et le
crawler PROD n'est PAS touché** (seul `check.py` est la couture ; `app.py` n'a qu'une docstring
corrigée). Confinement **portable** (rlimits/`setsid`/`killpg`/tmpdir/env minimal/`close_fds`/
`RLIMIT_CORE=0`) ; le **ring noyau** (namespaces `net=none`, seccomp, bwrap/gVisor, montages RO) est
reporté au **Plan F**. **clamav** est un **follow-up OBLIGATOIRE APRÈS Plan F**.

Jalon taggé **`v0.9.0-analysis`** (annoté, non poussé) sur `main`.

## 2. État vérifiable

Gate **PAR PAQUET** (le `pytest` nu depuis la racine est neutralisé par un `conftest.py` racine) :

```bash
( cd packages/verifier && uv run pytest -q )   # 103 passed, 7 deselected — 100.00% branch
( cd packages/crawler  && uv run pytest -q )   # 680 passed, 7 deselected — 100.00% branch (INCHANGÉ)
uv run ruff check . && uv run ruff format --check . && uv run mypy   # racine, span les 2 paquets
uv run sqlfluff lint packages/crawler/src
( cd packages/verifier && uv run pytest -m analysis_integration --no-cov -q )  # 7 passed (ffmpeg/ffprobe requis)
( cd packages/crawler  && uv run pytest -m verify_integration  --no-cov -q )   # 1 passed (sans Docker)
```

Tag : `git tag --list | grep analysis` → `v0.9.0-analysis` (NON poussé).

## 3. Ce qui a été construit (tout dans `packages/verifier/`)

Le verifier a deux faces, dans le seul paquet :
- **le service** (process parent) : `app.py` (INCHANGÉ bar docstring) → `check.verify_file` (la
  couture stable) → `spawn.run_analysis` → `egress.parse`.
- **l'enfant d'analyse** (process jetable) : `analysis_child.main` → `pipeline.run` → `checks/`.

Fichiers (neufs sauf `check.py`/`app.py`) :
- `config.py` — `AnalysisConfig` (frozen) + `from_env` (env → checks activés, ffprobe path, timeout,
  rlimits, egress cap, header bytes). Valeur invalide → `ValueError`.
- `checks/base.py` — `Status` (Literal), `STATUS_RANK`, `CheckOutcome` (frozen), `worst_status`.
- `checks/type_sniff.py` — `sniff(header) -> CheckOutcome` : **danger absolu** (DA7). Magic-bytes
  AVANT puremagic (exécutables ELF/MZ/Mach-O/shebang → `malicious` ; ZIP `PK..` → `suspicious`),
  puis classification mime puremagic.
- `checks/ffprobe.py` — `FfprobeRunner` (Protocol injectable), `ProdFfprobeRunner` (vrai
  `subprocess.run`, `# pragma`), `probe(path, runner, cfg)` : parse JSON **défensif** (champs
  STRING `duration`/`bit_rate`/`size`/`sample_rate` → float/int ; INT `width`/`height`/`channels`),
  champ absent/non-parsable OMIS.
- `pipeline.py` — `run(header, path, ffprobe_runner, cfg)` : exécute `cfg.enabled_checks` (registre ;
  nom inconnu → ignoré, p.ex. clamav), agrège worst-status, fusionne `real_meta`, trace `checks`.
- `egress.py` — `parse(stdout, returncode, timed_out, cfg)` : mapping DA6 toujours déterministe
  (timeout/exit≠0/oversize/illisible/hors-schéma → `suspicious` ; jamais d'exception).
- `spawn.py` — `ChildRunner` (Protocol injectable), `ProdChildRunner` (vrai `subprocess.Popen` +
  `_confine`, `# pragma`), `run_analysis(hash, cfg, runner)` : tmpdir jetable, argv re-exec, env
  minimal, délègue à `egress.parse`.
- `analysis_child.py` — `main(argv, *, ffprobe_runner=None, cfg=None) -> int` : revalide le hash
  canonique, lit `header_bytes` RO, `pipeline.run`, imprime l'égress ; `if __name__` `# pragma`.
- `check.py` — `verify_file(quarantine_path, expected, *, cfg=None, runner=None)` : défauts résolus
  AVANT `is_file()` (pour couvrir les branches None sans spawn) ; absent → `error` ; sinon spawn.
- `pyproject.toml` — dépendance `puremagic` ; marqueur `analysis_integration` (désélectionné +
  hors coverage).

Tests : unitaires à **100 % branch** (runners injectés, AUCUN subprocess réel) + le marqueur
`analysis_integration` (vrai spawn + vrai ffprobe : média→clean, ELF/shebang→malicious,
texte→suspicious, oversize→suspicious, timeout→suspicious, absent→error).

## 4. Pièges appris (les revues ont encore tout gagné — surtout pour le code `# pragma: no cover`)

- **Fuite du petit-fils `ffprobe` au timeout (CRITIQUE, prouvée empiriquement par la revue qualité).**
  `subprocess.run(timeout=...)` ne `SIGKILL` que l'enfant DIRECT, jamais le groupe. Or `_confine`
  fait `os.setsid()` (nouveau groupe) précisément pour tuer l'enfant ET ffprobe ensemble. Fix :
  `subprocess.Popen` + `communicate(timeout)` et sur `TimeoutExpired` →
  `os.killpg(os.getpgid(proc.pid), SIGKILL)` (race `ProcessLookupError` absorbée) + reap. **Leçon :
  `subprocess.run` ne suffit pas pour confiner un sous-arbre ; le code `# pragma` (non testé en
  unitaire) DOIT être relu attentivement — c'est la seule barrière avant l'intégration.**
- **`RLIMIT_CORE=0`** ajouté : un crash de l'enfant/ffprobe (parseur de contenu hostile) ne doit pas
  dumper d'octets du fichier dans le cwd (DA8).
- **`RLIMIT_NPROC` est PAR-UID GLOBAL** (pas par sous-arbre). Le défaut `64` n'est sain que sur un UID
  dédié peu peuplé (conteneur, Plan F) ; en dev/CI bare-metal (UID à >64 process) tout `fork()` de
  ffprobe est refusé → l'enfant crashe. Le test d'intégration override à 4096 (commenté). Commentaire
  ajouté dans `config.py`.
- **puremagic 2.2.0 — pièges de classification** : ELF/Mach-O → mime `''` (vide) ; shebang →
  `PureError` ; PE/MZ → `application/vnd.microsoft.portable-executable` ; **ZIP `PK\x03\x04` →
  `application/…wordprocessingml.document` (DOCX, jamais « zip »)**, et `PK\x05\x06`/`PK\x07\x08` →
  `application/zip` (absent des marqueurs). D'où les gardes magic-bytes `_EXECUTABLE_MAGICS` /
  `_ARCHIVE_MAGICS` AVANT puremagic. La branche `application/x-…executable` de `_classify` est MORTE
  (retirée) — tous les exécutables sont captés par les magic-bytes. **Leçon : ne pas deviner ce que
  puremagic rend — l'exécuter (`uv run python -c …`) et trancher empiriquement.**
- **Spawn réel sneaké dans le gate par défaut (revue qualité T8).** Après la bascule, `test_app.py`
  (2 tests) + le contract test crawler POSTaient un fichier existant via le vrai `build_app` → vraie
  `verify_file` → **vrai subprocess** dans le gate par défaut (le projet confine le spawn réel aux
  marqueurs). Fix : `monkeypatch.setattr(download_verifier.check, "ProdChildRunner", _Fake…)` dans
  ces 3 tests (égress canné, pas de spawn), en les GARDANT dans le gate (le contract test valide le
  contrat de fil). **Leçon : après une bascule, traquer les tests qui traversent désormais le vrai
  chemin système.**
- **ffmpeg refuse de muxer sans extension** : le nom de fichier est un hash (pas d'extension) →
  `ffmpeg -f matroska` explicite requis pour générer l'échantillon média de l'intégration.
- **`# pragma: no cover` sur la ligne `def …(`** (pas la dernière ligne d'une signature multi-ligne)
  pour exclure TOUT le corps de façon fiable. Vérifier le rapport coverage (0 manquant).
- **`_poison` factory** (egress) : un tuple module-level contenant `{}`/`[]` mutables partagés est un
  footgun ; une factory rendant des valeurs neuves l'évite.

## 5. Notes reportées / items Plan F & E (NON bloquants)

- **clamav** = `malicious` par signatures — **follow-up OBLIGATOIRE APRÈS Plan F** : `freshclam`
  exige un egress, en tension avec le verifier sur `internal: true`. Un **créneau est réservé** dans
  le registre de `pipeline.run` (un nom inconnu est ignoré) — il suffira d'ajouter une branche
  `elif name == "clamav": …` + le check.
- **Ring noyau** (Plan F) : `net=none` namespace, non-root, seccomp, **bwrap/nsjail/gVisor**, montages
  RO, vrai tmpfs. Aujourd'hui : process + rlimits + absence de réseau dans le code.
- **Durcissement du 2e `communicate()` (Plan F)** : si un petit-fils ÉCHAPPE au groupe (son propre
  `setsid` + garde stdout ouvert), le `communicate()` de reap après `killpg` peut bloquer
  indéfiniment. PAS un risque pour le modèle de menace actuel (ffprobe ne fait pas `setsid` et ne
  spawn rien d'arbitraire). Durcir en passant un `timeout` au 2e `communicate()`.
- **Blocage de l'event loop** : `verify_endpoint` (async) appelle `verify_file` (sync, qui spawn
  jusqu'à `timeout_s`) → bloque l'event loop. **Acceptable** car le verifier est mono-requête (la
  boucle de vérif du crawler est séquentielle ; `health` au démarrage seulement). Si le verifier
  devait servir des requêtes concurrentes, passer par un threadpool — MAIS attention alors à la
  thread-safety de `preexec_fn` (cf. spec §12).
- **Dédup `file_verifications`** (artefact at-least-once) : reporté à une future surface de
  lecture/export (inexistante aujourd'hui).
- **`verify_integration` fait désormais un vrai spawn** (pas de monkeypatch) : il dépend
  implicitement d'un sous-process Python (toujours dispo) ; robuste à l'absence de ffprobe (l'enfant
  crashe → `suspicious`, l'assertion tient). Pas de garde `skipif` (contrairement à
  `analysis_integration`) — inoffensif mais à savoir.

## 6. Contrats stables (pour la suite)

- **Contrat de fil** (3 définitions indépendantes, gardées en phase par le contract test + l'e2e) :
  `analysis_child._emit` imprime `{verdict, real_meta, checks}` → `egress.parse` l'exige (objet,
  verdict ∈ enum, real_meta dict, checks list) → DTO crawler `VerificationResult(verdict, real_meta,
  checks)`. `download_verifier` n'importe JAMAIS `emule_indexer` et inversement (vérifié : greps
  vides).
- **Injection des runners** : `FfprobeRunner` / `ChildRunner` (Protocols) — tout nouveau check ou
  toute nouvelle mécanique système se teste en unitaire via un fake, le code système réel sous
  `# pragma` + un test `analysis_integration`.

## 7. Méthode (bilan du jalon)

9 tâches exécutées **subagent-driven** (implémenteur frais → revue spec → revue qualité → revue
holistique finale avant tag). La revue **qualité** a attrapé la fuite du petit-fils ffprobe (bug réel
de confinement dans du code `# pragma`), le spawn réel sneaké dans le gate par défaut, et plusieurs
pièges puremagic. La revue **holistique** a confirmé le bout-en-bout (contrat de fil sur ses 3
définitions, frontière de paquet, gate complet) et relevé les notes de durcissement Plan F.

## 8. Prochaine étape

« Plan D » (auto-download + vérification + analyse) est désormais **réellement fonctionnel**. Restent
**Plan E** (observabilité : Prometheus/apprise) et **Plan F** (packaging : 2 images Docker, compose,
verifier sur `internal: true`, glueforward pour aMule, durcissement bwrap/gVisor, **puis clamav**).
Brainstormer d'abord (spec → plan → exécution). Note : **clamav dépend du Plan F** (egress freshclam).
