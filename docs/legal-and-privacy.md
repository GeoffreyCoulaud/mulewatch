# Légalité, confidentialité, éthique — pour l'opérateur d'un nœud

Ce guide s'adresse à **vous qui hébergez un nœud** `mulewatch` chez vous, sur un VPS, ou dans
une infra que vous administrez. Il répond honnêtement à trois questions :

1. **Qu'est-ce que mon nœud catalogue / stocke / transfère ?** (ce qui finit sur votre disque, ce
   qui circule sur votre réseau, ce qui n'existe nulle part)
2. **Qu'est-ce que je risque légalement ?** (le risque réel, qui dépend surtout de votre
   juridiction et de votre choix de stack)
3. **Qu'est-ce qu'un VPN protège vraiment ?** (et ce qu'il ne protège pas)

Ce document n'est **pas un avis juridique**. Si vous opérez dans un cadre institutionnel
(université, association de préservation, employeur), faites valider par un juriste qui connaît
votre juridiction.

---

## 1. Ce que votre nœud catalogue, stocke, transfère

### Ce qui finit dans le catalogue (volumes `catalog-db` et `local-db`)

- **Empreintes eD2k (hashes)** des fichiers vus sur le réseau eMule.
- **Noms de fichiers** tels qu'observés sur le réseau (les pairs publient ces noms pour leurs
  partages).
- **Tailles, types MIME** détectés.
- **Sources EC** sous forme d'identifiants anonymes (l'aMule local rapporte combien de pairs ont
  une copie, pas qui sont ces pairs).
- **Décisions de matching** : à quelle cible (épisode recherché) un fichier correspond, selon les
  règles YAML configurées.
- **Verdicts de vérification** : pour les fichiers téléchargés en mode download, le résultat des
  checks (`type_sniff`, `ffprobe`, `clamav`).
- **Métadonnées techniques de votre nœud** : `node_id` interne, état du scheduler, dernière passe
  de catalogage. Pas d'info utilisateur.

### Ce qui ne finit *pas* dans le catalogue

- **Aucune IP de pair eMule.** Le crawler interroge aMule via son protocole EC ; aMule expose des
  identifiants opaques pour les sources, jamais d'adresses IP.
- **Aucune trace utilisateur.** Pas de cookies, pas de session, pas de log d'accès — la WebUI est
  en lecture seule et n'authentifie personne (l'auth doit être fournie par un reverse proxy en
  amont si vous l'exposez).
- **Aucune télémétrie sortante.** Le crawler n'envoie rien à un service tiers. Les métriques
  Prometheus sont **locales** (vous les scrapez, ou vous lancez le profil `monitoring` qui les
  affiche dans votre Grafana sur votre machine).
- **Aucun contenu de fichier dans le catalogue.** Même en mode download, seul le `hash`, le nom et
  les métadonnées sont indexés — le fichier lui-même vit dans le volume `quarantine`, séparément.

### Ce qui circule sur votre réseau

- **Stack A/B (avec VPN gluetun)** : tout le trafic P2P passe par le tunnel VPN. Votre fournisseur
  d'accès Internet (FAI) ne voit que du trafic chiffré vers votre fournisseur VPN.
- **Stack C/D (sans VPN)** : le trafic P2P sort en clair depuis votre IP domestique. Votre FAI voit
  les connexions vers les pairs eMule (pas le contenu, mais les flux).
- **Trafic eMule** : protocole non chiffré (eD2k est ancien). Un pair sur le réseau peut voir
  quels fichiers vous demandez et quels hashes vous proposez.

### Ce qui finit sur votre disque

