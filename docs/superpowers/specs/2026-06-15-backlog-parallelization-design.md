# Plan d'orchestration — réduction parallèle du backlog (post-Plan E)

> **Nature** : ce doc est le **plan maître** qui orchestre l'exécution parallèle du backlog catalogué
> dans `docs/handoffs/2026-06-15 - handoff - post-E checkpoint (backlog).md`. Il définit les vagues,
> la partition anti-conflit des worktrees, le protocole de dispatch des agents, et la frontière
> d'intégration. Les **décisions de design** des tâches structurantes vivent dans des design docs
> dédiés (référencés en §5). À lire AVANT de dispatcher quoi que ce soit.

## 1. Objectif & principe

Réduire le backlog vite en **parallélisant l'exécution** dans des worktrees git séparés, sans
désalignement. Principe directeur retenu avec Geoffrey :

- **La réflexion est sérialisée sur un seul fil** (le co-design, déjà fait cette session) pour éviter
  le multitasking mental. **L'exécution est parallèle.**
- Les tâches n'ont pas le même besoin de planification : **spikes** (exploration), **simple/additif**
  (brief court, TDD direct), **structurant** (design approuvé avant code — fait).
- Un agent par worktree va **jusqu'au gate de code vert** (pytest 100 % branch + mypy + ruff +
  sqlfluff), **branche non mergée**. **Geoffrey + l'orchestrateur** intègrent ensuite (merge ordonné,
  tests Docker/réseau, docs transverses).

## 2. Le modèle en vagues

- **Vague 0 — spikes (FAIT)** : 5 investigations en lecture de source (aMule/gluetun/ngosang/ed2k)
  ont levé les inconnues. Résultats figés en §3. La source amont est clonée dans `vendor/` (gitignoré)
  pour que tout agent la lise en local via `Read`.
- **Co-design — structurantes (FAIT)** : clamav, ring noyau, fusion, port-sync, e2e — décisions
  figées dans les design docs (§5).
- **Vague 1 — exécution (À LANCER)** : worktrees parallèles (§5), chacun autonome jusqu'au gate de
  code. Voir le protocole de dispatch (§6).
- **Vague 2 — intégration (orchestrateur + Geoffrey)** : merge ordonné, résolution des fichiers
  transverses, marqueurs d'intégration Docker/réseau, checklist « réseau vivant » de Geoffrey. Voir
  §7.

## 3. Acquis des spikes (NE PAS re-investiguer — c'est tranché)

1. **`server.met` / `nodes.dat` : OK out-of-the-box** sur `ngosang/amule:3.0.0-1`. amuled télécharge
   silencieusement `nodes.dat` + `server.met` au 1er run (fix amont aMule 3.0.0 #558) ; `amule-config.sh`
   génère `amule.conf` avec `KadNodesUrl`/`Ed2kServersUrl` = `upd.emule-security.org`, `ConnectToKad=1`,
   `ECPort=4712`. → **rien à coder** ; reste : documenter le **pin `3.0.0-1`** (ne jamais dériver vers
   `latest`/`2.3.3-*`) + valider l'**egress-au-boot** (DNS+443 via VPN) en e2e + filet optionnel pré-seed.
2. **EC n'expose AUCUNE métadonnée média** sur les résultats de recherche (plafond = 8 tags non-média :
   `EC_TAG_SEARCHFILE` 0x0700 + enfants). Nuance eD2k/Kad nulle au niveau du résultat. La croyance
   fondatrice tient. Enrichissements doc mineurs suggérés (cf. tâche docs).
3. **Port d'écoute eD2k NON re-bindable à chaud** : le socket est bâti une seule fois dans
   `ReinitializeNetwork()` à `OnInit` (`vendor/amule/src/amule.cpp:664,873,963-964`). MAIS EC
   `SetPreferences` met à jour la pref en mémoire (`thePrefs::SetPort`,
   `vendor/amule/src/ECSpecialMuleTags.cpp:409-413`) et amuled sauve ses prefs au shutdown
   (`glob_prefs->Save()`, `amule.cpp:566`). → mécanisme port-sync = **EC `SetPort(N)` + restart amuled**
   (au shutdown il persiste N, au boot il bind N). Clés conf : `[eMule] Port=` / `UDPPort=`
   (`vendor/amule/src/Preferences.cpp:1064-1065`). L'image ngosang n'a **aucun env de port** (`Port=4662`
   codé en dur, `amule-config.sh:84-92`, généré seulement si absent).
