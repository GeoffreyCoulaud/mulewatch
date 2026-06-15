# Design — port-sync (High-ID) : EC `SetPort` + restart amuled piloté par gluetun

> **Nature** : design **DÉJÀ DÉCIDÉ** (co-design figé, cf. plan maître
> `docs/superpowers/specs/2026-06-15-backlog-parallelization-design.md` §3.3, §3.4, §5,
> worktree **WT-portsync**). Ce doc documente fidèlement la décision, ancré dans le code réel
> du paquet `emule_indexer` ET la source amont `vendor/amule/` (`fichier:ligne`). Il **ne
> re-designe pas** ; toute contradiction trouvée en cours de route est consignée en
> « Risques / à confirmer ». Doc exécutable par un implémenteur frais (TDD, 100 % branch).

## 0. Résumé exécutif

Le port d'écoute eD2k d'amuled **n'est PAS re-bindable à chaud** : le socket est bâti une seule
fois au boot (`ReinitializeNetwork()`, `vendor/amule/src/amule.cpp:664,873,963-964`). gluetun,
derrière qui amuled tourne (`network_mode: service:gluetun`), **renégocie périodiquement** un
port forwardé (ProtonVPN NAT-PMP) ; pour obtenir un **High-ID** (joignable, donc source
contactable), amuled doit écouter **sur ce port forwardé**.

Mécanisme retenu — une **boucle port-sync UNIFIÉE** côté crawler (boot + mid-life, un seul
chemin) :

1. **Lire** le port forwardé vivant : `GET http://gluetun:8000/v1/portforward` → `{"port":N}`.
2. **Comparer** N au port courant d'amuled, lu par EC `GetPreferences` →
   `EC_TAG_CONN_TCP_PORT` (0x1306).
3. **Si différent ET N > 0** : EC `SetPreferences(EC_TAG_CONN_TCP_PORT=N,
   EC_TAG_CONN_UDP_PORT=N)` — met à jour la pref **en mémoire** ET amuled persiste ses prefs au
   shutdown (`glob_prefs->Save()`, déclenché par `EC_OP_SET_PREFERENCES`,
   `vendor/amule/src/ExternalConn.cpp:2083`) — puis **restart amuled** via un
   **docker-socket-proxy à surface minimale**. Au reboot, amuled bind N. **Aucune édition de
   fichier `amule.conf`.**
4. **Garde-fous** : rate-limiter les restarts (≤ 1 / fenêtre) ; après restart, **re-vérifier le
   High-ID** via EC `GetConnState` → `EC_TAG_ED2K_ID` (0x0006) ≥ `0x01000000`. Pas de boucle si
   un restart ne donne pas High-ID.
5. **Alerte de repli** (audience OPERATIONS, edge-triggered) quand le port forwardé vivant ≠
   port configuré d'amuled ET l'auto-restart n'a pas corrigé.

Le mode dégradé (Low-ID) est **déjà toléré** : tout parsing défensif (port 0, JSON malformé,
control-server injoignable) → « pas prêt », backoff, on reste Low-ID sans crasher.

## 1. Contexte & rationale (non-hot-rebind ; EC SetPort + restart)

### 1.1 Le port n'est pas re-bindable à chaud (acquis du spike, §3.3 plan maître)

Le socket d'écoute eD2k est créé une seule fois dans `ReinitializeNetwork()`, appelé à `OnInit`
(`vendor/amule/src/amule.cpp:664,873,963-964`). **Il n'existe AUCUN opcode EC ni aucune commande
amulecmd qui re-bind le port d'un amuled en marche.** Changer la pref à chaud ne ré-ouvre pas le
socket.

### 1.2 Mais EC SetPort met à jour la pref ET amuled la persiste au shutdown

- Côté serveur, `EC_OP_SET_PREFERENCES` → `CEC_Prefs_Packet::Apply()` lit le tag
  `EC_TAG_PREFS_CONNECTIONS`, puis son enfant `EC_TAG_CONN_TCP_PORT` → `thePrefs::SetPort(...)`
  et `EC_TAG_CONN_UDP_PORT` → `thePrefs::SetUDPPort(...)`
  (`vendor/amule/src/ECSpecialMuleTags.cpp:409-413`). Ce sont des setters **en mémoire**.
- Le handler `EC_OP_SET_PREFERENCES` appelle **immédiatement après** `Apply()` le
  `theApp->glob_prefs->Save()` (`vendor/amule/src/ExternalConn.cpp:2081-2083`). La pref est donc
  **persistée à la première occasion** ; et de toute façon amuled sauve ses prefs au shutdown
  (`glob_prefs->Save()`, `amule.cpp:566`, acquis §3.3).
- Les clés persistées sont `[eMule] Port=` / `UDPPort=` dans `amule.conf`
  (`vendor/amule/src/Preferences.cpp:1064-1065` :
  `NewCfgItem(IDC_PORT, MkCfg_Int("/eMule/Port", s_port, DEFAULT_TCP_PORT))` et
  `IDC_UDPPORT → "/eMule/UDPPort"`).

**Conclusion (mécanisme port-sync)** : `SetPort(N)` via EC **puis restart amuled**. Au shutdown,
amuled persiste N dans `amule.conf` ; au reboot, `ReinitializeNetwork` bind N. **On ne touche
jamais le fichier `amule.conf` à la main** — EC + le cycle de vie d'amuled s'en chargent.

> **Note de robustesse** : comme `Save()` est appelé dès le `SetPreferences` (ExternalConn.cpp
> 2083), la persistance ne dépend même PAS d'un shutdown propre — un `restart` brutal du
> conteneur reprendra quand même le nouveau port. Le restart sert à re-bind, pas à persister.

### 1.3 Pourquoi une boucle UNIFIÉE (boot + mid-life)

Le port peut changer à **tout** moment (renégo VPN, reconnexion gluetun — acquis §3.4). Il n'y a
pas de différence de traitement entre « le port est faux au démarrage » et « le port est devenu
faux en cours de route » : dans les deux cas, on lit le port vivant, on compare, on corrige si
besoin. Un **seul algorithme polling** (§4) couvre les deux — pas de chemin « boot » distinct.

### 1.4 Topologie réseau (rappel `compose.yaml`)

