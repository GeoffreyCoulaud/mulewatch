# Richesse des champs EC — résultats de recherche (2026-06-11)

> Livrable 5 du plan B (`v0.5.0-ec-adapter`). Compare les champs ESPÉRÉS par la table
> `file_observations` (spec MVP §11) aux champs que EC expose RÉELLEMENT sur un résultat
> de recherche. Source : `docs/reference/ec-protocol.md` §5 (vérifié sur les sources
> aMule 2.3.3 et 3.0.0 — `CEC_SearchFile_Tag`, liste EXHAUSTIVE), confirmé par la suite
> d'intégration `ec_integration` contre `ngosang/amule:3.0.0-1`.

---

## Convention de fiabilité

Ce rapport distingue explicitement trois niveaux de certitude :

- **SOURCE** — fait établi par lecture des sources C++ d'aMule (tags 2.3.3 et 3.0.0),
  référencé dans `docs/reference/ec-protocol.md`.
- **EMPIRIQUE** — confirmé par exécution réelle : suite `ec_integration`
  (testcontainers) et/ou sonde `ec_probe` contre `ngosang/amule:3.0.0-1`.
- **PENDING** — non encore mesuré (réseau eD2k/Kad réel avec résultats non vides) ;
  un run homelab complétera cette section.

---

## Verdict en une ligne

**EC n'expose AUCUNE métadonnée média sur un résultat de recherche** (ni durée, ni
bitrate, ni codec) : la moitié « média » du schéma §11 ne sera PAS alimentée par la
recherche — elle devra venir d'ailleurs (analyse locale post-download, plan D/verifier).

---

## Champ par champ (§11 `file_observations` vs EC)