4. **gluetun expose le port forwardé** via `GET http://gluetun:8000/v1/portforward` → `{"port":N}` (route
   renommée depuis v3.40 ; auth-by-default v3.40+). Traiter `port:0` comme « pas prêt ». Le port peut
   changer (renégo VPN) → **polling**. **PF gluetun limité à 4 providers** (`Protonvpn`, PIA, `Privatevpn`,
   `Perfectprivacy` — `vendor/gluetun/internal/configuration/settings/portforward.go:68-76`) : un **custom
   WireGuard n'a PAS de PF** → impossible de tester le PF réel sans impersonate un de ces mécanismes
   (Proton=NAT-PMP, PIA=API). Conséquence : on ne teste PAS le PF (c'est le job de gluetun, de confiance) ;
   on teste seulement « amuled écoute sur le port annoncé ».
5. **Serveur eD2k de test** : aucune image maintenue n'existe. Retenu : **stub Python maison** (chemin
   crawl déterministe, HighID forcé) en couche A ; **`gureedo/ed2kd`** (C, MIT, à builder, ~15 lignes
   Dockerfile + 3 ajustements) en couche B pour le download réel des octets. eNode (2013, pre-alpha) et
   eserver/Lugdunum (propriétaire, 2006) écartés. Pièges ed2kd : **HighID requis** pour source contactable
   (port-check réel), **DB en mémoire volatile** (le seeder amuled doit partager le fichier planté).

## 4. Partition anti-conflit & fichiers « intégration-owned »

Pour que des worktrees parallèles ne se télescopent pas, **certains fichiers transverses ne sont JAMAIS
édités par un agent de Vague 1** — les agents **proposent un delta dans leur rapport**, l'orchestrateur
l'applique en Vague 2 :

- `uv.lock`, `pyproject.toml` (racine + paquets) — toute dépendance ajoutée est **déclarée**, jamais
  lockée par l'agent.
- `compose.yaml`, `compose.*.yaml`, `.env.example` (structure compose) — clamav (sidecar+volume),
  port-sync (proxy+env gluetun), e2e (compose e2e) y touchent tous → **deltas proposés, mergés par
  l'orchestrateur** (sauf un fichier compose NOUVEAU et dédié, ex. `compose.e2e.yaml`, que l'agent e2e
  peut créer seul).
- `CLAUDE.md`, `docs/handoffs/` — mis à jour à l'intégration seulement.
- **Points d'entrée partagés du paquet crawler** : le module CLI principal et `composition/app.py` —
  plusieurs tâches y touchent → l'agent crée ses **nouveaux modules** et signale le câblage à faire ;
  l'orchestrateur tisse le câblage à l'intégration (ou l'agent l'ajoute si sa tâche en est seule
  propriétaire — précisé dans le brief).

Règle générale : **un worktree possède ses fichiers NEUFS + ses tests** ; les fichiers partagés sont
intégration-owned.

## 5. Table des worktrees (Vague 1)

Légende — **Auto** : l'agent prouve le vert tout seul (gate de code sandbox). **Geoffrey** : validation
finale nécessite son shell réel (Docker/ffmpeg/réseau). Chaque structurante a son **design doc**
(`docs/superpowers/specs/2026-06-15-<tâche>-design.md`).

| Worktree | Tâches | Tier | Design doc | Vérif | Fichiers réservés notables |
|---|---|---|---|---|---|
| **WT-docs** | déspéc ProtonVPN ; réécriture runbook (public moyennement technique) + note pin `3.0.0-1` + egress-boot ; enrichissements doc richesse EC | simple | brief : `2026-06-15-simple-tasks-briefs.md` | Auto (texte) | n/a (docs) ; ne touche pas la structure compose |
| **WT-verifier** | **clamav** puis **ring noyau (seccomp)** (séquentiel, même worktree — overlap `config.py`/`pipeline.py`/`spawn.py`) | structurant | `clamav-design.md`, `ring-noyau-design.md` | Auto (runners injectés) ; clamav+seccomp réels = Geoffrey (`analysis_integration`) | `pyproject` (dép clamscan/seccomp → déclarée), `compose.yaml` (sidecar freshclam → delta) |
| **WT-fusion** | **merge** (script standalone, N bases → 1 fichier neuf, idempotent) | structurant | `fusion-merge-design.md` | **Auto** (SQLite temp, pas de Docker) | son propre point d'entrée (pas un sous-commande de l'app crawler) |
| **WT-portsync** | **port-sync** (EC `SetPreferences` dans `mule_ec` + lecteur port gluetun + boucle `application` + proxy restart) | structurant | `port-sync-design.md` | Auto (unit) ; réel = e2e/Geoffrey | `compose.yaml` (proxy `wollomatic` + env gluetun → delta), `composition/app.py` (câblage) |
| **WT-e2e** | **suite e2e A+B** : stub Python (couche A) + Dockerfile `ed2kd` vendoré (couche B) + `compose.e2e.yaml` + test `e2e_integration` + fichier planté ; valide port-sync via stub `/v1/portforward` | structurant | `e2e-suite-design.md` | stub = Auto (pur Python) ; suite = Geoffrey (Docker) | crée `compose.e2e.yaml` (neuf, OK) ; vendore le tarball ed2kd |
| **WT-crawler-cli** | sous-commande **`validate-config`** ; prép. probe **richesse EC** (dump tags `raw` dans `ec_probe.py`) | simple | — (brief : `2026-06-15-simple-tasks-briefs.md`) | Auto ; run probe = Geoffrey | module CLI (propriétaire ici), `tools/ec_probe.py` |
| **WT-crawler-app** | **I2** (granularité d'erreur par-étape `run_download_cycle`) ; **T12** (couverture d'arrêt : guard `if not task.done()` + test de mutation) | simple | brief : `2026-06-15-simple-tasks-briefs.md` | Auto (unit/mutation) ; `orchestration_integration` = Geoffrey | `application/run_download_cycle.py`, `composition` (guard arrêt) |

