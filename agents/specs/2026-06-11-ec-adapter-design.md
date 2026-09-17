# Spec — emule-indexer : Adapter EC + observation (Plan B)

> Sous-projet du MVP crawler (voir `2026-06-10-crawler-mvp-design.md`, §4–§6, §14, §16).
> Validé avec Geoffrey le 2026-06-11. Jalon visé : `v0.5.0-ec-adapter`.

## 1. Contexte & objectif

Le moteur de matching (`v0.4.0-engine`) est complet mais aveugle : rien ne lui fournit de
fichiers observés. Ce sous-projet construit le **client EC** — l'interface haut niveau,
écrite par nous, qui pilote `amuled` via son protocole binaire *External Connections*
(aucune lib Python existante fiable ; c'est le risque technique n°1 du projet, à dé-risquer
maintenant).

Objectif fonctionnel : se connecter à un `amuled`, lancer des recherches par mot-clé
(serveurs eD2k + Kad), relever les résultats sous forme d'objets domaine, et lire l'état
du réseau. Objectif stratégique : **mesurer empiriquement la richesse des métadonnées
que EC expose réellement** (conditionne le schéma `catalog.db` du plan A et la valeur du
catalogue).

## 2. Périmètre

**Dans le scope :**
- Connexion + authentification EC (challenge/réponse par mot de passe).
- Recherche mot-clé : démarrage (canal `global` serveurs ou `kad`), relevé des résultats,
  arrêt, progression si EC l'expose.
- Statut réseau : ID eD2k (High/Low), état Kad, serveur connecté.
- Mapping des résultats EC → `FileObservation` (domaine), capture-all (`raw_meta`).
- Outil probe CLI + rapport écrit sur la richesse des champs observés.
- Tests : unitaires (codec pur, faux serveur EC) + **intégration testcontainers
  obligatoire** contre un `amuled` réel.

