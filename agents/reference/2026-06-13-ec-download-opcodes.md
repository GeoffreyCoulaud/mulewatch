# Opcodes EC download — add_link + file de download (2026-06-13)

> Sonde empirique PRÉCOCE du plan D (download). Valide les MÉCANIQUES EC du download
> (option A) : `add_link` accepté par un `amuled` réel + `download_queue` décodée avec un
> statut lisible. Miroir, côté DOWNLOAD, de `agents/reference/2026-06-11-ec-field-richness.md`
> (côté recherche). Sources : `amule-org/amule@3.0.0` (`ECCodes.h`, `ExternalConn.cpp`),
> confirmé par la suite `download_integration` (testcontainers) et la sonde
> `download_probe`, toutes deux contre `ngosang/amule:3.0.0-1`.

---

## Convention de fiabilité

- **SOURCE** — fait établi par lecture des sources C++ d'aMule 3.0.0.
- **EMPIRIQUE** — confirmé par exécution réelle : suite `download_integration`
  (testcontainers) et/ou sonde `download_probe` contre `ngosang/amule:3.0.0-1`.
- **PENDING** — non encore mesuré (réseau eD2k réel avec sources et complétion effective) ;
  un run homelab complétera cette section.

---

## Verdict en une ligne

**`add_link` est accepté par amuled (réponse `EC_OP_NOOP`) et déclenche bien un
téléchargement côté daemon ; `download_queue` se décode sans erreur ET le partfile ajouté
APPARAÎT dans la file, son hash décodé depuis l'enfant `EC_TAG_PARTFILE_HASH` (0x031E).** Les
mécaniques EC du download (option A) sont validées de bout en bout. La COMPLÉTION réelle
(`size_done == size_full`) et le chemin staging restent PENDING (pas de sources eD2k depuis
un conteneur éphémère ; EC n'expose pas de chemin staging portable — DÉCISION D2).

---

## Opcodes confirmés (SOURCE)

Source : `amule-org/amule@3.0.0`, `src/ECCodes.h` (énumération des opcodes/tags) et
`src/ExternalConn.cpp` (le handler `add_link` lit `tag.GetStringData()` et répond
`EC_OP_NOOP` en succès).

| Symbole                       | Valeur  | Rôle |
|-------------------------------|---------|------|
| `EC_OP_ADD_LINK`              | `0x09`  | requête : ajoute un lien ed2k (un `EC_TAG_STRING` portant le lien) |
| `EC_OP_NOOP`                  | —       | réponse de SUCCÈS à `add_link` (et NON `EC_OP_STRINGS`) |
| `EC_OP_GET_DLOAD_QUEUE`       | `0x0D`  | requête : relève la file de download (au détail `EC_DETAIL_CMD`) |
| `EC_OP_DLOAD_QUEUE`           | `0x1F`  | réponse : N enfants `EC_TAG_PARTFILE` |
| `EC_OP_FAILED`                | —       | échec applicatif propre → `EcFailureError` (porte le message du daemon) |

Layout d'`EC_TAG_PARTFILE` (réponse `EC_OP_DLOAD_QUEUE`) — **vérifié contre un amuled RÉEL** :

| Tag                            | Valeur  | Contenu |
|--------------------------------|---------|---------|
| `EC_TAG_PARTFILE` (valeur PROPRE) | —    | **UINT8** : index/statut interne (ex. `0x0d`), **PAS** le hash — IGNORÉ |
| `EC_TAG_PARTFILE_HASH` (enfant) | `0x031E`| **hash MD4 (`HASH16`, 16 octets) — SEUL identifiant stable** |
| `EC_TAG_PARTFILE_NAME`         | `0x0301`| nom du partfile |
| `EC_TAG_PARTFILE_SIZE_FULL`    | `0x0303`| taille totale (octets) |
| `EC_TAG_PARTFILE_SIZE_DONE`    | `0x0306`| octets transférés |
| `EC_TAG_PARTFILE_STATUS`       | `0x0308`| statut de téléchargement côté daemon |
| `EC_TAG_PARTFILE_ED2K_LINK`    | `0x030E`| lien ed2k reconstitué |

> **CORRECTION (2026-06-13).** La valeur PROPRE de `EC_TAG_PARTFILE` était décrite à tort
> comme le hash. EMPIRIQUEMENT, contre un amuled réel, cette valeur propre est un **UINT8**
> (index/statut interne) ; le hash ed2k est l'enfant dédié `EC_TAG_PARTFILE_HASH` (0x031E,
> HASH16). Trame réelle (nom de tag sur le fil = `nom << 1`, donc `0x031E << 1 = 0x063C`) :
> `… 06 3c 09 00000010 aabbccddeeff00112233445566778899 …` (enfant 0x031E, type HASH16, 16
> octets). La MD4 du fichier VIDE (`31d6cfe0…`) est un cas DÉGÉNÉRÉ qu'amuled traite comme
> instantanément complet à 0 octet : il n'apparaît jamais comme partfile actif — ce qui avait
> masqué le bug. `_map_partfile` lit désormais le hash via `entry.find(EC_TAG_PARTFILE_HASH)`.

La complétion est DÉRIVÉE côté DTO (`DownloadEntry.is_complete = size_full > 0 and
size_done >= size_full`) : EC n'expose pas de booléen « complet » portable, et un
`size_full == 0` (entrée naissante) n'est jamais complet (réf. `ports/mule_download_client.py`).

---

## Confirmation empirique (EMPIRIQUE)

