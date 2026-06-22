# WebUI — lecture WAL vivante inter-conteneurs en mode `ro`

**Date :** 2026-06-22  
**Statut :** Question ouverte — à valider au premier déploiement homelab

## Contexte

Le service `webui` monte les volumes `catalog-db` et `local-db` en `:ro` dans `bricks/compose.core.yaml`.
Ces bases sont ouvertes en **mode WAL** par le crawler (écriture continue). SQLite WAL utilise deux
fichiers auxiliaires (`-shm` et `-wal`) dont les accès `mmap` peuvent être problématiques quand le
système de fichiers est monté avec `mode=ro` au niveau du noyau.

## Comportement attendu

SQLite supporte `ATTACH ... READ ONLY` et `PRAGMA query_only=ON` (utilisé par `open_ro` dans
`catalog_webui/adapters/db.py`) — mais ces pragmas opèrent **après** que le fichier est ouvert.
L'ouverture initiale d'une base WAL requiert une écriture sur le fichier `-shm` (shared memory
index), même en lecture seule. Si le FS est monté `mode=ro`, cette écriture échoue avec `EROFS`.

## Repli recommandé

Si la WebUI ne démarre pas ou retourne `unable to open database file` / `attempt to write a
readonly database`, retirer `:ro` du montage des volumes DB dans votre fichier `examples/*.yaml` :

```yaml
volumes:
  - catalog-db:/data/catalog    # RW au niveau FS — open_ro garde PRAGMA query_only=ON
  - local-db:/data/local        # idem
```

La garantie applicative reste assurée : `open_ro` ouvre avec `uri=True` et
`PRAGMA query_only=ON`, ce qui interdit toute écriture au niveau SQL.

## À valider

- [ ] Le montage `:ro` fonctionne-t-il quand le crawler tourne simultanément (WAL actif) ?
- [ ] Le montage `:ro` fonctionne-t-il quand le crawler est arrêté (base en état checkpoint, pas de `-wal`) ?
- [ ] Documenter le verdict ici et mettre à jour le runbook d'administration en conséquence.

## Références

- [SQLite WAL — Readonly Databases](https://www.sqlite.org/wal.html#readonly) : note officielle sur
  `-shm` en lecture seule.
- `catalog_webui/adapters/db.py` — implémentation de `open_ro`.
- `docs/runbook-administration.md` — section « WebUI (consultation du catalogue) ».
