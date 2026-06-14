# Spec — emule-indexer : D-analysis (le vrai verifier — confinement + checks réels)

> **Sous-projet** : troisième et dernier issu de la scission de « Plan D ». Ordre :
> **D-download** (`2026-06-13-download-orchestration-design.md`) → **D-verify**
> (`2026-06-13-verification-pipeline-design.md` : la plomberie full-mode bout-en-bout avec un
> verifier **NO-OP**) → **D-analysis** (CE document : on remplace le NO-OP par le **vrai**
> travail — un enfant jetable par fichier qui exécute `type_sniff`/`ffprobe`, remplit
> `real_meta` et rend des verdicts réels `clean`/`suspicious`/`malicious`).
>
> Réfs : MVP design `2026-06-10-crawler-mvp-design.md` §10 (sécurité/confinement/vérif/relais :
> « fork enfant jetable par fichier », « égress = stdout/exit parsé défensivement », « pipeline
> pluggable, agrégation worst-status », « ClamAV = follow-up »), §11 (dead-letter = signal de
> sécurité). Handoff `docs/handoffs/2026-06-14 - handoff - verification pipeline.md` §3
> (contrats stables que D-analysis doit respecter) et §5 (notes reportées : tension DV6/DV7,
> dédup at-least-once). État de départ : le verifier `download_verifier` (NO-OP) + sa frontière
> de paquet (n'importe JAMAIS `emule_indexer`).

---

## 1. But & périmètre

**But** : faire du verifier un analyseur réel **sans changer le contrat de fil**. À chaque
`POST /verify`, le service spawne un **enfant d'analyse jetable** (un process re-exec
`python -m download_verifier.analysis_child <hash>`) qui ouvre `quarantine/<hash>` en lecture
seule, exécute les checks activés (`type_sniff` via puremagic, `ffprobe` binaire), agrège leur
worst-status en un verdict, remplit `real_meta` (durée/bitrate/codec — le trou qu'EC ne comble
jamais), imprime un JSON sur stdout et meurt. Le service parse cet égress **défensivement** et
répond `{verdict, real_meta, checks}`. **Le crawler n'est pas modifié** (la couture est
entièrement dans `packages/verifier`).

**Dans le périmètre** :
- L'**enfant d'analyse** et son **confinement portable** : re-exec subprocess, rlimits durs
  (`resource`), timeout-kill du groupe de process, cwd tmpdir jetable, fichier RO, **enfant
  vierge** (aucun fd hérité, environnement explicite minimal), égress stdout/exit borné et
  parsé défensivement.
