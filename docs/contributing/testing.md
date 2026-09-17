# Guide des tests : mulewatch

Ce guide décrit **comment lancer les suites d'intégration** (les lourdes, désélectionnées par
défaut), leurs **prérequis exacts** et **ce qu'il faut attendre** en sortie. Il complète
[Installer un nœud](../install.md) : cette page-là explique comment faire tourner un nœud, celle-ci
explique comment le **valider**.

Public : **dev local et CI**. Pas pour les opérateurs (qui n'ont aucune raison de lancer les suites
de tests). Tout ce qui suit est **extrait du code réel** (fichiers de tests, `pyproject.toml`,
fichiers compose). Lorsqu'un prérequis ne peut pas être vérifié dans le code, il est marqué
« à confirmer ».

> **La suite e2e « transfert réel » a été abandonnée** (et son échafaudage supprimé du dépôt). La
> raison : faire en sorte qu'un vrai `amuled` signale un téléchargement terminé impliquerait
> d'orchestrer et de rétro-concevoir des outils tiers (`amuled`, `ed2kd`), ce qui valide surtout le
> comportement de tiers de confiance plutôt que notre code (le même argument que pour la couche de
> port-forwarding gluetun). La détection de complétion reste couverte par des **tests unitaires**,
> plus les contraintes de déploiement documentées dans
> `agents/reference/2026-06-17-amuled-completion-behavior.md`.

---

## 1. Vue d'ensemble : la pyramide de tests

Le projet a **deux niveaux** :

1. **Le gate unitaire** (lancé par défaut, **100 % de couverture de branches** imposée). C'est ce que
   vérifient le hook pre-push et la CI, via une source de vérité unique dans `pyproject.toml`
   (`[tool.poe.tasks]`) :

   ```bash
   uv run poe check     # the full gate: lint-all + test (what pre-push and CI run)
   uv run poe test      # the 3 unit suites alone, each in its own process
   ```

   > La tâche `test` reste **par paquet** : elle lance `pytest` avec `cwd = packages/<pkg>` pour
   > chacun des 3 paquets, dans des processus séparés, afin de garder la coverage isolée. Un simple
   > `uv run pytest` depuis la racine du dépôt n'est **pas** le gate (la racine n'a pas de config
   > pytest, et un `conftest.py` racine neutralise la collecte, donnant `exit 5`).

   L'`addopts` de chaque paquet **désélectionne** tous les markers d'intégration
   (`-m "not ec_integration and not …"`), si bien que le gate ne les lance jamais, et ils sont exclus
   de la mesure de coverage.

2. **Les suites d'intégration** (désélectionnées par défaut, lancées **à la demande**). Chacune porte
   un **marker** pytest. Lancez-les une à la fois avec `--no-cov` (sinon le seuil de 100 % valable
   pour tout le paquet fait « échouer » un run ciblé même quand les tests passent) :

   ```bash
   ( cd packages/<pkg> && uv run pytest -m <marker> --no-cov )
   ```

   Ces suites ont besoin de ressources externes (Docker). **Elles ne tournent pas dans un bac à sable
   sans accès réseau complet ni Docker** : lancez-les sur une vraie machine.

---

## 2. Récapitulatif des markers

| Marker | Paquet | Ce qu'il valide | Docker ? | Autres prérequis | Commande |
|---|---|---|---|---|---|
| `ec_integration` | crawler | L'adapter EC (auth, statut réseau, cycle de recherche, get/set du port) face à un vrai amuled | **Oui** (à lancer soi-même) | Un amuled que vous fournissez, désigné par `MULEWATCH_TEST_EC_HOST` (§3.0) | `( cd packages/crawler && uv run pytest -m ec_integration --no-cov )` |
| `download_integration` | crawler | La mécanique EC du téléchargement (`add_link` dans la file de téléchargement) face à un vrai amuled | **Oui** (à lancer soi-même) | Le même amuled que ci-dessus (§3.0) | `( cd packages/crawler && uv run pytest -m download_integration --no-cov )` |
| `orchestration_integration` | crawler | Une boucle de crawl complète (un cycle plus un arrêt borné) face à un vrai amuled | **Oui** (à lancer soi-même) | Le même amuled que ci-dessus (§3.0) | `( cd packages/crawler && uv run pytest -m orchestration_integration --no-cov )` |
| `compose_integration` | crawler | Smoke e2e de la pile docker compose assemblée (sans VPN) : câblage uniquement | **Oui** (compose v2) | docker compose v2 ; un build d'image | `( cd packages/crawler && uv run pytest -m compose_integration --no-cov )` |

---

## 3. Une section par marker (de la plus légère à la plus lourde)

