# Complétion d'un download côté amuled — où, sous quel nom, quand (2026-06-17)

> Confirme **DV10 / R6** par lecture de la source amont d'aMule (au lieu d'un transfert
> synthétique, abandonné — cf. [guide des tests](../testing-guide.md)). Répond à trois questions :
> **Q1** où atterrit le fichier fini, **Q2** sous quel nom, **Q3** quand EC le signale complet.
> Source : `amule-project/amule@5938915f10e6f2e011f87df90261feaf606136d6` (branche `master`,
> date auteur 2026-06-10). Tous les permaliens pointent sur ce SHA.

---

## Convention de fiabilité

- **SOURCE** — fait établi par lecture des sources C++ d'aMule au commit ci-dessus.
- **NÔTRE** — conséquence pour notre code (`composition/app.py`, `application/run_download_cycle.py`,
  `ports/mule_download_client.py`).

---

## Verdict en une ligne

**À la complétion, amuled déplace le fichier du *TempDir* vers l'*IncomingDir* (clé
`/eMule/IncomingDir`), en l'assainissant (`CPath::Cleanup`) puis en le dédupliquant par suffixe
`nom(0).ext` en cas de collision ; le statut EC ne passe `PS_COMPLETE`(9) qu'APRÈS le déplacement
(`PS_COMPLETING`(8) pendant).** Nos hypothèses DV10 sont **confirmées**. **L'angle mort initial — la
dédup de nom (`nom(0)`) qui cassait un nom deviné — est désormais RÉSOLU** : le crawler détecte la
complétion via la liste des fichiers partagés EC et promeut au **vrai nom on-disk** rapporté par
amuled (voir « Détection côté crawler » plus bas).

---

## Q1 — Où atterrit le fichier fini ? (SOURCE)

