# Handoff — emule-indexer (CHECKPOINT intermédiaire : post-Plan E — état + backlog catalogué)

> **Nature de ce handoff** : ce n'est PAS un nouveau build. C'est un **checkpoint** qui fige l'état
> du projet après le Plan E (observabilité) et **catalogue tout ce qui a été mentionné mais pas
> encore fait** au fil des sessions, avec quelques **cadrages révisés** par Geoffrey (§3). À lire en
> complément des handoffs de build (les `2026-06-15 - handoff - observabilite E{1,2,3}*.md` pour le
> détail du dernier plan). Snapshot — non maintenu vivant ; à re-trancher au prochain chantier.

## 1. TL;DR — où on en est

Le pipeline **bout-en-bout existe et est déployable** : Plans **A** (data-model) → **B** (EC adapter)
→ **C** (orchestration / crawl loop) → **D** (auto-download + verify + D-analysis = vrai verifier
confiné) → **F** (packaging : 2 images Docker + compose observer/full + smoke e2e + CI GHCR) →
**E** (observabilité : logs + métriques Prometheus + notifications apprise). Jalon le plus récent :
**`v0.11.0-observability`** (annoté, **non poussé**). Gate intégral **vert** (crawler 733 + verifier
113 tests, **100 % branch** les deux ; `mypy --strict` / `ruff` / `sqlfluff`).

**Ce qui n'a JAMAIS tourné en réel** : la stack assemblée derrière VPN sur un vrai réseau eD2k/Kad
(le smoke valide le câblage sans VPN et sans vrai serveur ; aucun download→verify réel n'a été
exercé depuis le staging amuled). Voir §4 (Testing) et §3.

## 2. État vérifiable

```bash
( cd packages/crawler  && uv run pytest -q )   # 733 passed, 100% branch
( cd packages/verifier && uv run pytest -q )   # 113 passed, 100% branch
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run sqlfluff lint packages/crawler/src
git tag --list | grep -E "v0\.(10|11)"         # v0.10.0-packaging, v0.11.0-observability
```
Tests d'intégration **désélectionnés par défaut** (Docker/ffmpeg requis), à lancer à la main :
`-m ec_integration|orchestration_integration|download_integration|compose_integration` (Docker) ;
`-m verify_integration|analysis_integration` (sans Docker, ffmpeg pour analysis). **NB** : ces
marqueurs Docker **ne tournent pas dans le bac à sable de session** (pas de `CAP_NET_ADMIN` → pas de
veth) — à exécuter dans un shell normal.

## 3. Cadrages RÉVISÉS (décisions/corrections de Geoffrey à ce checkpoint)

1. **VPN : déspécifier ProtonVPN.** Le projet n'a PAS besoin de ProtonVPN en particulier. Le vrai
   besoin = **un provider VPN qui accepte la redirection de port** (gluetun est déjà générique).
   **Alternatives valables** : tourner en **Low-ID** (sans port forward), ou **ouvrir un port chez
   soi**. → Tâche doc : retirer les mentions « ProtonVPN » comme s'il était requis dans les specs, le
   runbook, `.env.example`, et reformuler en « provider avec port forwarding (ou Low-ID / port
   ouvert) ». (Mémoire projet déjà corrigée à ce checkpoint.)
