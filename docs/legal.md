---
description: "Ce que votre nœud fait sur le réseau, ce qu'il stocke, et ce que vous risquez en l'hébergeant."
---

# Légalité et vie privée

Ce guide s'adresse à **vous qui hébergez un nœud** `mulewatch` : chez vous, sur un VPS ou dans
une infra que vous administrez. Il répond honnêtement à trois questions : ce que votre nœud
catalogue, ce que vous risquez légalement, ce qu'un VPN protège vraiment (ou pas).

Ce document n'est **pas un avis juridique**. Dans un cadre institutionnel (université, association
de préservation, employeur), faites valider par un juriste qui connaît votre juridiction.

---

## 1. Ce que votre nœud catalogue, stocke, transfère

### Ce qui finit dans le catalogue (`data/catalog.db` et `data/local.db`)

- **Empreintes eD2k (hashes)** des fichiers vus sur le réseau eMule.
- **Noms de fichiers** tels que publiés par les pairs.
- **Tailles** et nombre de sources rapporté par eD2k.
- **Sources EC** anonymisées : aMule dit combien de pairs ont une copie, pas qui.
- **Décisions de matching** : la cible (épisode recherché) qu'un fichier satisfait, selon vos
  règles YAML.
- **Métadonnées techniques du nœud** : `node_id` interne, état du scheduler, dernière passe de
  catalogage. Pas d'info utilisateur.

### Ce qui ne finit *pas* dans le catalogue

- **Aucune IP de pair eMule.** Le crawler passe par le protocole EC d'aMule, qui expose des
  identifiants opaques, jamais d'adresses IP.
- **Aucune trace utilisateur.** Ni cookies, ni session, ni log d'accès : la WebUI est en lecture
  seule et n'authentifie personne ; exposée, elle exige un reverse proxy en amont pour l'auth.
- **Aucune télémétrie sortante.** Rien ne part vers un service tiers. Les métriques restent
  locales : un endpoint `/metrics` à scraper si vous voulez, sans Prometheus ni Grafana dans la
  pile.
- **Aucun contenu de fichier.** Même téléchargement actif, seuls le `hash`, le nom et les
  métadonnées eD2k sont indexés ; le fichier vit à part dans `downloads/incoming`, et rien ne
  l'ouvre : mulewatch ne lit jamais ses octets (ni sniff de type, ni sonde média, ni antivirus).

### Ce qui circule sur votre réseau

- **Pile VPN (`gluetun.compose.yml`)** : tout le trafic P2P passe par le tunnel ; votre
  fournisseur d'accès (FAI) ne voit que du chiffré vers le fournisseur VPN.
- **Pile par défaut (`compose.yml`, sans VPN)** : le trafic P2P sort en clair depuis votre IP
  domestique ; votre FAI voit les flux vers les pairs eMule, pas leur contenu.
- **Trafic eMule** : eD2k est ancien, non chiffré. Un pair voit quels fichiers vous demandez et
  quels hashes vous proposez.

### Ce qui finit sur votre disque

