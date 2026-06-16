"""Script standalone de fusion de catalogues (N ``catalog.db`` → 1, idempotent).

Sous-paquet **outil** du paquet crawler, totalement disjoint de l'app (spec
``docs/superpowers/specs/2026-06-15-fusion-merge-design.md``) : il n'importe que
``adapters.persistence_sqlite.connection.open_catalog`` (pour créer le schéma de sortie
via la migration ``0001``) + la stdlib. Point d'entrée : ``python -m emule_indexer.merge``.
Aucune dépendance au domaine, à l'application ou à la composition.
"""