2. **Runbook de déploiement à réécrire pour un public moyennement technique.** Trop de jargon de
   second plan ; viser un déroulé accessible (prérequis simples, étapes numérotées, glossaire minimal,
   ce qu'on peut ignorer). → Tâche doc dédiée.
3. **WebUI légère = backlog basse prio (PAS abandonnée).** La spec MVP la classait « dépriorisée /
   hors-scope MVP » ; à ce checkpoint elle redevient une **fonctionnalité possible basse priorité**
   (petite UI de consultation du catalogue / état), pas un abandon définitif.
4. **e2e sans homelab = chantier de testing prioritaire** (voir §4) : monter un **conteneur
   WireGuard** (valider la chaîne VPN de bout en bout) **+ un serveur eD2k de test** (vraie recherche
   / observation / download). Objectif explicite : **suite e2e complète** qui permet d'**accueillir
   des contributions externes** sereinement (un contributeur reproduit la suite sans matériel réel).

## 4. Backlog catalogué

Criticité : 🔴 conditionne le fonctionnement réel · 🟡 amélioration nette · ⚪ mineur/cosmétique.
(Tout ce qui était « reporté au Plan A/B/C/D/E/F » et désormais FAIT a été filtré — verdict réel
ffprobe, câblage full-mode, observabilité, alerte `malicious`, etc. sont livrés.)

### 4a. Fonctionnalités
- 🔴 **`server.met` / `nodes.dat` (amorçage serveurs eD2k + bootstrap Kad)** — *à VÉRIFIER en
  premier*. Sans liste de serveurs ni bootstrap Kad, amuled ne se connecte à rien → le crawler ne
  voit rien. Le handoff packaging ne dit pas si l'image `ngosang/amule` les fournit/rafraîchit.
  (orchestration §2 : « le déploiement Plan F fournit ces fichiers » — à confirmer/implémenter.)
- 🔴 **port-sync / High-ID** — lire le port forwardé par gluetun → le poser en EC (`set_listen_port`),
  repli `amule.conf`. Remplace glueforward (abandonné). Full mode tourne en **Low-ID** tant que pas
  fait. Inconnu empirique : l'EC règle-t-il le port à chaud ? (packaging §5, ×3 handoffs.)
- 🟡 **Upgrades** (re-DL d'une meilleure version pour `partial`/`poor`) — *débloqué* : exigeait les
  métadonnées média post-download, désormais fournies par le vrai `ffprobe` (D-analysis).
- 🟡 **Sous-commandes CLI** (`merge` / `rebuild-local` / `validate-config`) — ergonomie ; `merge` est
  le pont vers la fusion multi-nœuds. (MVP §15.3, packaging §1.)
- 🟡 **Fusion / export multi-chercheurs** — merge des `catalog.db` (schéma déjà UNION-safe ; mécanique
  non écrite). Cœur du « réseau distribué de chercheurs ». (MVP §2/§17, data-model §2.)
- 🟡 **WebUI légère** (basse prio, cf. §3.3) — consultation du catalogue / état du crawler.
- ⚪ **Hub central** (Postgres, push, agrégation multi-nœuds) — phase ultérieure (après le merge
  décentralisé).
- ⚪ **Rétention / compaction** du `catalog.db` (défaut = tout garder ; volume Keroro modeste).
- ⚪ **`file_verifications` dedup** (doublons at-least-once) — lié à la future surface d'export/lecture.

### 4b. Packaging / durcissement
- 🟡 **clamav** (2ᵉ source `malicious` par signatures) — follow-up « obligatoire » ; slot réservé dans
  `pipeline.run` (`elif name == "clamav"`). **Tension réseau** : `freshclam` exige un egress vs
  verifier `internal: true` → réflexion réseau (sidecar updater ? volume de signatures monté ?).
- 🟡 **Ring noyau par-enfant** (`net=none` ns / seccomp / bwrap-nsjail / RO mounts / tmpfs réel dans
  `spawn.py`) — le ring **conteneur** est livré (non-root + `cap_drop`/`no-new-privileges`/`read_only`,
  gVisor opt-in) ; manque l'isolation **par enfant d'analyse** dans le code verifier.
- ⚪ **Durcissement 2ᵉ `communicate()`** (timeout si un petit-fils échappe au `killpg`) — hors modèle
  de menace actuel.
- ⚪ **Visibilité GHCR** (packages privés par défaut → publics, ou `docker login` PAT `read:packages`).
- ⚪ **Quota disque infra** des volumes nommés (vs plafond applicatif `disk_cap_bytes`).
- ⚪ **`mem_limit` legacy → `deploy.resources.limits`** (uniformiser v2→v3+).
- ⚪ **Double-build du smoke en CI** (nom de projet Compose différent — cosmétique).

### 4c. Testing renforcé
- 🟡 **Suite e2e « réseau complet » sans homelab** (cf. §3.4) : conteneur **WireGuard** (chaîne VPN
  end-to-end) + **serveur eD2k de test** + fichier planté → vraie recherche/observation/download/verify.
  *Bénéfice clé* : dérisque les **contributions externes** (suite reproductible sans matériel).
  *Inconnus* : (i) trouver une image de serveur eD2k viable en 2026 (eserver/lugdunum sont anciens —
  à sourcer ou bâtir) ; (ii) WireGuard en conteneur exige `CAP_NET_ADMIN` sur le runner (OK en CI
  réelle, pas dans le sandbox de session) ; (iii) comment partager le fichier planté pour qu'amuled le
  trouve via le serveur de test. Remplace/élargit l'ancienne « MVP §16 option lourde, non retenue ».
- 🔴 **Validation `download → verify` COMPLET en réel** — jamais exercé : l'e2e pré-place le fichier en
  quarantaine, il **n'exerce pas `os.replace` depuis le staging amuled réel** (`resolve_staging_path`,
  D2/DV10). La suite e2e ci-dessus *peut* couvrir ça si le serveur de test sert un vrai fichier.
- 🟡 **Couverture d'arrêt en intégration** (T12) — pas de tâche en fuite après shutdown
  (`asyncio.all_tasks`), guard `if not task.done()`, test de mutation. Promis en D-verify, jamais
  ajouté.
- ⚪ **Mesure empirique richesse EC** (tags `raw` sur un vrai amuled → compléter le rapport richesse).

### 4d. Autre (dette / à surveiller / raffinements)
- 📄 **Déspécifier ProtonVPN** dans specs + runbook + `.env.example` (cf. §3.1).
- 📄 **Réécrire le runbook** pour un public moyennement technique (cf. §3.2).
- 🟡 **Tension DV6/DV7** (tolérance verifier : health-gate dans la boucle vs retry-via-lease) —
  *devenu pertinent* maintenant que le verifier fait du vrai travail : distinguer « service down
  transitoire » de « tâche poison déterministe » (dead-letter).
- 🟡 **Granularité d'erreur par-étape** dans `run_download_cycle` (try/except séparés
  `_handle_completions` / `_queue_new_candidates`) — famine théorique (I2).
