# Limites connues


- **Migrations : le tri se fait en mémoire, sans plafond (2026-07-16)** : les migrations SQLite
  s'appliquent avec `temp_store=MEMORY` (`connection.py`, restauré juste après). Motif : construire
  un index déborde le tmpfs de 64 Mo de `/tmp` et échoue en `SQLITE_FULL`, ce qui fait boucler le
  crawler au démarrage (constaté sur le node réel avec la migration 0004). Le remède alternatif,
  régler l'espace temporaire, vit dans le compose de l'opérateur : il peut être oublié au moment
  d'une montée d'image, et cet oubli casse le node ; l'image porte donc son propre remède. **Risque
  résiduel accepté** : le trieur en mémoire de SQLite ne se vide jamais et n'est borné ni par
  `cache_size` ni par autre chose que le nombre de lignes (environ 116 octets par ligne). Repère
  mesuré : 1,19 M d'observations donnent un pic d'environ 150 Mo, soit un plafond vers **4,5 M de
  lignes** — chiffre mesuré à l'époque où la limite était `mem_limit: 512m`. **Elle est passée à
  `2g`** depuis le passage à un conteneur unique (trois processus à loger, valeur encore à régler
  sur un nœud réel), donc le plafond réel est plus haut ; il n'a pas été re-mesuré. Une fois la
  limite atteinte, le conteneur est tué par le noyau (exit 137, journal vide) au lieu de
  produire une erreur lisible : voir la fiche
  [« Un conteneur redémarre en boucle »](troubleshooting-start.md#un-conteneur-redémarre-en-boucle). Ne
  pas « corriger » sans rouvrir la décision. À surveiller : `file_observations` croît sans borne, et
  c'est une **future** migration triant cette table qui pose le risque, pas 0004 (ponctuelle, déjà
  passée).
