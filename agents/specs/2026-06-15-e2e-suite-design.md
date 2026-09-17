# Design — suite e2e A+B (« est-ce que ça marche vraiment » + reproductibilité contributeur)

> **Nature** : design **DÉJÀ DÉCIDÉ** (co-design figé avec Geoffrey, session post-Plan E). Ce doc
> documente fidèlement la suite e2e à construire ; il ne re-designe pas. Il est l'unique brief de
> l'agent WT-e2e (cf. plan maître `2026-06-15-backlog-parallelization-design.md` §5). Toute
> contradiction relevée entre les décisions figées et le code/vendor réel est notée en
> `## Risques / à confirmer`, jamais « corrigée » de mon chef.
>
> **Ancrages source** (lus, cités `fichier:ligne`) : `vendor/ed2kd/` (serveur eD2k C),
> `packages/crawler/src/` (chaîne download→quarantaine→verify + moteur de matching), et le PATRON
> smoke existant (`packages/crawler/tests/integration/test_compose_smoke.py`, `compose.smoke.yaml`,
> `deploy/smoke/`).

---

## 1. Objectif

La suite **dérisque « ça marche vraiment »** end-to-end et **rend la stack reproductible par un
contributeur externe** avec rien d'autre que Docker. Le smoke existant (`compose_integration`,
spec packaging §5) valide le **câblage** de la stack (build OK, verifier healthy, fail-fast) mais
**JAMAIS un téléchargement réel** : il n'y a ni serveur eD2k ni VPN, donc aucun octet ne transite
et `resolve_staging_path` (le `os.replace` depuis le vrai staging amuled, **DV10, jamais exercé en
test**) n'est jamais déclenché. La présente suite ferme exactement ce trou, en **deux couches** :

- **Couche A — chemin crawl déterministe, sans download réel.** Un **stub Python eD2k server** (pur,
  asyncio) répond à un amuled (celui du crawler) avec un HighID forcé, un résultat de recherche
  planté et une source plantée. Le crawler observe → `FileObservation` → décision de match → file de
  vérif. **100 % testable unitairement en sandbox** (pur Python, pas de Docker).
- **Couche B — download → verify RÉEL (couvre le 🔴).** On bâtit **`gureedo/ed2kd`** (serveur eD2k C
  vendoré), un amuled **seeder** partage un **fichier planté** valide en HighID, l'amuled **leecher**
  du crawler télécharge **les octets réels** → `os.replace` depuis le **vrai staging amuled** (exerce
  `resolve_staging_path`) → quarantaine → le verifier analyse → verdict.

La **couche C** (« chaîne WireGuard + PF réel ») est **ABANDONNÉE** (plan maître §3.4 / §8) : gluetun
ne fait pas de port-forwarding pour un custom-WireGuard sans impersonate un provider commercial
(`vendor/gluetun/internal/configuration/settings/portforward.go:68-76` : PF limité à Proton/PIA/
Privatevpn/Perfectprivacy), et cela testerait du tiers de confiance (gluetun) plutôt que notre code.
La **validation port-sync est donc repliée dans la couche B** (§5 ci-dessous, plan maître §3.5) via
un stub HTTP minimal — **zéro WireGuard, zéro impersonation, zéro `CAP_NET_ADMIN`**.

Marqueur dédié **`e2e_integration`**, désélectionné par défaut, Docker+compose requis, lancé par
Geoffrey. Conçu pour qu'un **contributeur externe** reproduise tout avec juste Docker.

---

## 2. Acquis figés depuis le vendor (à NE PAS re-investiguer)

Lecture de `vendor/ed2kd/` confirmant les décisions du plan maître §3.5 :

| Fait | Ancrage source |
|---|---|
| Chemin sources : `OP_OFFERFILES 0x15` publie, `OP_GETSOURCES 0x19` interroge par hash, `OP_FOUNDSOURCES 0x42` renvoie `<hash16><count1>(<ip4><port2>)[count]`. | `ed2k_proto.h:51,54,77` ; `server.c:397-409` ; `client.c:129-136` ; `packet.c:131-144` |
| **Port-check réel = HighID requis** : à la réception de `LOGINREQUEST`, ed2kd ouvre une connexion TCP **entrante** vers `client_ip:client_port` et envoie un `OP_HELLO 0x01` ; il attend `OP_HELLOANSWER 0x4C` portant le bon hash. Succès → **HighID = `clnt->id = clnt->ip`** ; échec/timeout → LowID (si `allow_lowid`) sinon déconnexion. | `server.c:116` (`client_portcheck_start`) ; `client.c:138-195` ; `portcheck.c:16-74,141-148` |
| **DB en mémoire VOLATILE et partagée** : `DB_NAME = "file:memdb?mode=memory&cache=shared"`. Le seeder amuled DOIT donc rester connecté pour que sa source survive (un client supprimé → `db_remove_source`, `client.c:84-88`). | `db_sqlite.c:21-22,127` ; `client.c:84-88` |
| Recherche par mot-clé : `db_search_files` traduit l'arbre en requête **FTS4** (`fnames MATCH ?`). | `db_sqlite.c:69 (fts4), 286-311` |
| `IDCHANGE 0x40` porte `<id4><tcp_flags4>`. | `ed2k_proto.h:75,98-103` ; `packet.c:14-25` |
| `SEARCHRESULT 0x33` : `<count4>` puis par fichier `<hash16><id4><port2><tag_count4>[tags…]` ; les tags incluent au minimum `TN_FILENAME 0x01` (string) + `TN_FILESIZE 0x02`. | `ed2k_proto.h:65,121-132` ; `packet.c:146-228` |

