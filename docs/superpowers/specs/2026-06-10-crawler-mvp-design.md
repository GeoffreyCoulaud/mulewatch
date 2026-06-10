# Spec — emule-indexer : Crawler MVP (+ auto-download)

> Conception validée en brainstorming le 2026-06-10. Premier sous-projet d'emule-indexer.
> Voir aussi `docs/knowledge-brief.md` (cadrage initial).

## 1. Contexte & objectif

- **Lost media** : doublage **VF de « Keroro mission Titar »** (diffusé en 2008 sur Teletoon, France). Majorité des épisodes perdue ; liste canonique connue (Wikipédia) + statut par épisode (complet/partiel/perdu/piètre). Communauté sur Discord.
- **Objectif** : retrouver un maximum d'épisodes perdus, et **cataloguer un maximum de métadonnées** (fichiers, sources, durées) même sans téléchargement.
- **Mécanisme** : les fichiers apparaissent **par intermittence** quand un détenteur se connecte → il faut une **surveillance permanente et distribuée** (plusieurs chercheurs) pour transformer le hasard en couverture.
- **Limite éthique** : le sujet du catalogue est **le fichier, pas la personne**. Pas de pistage ni de désanonymisation ; minimisation des données.

## 2. Périmètre de ce sous-projet & hors-scope

**Dans le périmètre (MVP) :** surveillance continue (recherche eD2k + Kad) → catalogue → scoring → notifications, **+ auto-download (capacité activable)** avec **confinement** du contenu téléchargé.

**Hors-scope (sous-projets ultérieurs) :** outil d'export/fusion multi-chercheurs ; hub central (option C : push, identités de bots) ; **vérification de contenu réelle** (ffprobe/type-sniff/ClamAV) — **NO-OP en MVP** ; durcissement gVisor/nsjail (opt-in Linux) ; UI web d'admin (déprioritisée) ; politique de rétention/compaction (défaut : tout garder) ; autres lost media que Keroro.

## 3. Modes de déploiement (même image, un flag)

| | **Observer** (défaut, distribution) | **Full** (homelab Linux) |
|---|---|---|
| Recherche + catalogue + notif (avec **lien ed2k**) | ✅ | ✅ |
| Auto-download | ❌ | ✅ (`download.enabled`) |
| Contenu hostile sur disque | aucun | oui → confinement |
| Sandbox de contenu / vérif | sans objet (amuled reste durci) | confinement (MVP) + vérif (NO-OP) |
| High ID / NAT-PMP | non requis | oui (via gluetun) |
| VPN | optionnel (métadonnées seules) | requis |

- **Observer** : portable (Docker Desktop/Windows), onboarding trivial pour les chercheurs Discord (`docker compose up -d`, VPN optionnel, pas de port-forward). Sert le « filet large ».
- **Full** : le download (risqué/peu portable) se concentre sur les nœuds capables et de confiance.

## 4. Architecture — vue d'ensemble

```
┌───────────────────────── homelab (docker compose) ─────────────────────────┐
│  gluetun (VPN ProtonVPN + NAT-PMP) ── netns ── amuled (durci, RO, no-share) │
│        ▲ port-sync (glueforward étendu) écrit le port (TCP=UDP)             │
│                                                                             │
│  crawler (orchestrateur, multi-homed)                                       │
│    • EC ↔ amuled    • RPC ↔ verifier (réseau internal)   • apprise (egress) │
│    • writer unique de catalog.db ET local.db                                │
│                                                                             │
│  verifier (mode Full) : HTTP sur réseau `internal: true`, SANS Internet     │
│    └─ fork enfant jetable par fichier (net=none, rlimits, RO, timeout)      │
│                                                                             │
│  volumes : quarantine/ (staging+quarantine, même FS) · catalog.db · local.db│
└─────────────────────────────────────────────────────────────────────────────┘
```