- Le **registre de checks branchable** + l'**agrégation worst-status** (`clean < suspicious <
  malicious`).
- Les **deux checks réels** : `type_sniff` (puremagic, détection de danger absolu) et `ffprobe`
  (binaire, cœur de `real_meta`).
- La **config verifier** (`config.py`, depuis l'env) : checks activés, chemin ffprobe, timeout,
  rlimits, cap d'égress.
- Le **marqueur d'intégration `analysis_integration`** (spawn réel + vrai ffprobe), désélectionné
  par défaut, exclu de la coverage.

**Hors périmètre** (voir §11) :
- **Ring noyau** : `net=none` (namespace réseau), non-root, seccomp, bwrap/nsjail/gVisor,
  montages RO, vrai tmpfs → **Plan F (packaging)**. Ici l'isolation vient du process +
  rlimits + de l'absence de réseau dans le code ; le no-Internet de prod vient du container
  `internal: true` du Plan F.
- **clamav** (source `malicious` par signatures) → **follow-up OBLIGATOIRE après Plan F**
  (un créneau est réservé dans le registre, mais non implémenté) ; raison : `freshclam` exige
  un egress réseau, en tension frontale avec `internal: true` → c'est un problème de packaging.
- **Dédup** des lignes `file_verifications` dupliquées (artefact at-least-once) → appartient à
  une future surface de lecture/export qui **n'existe pas encore**.
- **Alerte** sur `malicious` → Plan E. **Promotion** (humaine) → hors-scope permanent.
  **Windows** → non supporté (`preexec_fn`/`resource`/`setsid` Linux ; verifier conteneurisé).

## 2. Décisions verrouillées (issues du brainstorm)

1. **DA1 — Confinement portable maintenant, ring noyau au Plan F.** D-analysis livre l'enfant
   jetable « léger » (subprocess + rlimits + timeout + cwd tmpfs jetable + fichier RO + code
   sans réseau). Le `net=none`/non-root/seccomp/bwrap/gVisor arrive avec le container Plan F
   (`internal: true` couvre déjà le no-Internet en prod). Cohérent avec le découpage MVP : le
   ring noyau est une préoccupation *packaging*, pas *logique d'analyse*.
2. **DA2 — Verdict = axe SÛRETÉ/cohérence.** Le verdict répond « ce fichier est-il
   dangereux / cohérent avec ce qu'il prétend être ? », pas « est-ce le bon épisode ? »
   (l'identité reste un jugement humain à la promotion ; le matching sur nom a déjà filtré en
   amont). `real_meta` = enrichissement honnête pour le catalogue/l'humain. `expected` reste
   **minimal et non décisif** (`{target_id}` ou `{}`) → le verifier demeure **stateless, sans
   dépendance au domaine crawler** → **aucune modif crawler**.
3. **DA3 — Checks = `type_sniff` (puremagic) + `ffprobe` (binaire).** Registre branchable
   (activable par config) + agrégation worst-status. `clamav` = créneau réservé non implémenté.
4. **DA4 — clamav = follow-up OBLIGATOIRE après Plan F.** Pas optionnel, séquencé : la mise à
   jour de signatures (`freshclam`) impose un egress, à résoudre au niveau réseau/packaging.
5. **DA5 — Un enfant d'analyse Python unique par fichier** (re-exec subprocess, pas `os.fork`).
   C'est la frontière de confinement : *tous* les octets hostiles sont lus dans l'enfant
   jetable, jamais dans le process service. Runners injectés pour les tests (cf. DA9).
6. **DA6 — Mapping des échecs déterministe, toujours en 200.** Un fichier qui résiste à
   l'analyse n'est pas une panne de service : le verifier répond **200** avec un verdict
   déterministe (la boucle de vérif enregistre + complète, **pas** de retry). Seul un service
   *injoignable* (connexion/timeout HTTP/5xx) reste un `VerifierUnavailableError` côté adapter
   crawler (→ retry). **La boucle de vérif côté crawler est inchangée.** Table en §6.
7. **DA7 — `type_sniff` = détection de danger ABSOLU** (pas de comparaison à l'extension
   déclarée, qui vient du nom eD2k *hostile*). Conteneur média → `clean` ; **exécutable/script
   → `malicious`** (on cherchait une vidéo : un binaire livré sous l'apparence d'une vidéo est
   une tromperie délibérée) ; archive → `suspicious` (plausible mais pas une vidéo) ; inconnu →
   `clean` (ffprobe tranche). `malicious` est donc atteignable dès D-analysis.
8. **DA8 — Enfant le plus vierge possible.** `close_fds=True` (le parent n'**ouvre jamais** le
   fichier — `is_file()` métadonnée seulement ; c'est l'enfant qui ouvre RO → aucun fd passé) ;
   **environnement explicite minimal** (on n'hérite PAS de `os.environ` — secrets/config VPN ;
   on ne passe que `QUARANTINE_DIR`, la config des checks, `ffprobe_path` absolu, au plus un
   `PATH` minimal). Entrée = argv (hash) + env minimal ; sortie = stdout/exit.
9. **DA9 — 100 % branch via runners injectés ; subprocess réel derrière un marqueur.** Toute la
   logique pure (sniff, parse ffprobe→`real_meta`, agrégation, parse égress, mapping) est
   unit-testée sans subprocess. Le seul appel `subprocess.run` réel (impl prod du `Runner`) +
   `preexec_fn` est `# pragma: no cover`, couvert par `analysis_integration`.