**Ajustements build ed2kd** (CMakeLists vendoré, `vendor/ed2kd/CMakeLists.txt`) :
- `cmake_minimum_required(VERSION 2.8.7)` (l.1) → rejeté par CMake ≥ 4 → passer
  `-DCMAKE_POLICY_VERSION_MINIMUM=3.5`.
- Flags `RELEASE` toxiques sur toolchain 2026 (l.70-71) : `-Ofast -flto -march=native -funroll-loops`
  → écraser via `-DCMAKE_BUILD_TYPE=Release -DCMAKE_C_FLAGS_RELEASE="-O2 -DNDEBUG"`. `-march=native`
  casse la portabilité (build host ≠ run host), `-flto`/`-Ofast` sont fragiles.
- **`sqlite3.c` vendoré gardé** (`3rdparty/sqlite3/sqlite3.c`, CMakeLists l.50) : compilé avec
  `-DSQLITE_ENABLE_FTS4` (l.58), indispensable pour `fnames MATCH` (la recherche). Ne PAS le
  remplacer par la lib système.
- Dépendances apt : `cmake build-essential libevent-dev libconfig-dev zlib1g-dev` (cf. CMakeLists
  l.20-23 `find_package(Threads/Libevent/Libconfig/ZLIB)`). `-fopenmp` au link (l.68) → fourni par
  gcc de `build-essential`. `find_library(M_LIB m)` (l.66) → libm, présent.

---

## 3. Couche A — stub Python eD2k server (pur, unit-testable en sandbox)

### 3.1 Rôle et frontière

Un **serveur eD2k minimal en Python asyncio** (~250-400 lignes), suffisant pour qu'un amuled s'y
connecte, cherche, et que le crawler **observe un fichier planté puis le matche**. Il **force un
HighID** (pas de port-check réel : on ne peut/veut pas joindre amuled en entrant dans tous les
contextes ; le stub *décide* HighID). Il sert **un seul résultat de recherche planté** et **une
seule source plantée**.

**Le stub ne télécharge rien** : il n'expose que les opcodes serveur. Le download réel est la
couche B. **Hexagonal** : ce stub est un **outil de test** (`tests/e2e/` ou `tools/`), pas du code
de prod — il n'est dans aucun import de `emule_indexer`. Il est néanmoins écrit avec la même rigueur
(mypy strict, ruff, **100 % branch sur le codec pur**).

### 3.2 Framing & codec

Cadre eD2k confirmé `ed2k_proto.h:36-90` :

```
[proto u8 = 0xE3 (PROTO_EDONKEY)] [length u32 LE] [opcode u8] [payload …]
                                   ^-- length = 1 (opcode) + len(payload)
```

- `proto` : on n'émet QUE `0xE3` (PROTO_EDONKEY non compressé). On **accepte** `0xD4` (PROTO_PACKED,
  zlib) en lecture mais on peut le **rejeter proprement** (déconnexion) si aMule l'utilise — à
  confirmer (cf. risques). aMule envoie typiquement le login non compressé.
- `length` = `len(opcode) + len(payload)` = `1 + len(payload)`, **u32 little-endian** (le header est
  `__attribute__((packed))`, donc pas de padding — `packet_header{uint8 proto; uint32 length}`).
- **Tags** (`tag_header`, `ed2k_proto.h:163-167`) : `[type u8][name_len u16 LE][name…][value…]`. Le
  stub n'a besoin de **lire qu'un seul tag** (la feuille nom du search, §3.4) et d'**écrire** les
  tags d'un résultat (filename string + filesize). Le bit `0x80` du type = « tag court à nom
  entier » (`server.c:67,157`) — le stub émet des tags **longs** (`name_len=1`, `type` sans `0x80`),
  forme qu'ed2kd lui-même émet (`packet.c:176-178`).

Le codec (encode/decode header + tag string + tag uint) est une **fonction pure**, unit-testée à
100 % branch (round-trips, troncatures, longueurs limites).

### 3.3 Les 6 opcodes (3 reçus, 3 émis ; + 2 optionnels)

**Reçus** (l'amuled → stub) :

1. **`OP_LOGINREQUEST 0x01`** (`ed2k_proto.h:48`, payload `<hash16><id4><port2><tag_count4>[tags…]`).
   Le stub lit le hash + le port annoncé du client (tag `TN_PORT 0x0F` ou champ port), **ignore les
   autres tags** (parse tolérant : `name_len`, skip de la valeur selon le type), puis **répond
   immédiatement** par la séquence de connexion (`IDCHANGE` HighID + optionnels), **sans port-check**.
   Réf. de la séquence réelle ed2kd : `server.c:363-374` (welcome message + login) puis, après
   port-check, `client.c:191` (`send_id_change`). Le stub **court-circuite** le port-check.
2. **`OP_SEARCHREQUEST 0x16`** (`ed2k_proto.h:52`, payload = arbre de recherche). Le stub **n'extrait
   que la feuille de terme-chaîne nom** : un nœud `SO_STRING_TERM 0x01` (`ed2k_proto.h:266` ;
   `server.c:289-296`) est `[0x01][str_len u16 LE][str…]`. **On ne parse PAS l'arbre complet** (AND/
   OR/NOT, contraintes taille/type) — on scanne le payload pour le **premier** terme-chaîne nom et on
   l'ignore d'ailleurs (le stub renvoie toujours **le même** résultat planté quel que soit le mot-clé,
   cf. risque « encodage arbre »). Réf. encodage : tout le `process_search_request`, `server.c:247-356`.