### 3.0 L'amuled dont les trois suites EC ont besoin (à lancer soi-même)

`ec_integration`, `download_integration` et `orchestration_integration` parlent toutes au MÊME
`amuled`, et **aucune ne le démarre** : l'appelant en fournit un et y pointe les suites via trois
variables d'environnement. Elles démarraient autrefois leur propre conteneur via `testcontainers`,
ce qui est inutilisable sur les hôtes où Docker ne peut pas créer de paire veth sur son réseau
`bridge` par défaut (le mode de défaillance rencontré pendant des mois : `failed to add the host
(veth...) <=> sandbox (veth...) pair interfaces: operation not supported`). Un simple `docker run`
avec un port publié fonctionne partout.

| Variable | Requise | Défaut | Signification |
|---|---|---|---|
| `MULEWATCH_TEST_EC_HOST` | **Oui** | aucun | Hôte du serveur EC. **Absente, les trois suites sont ignorées (SKIP)**, avec un message qui reprend la commande ci-dessous. |
| `MULEWATCH_TEST_EC_PORT` | Non | `4712` | Port EC. |
| `MULEWATCH_TEST_EC_PASSWORD` | Non | `indexer-ec-test` | Mot de passe EC (`GUI_PWD` du démon). |

Lancez un démon jetable, attendez son serveur EC, lancez les suites, jetez-le :

```bash
docker run -d --rm --name mulewatch-test-amuled \
    -e GUI_PWD=indexer-ec-test -p 4712:4712 ngosang/amule:3.0.0-1
until docker logs mulewatch-test-amuled 2>&1 | grep -q 'listening on 0.0.0.0:4712'; do sleep 2; done

export MULEWATCH_TEST_EC_HOST=127.0.0.1
export MULEWATCH_TEST_EC_PORT=4712
export MULEWATCH_TEST_EC_PASSWORD=indexer-ec-test
( cd packages/crawler && uv run pytest -m "ec_integration or download_integration or orchestration_integration" --no-cov )

docker rm -f mulewatch-test-amuled
```

Le démon est à état (il persiste ses préférences et sa file de téléchargement dans son conteneur), si
bien que les suites ne sont fiablement répétables que face à un démon NEUF : recréez-le plutôt que de
réutiliser un conteneur de longue durée.

---

### 3.1 `ec_integration` (crawler, **Docker requis**)

**Ce que ça prouve.** L'adapter EC parle à un **vrai `amuled`** : la formule du hash d'auth est
validée face au démon, l'auth échoue avec un mauvais mot de passe, le statut réseau se décode, et le
cycle complet recherche, progression, récupération, arrêt se déroule. Le second fichier
(`test_amuled_preferences.py`) valide le **get/set du port d'écoute** (port-sync High-ID) :
`get_listen_port()` lit un port plausible, et l'aller-retour `set -> get` renvoie la valeur qui a été
posée.

**Prérequis exacts.** Un amuled lancé selon le **§3.0** et `MULEWATCH_TEST_EC_HOST` exportée. Sans
elle, la suite est ignorée (elle n'échoue jamais sur une absence, et ne passe jamais silencieusement).

> Le conteneur éphémère **n'a aucun accès au réseau eD2k** : une recherche peut renvoyer
> `EC_OP_FAILED` ou des résultats vides. Les tests **le tolèrent explicitement** : ce qui est validé,
> c'est le **cycle requête/réponse**, pas la richesse des résultats.

**Commande.**
```bash
( cd packages/crawler && uv run pytest -m ec_integration --no-cov )
```

**Attendu.** 6 tests passés (4 dans `test_amuled_ec.py` + 2 dans `test_amuled_preferences.py`), aucun
skip. `EC_OP_FAILED` est toléré en interne (le test passe quand même).

---

### 3.2 `download_integration` (crawler, **Docker requis**)

**Ce que ça prouve.** La mécanique EC du téléchargement face à un vrai `amuled` : `add_link` est
accepté et le lien apparaît dans `download_queue` avec un statut lisible. C'est le **garde-fou de
régression** du bug de décodage du hash de partfile (le hash vit dans le tag enfant
`EC_TAG_PARTFILE_HASH 0x031E`, pas dans la valeur propre du parent), d'où un hash et une taille
réalistes (~700 MiB), **jamais** le MD4 du fichier vide (qu'amuled traite comme instantanément
complet et ne liste pas).

**Prérequis exacts.** Les mêmes que `ec_integration` (§3.0).

**Commande.**
```bash
( cd packages/crawler && uv run pytest -m download_integration --no-cov )
```

**Attendu.** 2 tests passés (`test_add_link_then_appears_in_download_queue` et
`test_shared_files_round_trips`). Une complétion réelle n'est pas atteignable (pas de sources eD2k) :
seul le cycle add_link, file, statut est validé.

---

### 3.3 `orchestration_integration` (crawler, **Docker requis**)

**Ce que ça prouve.** Une vraie `CrawlerApp` (vrai `AmuleEcClient` + vraies bases SQLite sur
`tmp_path`) déroule **un cycle complet** face à l'`amuled` fourni puis **s'arrête proprement** dans
un `wait_for` de 120 s. L'assertion clé : l'index de cycle a avancé (`read_cycle_index() >= 1`),
prouvant qu'un cycle s'est réellement terminé.

**Prérequis exacts.** Les mêmes que `ec_integration` (§3.0). Le test charge la config du matcher
depuis la source de vérité unique, `deploy/matcher.yml`, et tourne avec la webui désactivée (son bind
est un `0.0.0.0:8080` fixe, qui entrerait en collision avec ce qui écoute déjà là).

**Commande.**
```bash
( cd packages/crawler && uv run pytest -m orchestration_integration --no-cov )
```

**Attendu.** 1 test passé (`test_real_loop_runs_one_cycle_and_stops`). Les résultats de recherche
peuvent tout à fait être vides : ce qui est validé, c'est la **boucle** (démarrage, recherche, mise
au catalogue, arrêt borné).

