# Déploiement — quickstart

Le sujet du catalogue est **le fichier, jamais la personne**.

**Prérequis :** Docker avec compose v2 (`docker compose version` doit afficher v2.x), connexion Internet permanente.

> Une fois le nœud monté : exploiter, régler, ajuster la RAM/clamav, durcissement conteneur → **[Runbook d'administration](administration.md)** ; en cas de souci → **[Runbook de dépannage](troubleshooting.md)**.

---

## 1. Choisir sa stack

| Stack | Fichier | IP visible des pairs | VPN requis |
|---|---|:--:|:--:|
| **Sans VPN (défaut)** | `deploy/direct.compose.yml` | **Oui** ¹ | Non |
| **Avec VPN (gluetun)** | `deploy/gluetun.compose.yml` | Non | Oui (WireGuard) |

¹ Ton IP est visible des pairs eD2k/Kad — choix informé, le réseau eMule est public par nature.

**Low-ID par défaut** (aucun port à ouvrir) — fonctionne pour cataloguer et télécharger.
**High-ID** (plus de sources, plus efficace) est optionnel ; voir [§ High-ID](#high-id-optionnel) plus bas.

---

## 2. Renseigner les secrets

```bash
cp deploy/.env.example deploy/.env
```

Éditez `deploy/.env` :

| Variable | Requis pour | Quoi |
|---|---|---|
| `AMULE_EC_PASSWORD` | Toutes | Mot de passe que **vous choisissez** (≥ 12 caractères) |
| `WIREGUARD_PRIVATE_KEY` | gluetun | Clé privée WireGuard (espace client de votre fournisseur VPN) |
| `VPN_SERVICE_PROVIDER` | gluetun | Ex. `protonvpn`, `pia`, `privatevpn` |
| `SERVER_COUNTRIES` | gluetun | Ex. `Switzerland` (noms anglais complets) |
| `GRAFANA_PWD` | `--profile monitoring` | Mot de passe Grafana (compte `admin`) |

> Aucune valeur `change-me` ne doit rester — elles ne provoquent pas d'erreur au lancement, mais causent un **échec silencieux** plus tard (le crawler ne peut pas s'authentifier à amuled).

---

## 3. Choisir le mode : observer ou download

**Observer** (crawl + catalogage uniquement, défaut) : rien à faire — c'est le comportement sans `--profile`.

**Download** (téléchargement + vérification antivirus) :

1. Dans `deploy/config/crawler/crawler.yml`, passer `download.enabled: false` → **`true`**.
2. Ajouter `--profile download` à la commande de lancement (§4).

> **Avant d'activer le mode download**, lisez les 4 contraintes de déploiement dans
> [`docs/reference/2026-06-17-amuled-completion-behavior.md`](../reference/2026-06-17-amuled-completion-behavior.md)
> — volume partagé crawler/amuled, FS Linux, catégories amuled désactivées, jeu partagé restreint.
> Un écart échoue silencieusement (les fichiers restent bloqués dans l'IncomingDir d'amuled).

---

## 4. Lancer

```bash
# Stack sans VPN, observer (le chemin le plus simple) :
docker compose -f deploy/direct.compose.yml up -d

# Stack sans VPN, download + monitoring :
docker compose -f deploy/direct.compose.yml --profile download --profile monitoring up -d

# Stack VPN, observer :
docker compose -f deploy/gluetun.compose.yml up -d

# Stack VPN, download :
docker compose -f deploy/gluetun.compose.yml --profile download up -d
```

---

## 5. Vérifier

```bash
docker compose -f deploy/direct.compose.yml ps                    # tous les services en "Up"
docker compose -f deploy/direct.compose.yml logs crawler          # activité du crawler
docker compose -f deploy/direct.compose.yml logs amuled | head -50 # connexion réseau amuled
```

> Stack VPN : remplacez `direct.compose.yml` par `gluetun.compose.yml` dans ces commandes.

amuled récupère sa liste de serveurs eD2k et nœuds Kad **automatiquement** au premier boot (1–3 min) — vous n'avez rien à faire. « Low-ID » dans les logs n'est **pas une panne**.

En mode download, le crawler redémarre en boucle pendant 1–2 min (il attend que le verifier soit sain) et les premiers fichiers ressortent `suspicious` le temps que clamav télécharge sa base (~5–20 min selon la connexion) — comportements transitoires normaux.

---

## Mettre à jour

Les images sont publiées sur ghcr et **la mise à jour est manuelle** : vous choisissez quand l'appliquer. Compose ne re-tire **pas** une image `:latest` déjà présente localement — il faut un `pull` explicite, puis recréer les conteneurs.

```bash
# Reprenez EXACTEMENT le même fichier de stack et les mêmes --profile qu'au lancement (§4).
# Un --profile omis = le service correspondant n'est ni tiré ni recréé (ex. webui reste périmé).
docker compose -f deploy/direct.compose.yml [--profile …] pull
docker compose -f deploy/direct.compose.yml [--profile …] up -d
```

`up -d` ne recrée que les conteneurs dont l'image a changé ; les volumes nommés (catalogue, état) **persistent**. Pour seulement redémarrer un service sans changer d'image : `docker compose -f deploy/direct.compose.yml restart <service>`.

> Détails cycle de vie (arrêt, persistance, reboot de l'hôte) : **[Runbook d'administration — Cycle de vie](administration.md#cycle-de-vie--données)**.

---

## High-ID (optionnel)

Plus de sources → recherche et téléchargement plus efficaces. Non requis pour cataloguer.

| Voie | Comment activer |
|---|---|
| `direct` + port ouvert | Rediriger `LISTEN_PORT` (défaut `4662` TCP + UDP) sur votre box vers cette machine. Régler `LISTEN_PORT` dans `.env` si vous changez le port. |
| `gluetun` + VPN avec port forwarding | `VPN_PORT_FORWARDING=on` dans `.env` **et** `port_sync.enabled: true` dans `deploy/config/crawler/crawler.yml`. Le fournisseur VPN doit supporter le port forwarding ([liste gluetun](https://github.com/qdm12/gluetun-wiki/tree/main/setup/providers)). |

Détails, compromis, activation pas à pas : **[Runbook d'administration — High-ID](administration.md#high-id-optionnel--devenir-joignable)**.

---

## Profils disponibles

| Profil | Ce qu'il ajoute |
|---|---|
| `--profile download` | verifier + freshclam (clamav) — nécessite `download.enabled: true` dans `crawler.yml` |
| `--profile monitoring` | Prometheus + Grafana sur `http://<hôte>:${GRAFANA_PORT:-3000}` (nécessite `GRAFANA_PWD`) |
| `--profile webui` | Interface web lecture seule du catalogue sur `http://<hôte>:${WEBUI_PORT:-8080}` |

---

## Glossaire minimal

| Terme | Signification |
|---|---|
| **eD2k / Kad** | Les deux réseaux eMule surveillés : eDonkey2000 (serveurs centralisés) et Kademlia (décentralisé). |
| **Low-ID / High-ID** | Joignabilité sur eD2k. High-ID = la machine est accessible de l'extérieur (plus de sources directes). Low-ID fonctionne, moins optimal. |
| **EC** | *External Connection* — protocole TCP interne entre le crawler et `amuled`. |
| **quarantine** | Dossier isolé où atterrissent les fichiers téléchargés avant vérification. |

---

## Pour aller plus loin

- **[Runbook d'administration](administration.md)** — cycle de vie (arrêt, mise à jour, persistance), High-ID, RAM/clamav, métriques Prometheus, durcissement conteneur, WebUI, outils de catalogue, limites connues.
- **[Runbook de dépannage](troubleshooting.md)** — amuled sans réseau, verdict `suspicious` persistant, port-sync inopérant, droits de volume.