3. **`OP_GETSOURCES 0x19`** (`ed2k_proto.h:54`, payload = `<hash16>`, `server.c:397-399` :
   `PB_LEFT(pb) == ED2K_HASH_SIZE`). Le stub lit le hash et **répond `FOUNDSOURCES` avec la source
   plantée** (toujours, indépendamment du hash — ou en matchant le hash planté ; cf. §3.5).

**Émis** (stub → amuled) :

4. **`OP_IDCHANGE 0x40`** (`ed2k_proto.h:75`, payload `<id4><tcp_flags4>`, `packet.c:14-25`). **HighID
   forcé** : `id = IP_du_peer en u32 LE`, avec la contrainte **`id ≥ 0x01000000`** (= `MAX_LOWID`,
   `ed2k_proto.h:10` ; un id `< MAX_LOWID` serait interprété LowID par aMule). On reflète l'IP source
   de la connexion TCP (`writer.get_extra_info("peername")`), convertie en u32 **little-endian** —
   c'est ce qu'ed2kd fait : HighID ⇒ `clnt->id = clnt->ip` (`client.c:188`), et `clnt->ip` vient de
   `sa_in->sin_addr.s_addr` (network order = big-endian) écrit tel quel dans le paquet (pas de
   `htonl`/`ntohl` à l'émission, `packet.c:21`). **À confirmer côté endianness** (cf. risques) : on
   reproduira l'ordre d'octets d'ed2kd (octets de l'IP dans l'ordre réseau, écrits tels quels dans le
   champ u32 du paquet). `tcp_flags` = `0` (ou un sous-ensemble sûr de `SRV_TCPFLG_*`,
   `ed2k_proto.h:14-21`). Si l'IP du peer est dans une plage privée (Docker), elle reste `≥
   0x01000000` (ex. `172.x` = `0xAC…`), donc HighID valide pour aMule.
5. **`OP_SEARCHRESULT 0x33`** (`ed2k_proto.h:65,121-132`). **Un seul** résultat : `<count4 = 1>` puis
   `<hash16 = HASH_PLANTÉ><id4><port2><tag_count4>` + tags `TN_FILENAME 0x01`(string = nom planté) et
   `TN_FILESIZE 0x02`(uint). Réf. d'encodage : `packet.c:159-228` (`write_search_file`). Le `<id>`/
   `<port>` ici réfèrent une source pour ce fichier ; pour le crawler de la couche A on n'en a pas
   besoin (il n'observe que le **nom** — rappel : **EC n'expose aucune métadonnée média**, plan
   maître §3.2). Le **nom planté est Keroro-class** (§4) pour que le moteur émette une décision.
6. **`OP_FOUNDSOURCES 0x42`** (`ed2k_proto.h:77`, `packet.c:131-144`). `<hash16><count1 = 1>` +
   **une** source `<ip4><port2>` (`file_source`, `ed2k_proto.h:82-85`). En couche A, peu importe
   (pas de download), mais on renvoie une source bien formée pour que le cycle GETSOURCES→FOUNDSOURCES
   soit exercé de bout en bout.

**Optionnels** (cosmétique, aident aMule à se croire « connecté ») :

- **`OP_SERVERMESSAGE 0x38`** (`ed2k_proto.h:69`, `packet.c:27-38`) : `<msg_len2><message>`, message
  de bienvenue. ed2kd l'envoie en tête de login (`server.c:368`).
- **`OP_SERVERSTATUS 0x34`** (`ed2k_proto.h:66`, `packet.c:40-51`) : `<user_count4><file_count4>`.

### 3.4 Inconnu résiduel — encodage de l'arbre de recherche

La décision figée est : **ne parser QUE la feuille de terme-chaîne nom `SO_STRING_TERM 0x01`** et
**ignorer** la structure (AND/OR/NOT, contraintes). Justification : aMule peut emballer le mot-clé
dans un arbre arbitraire (`server.c:247-356` montre la richesse : AND/OR/NOT, extension, codec, type,
min/maxsize, srcavail…). Le stub **renvoie toujours le même résultat planté** → il n'a pas besoin de
comprendre la requête, juste de répondre `SEARCHRESULT`. On scanne tolérant : on cherche le premier
octet `0x01` introduisant un terme-chaîne `[0x01][len u16][str]` et on s'arrête là (best-effort, sans
échouer si on ne le trouve pas — on répond quand même). **Risque** (§8) : l'encodage exact qu'aMule
3.0.0 émet sur EC `start_search` (le crawler ne parle PAS eD2k directement — c'est **amuled** qui
forge le `SEARCHREQUEST` eD2k vers le stub). À valider en couche A réelle (Geoffrey).

### 3.5 Hash planté & cohérence couche A / couche B

Le **HASH_PLANTÉ** du `SEARCHRESULT` doit être le **hash ed2k connu** du fichier planté (§4) pour que
la couche A et la couche B parlent du **même** fichier (et que, le cas échéant, le `GETSOURCES` du
crawler porte ce hash). Le stub peut soit (a) répondre `FOUNDSOURCES` quel que soit le hash demandé
(plus simple, tolérant), soit (b) ne répondre que si le hash == HASH_PLANTÉ. **Décision figée : (a)
tolérant** (le stub est un faux-serveur de test, pas un index).

### 3.6 Plan de tests unitaires (sandbox, 100 % branch sur le codec)

Le **codec pur** est intégralement unit-testé (TDD) :
- `encode_header`/`decode_header` round-trip ; `length` = `1 + len(payload)` ; u32 LE.
- `encode_string_tag` / `decode_string_tag` (filename) round-trip + troncature à `MAX_FILENAME_LEN`.
- `encode_uint_tag` (filesize u64/u32) ; `build_idchange(ip)` → u32 forcé `≥ 0x01000000` (test que
  `0.0.0.x` lèverait/serait corrigé ; que `172.x` reste HighID).
- `build_searchresult(name, size, hash)` → octets exacts (golden bytes), `count==1`.
- `build_foundsources(hash, [(ip,port)])` → `<hash16><count1>(<ip4><port2>)`.
- `extract_keyword(search_payload)` → trouve le terme-chaîne nom ; payload sans terme → `None` sans
  lever (les deux branches).

Le **dispatch asyncio** (lecture framée, routage par opcode, réponses) est testé via une **paire de
sockets en mémoire** (`asyncio` StreamReader/Writer connectés bout à bout) — pas de réseau réel, donc
**exécutable en sandbox**. Chaque opcode reçu → assertion sur les octets émis. Branches d'erreur
(opcode inconnu, header tronqué, proto inattendu) couvertes. Le serveur réel (bind sur un port) est
exercé **uniquement** sous le marqueur Docker (couche B / lancement par Geoffrey), `# pragma: no
cover` sur le `serve_forever`.

---

## 4. Fichier planté (média valide, hash ed2k connu, cible câblée)

### 4.1 Le média

Un **petit média valide** sur lequel **ffprobe passe** → le verifier le classe `clean` (le
`type_sniff` via puremagic ne le voit ni exécutable ni archive → `clean` ; `ffprobe` remplit
`real_meta` durée/bitrate/codec/conteneur). Génération **reproductible** (script versionné sous
`tests/e2e/fixtures/` ou `deploy/e2e/`) :

```
ffmpeg -f lavfi -i testsrc=duration=1:size=128x128:rate=10 \
       -f lavfi -i sine=frequency=440:duration=1 \
       -c:v libx264 -c:a aac -shortest -y planted.mp4