| Champ espéré (§11)          | Tag EC                                        | Exposé ? | Niveau    | Constat |
|-----------------------------|-----------------------------------------------|----------|-----------|---------|
| `filename`                  | `EC_TAG_PARTFILE_NAME` (0x0301)               | OUI      | SOURCE    | nom observé, UTF-8 + NUL final |
| `size_bytes` (via `files`)  | `EC_TAG_PARTFILE_SIZE_FULL` (0x0303)          | OUI      | SOURCE    | octets, entier à largeur variable (§9 piège 4) |
| `ed2k_hash` (clé contenu)   | `EC_TAG_PARTFILE_HASH` (0x031E)               | OUI      | SOURCE    | MD4 16 octets — SEUL identifiant stable (l'ECID est volatil, réf. §9 piège 13) |
| `source_count`              | `EC_TAG_PARTFILE_SOURCE_COUNT` (0x030A)       | OUI      | SOURCE    | nombre de sources |
| `complete_source_count`     | `EC_TAG_PARTFILE_SOURCE_COUNT_XFER` (0x030D)  | OUI      | SOURCE    | nom amont TROMPEUR (« XFER ») mais c'est bien `CompleteSourceCount` (réf. §9 piège 12) |
| `media_length_sec`          | —                                             | **NON**  | SOURCE    | aucun tag média ne transite par EC |
| `bitrate`                   | —                                             | **NON**  | SOURCE    | idem |
| `codec`                     | —                                             | **NON**  | SOURCE    | idem |
| `file_type`                 | —                                             | **NON**  | SOURCE    | `EC_TAG_SEARCH_FILE_TYPE` (0x0705) est un **filtre de requête**, pas une métadonnée de résultat |
| `raw_meta` JSON             | tous les tags non mappés                      | OUI      | SOURCE    | capture-all tenu dès la frontière (mapper + `FileObservation.raw_meta`) |
| `keyword`                   | (posé par le client : provenance)             | OUI      | SOURCE    | présent dans `FileObservation`, injecté par le client |
| `observed_at`, `node_id`    | (colonnes de persistance, plan A)             | n/a      | n/a       | injectées par l'adapter DB, hors scope EC |

Source pour la liste des tags de résultat : `CEC_SearchFile_Tag`,
`src/ECSpecialCoreTags.cpp:353-372` (aMule 2.3.3) et version 3.0.0 avec le rating
(`amule-org/amule@3.0.0`, même fichier). **C'est la liste exhaustive.**

---

## Champs exposés par EC NON prévus par §11 (récupérés via `raw_meta`)

Ces trois champs apparaissent dans `EC_OP_SEARCH_RESULTS` mais n'ont pas de colonne
dédiée dans la table `file_observations` de §11. Ils atterrissent automatiquement dans
`raw_meta` sans aucun changement de schéma :

- **`EC_TAG_PARTFILE_STATUS` (0x0308)** — statut de téléchargement côté daemon ; 0 sur
  un résultat neuf. Utile pour détecter si le daemon a déjà ce fichier en cours.
- **`EC_TAG_SEARCH_PARENT` (0x0709)** — ECID du parent dans la variante de résultats
  groupés. Volatil (identifiant de session), jamais à persister.
- **`EC_TAG_KNOWNFILE_RATING` (0x040F)** — note utilisateur, aMule 3.0.0 uniquement, si
  le fichier a été évalué. Absent sur les résultats non notés.

Aucune migration de schéma nécessaire : `raw_meta` (capture-all JSON) les absorbe.

---

## Conséquences pour le plan A (schéma `catalog.db`)

1. **Garder les colonnes `media_*` nullables** : elles resteront NULL pour toute
   observation issue d'une recherche EC. Les renseigner passera par l'analyse locale
   d'un fichier téléchargé (verifier, `file_verifications.real_meta`, §11) — pas par EC.
2. **Garder `raw_meta`** (capture-all) : status/parent/rating y sont déjà, et tout tag
   futur d'aMule y atterrira sans changement de schéma.
3. **`complete_source_count` est fiable et précieux** : `EC_TAG_PARTFILE_SOURCE_COUNT_XFER`
   livre bien le nombre de sources complètes (malgré son nom trompeur), utilisable pour
   la priorisation des cibles `download`.
4. **Ne JAMAIS persister l'ECID** : identifiant de session, écrasé à chaque
   `EC_OP_SEARCH_START` ; seule clé stable = hash MD4 (`EC_TAG_PARTFILE_HASH`).
5. Le compteur d'entrées écartées (`AmuleEcClient.skipped_entries_total`) est prêt pour
   la métrique du plan E.

---

## Confirmation empirique

### Suite d'intégration `ec_integration`

**Niveau : EMPIRIQUE.** Tests exécutés avec testcontainers contre `ngosang/amule:3.0.0-1`
(marqueur `ec_integration`, `tests/integration/test_amuled_ec.py`) :

- **Authentification réelle** : formule challenge/réponse du §4
  (`MD5(lower(MD5(pwd)) + MD5(format("%X", salt)))`) validée contre le vrai daemon —
  connexion acceptée avec le bon mot de passe, `EcAuthError` levée avec un mot de passe
  incorrect.
- **Décodage du statut réseau** : `EC_OP_GET_CONNSTATE` décodé, `NetworkStatus` retourné
  avec un `KadStatus` valide.
- **Cycle complet** `start_search` / `fetch_results` / `stop_search` : validé ; sans
  connectivité eD2k depuis le conteneur, `EC_OP_FAILED` est reçu proprement
  (`EcFailureError` avec le message du daemon), ou les résultats sont vides — dans les
  deux cas le cycle applicatif requête/réponse est confirmé.

### Sonde `ec_probe` — run direct contre `ngosang/amule:3.0.0-1`

**Niveau : EMPIRIQUE.** Run exécuté le 2026-06-11 depuis `localhost:14712`
(`docker run -d --rm --name ec-probe-target -e GUI_PWD=probe-test -p 14712:4712 ngosang/amule:3.0.0-1`) :

```
[probe] statut réseau :
  eD2k : id=169693 high=False
  Kad  : firewalled
  serveur : ed2k-rust (45.87.41.16:38621)
[probe] relevé 1/5 : 0 résultat(s), progression 100%
[probe] total : 0 résultat(s)
exit=0
```

Logs amuled :

```
2026-06-11 15:31:49: *** TCP socket (ECServer) listening on 0.0.0.0:4712
2026-06-11 15:31:54: New external connection accepted
2026-06-11 15:31:54: Access granted.
2026-06-11 15:32:03: New external connection accepted
2026-06-11 15:32:03: Access granted.
```

**Interprétation :** le conteneur était connecté à eD2k (LowID `169693`, serveur
`ed2k-rust`) et Kad (firewalled). L'authentification a réussi (`Access granted.`) —
deux connexions : une pour `network_status()`, une pour le cycle recherche. La recherche
`keroro` (canal `global`) a abouti proprement (`progression 100%`) mais sans résultats :
le réseau eD2k du conteneur éphémère ne disposait pas d'index de recherche (LowID +
environnement réseau contraint). **C'est le résultat attendu et valide** pour ce contexte
de test : il confirme que le cycle complet fonctionne sans erreur.

La richesse des champs réels (tags `raw_meta` sur de vrais résultats) sera mesurée lors
d'un run homelab (réseau eD2k/Kad réel avec index disponible) :

```bash
uv run python -m emule_indexer.tools.ec_probe --host <homelab> --port 4712 \
    --password <pwd> --keyword keroro --channel global
```

Le probe affiche chaque entrée `raw_meta` (nom hex + valeur) : toute trouvaille
inattendue lors d'un run réel s'ajoute ici en annexe.

### Ce que le probe N'a PAS encore mesuré (PENDING homelab)

- Présence/absence réelle de `EC_TAG_KNOWNFILE_RATING` sur des résultats non vides.
- Valeur effective de `EC_TAG_PARTFILE_STATUS` sur un résultat neuf depuis le réseau.
- Tout tag `raw_meta` éventuel non répertorié dans `CEC_SearchFile_Tag` (non attendu
  d'après les sources, mais à vérifier sur trafic réel).
