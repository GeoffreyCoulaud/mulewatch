# Spec — emule-indexer : ring noyau (filtre seccomp-bpf par-enfant d'analyse)

> **Sous-projet** : tâche **structurante** de la Vague 1 (worktree **WT-verifier**, séquencée
> **APRÈS clamav** dans le même worktree car overlap `config.py`/`pipeline.py`/`spawn.py` —
> cf. `docs/superpowers/specs/2026-06-15-backlog-parallelization-design.md` §4-5). Elle ajoute le
> **premier morceau de « ring noyau »** au confinement de l'enfant d'analyse du verifier : un
> **filtre seccomp-bpf par-enfant** qui réduit la surface d'attaque noyau d'un 0-day
> ffprobe/clamscan et coupe le mouvement latéral.
>
> **Réfs amont** :
> - MVP design `2026-06-10-crawler-mvp-design.md` §10 (sécurité/confinement : « fork enfant jetable
>   par fichier », « ring noyau »).
> - Analysis design `2026-06-14-analysis-design.md` §2 (DA1 — « Confinement portable maintenant,
>   ring noyau au Plan F »), §11 (hors-périmètre : « seccomp, bwrap/nsjail/gVisor → Plan F »), §12
>   (risques). **Ce document lève ce report** : la part *container* du ring est livrée (Plan F :
>   non-root 999 + `cap_drop: ALL` + `no-new-privileges` + `read_only` + `internal: true` +
>   gVisor opt-in) ; il reste la part *code de l'enfant* — c'est l'objet de cette spec.
> - Packaging design `2026-06-14-packaging-design.md` (durcissement conteneur, `compose.hardening.yml`
>   gVisor opt-in, réseau `verify-internal` `internal: true`).
> - Backlog `docs/handoffs/2026-06-15 - handoff - post-E checkpoint (backlog).md` (item « ring noyau »).
>
> **Nature** : le design est **figé** (co-design fait avec Geoffrey). Ce document le documente
> fidèlement, ancré dans le code réel, pour qu'un implémenteur frais l'exécute en TDD strict. Toute
> contradiction relevée avec le code va en `## 12. Risques / à confirmer`, **pas** dans une
> re-décision.

---

## 1. Contexte — ce que le confinement actuel couvre déjà, ce qui manque

L'enfant d'analyse (`download_verifier.analysis_child`) est re-exec **par fichier** depuis le
parent service (`spawn.py:83` `run_analysis` → `ProdChildRunner.__call__`, `spawn.py:46`). Le
confinement actuel se compose de **deux rings empilés** :

**Ring conteneur (Plan F, déjà livré, `compose.yaml`)** — s'applique au process verifier *et* à
tous ses descendants :
- non-root `user: "999:999"` (`compose.yaml:107`) ;
- `cap_drop: ALL` (`compose.yaml:111-112`) → aucune capability ;
- `security_opt: no-new-privileges:true` (`compose.yaml:113-114`) → **`no_new_privs` est déjà posé
  par le conteneur** (fait crucial pour cette spec, cf. §3) ;
- `read_only: true` + `tmpfs: /tmp` (`compose.yaml:108-110`) ;
- quarantaine montée **RO** (`compose.yaml:93` `:ro`) → l'enfant ne peut pas écrire les octets
  hostiles ;
- réseau `verify-internal` **`internal: true`** (`compose.yaml:96`, `121-122`) → **aucun egress
  Internet** pour le verifier ni son enfant ;
- `pids_limit: 256` + `mem_limit: 768m` (`compose.yaml:115-116`) ;
- **opt-in** gVisor `runtime: runsc` (`compose.hardening.yml:13-14`) → ring noyau syscall-émulé.

**Ring process (D-analysis, déjà livré, `spawn.py:_confine` lignes 70-80)** — appliqué dans le
`preexec_fn` du fork avant `exec`, donc hérité par l'enfant et son petit-fils ffprobe :
- `os.setsid()` (groupe de process dédié → kill du groupe au timeout, `spawn.py:71`) ;
- rlimits durs : `RLIMIT_CPU`, `RLIMIT_AS`, `RLIMIT_FSIZE`, `RLIMIT_NPROC`, `RLIMIT_NOFILE`
  (`spawn.py:73-77`) ;
- `RLIMIT_CORE = 0` (`spawn.py:80`) → pas de core dump (DA8 : pas d'octets hostiles écrits) ;
- `close_fds=True` (`spawn.py:56`) + env explicite minimal sans `os.environ` (`spawn.py:98-107`,
  DA8) ;
- cwd `tempfile.mkdtemp()` jetable supprimé en `finally` (`spawn.py:88-94`).

**Ce qui manque — le filtre seccomp applicatif.** Aucun des deux rings ne **restreint l'ensemble
des syscalls** que l'enfant (et son petit-fils ffprobe/clamscan) peut adresser au noyau hôte.
Concrètement :
- Hors gVisor (cas par défaut), ffprobe et clamscan parsent du **contenu hostile** en présentant
  toute la surface du noyau Linux hôte. Un 0-day d'analyse de format dans ffprobe/clamscan peut
  appeler n'importe quel syscall — y compris `socket`/`connect` (mouvement latéral), `ptrace`
  (injection dans un voisin), ou un syscall noyau exotique exploitable.
- gVisor (`runsc`) émule le noyau et réduirait cette surface — mais il est **opt-in** et exige
  gVisor installé sur l'hôte (`compose.hardening.yml:4-6`). On ne peut pas en dépendre par défaut.

Le filtre seccomp comble ce trou **dans le code de l'enfant**, donc **toujours actif** (par défaut,
sans gVisor, sans capability), portable d'un hôte à l'autre.