---

### 3.4 `compose_integration` : la pile smoke (crawler, **Docker + compose v2 requis**)

**Ce que ça prouve.** La pile `docker compose` **assemblée** (**un seul** service depuis le
2026-09-16, portant le crawler, amuled et amuleweb sous s6) démarre et se câble correctement.
**Aucun octet de contenu n'est jamais téléchargé** (amuled n'a ni serveur eD2k ni VPN ; seul son
serveur EC est sollicité). Quatre choses :
1. `docker compose build` réussit (l'image se construit) ;
2. le conteneur reste `Up`, devient **`healthy`**, `s6-svstat` signale les trois services up, et la
   webui in-process répond à `/health` (interrogée via `docker compose exec`, donc aucun port hôte
   n'est nécessaire) ;
3. un fichier qu'amuled partage et qui a quitté sa file est enregistré `completed` par le crawler :
   le vrai chemin EC par loopback, face au vrai amuled de l'image livrée ;
4. les deux points d'entrée de déploiement se rendent avec `docker compose config`, et la topologie
   rendue est vérifiée : un service `mulewatch`, la pile VPN n'ajoutant que `gluetun`, **rien** qui
   subsiste de `crawler` / `amuled` / `docker-proxy`, et **aucun volume nommé** nulle part.

Le smoke sollicite **délibérément** le vrai chemin de propriété : l'état vit dans des **bind mounts**
sous un répertoire jetable `SMOKE_STATE` créé sous l'utilisateur appelant, dont les uid/gid propres
sont passés en `PUID`/`PGID`. Le PID 1 root du conteneur chowne ces points de montage et chaque
service redescend ensuite vers l'utilisateur `amule`. Une régression là-dessus se manifeste par
`unable to open database file`.

**Prérequis exacts.**
- **Docker** + **docker compose v2** (le test pilote `docker compose …` via `subprocess`).
- Les builds tournent **depuis la racine du dépôt** (le test fixe `cwd = racine du dépôt` et
  `--project-directory`).
- Chaque variable interpolée est **bouchonnée par le test lui-même** : les quatre que l'image exige
  absolument (`PUID`, `PGID`, `AMULE_EC_PASSWORD`, `WEBUI_PWD`, sans lesquelles le one-shot de
  démarrage sort en 1 et le conteneur meurt), plus celles de gluetun (`WIREGUARD_PRIVATE_KEY`,
  `SERVER_COUNTRIES`), que compose interpole au parse même quand gluetun ne fait pas partie de la
  pile. Les ports et le tag d'image sont écrits en dur dans les fichiers compose de `deploy/`, ils
  n'interpolent donc rien. **Rien à régler pour l'opérateur.**
- Fichiers compose utilisés : `tests/smoke/compose.yaml` (autonome) plus `deploy/compose.yml` et
  `deploy/gluetun.compose.yml` pour `test_entrypoint_config_renders` ; les configs du smoke vivent
  sous `tests/smoke/`.
- Le test n'importe **aucun** module `mulewatch` (cela préserve les 100 % de couverture de branches
  du paquet).

**Commande.**
```bash
( cd packages/crawler && uv run pytest -m compose_integration --no-cov )
```

**Attendu.** **5 tests passés** en local : `test_build_succeeds`,
`test_one_container_supervises_the_three_services`,
`test_a_file_amuled_shares_is_recorded_completed`, et les 2 cas paramétrés de
`test_entrypoint_config_renders` (`compose` et `gluetun`). En CI l'image est préconstruite et
`IMAGE_TAG` est posée, si bien que `test_build_succeeds` est **ignoré** (4 passés, 1 skip) et que le
`up` réutilise l'image préconstruite. Le teardown est un `docker compose down -v` plus le répertoire
d'état jetable, dans un `finally`. Prévoyez plusieurs minutes (le build et le up tiennent sous des
timeouts de 900 s).

