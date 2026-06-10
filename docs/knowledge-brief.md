# Knowledge Brief — emule-indexer

> Document de cadrage issu de la session « pick-my-brain » du 2026-06-09.
> Sert de base à la conception. À relire/amender avant toute implémentation.

## Contexte & objectif

- **Lost media** : doublage **VF de « Keroro mission Titar »** (diffusé en 2008 sur Teletoon, France). La grande majorité des épisodes est perdue ; une liste canonique existe (Wikipédia) avec un statut par épisode (complet / partiel / perdu / piètre qualité). Une communauté s'organise sur un Discord.
- **Objectif principal** : retrouver un maximum d'épisodes perdus.
- **Objectif secondaire (toujours utile)** : cataloguer des métadonnées même sans téléchargement — quels épisodes, durée, hash, **qui les détient** et **quels autres fichiers** ces sources partagent.
- **Mécanisme observé** : les fichiers apparaissent **par intermittence**, quand un détenteur se connecte (un serveur eD2k a alors une source). L'épisode 62 a été trouvé ainsi, par hasard. → Il faut transformer ce hasard en **surveillance permanente** (et **distribuée**).
- **Limite éthique nette** : le sujet du catalogue est **le fichier/l'épisode, pas la personne**. Une source n'est qu'un vecteur vers d'autres épisodes. **Pas de pistage ni de désanonymisation.** → minimisation des données.

## Contraintes

- **Clean Architecture** (Bob Martin), ports/adapters. Pas de spaghetti.
- Langages autorisés : **Python, Kotlin, Java, JS, TS** (rien d'autre sans discussion). Geoffrey doit pouvoir **lire et comprendre tout le code** ; une dépendance externe de confiance (type Postgres, aMule) est acceptable en boîte noire.
- **Python par défaut** : `uv` + `mypy --strict` + `ruff` (lint + format). Repli **Kotlin** uniquement si un besoin **CPU-bound** est démontré (charge attendue **IO-bound**).
- **Le crawler ne doit rien manquer** de vivant et fréquenté : filet large, compatibilité maximale.
- **Déploiement homelab**, empaqueté **Docker** (`docker compose up -d`), config minimale (compte VPN compatible **ou** port ouvert). Lançable à terme par des non-techniciens.
- **Légal/risque** : passer par **ProtonVPN** (suisse, no-log, serveurs P2P) masque l'IP maison et absorbe les plaintes. Partie download = profil de risque isolé, partage minimisé. (Posture de réduction de risque, pas un avis juridique.)

## Décisions verrouillées

### Réseau & couverture
- **Moteur protocolaire = aMule seul** (`amuled` headless + API **EC**), piloté comme une brique d'infra.
  - *Pourquoi pas MLDonkey* : les serveurs eD2k sont **partagés** par tous les clients (un seul suffit) ; côté sans-serveur, le **Kad** vivant est celui d'eMule/aMule, tandis que le réseau de MLDonkey (dérivé d'**Overnet**) est **mort** depuis 2006 et de toute façon incompatible. MLDonkey n'apporterait **aucune** couverture vivante.
  - **Overnet = mort** : plus de bootstrap ni de pairs → un réseau mort n'héberge personne, donc aucun détenteur à y rater. Son héritier vivant est **Kad** (couvert par aMule).
- **G2 / Gnutella = HORS SCOPE** (décision, pas dette).
  - *Pourquoi* : réseau séparé et petit ; la communauté cible (fans FR ère eMule) n'y est pas ; **hash différent** (eD2k=MD4 vs G2=SHA-1/TTH → pas de clé de contenu commune) ; **aucune API headless propre**. ROI trop faible. L'abstraction « moteur source » garde toutefois la porte ouverte si un jour décision contraire.