## 2. Valeur réelle (franche — defense-in-depth, pas anti-exfiltration)

Soyons honnêtes sur ce que seccomp apporte **ici**, vu les rings déjà en place :

- **Le réseau verifier est déjà `internal: true`** (`compose.yaml:96`) → l'enfant n'a **déjà aucun
  egress Internet**. La valeur de seccomp **n'est PAS** « empêcher l'exfiltration des octets
  hostiles vers Internet » : c'est déjà couvert au niveau réseau Docker.
- **La quarantaine est déjà montée RO** et `RLIMIT_FSIZE`/`RLIMIT_CORE=0` empêchent déjà
  l'écriture des octets hostiles.

La valeur réelle de seccomp est **double**, et purement *defense-in-depth* :

1. **Réduire la surface d'attaque noyau.** ffprobe et clamscan sont des parseurs de **contenu
   hostile** (`analysis-design.md` §12 : « ffprobe est lui-même un parseur de contenu hostile »).
   Un 0-day dans l'un d'eux qui obtient l'exécution de code arbitraire est limité aux seuls
   syscalls que le filtre autorise → un exploit qui repose sur un syscall noyau dangereux (p. ex.
   un `keyctl`/`userfaultfd`/`io_uring`-LPE) est neutralisé avant d'atteindre le noyau hôte.
2. **Couper le mouvement latéral *intra-conteneur*.** `internal: true` bloque Internet mais **pas**
   le réseau interne `verify-internal` (Unix sockets, boucle locale, voisins éventuels). Deny
   `socket`/`connect`/`bind` empêche un enfant compromis d'**ouvrir le moindre socket** — il ne
   peut contacter ni un voisin, ni la boucle locale, ni quoi que ce soit. Deny `ptrace` empêche
   l'injection dans un autre process du conteneur.

C'est donc une **couche de plus** (Swiss-cheese), pas une frontière neuve indispensable. À
documenter franchement : si l'implémenteur cherche « le » bénéfice unique, c'est la réduction de
surface noyau + la coupure réseau intra-conteneur — **pas** l'anti-exfiltration (déjà acquise).

## 3. Pourquoi c'est portable (seccomp n'exige aucune capability)

Le point décisif qui rend cette tâche faisable **sans toucher au durcissement conteneur
`cap_drop: ALL`** :

- Installer un filtre seccomp **sur soi-même** exige **soit** `CAP_SYS_ADMIN`, **soit** que
  `no_new_privs` (PR_SET_NO_NEW_PRIVS) soit déjà positionné sur le thread appelant. C'est la
  sémantique noyau de `prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, …)` / `seccomp(2)`.
- **Le conteneur pose déjà `no_new_privs`** via `security_opt: no-new-privileges:true`
  (`compose.yaml:113-114`). Donc l'enfant — comme tout process du conteneur — a `no_new_privs=1`
  **dès son démarrage**. Il peut donc s'installer un filtre seccomp **sans aucune capability**,
  ce qui est exactement compatible avec `cap_drop: ALL`.
- **Pas de namespaces.** Un `net=none` (network namespace), un mount namespace pour de vrais
  montages RO, ou un pid namespace exigeraient `CAP_SYS_ADMIN` (ou un user namespace activé) — que
  `cap_drop: ALL` **interdit**. **On n'y va PAS** : ce serait incompatible avec le durcissement
  conteneur existant, et redondant avec `internal: true` (réseau) + le mount RO Docker. Le ring
  noyau de cette spec est **seccomp-only**.

**Conséquence d'implémentation** : aucun changement de `compose.yaml` (les capabilities, le réseau,
`no-new-privileges` restent tels quels). Le seul prérequis runtime — `no_new_privs` — est **déjà
satisfait** par la stack en prod. (En dev/CI hors conteneur, `no_new_privs` n'est pas forcément
posé ; l'installation du filtre le pose elle-même quand on le demande explicitement, cf. §6 — ou
échoue proprement, cf. §10/§12.)

## 4. Filtre = blocklist (deny-quelques-uns, allow-le-reste)

**Décision figée : un filtre seccomp en *blocklist*, pas en *allowlist*.**

- **Allowlist** (`default KILL_PROCESS` + `add_rule(ALLOW, …)` sur une liste blanche) serait plus
  stricte mais **fragile** : Python (CPython 3.12) émet un ensemble de syscalls riche et *variable*
  (allocation mémoire `mmap`/`brk`/`madvise`, signaux `rt_sigaction`/`rt_sigprocmask`,
  `epoll`/`futex`, GC, imports, ouverture de fichiers `openat`/`newfstatat`, `getrandom`, etc.), et
  ffprobe/clamscan en émettent d'autres encore. Une allowlist incomplète **tue le démarrage de
  l'interpréteur** ou un check légitime → faux `suspicious`/crash. Maintenir une allowlist exacte
  à travers les versions de Python/glibc/ffmpeg est un puits sans fond.
