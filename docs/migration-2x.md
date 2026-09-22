---
description: "Migrer un nœud 2.x vers la 3.0 : une variable à renommer, et une nouvelle interface web d'aMule sur le port 4711."
---

# Migrer un nœud 2.x vers la 3.0

La 3.0 remplace `amuleweb` par **amuleapi**, la nouvelle interface web et REST livrée avec aMule
3.1.0. Le crawler s'en sert aussi pour piloter le client eMule : il ne parle plus le protocole EC
binaire. Aucune donnée ne bouge, aucun port ne change, et le catalogue est repris intact.

Deux étapes, le nœud arrêté, depuis le dossier de votre `compose.yml`.

!!! warning "Votre nœud ne redémarrera pas sans l'étape 1"

    Le renommage de variable est volontairement cassant : sans l'édition, le conteneur s'arrête au
    démarrage en nommant la variable manquante. C'est préférable à un nœud qui démarre et ne se
    connecte à rien.

**1. Arrêtez le nœud, puis renommez la variable dans `.env`.**

```bash
docker compose down     # ou -f gluetun.compose.yml si vous êtes sur la pile VPN
sed -i 's/^WEBUI_PWD=/AMULE_API_PASSWORD=/' .env
```

Le mot de passe lui-même ne change pas : il protège toujours l'interface d'aMule sur le port 4711,
et il sert désormais aussi au crawler pour joindre le client eMule.

**2. Renommez la clé correspondante dans `crawler.yml`** (`deploy/crawler.yml` de la 3.0 fait
référence) :

| Action | Clé | Pourquoi |
|---|---|---|
| Remplacez | `amule_ec_password: ${AMULE_EC_PASSWORD}` par `amule_api_password: ${AMULE_API_PASSWORD}` | le crawler s'authentifie auprès d'amuleapi, avec le mot de passe admin |

`AMULE_EC_PASSWORD` reste nécessaire dans `.env` : amuleapi et amuled continuent de se parler en EC
à l'intérieur du conteneur.

**3. Démarrez.**

```bash
docker compose up -d
docker compose ps        # un seul service, `mulewatch`, Up (healthy) en ~30 s
```

## Ce qui change au premier boot

- **Le port 4711 sert une autre interface.** C'est celle d'amuleapi, plus complète que celle
  d'amuleweb : recherche par onglets, transferts, fichiers partagés, clients, statistiques,
  préférences, en thème clair comme sombre. Même adresse, même mot de passe, page différente.
- **Il n'y a plus que deux services supervisés.** `amuleweb` a disparu de la liste ; amuleapi n'y
  figure pas non plus, parce que c'est amuled qui le démarre et l'arrête avec lui.
  `docker compose exec mulewatch s6-svstat /etc/services.d/amuleweb` n'existe plus.
- **Le catalogue est repris intact** : `catalog.db` est append-only, son schéma ne bouge pas.
- **Trois colonnes vides commencent à se remplir.** La durée, le débit et le codec voyagent
  maintenant avec les résultats de recherche, quand le serveur qui répond les annonce. C'est une
  déclaration du réseau, jamais une mesure du fichier : traitez-les comme un indice.
- **Un fichier partagé sous plusieurs noms est catalogué sous chacun d'eux**, comme avant. aMule les
  regroupe désormais sous un seul résultat ; le crawler les redéplie.

## Retour arrière

La 2.x reste publiée : remettez votre `.env` et votre `crawler.yml` d'origine (`WEBUI_PWD` et
`amule_ec_password`), épinglez votre ancien tag `2.x` dans `compose.yml` à la place de `latest`,
puis `docker compose up -d`. Rien n'a été supprimé côté données.

Un détail à connaître si vous revenez en arrière : la 3.0 a écrit une section `[AmuleApi]` dans
`amule/amule.conf` et un fichier `amule/amuleapi-passwords`. Une 2.x les ignore, vous pouvez les
laisser en place.
