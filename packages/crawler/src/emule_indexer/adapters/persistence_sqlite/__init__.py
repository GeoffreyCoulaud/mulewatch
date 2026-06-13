"""Adapter persistence SQLite : les deux bases du spec MVP §11/§12 (spec data-model §4).

``catalog.db`` (append-only, adressé par contenu, prêt à fusionner) et ``local.db``
(opérationnel, jamais fusionné). ``sqlite3`` stdlib, repositories SYNCHRONES, SQL à la
main (spec §3) ; migrations ``.sql`` embarquées dans ``migrations/`` (lues via
``importlib.resources``), lintées par sqlfluff au gate.
"""