- **Seeding = abandonné** : aucun sens ici (raretés, file courte) et absurde (un nœud Keroro ne va pas seeder des ISOs). High ID + présence + échange de sources suffisent.
- **High ID** : un **seul numéro de port** ProtonVPN (NAT-PMP), configuré **identique en TCP et UDP** côté aMule → High ID + Kad « OK ». **glueforward** (projet de Geoffrey) étendu pour synchroniser le port dynamique dans la config aMule. gluetun tient le tunnel.

### Données & consolidation
- **Persistance = SQLite par nœud.** Modèle **adressé par contenu** (clé = **hash eD2k**/AICH) + **append-only** (observations horodatées « source S a vu F à T »). Le fichier DB **est** l'export partageable.
- **Consolidation = décentralisée / fusion par lots** d'exports entre chercheurs (sans conflit grâce à l'adressage par contenu). **Évolutif vers un hub central** (option C : push + identités de bots autorisées), qui pourrait être en **Postgres** — les nœuds restant en SQLite, « fusionner » = « importer ».

### Pipeline & sorties
- **Pipeline** : (1) **recherche par mot-clé** (mot-clé large `Keroro` + secondaires par épisode : titres, `Mission Titar`/`Titar`, n° `0XX`, `TELETOON`, dates) → fichiers candidats + compteurs de sources ; (2) **scoring** vs liste canonique (et détection d'une **meilleure version** d'un épisode déjà en piètre qualité) ; (3) **résolution de sources** pour les candidats (→ métadonnées « qui ») ; (4) **download optionnel**. Recherches **répétées en continu** (intermittence), en **respectant les rate-limits** serveurs.
- **« Qui détient quoi d'autre »** se reconstruit depuis **nos propres observations croisées** (la même source apparaissant pour plusieurs fichiers), pas en interrogeant le pair (le « voir fichiers partagés » est souvent refusé).
- **Observabilité / sorties** : **logs** + **métriques Prometheus** + **notifications via apprise** (canaux Discord/Telegram/etc. bridgés). **UI web d'admin = nice-to-have, déprioritisée.**
- **Adaptateur EC** : aucune lib Python EC n'existe (impl. PHP et Node.js seulement) → **on écrit notre client EC en Python** (protocole binaire documenté : couche transport 2×int32, couche appli opcode + tags). Image Docker `ngosang/docker-amule` disponible.

## Questions ouvertes (à trancher en conception — phase B)

- **Format d'export/fusion exact** (fichier SQLite brut ? dump ? event log ?).
- **Stratégie anti-rate-limit** + maintien d'une **liste de serveurs eD2k fraîche** + **bootstrap Kad** fiable.
- **Schéma append-only** détaillé (entités : Fichier, Observation, Source, Épisode-cible, Score…).
- **Richesse réelle des champs par source** exposés par l'EC à l'étape « résolution de sources » → **à mesurer empiriquement** (décide d'un éventuel code custom de crawling).
- **Langage final** : Python (défaut) vs Kotlin si CPU-bound avéré.
- **Politique de rétention** concrète (quelles métadonnées de source, combien de temps).

## Hypothèses à valider

- aMule 3.x headless + EC pilotable proprement depuis Python **et** en conteneur derrière gluetun (très probable).
- Réseau eD2k/Kad encore assez vivant en 2026 pour que la surveillance ait du sens (quelques gros serveurs subsistent ; le détenteur de l'ép. 62 le prouve).
- Hash eD2k suffisant comme clé de fusion. **Nuance** : un épisode **ré-encodé** = hash différent → corrélation « même épisode, versions différentes » au niveau **scoring/métadonnées**, pas au niveau hash.

## Hors scope

- Post-traitement des épisodes récupérés (vérif humaine, remux, archivage long terme, upload Discord/Mega/YouTube).
- Avis juridique formel.
- Autres réseaux P2P (BitTorrent…) et **G2/Gnutella**.
- Recherche d'autres lost media que Keroro (l'archi pourra le permettre).
- Sécurité/auth du futur hub central (lié à l'option C).
