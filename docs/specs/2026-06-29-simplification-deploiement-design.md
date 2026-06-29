# Spec — Simplification du déploiement

> Date : 2026-06-29 · Statut : design validé (Discuss), à transformer en plan.
> Origine : un essai de déploiement réel (machine de dev de Geoffrey) a rendu tangible que le
> parcours opérateur est trop complexe pour la cible « moyennement technique » visée par le runbook.

## 1. Problème

Déployer un nœud demande aujourd'hui un parcours d'obstacles disproportionné par rapport à
l'opération (lancer une stack Docker). Six points de friction, constatés sur le terrain :

1. `.env.example` vit à la **racine** du repo, pas dans `deploy/` où se fait le déploiement.
2. `deploy/examples/` éparpille **trois** points d'entrée (`gluetun.yaml`, `sans-vpn-lowid.yaml`,
   `sans-vpn-highid.yaml`) qu'il faut choisir.
3. Les fichiers compose ne suivent **pas** une convention de nommage homogène
   (`compose.base.yaml`, `examples/gluetun.yaml`, …) ⇒ ni Docker (sans `-f`) ni la validation des
   éditeurs ne les reconnaissent comme compose.
4. La configuration impose des **aller-retours yaml ↔ env** et une **double, voire triple saisie
   du même secret** : `AMULE_EC_PASSWORD` dans `.env` (pour amuled), puis `amules[].password`
   **et** `download_endpoint.password` dans le YAML crawler. C'est exactement ce qui a fait échouer
   l'essai (`change-me` oublié dans `download_endpoint`).
5. Le runbook de déploiement (382 lignes) est **trop verbeux et trop « dev »** pour une opération
   simple.
6. Les commentaires dans les YAML sont **pédagogiques au point d'être du bruit** (un commentaire par
   ligne).

Cause racine du point 4 : l'invariant **spec §3 « la config crawler n'utilise AUCUNE variable
d'environnement »**. Tant qu'il tient, un secret partagé entre amuled (env) et le crawler (yaml)
doit être saisi deux fois, et les fichiers de config doivent être copiés depuis des modèles
`*.example.yaml` puis édités à la main.

## 2. Objectifs / non-objectifs

**Objectifs.**

- L'opérateur saisit ses **secrets une seule fois**, dans un **seul** `deploy/.env`.
- **Plus aucune copie** de fichier `*.example` ni de double-saisie de secret.
- Fichiers compose **reconnus et validables** (nommage homogène).
- Un **point d'entrée par stack**, lancé d'**une** commande.
- Runbook **court** (quickstart) ; le détail relégué aux runbooks d'administration / dépannage
  existants.
- YAML **épurés** et lisibles.

**Non-objectifs.**