```

(Format/codec à figer pour que le hash soit **stable** ; cf. risque « déterminisme ffmpeg » §8 — un
même ffmpeg/mêmes flags donne le même fichier, mais une version d'ffmpeg différente peut changer un
octet. Mitigation : on **commite le fichier binaire** `planted.mp4` dans le repo de test ET le script
qui l'a produit, et on **calcule le hash ed2k DEPUIS le binaire commité**, pas depuis une
re-génération.)

### 4.2 Hash ed2k

Le **hash ed2k** (ed2k root hash) est : MD4 par chunk de **9 728 000 octets** ; si un seul chunk →
le hash du fichier est la MD4 de ce chunk ; si plusieurs chunks → MD4 de la concaténation des MD4 de
chunks. Notre média < 9,28 Mio ⇒ **un seul chunk** ⇒ `ed2k_hash = md4(bytes)` (hex, 32, minuscule).
On le calcule une fois, on le **fige en constante** (`PLANTED_ED2K_HASH`) partagée par le stub
(couche A) et les fixtures (couche B). **Ne pas** utiliser la MD4 du fichier vide (`31d6cfe0…`) : un
fichier 0-octet est traité « instantanément complet » par amuled et **jamais listé** comme partfile
actif (leçon du `download_integration`, `test_amuled_download.py:22-28`).

Implémentation du calcul : `hashlib.new("md4")` n'est plus garanti dispo (OpenSSL 3 retire MD4 du
provider par défaut) → utiliser une impl MD4 pure (petite, ~40 lignes) ou `Cryptodome.Hash.MD4`, **en
outil de test seulement** (pas une dép de prod ; déclarée dans le brief, lockée par l'orchestrateur).
**Risque** (§8) : disponibilité de MD4 sur le runner.

### 4.3 Câblage de la cible (matcher)

Pour que le moteur **émette une décision `tier=download`** sur le nom planté, on réutilise le PATRON
des configs smoke (`deploy/smoke/{matcher,targets}.yaml`) en les adaptant pour `deploy/e2e/`. Le
moteur (`domain/matching/`) matche un `FileCandidate` (le **nom** observé) contre les cibles ; une
règle `tier: download` produit une `MatchDecision`, persistée, qui devient un `DownloadCandidate`
(`engine.py:82-93`) consommé par la boucle download.

Le nom planté DOIT donc satisfaire une règle `download`. La règle smoke `id_segment_exact`
(`deploy/smoke/matcher.yaml:13`) exige `all: [is_video, segment_id, keroro]` :
- `is_video` = regex `\.(avi|mkv|mp4|mpg|ogm)$` → notre `planted.mp4` **satisfait** (extension `.mp4`).
- `segment_id` = regex `n[°o]?\s*0*{number}\s*{segment}` → le nom doit contenir p.ex. `n°62 A`.
- `keroro` = keyword `keroro`.

⇒ **nom planté retenu** : `Keroro n°62 A.mp4` (ou un alias couvrant `is_video`+`segment_id`+`keroro`),
avec la cible `S2E062A` de `targets.yaml` (saison 2, épisode 62, segment A — `deploy/smoke/targets.yaml`).
La config e2e fige ce nom et cette cible ; le **hash** du fichier (§4.2) est indépendant du nom
(le nom est métadonnée, le hash est contenu). On vérifie en test e2e que la décision émise est
`tier=download, target_id=S2E062A`.

---

## 5. Couche B — download → verify RÉEL + port-sync replié

### 5.1 Dockerfile ed2kd vendoré

Fichier **neuf** (l'agent WT-e2e le crée), p.ex. `deploy/e2e/ed2kd/Dockerfile`, source = `vendor/ed2kd`
(commit pinné **`f6c330da`**, à matérialiser : soit un tarball vendoré commité, soit le sous-dossier
`vendor/ed2kd/` déjà présent — cf. plan maître §2 : « vendore le tarball ed2kd »). Multi-stage :

```dockerfile
# --- build ---
FROM debian:bookworm AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
        cmake build-essential libevent-dev libconfig-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
