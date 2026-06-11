# Handoff — emule-indexer (adapter EC)

> **But de ce document** : permettre à une nouvelle session de reprendre naturellement après le
> jalon `v0.5.0-ec-adapter`. Lis d'abord le handoff précédent (`2026-06-11 - handoff - moteur de
> matching complet.md`) pour le moteur ; ce document couvre l'**adapter EC + observation** (Plan B).
>
> **Dernière mise à jour** : 2026-06-11, après le tag `v0.5.0-ec-adapter`.

---

## 1. TL;DR

- **Ce qui est fait** : l'**adapter EC complet** — le crawler sait maintenant parler à un `amuled`
  réel : auth challenge/réponse, recherche mot-clé (`global`/`kad`), relevé des résultats en
  `FileObservation` (capture-all), arrêt, progression, statut réseau. **285 tests unitaires
  (100 % branch) + 4 tests d'intégration contre un amuled Docker réel, tous verts au tag.**
- **Verdict stratégique du dé-risquage** (rapport : `docs/reference/2026-06-11-ec-field-richness.md`) :
  **EC n'expose AUCUNE métadonnée média** (durée/bitrate/codec) sur les résultats de recherche
  (vérifié au niveau source 2.3.3/3.0.0). Le catalogue vivra de : nom, taille, hash MD4,
  compteurs de sources (dont sources complètes), statut. Conséquences pour le schéma plan A
  documentées dans le rapport (§ « Conséquences »).
- **Prochaine étape recommandée** : **Plan A (modèle de données)** — le schéma peut maintenant
  être conçu en connaissance de cause ; ou Plan C (orchestration) si on veut brancher le moteur
  sur le flux d'observations d'abord. Brainstormer avant (choix de design ouverts).

## 2. État vérifiable

- Branche `main`, tag annoté **`v0.5.0-ec-adapter`** (non poussé, comme les précédents).
- Gates : `uv run pytest -q` → 285 passed, 4 deselected, 100 % branch ;
  `uv run pytest -m ec_integration --no-cov -q` → 4 passed (Docker requis,
  image `ngosang/amule:3.0.0-1`) ; ruff + format + mypy strict propres.
- Spec du jalon : `docs/superpowers/specs/2026-06-11-ec-adapter-design.md`.
  Plan exécuté : `docs/superpowers/plans/2026-06-11-crawler-mvp-03-ec-adapter.md` (17 tâches).
- **Référence protocole** (source de vérité wire-level, vérifiée sur les sources aMule) :
  `docs/reference/ec-protocol.md`.

## 3. Ce qui existe maintenant (au-dessus du moteur)

```
src/emule_indexer/
├── domain/observation.py        # FileObservation (capture-all) + .to_candidate() → moteur
├── ports/mule_client.py         # MuleClient (Protocol async), NetworkStatus, SearchChannel, KadStatus
├── adapters/mule_ec/
│   ├── codes.py                 # constantes (ECCodes.h transcrit, référencé)
│   ├── errors.py                # EcError → Connect/Auth/Protocol/Timeout/Failure
│   ├── codec.py                 # PUR/sync : EcTag/EcPacket, encode/décode, bornes 16 Mio + prof. 32
│   ├── transport.py             # async : framing, timeouts lecture, close() best-effort
│   ├── client.py                # AmuleEcClient (auth, recherche, statut) — satisfait MuleClient
│   └── mapping.py               # résultats EC → FileObservation, capture-all, écartés comptés
└── tools/ec_probe.py            # CLI probe : uv run python -m emule_indexer.tools.ec_probe
                                 #   (mot de passe via --password ou EC_PROBE_PASSWORD)
tests/integration/test_amuled_ec.py   # marker ec_integration, testcontainers, OBLIGATOIRE avant tag
```

## 4. Décisions prises pendant l'exécution (en plus de la spec)

- **`close()` du transport = best-effort** (`contextlib.suppress(OSError)`) — déviation ASSUMÉE
  de la lettre de DÉCISION 5 : « signaler » vaut pour les opérations, pas pour le cleanup (un
  `ConnectionResetError` brut dans un `finally` masquerait l'erreur d'origine ; vérifié
  empiriquement sur RST).
- **Machine à états du client durcie** (post-revue) : double `connect()` rejeté ; transport jeté
  sur `EcTimeoutError`/`EcConnectError`/**`EcProtocolError`** issus de `receive_packet` (flux
  potentiellement désynchronisé → l'appel suivant échoue vite et proprement « non connecté »).
- **Mapping vraiment capture-all** (post-revue) : descente récursive des sous-arbres non mappés ;
  `EC_TAG_SEARCH_PARENT` (ECID volatil, piège 13) jeté partout ; compteur optionnel malformé → 0
  (jamais fatal à une bonne observation) ; doublon d'un tag mappé → le surplus va en `raw_meta`.
- **Décodeur** : bit-enfants sans TAGCOUNT rejeté ; taille des enfants comptée par position de
  lecture réelle (pas de re-calcul encodeur) — corrige une sur-lecture silencieuse de 2 octets.
- `size_mb` du pont = **Mio** (1 048 576 octets) ; hash eD2k canonique = hex **minuscule**.

## 5. Pièges appris (à transmettre aux plans suivants)

- **`LogMessageWaitStrategy` vit dans `testcontainers.core.wait_strategies`** (pas
  `waiting_utils`) ; `wait_for_logs(str)` est déprécié.
- Le conteneur `ngosang/amule:3.0.0-1` **a accès Internet par défaut** (bridge Docker) : le probe
  s'y est connecté à un vrai serveur eD2k (LowID). Bien pour le réalisme, à garder en tête pour
  l'isolation des tests.
- `skipped_entries_total` du client = compteur d'**événements** sur relevés cumulatifs (une même
  entrée pourrie compte à chaque relevé) — ne pas l'interpréter « entrées uniques » (plan E).
- Les enfants d'un tag **mappé** ne sont pas descendus dans `raw_meta` (les tags de résultats
  sont plats en 2.3.3/3.0.0, liste exhaustive vérifiée source) — si un futur aMule imbrique des
  métadonnées sous NAME/SIZE/HASH, étendre `_raw_meta`.
- Le grep « domaine pur » de la Task 17 du plan 03 ne blanchit pas les deps pur-calcul du moteur
  (`re2`, `rapidfuzz`, `re`) — amender la liste d'exclusion si on le réutilise tel quel.
- FCFS strict sur le fil EC : une requête à la fois ; après timeout/trame illisible, le flux est
  mort — jeter le transport (le client le fait), jamais re-lire.

## 6. Comment mesurer la richesse réelle (homelab, plus tard)

```bash
EC_PROBE_PASSWORD=... uv run python -m emule_indexer.tools.ec_probe \
  --host <homelab> --port 4712 --keyword keroro --channel global --timeout 60 --interval 5
```
→ ajouter les constats (tags `raw` observés sur de VRAIS résultats) au rapport de richesse.

## 7. Méthode (reconduite, et elle a payé)

Subagent-driven : implémenteur frais par tâche + revue spec + revue qualité **adversariale**
(modèle fort) par tâche + revue holistique finale. Les revues qualité ont attrapé **quatre vrais
bugs du plan lui-même** (sur-lecture décodeur, fuite de `close()`, trous de machine à états du
client, trous capture-all du mapping) — aucun n'aurait été visible en suivant le plan à la lettre.
La revue holistique a confirmé la règle de dépendance et trouvé les derniers raffinements.
