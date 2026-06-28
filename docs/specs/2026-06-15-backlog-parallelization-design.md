# Plan d'orchestration — réduction du backlog (post-Plan E)

> **Nature** : ce doc est le **plan maître** qui orchestre l'exécution du backlog catalogué dans
> `docs/handoffs/2026-06-15 - handoff - post-E checkpoint (backlog).md`. Il définit l'ordre
> d'exécution, le protocole de dispatch des sous-agents, et la frontière de validation. Les
> **décisions de design** des tâches structurantes vivent dans des design docs dédiés (référencés en
> §5). À lire AVANT de lancer l'exécution.
>
> **⚠️ MÉTHODO (révisée après le co-design) — SÉQUENTIEL subagent-driven, PAS de worktrees
> parallèles.** Les tâches de cette passe partagent des fichiers (`composition/app.py`, `compose.yaml`,
> `uv.lock`, module CLI) → le parallélisme imposait une partition + une vague d'intégration coûteuses
> pour un gain de temps-mur sans intérêt (Geoffrey n'est pas bloqué). On exécute donc **une tâche à la
> fois, dans l'ordre des dépendances** (§5), chacune par un **sous-agent implémenteur frais** → revue
> spec + revue code (chaîne `CLAUDE.md`) → **commit sur `main`** → tâche suivante. C'est la méthodo
> PROUVÉE des Plans A→F. **Conséquence sur ce doc** : §4 (partition anti-conflit) et la « Vague 2 »
> d'intégration de §7 sont **CADUQUES** — en séquentiel, chaque tâche édite directement les fichiers
> partagés (compose, lock, `app.py`…) dans son tour ; plus de « delta proposé → orchestrateur merge ».
> Ce qui survit de §7 = la **checklist « réseau vivant » de Geoffrey** + les marqueurs d'intégration.

## 1. Objectif & principe

Réduire le backlog en exécutant les tâches planifiées **séquentiellement** (subagent-driven), sans
désalignement. Principe directeur retenu avec Geoffrey :

- **La réflexion est sérialisée sur un seul fil** (le co-design, déjà fait cette session) pour éviter
  le multitasking mental. **L'exécution est séquentielle** : une tâche à la fois, commit sur `main`,
  puis la suivante — aucun coût de réconciliation, aucune collision (les tâches partagent des
  fichiers, cf. §4 caduc).
- Les tâches n'ont pas le même besoin de planification : **spikes** (exploration — FAIT),
  **simple/additif** (brief court, TDD direct), **structurant** (design approuvé avant code — FAIT).
- Chaque tâche va **jusqu'au gate de code vert** (pytest 100 % branch + mypy + ruff + sqlfluff) via un
  **sous-agent implémenteur frais**, relue (spec + code) puis **commitée sur `main`**. La validation
  **réseau/Docker réel** reste à Geoffrey (§7).

## 2. Le modèle d'exécution

- **Vague 0 — spikes (FAIT)** : 5 investigations en lecture de source (aMule/gluetun/ngosang/ed2k)
  ont levé les inconnues. Résultats figés en §3. La source amont est clonée dans `vendor/` (gitignoré)
  pour que tout agent la lise en local via `Read`.
- **Co-design — structurantes (FAIT)** : clamav, ring noyau, fusion, port-sync, e2e — décisions
  figées dans les design docs (§5).
- **Exécution — SÉQUENTIELLE (À LANCER)** : les tâches dans l'ordre de §5, une à la fois, chacune par
  un sous-agent frais jusqu'au gate vert + revues, puis **commit sur `main`**. Voir le protocole (§6).
  *(Remplace l'ancienne « Vague 1 » worktrees-parallèles.)*
- **Validation réseau/réel (continue + fin)** : marqueurs d'intégration Docker/réseau lançables + la
  checklist « réseau vivant » de Geoffrey (§7). *(Plus de « Vague 2 » de merge : l'intégration est
  continue, chaque tâche atterrit sur `main`.)*

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

## 4. ~~Partition anti-conflit & fichiers « intégration-owned »~~ — CADUC (séquentiel)

> **CADUC depuis le passage au séquentiel.** La partition (réserver les fichiers transverses,
> « proposer un delta → l'orchestrateur merge ») n'existait que pour empêcher des worktrees parallèles
> de se télescoper. En séquentiel, **chaque tâche édite directement** ce dont elle a besoin (compose,
> `uv.lock`/`pyproject`, `composition/app.py`, module CLI) dans son tour, puis commit ; la tâche
> suivante part d'un `main` déjà à jour. Aucune réservation, aucun delta différé.

**Ce qui survit (pour mémoire)** — les fichiers que **plusieurs** tâches touchent, donc à éditer avec
soin en s'appuyant sur l'**ordre de §5** :
- `compose.yaml` (clamav : sidecar freshclam + mem_limit ; port-sync : proxy + env gluetun) ;
- `uv.lock`/`pyproject.toml` (pyseccomp, pycryptodome-test) — locké par l'orchestrateur juste après la
  tâche qui l'ajoute ;
- `composition/app.py` (T12 pose le guard d'arrêt **avant** que port-sync n'y câble sa boucle) ;
- module CLI (`validate-config`) ;
- `CLAUDE.md` + `docs/handoffs/` : mis à jour en **fin de passe** (pas par tâche).

## 5. Table des tâches (ORDRE d'exécution séquentiel)

Légende — **Auto** : le sous-agent prouve le vert tout seul (gate de code sandbox). **Geoffrey** :
validation finale nécessite son shell réel (Docker/ffmpeg/réseau). Structurantes → design doc
`2026-06-15-<tâche>-design.md` ; simple/additif → `2026-06-15-simple-tasks-briefs.md`. **On exécute
dans cet ordre** (dépendances + empilement propre des fichiers partagés) ; chaque tâche se termine par
un **commit sur `main`** avant la suivante.

| # | Tâche | Tier | Design doc / brief | Vérif | Fichiers partagés touchés (édités directement) |
|---|---|---|---|---|---|
| 1 | **fusion** — `merge` standalone, N→1 idempotent | structurant | `fusion-merge-design.md` | **Auto** (SQLite temp) | aucun (point d'entrée propre) — idéal pour valider la boucle subagent-driven |
| 2 | **docs** — déspéc ProtonVPN ; runbook (public moyen) + pin `3.0.0-1` + egress-boot ; enrichissements richesse EC | simple | briefs | Auto (texte) | docs / `.env.example` (texte), pas la structure compose |
| 3 | **crawler-cli** — `validate-config` ; prép. probe richesse EC | simple | briefs | Auto ; run probe = Geoffrey | module CLI, `tools/ec_probe.py` |
| 4 | **crawler-app** — I2 (granularité `run_download_cycle`) ; T12 (guard d'arrêt + mutation) | simple | briefs | Auto (unit/mutation) ; `orchestration_integration` = Geoffrey | `application/run_download_cycle.py`, `composition/app.py` (guard) |
| 5 | **verifier** — clamav **puis** ring noyau (seccomp) | structurant | `clamav-design.md`, `ring-noyau-design.md` | Auto (runners injectés) ; réels = Geoffrey (`analysis_integration`) | `pyproject`/`uv.lock` (clamav apt, pyseccomp), `compose.yaml` (sidecar freshclam, mem_limit), `packages/verifier/*` |
| 6 | **port-sync** — EC `SetPreferences` (`mule_ec`) + lecteur gluetun + boucle `application` + proxy restart | structurant | `port-sync-design.md` | Auto (unit) ; réel = e2e/Geoffrey | `compose.yaml` (proxy + env gluetun), `composition/app.py` (câblage), config |
| 7 | **e2e** — stub Python (A) + Dockerfile `ed2kd` (B) + `compose.e2e.yaml` + test `e2e_integration` + fichier planté ; valide port-sync via stub `/v1/portforward` | structurant | `e2e-suite-design.md` | stub = Auto (pur Python) ; suite = Geoffrey (Docker) | `compose.e2e.yaml` (neuf), `deploy/e2e/*`, `uv.lock` (pycryptodome, test) |

**Dépendances/ordre** : **fusion** d'abord (zéro collision, valide la boucle subagent-driven). **T12**
(tâche 4) pose le guard d'arrêt dans `app.py` **avant** que **port-sync** (6) n'y câble sa boucle.
**e2e** (7) en dernier : il assemble tout et **consomme l'API** de la boucle port-sync via le stub
`/v1/portforward` (pas son code — mais l'avoir intégrée avant simplifie le sous-test). **Fusion** résout
**aussi** l'item différé `file_verifications` dedup (dédup idempotente par clé naturelle).

## 6. Protocole d'exécution (anti-désalignement)

Chaque tâche est confiée à un **sous-agent implémenteur frais** (garde le contexte de l'orchestrateur
propre), qui reçoit un **brief** contenant exactement :

1. **Sa tâche** + son **design doc** (structurantes) ou son **brief court** (simple/additif — critères
   d'acceptation + exemples : déspéc = sweep texte ; CLI = sous-commande + tests ; I2/T12 = refactor/test
   ciblés).
2. **Les règles dures** (`CLAUDE.md`) : TDD strict (test qui échoue d'abord), **100 % branch**,
   hexagonal (`domain/` pur), `mypy --strict` sur src+tests, ruff `E,F,I,UP,B,SIM` ligne 100, sqlfluff
   pour le SQL embarqué. Le gate PAR PAQUET : `( cd packages/<pkg> && uv run pytest -q )`.
3. **L'état courant de `main`** : il part du commit de la tâche précédente. **Pas de worktree, pas de
   réservation** — il édite directement ce dont il a besoin (y compris compose/`composition/app.py`/
   config), en s'appuyant sur l'ordre §5.
4. **Les commandes exactes du gate** à auto-lancer avant de se déclarer fini.
5. **Le format de rapport de complétion** : ce qui a changé, **sortie du gate** (les commandes vertes),
   tests d'intégration **écrits-mais-non-lancés** (marqueur + comment Geoffrey les lance),
   **dépendance ajoutée** (à locker), et tout **écart au design + pourquoi**.

**Après l'implémenteur**, l'orchestrateur (moi) applique la chaîne de revue `CLAUDE.md` : **revue
spec-compliance** → **revue code-quality** (sous-agents reviewers) → corrections → `uv lock` si dép
ajoutée → **commit sur `main`** (préfixe conventionnel `feat(...)`/`test:`/`docs:`). La **revue
holistique finale** + le **tag de jalon** se font en fin de passe (après la tâche 7).

**Condition de fin d'une tâche** : code + tests **verts sur le gate**, deps lockées, **commitée sur
`main`** ; la tâche suivante part de là.

## 7. Validation réseau/réel (ce que le sandbox ne peut pas)

Il n'y a **plus de « Vague 2 » de merge** : l'intégration est **continue** (chaque tâche commit sur
`main` après ses revues). Restent les validations **hors-sandbox**.

**Orchestrateur** (au fil de l'eau + fin de passe) :
1. À chaque tâche : **gate complet vert sur `main`** (les deux paquets 100 % branch + mypy + ruff +
   sqlfluff) — c'est la condition de commit.
2. En fin de passe : lancer les **marqueurs d'intégration** runnables en sandbox-normal :
   `verify_integration`, `analysis_integration` (ffmpeg requis).
3. **Revue holistique finale** (sous-agent) sur l'ensemble de la passe → mise à jour `CLAUDE.md` +
   **handoff** + **tag de jalon**.

**Geoffrey** (checklist « réseau vivant ») :
- `e2e_integration` complet (Docker+compose : stub/ed2kd + amuled + verifier ; **download→verify réel** ;
  `resolve_staging_path`/DV10 exercé).
- `ec_/orchestration_/download_/compose_integration` (Docker requis).
- Probe **richesse EC** : lance le dump `ec_probe`, colle la sortie → l'orchestrateur met à jour le doc.
- Validation **egress-au-boot** derrière VPN (1er-run amuled fetch `server.met`/`nodes.dat`).
- Validation **port-sync HighID** réelle (couverte par e2e couche B via le port-check ed2kd) + **R3**
  (opcode réponse EC `GET_PREFERENCES`=0x40) + **R1/R2** (syntaxe wollomatic / auth gluetun via doc upstream).
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

Points qui **traversent** les tâches. **En séquentiel, chaque tâche les traite DIRECTEMENT dans son
tour** (plus de « signale → l'orchestrateur merge en Vague 2 ») : la tâche qui touche un fichier
partagé l'édite, l'orchestrateur lock/revoit, on commit, la suivante en hérite. La liste reste le
**rappel de ce que chaque tâche doit faire**.

**a. Deltas compose** (édités par la tâche concernée, dans son tour) :
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

**c. Fichiers partagés** (édités dans l'ordre §5, l'empilement règle la coordination) :
- `composition/app.py` : **T12** (tâche 4) pose le guard d'arrêt, **port-sync** (tâche 6) y câble sa
  boucle ensuite — l'ordre garantit que la 6 part d'un `app.py` déjà guardé.
- module CLI : sous-commande **`validate-config`** (tâche 3, éditée directement).

**d. Test à corriger** : `test_unknown_check_name_is_ignored` utilise aujourd'hui `"clamav"` comme nom
inconnu → l'activation de clamav le casse ; la tâche verifier (5) doit le réécrire.

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
le `clamscan` standalone du **clamav** ; les deux décisions se verrouillent. C'est pourquoi la tâche
verifier (5) fait clamav **puis** ring **séquencés**, dans cet ordre.