- `gluetun` est sur le réseau `ec` (`compose.yaml:22-24`).
- `amuled` partage la netns de gluetun (`network_mode: "service:gluetun"`,
  `compose.yaml:31`) → en prod, l'**hôte EC du crawler est `gluetun`** (le crawler joint amuled
  via gluetun ; cf. `compose.smoke.yaml` où, sans gluetun, l'hôte EC devient `amuled`).
- `crawler` est sur `ec` (+ `verify-internal` + `egress`) (`compose.yaml:65-68`).
- Le **control-server gluetun** (`:8000`) est donc joignable par le crawler sur le réseau `ec`
  à `http://gluetun:8000`.

## 2. Opcodes / tags EC à ajouter (valeurs hex citées de `vendor/amule/.../ECCodes.h`)

Toutes les valeurs ci-dessous sont **vérifiées dans
`vendor/amule/src/libs/ec/cpp/ECCodes.h`** (lignes citées). Les constantes déjà présentes dans
`adapters/mule_ec/codes.py` sont notées « DÉJÀ ».

### 2.1 Opcodes (ECCodes.h)

| Constante | Hex | ECCodes.h | Présence `codes.py` |
|---|---|---|---|
| `EC_OP_GET_PREFERENCES` | `0x3F` | ligne 102 | **À AJOUTER** |
| `EC_OP_SET_PREFERENCES` | `0x40` | ligne 103 | **À AJOUTER** |
| `EC_OP_NOOP` | `0x01` | ligne 47 | DÉJÀ (`codes.py:19`) — réponse de SET |
| `EC_OP_GET_CONNSTATE` | `0x0B` | ligne 57 | DÉJÀ (`codes.py:30`) |
| `EC_OP_MISC_DATA` | `0x07` | (ECCodes.h) | DÉJÀ (`codes.py:28`) — réponse de GET_CONNSTATE |

### 2.2 Tags & sélecteur de préférences (ECCodes.h)

| Constante | Hex | ECCodes.h | Présence `codes.py` |
|---|---|---|---|
| `EC_TAG_SELECT_PREFS` | `0x1000` | ligne 310 | **À AJOUTER** |
| `EC_TAG_PREFS_CONNECTIONS` | `0x1300` | ligne 323 | **À AJOUTER** (parent) |
| `EC_TAG_CONN_TCP_PORT` | `0x1306` | ligne 329 | **À AJOUTER** (enfant) |
| `EC_TAG_CONN_UDP_PORT` | `0x1307` | ligne 330 | **À AJOUTER** (enfant) |
| `EC_PREFS_CONNECTIONS` | `0x00000004` | ligne 462 | **À AJOUTER** (bitmask de `EC_TAG_SELECT_PREFS`) |
| `EC_DETAIL_FULL` | `0x02` | ligne 436 | DÉJÀ (`codes.py:47`) |
| `EC_TAG_CONNSTATE` | `0x0005` | ligne 134 | DÉJÀ (`codes.py:61`) — parent |
| `EC_TAG_ED2K_ID` | `0x0006` | ligne 135 | DÉJÀ (`codes.py:62`) — enfant de CONNSTATE |

> **Style** : ces constantes vont dans `adapters/mule_ec/codes.py`, mêmes conventions
> (`Final[int]`, commentaire de provenance amont). Le mapping mypy `re2`/stubs concerne le
> moteur de matching, **pas** `mule_ec` (aucun import `re2` ici) — rien à faire de ce côté.

### 2.3 Structure des paquets (vérifiée côté serveur amont)

**Requête `GetPreferences`** (`EC_OP_GET_PREFERENCES`, 0x3F) — modèle = `amulecmd` lui-même,
`vendor/amule/src/TextClient.cpp:566-567` :
```
EC_OP_GET_PREFERENCES
  └─ EC_TAG_SELECT_PREFS (uint, 0x1000) = EC_PREFS_CONNECTIONS (0x00000004)
```
Le détail level est `EC_DETAIL_FULL` (0x02) côté GUI ; CMD suffit aussi (le serveur lit
`request->GetDetailLevel()`, ExternalConn.cpp:2079). On émet `EC_DETAIL_FULL` pour rester aligné
avec le client GUI de référence — à confirmer empiriquement (cf. Risques R3).

**Réponse `GetPreferences`** — construite par `CEC_Prefs_Packet`
(`vendor/amule/src/ECSpecialMuleTags.cpp:105-132`) : un tag de premier niveau
`EC_TAG_PREFS_CONNECTIONS` (0x1300, un `CECEmptyTag` → **parent**), dont l'enfant
`EC_TAG_CONN_TCP_PORT` (0x1306) porte `thePrefs::GetPort()`
(`ECSpecialMuleTags.cpp:112`) et `EC_TAG_CONN_UDP_PORT` (0x1307) porte `GetUDPPort()` (ligne
113). L'opcode de la réponse est `EC_OP_SET_PREFERENCES` (0x40) — `CEC_Prefs_Packet` hérite de
`CECPacket(EC_OP_SET_PREFERENCES, ...)`, `ECSpecialMuleTags.cpp:83`.
```
EC_OP_SET_PREFERENCES        (opcode de la réponse — 0x40, NON 0x3F)
  └─ EC_TAG_PREFS_CONNECTIONS (0x1300, parent)
       ├─ … (UL_CAP, DL_CAP, …)
       ├─ EC_TAG_CONN_TCP_PORT (0x1306) = port TCP courant
       └─ EC_TAG_CONN_UDP_PORT (0x1307) = port UDP courant
```
> **PIÈGE** : la réponse à GET_PREFERENCES porte l'opcode **0x40** (`EC_OP_SET_PREFERENCES`),
> PAS 0x3F. C'est `CEC_Prefs_Packet` réutilisé en réponse. Le `_request(packet,
> expected_opcode)` du client (`client.py:234`) attend donc `EC_OP_SET_PREFERENCES` comme
> opcode de réponse. **À vérifier en intégration** (Risques R3) — si faux, ajuster l'`expected`.

**Requête `SetPreferences`** (`EC_OP_SET_PREFERENCES`, 0x40) — on construit la même structure ;
`Apply()` ne lit QUE les tags présents (`if ((oneTag = thisTab->GetTagByName(...)) != NULL)`,
ECSpecialMuleTags.cpp:409,412), donc on n'émet que TCP+UDP_PORT :
```
EC_OP_SET_PREFERENCES
  └─ EC_TAG_PREFS_CONNECTIONS (0x1300, parent, type CUSTOM/empty)
       ├─ EC_TAG_CONN_TCP_PORT (0x1306, uint) = N
       └─ EC_TAG_CONN_UDP_PORT (0x1307, uint) = N