COPY vendor/ed2kd /src/ed2kd
WORKDIR /src/ed2kd/build
RUN cmake .. \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_C_FLAGS_RELEASE="-O2 -DNDEBUG" \
    && cmake --build . --target ed2kd -j

# --- runtime ---
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        libevent-2.1-7 libconfig9 zlib1g \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/ed2kd/build/ed2kd /usr/local/bin/ed2kd
COPY deploy/e2e/ed2kd/ed2kd.conf /etc/ed2kd.conf
EXPOSE 4661
ENTRYPOINT ["ed2kd", "-c", "/etc/ed2kd.conf"]
```

Notes d'ancrage :
- Les 3 ajustements cmake correspondent un-à-un aux toxicités relevées (`CMakeLists.txt:1,70-71`).
- `sqlite3.c` reste compilé par le CMakeLists (l.50) avec FTS4 (l.58) — **on ne touche pas** au build
  SQLite (la recherche en dépend).
- `ed2kd.conf` = copie de `ed2kd.conf.dist` avec **`allow_lowid = 1`** (déjà la valeur dist, l.17 —
  filet de sécurité si le port-check échoue, voir §5.4), `listen_port = 4661` (l.8), un `server_hash`
  valide (l.2, 32 hex). `EXPOSE 4661` = port d'écoute eD2k confirmé (`config.c:62`, `listener.c:72`).
- Le flag réel d'invocation (`-c <conf>` ?) est à confirmer depuis `main.c` (cf. risques).

### 5.2 Topologie HighID (port-check ed2kd → amuled)

**Décision figée et CRITIQUE.** Le port-check d'ed2kd est une connexion TCP **entrante de ed2kd vers
amuled** (`client.c:147-150` : `bufferevent_socket_connect` vers `clnt->ip:clnt->port`). Pour
qu'ed2kd accorde un **HighID** au seeder (HighID = source contactable = condition pour être listé
comme source utile), il faut qu'**ed2kd puisse joindre directement le port TCP eD2k d'amuled** :

- **Même réseau Docker, routage direct, PAS de double-NAT.** Le seeder amuled et ed2kd sont sur le
  **même réseau bridge** `compose.e2e.yaml` ; ed2kd voit l'IP réelle du conteneur amuled (`clnt->ip`
  vient de `sa_in->sin_addr.s_addr` à l'`accept`, `listener.c:34`) et s'y connecte sur le port eD2k
  qu'amuled a annoncé dans son `LOGINREQUEST` (`server.c:58,84-86`). Sur un bridge Docker, IP de
  conteneur ↔ IP de conteneur est routable directement → le port-check réussit → **HighID**.
- **Conséquence : pas de `network_mode: service:gluetun`** ici (qui masquerait l'IP et casserait le
  port-check). C'est exactement la raison pour laquelle la couche C (VPN) est abandonnée : sous VPN
  l'IP n'est pas directement joignable, et reproduire un PF réel demanderait d'impersonate un
  provider.
- Le **port eD2k** d'amuled : l'image `ngosang/amule:3.0.0-1` code `Port=4662` en dur
  (`amule-config.sh:84-92`, plan maître §3.3). amuled annonce ce port dans son login → ed2kd le
  port-checke sur `4662`. Donc **exposer 4662 du seeder sur le réseau e2e** (pas besoin de le mapper
  vers l'hôte ; conteneur-à-conteneur suffit).

### 5.3 Seeder / leecher / fichier planté

Trois acteurs sur le réseau `compose.e2e.yaml` :

1. **ed2kd** : sert d'index. DB en mémoire volatile (`db_sqlite.c:21`) → les sources ne survivent
   qu'au temps de connexion du seeder ⇒ le seeder reste connecté toute la durée du test.
2. **amuled seeder** : partage le **fichier planté** (`planted.mp4`, §4) — placé dans son répertoire
   incoming/shared via un bind-mount au démarrage —, se connecte à ed2kd, obtient un **HighID**
   (port-check OK, §5.2), et `OFFERFILES 0x15` publie le hash + nom + taille (`server.c:402-409`,
   `client.c:197-235`). Configuré pour pointer son serveur eD2k sur `ed2kd:4661`.
3. **amuled leecher** (= celui du crawler) : reçoit du crawler un `add_link(ed2k://|file|Keroro
   n°62 A.mp4|<size>|<HASH>|/)` (`ed2k_link.py`), interroge ed2kd `GETSOURCES 0x19`, reçoit la source
   `FOUNDSOURCES 0x42` (le seeder en HighID), **se connecte au seeder et télécharge les octets
   réels**.

Le crawler n'a **pas besoin d'avoir « observé »** le fichier en couche A pour la couche B : on peut
**amorcer** la file de download directement (le crawler, en mode full, lit ses `download_decisions`
du catalogue). Deux montages possibles, **décision figée = le plus simple** : on **pré-amorce le
catalogue** (ou on laisse la couche A tourner d'abord) pour que le crawler émette une décision
`download` sur le hash planté, puis la boucle download fait le reste. (En pratique on peut faire
tourner A puis B dans le même `compose.e2e.yaml` : A peuple l'observation, B fournit la source réelle.)

### 5.4 `resolve_staging_path` exercé pour de vrai (DV10)

C'est **le cœur du dérisquage**. Quand le partfile leecher est **complet** (`size_done >= size_full`,
`mule_download_client.py:34-40`), la boucle download appelle `quarantine.promote` avec la **SOURCE** =
`resolve_staging_path(staging_base, catalog, entry)` (`composition/app.py:104-130,314`). Cette source
est :
- `staging_base / basename(filename de la dernière observation)` si une observation a survécu
  (anti-traversal : `Path.name` + rejet de `{'', '.', '..'}`, `app.py:121-130`), sinon
- `staging_base / entry.ed2k_hash` (fallback best-effort).

La **destination** est par hash (côté `quarantine_fs`, `os.replace` atomique). Tout le smoke existant
**ne déclenche jamais** ce chemin (aucun partfile ne complète). La couche B le **déclenche réellement** :
amuled écrit le fichier complété dans son **vrai staging** (le répertoire qu'amuled utilise pour les
fichiers finis ; monté sur `/data/quarantine` côté crawler, partagé avec amuled, cf.
`deploy/smoke/local.full.yaml` : `staging_dir = quarantine_dir = /data/quarantine`). Le crawler fait
`os.replace` depuis ce staging réel → quarantaine. On **asserte** :
1. le partfile complète (`size_done == size_full`) ;
2. `resolve_staging_path` résout le bon chemin (basename de l'observation `Keroro n°62 A.mp4`) ;
3. `os.replace` réussit (le fichier apparaît en quarantaine par hash) ;
4. le verifier l'analyse → verdict `clean` + `real_meta` non vide (ffprobe a lu le média).

C'est la **première et seule** exécution réelle de DV10. **Risque** (§8) : le nom/chemin exact du
répertoire staging d'amuled 3.0.0 dans l'image ngosang (où amuled dépose un fichier *fini*), et si
`os.replace` cross-device est évité (même volume `/data/quarantine` ⇒ même FS ⇒ rename atomique OK).

### 5.5 Validation port-sync repliée (stub `/v1/portforward`)

Décision figée (plan maître §3.5 / §8 couche C abandonnée). On **ne teste PAS le PF gluetun** (job de
tiers de confiance). On teste **« amuled écoute sur le port annoncé ET est joignable en entrant »** —
et le **port-check d'ed2kd est précisément la preuve d'entrée** :

1. Un **stub HTTP minuscule** (~10 lignes, asyncio/`http.server` ou Starlette) sert
   **`GET /v1/portforward` → `{"port": N}`** (la route exacte que la boucle port-sync lit, plan
   maître §3.4 : `GET http://gluetun:8000/v1/portforward`). Zéro WireGuard, zéro auth (ou auth stubée).
