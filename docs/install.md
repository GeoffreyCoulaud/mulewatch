# Installer un nœud mulewatch

Le sujet du catalogue est **le fichier, jamais la personne**.

Ce guide vous mène de rien à un nœud qui tourne. Tel quel, le nœud cherche, catalogue, notifie et
télécharge les fichiers qu'il identifie avec certitude dans un dossier `downloads/` posé à côté de
votre fichier compose. À la fin, vous aurez un catalogue web sur `http://localhost:8080`.

Votre adresse IP est visible des autres pairs du réseau eMule : c'est ainsi que ce réseau fonctionne
publiquement et normalement. Ce que mulewatch fait et ne fait pas est détaillé dans
[légalité et vie privée](legal.md) ; pour masquer votre IP derrière un VPN, voir
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
  [runbook d'administration, § Planification disque](operate.md#planification-disque).
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
[« Docker introuvable ou compose v1 »](troubleshooting-start.md#docker-introuvable-ou-compose-v1). Si plus
tard une commande répond `Cannot connect to the Docker daemon` (sous Windows
`error during connect ...`), c'est que le moteur Docker n'est pas démarré : ouvrez la fiche
[« Docker est installé mais ne répond pas »](troubleshooting-start.md#docker-est-installé-mais-ne-répond-pas).

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
> [« Une variable obligatoire manque »](troubleshooting-start.md#une-variable-obligatoire-manque)
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
> [runbook d'administration, § Exposition derrière un reverse proxy](operate.md#exposition-derrière-un-reverse-proxy).

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
[« Un conteneur redémarre en boucle »](troubleshooting-start.md#un-conteneur-redémarre-en-boucle). Si le
démarrage échoue sur un message de port déjà occupé, ouvrez la fiche
[« Le port est déjà pris »](troubleshooting-start.md#le-port-est-déjà-pris).

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
ouvrez la fiche [« La webui reste vide »](troubleshooting-start.md#la-webui-reste-vide).

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
> [runbook d'administration, § Cycle de vie & données](operate.md#cycle-de-vie--données).

---

## Pour aller plus loin

- [Runbook d'administration](operate.md) : cycle de vie, High-ID, métriques, durcissement,
  outils de catalogue, limites connues.
- [Runbook de dépannage](troubleshooting.md) : du symptôme à la cause à la solution.
- [Légalité et vie privée](legal.md) : ce que mulewatch fait, et surtout ce qu'il ne
  fait pas.