```
**Réponse** : `EC_OP_NOOP` (0x01) — le handler répond `new CECPacket(EC_OP_NOOP)`
(`ExternalConn.cpp:2096`). C'est le MÊME contrat que `add_link` (client.py:161-172) qui attend
déjà `EC_OP_NOOP` pour le succès. `_request(..., EC_OP_NOOP)`.

> **Construire le parent `EC_TAG_PREFS_CONNECTIONS`** : le codec (`codec.py`) sait déjà encoder
> des tags imbriqués (`uint_tag(name, value, children=(...))`, et `empty_tag(name, children)`
> pour un parent porteur). Le parent `EC_TAG_PREFS_CONNECTIONS` est un `CECEmptyTag` côté amont
> (valeur propre vide, type CUSTOM) → utiliser `empty_tag(codes.EC_TAG_PREFS_CONNECTIONS,
> children=(uint_tag(EC_TAG_CONN_TCP_PORT, N), uint_tag(EC_TAG_CONN_UDP_PORT, N)))`. Le décalage
> wire `(nom << 1) | enfants` est géré par `_encode_tag` (codec.py:124-132).

**Requête `GetConnState`** (`EC_OP_GET_CONNSTATE`, 0x0B) — **DÉJÀ implémentée** :
`AmuleEcClient.network_status()` (`client.py:149-159`) l'émet, lit `EC_TAG_CONNSTATE` (0x0005),
décode son enfant `EC_TAG_ED2K_ID` (0x0006) et calcule `ed2k_high = ed2k_id >= 16777216`
(`_LOWID_THRESHOLD`, `client.py:34`, = `0x01000000`). Côté amont, `CEC_ConnState_Tag` ajoute
`EC_TAG_ED2K_ID` à `EC_TAG_CONNSTATE` (`vendor/amule/src/ECSpecialCoreTags.cpp:146`, `0xffffffff`
si non connecté ED2K). **Rien à ajouter pour le re-check High-ID** — on appelle
`network_status()` et on lit `.ed2k_high`.

## 3. Lecteur du port gluetun (nouvel adapter + nouveau port)

### 3.1 Port (domaine d'I/O) — `ports/port_forwarding.py`

```python
class PortForwardingReader(Protocol):
    async def forwarded_port(self) -> int | None: ...
    # int > 0 = port vivant ; None = "pas prêt" (port 0 / malformé / control-server injoignable)
```
Stubs sur UNE ligne (le `def` compte comme couvert, gotcha CLAUDE.md). Aucun import adapter.

### 3.2 Adapter httpx — `adapters/gluetun_port.py`

`GET {base_url}/v1/portforward`. **Parsing défensif** (DÉCISION 7) — TOUT échec → `None`
(« pas prêt »), JAMAIS d'exception qui remonte :

| Cas | Traitement |
|---|---|
| 200, `{"port": N}` avec `N` entier > 0 | retourne `N` |
| 200, `{"port": 0}` | retourne `None` (PF pas encore négocié, §3.4 plan) |
| 200, JSON sans `port` / `port` non-entier / négatif | retourne `None` |
| 200, corps non-JSON | retourne `None` (capter `ValueError`/`json` de httpx) |
| status ≠ 200 (4xx/5xx) | retourne `None` |
| `httpx.TimeoutException` / `httpx.ConnectError` / `httpx.HTTPError` | retourne `None` |

> Miroir EXACT du parsing défensif de `HttpContentVerifier` (`adapters/verifier_http.py`) :
> connect/timeout/malformé → état dégradé, jamais fatal. Réutiliser le même style
> (un `httpx.AsyncClient` injecté, `aclose` au teardown via l'`AsyncExitStack`).

> **Auth** : sur le réseau interne `ec`, l'auth du control-server gluetun est désactivée
> (DÉCISION 6, §6 du doc) → pas d'en-tête à poser. Si un jour on durcit, c'est un en-tête
> `X-API-Key` / `Authorization` à ajouter ici (adapter), boucle inchangée.

## 4. La boucle port-sync unifiée (algorithme boot + mid-life)

### 4.1 Nouveau port `MuleRestarter` — `ports/mule_restarter.py`

```python
class MuleRestarter(Protocol):
    async def restart(self) -> None: ...
    # déclenche le restart du conteneur amuled ; lève RestarterError si le proxy refuse/échoue
```
Erreur dédiée `RestarterError` (dans `ports/`), absorbée par la boucle (jamais fatale).

### 4.2 Sous-ensembles de `MuleClient` consommés (typage local, pattern `run_verification_cycle`)

La boucle ne dépend que de DEUX méthodes EC. Comme `VerificationTaskQueue`
(`run_verification_cycle.py:45`), on déclare des **Protocols NARROW** locaux que le vrai
`AmuleEcClient` ET un fake minimal satisfont :

```python
class PortPreferences(Protocol):
    async def get_listen_port(self) -> int: ...          # NOUVELLE méthode AmuleEcClient
    async def set_listen_port(self, port: int) -> None: ...  # NOUVELLE méthode AmuleEcClient
    async def network_status(self) -> NetworkStatus: ...  # EXISTE DÉJÀ (client.py:149)