- **Durcissement conteneur, décisions au dossier (2026-06-17, mise à jour 2026-06-29, réduite
  2026-09-13, INVERSÉE pour le crawler 2026-09-16)** : le bac à sable gVisor (`runsc`) optionnel a
  été abandonné pour cause de YAGNI, et la blocklist seccomp par enfant ainsi que les rlimits ont
  quitté le projet le 2026-09-13, avec l'enfant d'analyse qu'elles confinaient. L'image à conteneur
  unique a ensuite réduit ce que le plancher portable peut tenir. PID 1 doit être root — il crée
  l'utilisateur `amule` à partir de `PUID`/`PGID`, fait le `chown` des bind mounts et écrit
  `amule.conf` — donc **`user:`, `read_only:` et `cap_drop: ALL` ne s'appliquent plus à aucun
  service livré** ; chaque service abandonne ses privilèges avec `setpriv` à la place. C'était
  délibéré, avec l'accord signé de l'opérateur (spec
  [`2026-09-16-single-container-embedded-amule.md`](https://github.com/GeoffreyCoulaud/mulewatch/blob/main/agents/specs/2026-09-16-single-container-embedded-amule.md)
  §9) : **le crawler est descendu au niveau de confinement d'amuled, plutôt qu'amuled ne monte à
  celui du crawler.** Ce qui reste, et que les fichiers compose doivent conserver, c'est
  `security_opt: no-new-privileges:true`, `pids_limit: 512` et `mem_limit: 2g` — les deux derniers
  relevés pour trois processus au lieu d'un, et tous deux encore à régler sur un nœud réel. **Pas
  encore validé sur matériel réel : `no-new-privileges` conjugué à `setpriv`.** Plus rien dans le
  crawler ne lance de sous-processus sur une entrée non fiable, et rien n'ouvre jamais un fichier
  téléchargé. L'isolation au niveau noyau au-delà de cela reste **explicitement hors périmètre** :
  `net=none`, bwrap et de vrais namespaces de montage en lecture seule exigent chacun soit
  `CAP_SYS_ADMIN`, soit des user namespaces non privilégiés (non portables : ils dépendent d'un
  sysctl de l'hôte et entrent en conflit avec le profil seccomp par défaut de Docker).
- **amuled n'est plus un conteneur tiers (2026-09-16)** : c'est un processus de notre propre image,
  donc la dérogation du 2026-06-17 qui l'exemptait de notre durcissement n'a plus rien à exempter —
  tout le service partage la posture ci-dessus. Le **risque résiduel est accepté, et il est
  désormais plus large** : la compromission de l'un quelconque des trois processus atteint les bind
  mounts `downloads/incoming` et `downloads/temp`, `data/` (le catalogue) **et** `amule/`. Ne
  « corrigez » pas cela sans rouvrir la décision au dossier.
- **port-sync, validation réelle** : la boucle est construite ; sa validation **bout-en-bout**
  (port-check High-ID réel derrière le VPN) se fait via un déploiement réel. Elle passe désormais
  par un `s6-svc -r` sur amuled dans le même conteneur, sans Docker ni socket : plus simple, mais
  encore jamais éprouvée sur du matériel réel.
- **Redémarrage d'un nœud 1.x : le backoff de recherche persisté repart de zéro, une fois.** Il est
  indexé sur le nom d'instance d'amuled, qui était lu dans le YAML (`amule-1`) et est maintenant une
  constante de code (`amuled`) ; il en va de même des clés de l'état d'ordonnancement. Les anciennes
  lignes restent dans `local.db` sans être relues. Sans conséquence pour une 2.0.0 cassante, mais
  autant ne pas être surpris.
- **Complétion de téléchargement, validation en conditions réelles** : la chaîne est **confirmée
  par la lecture des sources amont d'amuled** (voir
  [`agents/reference/2026-06-17-amuled-completion-behavior.md`](https://github.com/GeoffreyCoulaud/mulewatch/blob/main/agents/reference/2026-06-17-amuled-completion-behavior.md))
  et par un **transfert réel sur un nœud de production le 2026-09-11**, mais il n'existe **aucun
  test bout-en-bout sur un transfert réel** (cette suite e2e a été abandonnée, voir le guide des
  tests). Le décodage de `shared_files()` face à un amuled réel, lui, **est** couvert par
  `download_integration`.

  Mécanique : à la complétion, amuled déplace le fichier dans son **IncomingDir** et ne bascule le
  statut en « terminé » qu'ensuite (pas de course). Le crawler détecte la complétion comme
  **« partagé ET absent de la file de téléchargement »** (amuled partage automatiquement un fichier
  terminé, mais il partage aussi les fichiers partiels, donc c'est la file qui les sépare, voir
  CORRECTION 2026-09-11 dans la référence). Il enregistre alors l'état et notifie : depuis le
  2026-09-13, rien ne déplace le fichier, donc l'ancienne promotion depuis la quarantaine et sa
  gestion des collisions de noms ont disparu, et avec elles les contraintes sur un volume de
  quarantaine partagé et sur un système de fichiers Linux. Ce qui tient toujours : **aucune
  catégorie amuled** ne doit rediriger la destination, et amuled doit être **dédié** au crawler avec
  un **petit ensemble partagé**.

  **Conséquence de l'abandon de l'étape de promotion, pas encore mesurée** : les fichiers terminés
  restent désormais indéfiniment dans `IncomingDir`, donc la liste des fichiers partagés d'amuled
  croît sans borne, et elle est lue à chaque cycle de téléchargement. Sur un nœud de longue durée
  comptant beaucoup de téléchargements terminés, attendez-vous à une détection de complétion plus
  lente. Le ménage dans `downloads/incoming` est à la charge de l'opérateur.

- **WebUI (lecture seule)** : **point clos**. La WebUI est désormais servie **en intra-processus**
  par le crawler (plus de conteneur séparé, donc plus de montage inter-conteneurs). La garantie
  lecture seule repose sur `mode=ro` + `PRAGMA query_only=ON` ; l'ancien montage Docker `:ro` WAL
  est caduc. Voir section « WebUI » plus haut et
  [`agents/reference/2026-06-22-webui-wal-readonly.md`](https://github.com/GeoffreyCoulaud/mulewatch/blob/main/agents/reference/2026-06-22-webui-wal-readonly.md).
- **Hub central / rétention** : non planifiés à ce stade.
