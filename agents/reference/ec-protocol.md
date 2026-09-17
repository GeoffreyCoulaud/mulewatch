# Protocole EC (External Connections) d'aMule — référence pour le client Python

Référence vérifiée **sur les sources** : `amule-project/amule` au tag **`2.3.3`** (sauf mention
contraire) et `amule-org/amule` au tag **`3.0.0`** (la version compilée par l'image Docker
`ngosang/docker-amule`). La version de protocole est identique dans les deux
(`EC_CURRENT_PROTOCOL_VERSION = 0x0204`) ; les ajouts de 3.0.0 sont des capacités optionnelles
qu'un client 2.3.3 ignore sans danger. Quand le vieux document `docs/EC_Protocol.md` du dépôt
contredit le code, **le code gagne** (voir §9).

Connexion : **TCP**, port par défaut **4712** (`ECPort` dans `amule.conf`).

---

## 1. Trame au niveau du fil

Sources : `src/libs/ec/cpp/ECSocket.cpp` (fonctions `ReadHeader`, `WritePacket`, `ReadPacket`,
`ReadNumber`, `WriteNumber`, `ReadBuffer`, `WriteBuffer`), `ECSocket.h`, `ECCodes.h` (tag 2.3.3).

Chaque paquet EC est précédé d'un en-tête **fixe de 8 octets** (`EC_HEADER_SIZE = 8`,
`ECSocket.h:72`) :

```
uint32  flags        (big-endian / network byte order)
uint32  length       (big-endian) — longueur du payload APRÈS l'en-tête,
                      telle que transmise (donc taille COMPRESSÉE si zlib)
```

Tout le reste du protocole est aussi en **big-endian** (network byte order).

### Le champ flags

`ECCodes.h` 2.3.3, lignes 39-43 :

```c
enum ECFlags {
	EC_FLAG_ZLIB	 = 0x00000001,
	EC_FLAG_UTF8_NUMBERS = 0x00000002,
	EC_FLAG_UNKNOWN_MASK = 0xff7f7f08
};
```

3.0.0/master ajoute `EC_FLAG_LARGE_TAG_COUNT = 0x00000010` (négocié par tag `CAN`, jamais émis
sinon). Le bit de base **0x20 est toujours présent** (`m_my_flags(0x20)`, `ECSocket.cpp:275`).
À la lecture, `ReadPacket` (`ECSocket.cpp:814`) valide :

```cpp
if ( ((flags & 0x60) != 0x20) || (flags & EC_FLAG_UNKNOWN_MASK) ) { ... CloseSocket(); }
```

donc : bit 0x20 obligatoire, bit 0x40 interdit, et tout bit du masque `0xff7f7f08` ferme la
connexion. **Il n'existe ni champ « accepts » séparé, ni bit « has-ID », ni encodage LSB-first
à octets omis dans le protocole 0x0204** — c'est un vestige du vieux `docs/EC_Protocol.md` (§9).
Les flags sont choisis **par paquet** ; le récepteur doit relire les flags de chaque paquet.

Logique d'émission (`WritePacket`, `ECSocket.cpp:743-753`) :

```cpp
uint32_t flags = 0x20;
if (packet->GetPacketLength() > EC_MAX_UNCOMPRESSED   // = 1024 (ECSocket.cpp:40)
	&& ((m_my_flags & EC_FLAG_ZLIB) > 0)) {
	flags |= EC_FLAG_ZLIB;
} else {
	flags |= EC_FLAG_UTF8_NUMBERS;
}
flags &= m_my_flags;
```

`m_my_flags` ne contient `EC_FLAG_ZLIB`/`EC_FLAG_UTF8_NUMBERS` que si la capacité a été annoncée
au handshake (tags `EC_TAG_CAN_ZLIB`/`EC_TAG_CAN_UTF8_NUMBERS`, voir §4 ; côté serveur :
`src/ExternalConn.cpp:472-477`). **Un client qui n'annonce aucune capacité reçoit donc toujours
des paquets `flags = 0x20`** : ni zlib, ni nombres UTF-8 — c'est la stratégie la plus simple.

### EC_FLAG_UTF8_NUMBERS (0x02)

