# Migrer un nœud 1.x vers la 2.0

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
ancien `compose.yaml`, votre `.env` et votre `config/crawler/` (git, ou votre sauvegarde) — en 1.x,
le tag d'image se réglait par la variable `IMAGE_TAG` de ce `.env` ; mettez-y le tag 1.x que vous
utilisiez, puis `docker compose up -d`. (En 2.0 cette variable n'existe plus : le tag de l'image est
écrit dans `base.compose.yml`.) Les volumes nommés ont seulement été copiés, jamais déplacés ni
supprimés, donc le vieux nœud retrouve ses données là où il les avait laissées.

Une fois le nouveau nœud éprouvé — laissez-lui quelques jours — vous pouvez supprimer les anciens
volumes avec `docker volume rm mulewatch_catalog-db mulewatch_local-db mulewatch_amule-state`. C'est
le point de non-retour : faites-le en dernier, et seulement après avoir vérifié que
`data/catalog.db` contient bien votre historique.

---
