# Spec — emule-indexer : D-verify (pipeline de vérification, NO-OP)

> **Sous-projet** : deuxième des trois issus de la scission de « Plan D ». Ordre :
> **D-download** (capacité de téléchargement, spec `2026-06-13-download-orchestration-design.md`)
> → **D-verify** (CE document : le pipeline de vérification full-mode bout-en-bout avec un
> verifier **NO-OP**) → **D-analysis** (le confinement réel + les vrais checks
> type_sniff/ffprobe/clamav qui remplissent `real_meta`).
>
> Réfs : MVP design `2026-06-10-crawler-mvp-design.md` §3 (modes/activation), §4 (archi),
> §10 (sécurité/confinement/vérif/relais), §11/§12 (modèle de données/file). Orchestration
> `2026-06-12-orchestration-design.md` (patterns réutilisés). D-download (le producteur des
> tâches de vérification + de la quarantaine).

---

## 1. But & périmètre

**But** : fermer la boucle full-mode de bout en bout — consommer la **file de vérification**
produite par D-download, appeler le **service verifier** (déployable séparé) par RPC,
enregistrer le résultat dans `file_verifications`, et **activer le mode full** (`VERIFIER_URL`
+ health-check fail-fast) en câblant **les deux boucles** (download + vérification) vivantes
dans le `CrawlerApp`. Le verifier est **trivial/NO-OP** : il renvoie `unverified` sans rien
exécuter sur les octets — il prouve toute la plomberie, le **vrai** travail d'analyse
(confinement + checks) est D-analysis.

**Dans le périmètre** :
- **Restructuration en workspace uv** (racine pure + `packages/crawler` + `packages/verifier`)
  — premier task, gate vert avant toute autre chose.
- Le **service verifier** (paquet `download_verifier`) : app Starlette (`POST /verify`,
  `GET /health`), logique NO-OP séparée (`check.py`), entrée uvicorn (`__main__`).
- Côté crawler : port `ContentVerifier` + adapter `HttpContentVerifier` (httpx, parsing
  défensif) ; `CatalogRepository.record_verification` ; la boucle `run_verification_cycle`.
- Le **gate mode full** (`VERIFIER_URL` + health-check fail-fast) et le **câblage LIVE** des
  deux boucles (download de D-download + vérification) dans `CrawlerApp`.

**Hors périmètre** :
- **D-analysis** : le confinement réel (enfant jetable `net=none`/rlimits/timeout/RO/non-root)
  et les **vrais checks** (type_sniff/ffprobe/clamav) qui remplissent `real_meta`/donnent un
  verdict réel. Ici le verifier renvoie `unverified`, `real_meta={}`, `checks=[]`.
- **Plan F (packaging)** : l'image Docker du verifier, le réseau `internal: true`, le compose,
  gVisor/nsjail. Ici le service tourne en local/dev (uvicorn) pour l'e2e.
- Les **upgrades**, le **quota disque infra** (déjà hors D-download).

## 2. Décisions verrouillées (issues du brainstorm)

1. **Verifier NO-OP trivial** : renvoie `unverified`, **aucune** machinerie d'enfant jetable
   (légère déviation assumée de « machinerie dormante » §10.4) ; le confinement vient avec les
   checks en D-analysis.
2. **Stack HTTP** : `httpx` (client RPC async, côté crawler), `starlette` + `uvicorn` (service
   verifier). API exactes figées via **context7** au plan. Tests via `httpx.ASGITransport`
   (in-process, sans socket).
3. **Workspace uv, racine pure, deux enfants** : `packages/crawler` (paquet `emule_indexer`,
   déplacé) + `packages/verifier` (paquet `download_verifier`, neuf). Outillage `ruff`/`mypy`/
   `pytest` **partagé racine** ; **100 % branch par-paquet** ; `sqlfluff` ne vise que le
   crawler ; `config/` runtime reste racine.
4. **Le verifier ne partage RIEN** avec le crawler que le **contrat de fil** (schéma JSON
   `/verify`) — DTO `VerificationResult` (crawler) et réponse (verifier) définis
   indépendamment, gardés en phase par l'e2e. Frontière de paquet = isolation imposée.
5. **La file de vérification EST le couplage** : le download enfile, la vérif `claim` à sa
   cadence (polling `verify.poll_interval`) — pas de nouveau nudge.
6. **Séparation transitoire vs mauvaise réponse** (voir §8).
7. **Gate full-mode** par `VERIFIER_URL` + health-check fail-fast au démarrage ; tolérance à
   chaud (voir §7).

## 3. Workspace & restructuration (premier task)

Racine = workspace pur (aucun code applicatif). Migration **one-time** (`git mv`), sans
changement de comportement, **gate vert** comme critère de fin de ce task :