**Hors scope (plans ultérieurs) :**
- Persistance : aucune DB ; les résultats restent des objets en mémoire (plan A).
- Cadencement/backoff/reconnexion : aucune boucle ni politique de retry ici (plan C).
- Téléchargements : enfilage `ed2k://`, suivi de file, réconciliation (plan D).
- Métriques/notifications (plan E) — mais les objets produits (statut réseau, compteur
  d'entrées écartées) sont conçus pour s'y brancher.

## 3. Décisions verrouillées

- **Codec générique, usages minimaux.** La couche binaire (paquets/tags) encode-décode
  *n'importe quel* paquet EC — c'est un format conteneur récursif. Seuls trois usages
  sont câblés en haut niveau : auth, recherche, statut. Le plan D ajoutera des méthodes
  au client sans rouvrir codec/transport.
- **asyncio.** Transport et client sont async (`asyncio.StreamReader/Writer`) ; le codec
  reste **pur et synchrone** (bytes ↔ arbre de tags, zéro I/O). Dépendance dev :
  `pytest-asyncio`.
- **Le polling appartient à l'appelant.** L'adapter n'a aucun `sleep`, aucune boucle
  d'attente : il expose des actions unitaires ; le rythme est décidé par le scheduler
  (plan C) ou par l'outil probe.
- **Capture-all dès la frontière.** Tout tag non mappé part dans `raw_meta` ; on ne perd
  jamais une métadonnée (promesse §11 du spec MVP, tenue avant même d'avoir une DB).
- **L'intégration fait foi.** Les tests testcontainers contre un `amuled` réel sont
  **non négociables** : ils doivent être verts avant de taguer le jalon. Ils sont hors
  du calcul de couverture (marker dédié) ; le gate 100 % branch reste calculé sur les
  tests rapides.
- **Référence protocole = les sources aMule 2.3.x** (`ECCodes.h`, `ECTagTypes.h`,
  implémentation `libec`). Les constantes sont transcrites dans `codes.py` avec leur
  référence ; l'algorithme exact d'authentification est transcrit des sources puis
  **vérifié empiriquement** contre l'amuled testcontainers. Image de référence :
  `ngosang/docker-amule`.

## 4. Architecture & composants

```
src/emule_indexer/
├── domain/
│   └── observation.py            # NOUVEAU (pur) : FileObservation
├── ports/
│   └── mule_client.py            # NOUVEAU : Protocol async MuleClient + NetworkStatus
└── adapters/
    └── mule_ec/
        ├── codes.py              # constantes protocole (opcodes, noms/types de tags),
        │                         #   transcrites des headers aMule 2.3.x, référencées
        ├── codec.py              # PUR, sync : ECTag (arbre), encode/décode d'un paquet
        │                         #   complet (flags, compression zlib, entiers var-length)
        ├── transport.py          # async : framing sur StreamReader/Writer, timeouts,
        │                         #   send_packet() / receive_packet()
        ├── client.py             # async : AmuleEcClient (implémente MuleClient) —
        │                         #   handshake auth, search_*, network_status
        └── mapping.py            # tags EC → FileObservation ; capture-all raw_meta

src/emule_indexer/tools/
    └── ec_probe.py               # CLI : recherche réelle + dump de tous les tags reçus
```

Règles de dépendance (inchangées) : `domain/` pur ; `ports/` ne contient que le Protocol
et ses DTO figés ; tout le binaire/réseau vit dans `adapters/mule_ec/`. Personne d'autre
que l'adapter ne voit un opcode.

### Modèle domaine

`FileObservation` (frozen, pur) — un fichier vu sur le réseau lors d'une recherche.
Champs alignés sur la table `file_observations` (§11 du spec MVP) :
`ed2k_hash`, `filename`, `size_bytes`, `source_count`, `complete_source_count`,
`media_length_sec?`, `bitrate_kbps?`, `codec?`, `file_type?`,
`raw_meta` (tuple figé de paires `(nom, valeur)` — JSON-friendly), `keyword` (provenance).

- `.to_candidate() -> FileCandidate` : le pont vers le moteur de matching (conversions
  d'unités : octets → Mo, etc.).
- Le plan A persistera cet objet tel quel ; l'adapter DB ajoutera `observed_at`/`node_id`
  (même principe que `MatchDecision`).

`NetworkStatus` (frozen, défini à côté du port) : `ed2k_id`, `ed2k_high : bool`,
`kad_status` (enum fermé), `server_name?`/`server_addr?` — exactement ce que les
métriques §13 consommeront.

### Port `MuleClient` (Protocol async)

```python
class MuleClient(Protocol):
    async def connect(self) -> None: ...          # TCP + auth ; lève EcAuthError/EcConnectError
    async def close(self) -> None: ...
    async def start_search(self, keyword: str, channel: SearchChannel) -> None: ...
    async def fetch_results(self) -> tuple[FileObservation, ...]: ...   # snapshot cumulatif
    async def stop_search(self) -> None: ...
    async def search_progress(self) -> int | None: ...   # % si EC l'expose, sinon None
    async def network_status(self) -> NetworkStatus: ...
```

`SearchChannel` = enum fermé `global` | `kad`. Une convenance `search_and_wait(...)`
(poll + timeout) vit dans l'outil probe, **pas** dans le port.

## 5. Flux

EC = API client-serveur classique, en binaire sur TCP (au lieu de JSON sur HTTP).
`amuled` est le serveur ; notre client :

1. **Connexion + login** : TCP vers `host:ec_port` ; requête d'auth (nom client, version,
   version de protocole) ; le daemon envoie un défi (salt) ; on répond avec le hash du
   mot de passe combiné au défi ; OK → session établie. Échec → `EcAuthError`, sans retry.
2. **Recherche** : « cherche `keroro` sur `global`/`kad` ». Le daemon cherche en
   arrière-plan, les résultats **s'accumulent** chez lui.
3. **Relevé** : `fetch_results()` retourne le snapshot accumulé. Chaque entrée (un
   sous-arbre de tags : hash, nom, taille, sources, tags média…) passe par `mapping.py`
   → `FileObservation`, `keyword` de provenance attaché.
4. **Statut** : requête indépendante → `NetworkStatus`.

Au niveau du fil (enfermé dans codec/transport) : un paquet = en-tête de flags
(compression zlib, encodage des entiers à longueur variable) + longueur + opcode + arbre
de tags. Le transport lit exactement un paquet ; le codec le décode. Détails wire-level
spécifiés au plan d'implémentation, sources aMule en main.

## 6. Gestion d'erreurs

Principe (§14 du spec MVP) : **l'adapter signale, il ne décide pas.** Pas de retry caché,
pas de crash silencieux ; la politique de récupération appartient à l'appelant (plan C).

- Hiérarchie dédiée : `EcError` (base) → `EcConnectError` (TCP refusé/perdu),
  `EcAuthError` (mot de passe/version refusés), `EcProtocolError` (trame malformée ou
  réponse inattendue), `EcTimeoutError`. Permet de distinguer « amuled est down » de
  « ma config est fausse ».
- **Timeout sur chaque lecture réseau**, configurable à la construction du client.
- **Parsing défensif** : longueur de paquet bornée (rejet net d'une longueur aberrante),
  décompression zlib bornée. Les réponses d'amuled sont traitées comme une entrée non
  fiable.
- **Tolérance aux inconnus** : un tag inconnu n'est jamais une erreur → `raw_meta`.
  Seule une entrée inexploitable (sans hash, nom ou taille) est écartée ; le mapper
  **compte** ces écarts (futur brancheur de métrique, plan E) et un résultat pourri ne
  fait jamais échouer le lot.
- Erreur applicative EC (le daemon répond « échec ») : exception portant le message du
  daemon, distincte d'une trame illisible.

## 7. Stratégie de tests

TDD strict (tests d'abord, 100 % branch sur les tests rapides). Trois étages :

1. **Codec (pur, sync)** : octets connus ↔ arbres attendus, round-trip encode/décode,
   entrées hostiles (paquet tronqué, longueur menteuse, zlib corrompu, profondeur de
   tags bornée). Le gros de la valeur est ici.
2. **Transport + client (async)** : faux serveur EC en mémoire (streams asyncio) qui
   rejoue des trames — dont des **fixtures capturées** sur un amuled réel (le probe sert
   aussi d'outil de capture). `pytest-asyncio` en dev.
3. **Intégration (obligatoire, hors coverage)** : testcontainers lance
   `ngosang/docker-amule` avec un `amule.conf` préparé (EC activé + mot de passe),
   attente de readiness, puis : auth réelle, statut, cycle complet
   `start_search`/`fetch_results`/`stop_search` (résultats possiblement vides sans accès
   réseau eD2k — c'est le *cycle* qui est validé ; la richesse des champs réels vient du
   probe). Marker pytest dédié, **déselectionné par défaut** ; run dédié
   `uv run pytest -m <marker> --no-cov`. **Verts avant le tag, non négociable.**

Dépendances dev nouvelles : `pytest-asyncio`, `testcontainers` (overrides mypy si stubs
absents, comme pour `re2`).

## 8. Livrables & definition of done

1. Codec + transport + client + mapping, testés, gate 4-checks vert.
2. Port `MuleClient` + `FileObservation`/`NetworkStatus`.
3. Tests d'intégration testcontainers **verts** (Docker requis sur la machine de dev).
4. Outil probe (`uv run python -m emule_indexer.tools.ec_probe`) : se connecte, lance une
   vraie recherche, dumpe tous les tags reçus (y compris inconnus) — réutilisable contre
   le homelab plus tard.
5. **Rapport de richesse des champs** dans `docs/` : champs observés vs espérés (§11),
   constats et conséquences pour le schéma du plan A.
6. Tag annoté `v0.5.0-ec-adapter`.

## 9. Questions laissées au plan d'implémentation

- Transcription exacte des opcodes/tags/types nécessaires (depuis `ECCodes.h`) et de
  l'algorithme d'auth (vérifié contre l'amuled testcontainers).
- Format précis des entiers à longueur variable et du flag de compression (sources aMule).
- Découpage en tâches TDD et fixtures de trames initiales (capturées au probe ou
  forgées à la main depuis la spec du format).