2. La **boucle port-sync** (livrée par WT-portsync) lit `N`, fait **EC `SetPort(N)` + restart amuled
   via le proxy de restart** (mécanisme figé plan maître §3.3 : le port n'est pas re-bindable à chaud,
   donc `SetPreferences` persiste `N` au shutdown, puis amuled re-bind `N` au boot).
3. **Assertion** : après le restart, amuled **écoute sur `N`** ET est **joignable en entrant sur `N`**
   — prouvé par le fait qu'ed2kd, dont le port-check se connecte sur le port annoncé par amuled,
   **accorde le HighID**. Si amuled n'écoutait pas / n'était pas joignable sur `N`, le port-check
   échouerait → LowID. Donc **HighID observé ⇔ port-sync correct**.

L'e2e **consomme l'API** de la boucle port-sync (le stub `/v1/portforward` + l'observation HighID),
**pas son code** → pas de dépendance de build entre WT-portsync et WT-e2e (plan maître §5 note
dépendances). Si WT-portsync n'est pas encore intégré quand l'e2e tourne, ce **sous-test** est
skippable indépendamment (marqueur ou skip conditionnel) ; le download→verify réel (§5.4) reste la
valeur centrale.

---

## 6. Marqueur `e2e_integration` & `compose.e2e.yaml`

### 6.1 Marqueur (PATRON = `compose_integration`)

`e2e_integration` suit **exactement** le patron `compose_integration`
(`test_compose_smoke.py:41`, `pytestmark = pytest.mark.compose_integration`) :

- Déclaré dans `[tool.pytest.ini_options].markers` du paquet crawler.
- **Désélectionné par défaut** (le `addopts` du paquet déselectionne déjà les marqueurs
  d'intégration ; ajouter `e2e_integration` à la liste `-m "not …"`), **exclu du coverage** (comme
  les autres `*_integration` — le `--cov-fail-under=100` ne le voit pas).
- Le module de test **n'importe AUCUN module de prod** `emule_indexer` (il pilote la stack par
  `subprocess docker compose`, exactement comme `test_compose_smoke.py` qui n'importe que
  `json/os/subprocess/…`). → **le 100 % branch est préservé** par construction (rien à couvrir).
