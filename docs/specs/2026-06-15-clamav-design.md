# Design — check `clamav` (3ᵉ source de verdict, par signatures)

> **Nature** : design spec **actionnable** d'une tâche **structurante** du backlog
> (`2026-06-15-backlog-parallelization-design.md` §5, worktree **WT-verifier**, exécutée AVANT
> `ring-noyau` dans le même worktree). Le design est **figé** (co-conçu avec Geoffrey) ; ce doc le
> documente fidèlement, ancré dans le code réel (`fichier:ligne`). Un implémenteur frais l'exécute
> sans question. **Ne re-designe rien** : tout écart du code vis-à-vis d'une décision est consigné en
> `## Risques / à confirmer`, pas corrigé en silence.
>
> Réfs amont (décision déjà actée, NE PAS rouvrir) :
> - `2026-06-14-analysis-design.md` DA3/DA4 + §« registre & agrégation » (`clamav` = créneau réservé,
>   follow-up OBLIGATOIRE après Plan F, tension `freshclam` egress vs `internal: true`).
> - `2026-06-14-packaging-design.md` F-D5 (clamav après Plan F) + le réseau `verify-internal`
>   `internal: true` (`compose.yaml:121-122`).
> - `2026-06-15-backlog-parallelization-design.md` §4 (fichiers intégration-owned : `compose.yaml` →
>   **delta proposé**, jamais édité par l'agent ; `pyproject`/`uv.lock` → dépendance **déclarée**) et
>   §5 (clamav réel = Geoffrey, marqueur `analysis_integration`).

## 1. Contexte & objectif

Aujourd'hui l'analyseur (D-analysis, `packages/verifier/`) produit son verdict via **deux** checks
agrégés en worst-status (`packages/verifier/src/download_verifier/pipeline.py:24-30`) :

- `type_sniff` (puremagic) — danger ABSOLU par magic bytes (`checks/type_sniff.py`) ;
- `ffprobe` (binaire injecté) — « prétend-il être un média ? » + remplit `real_meta`
  (`checks/ffprobe.py`).

`type_sniff` ne capte que les **formes** dangereuses (un exécutable déguisé en vidéo). Il ne sait
**rien** d'un payload malveillant *à l'intérieur* d'un conteneur média plausible (ex. un MKV qui
exploite un parseur, un script encapsulé). **clamav** ajoute la **3ᵉ source de verdict** : un scan
**par signatures** (base virale), qui rend `malicious` sur match. C'est le créneau **réservé mais
non implémenté** du pipeline (`pipeline.py:29`, commentaire « clamav non implémenté ») et du registre
(`AnalysisConfig.enabled_checks`, `config.py:14,38`).

**Objectif** : implémenter le check `clamav` à l'**identique du pattern `ffprobe`** (fonction pure +
runner injectable), le câbler dans `pipeline.run`, le rendre **opt-in** (défaut `ENABLED_CHECKS`
INCHANGÉ), provisionner la base de signatures via un **sidecar `freshclam`** sans casser le no-Internet
du verifier (`internal: true`), et **relâcher conditionnellement les rlimits** du child confiné car
`clamscan` charge toute la base (~centaines de Mo).