Quand ce flag est posé sur un paquet, **tous les nombres de la couche structure** — opcode,
TAGNAME, TAGTYPE, TAGLEN, TAGCOUNT — passent par `WriteNumber`/`ReadNumber`
(`ECSocket.cpp:575-629`) qui encode la valeur comme un **point de code UTF-8 standard**
(tables importées de `linux/fs/nls_base.c`, séquences de 1 à 6 octets couvrant jusqu'à 31 bits) :

- valeur ≤ 0x7F → 1 octet tel quel ;
- sinon, séquence UTF-8 classique : `0xC0|…` + octets de continuation `0x80|…` (6 bits chacun).

Ne sont **pas** affectés : l'en-tête de 8 octets (toujours brut), et les **valeurs des tags**
(écrites via `WriteBuffer`, donc brutes — un `EC_TAGTYPE_UINT32` reste 4 octets big-endian).

### EC_FLAG_ZLIB (0x01)

Le **payload entier** (opcode + tagcount + tags) est compressé en **un seul flux zlib**
(RFC 1950 : `deflateInit` + `Z_FINISH` dans `FlushBuffers`, `ECSocket.cpp:755-767, 703-724`).
L'en-tête de 8 octets reste en clair ; `length` est la taille compressée. Le serveur ne
compresse que les paquets dont le payload non compressé dépasse `EC_MAX_UNCOMPRESSED = 1024`
octets, et seulement si le client a annoncé `EC_TAG_CAN_ZLIB`.

### Taille maximale

`ReadHeader` (`ECSocket.cpp:540`) rejette tout paquet annonçant plus de **16 MiB**
(`16*1024*1024`) et ferme la connexion.

---

## 2. Encodage d'un tag

Source : `src/libs/ec/cpp/ECTag.cpp` (tag 2.3.3), fonctions `WriteTag` (l. 452-474),
`ReadFromSocket` (l. 415-449), `GetTagLen` (l. 553-561), `WriteChildren`/`ReadChildren`.

```
uint16  TAGNAME   = (nom_logique << 1) | (1 si sous-tags, 0 sinon)
uint8   TAGTYPE
uint32  TAGLEN
[uint16 TAGCOUNT  — présent UNIQUEMENT si le bit 0 de TAGNAME est à 1]
[sous-tags sérialisés, récursivement]
[octets de la valeur propre du tag]
```

Citations exactes :

```cpp
// WriteTag, ECTag.cpp:454
ec_tagname_t tmp_tagName = (m_tagName << 1) | (m_tagList.empty() ? 0 : 1);
// ReadFromSocket, ECTag.cpp:421-422
m_tagName = tmp_tagName >> 1;
bool hasChildren = (tmp_tagName & 0x01) != 0;
```

Les constantes de `ECCodes.h` sont les noms **logiques** : sur le fil on transmet `nom*2` (ou
`nom*2+1`). Les sous-tags sont écrits **avant** la valeur propre.

### TAGLEN — le piège classique, tranché par `GetTagLen` (ECTag.cpp:553-561)

```cpp
uint32 length = m_dataLen;
for (const_iterator it = begin(); it != end(); ++it) {
	length += it->GetTagLen();
	length += sizeof(ec_tagname_t) + sizeof(ec_tagtype_t) + sizeof(ec_taglen_t)
	          + (it->HasChildTags() ? 2 : 0);
}
```

`TAGLEN` = **longueur de la valeur propre + taille sérialisée complète de chaque enfant**
(son TAGLEN + son en-tête de 7 octets + ses 2 octets de TAGCOUNT s'il a lui-même des enfants).
Il **exclut** l'en-tête du tag lui-même (7 octets) et son propre champ TAGCOUNT. À la lecture,
la longueur de la valeur propre se déduit : `m_dataLen = TAGLEN - Σ(taille des enfants)`
(`ECTag.cpp:436-438`).

### Tag vide

`CECEmptyTag` (`ECTag.h:248-251`) → `CECTag(name, 0, NULL)` → `TAGTYPE = EC_TAGTYPE_CUSTOM (1)`,
`TAGLEN = 0`, aucune donnée (`ECTag.cpp:67-79`). C'est la forme des tags `EC_TAG_CAN_*`.

### Le paquet est un pseudo-tag

`CECPacket` hérite de `CECEmptyTag` ; sur le fil (`ECPacket.cpp:34-46`) :

```
uint8   OPCODE      (via WriteNumber → sujet à UTF8_NUMBERS)
uint16  TAGCOUNT    (nombre de tags de premier niveau)
        <tags>
```

Pas de TAGNAME/TAGTYPE/TAGLEN au niveau paquet. `EC_TAG_DETAIL_LEVEL` n'est ajouté par le
constructeur que si le niveau diffère de `EC_DETAIL_FULL` (`ECPacket.h:42-49`).

---

## 3. Types de valeurs

Source : `src/libs/ec/cpp/ECTagTypes.h` (2.3.3, identique en 3.0.0) ; représentations :
`ECTag.cpp` (constructeurs, l. 67-262, `ConstructStringTag` l. 680-687).

| Nom | Hex | Représentation sur le fil |
|---|---|---|
| `EC_TAGTYPE_UNKNOWN` | 0x00 | jamais émis (`EC_ASSERT` dans `WriteTag`) |
| `EC_TAGTYPE_CUSTOM` | 0x01 | octets opaques ; aussi le type des tags vides |
| `EC_TAGTYPE_UINT8` | 0x02 | 1 octet |
| `EC_TAGTYPE_UINT16` | 0x03 | 2 octets big-endian |
| `EC_TAGTYPE_UINT32` | 0x04 | 4 octets big-endian |
| `EC_TAGTYPE_UINT64` | 0x05 | 8 octets big-endian |
| `EC_TAGTYPE_STRING` | 0x06 | **UTF-8 + octet NUL final, inclus dans TAGLEN** |
| `EC_TAGTYPE_DOUBLE` | 0x07 | représentation **texte** (`ostringstream`, point décimal `.`) + NUL |
| `EC_TAGTYPE_IPV4` | 0x08 | 6 octets : 4 octets d'IP + port uint16 big-endian (`ECTag.cpp:108-116`) |
| `EC_TAGTYPE_HASH16` | 0x09 | 16 octets bruts, MSB first (MD4/MD5) |
| `EC_TAGTYPE_UINT128` | 0x0A | 16 octets big-endian (ID Kad) |

**Les entiers sont toujours encodés au plus court** (`InitInt`, `ECTag.cpp:207-221`) : une
valeur ≤ 0xFF part en UINT8, ≤ 0xFFFF en UINT16, etc., quel que soit le type C++ d'origine.
Un client doit donc lire « un entier » en acceptant les 4 largeurs (équivalent de `GetInt()`).
Booléens : à l'écriture, uint8 0/1 ; à la lecture, l'**absence** du tag vaut `false`
(`docs/EC_Protocol.md` §2 + usage `AssignIfExist`).

---

## 4. Handshake d'authentification (côté client)

Sources : `src/libs/ec/cpp/RemoteConnect.cpp` 2.3.3 (l. 40-70 `CECLoginPacket`, l. 62-68
`CECAuthPacket`, l. 243-280 `ProcessAuthPacket`) ; croisé avec le serveur
`src/ExternalConn.cpp` 2.3.3 (`CECServerSocket::Authenticate`, l. 423-547).

Séquence (le client parle en premier, sinon le serveur coupe) :

```
client → EC_OP_AUTH_REQ (0x02)
           EC_TAG_CLIENT_NAME       (0x0100, STRING)
           EC_TAG_CLIENT_VERSION    (0x0101, STRING)
           EC_TAG_PROTOCOL_VERSION  (0x0002, entier = 0x0204 → émis en UINT16)
           [EC_TAG_CAN_ZLIB         (0x000C, tag vide) — optionnel]
           [EC_TAG_CAN_UTF8_NUMBERS (0x000D, tag vide) — optionnel]
           [EC_TAG_CAN_NOTIFY       (0x000E, tag vide) — optionnel]
           (EC_TAG_VERSION_ID 0x0003 : UNIQUEMENT builds SVN ; interdit face à une release)
serveur → EC_OP_AUTH_SALT (0x4F)
           EC_TAG_PASSWD_SALT (0x000B) = uint64 aléatoire (ATTENTION : encodage entier
           minimal — peut arriver en UINT8/16/32/64 ; lire générique)
client → EC_OP_AUTH_PASSWD (0x50)
           EC_TAG_PASSWD_HASH (0x0001, HASH16) = 16 octets du hash salé (ci-dessous)
serveur → EC_OP_AUTH_OK (0x04)  avec EC_TAG_SERVER_VERSION (0x050B, STRING, ex. "3.0.0")
       ou EC_OP_AUTH_FAIL (0x03) avec EC_TAG_STRING (0x0000) = raison ; connexion fermée.
```

### Calcul exact du hash

Code client (`RemoteConnect.cpp:252-253`, identique au serveur `ExternalConn.cpp:503-506`) :

```cpp
wxString saltHash = MD5Sum(CFormat(wxT("%lX")) % passwordSalt->GetInt()).GetHash();
m_connectionPassword = MD5Sum(m_connectionPassword.Lower() + saltHash).GetHash();
```

où `m_connectionPassword` est **déjà** le MD5 hexadécimal du mot de passe (amulecmd fait
`m_password.Decode(MD5Sum(pass_plain).GetHash())`, `src/ExternalConnector.cpp:553`). Donc :

```
hash_envoyé = MD5( lower(md5_hex(password)) + md5_hex(format("%X", salt)) )
```

Précisions de formatage, toutes vérifiées :

- `MD5Sum::GetHash()` produit de l'hex **minuscule** (`%02x`, `src/libs/common/MD5Sum.cpp:80`).
- Le sel uint64 est converti en chaîne par `CFormat("%lX")` : hex **MAJUSCULE**, **sans** `0x`,
  **sans zéros de tête** (sémantique printf ; sel 0 → `"0"`). C'est cette chaîne ASCII qui est
  passée à MD5.
- `.Lower()` : le md5-hex du mot de passe est forcé en minuscule avant concaténation (le sel
  haché `saltHash` sort déjà en minuscule de `GetHash()`).
- La concaténation (64 caractères ASCII) est hachée en MD5 ; les 16 octets bruts du résultat
  partent dans `EC_TAG_PASSWD_HASH` (type `EC_TAGTYPE_HASH16`).

En Python :

```python
salt_str = format(salt, "X")                      # %lX : majuscules, pas de padding
salt_hash = hashlib.md5(salt_str.encode("ascii")).hexdigest()          # minuscules
passwd_md5 = hashlib.md5(password.encode("utf-8")).hexdigest()         # minuscules
ec_hash = hashlib.md5((passwd_md5 + salt_hash).encode("ascii")).digest()  # 16 octets
```

### Échecs et cas particuliers (serveur, `ExternalConn.cpp:423-547`)

- Version de protocole vérifiée par **égalité stricte** avec 0x0204 ; sinon `EC_OP_AUTH_FAIL`
  + `EC_TAG_STRING` « Invalid protocol version. ( %#.4x != %#.4x ) » (l. 463-487).
- Tag de version manquant → « Missing protocol version tag. » ; `EC_TAG_VERSION_ID` présent
  face à une release → refus (l. 459-461).
- `ECPassword` vide côté serveur → refus immédiat (l. 432-436) ; côté client, mot de passe
  vide ou égal à `d41d8cd98f00b204e9800998ecf8427e` (MD5 de "") refusé avant connexion
  (`RemoteConnect.cpp:117` en 2.3.3).
- Après `EC_OP_AUTH_FAIL` le serveur loggue « Unauthorized access attempt from %s. Connection
  closed. » et ferme (l. 536-543).
- En 3.0.0, `EC_OP_AUTH_OK` peut aussi contenir les tags vides `EC_TAG_CAN_LARGE_TAG_COUNT`
  (0x0011) et `EC_TAG_CAN_PARTIAL_UPDATE` (0x0012) en écho des capacités
  (`amule-org/amule@3.0.0 src/ExternalConn.cpp:639-654`) — un client qui ne les a pas
  annoncées peut les ignorer.

Les requêtes sont servies en **FCFS strict, une réponse par requête** (commentaire
`RemoteConnect.cpp:298-301`) ; `EC_OP_NOOP` (0x01) sert de keepalive.

---

## 5. Recherche

Sources : `src/ExternalConn.cpp` 2.3.3 (l. 1017-1129, 1589-1615), `src/libs/ec/cpp/ECSpecialTags.cpp`
(l. 65-86, `CEC_Search_Tag`), `src/ECSpecialCoreTags.cpp` (l. 353-372, `CEC_SearchFile_Tag`),
`src/TextClient.cpp` (l. 507-560, 852-875), `src/SearchList.cpp` (l. 412-435).

### Lancer : `EC_OP_SEARCH_START` (0x26)

Un seul tag de premier niveau, dont la **valeur propre est le type de recherche** :

```
EC_TAG_SEARCH_TYPE (0x0701, entier) = EC_SEARCH_LOCAL 0x00 | EC_SEARCH_GLOBAL 0x01
                                      | EC_SEARCH_KAD 0x02 | (EC_SEARCH_WEB 0x03 → refusé)
  ├─ EC_TAG_SEARCH_NAME         (0x0702, STRING)  — la requête          [obligatoire]
  ├─ EC_TAG_SEARCH_FILE_TYPE    (0x0705, STRING)  — "" = tous ; sinon chaîne eD2k :
  │     "Audio", "Video", "Image", "Doc", "Pro", "Arc", "Iso"
  │     (src/include/tags/FileTags.h:122-128)                            [obligatoire, peut être ""]
  ├─ EC_TAG_SEARCH_EXTENSION    (0x0706, STRING)  [omis si vide]
  ├─ EC_TAG_SEARCH_AVAILABILITY (0x0707, entier)  [omis si 0]
  ├─ EC_TAG_SEARCH_MIN_SIZE     (0x0703, entier, octets)  [omis si 0]
  └─ EC_TAG_SEARCH_MAX_SIZE     (0x0704, entier, octets)  [omis si 0]
```

Réponse : `EC_OP_STRINGS` (0x06) + `EC_TAG_STRING` « Search in progress. Refetch results in a
moment! » en cas de succès, sinon `EC_OP_FAILED` (0x05) + `EC_TAG_STRING` (erreur, ex. Kad
arrêté). **Lancer une recherche efface les résultats de la précédente**
(`theApp->searchlist->RemoveResults`, `ExternalConn.cpp:1085`).

### Progression : `EC_OP_SEARCH_PROGRESS` (0x29)

Réponse de même opcode avec `EC_TAG_SEARCH_STATUS` (0x0708, entier) — sémantique
(`SearchList.cpp:412-435`) :

- recherche **globale** : pourcentage 0-100 ;
- recherche **locale** : `0xffff` immédiatement (pas de mesure) ;
- recherche **Kad** : `0` tant que ça tourne, **`0xfffe`** quand c'est fini.

amulecmd n'affiche le pourcentage que si `valeur <= 100` (`TextClient.cpp:865-873`).

### Résultats : `EC_OP_SEARCH_RESULTS` (0x28)

Requête : `CECPacket(EC_OP_SEARCH_RESULTS, EC_DETAIL_FULL)` (le niveau FULL étant le défaut,
aucun tag n'est émis). La requête peut contenir des tags `EC_TAG_SEARCHFILE` pour filtrer par
ECID (`CTagSet`, `ExternalConn.cpp:1024`). Réponse : même opcode, contenant N tags :

```
EC_TAG_SEARCHFILE (0x0700, entier) = ECID du résultat (identifiant de session, valeur propre)
  ├─ EC_TAG_PARTFILE_SOURCE_COUNT      (0x030A, entier) — sources
  ├─ EC_TAG_PARTFILE_SOURCE_COUNT_XFER (0x030D, entier) — **sources complètes** (!)
  ├─ EC_TAG_PARTFILE_STATUS            (0x0308, entier) — statut download ; 0 = nouveau
  ├─ EC_TAG_PARTFILE_NAME              (0x0301, STRING) — nom de fichier observé
  ├─ EC_TAG_PARTFILE_SIZE_FULL         (0x0303, entier) — taille en octets
  ├─ EC_TAG_PARTFILE_HASH              (0x031E, HASH16) — hash eD2k (MD4)
  ├─ [EC_TAG_SEARCH_PARENT             (0x0709, entier) — ECID du parent, si variante groupée]
  └─ [EC_TAG_KNOWNFILE_RATING          (0x040F, UINT8)  — 3.0.0 uniquement, si noté]
```

(`CEC_SearchFile_Tag`, `src/ECSpecialCoreTags.cpp:353-372` en 2.3.3 ; version 3.0.0 avec le
rating : `amule-org/amule@3.0.0`, même fichier.) En `EC_DETAIL_UPDATE`, seuls les trois
premiers tags sont émis.

**C'est la liste exhaustive des métadonnées qu'EC expose sur un résultat de recherche** : nom,
taille, hash MD4, compte de sources, sources complètes, statut, parent, (rating). Aucun tag
média (codec, durée, bitrate) ne transite par EC — la corrélation de métadonnées riches devra
venir d'ailleurs.

### Télécharger un résultat : `EC_OP_DOWNLOAD_SEARCH_RESULT` (0x2A)

Un tag par fichier ; le serveur lit le hash dans la valeur du tag et la catégorie dans son
premier enfant (`ExternalConn.cpp:1061-1071`). amulecmd émet (`TextClient.cpp:556-561`) :

```
EC_TAG_PARTFILE (0x0300, HASH16 = hash du fichier)
  └─ EC_TAG_PARTFILE_CAT (0x030F, entier = catégorie, 0 par défaut)
```

Réponse : `EC_OP_STRINGS`.

### Arrêter : `EC_OP_SEARCH_STOP` (0x27) → réponse `EC_OP_MISC_DATA` (0x07).

---

## 6. Statut réseau et statistiques

Sources : `src/ExternalConn.cpp` 2.3.3 (l. 1393-1401, 567-600), `src/ECSpecialCoreTags.cpp`
(l. 124-156, `CEC_ConnState_Tag`), `src/libs/ec/cpp/ECSpecialTags.h` (l. 198-212),
`src/NetworkFunctions.h:123`.

### `EC_OP_GET_CONNSTATE` (0x0B) → réponse `EC_OP_MISC_DATA` (0x07)

Joindre `EC_TAG_DETAIL_LEVEL (0x0004) = EC_DETAIL_CMD (0x00)` pour obtenir le tag serveur
complet. La réponse contient :

```
EC_TAG_CONNSTATE (0x0005, UINT8 = bitfield)
  bit 0x01 : connecté eD2k          bit 0x02 : connexion eD2k en cours
  bit 0x04 : connecté Kad           bit 0x08 : Kad firewalled
  bit 0x10 : Kad lancé
  ├─ [si connecté eD2k]
  │    EC_TAG_SERVER (0x0500, IPV4 = ip:port du serveur)   ← détail CMD/FULL
  │      └─ EC_TAG_SERVER_NAME (0x0501, STRING) [+ DESC/PING/USERS/FILES... si non nuls]
  │    EC_TAG_ED2K_ID (0x0006, entier)  — l'ID eD2k ; LowID si < 16777216
  │                                       (HIGHEST_LOWID_ED2K_KAD, NetworkFunctions.h:123)
  ├─ [si connexion en cours] EC_TAG_ED2K_ID = 0xffffffff
  ├─ EC_TAG_CLIENT_ID (0x000A, entier) — ID client (toujours présent)
  └─ [si Kad lancé] EC_TAG_KAD_ID (0x0010, UINT128)
```

État Kad : `0x10` seul = Kad tourne sans être connecté ; `0x10|0x04` = connecté OK ;
`0x10|0x04|0x08` = connecté mais firewalled ; ni `0x10` → Kad arrêté.

### `EC_OP_STAT_REQ` (0x0A) → réponse `EC_OP_STATS` (0x0C)

Avec `EC_DETAIL_CMD`, la réponse contient (`Get_EC_Response_StatRequest`,
`ExternalConn.cpp:567-600`) : `EC_TAG_STATS_UL_SPEED` 0x0200, `EC_TAG_STATS_DL_SPEED` 0x0201,
`EC_TAG_STATS_UL_SPEED_LIMIT` 0x0202, `EC_TAG_STATS_DL_SPEED_LIMIT` 0x0203,
`EC_TAG_STATS_UL_QUEUE_LEN` 0x0208, `EC_TAG_STATS_TOTAL_SRC_COUNT` 0x0206, les
utilisateurs/fichiers eD2k+Kad (`EC_TAG_STATS_ED2K_USERS` 0x0209, `EC_TAG_STATS_KAD_USERS`
0x020A, `EC_TAG_STATS_ED2K_FILES` 0x020B, `EC_TAG_STATS_KAD_FILES` 0x020C), et — appendu par le
dispatcher (l. 1394-1396) — un `EC_TAG_CONNSTATE` complet comme ci-dessus. Au niveau
`EC_DETAIL_FULL` s'ajoutent overheads, octets totaux, `EC_TAG_STATS_SHARED_FILE_COUNT` 0x021A, etc.

---

## 7. Table récapitulative des constantes (base de `codes.py`)

Source unique : `src/libs/ec/cpp/ECCodes.h` + `ECTagTypes.h` (tag 2.3.3 ; ✦ = ajout 3.0.0).

| Constante | Valeur | | Constante | Valeur |
|---|---|---|---|---|
| `EC_CURRENT_PROTOCOL_VERSION` | `0x0204` | | `EC_TAG_PASSWD_HASH` | `0x0001` |
| `EC_FLAG_ZLIB` | `0x00000001` | | `EC_TAG_PROTOCOL_VERSION` | `0x0002` |
| `EC_FLAG_UTF8_NUMBERS` | `0x00000002` | | `EC_TAG_PASSWD_SALT` | `0x000B` |
| `EC_FLAG_LARGE_TAG_COUNT` ✦ | `0x00000010` | | `EC_TAG_CAN_ZLIB` | `0x000C` |
| `EC_FLAG_UNKNOWN_MASK` | `0xff7f7f08` | | `EC_TAG_CAN_UTF8_NUMBERS` | `0x000D` |
| flag de base (toujours) | `0x20` | | `EC_TAG_CAN_NOTIFY` | `0x000E` |
| `EC_OP_NOOP` | `0x01` | | `EC_TAG_STRING` | `0x0000` |
| `EC_OP_AUTH_REQ` | `0x02` | | `EC_TAG_DETAIL_LEVEL` | `0x0004` |
| `EC_OP_AUTH_FAIL` | `0x03` | | `EC_TAG_CONNSTATE` | `0x0005` |
| `EC_OP_AUTH_OK` | `0x04` | | `EC_TAG_ED2K_ID` | `0x0006` |
| `EC_OP_FAILED` | `0x05` | | `EC_TAG_CLIENT_ID` | `0x000A` |
| `EC_OP_STRINGS` | `0x06` | | `EC_TAG_KAD_ID` | `0x0010` |
| `EC_OP_MISC_DATA` | `0x07` | | `EC_TAG_CLIENT_NAME` | `0x0100` |
| `EC_OP_STAT_REQ` | `0x0A` | | `EC_TAG_CLIENT_VERSION` | `0x0101` |
| `EC_OP_GET_CONNSTATE` | `0x0B` | | `EC_TAG_SERVER` | `0x0500` |
| `EC_OP_STATS` | `0x0C` | | `EC_TAG_SERVER_NAME` | `0x0501` |
| `EC_OP_SEARCH_START` | `0x26` | | `EC_TAG_SERVER_VERSION` | `0x050B` |
| `EC_OP_SEARCH_STOP` | `0x27` | | `EC_TAG_SEARCHFILE` | `0x0700` |
| `EC_OP_SEARCH_RESULTS` | `0x28` | | `EC_TAG_SEARCH_TYPE` | `0x0701` |
| `EC_OP_SEARCH_PROGRESS` | `0x29` | | `EC_TAG_SEARCH_NAME` | `0x0702` |
| `EC_OP_DOWNLOAD_SEARCH_RESULT` | `0x2A` | | `EC_TAG_SEARCH_MIN_SIZE` | `0x0703` |
| `EC_OP_KAD_START` | `0x48` | | `EC_TAG_SEARCH_MAX_SIZE` | `0x0704` |
| `EC_OP_KAD_STOP` | `0x49` | | `EC_TAG_SEARCH_FILE_TYPE` | `0x0705` |
| `EC_OP_SERVER_CONNECT` | `0x2F` | | `EC_TAG_SEARCH_EXTENSION` | `0x0706` |
| `EC_OP_SERVER_DISCONNECT` | `0x2E` | | `EC_TAG_SEARCH_AVAILABILITY` | `0x0707` |
| `EC_OP_AUTH_SALT` | `0x4F` | | `EC_TAG_SEARCH_STATUS` | `0x0708` |
| `EC_OP_AUTH_PASSWD` | `0x50` | | `EC_TAG_SEARCH_PARENT` | `0x0709` |
| `EC_DETAIL_CMD` | `0x00` | | `EC_TAG_PARTFILE` | `0x0300` |
| `EC_DETAIL_WEB` | `0x01` | | `EC_TAG_PARTFILE_NAME` | `0x0301` |
| `EC_DETAIL_FULL` | `0x02` | | `EC_TAG_PARTFILE_SIZE_FULL` | `0x0303` |
| `EC_DETAIL_UPDATE` | `0x03` | | `EC_TAG_PARTFILE_STATUS` | `0x0308` |
| `EC_DETAIL_INC_UPDATE` | `0x04` | | `EC_TAG_PARTFILE_SOURCE_COUNT` | `0x030A` |
| `EC_SEARCH_LOCAL` | `0x00` | | `EC_TAG_PARTFILE_SOURCE_COUNT_XFER` | `0x030D` |
| `EC_SEARCH_GLOBAL` | `0x01` | | `EC_TAG_PARTFILE_CAT` | `0x030F` |
| `EC_SEARCH_KAD` | `0x02` | | `EC_TAG_PARTFILE_HASH` | `0x031E` |
| `EC_SEARCH_WEB` | `0x03` | | `EC_TAG_KNOWNFILE_RATING` | `0x040F` |
| `EC_TAGTYPE_CUSTOM` | `0x01` | | `EC_TAGTYPE_STRING` | `0x06` |
| `EC_TAGTYPE_UINT8/16/32/64` | `0x02-0x05` | | `EC_TAGTYPE_DOUBLE` | `0x07` |
| `EC_TAGTYPE_IPV4` | `0x08` | | `EC_TAGTYPE_HASH16` | `0x09` |
| `EC_TAGTYPE_UINT128` | `0x0A` | | `EC_TAG_CAN_LARGE_TAG_COUNT` ✦ | `0x0011` |
| | | | `EC_TAG_CAN_PARTIAL_UPDATE` ✦ | `0x0012` |

(Divers : `EC_TAG_STATS_UL_SPEED` 0x0200, `..._DL_SPEED` 0x0201, `..._UL_SPEED_LIMIT` 0x0202,
`..._DL_SPEED_LIMIT` 0x0203, `..._UL_QUEUE_LEN` 0x0208, `..._TOTAL_SRC_COUNT` 0x0206,
`..._ED2K_USERS` 0x0209, `..._KAD_USERS` 0x020A, `..._ED2K_FILES` 0x020B,
`..._KAD_FILES` 0x020C.)

---

## 8. Image `ngosang/docker-amule`

Sources : `README.md`, `Dockerfile`, `docker/amule-config.sh` du dépôt `ngosang/docker-amule`
(branche master, consultés le 2026-06-11).

- L'image compile **aMule 3.0.0** depuis `amule-org/amule` (`Dockerfile` : `ARG AMULE_REF=3.0.0`).
  Protocole EC inchangé : `0x0204`.
- **Mot de passe EC** : variable d'environnement **`GUI_PWD`** (en clair). Le script de config
  le hache et l'écrit dans `amule.conf` (`docker/amule-config.sh:70,200-205`) :

  ```sh
  AMULE_GUI_ENCODED_PWD=$(printf "%s" "${AMULE_GUI_PWD}" | md5sum | cut -d ' ' -f 1)
  ...
  [ExternalConnect]
  AcceptExternalConnections=1
  ECPort=4712
  ECPassword=${AMULE_GUI_ENCODED_PWD}
  ```

  Donc `ECPassword` contient le **MD5 hex (minuscule)** du mot de passe — exactement la valeur
  `md5_hex(password)` qui entre dans la formule du §4. `WEBUI_PWD` est distinct (webserver).
  Si `GUI_PWD` n'est pas fourni, un mot de passe aléatoire est généré et **affiché dans les
  logs** (`[INIT] Remote GUI password: ...`).
- **Port EC : 4712** (`-p 4712:4712`, « External connections (amulegui, amulecmd, amuleweb) »).
- **Signal de readiness** dans les logs du démon (émis par `ExternalConn::ExternalConn`,
  `src/ExternalConn.cpp:333`) :

  ```
  *** TCP socket (ECServer) listening on 0.0.0.0:4712
  ```

  Lignes utiles ensuite : « New external connection accepted », « Access granted. »,
  « Unauthorized access attempt from %s. Connection closed. ». Attention : au premier
  démarrage, le scan des dossiers partagés peut retarder la disponibilité (README).

---

## 9. Pièges identifiés

1. **`docs/EC_Protocol.md` du dépôt aMule est périmé sur la couche transport.** Il décrit des
   flags LSB-first à octets omis, un bit « has-ID », un champ « accepts », et ses exemples
   utilisent des valeurs de tags pré-multipliées d'une version antérieure
   (`EC_TAG_CLIENT_NAME (0x06)` alors que `ECCodes.h` dit `0x0100`). Le code (2.3.3 et 3.0.0)
   impose : en-tête fixe de 8 octets, flags uint32 big-endian, pas d'ID de paquet. **Suivre le
   code, pas ce document.**
2. **TAGNAME décalé d'un bit** : les constantes sont logiques, le fil porte `(name << 1) | enfants`.
   Oublier le `>> 1` à la lecture fait « trouver » des tags fantômes.
3. **TAGLEN inclut les enfants avec leurs en-têtes** ; la longueur de la valeur propre n'existe
   pas sur le fil, elle se calcule par soustraction (`ECTag.cpp:436-438`). Et le TAGCOUNT (2
   octets) d'un tag n'est PAS compté dans son propre TAGLEN, mais l'est dans celui de son parent.
4. **Entiers à largeur variable** : tout entier (y compris le **sel d'authentification**) est
   émis au plus court. Lire `EC_TAG_PASSWD_SALT` comme un uint64 fixe casse dès que le sel est
   petit. La valeur logique reste la même : c'est elle (pas sa forme sur le fil) qu'on formate
   en `%lX`.
5. **Formatage du sel** : `"%lX"` = hex majuscule **sans zéros de tête** ; le hash du mot de
   passe, lui, est en hex **minuscule**. Mélanger les casses donne un AUTH_FAIL silencieux
   (« wrong password »).
6. **`EC_FLAG_UTF8_NUMBERS` ne touche que les nombres de structure** (opcode, name, type, len,
   count), jamais les valeurs de tags ni l'en-tête de 8 octets. Un client qui n'annonce pas
   `EC_TAG_CAN_UTF8_NUMBERS` (ni `EC_TAG_CAN_ZLIB`) reçoit toujours `flags = 0x20` et peut
   toujours émettre `0x20` : c'est la voie simple et sûre (`flags &= m_my_flags` côté serveur).
7. **zlib** : négocié uniquement si le client l'annonce ; appliqué seulement aux paquets
   > 1024 octets ; le flux couvre tout le payload et le champ `length` de l'en-tête donne la
   taille **compressée**. Si on annonce `CAN_ZLIB`, il faut savoir inflater — les réponses de
   recherche volumineuses dépasseront vite 1024 octets.
8. **Validation stricte des flags à la lecture** (`(flags & 0x60) != 0x20` ou bit inconnu →
   fermeture immédiate) et **plafond de 16 MiB** par paquet.
9. **Version de protocole : égalité stricte** avec `0x0204` ; et une release refuse tout client
   envoyant `EC_TAG_VERSION_ID` (réservé aux builds SVN/CVS).
10. **Chaînes** : UTF-8 avec NUL final inclus dans la longueur — oublier le NUL à l'émission
    corrompt le cadrage du tag. Les `DOUBLE` sont des chaînes.
11. **Booléens** : à la lecture, absence = false ; à l'écriture, toujours envoyer un uint8.
12. **Sources complètes d'un résultat de recherche** : champ au nom trompeur
    `EC_TAG_PARTFILE_SOURCE_COUNT_XFER` (`CompleteSourceCount`, `ECSpecialTags.h:369`).
13. **Les résultats de recherche sont volatils et écrasés** par chaque `EC_OP_SEARCH_START` ;
    l'`ECID` (valeur du tag `EC_TAG_SEARCHFILE`) n'est qu'un identifiant de session, seul le
    hash MD4 est stable.
14. **FCFS** : le core répond à chaque requête dans l'ordre ; ne pas corréler les réponses par
    contenu mais par ordre d'envoi.
15. **Deux dépôts GitHub** : `amule-project/amule` (historique, tag 2.3.3) et `amule-org/amule`
    (actuel, tag 3.0.0, utilisé par docker-amule). Les fichiers EC de 3.0.0 sont identiques au
    master d'`amule-project` consulté.
