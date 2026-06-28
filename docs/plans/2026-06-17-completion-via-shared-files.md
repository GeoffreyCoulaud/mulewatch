# Détection de complétion par les fichiers partagés EC — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remplacer la détection de complétion byte-based par un signal positif — la présence du fichier dans la liste des fichiers partagés EC (`EC_OP_GET_SHARED_FILES`) — en promouvant avec le **vrai nom on-disk** rapporté par amuled.

**Architecture:** Nouvelle surface EC `shared_files()` côté adaptateur `mule_ec` (DTO `SharedFileEntry`, mapping tolérant aux inconnus). La boucle download (`run_download_cycle`) détecte la complétion via les partagés au lieu des octets ; `_monitor` ne fait plus que `QUEUED→DOWNLOADING`. `resolve_staging_path` (devine-nom catalogue) est supprimé ; le nom vient désormais d'amuled, confiné anti-traversal. Le 2-temps `COMPLETED→QUARANTINED` est conservé (stampe `completed_at`).

**Tech Stack:** Python 3.12, asyncio, hexagonal (ports/adapters), pytest (100 % branch), mypy --strict, ruff. Spec : `docs/superpowers/specs/2026-06-17-completion-via-shared-files-design.md`.

---

## Structure des fichiers

| Fichier | Responsabilité | Action |
|---|---|---|
| `ports/mule_download_client.py` | DTO `SharedFileEntry` + méthode port `shared_files()` | Modifier |
| `adapters/mule_ec/codes.py` | constantes `EC_OP_GET_SHARED_FILES`/`EC_OP_SHARED_FILES`/`EC_TAG_KNOWNFILE` | Modifier |
| `adapters/mule_ec/client.py` | `_map_shared_file` + `AmuleEcClient.shared_files()` | Modifier |
| `application/run_download_cycle.py` | détection via partagés ; `_safe_basename` ; deps `staging_dir` | Modifier |
| `composition/app.py` | câblage `staging_dir` ; suppression `resolve_staging_path` | Modifier |
| tests associés (`tests/ports`, `tests/adapters/mule_ec`, `tests/application`, `tests/composition`, `tests/integration`) | TDD | Modifier / Supprimer |
| docs (reference + runbook + CLAUDE.md) | refléter le nouveau mécanisme | Modifier |

Convention de gate (à exécuter à chaque commit) : `( cd packages/crawler && uv run pytest -q )` + `uv run ruff check .` + `uv run ruff format --check .` + `uv run mypy` (depuis la racine). Les tests d'intégration (`-m download_integration`) ne tournent PAS dans le gate par défaut.

---

### Task 1 : DTO `SharedFileEntry` (SEUL — pas de changement de Protocol)