- Les bases SQLite (`catalog.db`, `local.db`) : de quelques Mo à quelques Go selon l'usage et la
  compaction (cf. [Faire tourner un nœud](operate.md#planification-disque)).
- Téléchargement actif : les fichiers écrits directement dans `downloads/incoming`. Rien ne les
  purge : ils s'accumulent jusqu'à votre ménage.

---

## 2. Risque légal, honnêtement

### Le constat de base

**Partager une œuvre soumise au droit d'auteur sans autorisation est illégal dans la plupart des
juridictions.** Cela vaut dès qu'un nœud eMule tourne, quel que soit le mode :

- **Catalogage seul** (`download.enabled: false`) : aMule annonce une « source » dès qu'un fichier
  est dans son IncomingDir ; sans téléchargement il reste vide, donc exposition faible.
- **Téléchargement actif** (le défaut) : vous téléchargez ET re-partagez. eMule est symétrique,
  ce que vous prenez est offert aux pairs tant qu'il reste dans votre dossier partagé. Les
  fichiers finis restent dans l'IncomingDir, donc partagés jusqu'à ce que vous les déplaciez.
- **High-ID Route B** : un port ouvert sur votre box vous rend joignable directement par les
  pairs ; visibilité accrue comme source, IP visible.

### Le risque pratique pour ce projet

Le risque est **statistiquement faible mais non nul**. Il dépend de trois facteurs :

1. **Votre juridiction.** France et Belgique ont des dispositifs actifs (Hadopi, géré par l'Arcom
   depuis 2022) ; l'Allemagne pratique les *Abmahnungen*, avertissements payants des ayants
   droit ; Suisse, Canada et d'autres sont moins agressifs sur le P2P. Renseignez-vous sur votre
   pays.
2. **La nature de votre cible.** Ce projet vise des médias perdus : œuvres non rééditées, aux
   ayants droit inactifs ou introuvables. Les surveillances P2P visent les nouveautés à forte valeur
   commerciale, pas un dessin animé Teletoon de 2008.
3. **Votre choix de pile.** gluetun masque votre IP ; la pile par défaut l'expose.

**Aucune de ces protections n'est une absolution juridique.** Face à une procédure, « j'utilisais
un VPN » n'est pas une défense : c'est seulement plus dur à remonter pour la partie adverse.

### Ce qui distingue ce projet d'un client P2P généraliste

- Un fichier retrouvé enrichit le patrimoine et, tant que l'ayant droit est inactif, ne lui cause
  aucun préjudice économique : ni vente perdue, ni marché concurrencé.
- Le projet est non-commercial, sans publicité ni monétisation.
- Le catalogue ne fournit aucun service de téléchargement public : il documente l'existence d'un
  fichier sur le réseau.

Ces arguments ne font pas le droit. Ils peuvent peser dans une discussion, pas dans un tribunal.

### Si vous opérez dans un cadre institutionnel

Pour une **bibliothèque, un musée, une fondation de préservation** ou toute structure publique,
des dérogations existent peut-être (exceptions pédagogiques ou de préservation patrimoniale,
selon les pays). Faites valider par votre service juridique ; ne supposez pas qu'elles couvrent
automatiquement le P2P.

---

## 3. Ce qu'un VPN protège vraiment (et pas)

### Ce qu'un VPN bien configuré (gluetun) protège

- **Votre IP domestique face aux pairs eMule** : ils voient l'IP du serveur VPN, pas la vôtre.
- **Vos flux face à votre FAI** : il ne voit qu'un tunnel chiffré ; une requête d'ayant droit ne
  lui retournera rien d'utile dans une procédure légale ordinaire.

### Ce qu'un VPN *ne protège pas*

- **Une procédure judiciaire visant votre fournisseur VPN.** Il peut être contraint de livrer ses
  logs, ou de prouver qu'il n'en garde pas. Un « no-log » avéré protège en théorie ; vérifiez
  sa juridiction et son historique.
- **Une fuite DNS ou IPv6.** Des résolutions DNS hors tunnel ou un IPv6 en clair font fuir votre
  IP ; gluetun les bloque par défaut dans `deploy/gluetun.compose.yml`, c'est une de ses raisons
  d'être.
- **Une corrélation de timing.** Seul en France à télécharger une œuvre obscure à 3h du matin,
  vous restez identifiable par analyse de flux côté FAI malgré le VPN. Science-fiction ici : cible
  trop banale, volume trop faible.
- **Un compromis de votre machine.** Un attaquant entré dans votre conteneur amuled (rappel :
  amuled n'est pas durci, [risque accepté](limits.md)) atteint `downloads/` monté en bind et
  l'état d'amuled : pas votre IP via le VPN, mais tout leur contenu.

---

## 4. Recommandations opérationnelles

Pour minimiser votre exposition :

- **Préférez la pile VPN** (gluetun, Low-ID) à la pile par défaut.
- **N'exposez sur Internet ni la WebUI ni `/metrics`.** Restez en réseau local, ou passez par un
  VPN d'accès (WireGuard, Tailscale) et un reverse proxy avec auth.
- **Ne partagez pas votre IP publique** sur des forums liés au projet : « mon nœud est ici, venez
  voir » vous expose même via VPN si vous êtes seul à l'utiliser à cet instant.
- **Gardez votre système à jour.** Un port entrant ouvert (Route B) ou un conteneur compromis
  élargissent votre surface d'attaque.
- **Ne mélangez pas les usages.** Ce nœud ne sert qu'à mulewatch : pas de bibliothèque P2P
  partagée pré-existante, pas d'autres tests.

Pour collaborer avec d'autres chercheurs, voir
[la page d'accueil](index.md#partage) :
partage de catalogues hors-ligne.