```
emule-indexer/                         # racine = workspace
├── pyproject.toml                     # [tool.uv.workspace] members=["packages/*"] + outillage dev partagé
├── uv.lock                            # un seul lockfile
├── packages/
│   ├── crawler/                       # paquet emule_indexer (déplacé : src/ + tests/ + pyproject crawler + httpx)
│   └── verifier/                      # paquet download_verifier (neuf : starlette, uvicorn)
├── config/  docs/  scripts/  .github/  hook pre-push   # restent racine, adaptés au workspace
```

Le gate 5 checks tourne depuis la racine **sur les deux paquets** ; chacun atteint **100 %
branch**. La commande de gate (CLAUDE.md), la CI et le hook pre-push sont **adaptés au
workspace**. La config exacte (membres uv, collecte pytest + coverage des deux sources, mypy
sur les deux) sera figée via **context7** au plan.

## 4. Le service verifier (`packages/verifier`, NO-OP)

Paquet `download_verifier`, déployable séparé : **stateless, no-DB, no-domain, no-Internet**,
ne lit que `quarantine/<hash>` en RO.

```
src/download_verifier/
├── app.py      # Starlette : POST /verify, GET /health
├── check.py    # NO-OP : verify_file(quarantine_path, expected) -> VerificationResult(unverified, {}, [])
└── __main__.py # uvicorn (python -m download_verifier) — image/compose = Plan F
```

- `POST /verify {hash, expected}` → `{verdict, real_meta, checks}`. NO-OP : confirme
  l'existence de `quarantine/<hash>` (stat RO) → `{verdict:"unverified", real_meta:{}, checks:[]}` ;
  fichier absent → `{verdict:"error"}`. Schéma de requête validé (strict, taille bornée).
- `GET /health` → 200.
- `check.py` sépare la logique de la couche HTTP (unit-testable directement ; le NO-OP ignore
  `expected` et **ne touche pas les octets**). Le dossier racine de quarantaine vient de la
  config du service.

## 5. Ports & adapter (côté crawler)

- `ports/content_verifier.py` — `ContentVerifier` (Protocol async) : `verify(ed2k_hash,
  expected) -> VerificationResult` ; `health() -> bool`. `VerificationResult` (DTO frozen :
  `verdict: str`, `real_meta: Mapping[str, object]`, `checks: tuple[...]`).
- `adapters/verifier_http.py` — `HttpContentVerifier` (httpx `AsyncClient` sur `VERIFIER_URL`) :
  `POST /verify`, `GET /health`. **Parsing défensif** (§10.4) : corps borné, schéma strict ;
  réponse malformée/hors-schéma → `VerificationResult(verdict="error", …)` ; **erreur de
  connexion / timeout / 5xx → exception transitoire de port** (analogue à `MuleUnreachableError`,
  p. ex. `VerifierUnavailableError`) que la boucle attrape pour retry.
- `ports/catalog_repository.py` + `record_verification(ed2k_hash, verdict, real_meta, checks)`
  — table `file_verifications` (catalogue, **append-only**, mergeable, taguée `node_id`).

## 6. La boucle `run_verification_cycle` (application)

Une tâche concurrente de plus dans `CrawlerApp` (gated full-mode). Chaque itération :