10. **DA10 — Défauts config raffinables, flags ffprobe figés au plan.** timeout 30 s ; rlimits
    cpu ~20 s, AS ~512 Mio, nproc/nofile modestes, fsize borné ; `egress_cap` 64 Kio ; tous
    overridables par env. Les flags/champs exacts de `ffprobe` seront figés via **context7**/doc
    ffprobe au moment du plan (comme httpx/starlette/uvicorn en D-verify).

## 3. Architecture — structure du paquet & modèle à deux process

Le verifier a deux faces, dans le **seul** `packages/verifier` :
- **le service** (process parent) : HTTP, spawn l'enfant, parse l'égress ;
- **l'enfant d'analyse** (process jetable) : lit les octets, exécute les checks, imprime un
  JSON, meurt.

**Couture stable** : `check.verify_file(quarantine_path, expected) -> (verdict, real_meta,
checks)`. `app.py` l'appelle **exactement comme aujourd'hui** ; seul son corps change (stat →
spawn + parse). Le contrat de fil JSON et l'e2e `verify_integration` passent tels quels.

```
packages/verifier/src/download_verifier/
  __init__.py
  __main__.py          # uvicorn entry — INCHANGÉ
  app.py               # Starlette POST /verify, GET /health — INCHANGÉ (appelle verify_file)
  check.py             # MODIFIÉ : verify_file = façade service-side → is_file → spawn → parse
  config.py            # NOUVEAU : AnalysisConfig (env → enabled_checks, ffprobe_path, timeout_s,
                       #           rlimits, egress_cap) + from_env()
  spawn.py             # NOUVEAU (parent) : run_analysis(path, cfg, runner) — argv/env minimal,
                       #           tmpdir jetable, Runner injectable ; mappe issue → résultat brut
  egress.py            # NOUVEAU (parent) : parse DÉFENSIF du stdout enfant (borné, schéma
                       #           strict, enum) → (verdict, real_meta, checks) | échec
  analysis_child.py    # NOUVEAU (enfant) : main(argv) → revalide hash, ouvre RO, pipeline.run,
                       #           json.dumps(stdout), exit ; `if __name__` sous pragma
  pipeline.py          # NOUVEAU (pur) : run(file, enabled) → exécute checks, agrège worst-status,
                       #           fusionne real_meta, trace checks → (verdict, real_meta, checks)
  checks/
    __init__.py
    base.py            # NOUVEAU : CheckOutcome(name, status ∈ {clean,suspicious,malicious}, meta)
                       #           + protocole Check + registre
    type_sniff.py      # NOUVEAU : puremagic(1ers octets) → classe danger absolu → status + meta
    ffprobe.py         # NOUVEAU : Runner ffprobe injecté → parse JSON → real_meta + status
```

