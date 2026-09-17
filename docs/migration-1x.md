# Migrer un nœud 1.x vers la 2.0

**Lisez ceci avant tout `docker compose pull` sur un nœud existant.** La 2.0 remplace deux images et
jusqu'à quatre services par une seule image et un seul service, et sort vos données des volumes
nommés Docker vers de simples dossiers. Ni code de compatibilité, ni migration automatique : à la
main, une fois, le vieux nœud arrêté.

Lancez toutes les commandes depuis le dossier de l'ancien `compose.yaml`.

**Étape 1, arrêtez le vieux nœud.** Sans `-v` : les volumes nommés sont vos données et votre retour
arrière.

```
docker compose down
```

(ou `docker compose -f gluetun.compose.yml down` si vous étiez sur la pile VPN.)

**Étape 2, copiez chaque volume nommé dans son nouveau dossier.** La 1.x gardait `catalog.db`,
`local.db` et l'état d'aMule dans des volumes nommés ; la 2.0 les lit depuis `data/` et `amule/`.
Copiez sans déplacer : des volumes intacts sont votre retour arrière.

```
mkdir -p data amule downloads/incoming downloads/temp
docker run --rm -v mulewatch_catalog-db:/src -v "$PWD/data":/dst alpine sh -c "cp -a /src/. /dst/"
docker run --rm -v mulewatch_local-db:/src   -v "$PWD/data":/dst alpine sh -c "cp -a /src/. /dst/"
docker run --rm -v mulewatch_amule-state:/src -v "$PWD/amule":/dst alpine sh -c "cp -a /src/. /dst/"
```

Un doute sur les noms ? `docker volume ls` : avant le renommage du projet, le préfixe était
`deploy_` et non `mulewatch_`.

Montés sur `/data/catalog` et `/data/local`, ces volumes portent leur fichier à la racine : copiés
dans `data/`, les deux se retrouvent côte à côte, là où la 2.0 les cherche. Vérifiez :

```
ls data/     # doit montrer catalog.db et local.db, côte à côte
```

**Étape 3, déplacez vos trois fichiers de config à la racine du dossier de travail.** La 2.0 les
monte d'à côté du fichier compose, non plus de `config/crawler/`.

```
mv config/crawler/crawler.yml config/crawler/targets.yml config/crawler/matcher.yml .
rmdir config/crawler config
```

**Étape 4, éditez `crawler.yml`.** Quatre retraits, trois changements, un ajout :

- **retirez** la liste `amules:` : le conteneur a un seul client eMule, à une adresse figée dans le
  code (`127.0.0.1:4712`) ;
- **retirez** `download.endpoint:` (même raison) ;
- **retirez** `port_sync.restarter_url:` : plus de proxy Docker à qui parler ;
- **ajoutez**, au niveau racine, `amule_ec_password: ${AMULE_EC_PASSWORD}` ;
- **changez** `catalog_db_path` en `/data/catalog.db` et `local_db_path` en `/data/local.db` ;
- **changez** `download.output_dir` en `/downloads` ;
- **changez** `port_sync.gluetun_control_url` en `http://localhost:8000` (mulewatch partage le
  namespace réseau de gluetun, son contrôle est sur localhost).

Le `deploy/crawler.yml` de la 2.0 fait référence en cas de doute.

**Étape 5, complétez `.env`, puis prenez possession des dossiers.** La 2.0 exige quatre variables là
où la 1.x en exigeait une : ajoutez `PUID`, `PGID` et `WEBUI_PWD` (étape 4 du parcours
principal). Donnez ensuite à cet uid les données copiées, sorties de volumes appartenant à
quelqu'un d'autre :

```
sudo chown -R "$PUID:$PGID" data amule downloads
```

**Étape 6, démarrez la nouvelle pile.** La pile directe est désormais `compose.yml`, et non
`compose.yaml` :

```
docker compose up -d
docker compose ps        # one service, `mulewatch`, Up (healthy) after ~30 s
```

### Ce qui est repris, et ce qui ne l'est pas

- **Votre `amule.conf` existant est conservé**, à une clé près : le conteneur l'écrit s'il est
  absent, puis aligne à chaque boot `ECPassword` (`[ExternalConnect]`) sur `AMULE_EC_PASSWORD`, qui
  fait autorité. Changer ce mot de passe passe donc par `.env` et un redémarrage ; le reste est le
  vôtre. Vérifiez qu'`IncomingDir` et `TempDir` pointent sur `/downloads/incoming` et
  `/downloads/temp`, corrigez sinon :
  ```
  grep -E "^(Incoming|Temp)Dir" amule/amule.conf
  ```
- **Votre catalogue est repris intact.** `catalog.db` est append-only, son schéma n'est pas touché
  par cette version.
- **Le backoff de recherche persisté repart de zéro, une fois.** Le nom interne du client eMule est
  désormais la constante `amuled`, là où la 1.x le lisait dans `crawler.yml` (typiquement
  `amule-1`). Backoff et avancement de l'ordonnanceur étant indexés dessus, les lignes de l'ancien
  nom sont ignorées : le premier cycle 2.0 part d'une ardoise vierge. Sans gravité (au pire un canal mis en
  pause est réessayé), mais cela explique les journaux bavards du premier boot.
- **Le label `instance` a disparu** des métriques Prometheus qui le portaient : avec un seul client,
  c'était une constante. Retirez-le des tableaux de bord qui groupaient dessus.

### Retour arrière

L'image 1.x reste publiée sous son ancien nom, `ghcr.io/geoffreycoulaud/mulewatch-crawler`, figée
et **jamais supprimée, délibérément** : c'est le chemin de retour. Restaurez votre ancien
`compose.yaml`, votre `.env` et votre `config/crawler/` (git ou sauvegarde), remettez dans
`IMAGE_TAG` le tag 1.x que vous utilisiez (la 2.0 n'a plus cette variable, son tag est dans
`base.compose.yml`), puis `docker compose up -d`. Les volumes ont été copiés, jamais déplacés ni
supprimés : le vieux nœud retrouve ses données intactes.

Après quelques jours de nouveau nœud éprouvé, supprimez les anciens volumes :
`docker volume rm mulewatch_catalog-db mulewatch_local-db mulewatch_amule-state`. **C'est le point
de non-retour** : en dernier, et seulement après avoir vérifié que `data/catalog.db` contient bien
votre historique.

---