- Réduire le nombre de stacks supportées (on **garde** le sans-VPN : tout le monde ne veut pas d'un
  VPN, et le risque légal d'un crawl eMule pour du lost media en 2026 est jugé faible).
- Toucher au domaine pur, à la couverture 100 % branch, ou aux frontières de packages.
- Un installeur « clic-bouton » Windows (chantier distinct, hors scope).

## 3. Décisions de design

- **D1 — Interpolation `${NAME}`, paresseuse et par sous-chaîne.** La config crawler référence des
  variables d'environnement via le motif `${NAME}` (`NAME` = `[A-Za-z_][A-Za-z0-9_]*`), substituable
  **n'importe où dans une chaîne** — `url: "discord://${WEBHOOK_ID}/${WEBHOOK_TOKEN}"` est valide :
  on garde l'ID et le token en secret, le reste de l'URL en clair. La substitution est
  **paresseuse** : appliquée à la **consommation** de chaque chaîne par le parseur, si bien qu'une
  section que `enabled: false` rend inerte n'est **jamais** interpolée et ses variables n'ont pas à
  exister. **Fail-fast strict** : un `${NAME}` consommé dont la variable manque ⇒ `ConfigError`
  nommant la variable et le champ. Pas de syntaxe de défaut/optionnel, pas d'échappement `$$`
  (aucune valeur littérale ne contient `${…}`).
  *Dérogation YAGNI assumée* : la paresse est un petit surcoût immédiat (interpoler à la lecture de
  chaque champ plutôt qu'en un bloc) qui débloque les itérations futures — une section optionnelle à
  secret propre — sans rouvrir le design.
- **D2 — Frontière secrets / config.** **Secrets → `.env`** (référencés par `${VAR}` dans le yaml).
  **Config (flags, modes, chemins, URLs internes) → yaml**, en clair, éditable directement.
- **D3 — `${VAR}` est de l'I/O.** L'interpolation lit l'environnement : elle vit dans
  `adapters/config/` (pas dans le domaine). **L'invariant « domaine pur » est intact** ; seul
  l'invariant §3 « aucune variable d'environnement » est révisé en « interpolation `${VAR}` dans
  l'adapter de config ».
- **D4 — Config crawler unifiée.** `crawler.yaml` (politique) et `local.yaml` (câblage + secrets)
  fusionnent en un **seul `crawler.yml` versionné**. Les préoccupations download / vérification /
  port-sync deviennent des **sections cohérentes** mêlant politique et câblage.
- **D5 — Sections optionnelles à flag `enabled`.** Les sections `download` et `port_sync` portent un
  champ `enabled` **en dur** (config, pas secret). Le flag pilote la **validation structurelle** de
  la section (`download.enabled: true` ⇒ `endpoint` + `staging_dir` + `quarantine_dir` +
  `verifier_url` requis ; `port_sync.enabled: true` ⇒ ses URLs requises). Le **mode** n'est plus
  déduit de la présence de `verifier_url` mais de `download.enabled`.
- **D6 — Le mode va de pair avec le profil compose.** `download.enabled: true` (yaml) se conjugue
  avec `--profile download` (qui démarre `verifier` + `freshclam`). Une **mauvaise paire n'est
  jamais silencieuse** : `enabled` sans le profil ⇒ le health-check verifier échoue, refus de
  démarrer avec message clair (fail-fast déjà en place) ; le profil sans `enabled` ⇒ verifier tourne
  pour rien, inoffensif.
- **D7 — Compose par `include:`.** Un point d'entrée **par stack** (`gluetun.compose.yml`,
  `direct.compose.yml`) qui `include:` un fragment commun `base.compose.yml`. Lancement d'**un seul
  `-f`** ; `docker compose -f <stack>.compose.yml config` rend la stack complète explicite et
  validable. Nommage **`*.compose.yml`**.
- **D8 — Service `crawler` unique.** Remplace `crawler-observer` + `crawler-download`. Sans profil
  (toujours actif), monte l'unique `crawler.yml`. Profils réservés aux satellites : `download`
  (verifier + freshclam), `webui`, `monitoring` (prometheus + grafana).
- **D9 — Chemin par défaut = `direct` + observer (Low-ID).** C'est le démarrage le plus simple, et
  il fonctionne pleinement (recherche, catalogage, téléchargement). Le **High-ID** (plus de pairs
  disponibles ⇒ meilleure recherche et meilleur téléchargement) est une **option recommandée mais
  non obligatoire**, par ouverture d'un port (reste `direct`, IP exposée) **ou** VPN à port
  forwarding (masque l'IP en prime). Le runbook présente le défaut ainsi, avec une mention honnête
  « ton IP est visible des pairs » en `direct`.

## 4. Modèle cible

### 4.1 Arborescence `deploy/`

```
deploy/
  .env.example              # rapatrié de la racine ; SECRETS uniquement (+ vars services tiers)
  base.compose.yml          # FRAGMENT app (inclus, jamais lancé seul)
  gluetun.compose.yml       # include base + gluetun + amuled(netns) + docker-proxy
  direct.compose.yml        # include base + amuled(réseau ec direct)
  config/
    crawler/
      crawler.yml            # fusion politique + câblage + secrets ${…}
      matcher.yml            # inchangé (partagé avec la webui)
      targets.yml            # inchangé (partagé avec la webui)
    verifier.yml
    prometheus.yml
    grafana/…
```

Supprimés : `/.env.example` (racine), `deploy/compose.base.yaml`, tout `deploy/examples/`,
`deploy/config/crawler/{observer,download}.example.yaml`, l'ancien
`deploy/config/crawler/crawler.yaml` + `local.yaml`, les `*.example.yaml` résiduels.

### 4.2 `.env` (secrets + vars des services tiers)

Ne contient plus **aucun flag applicatif crawler**. Seulement :

- secrets : `AMULE_EC_PASSWORD`, `WIREGUARD_PRIVATE_KEY` (stacks VPN), `GRAFANA_PWD` (si monitoring),
  et tout webhook de notification ;
- vars consommées par des **services tiers** qui ne lisent pas notre yaml : gluetun
  (`SERVER_COUNTRIES`, `VPN_SERVICE_PROVIDER`, `VPN_PORT_FORWARDING`), compose (`IMAGE_TAG`,
  `GRAFANA_PORT`, `LISTEN_PORT`).

### 4.3 `crawler.yml` unifié (esquisse)

```yaml
# Config du crawler — versionnée. Secrets via ${VAR} (depuis .env), reste en clair.

cycle_interval_seconds: 300.0
search_poll_budget_seconds: 30.0
search_poll_interval_seconds: 5.0
keyword_pause_min_seconds: 1.0
keyword_pause_max_seconds: 4.0
decision_poll_interval_seconds: 5.0
shutdown_deadline_seconds: 10.0

backoff:
  base_seconds: 2.0
  cap_seconds: 300.0
  factor: 2.0
  jitter_ratio: 0.3

amules:
  - name: amule-1
    host: amuled
    port: 4712
    password: ${AMULE_EC_PASSWORD}

catalog_db_path: /data/catalog/catalog.db
local_db_path: /data/local/local.db

observability:
  log_level: INFO
  metrics: { enabled: true, port: 9090 }
  notification_timeout_seconds: 5.0
  # notifications:                       # optionnel ; ${…} en sous-chaîne (ID/token secrets)
  #   - url: "discord://${DISCORD_WEBHOOK_ID}/${DISCORD_WEBHOOK_TOKEN}"
  #     tag: operations

# Mode download — va de pair avec `--profile download`.
download:
  enabled: false
  poll_interval_seconds: 30.0
  disk_cap_bytes: 53687091200
  endpoint:
    name: amule-dl
    host: amuled
    port: 4712
    password: ${AMULE_EC_PASSWORD}
  staging_dir: /data/quarantine
  quarantine_dir: /data/quarantine
  verifier_url: http://verifier:8000
  verify:
    poll_interval_seconds: 10.0
    client_timeout_seconds: 180.0

# Port-sync High-ID — stack gluetun uniquement.
port_sync:
  enabled: false
  poll_interval_seconds: 60.0
  restart_min_interval_seconds: 300.0
  gluetun_control_url: http://gluetun:8000
  restarter_url: http://docker-proxy:2375
```

`verify` est imbriqué sous `download` (la vérification n'a de sens qu'en mode download). Le détail de
l'arbre exact est laissé au plan ; les principes (sections cohérentes, `enabled` en tête) priment.

### 4.4 Lancement

Deux points de référence, tous deux **sans VPN** (le plus simple à démarrer) :

```bash
# (a) Observer — sans VPN. Démarrage minimal : catalogue seul.
docker compose -f deploy/direct.compose.yml up -d

# (b) Complet — sans VPN. Download + vérif + monitoring + webui.
#     Prérequis : download.enabled: true dans crawler.yml.
docker compose -f deploy/direct.compose.yml \
  --profile download --profile monitoring --profile webui up -d
```

**High-ID — recommandé, pas obligatoire.** Un nœud `direct` tourne par défaut en **Low-ID** :
recherche, catalogage et téléchargement fonctionnent, mais le nœud atteint **moins de pairs**. Pour
un **High-ID** (plus de pairs ⇒ meilleure recherche et meilleur téléchargement), deux voies au
choix, aucune obligatoire :

- **ouvrir un port** sur sa box (NAT → `LISTEN_PORT`), en restant `direct` — simple, mais l'IP
  domestique est exposée aux pairs ;
- **passer par le VPN** avec port forwarding (`gluetun.compose.yml` + `port_sync.enabled: true`) —
  masque l'IP *et* obtient le High-ID automatiquement.

## 5. Impact code (`packages/crawler`)

- **Interpolation à la consommation** dans `adapters/config/` : le helper de lecture de chaîne du
  parseur fusionné (type `_require_str`) substitue `${NAME}` (sous-chaîne, fail-fast) sur **chaque
  chaîne lue** ; l'environnement est **injecté** (mapping passé au parseur) pour la testabilité. La
  paresse est gratuite : le parseur ne descend pas dans une section `enabled: false`, donc ses
  chaînes ne sont jamais interpolées. S'applique à la **config crawler** uniquement — `matcher.yml`
  (regex RE2, qui contiennent `$`) et `targets.yml` ne sont pas touchés.
- **Fusion des parseurs** `parse_crawler_config` + `parse_local_config` → un parseur d'un seul dict,
  réorganisé en sections (`download` englobe désormais endpoint/staging/quarantine/verifier_url +
  sous-section `verify` ; `port_sync` englobe ses URLs). Le flag `enabled` remplace la détection
  implicite.
- **Composition (`app.py`)** : `_require_full_config` et `_require_port_sync_config` se réécrivent
  autour de `download.enabled` / `port_sync.enabled` (la règle « 3 réglages solidaires » disparaît
  au profit de « enabled ⇒ champs requis »). Le déclencheur du mode full passe de
  `verifier_url is not None` à `download.enabled`.
- **CLI (`__main__.py`)** : `--crawler` + `--local` fusionnent en un seul `--config` (défaut
  `deploy/config/crawler/crawler.yml`) ; `--targets` / `--matcher` inchangés. `validate-config`
  reste, et **valide désormais aussi la présence des variables d'env référencées** (effet de bord
  bienvenu de l'interpolation eager).
- **Tests** : config (interpolation : variable présente / absente / valeur scalaire ; fusion ;
  `enabled` des deux côtés), composition (mode via `enabled`), CLI. Couverture **100 % branch**
  maintenue.

## 6. Impact documentation

- `docs/runbooks/deployment.md` — **réécriture quickstart** (matrice stack courte → `.env` → flags →
  une commande), chemin par défaut `direct` + observer. Détail relégué.
- `docs/runbooks/administration.md` & `troubleshooting.md` — références de fichiers/chemins, et
  activation port-sync (désormais `port_sync.enabled`).
- `docs/specs/2026-06-10-crawler-mvp-design.md` §3 — révision de l'invariant « aucune variable
  d'environnement ».
- `docs/specs/2026-06-20-deploiement-exemples-design.md` — mise à jour vers la structure `include:`.
- `CLAUDE.md` — invariant § confinement/§3, « Two run modes » (observer = `download.enabled: false`),
  chemins `deploy/`, commandes.
- `docs/testing-guide.md` — si le `compose_integration` / smoke référence des noms de fichiers.

## 7. Style YAML (appliqué à tout fichier retouché)

- En-tête court par fichier ; commentaire **seulement** là où c'est non-évident.
- **80 colonnes max** par ligne, commentaires compris.
- Lignes vides pour aérer : entre grandes sections, autour des blocs de commentaires.

## 8. Invariants préservés

- Domaine pur (interpolation cantonnée à l'adapter de config).
- 100 % branch coverage par package ; `mypy --strict` ; `ruff` ; `sqlfluff`.
- Frontières de packages (le crawler n'importe pas le verifier, etc.).
- Le sujet du catalogue reste **le fichier, jamais la personne**.
- Confinement conteneur inchangé (la baseline `cap_drop: ALL` / `read_only` / `no-new-privileges`).

## 9. Risques & points ouverts

- **Interpolation paresseuse — propriété, plus un risque.** Une section `enabled: false` n'est pas
  lue, donc ses `${NAME}` ne sont jamais exigés : un secret exclusif à une section à flag n'est
  requis que si la section est active (cf. D1). C'est désormais garanti par construction.
- **`$` littéral dans `matcher.yml`.** Les regex RE2 utilisent `$` (ancre de fin). L'interpolation
  ne touchant **que** la config crawler, ces `$` sont saufs — et de toute façon le motif strict
  `${NAME}` n'attrape pas un `$` isolé.
- **Smoke stack** (`tests/smoke/`) à réaligner sur le nouveau modèle (include, config fusionnée,
  nommage) — fait partie du chantier, pas un oubli.
- **Cohérence d'extension** (`.yml` vs `.yaml`) : les compose passent en `*.compose.yml` (requis) ;
  on aligne les configs crawler en `.yml` puisqu'on y touche. Reste mineur pour verifier/prometheus.
- **Migration opérateur existant** : un `local.yaml` déjà déployé n'est plus lu. Documenter la
  bascule dans le runbook (rare : pas d'utilisateur en prod à ce jour).

## 10. Découpage indicatif pour le plan

1. **Cœur config** : interpolateur `${VAR}` + fusion des parseurs + `enabled` + tests.
2. **Composition + CLI** : mode via `enabled`, `--config`, `validate-config` + tests.
3. **`deploy/`** : `.env.example` rapatrié, `base/gluetun/direct.compose.yml` via `include:`, service
   `crawler` unique, profils, `crawler.yml` fusionné, épuration YAML.
4. **Smoke** : réalignement `tests/smoke/`.
5. **Docs** : runbook quickstart + administration/troubleshooting + specs + `CLAUDE.md`.

Lots 1→2 séquentiels (2 dépend de 1) ; 3 dépend de 1 ; 4 dépend de 3 ; 5 en parallèle de 3-4.
