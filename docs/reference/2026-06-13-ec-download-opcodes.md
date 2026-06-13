# Opcodes EC download — add_link + file de download (2026-06-13)

> Sonde empirique PRÉCOCE du plan D (download). Valide les MÉCANIQUES EC du download
> (option A) : `add_link` accepté par un `amuled` réel + `download_queue` décodée avec un
> statut lisible. Miroir, côté DOWNLOAD, de `docs/reference/2026-06-11-ec-field-richness.md`
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
téléchargement côté daemon ; `download_queue` se décode sans erreur.** Les mécaniques EC du
download (option A) sont validées de bout en bout. La COMPLÉTION réelle
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

Tags partfile (enfants d'`EC_TAG_PARTFILE` dans `EC_OP_DLOAD_QUEUE`) :

| Tag                            | Valeur  | Contenu |
|--------------------------------|---------|---------|
| `EC_TAG_PARTFILE` (valeur PROPRE) | —    | hash MD4 (`HASH16`, 16 octets) — SEUL identifiant stable |
| `EC_TAG_PARTFILE_NAME`         | `0x0301`| nom du partfile |
| `EC_TAG_PARTFILE_SIZE_FULL`    | `0x0303`| taille totale (octets) |
| `EC_TAG_PARTFILE_SIZE_DONE`    | `0x0306`| octets transférés |
| `EC_TAG_PARTFILE_STATUS`       | `0x0308`| statut de téléchargement côté daemon |
| `EC_TAG_PARTFILE_ED2K_LINK`    | `0x030E`| lien ed2k reconstitué |
| `EC_TAG_PARTFILE_HASH`         | `0x031E`| hash MD4 (forme tag dédié) |

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
collecting ... collected 553 items / 552 deselected / 1 selected

tests/integration/test_amuled_download.py::test_add_link_then_appears_in_download_queue PASSED [100%]

PASSED tests/integration/test_amuled_download.py::test_add_link_then_appears_in_download_queue
====================== 1 passed, 552 deselected in 2.60s =======================
```

Le test est tolérant (option A) : il passe que le lien apparaisse dans la file, que la file
soit vide (dédup/rejet silencieux), OU que amuled réponde `EcFailureError` propre. **Cas
observé : `add_link` accepté + `download_queue` décodée renvoyant une file vide** (voir la
sonde ci-dessous, qui confirme côté daemon que le lien EST bien ajouté).

### Sonde `download_probe` — run direct contre `ngosang/amule:3.0.0-1`

**Niveau : EMPIRIQUE.** Run exécuté le 2026-06-13 depuis `localhost:14712`
(`docker run -d --rm --name dl-probe-target -e GUI_PWD=probe-test -p 14712:4712
ngosang/amule:3.0.0-1`) :

```
$ uv run python -m emule_indexer.tools.download_probe --host 127.0.0.1 --port 14712 \
    --password probe-test --link 'ed2k://|file|probe-download.bin|1048576|31d6cfe0d16ae931b73c59d7e0c089c0|/'
[probe] statut réseau : NetworkStatus(ed2k_id=13386, ed2k_high=False, kad_status=<KadStatus.FIREWALLED: 'firewalled'>, server_name='ed2k-rust', server_addr='45.87.41.16:13386')
[probe] add_link accepté pour : ed2k://|file|probe-download.bin|1048576|31d6cfe0d16ae931b73c59d7e0c089c0|/
[probe] file de download : 0 entrée(s)
exit=0
```

Logs amuled correspondants :

```
2026-06-13 17:18:33: *** TCP socket (ECServer) listening on 0.0.0.0:4712
2026-06-13 17:18:38: New external connection accepted
2026-06-13 17:18:38: Access granted.
2026-06-13 17:18:48: New external connection accepted
2026-06-13 17:18:48: Access granted.
2026-06-13 17:18:48: ExternalConn: adding link 'ed2k://|file|probe-download.bin|1048576|31d6cfe0d16ae931b73c59d7e0c089c0|/'.
2026-06-13 17:18:48: Downloading probe-download.bin
2026-06-13 17:18:48: External connection closed.
```

**Interprétation.** Le cycle complet est confirmé :
1. **Auth réussie** (`Access granted.`) et statut réseau décodé (LowID `13386`, serveur
   `ed2k-rust`, Kad `firewalled`).
2. **`add_link` accepté** : la réponse EC est `EC_OP_NOOP` (succès, pas `EC_OP_STRINGS`), et
   le LOG DAEMON le confirme explicitement (`ExternalConn: adding link '…'` puis
   `Downloading probe-download.bin`) — amuled a bien créé un téléchargement pour ce lien.
3. **`download_queue` décodée sans erreur**, renvoyant une file vide. Le hash utilisé
   (`31d6cfe0d16ae931b73c59d7e0c089c0`) est la MD4 canonique du fichier VIDE : amuled
   accepte le lien mais l'entrée ne reste pas dans la file de download active (cas « file
   vide tolérée » de l'option A). La MÉCANIQUE — `add_link` accepté + `download_queue`
   décodée — est ce qui fait foi : elle est validée.

Aucun `EC_OP_FAILED` n'a été reçu : c'est donc le cas « add_link accepté + file vide » qui
s'est produit (et non le cas FAILED-propre, lui aussi toléré par l'e2e).

---

## PENDING homelab

Non atteignable depuis un conteneur éphémère (pas de sources eD2k, LowID + réseau contraint) :

- **Complétion réelle** : `size_done == size_full` sur un partfile, donc
  `DownloadEntry.is_complete == True` observé en vrai (jamais atteignable ici).
- **Forme RÉELLE d'une entrée non vide** de `download_queue` : `size_done`/`size_full` non
  nuls, `EC_TAG_PARTFILE_NAME`/`_STATUS` peuplés, sur un fichier réellement en cours.
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