La complétion réelle tourne dans un thread worker `CCompletionTask::Entry()`
([`src/ThreadTasks.cpp:539`](https://github.com/amule-project/amule/blob/5938915f10e6f2e011f87df90261feaf606136d6/src/ThreadTasks.cpp#L539-L607)),
lancé par `CPartFile::CompleteFile(true)` → `PerformFileComplete()` → `CThreadScheduler::AddTask`
([`src/PartFile.cpp:2335`](https://github.com/amule-project/amule/blob/5938915f10e6f2e011f87df90261feaf606136d6/src/PartFile.cpp#L2335-L2348)).

- **Destination** : pour un download **sans catégorie** (cas par défaut, catégorie 0, dont
  `Category_Struct::path` reste vide → `DirExists()` faux), fallback systématique sur
  `thePrefs::GetIncomingDir()` (clé `/eMule/IncomingDir`, `src/Preferences.cpp:1121`). Le *TempDir*
  (`/eMule/TempDir`) est une préférence **distincte** → potentiellement un autre FS.
- **Déplacement vs copie** (`src/ThreadTasks.cpp:580-591`) : `CPath::RenameFile` si même partition,
  sinon `CPath::CloneFile` + suppression de l'original. La source est le `.part` dans le *TempDir*.

**NÔTRE** : `staging_dir` doit pointer l'**IncomingDir** d'amuled (pas le Temp). Le déploiement
fait `staging_dir = quarantine_dir = /data/quarantine` (le même volume), donc notre `os.replace`
est un rename intra-FS `nom → <hash>` dans ce dossier. amuled's `IncomingDir` doit donc être
configuré = ce même `/data/quarantine`. **Ne pas créer de catégories** (une catégorie avec son
propre `path` redirigerait le fichier ailleurs).

---

## Q2 — Sous quel nom ? (SOURCE — le point sensible)

Le nom de destination n'est **pas** le nom partagé brut. amuled applique :

1. **Assainissement** `m_filename.Cleanup(true, !CanFSHandleSpecialChars(targetPath))`
   ([`src/ThreadTasks.cpp:557`](https://github.com/amule-project/amule/blob/5938915f10e6f2e011f87df90261feaf606136d6/src/ThreadTasks.cpp#L557)),
   impl. `DoCleanup`
   ([`src/libs/common/Path.cpp:112`](https://github.com/amule-project/amule/blob/5938915f10e6f2e011f87df90261feaf606136d6/src/libs/common/Path.cpp#L112-L147)) :
   - `'/'` et les caractères de contrôle `< 32` → **toujours supprimés** ;
   - `" * < > ? | \ :` → supprimés **uniquement si FAT32/NTFS** ;
   - espaces conservés ; pas de troncature, pas de lowercasing.
   - `CanFSHandleSpecialChars`
     ([`src/PlatformSpecific.cpp:188`](https://github.com/amule-project/amule/blob/5938915f10e6f2e011f87df90261feaf606136d6/src/PlatformSpecific.cpp#L188-L230))
     rend `true` (→ pas de FAT32, on conserve les caractères spéciaux) pour **ext4/btrfs/xfs/overlay/
     tmpfs** et tout type inconnu ; `false` pour ntfs/vfat/fat*/hfs.
   - **Sur un FS Linux normal (notre conteneur), Cleanup ne retire en pratique que `/` et les
     contrôles `<32` → identité.** Notre `Path(observation.filename).name` matche.
2. **Dédup par collision** (`src/ThreadTasks.cpp:568-578`) : si un fichier du même nom existe déjà
   dans l'Incoming, amuled insère un suffixe **avant l'extension**, compteur **démarrant à 0** :
   `episode.avi` → `episode(0).avi`, puis `(1)`, `(2)`…

**NÔTRE — RÉSOLU par la détection via les partagés.** On ne **devine** plus le nom : `_handle_completions`
(`application/run_download_cycle.py`) lit `client.shared_files()` et promeut au **vrai nom on-disk**
(`EC_TAG_PARTFILE_NAME` = `GetFileName`, donc post-cleanup ET post-dédup `nom(0).ext`) →
`staging_dir / <vrai nom>`. La collision est donc gérée par construction. Le confinement
anti-traversal sur ce nom (entrée externe) vit dans `_safe_basename`. Voir « Détection côté crawler ».

---

## Q3 — Quand EC signale-t-il « complété » ? (SOURCE)

Le statut reste `PS_COMPLETING`(8) pendant **tout** le déplacement ; il ne passe `PS_COMPLETE`(9)
qu'après, dans `CompleteFileEnded`
([`src/PartFile.cpp:2276`](https://github.com/amule-project/amule/blob/5938915f10e6f2e011f87df90261feaf606136d6/src/PartFile.cpp#L2276-L2332)),
une fois `m_fullname` pointé sur le chemin Incoming final (sinon `PS_ERROR`(4)). EC expose la valeur
brute via `EC_TAG_PARTFILE_STATUS`
([`src/ECSpecialCoreTags.cpp:162`](https://github.com/amule-project/amule/blob/5938915f10e6f2e011f87df90261feaf606136d6/src/ECSpecialCoreTags.cpp#L158-L162) ;
`PS_COMPLETING=8`, `PS_COMPLETE=9`, `src/Constants.h:96`). **Voir `PS_COMPLETE`(9) garantit donc le
fichier présent et complet à son chemin final** — pas de race.

**NÔTRE** : on n'utilise PAS le tag de statut `PS_COMPLETE` (il est **inobservable** via la file de
download — l'entrée quitte `m_filelist` au moment exact où elle passe à 9, et la file EC n'inclut pas
`m_completedDownloads`). On déclenche la complétion par la **présence dans les fichiers partagés** (le
fichier fini est auto-partagé par `SafeAddKFile` dans `CompleteFileEnded`, donc déjà déplacé et en
place quand on le voit). Pas de race : voir le fichier partagé garantit qu'il est complet à son chemin
final. Plus de byte-based, plus de `PromotionFailed` transitoire, plus de contrainte « TempDir et
Incoming sur le même FS ».

---

## Détection côté crawler (IMPLÉMENTÉ — `EC_OP_GET_SHARED_FILES`)

Conséquence des trois faits ci-dessus, la boucle de download détecte la complétion par un **signal
positif** (cf. design `docs/superpowers/specs/2026-06-17-completion-via-shared-files-design.md`) :

- **`AmuleEcClient.shared_files()`** émet `EC_OP_GET_SHARED_FILES` (0x10) au détail CMD, décode la
  réponse `EC_OP_SHARED_FILES` (0x22 ; N enfants `EC_TAG_KNOWNFILE` 0x0400) → `SharedFileEntry(hash, name)`.
- **`_handle_completions`** : un hash suivi non-terminal présent dans les partagés = complétion →
  `set_state(completed)` → `quarantine.promote(staging_dir / _safe_basename(name), hash)` →
  `enqueue_verification` → `quarantined`. Idempotent : `promote` échoue → reste `completed`, retry
  (le hash reste partagé). `_monitor` (file de download) ne fait plus que `QUEUED→DOWNLOADING`.
- Le `EC_TAG_KNOWNFILE_FILENAME` (chemin complet) est **ignoré** (namespace de montage d'amuled,
  potentiellement ≠ du nôtre) : on prend le **basename** (`EC_TAG_PARTFILE_NAME`) + notre `staging_dir`.

---

## Contraintes de déploiement (résumé)

1. `staging_dir` = `quarantine_dir` = l'**IncomingDir** d'amuled (même volume `/data/quarantine`).
2. Ce volume sur un **FS Linux normal** (ext4/overlay…) — pas vfat/NTFS/HFS (sinon le cleanup
   diverge sur les caractères spéciaux du nom).
3. **Pas de catégories** amuled (sinon la destination change).
4. amuled **dédié** au crawler, **jeu partagé restreint** : `shared_files()` est interrogé à chaque
   cycle ; ne pas pointer une grosse bibliothèque partagée pré-existante (la liste reste petite car on
   sort les fichiers de l'Incoming à chaque cycle). La contrainte « même FS Temp/Incoming » n'est PLUS
   nécessaire (on n'agit qu'une fois le fichier partagé, donc déjà déplacé).
