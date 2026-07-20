# Déployer un nœud mulewatch

Le sujet du catalogue est **le fichier, jamais la personne**.

Ce guide vous mène de zéro à un nœud qui tourne. Par défaut, il tourne en mode **observer** : il
cherche et catalogue, mais ne télécharge ni ne partage rien. À la fin, vous aurez un catalogue web
sur `http://localhost:8080` et des tableaux de bord sur `http://localhost:3000`.

Votre adresse IP est visible des autres pairs du réseau eMule : c'est le fonctionnement public et
normal de ce réseau. Ce que mulewatch fait et ne fait pas est détaillé dans
[légalité et confidentialité](../legal-and-privacy.md) ; pour masquer votre IP derrière un VPN, voir
l'annexe A.

Suivez les sept étapes dans l'ordre : elles suffisent à obtenir un nœud qui tourne. Chacune se
termine par un **Point de contrôle** qui vous dit ce que vous devez voir, et sinon quelle fiche de
dépannage ouvrir. Les variantes (VPN, téléchargement, High-ID, monitoring) sont en annexe : faites
d'abord les sept étapes, puis lisez seulement l'annexe qui vous concerne, chacune décrivant ce
qu'elle ajoute à cette voie.

---

## 1. Ce qu'il vous faut

- **Une machine qui reste allumée.** Un vieux PC ou un mini-PC suffit : le nœud n'est utile que
  s'il veille en continu. Inutile d'y toucher une fois lancé.
- **Une connexion Internet permanente.**
- **Environ 2 Go de RAM libres** et **environ 5 Go d'espace disque** au départ (le catalogue grossit
  lentement ensuite).
- **De quoi ouvrir un terminal** : l'application Terminal sous macOS et Linux, PowerShell sous
  Windows.

---

## 2. Installer Docker

mulewatch tourne dans Docker. Installez-le depuis la page officielle, tenue à jour et valable pour
chaque système : <https://docs.docker.com/get-started/get-docker/>.

- **Windows / macOS** : installez Docker Desktop, puis **lancez-le** et attendez qu'il indique qu'il
  tourne.
- **Linux** : installez Docker Engine (choix « Server » sur cette page), puis suivez ses étapes de
  post-installation pour utiliser `docker` sans `sudo`.

**Point de contrôle.** Tapez :

```
docker compose version
```

