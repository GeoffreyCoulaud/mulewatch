# Régler le nœud

## Mode catalogue seul (sans téléchargement)
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

---

## Ports et métriques
- **Changer un port web.** Les ports sont écrits en clair dans la section `ports:` du fichier de
  votre pile — `compose.yml`, ou `gluetun.compose.yml` où ils sont publiés par le service
  `gluetun` : `8080` pour le catalogue, `4711` pour l'interface d'aMule. Utile si l'un d'eux est
  déjà pris sur votre machine. Ne changez que le nombre de **gauche**, le côté hôte
  (`"8090:8080"`) ; dans le conteneur, les ports sont figés.
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
[runbook d'administration, § Métriques Prometheus](settings.md#métriques-prometheus) et
[§ Exposition derrière un reverse proxy](operate.md#exposition-derrière-un-reverse-proxy).

---

---

## Métriques Prometheus

Le crawler expose un point d'accès Prometheus sur le port réglé par `observability.metrics.port`
dans `crawler.yml` (`9090` par défaut, `enabled: true` par défaut). **Aucun conteneur Prometheus ni
Grafana n'est livré avec la pile** : si vous voulez des tableaux de bord, faites pointer votre
propre Prometheus sur le crawler.

Ce port n'est pas publié sur l'hôte par défaut, et il n'y a plus de réseau interne partagé à
rejoindre — les réseaux `ec` et `egress` ont disparu avec la pile multi-services. Il reste donc une
seule route : **le publier vous-même**, en ajoutant `"9090:9090"` à la liste `ports:` du fichier de
pile que vous utilisez réellement — `compose.yml` sous le service `mulewatch`,
`gluetun.compose.yml` sous le service `gluetun` (mulewatch n'y a pas de réseau propre). Ne l'ajoutez
jamais à `base.compose.yml` : compose fusionne les `ports` de façon additive et ne sait pas retirer
une entrée apportée par un fragment, ce pourquoi le fragment n'en déclare aucune.

Traitez-le comme le port 8080 : **aucune authentification**, donc gardez-le hors de l'Internet
ouvert.

Exemple de `scrape_config` pour votre propre `prometheus.yml` :

```yaml
scrape_configs:
  - job_name: 'mulewatch'
    static_configs:
      - targets: ['node.example.lan:9090']   # l'hôte sur lequel vous avez publié 9090
```

Mettre `observability.metrics.enabled: false` coupe entièrement le point d'accès ; le crawl et la
webui ne sont pas affectés.

---