- **Docker + docker compose v2 requis ; lancé par Geoffrey** (`( cd packages/crawler && uv run pytest
  -m e2e_integration --no-cov )`). Le sandbox n'a pas de veth/réseau Docker complet (mémoire
  « integration-tests-need-real-shell ») → l'agent **écrit** le test, **ne le lance pas**.

Le **stub Python (couche A)**, lui, EST unit-testé en sandbox (codec pur + dispatch sur sockets
mémoire, §3.6) sous le run normal — c'est la partie « Auto » du WT-e2e (plan maître §5).

### 6.2 `compose.e2e.yaml` (fichier NEUF, l'agent peut le créer seul)

Le plan maître (§4, §5) autorise explicitement l'agent e2e à **créer un fichier compose neuf et
dédié** (contrairement aux `compose.*.yaml` existants qui sont intégration-owned). On crée donc
**`compose.e2e.yaml`** (+ `deploy/e2e/*` pour les configs/Dockerfile/fixtures), assemblant la stack
**SANS gluetun** (comme `compose.smoke.yaml` retire gluetun, `compose.smoke.yaml:13-15`), avec :

- `ed2kd` (build `deploy/e2e/ed2kd/Dockerfile`, réseau `e2e`, expose 4661).
- `amuled-seeder` (image `ngosang/amule:3.0.0-1`, bind-mount du `planted.mp4` en shared, conf
  pointant `ed2kd:4661`, réseau `e2e`, port eD2k 4662 joignable conteneur-à-conteneur).
- `amuled` (leecher du crawler) + `crawler` + `verifier` réutilisés (réseaux : le crawler garde
  `verify-internal` interne pour le verifier ; ajoute `e2e` pour atteindre son amuled/ed2kd).
- `portforward-stub` (`GET /v1/portforward`) optionnel pour le sous-test port-sync (§5.5).
- Volumes nommés RÉELS (`quarantine`/`catalog-db`/`local-db`) — comme le smoke — pour exercer le
  vrai chemin de persistance non-root 999 (le défaut perms attrapé par le smoke,
  `test_compose_smoke.py:13-21`).
- Configs e2e dédiées sous `deploy/e2e/` (`local.e2e.yaml` mode full ; `matcher.yaml`/`targets.yaml`
  câblés sur le nom/cible planté, §4.3 ; `crawler.yaml` à cadences courtes).

Le **harness de test** (un nouveau `tests/integration/test_e2e.py`) réutilise les helpers du smoke
(`_run`/`_down`/`_wait_state`, `test_compose_smoke.py:89-154`) — patron `subprocess docker compose
-p <projet-isolé> -f compose.yaml -f compose.e2e.yaml …`, tear-down `down -v` en `finally`, projet à
préfixe unique (`uuid`) pour ne jamais toucher une stack de l'hôte.

---

## 7. Reproductibilité contributeur

L'objectif « un contributeur externe reproduit avec juste Docker » impose :

- **Tout est vendoré ou buildé** : pas d'image eD2k de registre tierce non maintenue (aucune
  n'existe, plan maître §3.5) → `ed2kd` est buildé depuis `vendor/ed2kd` (commit pinné `f6c330da`),
  amuled est `ngosang/amule:3.0.0-1` (pin strict, jamais `latest`, plan maître §3.1).
