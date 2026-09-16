# Déployer un nœud mulewatch

Le sujet du catalogue est **le fichier, jamais la personne**.

Ce guide vous mène de rien à un nœud qui tourne. Tel quel, le nœud cherche, catalogue, notifie et
télécharge les fichiers qu'il identifie avec certitude dans un dossier `downloads/` posé à côté de
votre fichier compose. À la fin, vous aurez un catalogue web sur `http://localhost:8080`.

Votre adresse IP est visible des autres pairs du réseau eMule : c'est ainsi que ce réseau fonctionne
publiquement et normalement. Ce que mulewatch fait et ne fait pas est détaillé dans
[légalité et vie privée](../legal-and-privacy.md) ; pour masquer votre IP derrière un VPN, voir
l'annexe A.

Suivez les sept étapes dans l'ordre : elles suffisent à obtenir un nœud qui tourne. Chacune se
termine par un **Point de contrôle** qui dit ce que vous devez voir, et quelle fiche de dépannage
ouvrir sinon. Les variantes (VPN, catalogue seul, High-ID, ports) sont en annexes : faites d'abord
les sept étapes, puis lisez seulement l'annexe qui vous concerne, chacune décrivant ce qu'elle
ajoute à ce parcours. Si vous **mettez à niveau un nœud 1.x existant**, lisez d'abord l'annexe E :
la 2.0 est une version cassante et la migration se fait à la main, une fois.

> **Un conteneur, trois processus.** Depuis la 2.0, un nœud est un **seul** conteneur. À
> l'intérieur, le superviseur [s6](https://skarnet.org/software/s6/) fait tourner trois processus :
> `amuled` (le client eMule), `amuleweb` (l'interface web propre à aMule) et `mulewatch` (le
> crawler, qui sert aussi le catalogue web). Vous n'avez normalement pas à le savoir ; cela compte
> quand vous lisez les journaux ou redémarrez une pièce, et les runbooks le rappellent là où ça se
> voit.

---

## 1. Ce qu'il vous faut

- **Une machine qui reste allumée.** Un vieux PC ou un mini-PC suffit : un nœud n'est utile que
  s'il surveille en continu. Vous n'aurez plus à y toucher une fois lancé.