**Contraintes non négociables** (CLAUDE.md, rappel) : TDD strict (test rouge d'abord) ; **100 % branch
coverage** des deux paquets ; `mypy --strict` sur **src + tests** ; ruff `E,F,I,UP,B,SIM` ligne 100. Le
code système (vrai subprocess) est `# pragma: no cover` ; **tout** est testé via le runner injecté ; le
réel n'est exercé que par le marqueur `analysis_integration`. Le verifier **n'importe toujours rien**
de `emule_indexer`. Aucune modif crawler.

## 2. Architecture & flux (où clamav s'insère)

Le chemin d'analyse existant (inchangé en structure) :

```
POST /verify {hash, expected}                          app.py:45
  → check.verify_file(quarantine/<hash>, expected)     check.py:26  (is_file ; ne lit JAMAIS les octets)
    → spawn.run_analysis(hash, cfg, ProdChildRunner)   spawn.py:83  (re-exec enfant confiné : rlimits/setsid/env minimal)
        ── enfant : python -m download_verifier.analysis_child <hash>  analysis_child.py:29
             lit ≤ header_bytes RO + revalide le hash canonique
             → pipeline.run(header, path, ffprobe_runner, cfg)        pipeline.py:19
                  pour name in cfg.enabled_checks :
                    type_sniff.sniff(header)            ← en-tête déjà lu
                    ffprobe.probe(path, runner, cfg)    ← chemin + runner injecté
              ►►  clamav.scan(path, runner, cfg)        ← chemin + runner injecté   (NOUVEAU)
                  worst_status([...])  →  verdict       base.py:29
             imprime json {verdict, real_meta, checks} sur stdout
        ── parent : egress.parse(stdout, rc, timed_out) spawn.py:95 / egress.py:23  (défensif → suspicious si poison)
  → JSONResponse {verdict, real_meta, checks}           app.py:72
```

clamav s'insère **comme `ffprobe`** : un check **dans l'enfant confiné**, sélectionné par
`enabled_checks`, agrégé en worst-status. Il **tourne dans l'enfant** (décision 2A) : pas de réseau
requis (base virale **locale** sur un volume RO + fichier **local**). Le parent ne touche jamais les
octets ; le scan se fait côté enfant comme `ffprobe`.

Deux décisions de provisioning/mécanisme **figées** (du plan maître / co-design) :

- **Décision 1A — provisioning par sidecar `freshclam` + volume partagé RO.** Un service Docker
  séparé (`freshclam`, sur le réseau `egress`) met à jour la base de signatures dans un **volume nommé
  `clamav-db`** ; le verifier **monte ce volume en RO** et ne fetch **jamais** → `internal: true`
  préservé (`compose.yaml:121-122`). `freshclam` vit dans l'**image SIDECAR**, jamais dans l'image
  verifier (§8).
- **Décision 2A — scan via `clamscan` one-shot** (charge la base par fichier) à travers le runner
  injectable. **PAS** le démon `clamd` (qui resterait résident, garderait la base en mémoire, et
  exigerait un socket/port — complexité réseau inutile pour un débit de quelques fichiers/cycle).

## 3. Le check clamav (`checks/clamav.py`) — miroir exact de `ffprobe.py`

Nouveau module `packages/verifier/src/download_verifier/checks/clamav.py`. **Calque
`checks/ffprobe.py`** ligne pour ligne sur la forme : un `Protocol` injectable, un runner PROD
`# pragma: no cover`, une fonction pure `scan`.

### 3.1 Le runner injectable

```python
class ClamavRunner(Protocol):
    """Exécute clamscan et rend (returncode, stdout). Injecté pour les tests."""

    def __call__(self, argv: Sequence[str]) -> tuple[int, bytes]: ...
```

Signature **identique** à `FfprobeRunner` (`ffprobe.py:25-28`) : `(argv) -> (returncode, stdout)`.
Raison : `clamscan` encode son verdict dans son **code de sortie** (voir §3.3), exactement comme on
peut lire `(rc, stdout)` pour ffprobe. Garder la même signature permet de réutiliser le `ProdRunner`
sous la même forme et le même test de conformité de Protocol.

```python
class ProdClamavRunner:
    """ClamavRunner de PROD : vrai subprocess.run (couvert par analysis_integration)."""

    def __init__(self, timeout_s: float) -> None:
        self._timeout_s = timeout_s

    def __call__(self, argv: Sequence[str]) -> tuple[int, bytes]:  # pragma: no cover
        completed = subprocess.run(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=self._timeout_s,
            check=False,
        )
        return completed.returncode, completed.stdout
```

**Identique** à `ProdFfprobeRunner` (`ffprobe.py:31-46`) : `check=False` (on lit le code de retour
nous-mêmes), `stderr=DEVNULL`, `# pragma: no cover` sur `__call__` (le constructeur, lui, n'est PAS
pragma → couvert par un `test_prod_clamav_runner_constructs`, cf. `test_ffprobe.py:55-57`).

> **Note `timeout`** — `subprocess.run(timeout=…)` lève `TimeoutExpired` si dépassé. Côté
> `ffprobe`/`clamav` runner PROD c'est `# pragma: no cover` ; en pratique le **timeout dur** vient du
> parent (`killpg` du groupe, `spawn.py:60-67`). On garde le `timeout=` du runner par symétrie avec
> `ProdFfprobeRunner`. Le child confiné, lui, est aussi borné par `RLIMIT_CPU`.

### 3.2 La fonction pure `scan`

```python
def scan(path: Path, runner: ClamavRunner, cfg: AnalysisConfig) -> CheckOutcome:
    """Scanne path via runner ; rend CheckOutcome (status + meta)."""
    argv = [
        cfg.clamscan_path,
        "--no-summary",
        "--stdout",
        "--database",
        cfg.clamav_db_dir,
        str(path),
    ]
    returncode, _stdout = runner(argv)
    if returncode == 0:
        return CheckOutcome(name="clamav", status="clean", meta={})
    if returncode == 1:
        signature = _parse_signature(_stdout)
        meta: dict[str, object] = {}
        if signature is not None:
            meta["clamav_signature"] = signature
        return CheckOutcome(name="clamav", status="malicious", meta=meta)
    # rc ≥ 2 (ou tout autre) : erreur clamscan (base absente/corrompue, I/O…) → défensif.
    return CheckOutcome(name="clamav", status="suspicious", meta={})
```

Détails figés :

- **Flags clamscan FIGÉS** (comme les flags ffprobe figés, `ffprobe.py:51-60`) :
  - `--no-summary` : pas de bloc récap multi-ligne (on lit le rc, pas le texte).
  - `--stdout` : envoie le détail sur stdout (pas stderr, qui est `DEVNULL`) → permet d'extraire le
    nom de signature pour `meta`.
  - `--database <dir>` : pointe explicitement la base RO du volume `clamav-db` (ne dépend pas du
    `/var/lib/clamav` par défaut, absent de l'image verifier).
  - `str(path)` : le fichier à scanner (le hash eD2k, déjà revalidé dans l'enfant).
- **`meta`** : on extrait **au mieux** le nom de la signature (`clamav_signature`) via
  `_parse_signature` (défensif, voir §3.4). Si introuvable → `meta` vide. `real_meta` n'a **aucun
  champ obligatoire** côté clamav (contrairement à ffprobe qui remplit container/codec).

### 3.3 Mapping de verdict (figé, défensif, cohérent worst-status)

`clamscan` documente trois familles de codes de sortie :

| `clamscan` rc | Sens | `status` clamav |
|---|---|---|
| `0` | aucun virus trouvé | **`clean`** |
| `1` | virus(s) trouvé(s) | **`malicious`** |
| `≥ 2` | erreur (base absente, I/O, args…) | **`suspicious`** (défensif) |

Cohérent avec la philosophie worst-status (`base.py`) et avec ffprobe (`ffprobe.py:62-75`, exit ≠ 0 →
`suspicious`) :

- **signature trouvée → `malicious`** (la source la plus grave écrase les autres dans
  `worst_status`, `base.py:16,29-31`) ;
- **rien → `clean`** ;
- **erreur / base indisponible → `suspicious`** (on ne peut pas affirmer « sûr » sans base ; on ne
  jette pas le fichier non plus → `suspicious`, comme un poison ffprobe). Un **timeout** dur du child
  remonte de toute façon en `suspicious` via `egress.parse` (`egress.py:27`), donc cohérent même si
  `clamscan` est tué par `killpg`.

Le verdict du **fichier** reste `worst_status` sur les statuts de tous les checks activés — **déjà
géré** dans `pipeline.run` (`pipeline.py:30`). clamav `malicious` écrasera donc un `ffprobe` clean,
comme `type_sniff malicious` le fait déjà (cf. `test_pipeline.py:43-50`).

> **Important — `error` n'est PAS un statut de check** (`base.py:5-6` : `Status = clean | suspicious |
> malicious`). clamav ne doit JAMAIS rendre `error`. Le `error` service-level (fichier absent) reste
> la prérogative de `check.verify_file` (`check.py:36-37`).

### 3.4 `_parse_signature` (défensif, optionnel)

Sur un match (rc 1), `clamscan --stdout` imprime une ligne du genre
`/path/to/file: Win.Test.EICAR_HDB-1 FOUND`. On extrait le token entre `: ` et ` FOUND` au mieux. Si
le format diffère / aucune ligne `FOUND` → retourne `None` (le `meta` est alors vide ; le **verdict**
`malicious` est inchangé — la signature est purement informative). Garder ce parsing **strictement
défensif et borné** (split simple, pas de regex coûteuse sur entrée hostile). Exemple de forme :

```python
def _parse_signature(stdout: bytes) -> str | None:
    for line in stdout.decode("utf-8", "replace").splitlines():
        if line.endswith(" FOUND") and ": " in line:
            return line.rsplit(": ", 1)[1].removesuffix(" FOUND").strip() or None
    return None
```

> Couverture : il faut tester **les deux** branches (`FOUND` présent → nom extrait ; absent → `None`),
> et le cas `: ` absent. Voir §10.

## 4. Câblage dans `pipeline.run`

`pipeline.run` reçoit aujourd'hui **un seul** runner (`ffprobe_runner`) en plus de la config
(`pipeline.py:19-21`). clamav a besoin **de son propre runner**. La signature de `run` change pour
accepter un `clamav_runner` (un nouveau paramètre injecté), à l'identique de `ffprobe_runner`.

**Remplacer** le commentaire `pipeline.py:29` (« tout autre nom … est ignoré (DA4) ») par la branche
`elif`, en **gardant** un commentaire pour les noms inconnus restants (DA4 reste vrai : une faute de
frappe doit toujours être ignorée silencieusement) :

```python
def run(
    header: bytes,
    path: Path,
    ffprobe_runner: FfprobeRunner,
    clamav_runner: ClamavRunner,
    cfg: AnalysisConfig,
) -> tuple[str, dict[str, object], list[dict[str, object]]]:
    outcomes: list[CheckOutcome] = []
    for name in cfg.enabled_checks:
        if name == "type_sniff":
            outcomes.append(type_sniff_check.sniff(header))
        elif name == "ffprobe":
            outcomes.append(ffprobe_check.probe(path, ffprobe_runner, cfg))
        elif name == "clamav":
            outcomes.append(clamav_check.scan(path, clamav_runner, cfg))
        # tout AUTRE nom (faute de frappe) est ignoré (DA4).
    ...
```

DA4 **tient toujours** : `clamav` ne tourne **que** s'il figure dans `enabled_checks`. Le défaut
(`config.py:14`) est INCHANGÉ → en déploiement non-configuré, `clamav` ne s'exécute pas. Et un nom
inconnu (`clamavv`) reste ignoré (le test `test_unknown_check_name_is_ignored`, `test_pipeline.py:84`,
doit être **mis à jour** pour utiliser un nom encore inconnu, p. ex. `clamavv`, puisque `clamav` est
désormais reconnu — voir §10).

### Propagation de la signature de `run` (appelants)

`pipeline.run` est appelé **uniquement** par l'enfant (`analysis_child.py:47`). Il faut donc :

1. **`analysis_child.main`** (`analysis_child.py:29-49`) : ajouter un paramètre `clamav_runner:
   ClamavRunner | None = None`, construire le `ProdClamavRunner` par défaut quand `None` (miroir de
   `ffprobe_runner`, `analysis_child.py:32,37`), et le passer à `pipeline.run`. Le `ProdClamavRunner`
   prend `config.timeout_s` (comme `ProdFfprobeRunner(config.timeout_s)`, `analysis_child.py:37`).
2. **Aucun autre appelant** (le parent `spawn.py`/`check.py` ne touche pas `pipeline.run` — il
   re-exec l'enfant).

> **Vérif d'intégrité** : `grep -rn "pipeline.run" packages/verifier` doit ne renvoyer que
> `analysis_child.py` (+ les tests). Tout test appelant `pipeline.run` directement (`test_pipeline.py`)
> doit recevoir le nouveau `clamav_runner` (un stub `clean` par défaut quand on ne teste pas clamav).

## 5. Provisioning des signatures — sidecar `freshclam` + delta compose

**Décision 1A (figée).** Le verifier ne fetch jamais : la base est **poussée** dans un volume RO par
un sidecar.

### 5.1 Topologie

```
                egress (réseau, accès Internet)        verify-internal (internal: true, pas d'Internet)
                ┌───────────────────────────┐          ┌─────────────────────────────────────┐
  Internet ───► │ freshclam (sidecar)        │          │ verifier                             │
   (db.* CDN)   │  boucle/cron : met à jour  │          │  monte clamav-db EN RO               │
                │  /var/lib/clamav  ──────────┼──┐       │  clamscan --database /clamav-db ...  │
                └───────────────────────────┘  │       └─────────────────────────────────────┘
                                                 ▼                         ▲
                                      volume nommé partagé  clamav-db  ────┘ (RO côté verifier)
```

- `freshclam` est **sur `egress`** (a Internet) ; le verifier reste **seul sur `verify-internal`**
  (`internal: true`, `compose.yaml:95-96,121-122`) → **aucun changement** au no-Internet du verifier.
- Le volume `clamav-db` est **écrit** par freshclam (RW) et **lu** par le verifier (**RO**). C'est le
  même motif que `quarantine` (amuled écrit, verifier lit RO, `compose.yaml:93`).

### 5.2 Delta compose **proposé** (NE PAS éditer `compose.yaml` dans le worktree — §4 du plan maître)

L'agent **ne touche pas** `compose.yaml` (intégration-owned). Il **livre ce delta dans son rapport** ;
l'orchestrateur l'applique en Vague 2. Le delta (profil **`full`** uniquement, comme le verifier) :

```yaml
# services: (à ajouter)
  freshclam:
    image: clamav/clamav:1.4              # image officielle ; freshclam + clamscan inclus (sidecar)
    profiles: [full]
    command: ["freshclam", "--daemon", "--foreground", "--checks=2"]  # boucle (2 maj/jour)
    environment:
      FRESHCLAM_CHECKS: "2"
    volumes:
      - clamav-db:/var/lib/clamav         # ÉCRIT la base ici (RW)
    networks:
      - egress                            # a besoin d'Internet pour fetch les signatures
    restart: unless-stopped
    # NB durcissement : freshclam doit écrire la base → PAS de read_only sur ce service
    #     (contrairement au verifier). cap_drop: ALL + no-new-privileges restent applicables.

# service verifier (compose.yaml:81-117) — AJOUTER un montage RO + le réseau reste inchangé :
  verifier:
    volumes:
      - quarantine:/quarantine:ro
      - clamav-db:/clamav-db:ro           # NOUVEAU : base de signatures EN RO
      - ./config/verifier.yaml:/config/verifier.yaml:ro
    environment:
      # ... existant ...
      CLAMAV_DB_DIR: /clamav-db           # NOUVEAU : pointe clamscan vers la base montée
      ENABLED_CHECKS: type_sniff,ffprobe,clamav   # OPT-IN : active clamav en prod full
      # rlimit overrides clamav (voir §6) — p.ex. :
      RLIMIT_AS_BYTES_CLAMAV: "1610612736"        # 1.5 Gio
      RLIMIT_CPU_S_CLAMAV: "120"

# volumes: (à ajouter)
  clamav-db: {}
```

Points figés :

- `freshclam` **N'EST PAS** sur `verify-internal` (il a besoin d'Internet) ; le verifier **n'a
  toujours pas** d'accès Internet.
- Le verifier active clamav via `ENABLED_CHECKS` **dans le compose `full`** (opt-in déploiement) :
  c'est la SEULE façon de l'allumer (décision 4). Le **smoke** (`compose.smoke.yaml`) et le profil
  **observer** restent au défaut `type_sniff,ffprobe` → clamav OFF.
- L'image `clamav/clamav` est l'**image officielle** : elle contient `freshclam` ET `clamscan`. On ne
  s'en sert ici que pour `freshclam` (le verifier a son propre `clamscan` apt, §8). C'est volontaire :
  l'image sidecar n'a pas à partager la base de code Python.

> **À confirmer (compose)** : faut-il un `depends_on` du verifier vers freshclam ? Non bloquant —
> clamav rend `suspicious` (défensif) tant que la base n'est pas là ; mais un `depends_on` avec
> `condition: service_healthy` (si l'image freshclam expose un healthcheck sur la fraîcheur de la base)
> éviterait des `suspicious` au premier boot. **Laissé à l'intégration** (Geoffrey/orchestrateur).

## 6. rlimits — le point dur (relâchement conditionnel)

**Problème.** Le child confiné applique aujourd'hui (`spawn.py:70-80`, valeurs `config.py:41-48`) :
`RLIMIT_AS = 512 Mio`, `RLIMIT_CPU = 20 s`. `clamscan` **charge toute la base de signatures en
mémoire** (la base officielle complète — `main.cvd` + `daily.cvd` + `bytecode.cvd` — pèse plusieurs
**centaines de Mo**, et l'empreinte mémoire résidente du moteur chargé est **supérieure** à la taille
sur disque). À 512 Mio d'AS, `clamscan` **crève** (allocation refusée → crash → rc ≥ 2 → notre mapping
le voit `suspicious`, donc « faux poison » systématique). De même, charger + scanner peut dépasser
20 s de CPU.

**Décision (figée)** : **relâcher `RLIMIT_AS` et `RLIMIT_CPU` du child UNIQUEMENT quand `clamav` est
dans `enabled_checks`.** Le child **reste confiné** (setsid, killpg-timeout, `RLIMIT_CORE=0`,
`RLIMIT_NPROC`, `RLIMIT_NOFILE`, `RLIMIT_FSIZE`, env minimal, cwd jetable, no-Internet par le
container) — on **monte seulement le plafond mémoire/CPU**.

### 6.1 Ordre de grandeur (à valider en `analysis_integration` — §11)

| rlimit | défaut (sans clamav) | override clamav (ordre de grandeur) | rationale |
|---|---|---|---|
| `RLIMIT_AS` | 512 Mio (`config.py:42`) | **~1.5 Gio** (`1.5 * 1024³`) | base complète chargée + heap scan + marge. 1 Gio est probablement trop juste avec bytecode ; 1.5 Gio donne du mou sans laisser le child consommer toute la RAM hôte. |
| `RLIMIT_CPU` | 20 s (`config.py:41`) | **~120 s** | premier scan = chargement base (lent) + scan ; généreux mais borné (un scan qui boucle reste tué). |

> Compromis assumé : le child clamav **peut** consommer jusqu'à ~1.5 Gio / ~120 s — un plafond **plus
> haut**, mais **toujours un plafond** (pas d'illimité). Couplé au `mem_limit` du container verifier
> (`compose.yaml:116` : `768m` aujourd'hui → **à relever** côté delta compose, p. ex. `2g`, sinon
> l'OOM-killer du cgroup tue le child avant le rlimit). **Signaler ce delta `mem_limit` à
> l'intégration** (intégration-owned).

### 6.2 Mécanisme : overrides conditionnels dans la config

Le child ne sait PAS reconfigurer ses propres rlimits *après* le `preexec_fn` (ils sont posés par le
**parent** avant exec, `spawn.py:70-80`). La config doit donc exposer la **valeur déjà résolue** que
`_confine` appliquera. Deux options de forme (équivalentes ; choisir la plus simple à tester) :

**Option A (retenue) — champs effectifs résolus dans `from_env`.** `AnalysisConfig` garde
`rlimit_as_bytes` / `rlimit_cpu_s` comme **valeurs effectives**, mais `from_env` les **résout** en
fonction de `enabled_checks` :

```python
# config.py — dans from_env, après avoir lu enabled_checks :
enabled = _parse_checks(env.get("ENABLED_CHECKS"))
clamav_on = "clamav" in enabled
rlimit_as = _parse_int(
    env.get("RLIMIT_AS_BYTES"),
    _parse_int(env.get("RLIMIT_AS_BYTES_CLAMAV"), 1536 * 1024 * 1024) if clamav_on
    else 512 * 1024 * 1024,
)
rlimit_cpu = _parse_int(
    env.get("RLIMIT_CPU_S"),
    _parse_int(env.get("RLIMIT_CPU_S_CLAMAV"), 120) if clamav_on else 20,
)
```

- Si `RLIMIT_AS_BYTES` (override **explicite, prioritaire**) est posé, il **gagne** dans tous les cas
  (l'opérateur garde la main fine ; cohérent avec le test `test_from_env_overrides_each_field`,
  `test_config.py:21`).
- Sinon, le défaut est **conditionnel** : `RLIMIT_AS_BYTES_CLAMAV` (ou 1.5 Gio) si clamav actif, sinon
  512 Mio (INCHANGÉ).
- Idem `RLIMIT_CPU_S` / `RLIMIT_CPU_S_CLAMAV` / 120 s vs 20 s.

Avantage : `spawn._confine` (`spawn.py:73-74`) et `_minimal_env`/le child sont **inchangés** (ils
lisent toujours `cfg.rlimit_as_bytes` / `cfg.rlimit_cpu_s`). Le seul fichier touché est `config.py`.

> **Subtilité env minimal (`spawn._minimal_env`, `spawn.py:98-107`)** : l'enfant **reconstruit** sa
> config depuis l'env minimal (`analysis_child.py:36`, `AnalysisConfig.from_env(os.environ)`). Mais le
> child applique des rlimits ? **Non** — c'est le **parent** qui les applique avant exec
> (`_confine`). Le child n'a donc PAS besoin de `RLIMIT_*` dans son env minimal. **MAIS** le child a
> besoin de `CLAMSCAN_PATH` et `CLAMAV_DB_DIR` (il construit `argv` de `clamscan`). Il faut donc les
> **ajouter à `_minimal_env`** (§7), exactement comme `FFPROBE_PATH` y est (`spawn.py:103`). Et —
> piège — `enabled_checks` est déjà passé (`spawn.py:102`), donc le child **re-résout** correctement
> les rlimits via `from_env` (clamav présent → mêmes valeurs effectives). C'est cohérent : parent et
> child voient la même config résolue. ✔

**Option B (rejetée pour ce design)** — exposer `rlimit_as_bytes_clamav` comme champ séparé et faire
le choix dans `_confine`. Rejetée : disperse la logique de décision dans le code système
`# pragma: no cover` (`_confine`), donc **non testable** sans `analysis_integration`. L'option A garde
toute la décision dans `from_env` (pur, 100 %-testable).

## 7. Config — nouveaux champs

`AnalysisConfig` (`config.py:18-32`) gagne **deux** champs (chemin binaire + dossier base) ; les
rlimits restent les champs existants mais **résolus conditionnellement** (§6) :

| Champ | env | défaut | usage |
|---|---|---|---|
| `clamscan_path: str` | `CLAMSCAN_PATH` | `"clamscan"` | binaire (miroir de `ffprobe_path`, `config.py:39`) |
| `clamav_db_dir: str` | `CLAMAV_DB_DIR` | `"/clamav-db"` | `--database` (le volume RO monté) |
| `rlimit_as_bytes` | `RLIMIT_AS_BYTES` / `RLIMIT_AS_BYTES_CLAMAV` | 512 Mio / 1.5 Gio (cond.) | §6 |
| `rlimit_cpu_s` | `RLIMIT_CPU_S` / `RLIMIT_CPU_S_CLAMAV` | 20 / 120 (cond.) | §6 |

`from_env` (`config.py:34-52`) ajoute les deux `env.get(...)` (forme de `ffprobe_path`,
`config.py:39`) + la résolution conditionnelle des rlimits (§6.2).

`spawn._minimal_env` (`spawn.py:98-107`) ajoute **deux** clés (le child en a besoin pour construire
l'argv clamscan) — à l'identique de `FFPROBE_PATH` :

```python
return {
    "QUARANTINE_DIR": cfg.quarantine_dir,
    "ENABLED_CHECKS": ",".join(cfg.enabled_checks),
    "FFPROBE_PATH": cfg.ffprobe_path,
    "CLAMSCAN_PATH": cfg.clamscan_path,        # NOUVEAU
    "CLAMAV_DB_DIR": cfg.clamav_db_dir,        # NOUVEAU
    "HEADER_BYTES": str(cfg.header_bytes),
    "ANALYSIS_TIMEOUT_S": str(cfg.timeout_s),
    "PATH": _MINIMAL_PATH,
}
```

> Le test `test_minimal_env_contains_only_whitelisted_vars` (`test_spawn.py:94-109`) **assert le set
> exact** des clés → il **DOIT** être étendu aux deux nouvelles. Idem
> `test_from_env_uses_defaults_when_empty` / `_overrides_each_field` (`test_config.py:6,21`) → ajouter
> les assertions des deux champs + des défauts rlimit conditionnels (cas clamav ON / OFF).

Le parent (service) et le **mini-loader d'observabilité** (`obs_config.py`) ne changent pas. Côté
crawler : **aucune config nouvelle** (DA2 ; le verifier reste stateless/no-domain).

## 8. Image verifier (`Dockerfile`)

**Décision (figée, §8 du brief).** Le runtime du verifier installe le **binaire `clamscan`** ; **PAS**
`freshclam` (il vit dans le sidecar). Sur Debian bookworm, le paquet est **`clamav`** (fournit
`/usr/bin/clamscan`). Le paquet `clamav-freshclam` n'est **PAS** installé.

Modifier la couche apt du runtime (`packages/verifier/Dockerfile:28-31`) :

```dockerfile
# ffmpeg fournit ffprobe (D-analysis) ; clamav fournit clamscan (check par signatures, opt-in).
# freshclam N'EST PAS installé (la base vient d'un volume RO peuplé par le sidecar freshclam).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg clamav \
    && rm -rf /var/lib/clamav/* /var/lib/apt/lists/*
```

- `--no-install-recommends` **évite** que `clamav` tire `clamav-freshclam` (recommandé) → on garde
  juste `clamscan` + le moteur `libclamav`. **À vérifier** : sur certaines versions Debian, le paquet
  `clamav` recommande `clamav-freshclam` mais ne le *dépend* pas — `--no-install-recommends` suffit.
  **Confirmer** que `clamscan --version` marche sans `clamav-freshclam` (oui : `clamscan` ne dépend que
  de `libclamav`).
- `rm -rf /var/lib/clamav/*` : l'install apt peut déposer un répertoire base vide ; on le purge — la
  base **réelle** vient du volume RO `/clamav-db` (jamais `/var/lib/clamav`).

**Taille image (à noter).** `clamav` + `libclamav` ajoutent **~50–80 Mo** à l'image runtime (le moteur,
pas la base — la base n'est PAS dans l'image, c'est tout l'intérêt du sidecar). C'est un coût
acceptable et **borné** (sans la base, qui ferait +300–500 Mo). À documenter dans le rapport de l'agent
+ le runbook.

> Le `clamscan` apt n'a **pas besoin d'Internet** à l'exécution (base locale). L'image verifier reste
> donc compatible `internal: true`. ✔

## 9. Frontières & invariants à NE PAS casser

- **Pattern injectable** : `scan` est pure ; `ClamavRunner`/`ProdClamavRunner` calquent `ffprobe.py`.
  Le runner PROD est `# pragma: no cover`, son **constructeur** est couvert.
- **worst-status inchangé** : `pipeline.run` agrège toujours via `worst_status` (`base.py`) ;
  `clamav malicious` écrase, `clamav suspicious` n'écrase pas un autre `malicious`. Rien à toucher
  dans `base.py` / `egress.py` / `app.py` / `check.py`.
- **DA4 (opt-in) intact** : défaut `ENABLED_CHECKS` INCHANGÉ (`config.py:14`,
  `test_config.py:7-8`). clamav ne tourne que si activé **et** la base est montée.
- **Confinement préservé** : seuls `RLIMIT_AS`/`RLIMIT_CPU` montent (conditionnels) ; setsid / killpg /
  `RLIMIT_CORE=0` / NPROC / NOFILE / FSIZE / env minimal / cwd jetable / no-Internet container :
  **inchangés** (`spawn.py:70-80`).
- **`internal: true` préservé** : le verifier ne fetch jamais ; la base arrive par volume RO. Le
  parent ne lit **jamais** les octets (clamav tourne dans l'enfant). ✔
- **Aucun import croisé** : le verifier n'importe rien de `emule_indexer` ; aucune modif crawler.
- **`error` interdit comme statut de check** (`base.py:5-6`) : clamav rend uniquement
  `clean|suspicious|malicious`.

## 10. Plan de tests TDD (rouge d'abord ; 100 % branch)

Ordre TDD recommandé : **config → clamav check → pipeline → spawn env → child** (chaque test écrit et
**vu rouge** avant l'implémentation). Tous les tests unit injectent un runner ; **aucun** ne lance de
vrai `clamscan` (réservé `analysis_integration`, §11).

### 10.1 `tests/test_clamav.py` (NOUVEAU — calque `test_ffprobe.py`)

Stub runner identique à `_StubRunner` (`test_ffprobe.py:42-52`) : `(rc, stdout) → capture argv`.
Cas **branch** à couvrir (chaque `return`/branche de `scan` + `_parse_signature`) :

| Test | Entrée stub | Attendu |
|---|---|---|
| `test_prod_clamav_runner_constructs` | — | `ProdClamavRunner(30.0)._timeout_s == 30.0` (constructeur non-pragma) |
| `test_stub_runner_satisfies_protocol` | — | `runner: ClamavRunner = _StubRunner(...)` typé (mypy vérifie le Protocol) |
| `test_clean_when_rc_zero` | `(0, b"")` | `status == "clean"`, `meta == {}` |
| `test_malicious_when_rc_one_with_signature` | `(1, b"/q/f: Eicar-Test-Signature FOUND\n")` | `status == "malicious"`, `meta["clamav_signature"] == "Eicar-Test-Signature"` |
| `test_malicious_when_rc_one_without_parsable_signature` | `(1, b"garbage")` | `status == "malicious"`, `"clamav_signature" not in meta` |
| `test_suspicious_when_rc_two` | `(2, b"ERROR: ...")` | `status == "suspicious"`, `meta == {}` |
| `test_suspicious_when_rc_other` | `(40, b"")` | `status == "suspicious"` (garde la branche `else` du `≥2`) |
| `test_argv_uses_frozen_flags_and_db_and_path` | `(0, b"")` | argv == `[clamscan_path, "--no-summary", "--stdout", "--database", db_dir, "/q/abc"]` (cf. `test_ffprobe.py:86-99`) |
| `test_signature_line_without_colon_space_returns_none` | `(1, b"NoColon FOUND")` | `"clamav_signature" not in meta` (branche `": " in line` False) |

> Les deux dernières lignes garantissent les **deux côtés** de chaque conditionnel de
> `_parse_signature` (la règle « exercer les deux branches »). Bien couvrir : `endswith(" FOUND")`
> True/False, `": " in line` True/False, et le `or None` (token vide).

### 10.2 `tests/test_pipeline.py` (étendre)

- **Mettre à jour la signature** : tous les appels `pipeline.run(...)` passent désormais un
  `clamav_runner`. Ajouter un `_StubClamav` (calque `_StubFfprobe`, `test_pipeline.py:11-18`) et un
  défaut « clean » pour les tests qui ne ciblent pas clamav.
- `test_unknown_check_name_is_ignored` (`test_pipeline.py:84-92`) : **remplacer** `"clamav"` par un nom
  TOUJOURS inconnu (`"clamavv"` ou `"bogus"`), sinon le test ment (clamav est maintenant câblé).
- **Nouveaux** :
  - `test_clamav_malicious_overrides_clean_media` : header média clean + ffprobe clean + clamav
    `(1, "...FOUND")` → verdict `malicious` ; `checks` contient les 3 noms.
  - `test_clamav_clean_keeps_clean` : 3 checks clean → `clean` ; `[c["name"] for c in checks] ==
    ["type_sniff", "ffprobe", "clamav"]`.
  - `test_clamav_suspicious_aggregates` : ffprobe clean + clamav `(2, ...)` → `suspicious`.
  - `test_enabled_checks_selects_only_clamav` : `ENABLED_CHECKS="clamav"` → seul clamav tourne
    (`[c["name"] …] == ["clamav"]`), ffprobe/type_sniff désactivés (calque
    `test_enabled_checks_selects_only_ffprobe`, `test_pipeline.py:72-81`).

### 10.3 `tests/test_config.py` (étendre)

- `test_from_env_uses_defaults_when_empty` (`test_config.py:6`) : ajouter
  `cfg.clamscan_path == "clamscan"`, `cfg.clamav_db_dir == "/clamav-db"`, et — **clamav OFF par
  défaut** → `cfg.rlimit_as_bytes == 512*1024*1024`, `cfg.rlimit_cpu_s == 20` (INCHANGÉ).
- `test_from_env_overrides_each_field` (`test_config.py:21`) : ajouter `CLAMSCAN_PATH`/`CLAMAV_DB_DIR`.
- **Nouveaux** (le cœur de §6) :
  - `test_clamav_enabled_raises_rlimits_to_defaults` : `ENABLED_CHECKS="type_sniff,ffprobe,clamav"`
    sans override explicite → `rlimit_as_bytes == 1536*1024*1024`, `rlimit_cpu_s == 120`.
  - `test_clamav_enabled_respects_clamav_override` : `+ RLIMIT_AS_BYTES_CLAMAV`/`RLIMIT_CPU_S_CLAMAV`
    → valeurs custom.
  - `test_explicit_rlimit_wins_over_clamav_default` : clamav ON **+** `RLIMIT_AS_BYTES=1234` →
    `rlimit_as_bytes == 1234` (l'override explicite prime sur le défaut conditionnel).
  - `test_clamav_off_keeps_baseline_rlimits` : clamav absent + aucun override → 512 Mio / 20 s.

  Ces 4 tests couvrent les **deux** côtés de `clamav_on` ET les **deux** côtés du « override explicite
  vs défaut conditionnel » → toutes les branches de la résolution §6.2.

### 10.4 `tests/test_spawn.py` (étendre)

- `test_minimal_env_contains_only_whitelisted_vars` (`test_spawn.py:94-109`) : le `set(...)` exact des
  clés gagne `CLAMSCAN_PATH` + `CLAMAV_DB_DIR` ; assert leurs valeurs (`cfg.clamscan_path` /
  `cfg.clamav_db_dir`).

### 10.5 `tests/test_analysis_child.py` (étendre)

- L'appel à `pipeline.run` dans le child reçoit le `clamav_runner`. Vérifier que `main` injecte un
  `clamav_runner` (paramètre `clamav_runner: ClamavRunner | None = None`, défaut `ProdClamavRunner`),
  et qu'un stub injecté est bien utilisé (calque le pattern `ffprobe_runner` injecté des tests child).
  Couvrir la branche `clamav_runner is None` (défaut PROD construit) **et** injecté.

> Toutes les branches `# pragma: no cover` (les `__call__` PROD, le `if __name__` du child) restent
> non comptées — comme pour ffprobe. Lancer le gate verifier `( cd packages/verifier && uv run pytest
> -q )` doit rester **100 % branch**.

## 11. Ce qui revient à Geoffrey (`analysis_integration`, shell réel)

Le sandbox ne peut PAS exécuter de vrai `clamscan` ni provisionner une vraie base
(`integration-tests-need-real-shell` + pas de réseau). L'agent **écrit** les tests d'intégration
**sans les lancer** ; Geoffrey les exécute. Étendre `tests/test_analysis_integration.py` (marqueur
`pytest.mark.analysis_integration`, `pyproject.toml:24-26`), **skip-if** `clamscan`/base absents
(calque `_NEEDS_FFMPEG`, `test_analysis_integration.py:25-30`) :

- `_NEEDS_CLAMAV = skipif(shutil.which("clamscan") is None or <base absente>, …)`.
- **EICAR** : le fichier test antivirus standard
  (`X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*`) écrit sous le nom = hash,
  `ENABLED_CHECKS="clamav"` (+ `CLAMAV_DB_DIR` réel) → verdict **`malicious`**. C'est LE test qui
  prouve le scan réel de bout en bout (re-exec child + rlimits relâchés + vrai `clamscan` + vraie
  base).
- Un vrai petit média (le `ffmpeg` existant, `test_analysis_integration.py:52-87`) avec
  `ENABLED_CHECKS="type_sniff,ffprobe,clamav"` → **`clean`** (prouve que clamav ne crève pas le rlimit
  relâché sur un fichier sain ; **valide l'ordre de grandeur §6.1** — si `clamscan` OOM/CPU-kill, le
  verdict tombera `suspicious` et le test échouera → signal pour ajuster les rlimits).
- Base absente (`CLAMAV_DB_DIR` vide) + `ENABLED_CHECKS="clamav"` → **`suspicious`** (rc ≥ 2 défensif).

Geoffrey :
1. lance `( cd packages/verifier && uv run pytest -m analysis_integration --no-cov )` avec
   `clamscan` + une base (`freshclam` local, ou le volume du sidecar) ;
2. **valide les rlimits §6.1** : si le média sain ressort `suspicious`/le child est tué, relever
   `RLIMIT_AS_BYTES_CLAMAV` / `RLIMIT_CPU_S_CLAMAV` jusqu'à `clean`, et reporter la valeur retenue ;
3. côté Docker (hors `analysis_integration`) : valider que le sidecar `freshclam` peuple `clamav-db`
   et que le verifier le lit en RO sans casser `internal: true` (checklist « réseau vivant »,
   plan maître §7).

## 12. Risques / à confirmer

1. **Empreinte mémoire réelle de `clamscan`** — l'ordre de grandeur §6.1 (1.5 Gio AS) est une
   **estimation** ; la valeur exacte dépend de la version du moteur et de la base. **À mesurer** en
   `analysis_integration` (point §11.2). Risque : si 1.5 Gio est insuffisant, faux `suspicious`
   systématiques. Mitigation : override env `RLIMIT_AS_BYTES_CLAMAV` ajustable sans rebuild.
2. **`mem_limit` du container verifier** (`compose.yaml:116`, `768m`) est **inférieur** au rlimit AS
   clamav proposé (1.5 Gio) → l'OOM-killer du cgroup tuerait le child **avant** le rlimit. **Le delta
   compose DOIT relever `mem_limit`** (p. ex. `2g`) — c'est un **fichier intégration-owned**, donc un
   delta proposé, pas une édition de l'agent. **À confirmer/dimensionner avec Geoffrey.**
3. **`pipeline.run` change de signature** (nouveau `clamav_runner`). C'est une fonction **interne** au
   paquet verifier, appelée seulement par `analysis_child` (+ tests) → pas de contrat de fil cassé (le
   contrat HTTP `{verdict, real_meta, checks}` est INCHANGÉ). Mais **tous** les appelants/tests de
   `pipeline.run` doivent être migrés en même temps (sinon mypy/tests rouges). Listé en §4/§10.2.
4. **`test_unknown_check_name_is_ignored`** utilise aujourd'hui `"clamav"` comme nom inconnu
   (`test_pipeline.py:84-92`). Une fois clamav câblé, ce test **doit changer de nom inconnu** sinon il
   teste l'inverse de son intention. Consigné §10.2 — **pas un re-design**, juste une mise à jour de
   test rendue nécessaire par la décision figée.
5. **Paquet apt `clamav` vs `clamav-freshclam`** — l'hypothèse est que `clamscan` fonctionne avec le
   seul paquet `clamav` (+ `libclamav`) sans `clamav-freshclam`, via `--no-install-recommends`. **À
   confirmer** au build (`clamscan --version` doit réussir). Si Debian force `freshclam` en dépendance
   dure (peu probable), il faudra le neutraliser (pas de cron freshclam dans l'image verifier — la
   base vient du volume).
6. **Format de sortie `clamscan --stdout`** pour `_parse_signature` — le format `<file>: <sig> FOUND`
   est stable de longue date, mais le parsing est **strictement défensif** (échec → `None`, verdict
   inchangé). Aucun verdict ne dépend du parsing. Risque nul sur le verdict, faible sur le `meta`.
7. **Ordre clamav dans `enabled_checks`** — le delta compose le met en **dernier**
   (`type_sniff,ffprobe,clamav`). worst-status est commutatif → l'ordre n'affecte pas le verdict,
   seulement l'ordre de la liste `checks` (cosmétique). Aucun risque.
8. **Séquencement avec `ring-noyau`** (même worktree WT-verifier, exécuté APRÈS) — le ring noyau
   ajoutera du confinement (seccomp/namespaces) au child. clamav **doit pouvoir tourner sous ce ring**
   (lire la base RO + le fichier + allouer la mémoire relâchée). À garder en tête côté ring-noyau :
   le profil seccomp ne doit pas bloquer les syscalls de `clamscan` (mmap de la base, etc.). **Signalé
   ici** pour le design ring-noyau à venir ; **hors scope** de ce doc.

---

**Récapitulatif des fichiers touchés** (par l'agent, dans le worktree) :
`packages/verifier/src/download_verifier/checks/clamav.py` (NEUF),
`.../pipeline.py` (branche `elif` + param `clamav_runner`), `.../config.py` (2 champs + rlimits
conditionnels), `.../spawn.py` (`_minimal_env` : 2 clés), `.../analysis_child.py` (injecte
`clamav_runner`), `packages/verifier/Dockerfile` (apt `clamav`) ; tests : `test_clamav.py` (NEUF),
`test_pipeline.py`, `test_config.py`, `test_spawn.py`, `test_analysis_child.py`,
`test_analysis_integration.py` (étendus).

**Deltas intégration-owned proposés** (PAS édités par l'agent — rapport → orchestrateur, plan maître
§4) : `compose.yaml` (sidecar `freshclam` + volume `clamav-db` + montage RO verifier + `ENABLED_CHECKS`
full + `mem_limit` relevé) ; `pyproject.toml`/`uv.lock` (**aucune dépendance Python ajoutée** — clamav
est un binaire système ; rien à locker) ; `docs/runbook-deployment.md` (provisioning base + taille
image).