### Suite `download_integration` (testcontainers)

`tests/integration/test_amuled_download.py`, marqueur `download_integration`, contre
`ngosang/amule:3.0.0-1`. Run dédié :

```
$ uv run pytest -m download_integration --no-cov -v -rA
collecting ... collected 620 items / 619 deselected / 1 selected

tests/integration/test_amuled_download.py::test_add_link_then_appears_in_download_queue PASSED [100%]

PASSED tests/integration/test_amuled_download.py::test_add_link_then_appears_in_download_queue
====================== 1 passed, 619 deselected in 2.59s =======================
```

Le test affirme désormais, sur le chemin accepté, que **le hash ajouté APPARAÎT dans la
file** (`_HASH in {e.ed2k_hash for e in queue}`) — c'est le RÉGRESSION GUARD du bug de
décodage de hash. Il tolère encore un `EcFailureError` propre (rejet explicite). Le hash
utilisé est NON DÉGÉNÉRÉ (`aabbccddeeff00112233445566778899`, pas la MD4 du fichier vide) avec
une vraie taille (`734003200`) → un partfile actif réellement listé.

### Sonde `download_probe` — run direct contre `ngosang/amule:3.0.0-1` (run corrigé 2026-06-13)

**Niveau : EMPIRIQUE.** Run exécuté le 2026-06-13 depuis `localhost:14712`
(`docker run -d --rm -e GUI_PWD=probe-test -p 14712:4712 ngosang/amule:3.0.0-1`), avec un
hash NON DÉGÉNÉRÉ et une vraie taille :

```
$ uv run python -m emule_indexer.tools.download_probe --host 127.0.0.1 --port 14712 \
    --password probe-test --link 'ed2k://|file|probe-download.bin|734003200|aabbccddeeff00112233445566778899|/'
[probe] statut réseau : NetworkStatus(ed2k_id=None, ed2k_high=False, kad_status=<KadStatus.FIREWALLED: 'firewalled'>, server_name=None, server_addr=None)
[probe] add_link accepté pour : ed2k://|file|probe-download.bin|734003200|aabbccddeeff00112233445566778899|/
[probe] file de download : 1 entrée(s)
[probe] aabbccddeeff00112233445566778899 : 0/734003200 o (complet=False)
exit=0
```

**Interprétation.** Le cycle complet est confirmé, et CETTE FOIS le partfile apparaît :
1. **Auth réussie** et statut réseau décodé (Kad `firewalled`, pas encore de serveur eD2k).
2. **`add_link` accepté** : réponse EC `EC_OP_NOOP` (succès, pas `EC_OP_STRINGS`).
3. **`download_queue` rend 1 entrée** : `aabbccddeeff00112233445566778899 : 0/734003200 o
   (complet=False)`. Le hash est décodé depuis l'enfant `EC_TAG_PARTFILE_HASH` (0x031E), la
   valeur propre (UINT8) étant ignorée. `size_done=0 < size_full=734003200` → `is_complete`
   correctement `False`.

> **Pourquoi le run précédent voyait une file vide.** L'ancienne sonde utilisait la MD4 du
> fichier VIDE (`31d6cfe0…`) : amuled la traite comme instantanément complète à 0 octet et ne
> la liste jamais comme partfile actif. Conjugué au bug de décodage (`_map_partfile` lisait le
> hash dans la valeur PROPRE — un UINT8 — au lieu de l'enfant 0x031E), ce double facteur
> masquait totalement la panne : `download_queue` renvoyait `()` même pour un vrai partfile.

Aucun `EC_OP_FAILED` n'a été reçu : c'est le cas « add_link accepté + partfile listé » qui
s'est produit.

---

## PENDING homelab

> **Statut (2026-06-25)** : ces mesures restent **DEFERRED** — elles exigent un déploiement
> homelab réel avec sources eD2k actives et un transfert qui aboutit, ce qui n'a pas encore été
> orchestré. Conséquence : la mécanique de complétion EC repose sur la lecture des sources amont
> d'aMule (cf. `2026-06-17-amuled-completion-behavior.md`), pas sur une observation directe via EC.
> Si un déploiement réel a lieu, mettre à jour cette section avec le verdict
> (COMPLETED / ABANDONED / nouvelles trouvailles).

Non atteignable depuis un conteneur éphémère (pas de sources eD2k, LowID + réseau contraint) :

- **Complétion réelle** : `size_done == size_full` sur un partfile, donc
  `DownloadEntry.is_complete == True` observé en vrai (jamais atteignable ici).
- **`size_done` non nul EN COURS** : un partfile RÉELLEMENT en transfert (octets reçus de
  sources), donc `0 < size_done < size_full`. (La forme « partfile listé, `size_done=0`,
  hash dans l'enfant 0x031E » est désormais CONFIRMÉE empiriquement — voir ci-dessus.)
- **Chemin staging réel** : non exposé par EC (DÉCISION D2 — la localisation pour la
  quarantaine est dérivée d'un staging configuré par l'appelant, pas lue dans le DTO).

Commande homelab (réutilise la sonde telle quelle, sur un lien à sources) :

```bash
uv run python -m emule_indexer.tools.download_probe --host <homelab> --port 4712 \
    --password <pwd> --link '<lien ed2k d'un fichier réel à sources>'
```

La sonde affiche chaque entrée de la file (hash, `size_done`/`size_full`, `complet=…`) :
toute observation d'une complétion réelle ou d'une forme inattendue de la file s'ajoute ici
en annexe.