```

> **Nouvelles méthodes sur `AmuleEcClient`** (et sur le port `MuleClient` si l'on veut le
> contrat partagé — à trancher : la boucle n'a besoin que du Protocol narrow, donc on PEUT ne
> PAS toucher `ports/mule_client.py` et garder `get/set_listen_port` propres à l'adapter +
> au narrow local. **Recommandé** : ne pas élargir `MuleClient` ; définir les narrows dans le
> module de la boucle, comme `run_verification_cycle`.) :
> - `get_listen_port() -> int` : `GetPreferences(EC_PREFS_CONNECTIONS)` → lire l'enfant
>   `EC_TAG_CONN_TCP_PORT` sous `EC_TAG_PREFS_CONNECTIONS`. Tag/parent absent → `EcProtocolError`
>   (réponse non conforme), capté par la boucle comme « EC indisponible » → backoff.
> - `set_listen_port(port) -> None` : `SetPreferences` avec TCP+UDP = `port`, attend
>   `EC_OP_NOOP`.

### 4.3 Dépendances de la boucle — `application/port_sync_loop.py`

```python
@dataclass
class PortSyncDeps:
    reader: PortForwardingReader     # lit le port vivant gluetun
    ports: PortPreferences           # EC get/set/connstate (AmuleEcClient)
    restarter: MuleRestarter         # restart amuled via proxy
    clock: Clock                     # sleep/now injectés (déterminisme)
    telemetry: Telemetry             # events d'observabilité
    edge: EdgeState                  # alerte edge-triggered (port mismatch non corrigé)
    poll_interval_seconds: float     # cadence du poll
    restart_min_interval_seconds: float  # rate-limit des restarts (≤ 1 / fenêtre)

@dataclass
class PortSyncLoopDeps(PortSyncDeps):
    shutdown: asyncio.Event
```

État inter-itérations (comme `BackoffRegistry`/`EdgeState`) : la boucle mémorise **l'instant du
dernier restart** (pour le rate-limit) et **le port visé du dernier restart** (pour savoir si le
restart précédent a « pris » → re-check High-ID). Détenu par la boucle (mutable, mono-thread sur
l'event loop, non persisté — comme `EdgeState`).

### 4.4 Algorithme d'un cycle (`run_port_sync_cycle`)

NE LÈVE JAMAIS (filet top-level comme `run_verification_cycle`) ; tout chemin re-bouclant dort
`poll_interval_seconds` (pas de busy-spin). Pseudo-code :

```
async def run_port_sync_cycle(deps, state):
    try:
        live = await deps.reader.forwarded_port()          # int>0 ou None
        if live is None:
            # control-server pas prêt / PF non négocié → on reste Low-ID, pas d'alerte
            await deps.clock.sleep(deps.poll_interval_seconds); return

        current = await deps.ports.get_listen_port()        # port EC d'amuled (peut lever EcError)

        if live == current:
            # déjà aligné → si on était en alerte mismatch, on réarme
            deps.edge.leave("port_mismatch")
            await deps.clock.sleep(deps.poll_interval_seconds); return

        # --- divergence : live != current, et live > 0 garanti ---
        now = deps.clock.now()
        if state.too_soon(now, deps.restart_min_interval_seconds):
            # rate-limit : on a restart récemment, on attend (ne pas boucler les restarts)
            await deps.clock.sleep(deps.poll_interval_seconds); return

        await deps.ports.set_listen_port(live)              # EC SetPort(N) (persisté par amuled)
        await deps.telemetry.emit(PortSyncTriggered(old=current, new=live))
        try:
            await deps.restarter.restart()                  # restart amuled (proxy)
        except RestarterError:
            # restart impossible → alerte edge-triggered + backoff
            await deps.telemetry.emit(
                PortMismatchUnresolved(first_occurrence=deps.edge.enter("port_mismatch"),
                                       live=live, configured=current))
            await deps.clock.sleep(deps.poll_interval_seconds); return
        state.record_restart(now, live)                     # rate-limit + cible visée

        # --- re-check High-ID après restart ---
        # amuled redémarre ; on laisse un délai borné puis on lit le connstate. NE PAS BOUCLER
        # si pas High-ID (DÉCISION 4) : on émet l'alerte et on rend, le prochain cycle re-vérifiera
        # SANS re-restart tant que le rate-limit court.
        await deps.clock.sleep(deps.poll_interval_seconds)  # laisse amuled rebind
        status = await deps.ports.network_status()
        if status.ed2k_high:
            deps.edge.leave("port_mismatch")
            await deps.telemetry.emit(HighIdRecovered(port=live))
        else:
            await deps.telemetry.emit(
                PortMismatchUnresolved(first_occurrence=deps.edge.enter("port_mismatch"),
                                       live=live, configured=live))
        return
    except EcError:           # get_listen_port / set_listen_port / network_status injoignable
        # amuled down / EC mort → toléré (comme la composition tolère MuleUnreachableError au boot)
        await deps.clock.sleep(deps.poll_interval_seconds)
    except RepositoryError:   # (si jamais un état persisté est introduit ; sinon retirer ce bras)
        await deps.clock.sleep(deps.poll_interval_seconds)
```

> **Boot vs mid-life = même chemin** : au tout premier cycle, `current` est le port codé en dur
> de l'image (`Port=4662`, acquis §3.3) ; si `live` diffère, on `SetPort` + restart une fois,
> puis on re-vérifie High-ID. Aux cycles suivants, idem en cas de renégo VPN. **Un seul
> algorithme.** Pas de branche « première fois ».

> **Rate-limit (DÉCISION 4)** : `state.too_soon(now, window)` = `now - last_restart < window`.
> Garantit ≤ 1 restart par `restart_min_interval_seconds`. Empêche une boucle de restarts si
> gluetun oscille ou si le port forwardé refuse obstinément de donner un High-ID.

> **Re-check High-ID sans boucler (DÉCISION 4)** : si `network_status()` rend `ed2k_high=False`
> après restart, on **n'essaie pas un nouveau restart immédiatement** — on émet l'alerte
> `PortMismatchUnresolved` (edge-triggered) et on rend ; le rate-limit empêche le prochain cycle
> de re-restarter avant la fenêtre. L'opérateur est notifié (audience OPERATIONS).

### 4.5 Boucle (`port_sync_loop`) — pattern `verification_loop`

```
async def port_sync_loop(deps: PortSyncLoopDeps) -> None:
    state = _PortSyncState()
    while not deps.shutdown.is_set():
        await run_port_sync_cycle(deps, state)
        if deps.shutdown.is_set():
            break
