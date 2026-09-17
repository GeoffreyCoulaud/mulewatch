# WebUI — lecture WAL vivante inter-conteneurs en mode `ro`

**Date :** 2026-06-22  
**Statut (2026-06-25) :** **CLOS — `:ro` abandonné en faveur de `PRAGMA query_only=ON` applicatif.**

> ✅ **Décision prise.** Le montage Docker `:ro` a été **retiré** des `examples/*.yaml` (et de
> `bricks/compose.core.yaml`). Les bases sont désormais montées **RW** au niveau FS ; la garantie
> lecture seule reste assurée par `mode=ro` (URI SQLite) + `PRAGMA query_only=ON` côté webui (cf.
> `catalog_webui/adapters/db.py`). Solution **aussi sûre** au niveau SQL et **plus robuste**
> vis-à-vis du WAL (pas de risque d'`EROFS` sur `-shm`/`-wal` mmap). Voir runbook d'administration
> § WebUI (commit `12d9dad`) et le yaml change (commit `b0b5faf`).
>
> Le reste du document est conservé comme **trace de la décision** (contexte, alternatives évaluées).

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

## Décision (2026-06-25)

`:ro` au niveau Docker a été **retiré** sans test homelab préalable parce que les deux pragmas
applicatifs (`mode=ro` URI + `query_only=ON`) suffisent à garantir la lecture seule au niveau SQL,
et que le risque d'`EROFS` sur les fichiers WAL auxiliaires rendait le montage `:ro` fragile. La
solution actuelle est **plus simple** (un seul mécanisme de garantie au lieu de deux) et **plus
robuste** (pas d'interaction `mmap`/FS).

Les questions originellement listées comme « à valider » sont donc devenues sans objet.

## Références

- [SQLite WAL — Readonly Databases](https://www.sqlite.org/wal.html#readonly) : note officielle sur
  `-shm` en lecture seule.
- `catalog_webui/adapters/db.py` — implémentation de `open_ro`.
- `docs/runbook-administration.md` — section « WebUI (consultation du catalogue) ».
