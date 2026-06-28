# Crawler MVP — Plan 3 : Adapter EC + observation (`v0.5.0-ec-adapter`) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construire le **client EC** complet (le risque technique n°1 du projet) : un codec binaire **pur et synchrone** (bytes ↔ arbre de tags, générique pour N'IMPORTE QUEL paquet EC), un transport **async** (framing d'un paquet sur `asyncio.StreamReader/Writer`, timeout sur chaque lecture), un client haut niveau `AmuleEcClient` (auth challenge/réponse, recherche mot-clé `global`/`kad`, relevé des résultats, arrêt, progression, statut réseau) qui implémente le port `MuleClient`, un mapping **capture-all** des résultats vers le nouvel objet domaine `FileObservation` (pont `.to_candidate()` vers le moteur de matching), un outil probe CLI (`uv run python -m emule_indexer.tools.ec_probe`), des tests d'intégration **testcontainers contre un `amuled` réel** (obligatoires avant le tag, hors coverage), et un **rapport de richesse des champs** (espérés §11 vs réellement exposés par EC). Spec : `docs/superpowers/specs/2026-06-11-ec-adapter-design.md`. **TOUT fait wire-level de ce plan provient de `docs/reference/ec-protocol.md`** (réf. vérifiée sur les sources aMule 2.3.3/3.0.0) — chaque séquence d'octets de test porte sa dérivation en commentaire.

**Architecture:** Clean/Hexagonal, inchangée. `domain/observation.py` est **PUR** (aucun import hors stdlib/domaine). `ports/mule_client.py` ne contient que le Protocol async `MuleClient` et ses DTO figés (`NetworkStatus`, enums fermés `SearchChannel`/`KadStatus`) — il n'importe que le domaine. Tout le binaire/réseau vit dans `adapters/mule_ec/` (`codes.py` constantes référencées, `errors.py` hiérarchie `EcError`, `codec.py` PUR/sync, `transport.py` async, `client.py` async, `mapping.py` pur) : **personne d'autre que l'adapter ne voit un opcode**. L'adapter **signale, il ne décide pas** (spec §6) : aucun `sleep`, aucun retry, aucune reconnexion, aucune boucle d'attente — le polling appartient à l'appelant ; la convenance `search_and_wait` vit dans l'outil probe (`tools/ec_probe.py`), PAS dans le port. Parsing **défensif** : longueur de paquet bornée (16 Mio, plafond aMule), décompression zlib bornée, profondeur de tags bornée (32) ; un tag inconnu n'est JAMAIS une erreur (→ `raw_meta`) ; une entrée de résultat inexploitable est écartée et **comptée**, jamais fatale au lot.

**Tech Stack:** Python ≥ 3.12, `uv`, `ruff` (`select=["E","F","I","UP","B","SIM"]`, line-length 100), `mypy --strict` (`files=["src","tests"]`), `pytest` + `pytest-cov` (gate **100 % branch** sur le run par défaut). **Nouvelles dépendances dev** : `pytest-asyncio` (mode `strict`, markers explicites — vérifié sur la doc courante : `asyncio_mode` + `asyncio_default_fixture_loop_scope` dans `[tool.pytest.ini_options]`) et `testcontainers` (`DockerContainer` + `with_env`/`with_exposed_ports`/`get_exposed_port` + `wait_for_logs` — vérifié sur la doc courante). Tests d'intégration sous marker `ec_integration`, **déselectionnés par défaut** (`addopts` + `-m "not ec_integration"`), run dédié `uv run pytest -m ec_integration --no-cov`. Aucune nouvelle dépendance de prod : le codec n'utilise que `zlib`/`hashlib`/`asyncio` de la stdlib.

> **Référence spec :** `docs/superpowers/specs/2026-06-11-ec-adapter-design.md` — §2 (périmètre), §3 (décisions verrouillées), §4 (architecture, `FileObservation`, port `MuleClient`), §5 (flux), §6 (erreurs), §7 (tests trois étages), §8 (livrables). **Référence protocole (unique source wire-level) :** `docs/reference/ec-protocol.md` — §1 (trame/flags/zlib/16 Mio), §2 (encodage tag, TAGLEN), §3 (types), §4 (auth + formule exacte du hash), §5 (recherche), §6 (statut), §7 (table des constantes → `codes.py`), §8 (image docker), §9 (pièges).

> **HORS PÉRIMÈTRE (plans ultérieurs — RIEN de tout ceci n'apparaît ici) :** persistance (`catalog.db`/`local.db`, plan A) ; cadencement/backoff/reconnexion (plan C) ; téléchargements `ed2k://` (plan D) ; métriques/notifications (plan E). Les objets produits (`NetworkStatus`, compteur d'entrées écartées) sont seulement CONÇUS pour s'y brancher.

---

## File Structure & décisions verrouillées

```
src/emule_indexer/
├── domain/observation.py             # Create (PUR) : FileObservation + .to_candidate()
├── ports/__init__.py                 # Create
├── ports/mule_client.py              # Create : MuleClient (Protocol async), NetworkStatus,
│                                     #          SearchChannel, KadStatus (enums fermés)
├── adapters/mule_ec/__init__.py      # Create
├── adapters/mule_ec/codes.py         # Create : constantes (réf. §7), transcrites + référencées
├── adapters/mule_ec/errors.py        # Create : EcError → EcConnectError/EcAuthError/
│                                     #          EcProtocolError/EcTimeoutError/EcFailureError
├── adapters/mule_ec/codec.py         # Create (PUR, sync) : EcTag/EcPacket, encode/décode
├── adapters/mule_ec/transport.py     # Create (async) : EcTransport, open_ec_transport
├── adapters/mule_ec/client.py        # Create (async) : AmuleEcClient (implémente MuleClient)
├── adapters/mule_ec/mapping.py       # Create (pur) : tags EC → FileObservation, capture-all
├── tools/__init__.py                 # Create
└── tools/ec_probe.py                 # Create : CLI probe (recherche réelle + dump des tags)

tests/
├── domain/test_observation.py        # Create
├── ports/{__init__.py,test_mule_client.py}            # Create
├── adapters/mule_ec/{__init__.py,ec_fakes.py,         # Create (ec_fakes = faux serveur EC)
│   test_codes.py,test_codec.py,test_transport.py,
│   test_mapping.py,test_client.py}
├── tools/{__init__.py,test_ec_probe.py}               # Create
└── integration/{__init__.py,test_amuled_ec.py}        # Create (marker ec_integration)

docs/reference/2026-06-11-ec-field-richness.md         # Create (livrable 5)
pyproject.toml                                          # Modify (Task 1 UNIQUEMENT)
```

> **DÉCISION 1 — Hiérarchie d'erreurs dans un module dédié `adapters/mule_ec/errors.py`.**
> La spec (§6) impose la hiérarchie `EcError` → `EcConnectError`/`EcAuthError`/`EcProtocolError`/`EcTimeoutError` mais ne dit pas où elle vit. La répartir entre `codec.py`/`transport.py`/`client.py` éclaterait une hiérarchie qui se lit d'un bloc ; `codes.py` est réservé aux constantes. Donc : un module feuille `errors.py`, importé par tous les autres. L'« erreur applicative EC » (le daemon répond `EC_OP_FAILED`, spec §6 dernier point) est **`EcFailureError(EcError)`**, portant le message du daemon — distincte d'`EcProtocolError` (trame illisible) comme exigé.

> **DÉCISION 2 — Stratégie de flags : n'annoncer AUCUNE capacité ; émettre toujours `0x20` ; n'accepter que `0x20` et `0x21` en lecture.**
> Réf. §1 : un client qui n'annonce ni `EC_TAG_CAN_ZLIB` ni `EC_TAG_CAN_UTF8_NUMBERS` **reçoit toujours `flags = 0x20`** (`flags &= m_my_flags` côté serveur) — c'est « la voie simple et sûre » (réf. §9 piège 6). On émet donc toujours `flags = 0x20` (base obligatoire, `m_my_flags(0x20)`, `ECSocket.cpp:275`) sans compression. En lecture, plutôt que de recopier le test laxiste d'aMule (`(flags & 0x60) != 0x20 || flags & 0xff7f7f08`, qui laisse passer des bits non définis comme `0x80`), on est **strictement défensif** : seuls `0x20` (base) et `0x21` (base|zlib) sont acceptés ; tout le reste → `EcProtocolError`. Le support zlib en LECTURE est conservé (décompression **bornée**, décision verrouillée spec §6) bien que jamais négocié : le codec est générique et les fixtures zlib le prouvent.

> **DÉCISION 3 — Bornes défensives chiffrées.** `_MAX_PACKET_PAYLOAD = 16 * 1024 * 1024` (plafond exact d'aMule, `ReadHeader`, `ECSocket.cpp:540`, réf. §1) ; `_MAX_DECOMPRESSED = 16 * 1024 * 1024` (même ordre : un payload légitime décompressé ne peut pas dépasser ce qu'aMule accepterait en clair) via `zlib.decompressobj().decompress(data, max_length)` + contrôle `unconsumed_tail`/`eof` ; `_MAX_TAG_DEPTH = 32` (cohérent avec la borne de profondeur 32 de `validation.py` côté moteur). Les trois sont des constantes module de `codec.py`, testées des deux côtés.

> **DÉCISION 4 — Modèle de tag : `EcTag` gelé portant le nom LOGIQUE (déjà `>>1`), constructeurs « builders » et accesseurs à largeur variable.**
> `EcTag(name, tag_type, value, children)` est gelé/hashable (eq structurel → round-trip testable par `==`). Le décalage `(name << 1) | enfants` (réf. §2, piège 2) est ENFERMÉ dans l'encode/décode : tout le reste du code manipule des noms logiques de `codes.py`. Builders : `uint_tag` (entier **au plus court**, réf. §3 `InitInt`), `string_tag` (UTF-8 **+ NUL final inclus dans TAGLEN**, piège 10), `hash16_tag` (16 octets exigés), `empty_tag` (type `CUSTOM`, TAGLEN 0, forme des tags `CAN_*`, réf. §2). Accesseurs : `int_value()` accepte **les 4 largeurs** (équivalent `GetInt()`, piège 4 — indispensable pour le sel d'auth), `string_value()` (NUL final exigé, décodage `errors="replace"` : un nom de fichier hostile ne crashe jamais), `ipv4_value()` (6 octets → `"a.b.c.d:port"`, réf. §3), `find(name)` (premier enfant du nom logique donné). `EcPacket(opcode, tags)` gelé + `find(name)`.

> **DÉCISION 5 — Transport : timeout sur chaque LECTURE (+ connexion) ; l'écriture enveloppe `OSError` → `EcConnectError` ; pas de timeout d'écriture.**
> Spec §6 : « timeout sur chaque lecture réseau, configurable à la construction ». `receive_packet` lit l'en-tête (8 octets) puis le payload, chacun sous `asyncio.wait_for(reader.readexactly(...), timeout)` → `TimeoutError` → `EcTimeoutError` ; EOF (`IncompleteReadError`) ou erreur socket → `EcConnectError`. `open_ec_transport` borne aussi l'établissement TCP (refus → `EcConnectError`, délai → `EcTimeoutError`). `send_packet` = `write()` + `drain()` sous `except OSError → EcConnectError` (un `drain` sur connexion perdue lève `ConnectionResetError`, déterministe à tester après `close()`) — pas de `wait_for` sur l'écriture : la spec ne l'exige que sur les lectures et un timeout d'écriture serait intestable déterministiquement. `close()` ne supprime aucune erreur (l'adapter signale). **Une trame à la fois, FCFS** (réf. §9 piège 14) : aucune corrélation par contenu, l'appelant enchaîne requête → réponse.