- **Le fichier planté est commité** (binaire `planted.mp4`) + son script de génération + le hash
  ed2k figé en constante → aucun ffmpeg requis chez le contributeur (le binaire suffit ; ffmpeg
  n'est nécessaire que pour *re-générer*).
- **Une seule commande** : `( cd packages/crawler && uv run pytest -m e2e_integration --no-cov )`
  (Docker + compose v2 prérequis, documentés). Le test build les images, monte la stack, asserte,
  tear-down.
- **Pas de secret** : aucune clé WireGuard, aucun compte VPN, aucune capacité privilégiée. Le stub
  `/v1/portforward` remplace gluetun ; ed2kd remplace un serveur réel. Réseaux Docker isolés.
- Documenté dans le runbook / README de test (delta à proposer à l'orchestrateur ; ne pas éditer les
  docs transverses soi-même, plan maître §4).

---

## 8. Risques / à confirmer (inconnus résiduels du spike)

1. **Encodage de l'arbre de recherche** (couche A) : l'octet-pour-octet exact qu'**amuled 3.0.0**
   émet en `SEARCHREQUEST 0x16` vers le stub, suite à un EC `start_search` du crawler
   (`adapters/mule_ec/client.py:106-117`). Le stub ne parse que la feuille nom `SO_STRING_TERM 0x01`
   (`server.c:289-296`) et **ignore** la structure — mais il faut confirmer que (a) le terme nom est
   bien présent et trouvable par scan, (b) aMule n'envoie pas le paquet compressé `PROTO_PACKED 0xD4`
   (sinon le stub doit dézipper, `server.c:459-471`). **Validation : Geoffrey** (couche A réelle,
   capture du paquet).
2. **Build ed2kd sur toolchain 2026** : les 3 ajustements cmake (`-DCMAKE_POLICY_VERSION_MINIMUM=3.5`,
   `Release`, flags neutralisés) suffisent-ils ? `-fopenmp` au link (`CMakeLists.txt:68`), `find_package
   (Libconfig 1.4.8)` (l.22), `gnu11` (l.72), `malloc.h`/`alloca.h` (`packet.c:5-6`) — risques de
   warnings-as-errors ou d'incompat libevent 2.1 (commentaire `server.c:25` « after moving to libevent
   2.1.x »). **Validation : Geoffrey** (build réel de l'image).
3. **Flag d'invocation d'ed2kd** : `ENTRYPOINT ["ed2kd", "-c", "/etc/ed2kd.conf"]` suppose un parseur
   `-c <conf>`. À confirmer depuis `vendor/ed2kd/src/main.c` (chemin de conf par défaut `CFG_DEFAULT_PATH`,
   `config.c:38-40`). **À lire/confirmer par l'implémenteur.**
4. **Joignabilité HighID dans la topo Docker** (couche B) : que le port-check entrant ed2kd→amuled
   réussisse réellement sur un bridge Docker (IP conteneur joignable, port eD2k 4662 ouvert,
   `client.c:147-150` ; `allow_lowid=1` comme filet). Si le port-check échoue → LowID → la source
   n'est pas contactable → pas de download → l'e2e échoue. **Validation : Geoffrey.**
5. **Disponibilité MD4** pour calculer le hash ed2k (§4.2) : OpenSSL 3 retire MD4 → impl pure ou
   `pycryptodome` (dép de test, déclarée). Et **déterminisme ffmpeg** : on commite le binaire +
   calcule le hash depuis lui (pas depuis une re-génération) pour éviter la dérive d'octets entre
   versions d'ffmpeg.
6. **Chemin staging réel d'amuled** (DV10, §5.4) : nom/emplacement exact du répertoire où amuled
   3.0.0 (image ngosang) dépose un fichier **fini**, monté de façon à coïncider avec `staging_dir =
   /data/quarantine` côté crawler, et garantie que `os.replace` reste intra-FS (même volume nommé).
   **Validation : Geoffrey** (c'est précisément le chemin jamais testé qu'on dérisque).
7. **Endianness de l'IDCHANGE HighID** (§3.3) : reproduire l'ordre d'octets d'ed2kd (IP en ordre
   réseau écrite telle quelle dans le champ u32, `packet.c:21` ; pas de `htonl` à l'émission) pour
   qu'aMule interprète HighID. À valider par capture (le stub émet, aMule accepte un HighID).

---

## 9. Part Geoffrey (réseau vivant — ce que le sandbox ne peut pas)

L'agent WT-e2e **écrit tout** et prouve **Auto** le vert sur :
- le **stub Python pur** (codec + dispatch sockets-mémoire) à **100 % branch** sous le run normal ;
- mypy strict src+tests, ruff (ligne 100), pas de SQL embarqué nouveau.

**Geoffrey lance** (Docker + réseau, mémoire « integration-tests-need-real-shell ») :
- `( cd packages/crawler && uv run pytest -m e2e_integration --no-cov )` complet : build ed2kd +
  amuled seeder/leecher + crawler + verifier ; **download→verify réel** ; `resolve_staging_path`
  exercé ; verdict `clean`.
- La validation **HighID** (port-check ed2kd) et le **sous-test port-sync** (stub `/v1/portforward`
  → SetPort+restart → HighID observé) une fois WT-portsync intégré.
- Remontée des inconnus §8 (encodage search, build ed2kd, flag d'invocation, staging amuled) →
  l'orchestrateur fige les réponses dans ce doc / le handoff.

L'agent **ne touche pas** : `compose.yaml`/`compose.smoke.yaml`/`.env.example` (intégration-owned,
plan maître §4), `uv.lock`/`pyproject` (dép MD4/pycryptodome **déclarée**, lockée par
l'orchestrateur), `composition/app.py` (sauf si son brief l'en désigne propriétaire — ici non : il
**consomme** l'app, ne la recâble pas). Il **crée seul** : `compose.e2e.yaml`, `deploy/e2e/*`, le stub
(`tests/e2e/` ou `tools/`), `tests/integration/test_e2e.py`, les fixtures (binaire planté + script +
constante de hash).