Principe « ce qui change ensemble vit ensemble » : la logique *pure et testable* (`pipeline`,
`checks/`, `egress`) est isolée du *spawn réel* (`spawn`, l'appel subprocess réel de `ffprobe`),
ce qui donne le 100 % branch unitaire + le marqueur d'intégration pour le vrai subprocess.

**Le crawler est intouché.** `record_verification` stocke déjà `verdict` (string) + `real_meta`/
`checks` (JSON) sans interpréter ; aucune logique crawler ne branche sur la valeur du verdict
(promotion humaine hors-scope ; alerte `malicious` = Plan E). Comme `expected` reste minimal
(DA2) : ni nouveau port, ni enrichissement d'`expected`, ni changement de boucle. Le contrat de
fil verdict/real_meta/checks est stable.

## 4. L'enfant d'analyse & le confinement

**Spawn (`spawn.py`, côté parent).** Pour chaque fichier :

```
subprocess.run(
    [sys.executable, "-m", "download_verifier.analysis_child", "<hash>"],
    cwd=<tempfile.mkdtemp()>,        # scratch jetable, supprimé en finally
    stdin=DEVNULL, stdout=PIPE, stderr=DEVNULL,   # égress = stdout ; bruit ffprobe jeté
    timeout=cfg.timeout_s,           # timeout-kill côté parent
    preexec_fn=_confine,             # rlimits + setsid AVANT exec (Linux)
    close_fds=True,                  # DA8 : aucun fd hérité
    env=_minimal_env(cfg),           # DA8 : env explicite minimal, PAS os.environ
)
```

- **`_confine` (preexec_fn, Linux)** : `os.setsid()` (groupe de process dédié → on tue
  l'enfant **et** son petit-fils ffprobe d'un coup), puis `resource.setrlimit` durs :
  `RLIMIT_CPU`, `RLIMIT_AS`, `RLIMIT_FSIZE`, `RLIMIT_NPROC`, `RLIMIT_NOFILE` (hérités par
  ffprobe).
- **`_minimal_env`** : `QUARANTINE_DIR`, les vars de config des checks (enabled, ffprobe_path),
  au plus un `PATH` minimal. Aucune autre variable du service.
- **cwd tmpdir jetable** : scratch neuf par fichier, supprimé en `finally` (le vrai tmpfs/RO
  bind vient du Plan F).
- **Timeout** : `subprocess.run(timeout=…)` ; sur `TimeoutExpired`, `os.killpg(pgid, SIGKILL)`.
- **Pas de réseau** : le code n'ouvre aucun socket ; le vrai `net=none` vient du Plan F
  (documenté honnêtement).

**L'enfant (`analysis_child.py`)** : parse l'argv hash → **revalide le hash canonique** (32 hex,
anti-traversal en défense de profondeur) → `path = QUARANTINE_DIR / hash`, ouvert RO → exécute
`pipeline.run(path, enabled)` → imprime `json.dumps({"verdict","real_meta","checks"})` sur
stdout → `exit 0`. Sur exception interne → exit ≠ 0 (best-effort, **pas** de stack en égress).

**Contrat d'égress (`egress.py`, côté parent) — toute la robustesse du modèle** :
- **stdout borné** : au plus `cfg.egress_cap` octets ; au-delà → illisible.
- **schéma strict** : objet `{verdict: str ∈ {clean,suspicious,malicious}, real_meta: obj,
  checks: list}` ; tout écart (clé manquante, mauvais type, enum hors-domaine) → illisible.
- l'illisible/hors-schéma est mappé en §6 (jamais une exception remontée — réponse 200).

## 5. Les checks, `real_meta` & l'agrégation

**Modèle.** Chaque check rend `CheckOutcome(name, status, meta)` avec `status ∈ {clean,
suspicious, malicious}`. Verdict du fichier = **worst-status** sur `clean < suspicious <
malicious`. `real_meta` du fichier = fusion des `meta` (essentiellement ffprobe + `sniffed_type`).
`checks` = trace `[{name, status, …}]` (audit des checks exécutés). `error` n'est **pas** un
statut de check — c'est un résultat *service-level* (§6).

**`type_sniff` (puremagic) — danger absolu (DA7).** Sniff des premiers octets, classé :

| type réellement sniffé | status |
|---|---|
| conteneur média connu (avi/mkv/mp4/mpeg/ogg/flv…) | `clean` |
| exécutable / script (ELF, PE/MZ, Mach-O, shebang `#!`) | `malicious` |
| archive (zip/rar/7z…) | `suspicious` |
| inconnu / non concluant | `clean` (ffprobe tranchera) |

`sniffed_type` est mis dans `real_meta` dans tous les cas.

**`ffprobe` — cœur de `real_meta`.** Runner injecté (prod = subprocess réel ; tests = JSON
canné) : `ffprobe -v error -print_format json -show_format -show_streams <fichier>` (flags exacts
figés au plan via context7) :
- parse OK avec ≥ 1 flux audio/vidéo → `clean` + remplit `real_meta` ;
- échec / sortie non-nulle / aucun flux média → `suspicious` (prétend être un média, n'en est
  pas un).

Forme de `real_meta` (champs absents → omis ; parsing défensif) :
```json
{
  "sniffed_type": "video/x-matroska",
  "container": "matroska,webm",
  "duration_s": 1294.5,
  "bit_rate": 1500000,
  "size_bytes": 242884608,
  "video": {"codec": "h264", "width": 720, "height": 576, "frame_rate": "25/1"},
  "audio": [{"codec": "aac", "channels": 2, "sample_rate": 48000, "language": "fre"}]
}
```

**Registre & agrégation (`pipeline.py`).** `{name: check}` ; `AnalysisConfig.enabled_checks`
sélectionne lesquels tournent (défaut `["type_sniff","ffprobe"]`). `clamav` a sa place réservée
(statut `malicious` à terme) mais **non implémenté** (DA4). `pipeline.run` exécute les checks
activés, agrège worst-status, fusionne `real_meta`, renvoie `(verdict, real_meta, checks)`.

## 6. Flux de données & mapping verdict

```
POST /verify {hash, expected}
  → app.py (validation hash canonique + corps borné — INCHANGÉ)
  → check.verify_file(quarantine/<hash>, expected)
       1. is_file() ?  non → ("error", {}, [])        # métadonnée seulement, pas d'octets
       2. spawn.run_analysis(path, cfg, runner)        # enfant jetable, DA5/DA8
       3. egress.parse(stdout, exit, timed_out)        # défensif, DA6
  → {verdict, real_meta, checks}  (toujours 200)
```

Mapping service-level (DA6) — **toujours 200**, verdict déterministe :

| Situation | verdict |
|---|---|
| Fichier absent / non-régulier | `error` |
| Enfant : exit ≠ 0 / **timeout** / OOM-killé (rlimit) / égress illisible ou hors-schéma | `suspicious` |
| Enfant OK, égress valide | worst-status des checks (`clean`/`suspicious`/`malicious`) |

Le `suspicious` sur timeout/crash est **intentionnel** : un fichier qui fait planter/timeouter
l'analyseur est un signal de poison (MVP §11 : crash répété → dead-letter = suspect). Distinction
préservée : ces cas restent des **200** (déterministe → enregistré + complété, pas de retry) ;
seul un **service injoignable** reste `VerifierUnavailableError` côté crawler (→ retry).

## 7. Gestion d'erreurs

- **Octets hostiles** : lus uniquement dans l'enfant jetable (DA5) ; le parent ne lit jamais
  d'octets (`is_file` métadonnée).
- **Défense en profondeur** : revalidation du hash canonique dans l'enfant (DA8) ; égress borné
  + schéma strict (§4) ; `_minimal_env` + `close_fds` (DA8).
- **Le service ne lève jamais** vers le client pour un fichier problématique → 200 + verdict
  déterministe (DA6). Les exceptions de plomberie HTTP restent gérées par Starlette (et perçues
  comme `VerifierUnavailableError` côté crawler).
- **rlimits/timeout** : OOM/CPU dépassé → l'enfant est tué par le noyau (rlimit) → exit ≠ 0 →
  `suspicious` ; timeout → `killpg` → `suspicious`.

## 8. Config (verifier)

`config.py` : `AnalysisConfig` (frozen) + `from_env()`. Champs (défauts DA10, overridables env) :
`enabled_checks` (`type_sniff,ffprobe`), `ffprobe_path` (`ffprobe`, idéalement absolu),
`timeout_s` (30), `rlimit_cpu_s` (~20), `rlimit_as_bytes` (~512 Mio), `rlimit_nproc`,
`rlimit_nofile`, `rlimit_fsize_bytes`, `egress_cap_bytes` (65536). Le parent l'utilise pour
rlimits/timeout/env minimal ; l'enfant relit la part « checks » depuis l'env minimal que le
parent lui passe. **Côté crawler : aucune nouvelle config** (`verifier_url`/`VerifyConfig`
inchangés). Nouvelle dépendance paquet : **`puremagic`** (pip, pur-Python) dans
`packages/verifier/pyproject.toml`. `ffprobe` = binaire système (image Plan F ; requis pour le
marqueur d'intégration en dev).

## 9. Tests

**Unitaire (run par défaut, 100 % branch, aucun subprocess réel)** :
- `type_sniff` : en-têtes d'octets (ELF/MZ/Mach-O/shebang/zip/avi/mkv/texte) → status + meta.
- `ffprobe` : **Runner injecté** rendant du JSON canné (média valide, sans flux, JSON malformé,
  exit ≠ 0) → `real_meta` + status. L'appel subprocess réel (impl prod du Runner) = `# pragma:
  no cover`.
- `egress` : valide / trop volumineux / non-JSON / hors-schéma / enum invalide → mapping.
- `pipeline` : checks factices → worst-status, fusion `real_meta`, trace, `enabled_checks`.
- `spawn` : Runner injecté simulant stdout / `TimeoutExpired` / exit ≠ 0 → mapping + cycle de
  vie tmpdir + `killpg`. `preexec_fn` réel + `subprocess.run` réel = `# pragma: no cover`.
- `config` (parsing env : défauts, overrides, valeurs invalides), `check.verify_file` (glue avec
  Runner injecté), `analysis_child.main(argv, …)` (tmp + Runner ffprobe injecté ; `if __name__`
  sous pragma).

**Intégration (`analysis_integration`, désélectionné, exclu de la coverage — comme
`ec_integration`/`verify_integration`/`download_integration`)** : spawn réel de l'enfant + **vrai
`ffprobe`** sur de vrais échantillons (petit média valide, un ELF/script, un texte, un cas égress
surdimensionné, un cas timeout). Prouve rlimits/timeout/`killpg`/RO/env-minimal/close_fds pour de
vrai. Dépendance : `ffmpeg/ffprobe` présent.

## 10. Modèle de données & contrat de fil

**Inchangés.** `file_verifications(id, ed2k_hash, verdict, real_meta JSON, checks JSON,
verified_at, node_id)` (append-only) accueille déjà n'importe quel verdict string + JSON.
Contrat de fil `/verify` : requête `{hash, expected}` → réponse `{verdict, real_meta, checks}`
(identique à D-verify ; seuls les *contenus* `real_meta`/`checks` cessent d'être vides et
`verdict` peut valoir `clean`/`suspicious`/`malicious`). DTO crawler `VerificationResult` et
réponse verifier restent définis indépendamment (frontière de paquet), gardés en phase par le
test de contrat + l'e2e.

## 11. Hors-périmètre / reporté (explicite)

- **Ring noyau** (`net=none` namespace, non-root, seccomp, bwrap/nsjail/gVisor, montages RO,
  vrai tmpfs) → **Plan F**.
- **clamav** (source `malicious` par signatures) → **follow-up OBLIGATOIRE après Plan F**
  (tension `freshclam` egress vs `internal: true`). Créneau réservé dans le registre.
- **Dédup** des lignes `file_verifications` dupliquées (artefact at-least-once) → future surface
  de lecture/export (inexistante aujourd'hui).
- **Alerte** `malicious` → Plan E. **Promotion** humaine → hors-scope permanent.
- **Windows** non supporté (`preexec_fn`/`resource`/`setsid` Linux ; verifier conteneurisé).

## 12. Risques & notes

- **`preexec_fn` n'est pas thread-safe / POSIX-only** : acceptable, le verifier est Linux et le
  spawn est par-requête (uvicorn ; pas de fork dans un état partagé fragile). À surveiller si le
  service passe multi-thread agressif.
- **Confinement portable ≠ frontière noyau** : entre D-analysis et Plan F, l'isolation repose
  sur process + rlimits + absence de réseau dans le code, pas sur un namespace. Honnêteté
  d'étiquetage : c'est documenté ; le ring dur est explicitement Plan F.
- **`ffprobe` est lui-même un parseur de contenu hostile** : il tourne en petit-fils, sous les
  rlimits/timeout/groupe de process de l'enfant → un ffprobe qui boucle/explose est tué avec
  l'enfant et donne `suspicious`.
- **Flags ffprobe & forme exacte de `real_meta`** : figés au plan (context7/doc ffprobe).