> **Séquençage (important) :** ajouter `shared_files` au Protocol `MuleDownloadClient` casse mypy pour TOUS ses implémenteurs (l'adapter réel + tous les fakes typés `MuleDownloadClient`). Un commit gate-vert ne peut donc PAS élargir le Protocol seul. Task 1 n'ajoute que le **DTO** (n'élargit rien) ; l'ajout de la méthode au Protocol + sa présence sur l'adapter et tous les fakes est **atomique en Task 3**.

**Files:**
- Modify: `packages/crawler/src/emule_indexer/ports/mule_download_client.py`
- Test: `packages/crawler/tests/ports/test_mule_download_client.py`

- [ ] **Step 1 : Tests qui échouent**

Ajouter dans `tests/ports/test_mule_download_client.py` (imports : ajouter `SharedFileEntry` à l'import depuis `emule_indexer.ports.mule_download_client`, et `from dataclasses import FrozenInstanceError`) :

```python
def test_shared_file_entry_carries_hash_and_real_name() -> None:
    entry = SharedFileEntry(ed2k_hash="a" * 32, name="Keroro 62a.avi")
    assert entry.ed2k_hash == "a" * 32
    assert entry.name == "Keroro 62a.avi"


def test_shared_file_entry_is_frozen() -> None:
    entry = SharedFileEntry(ed2k_hash="a" * 32, name="x.avi")
    with pytest.raises(FrozenInstanceError):
        entry.name = "y.avi"  # type: ignore[misc]
```

NE PAS toucher au Protocol ni à `_StubDownloadClient` (le Protocol reste inchangé en Task 1).

- [ ] **Step 2 : Lancer, voir échouer**

Run: `( cd packages/crawler && uv run pytest tests/ports/test_mule_download_client.py --no-cov -q )`
Expected: FAIL (ImportError : `SharedFileEntry` n'existe pas).

- [ ] **Step 3 : Implémentation minimale**

Dans `ports/mule_download_client.py`, ajouter le DTO après `DownloadEntry` (et RIEN d'autre — pas de méthode au Protocol) :

```python
@dataclass(frozen=True)
class SharedFileEntry:
    """Une entrée de la liste des fichiers PARTAGÉS d'amuled (réponse ``EC_OP_SHARED_FILES``).

    Un fichier téléchargé est auto-partagé par amuled à la complétion (signal POSITIF de
    complétion, cf. design 2026-06-17). ``name`` est le VRAI nom on-disk (``GetFileName`` côté
    amuled, post-cleanup ET post-dédup ``nom(0).ext``) ; ``ed2k_hash`` (hex minuscule 32) sert à
    matcher un download suivi. AUCUN octet n'est lu (métadonnée EC seule, spec §4).
    """

    ed2k_hash: str
    name: str
```

- [ ] **Step 4 : Gate complet, voir passer**

Run: `( cd packages/crawler && uv run pytest -q )` puis racine `uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: PASS, 100 % branch (le Protocol n'étant pas élargi, mypy reste vert).

- [ ] **Step 5 : Commit**

```bash
git add packages/crawler/src/emule_indexer/ports/mule_download_client.py packages/crawler/tests/ports/test_mule_download_client.py
git commit -m "feat(download): DTO SharedFileEntry (entrée de la liste des fichiers partagés)"
```

---

### Task 2 : constantes EC + mapping `_map_shared_file`

**Files:**
- Modify: `packages/crawler/src/emule_indexer/adapters/mule_ec/codes.py`
- Modify: `packages/crawler/src/emule_indexer/adapters/mule_ec/client.py`
- Test: `packages/crawler/tests/adapters/mule_ec/test_client_download.py`

- [ ] **Step 1 : Test qui échoue**

Dans `tests/adapters/mule_ec/test_client_download.py`, ajouter l'import `SharedFileEntry` (depuis `emule_indexer.ports.mule_download_client`) et un helper + tests :

```python
def _knownfile_entry(hash_hex: str, name: str) -> EcTag:
    # Conteneur EC_TAG_KNOWNFILE (0x0400) ; valeur propre = ECID (UINT, ignoré). Le hash est
    # l'enfant EC_TAG_PARTFILE_HASH (HASH16), le nom l'enfant EC_TAG_PARTFILE_NAME (vrai nom
    # on-disk côté amuled). Mêmes tags enfants que le partfile (confirmé amont, commit 5938915).
    return EcTag(
        codes.EC_TAG_KNOWNFILE,
        codes.EC_TAGTYPE_UINT8,
        bytes([1]),
        (
            EcTag(codes.EC_TAG_PARTFILE_HASH, codes.EC_TAGTYPE_HASH16, bytes.fromhex(hash_hex), ()),
            string_tag(codes.EC_TAG_PARTFILE_NAME, name),
        ),
    )


def test_map_shared_file_extracts_hash_and_name() -> None:
    from emule_indexer.adapters.mule_ec.client import _map_shared_file

    entry = _map_shared_file(_knownfile_entry(_HASH, "Keroro 62a.avi"))
    assert entry == SharedFileEntry(ed2k_hash=_HASH, name="Keroro 62a.avi")


def test_map_shared_file_without_hash_is_none() -> None:
    from emule_indexer.adapters.mule_ec.client import _map_shared_file

    no_hash = EcTag(
        codes.EC_TAG_KNOWNFILE,
        codes.EC_TAGTYPE_UINT8,
        b"\x01",
        (string_tag(codes.EC_TAG_PARTFILE_NAME, "orpheline.avi"),),
    )
    assert _map_shared_file(no_hash) is None


def test_map_shared_file_without_name_is_none() -> None:
    from emule_indexer.adapters.mule_ec.client import _map_shared_file

    no_name = EcTag(
        codes.EC_TAG_KNOWNFILE,
        codes.EC_TAGTYPE_UINT8,
        b"\x01",
        (EcTag(codes.EC_TAG_PARTFILE_HASH, codes.EC_TAGTYPE_HASH16, bytes.fromhex(_HASH), ()),),
    )
    assert _map_shared_file(no_name) is None


def test_map_shared_file_with_wrong_length_hash_is_none() -> None:
    from emule_indexer.adapters.mule_ec.client import _map_shared_file

    bad = EcTag(
        codes.EC_TAG_KNOWNFILE,
        codes.EC_TAGTYPE_UINT8,
        b"\x01",
        (
            EcTag(codes.EC_TAG_PARTFILE_HASH, codes.EC_TAGTYPE_HASH16, b"\x00" * 8, ()),
            string_tag(codes.EC_TAG_PARTFILE_NAME, "x.avi"),
        ),
    )
    assert _map_shared_file(bad) is None
```

- [ ] **Step 2 : Lancer, voir échouer**

Run: `( cd packages/crawler && uv run pytest tests/adapters/mule_ec/test_client_download.py -k shared --no-cov -q )`
Expected: FAIL (`EC_TAG_KNOWNFILE` absent de `codes` / `_map_shared_file` introuvable).

- [ ] **Step 3 : Implémentation**

Dans `adapters/mule_ec/codes.py`, ajouter (près des opcodes download et des tags partfile) :

```python
EC_OP_GET_SHARED_FILES: Final[int] = 0x10  # requête de la liste des fichiers partagés (détail CMD)
EC_OP_SHARED_FILES: Final[int] = 0x22  # réponse : N enfants EC_TAG_KNOWNFILE
```

```python
EC_TAG_KNOWNFILE: Final[int] = 0x0400  # conteneur d'un fichier partagé/connu (réponse SHARED_FILES)
```

(`EC_TAG_PARTFILE_NAME` 0x0301 et `EC_TAG_PARTFILE_HASH` 0x031E existent déjà — réutilisés : le tag KNOWNFILE porte ces MÊMES enfants.)

Dans `adapters/mule_ec/client.py`, ajouter `SharedFileEntry` à l'import existant `from emule_indexer.ports.mule_download_client import ...`, puis ajouter après `_map_partfile` :

```python
def _map_shared_file(entry: EcTag) -> SharedFileEntry | None:
    """Une entrée ``EC_TAG_KNOWNFILE`` → ``SharedFileEntry``, ou ``None`` si inexploitable.

    Hash = enfant dédié ``EC_TAG_PARTFILE_HASH`` (HASH16, 16 octets) ; nom = enfant
    ``EC_TAG_PARTFILE_NAME`` (le VRAI nom on-disk, ``GetFileName`` côté amuled, post-cleanup/dédup).
    Sans hash exploitable OU sans nom → écartée (tolérance aux inconnus, comme ``_map_partfile``).
    """
    hash_tag = entry.find(codes.EC_TAG_PARTFILE_HASH)
    if (
        hash_tag is None
        or hash_tag.tag_type != codes.EC_TAGTYPE_HASH16
        or len(hash_tag.value) != 16
    ):
        return None
    name_tag = entry.find(codes.EC_TAG_PARTFILE_NAME)
    if name_tag is None:
        return None
    try:
        name = name_tag.string_value()
    except EcProtocolError:
        return None
    return SharedFileEntry(ed2k_hash=hash_tag.value.hex(), name=name)
```

(`EcProtocolError` est déjà importé dans `client.py` ; `string_value()` lève `EcProtocolError` sur octets non décodables — la branche `except` est exercée au Step suivant.)

- [ ] **Step 3b : Test de la branche `string_value` qui lève**

`EcTag.string_value()` (codec.py:53) lève `EcProtocolError` si le tag n'est pas de type STRING **ou** ne se termine pas par `\x00` (sinon il décode en `errors="replace"`, sans jamais lever). On déclenche donc la branche `except` avec un tag de nom STRING SANS NUL final :

```python
def test_map_shared_file_with_invalid_name_tag_is_none() -> None:
    from emule_indexer.adapters.mule_ec.client import _map_shared_file

    # name tag de type STRING mais SANS NUL terminal → string_value() lève EcProtocolError.
    bad_name = EcTag(codes.EC_TAG_PARTFILE_NAME, codes.EC_TAGTYPE_STRING, b"no-nul", ())
    entry = EcTag(
        codes.EC_TAG_KNOWNFILE,
        codes.EC_TAGTYPE_UINT8,
        b"\x01",
        (
            EcTag(codes.EC_TAG_PARTFILE_HASH, codes.EC_TAGTYPE_HASH16, bytes.fromhex(_HASH), ()),
            bad_name,
        ),
    )
    assert _map_shared_file(entry) is None
```

- [ ] **Step 4 : Lancer, voir passer**

Run: `( cd packages/crawler && uv run pytest tests/adapters/mule_ec/test_client_download.py -k shared --no-cov -q )`
Expected: PASS.

- [ ] **Step 5 : Commit**

```bash
git add packages/crawler/src/emule_indexer/adapters/mule_ec/codes.py packages/crawler/src/emule_indexer/adapters/mule_ec/client.py packages/crawler/tests/adapters/mule_ec/test_client_download.py
git commit -m "feat(ec): mapping _map_shared_file + opcodes/tag shared files"
```

---

### Task 3 : `AmuleEcClient.shared_files()` + élargissement ATOMIQUE du Protocol

> **Commit atomique gate-vert :** ce commit ajoute `shared_files` au Protocol `MuleDownloadClient`, l'implémente sur l'adapter réel `AmuleEcClient`, ET ajoute un `shared_files` minimal (`return ()`) à TOUS les fakes/stubs typés `MuleDownloadClient`, sinon mypy --strict casse (l'implémenteur de Task 1 a confirmé : 23 erreurs dans 6 fichiers). Les fakes renvoyant `()` sont inertes jusqu'à Task 4 (la prod n'appelle `shared_files` qu'en Task 4).

**Files:**
- Modify: `packages/crawler/src/emule_indexer/adapters/mule_ec/client.py` (méthode `shared_files`)
- Modify: `packages/crawler/src/emule_indexer/ports/mule_download_client.py` (ajout au Protocol)
- Test: `packages/crawler/tests/adapters/mule_ec/test_client_download.py` (tests de la méthode)
- Modify (fakes, ajout d'un `shared_files` → `()` chacun) : `tests/ports/test_mule_download_client.py` (`_StubDownloadClient`), `tests/application/test_run_download_cycle.py` (`FakeDownloadClient`), `tests/application/test_download_loop.py` (son fake/import), `tests/composition/test_app.py` (`FakeDownloadClient` + sous-classes `_ShutdownOnQueueDownloadClient`/`_UnreachableDownloadClient`), `tests/tools/test_download_probe.py` (`FakeDownloadClient` + `_Interrupting`)

- [ ] **Step 1 : Test qui échoue**

```python
@pytest.mark.asyncio
async def test_shared_files_maps_entries() -> None:
    reply = EcPacket(
        codes.EC_OP_SHARED_FILES,
        (_knownfile_entry(_HASH, "A.avi"), _knownfile_entry("b" * 32, "B.avi")),
    )
    client = _connected_client(_ScriptedTransport([reply]))
    shared = await client.shared_files()
    assert shared == (
        SharedFileEntry(ed2k_hash=_HASH, name="A.avi"),
        SharedFileEntry(ed2k_hash="b" * 32, name="B.avi"),
    )


@pytest.mark.asyncio
async def test_shared_files_requests_at_cmd_detail() -> None:
    transport = _ScriptedTransport([EcPacket(codes.EC_OP_SHARED_FILES)])
    client = _connected_client(transport)
    await client.shared_files()
    sent = transport.sent[0]
    assert sent.opcode == codes.EC_OP_GET_SHARED_FILES
    detail = sent.find(codes.EC_TAG_DETAIL_LEVEL)
    assert detail is not None and detail.int_value() == codes.EC_DETAIL_CMD


@pytest.mark.asyncio
async def test_shared_files_skips_non_knownfile_top_level_tags() -> None:
    reply = EcPacket(
        codes.EC_OP_SHARED_FILES,
        (string_tag(codes.EC_TAG_STRING, "bruit"), _knownfile_entry(_HASH, "A.avi")),
    )
    client = _connected_client(_ScriptedTransport([reply]))
    shared = await client.shared_files()
    assert shared == (SharedFileEntry(ed2k_hash=_HASH, name="A.avi"),)


@pytest.mark.asyncio
async def test_shared_files_empty_reply_is_empty_tuple() -> None:
    client = _connected_client(_ScriptedTransport([EcPacket(codes.EC_OP_SHARED_FILES)]))
    assert await client.shared_files() == ()
```

- [ ] **Step 2 : Lancer, voir échouer**

Run: `( cd packages/crawler && uv run pytest tests/adapters/mule_ec/test_client_download.py -k shared_files --no-cov -q )`
Expected: FAIL (`AmuleEcClient` n'a pas `shared_files`).

- [ ] **Step 3 : Implémentation**

Dans `client.py`, ajouter la méthode (calquée sur `download_queue`, juste après elle) :

```python
    async def shared_files(self) -> tuple[SharedFileEntry, ...]:
        """Snapshot des fichiers PARTAGÉS d'amuled (réf. EC). NE LIT JAMAIS les octets.

        Émet ``EC_OP_GET_SHARED_FILES`` au détail CMD ; la réponse ``EC_OP_SHARED_FILES`` porte
        N enfants ``EC_TAG_KNOWNFILE`` (hash + vrai nom on-disk). Une entrée sans hash/nom
        exploitable est ÉCARTÉE (tolérance aux inconnus, comme ``download_queue``).
        """
        request = EcPacket(
            codes.EC_OP_GET_SHARED_FILES,
            (uint_tag(codes.EC_TAG_DETAIL_LEVEL, codes.EC_DETAIL_CMD),),
        )
        reply = await self._request(request, codes.EC_OP_SHARED_FILES)
        entries: list[SharedFileEntry] = []
        for tag in reply.tags:
            if tag.name != codes.EC_TAG_KNOWNFILE:
                continue
            entry = _map_shared_file(tag)
            if entry is not None:
                entries.append(entry)
        return tuple(entries)
```

- [ ] **Step 3b : Élargir le Protocol + mettre TOUS les fakes en conformité**

Dans `ports/mule_download_client.py`, ajouter au Protocol `MuleDownloadClient` :

```python
    async def shared_files(self) -> tuple[SharedFileEntry, ...]: ...
```

(`SharedFileEntry` est déjà importé/défini dans ce fichier depuis Task 1.) Puis ajouter à CHAQUE fake/stub typé `MuleDownloadClient` une implémentation minimale inerte (la prod ne l'appelle pas avant Task 4) :

```python
    async def shared_files(self) -> tuple[SharedFileEntry, ...]:
        return ()
```

Fichiers concernés (importer `SharedFileEntry` où nécessaire) : `tests/ports/test_mule_download_client.py` (`_StubDownloadClient`), `tests/application/test_run_download_cycle.py` (`FakeDownloadClient`), `tests/application/test_download_loop.py` (son fake, ou l'import si réutilisé), `tests/composition/test_app.py` (`FakeDownloadClient` — les sous-classes `_ShutdownOnQueueDownloadClient`/`_UnreachableDownloadClient` héritent), `tests/tools/test_download_probe.py` (`FakeDownloadClient` ; `_Interrupting` hérite). Vérifier avec mypy quels fakes le réclament (mypy liste précisément les non-conformes).

- [ ] **Step 4 : Gate COMPLET, voir passer**

Run: `( cd packages/crawler && uv run pytest -q )` puis racine `uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: PASS, 100 % branch, mypy vert (le Protocol est élargi ET tous ses implémenteurs le satisfont → commit atomique gate-vert).

- [ ] **Step 5 : Commit**

```bash
git add -A
git commit -m "feat(ec): AmuleEcClient.shared_files + élargit le port (GET_SHARED_FILES → SHARED_FILES)"
```

---

### Task 4 : détection de complétion via les partagés (`run_download_cycle`)

**Files:**
- Modify: `packages/crawler/src/emule_indexer/application/run_download_cycle.py`
- Test: `packages/crawler/tests/application/test_run_download_cycle.py`

**Changements de prod (à appliquer ensemble) :**

1. Imports : retirer `Callable` de `from collections.abc import ...` (garder `Sequence`) ; retirer `DownloadEntry` de l'import `ports.mule_download_client` (garder `MuleDownloadClient`) ; retirer la ligne `StagingResolver = Callable[[DownloadEntry], Path]`.
2. `DownloadDeps` : remplacer le champ `staging_path_for: StagingResolver` par `staging_dir: Path`.
3. Ajouter le helper :

```python
def _safe_basename(name: str) -> str | None:
    """Basename confiné anti-traversal ; ``None`` si dégénéré (``""``/``.``/``..``).

    Le nom vient d'amuled (entrée externe — défense en profondeur, cf. CLAUDE.md « filenames
    are hostile input ») : on confine la SOURCE de ``os.replace`` à ``staging_dir``.
    """
    base = Path(name).name
    if base in {"", ".", ".."}:
        return None
    return base
```

4. Réécrire `_monitor` (ne fait plus que `QUEUED→DOWNLOADING` ; ne touche plus à la complétion) :

```python
async def _monitor(deps: DownloadDeps, states: dict[str, DownloadState]) -> None:
    """Réconcilie ``downloads`` avec la file amuled : QUEUED→DOWNLOADING (étape 1, spec §5).

    La complétion ne se déduit PLUS des octets (PS_COMPLETE est inobservable via la file — cf.
    docs/reference/2026-06-17-amuled-completion-behavior.md) : elle vient des fichiers partagés
    (_handle_completions). Ici on ne fait qu'acter qu'amuled tire un download mis en file.
    """
    queue = await deps.client.download_queue()
    for entry in queue:
        current = states.get(entry.ed2k_hash)
        if current is None:
            continue  # download hors crawler : ignoré
        if current in {
            DownloadState.QUARANTINED,
            DownloadState.FAILED,
            DownloadState.COMPLETED,
        }:
            continue  # terminal / déjà complété : ne pas régresser
        if current is not DownloadState.DOWNLOADING:
            deps.downloads.set_state(entry.ed2k_hash, DownloadState.DOWNLOADING)
            states[entry.ed2k_hash] = DownloadState.DOWNLOADING
```

5. Réécrire `_promote_completion` (reçoit le vrai nom + l'état courant ; stampe `COMPLETED` puis promeut) :

```python
async def _promote_completion(
    deps: DownloadDeps,
    ed2k_hash: str,
    name: str,
    current: DownloadState,
    states: dict[str, DownloadState],
) -> None:
    """Marque ``completed`` (stampe completed_at) puis promeut → quarantaine (étape 2, §5).

    Le ``staging_path`` est ``staging_dir / <vrai nom amuled>`` (résout DV10-Q2 : la dédup
    ``nom(0)`` est gérée puisque le nom vient d'amuled). ``promote`` échoue → reste ``completed``,
    retry au tour suivant (le hash est toujours dans les partagés — signal persistant).
    """
    safe = _safe_basename(name)
    if safe is None:
        _logger.warning("nom partagé dégénéré pour hash=%s (%r) — promotion sautée", ed2k_hash, name)
        return
    if current is not DownloadState.COMPLETED:
        deps.downloads.set_state(ed2k_hash, DownloadState.COMPLETED)
        states[ed2k_hash] = DownloadState.COMPLETED
    try:
        deps.quarantine.promote(deps.staging_dir / safe, ed2k_hash)
    except Exception as error:  # noqa: BLE001 — toute panne FS laisse completed (retry idempotent)
        _logger.warning(
            "quarantaine échouée pour hash=%s (%s) — reste completed, retry", ed2k_hash, error
        )
        await deps.telemetry.emit(PromotionFailed(ed2k_hash=ed2k_hash))
        return
    deps.local.enqueue_verification(ed2k_hash)
    deps.downloads.set_state(ed2k_hash, DownloadState.QUARANTINED)
    states[ed2k_hash] = DownloadState.QUARANTINED
    target_id = deps.downloads.get_target_id(ed2k_hash) or "inconnu"
    await deps.telemetry.emit(DownloadCompleted(target_id=target_id, ed2k_hash=ed2k_hash))
    _logger.info("hash=%s mis en quarantaine + vérification enfilée", ed2k_hash)
```

6. Réécrire `_handle_completions` (pilotée par les partagés) :

```python
async def _handle_completions(deps: DownloadDeps, states: dict[str, DownloadState]) -> None:
    """Promeut chaque hash suivi qui apparaît dans les fichiers PARTAGÉS d'amuled (étape 2, §5).

    Présence dans les partagés = complétion POSITIVE (fichier déjà déplacé/en place, auto-partagé
    par amuled). On promeut avec le vrai nom. Les hash terminaux (quarantined/failed) sont ignorés.
    """
    shared = await deps.client.shared_files()
    for entry in shared:
        current = states.get(entry.ed2k_hash)
        if current is None:
            continue  # fichier partagé hors crawler : ignoré
        if current in {DownloadState.QUARANTINED, DownloadState.FAILED}:
            continue  # déjà promu / échoué
        await _promote_completion(deps, entry.ed2k_hash, entry.name, current, states)
```

> `_handle_completions` fait désormais un I/O client (`shared_files()`). Dans `run_download_cycle`, l'appel est aujourd'hui enveloppé en `except RepositoryError` seulement. Il faut le ranger comme étape pouvant lever `MuleUnreachableError` (daemon mort → ABORT itération) en plus de `RepositoryError`. Voir Step 3 ci-dessous.

7. Dans `run_download_cycle`, l'étape « complétions » devient un I/O client → ajouter la capture `MuleUnreachableError` (ABORT) à côté de `RepositoryError` :

```python
    try:
        await _handle_completions(deps, states)
    except MuleUnreachableError as error:
        _logger.warning("daemon download injoignable (%s) — itération sautée, retry", error)
        return
    except RepositoryError as error:
        _logger.error("complétions download en échec repo (%s) — étape sautée, continue", error)
```

- [ ] **Step 1 : Mettre à jour le faux client + le builder de deps (test infra)**

Dans `tests/application/test_run_download_cycle.py` :

(a) Ajouter `SharedFileEntry` à l'import depuis `emule_indexer.ports.mule_download_client`.
(b) Étendre `FakeDownloadClient.__init__` avec un paramètre `shared: list[tuple[SharedFileEntry, ...]] | None = None` → `self._shared = list(shared or [()])`, et ajouter :

```python
    async def shared_files(self) -> tuple[SharedFileEntry, ...]:
        return self._shared.pop(0) if self._shared else ()
```

(c) Dans `_deps(...)`, remplacer `staging_path_for=lambda entry: Path("/staging") / entry.ed2k_hash,` par `staging_dir=Path("/staging"),`.

- [ ] **Step 2 : Tests qui échouent (nouveaux comportements shared-driven)**

Ajouter ces tests (et SUPPRIMER les anciens tests de complétion byte-based — ceux qui passent `queue=[(DownloadEntry(..., size_done=N, size_full=N),)]` et attendent `COMPLETED`/promotion ; les identifier en cherchant `size_done=10, size_full=10` dans les tests de complétion/monitor et les réécrire/retirer) :

```python
@pytest.mark.asyncio
async def test_shared_file_for_tracked_hash_is_promoted_with_real_name() -> None:
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
    quarantine = FakeQuarantine()
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A, name="Keroro 62a.avi"),)])
    local = FakeLocalRepo()
    deps = _deps(
        client=client, quarantine=quarantine, downloads=downloads,
        catalog=FakeCatalogReads(), local=local,
    )
    await run_download_cycle(deps)
    assert (Path("/staging") / "Keroro 62a.avi", _A) in quarantine.promoted
    assert downloads.states[_A] is DownloadState.QUARANTINED
    assert local.enqueued == [_A]


@pytest.mark.asyncio
async def test_shared_file_for_untracked_hash_is_ignored() -> None:
    downloads = FakeDownloadRepo()  # _A non suivi
    quarantine = FakeQuarantine()
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A, name="x.avi"),)])
    deps = _deps(
        client=client, quarantine=quarantine, downloads=downloads,
        catalog=FakeCatalogReads(), local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert quarantine.promoted == []


@pytest.mark.asyncio
async def test_already_quarantined_shared_hash_is_not_repromoted() -> None:
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.QUARANTINED
    quarantine = FakeQuarantine()
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A, name="x.avi"),)])
    deps = _deps(
        client=client, quarantine=quarantine, downloads=downloads,
        catalog=FakeCatalogReads(), local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert quarantine.promoted == []


@pytest.mark.asyncio
async def test_degenerate_shared_name_is_skipped() -> None:
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
    quarantine = FakeQuarantine()
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A, name=".."),)])
    deps = _deps(
        client=client, quarantine=quarantine, downloads=downloads,
        catalog=FakeCatalogReads(), local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert quarantine.promoted == []
    assert downloads.states[_A] is DownloadState.COMPLETED  # marqué completed, pas promu


@pytest.mark.asyncio
async def test_promotion_failure_leaves_completed_for_retry() -> None:
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
    quarantine = FakeQuarantine(fail_for={_A})
    client = FakeDownloadClient(shared=[(SharedFileEntry(ed2k_hash=_A, name="x.avi"),)])
    local = FakeLocalRepo()
    telemetry = RecordingTelemetry()
    deps = _deps(
        client=client, quarantine=quarantine, downloads=downloads,
        catalog=FakeCatalogReads(), local=local, telemetry=telemetry,
    )
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.COMPLETED
    assert local.enqueued == []
    assert any(isinstance(e, PromotionFailed) for e in telemetry.events)


@pytest.mark.asyncio
async def test_monitor_promotes_queued_to_downloading_not_completed() -> None:
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.QUEUED
    client = FakeDownloadClient(
        queue=[(DownloadEntry(ed2k_hash=_A, size_done=10, size_full=10),)],
        shared=[()],  # pas encore partagé → pas de complétion
    )
    quarantine = FakeQuarantine()
    deps = _deps(
        client=client, quarantine=quarantine, downloads=downloads,
        catalog=FakeCatalogReads(), local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)
    assert downloads.states[_A] is DownloadState.DOWNLOADING  # PAS completed (octets ignorés)
    assert quarantine.promoted == []
```

(Conserver l'import `DownloadEntry` dans le test — il sert encore au monitor et au probe.)

Pour la branche `MuleUnreachableError` de `_handle_completions` (Step 3 de prod) : ajouter un test où `shared_files` lève `MuleUnreachableError` et vérifier que le cycle ne lève pas et n'a pas promu. Le `FakeDownloadClient` doit pouvoir scripter un échec `shared_files` — ajouter un paramètre `shared_failures: list[Exception] | None = None` consommé dans `shared_files` (calqué sur `queue_failures`).

```python
@pytest.mark.asyncio
async def test_shared_files_unreachable_aborts_iteration_gracefully() -> None:
    downloads = FakeDownloadRepo()
    downloads.states[_A] = DownloadState.DOWNLOADING
    client = FakeDownloadClient(shared_failures=[MuleUnreachableError("flux mort")])
    quarantine = FakeQuarantine()
    deps = _deps(
        client=client, quarantine=quarantine, downloads=downloads,
        catalog=FakeCatalogReads(), local=FakeLocalRepo(),
    )
    await run_download_cycle(deps)  # ne lève pas
    assert quarantine.promoted == []
```

- [ ] **Step 3 : Lancer, voir échouer**

Run: `( cd packages/crawler && uv run pytest tests/application/test_run_download_cycle.py --no-cov -q )`
Expected: FAIL (deps `staging_dir` inconnu / `shared_files` non géré / nouveaux comportements absents).

- [ ] **Step 4 : Appliquer les changements de prod (1–7 ci-dessus) puis relancer**

Run: `( cd packages/crawler && uv run pytest tests/application/test_run_download_cycle.py --no-cov -q )`
Expected: PASS.

- [ ] **Step 5 : Commit**

```bash
git add packages/crawler/src/emule_indexer/application/run_download_cycle.py packages/crawler/tests/application/test_run_download_cycle.py
git commit -m "feat(download): complétion via fichiers partagés EC + vrai nom (résout DV10-Q2)"
```

---

### Task 5 : câblage composition + suppression de `resolve_staging_path`

**Files:**
- Modify: `packages/crawler/src/emule_indexer/composition/app.py`
- Delete: `packages/crawler/tests/composition/test_staging_resolver.py`
- Modify: `packages/crawler/tests/composition/test_app.py`
- Modify: `packages/crawler/tests/application/test_download_loop.py`

- [ ] **Step 1 : Mettre à jour les fakes/deps des tests (qui vont échouer)**

(a) `tests/application/test_download_loop.py:57` : remplacer `staging_path_for=lambda entry: Path("/staging") / entry.ed2k_hash,` par `staging_dir=Path("/staging"),`. Vérifier que le `FakeDownloadClient` de ce fichier (ou importé) expose `shared_files` ; sinon ajouter `async def shared_files(self) -> tuple[SharedFileEntry, ...]: return ()`.

(b) `tests/composition/test_app.py` : la classe `FakeDownloadClient` (ligne ~518) doit gagner `async def shared_files(self) -> tuple[SharedFileEntry, ...]: return ()` (import `SharedFileEntry`). Si un test y vérifiait la promotion via `staging_path_for`, l'adapter au nouveau câblage `staging_dir`.

- [ ] **Step 2 : Lancer, voir échouer**

Run: `( cd packages/crawler && uv run pytest tests/composition/test_app.py tests/application/test_download_loop.py --no-cov -q )`
Expected: FAIL (`staging_path_for` n'existe plus dans `DownloadDeps` après Task 4 / `shared_files` manquant).

- [ ] **Step 3 : Modifier `composition/app.py`**

(a) Supprimer la fonction `resolve_staging_path` (def + docstring, ~lignes 127-153).
(b) Dans l'import `from emule_indexer.application.run_download_cycle import (...)`, retirer `CatalogReader` (n'était utilisé QUE par la fonction supprimée — confirmer via ruff).
(c) Dans `from emule_indexer.ports.mule_download_client import DownloadEntry, MuleDownloadClient`, retirer `DownloadEntry` (idem).
(d) Dans `_build_download_and_verify_deps`, supprimer la ligne `staging_base = Path(staging_dir)` et le commentaire `# resolve_staging_path est une fonction MODULE-LEVEL...`, et remplacer `staging_path_for=lambda entry: resolve_staging_path(staging_base, catalog_repo, entry),` par `staging_dir=Path(staging_dir),`. Mettre à jour la docstring de la méthode (retirer la mention `staging_path_for`/DÉCISION DV10 ; décrire « `staging_dir` = l'Incoming d'amuled ; le nom vient des partagés »).

- [ ] **Step 4 : Supprimer le test obsolète**

```bash
git rm packages/crawler/tests/composition/test_staging_resolver.py
```

- [ ] **Step 5 : Lancer le gate complet, voir passer**

Run: `( cd packages/crawler && uv run pytest -q )` puis `uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: PASS, 100 % branch. (Si ruff signale un import devenu inutile dans `app.py`, le retirer.)

- [ ] **Step 6 : Commit**

```bash
git add -A
git commit -m "refactor(download): câble staging_dir, supprime resolve_staging_path (nom via partagés)"
```

---

### Task 6 : test d'intégration — décodage `shared_files()` contre un vrai amuled

**Files:**
- Modify: `packages/crawler/tests/integration/test_amuled_download.py`

- [ ] **Step 1 : Ajouter le test (round-trip)**

```python
@pytest.mark.asyncio
async def test_shared_files_round_trips(amuled: tuple[str, int]) -> None:
    # Confirme EMPIRIQUEMENT le cycle requête/réponse GET_SHARED_FILES → SHARED_FILES et que
    # le décodage ne lève pas (opcodes 0x10/0x22). Sur un amuled neuf la liste peut être vide ;
    # le mapping (conteneur EC_TAG_KNOWNFILE 0x0400, nom/hash) est couvert par les tests unit +
    # la source amont. Si des entrées remontent, elles doivent être des SharedFileEntry valides.
    host, port = amuled
    client = AmuleEcClient(host, port, _EC_PASSWORD, timeout=30.0)
    await client.connect()
    try:
        shared = await client.shared_files()
        assert isinstance(shared, tuple)
        assert all(isinstance(e, SharedFileEntry) for e in shared)
        assert all(len(e.ed2k_hash) == 32 and e.name for e in shared)
    finally:
        await client.close()
```

Ajouter l'import `from emule_indexer.ports.mule_download_client import DownloadEntry, SharedFileEntry`.

- [ ] **Step 2 : Vérifier la collecte (sans Docker, le marqueur est désélectionné par défaut)**

Run: `( cd packages/crawler && uv run pytest tests/integration/test_amuled_download.py --collect-only -q )`
Expected: le nouveau test est listé ; aucune erreur d'import.

- [ ] **Step 3 : (Geoffrey, vrai shell) lancer l'intégration**

Run: `( cd packages/crawler && uv run pytest -m download_integration --no-cov )`
Expected: PASS (round-trip OK). *À lancer par Geoffrey — Docker requis, ne tourne pas dans le sandbox.*

- [ ] **Step 4 : Commit**

```bash
git add packages/crawler/tests/integration/test_amuled_download.py
git commit -m "test(download): intégration décodage shared_files contre un vrai amuled"
```

---

### Task 7 : documentation

**Files:**
- Modify: `docs/reference/2026-06-17-amuled-completion-behavior.md`
- Modify: `docs/runbook-deployment.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1 : Reference doc** — ajouter une section « Détection côté crawler : par les fichiers partagés » (signal positif via `EC_OP_GET_SHARED_FILES`, vrai nom → Q2 résolu) ; requalifier l'« angle mort Q2 » (résolu) et la note « même volume » (caduque).

- [ ] **Step 2 : Runbook** — dans la section DV10 : retirer la contrainte « même volume » et la limite Q2 ; ajouter la reco « amuled **dédié**, jeu partagé restreint (un round-trip `GET_SHARED_FILES`/cycle) ».

- [ ] **Step 3 : CLAUDE.md** — mettre à jour la ligne DV10 : complétion désormais via la liste des partagés (`shared_files()`), vrai nom on-disk → Q2 résolu ; `_monitor` ne fait plus que QUEUED→DOWNLOADING.

- [ ] **Step 4 : Gate complet final**

Run: `( cd packages/crawler && uv run pytest -q ) && ( cd packages/verifier && uv run pytest -q ) && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run sqlfluff lint packages/crawler/src`
Expected: tout vert, 100 % branch des deux paquets.

- [ ] **Step 5 : Commit**

```bash
git add -A
git commit -m "docs(download): complétion via fichiers partagés (Q2 résolu, contrainte volume levée)"
```

---

## Auto-revue (faite à l'écriture)

- **Couverture spec** : §3 constantes → Task 2 ; §4.1 EC layer → Tasks 1-3 ; §4.2 détection → Task 4 ; §4.3 suppression resolve_staging_path → Task 5 ; §4.4 docs → Task 7 ; §5 tests → Tasks 1-6 (unit) + Task 6 (intégration) ; §6 risques (round-trip, dédié) → docs Task 7.
- **Cohérence des types** : `SharedFileEntry(ed2k_hash, name)` défini Task 1, utilisé identiquement Tasks 2-6 ; `shared_files() -> tuple[SharedFileEntry, ...]` cohérent port (Task 1) / adapter (Task 3) / fakes (Tasks 4-5) ; `DownloadDeps.staging_dir: Path` cohérent prod (Task 4) / câblage (Task 5) / fakes (Tasks 4-5).
- **`COMPLETED` préservé** : `_promote_completion` stampe `COMPLETED` (branche `download_repository.py:77` + colonne `completed_at` toujours exercées) ; `_monitor` ignore `COMPLETED` (pas de régression).
- **Pas de code mort** : `DownloadEntry.is_complete`/`size_done`/`size_full` restent utilisés par `tools/download_probe.py` (couverts).
- **`EcTag.string_value()` vérifié** (codec.py:53) : lève `EcProtocolError` si non-STRING ou sans NUL final ; sinon décode `errors="replace"`. La branche `except` de `_map_shared_file` est donc exercée par un name tag STRING sans NUL (Task 2 Step 3b) — pas par des octets « non décodables ».