- ⚪ **I1** : ré-émission `add_link` (flag `SENT`) — seulement si un partfile sourceless est observé.
- ⚪ **Observability follow-ups** (spec E §12) : gauge `emule_quarantine_bytes`, routage apprise > 2
  audiences, persistance de l'état edge-trigger, queue async pour notifs si latence.
- ⚪ **`MatchingEngine.evaluate`** O(cibles × règles) sans entonnoir — à revisiter si le catalogue
  explose (OK pour Keroro).
- ⚪ **Anti-rate-limit eD2k / fraîcheur liste serveurs / bootstrap Kad fiable** — question ouverte du
  knowledge brief (liée à `server.met`/`nodes.dat`).
- ⚪ **Blocage event loop verifier** si requêtes concurrentes (→ threadpool) — acceptable en
  mono-requête.

### 4e. Hors-scope VERROUILLÉ (décisions conscientes — pas des oublis)
Seeding (invariant dur désactivé), **G2/Gnutella**, **MLDonkey**, **Windows** (verifier conteneurisé
Linux), **autres lost media**, **promotion humaine** (tâche humaine assumée),
**OpenTelemetry/tracing** (YAGNI), **multi-instance verifier**. *(La WebUI n'est plus ici — voir
§3.3 / §4a.)*

## 5. Prochaine étape recommandée

Avant tout nouveau code : **brainstormer** (hard gate design du projet). Ordre suggéré, du plus
« est-ce que ça marche vraiment » au confort :
1. **Vérifier `server.met`/`nodes.dat`** (🔴) — sans ça rien ne se connecte ; trancher provisioning.
2. **Suite e2e réseau-complet** (§3.4 / §4c) — débloque la confiance ET les contributions externes ;
   couvre aussi la validation download→verify réelle.
3. **port-sync / High-ID** (🔴) — passe le full mode en High-ID.
4. **clamav** (durcissement) + **ring noyau par-enfant**.
5. **Doc** : déspécifier ProtonVPN + réécrire le runbook (rapide, fort impact onboarding).
