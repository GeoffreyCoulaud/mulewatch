# Déployer un nœud mulewatch

Le sujet du catalogue est **le fichier, jamais la personne**.

Ce guide vous emmène de zéro à un nœud qui tourne, en une quinzaine de minutes une fois Docker
installé. L'installation de Docker et le premier téléchargement des images viennent en plus, selon
votre connexion. Il ne suppose aucune connaissance en ligne de commande au-delà de « suivre un
tutoriel ». L'étape la plus difficile est d'installer Docker : le reste tient en une commande et
deux mots de passe à choisir.

Par défaut, votre nœud tourne en mode **observer** : il ne télécharge rien et ne partage rien. Il
cherche, catalogue et vous laisse consulter le résultat. Une fois lancé, vous aurez un catalogue web
sur `http://localhost:8080` et des tableaux de bord sur `http://localhost:3000`.

Par défaut, votre adresse IP est visible des autres pairs du réseau eMule : c'est le fonctionnement
public et normal de ce réseau. Ce que mulewatch fait et ne fait pas est détaillé dans
[légalité et confidentialité](../legal-and-privacy.md) ; pour masquer votre IP derrière un VPN, voir
l'annexe A.

Suivez les 8 étapes dans l'ordre : c'est la voie royale, celle qui marche à coup sûr. Les variantes
(VPN, téléchargement, High-ID, réglage du monitoring) sont en annexe, sous forme d'ajouts à cette
voie. En cas de blocage, chaque étape se termine par un **Point de contrôle** qui vous dit ce que
vous devez voir, et sinon quelle fiche de dépannage ouvrir.

---

## 1. Ce qu'il vous faut

- **Une machine qui reste allumée.** Un vieux PC ou un mini-PC suffisent : le nœud n'est utile que
  s'il veille en continu. Inutile d'y toucher une fois lancé.
- **Une connexion Internet permanente.**
- **Environ 2 Go de RAM libres** et **environ 5 Go d'espace disque** au départ (le catalogue grossit
  lentement ensuite).
- **De quoi ouvrir un terminal** sur cette machine : l'application Terminal sous macOS et Linux,
  PowerShell sous Windows.

**Point de contrôle.** Ouvrez un terminal sur la machine et tapez :

```
echo bonjour
```

Le terminal doit répondre `bonjour` sur la ligne suivante. Si c'est le cas, vous savez saisir des
commandes : tout le reste du guide fonctionne pareil, une commande à la fois.

---

## 2. Installer Docker

mulewatch tourne dans Docker. Installez-le depuis la documentation officielle, elle est tenue à jour
et couvre chaque système :

- **Windows ou macOS** : Docker Desktop, <https://docs.docker.com/get-started/get-docker/>. Après
  l'installation, **lancez l'application Docker Desktop** et attendez qu'elle indique qu'elle tourne.
- **Serveur Linux** : Docker Engine, <https://docs.docker.com/engine/install/>. Suivez aussi les
  étapes de post-installation du même site pour utiliser `docker` sans `sudo`.

Ne recopiez pas d'instructions d'installation d'ailleurs : ces deux pages sont les sources de
référence.

**Point de contrôle.** Deux vérifications, à réussir toutes les deux. D'abord la version de
`docker compose` :

```
docker compose version
```