- **Blocklist** (`default ALLOW` + `add_rule(<deny>, …)` sur une liste noire courte et ciblée) :
  **n'empêche jamais le démarrage de Python** ni un check légitime (ils n'appellent pas les
  syscalls de la liste noire en marche normale), tout en fermant les portes qui comptent vraiment
  pour la valeur de §2. C'est le bon compromis robustesse/bénéfice **pour une couche
  defense-in-depth**.

**Action de deny figée : `ERRNO(EPERM)`**, *pas* `KILL_PROCESS`. Justification :
- `KILL_PROCESS` sur, p. ex., `socket` ferait **mourir l'enfant** → égress vide → mappé
  `suspicious` par `egress.parse` (`egress.py:27`). Or un `socket()` peut apparaître dans du code
  *légitime mais inattendu* (une lib qui sonde `/etc/resolv.conf`, un getaddrinfo paresseux) →
  faux positif `suspicious` sur un média sain. `EPERM` laisse l'appelant **gérer l'échec lui-même**
  (la plupart des libs dégradent gracieusement) → moins de faux positifs, valeur de sécurité
  identique (le syscall ne s'exécute jamais). **Exception : `ptrace` est deny en `KILL_PROCESS`**
  — aucune raison légitime qu'un parseur de média trace un autre process ; un `ptrace` est un
  signal d'attaque univoque → on tue.

### Liste des syscalls à deny (figée, justifiée, compatible Python/ffprobe/clamscan-local)

| Syscall(s) | Action | Pourquoi deny | Pourquoi sûr (jamais en marche normale) |
|---|---|---|---|
| `socket` | `ERRNO(EPERM)` | Coupe toute ouverture de socket → pas de mouvement latéral réseau (§2.2). | Python/ffprobe/clamscan **analysant un fichier local** n'ouvrent aucun socket. clamscan en mode `--no-summary` sur un chemin local ne parle pas à `clamd` (cf. §12). |
| `socketcall` | `ERRNO(EPERM)` | Équivalent multiplexé de `socket`/`connect`/… sur certaines archis (i386). | Idem ; absent sur x86-64/arm64 → `add_rule` lève `OSError`, **ignoré** (cf. §6). |
| `connect` | `ERRNO(EPERM)` | Empêche de contacter un voisin/la boucle locale même si un fd socket existait. | Aucune connexion sortante dans l'analyse d'un fichier local. |
| `bind` | `ERRNO(EPERM)` | Empêche d'ouvrir un port d'écoute (backdoor/pivot). | Aucun service écouté par l'enfant. |
| `listen` | `ERRNO(EPERM)` | Idem `bind` — pas de socket serveur. | Idem. |
| `accept`, `accept4` | `ERRNO(EPERM)` | Pas d'acceptation de connexion entrante. | Idem. |
| `ptrace` | `KILL_PROCESS` | Bloque l'injection/inspection d'un autre process du conteneur. | Un parseur de média ne trace jamais → tout `ptrace` est une attaque. |
| `process_vm_readv`, `process_vm_writev` | `ERRNO(EPERM)` | Lecture/écriture mémoire cross-process (exfiltration/injection sans ptrace). | Jamais émis par Python/ffprobe/clamscan en analyse de fichier. |
| `ptrace`-famille noyau dangereux : `bpf` | `ERRNO(EPERM)` | Charger un programme BPF (LPE/persistance noyau). | Jamais émis en analyse de fichier. |
| `userfaultfd` | `ERRNO(EPERM)` | Primitive d'exploitation noyau classique (heap-spray race). | Jamais émis en analyse de fichier. |

**Notes de cadrage de la liste** (figées) :
- **On ne touche PAS** `clone`/`fork`/`execve`/`vfork` : l'enfant **doit** pouvoir `fork`+`exec`
  son petit-fils ffprobe/clamscan (`ProdFfprobeRunner.__call__`, `ffprobe.py:37` ; le futur
  `ProdClamscanRunner` de la tâche clamav). Les borner relève de `RLIMIT_NPROC` (`spawn.py:76`),
  pas de seccomp. **Le filtre seccomp est hérité par le petit-fils** (fork/exec sous `no_new_privs`
  préserve le filtre — c'est garanti par le noyau), donc ffprobe/clamscan tournent **eux aussi**
  sous la blocklist sans rien à faire de plus. C'est le point clé : on installe une fois dans
  l'enfant, la couverture s'étend à tout le sous-arbre.
- **On ne deny PAS** les syscalls fichier (`openat`/`read`/`write`/`newfstatat`/…) : l'enfant lit
  l'en-tête RO (`analysis_child.py:42`), ffprobe ouvre le fichier, clamscan le scanne. Le RO est
  assuré par le mount Docker, pas par seccomp.
- **`open`/`creat` vs `openat`** : ne rien deny ici (Python utilise `openat`, et borner l'écriture
  relève déjà du mount RO + `RLIMIT_FSIZE`). seccomp ne duplique pas ce que le mount fait mieux.
- Cette liste est **délibérément courte et conservatrice** : chaque entrée est soit « jamais émise
  en analyse de fichier local », soit (réseau) « émise seulement par du code qui sort de son rôle ».
  L'objectif est zéro faux positif sur un média sain **et** la fermeture des portes de §2. Élargir
  la liste est un follow-up possible mais **hors scope figé** (on ne re-designe pas).

## 5. L'abstraction injectable : un `Confiner` Protocol

Miroir exact du pattern `ChildRunner`/`FfprobeRunner` (Protocol + impl prod `# pragma: no cover` +
stub injecté en test) — c'est ce qui préserve le **100 % branch** sans jamais installer un vrai
filtre seccomp dans la suite unitaire.

**Nouveau module `confine.py`** (côté enfant, dans `packages/verifier/src/download_verifier/`) :

```python
"""Ring noyau de l'enfant d'analyse : filtre seccomp-bpf par-enfant (blocklist).

``apply_seccomp`` installe un filtre seccomp ``ALLOW`` par défaut qui DENY un petit ensemble de
syscalls réseau/dangereux (cf. spec ring noyau §4) — réduit la surface d'attaque noyau d'un 0-day
ffprobe/clamscan et coupe le mouvement latéral. Le filtre est HÉRITÉ par le petit-fils
(fork/exec sous ``no_new_privs``). Le ``Confiner`` est INJECTABLE : l'impl PROD installe le vrai
filtre via ``pyseccomp`` (``# pragma: no cover`` — couvert par analysis_integration) ; les tests
injectent un no-op. AUCUNE capability requise : ``no_new_privs`` est déjà posé par le conteneur
(``no-new-privileges:true``, compose.yaml) — voir spec §3.
"""

from typing import Protocol


class Confiner(Protocol):
    """Installe le ring noyau sur le process courant. Injecté pour les tests."""

    def __call__(self) -> None: ...


class ProdConfiner:
    """``Confiner`` de PROD : vrai filtre seccomp (couvert par analysis_integration)."""

    def __call__(self) -> None:  # pragma: no cover
        ...  # cf. §7 — pyseccomp SyscallFilter(ALLOW) + add_rule(...) + load()


class NoopConfiner:
    """``Confiner`` no-op : ne pose AUCUN filtre. Défaut quand le ring est désactivé/indispo."""

    def __call__(self) -> None:
        return None
```

`NoopConfiner.__call__` est une **vraie ligne couverte** (return None) — pas un pragma : c'est le
chemin « ring désactivé » exécuté par défaut hors prod (et le fallback de §10). Les tests injectent
soit `NoopConfiner`, soit un stub-espion (`_RecordingConfiner` qui note qu'il a été appelé) pour
prouver l'**ordre d'installation** (§9).

## 6. Implémentation : `pyseccomp` vs `prctl` brut par `ctypes`

Deux options pèsent ; **on tranche pour `pyseccomp`** (option lib).

**Option A — `pyseccomp` (lib, RETENUE).** Interface pure-Python de libseccomp via ctypes,
API-compatible avec les bindings Python officiels de libseccomp, sur PyPI (`/cptpcrd/pyseccomp`,
source High). L'`ProdConfiner` devient :

```python
import errno
import pyseccomp  # pragma: no cover (tout ProdConfiner.__call__ est pragma)

filt = pyseccomp.SyscallFilter(pyseccomp.ALLOW)          # blocklist : allow par défaut
for name in ("socket", "socketcall", "connect", "bind",
             "listen", "accept", "accept4",
             "process_vm_readv", "process_vm_writev", "bpf", "userfaultfd"):
    try:
        filt.add_rule(pyseccomp.ERRNO(errno.EPERM), name)
    except OSError:
        pass  # syscall absent de cette arch (ex. socketcall sur x86-64) → ignoré
filt.add_rule(pyseccomp.KILL_PROCESS, "ptrace")
filt.load()                                              # applique au process courant
```

- **Avantages** : lisible, maintenable, résolution de nom→numéro de syscall **par arch** gérée par
  la lib (un atout réel pour amd64 **et** arm64, les deux cibles GHCR du Plan F) ; `OSError` propre
  pour un syscall absent de l'arch (`socketcall` sur x86-64) → on `pass` (pattern documenté par la
  lib elle-même). `load()` pose le filtre sur le thread courant ; comme `no_new_privs` est déjà là,
  pas besoin de privilège.
- **Coût image** : `pyseccomp` est **pure-Python (ctypes)** mais **charge `libseccomp.so.2` à
  l'exécution** → il faut **`libseccomp2`** dans l'image runtime du verifier. C'est **un paquet apt
  léger** (~quelques centaines de Ko), à ajouter à côté de `ffmpeg` dans
  `packages/verifier/Dockerfile:29-31`. Delta Dockerfile **proposé** (intégration-owned : la
  dépendance apt est déclarée dans le rapport, l'orchestrateur l'applique — cf.
  parallelization-design §4 ; mais le Dockerfile verifier n'est PAS dans la liste intégration-owned,
  donc l'agent **peut** l'éditer directement — à confirmer dans le brief, cf. §12). Dépendance PyPI
  `pyseccomp` ajoutée à `packages/verifier/pyproject.toml:6-12` — **déclarée, pas lockée** par
  l'agent (`uv.lock` est intégration-owned, parallelization-design §4).

**Option B — `prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, …)` brut via `ctypes` (REJETÉE).** Zéro
dépendance Python *et* zéro dépendance image (`libc` suffit). Mais il faut **assembler le programme
BPF à la main** : construire le tableau `sock_filter[]` (chargement de `seccomp_data.nr` et
`.arch`, comparaisons, sauts relatifs), gérer **les numéros de syscall par architecture** (amd64 ≠
arm64 — `socket`=41 sur amd64, n'existe pas pareil sur arm64), poser `PR_SET_NO_NEW_PRIVS` puis
`PR_SET_SECCOMP`. C'est **fastidieux, fragile et non portable arm64/amd64 sans table de syscalls
maison** — exactement le genre de code BPF bas-niveau qu'on ne veut pas maintenir ni tester. Le gain
(« zéro lib ») ne justifie pas le risque d'un filtre BPF erroné (un mauvais saut = filtre qui ne
filtre rien, *ou* qui tue Python).

**Verdict** : **`pyseccomp`** — la maintenabilité et le support multi-arch priment ; le coût image
(`libseccomp2`, un apt léger) est négligeable face à `ffmpeg` déjà présent. La dépendance reste
**confinée au seul paquet verifier** (le crawler n'embarque rien — frontière de paquet préservée).

## 7. Où installer le filtre : dans `analysis_child.main`, pas dans `preexec_fn`

Deux emplacements possibles ; **on tranche pour (a) — dans l'enfant, juste avant `pipeline.run`**.

**(a) Dans `analysis_child.main`, juste avant `pipeline.run` (RETENUE).** L'enfant a déjà **fini
tous ses imports** (`json`, `re`, `pathlib`, `puremagic` via `pipeline`, etc. — `analysis_child.py`
+ `pipeline.py` + `checks/*`) et déjà **lu l'en-tête RO** (`analysis_child.py:42`). On installe le
filtre **à ce moment précis**, juste avant d'entrer dans `pipeline.run` (`analysis_child.py:47`) :

```python
    try:
        with path.open("rb") as handle:
            header = handle.read(config.header_bytes)
    except OSError:
        _emit("suspicious", {}, [])
        return 0
    confiner()                                  # ← RING NOYAU : pose le filtre seccomp ICI
    verdict, real_meta, checks = pipeline.run(header, path, runner, config)
```

- **Pourquoi le plus sûr** : tout le code Python qui a besoin de syscalls « riches » (imports, lib
  loading, ouverture du fichier) **a déjà tourné**. Le filtre ne couvre donc que `pipeline.run` —
  qui n'a **aucune raison légitime** d'ouvrir un socket. C'est aussi le moment où ffprobe/clamscan
  vont être fork/exec → ils **héritent** du filtre (fork/exec sous `no_new_privs` conserve le
  filtre seccomp — garanti noyau). On droppe le réseau exactement quand on entre dans la zone de
  traitement du contenu hostile.
- **Pourquoi pas avant les imports** : poser le filtre trop tôt risquerait de bloquer un syscall
  d'import paresseux (`dlopen` d'une extension C de puremagic, `getrandom` au chargement, etc.). En
  l'installant **après** la lecture de l'en-tête, on garantit que tout le bootstrap est passé.
- Le `Confiner` est **injecté dans `main`** comme `ffprobe_runner` l'est déjà (`analysis_child.py:29-37`) :
  paramètre optionnel `confiner: Confiner | None = None`, défaut prod = `ProdConfiner()`, défaut
  désactivé = `NoopConfiner()` selon la config (§8). Les tests passent un stub.

**(b) Dans le `preexec_fn` parent (`_confine`, `spawn.py:70`) — REJETÉE.** Le `preexec_fn` tourne
**dans le fork du parent, entre `fork()` et `exec()`** — un contexte **extrêmement contraint** (un
seul thread, pas de réimport, async-signal-safety requise). Y poser un filtre seccomp avant
l'`exec` de l'interpréteur enfant signifie que **l'exec lui-même + tout le bootstrap Python de
l'enfant** tournent déjà sous le filtre → on revient au risque de tuer le démarrage de Python qu'on
veut éviter (et un import C qui `mmap`/`openat`/`getrandom` une lib pourrait être bloqué selon
l'élargissement futur de la liste). De plus, `pyseccomp` (ctypes + dlopen de `libseccomp.so`) dans
un `preexec_fn` post-fork est **fragile** (état d'allocation/threads hérité du parent multi-thread
uvicorn — cf. `analysis-design.md` §12 « `preexec_fn` n'est pas thread-safe »). **(a) évite tout
cela** en posant le filtre **dans l'enfant, après l'exec et après les imports**, dans un process
mono-thread propre.

**Conséquence** : `spawn.py` / `_confine` **ne changent pas** pour le ring noyau (ils gardent
setsid + rlimits). Le seul fichier *enfant* modifié est `analysis_child.py` (+ le nouveau
`confine.py`). C'est aussi plus propre vis-à-vis de l'overlap worktree : la tâche clamav touche
`pipeline.py`/`config.py`/`spawn.py` (nouveau runner) ; le ring touche surtout `analysis_child.py`
+ `confine.py` + `config.py` (un flag) → friction minimale dans WT-verifier.

## 8. Config — un flag d'activation (défaut ON en prod, overridable)

`AnalysisConfig` (`config.py:18`) reçoit **un champ** `seccomp_enabled: bool`, lu de l'env par
`from_env` (`config.py:34`), **comme les autres** :

```python
    seccomp_enabled: bool
    ...
    seccomp_enabled=_parse_bool(env.get("SECCOMP_ENABLED"), True),   # défaut ON
```

- **Défaut `True`** : en prod (conteneur, `no_new_privs` posé) le ring s'installe sans config.
- **Overridable `SECCOMP_ENABLED=0`** : indispensable pour le **dev/CI bare-metal** où
  `no_new_privs` n'est pas posé et/ou `libseccomp` est absent → on désactive (sinon échec, cf. §10).
  Même logique que `RLIMIT_NPROC=4096` que l'intégration force déjà hors-CI
  (`test_analysis_integration.py:33-44`).
- Nécessite un petit `_parse_bool(raw, default)` dans `config.py` (miroir de `_parse_int`/`_parse_float`,
  `config.py:64-79`) : `None → default` ; `"0"/"false"/"no" → False` ; `"1"/"true"/"yes" → True` ;
  autre → `ValueError` (fail-fast cohérent §8 analysis-design). **Les deux branches** (défaut + parse)
  sont testées.
- `_minimal_env` (`spawn.py:98`) **doit propager** `SECCOMP_ENABLED` à l'enfant (comme
  `ENABLED_CHECKS`/`HEADER_BYTES`) → ajouter `"SECCOMP_ENABLED": _bool_str(cfg.seccomp_enabled)` au
  dict et à l'assertion `set(...)` du test (`test_spawn.py:102-109`). **C'est le seul point qui
  force aussi une retouche `spawn.py`** (une entrée de dict), sans toucher `_confine`.

**Sélection du `Confiner` dans `analysis_child.main`** : `confiner = ProdConfiner() if
config.seccomp_enabled else NoopConfiner()` (quand aucun n'est injecté). Les deux branches sont
couvrables en unit (injection) — cf. §9.

## 9. Plan de tests TDD (100 % branch, seccomp réel = Geoffrey)

**Règle d'or (CLAUDE.md)** : test qui échoue d'abord, puis impl minimale. Tout le système
(`ProdConfiner.__call__`, l'`import pyseccomp`) est `# pragma: no cover` ; le réel est exercé par
`analysis_integration` (Geoffrey). Les branches sont couvertes par **injection d'un `Confiner`
stub**.

**`tests/test_confine.py`** (nouveau) :
- `test_noop_confiner_does_nothing` : `NoopConfiner()()` retourne `None` sans lever (couvre la
  vraie ligne `return None`).
- `test_prod_confiner_constructs` : `isinstance(ProdConfiner(), ProdConfiner)` — le **constructeur**
  n'est pas pragma ; `__call__` (vrai seccomp) l'est (miroir `test_spawn.py:119-121`
  `test_prod_child_runner_constructs`).

**`tests/test_analysis_child.py`** (étendre l'existant) — un `_RecordingConfiner` espion :
- `test_confiner_is_called_before_pipeline_run` : injecter un `_RecordingConfiner` + un
  `ffprobe_runner` stub qui, **quand il est appelé**, asserte que le confiner a déjà été invoqué →
  prouve l'**ordre** (confiner AVANT `pipeline.run`). C'est la branche critique de §7.
- `test_confiner_not_called_when_file_missing` : fichier absent → égress `suspicious` **sans**
  appeler le confiner (la branche `except OSError` de `analysis_child.py:44` retourne avant le
  point d'installation) → le `_RecordingConfiner` n'a pas été appelé. (Couvre que l'install est
  bien **après** la lecture d'en-tête, pas avant.)
- `test_seccomp_enabled_selects_prod_confiner` / `test_seccomp_disabled_selects_noop` : appeler
  `main` **sans** `confiner` injecté, avec `cfg.seccomp_enabled` True puis False, et vérifier le
  type sélectionné. *Astuce coverage* : pour ne PAS exécuter le vrai `ProdConfiner.__call__` (qui
  poserait un vrai filtre dans le process de test !), tester la **sélection** via un point
  d'injection dédié — p. ex. `main` construit le confiner par défaut via un petit factory
  `_default_confiner(config)` qu'on peut soit tester en isolation (il retourne le bon **type** sans
  l'appeler), soit monkeypatch. **Figer** : `_default_confiner(config) -> Confiner` retourne
  l'instance (sans l'appeler) ; `main` l'appelle ensuite. Le test asserte le **type retourné** par
  `_default_confiner` (les deux branches `seccomp_enabled`), jamais son `__call__`.

**`tests/test_config.py`** (étendre) :
- `test_seccomp_enabled_defaults_true` (env sans `SECCOMP_ENABLED`) ;
- `test_seccomp_enabled_parsed_false` (`"0"`/`"false"`) et `_true` (`"1"`/`"true"`) ;
- `test_seccomp_enabled_invalid_raises` (`"maybe"` → `ValueError`).
  Couvre **les deux branches** de `_parse_bool`.

**`tests/test_spawn.py`** (étendre) :
- mettre à jour `test_minimal_env_contains_only_whitelisted_vars` : `SECCOMP_ENABLED` présent dans
  `runner.env` + ajouté au `set(...)` attendu (`test_spawn.py:102-109`).

**Intégration (`analysis_integration`, déselectionné, exclu de coverage — Geoffrey)** : étendre
`test_analysis_integration.py`. Le réel seccomp ne tourne **que** là (le sandbox de Claude n'a ni
`no_new_privs` ni forcément `libseccomp` — cf. mémoire « tests d'intégration = vrai shell »). Cas :
- `test_real_seccomp_blocks_socket` : un enfant **réel** (`ProdConfiner`) qui tenterait
  `socket()` (via un faux check de test, ou en vérifiant que ffprobe tourne quand même clean →
  preuve que le filtre n'a pas cassé l'analyse légitime). **Cas minimal figé** : prouver qu'un
  média sain reste `clean` **avec `SECCOMP_ENABLED=1`** (le filtre ne casse RIEN de légitime) — la
  preuve « socket bloqué » est plus délicate à fabriquer sans un binaire de test dédié ; on la
  documente comme cas à étoffer mais le **filet minimal est « clean préservé sous filtre réel »**.
- `_cfg` de l'intégration force déjà `RLIMIT_NPROC=4096` ; ajouter `SECCOMP_ENABLED=1` explicitement
  (il faut `no_new_privs` — donc ce test **skip** proprement si `no_new_privs` n'est pas posable,
  comme `_NEEDS_FFMPEG` skip si ffprobe absent : ajouter un `_NEEDS_SECCOMP` skipif qui teste la
  faisabilité, cf. §10).

## 10. Gestion d'échec (fail-open documenté, jamais un faux malicious)

`ProdConfiner.__call__` peut échouer **légitimement** :
- `no_new_privs` non posé (dev/CI hors conteneur) → `load()` lève (EACCES/EPERM) ;
- `libseccomp.so` absent → `import pyseccomp`/`load()` lève ;
- noyau sans seccomp (improbable, mais bare-metal exotique).

**Décision figée : fail-open contrôlé, jamais fail-malicious.** Un échec d'installation du ring
**ne doit pas** transformer un média sain en `suspicious`/`malicious`. Deux niveaux :
1. En prod (conteneur) le ring **doit** s'installer ; un échec y est anormal. Mais comme c'est une
   couche *defense-in-depth* (pas la seule barrière — §2), on **ne casse pas l'analyse** : on log
   l'échec et on continue **sans** filtre (les autres rings tiennent : `internal: true`, RO,
   rlimits, `cap_drop`).
2. En dev/CI, `SECCOMP_ENABLED=0` désactive proprement (`NoopConfiner`) → aucun échec.

**Implémentation** : `ProdConfiner.__call__` enveloppe l'install dans un `try/except OSError`
(+ `ImportError` si `pyseccomp` indispo) qui **log un warning et retourne** (pragma — non couvert
en unit, observé en intégration). **Alternative figée à confirmer §12** : laisser l'exception
remonter ferait crasher l'enfant → égress vide → `suspicious` (faux positif) → **NON**. Donc
**fail-open avec log**. Le `_NEEDS_SECCOMP` skipif de l'intégration détecte la faisabilité en
amont pour éviter de tester un fail-open silencieux.

## 11. Compat gVisor (`runtime: runsc`)

Cas-limite à documenter (interaction filtre seccomp applicatif ↔ gVisor opt-in,
`compose.hardening.yml`) :
- gVisor **interpose son propre noyau** (sentry) entre l'application et le noyau hôte. Il
  **supporte les filtres seccomp applicatifs** : `runsc` implémente `prctl(PR_SET_SECCOMP)` /
  `seccomp()` et applique le filtre au sein de la sentry (les syscalls de l'app sont d'abord vus
  par notre filtre, puis par la sentry). **Donc empiler notre seccomp SOUS gVisor est cohérent** :
  notre blocklist s'applique, gVisor ajoute sa propre médiation. **À confirmer** (§12) que la
  version de gVisor visée applique bien le filtre applicatif sans le rejeter (certaines versions
  anciennes avaient un support partiel de `seccomp()`/des actions).
- **Aucune action requise** : `compose.hardening.yml` reste inchangé (`runsc` sur crawler+verifier).
  Le ring seccomp est **orthogonal** et toujours-ON par défaut ; gVisor reste opt-in par-dessus.
- **Risque inverse** : si une version de gVisor **n'applique pas** le filtre seccomp applicatif (le
  no-op silencieusement), on **ne perd rien** par rapport à l'état actuel (gVisor médie déjà les
  syscalls) — donc dégradation gracieuse, pas de régression.

## 12. Risques / à confirmer

- **`no_new_privs` en intégration locale.** Le test `analysis_integration` réel exige
  `no_new_privs` posable. Hors conteneur, un process **peut** poser `PR_SET_NO_NEW_PRIVS` sur
  lui-même (c'est permis sans privilège) — donc `ProdConfiner` peut le poser avant le filtre. **À
  confirmer** : `pyseccomp.SyscallFilter.load()` pose-t-il `no_new_privs` lui-même, ou faut-il un
  `prctl(PR_SET_NO_NEW_PRIVS, 1)` explicite (via `pyseccomp` ou `ctypes`) **avant** `load()` quand
  on n'est pas dans le conteneur ? À vérifier au plan via context7/doc pyseccomp + un essai dans le
  shell de Geoffrey. (En prod le conteneur l'a déjà posé, donc le doute ne concerne que
  l'intégration locale.)
- **Liste de syscalls vs faux positifs réels.** La liste §4 est conçue « zéro faux positif sur
  média sain », mais ffprobe/clamscan peuvent émettre un `socket` inattendu (résolution
  `/etc/nsswitch.conf`, getaddrinfo paresseux). `ERRNO(EPERM)` (au lieu de `KILL`) **absorbe** ce
  cas (l'appelant gère l'échec) — mais **à confirmer** par l'intégration que ffprobe reste `clean`
  sous filtre sur un échantillon réel. Si un faux `suspicious` apparaît, retirer le syscall fautif
  de la liste (la liste est volontairement conservatrice et révisable — sans re-design).
- **clamscan ↔ socket.** clamscan **local** (binaire, scan d'un chemin) ne parle pas à `clamd` et
  n'ouvre pas de socket. **À confirmer** quand la tâche clamav atterrit (séquencée AVANT le ring
  dans WT-verifier) : si la tâche clamav choisit le **daemon** `clamd` (socket Unix !), deny
  `socket` casserait clamdscan → il faudrait soit autoriser `socket` famille `AF_UNIX` (seccomp ne
  filtre pas trivialement par argument de socket — c'est faisable via `add_rule` avec un `Arg` mais
  fragile), soit s'en tenir au `clamscan` standalone (recommandé). **Dépendance de design inter-tâches
  à signaler dans le brief WT-verifier** : clamav doit rester en mode binaire local pour cohabiter
  avec la blocklist `socket`.
- **`libseccomp2` dans l'image + édition du Dockerfile.** La parallelization-design §4 liste
  `compose.*` et `uv.lock`/`pyproject` comme intégration-owned, mais **pas** `packages/verifier/Dockerfile`
  explicitement. **À confirmer dans le brief** : l'agent WT-verifier édite-t-il directement le
  Dockerfile (ajout `libseccomp2` à `apt-get install`, `Dockerfile:29-31`) ou propose-t-il un delta ?
  Recommandation : **delta proposé** (cohérent avec « toute dépendance déclarée, pas appliquée par
  l'agent »), l'orchestrateur applique. La dépendance PyPI `pyseccomp` est, elle, **déclarée** dans
  `pyproject.toml` (non lockée par l'agent).
- **gVisor applique-t-il notre filtre ?** §11 — à confirmer sur la version gVisor visée (support
  `seccomp()` applicatif complet). Dégradation gracieuse si non (pas de régression).
- **Fail-open vs fail-closed.** §10 fige le **fail-open avec log** (un ring qui ne s'installe pas
  ne doit pas produire de faux `suspicious`). C'est un choix de sécurité **assumé** : on privilégie
  l'exactitude du verdict (pas de faux positif) sur la garantie d'installation, *parce que* seccomp
  est une couche parmi d'autres. À acter explicitement avec Geoffrey si une politique « fail-closed
  en prod » (refuser de tourner sans ring) est préférée — **par défaut figé : fail-open + warning**.
- **Multi-arch (amd64/arm64).** `pyseccomp`/libseccomp résolvent les noms de syscalls par arch →
  `socketcall` (i386-only) lève `OSError` sur amd64/arm64 → on `pass`. C'est géré, mais **à
  confirmer** que toute la liste §4 existe bien sur arm64 (les entrées modernes `userfaultfd`/`bpf`/
  `process_vm_*` y sont ; le `try/except OSError` autour de chaque `add_rule` est le filet).

## 13. Part de Geoffrey (réseau/seccomp vivant — ce que le sandbox ne peut pas)

- Lancer `( cd packages/verifier && uv run pytest -m analysis_integration --no-cov )` avec
  `SECCOMP_ENABLED=1` et `libseccomp` installé → prouver qu'un média sain reste **`clean` sous
  filtre réel** (le filtre ne casse rien de légitime), et idéalement qu'un essai de `socket` est
  bien `EPERM`/que `ptrace` tue.
- Confirmer le comportement `no_new_privs` local (le `ProdConfiner` pose-t-il `PR_SET_NO_NEW_PRIVS`
  lui-même hors conteneur, cf. §12) — le sandbox Claude n'a pas de veth ni forcément les prctl
  nécessaires.
- Valider l'image : `libseccomp2` présent dans le runtime verifier, `import pyseccomp` OK, filtre
  posé au boot d'un vrai `/verify` (log « seccomp filter installed »).
- Confirmer la **cohabitation clamav** : que la tâche clamav (atterrie avant) utilise bien
  `clamscan` standalone (pas `clamd`/socket) → pas de conflit avec deny `socket` (§12).
- Valider sous gVisor opt-in (`docker compose -f compose.yaml -f compose.hardening.yml --profile full
  up`) que le filtre applicatif est accepté par `runsc` (§11).
