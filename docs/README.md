# Documentation — emule-indexer

`emule-indexer` surveille en continu le réseau eMule (eD2k + Kad) pour retrouver les épisodes perdus
de la VF de *Keroro mission Titar*, en cataloguant les métadonnées disponibles au passage. Contrainte
de conception : **le sujet du catalogue est le fichier, jamais la personne** (pas de pistage, pas de
désanonymisation).

Cette doc est organisée **par audience**. Choisissez votre point d'entrée :

## Opérateur / hébergeur de nœud

Vous voulez **déployer et exploiter** un nœud (homelab, serveur). *Prérequis honnête : cela suppose
d'être à l'aise avec un **terminal** et **Docker** (orientation Linux/serveur) ; l'état par défaut
**Low-ID** suffit pour contribuer.*

- **[Runbook de déploiement](runbooks/deployment.md)** — *monter* la stack `docker compose` et la voir
  tourner : matrice de choix (stacks A/B/C/D), profils observer/download, secrets, premier boot, Low-ID.
- **[Runbook d'administration](runbooks/administration.md)** — *exploiter et régler* un nœud monté :
  cycle de vie, High-ID (optionnel), analyse antivirus (clamav), métriques Prometheus, durcissement
  gVisor, outils de catalogue (fusion/compaction/validation), limites connues.
- **[Runbook de dépannage](runbooks/troubleshooting.md)** — *résoudre un problème* concret, quel que
  soit votre niveau : symptôme → cause → solution.
- **[Légalité, confidentialité, éthique](legal-and-privacy.md)** — ce que votre nœud catalogue et
  stocke (et ce qu'il ne stocke pas), le risque légal honnêtement, ce qu'un VPN protège vraiment.
  À lire avant de déployer un nœud public.

## Collaboration entre chercheurs

`emule-indexer` est conçu pour qu'**un chercheur déploie son propre nœud** ; il n'y a **pas de hub
central** et c'est volontaire (non-objectif pour la v0.x). La collaboration se fait **hors-ligne**,
en partageant des bases SQLite (`catalog.db`) entre chercheurs qui ont chacun leur propre nœud.

**Architecture :**
- Chaque chercheur héberge **une instance complète** (un crawler + un amuled + optionnellement
  verifier/webui). Chaque instance gère sa propre base `catalog.db`.
- Les instances **ne se connaissent pas** entre elles ; elles ne se synchronisent pas.
- Pour partager ses découvertes : envoyer son `catalog.db` (via un drive partagé, un git LFS,
  un Nextcloud, peu importe le moyen) à un autre chercheur, qui le **fusionne** dans son catalogue
  avec l'outil `merge`.

**Outil de fusion :** chaque chercheur peut fusionner N catalogues collectés vers un seul, avec
[l'outil `emule_indexer.merge` documenté dans le runbook d'administration § Outils de catalogue](runbooks/administration.md#outils-de-catalogue).
La fusion est **idempotente** (re-merger le même fichier est un no-op) et **safe-by-default**
(pas d'écrasement sans `--force`). Chaque fichier est identifié par son **empreinte de contenu
eD2k** — la fusion ne crée jamais de doublons.

**Cycle de partage typique :**

1. Vous catalogez localement pendant N semaines.
2. Vous exportez votre `catalog.db` (copie depuis le volume Docker, voir runbooks/administration.md
   § Planification disque).
3. Vous l'échangez avec d'autres chercheurs via un canal hors-ligne.
4. Vous re-fusionnez les catalogues reçus dans le vôtre : `python -m emule_indexer.merge --output
   catalog-merged.db votre-catalog.db catalog-reçu-de-X.db catalog-reçu-de-Y.db`.
5. Vous remplacez votre `catalog.db` actif par `catalog-merged.db` (arrêter le crawler, swap,
   redémarrer).

**Ce qui n'existe pas (à ce stade) :**
- Pas de protocole de découverte des autres chercheurs.
- Pas de notification automatique « un autre nœud a trouvé un fichier que vous cherchez ».
- Pas de synchronisation en temps réel ni de hub central.

Ces fonctionnalités peuvent émerger un jour si la communauté grossit ; pour l'instant, le partage
manuel suffit largement.

## Développeur / contributeur / CI

Vous **modifiez le code** ou montez la CI : comment lancer les suites de tests (gate par paquet +
suites d'intégration), leurs prérequis exacts, les pistes d'intégration continue, et l'architecture /
les décisions de conception.

- **[Guide des tests](testing-guide.md)** — toutes les suites (unitaire + intégration) + pistes CI +
  outils de diagnostic.
- **[Specs de conception](specs/)** — le design MVP autoritatif (17 sections) et
  l'architecture (moteur de matching, hexagonal/Clean).
- Le **gate** (commandes de build/test/lint, règles dures) est décrit dans le `CLAUDE.md` à la racine.

## Historique / trace de décision

Le **pourquoi** des choix, jalon par jalon, et les plans d'implémentation exécutés.

- **[Handoffs](handoffs/)** — un guide de continuation par jalon (`<date ISO> - handoff - <contexte>.md`) ;
  le plus récent est le point d'entrée du contexte courant.
- **[Plans d'implémentation](plans/)** — les plans exécutés en mode subagent-driven.
- **[Notes de référence](reference/)** — constats empiriques datés (richesse des champs EC, opcodes
  download, etc.).