> **Jamais exécutée.** Au 2026-09-16 il n'y a aucun runtime de conteneurs sur la machine de
> développement : cette suite (et l'image qu'elle construit) n'a donc pas été lancée une seule fois.
> Voir le
> [handoff mono-conteneur](https://github.com/GeoffreyCoulaud/mulewatch/blob/main/agents/handoffs/2026-09-16%20-%20handoff%20-%20single%20container%20with%20embedded%20aMule.md),
> section 5.

---

## 4. Prérequis machine (récapitulatif installable)

Pour pouvoir lancer **toutes** les suites :

- **Docker** + **docker compose v2**. Les suites EC parlent à un amuled que **vous** lancez (§3.0,
  image `ngosang/amule:3.0.0-1`) ; la suite compose pilote `docker compose` directement.
- Un **`.env`** (copié depuis `deploy/.env.example`) pour les commandes compose **manuelles** :
  `WIREGUARD_PRIVATE_KEY`, `SERVER_COUNTRIES`, `AMULE_EC_PASSWORD`. À noter que le test
  `compose_integration` **les bouchonne lui-même**, donc le `.env` n'est pas requis pour le lancer.

---

## 5. Intégration CI

Déjà en CI :

- `.github/workflows/validate.yml` est le **gate** réutilisable, appelé par `pr.yml` (sur les pull
  requests) et par `release.yml` (sur un push de tag). Ses jobs :
  - `lint` : `uv run poe lint-all` (ruff, format, mypy, sqlfluff, vérification des templates) ;
  - `test` : `uv run poe test` (les 3 suites unitaires par paquet, 100 % de branches chacune) ;
  - `build-and-verify` : un job **par architecture sur son runner natif** (`amd64` sur
    `ubuntu-latest`, `arm64` sur `ubuntu-24.04-arm`). Chacun construit l'image du crawler puis lance
    **`compose_integration`** contre cette image construite localement (`IMAGE_TAG=ci-<sha>`) ;
  - `ec-integration` : lance un `ngosang/amule:3.0.0-1` avec `docker run -p 4712:4712`, attend sa
    ligne de log EC, puis lance **`ec_integration`, `download_integration` et
    `orchestration_integration`** contre lui en un seul appel pytest. Le Docker du runner sait créer
    un veth, donc la défaillance réseau qui bloque ces suites sur certaines machines de
    développement ne s'applique pas ;
  - `gate` : l'unique check d'agrégation exigé par la protection de branche.
- `.github/workflows/pr.yml` lance aussi le job `vex-checks` (`poe vex-source-claims` +
  `poe vex-claim-coverage`).
- `.github/workflows/grype-scan.yml` scanne quotidiennement l'image publiée et remonte dans Code
  scanning.

Tous les markers tournent désormais en CI. Les seules suites encore absentes sont celles appartenant
aux autres paquets (voir leurs sections ci-dessus).

Le job `ec-integration` tourne sur `ubuntu-latest` seulement, pas sur les deux arches : il sollicite
le code du protocole EC, qui est du Python pur et indépendant de l'architecture. L'artefact sensible
à l'architecture est l'image du crawler, et c'est ce que `build-and-verify` couvre sur les deux
runners.

---

## 6. Outils de diagnostic (mesure, dev)

Outils ponctuels destinés au **développeur** (mesure et diagnostic, pas exploitation) :

- **Sonde de richesse EC** : `uv run python -m mulewatch.tools.ec_probe --all-tags …` déverse
  **chaque** tag brut d'un vrai résultat de recherche (mappé ou non). C'est lui qui a mesuré le taux
  de remplissage des champs qu'expose EC. C'est un outil de **diagnostic** : un déploiement n'en a
  pas besoin (voir le constat « EC n'expose aucune métadonnée média sur les résultats de
  recherche »).

---

## 7. Voir aussi

- [Installer un nœud](../install.md) : pour déployer et faire tourner un nœud.
- [Architecture du code](architecture.md) : comment le crawler fonctionne à l'intérieur.