> **DÉCISION 6 — Surface du client.** `AmuleEcClient(host, port, password, *, timeout=10.0)` implémente structurellement `MuleClient` (sans l'importer pour `isinstance` — même typage structurel que `matchers.py`/`combinators.py`). (a) `connect()` refuse un mot de passe **vide** avant toute I/O (`EcAuthError`, miroir de `RemoteConnect.cpp:117`, réf. §4) ; (b) le sel est lu **générique** (`int_value()`, piège 4) ; (c) le hash d'auth est isolé dans la fonction pure `salted_password_hash(password, salt)` (formule exacte réf. §4, testée sur vecteurs précalculés) ; (d) `search_progress()` suit la convention d'amulecmd (`TextClient.cpp:865-873`, réf. §5) : valeur ≤ 100 → pourcentage, sinon (`0xffff` locale, `0xfffe` Kad fini) ou tag absent → `None` ; (e) le mot-clé de provenance est mémorisé **après** le succès de `start_search` ; (f) le compte d'entrées écartées du mapper s'accumule dans l'attribut public **`skipped_entries_total: int`** (hors Protocol — futur brancheur de métrique plan E, spec §2/§6) ; (g) toute réponse `EC_OP_FAILED` → `EcFailureError(message du daemon)` ; tout opcode inattendu → `EcProtocolError` ; opération sans connexion → `EcConnectError`.

> **DÉCISION 7 — Mapping capture-all : `raw_meta` = tuple de paires `("0xNNNN", valeur_rendue)` ; rendu qui ne lève JAMAIS.**
> Le nom d'un tag inconnu est rendu en hex 4 chiffres (`f"0x{name:04X}"` — stable, JSON-friendly, lisible dans le rapport du probe). La valeur : entier décimal si le type/largeur est entier valide, chaîne décodée si STRING bien formé, **hex brut sinon** — `_render_value` n'a aucun chemin d'exception (une métadonnée pourrie est conservée en hex, jamais perdue, jamais fatale : spec §6). Sont mappés (donc EXCLUS de `raw_meta`) : NAME, SIZE_FULL, HASH, SOURCE_COUNT, SOURCE_COUNT_XFER. Tout le reste (STATUS 0x0308, PARENT 0x0709, RATING 0x040F, inconnus futurs) → `raw_meta`. Une entrée sans hash/nom/taille exploitables → `None`, comptée par l'appelant (`map_search_results` retourne `(observations, skipped)`). Un tag de premier niveau qui n'est pas `EC_TAG_SEARCHFILE` est ignoré (tolérance). `source_count`/`complete_source_count` absents → 0 (réf. §3 : absence = valeur nulle/false). **L'ECID (valeur propre du tag SEARCHFILE) n'est jamais conservé** (identifiant de session volatil, piège 13) ; seul le hash MD4 identifie.

> **DÉCISION 8 — Conversion d'unités du pont `to_candidate()` : `size_mb = size_bytes / (1024 * 1024)` (Mio).**
> `FileCandidate.size_mb` (moteur) est un `float` sans unité documentée ; les clients eMule affichent des « MB » binaires (Mio) et les seuils `attr_between` de la config canonique ont été pensés sur ces affichages. On fige 1 Mio = 1 048 576 octets, en constante commentée. `duration_sec`/`bitrate_kbps` : conversion `int → float` si présents, sinon `None` (les deux côtés testés).

> **DÉCISION 9 — pytest-asyncio en mode `strict` + markers explicites.**
> Conforme à l'esprit du projet (« pas de magie ») : chaque test async porte `@pytest.mark.asyncio` ; `asyncio_mode = "strict"` et `asyncio_default_fixture_loop_scope = "function"` dans `pyproject.toml` (option requise par les versions courantes pour fixer la portée de boucle par défaut sans warning). Pas de fixture async dans ce plan (les faux serveurs sont des context managers async DANS les tests). `--strict-markers` est déjà actif : le marker `ec_integration` est **déclaré** dans `[tool.pytest.ini_options].markers`.

> **DÉCISION 10 — Intégration : image Docker `ngosang/amule:3.0.0-1`.**
> Le dépôt GitHub s'appelle `ngosang/docker-amule` mais l'image publiée sur Docker Hub est **`ngosang/amule`** (vérifié sur le Hub le 2026-06-11 ; le tag `3.0.0-1` épingle aMule 3.0.0, protocole EC `0x0204`, réf. §8). Mot de passe EC : env **`GUI_PWD`** (le script de l'image écrit son MD5 dans `ECPassword`, exactement la valeur qui entre dans la formule §4). Readiness : ligne de log `*** TCP socket (ECServer) listening on 0.0.0.0:4712` (`ExternalConn.cpp:333`) attendue via `wait_for_logs` (timeout large : premier démarrage lent, réf. §8). Conteneur **module-scoped** (un seul démarrage pour les 4 tests). Le test du cycle de recherche tolère un `EcFailureError` propre sur `start_search` (conteneur possiblement sans serveur eD2k joignable) : la spec valide le CYCLE requête/réponse, pas la présence de résultats ; les deux issues sont des validations réelles du protocole et le test l'documente.

> **DÉCISION 11 — Probe : `search_and_wait` à budget borné, horloge-indépendant, sortie testée.**
> `rounds = max(1, ceil(timeout / interval))` relevés, `await sleep(interval)` entre deux (le `sleep` est **injectable** — `asyncio.sleep` par défaut, faux instantané en test : zéro flakiness, pas de lecture d'horloge). Arrêt anticipé si `progress == 100`. `main()` : parse → `asyncio.run(run_probe(...))` ; `EcError` attrapée → message sur stderr + code retour 1 ; la fabrique de client est injectable (`client_factory`) pour couvrir `main` à 100 % avec un faux client ; le `if __name__ == "__main__"` porte `# pragma: no cover`. Le probe dumpe le statut réseau, chaque observation ET chaque entrée `raw_meta` (nom hex + valeur) — c'est l'outil de mesure de richesse des champs (livrable 4/5).

> **Note couverture (gate 100 % branch — points chauds par tâche) :** chaque conditionnel exercé des deux côtés. En particulier : `uint_tag` (les 4 largeurs + négatif + trop grand) ; `_encode_tag`/`_tag_len` (avec/sans enfants) ; `decode_header` (chaque rejet + les deux flags acceptés) ; `_decode_tag` (profondeur ≤/>, enfants oui/non, TAGLEN menteur, tronqué) ; `_inflate` (corrompu/tronqué/borné/valide) ; transport (chaque `except` déclenché déterministiquement + chemins nominaux) ; client (chaque opcode attendu/`AUTH_FAIL`/`FAILED`/inattendu, sel large/étroit, mdp vide/non, connecté/non, progress ≤100/>100/absent, chaque bit CONNSTATE dans les deux états) ; mapping (entrée complète/incomplète, chaque type de rendu, tag non-SEARCHFILE) ; probe (succès/erreur, arrêt anticipé/épuisement, raw_meta vide/non). Stubs du Protocol : **une ligne** (`async def x(...) -> T: ...`) pour être couverts par le `def` (handoff §8).

> **Note typage (`mypy --strict` sur src ET tests) :** toutes les fonctions de test `-> None`, paramètres typés. `testcontainers` reçoit un override `ignore_missing_imports` (comme `re2`) : inoffensif si le paquet embarque finalement `py.typed`, indispensable sinon ; `pytest-asyncio` est typé (pas d'override). Aucun `cast`, aucun `type: ignore` hors motif documenté.

---

## Task 1: Outillage — dépendances dev, marker, config pytest-asyncio, override mypy

**Files:**
- Modify: `pyproject.toml`

> Tout le plan tourne ensuite sous cette config : c'est la première tâche, exprès. Aucune dépendance de PROD n'est ajoutée.

- [ ] **Step 1: Ajouter les dépendances dev**

```bash
uv add --dev "pytest-asyncio>=1.2" "testcontainers>=4.10"
```

- [ ] **Step 2: Éditer `pyproject.toml`**

Remplacer la section `[tool.pytest.ini_options]` existante par (la ligne `addopts` passe en chaîne TOML à quotes simples pour porter des quotes doubles internes ; le filtre `-m` de la ligne de commande PRIME sur celui d'`addopts`, c'est ce qui permet le run dédié d'intégration) :

```toml
[tool.pytest.ini_options]
addopts = '--cov=emule_indexer --cov-report=term-missing --cov-fail-under=100 --strict-markers -m "not ec_integration"'
testpaths = ["tests"]
markers = [
    "ec_integration: tests d'intégration contre un amuled réel (Docker requis) — déselectionnés par défaut ; run dédié : uv run pytest -m ec_integration --no-cov",
]
asyncio_mode = "strict"
asyncio_default_fixture_loop_scope = "function"
```

Ajouter, à la suite de l'override `re2` existant :

```toml
[[tool.mypy.overrides]]
module = "testcontainers.*"
ignore_missing_imports = true
```

- [ ] **Step 3: Vérifier que la suite existante reste verte sous la nouvelle config**

Run: `uv run pytest -q`
Expected: PASS — tous les tests existants verts, coverage 100 % ; le filtre `-m "not ec_integration"` ne déselectionne rien (aucun test marqué n'existe encore).

Run: `uv run pytest -m ec_integration --no-cov -q`
Expected: « no tests ran » (code retour 5 — **attendu et normal** à ce stade : aucun test d'intégration n'existe encore ; ne pas « corriger »).

- [ ] **Step 4: Vérifier lint + types**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert (la config TOML n'introduit aucun code).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: EC adapter toolchain (pytest-asyncio strict, testcontainers, ec_integration marker)"
```

---

## Task 2: Domaine — `FileObservation` + pont `to_candidate()`

**Files:**
- Create: `src/emule_indexer/domain/observation.py`
- Create: `tests/domain/test_observation.py`

- [ ] **Step 1: Écrire les tests qui échouent**

`tests/domain/test_observation.py` :
```python
import dataclasses

import pytest

from emule_indexer.domain.matching.models import FileCandidate
from emule_indexer.domain.observation import FileObservation


def _full_observation() -> FileObservation:
    return FileObservation(
        ed2k_hash="31d6cfe0d16ae931b73c59d7e0c089c0",
        filename="Keroro 062A.avi",
        size_bytes=3 * 1024 * 1024,
        source_count=5,
        complete_source_count=2,
        keyword="keroro",
        media_length_sec=1234,
        bitrate_kbps=1500,
        codec="xvid",
        file_type="Video",
        raw_meta=(("0x0308", "0"),),
    )


def test_file_observation_is_frozen_and_holds_fields() -> None:
    observation = _full_observation()
    assert observation.ed2k_hash == "31d6cfe0d16ae931b73c59d7e0c089c0"
    assert observation.filename == "Keroro 062A.avi"
    assert observation.size_bytes == 3 * 1024 * 1024
    assert observation.source_count == 5
    assert observation.complete_source_count == 2
    assert observation.keyword == "keroro"
    assert observation.raw_meta == (("0x0308", "0"),)
    with pytest.raises(dataclasses.FrozenInstanceError):
        observation.filename = "autre"  # type: ignore[misc]


def test_media_fields_and_raw_meta_default_to_absent() -> None:
    observation = FileObservation(
        ed2k_hash="31d6cfe0d16ae931b73c59d7e0c089c0",
        filename="Keroro 062A.avi",
        size_bytes=100,
        source_count=0,
        complete_source_count=0,
        keyword="keroro",
    )
    assert observation.media_length_sec is None
    assert observation.bitrate_kbps is None
    assert observation.codec is None
    assert observation.file_type is None
    assert observation.raw_meta == ()


def test_to_candidate_converts_units_with_media_metadata() -> None:
    # 3 Mio exactement -> size_mb == 3.0 (DÉCISION 8 : 1 Mio = 1024*1024 octets).
    candidate = _full_observation().to_candidate()
    assert candidate == FileCandidate(
        filename="Keroro 062A.avi",
        size_mb=3.0,
        duration_sec=1234.0,
        bitrate_kbps=1500.0,
    )


def test_to_candidate_maps_absent_media_metadata_to_none() -> None:
    observation = FileObservation(
        ed2k_hash="31d6cfe0d16ae931b73c59d7e0c089c0",
        filename="Keroro 062A.avi",
        size_bytes=524288,  # 0.5 Mio
        source_count=1,
        complete_source_count=0,
        keyword="keroro",
    )
    candidate = observation.to_candidate()
    assert candidate == FileCandidate(
        filename="Keroro 062A.avi",
        size_mb=0.5,
        duration_sec=None,
        bitrate_kbps=None,
    )
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/domain/test_observation.py --no-cov -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'emule_indexer.domain.observation'`.

- [ ] **Step 3: Écrire l'implémentation**

`src/emule_indexer/domain/observation.py` :
```python
"""Observation d'un fichier vu sur le réseau (cf. spec EC-adapter §4 ; spec MVP §11).

Domaine PUR. ``FileObservation`` est aligné sur la table ``file_observations`` (§11) :
le plan A persistera cet objet tel quel ; l'adapter DB ajoutera ``observed_at``/``node_id``
(même principe que ``MatchDecision``). ``raw_meta`` est le capture-all (paires
``(nom, valeur)`` JSON-friendly) : on ne perd JAMAIS une métadonnée, même inconnue.
"""

from dataclasses import dataclass

from emule_indexer.domain.matching.models import FileCandidate

# DÉCISION 8 : les « MB » affichés par les clients eMule sont binaires (Mio).
_BYTES_PER_MIB = 1024 * 1024


@dataclass(frozen=True)
class FileObservation:
    """Un fichier observé lors d'une recherche (clé contenu = hash eD2k, jamais la personne).

    Les champs média sont ``None`` si le réseau ne les a pas fournis (métadonnées
    auto-déclarées, non fiables — spec MVP §10.1). ``keyword`` est la provenance
    (le mot-clé de la recherche qui a produit l'observation).
    """

    ed2k_hash: str
    filename: str
    size_bytes: int
    source_count: int
    complete_source_count: int
    keyword: str
    media_length_sec: int | None = None
    bitrate_kbps: int | None = None
    codec: str | None = None
    file_type: str | None = None
    raw_meta: tuple[tuple[str, str], ...] = ()

    def to_candidate(self) -> FileCandidate:
        """Pont vers le moteur de matching : conversions d'unités (octets → Mio, int → float)."""
        duration = float(self.media_length_sec) if self.media_length_sec is not None else None
        bitrate = float(self.bitrate_kbps) if self.bitrate_kbps is not None else None
        return FileCandidate(
            filename=self.filename,
            size_mb=self.size_bytes / _BYTES_PER_MIB,
            duration_sec=duration,
            bitrate_kbps=bitrate,
        )
```

- [ ] **Step 4: Lancer pour vérifier que tout passe**

Run: `uv run pytest tests/domain/test_observation.py --no-cov -q`
Expected: PASS — 4 tests verts (gelé, défauts, conversion avec et sans métadonnées média).

- [ ] **Step 5: Vérifier la suite complète + types + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert ; coverage 100 %.

- [ ] **Step 6: Commit**

```bash
git add src/emule_indexer/domain/observation.py tests/domain/test_observation.py
git commit -m "feat(domain): FileObservation (capture-all) + bridge to FileCandidate"
```

---

## Task 3: Port — `MuleClient` (Protocol async) + `NetworkStatus`/`SearchChannel`/`KadStatus`

**Files:**
- Create: `src/emule_indexer/ports/__init__.py`
- Create: `src/emule_indexer/ports/mule_client.py`
- Create: `tests/ports/__init__.py`
- Create: `tests/ports/test_mule_client.py`

> Premier test async du dépôt : valide au passage la config pytest-asyncio de la Task 1.

- [ ] **Step 1: Écrire les tests qui échouent**

`tests/ports/__init__.py` :
```python
"""Tests des ports (Protocols + DTO)."""
```

`tests/ports/test_mule_client.py` :
```python
import dataclasses

import pytest

from emule_indexer.domain.observation import FileObservation
from emule_indexer.ports.mule_client import (
    KadStatus,
    MuleClient,
    NetworkStatus,
    SearchChannel,
)


def test_search_channel_is_the_closed_global_kad_enum() -> None:
    assert {channel.value for channel in SearchChannel} == {"global", "kad"}
    assert SearchChannel("global") is SearchChannel.GLOBAL
    assert SearchChannel("kad") is SearchChannel.KAD


def test_kad_status_is_the_closed_four_state_enum() -> None:
    # Réf. §6 : ni 0x10 -> arrêté ; 0x10 seul -> tourne ; |0x04 -> connecté ; |0x08 -> firewalled.
    assert {status.value for status in KadStatus} == {"off", "running", "connected", "firewalled"}


def test_network_status_is_frozen_and_holds_fields() -> None:
    status = NetworkStatus(
        ed2k_id=33554433,
        ed2k_high=True,
        kad_status=KadStatus.CONNECTED,
        server_name="TestServer",
        server_addr="1.2.3.4:4661",
    )
    assert status.ed2k_id == 33554433
    assert status.ed2k_high is True
    assert status.kad_status is KadStatus.CONNECTED
    assert status.server_name == "TestServer"
    assert status.server_addr == "1.2.3.4:4661"
    with pytest.raises(dataclasses.FrozenInstanceError):
        status.ed2k_high = False  # type: ignore[misc]


def test_network_status_server_fields_default_to_none() -> None:
    status = NetworkStatus(ed2k_id=None, ed2k_high=False, kad_status=KadStatus.OFF)
    assert status.server_name is None
    assert status.server_addr is None


class _StubClient:
    """Implémentation structurelle minimale : satisfait MuleClient SANS l'importer."""

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def start_search(self, keyword: str, channel: SearchChannel) -> None:
        return None

    async def fetch_results(self) -> tuple[FileObservation, ...]:
        return ()

    async def stop_search(self) -> None:
        return None

    async def search_progress(self) -> int | None:
        return None

    async def network_status(self) -> NetworkStatus:
        return NetworkStatus(ed2k_id=None, ed2k_high=False, kad_status=KadStatus.OFF)


@pytest.mark.asyncio
async def test_stub_client_satisfies_mule_client_protocol() -> None:
    # L'annotation `MuleClient` force mypy à vérifier la compatibilité STRUCTURELLE.
    client: MuleClient = _StubClient()
    await client.connect()
    await client.start_search("keroro", SearchChannel.GLOBAL)
    assert await client.fetch_results() == ()
    assert await client.search_progress() is None
    assert (await client.network_status()).kad_status is KadStatus.OFF
    await client.stop_search()
    await client.close()
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/ports/test_mule_client.py --no-cov -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'emule_indexer.ports'`.

- [ ] **Step 3: Écrire l'implémentation**

`src/emule_indexer/ports/__init__.py` :
```python
"""Ports (Clean Architecture) : Protocols async + DTO figés, AUCUNE implémentation."""
```

`src/emule_indexer/ports/mule_client.py` :
```python
"""Port ``MuleClient`` : ce que le crawler attend d'un client eMule (cf. spec EC-adapter §4).

Le port n'importe QUE le domaine. Les stubs du Protocol tiennent sur UNE ligne (le ``def``
s'exécute à la création de la classe : couvert). La convenance ``search_and_wait`` (poll +
timeout) vit dans l'outil probe, PAS ici : le polling appartient à l'appelant (spec §3).
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from emule_indexer.domain.observation import FileObservation


class SearchChannel(StrEnum):
    """Canal de recherche (enum fermé, spec §4) : serveurs eD2k ou Kad."""

    GLOBAL = "global"
    KAD = "kad"


class KadStatus(StrEnum):
    """État Kad (enum fermé), décodé du bitfield CONNSTATE (réf. protocole §6)."""

    OFF = "off"
    RUNNING = "running"
    CONNECTED = "connected"
    FIREWALLED = "firewalled"


@dataclass(frozen=True)
class NetworkStatus:
    """Statut réseau (spec §4) — exactement ce que les métriques (§13 MVP) consommeront.

    ``ed2k_id`` est ``None`` quand le client n'est pas connecté à un serveur eD2k.
    ``ed2k_high`` : LowID si l'ID < 16777216 (HIGHEST_LOWID_ED2K_KAD, réf. §6).
    """

    ed2k_id: int | None
    ed2k_high: bool
    kad_status: KadStatus
    server_name: str | None = None
    server_addr: str | None = None


class MuleClient(Protocol):
    """Contrat async du client eMule. Actions UNITAIRES : aucun sleep/retry/boucle ici.

    ``fetch_results`` retourne le snapshot CUMULATIF accumulé par le daemon (réf. §5) ;
    ``search_progress`` retourne un pourcentage si EC l'expose, sinon ``None``.
    """

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def start_search(self, keyword: str, channel: SearchChannel) -> None: ...

    async def fetch_results(self) -> tuple[FileObservation, ...]: ...

    async def stop_search(self) -> None: ...

    async def search_progress(self) -> int | None: ...

    async def network_status(self) -> NetworkStatus: ...
```

- [ ] **Step 4: Lancer pour vérifier que tout passe**

Run: `uv run pytest tests/ports/test_mule_client.py --no-cov -q`
Expected: PASS — 5 tests verts, dont le test async (preuve que pytest-asyncio strict est opérationnel).

- [ ] **Step 5: Vérifier la suite complète + types + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert ; coverage 100 % (les stubs une-ligne du Protocol sont couverts par l'import).

- [ ] **Step 6: Commit**

```bash
git add src/emule_indexer/ports tests/ports
git commit -m "feat(ports): MuleClient async protocol + NetworkStatus/SearchChannel/KadStatus"
```

---

## Task 4: Adapter — constantes protocole (`codes.py`) + hiérarchie d'erreurs (`errors.py`)

**Files:**
- Create: `src/emule_indexer/adapters/mule_ec/__init__.py`
- Create: `src/emule_indexer/adapters/mule_ec/codes.py`
- Create: `src/emule_indexer/adapters/mule_ec/errors.py`
- Create: `tests/adapters/mule_ec/__init__.py`
- Create: `tests/adapters/mule_ec/test_codes.py`

- [ ] **Step 1: Écrire les tests qui échouent**

`tests/adapters/mule_ec/__init__.py` :
```python
"""Tests de l'adapter EC."""
```

`tests/adapters/mule_ec/test_codes.py` :
```python
import pytest

from emule_indexer.adapters.mule_ec import codes
from emule_indexer.adapters.mule_ec.errors import (
    EcAuthError,
    EcConnectError,
    EcError,
    EcFailureError,
    EcProtocolError,
    EcTimeoutError,
)


def test_protocol_version_and_flags_match_reference() -> None:
    # docs/reference/ec-protocol.md §7 (source : ECCodes.h 2.3.3).
    assert codes.EC_CURRENT_PROTOCOL_VERSION == 0x0204
    assert codes.EC_FLAG_BASE == 0x20
    assert codes.EC_FLAG_ZLIB == 0x00000001
    assert codes.EC_FLAG_UTF8_NUMBERS == 0x00000002
    assert codes.EC_FLAG_UNKNOWN_MASK == 0xFF7F7F08


def test_auth_opcodes_and_tags_match_reference() -> None:
    assert codes.EC_OP_AUTH_REQ == 0x02
    assert codes.EC_OP_AUTH_FAIL == 0x03
    assert codes.EC_OP_AUTH_OK == 0x04
    assert codes.EC_OP_AUTH_SALT == 0x4F
    assert codes.EC_OP_AUTH_PASSWD == 0x50
    assert codes.EC_TAG_PASSWD_HASH == 0x0001
    assert codes.EC_TAG_PROTOCOL_VERSION == 0x0002
    assert codes.EC_TAG_PASSWD_SALT == 0x000B
    assert codes.EC_TAG_CLIENT_NAME == 0x0100
    assert codes.EC_TAG_CLIENT_VERSION == 0x0101
    assert codes.EC_TAG_SERVER_VERSION == 0x050B


def test_search_opcodes_and_tags_match_reference() -> None:
    assert codes.EC_OP_SEARCH_START == 0x26
    assert codes.EC_OP_SEARCH_STOP == 0x27
    assert codes.EC_OP_SEARCH_RESULTS == 0x28
    assert codes.EC_OP_SEARCH_PROGRESS == 0x29
    assert codes.EC_OP_STRINGS == 0x06
    assert codes.EC_OP_FAILED == 0x05
    assert codes.EC_OP_MISC_DATA == 0x07
    assert codes.EC_SEARCH_GLOBAL == 0x01
    assert codes.EC_SEARCH_KAD == 0x02
    assert codes.EC_TAG_SEARCHFILE == 0x0700
    assert codes.EC_TAG_SEARCH_TYPE == 0x0701
    assert codes.EC_TAG_SEARCH_NAME == 0x0702
    assert codes.EC_TAG_SEARCH_FILE_TYPE == 0x0705
    assert codes.EC_TAG_SEARCH_STATUS == 0x0708


def test_partfile_result_tags_match_reference() -> None:
    assert codes.EC_TAG_PARTFILE_NAME == 0x0301
    assert codes.EC_TAG_PARTFILE_SIZE_FULL == 0x0303
    assert codes.EC_TAG_PARTFILE_HASH == 0x031E
    assert codes.EC_TAG_PARTFILE_SOURCE_COUNT == 0x030A
    assert codes.EC_TAG_PARTFILE_SOURCE_COUNT_XFER == 0x030D
    assert codes.EC_TAG_PARTFILE_STATUS == 0x0308


def test_connstate_tags_and_bits_match_reference() -> None:
    assert codes.EC_OP_GET_CONNSTATE == 0x0B
    assert codes.EC_TAG_CONNSTATE == 0x0005
    assert codes.EC_TAG_ED2K_ID == 0x0006
    assert codes.EC_TAG_CLIENT_ID == 0x000A
    assert codes.EC_TAG_SERVER == 0x0500
    assert codes.EC_TAG_SERVER_NAME == 0x0501
    assert codes.EC_TAG_KAD_ID == 0x0010
    assert codes.EC_TAG_DETAIL_LEVEL == 0x0004
    assert codes.EC_DETAIL_CMD == 0x00
    assert codes.CONNSTATE_CONNECTED_ED2K == 0x01
    assert codes.CONNSTATE_CONNECTING_ED2K == 0x02
    assert codes.CONNSTATE_CONNECTED_KAD == 0x04
    assert codes.CONNSTATE_KAD_FIREWALLED == 0x08
    assert codes.CONNSTATE_KAD_RUNNING == 0x10


def test_tag_types_match_reference() -> None:
    # Réf. §3 (ECTagTypes.h).
    assert codes.EC_TAGTYPE_CUSTOM == 0x01
    assert codes.EC_TAGTYPE_UINT8 == 0x02
    assert codes.EC_TAGTYPE_UINT16 == 0x03
    assert codes.EC_TAGTYPE_UINT32 == 0x04
    assert codes.EC_TAGTYPE_UINT64 == 0x05
    assert codes.EC_TAGTYPE_STRING == 0x06
    assert codes.EC_TAGTYPE_DOUBLE == 0x07
    assert codes.EC_TAGTYPE_IPV4 == 0x08
    assert codes.EC_TAGTYPE_HASH16 == 0x09
    assert codes.EC_TAGTYPE_UINT128 == 0x0A


def test_error_hierarchy_matches_spec_section_6() -> None:
    for subtype in (EcConnectError, EcAuthError, EcProtocolError, EcTimeoutError, EcFailureError):
        assert issubclass(subtype, EcError)
        assert issubclass(subtype, Exception)
    # EcFailureError (échec applicatif) est DISTINCTE d'EcProtocolError (trame illisible).
    assert not issubclass(EcFailureError, EcProtocolError)
    with pytest.raises(EcError):
        raise EcAuthError("Invalid password")
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/adapters/mule_ec/test_codes.py --no-cov -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'emule_indexer.adapters.mule_ec'`.

- [ ] **Step 3: Écrire l'implémentation**

`src/emule_indexer/adapters/mule_ec/__init__.py` :
```python
"""Adapter EC (External Connections) : pilotage binaire d'amuled.

Personne d'autre que cet adapter (et le probe, frontière assumée) ne voit un opcode.
"""
```

`src/emule_indexer/adapters/mule_ec/errors.py` :
```python
"""Hiérarchie d'erreurs de l'adapter EC (cf. spec EC-adapter §6).

L'adapter SIGNALE, il ne décide pas : pas de retry caché, pas de crash silencieux. Cette
hiérarchie permet à l'appelant (plan C) de distinguer « amuled est down » (EcConnectError)
de « ma config est fausse » (EcAuthError), une trame illisible (EcProtocolError) d'un échec
applicatif proprement signalé par le daemon (EcFailureError).
"""


class EcError(Exception):
    """Base de toutes les erreurs de l'adapter EC."""


class EcConnectError(EcError):
    """TCP refusé, connexion perdue, ou opération sans connexion établie."""


class EcAuthError(EcError):
    """Authentification refusée (mot de passe ou version de protocole)."""


class EcProtocolError(EcError):
    """Trame malformée ou réponse inattendue (l'entrée réseau est non fiable)."""


class EcTimeoutError(EcError):
    """Délai dépassé (lecture réseau ou établissement de connexion)."""


class EcFailureError(EcError):
    """Échec applicatif signalé par le daemon (EC_OP_FAILED) ; porte son message."""
```

`src/emule_indexer/adapters/mule_ec/codes.py` :
```python
"""Constantes du protocole EC, transcrites de docs/reference/ec-protocol.md §7.

Source amont : ``src/libs/ec/cpp/ECCodes.h`` + ``ECTagTypes.h`` (aMule tag 2.3.3 ;
identiques en 3.0.0 sauf mention ✦). Les noms de tags sont les noms LOGIQUES : sur le
fil on transmet ``(nom << 1) | enfants`` (réf. §2) — le décalage vit dans codec.py.
"""

from typing import Final

# --- Protocole & flags (réf. §1, §7) -------------------------------------------------
EC_CURRENT_PROTOCOL_VERSION: Final[int] = 0x0204
EC_FLAG_BASE: Final[int] = 0x20  # bit de base TOUJOURS présent (m_my_flags(0x20), ECSocket.cpp:275)
EC_FLAG_ZLIB: Final[int] = 0x00000001
EC_FLAG_UTF8_NUMBERS: Final[int] = 0x00000002
EC_FLAG_UNKNOWN_MASK: Final[int] = 0xFF7F7F08

# --- Opcodes (réf. §7) ----------------------------------------------------------------
EC_OP_NOOP: Final[int] = 0x01
EC_OP_AUTH_REQ: Final[int] = 0x02
EC_OP_AUTH_FAIL: Final[int] = 0x03
EC_OP_AUTH_OK: Final[int] = 0x04
EC_OP_FAILED: Final[int] = 0x05
EC_OP_STRINGS: Final[int] = 0x06
EC_OP_MISC_DATA: Final[int] = 0x07
EC_OP_STAT_REQ: Final[int] = 0x0A
EC_OP_GET_CONNSTATE: Final[int] = 0x0B
EC_OP_STATS: Final[int] = 0x0C
EC_OP_SEARCH_START: Final[int] = 0x26
EC_OP_SEARCH_STOP: Final[int] = 0x27
EC_OP_SEARCH_RESULTS: Final[int] = 0x28
EC_OP_SEARCH_PROGRESS: Final[int] = 0x29
EC_OP_DOWNLOAD_SEARCH_RESULT: Final[int] = 0x2A
EC_OP_SERVER_DISCONNECT: Final[int] = 0x2E
EC_OP_SERVER_CONNECT: Final[int] = 0x2F
EC_OP_KAD_START: Final[int] = 0x48
EC_OP_KAD_STOP: Final[int] = 0x49
EC_OP_AUTH_SALT: Final[int] = 0x4F
EC_OP_AUTH_PASSWD: Final[int] = 0x50

# --- Niveaux de détail & types de recherche (réf. §5, §7) ------------------------------
EC_DETAIL_CMD: Final[int] = 0x00
EC_DETAIL_WEB: Final[int] = 0x01
EC_DETAIL_FULL: Final[int] = 0x02
EC_DETAIL_UPDATE: Final[int] = 0x03
EC_DETAIL_INC_UPDATE: Final[int] = 0x04
EC_SEARCH_LOCAL: Final[int] = 0x00
EC_SEARCH_GLOBAL: Final[int] = 0x01
EC_SEARCH_KAD: Final[int] = 0x02
EC_SEARCH_WEB: Final[int] = 0x03  # refusé par le serveur (réf. §5)

# --- Tags (noms logiques, réf. §7) ------------------------------------------------------
EC_TAG_STRING: Final[int] = 0x0000
EC_TAG_PASSWD_HASH: Final[int] = 0x0001
EC_TAG_PROTOCOL_VERSION: Final[int] = 0x0002
EC_TAG_VERSION_ID: Final[int] = 0x0003  # builds SVN uniquement ; INTERDIT face à une release
EC_TAG_DETAIL_LEVEL: Final[int] = 0x0004
EC_TAG_CONNSTATE: Final[int] = 0x0005
EC_TAG_ED2K_ID: Final[int] = 0x0006
EC_TAG_CLIENT_ID: Final[int] = 0x000A
EC_TAG_PASSWD_SALT: Final[int] = 0x000B
EC_TAG_CAN_ZLIB: Final[int] = 0x000C
EC_TAG_CAN_UTF8_NUMBERS: Final[int] = 0x000D
EC_TAG_CAN_NOTIFY: Final[int] = 0x000E
EC_TAG_KAD_ID: Final[int] = 0x0010
EC_TAG_CAN_LARGE_TAG_COUNT: Final[int] = 0x0011  # ✦ 3.0.0
EC_TAG_CAN_PARTIAL_UPDATE: Final[int] = 0x0012  # ✦ 3.0.0
EC_TAG_CLIENT_NAME: Final[int] = 0x0100
EC_TAG_CLIENT_VERSION: Final[int] = 0x0101
EC_TAG_STATS_UL_SPEED: Final[int] = 0x0200
EC_TAG_STATS_DL_SPEED: Final[int] = 0x0201
EC_TAG_STATS_UL_SPEED_LIMIT: Final[int] = 0x0202
EC_TAG_STATS_DL_SPEED_LIMIT: Final[int] = 0x0203
EC_TAG_STATS_TOTAL_SRC_COUNT: Final[int] = 0x0206
EC_TAG_STATS_UL_QUEUE_LEN: Final[int] = 0x0208
EC_TAG_STATS_ED2K_USERS: Final[int] = 0x0209
EC_TAG_STATS_KAD_USERS: Final[int] = 0x020A
EC_TAG_STATS_ED2K_FILES: Final[int] = 0x020B
EC_TAG_STATS_KAD_FILES: Final[int] = 0x020C
EC_TAG_PARTFILE: Final[int] = 0x0300
EC_TAG_PARTFILE_NAME: Final[int] = 0x0301
EC_TAG_PARTFILE_SIZE_FULL: Final[int] = 0x0303
EC_TAG_PARTFILE_STATUS: Final[int] = 0x0308
EC_TAG_PARTFILE_SOURCE_COUNT: Final[int] = 0x030A
EC_TAG_PARTFILE_SOURCE_COUNT_XFER: Final[int] = 0x030D  # = sources COMPLÈTES (réf. §9 piège 12)
EC_TAG_PARTFILE_CAT: Final[int] = 0x030F
EC_TAG_PARTFILE_HASH: Final[int] = 0x031E
EC_TAG_KNOWNFILE_RATING: Final[int] = 0x040F  # ✦ 3.0.0
EC_TAG_SERVER: Final[int] = 0x0500
EC_TAG_SERVER_NAME: Final[int] = 0x0501
EC_TAG_SERVER_VERSION: Final[int] = 0x050B
EC_TAG_SEARCHFILE: Final[int] = 0x0700
EC_TAG_SEARCH_TYPE: Final[int] = 0x0701
EC_TAG_SEARCH_NAME: Final[int] = 0x0702
EC_TAG_SEARCH_MIN_SIZE: Final[int] = 0x0703
EC_TAG_SEARCH_MAX_SIZE: Final[int] = 0x0704
EC_TAG_SEARCH_FILE_TYPE: Final[int] = 0x0705
EC_TAG_SEARCH_EXTENSION: Final[int] = 0x0706
EC_TAG_SEARCH_AVAILABILITY: Final[int] = 0x0707
EC_TAG_SEARCH_STATUS: Final[int] = 0x0708
EC_TAG_SEARCH_PARENT: Final[int] = 0x0709

# --- Types de valeurs (réf. §3, ECTagTypes.h) -------------------------------------------
EC_TAGTYPE_UNKNOWN: Final[int] = 0x00  # jamais émis
EC_TAGTYPE_CUSTOM: Final[int] = 0x01  # octets opaques ; aussi le type des tags vides
EC_TAGTYPE_UINT8: Final[int] = 0x02
EC_TAGTYPE_UINT16: Final[int] = 0x03
EC_TAGTYPE_UINT32: Final[int] = 0x04
EC_TAGTYPE_UINT64: Final[int] = 0x05
EC_TAGTYPE_STRING: Final[int] = 0x06  # UTF-8 + NUL final INCLUS dans TAGLEN
EC_TAGTYPE_DOUBLE: Final[int] = 0x07  # représentation texte + NUL
EC_TAGTYPE_IPV4: Final[int] = 0x08  # 4 octets IP + port uint16 big-endian
EC_TAGTYPE_HASH16: Final[int] = 0x09  # 16 octets bruts MSB first (MD4/MD5)
EC_TAGTYPE_UINT128: Final[int] = 0x0A  # 16 octets big-endian (ID Kad)

# --- Bitfield EC_TAG_CONNSTATE (réf. §6) ------------------------------------------------
CONNSTATE_CONNECTED_ED2K: Final[int] = 0x01
CONNSTATE_CONNECTING_ED2K: Final[int] = 0x02
CONNSTATE_CONNECTED_KAD: Final[int] = 0x04
CONNSTATE_KAD_FIREWALLED: Final[int] = 0x08
CONNSTATE_KAD_RUNNING: Final[int] = 0x10
```

- [ ] **Step 4: Lancer pour vérifier que tout passe**

Run: `uv run pytest tests/adapters/mule_ec/test_codes.py --no-cov -q`
Expected: PASS — 7 tests verts (constantes conformes à la table réf. §7, hiérarchie d'erreurs conforme à la spec §6).

- [ ] **Step 5: Vérifier la suite complète + types + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert ; coverage 100 % (modules de constantes/classes, couverts à l'import).

- [ ] **Step 6: Commit**

```bash
git add src/emule_indexer/adapters/mule_ec tests/adapters/mule_ec
git commit -m "feat(adapters): EC protocol constants (ref'd to ECCodes.h) + error hierarchy"
```

---

## Task 5: Codec — modèle `EcTag`/`EcPacket`, builders et accesseurs

**Files:**
- Create: `src/emule_indexer/adapters/mule_ec/codec.py`
- Create: `tests/adapters/mule_ec/test_codec.py`

> Le codec est PUR et SYNCHRONE (bytes ↔ arbre de tags, zéro I/O) et GÉNÉRIQUE. Cette tâche pose le modèle ; l'encodage arrive en Task 6, le décodage en Tasks 7–8 (le fichier `test_codec.py` grandit de tâche en tâche).

- [ ] **Step 1: Écrire les tests qui échouent**

`tests/adapters/mule_ec/test_codec.py` :
```python
import pytest

from emule_indexer.adapters.mule_ec import codes
from emule_indexer.adapters.mule_ec.codec import (
    EcPacket,
    EcTag,
    empty_tag,
    hash16_tag,
    string_tag,
    uint_tag,
)
from emule_indexer.adapters.mule_ec.errors import EcProtocolError

# ---------------------------------------------------------------- builders


def test_uint_tag_encodes_shortest_width_like_amule_initint() -> None:
    # Réf. §3 : « les entiers sont toujours encodés au plus court » (InitInt, ECTag.cpp:207-221).
    assert uint_tag(0x0001, 0xAB) == EcTag(0x0001, codes.EC_TAGTYPE_UINT8, b"\xab")
    assert uint_tag(0x0001, 0x0204) == EcTag(0x0001, codes.EC_TAGTYPE_UINT16, b"\x02\x04")
    assert uint_tag(0x0001, 0x02000001) == EcTag(
        0x0001, codes.EC_TAGTYPE_UINT32, b"\x02\x00\x00\x01"
    )
    assert uint_tag(0x0001, 0x6B5E8D3A12F0C4D7) == EcTag(
        0x0001, codes.EC_TAGTYPE_UINT64, b"\x6b\x5e\x8d\x3a\x12\xf0\xc4\xd7"
    )
    assert uint_tag(0x0001, 0) == EcTag(0x0001, codes.EC_TAGTYPE_UINT8, b"\x00")


def test_uint_tag_rejects_negative_and_oversized_values() -> None:
    with pytest.raises(EcProtocolError):
        uint_tag(0x0001, -1)
    with pytest.raises(EcProtocolError):
        uint_tag(0x0001, 1 << 64)


def test_string_tag_appends_the_final_nul_inside_the_value() -> None:
    # Réf. §3/§9 piège 10 : UTF-8 + NUL final INCLUS dans TAGLEN.
    tag = string_tag(codes.EC_TAG_CLIENT_NAME, "probe")
    assert tag == EcTag(codes.EC_TAG_CLIENT_NAME, codes.EC_TAGTYPE_STRING, b"probe\x00")
    assert string_tag(codes.EC_TAG_SEARCH_FILE_TYPE, "").value == b"\x00"


def test_hash16_tag_requires_exactly_16_bytes() -> None:
    digest = bytes(range(16))
    assert hash16_tag(codes.EC_TAG_PASSWD_HASH, digest) == EcTag(
        codes.EC_TAG_PASSWD_HASH, codes.EC_TAGTYPE_HASH16, digest
    )
    with pytest.raises(EcProtocolError):
        hash16_tag(codes.EC_TAG_PASSWD_HASH, b"\x00" * 15)


def test_empty_tag_is_custom_type_with_no_value() -> None:
    # Réf. §2 : CECEmptyTag -> type CUSTOM (1), TAGLEN 0 — la forme des tags EC_TAG_CAN_*.
    assert empty_tag(codes.EC_TAG_CAN_ZLIB) == EcTag(
        codes.EC_TAG_CAN_ZLIB, codes.EC_TAGTYPE_CUSTOM, b""
    )


# ---------------------------------------------------------------- accesseurs


def test_int_value_reads_all_four_widths() -> None:
    # Réf. §9 piège 4 : lire « un entier » en acceptant les 4 largeurs (équivalent GetInt()).
    assert EcTag(0x000B, codes.EC_TAGTYPE_UINT8, b"\xab").int_value() == 0xAB
    assert EcTag(0x000B, codes.EC_TAGTYPE_UINT16, b"\x02\x04").int_value() == 0x0204
    assert EcTag(0x000B, codes.EC_TAGTYPE_UINT32, b"\x02\x00\x00\x01").int_value() == 0x02000001
    assert (
        EcTag(0x000B, codes.EC_TAGTYPE_UINT64, b"\x6b\x5e\x8d\x3a\x12\xf0\xc4\xd7").int_value()
        == 0x6B5E8D3A12F0C4D7
    )


def test_int_value_rejects_non_int_type_and_lying_width() -> None:
    with pytest.raises(EcProtocolError):
        EcTag(0x000B, codes.EC_TAGTYPE_STRING, b"12\x00").int_value()
    with pytest.raises(EcProtocolError):
        EcTag(0x000B, codes.EC_TAGTYPE_UINT32, b"\x01\x02").int_value()  # largeur menteuse


def test_string_value_strips_the_final_nul_and_never_crashes_on_hostile_bytes() -> None:
    assert EcTag(0x0000, codes.EC_TAGTYPE_STRING, b"keroro\x00").string_value() == "keroro"
    # Octets non-UTF-8 dans un nom hostile : remplacés, jamais d'exception (errors="replace").
    hostile = EcTag(0x0000, codes.EC_TAGTYPE_STRING, b"\xff\xfe\x00")
    assert "�" in hostile.string_value()


def test_string_value_rejects_wrong_type_or_missing_nul() -> None:
    with pytest.raises(EcProtocolError):
        EcTag(0x0000, codes.EC_TAGTYPE_UINT8, b"\x01").string_value()
    with pytest.raises(EcProtocolError):
        EcTag(0x0000, codes.EC_TAGTYPE_STRING, b"sans-nul").string_value()


def test_ipv4_value_renders_ip_and_port() -> None:
    # Réf. §3 : 6 octets = 4 octets d'IP + port uint16 big-endian (ECTag.cpp:108-116).
    tag = EcTag(
        codes.EC_TAG_SERVER, codes.EC_TAGTYPE_IPV4, bytes([1, 2, 3, 4]) + (4661).to_bytes(2, "big")
    )
    assert tag.ipv4_value() == "1.2.3.4:4661"


def test_ipv4_value_rejects_wrong_type_or_length() -> None:
    with pytest.raises(EcProtocolError):
        bad_type = EcTag(codes.EC_TAG_SERVER, codes.EC_TAGTYPE_CUSTOM, b"\x01\x02\x03\x04\x12\x35")
        bad_type.ipv4_value()
    with pytest.raises(EcProtocolError):
        EcTag(codes.EC_TAG_SERVER, codes.EC_TAGTYPE_IPV4, b"\x01\x02\x03\x04").ipv4_value()


def test_find_returns_first_child_by_logical_name_or_none() -> None:
    child_a = uint_tag(0x030A, 5)
    child_b = uint_tag(0x030D, 2)
    parent = EcTag(0x0700, codes.EC_TAGTYPE_UINT8, b"\x01", (child_a, child_b))
    assert parent.find(0x030D) is child_b
    assert parent.find(0x9999) is None


def test_packet_find_returns_first_top_level_tag_or_none() -> None:
    tag = string_tag(codes.EC_TAG_STRING, "ok")
    packet = EcPacket(codes.EC_OP_STRINGS, (tag,))
    assert packet.find(codes.EC_TAG_STRING) is tag
    assert packet.find(codes.EC_TAG_CONNSTATE) is None
    assert EcPacket(codes.EC_OP_NOOP).tags == ()
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/adapters/mule_ec/test_codec.py --no-cov -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'emule_indexer.adapters.mule_ec.codec'`.

- [ ] **Step 3: Écrire l'implémentation**

`src/emule_indexer/adapters/mule_ec/codec.py` :
```python
"""Codec EC PUR et SYNCHRONE : bytes ↔ arbre de tags (cf. docs/reference/ec-protocol.md §1-§3).

GÉNÉRIQUE : encode/décode N'IMPORTE QUEL paquet EC (format conteneur récursif). AUCUNE I/O.
Les noms de tags manipulés ici sont LOGIQUES ; le décalage wire ``(nom << 1) | enfants``
(réf. §2, piège 2) est enfermé dans l'encodage/décodage (Tasks 6-8).
"""

from dataclasses import dataclass
from typing import Final

from emule_indexer.adapters.mule_ec import codes
from emule_indexer.adapters.mule_ec.errors import EcProtocolError

# Largeur (octets) de chaque type entier — réf. §3. Ordre croissant : uint_tag prend le 1er
# qui suffit (« encodé au plus court », InitInt, ECTag.cpp:207-221).
INT_WIDTHS: Final[dict[int, int]] = {
    codes.EC_TAGTYPE_UINT8: 1,
    codes.EC_TAGTYPE_UINT16: 2,
    codes.EC_TAGTYPE_UINT32: 4,
    codes.EC_TAGTYPE_UINT64: 8,
}


@dataclass(frozen=True)
class EcTag:
    """Un tag EC : nom LOGIQUE (déjà ``>> 1``), type, valeur propre, sous-tags."""

    name: int
    tag_type: int
    value: bytes = b""
    children: tuple["EcTag", ...] = ()

    def find(self, name: int) -> "EcTag | None":
        """Premier enfant portant ce nom logique, ou ``None``."""
        for child in self.children:
            if child.name == name:
                return child
        return None

    def int_value(self) -> int:
        """Valeur entière à LARGEUR VARIABLE (réf. §9 piège 4 — équivalent ``GetInt()``)."""
        if self.tag_type not in INT_WIDTHS or len(self.value) != INT_WIDTHS[self.tag_type]:
            raise EcProtocolError(f"tag 0x{self.name:04X} : pas un entier EC valide")
        return int.from_bytes(self.value, "big")

    def string_value(self) -> str:
        """Valeur chaîne : UTF-8 + NUL final inclus dans TAGLEN (réf. §3, piège 10).

        Décodage ``errors="replace"`` : un nom de fichier hostile ne crashe jamais
        (les octets bruts restent disponibles dans ``value``).
        """
        if self.tag_type != codes.EC_TAGTYPE_STRING or not self.value.endswith(b"\x00"):
            raise EcProtocolError(f"tag 0x{self.name:04X} : pas une chaîne EC valide")
        return self.value[:-1].decode("utf-8", errors="replace")

    def ipv4_value(self) -> str:
        """Valeur IPV4 (réf. §3) : 4 octets d'IP + port uint16 big-endian → ``"a.b.c.d:port"``."""
        if self.tag_type != codes.EC_TAGTYPE_IPV4 or len(self.value) != 6:
            raise EcProtocolError(f"tag 0x{self.name:04X} : pas un IPv4 EC valide")
        ip = ".".join(str(byte) for byte in self.value[:4])
        port = int.from_bytes(self.value[4:6], "big")
        return f"{ip}:{port}"


@dataclass(frozen=True)
class EcPacket:
    """Un paquet EC : opcode + tags de premier niveau (le paquet est un pseudo-tag, réf. §2)."""

    opcode: int
    tags: tuple[EcTag, ...] = ()

    def find(self, name: int) -> EcTag | None:
        """Premier tag de premier niveau portant ce nom logique, ou ``None``."""
        for tag in self.tags:
            if tag.name == name:
                return tag
        return None


def uint_tag(name: int, value: int, children: tuple[EcTag, ...] = ()) -> EcTag:
    """Tag entier encodé AU PLUS COURT (réf. §3 : InitInt)."""
    if value < 0:
        raise EcProtocolError(f"entier EC négatif : {value}")
    for tag_type, width in INT_WIDTHS.items():
        if value < 1 << (8 * width):
            return EcTag(name, tag_type, value.to_bytes(width, "big"), children)
    raise EcProtocolError(f"entier trop grand pour EC : {value}")


def string_tag(name: int, text: str, children: tuple[EcTag, ...] = ()) -> EcTag:
    """Tag chaîne : UTF-8 + NUL final, INCLUS dans la longueur (réf. §3, piège 10)."""
    return EcTag(name, codes.EC_TAGTYPE_STRING, text.encode("utf-8") + b"\x00", children)


def hash16_tag(name: int, digest: bytes, children: tuple[EcTag, ...] = ()) -> EcTag:
    """Tag hash : exactement 16 octets bruts, MSB first (réf. §3)."""
    if len(digest) != 16:
        raise EcProtocolError(f"hash EC : 16 octets attendus, reçu {len(digest)}")
    return EcTag(name, codes.EC_TAGTYPE_HASH16, digest, children)


def empty_tag(name: int, children: tuple[EcTag, ...] = ()) -> EcTag:
    """Tag vide (CECEmptyTag, réf. §2) : type CUSTOM, TAGLEN 0 — forme des tags ``CAN_*``."""
    return EcTag(name, codes.EC_TAGTYPE_CUSTOM, b"", children)
```

*(Pas d'import `zlib` ici : il n'arrive qu'avec le décodage borné, Task 7 — un import inutilisé ferait échouer ruff F401.)*

- [ ] **Step 4: Lancer pour vérifier que tout passe**

Run: `uv run pytest tests/adapters/mule_ec/test_codec.py --no-cov -q`
Expected: PASS — 13 tests verts (builders : 4 largeurs, négatif, trop grand, NUL final, 16 octets ; accesseurs : 4 largeurs en lecture, types/largeurs menteurs, NUL manquant, IPv4, `find` trouvé/absent sur tag ET paquet).

- [ ] **Step 5: Vérifier la suite complète + types + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert ; coverage 100 % (chaque `if` des accesseurs/builders exercé des deux côtés ; F401 résolu selon la note du Step 3).

- [ ] **Step 6: Commit**

```bash
git add src/emule_indexer/adapters/mule_ec/codec.py tests/adapters/mule_ec/test_codec.py
git commit -m "feat(adapters): EC tag model, builders and variable-width accessors"
```

---

## Task 6: Codec — encodage wire (TAGLEN récursif, tag, paquet, en-tête)

**Files:**
- Modify: `src/emule_indexer/adapters/mule_ec/codec.py`
- Modify: `tests/adapters/mule_ec/test_codec.py`

- [ ] **Step 1: Ajouter les tests qui échouent**

Ajouter `encode_packet` au bloc d'import `codec` existant de `tests/adapters/mule_ec/test_codec.py` (UN SEUL `from ... import (...)` trié — une ligne d'import séparée du même module casserait ruff I001) :
```python
from emule_indexer.adapters.mule_ec.codec import (
    EcPacket,
    EcTag,
    empty_tag,
    encode_packet,
    hash16_tag,
    string_tag,
    uint_tag,
)
```

Puis ajouter à la fin du fichier :
```python
# ---------------------------------------------------------------- encodage

# Trame AUTH_REQ de référence, dérivée OCTET PAR OCTET de la réf. §1/§2/§4 :
#   en-tête 8 octets : flags=0x00000020 (base seule, DÉCISION 2), length=0x00000024 (36)
#   payload : opcode 0x02 (EC_OP_AUTH_REQ) ; TAGCOUNT 0x0003
#     tag1 : TAGNAME 0x0200 (= 0x0100 CLIENT_NAME << 1, bit enfants 0), TAGTYPE 0x06 (STRING),
#            TAGLEN 0x00000006, valeur "probe\0" (NUL inclus, piège 10)
#     tag2 : TAGNAME 0x0202 (= 0x0101 CLIENT_VERSION << 1), STRING, TAGLEN 4, "1.0\0"
#     tag3 : TAGNAME 0x0004 (= 0x0002 PROTOCOL_VERSION << 1), TAGTYPE 0x03 (UINT16 :
#            0x0204 émis au plus court), TAGLEN 2, valeur 0x0204
# Le groupement par champ EST la dérivation ; ruff format recollerait les chaînes.
# fmt: off
_AUTH_REQ_FRAME = bytes.fromhex(
    "00000020" "00000024"
    "02" "0003"
    "0200" "06" "00000006" "70726f626500"
    "0202" "06" "00000004" "312e3000"
    "0004" "03" "00000002" "0204"
)
# fmt: on


def _auth_req_packet() -> EcPacket:
    return EcPacket(
        codes.EC_OP_AUTH_REQ,
        (
            string_tag(codes.EC_TAG_CLIENT_NAME, "probe"),
            string_tag(codes.EC_TAG_CLIENT_VERSION, "1.0"),
            uint_tag(codes.EC_TAG_PROTOCOL_VERSION, codes.EC_CURRENT_PROTOCOL_VERSION),
        ),
    )


def test_encode_packet_produces_the_exact_auth_req_frame() -> None:
    assert encode_packet(_auth_req_packet()) == _AUTH_REQ_FRAME


# Trame SEARCH_RESULTS imbriquée, dérivée de la réf. §2 (TAGLEN, piège 3) et §5 :
#   parent : TAGNAME 0x0E01 (= 0x0700 SEARCHFILE << 1 | 1 enfants), TAGTYPE 0x02 (UINT8 :
#            ECID=1 émis au plus court), TAGLEN 0x52 (82), TAGCOUNT 0x0006
#   TAGLEN parent = valeur propre (1) + Σ enfants (TAGLEN + 7 d'en-tête chacun, aucun
#   petit-enfant donc pas de +2) = 1 + (16+7)+(4+7)+(16+7)+(1+7)+(1+7)+(1+7) = 82
#   enfants (TAGNAME = nom << 1) :
#     0x0602 (=0x0301 NAME) STRING  len 16 : "Keroro 062A.avi\0"
#     0x0606 (=0x0303 SIZE_FULL) UINT32 len 4 : 234567890 = 0x0DFB38D2
#     0x063C (=0x031E HASH) HASH16 len 16 : 000102...0f
#     0x0614 (=0x030A SOURCE_COUNT) UINT8 len 1 : 5
#     0x061A (=0x030D SOURCE_COUNT_XFER) UINT8 len 1 : 2
#     0x1332 (=0x0999 tag INCONNU forgé) UINT8 len 1 : 7
#   payload = opcode(1) + tagcount(2) + en-tête parent(7) + TAGCOUNT parent(2) + 82 = 94 = 0x5E
# fmt: off
_SEARCH_RESULT_FRAME = bytes.fromhex(
    "00000020" "0000005e"
    "28" "0001"
    "0e01" "02" "00000052" "0006"
    "0602" "06" "00000010" "4b65726f726f20303632412e61766900"
    "0606" "04" "00000004" "0dfb38d2"
    "063c" "09" "00000010" "000102030405060708090a0b0c0d0e0f"
    "0614" "02" "00000001" "05"
    "061a" "02" "00000001" "02"
    "1332" "02" "00000001" "07"
    "01"
)
# fmt: on


def _search_result_packet() -> EcPacket:
    entry = EcTag(
        codes.EC_TAG_SEARCHFILE,
        codes.EC_TAGTYPE_UINT8,
        b"\x01",  # ECID (identifiant de session VOLATIL, piège 13 — jamais persisté)
        (
            string_tag(codes.EC_TAG_PARTFILE_NAME, "Keroro 062A.avi"),
            uint_tag(codes.EC_TAG_PARTFILE_SIZE_FULL, 234567890),
            hash16_tag(codes.EC_TAG_PARTFILE_HASH, bytes(range(16))),
            uint_tag(codes.EC_TAG_PARTFILE_SOURCE_COUNT, 5),
            uint_tag(codes.EC_TAG_PARTFILE_SOURCE_COUNT_XFER, 2),
            uint_tag(0x0999, 7),  # tag inconnu : doit voyager sans erreur (capture-all)
        ),
    )
    return EcPacket(codes.EC_OP_SEARCH_RESULTS, (entry,))


def test_encode_packet_handles_children_taglen_and_tagcount() -> None:
    # Vérifie le piège 3 : TAGLEN parent inclut en-têtes des enfants, PAS son propre TAGCOUNT.
    assert encode_packet(_search_result_packet()) == _SEARCH_RESULT_FRAME


def test_encode_packet_with_no_tags_is_the_minimal_frame() -> None:
    # NOOP sans tag : payload = opcode (1) + TAGCOUNT 0x0000 (2) = 3 octets.
    expected = bytes.fromhex("00000020" "00000003" "01" "0000")  # fmt: skip
    assert encode_packet(EcPacket(codes.EC_OP_NOOP)) == expected
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/adapters/mule_ec/test_codec.py --no-cov -q`
Expected: FAIL — `ImportError: cannot import name 'encode_packet' from 'emule_indexer.adapters.mule_ec.codec'`.

- [ ] **Step 3: Écrire l'implémentation**

Ajouter à la fin de `src/emule_indexer/adapters/mule_ec/codec.py` :
```python
_TAG_HEADER_SIZE = 7  # TAGNAME (2) + TAGTYPE (1) + TAGLEN (4) — réf. §2
_TAGCOUNT_SIZE = 2  # uint16, présent UNIQUEMENT si le bit 0 du TAGNAME est à 1


def _tag_len(tag: EcTag) -> int:
    """TAGLEN (réf. §2, GetTagLen, ECTag.cpp:553-561) : valeur propre + taille sérialisée
    COMPLÈTE de chaque enfant (son TAGLEN + ses 7 octets d'en-tête + ses 2 octets de
    TAGCOUNT s'il a lui-même des enfants). EXCLUT l'en-tête et le TAGCOUNT du tag lui-même."""
    return len(tag.value) + sum(_serialized_len(child) for child in tag.children)


def _serialized_len(tag: EcTag) -> int:
    """Taille sérialisée complète d'un tag (ce que son PARENT compte dans son TAGLEN)."""
    return _TAG_HEADER_SIZE + (_TAGCOUNT_SIZE if tag.children else 0) + _tag_len(tag)


def _encode_tag(tag: EcTag) -> bytes:
    """Sérialise un tag : TAGNAME décalé, type, TAGLEN, [TAGCOUNT + enfants], valeur (réf. §2)."""
    wire_name = (tag.name << 1) | (1 if tag.children else 0)
    out = wire_name.to_bytes(2, "big") + bytes([tag.tag_type]) + _tag_len(tag).to_bytes(4, "big")
    if tag.children:
        out += len(tag.children).to_bytes(2, "big")
        for child in tag.children:
            out += _encode_tag(child)
    return out + tag.value  # sous-tags AVANT la valeur propre (réf. §2)


def encode_packet(packet: EcPacket) -> bytes:
    """Trame complète : en-tête 8 octets (flags 0x20, length) + opcode + TAGCOUNT + tags.

    DÉCISION 2 : on n'annonce aucune capacité → on émet TOUJOURS ``flags = 0x20`` (ni zlib
    ni nombres UTF-8) ; l'opcode et les compteurs sont donc bruts (réf. §1).
    """
    payload = bytes([packet.opcode]) + len(packet.tags).to_bytes(2, "big")
    for tag in packet.tags:
        payload += _encode_tag(tag)
    return codes.EC_FLAG_BASE.to_bytes(4, "big") + len(payload).to_bytes(4, "big") + payload
```

- [ ] **Step 4: Lancer pour vérifier que tout passe**

Run: `uv run pytest tests/adapters/mule_ec/test_codec.py --no-cov -q`
Expected: PASS — les trames AUTH_REQ et SEARCH_RESULTS sortent OCTET POUR OCTET comme dérivées de la référence (TAGLEN 82 du parent compris), trame minimale NOOP correcte.

- [ ] **Step 5: Vérifier la suite complète + types + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert ; coverage 100 % (`_encode_tag` avec ET sans enfants exercé via les deux fixtures).

- [ ] **Step 6: Commit**

```bash
git add src/emule_indexer/adapters/mule_ec/codec.py tests/adapters/mule_ec/test_codec.py
git commit -m "feat(adapters): EC wire encoding (shifted TAGNAME, recursive TAGLEN, 0x20 header)"
```

---

## Task 7: Codec — décodage nominal + round-trip

**Files:**
- Modify: `src/emule_indexer/adapters/mule_ec/codec.py`
- Modify: `tests/adapters/mule_ec/test_codec.py`

- [ ] **Step 1: Ajouter les tests qui échouent**

Ajouter `decode_header` et `decode_packet` au bloc d'import `codec` existant de `tests/adapters/mule_ec/test_codec.py` (toujours UN SEUL import trié du module — pas de ligne séparée) :
```python
from emule_indexer.adapters.mule_ec.codec import (
    EcPacket,
    EcTag,
    decode_header,
    decode_packet,
    empty_tag,
    encode_packet,
    hash16_tag,
    string_tag,
    uint_tag,
)
```

Puis ajouter à la fin du fichier :
```python
# ---------------------------------------------------------------- décodage nominal


def test_decode_header_accepts_base_and_base_zlib_flags() -> None:
    assert decode_header(bytes.fromhex("00000020" "00000003")) == (0x20, 3)  # fmt: skip
    assert decode_header(bytes.fromhex("00000021" "00000010")) == (0x21, 16)  # fmt: skip


def test_decode_packet_rebuilds_the_auth_req_tree() -> None:
    assert decode_packet(_AUTH_REQ_FRAME) == _auth_req_packet()


def test_decode_packet_rebuilds_the_nested_search_result_tree() -> None:
    # Piège 2 (TAGNAME >> 1) et piège 3 (valeur propre = TAGLEN - Σ enfants) traversés.
    assert decode_packet(_SEARCH_RESULT_FRAME) == _search_result_packet()


def test_decode_packet_minimal_noop() -> None:
    frame = bytes.fromhex("00000020" "00000003" "01" "0000")  # fmt: skip
    assert decode_packet(frame) == EcPacket(codes.EC_OP_NOOP)


def test_roundtrip_encode_decode_is_identity_on_forged_packets() -> None:
    # Round-trip sur un éventail de formes : tags vides, toutes largeurs d'entiers, hash,
    # chaînes accentuées, imbrication à 3 niveaux, valeur propre + enfants simultanés.
    deep = EcTag(
        0x0700,
        codes.EC_TAGTYPE_UINT16,
        b"\x12\x34",
        (
            empty_tag(codes.EC_TAG_CAN_ZLIB),
            string_tag(0x0301, "épisode 062A — « démo »"),
            EcTag(
                0x0500,
                codes.EC_TAGTYPE_IPV4,
                bytes([10, 0, 0, 1]) + (4712).to_bytes(2, "big"),
                (string_tag(0x0501, "serveur"),),
            ),
        ),
    )
    packets = [
        EcPacket(codes.EC_OP_NOOP),
        _auth_req_packet(),
        _search_result_packet(),
        EcPacket(
            codes.EC_OP_MISC_DATA,
            (
                deep,
                uint_tag(0x0001, 0),
                uint_tag(0x0002, 0xFFFF),
                uint_tag(0x0003, 0xFFFFFFFF),
                uint_tag(0x0004, (1 << 64) - 1),
                hash16_tag(0x031E, bytes(range(16))),
            ),
        ),
    ]
    for packet in packets:
        assert decode_packet(encode_packet(packet)) == packet
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/adapters/mule_ec/test_codec.py --no-cov -q`
Expected: FAIL — `ImportError: cannot import name 'decode_header' from 'emule_indexer.adapters.mule_ec.codec'`.

- [ ] **Step 3: Écrire l'implémentation**

Ajouter `import zlib` EN TÊTE des imports de `src/emule_indexer/adapters/mule_ec/codec.py` (première ligne d'import, ordre ruff/isort : stdlib d'abord), puis ajouter à la fin du fichier (les bornes défensives — DÉCISION 3 — sont posées ici ; leurs tests hostiles arrivent en Task 8) :
```python
_HEADER_SIZE = 8  # EC_HEADER_SIZE (ECSocket.h:72), réf. §1
_MAX_PACKET_PAYLOAD = 16 * 1024 * 1024  # plafond aMule (ReadHeader, ECSocket.cpp:540)
_MAX_DECOMPRESSED = 16 * 1024 * 1024  # borne défensive sur l'inflation zlib (DÉCISION 3)
_MAX_TAG_DEPTH = 32  # borne défensive d'imbrication (DÉCISION 3)
# DÉCISION 2 : seules deux combinaisons de flags sont acceptées en lecture.
_ACCEPTED_FLAGS = (codes.EC_FLAG_BASE, codes.EC_FLAG_BASE | codes.EC_FLAG_ZLIB)


class _Reader:
    """Curseur borné sur un payload : toute lecture au-delà → ``EcProtocolError``."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    @property
    def exhausted(self) -> bool:
        return self._pos == len(self._data)

    def take(self, count: int) -> bytes:
        if self._pos + count > len(self._data):
            raise EcProtocolError("paquet EC tronqué")
        chunk = self._data[self._pos : self._pos + count]
        self._pos += count
        return chunk

    def read_u8(self) -> int:
        return self.take(1)[0]

    def read_u16(self) -> int:
        return int.from_bytes(self.take(2), "big")

    def read_u32(self) -> int:
        return int.from_bytes(self.take(4), "big")


def decode_header(header: bytes) -> tuple[int, int]:
    """En-tête fixe de 8 octets → ``(flags, length)``, validation STRICTE (réf. §1)."""
    if len(header) != _HEADER_SIZE:
        raise EcProtocolError(f"en-tête EC : 8 octets attendus, reçu {len(header)}")
    flags = int.from_bytes(header[:4], "big")
    length = int.from_bytes(header[4:], "big")
    if flags not in _ACCEPTED_FLAGS:
        raise EcProtocolError(f"flags EC refusés : 0x{flags:08X} (acceptés : 0x20, 0x21)")
    if length > _MAX_PACKET_PAYLOAD:
        raise EcProtocolError(f"longueur de paquet aberrante : {length}")
    return flags, length


def _inflate(data: bytes) -> bytes:
    """Décompression zlib BORNÉE (réf. §1 EC_FLAG_ZLIB ; spec §6 parsing défensif)."""
    decompressor = zlib.decompressobj()
    try:
        inflated = decompressor.decompress(data, _MAX_DECOMPRESSED)
    except zlib.error as exc:
        raise EcProtocolError(f"flux zlib corrompu : {exc}") from exc
    if decompressor.unconsumed_tail or not decompressor.eof:
        raise EcProtocolError("flux zlib hors borne ou tronqué")
    return inflated


def _decode_tag(reader: _Reader, depth: int) -> EcTag:
    """Décode un tag (réf. §2) : ``TAGNAME >> 1``, enfants AVANT la valeur propre,
    valeur propre = ``TAGLEN - Σ(taille sérialisée des enfants)`` (ECTag.cpp:436-438)."""
    if depth >= _MAX_TAG_DEPTH:
        raise EcProtocolError("imbrication de tags trop profonde")
    wire_name = reader.read_u16()
    name = wire_name >> 1
    has_children = bool(wire_name & 0x01)
    tag_type = reader.read_u8()
    tag_len = reader.read_u32()
    children: tuple[EcTag, ...] = ()
    children_size = 0
    if has_children:
        count = reader.read_u16()
        decoded = []
        for _ in range(count):
            child = _decode_tag(reader, depth + 1)
            children_size += _serialized_len(child)
            decoded.append(child)
        children = tuple(decoded)
    own_len = tag_len - children_size
    if own_len < 0:
        raise EcProtocolError(f"TAGLEN menteur sur le tag 0x{name:04X}")
    return EcTag(name, tag_type, reader.take(own_len), children)


def decode_payload(flags: int, payload: bytes) -> EcPacket:
    """Payload (éventuellement zlib) → ``EcPacket``. Tout octet résiduel est une erreur."""
    if flags & codes.EC_FLAG_ZLIB:
        payload = _inflate(payload)
    reader = _Reader(payload)
    opcode = reader.read_u8()
    tag_count = reader.read_u16()
    tags = tuple(_decode_tag(reader, depth=0) for _ in range(tag_count))
    if not reader.exhausted:
        raise EcProtocolError("octets résiduels après le dernier tag")
    return EcPacket(opcode, tags)


def decode_packet(frame: bytes) -> EcPacket:
    """Trame complète (en-tête + payload) → ``EcPacket`` (convenance tests/faux serveur)."""
    flags, length = decode_header(frame[:_HEADER_SIZE])
    if len(frame) != _HEADER_SIZE + length:
        raise EcProtocolError("longueur de trame incohérente avec l'en-tête")
    return decode_payload(flags, frame[_HEADER_SIZE:])
```

- [ ] **Step 4: Lancer pour vérifier que tout passe**

Run: `uv run pytest tests/adapters/mule_ec/test_codec.py --no-cov -q`
Expected: PASS — les deux trames de référence redonnent EXACTEMENT les arbres d'origine ; round-trip identité sur tout l'éventail forgé.

- [ ] **Step 5: Vérifier lint + types (PAS la suite complète)**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert. *(Le run avec coverage attendra la Task 8 : les branches hostiles de `decode_header`/`_inflate`/`_decode_tag` ne sont pas encore toutes exercées — c'est l'objet de la tâche suivante ; on committe les deux tâches ENSEMBLE seulement si le gate l'exige, sinon on committe ici en sachant que `uv run pytest -q` échouerait au seuil. Pour rester rigoureux : NE PAS committer ici, enchaîner directement la Task 8 et committer les deux fichiers en un seul commit à la fin de la Task 8.)*

---

## Task 8: Codec — entrées hostiles (tronqué, TAGLEN menteur, profondeur, flags, zlib borné)

**Files:**
- Modify: `tests/adapters/mule_ec/test_codec.py` (le code de prod de la Task 7 est déjà complet)

- [ ] **Step 1: Ajouter les tests hostiles**

Ajouter à l'import en tête de `tests/adapters/mule_ec/test_codec.py` :
```python
import zlib
```

Puis ajouter à la fin du fichier :
```python
# ---------------------------------------------------------------- entrées hostiles


def test_decode_header_rejects_wrong_size_unknown_flags_and_oversized_length() -> None:
    with pytest.raises(EcProtocolError):
        decode_header(bytes.fromhex("0000002000"))  # 5 octets au lieu de 8
    # Flags refusés (DÉCISION 2) : UTF8_NUMBERS non négocié, bit 0x40 interdit, base absente.
    for flags_hex in ("00000022", "00000060", "00000000", "00000028"):
        with pytest.raises(EcProtocolError):
            decode_header(bytes.fromhex(flags_hex + "00000003"))
    # Plafond 16 Mio (ReadHeader, ECSocket.cpp:540) : 16 Mio + 1 → rejet net.
    with pytest.raises(EcProtocolError):
        decode_header(bytes.fromhex("00000020" "01000001"))  # fmt: skip
    # Exactement 16 Mio (0x01000000) : accepté (borne incluse).
    assert decode_header(bytes.fromhex("0000002001000000")) == (0x20, 16 * 1024 * 1024)


def test_decode_rejects_truncated_value_inside_a_tag() -> None:
    # Tag STRING annonçant TAGLEN=5 mais 1 seul octet présent ; length d'en-tête cohérente (11).
    # payload : 28 | 0001 | 0602 06 00000005 | 41  →  take(5) déborde → « paquet EC tronqué ».
    frame = bytes.fromhex("000000200000000b2800010602060000000541")
    with pytest.raises(EcProtocolError, match="tronqué"):
        decode_packet(frame)


def test_decode_rejects_lying_taglen_smaller_than_children() -> None:
    # Parent 0x0700 avec 1 enfant de 8 octets sérialisés mais TAGLEN=0 → valeur propre -8.
    # payload : 28 | 0001 | 0E01 02 00000000 0001 | 0614 02 00000001 05  (20 octets = 0x14)
    # fmt: off
    frame = bytes.fromhex(
        "00000020" "00000014" "28" "0001" "0e01" "02" "00000000" "0001" "0614" "02" "00000001" "05"
    )
    # fmt: on
    with pytest.raises(EcProtocolError, match="TAGLEN menteur"):
        decode_packet(frame)


def test_decode_rejects_trailing_garbage_after_last_tag() -> None:
    # Trame NOOP valide + 1 octet 0xFF compté dans length → « octets résiduels ».
    frame = bytes.fromhex("00000020" "00000004" "01" "0000" "ff")  # fmt: skip
    with pytest.raises(EcProtocolError, match="résiduels"):
        decode_packet(frame)


def test_decode_packet_rejects_frame_length_mismatch() -> None:
    with pytest.raises(EcProtocolError, match="incohérente"):
        decode_packet(_AUTH_REQ_FRAME[:-1])  # un octet manquant par rapport à l'en-tête


def _nested_empty_tags(levels: int) -> EcTag:
    tag = empty_tag(0x0999)
    for _ in range(levels - 1):
        tag = empty_tag(0x0999, (tag,))
    return tag


def test_decode_accepts_depth_32_and_rejects_depth_33() -> None:
    ok_frame = encode_packet(EcPacket(codes.EC_OP_NOOP, (_nested_empty_tags(32),)))
    assert decode_packet(ok_frame).tags[0].children  # 32 niveaux : décodé sans erreur
    bad_frame = encode_packet(EcPacket(codes.EC_OP_NOOP, (_nested_empty_tags(33),)))
    with pytest.raises(EcProtocolError, match="profonde"):
        decode_packet(bad_frame)


# ---------------------------------------------------------------- zlib borné


def _zlib_frame(payload: bytes) -> bytes:
    compressed = zlib.compress(payload)
    return bytes.fromhex("00000021") + len(compressed).to_bytes(4, "big") + compressed


def test_decode_inflates_a_valid_zlib_frame() -> None:
    # Le payload SEARCH_RESULTS (clair, déjà validé) compressé : même arbre à l'arrivée.
    assert decode_packet(_zlib_frame(_SEARCH_RESULT_FRAME[8:])) == _search_result_packet()


def test_decode_rejects_corrupt_zlib_stream() -> None:
    frame = _zlib_frame(_SEARCH_RESULT_FRAME[8:])
    corrupted = frame[:8] + b"\x00\x00" + frame[10:]  # écrase l'en-tête zlib
    with pytest.raises(EcProtocolError, match="zlib"):
        decode_packet(corrupted)


def test_decode_rejects_truncated_zlib_stream() -> None:
    compressed = zlib.compress(_SEARCH_RESULT_FRAME[8:])[:-4]  # flux valide mais incomplet
    frame = bytes.fromhex("00000021") + len(compressed).to_bytes(4, "big") + compressed
    with pytest.raises(EcProtocolError, match="zlib"):
        decode_packet(frame)


def test_decode_rejects_zlib_bomb_beyond_the_decompression_bound() -> None:
    # 16 Mio + 1 de zéros compressés en ~16 Kio : la décompression BORNÉE refuse (DÉCISION 3).
    bomb = zlib.compress(b"\x00" * (16 * 1024 * 1024 + 1))
    frame = bytes.fromhex("00000021") + len(bomb).to_bytes(4, "big") + bomb
    with pytest.raises(EcProtocolError, match="zlib"):
        decode_packet(frame)
```

- [ ] **Step 2: Lancer pour vérifier**

Run: `uv run pytest tests/adapters/mule_ec/test_codec.py --no-cov -q`
Expected: PASS — chaque entrée hostile est rejetée par un `EcProtocolError` ciblé ; profondeur 32 acceptée, 33 refusée ; zlib valide/corrompu/tronqué/bombe traités. *(Si un test hostile PASSE sans l'implémentation de la Task 7, c'est que la branche correspondante manque : vérifier.)*

- [ ] **Step 3: Vérifier la suite complète + types + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert ; coverage 100 % — toutes les branches du décodeur sont maintenant exercées des deux côtés (`len(header)`, flags acceptés/refusés, plafond ≤/>, `take` dans/hors borne, `has_children` oui/non, `own_len` ≥ 0/< 0, profondeur ≤/>, `exhausted` oui/non, zlib ok/corrompu/tronqué/borné, longueur de trame cohérente/non).

- [ ] **Step 4: Commit (Tasks 7 + 8 ensemble — voir note Task 7 Step 5)**

```bash
git add src/emule_indexer/adapters/mule_ec/codec.py tests/adapters/mule_ec/test_codec.py
git commit -m "feat(adapters): EC packet decoding — round-trip + defensive bounds (size, depth, zlib)"
```

---

## Task 9: Transport async — framing, timeouts, faux serveur EC

**Files:**
- Create: `src/emule_indexer/adapters/mule_ec/transport.py`
- Create: `tests/adapters/mule_ec/ec_fakes.py`
- Create: `tests/adapters/mule_ec/test_transport.py`

- [ ] **Step 1: Écrire le faux serveur (helper de test) et les tests qui échouent**

`tests/adapters/mule_ec/ec_fakes.py` :
```python
"""Faux serveur EC en mémoire (streams asyncio) pour les tests transport/client.

Rejoue des réponses PRÉ-ENCODÉES, une par requête reçue (FCFS strict, réf. §9 piège 14).
``replies`` épuisées → le serveur SE TAIT (utile pour tester le timeout de lecture).
``close_after=N`` → ferme la connexion après N requêtes lues (0 = dès l'accept).
"""

import asyncio
import contextlib
from collections.abc import Sequence
from types import TracebackType

from emule_indexer.adapters.mule_ec.codec import EcPacket, decode_header, decode_payload


class FakeEcServer:
    def __init__(self, replies: Sequence[bytes] = (), *, close_after: int | None = None) -> None:
        self.replies = list(replies)
        self.received: list[EcPacket] = []
        self.port = 0
        self._close_after = close_after
        self._release = asyncio.Event()
        self._server: asyncio.Server | None = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(Exception):
            count = 0
            while self._close_after is None or count < self._close_after:
                header = await reader.readexactly(8)
                flags, length = decode_header(header)
                payload = await reader.readexactly(length)
                self.received.append(decode_payload(flags, payload))
                count += 1
                if not self.replies:
                    await self._release.wait()  # se taire jusqu'au teardown
                    break
                writer.write(self.replies.pop(0))
                await writer.drain()
        writer.close()

    async def __aenter__(self) -> "FakeEcServer":
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = int(self._server.sockets[0].getsockname()[1])
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._release.set()
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()
```

`tests/adapters/mule_ec/test_transport.py` :
```python
import asyncio
import socket

import pytest

from emule_indexer.adapters.mule_ec import codes
from emule_indexer.adapters.mule_ec.codec import EcPacket, encode_packet, string_tag
from emule_indexer.adapters.mule_ec.errors import (
    EcConnectError,
    EcProtocolError,
    EcTimeoutError,
)
from emule_indexer.adapters.mule_ec.transport import open_ec_transport
from tests.adapters.mule_ec.ec_fakes import FakeEcServer

_NOOP = EcPacket(codes.EC_OP_NOOP)
_REPLY = EcPacket(codes.EC_OP_STRINGS, (string_tag(codes.EC_TAG_STRING, "ok"),))


@pytest.mark.asyncio
async def test_send_then_receive_one_packet_fcfs() -> None:
    async with FakeEcServer([encode_packet(_REPLY)]) as server:
        transport = await open_ec_transport("127.0.0.1", server.port, timeout=2.0)
        await transport.send_packet(_NOOP)
        assert await transport.receive_packet() == _REPLY
        assert server.received == [_NOOP]
        await transport.close()


@pytest.mark.asyncio
async def test_receive_times_out_when_server_stays_silent() -> None:
    async with FakeEcServer([]) as server:  # lit la requête puis se tait
        transport = await open_ec_transport("127.0.0.1", server.port, timeout=0.2)
        await transport.send_packet(_NOOP)
        with pytest.raises(EcTimeoutError):
            await transport.receive_packet()
        await transport.close()


@pytest.mark.asyncio
async def test_receive_raises_connect_error_on_eof() -> None:
    async with FakeEcServer([], close_after=0) as server:  # ferme dès l'accept
        transport = await open_ec_transport("127.0.0.1", server.port, timeout=2.0)
        with pytest.raises(EcConnectError):
            await transport.receive_packet()
        await transport.close()


@pytest.mark.asyncio
async def test_send_raises_connect_error_on_lost_connection() -> None:
    async with FakeEcServer([]) as server:
        transport = await open_ec_transport("127.0.0.1", server.port, timeout=2.0)
        await transport.close()  # drain() sur connexion fermée → ConnectionResetError
        with pytest.raises(EcConnectError):
            await transport.send_packet(_NOOP)


@pytest.mark.asyncio
async def test_receive_propagates_protocol_error_on_malformed_header() -> None:
    async with FakeEcServer([bytes.fromhex("00000060" "00000003" "010000")]) as server:  # fmt: skip
        transport = await open_ec_transport("127.0.0.1", server.port, timeout=2.0)
        await transport.send_packet(_NOOP)
        with pytest.raises(EcProtocolError):
            await transport.receive_packet()
        await transport.close()


@pytest.mark.asyncio
async def test_connect_refused_raises_connect_error() -> None:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    free_port = probe.getsockname()[1]
    probe.close()  # le port vient d'être libéré : connexion refusée
    with pytest.raises(EcConnectError):
        await open_ec_transport("127.0.0.1", free_port, timeout=2.0)


@pytest.mark.asyncio
async def test_connect_timeout_raises_timeout_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _hang(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    monkeypatch.setattr(asyncio, "open_connection", _hang)
    with pytest.raises(EcTimeoutError):
        await open_ec_transport("127.0.0.1", 4712, timeout=0.05)
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/adapters/mule_ec/test_transport.py --no-cov -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'emule_indexer.adapters.mule_ec.transport'`.

- [ ] **Step 3: Écrire l'implémentation**

`src/emule_indexer/adapters/mule_ec/transport.py` :
```python
"""Transport EC async : framing d'UN paquet à la fois sur StreamReader/Writer (spec §4).

Timeout sur CHAQUE lecture réseau (+ l'établissement TCP), configurable (spec §6).
AUCUNE politique ici : pas de retry, pas de reconnexion, pas de sleep — l'adapter
signale, l'appelant décide (spec §3/§6). FCFS strict : une réponse par requête.
"""

import asyncio

from emule_indexer.adapters.mule_ec.codec import (
    EcPacket,
    decode_header,
    decode_payload,
    encode_packet,
)
from emule_indexer.adapters.mule_ec.errors import EcConnectError, EcTimeoutError

_HEADER_SIZE = 8  # réf. §1 (EC_HEADER_SIZE, ECSocket.h:72)


class EcTransport:
    """Encadre l'envoi/la réception d'un paquet EC complet sur une connexion établie."""

    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *, timeout: float
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._timeout = timeout

    async def send_packet(self, packet: EcPacket) -> None:
        """Émet une trame complète (DÉCISION 5 : pas de timeout d'écriture)."""
        try:
            self._writer.write(encode_packet(packet))
            await self._writer.drain()
        except OSError as exc:
            raise EcConnectError(f"connexion perdue à l'écriture : {exc}") from exc

    async def receive_packet(self) -> EcPacket:
        """Lit EXACTEMENT un paquet : en-tête 8 octets, puis ``length`` octets de payload."""
        header = await self._read_exactly(_HEADER_SIZE)
        flags, length = decode_header(header)
        payload = await self._read_exactly(length)
        return decode_payload(flags, payload)

    async def close(self) -> None:
        self._writer.close()
        await self._writer.wait_closed()

    async def _read_exactly(self, count: int) -> bytes:
        try:
            return await asyncio.wait_for(self._reader.readexactly(count), self._timeout)
        except TimeoutError as exc:
            raise EcTimeoutError("délai de lecture EC dépassé") from exc
        except (asyncio.IncompleteReadError, OSError) as exc:
            raise EcConnectError(f"connexion EC perdue : {exc}") from exc


async def open_ec_transport(host: str, port: int, *, timeout: float) -> EcTransport:
    """Établit la connexion TCP vers ``host:port`` (réf. §0 : port EC par défaut 4712)."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except TimeoutError as exc:
        raise EcTimeoutError(f"délai de connexion à {host}:{port} dépassé") from exc
    except OSError as exc:
        raise EcConnectError(f"connexion à {host}:{port} impossible : {exc}") from exc
    return EcTransport(reader, writer, timeout=timeout)
```

- [ ] **Step 4: Lancer pour vérifier que tout passe**

Run: `uv run pytest tests/adapters/mule_ec/test_transport.py --no-cov -q`
Expected: PASS — 7 tests verts : aller-retour nominal, timeout de lecture, EOF → `EcConnectError`, écriture sur connexion perdue → `EcConnectError`, en-tête malformé → `EcProtocolError` (pass-through codec), refus TCP → `EcConnectError`, délai de connexion → `EcTimeoutError`.

- [ ] **Step 5: Vérifier la suite complète + types + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert ; coverage 100 % (chaque `except` du transport déclenché + chemins nominaux ; `ec_fakes.py` est du code de test, hors mesure de coverage mais sous mypy strict).

- [ ] **Step 6: Commit**

```bash
git add src/emule_indexer/adapters/mule_ec/transport.py tests/adapters/mule_ec/ec_fakes.py tests/adapters/mule_ec/test_transport.py
git commit -m "feat(adapters): async EC transport (one-packet framing, read timeouts, no policy)"
```

---

## Task 10: Client — connexion, authentification, fermeture

**Files:**
- Create: `src/emule_indexer/adapters/mule_ec/client.py`
- Create: `tests/adapters/mule_ec/test_client.py`

> Le fichier `test_client.py` grandit ensuite (Tasks 12-13). Les vecteurs du hash d'auth ci-dessous sont PRÉCALCULÉS depuis la formule exacte de la réf. §4 (vérifiables en 4 lignes de Python : `md5("secret123") = 5d7845ac6ee7cfffafc5fe5f35cf666d` ; `format(salt, "X")` ; etc.).

- [ ] **Step 1: Écrire les tests qui échouent**

`tests/adapters/mule_ec/test_client.py` :
```python
import pytest

from emule_indexer.adapters.mule_ec import codes
from emule_indexer.adapters.mule_ec.client import AmuleEcClient, salted_password_hash
from emule_indexer.adapters.mule_ec.codec import EcPacket, encode_packet, string_tag, uint_tag
from emule_indexer.adapters.mule_ec.errors import (
    EcAuthError,
    EcConnectError,
    EcProtocolError,
)
from tests.adapters.mule_ec.ec_fakes import FakeEcServer

_PASSWORD = "secret123"


# ---------------------------------------------------------------- formule d'auth (pure)


def test_salted_password_hash_matches_the_reference_formula() -> None:
    # Réf. §4 : hash = md5( lower(md5_hex(pwd)) + md5_hex(format("%X", salt)) ), 16 octets bruts.
    # Vecteurs précalculés : md5("secret123")=5d7845ac6ee7cfffafc5fe5f35cf666d ;
    #   salt 0x6B5E8D3A12F0C4D7 → "6B5E8D3A12F0C4D7" → md5=cd35cbb9bcdce6dc1510a4ff66e2be9a.
    assert salted_password_hash(_PASSWORD, 0x6B5E8D3A12F0C4D7) == bytes.fromhex(
        "1fd30b937affac0994f651b1b4f3aaf4"
    )
    # Sel ÉTROIT (piège 4) : 0xAB → "AB" (majuscules, sans zéros de tête).
    assert salted_password_hash(_PASSWORD, 0xAB) == bytes.fromhex(
        "36f8e4902449fcaa91e76f7dc1d87e9e"
    )
    # Sel zéro : "%lX" de 0 → "0" (réf. §4).
    assert salted_password_hash(_PASSWORD, 0) == bytes.fromhex("29e6c939d92ec99adbe3a50970506102")


# ---------------------------------------------------------------- handshake


def _auth_replies(salt: int) -> list[bytes]:
    return [
        encode_packet(EcPacket(codes.EC_OP_AUTH_SALT, (uint_tag(codes.EC_TAG_PASSWD_SALT, salt),))),
        encode_packet(
            EcPacket(codes.EC_OP_AUTH_OK, (string_tag(codes.EC_TAG_SERVER_VERSION, "3.0.0"),))
        ),
    ]


@pytest.mark.asyncio
async def test_connect_performs_the_full_auth_handshake() -> None:
    async with FakeEcServer(_auth_replies(0x6B5E8D3A12F0C4D7)) as server:
        client = AmuleEcClient("127.0.0.1", server.port, _PASSWORD, timeout=2.0)
        await client.connect()
        await client.close()
        auth_req, auth_passwd = server.received
        # AUTH_REQ : nom + version + version de protocole, AUCUN tag CAN_* (DÉCISION 2).
        assert auth_req.opcode == codes.EC_OP_AUTH_REQ
        assert auth_req.find(codes.EC_TAG_CLIENT_NAME) is not None
        assert auth_req.find(codes.EC_TAG_CLIENT_VERSION) is not None
        protocol = auth_req.find(codes.EC_TAG_PROTOCOL_VERSION)
        assert protocol is not None
        assert protocol.int_value() == 0x0204
        assert auth_req.find(codes.EC_TAG_CAN_ZLIB) is None
        assert auth_req.find(codes.EC_TAG_CAN_UTF8_NUMBERS) is None
        # AUTH_PASSWD : le hash salé EXACT, en HASH16.
        assert auth_passwd.opcode == codes.EC_OP_AUTH_PASSWD
        passwd_hash = auth_passwd.find(codes.EC_TAG_PASSWD_HASH)
        assert passwd_hash is not None
        assert passwd_hash.tag_type == codes.EC_TAGTYPE_HASH16
        assert passwd_hash.value == bytes.fromhex("1fd30b937affac0994f651b1b4f3aaf4")


@pytest.mark.asyncio
async def test_connect_reads_a_narrow_salt_generically() -> None:
    # Réf. §9 piège 4 : le sel arrive UINT8 quand il est petit ; lecture générique exigée.
    async with FakeEcServer(_auth_replies(0xAB)) as server:
        client = AmuleEcClient("127.0.0.1", server.port, _PASSWORD, timeout=2.0)
        await client.connect()
        await client.close()
        passwd_hash = server.received[1].find(codes.EC_TAG_PASSWD_HASH)
        assert passwd_hash is not None
        assert passwd_hash.value == bytes.fromhex("36f8e4902449fcaa91e76f7dc1d87e9e")


@pytest.mark.asyncio
async def test_connect_raises_auth_error_with_daemon_message_on_auth_fail() -> None:
    replies = [
        encode_packet(EcPacket(codes.EC_OP_AUTH_SALT, (uint_tag(codes.EC_TAG_PASSWD_SALT, 1),))),
        encode_packet(
            EcPacket(
                codes.EC_OP_AUTH_FAIL,
                (string_tag(codes.EC_TAG_STRING, "Authentication failed."),),
            )
        ),
    ]
    async with FakeEcServer(replies) as server:
        client = AmuleEcClient("127.0.0.1", server.port, "mauvais", timeout=2.0)
        with pytest.raises(EcAuthError, match="Authentication failed."):
            await client.connect()
        # Après l'échec, le client n'est PAS connecté.
        with pytest.raises(EcConnectError):
            await client.stop_search()


@pytest.mark.asyncio
async def test_connect_raises_auth_error_when_first_reply_is_auth_fail() -> None:
    # Réf. §4 : une version de protocole refusée répond AUTH_FAIL dès la 1re réponse.
    replies = [
        encode_packet(
            EcPacket(
                codes.EC_OP_AUTH_FAIL,
                (string_tag(codes.EC_TAG_STRING, "Invalid protocol version."),),
            )
        )
    ]
    async with FakeEcServer(replies) as server:
        client = AmuleEcClient("127.0.0.1", server.port, _PASSWORD, timeout=2.0)
        with pytest.raises(EcAuthError, match="Invalid protocol version."):
            await client.connect()


@pytest.mark.asyncio
async def test_connect_raises_auth_error_without_message_tag() -> None:
    replies = [encode_packet(EcPacket(codes.EC_OP_AUTH_FAIL))]
    async with FakeEcServer(replies) as server:
        client = AmuleEcClient("127.0.0.1", server.port, _PASSWORD, timeout=2.0)
        with pytest.raises(EcAuthError, match="sans message"):
            await client.connect()


@pytest.mark.asyncio
async def test_connect_raises_protocol_error_on_unexpected_opcode_at_salt_step() -> None:
    replies = [encode_packet(EcPacket(codes.EC_OP_NOOP))]
    async with FakeEcServer(replies) as server:
        client = AmuleEcClient("127.0.0.1", server.port, _PASSWORD, timeout=2.0)
        with pytest.raises(EcProtocolError):
            await client.connect()


@pytest.mark.asyncio
async def test_connect_raises_protocol_error_when_salt_tag_is_missing() -> None:
    replies = [encode_packet(EcPacket(codes.EC_OP_AUTH_SALT))]  # AUTH_SALT sans tag de sel
    async with FakeEcServer(replies) as server:
        client = AmuleEcClient("127.0.0.1", server.port, _PASSWORD, timeout=2.0)
        with pytest.raises(EcProtocolError, match="PASSWD_SALT"):
            await client.connect()


@pytest.mark.asyncio
async def test_connect_raises_protocol_error_on_unexpected_verdict_opcode() -> None:
    replies = [
        encode_packet(EcPacket(codes.EC_OP_AUTH_SALT, (uint_tag(codes.EC_TAG_PASSWD_SALT, 1),))),
        encode_packet(EcPacket(codes.EC_OP_NOOP)),
    ]
    async with FakeEcServer(replies) as server:
        client = AmuleEcClient("127.0.0.1", server.port, _PASSWORD, timeout=2.0)
        with pytest.raises(EcProtocolError):
            await client.connect()


@pytest.mark.asyncio
async def test_connect_refuses_empty_password_before_any_io() -> None:
    # Miroir de RemoteConnect.cpp:117 (réf. §4) — aucun serveur nécessaire.
    client = AmuleEcClient("127.0.0.1", 1, "", timeout=2.0)
    with pytest.raises(EcAuthError, match="vide"):
        await client.connect()


@pytest.mark.asyncio
async def test_close_is_a_noop_when_never_connected_and_idempotent() -> None:
    client = AmuleEcClient("127.0.0.1", 1, _PASSWORD, timeout=2.0)
    await client.close()  # jamais connecté : no-op
    async with FakeEcServer(_auth_replies(7)) as server:
        client = AmuleEcClient("127.0.0.1", server.port, _PASSWORD, timeout=2.0)
        await client.connect()
        await client.close()
        await client.close()  # idempotent
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/adapters/mule_ec/test_client.py --no-cov -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'emule_indexer.adapters.mule_ec.client'`.

- [ ] **Step 3: Écrire l'implémentation**

`src/emule_indexer/adapters/mule_ec/client.py` :
```python
"""Client EC haut niveau : auth, recherche, statut (cf. spec EC-adapter §4-§6).

Implémente STRUCTURELLEMENT le port ``MuleClient`` (sans l'importer — même typage
structurel que les matchers vis-à-vis du Protocol ``Matcher``). AUCUN sleep, retry ou
reconnexion ici : l'adapter signale, l'appelant décide (spec §3/§6). Une requête à la
fois, réponses corrélées par ORDRE (FCFS strict, réf. §9 piège 14).
"""

import hashlib

from emule_indexer.adapters.mule_ec import codes
from emule_indexer.adapters.mule_ec.codec import (
    EcPacket,
    EcTag,
    hash16_tag,
    string_tag,
    uint_tag,
)
from emule_indexer.adapters.mule_ec.errors import (
    EcAuthError,
    EcConnectError,
    EcFailureError,
    EcProtocolError,
)
from emule_indexer.adapters.mule_ec.mapping import map_search_results
from emule_indexer.adapters.mule_ec.transport import EcTransport, open_ec_transport
from emule_indexer.domain.observation import FileObservation
from emule_indexer.ports.mule_client import KadStatus, NetworkStatus, SearchChannel

_CLIENT_NAME = "emule-indexer"
_CLIENT_VERSION = "0.5.0"
_LOWID_THRESHOLD = 16777216  # HIGHEST_LOWID_ED2K_KAD (NetworkFunctions.h:123, réf. §6)
_MAX_PROGRESS_PERCENT = 100  # au-delà : 0xffff (locale) / 0xfffe (Kad fini), réf. §5

_CHANNEL_TO_SEARCH_TYPE = {
    SearchChannel.GLOBAL: codes.EC_SEARCH_GLOBAL,
    SearchChannel.KAD: codes.EC_SEARCH_KAD,
}


def salted_password_hash(password: str, salt: int) -> bytes:
    """Hash d'auth EC, formule EXACTE de la réf. §4 (RemoteConnect.cpp:252-253).

    ``md5( lower(md5_hex(password)) + md5_hex(format("%X", salt)) )`` → 16 octets bruts.
    Pièges 4/5 : le sel est une valeur LOGIQUE (lue à largeur variable) formatée en hex
    MAJUSCULE sans zéros de tête ; les deux md5-hex intermédiaires sont en minuscule.
    """
    salt_str = format(salt, "X")
    salt_hash = hashlib.md5(salt_str.encode("ascii")).hexdigest()
    passwd_md5 = hashlib.md5(password.encode("utf-8")).hexdigest()
    return hashlib.md5((passwd_md5 + salt_hash).encode("ascii")).digest()


def _failure_message(reply: EcPacket) -> str:
    """Message d'un AUTH_FAIL/FAILED (EC_TAG_STRING), ou un libellé sûr s'il manque."""
    tag = reply.find(codes.EC_TAG_STRING)
    if tag is None:
        return "échec signalé par amuled (sans message)"
    return tag.string_value()


class AmuleEcClient:
    """Pilote un ``amuled`` via EC. Trois usages câblés : auth, recherche, statut (spec §3).

    ``skipped_entries_total`` accumule les entrées de résultats écartées par le mapper
    (futur brancheur de métrique, plan E — DÉCISION 6).
    """

    def __init__(self, host: str, port: int, password: str, *, timeout: float = 10.0) -> None:
        self._host = host
        self._port = port
        self._password = password
        self._timeout = timeout
        self._transport: EcTransport | None = None
        self._current_keyword = ""
        self.skipped_entries_total = 0

    async def connect(self) -> None:
        """TCP + handshake d'auth (réf. §4). Échec → exception, SANS retry (spec §5)."""
        if not self._password:
            raise EcAuthError("mot de passe EC vide (refusé, miroir de RemoteConnect.cpp:117)")
        transport = await open_ec_transport(self._host, self._port, timeout=self._timeout)
        try:
            await self._authenticate(transport)
        except Exception:
            await transport.close()
            raise
        self._transport = transport

    async def close(self) -> None:
        if self._transport is not None:
            await self._transport.close()
            self._transport = None

    async def start_search(self, keyword: str, channel: SearchChannel) -> None:
        """Lance une recherche (réf. §5). Efface les résultats de la précédente (côté daemon)."""
        search_tag = uint_tag(
            codes.EC_TAG_SEARCH_TYPE,
            _CHANNEL_TO_SEARCH_TYPE[channel],
            (
                string_tag(codes.EC_TAG_SEARCH_NAME, keyword),
                string_tag(codes.EC_TAG_SEARCH_FILE_TYPE, ""),  # obligatoire, "" = tous types
            ),
        )
        await self._request(EcPacket(codes.EC_OP_SEARCH_START, (search_tag,)), codes.EC_OP_STRINGS)
        self._current_keyword = keyword  # provenance, posée APRÈS le succès

    async def fetch_results(self) -> tuple[FileObservation, ...]:
        """Snapshot CUMULATIF des résultats accumulés par le daemon (réf. §5)."""
        reply = await self._request(
            EcPacket(codes.EC_OP_SEARCH_RESULTS), codes.EC_OP_SEARCH_RESULTS
        )
        observations, skipped = map_search_results(reply.tags, self._current_keyword)
        self.skipped_entries_total += skipped
        return observations

    async def stop_search(self) -> None:
        await self._request(EcPacket(codes.EC_OP_SEARCH_STOP), codes.EC_OP_MISC_DATA)

    async def search_progress(self) -> int | None:
        """Pourcentage 0-100 si EC l'expose, sinon ``None`` (convention amulecmd, réf. §5)."""
        reply = await self._request(
            EcPacket(codes.EC_OP_SEARCH_PROGRESS), codes.EC_OP_SEARCH_PROGRESS
        )
        status = reply.find(codes.EC_TAG_SEARCH_STATUS)
        if status is None:
            return None
        value = status.int_value()
        if value > _MAX_PROGRESS_PERCENT:
            return None
        return value

    async def network_status(self) -> NetworkStatus:
        """État réseau (réf. §6) : EC_OP_GET_CONNSTATE au niveau de détail CMD."""
        request = EcPacket(
            codes.EC_OP_GET_CONNSTATE,
            (uint_tag(codes.EC_TAG_DETAIL_LEVEL, codes.EC_DETAIL_CMD),),
        )
        reply = await self._request(request, codes.EC_OP_MISC_DATA)
        connstate = reply.find(codes.EC_TAG_CONNSTATE)
        if connstate is None:
            raise EcProtocolError("réponse GET_CONNSTATE sans EC_TAG_CONNSTATE")
        return _parse_connstate(connstate)

    async def _authenticate(self, transport: EcTransport) -> None:
        auth_req = EcPacket(
            codes.EC_OP_AUTH_REQ,
            (
                string_tag(codes.EC_TAG_CLIENT_NAME, _CLIENT_NAME),
                string_tag(codes.EC_TAG_CLIENT_VERSION, _CLIENT_VERSION),
                # Émis en UINT16 (au plus court). AUCUN tag CAN_* (DÉCISION 2), AUCUN
                # EC_TAG_VERSION_ID (interdit face à une release, réf. §4).
                uint_tag(codes.EC_TAG_PROTOCOL_VERSION, codes.EC_CURRENT_PROTOCOL_VERSION),
            ),
        )
        await transport.send_packet(auth_req)
        salt_reply = await transport.receive_packet()
        if salt_reply.opcode == codes.EC_OP_AUTH_FAIL:
            raise EcAuthError(_failure_message(salt_reply))
        if salt_reply.opcode != codes.EC_OP_AUTH_SALT:
            raise EcProtocolError(f"opcode inattendu pendant l'auth : 0x{salt_reply.opcode:02X}")
        salt_tag = salt_reply.find(codes.EC_TAG_PASSWD_SALT)
        if salt_tag is None:
            raise EcProtocolError("EC_OP_AUTH_SALT sans EC_TAG_PASSWD_SALT")
        salt = salt_tag.int_value()  # largeur VARIABLE (réf. §9 piège 4)
        passwd_packet = EcPacket(
            codes.EC_OP_AUTH_PASSWD,
            (hash16_tag(codes.EC_TAG_PASSWD_HASH, salted_password_hash(self._password, salt)),),
        )
        await transport.send_packet(passwd_packet)
        verdict = await transport.receive_packet()
        if verdict.opcode == codes.EC_OP_AUTH_FAIL:
            raise EcAuthError(_failure_message(verdict))
        if verdict.opcode != codes.EC_OP_AUTH_OK:
            raise EcProtocolError(f"opcode inattendu pendant l'auth : 0x{verdict.opcode:02X}")

    def _require_transport(self) -> EcTransport:
        if self._transport is None:
            raise EcConnectError("client EC non connecté (appeler connect() d'abord)")
        return self._transport

    async def _request(self, packet: EcPacket, expected_opcode: int) -> EcPacket:
        """Une requête → une réponse (FCFS). FAILED → EcFailureError ; autre → EcProtocolError."""
        transport = self._require_transport()
        await transport.send_packet(packet)
        reply = await transport.receive_packet()
        if reply.opcode == codes.EC_OP_FAILED:
            raise EcFailureError(_failure_message(reply))
        if reply.opcode != expected_opcode:
            raise EcProtocolError(
                f"opcode inattendu : 0x{reply.opcode:02X} (attendu 0x{expected_opcode:02X})"
            )
        return reply


def _parse_connstate(connstate: EcTag) -> NetworkStatus:
    """Décode le bitfield + sous-tags d'EC_TAG_CONNSTATE (réf. §6)."""
    bits = connstate.int_value()
    if not bits & codes.CONNSTATE_KAD_RUNNING:
        kad = KadStatus.OFF
    elif not bits & codes.CONNSTATE_CONNECTED_KAD:
        kad = KadStatus.RUNNING
    elif bits & codes.CONNSTATE_KAD_FIREWALLED:
        kad = KadStatus.FIREWALLED
    else:
        kad = KadStatus.CONNECTED
    ed2k_id: int | None = None
    server_name: str | None = None
    server_addr: str | None = None
    if bits & codes.CONNSTATE_CONNECTED_ED2K:
        id_tag = connstate.find(codes.EC_TAG_ED2K_ID)
        if id_tag is not None:
            ed2k_id = id_tag.int_value()
        server = connstate.find(codes.EC_TAG_SERVER)
        if server is not None:
            server_addr = server.ipv4_value()
            name_tag = server.find(codes.EC_TAG_SERVER_NAME)
            if name_tag is not None:
                server_name = name_tag.string_value()
    ed2k_high = ed2k_id is not None and ed2k_id >= _LOWID_THRESHOLD
    return NetworkStatus(
        ed2k_id=ed2k_id,
        ed2k_high=ed2k_high,
        kad_status=kad,
        server_name=server_name,
        server_addr=server_addr,
    )
```

> **Note d'ordonnancement (lue AVANT d'exécuter cette tâche) :** `client.py` importe `mapping.py` qui n'existe pas encore — créer en même temps un `mapping.py` MINIMAL pour cette tâche est interdit (pas de code de prod sans test). **Décision : la Task 11 (mapping — pure, sans dépendance au client) S'EXÉCUTE EN ENTIER (commit inclus) AVANT la présente tâche** : à ce moment-là `test_client.py` n'existe pas encore, donc son gate complet est VERT et son commit est propre. Ensuite, parce que le gate 100 % exige que TOUTES les branches de `client.py` soient couvertes (les méthodes recherche/statut ne le sont que par les tests des Tasks 12-13), l'implémenteur enchaîne SANS COMMITTER : Task 10 Steps 1-4 → Task 12 Steps 1-2 → Task 13 Steps 1-2 → gate complet (Task 13 Step 3) → **UN SEUL commit** `client.py` + `test_client.py` complet (le Step 6 ci-dessous, avec le message élargi). Les steps commit des Tasks 12 et 13 sont alors **SAUTÉS** (sans objet : tout commit intermédiaire aurait un gate rouge, et il n'y aurait plus rien à committer ensuite).

- [ ] **Step 4: Lancer pour vérifier que tout passe** *(la Task 11 est déjà committée à ce stade)*

Run: `uv run pytest tests/adapters/mule_ec/test_client.py --no-cov -q`
Expected: PASS — 11 tests verts : formule du hash (3 vecteurs), handshake complet (trames AUTH_REQ/AUTH_PASSWD inspectées côté serveur), sel étroit, AUTH_FAIL aux deux étapes (avec et sans message), opcodes inattendus aux deux étapes, sel manquant, mot de passe vide, close no-op/idempotent.

- [ ] **Step 5: Vérifier lint + types (PAS la suite complète avec coverage)**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: lint + types verts. **NE PAS lancer `uv run pytest -q` ici, NE PAS committer ici** : la couverture de `client.py` n'atteint 100 % qu'avec les tests des Tasks 12-13 (méthodes recherche/statut) — enchaîner Task 12 Steps 1-2 puis Task 13 Steps 1-3, puis revenir au Step 6 ci-dessous (voir la note d'ordonnancement).

- [ ] **Step 6: Commit (UNIQUE pour les Tasks 10+12+13, exécuté APRÈS le gate vert de la Task 13 Step 3)**

```bash
git add src/emule_indexer/adapters/mule_ec/client.py tests/adapters/mule_ec/test_client.py
git commit -m "feat(adapters): AmuleEcClient (auth handshake, search cycle, network status)"
```

---

## Task 11: Mapping — résultats EC → `FileObservation` (capture-all, écartés comptés)

**Files:**
- Create: `src/emule_indexer/adapters/mule_ec/mapping.py`
- Create: `tests/adapters/mule_ec/test_mapping.py`

> Pur et synchrone (tags déjà décodés → objets domaine). **S'exécute EN ENTIER (commit inclus) AVANT la Task 10** (voir note d'ordonnancement ci-dessus) : à ce moment, `test_client.py` n'existe pas encore, le gate complet est donc vert.

- [ ] **Step 1: Écrire les tests qui échouent**

`tests/adapters/mule_ec/test_mapping.py` :
```python
from emule_indexer.adapters.mule_ec import codes
from emule_indexer.adapters.mule_ec.codec import EcTag, hash16_tag, string_tag, uint_tag
from emule_indexer.adapters.mule_ec.mapping import map_search_results
from emule_indexer.domain.observation import FileObservation

_HASH = bytes(range(16))
_HASH_HEX = _HASH.hex()


def _entry(children: tuple[EcTag, ...]) -> EcTag:
    # EC_TAG_SEARCHFILE : valeur propre = ECID (identifiant de session VOLATIL, piège 13).
    return EcTag(codes.EC_TAG_SEARCHFILE, codes.EC_TAGTYPE_UINT8, b"\x07", children)


def _full_entry() -> EcTag:
    return _entry(
        (
            string_tag(codes.EC_TAG_PARTFILE_NAME, "Keroro 062A.avi"),
            uint_tag(codes.EC_TAG_PARTFILE_SIZE_FULL, 234567890),
            hash16_tag(codes.EC_TAG_PARTFILE_HASH, _HASH),
            uint_tag(codes.EC_TAG_PARTFILE_SOURCE_COUNT, 5),
            uint_tag(codes.EC_TAG_PARTFILE_SOURCE_COUNT_XFER, 2),
            uint_tag(codes.EC_TAG_PARTFILE_STATUS, 0),  # non mappé → raw_meta
            string_tag(0x0999, "mystère"),  # tag INCONNU → raw_meta, jamais une erreur
        )
    )


def test_maps_a_complete_entry_with_capture_all_raw_meta() -> None:
    observations, skipped = map_search_results((_full_entry(),), "keroro")
    assert skipped == 0
    assert observations == (
        FileObservation(
            ed2k_hash=_HASH_HEX,
            filename="Keroro 062A.avi",
            size_bytes=234567890,
            source_count=5,
            complete_source_count=2,
            keyword="keroro",
            media_length_sec=None,  # EC n'expose AUCUN tag média (réf. §5) — None attendu
            bitrate_kbps=None,
            codec=None,
            file_type=None,
            raw_meta=(("0x0308", "0"), ("0x0999", "mystère")),
        ),
    )


def test_source_counts_default_to_zero_when_absent() -> None:
    entry = _entry(
        (
            string_tag(codes.EC_TAG_PARTFILE_NAME, "x.avi"),
            uint_tag(codes.EC_TAG_PARTFILE_SIZE_FULL, 1),
            hash16_tag(codes.EC_TAG_PARTFILE_HASH, _HASH),
        )
    )
    observations, skipped = map_search_results((entry,), "keroro")
    assert skipped == 0
    assert observations[0].source_count == 0
    assert observations[0].complete_source_count == 0
    assert observations[0].raw_meta == ()


def test_skips_entries_missing_hash_name_or_size_without_failing_the_batch() -> None:
    name = string_tag(codes.EC_TAG_PARTFILE_NAME, "x.avi")
    size = uint_tag(codes.EC_TAG_PARTFILE_SIZE_FULL, 1)
    hsh = hash16_tag(codes.EC_TAG_PARTFILE_HASH, _HASH)
    no_hash = _entry((name, size))
    no_name = _entry((size, hsh))
    no_size = _entry((name, hsh))
    observations, skipped = map_search_results((no_hash, _full_entry(), no_name, no_size), "k")
    assert skipped == 3  # le mapper COMPTE les écartés (spec §6)
    assert len(observations) == 1  # une entrée pourrie ne fait JAMAIS échouer le lot


def test_skips_entry_with_malformed_mandatory_tag() -> None:
    # Hash au mauvais type/longueur : entrée inexploitable → écartée, pas d'exception.
    bad_hash = _entry(
        (
            string_tag(codes.EC_TAG_PARTFILE_NAME, "x.avi"),
            uint_tag(codes.EC_TAG_PARTFILE_SIZE_FULL, 1),
            EcTag(codes.EC_TAG_PARTFILE_HASH, codes.EC_TAGTYPE_HASH16, b"\x01\x02"),
        )
    )
    observations, skipped = map_search_results((bad_hash,), "k")
    assert observations == ()
    assert skipped == 1


def test_ignores_non_searchfile_top_level_tags() -> None:
    stray = string_tag(codes.EC_TAG_STRING, "bruit")
    observations, skipped = map_search_results((stray, _full_entry()), "k")
    assert len(observations) == 1
    assert skipped == 0  # un tag de premier niveau inattendu n'est PAS une entrée écartée


def test_raw_meta_renders_ints_strings_and_falls_back_to_hex() -> None:
    entry = _entry(
        (
            string_tag(codes.EC_TAG_PARTFILE_NAME, "x.avi"),
            uint_tag(codes.EC_TAG_PARTFILE_SIZE_FULL, 1),
            hash16_tag(codes.EC_TAG_PARTFILE_HASH, _HASH),
            uint_tag(0x0701, 65535),  # entier → décimal
            string_tag(0x0702, "texte"),  # chaîne bien formée → texte
            EcTag(0x0703, codes.EC_TAGTYPE_STRING, b"sans-nul"),  # STRING cassé → hex
            EcTag(0x0704, codes.EC_TAGTYPE_UINT32, b"\x01"),  # largeur menteuse → hex
            EcTag(0x0705, codes.EC_TAGTYPE_CUSTOM, b"\xde\xad"),  # opaque → hex
        )
    )
    observations, _ = map_search_results((entry,), "k")
    assert observations[0].raw_meta == (
        ("0x0701", "65535"),
        ("0x0702", "texte"),
        ("0x0703", "73616e732d6e756c"),
        ("0x0704", "01"),
        ("0x0705", "dead"),
    )
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/adapters/mule_ec/test_mapping.py --no-cov -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'emule_indexer.adapters.mule_ec.mapping'`.

- [ ] **Step 3: Écrire l'implémentation**

`src/emule_indexer/adapters/mule_ec/mapping.py` :
```python
"""Mapping des résultats de recherche EC → ``FileObservation`` (spec §4/§6, capture-all).

Réf. §5 : la liste EXHAUSTIVE des métadonnées qu'EC expose sur un résultat est : nom,
taille, hash MD4, sources, sources complètes, statut, parent, (rating 3.0.0). AUCUN tag
média (durée/bitrate/codec) ne transite — les champs média de ``FileObservation`` restent
``None`` ; le capture-all ``raw_meta`` ramasse tout tag non mappé, connu ou inconnu.
Tolérance aux inconnus : un tag inconnu n'est JAMAIS une erreur ; seule une entrée sans
hash/nom/taille exploitables est écartée — et COMPTÉE, jamais fatale au lot (spec §6).
"""

from emule_indexer.adapters.mule_ec import codes
from emule_indexer.adapters.mule_ec.codec import INT_WIDTHS, EcTag
from emule_indexer.adapters.mule_ec.errors import EcProtocolError
from emule_indexer.domain.observation import FileObservation

# Tags d'entrée mappés vers des champs structurés (donc EXCLUS de raw_meta).
_MAPPED_CHILD_TAGS = frozenset(
    {
        codes.EC_TAG_PARTFILE_NAME,
        codes.EC_TAG_PARTFILE_SIZE_FULL,
        codes.EC_TAG_PARTFILE_HASH,
        codes.EC_TAG_PARTFILE_SOURCE_COUNT,
        codes.EC_TAG_PARTFILE_SOURCE_COUNT_XFER,
    }
)


def map_search_results(
    tags: tuple[EcTag, ...], keyword: str
) -> tuple[tuple[FileObservation, ...], int]:
    """Tags de premier niveau d'un EC_OP_SEARCH_RESULTS → ``(observations, nb_écartés)``."""
    observations: list[FileObservation] = []
    skipped = 0
    for tag in tags:
        if tag.name != codes.EC_TAG_SEARCHFILE:
            continue  # premier niveau inattendu : toléré, ignoré (pas une entrée)
        observation = _map_entry(tag, keyword)
        if observation is None:
            skipped += 1
        else:
            observations.append(observation)
    return tuple(observations), skipped


def _map_entry(entry: EcTag, keyword: str) -> FileObservation | None:
    """Une entrée (sous-arbre EC_TAG_SEARCHFILE) → observation, ou ``None`` si inexploitable.

    L'ECID (valeur propre de l'entrée) n'est JAMAIS conservé : identifiant de session
    volatil (réf. §9 piège 13) ; seul le hash MD4 identifie le fichier.
    """
    hash_tag = entry.find(codes.EC_TAG_PARTFILE_HASH)
    name_tag = entry.find(codes.EC_TAG_PARTFILE_NAME)
    size_tag = entry.find(codes.EC_TAG_PARTFILE_SIZE_FULL)
    if hash_tag is None or name_tag is None or size_tag is None:
        return None
    try:
        ed2k_hash = _hash_hex(hash_tag)
        filename = name_tag.string_value()
        size_bytes = size_tag.int_value()
        source_count = _optional_int(entry, codes.EC_TAG_PARTFILE_SOURCE_COUNT)
        complete_source_count = _optional_int(entry, codes.EC_TAG_PARTFILE_SOURCE_COUNT_XFER)
    except EcProtocolError:
        return None  # entrée pourrie : écartée (l'appelant compte), jamais fatale
    return FileObservation(
        ed2k_hash=ed2k_hash,
        filename=filename,
        size_bytes=size_bytes,
        source_count=source_count,
        complete_source_count=complete_source_count,
        keyword=keyword,
        raw_meta=_raw_meta(entry),
    )


def _hash_hex(tag: EcTag) -> str:
    """Hash MD4 → hex minuscule 32 caractères (16 octets HASH16 exigés, réf. §3)."""
    if tag.tag_type != codes.EC_TAGTYPE_HASH16 or len(tag.value) != 16:
        raise EcProtocolError("hash eD2k inexploitable")
    return tag.value.hex()


def _optional_int(entry: EcTag, name: int) -> int:
    """Entier optionnel d'une entrée : absence = 0 (réf. §3 : absence = valeur nulle)."""
    tag = entry.find(name)
    if tag is None:
        return 0
    return tag.int_value()


def _raw_meta(entry: EcTag) -> tuple[tuple[str, str], ...]:
    """Capture-all (DÉCISION 7) : tout tag non mappé → ``("0xNNNN", valeur_rendue)``."""
    collected: list[tuple[str, str]] = []
    for child in entry.children:
        if child.name in _MAPPED_CHILD_TAGS:
            continue
        collected.append((f"0x{child.name:04X}", _render_value(child)))
    return tuple(collected)


def _render_value(tag: EcTag) -> str:
    """Rendu JSON-friendly qui ne lève JAMAIS : entier décimal, texte, sinon hex brut."""
    if tag.tag_type in INT_WIDTHS and len(tag.value) == INT_WIDTHS[tag.tag_type]:
        return str(int.from_bytes(tag.value, "big"))
    if tag.tag_type == codes.EC_TAGTYPE_STRING and tag.value.endswith(b"\x00"):
        return tag.value[:-1].decode("utf-8", errors="replace")
    return tag.value.hex()
```

- [ ] **Step 4: Lancer pour vérifier que tout passe**

Run: `uv run pytest tests/adapters/mule_ec/test_mapping.py --no-cov -q`
Expected: PASS — 6 tests verts : entrée complète (raw_meta = STATUS + inconnu, champs média `None`), compteurs absents → 0, 3 entrées incomplètes écartées et COMPTÉES sans casser le lot, tag obligatoire malformé → écartée, premier niveau inattendu ignoré, rendu int/texte/hex (les 3 chemins de `_render_value`, chaque condition des deux côtés).

- [ ] **Step 5: Vérifier la suite complète + types + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert ; coverage 100 % sur `mapping.py` (cette tâche s'exécute AVANT la Task 10 : aucun fichier rouge dans l'arbre).

- [ ] **Step 6: Commit**

```bash
git add src/emule_indexer/adapters/mule_ec/mapping.py tests/adapters/mule_ec/test_mapping.py
git commit -m "feat(adapters): EC search results -> FileObservation mapping (capture-all, counted skips)"
```

---

## Task 12: Client — cycle de recherche (start/fetch/stop/progress)

**Files:**
- Modify: `tests/adapters/mule_ec/test_client.py` (le code de prod de la Task 10 couvre déjà ces méthodes)

- [ ] **Step 1: Ajouter les tests qui échouent (ou verrouillent, si Task 10 complète)**

Mettre à jour les imports de `tests/adapters/mule_ec/test_client.py` — fusionner dans les blocs existants (pas de ligne d'import séparée d'un module déjà importé : ruff I001), et insérer l'import `ports` AVANT la ligne `from tests.adapters...` :
```python
from emule_indexer.adapters.mule_ec.codec import (
    EcPacket,
    EcTag,
    encode_packet,
    hash16_tag,
    string_tag,
    uint_tag,
)
from emule_indexer.adapters.mule_ec.errors import (
    EcAuthError,
    EcConnectError,
    EcFailureError,
    EcProtocolError,
)
from emule_indexer.ports.mule_client import SearchChannel
```
*(`KadStatus`/`NetworkStatus` n'arrivent qu'en Task 13 — les importer ici ferait échouer ruff F401.)*

Puis ajouter à la fin du fichier :
```python
# ---------------------------------------------------------------- cycle de recherche


def _search_ok_reply() -> bytes:
    return encode_packet(
        EcPacket(
            codes.EC_OP_STRINGS,
            (string_tag(codes.EC_TAG_STRING, "Search in progress. Refetch results in a moment!"),),
        )
    )


def _results_reply(entries: tuple[EcTag, ...]) -> bytes:
    return encode_packet(EcPacket(codes.EC_OP_SEARCH_RESULTS, entries))


def _result_entry(name: str, with_hash: bool) -> EcTag:
    children: list[EcTag] = [
        string_tag(codes.EC_TAG_PARTFILE_NAME, name),
        uint_tag(codes.EC_TAG_PARTFILE_SIZE_FULL, 1000),
        uint_tag(codes.EC_TAG_PARTFILE_SOURCE_COUNT, 3),
    ]
    if with_hash:
        children.append(hash16_tag(codes.EC_TAG_PARTFILE_HASH, bytes(range(16))))
    return EcTag(codes.EC_TAG_SEARCHFILE, codes.EC_TAGTYPE_UINT8, b"\x01", tuple(children))


async def _connected(server: FakeEcServer) -> AmuleEcClient:
    client = AmuleEcClient("127.0.0.1", server.port, _PASSWORD, timeout=2.0)
    await client.connect()
    return client


@pytest.mark.asyncio
async def test_start_search_sends_the_documented_tree_per_channel() -> None:
    replies = _auth_replies(1) + [_search_ok_reply(), _search_ok_reply()]
    async with FakeEcServer(replies) as server:
        client = await _connected(server)
        await client.start_search("keroro", SearchChannel.GLOBAL)
        await client.start_search("titar", SearchChannel.KAD)
        await client.close()
        global_req, kad_req = server.received[2], server.received[3]
        for request, search_type, keyword in (
            (global_req, codes.EC_SEARCH_GLOBAL, "keroro"),
            (kad_req, codes.EC_SEARCH_KAD, "titar"),
        ):
            assert request.opcode == codes.EC_OP_SEARCH_START
            search_tag = request.find(codes.EC_TAG_SEARCH_TYPE)
            assert search_tag is not None
            assert search_tag.int_value() == search_type  # valeur PROPRE = type (réf. §5)
            name = search_tag.find(codes.EC_TAG_SEARCH_NAME)
            assert name is not None
            assert name.string_value() == keyword
            file_type = search_tag.find(codes.EC_TAG_SEARCH_FILE_TYPE)
            assert file_type is not None
            assert file_type.string_value() == ""  # obligatoire, "" = tous (réf. §5)


@pytest.mark.asyncio
async def test_start_search_failure_raises_ec_failure_with_daemon_message() -> None:
    failed = encode_packet(
        EcPacket(codes.EC_OP_FAILED, (string_tag(codes.EC_TAG_STRING, "Kad is not running"),))
    )
    async with FakeEcServer(_auth_replies(1) + [failed]) as server:
        client = await _connected(server)
        with pytest.raises(EcFailureError, match="Kad is not running"):
            await client.start_search("keroro", SearchChannel.KAD)
        await client.close()


@pytest.mark.asyncio
async def test_fetch_results_maps_keyword_provenance_and_accumulates_skips() -> None:
    replies = _auth_replies(1) + [
        _search_ok_reply(),
        _results_reply((_result_entry("a.avi", True), _result_entry("sans-hash.avi", False))),
        _results_reply((_result_entry("sans-hash2.avi", False),)),
    ]
    async with FakeEcServer(replies) as server:
        client = await _connected(server)
        await client.start_search("keroro", SearchChannel.GLOBAL)
        first = await client.fetch_results()
        assert [observation.filename for observation in first] == ["a.avi"]
        assert first[0].keyword == "keroro"  # provenance posée par le client
        assert client.skipped_entries_total == 1
        second = await client.fetch_results()
        assert second == ()
        assert client.skipped_entries_total == 2  # compteur CUMULATIF (DÉCISION 6)
        await client.close()


@pytest.mark.asyncio
async def test_stop_search_expects_misc_data_reply() -> None:
    stop_ok = encode_packet(EcPacket(codes.EC_OP_MISC_DATA))
    async with FakeEcServer(_auth_replies(1) + [stop_ok]) as server:
        client = await _connected(server)
        await client.stop_search()  # réponse EC_OP_MISC_DATA (réf. §5) : pas d'exception
        assert server.received[2].opcode == codes.EC_OP_SEARCH_STOP
        await client.close()


@pytest.mark.asyncio
async def test_unexpected_reply_opcode_raises_protocol_error() -> None:
    noop_reply = encode_packet(EcPacket(codes.EC_OP_NOOP))
    async with FakeEcServer(_auth_replies(1) + [noop_reply]) as server:
        client = await _connected(server)
        with pytest.raises(EcProtocolError, match="attendu"):
            await client.stop_search()
        await client.close()


def _progress_reply(value: int) -> bytes:
    return encode_packet(
        EcPacket(codes.EC_OP_SEARCH_PROGRESS, (uint_tag(codes.EC_TAG_SEARCH_STATUS, value),))
    )


@pytest.mark.asyncio
async def test_search_progress_follows_the_amulecmd_convention() -> None:
    replies = _auth_replies(1) + [
        _progress_reply(42),  # globale : pourcentage
        _progress_reply(100),
        _progress_reply(0xFFFF),  # locale : pas de mesure → None (réf. §5)
        _progress_reply(0xFFFE),  # Kad fini → None
        encode_packet(EcPacket(codes.EC_OP_SEARCH_PROGRESS)),  # tag absent → None
    ]
    async with FakeEcServer(replies) as server:
        client = await _connected(server)
        assert await client.search_progress() == 42
        assert await client.search_progress() == 100
        assert await client.search_progress() is None
        assert await client.search_progress() is None
        assert await client.search_progress() is None
        await client.close()


@pytest.mark.asyncio
async def test_operations_without_connect_raise_connect_error() -> None:
    client = AmuleEcClient("127.0.0.1", 1, _PASSWORD, timeout=2.0)
    with pytest.raises(EcConnectError, match="non connecté"):
        await client.fetch_results()
```

- [ ] **Step 2: Lancer pour vérifier**

Run: `uv run pytest tests/adapters/mule_ec/test_client.py --no-cov -q`
Expected: PASS — l'arbre `SEARCH_START` (type en valeur PROPRE, nom + type de fichier "" en enfants) est exactement celui de la réf. §5 pour les DEUX canaux ; `EC_OP_FAILED` → `EcFailureError` avec le message du daemon ; provenance + compteur cumulatif d'écartés ; progression 42/100/0xFFFF/0xFFFE/absent ; garde « non connecté ».

- [ ] **Step 3: Vérifier lint + types (PAS la suite complète)**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert (le run avec coverage attend la Task 13 : les branches CONNSTATE de `client.py` ne sont pas encore exercées — enchaîner directement).

- [ ] **Step 4: Commit — SAUTÉ (commit unique au Step 6 de la Task 10, après le gate vert de la Task 13 ; voir note d'ordonnancement)**

---

## Task 13: Client — statut réseau (`network_status`)

**Files:**
- Modify: `tests/adapters/mule_ec/test_client.py`

- [ ] **Step 1: Ajouter les tests**

Ajouter à la fin de `tests/adapters/mule_ec/test_client.py` :
```python
# ---------------------------------------------------------------- statut réseau


def _connstate_reply(bits: int, children: tuple[EcTag, ...] = ()) -> bytes:
    return encode_packet(
        EcPacket(codes.EC_OP_MISC_DATA, (uint_tag(codes.EC_TAG_CONNSTATE, bits, children),))
    )


def _server_tag(with_name: bool) -> EcTag:
    children = (string_tag(codes.EC_TAG_SERVER_NAME, "TestServer"),) if with_name else ()
    return EcTag(
        codes.EC_TAG_SERVER,
        codes.EC_TAGTYPE_IPV4,
        bytes([1, 2, 3, 4]) + (4661).to_bytes(2, "big"),
        children,
    )


async def _status_for(bits: int, children: tuple[EcTag, ...] = ()) -> NetworkStatus:
    async with FakeEcServer(_auth_replies(1) + [_connstate_reply(bits, children)]) as server:
        client = await _connected(server)
        status = await client.network_status()
        # Le client a bien demandé le niveau de détail CMD (réf. §6).
        request = server.received[2]
        assert request.opcode == codes.EC_OP_GET_CONNSTATE
        detail = request.find(codes.EC_TAG_DETAIL_LEVEL)
        assert detail is not None
        assert detail.int_value() == codes.EC_DETAIL_CMD
        await client.close()
        return status


@pytest.mark.asyncio
async def test_network_status_connected_high_id_with_server() -> None:
    # bits 0x15 = eD2k connecté | Kad connecté | Kad lancé ; ID 0x02000001 ≥ 16777216 → High.
    status = await _status_for(
        0x15,
        (
            _server_tag(with_name=True),
            uint_tag(codes.EC_TAG_ED2K_ID, 0x02000001),
            uint_tag(codes.EC_TAG_CLIENT_ID, 0x02000001),
        ),
    )
    assert status.ed2k_id == 0x02000001
    assert status.ed2k_high is True
    assert status.kad_status is KadStatus.CONNECTED
    assert status.server_name == "TestServer"
    assert status.server_addr == "1.2.3.4:4661"


@pytest.mark.asyncio
async def test_network_status_low_id() -> None:
    # LowID si < 16777216 (HIGHEST_LOWID_ED2K_KAD, réf. §6).
    status = await _status_for(
        0x01, (_server_tag(with_name=True), uint_tag(codes.EC_TAG_ED2K_ID, 100))
    )
    assert status.ed2k_id == 100
    assert status.ed2k_high is False
    assert status.kad_status is KadStatus.OFF  # ni 0x10 → Kad arrêté


@pytest.mark.asyncio
async def test_network_status_kad_running_not_connected_and_no_ed2k() -> None:
    status = await _status_for(0x10)
    assert status.ed2k_id is None
    assert status.ed2k_high is False
    assert status.kad_status is KadStatus.RUNNING
    assert status.server_name is None
    assert status.server_addr is None


@pytest.mark.asyncio
async def test_network_status_kad_firewalled() -> None:
    # 0x10|0x04|0x08 = connecté mais firewalled (réf. §6).
    status = await _status_for(0x1C)
    assert status.kad_status is KadStatus.FIREWALLED


@pytest.mark.asyncio
async def test_network_status_tolerates_connected_ed2k_without_id_or_server_tags() -> None:
    # Défensif : bit eD2k posé mais sous-tags absents → None partout, pas d'exception.
    status = await _status_for(0x01)
    assert status.ed2k_id is None
    assert status.ed2k_high is False
    assert status.server_addr is None


@pytest.mark.asyncio
async def test_network_status_server_without_name_child() -> None:
    status = await _status_for(
        0x01, (_server_tag(with_name=False), uint_tag(codes.EC_TAG_ED2K_ID, 100))
    )
    assert status.server_addr == "1.2.3.4:4661"
    assert status.server_name is None


@pytest.mark.asyncio
async def test_network_status_without_connstate_tag_raises_protocol_error() -> None:
    empty_reply = encode_packet(EcPacket(codes.EC_OP_MISC_DATA))
    async with FakeEcServer(_auth_replies(1) + [empty_reply]) as server:
        client = await _connected(server)
        with pytest.raises(EcProtocolError, match="CONNSTATE"):
            await client.network_status()
        await client.close()
```

Remplacer la ligne d'import `ports.mule_client` existante (Task 12) par la version complète :
```python
from emule_indexer.ports.mule_client import KadStatus, NetworkStatus, SearchChannel
```

- [ ] **Step 2: Lancer pour vérifier**

Run: `uv run pytest tests/adapters/mule_ec/test_client.py --no-cov -q`
Expected: PASS — les 4 états Kad, HighID/LowID/non connecté, serveur avec/sans nom, sous-tags manquants tolérés, CONNSTATE manquant → `EcProtocolError`, et la requête sortante porte bien `EC_DETAIL_CMD`.

- [ ] **Step 3: Vérifier la suite complète + types + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert ; coverage 100 % — TOUTES les branches de `client.py` sont maintenant exercées (mot de passe vide/non ; AUTH_FAIL/SALT/inattendu ; sel présent/absent ; verdict OK/FAIL/inattendu ; transport présent/absent ; FAILED/attendu/inattendu ; progress tag présent/absent, ≤100/>100 ; chaque bit CONNSTATE dans les deux états ; id/server/name présents/absents ; high/low).

- [ ] **Step 4: Commit — SAUTÉ : revenir au Step 6 de la Task 10 (commit unique `client.py` + `test_client.py`, le gate ci-dessus étant vert)**

---

## Task 14: Outil probe CLI (`emule_indexer.tools.ec_probe`)

**Files:**
- Create: `src/emule_indexer/tools/__init__.py`
- Create: `src/emule_indexer/tools/ec_probe.py`
- Create: `tests/tools/__init__.py`
- Create: `tests/tools/test_ec_probe.py`

- [ ] **Step 1: Écrire les tests qui échouent**

`tests/tools/__init__.py` :
```python
"""Tests des outils CLI."""
```

`tests/tools/test_ec_probe.py` :
```python
import pytest

from emule_indexer.adapters.mule_ec.client import AmuleEcClient
from emule_indexer.adapters.mule_ec.errors import EcAuthError, EcError
from emule_indexer.domain.observation import FileObservation
from emule_indexer.ports.mule_client import KadStatus, NetworkStatus, SearchChannel
from emule_indexer.tools.ec_probe import (
    _default_client,
    build_parser,
    main,
    search_and_wait,
)

_STATUS_FULL = NetworkStatus(
    ed2k_id=33554433,
    ed2k_high=True,
    kad_status=KadStatus.CONNECTED,
    server_name="TestServer",
    server_addr="1.2.3.4:4661",
)
_STATUS_OFF = NetworkStatus(ed2k_id=None, ed2k_high=False, kad_status=KadStatus.OFF)

_OBSERVATION = FileObservation(
    ed2k_hash="000102030405060708090a0b0c0d0e0f",
    filename="Keroro 062A.avi",
    size_bytes=234567890,
    source_count=5,
    complete_source_count=2,
    keyword="keroro",
    raw_meta=(("0x0308", "0"), ("0x0999", "mystère")),
)


class FakeMuleClient:
    """Faux client conforme au port : journal d'appels + données en conserve."""

    def __init__(
        self,
        *,
        status: NetworkStatus,
        batches: list[tuple[FileObservation, ...]],
        progresses: list[int | None],
        connect_error: EcError | None = None,
    ) -> None:
        self.calls: list[str] = []
        self._status = status
        self._batches = batches
        self._progresses = progresses
        self._connect_error = connect_error

    async def connect(self) -> None:
        self.calls.append("connect")
        if self._connect_error is not None:
            raise self._connect_error

    async def close(self) -> None:
        self.calls.append("close")

    async def start_search(self, keyword: str, channel: SearchChannel) -> None:
        self.calls.append(f"start:{keyword}:{channel.value}")

    async def fetch_results(self) -> tuple[FileObservation, ...]:
        self.calls.append("fetch")
        return self._batches.pop(0) if len(self._batches) > 1 else self._batches[0]

    async def stop_search(self) -> None:
        self.calls.append("stop")

    async def search_progress(self) -> int | None:
        self.calls.append("progress")
        return self._progresses.pop(0) if len(self._progresses) > 1 else self._progresses[0]

    async def network_status(self) -> NetworkStatus:
        self.calls.append("status")
        return self._status


# ---------------------------------------------------------------- parsing


def test_parser_defaults() -> None:
    args = build_parser().parse_args(["--password", "pwd", "--keyword", "keroro"])
    assert args.host == "127.0.0.1"
    assert args.port == 4712
    assert args.channel == "global"
    assert args.timeout == 60.0
    assert args.interval == 5.0


def test_parser_rejects_unknown_channel() -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--password", "p", "--keyword", "k", "--channel", "web"])
    assert excinfo.value.code == 2


def test_parser_requires_password_and_keyword() -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args([])
    assert excinfo.value.code == 2


# ---------------------------------------------------------------- cycle complet via main()


def test_main_success_dumps_status_results_and_raw_meta(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeMuleClient(status=_STATUS_FULL, batches=[(_OBSERVATION,)], progresses=[100])
    code = main(
        ["--password", "pwd", "--keyword", "keroro"],
        client_factory=lambda args: fake,
    )
    assert code == 0
    assert fake.calls == [
        "connect",
        "status",
        "start:keroro:global",
        "fetch",
        "progress",
        "stop",
        "close",
    ]
    out = capsys.readouterr().out
    assert "TestServer (1.2.3.4:4661)" in out
    assert "Keroro 062A.avi" in out
    assert "hash=000102030405060708090a0b0c0d0e0f" in out
    # Dump de TOUS les tags reçus, y compris inconnus (noms bruts/hex) — livrable 4.
    assert "raw 0x0308 = 0" in out
    assert "raw 0x0999 = mystère" in out


def test_main_kad_channel_and_status_without_server(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeMuleClient(status=_STATUS_OFF, batches=[()], progresses=[100])
    code = main(
        ["--password", "pwd", "--keyword", "keroro", "--channel", "kad"],
        client_factory=lambda args: fake,
    )
    assert code == 0
    assert "start:keroro:kad" in fake.calls
    out = capsys.readouterr().out
    assert "serveur : —" in out  # branche « pas de serveur » du format
    assert "total : 0 résultat(s)" in out  # boucle d'observations à zéro itération


def test_main_returns_1_on_ec_error_and_still_closes(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeMuleClient(
        status=_STATUS_OFF,
        batches=[()],
        progresses=[None],
        connect_error=EcAuthError("Invalid password"),
    )
    code = main(["--password", "bad", "--keyword", "keroro"], client_factory=lambda args: fake)
    assert code == 1
    assert fake.calls == ["connect", "close"]  # close() TOUJOURS appelé (finally)
    assert "Invalid password" in capsys.readouterr().err


# ---------------------------------------------------------------- search_and_wait


@pytest.mark.asyncio
async def test_search_and_wait_polls_until_budget_exhausted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    sleeps: list[float] = []

    async def _instant_sleep(delay: float) -> None:
        sleeps.append(delay)

    client = FakeMuleClient(status=_STATUS_OFF, batches=[()], progresses=[None])
    results = await search_and_wait(
        client, "keroro", SearchChannel.GLOBAL, timeout=10.0, interval=5.0, sleep=_instant_sleep
    )
    assert results == ()
    assert sleeps == [5.0]  # 2 relevés (ceil(10/5)), 1 seul sleep (pas après le dernier)
    assert client.calls.count("fetch") == 2
    assert client.calls[-1] == "stop"
    assert "progression ?" in capsys.readouterr().out  # progress None affiché « ? »


@pytest.mark.asyncio
async def test_search_and_wait_breaks_early_when_progress_reaches_100() -> None:
    sleeps: list[float] = []

    async def _instant_sleep(delay: float) -> None:
        sleeps.append(delay)

    client = FakeMuleClient(status=_STATUS_OFF, batches=[()], progresses=[100])
    await search_and_wait(
        client, "keroro", SearchChannel.GLOBAL, timeout=60.0, interval=5.0, sleep=_instant_sleep
    )
    assert sleeps == []  # arrêt anticipé : aucun sleep
    assert client.calls.count("fetch") == 1


# ---------------------------------------------------------------- fabrique réelle


def test_default_client_builds_an_amule_ec_client() -> None:
    args = build_parser().parse_args(
        ["--host", "homelab", "--port", "4713", "--password", "pwd", "--keyword", "k"]
    )
    client = _default_client(args)
    assert isinstance(client, AmuleEcClient)  # constructeur sans I/O : sûr en test
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `uv run pytest tests/tools/test_ec_probe.py --no-cov -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'emule_indexer.tools'`.

- [ ] **Step 3: Écrire l'implémentation**

`src/emule_indexer/tools/__init__.py` :
```python
"""Outils CLI (frontière I/O assumée : print/argparse, testés via faux clients)."""
```

`src/emule_indexer/tools/ec_probe.py` :
```python
"""Sonde EC : recherche réelle contre un amuled + dump de TOUS les tags reçus (spec §8.4).

Usage :
    uv run python -m emule_indexer.tools.ec_probe --host 127.0.0.1 --port 4712 \\
        --password <pwd> --keyword keroro --channel global

C'est l'outil de MESURE de la richesse des champs (rapport livrable 5) : chaque entrée
``raw_meta`` (tags non mappés, connus ou inconnus) est affichée nom-hex + valeur. La
convenance ``search_and_wait`` (poll + budget) vit ICI, pas dans le port : le polling
appartient à l'appelant (spec §3) — ici l'appelant, c'est nous. Réutilisable tel quel
contre le homelab.
"""

import argparse
import asyncio
import math
import sys
from collections.abc import Awaitable, Callable, Sequence

from emule_indexer.adapters.mule_ec.client import AmuleEcClient
from emule_indexer.adapters.mule_ec.errors import EcError
from emule_indexer.domain.observation import FileObservation
from emule_indexer.ports.mule_client import MuleClient, NetworkStatus, SearchChannel

Sleeper = Callable[[float], Awaitable[None]]
ClientFactory = Callable[[argparse.Namespace], MuleClient]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ec_probe", description="Sonde EC : recherche réelle + dump des tags reçus"
    )
    parser.add_argument("--host", default="127.0.0.1", help="hôte amuled")
    parser.add_argument("--port", type=int, default=4712, help="port EC (ECPort)")
    parser.add_argument("--password", required=True, help="mot de passe EC (en clair)")
    parser.add_argument("--keyword", required=True, help="mot-clé de recherche")
    parser.add_argument(
        "--channel",
        choices=[channel.value for channel in SearchChannel],
        default=SearchChannel.GLOBAL.value,
        help="canal de recherche",
    )
    parser.add_argument("--timeout", type=float, default=60.0, help="budget total de polling (s)")
    parser.add_argument("--interval", type=float, default=5.0, help="intervalle entre relevés (s)")
    return parser


def format_status(status: NetworkStatus) -> str:
    server = "—"
    if status.server_name is not None or status.server_addr is not None:
        server = f"{status.server_name or '?'} ({status.server_addr or '?'})"
    return (
        "[probe] statut réseau :\n"
        f"  eD2k : id={status.ed2k_id} high={status.ed2k_high}\n"
        f"  Kad  : {status.kad_status.value}\n"
        f"  serveur : {server}"
    )


def format_observation(observation: FileObservation) -> str:
    lines = [
        f"[probe] {observation.filename}",
        f"  hash={observation.ed2k_hash} taille={observation.size_bytes} o",
        f"  sources={observation.source_count} complètes={observation.complete_source_count}",
    ]
    for name, value in observation.raw_meta:
        lines.append(f"  raw {name} = {value}")
    return "\n".join(lines)


async def search_and_wait(
    client: MuleClient,
    keyword: str,
    channel: SearchChannel,
    *,
    timeout: float,
    interval: float,
    sleep: Sleeper = asyncio.sleep,
) -> tuple[FileObservation, ...]:
    """Lance une recherche puis relève à intervalle fixe jusqu'au budget ``timeout``.

    Horloge-indépendant (déterministe en test) : ``ceil(timeout / interval)`` relevés,
    ``sleep(interval)`` entre deux, arrêt anticipé si la progression atteint 100 %.
    """
    await client.start_search(keyword, channel)
    rounds = max(1, math.ceil(timeout / interval))
    results: tuple[FileObservation, ...] = ()
    for round_index in range(rounds):
        results = await client.fetch_results()
        progress = await client.search_progress()
        shown = "?" if progress is None else f"{progress}%"
        print(
            f"[probe] relevé {round_index + 1}/{rounds} : "
            f"{len(results)} résultat(s), progression {shown}"
        )
        if progress == 100:
            break
        if round_index < rounds - 1:
            await sleep(interval)
    await client.stop_search()
    return results


async def run_probe(
    client: MuleClient, args: argparse.Namespace, *, sleep: Sleeper = asyncio.sleep
) -> int:
    try:
        await client.connect()
        print(format_status(await client.network_status()))
        results = await search_and_wait(
            client,
            str(args.keyword),
            SearchChannel(str(args.channel)),
            timeout=float(args.timeout),
            interval=float(args.interval),
            sleep=sleep,
        )
        print(f"[probe] total : {len(results)} résultat(s)")
        for observation in results:
            print(format_observation(observation))
    finally:
        await client.close()
    return 0


def _default_client(args: argparse.Namespace) -> MuleClient:
    return AmuleEcClient(str(args.host), int(args.port), str(args.password))


def main(
    argv: Sequence[str] | None = None, *, client_factory: ClientFactory = _default_client
) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(run_probe(client_factory(args), args))
    except EcError as exc:
        print(f"[probe] ERREUR : {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
```

- [ ] **Step 4: Lancer pour vérifier que tout passe**

Run: `uv run pytest tests/tools/test_ec_probe.py --no-cov -q`
Expected: PASS — 9 tests verts : défauts/choix/canal invalide/options requises, cycle complet (ordre des appels journalisé, statut + observations + lignes `raw` dans stdout), canal kad + statut sans serveur, erreur EC → code 1 + stderr + `close()` quand même, budget épuisé (sleeps exacts), arrêt anticipé à 100 %, fabrique réelle.

- [ ] **Step 5: Vérifier la suite complète + types + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert ; coverage 100 % sur `ec_probe.py` (les deux branches de chaque conditionnel : serveur affiché/«—», `raw_meta` vide/non, `progress` None/100/intermédiaire, dernier relevé sans sleep, `EcError` attrapée/non ; le bloc `__main__` est exclu par `# pragma: no cover`).

- [ ] **Step 6: Vérifier le point d'entrée réel (smoke manuel, sans amuled : échec PROPRE attendu)**

Run: `uv run python -m emule_indexer.tools.ec_probe --password x --keyword keroro --timeout 1 --interval 1 ; echo "exit=$?"`
Expected: `[probe] ERREUR : connexion à 127.0.0.1:4712 impossible : ...` sur stderr puis `exit=1` — le module s'exécute, l'erreur réseau est signalée proprement, pas de traceback.

- [ ] **Step 7: Commit**

```bash
git add src/emule_indexer/tools tests/tools
git commit -m "feat(tools): ec_probe CLI (real search, NetworkStatus dump, raw tag dump)"
```

---

## Task 15: Intégration testcontainers — `amuled` réel (OBLIGATOIRE avant le tag)

**Files:**
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/test_amuled_ec.py`

> Docker requis sur la machine. Premier run : pull de l'image (`ngosang/amule:3.0.0-1`, DÉCISION 10) + premier démarrage potentiellement lent (scan des dossiers partagés, réf. §8) → readiness attendue jusqu'à 180 s. Ces tests sont DÉSELECTIONNÉS par défaut (marker) et HORS coverage (run avec `--no-cov`) ; le gate 100 % reste calculé sur les tests rapides (spec §3).

- [ ] **Step 1: Écrire les tests d'intégration**

`tests/integration/__init__.py` :
```python
"""Tests d'intégration (Docker requis, marker ec_integration, hors coverage)."""
```

`tests/integration/test_amuled_ec.py` :
```python
"""Intégration contre un amuled RÉEL (image ngosang/amule, réf. protocole §8).

Run dédié : uv run pytest -m ec_integration --no-cov
Valide : auth réelle (formule du hash §4 contre le vrai daemon), échec d'auth, statut
réseau, et le CYCLE complet start/fetch/stop — les résultats peuvent être vides sans
accès réseau eD2k : c'est le cycle qui est validé (spec §7.3), la richesse des champs
réels vient du probe (rapport livrable 5).
"""

from collections.abc import Iterator

import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs

from emule_indexer.adapters.mule_ec.client import AmuleEcClient
from emule_indexer.adapters.mule_ec.errors import EcAuthError, EcFailureError
from emule_indexer.ports.mule_client import KadStatus, NetworkStatus, SearchChannel

pytestmark = pytest.mark.ec_integration

_EC_PASSWORD = "indexer-ec-test"
_IMAGE = "ngosang/amule:3.0.0-1"  # DÉCISION 10 : image Docker Hub du dépôt ngosang/docker-amule


@pytest.fixture(scope="module")
def amuled() -> Iterator[tuple[str, int]]:
    container = DockerContainer(_IMAGE).with_env("GUI_PWD", _EC_PASSWORD).with_exposed_ports(4712)
    container.start()
    try:
        # Readiness (réf. §8) : « *** TCP socket (ECServer) listening on 0.0.0.0:4712 »
        # (ExternalConn.cpp:333). Motif regex SANS les parenthèses littérales.
        wait_for_logs(container, r"listening on 0\.0\.0\.0:4712", timeout=180)
        yield container.get_container_host_ip(), int(container.get_exposed_port(4712))
    finally:
        container.stop()


@pytest.mark.asyncio
async def test_real_auth_succeeds(amuled: tuple[str, int]) -> None:
    host, port = amuled
    client = AmuleEcClient(host, port, _EC_PASSWORD, timeout=30.0)
    await client.connect()  # formule du hash §4 validée contre le VRAI daemon
    await client.close()


@pytest.mark.asyncio
async def test_real_auth_fails_with_wrong_password(amuled: tuple[str, int]) -> None:
    host, port = amuled
    client = AmuleEcClient(host, port, "mauvais-mot-de-passe", timeout=30.0)
    with pytest.raises(EcAuthError):
        await client.connect()


@pytest.mark.asyncio
async def test_real_network_status(amuled: tuple[str, int]) -> None:
    host, port = amuled
    client = AmuleEcClient(host, port, _EC_PASSWORD, timeout=30.0)
    await client.connect()
    try:
        status = await client.network_status()
        assert isinstance(status, NetworkStatus)
        assert status.kad_status in set(KadStatus)  # état réel quelconque, mais DÉCODÉ
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_real_search_cycle(amuled: tuple[str, int]) -> None:
    host, port = amuled
    client = AmuleEcClient(host, port, _EC_PASSWORD, timeout=30.0)
    await client.connect()
    try:
        try:
            await client.start_search("keroro", SearchChannel.GLOBAL)
        except EcFailureError as exc:
            # amuled a répondu EC_OP_FAILED proprement (pas de serveur eD2k joignable
            # depuis le conteneur) : le cycle requête/réponse applicatif EST validé,
            # avec le message du daemon transmis (spec §6, DÉCISION 10).
            assert str(exc)
            return
        progress = await client.search_progress()
        assert progress is None or 0 <= progress <= 100
        results = await client.fetch_results()  # possiblement vide : le CYCLE compte
        assert isinstance(results, tuple)
        await client.stop_search()
    finally:
        await client.close()
```

- [ ] **Step 2: Vérifier que le run par défaut les DÉSELECTIONNE**

Run: `uv run pytest -q`
Expected: PASS — la ligne de résumé mentionne `4 deselected` ; coverage toujours 100 % (les tests d'intégration ne comptent pas dans le gate).

- [ ] **Step 3: Lancer l'intégration réelle (Docker requis)**

Run: `uv run pytest -m ec_integration --no-cov -q`
Expected: `4 passed` (lent au premier run : pull de l'image + démarrage d'amuled). En cas d'échec d'auth réel, suspecter d'abord la formule du hash (réf. §9 piège 5 : casses hex mélangées → « wrong password » silencieux) — les logs du conteneur montrent « Access granted. » ou « Unauthorized access attempt » (réf. §8).

- [ ] **Step 4: Vérifier lint + types**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert (l'override mypy `testcontainers.*` de la Task 1 absorbe l'absence éventuelle de stubs).

- [ ] **Step 5: Commit**

```bash
git add tests/integration
git commit -m "test: testcontainers integration vs real amuled (auth, status, search cycle)"
```

---

## Task 16: Rapport de richesse des champs (livrable 5)

**Files:**
- Create: `docs/reference/2026-06-11-ec-field-richness.md`

- [ ] **Step 1: Écrire le rapport**

`docs/reference/2026-06-11-ec-field-richness.md` (fence externe à 4 backticks : le document contient lui-même un bloc ```bash) :
````markdown
# Richesse des champs EC — résultats de recherche (2026-06-11)

> Livrable 5 du plan B (`v0.5.0-ec-adapter`). Compare les champs ESPÉRÉS par la table
> `file_observations` (spec MVP §11) aux champs que EC expose RÉELLEMENT sur un résultat
> de recherche. Source : `docs/reference/ec-protocol.md` §5 (vérifié sur les sources
> aMule 2.3.3 et 3.0.0 — `CEC_SearchFile_Tag`, liste EXHAUSTIVE), confirmé par la suite
> d'intégration `ec_integration` contre `ngosang/amule:3.0.0-1`.

## Verdict en une ligne

**EC n'expose AUCUNE métadonnée média sur un résultat de recherche** (ni durée, ni
bitrate, ni codec) : la moitié « média » du schéma §11 ne sera PAS alimentée par la
recherche — elle devra venir d'ailleurs (analyse locale post-download, plan D/verifier).

## Champ par champ (§11 `file_observations` vs EC)

| Champ espéré (§11)        | Tag EC                                   | Exposé ? | Constat |
|---------------------------|------------------------------------------|----------|---------|
| `filename`                | `EC_TAG_PARTFILE_NAME` (0x0301)          | OUI      | nom observé, UTF-8 |
| `size_bytes` (via `files`)| `EC_TAG_PARTFILE_SIZE_FULL` (0x0303)     | OUI      | octets, entier à largeur variable |
| `ed2k_hash` (clé contenu) | `EC_TAG_PARTFILE_HASH` (0x031E)          | OUI      | MD4 16 octets — SEUL identifiant stable (l'ECID est volatil, réf. §9 piège 13) |
| `source_count`            | `EC_TAG_PARTFILE_SOURCE_COUNT` (0x030A)  | OUI      | |
| `complete_source_count`   | `EC_TAG_PARTFILE_SOURCE_COUNT_XFER` (0x030D) | OUI  | nom amont TROMPEUR (« XFER ») mais c'est bien CompleteSourceCount (réf. §9 piège 12) |
| `media_length_sec`        | —                                        | **NON**  | aucun tag média ne transite par EC |
| `bitrate`                 | —                                        | **NON**  | idem |
| `codec`                   | —                                        | **NON**  | idem |
| `file_type`               | —                                        | **NON**  | `EC_TAG_SEARCH_FILE_TYPE` (0x0705) est un FILTRE de requête, pas une métadonnée de résultat |
| `raw_meta` JSON           | tous les tags non mappés                 | OUI      | capture-all tenu dès la frontière (mapper + `FileObservation.raw_meta`) |
| `keyword`                 | (posé par le client : provenance)        | OUI      | |
| `observed_at`, `node_id`  | (colonnes de persistance, plan A)        | n/a      | injectées par l'adapter DB |

## Champs exposés par EC NON prévus par §11 (récupérés via `raw_meta`)

- `EC_TAG_PARTFILE_STATUS` (0x0308) — statut download côté daemon ; 0 = nouveau.
- `EC_TAG_SEARCH_PARENT` (0x0709) — ECID du parent dans la variante groupée (volatil).
- `EC_TAG_KNOWNFILE_RATING` (0x040F) — 3.0.0 uniquement, si le fichier est noté.

Aucune migration nécessaire pour les accueillir : ils vivent déjà dans `raw_meta`.

## Conséquences pour le plan A (schéma `catalog.db`)

1. **Garder les colonnes `media_*` nullables** : elles resteront NULL pour toute
   observation issue de recherche. Les renseigner passera par l'analyse locale d'un
   fichier téléchargé (verifier, `file_verifications.real_meta`, §11) — pas par EC.
2. **Garder `raw_meta`** (capture-all) : status/parent/rating y sont déjà, et tout tag
   futur d'aMule y atterrira sans changement de schéma.
3. **`complete_source_count` est fiable et précieux** (priorisation des cibles `download`).
4. **Ne JAMAIS persister l'ECID** : identifiant de session, écrasé à chaque
   `EC_OP_SEARCH_START` ; seule clé stable = hash MD4.
5. Le compteur d'entrées écartées (`AmuleEcClient.skipped_entries_total`) est prêt pour
   la métrique du plan E.

## Confirmation empirique

- La suite `ec_integration` (testcontainers, `ngosang/amule:3.0.0-1`) valide : auth
  réelle (formule §4), refus du mauvais mot de passe, décodage du statut réseau, cycle
  complet `start_search`/`fetch_results`/`stop_search`. Sans connectivité eD2k/Kad du
  conteneur, les résultats peuvent être vides : la richesse « réseau réel » sera mesurée
  au homelab avec le probe (réutilisable tel quel) :

  ```bash
  uv run python -m emule_indexer.tools.ec_probe --host <homelab> --port 4712 \
      --password <pwd> --keyword keroro --channel global
  ```

  Le probe affiche chaque entrée `raw_meta` (nom hex + valeur) : toute trouvaille
  inattendue lors d'un run réel s'ajoute ici en annexe.
````

- [ ] **Step 2: Vérifier que rien d'autre n'a bougé**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert (document seul, aucun code touché).

- [ ] **Step 3: Commit**

```bash
git add "docs/reference/2026-06-11-ec-field-richness.md"
git commit -m "docs: EC field-richness report (hoped vs exposed, consequences for plan A schema)"
```

---

## Task 17: Revue holistique finale + tag `v0.5.0-ec-adapter`

**Files:** (aucun fichier nouveau ; corrections éventuelles issues de la revue, puis tag git)

> La revue finale holistique a attrapé de vrais bugs transverses à chaque plan précédent — la garder (CLAUDE.md). Dérouler la checklist ; toute trouvaille = correction TDD (test d'abord) AVANT le tag.

- [ ] **Step 1: Vérifier la règle de dépendance (Clean Architecture)**

```bash
# 1. Le domaine reste PUR (seuls stdlib « pure » et le domaine lui-même) :
grep -rnE "^(import|from) " src/emule_indexer/domain --include="*.py" \
  | grep -vE "from (dataclasses|typing|collections\.abc|enum) import|import (datetime|unicodedata)|from emule_indexer\.domain"
# Expected: AUCUNE sortie.

# 2. ports/ n'importe QUE le domaine (+ stdlib de typage) :
grep -rnE "^(import|from) " src/emule_indexer/ports --include="*.py" \
  | grep -vE "from (dataclasses|typing|enum) import|from emule_indexer\.domain"
# Expected: AUCUNE sortie.

# 3. Personne ne voit un opcode hors de l'adapter (et du probe, frontière assumée) :
grep -rn "mule_ec" src/emule_indexer --include="*.py" \
  | grep -v "adapters/mule_ec/" | grep -v "tools/ec_probe.py"
# Expected: AUCUNE sortie.

# 4. Le domaine n'importe pas les ports ni les adapters :
grep -rn "emule_indexer.ports\|emule_indexer.adapters" src/emule_indexer/domain --include="*.py"
# Expected: AUCUNE sortie.
```

- [ ] **Step 2: Checklist de cohérence transverse (lire le code, pas survoler)**

- [ ] `FileObservation.to_candidate()` produit EXACTEMENT un `FileCandidate` du moteur (mêmes noms de champs : `filename`, `size_mb`, `duration_sec`, `bitrate_kbps`) — vérifier contre `src/emule_indexer/domain/matching/models.py`.
- [ ] Le client n'annonce AUCUNE capacité (`CAN_ZLIB`/`CAN_UTF8_NUMBERS`/`CAN_NOTIFY` absents d'AUTH_REQ) et n'émet jamais `EC_TAG_VERSION_ID` — cohérent avec la DÉCISION 2 et la réf. §4.
- [ ] Aucun `sleep`/boucle d'attente dans `adapters/mule_ec/` : `grep -rn "sleep\|retry" src/emule_indexer/adapters/mule_ec` ne sort RIEN.
- [ ] Aucune I/O dans `codec.py`/`mapping.py` (pas d'`asyncio`, pas de socket, pas de fichier) ; `zlib`/`hashlib` sont des calculs purs.
- [ ] Toutes les erreurs réseau qui sortent de l'adapter sont des `EcError` (jamais un `OSError`/`TimeoutError` nu) — relire les `except` de `transport.py`.
- [ ] Le port `MuleClient` est satisfait STRUCTURELLEMENT par `AmuleEcClient` : ajouter mentalement `client: MuleClient = AmuleEcClient(...)` — mypy le vérifie déjà via le test d'intégration et le probe (`_default_client -> MuleClient`).
- [ ] `MatchDecision`-style : `FileObservation` n'a NI `observed_at` NI `node_id` (colonnes d'adapter DB, plan A).
- [ ] Les docstrings citent la référence (`réf. §N` / fichier:ligne aMule) pour chaque fait wire-level non évident.

- [ ] **Step 3: Gate complet + intégration (les DEUX, verts, non négociable)**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: tout vert ; coverage **100 % branch** ; `4 deselected` (intégration).

Run: `uv run pytest -m ec_integration --no-cov -q`
Expected: `4 passed` — l'intégration réelle est VERTE au moment du tag (spec §3 : « verts avant de taguer, non négociable »).

- [ ] **Step 4: Tag annoté (NON poussé)**

```bash
git tag -a v0.5.0-ec-adapter -m "Adapter EC complet : codec pur, transport async, AmuleEcClient (auth/recherche/statut), mapping capture-all, probe CLI, intégration amuled réelle"
git tag -n1 | grep v0.5.0
```
Expected: le tag apparaît avec son message. Ne PAS pousser (convention du dépôt).

---

## Self-Review (rempli par l'auteur du plan)

- **Couverture de la spec (`2026-06-11-ec-adapter-design.md`) :**
  - **§2/§5 — connexion + auth challenge/réponse** → Task 10 (handshake exact réf. §4 : AUTH_REQ → SALT → PASSWD → OK/FAIL ; formule du hash avec 3 vecteurs précalculés ; sel à largeur variable ; mdp vide refusé avant I/O) + Task 15 (auth RÉELLE + échec réel). ✓
  - **§2/§5 — recherche mot-clé (global/kad), relevé, arrêt, progression** → Task 12 (arbre SEARCH_START conforme réf. §5 pour les 2 canaux, STRINGS/FAILED, fetch cumulatif + provenance, STOP→MISC_DATA, progression convention amulecmd) + Task 15 (cycle réel). ✓
  - **§2/§5 — statut réseau (ID High/Low, état Kad, serveur)** → Task 13 (bitfield CONNSTATE complet, 4 états Kad, seuil LowID 16777216, sous-tags défensifs) + Task 15. ✓
  - **§2/§4 — mapping → `FileObservation` + capture-all `raw_meta`** → Tasks 2 et 11 (champs §11, tuple gelé JSON-friendly, tags inconnus jamais une erreur, ECID jamais conservé). ✓
  - **§3 — codec générique pur/sync, usages minimaux câblés** → Tasks 5-8 (encode/décode N'IMPORTE QUEL paquet ; zéro I/O) ; le client ne câble QUE auth/recherche/statut (Task 10/12/13) — le plan D ajoutera des méthodes sans rouvrir codec/transport. ✓
  - **§3 — asyncio (transport/client), codec sync** → Task 9 (StreamReader/Writer, `wait_for`), codec sans une ligne d'async. ✓
  - **§3 — le polling appartient à l'appelant** → aucun sleep dans l'adapter (vérifié par grep, Task 17) ; `search_and_wait` UNIQUEMENT dans le probe (Task 14). ✓
  - **§3 — l'intégration fait foi, hors coverage, verte avant tag** → Tasks 1 (marker + déselection par défaut), 15 (testcontainers), 17 (re-run obligatoire avant tag). ✓
  - **§3 — constantes transcrites + référencées, auth vérifiée empiriquement** → Task 4 (`codes.py` = table réf. §7, commentaires fichier:ligne) + Task 15 (auth contre amuled réel). ✓
  - **§4 — fichiers exacts** → tous créés : `domain/observation.py`, `ports/mule_client.py`, `adapters/mule_ec/{codes,codec,transport,client,mapping}.py`, `tools/ec_probe.py` (+ `errors.py`, DÉCISION 1 — seul ajout, justifié). ✓
  - **§4 — port `MuleClient` à 7 méthodes, `SearchChannel`, `NetworkStatus` + enum Kad fermé** → Task 3, signatures identiques à la spec. ✓
  - **§6 — hiérarchie d'erreurs, timeout par lecture, parsing défensif (longueur/zlib bornés), tolérance aux inconnus, écartés comptés, échec applicatif distinct** → Tasks 4 (hiérarchie + `EcFailureError`), 7-8 (bornes 16 Mio/zlib/profondeur 32), 9 (timeouts), 11 (raw_meta, skips comptés, lot jamais en échec), 10/12 (FAILED → `EcFailureError` avec message). ✓
  - **§7.1 — codec : octets connus ↔ arbres, round-trip, hostiles** → Tasks 6-8 (trames dérivées octet par octet en commentaire, round-trip identité, tronqué/TAGLEN menteur/zlib corrompu-tronqué-bombe/profondeur/flags). ✓
  - **§7.2 — faux serveur EC en mémoire** → Task 9 (`FakeEcServer`, asyncio.start_server, rejoue des trames pré-encodées, FCFS). *(Pas de fixtures « capturées » d'un amuled réel à ce stade : les trames forgées sont dérivées de la référence vérifiée sources-en-main, et l'intégration §7.3 fournit la confrontation au réel ; le probe reste l'outil de capture pour enrichir plus tard.)* ✓
  - **§7.3 — testcontainers : config EC + readiness + auth/statut/cycle** → Task 15 (GUI_PWD, port 4712, log de readiness réf. §8, 4 tests). ✓
  - **§8 — livrables 1-6** → 1 : Tasks 5-13 ; 2 : Tasks 2-3 ; 3 : Task 15 ; 4 : Task 14 ; 5 : Task 16 ; 6 : Task 17. ✓
  - **§9 — questions laissées au plan** : opcodes/tags transcrits (Task 4), algo d'auth transcrit + vérifié (Tasks 10/15), format des entiers et flag de compression spécifiés (Tasks 5-8, DÉCISION 2), découpage TDD + fixtures forgées depuis la référence (toutes les tâches). ✓
- **Scan des placeholders :** aucun « TBD », aucun « similaire à la Task N », aucun « ajouter la gestion d'erreurs ». Chaque step de code porte le code COMPLET (imports inclus) ; chaque run a sa commande exacte et sa sortie attendue ; chaque tâche se clôt par un commit exact. Les deux notes d'ordonnancement (Tasks 7→8 et 10↔11) sont des instructions PRÉCISES de séquencement de commits, pas des trous.
- **Séquencement & gate 100 % :** deux points de friction identifiés et résolus EXPLICITEMENT : (a) Task 7 (décodeur avec branches défensives) n'atteint 100 % qu'avec les tests hostiles de la Task 8 → commit UNIQUE en fin de Task 8 ; (b) `client.py` (Task 10) importe `mapping.py` (Task 11) et ses méthodes recherche/statut ne sont couvertes qu'aux Tasks 12-13 → ordre d'exécution réel documenté (Task 11 ENTIÈRE d'abord, commit vert ; puis 10 → 12 → 13 sans commit intermédiaire, et UN SEUL commit `client.py` + `test_client.py` une fois le gate de la Task 13 vert) — chaque commit du plan se fait ainsi sur un arbre au gate vert.
- **Tout fait wire-level est sourcé :** chaque trame de test porte sa dérivation (flags, TAGNAME décalé, TAGLEN calculé poste par poste) pointant vers `ec-protocol.md` §1-§6 ; les vecteurs d'auth sont recalculables en 4 lignes données en commentaire ; aucune valeur « de mémoire ».
- **Décisions prises là où spec/référence laissaient ouvert (à relire en revue) :** DÉCISION 1 (`errors.py` ajouté + nom `EcFailureError`), 2 (flags acceptés restreints à {0x20, 0x21} — plus strict qu'aMule), 3 (borne de décompression 16 Mio, profondeur 32), 5 (pas de timeout d'écriture), 6 (`skipped_entries_total` attribut public hors port ; progress > 100 → `None` ; mdp vide refusé client-side), 7 (format `raw_meta` `"0xNNNN"` + rendu sans exception), 8 (Mio pour `size_mb`), 9 (asyncio strict mode), 10 (image `ngosang/amule:3.0.0-1` ; tolérance `EcFailureError` dans le test de cycle réel), 11 (probe : `rounds = ceil(timeout/interval)`, sleep injectable).
- **Argument gate coverage :** chaque module nouveau a ses deux côtés de branche listés dans la « Note couverture » d'en-tête et exercés par les tests nommés ; les seuls pragmas : `# pragma: no cover` sur le bloc `__main__` du probe (convention handoff §8). Les tests d'intégration ne comptent pas dans le gate (déselectionnés + `--no-cov`).

### OPEN QUESTIONS FOR THE HUMAN

Aucune bloquante. Deux choix à confirmer en revue si désaccord : (1) `size_mb` en **Mio** (DÉCISION 8) — si la config canonique pensait en Mo décimaux, changer UNE constante + 2 tests ; (2) la restriction des flags acceptés à {0x20, 0x21} (DÉCISION 2) — plus stricte que le test d'aMule, justifiée car nous ne négocions aucune capacité.