**Note dépendances inter-worktrees** : port-sync (boucle) et l'e2e (qui la valide) sont conçus
indépendamment ; l'e2e consomme l'API de la boucle port-sync via le stub `/v1/portforward`, pas son code
→ pas de dépendance de build. Fusion résout **aussi** l'item différé `file_verifications` dedup (la
dédup idempotente par clé naturelle). 

## 6. Protocole de dispatch (anti-désalignement)

Chaque agent de Vague 1 reçoit un **brief** contenant exactement :

1. **Sa tâche** + son **design doc** (structurantes) ou son **brief court** (simple/additif — critères
   d'acceptation + exemples, pas de design : déspéc = sweep texte ; CLI = sous-commande + tests ; I2/T12 =
   refactor/test ciblés).
2. **Les règles dures** (`CLAUDE.md`) : TDD strict (test qui échoue d'abord), **100 % branch**,
   hexagonal (`domain/` pur), `mypy --strict` sur src+tests, ruff `E,F,I,UP,B,SIM` ligne 100, sqlfluff
   pour le SQL embarqué. Le gate PAR PAQUET : `( cd packages/<pkg> && uv run pytest -q )`.
3. **Son worktree** + la **liste explicite des fichiers à NE PAS toucher** (les intégration-owned du §4).
4. **Les commandes exactes du gate** à auto-lancer avant de se déclarer fini.
5. **Le format de rapport de complétion** : ce qui a changé (fichiers neufs/modifiés), **sortie du gate**
   (les 4 commandes vertes), tests d'intégration **écrits-mais-non-lancés** (marqueur + comment Geoffrey
   les lance), **dépendance ajoutée** (pour le lock), **delta compose/CLI/composition** à appliquer à
   l'intégration, et tout **écart au design + pourquoi**.

**Condition d'arrêt de l'agent** : code + tests unitaires/contract **verts sur le gate de code**, dans sa
branche de worktree, **NON mergée**. Il ne touche pas `main`, ni les fichiers intégration-owned, ni le
lock.

## 7. Frontière de vérification & d'intégration (Vague 2)

**Orchestrateur** (séquentiel, après que les agents ont fini) :
1. Merge les branches dans l'**ordre de dépendance** (fusion/CLI/app d'abord, puis verifier, puis
   port-sync, puis e2e — l'e2e en dernier car il assemble le tout).
2. Résout les **fichiers intégration-owned** : applique les deltas `compose`/CLI/`composition`, lock les
   dépendances déclarées (`uv lock`), tisse le câblage `composition/app.py`.
3. Relance le **gate complet sur `main` intégré** (les deux paquets 100 % branch + mypy + ruff + sqlfluff).
4. Lance les **marqueurs d'intégration** runnables : `verify_integration`, `analysis_integration` (ffmpeg),
   et — quand Docker dispo — `ec_/orchestration_/download_/compose_integration`.
5. Met à jour `CLAUDE.md` + écrit le **handoff** de la passe.

**Geoffrey** (checklist « réseau vivant » — ce que le sandbox ne peut pas) :
- `e2e_integration` complet (Docker+compose : stub/ed2kd + amuled + verifier ; download→verify réel).
- Probe **richesse EC** : lance le dump `ec_probe`, colle la sortie → l'orchestrateur met à jour le doc.
- Validation **egress-au-boot** derrière VPN (le 1er-run amuled fetch `server.met`/`nodes.dat`).
- Validation **port-sync HighID** réelle (la suite e2e couche B la couvre via le port-check ed2kd).
- clamav/seccomp réels (`analysis_integration`) si non couverts par le runner CI.