- **Une connexion Internet permanente.**
- **Environ 2 Go de RAM libre** (le conteneur est plafonné à 2 Go) et **environ 5 Go de disque
  libre** pour commencer. Le catalogue grossit ensuite lentement, et les fichiers téléchargés
  s'accumulent par-dessus : voir
  [runbook d'administration, § Planification disque](administration.md#planification-disque).
- **De quoi ouvrir un terminal** : l'application Terminal sur macOS et Linux, PowerShell sur
  Windows.

---

## 2. Installer Docker

mulewatch tourne dans Docker. Installez-le depuis la page officielle, tenue à jour et valable pour
tous les systèmes : <https://docs.docker.com/get-started/get-docker/>.

- **Windows / macOS** : installez Docker Desktop, puis **lancez-le** et attendez qu'il indique
  qu'il tourne.
- **Linux** : installez Docker Engine (le choix « Server » sur cette page), puis suivez ses étapes
  de post-installation pour pouvoir utiliser `docker` sans `sudo`.

**Point de contrôle.** Tapez :

```
docker compose version
```

Vous devez voir une ligne du type `Docker Compose version v2.x.x` (un numéro plus récent convient
aussi). Si vous obtenez `command not found` ou une version `1.x`, ouvrez la fiche
[« Docker introuvable ou compose v1 »](troubleshooting.md#docker-introuvable-ou-compose-v1). Si plus
tard une commande répond `Cannot connect to the Docker daemon` (sous Windows
`error during connect ...`), c'est que le moteur Docker n'est pas démarré : ouvrez la fiche
[« Docker est installé mais ne répond pas »](troubleshooting.md#docker-est-installé-mais-ne-répond-pas).

---

## 3. Créer votre dossier de travail

1. Ouvrez <https://github.com/GeoffreyCoulaud/mulewatch>, cliquez sur le bouton vert **`Code`**,
   puis **`Download ZIP`**.
2. Décompressez le fichier téléchargé. Il contient un dossier **`deploy`** : c'est le seul dont
   vous avez besoin.
3. **Copiez ce dossier `deploy`** où vous voulez travailler et renommez-le à votre goût, par exemple
   **`mulewatch`**. C'est votre **dossier de travail**. Le reste du ZIP est inutile, vous pouvez le
   supprimer.
4. Ouvrez un terminal **dans ce dossier de travail** : clic droit « Ouvrir dans le terminal » sous
   Windows ; sous macOS/Linux, faites un `cd` dedans.

Si vous connaissez git, l'équivalent est
`git clone https://github.com/GeoffreyCoulaud/mulewatch.git`, puis prenez son sous-dossier `deploy`
comme dossier de travail.

Ce que contient ce dossier, et à quoi sert chaque pièce :

| Entrée | Ce que c'est |
|---|---|
| `compose.yml` | la pile que vous lancez (celle de ces sept étapes) |
| `gluetun.compose.yml` | la variante VPN (annexe A) |
| `base.compose.yml` | la part commune aux deux piles ; celui-là ne se lance pas seul |
| `.env.example` | le modèle de votre propre `.env` (étape 4) |
| `crawler.yml` | les réglages du nœud (téléchargement oui/non, intervalles, notifications) |
| `targets.yml`, `matcher.yml` | les épisodes recherchés, et les règles qui les reconnaissent |
| `amule/` | l'état propre au client eMule (son `amule.conf`, sa liste de serveurs, ses nœuds Kad) |
| `data/` | votre catalogue : `catalog.db` et `local.db` |
| `downloads/` | `incoming/` pour les fichiers terminés, `temp/` pour les fichiers partiels |

Les trois derniers sont vides au départ (ils ne contiennent qu'un `.gitkeep`) et se remplissent
quand le nœud tourne. **Ce sont de simples dossiers sur votre disque**, pas des volumes Docker :
vous pouvez ouvrir `data/catalog.db` avec n'importe quel outil SQLite, et sauvegarder tout le
dossier de travail par une copie.

**Point de contrôle.** Depuis ce dossier, tapez :

```
ls
```

La liste doit contenir **`compose.yml`** (sous Windows/PowerShell, `ls` affiche un tableau :
cherchez `compose.yml` dans la colonne `Name`). Sinon, vous n'êtes pas dans le bon dossier :
placez-vous dans le dossier de travail (celui qui contient `compose.yml`) et recommencez.

---

## 4. Votre mot de passe

Votre dossier de travail contient un fichier `.env.example`. **Faites-en une copie nommée `.env`**,
ouvrez cette copie dans un éditeur de texte, et renseignez les quatre valeurs obligatoires :

| Variable | Ce qu'il faut y mettre |
|---|---|
| `AMULE_EC_PASSWORD` | Un mot de passe d'au moins **12 caractères**, de votre choix. Il relie le crawler au client eMule dans le conteneur ; notez-le quelque part. |
| `WEBUI_PWD` | Un autre mot de passe de votre choix. Il protège **l'interface web d'aMule sur le port 4711** (étape 6). |
| `PUID` | Votre propre identifiant d'utilisateur. Sous macOS/Linux, lancez `id -u` ; sous Windows avec Docker Desktop, laissez `1000`. |
| `PGID` | Votre propre identifiant de groupe. Sous macOS/Linux, lancez `id -g` ; sous Windows, laissez `1000`. |

Laissez le reste tel quel (les autres valeurs ne servent qu'aux variantes des annexes). Ne laissez
pas `change-me` en place : ce sont des mots de passe en clair, donc des portes ouvertes.

`PUID`/`PGID` sont ce qui garde `data/`, `amule/` et `downloads/` lisibles et modifiables **par
vous**, depuis l'hôte, sans `sudo`. Le conteneur prend possession de ces dossiers à chaque
démarrage, avec exactement ces numéros.

> **Les quatre sont obligatoires.** Le conteneur refuse de démarrer si l'une d'elles manque : il
> sort immédiatement, avec une seule ligne nommant la variable. Voir
> [« Une variable obligatoire manque »](troubleshooting.md#une-variable-obligatoire-manque)
> si cela vous arrive.

> Le fichier `.env` commence par un point, donc le Finder de macOS et certains gestionnaires de
> fichiers Linux le **cachent**. Le plus fiable partout est de le créer et de l'éditer au terminal :
> `cp .env.example .env`, puis `nano .env` (macOS/Linux) ou `notepad .env` (Windows).

> **⚠ Le port 8080 n'a aucune authentification.** `WEBUI_PWD` protège le port **4711 seulement**. Le
> catalogue mulewatch sur le port **8080** est servi **sans mot de passe, sans connexion, sans jeton
> CSRF** — et il expose le catalogue, les contrôles de crawl (pause, passe forcée, redémarrage)
> **et une console SQL en lecture seule** à quiconque atteint ce port. C'est voulu : l'authentification
> est déléguée à ce que vous mettez devant. Sur une machine joignable depuis Internet, mettez-le
> derrière un reverse proxy authentifié, ou un VPN, ou ne publiez pas le 8080 du tout. Voir
> [runbook d'administration, § Exposition derrière un reverse proxy](administration.md#exposition-derrière-un-reverse-proxy).

---

## 5. Lancer

Depuis votre dossier de travail :

```
docker compose up -d
```

Au tout premier lancement, Docker télécharge l'image : cela peut prendre quelques minutes selon
votre connexion.

**Point de contrôle.** Une fois la commande rendue, tapez :

```
docker compose ps
```

Vous devez voir **un seul service**, `mulewatch`, dont l'état commence par `Up`. Au bout d'une
demi-minute environ, il doit afficher `Up (healthy)` : cela signifie que le client eMule tourne
vraiment à l'intérieur. S'il est en `Restarting` ou `Exited`, ouvrez la fiche
[« Un conteneur redémarre en boucle »](troubleshooting.md#un-conteneur-redémarre-en-boucle). Si le
démarrage échoue sur un message de port déjà occupé, ouvrez la fiche
[« Le port est déjà pris »](troubleshooting.md#le-port-est-déjà-pris).

---

## 6. Voir votre nœud

Votre nœud sert **deux** pages web :

| Adresse | Ce que c'est | Mot de passe |
|---|---|---|
| <http://localhost:8080> | **Le catalogue mulewatch** — le catalogue en lecture seule, les contrôles de crawl et la console SQL. | **Aucun.** Voir l'avertissement de l'étape 4. |
| <http://localhost:4711> | **L'interface web propre à aMule** — transferts, serveurs, état Kad. Utile pour voir directement le côté eMule. | `WEBUI_PWD` de votre `.env`. |

Ouvrez **<http://localhost:8080>** : vous devez voir le tableau de bord mulewatch, avec
l'identifiant de votre nœud et la liste des épisodes cibles (la colonne **Status** affiche `none` au
début). **Si cette page se charge, votre nœud tourne.** Sa navigation porte un lien **aMule** vers
l'autre page.

Le catalogue est **vide au début** et se remplit au fil des heures, à mesure que des fichiers sont
croisés sur le réseau (certaines cibles rares peuvent mettre des jours à réapparaître : c'est la
nature du lost media). Pour suivre l'activité de recherche, ouvrez la page **Nodes** : après le
premier cycle (quelques minutes), elle affiche le numéro et l'horodatage du dernier cycle, qui
avancent à chaque rechargement.

Les fichiers téléchargés atterrissent dans le dossier **`downloads/incoming`** de votre dossier de
travail (les fichiers partiels attendent dans `downloads/temp`). Rien ne les inspecte : mulewatch
n'ouvre jamais un fichier téléchargé, donc vérifier qu'un fichier est bien l'épisode voulu est à
votre charge.

Si votre nœud tourne sur un serveur distant, remplacez `localhost` par l'adresse IP ou le nom de ce
serveur.

**Point de contrôle.** La page <http://localhost:8080> s'ouvre et affiche le tableau de bord
(identifiant du nœud, liste des cibles). Si elle ne se charge pas du tout (connexion refusée),
ouvrez la fiche [« La webui reste vide »](troubleshooting.md#la-webui-reste-vide).

---

## 7. Vivre avec le nœud

Votre nœud est autonome. Quelques gestes utiles, tous depuis votre dossier de travail :

- **Mettre à jour.** L'image ne se met **pas** à jour toute seule : c'est vous qui décidez.

  ```
  docker compose pull
  ```

  ```
  docker compose up -d
  ```

  `up -d` ne recrée le conteneur que si l'image a changé. Vos données ne bougent pas.

- **Arrêter le nœud.**

  ```
  docker compose down
  ```

  Le catalogue, l'état d'eMule et vos téléchargements vivent dans de **simples dossiers** de votre
  dossier de travail (`data/`, `amule/`, `downloads/`) : ils **persistent**. Un `down` suivi plus
  tard d'un `up -d` retrouve tout. Il n'y a rien qu'un `down -v` pourrait effacer — pour vraiment
  repartir de zéro, vous supprimez `data/` vous-même, et seulement si c'est bien votre intention.

- **Sauvegarder.** Copiez le dossier de travail (arrêtez le nœud d'abord, pour que les bases SQLite
  ne soient pas en cours d'écriture). C'est toute la sauvegarde.

- **Redémarrage de l'hôte.** Le conteneur revient seul au boot de l'hôte (Docker doit démarrer en
  service système). Aucune commande à retaper.

- **Redémarrer un seul processus.** `docker compose restart mulewatch` redémarre le conteneur, donc
  les trois processus — le client eMule perd ses sessions eD2k et Kad et doit se reconnecter. Pour
  n'en redémarrer qu'un, adressez-vous à s6 dans le conteneur :

  ```
  docker compose exec mulewatch s6-svc -r /etc/services.d/amuled
  ```

  Les trois services s'appellent `amuled`, `amuleweb` et `mulewatch`. Le bouton de redémarrage de
  la webui (sur `/controls`) fait exactement cela pour le crawler, et c'est pourquoi le client eMule
  garde ses sessions au travers.

**Point de contrôle.** Vous pouvez le vérifier : un `docker compose down` suivi d'un
`docker compose up -d` retrouve, sur <http://localhost:8080>, tout ce que le catalogue avait déjà
vu. Vos données survivent à un arrêt.

> Le cycle de vie en détail (diagnostic après panne, planification disque, reboot) :
> [runbook d'administration, § Cycle de vie & données](administration.md#cycle-de-vie--données).

---

## Annexe A. Passer derrière un VPN

Pour masquer votre IP aux autres pairs eD2k/Kad, faites passer le nœud par un VPN avec le conteneur
`gluetun`.

**Ce qui change par rapport au parcours principal :**

1. **Un fournisseur VPN qui gère WireGuard.** C'est obligatoire : gluetun établit le tunnel en
   WireGuard.
2. **Trois variables de plus** dans votre `.env` :

   | Variable | Quoi |
   |---|---|
   | `WIREGUARD_PRIVATE_KEY` | La clé privée WireGuard, fournie dans l'espace client de votre VPN. |
   | `VPN_SERVICE_PROVIDER` | Le nom du fournisseur, par exemple `protonvpn`, `pia`, `privatevpn`. |
   | `SERVER_COUNTRIES` | Le ou les pays de sortie, en anglais, par exemple `Switzerland`. |

3. **Un fichier de pile différent.** À la place de `docker compose up -d`, vous utilisez la pile
   `gluetun.compose.yml`, et vous ajoutez `-f gluetun.compose.yml` à **toutes** les commandes
   compose ensuite (`ps`, `logs`, `pull`, `down`, etc.) :

   ```
   docker compose -f gluetun.compose.yml up -d
   ```

Cette pile ajoute exactement un service, `gluetun`, donc `docker compose -f gluetun.compose.yml ps`
montre **deux** services au lieu d'un. mulewatch n'y a pas de réseau propre : il partage celui de
gluetun (`network_mode: service:gluetun`), donc tout son trafic — celui du client eMule compris —
passe par le tunnel, et ses deux pages web sont publiées **sur le service gluetun** à la place.

Le port eD2k n'est délibérément **pas** publié dans cette pile : les connexions entrantes arrivent
par le port forwardé du VPN, pas par votre hôte (annexe C, route A).

> **Non validé sur matériel réel.** Les sources divergent sur la nécessité de donner aussi
> `FIREWALL_INPUT_PORTS=8080,4711` au pare-feu de gluetun pour les connexions venues de votre LAN.
> Si les deux pages répondent sur l'hôte lui-même mais pas depuis une autre machine de votre réseau,
> cette variable est la première chose à essayer.

---

## Annexe B. Mode catalogue seul (sans téléchargement)

Par défaut, un nœud télécharge les candidats qu'il identifie avec certitude. Si vous voulez
seulement cataloguer et être notifié, sans qu'aucun fichier n'atterrisse sur votre disque :

1. Dans `crawler.yml`, passez `download.enabled: true` à **`false`**.
2. Relancez depuis votre dossier de travail :

   ```
   docker compose up -d
   ```

Rien d'autre ne change : le même conteneur démarre, les mêmes trois processus tournent, le même
catalogue web est servi, les notifications partent toujours. Seule la boucle de téléchargement n'est
pas câblée, donc `downloads/incoming` reste vide.

> C'est un **drapeau de configuration**, pas une autre pile : il n'y a aucun profil compose à
> ajouter ou à retirer, dans un sens comme dans l'autre.

---

## Annexe C. High-ID (optionnel)

Par défaut, votre nœud est en **Low-ID** : il catalogue et télécharge, mais avec moins de sources
directes. Passer en **High-ID** (joignable depuis l'extérieur) apporte plus de sources et une
recherche plus efficace. Ce n'est **pas obligatoire** pour cataloguer. Deux routes, selon votre
pile :

| Route | Comment l'activer |
|---|---|
| **Pile par défaut, port ouvert** | Redirigez `LISTEN_PORT` (`4662` par défaut, en TCP **et** en UDP) depuis votre routeur vers cette machine. Si vous changez de port, ajustez `LISTEN_PORT` dans votre `.env`. |
| **Pile VPN (gluetun), port forwarding** | Mettez `VPN_PORT_FORWARDING=on` dans votre `.env` **et** `port_sync.enabled: true` dans `crawler.yml`. Votre fournisseur VPN doit gérer le port forwarding ([liste gluetun](https://github.com/qdm12/gluetun-wiki/tree/main/setup/providers)). |

Sur la route VPN, le nœud aligne désormais le client eMule sur le port forwardé entièrement **dans
son propre conteneur** : il redémarre ce seul processus avec `s6-svc`. Il n'y a plus ni socket
Docker, ni proxy de socket, ni service supplémentaire dans la boucle.

Compromis, activation pas à pas et vérification :
[runbook d'administration, § High-ID](administration.md#high-id-optionnel--devenir-joignable).

---

## Annexe D. Ports et métriques

- **Changer un port web.** Dans votre `.env` : `WEBUI_PORT` (`8080` par défaut, le catalogue) et
  `AMULEWEB_PORT` (`4711` par défaut, l'interface d'aMule). Utile si l'un d'eux est déjà pris sur
  votre machine. Ils ne changent que le côté **hôte** ; dans le conteneur, les ports sont figés.
- **Derrière un reverse proxy.** Si vous mettez un proxy devant le port 8080, réglez
  `webui.amule_url` dans `crawler.yml` sur l'adresse à laquelle **le navigateur** peut joindre
  l'interface d'aMule — cette clé n'est que la cible du lien de navigation, et c'est le navigateur,
  pas le conteneur, qui la résout. Sa valeur par défaut est `http://localhost:4711`.
- **Métriques.** Le crawler expose un point d'accès Prometheus `/metrics` sur le port configuré par
  `observability.metrics.port` dans `crawler.yml` (`9090` par défaut). **Ni Prometheus ni Grafana ne
  sont livrés avec la pile** : si vous voulez des tableaux de bord, faites pointer votre propre
  Prometheus sur le nœud. Ce port n'est pas publié sur l'hôte par défaut : ajoutez-lui un mapping
  sur le service `mulewatch` de votre fichier de pile — et traitez-le comme le catalogue, il n'a pas
  d'authentification propre.
- **Couper les métriques.** Mettez `observability.metrics.enabled: false` dans `crawler.yml`. Le
  crawler et le catalogue continuent de fonctionner normalement.

Détail des métriques et exposition derrière un reverse proxy :
[runbook d'administration, § Métriques Prometheus](administration.md#métriques-prometheus) et
[§ Exposition derrière un reverse proxy](administration.md#exposition-derrière-un-reverse-proxy).

---

## Annexe E. Migrer un nœud 1.x vers la 2.0

**Lisez ceci avant tout `docker compose pull` sur un nœud existant.** La 2.0 remplace deux images et
jusqu'à quatre services par une image et un service, et sort vos données des volumes nommés de
Docker pour les poser dans de simples dossiers. Il n'y a **ni code de compatibilité, ni migration
automatique** : vous faites cela à la main, une fois, et le vieux nœud doit être arrêté pendant
l'opération.

Votre dossier de travail est celui qui contient l'ancien `compose.yaml`. Toutes les commandes
ci-dessous se lancent depuis là.

**Étape 1 — arrêter le vieux nœud.** Sans `-v` : les volumes nommés doivent survivre, ce sont vos
données et votre retour arrière.

```
docker compose down
```

(ou `docker compose -f gluetun.compose.yml down` si vous étiez sur la pile VPN.)

**Étape 2 — copier chaque volume nommé dans son nouveau dossier.** Le vieux nœud gardait
`catalog.db`, `local.db` et l'état d'aMule dans des volumes nommés ; la 2.0 les lit depuis `data/`
et `amule/`. Copiez, ne déplacez pas : laisser les volumes intacts est ce qui rend possible le
retour arrière décrit plus bas.

```
mkdir -p data amule downloads/incoming downloads/temp
docker run --rm -v mulewatch_catalog-db:/src -v "$PWD/data":/dst alpine sh -c "cp -a /src/. /dst/"
docker run --rm -v mulewatch_local-db:/src   -v "$PWD/data":/dst alpine sh -c "cp -a /src/. /dst/"
docker run --rm -v mulewatch_amule-state:/src -v "$PWD/amule":/dst alpine sh -c "cp -a /src/. /dst/"
```

Lancez d'abord `docker volume ls` si vous avez un doute sur les noms : un nœud créé avant le
renommage du projet porte un préfixe `deploy_` au lieu de `mulewatch_`.

Les deux volumes étaient montés sur `/data/catalog` et `/data/local`, donc la racine de chacun
contient déjà son fichier de base : les copier tous les deux dans `data/` les pose côte à côte, ce
qui est exactement là où la 2.0 les cherche. Vérifiez-le avant de continuer :

```
ls data/     # doit montrer catalog.db et local.db, côte à côte
```

**Étape 3 — déplacer vos trois fichiers de config à la racine du dossier de travail.** Ils vivaient
dans `config/crawler/` ; la 2.0 les monte depuis le voisinage du fichier compose.

```
mv config/crawler/crawler.yml config/crawler/targets.yml config/crawler/matcher.yml .
rmdir config/crawler config
```

**Étape 4 — éditer `crawler.yml`.** Quatre choses à retirer, trois à changer, une à ajouter :

- **retirez** toute la liste `amules:` — le conteneur contient exactement un client eMule, à une
  adresse figée dans le code (`127.0.0.1:4712`) ;
- **retirez** `download.endpoint:` (même raison) ;
- **retirez** `port_sync.restarter_url:` — il n'y a plus de proxy Docker à qui parler ;
- **ajoutez**, au niveau racine, `amule_ec_password: ${AMULE_EC_PASSWORD}` ;
- **changez** `catalog_db_path` en `/data/catalog.db` et `local_db_path` en `/data/local.db` ;
- **changez** `download.output_dir` en `/downloads` ;
- **changez** `port_sync.gluetun_control_url` en `http://localhost:8000` (mulewatch partage
  désormais le namespace réseau de gluetun, donc son serveur de contrôle est sur localhost).

Le `deploy/crawler.yml` livré avec la 2.0 fait référence : comparez le vôtre au sien en cas de
doute.

**Étape 5 — ajouter les nouvelles variables à `.env`, puis prendre possession des dossiers.** La
2.0 exige quatre variables là où la 1.x en exigeait une. Ajoutez `PUID`, `PGID` et `WEBUI_PWD` (voir
l'étape 4 du parcours principal), puis donnez les données copiées à cet uid, puisqu'elles sortent
de volumes appartenant à quelqu'un d'autre :

```
sudo chown -R "$PUID:$PGID" data amule downloads
```

**Étape 6 — démarrer la nouvelle pile.** Le fichier de la pile directe est désormais `compose.yml`,
et non `compose.yaml` :

```
docker compose up -d
docker compose ps        # one service, `mulewatch`, Up (healthy) after ~30 s
```

### Ce qui est repris, et ce qui ne l'est pas

- **Votre `amule.conf` existant est conservé**, à une clé près. Le conteneur écrit le fichier quand
  il est absent, et à chaque boot il réconcilie `ECPassword` dans `[ExternalConnect]` avec
  `AMULE_EC_PASSWORD` : cette variable fait autorité, donc la faire tourner revient à éditer `.env`
  et à redémarrer. Tout autre réglage reste le vôtre. Vérifiez qu'`IncomingDir` et `TempDir`
  pointent sur `/downloads/incoming` et `/downloads/temp`, et corrigez-les à la main sinon :
  ```
  grep -E "^(Incoming|Temp)Dir" amule/amule.conf
  ```
- **Votre catalogue est repris intact.** `catalog.db` est append-only et son schéma n'est pas touché
  par cette version.
- **Le backoff de recherche persisté du crawler repart de zéro, une fois.** Le nom interne du client
  eMule est désormais une constante, `amuled`, là où la 1.x le lisait dans `crawler.yml`
  (typiquement `amule-1`). L'état de backoff et l'avancement de l'ordonnanceur sont indexés sur ce
  nom, donc les lignes écrites sous l'ancien nom sont ignorées et le nœud démarre son premier cycle
  2.0 avec une ardoise vierge. C'est sans gravité — l'effet est un cycle qui réessaie un canal
  qu'il aurait sinon mis en pause — mais autant le savoir avant de vous demander pourquoi les
  journaux semblent plus bavards que d'habitude au premier boot.
- **Le label `instance` a disparu** des métriques Prometheus qui le portaient. Si vous aviez
  construit un tableau de bord qui groupe dessus, retirez cette dimension : avec un seul client,
  c'était une constante.

### Retour arrière

L'image 1.x est toujours publiée, sous son **ancien nom** :
`ghcr.io/geoffreycoulaud/mulewatch-crawler`. Ce paquet est figé en 1.x et **n'est délibérément
jamais supprimé** — il est exactement ce chemin de retour arrière. Pour revenir : restaurez votre
ancien `compose.yaml` et votre `config/crawler/` (git, ou votre sauvegarde), faites pointer
`IMAGE_TAG` sur le tag 1.x que vous utilisiez, et `docker compose up -d`. Les volumes nommés ont
seulement été copiés, jamais déplacés ni supprimés, donc le vieux nœud retrouve ses données là où il
les avait laissées.

Une fois le nouveau nœud éprouvé — laissez-lui quelques jours — vous pouvez supprimer les anciens
volumes avec `docker volume rm mulewatch_catalog-db mulewatch_local-db mulewatch_amule-state`. C'est
le point de non-retour : faites-le en dernier, et seulement après avoir vérifié que
`data/catalog.db` contient bien votre historique.

---

## Glossaire minimal

| Terme | Sens |
|---|---|
| **service** | Une brique de la pile : un conteneur géré par `docker compose`. Un nœud est un service, `mulewatch` (deux avec le VPN, qui ajoute `gluetun`). |
| **s6** | Le petit superviseur qui fait tourner les trois processus du conteneur (`amuled`, `amuleweb`, `mulewatch`) et en relance un s'il meurt. |
| **eD2k / Kad** | Les deux réseaux eMule surveillés : eDonkey2000 (serveurs centraux) et Kademlia (décentralisé, sans serveur). |
| **Low-ID / High-ID** | Le degré de joignabilité de votre nœud sur eD2k. High-ID = la machine est joignable depuis l'extérieur (plus de sources directes). Low-ID fonctionne aussi, simplement moins bien. |
| **IncomingDir** | Le dossier où le client eMule écrit un fichier terminé. Ici, il est monté en bind sur `downloads/incoming` dans votre dossier de travail. |

---

## Pour aller plus loin

- [Runbook d'administration](administration.md) : cycle de vie, High-ID, métriques, durcissement,
  outils de catalogue, limites connues.
- [Runbook de dépannage](troubleshooting.md) : du symptôme à la cause à la solution.
- [Légalité et vie privée](../legal-and-privacy.md) : ce que mulewatch fait, et surtout ce qu'il ne
  fait pas.
