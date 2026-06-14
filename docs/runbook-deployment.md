# Runbook de déploiement — emule-indexer

Déploiement `docker compose` de la stack emule-indexer (gluetun + amuled + crawler + verifier).
Deux profils : **observer** (recherche + catalogage + notification, ne télécharge rien) et **full**
(observer + auto-download + vérification isolée). Le sujet du catalogue reste **le fichier**, jamais
la personne.

---

## Prérequis

- **Docker** + **Buildx** + **docker compose v2** (`docker compose version`).
- Identifiants **ProtonVPN** : une clé privée **WireGuard** (obtenue depuis le portail ProtonVPN,
  section WireGuard).
- **`/dev/net/tun`** disponible sur l'hôte (gluetun monte le tunnel WireGuard ; le device est
  exigé par le conteneur gluetun).
- (Opt-in) **gVisor** (`runsc`) installé et enregistré comme runtime Docker si vous voulez le
  durcissement noyau de `compose.hardening.yml`. Sans gVisor, ne pas charger ce fichier (la base
  est déjà durcie au niveau conteneur).

---

## Construction des images (incantation uv workspace résolue)

Le dépôt est un **workspace uv virtuel** : un seul `uv.lock` racine, un `pyproject.toml` racine, et
deux membres (`packages/crawler`, dist `emule-indexer` ; `packages/verifier`, dist
`download-verifier`). Chaque image se construit en **deux couches** pour maximiser le cache Docker :

1. **Couche dépendances** (avant de copier le code) — bind-mount de `uv.lock`, du `pyproject.toml`
   racine **et des DEUX `pyproject.toml` membres**, puis :

   ```dockerfile
   uv sync --locked --no-install-workspace --package <dist>
   ```

2. **Couche projet** (après `COPY . /app`) :

   ```dockerfile
   uv sync --locked --no-editable --package <dist>
   ```

Où `<dist>` vaut `download-verifier` (verifier) ou `emule-indexer` (crawler). Notes empiriques
(validées tel quel, sans ajustement) :

- `--locked` (PAS `--frozen`) ; `--package` fonctionne **sans** `--all-packages`.
- L'incantation a marché **verbatim** depuis le squelette du workspace, aucune adaptation.

**Libs système :**

- **verifier** : il faut **uniquement** `ffmpeg` (qui fournit `ffprobe`, requis par l'analyse
  D-analysis). Aucune autre lib apt.
- **crawler** : **zéro** lib apt. `google-re2` (importé `re2`) et `rapidfuzz` s'importent
  proprement sur `python:3.12-slim-bookworm` — leurs wheels manylinux embarquent le code natif, et
  `libstdc++6` est déjà présent dans l'image slim.

Les deux images : runtime `python:3.12-slim-bookworm`, **non-root** (uid/gid 999), entrypoint
exec-form `["python","-m","<pkg>"]`.

**Build local de toute la stack :**

```bash
docker compose --profile full build
```

---

## Propriété des volumes `/data` (non-root + read_only)

Le crawler tourne en `user: 999` avec `read_only: true` sur le rootfs. Docker crée les **volumes
nommés vides** en **propriété root** : sans intervention, le crawler (uid 999) ne pouvait pas créer
ses bases SQLite et échouait avec `unable to open database file` (défaut réel rencontré puis
corrigé).

**Correctif appliqué dans `packages/crawler/Dockerfile`** : l'image **pré-crée**
`/data/{catalog,local,quarantine}` en propriété `nonroot:nonroot`. Quand un volume nommé **vide** se
monte pour la première fois sur l'un de ces points, il **hérite** de la propriété 999. Vérifié
empiriquement ; le smoke utilise désormais de **vrais volumes nommés** (donc ce chemin est couvert).

> ⚠️ **Volumes pré-existants** : si vous montez un volume nommé **déjà peuplé** (donc déjà
> root-owned, l'astuce d'héritage ne joue qu'au premier montage d'un volume vide), il faut le
> `chown` manuellement :
> ```bash
> docker run --rm -v emule-indexer_catalog-db:/d alpine chown -R 999:999 /d
> ```
> (idem pour `local-db` et `quarantine`).

---

## User amuled

`amuled` est une image **tierce** (`ngosang/amule:3.0.0-1`) lancée avec **son propre user** : nous
ne durcissons **pas** ce que nous ne construisons pas (pas de `read_only`/`user:` imposés au smoke,
aucune relaxation nécessaire).