```
Annulation atterrit au prochain `await` (poll/EC/sleep). NE PEUT PAS crasher le `TaskGroup`
(`run_port_sync_cycle` ne lève jamais), exactement comme `verification_loop`
(`run_verification_cycle.py:201-213`).

## 5. Le proxy de restart à surface minimale (décision B)

### 5.1 Pourquoi PAS `tecnativa/docker-socket-proxy`

`tecnativa/docker-socket-proxy` filtre par **opération** (`POST`, `CONTAINERS`, …), pas par
**conteneur** : autoriser `POST /containers/*/restart` autoriserait restart/stop/kill de
**n'importe quel** conteneur joignable via le socket. Surface trop large pour un service dont le
seul besoin est « redémarrer amuled ». (DÉCISION 3.)

### 5.2 Option retenue : `wollomatic/socket-proxy` OU mini-proxy maison (l'implémenteur tranche)

**Surface ciblée** : autoriser EXACTEMENT `POST` sur
`/<version>/containers/amuled/restart` (et rien d'autre).

- **`wollomatic/socket-proxy`** : allowlist par **regex** sur le chemin + méthode. Une règle du
  type `^/v1\..+/containers/amuled/restart$` + méthode `POST` suffit. Avantage : zéro code à
  maintenir, image upstream. À vérifier : la syntaxe exacte d'allowlist de la version épinglée
  (cf. Risques R1 — **utiliser context7/doc upstream pour la config exacte**, ne pas inventer
  les flags).
- **Mini-proxy maison (~30 lignes)** : un petit serveur (Python stdlib `http.server` ou un
  binaire Go) qui, sur `POST /restart`, fait un `POST` au socket Docker
  `/v1.43/containers/amuled/restart` et renvoie le code. Avantage : surface auditée par nous,
  aucune dépendance ; coût : du code + un test. **Si maison ⇒ TDD strict, 100 % branch** comme
  tout le reste (le HTTP réel = test d'intégration / Geoffrey).

> **Critère de choix (figé)** : l'implémenteur tranche **wollomatic vs maison selon la
> maintenabilité** (DÉCISION 3). Recommandation par défaut : wollomatic si sa config
> d'allowlist confine bien à `amuled/restart` ; sinon maison. **Quel que soit le choix, le
> proxy NE DOIT exposer QUE restart-amuled.**

### 5.3 Comment le crawler appelle le proxy — `adapters/docker_restart_http.py`

Adapter httpx implémentant `MuleRestarter` :
- `restart()` → `POST {proxy_url}/...` (chemin selon le proxy choisi : soit
  `/v1.43/containers/amuled/restart` exposé tel quel par wollomatic, soit `/restart` du maison).
- Réponse 2xx (Docker renvoie **204 No Content** sur restart réussi) → succès.
- status ≠ 2xx / timeout / connect error → `RestarterError` (la boucle l'absorbe en alerte +
  backoff). **Pas** de retry interne (le cycle suivant ré-essaiera, sous rate-limit).

### 5.4 Delta `compose.yaml` (intégration-owned — PROPOSÉ, mergé par l'orchestrateur, §4 plan)

> WT-portsync **ne touche PAS `compose.yaml`** directement (fichier intégration-owned). Il
> **propose** ce delta dans son rapport. Exemple (wollomatic) :

```yaml
  # --- proxy de restart à surface minimale (port-sync High-ID) ---
  docker-proxy:
    image: wollomatic/socket-proxy:<pin>      # épingler une version (jamais latest)
    profiles: [full]                           # restart utile en full (download/High-ID)
    # Allowlist : POST sur /v?.?/containers/amuled/restart UNIQUEMENT (cf. doc upstream
    # pour la syntaxe EXACTE des flags — ne pas inventer).
    command:
      - "-allowGET=^$"                          # exemple — À VALIDER contre la doc wollomatic
      - "-allowPOST=^/v1\\..+/containers/amuled/restart$"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    networks:
      - ec                                      # joignable par le crawler, PAS d'egress
    user: "..."                                 # selon les exigences de l'image
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    read_only: true
    restart: unless-stopped
```

Le crawler gagne la variable d'environnement / config `restarter_url` →
`http://docker-proxy:<port>` (cf. §7). amuled doit avoir un **nom de conteneur stable**
`amuled` (il l'a déjà : la clé de service `amuled`, `compose.yaml:26`) — la regex le cible par
ce nom.

> **PIÈGE socket Docker** : monter `/var/run/docker.sock` dans le proxy est la surface
> sensible ; c'est POURQUOI le proxy est minimal et le crawler ne voit JAMAIS le socket (il
> ne parle qu'au proxy, sur `ec`). Le crawler reste `cap_drop: ALL`, sans accès Docker.

## 6. Auth gluetun control-server (décision D3)

Sur le réseau interne `ec`, désactiver l'auth du control-server gluetun — **le plus simple,
défendable** (le réseau `ec` n'est pas exposé hors compose ; seuls amuled/crawler/proxy y sont).

Delta `compose.yaml` sur le service `gluetun` (intégration-owned → PROPOSÉ) :
```yaml
  gluetun:
    environment:
      # ... existant (VPN_SERVICE_PROVIDER, WIREGUARD_PRIVATE_KEY, VPN_PORT_FORWARDING: "on") ...
      # Control-server: pas d'auth sur le réseau interne `ec` (DÉCISION D3).
      HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE: '{"auth":"none"}'
```

> **À vérifier contre la doc gluetun de la version épinglée** (cf. Risques R2) : depuis gluetun
> v3.40 l'auth-by-default est activée et la route a été renommée `/v1/portforward` (acquis §3.4
> plan maître). Le nom exact de la variable et le format du rôle « none » doivent être
> **confirmés via la doc upstream** (context7/web) avant de figer le compose — **ne pas
> inventer**. La décision (auth none sur `ec`) est figée ; la **syntaxe** est à valider.

## 7. Events d'observabilité + EdgeState

Trois nouveaux events (couche domaine pure, `domain/observability/events.py`), trois branches
`describe` (`domain/observability/policy.py`), trois `MetricName`. Pattern EXACT des events
existants (frozen dataclass + union `Event` + match exhaustif `assert_never`).

### 7.1 Nouveaux events (`events.py`)

```python
@dataclass(frozen=True)
class PortSyncTriggered:
    old: int       # port configuré avant
    new: int       # port forwardé visé

@dataclass(frozen=True)
class HighIdRecovered:
    port: int      # port High-ID confirmé après restart

@dataclass(frozen=True)
class PortMismatchUnresolved:
    first_occurrence: bool   # edge-triggered (E-D8) — calculé via EdgeState
    live: int
    configured: int
```
Ajouter les trois à la tagged union `type Event = (... | PortSyncTriggered | HighIdRecovered |
PortMismatchUnresolved)`.

### 7.2 Politique (`policy.py`) — trois branches `describe` + trois `MetricName`

| Event | Severity | Audience | Métrique (sans `_total`, ajouté à l'expo) |
|---|---|---|---|
| `PortSyncTriggered` | INFO | — | `emule_port_sync_triggered` (counter `inc`) |
| `HighIdRecovered` | INFO | COMMUNITY (optionnel) | `emule_high_id_recovered` (counter `inc`) ; ou gauge `emule_ed2k_high` set 1.0 |
| `PortMismatchUnresolved` | WARNING | **OPERATIONS** si `first_occurrence` | `emule_port_mismatch` (counter `inc`) |

> **`PortMismatchUnresolved` = l'alerte de repli (DÉCISION 5)** : audience OPERATIONS, routée
> **edge-triggered** — `frozenset({Audience.OPERATIONS}) if event.first_occurrence else
> frozenset()`, EXACTEMENT comme `VerifierUnavailable`/`AllInstancesBlind`
> (`policy.py:171-177,212-218`). La métrique `emule_port_mismatch` s'incrémente à CHAQUE
> occurrence (Prometheus veut l'état brut) ; seule la NOTIF est anti-spammée (commentaire
> `policy.py` / `edge_state.py`).

> **GOTCHA exhaustivité** : les enums `Severity`/`Audience`/`MetricName` existent déjà ; ajouter
> les 3 `MetricName` à `MetricName(StrEnum)` (`policy.py:53-72`) et les 3 `case` à `describe`
> AVANT le `case _: assert_never` (sinon mypy/le test d'exhaustivité échoue → c'est voulu, TDD).

### 7.3 EdgeState — condition `"port_mismatch"`

Une seule condition `EdgeState` : `"port_mismatch"`. `enter("port_mismatch")` à la transition
vers « port faux non corrigé » (rend `True` 1re occurrence → notif) ; `leave("port_mismatch")`
au rétablissement (alignement OK ou High-ID retrouvé). L'`EdgeState` est **déjà** construit par
`CrawlerApp` (`app.py:436`) et passé aux boucles → on le passe aussi à `port_sync_loop`
(paramètre, jamais un `self.` non déclaré — règle mypy strict, cf. `app.py`).

## 8. Configuration

### 8.1 `crawler.yaml` (non secret) — nouvelle section optionnelle `port_sync`

Pattern EXACT de `DownloadConfig`/`VerifyConfig` (`crawler_config.py:31-53`) :
dataclass GELÉE optionnelle, parsée fail-fast, `_positive`/`_positive_int`.

```python
@dataclass(frozen=True)
class PortSyncConfig:
    poll_interval_seconds: float          # _positive
    restart_min_interval_seconds: float   # _positive (rate-limit)

# dans CrawlerConfig : port_sync: PortSyncConfig | None = None
# dans parse_crawler_config : if "port_sync" in raw: ... (comme download/verify)
```
```yaml
# config/crawler.yaml
port_sync:
  poll_interval_seconds: 60        # cadence du poll gluetun + compare
  restart_min_interval_seconds: 300  # ≤ 1 restart / 5 min
```

### 8.2 `local.yaml` (machine/secret) — URLs gluetun + proxy

Pattern EXACT de `verifier_url` (`local_config.py:115`) : champs optionnels, `_require_str` si
présents.

```python
# dans LocalConfig :
gluetun_control_url: str | None = None   # ex. http://gluetun:8000
restarter_url: str | None = None         # ex. http://docker-proxy:2375 (ou port du maison)
# parse_local_config : _require_str(...) si présents
```
```yaml
# config/local.yaml (et local.example.yaml)
gluetun_control_url: "http://gluetun:8000"
restarter_url: "http://docker-proxy:2375"
```

> **Déclencheur du port-sync** : la boucle port-sync s'active SSI `gluetun_control_url` ET
> `restarter_url` ET `crawler.port_sync` sont tous présents (analogue à `verifier_url`
> déclenchant le mode full, `app.py:488`). Absents → boucle OFF (Low-ID toléré, comportement
> observer/full inchangé). **Fail-fast** : si l'un est présent sans les autres, `ConfigError`
> au montage (miroir de `_require_full_config`, `app.py:235-259`). À figer dans la composition.

> **`.env.example`** : aucune nouvelle variable secrète obligatoire (l'auth gluetun est
> désactivée sur `ec`). Le delta compose pose `HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE` en clair
> (non secret). Rien à ajouter à `.env.example` sauf si l'on garde une clé d'API gluetun (non
> retenu, DÉCISION D3).

## 9. Câblage dans la composition (`composition/app.py`)

> `composition/app.py` est **intégration-owned** (plusieurs tâches y touchent — §4 plan) :
> WT-portsync **crée ses modules neufs** et **signale le câblage** ; l'orchestrateur tisse
> (ou l'agent l'ajoute si sa tâche en est seule propriétaire — à préciser au dispatch).

Câblage (miroir de `_build_full_loops`, `app.py:261-332`) :

1. **Factories injectables** (pour les tests — pattern `verifier_factory`, `app.py:90-93`) :
   ```python
   PortForwardingReaderFactory = Callable[[str], PortForwardingReader]   # url gluetun
   MuleRestarterFactory = Callable[[str], MuleRestarter]                  # url proxy
   def default_port_forwarding_reader_factory(url): return GluetunPortReader(httpx.AsyncClient(base_url=url, ...))
   def default_mule_restarter_factory(url): return HttpMuleRestarter(httpx.AsyncClient(base_url=url, ...))
   ```
2. **Construire** la boucle SSI les 3 configs sont présentes :
   - lecteur gluetun (factory) ; restarter (factory) ; `aclose` poussé sur l'`AsyncExitStack`.
   - une connexion EC dédiée à amuled pour get/set_listen_port. **Réutiliser un client EC
     existant** : l'`AmuleEcClient` du `download_endpoint` (mode full) OU une 3e connexion EC
     dédiée. **Recommandé** : une connexion EC port-sync DÉDIÉE (comme download a la sienne,
     DÉCISION D3 « connexion EC distincte ») vers l'endpoint amuled — tolère
     `MuleUnreachableError` au boot (`app.py:293-299`).
   - `PortSyncLoopDeps(reader=…, ports=ec_client, restarter=…, clock=self._clock,
     telemetry=telemetry, edge=edge, poll_interval_seconds=…, restart_min_interval_seconds=…,
     shutdown=self._shutdown)`.
3. **Ajouter au `TaskGroup`** dans `_supervise` (`app.py:371-394`) : comme download/verify,
   `if port_sync_deps is not None: tasks.append(group.create_task(port_sync_loop(port_sync_deps)))`.
   L'arrêt prompt annule explicitement chaque tâche sœur (déjà fait pour toutes — `app.py:393`).

> **Hôte EC pour le port-sync** : en prod, le client port-sync se connecte à amuled via
> l'endpoint configuré (host = `gluetun` en prod, `compose.yaml:31` ; `amuled` en smoke). C'est
> le MÊME endpoint que les autres clients EC — pas de host spécial.

## 10. Plan de tests TDD (100 % branch — cas par branche)

Tout le système réel (httpx, EC, Docker) est testé via **fakes injectés** (unit) ou marqueurs
d'intégration (Geoffrey / e2e). TDD strict : test rouge d'abord. `mypy --strict` src+tests, ruff
ligne 100, chaque test `-> None` et params typés.

### 10.1 `adapters/mule_ec/codes.py` — nouvelles constantes
- (pas de test propre — couvertes par les tests du client ci-dessous ; ce sont des `Final`).

### 10.2 `AmuleEcClient.get_listen_port` / `set_listen_port` (tests via codec, sans réseau)
Pattern des tests EC existants (faux transport en mémoire, paquets encodés par `codec`) :
- `get_listen_port` : réponse `EC_OP_SET_PREFERENCES` (0x40) portant
  `EC_TAG_PREFS_CONNECTIONS` → `EC_TAG_CONN_TCP_PORT=4662` → rend `4662`.
- `get_listen_port` : réponse SANS `EC_TAG_PREFS_CONNECTIONS` → `EcProtocolError` (les deux
  branches : parent absent ET parent présent sans enfant TCP_PORT).
- `set_listen_port(N)` : émet `EC_TAG_PREFS_CONNECTIONS` parent avec TCP+UDP = N (assert sur le
  paquet encodé), réponse `EC_OP_NOOP` → succès.
- `set_listen_port` : réponse opcode inattendu → `EcProtocolError` (via `_request`).
- (les chemins `EcTimeout/Connect` sont déjà couverts par `_request`, pas à re-tester ici).

### 10.3 `GluetunPortReader.forwarded_port` (httpx `MockTransport`/ASGI fake)
Une branche par cas du tableau §3.2 :
- 200 `{"port": 51820}` → `51820`.
- 200 `{"port": 0}` → `None`.
- 200 `{"port": -1}` → `None`. / 200 `{"port": "x"}` (non entier) → `None`. / 200 sans `port` → `None`.
- 200 corps non-JSON → `None`.
- 404 / 500 → `None` (status ≠ 200).
- `httpx.ConnectError` → `None`. / `httpx.TimeoutException` → `None`.

### 10.4 `HttpMuleRestarter.restart` (httpx fake)
- 204 (No Content) → succès (pas d'exception).
- 200 → succès. / 404 → `RestarterError`. / 500 → `RestarterError`.
- `ConnectError`/`TimeoutException` → `RestarterError`.

### 10.5 `run_port_sync_cycle` (fakes injectés) — LE cœur des branches
Un `FakePortForwardingReader`, `FakePortPreferences` (programmable :
get/set/network_status), `FakeMuleRestarter` (succès / lève `RestarterError`),
`FakeClock` (now/sleep enregistrés), `FakeTelemetry` (collecte les events émis), vrai `EdgeState`.

| Cas | Setup | Attendu (branche) |
|---|---|---|
| **port pas prêt** | reader → `None` | sleep, aucun set/restart, aucun event ; `leave` non appelé |
| **port inchangé** | live=4662, current=4662 | sleep, aucun set/restart ; `edge.leave("port_mismatch")` appelé |
| **port changé, restart OK, High-ID** | live=51820≠current ; restart OK ; network_status `ed2k_high=True` | `set_listen_port(51820)` ; `PortSyncTriggered` ; restart appelé ; `HighIdRecovered` ; `edge.leave` |
| **port changé, restart OK, PAS High-ID** | restart OK ; `ed2k_high=False` | set+restart ; `PortMismatchUnresolved(first_occurrence=True)` ; PAS de 2e restart |
| **port changé, restart ÉCHOUE** | restart lève `RestarterError` | set appelé ; `PortMismatchUnresolved(first_occurrence=True)` ; pas de re-check ; sleep |
| **rate-limit actif** | restart récent (state) ; live≠current | NI set NI restart (too_soon True) ; sleep |
| **rate-limit expiré** | dernier restart > fenêtre ; live≠current | set+restart exécutés (too_soon False) |
| **EC injoignable (get)** | `get_listen_port` lève `EcError` | absorbé ; sleep ; pas de crash |
| **EC injoignable (set)** | `set_listen_port` lève `EcError` | absorbé ; sleep ; pas de crash |
| **mismatch puis rétabli** | cycle 1 unresolved (enter) ; cycle 2 live==current | cycle 2 : `leave` rend True → réarmé (1re occ. suivante re-notifiera) |
| **edge first_occurrence False** | 2 cycles unresolved d'affilée | 2e cycle : `first_occurrence=False` → policy ne route PAS la notif (mais métrique inc) |

> **Couvrir les DEUX côtés de chaque conditionnel** (CLAUDE.md). Notamment :
> `live is None` / non-None ; `live == current` / ≠ ; `too_soon` True/False ; restart OK /
> `RestarterError` ; `ed2k_high` True/False ; `enter`/`leave` True/False.

### 10.6 `port_sync_loop` (arrêt)
- `shutdown` déjà set → 0 cycle (boucle `while not is_set()`).
- 1 cycle puis `shutdown.set()` (FakeClock le set pendant le sleep) → `break` post-cycle (pas de
  2e cycle). Miroir des tests `verification_loop`.

### 10.7 `policy.describe` — 3 nouveaux events
- `PortSyncTriggered` → INFO, métrique `emule_port_sync_triggered` inc, aucune audience.
- `HighIdRecovered` → INFO, métrique, audience (COMMUNITY si retenu).
- `PortMismatchUnresolved(first_occurrence=True)` → WARNING, métrique inc, audience OPERATIONS.
- `PortMismatchUnresolved(first_occurrence=False)` → WARNING, métrique inc, **aucune** audience.
- (l'exhaustivité `assert_never` est validée par les tests d'union existants).

### 10.8 Config
- `parse_crawler_config` : section `port_sync` présente valide → `PortSyncConfig` ; absente →
  `None` ; `poll_interval_seconds` ≤ 0 → `ConfigError` ; `restart_min_interval_seconds` ≤ 0 →
  `ConfigError` ; `port_sync` non-mapping → `ConfigError`.
- `parse_local_config` : `gluetun_control_url`/`restarter_url` présents valides → posés ;
  absents → `None` ; vides → `ConfigError`.
- Composition : déclencheur partiel (1 des 3 configs présent sans les autres) → `ConfigError`
  fail-fast (test du `_require_port_sync_config` analogue à `_require_full_config`).

### 10.9 Intégration (NON dans le gate de code — Geoffrey / e2e)
- EC réel get/set port contre un `amuled` (marqueur `ec_integration` ou nouveau marqueur).
- Restart réel via proxy + High-ID réel : **couvert par la suite e2e couche B** (WT-e2e valide
  port-sync via le stub `/v1/portforward`, plan §5/§7). Le proxy maison, s'il est retenu, a son
  propre test (HTTP réel = marqueur d'intégration).

## 11. Risques / à confirmer

- **R1 — syntaxe d'allowlist `wollomatic/socket-proxy`** : la forme exacte des flags
  (`-allowPOST=<regex>`, échappement, ancrage) **n'est PAS vérifiée dans `vendor/`** (pas de
  source wollomatic clonée). À **confirmer via la doc upstream (context7/web)** avant de figer
  le compose. La DÉCISION (allowlist confinée à `amuled/restart` par regex+méthode) est figée ;
  la syntaxe est à valider. Si wollomatic ne confine pas proprement par conteneur → **basculer
  sur le mini-proxy maison** (DÉCISION 3 laisse ce choix à l'implémenteur).
- **R2 — nom/format de la variable d'auth gluetun** :
  `HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE='{"auth":"none"}'` est le mécanisme retenu (DÉCISION
  D3) mais le **nom exact de la variable et le format JSON du rôle** dépendent de la version
  gluetun épinglée (auth-by-default introduite v3.40, acquis §3.4). À **confirmer via la doc
  gluetun upstream** — ne pas inventer. De même, vérifier que la route est bien
  `GET /v1/portforward` → `{"port":N}` sur la version épinglée (renommée depuis v3.40).
- **R3 — opcode de réponse de `GET_PREFERENCES`** : la lecture amont
  (`ECSpecialMuleTags.cpp:83`, `CEC_Prefs_Packet : CECPacket(EC_OP_SET_PREFERENCES, ...)`)
  indique que la réponse à `EC_OP_GET_PREFERENCES` (0x3F) porte l'opcode **`EC_OP_SET_PREFERENCES`
  (0x40)**, PAS 0x3F. À **vérifier en intégration** contre un vrai `amuled` (le test
  `get_listen_port` doit asserter l'opcode réel reçu). Si l'observation diffère, ajuster
  l'`expected_opcode` du `_request`.
- **R4 — `EC_DETAIL_FULL` requis pour GET_PREFERENCES ?** : `amulecmd` lit le port avec un
  `CECPacket(EC_OP_GET_PREFERENCES)` sans forcer le détail (TextClient.cpp:566) ; le serveur lit
  `request->GetDetailLevel()` (ExternalConn.cpp:2079). `EC_DETAIL_CMD` (défaut) devrait suffire
  pour récupérer `EC_TAG_CONN_TCP_PORT`. À **confirmer empiriquement** ; en cas de doute, émettre
  `EC_DETAIL_FULL`.
- **R5 — délai de rebind d'amuled après restart** : le re-check High-ID dort
  `poll_interval_seconds` avant de lire le connstate (§4.4). Si amuled met plus de temps à se
  reconnecter à un serveur eD2k, `ed2k_high` sera momentanément `False` → une alerte
  `PortMismatchUnresolved` de trop pourrait partir. **Mitigation déjà en place** : edge-triggered
  (une seule notif par épisode) + rate-limit (pas de re-restart). À **affiner empiriquement** le
  délai en e2e (couche B). Option : un délai de grâce dédié `highid_recheck_delay_seconds` distinct
  du `poll_interval` (laissé en option, non figé).
- **R6 — réutilisation de connexion EC** : §9 recommande une connexion EC port-sync DÉDIÉE.
  Alternative (réutiliser celle du download) = une connexion de moins mais couple les deux
  boucles sur le même transport (rappel : « une requête à la fois, FCFS » — `client.py:6`). La
  connexion dédiée est plus sûre (pas de contention) ; coût = une socket EC de plus. **Décision
  recommandée mais à acter par l'implémenteur** (les deux satisfont le Protocol narrow).

## 12. Part de Geoffrey (réseau vivant — hors sandbox)

- **EC get/set port réel** contre un `amuled` (le sandbox n'a pas de veth — mémoire :
  « tests d'intégration = vrai shell »). Vérifier R3 (opcode de réponse) et R4 (detail level).
- **Restart réel** d'amuled via le proxy + **High-ID réel** : couvert par la suite **e2e couche
  B** (WT-e2e), qui valide port-sync via le stub `/v1/portforward` et le port-check `ed2kd`.
- **Validation auth gluetun** (R2) et **route `/v1/portforward`** sur la version gluetun épinglée
  derrière le VPN réel (PF ProtonVPN actif) — le PF lui-même n'est PAS testé en CI (job de
  gluetun, de confiance, §3.4 plan).
- **Choix final wollomatic vs maison** (R1) selon la maintenabilité observée.
```