Vous devez voir une ligne du type `Docker Compose version v2.x.x` (un numéro plus récent convient
aussi). Ce qui compte : c'est bien `docker compose` en une seule commande. Si vous obtenez
`command not found`, ou une version `1.x` du vieil outil `docker-compose`, ouvrez la fiche
[« Docker introuvable ou compose v1 »](troubleshooting.md#docker-introuvable-ou-compose-v1).

Ensuite, vérifiez que le **moteur** Docker tourne : la commande précédente ne parle qu'au client et
répond même moteur arrêté, alors que `docker ps` exige le moteur.

```
docker ps
```

En cas de succès, vous obtenez un tableau d'en-têtes de colonnes (`CONTAINER ID   IMAGE   ...`),
même vide : c'est bon signe. Si vous obtenez à la place un message d'erreur (`Cannot connect to the
Docker daemon`, ou sous Windows `error during connect ... The system cannot find the file
specified`), le moteur n'est pas démarré : ouvrez la fiche
[« Docker est installé mais ne répond pas »](troubleshooting.md#docker-est-installé-mais-ne-répond-pas).

---

## 3. Obtenir mulewatch

Le plus simple, sans rien installer de plus :

1. Ouvrez la page du projet : <https://github.com/GeoffreyCoulaud/mulewatch>.
2. Cliquez sur le bouton vert **`Code`**, puis sur **`Download ZIP`**.
3. Décompressez le fichier téléchargé. Vous obtenez un dossier nommé **`mulewatch-main`**. Sous
   Windows, « Extraire tout » crée souvent un dossier dans un dossier
   (`mulewatch-main\mulewatch-main`) : le bon dossier est **celui qui contient `deploy`**,
   c'est-à-dire, sous Windows, en général le dossier intérieur.
4. Ouvrez un terminal **dans ce dossier `mulewatch-main`** :
   - **Windows** : clic droit dans le dossier, « Ouvrir dans le terminal ».
   - **macOS / Linux** : ouvrez le Terminal, puis déplacez-vous dans le dossier avec `cd`. Si le
     ZIP a été décompressé dans vos téléchargements, c'est typiquement :

     ```
     cd ~/Téléchargements/mulewatch-main
     ```

     (`cd ~/Downloads/mulewatch-main` si votre système est en anglais.) Astuce fiable : tapez
     `cd ` (avec l'espace après), puis **glissez le dossier depuis le Finder ou le gestionnaire de
     fichiers sur la fenêtre du Terminal** : son chemin exact se colle tout seul, appuyez ensuite
     sur Entrée. Notez que l'option « Ouvrir dans le terminal » du clic droit n'existe pas par
     défaut sur macOS, d'où cette astuce.

Si vous connaissez git, l'alternative équivalente est :

```
git clone https://github.com/GeoffreyCoulaud/mulewatch.git
```

puis ouvrez un terminal dans le dossier `mulewatch` créé.

**Point de contrôle.** Depuis ce dossier, tapez :

```
ls deploy
```

La liste affichée doit contenir `compose.yaml` (sous Windows/PowerShell, `ls` affiche un tableau :
cherchez `compose.yaml` dans la colonne `Name`). Si le terminal répond que le dossier n'existe pas,
c'est que vous n'êtes pas au bon endroit : placez-vous dans le dossier du projet (celui qui contient
`deploy/`, sous Windows en général le dossier intérieur `mulewatch-main\mulewatch-main`) et
réessayez.

---

## 4. Choisir ses deux mots de passe

mulewatch a besoin de deux mots de passe **que vous inventez** : un pour la liaison interne entre le
crawler et le client eMule, un pour l'accès aux tableaux de bord.

Copiez le fichier d'exemple en un vrai fichier de configuration :

```
cp deploy/.env.example deploy/.env
```

Il faut maintenant éditer ce fichier. Son nom commence par un point : sous macOS (Finder) et la
plupart des gestionnaires de fichiers Linux, un tel fichier est **caché** par défaut ; l'Explorateur
de Windows, lui, l'affiche normalement. Le plus simple, partout, est de l'éditer directement depuis
le terminal.

Sous **macOS / Linux**, avec l'éditeur `nano` (déjà présent) :

```
nano deploy/.env
```

Sous **Windows**, avec le Bloc-notes :

```
notepad deploy\.env
```

Dans `nano`, déplacez-vous avec les flèches, modifiez le texte, puis enregistrez avec **Ctrl+O**
suivi d'**Entrée**, et quittez avec **Ctrl+X** (les raccourcis sont rappelés en bas de l'écran). Le
Bloc-notes s'édite comme n'importe quel fichier texte, puis enregistrez avec **Ctrl+S**. Sous macOS,
si vous tenez à un éditeur graphique, évitez TextEdit en mode « texte enrichi » (menu Format,
« Convertir au format Texte » d'abord), sinon il abîme le fichier.

Dans le fichier, remplacez les deux valeurs `change-me` suivantes :

- `AMULE_EC_PASSWORD` : un mot de passe d'au moins **12 caractères**, que vous choisissez. Vous
  n'avez pas à le retenir par cœur, mais notez-le quelque part.
- `GRAFANA_PWD` : le mot de passe du compte `admin` des tableaux de bord Grafana.

Laissez tout le reste du fichier tel quel : les autres valeurs ne servent qu'aux variantes en annexe.

**Point de contrôle.** Toujours depuis le dossier du projet, tapez :

```
grep -E '(AMULE_EC_PASSWORD|GRAFANA_PWD)=change-me' deploy/.env
```

Sous **Windows (PowerShell)**, la vérification équivalente est :

```
Select-String -Path deploy\.env -Pattern 'AMULE_EC_PASSWORD=change-me|GRAFANA_PWD=change-me'
```

La commande ne doit **rien afficher** (le terminal revient directement à la ligne de saisie). Si
elle affiche une ligne, c'est que ce mot de passe est encore le `change-me` d'origine : corrigez-le,
puis relancez la commande. Si vous n'y arrivez pas, ouvrez la fiche
[« Une valeur change-me est restée dans .env »](troubleshooting.md#une-valeur-change-me-est-restée-dans-env).

---

## 5. Lancer

Placez-vous dans le dossier `deploy`, puis démarrez la pile :

```
cd deploy
```

```
docker compose up -d
```

Au tout premier lancement, Docker télécharge les images : cela peut prendre quelques minutes selon
votre connexion. C'est normal, laissez faire.

**Point de contrôle.** Une fois la commande revenue, tapez :

```
docker compose ps
```

Vous devez voir **cinq services**, chacun avec un statut qui commence par `Up` :

- `crawler`
- `amuled`
- `webui`
- `prometheus`
- `grafana`

(`webui` peut afficher `Up (healthy)` après quelques secondes : c'est le mieux.) Si un service est
en `Restarting` ou `Exited`, ouvrez la fiche
[« Un conteneur redémarre en boucle »](troubleshooting.md#un-conteneur-redémarre-en-boucle). Si le
lancement s'est interrompu avec un message d'adresse ou de port déjà utilisé, ouvrez la fiche
[« Le port est déjà pris »](troubleshooting.md#le-port-est-déjà-pris). Si `docker compose up -d`
s'est arrêté tout de suite avec un message `Cannot connect to the Docker daemon` (ou, sous Windows,
`error during connect ... The system cannot find the file specified`), le moteur Docker n'est pas
démarré : ouvrez la fiche
[« Docker est installé mais ne répond pas »](troubleshooting.md#docker-est-installé-mais-ne-répond-pas).

---

## 6. Vérifier que le nœud vit

Le crawler tourne en boucle : à chaque cycle, il génère des recherches et les envoie sur les deux
réseaux d'eMule. Regardez son journal :

```
docker compose logs crawler
```

Dans la première à la troisième minute, vous devez voir apparaître des lignes de cycle. Elles
ressemblent à ceci (les horodatages et les nombres varient) :

```
2026-07-05 14:02:41,512 INFO mulewatch.composition.app crawler started: 1 instance(s), node_id=...
2026-07-05 14:02:41,689 INFO mulewatch.application.run_search_cycle effective_coverage=healthy (1 instance(s))
2026-07-05 14:02:41,690 INFO mulewatch.application.run_search_cycle cycle 0: 42 keyword(s) × 2 channels = 84 task(s)
2026-07-05 14:02:41,701 INFO mulewatch.application.search_worker instance amule-1 connected
2026-07-05 14:04:53,118 INFO mulewatch.application.run_search_cycle cycle 0 done
```

amuled récupère **tout seul** sa liste de serveurs eD2k et de nœuds Kad au premier démarrage : vous
n'avez aucun serveur à ajouter. Pendant ce court temps de connexion, le crawler peut afficher
`effective_coverage=blind` et quelques avertissements : c'est transitoire, cela se résorbe
dès qu'amuled est connecté.

La mention **« Low-ID »**, si elle apparaît (surtout dans `docker compose logs amuled`), **n'est pas
une panne** : un nœud Low-ID catalogue et télécharge très bien. Devenir High-ID est un réglage
optionnel (annexe C).

**Point de contrôle.** Le journal du crawler contient, en quelques minutes, une ligne se terminant
par `cycle 0 done` (précédée d'une ligne `cycle 0: ... task(s)`). Cela prouve que le nœud est en vie
et boucle. Si, au bout de plusieurs minutes, le conteneur `crawler` redémarre sans cesse, ouvrez la
fiche [« Un conteneur redémarre en boucle »](troubleshooting.md#un-conteneur-redémarre-en-boucle).
Si le crawler boucle mais reste indéfiniment en `effective_coverage=blind` avec des
avertissements d'injoignabilité, c'est qu'amuled n'atteint pas le réseau : ouvrez la fiche
[« amuled ne se connecte à rien »](troubleshooting.md#amuled-ne-se-connecte-à-rien).

---

## 7. Voir le catalogue

Ouvrez dans votre navigateur :

- **Le catalogue** : <http://localhost:8080>. C'est l'interface de consultation en lecture seule.
  Elle est **vide au début** et se remplit au fil des heures, à mesure que des fichiers sont croisés
  sur le réseau (certaines cibles rares peuvent prendre des jours à réapparaître : c'est le principe
  du lost media).
- **Les tableaux de bord** : <http://localhost:3000>. C'est Grafana. Connectez-vous avec
  l'identifiant `admin` et le mot de passe `GRAFANA_PWD` que vous avez choisi à l'étape 4.

Si votre nœud tourne sur un serveur distant (pas sur votre ordinateur de bureau), remplacez
`localhost` par l'adresse IP ou le nom de ce serveur.

**Point de contrôle.** La page <http://localhost:8080> s'ouvre et affiche l'interface de mulewatch
(l'en-tête et les filtres du catalogue). Un **tableau vide à ce stade est normal** : ce qui compte,
c'est que la page se charge. Si la page ne se charge pas du tout (connexion refusée, page
inaccessible), ouvrez la fiche
[« La webui reste vide »](troubleshooting.md#la-webui-reste-vide).

Côté tableaux de bord, si Grafana refuse le mot de passe `admin` que vous avez choisi (souvent une
coquille dans `GRAFANA_PWD`), rouvrez `deploy/.env` (étape 4) et corrigez `GRAFANA_PWD`. Grafana
n'applique ce mot de passe qu'à son **premier** démarrage : réinitialisez donc aussi son état local.
Depuis le dossier `deploy` :

```
docker compose down
docker volume rm mulewatch_grafana-data
docker compose up -d
```

Vous ne perdez que l'état local de Grafana : les tableaux de bord sont provisionnés depuis des
fichiers, et le catalogue n'est pas touché.

---

## 8. Vivre avec le nœud

Votre nœud est autonome. Quelques gestes utiles, tous depuis le dossier `deploy` :

- **Mettre à jour.** Les images ne se mettent **pas** à jour toutes seules : vous décidez quand.
  Retirez les nouvelles images puis recréez les conteneurs :

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

**Point de contrôle.** Faites `docker compose down` puis `docker compose up -d` : le catalogue sur
<http://localhost:8080> affiche toujours ce qu'il avait déjà vu. Vos données ont bien survécu à
l'arrêt.

> Cycle de vie plus en détail (diagnostic après panne, planification disque, reboot) :
> [runbook d'administration, § Cycle de vie & données](administration.md#cycle-de-vie--données).
> Si votre nœud a été créé **avant le renommage en `mulewatch`** et semble avoir perdu son catalogue
> après une mise à jour, la même section explique comment récupérer les anciens volumes.

---

## Annexe A. Passer derrière un VPN

Par défaut, votre adresse IP est visible des autres pairs eD2k/Kad : c'est le fonctionnement normal
et public du réseau eMule. Pour masquer votre IP, faites passer amuled par un VPN grâce au conteneur
`gluetun`.

**Delta par rapport à la voie royale :**

1. **Un fournisseur VPN qui supporte WireGuard.** C'est indispensable : gluetun établit le tunnel en
   WireGuard.
2. **Trois variables supplémentaires** dans `deploy/.env` :

   | Variable | Quoi |
   |---|---|
   | `WIREGUARD_PRIVATE_KEY` | La clé privée WireGuard, fournie dans l'espace client de votre VPN. |
   | `VPN_SERVICE_PROVIDER` | Le nom du fournisseur, par exemple `protonvpn`, `pia`, `privatevpn`. |
   | `SERVER_COUNTRIES` | Le ou les pays de sortie, en anglais, par exemple `Switzerland`. |

3. **Un autre fichier de pile.** Au lieu de `docker compose up -d`, vous utilisez la pile
   `gluetun.compose.yml`, et vous ajoutez `-f gluetun.compose.yml` à **chaque** commande compose
   ensuite (`ps`, `logs`, `pull`, `down`...). Depuis le dossier `deploy` :

   ```
   docker compose -f gluetun.compose.yml up -d
   ```

Cette pile ajoute un service `gluetun` : `docker compose -f gluetun.compose.yml ps` affiche donc six
services au lieu de cinq. amuled partage le réseau de gluetun. Si vous combinez VPN **et** mode
download (annexe B), un service `docker-proxy` démarre en plus (un proxy du socket Docker utilisé
par le port-sync) : sa présence dans `ps` est normale.

> **Commandes longues.** Les commandes de ce guide tiennent sur une seule ligne. Si vous préférez en
> couper une avec un `\` en fin de ligne (Linux/macOS), sous Windows/PowerShell gardez au contraire
> tout sur une seule ligne et retirez les `\`. Exemple (téléchargement derrière VPN, voir annexe B) :
>
> ```
> docker compose \
>   -f gluetun.compose.yml \
>   --profile download \
>   up -d
> ```

---

## Annexe B. Activer le mode download

En mode observer (défaut), le nœud ne télécharge rien. Le mode **download** ajoute le téléchargement
d'un candidat sûr, dans un environnement isolé, avec vérification antivirus avant catalogage. Rien
n'est jamais re-partagé ni exécuté.

**Delta par rapport à la voie royale :**

1. Dans `deploy/config/crawler/crawler.yml`, passez `download.enabled: false` à **`true`**.
2. Ajoutez `--profile download` à **chaque** commande compose. Depuis le dossier `deploy` :

   ```
   docker compose --profile download up -d
   ```

   (Derrière un VPN, combinez avec l'annexe A : `-f gluetun.compose.yml --profile download`.)

Ce profil ajoute deux services, `verifier` et `freshclam` :
`docker compose --profile download ps` doit les montrer `Up` en plus des cinq autres. Côté RAM,
le verifier peut consommer jusqu'à 2 Go à lui seul (analyse antivirus) : prévoyez de la marge
au-delà des ~2 Go du mode observer.

> **À lire avant d'activer le mode download.** Quatre contraintes de déploiement doivent être
> respectées, sinon les fichiers finis restent bloqués sans être catalogués (échec silencieux) :
> volume `quarantine` partagé entre crawler et amuled, volume sur un système de fichiers Linux normal
> (pas vfat/NTFS/HFS), pas de catégories amuled, et un amuled dédié au crawler avec un jeu partagé
> restreint. Le détail est dans
> [`docs/reference/2026-06-17-amuled-completion-behavior.md`](../reference/2026-06-17-amuled-completion-behavior.md).

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
| **Pile par défaut, port ouvert** | Redirigez `LISTEN_PORT` (par défaut `4662`, en TCP **et** UDP) depuis votre box/routeur vers cette machine. Si vous changez de port, ajustez `LISTEN_PORT` dans `deploy/.env`. |
| **Pile VPN (gluetun), port forwarding** | Mettez `VPN_PORT_FORWARDING=on` dans `deploy/.env` **et** `port_sync.enabled: true` dans `deploy/config/crawler/crawler.yml`. Votre fournisseur VPN doit supporter le port forwarding ([liste gluetun](https://github.com/qdm12/gluetun-wiki/tree/main/setup/providers)). |

Compromis, activation pas à pas et vérification :
[runbook d'administration, § High-ID](administration.md#high-id-optionnel--devenir-joignable).

---

## Annexe D. Régler le monitoring

Le catalogue (webui) et les tableaux de bord (Prometheus + Grafana) sont **toujours actifs**, sans
profil à ajouter. Quelques réglages, tous depuis le dossier `deploy` :

- **Changer les ports.** Dans `deploy/.env`, `WEBUI_PORT` (défaut `8080`) et `GRAFANA_PORT` (défaut
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
| **service** | Une brique du nœud : un conteneur géré par `docker compose` (par exemple `crawler`, `amuled`, `webui`). |
| **eD2k / Kad** | Les deux réseaux d'eMule surveillés : eDonkey2000 (serveurs centralisés) et Kademlia (décentralisé, sans serveur). |
| **Low-ID / High-ID** | La joignabilité de votre nœud sur eD2k. High-ID = la machine est accessible de l'extérieur (plus de sources directes). Low-ID fonctionne aussi, en moins optimal. |
| **EC** | *External Connection* : le protocole TCP interne par lequel le crawler pilote le client `amuled`. |
| **quarantine** | Le dossier isolé où atterrissent les fichiers téléchargés (mode download) avant leur vérification antivirus. |

---

## Pour aller plus loin

- [Runbook d'administration](administration.md) : cycle de vie, High-ID, réglage RAM/clamav,
  métriques, durcissement, outils de catalogue, limites connues.
- [Runbook de dépannage](troubleshooting.md) : du symptôme à la cause à la solution.
- [Légalité et confidentialité](../legal-and-privacy.md) : ce que mulewatch fait, et surtout ce
  qu'il ne fait pas.