> **Note quarantaine (croisement d'uid à surveiller en prod)** : le volume `quarantine` est écrit à
> la fois par **amuled** (fichiers complétés) et par le **crawler** (`os.replace` atomique). Le
> premier conteneur qui monte le volume **vide** fixe sa propriété — un éventuel accroc cross-uid à
> surveiller au vrai déploiement. Non encore exercé (pas de vrai téléchargement dans le smoke).

---

## Astuce diagnostic (entrypoint exec-form)

Les deux images ont un **entrypoint exec-form** `["python","-m","<pkg>"]`. Un `docker run IMAGE
python -c "..."` **n'override pas** l'entrypoint : il y **ajoute** ses arguments. Pour lancer une
commande ponctuelle dans une image, passer par `--entrypoint` :

```bash
docker run --rm --entrypoint python <image> -c "import re2, rapidfuzz; print('ok')"
```

(`docker compose exec` et le `CMD` du healthcheck ne traversent PAS l'entrypoint — ils ne sont pas
affectés.)

---

## Setup

1. **Secrets** :
   ```bash
   cp .env.example .env
   ```
   Renseigner dans `.env` : `WIREGUARD_PRIVATE_KEY` (clé WireGuard ProtonVPN), `SERVER_COUNTRIES`
   (ex. `Switzerland`) et `AMULE_EC_PASSWORD` (mot de passe EC). Le `.env` est **gitignoré**.

2. **Config locale** :
   ```bash
   cp config/local.example.yaml config/local.yaml
   ```
   Renseigner dans `config/local.yaml` :
   - `amules[].host: gluetun`, `amules[].port: 4712`, `amules[].password:` = la valeur de
     `AMULE_EC_PASSWORD` (l'hôte EC est le conteneur **gluetun** car amuled tourne en
     `network_mode: service:gluetun`).
   - `catalog_db_path: /data/catalog/catalog.db` et `local_db_path: /data/local/local.db` (chemins
     cohérents avec les volumes nommés `catalog-db` @ `/data/catalog` et `local-db` @ `/data/local`).
   - **Mode full uniquement** : décommenter le bloc `download_endpoint`, renseigner `staging_dir:
     /data/quarantine` + `quarantine_dir: /data/quarantine` (un **unique** volume `quarantine`
     partagé, staging et quarantaine sur le même FS), et `verifier_url: http://verifier:8000`.

---

## Démarrage

- **Observer** (gluetun + amuled + crawler ; pas de download ni de vérif) :
  ```bash
  docker compose --profile observer up -d
  ```

- **Full** (+ verifier) :
  ```bash
  docker compose --profile full up -d
  ```
  > ⚠️ En full, le crawler **health-gate le verifier** au démarrage et **refuse de démarrer** s'il
  > est injoignable (pas de download sans vérif). Comme `compose.yaml` ne pose **pas** de
  > `depends_on: verifier`, si le verifier n'est pas encore prêt le crawler fail-fast — son
  > `restart: unless-stopped` le relance jusqu'à ce que le verifier soit sain (acceptable en
  > long-running). Pour éviter les redémarrages, démarrer le verifier d'abord :
  > ```bash
  > docker compose --profile full up -d verifier
  > docker compose --profile full up -d
  > ```

- **Homelab (pull depuis GHCR)** :
  ```bash
  docker compose pull
  docker compose --profile full up -d
  ```
  **Build local** plutôt que pull : `docker compose --profile full build`.

---

## Durcissement opt-in (gVisor)

```bash
docker compose -f compose.yaml -f compose.hardening.yml --profile full up -d
```

Exige le runtime gVisor `runsc` enregistré sur l'hôte. **Sinon, ne pas charger ce fichier** : la
base est déjà durcie au niveau conteneur — non-root (999), `cap_drop: ALL`,
`no-new-privileges:true`, `read_only: true`, et le réseau `verify-internal` en `internal: true`
(le verifier n'a pas d'egress).

---

## Visibilité GHCR

Les packages GHCR sont **privés par défaut**. Deux options :

- Les rendre **publics** dans les settings du package GitHub ; OU
- S'authentifier avant le pull :
  ```bash
  docker login ghcr.io -u <user>   # PAT avec scope read:packages
  docker compose pull
  ```

Références d'images (lowercase) :

- `ghcr.io/geoffreycoulaud/emule-indexer-crawler`
- `ghcr.io/geoffreycoulaud/emule-indexer-verifier`

---

## Validation homelab (manuelle)

1. Monter la stack en **full** : `docker compose --profile full up -d`.
2. Suivre les logs : `docker compose logs -f crawler`.
3. Confirmer le cycle complet sur le **vrai** réseau eMule : recherche → download → quarantaine →
   vérification.

> **Low-ID pour l'instant** : le High-ID attend le follow-up de **synchronisation de port**
> (glueforward abandonné — voir Limites connues). En Low-ID la connectivité fonctionne mais reste
> sous-optimale.

**Où vivent les données (volumes nommés) :**

```bash
docker volume inspect emule-indexer_quarantine
docker compose exec crawler ls /data            # /data/catalog, /data/local, /data/quarantine
docker compose exec verifier ls /quarantine     # inspecter la quarantaine côté verifier
```

---

## Smoke local

```bash
( cd packages/crawler && uv run pytest -m compose_integration --no-cov -q )
```

Docker requis. Monte la stack **sans VPN** (amuled sur le réseau `ec`) et asserte le câblage
(réseaux, volumes, propriété 999 de `/data`, santé des services). Désélectionné du run par défaut,
exclu de la couverture.

---

## Limites connues / follow-ups

- **Synchronisation de port / High-ID** : remplace glueforward (abandonné) ; tant qu'il n'est pas
  là, on tourne en **Low-ID**.
- **clamav** : seconde source `malicious` (signatures) — **après Plan F** ; `freshclam` exige un
  egress, en tension avec le `internal: true` du verifier (un slot de registre est réservé,
  non implémenté).
- **Ring noyau bwrap par-enfant** : isolation namespace `net=none` / seccomp / RO mounts /
  tmpfs réel par enfant d'analyse — **changement de code, hors Plan F**.
- **Sous-commandes CLI** : ergonomie d'exploitation (à venir).