- Les bases SQLite du catalogue (`catalog.db`, `local.db`) : qq Mo à qq Go selon l'usage et la
  compaction (cf. [runbook-administration § Planification disque](runbooks/administration.md#planification-disque)).
- En **mode download** : les fichiers téléchargés, en quarantaine puis remis à votre disposition
  s'ils passent la vérification.

---

## 2. Risque légal — honnêtement

### Le constat de base

**Partager une œuvre soumise au droit d'auteur sans autorisation est illégal dans la plupart des
juridictions.** C'est vrai dès que vous faites tourner un nœud eMule, quel que soit le mode :

- **Mode observer** (sans téléchargement) : techniquement, votre aMule annonce une « source » sur
  le réseau dès qu'il a un fichier dans son IncomingDir. En pratique, l'IncomingDir est quasi vide
  en mode observer (vous ne téléchargez rien) — votre exposition est faible.
- **Mode download** : vous téléchargez ET re-partagez (eMule est un réseau symétrique : ce que
  vous prenez, vous le rendez disponible aux autres pairs pendant qu'il est dans votre dossier
  partagé).
- **High-ID Route B** : vous ouvrez un port sur votre box, ce qui augmente votre visibilité comme
  source — vous êtes joignable directement par les pairs, votre IP est visible.

### Le risque pratique pour ce projet

Le risque est **statistiquement faible mais non nul**, et dépend de trois facteurs :

1. **Votre juridiction.** France et Belgique ont des dispositifs actifs (Hadopi en France, géré
   par l'Arcom depuis 2022). L'Allemagne pratique les *Abmahnungen* (avertissements payants par
   les ayants droit). La Suisse, le Canada, beaucoup d'autres juridictions sont moins agressives
   sur le P2P. Renseignez-vous sur votre pays.
2. **La nature de votre cible.** Ce projet vise des **médias perdus** (œuvres non rééditées, aux
   ayants droit inactifs ou introuvables). Statistiquement, ces œuvres ne mobilisent personne —
   les surveillances P2P ciblent les nouveautés à forte valeur commerciale, pas les épisodes
   d'un dessin animé Teletoon de 2008.
3. **Votre choix de stack.** Stack A/B (VPN gluetun) masque votre IP au FAI et aux pairs eMule.
   Stack C/D expose votre IP domestique.

**Aucune de ces protections n'est une absolution juridique.** Si une procédure vous tombe dessus,
« j'utilisais un VPN » n'est pas une défense — c'est juste plus difficile pour la partie
adverse de remonter à vous.

### Ce qui distingue ce projet d'un client P2P généraliste

Vous ne cherchez **pas** des nouveautés. Vous cherchez ce que **personne ne re-diffuse plus**.
Argumentairement :

- Un fichier *retrouvé* enrichit le patrimoine et, dans la mesure où l'ayant droit est inactif,
  ne lui cause aucun préjudice économique (pas de vente perdue, pas de marché concurrencé).
- Le projet est explicitement **non-commercial**, sans publicité, sans monétisation.
- Le catalogue ne sert pas à fournir un service de téléchargement public — il documente
  l'existence d'un fichier sur le réseau (preuve d'existence).

Ces arguments ne font pas le droit. Ils peuvent peser dans une discussion, pas dans un tribunal.

### Si vous opérez dans un cadre institutionnel

Si votre nœud tourne pour le compte d'une **bibliothèque, d'un musée, d'une fondation de
préservation** ou de toute structure publique, vous bénéficiez potentiellement de **dérogations
spécifiques** (exceptions pédagogiques, exceptions de préservation patrimoniale dans certains
pays). Faites valider par votre service juridique — ne déployez pas en supposant que ces
dérogations couvrent automatiquement le P2P.

---

## 3. Ce qu'un VPN protège vraiment (et pas)

### Ce qu'un VPN bien configuré (stack A/B avec gluetun) protège

- **Votre IP domestique vis-à-vis des pairs eMule.** Les autres clients sur le réseau voient l'IP
  du serveur VPN, pas la vôtre.
- **Vos flux vis-à-vis de votre FAI.** Votre FAI voit un tunnel chiffré vers votre fournisseur
  VPN, pas le contenu du trafic.
- **Votre IP dans une procédure légale ordinaire.** Une requête d'ayant droit à votre FAI ne
  retourne rien d'utile (le FAI ne voit que le tunnel VPN).

### Ce qu'un VPN *ne protège pas*

- **Une procédure judiciaire visant votre fournisseur VPN.** Les fournisseurs VPN peuvent être
  contraints de fournir des logs (ou de prouver qu'ils n'en gardent pas). En théorie, un VPN
  « no-log » avéré est protecteur ; en pratique, vérifiez la juridiction du fournisseur et son
  historique.
- **Une fuite DNS ou IPv6.** Si votre système fait des résolutions DNS hors tunnel, ou si IPv6
  passe en clair, votre IP fuit. gluetun bloque ces fuites par défaut dans la stack `deploy/examples/`
  — c'est une de ses raisons d'être.
- **Une corrélation de timing.** Si vous êtes la seule personne en France à télécharger une œuvre
  obscure à 3h du matin, une analyse de flux côté FAI peut vous identifier malgré le VPN. Pour ce
  projet, c'est de la science-fiction (la cible est trop banale et le volume trop faible pour
  justifier une telle analyse).
- **Un compromis de votre machine.** Si un attaquant entre dans votre conteneur amuled (rappel :
  amuled n'est pas durci, [risque accepté pour v0.x](runbooks/administration.md#limites-connues--follow-ups)),
  il accède au volume quarantaine — pas à votre IP via le VPN, mais à tout ce qui est sur ce
  volume.

---

## 4. Recommandations opérationnelles

Si vous voulez minimiser votre exposition :

- **Préférez Stack A** (VPN gluetun, Low-ID) à Stack C/D (sans VPN).
- **N'exposez pas Grafana, ni la WebUI, ni le verifier sur Internet.** Restez en réseau local, ou
  passez par un VPN d'accès (WireGuard, Tailscale) + reverse proxy avec auth.
- **Ne partagez pas votre IP publique** sur des forums liés au projet (« mon nœud est ici, venez
  voir » expose votre IP même via VPN si vous êtes le seul à utiliser ce VPN à cet instant).
- **Gardez votre système à jour.** Un port entrant ouvert (Route B) ou un conteneur compromis
  élargissent votre surface d'attaque.
- **Ne mélangez pas usages.** N'utilisez pas ce nœud pour autre chose que mulewatch (pas de
  bibliothèque P2P partagée pré-existante, pas de tests autres).

Si vous opérez en collaboration avec d'autres chercheurs, voir
[docs/README.md § Collaboration](README.md) pour le partage de catalogues hors-ligne.

---

## Pour aller plus loin

- [`runbooks/administration.md`](runbooks/administration.md) — opérations courantes, sécurité du
  durcissement, durcissement conteneur.
- [`runbooks/troubleshooting.md`](runbooks/troubleshooting.md) — quand quelque chose casse.
- [`CLAUDE.md`](../CLAUDE.md) — invariants de design (notamment : « le sujet du catalogue est le
  fichier, jamais la personne »).
