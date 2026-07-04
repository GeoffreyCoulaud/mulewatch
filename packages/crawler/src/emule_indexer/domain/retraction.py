"""Retraction: the sentinel decision recorded when a file stops matching (spec §5).

PURE domain. ``catalog.db``'s ``match_decisions`` table is append-only (DB triggers) —
there is no "delete" primitive for a stale decision. When the current matcher policy no
longer matches a previously-catalogued file, exclusion is represented as an *appended*
sentinel row: ``target_id=""``, ``rule_name=""``, ``tier=RETRACTED_TIER``.

``RETRACTED_TIER`` is deliberately NOT a member of ``catalog_matching.config.TIERS``
(``{"catalog", "notify", "download"}``): the matching engine never produces it — it is
synthesized by the crawler alone, on the "matched → no longer matched" transition.
"""

RETRACTED_TIER = "retracted"
