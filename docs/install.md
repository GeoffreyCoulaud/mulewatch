# Installer un nœud

Sept étapes, dans l'ordre, et vous avez un nœud qui tourne. À la fin, un catalogue web sur
`http://localhost:8080`, et un nœud qui cherche, catalogue, vous notifie et télécharge ce qu'il
identifie avec certitude dans un dossier `downloads/` posé à côté de votre fichier compose.

Chaque étape finit par un **Point de contrôle** : ce que vous devez voir, et quelle page ouvrir
sinon.

Votre adresse IP est visible des autres pairs du réseau eMule, c'est le fonctionnement normal de ce
réseau. Pour la masquer, voyez [Passer derrière un VPN](vpn.md) une fois les sept étapes faites. Ce
que le nœud enregistre et ce que vous risquez sont dans [Légalité et vie privée](legal.md).

Si vous **mettez à niveau un nœud 1.x**, lisez d'abord [Migrer un nœud 1.x](migration-1x.md) et ne
lancez rien avant : la migration se fait à la main, une fois.

---

## 1. Ce qu'il vous faut

- **Une machine qui reste allumée.** Un vieux PC ou un mini-PC suffit : un nœud n'est utile que s'il
  surveille en continu. Vous n'y toucherez plus une fois lancé.
- **Une connexion Internet permanente.**
- **Environ 2 Go de RAM libre** et 5 Go de disque pour commencer. Le catalogue grossit lentement, et
  les fichiers téléchargés s'accumulent par-dessus : voir
  [Faire tourner un nœud, § Planification disque](operate.md#planification-disque).
- **De quoi ouvrir un terminal** : l'application Terminal sur macOS et Linux, PowerShell sur
  Windows.

---

## 2. Installer Docker

mulewatch tourne dans Docker. Installez-le depuis la page officielle, valable pour tous les
systèmes : <https://docs.docker.com/get-started/get-docker/>.

- **Windows et macOS** : installez Docker Desktop, puis lancez-le et attendez qu'il indique qu'il
  tourne.
- **Linux** : installez Docker Engine (le choix « Server » sur cette page), puis suivez ses étapes
  de post-installation pour utiliser `docker` sans `sudo`.

**Point de contrôle.** Tapez :

```
docker compose version
```

Vous devez lire `Docker Compose version v2.x.x`, ou un numéro plus récent. Si vous obtenez
`command not found` ou une version `1.x`, ouvrez
[« Docker introuvable ou compose v1 »](troubleshooting-start.md#docker-introuvable-ou-compose-v1).

Si plus tard une commande répond `Cannot connect to the Docker daemon` (sous Windows
`error during connect ...`), le moteur Docker n'est pas démarré : ouvrez
[« Docker est installé mais ne répond pas »](troubleshooting-start.md#docker-est-installé-mais-ne-répond-pas).

---

## 3. Créer votre dossier de travail

1. Ouvrez <https://github.com/GeoffreyCoulaud/mulewatch>, cliquez sur le bouton vert **`Code`**,
   puis **`Download ZIP`**.
2. Décompressez le fichier. Seul son dossier `deploy` vous est utile.
3. Copiez ce dossier `deploy` où vous voulez travailler et renommez-le à votre goût, par exemple
   `mulewatch`. C'est votre **dossier de travail**. Le reste du ZIP peut être supprimé.
4. Ouvrez un terminal dans ce dossier : clic droit « Ouvrir dans le terminal » sous Windows, un `cd`
   sous macOS et Linux.

Si vous connaissez git, clonez
`https://github.com/GeoffreyCoulaud/mulewatch.git` et prenez son sous-dossier `deploy`.

Ce que vous y trouvez :

| Entrée | Ce que c'est |
|---|---|
| `compose.yml` | la pile que vous lancez (celle de ces sept étapes) |
| `gluetun.compose.yml` | la variante VPN, voir [Passer derrière un VPN](vpn.md) |
| `base.compose.yml` | la part commune aux deux piles ; ne se lance pas seul |
| `.env.example` | le modèle de votre propre `.env` (étape 4) |
| `crawler.yml` | les réglages du nœud (téléchargement oui/non, intervalles, notifications) |
| `targets.yml`, `matcher.yml` | les épisodes recherchés, et les règles qui les reconnaissent |
| `amule/` | l'état du client eMule (`amule.conf`, liste de serveurs, nœuds Kad) |
| `data/` | votre catalogue : `catalog.db` et `local.db` |
| `downloads/` | `incoming/` pour les fichiers terminés, `temp/` pour les fichiers partiels |

Les trois derniers sont vides au départ et se remplissent quand le nœud tourne. **Ce sont de simples
dossiers sur votre disque**, pas des volumes Docker : vous pouvez ouvrir `data/catalog.db` avec
n'importe quel outil SQLite, et sauvegarder l'ensemble par une copie.

**Point de contrôle.** Depuis ce dossier, tapez :

```
ls
```

La liste doit contenir **`compose.yml`** (sous PowerShell, cherchez-le dans la colonne `Name`).
Sinon, vous n'êtes pas dans le bon dossier.

---

## 4. Votre mot de passe

Copiez `.env.example` en `.env`, ouvrez la copie dans un éditeur, et renseignez les quatre valeurs
obligatoires :

| Variable | Ce qu'il faut y mettre |
|---|---|
| `AMULE_EC_PASSWORD` | Un mot de passe d'au moins **12 caractères**, de votre choix. Il relie le crawler au client eMule dans le conteneur ; notez-le quelque part. |
| `WEBUI_PWD` | Un autre mot de passe de votre choix. Il protège l'interface web d'aMule sur le port 4711 (étape 6). |
| `PUID` | Votre identifiant d'utilisateur. Sous macOS et Linux, lancez `id -u` ; sous Windows, laissez `1000`. |
| `PGID` | Votre identifiant de groupe. Sous macOS et Linux, lancez `id -g` ; sous Windows, laissez `1000`. |

Laissez le reste tel quel, les autres valeurs ne servent qu'au VPN. **Ne laissez aucun `change-me`
en place** : ce sont des mots de passe en clair, donc des portes ouvertes.

`PUID` et `PGID` sont ce qui garde `data/`, `amule/` et `downloads/` lisibles et modifiables par
vous depuis l'hôte, sans `sudo`. Le conteneur prend possession de ces dossiers à chaque démarrage,
avec exactement ces numéros.

Les quatre sont obligatoires : le conteneur refuse de démarrer si l'une manque, et sort avec une
seule ligne nommant la variable. Si cela arrive, voyez
[« Une variable obligatoire manque »](troubleshooting-start.md#une-variable-obligatoire-manque).

Le fichier `.env` commence par un point, donc le Finder de macOS et certains gestionnaires de
fichiers Linux le cachent. Le plus fiable partout est de passer par le terminal :
`cp .env.example .env`, puis `nano .env` sous macOS et Linux, `notepad .env` sous Windows.

> **⚠ Le port 8080 n'a aucune authentification.** `WEBUI_PWD` protège le port **4711 seulement**. Le
> catalogue mulewatch sur le port **8080** est servi **sans mot de passe, sans connexion, sans jeton
> CSRF**, et il expose le catalogue, les contrôles de crawl (pause, passe forcée, redémarrage)
> **et une console SQL en lecture seule** à quiconque atteint ce port. C'est voulu : l'authentification
> est déléguée à ce que vous mettez devant. Sur une machine joignable depuis Internet, mettez-le
> derrière un reverse proxy authentifié, ou un VPN, ou ne publiez pas le 8080 du tout. Voir
> [Faire tourner un nœud, § Exposition derrière un reverse proxy](operate.md#exposition-derrière-un-reverse-proxy).

---

## 5. Lancer

Depuis votre dossier de travail :

```
docker compose up -d
```

Au premier lancement, Docker télécharge l'image, ce qui peut prendre quelques minutes.

**Point de contrôle.** Une fois la commande rendue, tapez :

```
docker compose ps
```

Vous devez voir **un seul service**, `mulewatch`, dont l'état commence par `Up`. Au bout d'une
demi-minute environ, il passe à `Up (healthy)` : le client eMule tourne vraiment à l'intérieur.

S'il est en `Restarting` ou `Exited`, ouvrez
[« Un conteneur redémarre en boucle »](troubleshooting-start.md#un-conteneur-redémarre-en-boucle).
Si le message parle d'un port déjà occupé, ouvrez
[« Le port est déjà pris »](troubleshooting-start.md#le-port-est-déjà-pris).

---

## 6. Voir votre nœud

Votre nœud sert deux pages web :

| Adresse | Ce que c'est | Mot de passe |
|---|---|---|
| <http://localhost:8080> | **Le catalogue mulewatch** : le catalogue en lecture seule, les contrôles de crawl et la console SQL. | **Aucun.** Voir l'avertissement de l'étape 4. |
| <http://localhost:4711> | **L'interface web propre à aMule** : transferts, serveurs, état Kad. Utile pour voir directement le côté eMule. | `WEBUI_PWD` de votre `.env`. |

Ouvrez <http://localhost:8080>. Vous devez voir le tableau de bord, avec l'identifiant de votre nœud
et la liste des épisodes cibles, dont la colonne **Status** affiche `none` au début. **Si cette page
se charge, votre nœud tourne.**

Le catalogue est vide au début et se remplit au fil des heures. Certaines cibles rares mettent des
jours à réapparaître : c'est la nature du lost media. Pour suivre l'activité, ouvrez la page
**Nodes** : après le premier cycle, quelques minutes, elle affiche le numéro et l'horodatage du
dernier cycle, qui avancent à chaque rechargement.

Les fichiers téléchargés atterrissent dans `downloads/incoming`, les fichiers partiels attendent
dans `downloads/temp`. **Rien ne les inspecte** : mulewatch n'ouvre jamais un fichier téléchargé,
donc vérifier qu'un fichier est bien l'épisode voulu est à votre charge.

Si votre nœud tourne sur un serveur distant, remplacez `localhost` par son adresse.

**Point de contrôle.** La page <http://localhost:8080> s'ouvre et affiche le tableau de bord. Si
elle ne se charge pas du tout, ouvrez
[« La webui reste vide »](troubleshooting-start.md#la-webui-reste-vide).

---

## 7. Vivre avec le nœud

Votre nœud est autonome. Trois gestes suffisent au quotidien, tous depuis votre dossier de travail.

**Mettre à jour.** L'image ne se met pas à jour toute seule, c'est vous qui décidez quand :

```
docker compose pull
docker compose up -d
```

`up -d` ne recrée le conteneur que si l'image a changé, et vos données ne bougent pas.

**Arrêter le nœud.**

```
docker compose down
```

Le catalogue, l'état d'eMule et vos téléchargements sont de simples dossiers (`data/`, `amule/`,
`downloads/`). Ils survivent : un `down` suivi plus tard d'un `up -d` retrouve tout. Aucune commande
ne peut les effacer par accident. Pour repartir de zéro, c'est à vous de supprimer `data/`.

**Sauvegarder.** Copiez le dossier de travail, nœud arrêté pour que les bases ne soient pas en cours
d'écriture. C'est toute la sauvegarde.

**Point de contrôle.** Faites un `docker compose down`, puis un `docker compose up -d` : sur
<http://localhost:8080>, le catalogue a gardé tout ce qu'il avait déjà vu.

Pour la suite, redémarrer un seul des trois processus, savoir quoi regarder quand le nœud ne
catalogue plus, prévoir la place disque, lisez [Faire tourner un nœud](operate.md).