1. `reclaim_expired()` (récupère les leases expirés au fil de l'eau + au démarrage).
2. `claim_verification()` → si `None`, dort `verify.poll_interval` (ou attend ; la file est le
   couplage) et reboucle.
3. Tâche claimée : assemble `expected` depuis la cible (`downloads.target_id` → `target` ;
   **minimal en NO-OP**, p. ex. `{target_id}`).
4. `content_verifier.verify(ed2k_hash, expected)` → `VerificationResult`.
5. `record_verification(hash, verdict, real_meta, checks)` (catalogue).
6. `complete_verification(task_id)`.

Writer unique sur l'event loop → aucun verrou. `Clock`/`sleep` injectés → déterminisme.

## 7. Gate full-mode + câblage live des deux boucles (composition)

- `VERIFIER_URL` **défini** → mode **full** ; **absent** → **observer** (les boucles download
  ET vérification restent OFF ; le crawler ne fait que rechercher/cataloguer, Plan C).
- Au démarrage en full : construit `HttpContentVerifier` → **`GET /health`** → **injoignable
  ⇒ fail-fast** (le crawler refuse de démarrer en full plutôt que télécharger sans vérif, §3).
- Câble dans le `TaskGroup` (à côté du cycle de recherche, repos uniques partagés) : la boucle
  de **download** (livrée par D-download) **+** la boucle de **vérification**. Construit aussi
  ici : le client EC download (endpoint config), la `Quarantine`, le repo `downloads`, le
  `ContentVerifier`.
- **Mi-parcours** : si le verifier tombe, la boucle de vérif **tolère** (RPC transitoire →
  retry via lease, les tâches s'empilent) et le download **continue** (back-pressure par le
  plafond disque) — **pas** de fail-fast à chaud (seulement au démarrage).
- **Arrêt** : les deux nouvelles boucles sont des tâches du `TaskGroup` → annulation au
  prochain `await`, jamais en pleine écriture DB, observable & borné comme le Plan C.

## 8. Gestion d'erreurs & résilience

- **Service indisponible** (connexion refusée / timeout / 5xx) → **transitoire** :
  `fail_verification(task_id)` (lease → retry) ; on **n'invente pas** de verdict. Après
  `max_attempts` → **dead-letter = signal de poison** (§12).
- **Réponse 200 malformée / hors-schéma / trop grosse** → parsing défensif → verdict `error`
  **enregistré** + `complete` (pas de boucle infinie sur une réponse déterministe mauvaise).
- **`record_verification` échoue** (`RepositoryError`) → `fail_verification` (retry) ; le
  verifier est idempotent/stateless → re-RPC sans dommage.
- **`reclaim_expired`** au démarrage + au fil de l'eau (un crash en plein traitement re-libère
  la tâche). Idempotence d'ensemble : la file est la vérité durable, le RPC la vivacité (§10.5).

## 9. Stratégie de tests (TDD, 100 % branch par-paquet)

**`packages/verifier`** : `check.py` (NO-OP → `unverified` ; fichier absent → `error`) +
l'app Starlette via **`httpx.ASGITransport`** in-process (`POST /verify` cas valides/invalides,
`GET /health`), schéma de requête (strict, borné). 100 % branch sur `download_verifier`.

**`packages/crawler`** : la boucle `run_verification_cycle` avec **faux `ContentVerifier`** +
**vrais** sqlite (file `local.db` + catalogue) sur `tmp_path` (claim→verify→record→complete ;
service indisponible→fail/retry ; malformé→verdict `error` ; dead-letter ; `reclaim_expired` ;
arrêt). Le gate full-mode (`CrawlerApp`) : observer (boucles OFF) / full (health-check ok →
boucles ON) / health-check injoignable → fail-fast / tolérance à chaud. 100 % branch.

**Test de contrat** : `HttpContentVerifier` testé **contre la vraie app Starlette via
ASGITransport** (in-process) → prouve le contrat de fil (DTO crawler ↔ réponse verifier) sans
socket ni mocks.

**E2e opt-in** (`verify_integration`, hors coverage, désélectionné par défaut) : la boucle de
vérif contre le **vrai service** lancé (ASGITransport ou uvicorn) — fichier **pré-placé** en
quarantaine + tâche enfilée → la boucle produit une ligne `file_verifications` `unverified`.
Prouve le RPC réel sans dépendre d'un vrai download (option A de D-download). Le download→verify
**complet** reste la **validation homelab manuelle** documentée.

## 10. Definition of Done

- Workspace uv (racine pure + `packages/crawler` + `packages/verifier`), gate vert sur les
  deux paquets, CI + hook + commande de gate adaptés.
- Service `download_verifier` (Starlette `POST /verify`/`GET /health`, `check.py` NO-OP,
  entrée uvicorn), 100 % branch.
- Port `ContentVerifier` + `HttpContentVerifier` (httpx, parsing défensif) + `record_verification`.
- Boucle `run_verification_cycle`.
- Gate full-mode (`VERIFIER_URL` + health-check fail-fast) + câblage live des **deux** boucles
  (download + vérif) dans `CrawlerApp` — la couture d'activation renvoyée par D-download.
- Config : `VERIFIER_URL` + endpoint download (local.example.yaml), `verify.poll_interval`
  (crawler.yaml).
- Gate 5 checks vert + e2e `verify_integration` vert (boucle de vérif ↔ vrai service).
- Deps ajoutées : `httpx` (crawler), `starlette`/`uvicorn` (verifier).
- **NON inclus** (D-analysis) : confinement réel, vrais checks, `real_meta` rempli.

## 11. Suite

**D-analysis** (sous-projet 3) : enfant jetable `net=none`/rlimits/timeout/RO/non-root,
pipeline de checks branchable (type_sniff → ffprobe → clamav, agrégation worst-status),
remplissage de `real_meta` (durée/bitrate/codec — le trou qu'EC ne comble pas), verdicts réels
(`clean`/`suspicious`/`malicious`). **Plan F** : images Docker (crawler + verifier), réseau
`internal: true`, compose, durcissement opt-in (gVisor/nsjail).
