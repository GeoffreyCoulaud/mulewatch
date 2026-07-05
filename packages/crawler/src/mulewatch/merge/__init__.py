"""Standalone catalog merge script (N ``catalog.db`` → 1, idempotent).

**Tool** subpackage of the crawler package, fully disjoint from the app (spec
``docs/specs/2026-06-15-fusion-merge-design.md``): it only imports
``adapters.persistence_sqlite.connection.open_catalog`` (to create the output schema
via the ``0001`` migration) + the stdlib. Entry point: ``python -m mulewatch.merge``.
No dependency on the domain, the application, or the composition.
"""