**Couches (Clean/Hexagonal), Python `src/`** :
```
src/emule_indexer/
  domain/        # PUR : entités, normalisation, moteur de matching, règles de décision
  application/   # use-cases : RunSearchCycle, ScoreResults, RecordObservations,
                 #             DecideDownload, CompleteDownload, RunVerification
  ports/         # MuleClient, CatalogRepository, LocalStateRepository,
                 # ContentVerifier, Quarantine, Notifier, MetricsSink, Clock
  adapters/      # mule_ec/ persistence_sqlite/ verifier_http/ notify_apprise/
                 # metrics_prometheus/ quarantine_fs/ config/
  composition/   # composition root, entrypoint, scheduler
```
**Règle de dépendance** : `adapters → ports ← application → domain`. Le domaine ne dépend de rien ; tout l'I/O est derrière un port mockable.

## 5. Réseau & protocole

- **Moteur = aMule seul** (`amuled` headless + API **EC**), piloté comme une brique d'infra. Adaptateur EC **écrit en Python** (aucune lib existante ; protocole binaire documenté). Image : `ngosang/docker-amule`.
- Pas de MLDonkey (réseau Overnet mort/incompatible ; serveurs eD2k partagés). Pas de G2/Gnutella. **Pas de seeding.**
- **High ID** (mode Full) : **gluetun** assure le tunnel ProtonVPN **et le NAT-PMP**, et expose le port forwarded via son **control server API**. **glueforward** (étendu, voir §15.2) lit ce port et l'applique à aMule en **TCP et UDP au même numéro**. amuled partage la pile réseau de gluetun (**killswitch** = pas de fuite si le VPN tombe).
- **Réseau** :
  - **amuled n'a pas de réseau Docker propre** : `network_mode: service:gluetun` → il partage la stack réseau de gluetun (tout le P2P sort par le VPN). Son **port EC est exposé par le conteneur gluetun**.
  - `ec` (interne) : crawler ↔ `gluetun:<ec_port>` (pilotage d'amuled via EC).
  - `verify-internal` (**`internal: true`**) : crawler ↔ verifier (RPC, **sans Internet**).
  - `egress` : crawler ↔ Internet (apprise, DNS) ; gluetun a son propre accès WAN pour le tunnel.
  - Le **crawler** est multi-homed (`ec` + `verify-internal` + `egress`). Le **verifier** n'est que sur `verify-internal`. L'**enfant jetable** n'a **aucun réseau** (`net=none`).

## 6. Orchestration des recherches

- **Mots-clés** générés depuis `targets.yaml` : **large** (`Keroro`) pour ratisser + cataloguer tout ; **ciblés par segment** (tokens du titre, `062A`, `Mission Titar`, `TELETOON`, date) pour précision/scoring.
- **Canaux** : recherche **globale serveurs** (UDP) + **Kad**, via EC. Résultats versés au catalogue.
- **Scheduler** : boucle continue, **cadence rate-limitée**, **ordre randomisé par nœud** (seed dérivé du `node_id` + index de cycle → divergence inter-nœuds, déterministe/testable) + **jitter** sur les délais. → supprime les angles morts temporels même à 2-3 chercheurs.
- **Anti-rate-limit** : espacement, **backoff exponentiel par serveur**, jitter, répartition sur plusieurs serveurs. Événements de throttle détectés et exposés en métrique.
- **Bootstrap réseau** : provisionner `server.met` frais + `nodes.dat` Kad ; surveiller High ID / état Kad via EC (→ métriques + alerte si Low ID / firewalled).
- **Anti-redondance** : `last_resolved_at` par hash ; re-observation périodique throttlée (pour capter l'intermittence).

## 7. Liste cible (`config/targets.yaml`)

Curée, versionnée, seedée depuis Wikipédia + le tableau de statut Discord ; contributions par PR. Granularité **segment**.

```yaml
episodes:
  - season: 2
    number: 62
    broadcast_date: 2008-09-21
    status: partial            # lost | partial | poor | complete
    segments:
      - { letter: A, title: "Les demoiselles cambrioleuses", aliases: [] }
      - { letter: B, title: "Le grand combat sous-marin" }
```
→ `target_id` stable (`S2E62A`) ; fournit `{number}`, `{segment}`, `{title}`, `{date_alt}` à l'interpolation.

## 8. Moteur de matching (cœur)

**Principe** : moteur minimal en code, **politique 100 % en config**.

### 8.1 Normalisation
- `raw` = basename observé. `norm(s)` = **normalisation Unicode NFKD** (décomposition de compatibilité, puis suppression des diacritiques combinants pour le repli d'accents) → minuscules → non-alphanumériques en espaces → trim. *(NFKC disponible comme variante de composition si besoin.)* `tokens(s)` = `norm(s)` découpé sur les espaces.

### 8.2 Types de tokens (4, en code)
| Type | Forme | Opère sur | Vrai si… |
|---|---|---|---|
| `keyword` | `{ keyword: "mission titar" }` | `tokens(norm)` | phrase = sous-suite contiguë de tokens |
| `regex` | `{ regex: "...", flags: "i" }` | `raw` (flag `i` défaut), **après interpolation** | `re.search` ≠ ∅ |
| `coverage` | `{ coverage: title, min: 0.6, fuzz: 0.85 }` | `tokens(norm)` | fraction fuzzy des tokens significatifs de `title` ≥ `min` |
| `attr_between` | `{ attr_between: size_mb, min: 30, max: 600 }` | attribut fichier | attribut **présent** et dans `[min,max]` |

- `coverage` : `value = |{ r∈R : ∃ token fichier f, ratio(r,f) ≥ fuzz }| / |R|`, `R = tokens(norm(title)) \ stopwords`. Renvoie `value`. `min`/`fuzz` surchargeables au point d'usage.
- **Fuzzy `ratio`** = **Levenshtein normalisé via `rapidfuzz`** (token court), déterministe. *(Trigrammes réservés à une éventuelle recherche fuzzy côté DB, hors scope.)*
- `attr_between` : **enum fermé** = `size_mb | duration_sec | bitrate_kbps`. Tout autre nom → **erreur de validation au chargement**. Attribut absent → faux.
- **Interpolation** (regex uniquement) : whitelist `{number}` `{segment}` `{title}` `{date_alt}` (étend la `broadcast_date` en alternance de formats). Placeholder inconnu → erreur de chargement.

### 8.3 Tokens nommés & règles (config)
```yaml
tokens:
  keroro:     { keyword: keroro }
  titar:      { keyword: titar }
  keroro_titar: { any: [keroro, titar] }     # token COMPOSITE (référence d'autres tokens)
  teletoon:   { regex: "t[eé]l[eé]toon" }
  segment_id: { regex: "n[°o]?\\s*0*{number}\\s*{segment}" }
  air_date:   { regex: "{date_alt}" }
  title_hit:  { coverage: title, min: 0.6 }
  is_video:   { regex: "\\.(avi|mkv|mp4|mpg|ogm)$" }

rules:                                  # liste ORDONNÉE, 1re règle vraie gagne
  - { name: id_segment_exact,    tier: download, all: [is_video, segment_id, keroro] }
  - { name: date_teletoon_titre, tier: download, all: [air_date, teletoon, {token: title_hit, min: 0.4}] }
  - { name: numero_titre,        tier: notify,   all: [segment_id, {token: title_hit, min: 0.5}] }
  - { name: keroro_large,        tier: catalog,  any: [keroro_titar] }
```
**Grammaire (EBNF)** :
```
condition = "all:" "[" operand ("," operand)* "]" | "any:" "[" operand … "]" | "not:" operand
operand   = token_name | "{ token:" token_name ("," "min:" num)? ("," "fuzz:" num)? "}" | "{" condition "}"
tier      = "catalog" | "notify" | "download"
```
Un **matcher** = feuille (4 types) **ou** combinateur (`all`/`any`/`not`). Les tokens nommés peuvent être **composites** (référencer d'autres tokens).

### 8.4 Validation au chargement (fail-fast)
- **Graphe de références acyclique (DAG)** ; cycle → **erreur fatale** (on nomme le cycle).
- **Profondeur de résolution bornée** (défaut 32) → dépassement = erreur fatale.
- `attr_between` dans l'enum ; regex **compilables sous RE2** ; schéma YAML valide.

### 8.5 Évaluation
- **Brute-force** : pour chaque fichier, évaluer les règles contre **toutes** les cibles (pas d'heuristique d'entonnoir codée — c'était prématuré et incorrect). Regex **précompilées par cible** au chargement.
- Par paire `(fichier, cible)` : **1re règle vraie** → `(règle, tier, cible)`. **Décision fichier** = palier le plus haut (`download>notify>catalog`), départage déterministe : index de règle, puis `target_id`. Aucune règle vraie nulle part → **fichier écarté**.
- **Moteur regex = RE2** (`google-re2`/`pyre2`) : **temps linéaire**, ReDoS éliminé (noms de fichiers = input hostile, regex contribuées par PR). **Bornage de la longueur** du nom avant matching.
- **Explicabilité** : chaque décision logge tokens/règles déclenchés + `value` des `coverage`.
- **Sécurité d'exécution** (dédup par hash, plafond disque) = **code**, distincte de la politique de matching.

## 9. Auto-download (mode Full)

- **Politique « tout sauf complet »** : auto-DL si **tier `download`** ET `status ∈ {lost, partial, poor}` ET garde-fous OK :
  - type vidéo, taille/durée plausibles (**filtres de pertinence**, *pas* de sécurité — les tags sont non fiables), **dédup par hash eD2k**, **plafond disque/quota**.
- **`notify`** → notification seule (décision humaine) **avec lien `ed2k://`**. **`catalog`** → stockage seul.
- **Upgrades** : pour partiel/piètre, un match `download` d'un fichier **meilleur** (taille/bitrate/durée) → DL + notif « meilleure version ».
- aMule ajoute le lien ed2k via EC → **persiste et reprend** aux fenêtres d'intermittence.

## 10. Sécurité & traitement du contenu

### 10.1 Vérité fondatrice
L'adressage par contenu garantit l'**intégrité** (on reçoit les octets du hash), **pas l'innocuité** (l'attaquant choisit ces octets ; **tous** les meta-tags sont auto-déclarés → non fiables). **Tout contenu réseau est radioactif jusqu'à vérification.** Stratégie = **confinement + vérification + étiquetage honnête + humain dans la boucle**, pas une garantie de sûreté.

### 10.2 Modèle de menace
Prank/mauvais contenu · malware déguisé · exploit player/codec (chez le consommateur) · bombe média (DoS du vérificateur) · saturation disque · empoisonnement des meta-tags · **RCE amuled** · RCE de nos parseurs · pollution catalogue · **redistribution** involontaire.

### 10.3 Confinement (MVP, non négociable)
- **amuled sandboxé** : non-root, rootfs RO, `cap_drop: ALL`, `no-new-privileges`, seccomp, limites CPU/RAM/pids, réseau via gluetun ; montages écrivables limités à son état (config) et au volume de téléchargement (`staging`/`quarantine`). **Ce durcissement s'applique aux DEUX modes** — amuled ingère l'input réseau hostile même en observer. La quarantaine + le verifier, eux, ne concernent que le mode Full (présence de contenu).
- **`quarantine/`** : jamais exécuté, jamais `+x`, jamais ouvert automatiquement.
- **Invariant dur : partage aMule des téléchargements DÉSACTIVÉ** (incoming ≠ shared) → **on ne re-seede jamais un poison**.
- **Le crawler ne lit jamais le contenu** : uniquement des opérations FS de métadonnées (`rename`), derrière le port `Quarantine`.
- **Modèle de confiance à 2 axes** : `quarantine` (toujours, jusqu'à promotion humaine) ⟂ `verdict` (`unverified` | `clean` | `suspicious` | `malicious`).

### 10.4 Vérification (NO-OP en MVP, branchable)
- **Verifier** = service HTTP **stateless**, sur `verify-internal` (**sans Internet, sans DB**). `POST /verify { hash, expected: {duration_range, target_id, …} }` → `{ verdict, real_meta }`. Lit seulement le fichier `quarantine/<hash>` en RO.
- **Enfant jetable par fichier** : le verifier **fork** un enfant par fichier — scratch tmpfs neuf, fichier RO, **rlimits durs + timeout-kill**, non-root, **`net=none`**. **Égress = stdout/exit**, **parsé défensivement** (taille bornée, schéma strict ; échec/timeout → `suspicious`/`error`).
- **Checks** = pipeline pluggable (`type_sniff` / `ffprobe` / `clamav`) en **squelettes NO-OP** (gated par config, slot dans l'image, agrégation worst-status). **ClamAV = follow-up.** En MVP, la machinerie de l'enfant est **dormante** (rien ne s'exécute sur les octets) ; seul le confinement amuled est actif ; le verifier renvoie `unverified`.
- **Durcissement opt-in Linux** (hors MVP) : enfant sous `nsjail`/`bwrap` (frontière noyau par fichier) et/ou verifier sous **gVisor** (`runtime: runsc`). **Pas sur Windows** (Docker Desktop = VM Linux ; gVisor non supporté en WSL2).

### 10.5 Relais crawler ↔ verifier
- **Aller (fichier)** : à la complétion (connue via EC), le crawler **rename atomiquement** `staging/<nom>` → `quarantine/<hash>` (métadonnée only, **même FS**), RO côté verifier.
- **RPC** : crawler → `POST /verify` → réponse `{verdict, real_meta}`. **Supprime inbox/outbox.** Le fichier ne transite **pas** par HTTP (volume partagé).
- **Durabilité = DB, RPC = vivacité** : la **file de tâches** (`local.db`) est la vérité du travail en attente ; au redémarrage on réclame/re-émet. Idempotent (verifier stateless).

## 11. Modèle de données — deux bases

**`catalog.db`** — partageable, **append-only, adressé par contenu**, mergeable (UNION ; dédup par clés ; observations taguées `node_id` ; **sans conflit**). Principe **capture-all** : colonnes structurées **+ `raw_meta` JSON** (on ne perd jamais une métadonnée).
```
files(ed2k_hash PK, size_bytes, aich_hash?)
file_observations(id, ed2k_hash FK, filename, source_count, complete_source_count,
                  media_length_sec?, bitrate?, codec?, file_type?, raw_meta JSON,
                  keyword, observed_at, node_id)
sources(user_hash PK, client_name?, client_version?)
source_observations(id, user_hash?, ed2k_hash FK, ip, port, nickname, client_name,
                    client_version, country?, id_type?, has_complete_file?, origin,
                    raw_meta JSON, observed_at, node_id)
match_decisions(id, ed2k_hash FK, target_id, rule_name, tier, decided_at, node_id)
file_verifications(id, ed2k_hash FK, verdict, real_meta JSON, checks JSON,
                   verified_at, node_id)        -- résultat de vérif, CATALOGUÉ (mergeable)
```
- **Clé contenu = hash eD2k** ; **clé source = userhash** (stable, survit aux IP).
- **« Qui détient quoi d'autre »** = `source_observations` groupé par `user_hash` (nos observations croisées, jamais en interrogeant le pair).
- On **stocke IP + pseudo + client + pays + tags média** : toute métadonnée disponible est prise.

**`local.db`** — **opérationnel, jamais mergé** (exclu de la réconciliation inter-nœuds).
```
verification_tasks(id, ed2k_hash, status, claimed_at?, attempts, enqueued_at, lease_until?)
downloads(ed2k_hash PK, target_id, state, queued_at, completed_at?)   -- miroir de la file aMule
scheduler_state(...)   -- backoff par serveur, curseurs de cycle, dernier full-cycle
node_runtime(...)      -- identité/état du nœud
```
→ **Invariant de fusion : seul `catalog.db` traverse la frontière du nœud.** **Chaque base a un writer unique : le crawler.**

## 12. File de tâches (`local.db`)

Remplace le « busy » : à la complétion, **enfiler** une tâche ; un **pool de workers borné** la draine (**pool = back-pressure**). Pièges traités :
- **Claim atomique** : `BEGIN IMMEDIATE` + `UPDATE … RETURNING` (writer unique → pas de double-traitement).
- **Lease / visibility-timeout** : `in_progress` expirée → réclamée `pending` ; au démarrage, réclamer toutes les `in_progress`.
- **Retries bornés → dead-letter + alerte**, qui **double comme signal de sécurité** : un fichier qui fait crasher le verifier de façon répétée = **poison probable** → on arrête de réessayer et on marque suspect.
- **Ordre** = FIFO par défaut (LIFO = `ORDER BY`, trivial).
- **Reconstructible** : perte de `local.db` non fatale (re-dérivée du catalogue + file réelle aMule via réconciliation EC).

## 13. Observabilité

- **Anti dégradation silencieuse** : signal dérivé `effective_coverage` (`healthy`/`degraded`/`blind`). « Le process vit » ≠ « on peut trouver maintenant ».
- **Métriques Prometheus** : santé réseau (`mule_ed2k_id`, `mule_kad_status`, `servers_connected`, `forwarded_port`, `portsync_last_success`), activité recherche (`searches_total{channel}`, `search_results_total`, `search_errors_total`, `seconds_since_last_full_cycle`), `ratelimit_events_total{server}`, croissance catalogue, `ec_connected`/`ec_reconnects_total`, `download_disk_bytes` vs quota, `matches_total{tier}`, `downloads_total{state}`, profondeur de file & dead-letters.
- **Logs** structurés (JSON), corrélés par `search_cycle_id` ; décisions de matching explicables en DEBUG.
- **Notifications apprise** : candidat (`notify`/`download`) avec **lien ed2k** + métadonnées ; DL terminé + **verdict** ; **alertes santé** déclenchées **au changement d'état** (dédup + cooldown → **anti-fatigue**) ; routage par sévérité configurable.

## 14. Gestion d'erreurs & résilience

- **amuled/EC injoignable** : reconnexion backoff ; recherches **en pause** (pas de perte) ; jamais de crash.
- **Coupure VPN** : amuled offline (killswitch → **pas de fuite**), détecté → dégradé + alerte.
- **Changement de port** : portsync réécrit la config ; Low ID transitoire remonté.
- **Rate-limit/ban** : backoff par serveur + jitter + répartition.
- **Input adverse** : **RE2** + bornage longueur.
- **Divergence vue/aMule** : **réconciliation** des `downloads` avec la file réelle aMule au démarrage + périodiquement.
- **Données empoisonnées** : quarantaine + garde-fous + dead-letter sur crash répété.
- **Croissance append-only** : tout garder en MVP (volume modeste) ; compaction préservant la fusion = différée (rétention ouverte).
- **Config invalide** : **fail-fast** au chargement (DAG, enum, RE2, schéma).
- **Crash/redémarrage** : bases durables ; réconciliation ; relance scheduler ; idempotence auto-DL (dédup par hash).

## 15. Stack technique & empaquetage

- **Python uniquement** (pour ce projet) ; `uv`, `mypy --strict`, `ruff` (lint+format).
- **Clean/Hexagonal** (cf. §4). Dépendances : `rapidfuzz`, `pyre2`/`google-re2`, `apprise`, `prometheus_client`, SQLite (stdlib).
- **Adaptateur EC** = notre code Python (aucune lib) ; **test-first** sur trames capturées + intégration opt-in contre un vrai `amuled`.

### 15.1 Points d'entrée & images
- **Un seul codebase** (domaine partagé : types de verdict, config, modèle de données) avec **deux points d'entrée** → **deux images Docker** : `crawler` et `verifier`. Footprints de dépendances distincts (le verifier embarquera plus tard ffprobe/libmagic/ClamAV ; le crawler porte rapidfuzz/re2/apprise/prometheus).
- **`docker compose`** avec **profils `observer` / `full`** : observer = crawler + amuled + gluetun ; full = + verifier (+ glueforward). **glueforward** = conteneur séparé (image `ghcr.io/geoffreycoulaud/glueforward`).

### 15.2 glueforward — extension service `amule`
- Projet existant : <https://github.com/GeoffreyCoulaud/glueforward> (Python, AGPL-3.0). Il interroge l'**API control de gluetun** (`GLUETUN_URL`, `GLUETUN_API_KEY`) pour le port forwarded et l'applique à un service cible (abstraction `SERVICE_TYPE`, actuellement qBittorrent). **C'est gluetun, pas glueforward, qui fait le NAT-PMP.**
- **On contribue un service `amule`** : il applique le port à aMule en **TCP et UDP au même numéro** (via EC si le réglage du port d'écoute y est supporté, sinon écriture d'`amule.conf` + reload/restart d'amuled). Config par variables d'env, comme les autres services.

### 15.3 Outils opérationnels (ergonomie non-dev)
- **Fusion** : conçue pour être **triviale et utilisable par un non-dev** — une commande (ex. `docker compose run --rm merge <autres catalog.db…>`), **idempotente** (UNION par clés de contenu, cf. §11). *(Le moteur de fusion complet est un sous-projet ultérieur, mais l'ergonomie est un objectif de conception dès maintenant — la propreté append-only/adressée-contenu existe pour ça.)*
- **`rebuild-local`** : re-dérive `local.db` depuis `catalog.db` + la file réelle d'aMule (réconciliation EC) en **une commande**.

## 16. Stratégie de tests — TDD

- **TDD strict** : les **tests sont la spec d'une feature**. **Aucun code de prod écrit avant les tests.** La **revue de code porte d'abord sur l'exactitude des tests** — ce sont eux qui décident si le code est bon.
- **Quatre niveaux combinés** :
  - **Unitaires (domaine pur)** : moteur de matching = joyau → cycles/DAG, profondeur, interpolation, seuils `coverage`/`fuzz`, ordre des règles, **déterminisme** du palier ; property-based (« une règle plus prioritaire ne baisse jamais le palier », « config acyclique termine »). **Corpus golden** de noms (réels + forgés : accents, encodages, quasi-collisions, leurres, ép. 62A) → palier/cible attendus, extensible par la communauté.
  - **Intégration** : contrats de ports (fake + adaptateur réel) ; **EC** = fixtures de trames (rapide) + intégration opt-in vs `amuled` réel → **mesure empirique** de la richesse des champs (dé-risque l'unique gros inconnu).
  - **DB** : sur **SQLite réel** — schéma/migrations, append-only, **claim atomique + lease/reclaim** de la file, idempotence, fusion (UNION par clés).
  - **End-to-end** : `docker compose` contrôlé ; option serveur eD2k local + fichier planté (lourd, opt-in).
- **Déterminisme** : shuffle seedé (`node_id`), `Clock`/random injectables → zéro flakiness.
- **Sécurité** : sandbox de l'enfant (`net=none`, rlimits, timeout-kill), dead-letter sur poison, parse défensif de la sortie de l'enfant.
- **Coverage validé sans ambiguïté** : `coverage.py` via `pytest-cov`, **seuil minimal imposé** (statement + branch ; le build/CI **échoue sous le seuil**) → preuve objective que le TDD est suivi.

## 17. Questions ouvertes / différé

- Rétention/compaction du catalogue (défaut : tout garder).
- Checks de vérif réels (`type_sniff`/`ffprobe`/`clamav`) — NO-OP en MVP.
- Durcissement gVisor/nsjail (opt-in Linux).
- Export/fusion multi-chercheurs (sous-projet).
- Hub central / option C (push, identités de bots).
- UI web d'admin (déprioritisée).
- FIFO vs LIFO de la file (trivial).