## 8. Hors-scope de cette passe (conscient, pas un oubli)

- **upgrades** (re-DL meilleure version) — débloqué mais demande un design-léger propre (déclencheurs
  re-DL, dédup de versions) ; **différé** à une passe suivante (🟡, ne bloque pas « est-ce que ça marche »).
- **`rebuild-local`** (sous-commande CLI) — **sorti du lot simple** : ambiguïté de périmètre (qu'est-ce
  qui est autoritaire dans `local.db` vs dérivable de `catalog.db` ?). Demande une clarification courte
  (data-model / MVP §15.3) avant brief ; différé jusque-là. `validate-config` (clairement simple) reste
  dans la passe.
- **couche e2e C (chaîne WireGuard + PF réel)** — **abandonnée** : infaisable sans impersonate un
  provider commercial (cf. §3.4), et testerait du tiers de confiance (gluetun), pas notre code.
- **WebUI**, **hub central** (Postgres/push), **rétention/compaction**, et les ⚪ mineurs (2ᵉ
  `communicate()`, visibilité GHCR, quota disque, `mem_limit`→`deploy.resources`, double-build smoke) —
  backlog basse prio.
- **`file_verifications` dedup** — **résolu** par la fusion idempotente (plus un item séparé).

## 9. Watch-list d'intégration (cross-cutting — relevé pendant la relecture des 5 design docs)

Points qui **traversent** les worktrees et que l'**orchestrateur** traite en Vague 2 (les agents les
*signalent*, ne les *résolvent* pas).

**a. Deltas compose à merger** (intégration-owned) :
- clamav : sidecar `freshclam` + volume `clamav-db` RO **+ relever le `mem_limit` du conteneur
  verifier** (768 Mo < rlimit AS clamscan ~1.5 Gio → sinon l'OOM-killer tue le child avant le rlimit).
- port-sync : sidecar `docker-proxy` (wollomatic ou maison, **surface = restart-amuled-only**) + env
  gluetun `HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE` (auth none sur `ec`).
- e2e : crée son **propre** `compose.e2e.yaml` (pas de merge dans `compose.yaml`).

**b. Dépendances à locker** (l'agent déclare, l'orchestrateur `uv lock`) :
- **`pyseccomp`** (ring noyau) + apt `libseccomp2` ; apt `clamav` (binaire `clamscan`) côté image verifier.
- **`pycryptodome`** (ou impl MD4 pure) — dép de **test** e2e (hash ed2k ; `hashlib` MD4 retiré par
  OpenSSL 3).
- clamav/clamscan = binaires système (pas de dép Python).

**c. Tissage de fichiers partagés** (intégration-owned) :
- `composition/app.py` : câblage de la boucle **port-sync** + guard d'arrêt **T12** (deux worktrees y
  touchent → l'orchestrateur tisse).
- module CLI : sous-commande **`validate-config`** (WT-crawler-cli en est seul propriétaire ici → peut
  l'ajouter, mais coordonner si un autre worktree touche le même module).

**d. Test à corriger** : `test_unknown_check_name_is_ignored` utilise aujourd'hui `"clamav"` comme nom
inconnu → l'activation de clamav le casse ; le worktree verifier doit le réécrire.

**e. Vérifications « ne pas inventer » à faire à l'implémentation** (doc upstream via context7/web,
PAS de `vendor/` pour ces deux-là) :
- **R1** syntaxe exacte d'allowlist `wollomatic/socket-proxy` (sinon basculer mini-proxy maison).
- **R2** nom/format exact de la variable d'auth gluetun + route `/v1/portforward` sur la version épinglée.

**f. Vérifications à l'intégration/réel** :
- **R3 port-sync** : confirmer que la réponse EC à `GET_PREFERENCES` (0x3F) porte bien l'opcode
  `SET_PREFERENCES` (0x40) contre un vrai `amuled`.
- **seccomp ↔ clamav** : confirmer que `clamscan` (standalone, jamais `clamd`) ne fait aucun `socket()`
  bloqué par le filtre (cohérence déjà acquise : clamd aurait cassé, clamscan non).
- **clamav mem** : mesurer l'empreinte réelle de `clamscan` pour caler rlimit AS + `mem_limit`.

**g. Cohérence émergente notable** (pas une action — un renfort) : le `deny socket` du **ring** IMPOSE
le `clamscan` standalone du **clamav** ; les deux décisions se verrouillent. C'est pourquoi WT-verifier
fait clamav **puis** ring **dans le même worktree**.