Vous devez voir une ligne du type `Docker Compose version v2.x.x` (plus récent convient aussi). Si
vous obtenez `command not found` ou une version `1.x`, ouvrez la fiche
[« Docker introuvable ou compose v1 »](troubleshooting.md#docker-introuvable-ou-compose-v1). Si plus
tard une commande répond `Cannot connect to the Docker daemon` (sous Windows
`error during connect ...`), c'est que le moteur Docker n'est pas démarré : ouvrez la fiche
[« Docker est installé mais ne répond pas »](troubleshooting.md#docker-est-installé-mais-ne-répond-pas).

---

## 3. Créer votre dossier de travail

1. Ouvrez <https://github.com/GeoffreyCoulaud/mulewatch>, cliquez sur le bouton vert **`Code`**, puis
   sur **`Download ZIP`**.
2. Décompressez le fichier téléchargé. À l'intérieur se trouve un dossier **`deploy`** : c'est le
   seul dont vous avez besoin.
3. **Copiez ce dossier `deploy`** là où vous voulez travailler et renommez-le à votre guise, par
   exemple **`mulewatch`**. Ce sera votre **dossier de travail**. Le reste du ZIP ne sert pas, vous
   pouvez le supprimer.
4. Ouvrez un terminal **dans ce dossier de travail** : clic droit « Ouvrir dans le terminal » sous
   Windows ; sous macOS/Linux, déplacez-vous-y avec `cd`.

Si vous connaissez git, l'alternative équivalente est
`git clone https://github.com/GeoffreyCoulaud/mulewatch.git`, puis prenez son sous-dossier `deploy`
comme dossier de travail.

**Point de contrôle.** Depuis ce dossier, tapez :

```
ls
```

La liste doit contenir **`compose.yaml`** (sous Windows/PowerShell, `ls` affiche un tableau :
cherchez `compose.yaml` dans la colonne `Name`). Sinon, vous n'êtes pas dans le bon dossier :
placez-vous dans le dossier de travail (celui qui contient `compose.yaml`) et réessayez.

---

## 4. Vos deux mots de passe

Votre dossier de travail contient un fichier `.env.example`. **Faites-en une copie nommée `.env`**,
ouvrez cette copie dans un éditeur de texte et remplacez les deux valeurs `change-me` :

- `AMULE_EC_PASSWORD` : un mot de passe d'au moins **12 caractères**, que vous inventez. Il relie le
  crawler au client eMule ; notez-le quelque part.
- `GRAFANA_PWD` : le mot de passe du compte `admin` des tableaux de bord.

Laissez tout le reste tel quel (les autres valeurs ne servent qu'aux variantes en annexe). Ne
laissez pas les `change-me` en place : ce sont deux mots de passe en clair, donc deux portes
ouvertes.

> Le fichier `.env` commence par un point, donc le Finder de macOS et certains gestionnaires de
> fichiers Linux le **cachent**. Le plus fiable, partout, est de le créer et l'éditer depuis le
> terminal : `cp .env.example .env`, puis `nano .env` (macOS/Linux) ou `notepad .env` (Windows).

---

## 5. Lancer

Depuis votre dossier de travail :

```
docker compose up -d
```

Au tout premier lancement, Docker télécharge les images : cela peut prendre quelques minutes selon
votre connexion.

**Point de contrôle.** Une fois la commande revenue, tapez :

```
docker compose ps
```

Vous devez voir **quatre services**, chacun avec un statut qui commence par `Up` : `crawler`,
`amuled`, `prometheus`, `grafana`. (`crawler` peut passer à `Up (healthy)` après quelques secondes :
c'est encore mieux.) Si un service est en `Restarting` ou `Exited`, ouvrez la fiche
[« Un conteneur redémarre en boucle »](troubleshooting.md#un-conteneur-redémarre-en-boucle). Si le
lancement échoue sur un message de port déjà utilisé, ouvrez la fiche
[« Le port est déjà pris »](troubleshooting.md#le-port-est-déjà-pris).

---

## 6. Voir votre nœud

Ouvrez **<http://localhost:8080>** dans votre navigateur : c'est le catalogue, en lecture seule.
Vous devez voir le tableau de bord de mulewatch, avec l'identifiant de votre nœud et la liste des
épisodes ciblés (colonne **Statut** à `none` au départ). **Si cette page s'affiche, votre nœud
tourne.**

Le catalogue est **vide au début** et se remplit au fil des heures, à mesure que des fichiers sont
croisés sur le réseau (certaines cibles rares peuvent prendre des jours à réapparaître : c'est le
principe du lost media). Pour suivre l'activité de recherche, ouvrez la page **Nodes** : après le
premier cycle (quelques minutes), elle affiche le numéro du dernier cycle et son horodatage, qui
avancent à chaque rechargement.

Les **tableaux de bord** sont sur **<http://localhost:3000>** (Grafana). Connectez-vous avec
l'identifiant `admin` et le mot de passe `GRAFANA_PWD` choisi à l'étape 4.

Si votre nœud tourne sur un serveur distant, remplacez `localhost` par l'adresse IP ou le nom de ce
serveur.

**Point de contrôle.** La page <http://localhost:8080> s'ouvre et affiche le tableau de bord
(identifiant du nœud, liste des cibles). Si elle ne se charge pas du tout (connexion refusée), ouvrez
la fiche [« La webui reste vide »](troubleshooting.md#la-webui-reste-vide).

> Si Grafana refuse le mot de passe `admin` que vous avez choisi (souvent une coquille dans
> `GRAFANA_PWD`) : Grafana ne l'applique qu'à son **premier** démarrage, il faut donc réinitialiser
> son état local. Depuis votre dossier de travail (pile VPN : ajoutez `-f gluetun.compose.yml` à
> chaque commande) :
>
> ```
> docker compose down
> docker volume rm mulewatch_grafana-data
> docker compose up -d
> ```
>
> Vous ne perdez que l'état local de Grafana : les tableaux de bord sont reprovisionnés depuis des
> fichiers, et le catalogue n'est pas touché.

---

## 7. Vivre avec le nœud

Votre nœud est autonome. Quelques gestes utiles, tous depuis votre dossier de travail :

- **Mettre à jour.** Les images ne se mettent **pas** à jour toutes seules : vous décidez quand.

  ```
  docker compose pull
  ```

  ```
  docker compose up -d
  ```

  `up -d` ne recrée que les conteneurs dont l'image a changé. Vos données ne bougent pas.

- **Arrêter le nœud.**

  ```
  docker compose down
  ```

  Le catalogue et l'état vivent dans des volumes Docker nommés : ils **persistent**. Un `down` puis
  un `up -d` plus tard retrouve tout. Pour **tout effacer** (catalogue compris), il faudrait ajouter
  `-v` à `down` : ne le faites que si c'est vraiment votre intention.

- **Redémarrage de la machine.** Les conteneurs reviennent seuls au démarrage de l'hôte (Docker doit
  être lancé en service système). Aucune commande à retaper.

**Point de contrôle.** Vous pouvez le vérifier : un `docker compose down` suivi d'un
`docker compose up -d` retrouve, sur <http://localhost:8080>, tout ce que le catalogue avait déjà vu.
Vos données survivent à l'arrêt.

> Cycle de vie plus en détail (diagnostic après panne, planification disque, reboot) :
> [runbook d'administration, § Cycle de vie & données](administration.md#cycle-de-vie--données).
> Si votre nœud a été créé **avant le renommage en `mulewatch`** et semble avoir perdu son catalogue
> après une mise à jour, la même section explique comment récupérer les anciens volumes.

---

## Annexe A. Passer derrière un VPN

Pour masquer votre IP des autres pairs eD2k/Kad, faites passer amuled par un VPN grâce au conteneur
`gluetun`.

**Delta par rapport à la voie royale :**

1. **Un fournisseur VPN qui supporte WireGuard.** C'est indispensable : gluetun établit le tunnel en
   WireGuard.
2. **Trois variables supplémentaires** dans votre `.env` :

   | Variable | Quoi |
   |---|---|
   | `WIREGUARD_PRIVATE_KEY` | La clé privée WireGuard, fournie dans l'espace client de votre VPN. |
   | `VPN_SERVICE_PROVIDER` | Le nom du fournisseur, par exemple `protonvpn`, `pia`, `privatevpn`. |
   | `SERVER_COUNTRIES` | Le ou les pays de sortie, en anglais, par exemple `Switzerland`. |

3. **Un autre fichier de pile.** Au lieu de `docker compose up -d`, vous utilisez la pile
   `gluetun.compose.yml`, et vous ajoutez `-f gluetun.compose.yml` à **chaque** commande compose
   ensuite (`ps`, `logs`, `pull`, `down`...) :

   ```
   docker compose -f gluetun.compose.yml up -d
   ```

Cette pile ajoute un service `gluetun` : `docker compose -f gluetun.compose.yml ps` affiche donc cinq
services au lieu de quatre. amuled partage le réseau de gluetun. Si vous combinez VPN **et** mode
download (annexe B), un service `docker-proxy` démarre en plus (un proxy du socket Docker utilisé
par le port-sync) : sa présence dans `ps` est normale.

---

## Annexe B. Activer le mode download

En mode observer (défaut), le nœud ne télécharge rien. Le mode **download** ajoute le téléchargement
d'un candidat sûr, dans un environnement isolé, avec vérification antivirus avant catalogage. Rien
n'est jamais re-partagé ni exécuté.

> **À lire avant d'activer le mode download.** Quatre contraintes de déploiement doivent être
> respectées, sinon les fichiers finis restent bloqués sans être catalogués (échec silencieux) :
> volume `quarantine` partagé entre crawler et amuled, volume sur un système de fichiers Linux normal
> (pas vfat/NTFS/HFS), pas de catégories amuled, et un amuled dédié au crawler avec un jeu partagé
> restreint. Le détail est dans
> [`docs/reference/2026-06-17-amuled-completion-behavior.md`](../reference/2026-06-17-amuled-completion-behavior.md).

**Delta par rapport à la voie royale :**

1. Dans `config/crawler/crawler.yml`, passez `download.enabled: false` à **`true`**.
2. Ajoutez `--profile download` à **chaque** commande compose :

   ```
   docker compose --profile download up -d
   ```

   (Derrière un VPN, combinez avec l'annexe A : `-f gluetun.compose.yml --profile download`.)

Ce profil ajoute deux services, `verifier` et `freshclam` :
`docker compose --profile download ps` doit les montrer `Up` en plus des quatre autres. Côté RAM,
le verifier peut consommer jusqu'à 2 Go à lui seul (analyse antivirus) : prévoyez de la marge
au-delà des ~2 Go du mode observer.

**Comportements transitoires normaux au premier démarrage en download :**

- Le `crawler` **redémarre en boucle pendant 1 à 2 minutes** : il refuse de démarrer tant que le
  `verifier` n'est pas sain. Dès que le verifier répond, la boucle se stabilise.
- Les tout premiers fichiers ressortent avec le verdict **`suspicious`** le temps que clamav
  télécharge sa base de signatures (**5 à 20 minutes** selon la connexion). Ensuite les verdicts se
  normalisent.

---

## Annexe C. High-ID (optionnel)

Par défaut, votre nœud est **Low-ID** : il catalogue et télécharge, mais avec moins de sources
directes. Devenir **High-ID** (joignable de l'extérieur) apporte plus de sources et une recherche
plus efficace. Ce n'est **pas requis** pour cataloguer. Deux voies, selon votre pile :

| Voie | Comment l'activer |
|---|---|
| **Pile par défaut, port ouvert** | Redirigez `LISTEN_PORT` (par défaut `4662`, en TCP **et** UDP) depuis votre box/routeur vers cette machine. Si vous changez de port, ajustez `LISTEN_PORT` dans votre `.env`. |
| **Pile VPN (gluetun), port forwarding** | Mettez `VPN_PORT_FORWARDING=on` dans votre `.env` **et** `port_sync.enabled: true` dans `config/crawler/crawler.yml`. Votre fournisseur VPN doit supporter le port forwarding ([liste gluetun](https://github.com/qdm12/gluetun-wiki/tree/main/setup/providers)). |

Compromis, activation pas à pas et vérification :
[runbook d'administration, § High-ID](administration.md#high-id-optionnel--devenir-joignable).

---

## Annexe D. Régler le monitoring

Le catalogue (webui) et les tableaux de bord (Prometheus + Grafana) sont **toujours actifs**, sans
profil à ajouter. Quelques réglages, tous depuis votre dossier de travail :

- **Changer les ports.** Dans votre `.env`, `WEBUI_PORT` (défaut `8080`) et `GRAFANA_PORT` (défaut
  `3000`). Utile si ces ports sont déjà pris sur votre machine.
- **Se passer des tableaux de bord.** Si vous ne voulez que le catalogue, arrêtez les deux services
  de métriques :

  ```
  docker compose stop grafana prometheus
  ```

  Le crawler et le catalogue continuent normalement.

Détail des métriques et exposition derrière un reverse proxy :
[runbook d'administration, § Métriques Prometheus](administration.md#métriques-prometheus) et
[§ Exposition derrière un reverse proxy](administration.md#exposition-derrière-un-reverse-proxy).

---

## Glossaire minimal

| Terme | Signification |
|---|---|
| **service** | Une brique du nœud : un conteneur géré par `docker compose` (par exemple `crawler`, `amuled`, `grafana`). |
| **eD2k / Kad** | Les deux réseaux d'eMule surveillés : eDonkey2000 (serveurs centralisés) et Kademlia (décentralisé, sans serveur). |
| **Low-ID / High-ID** | La joignabilité de votre nœud sur eD2k. High-ID = la machine est accessible de l'extérieur (plus de sources directes). Low-ID fonctionne aussi, en moins optimal. |
| **quarantine** | Le dossier isolé où atterrissent les fichiers téléchargés (mode download) avant leur vérification antivirus. |

---

## Pour aller plus loin

- [Runbook d'administration](administration.md) : cycle de vie, High-ID, réglage RAM/clamav,
  métriques, durcissement, outils de catalogue, limites connues.
- [Runbook de dépannage](troubleshooting.md) : du symptôme à la cause à la solution.
- [Légalité et confidentialité](../legal-and-privacy.md) : ce que mulewatch fait, et surtout ce
  qu'il ne fait pas.
