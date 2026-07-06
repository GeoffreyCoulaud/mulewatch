-- catalog.db — migration 0003: composite read-path indices for the webui explorer/dashboard.
-- The webui derives "latest row per group" (decisions per (hash, target), latest verification
-- per hash) with ROW_NUMBER() window functions; these indices let SQLite drive those scans by
-- index instead of the earlier correlated COUNT(*)=0 subqueries (quadratic on accumulated
-- history). file_verifications gets its FIRST ed2k_hash index (it had none, forcing a full
-- table scan on every /files render, the worst offender). file_observations keeps only its
-- existing idx_file_observations_ed2k_hash: the window's per-hash sort is cheap (few
-- observations per file) and a composite there was measured unused by the planner, so it is
-- not added to that hot append path. ADDITIVE DDL: no table rebuilt, the append-only triggers
-- from 0001 are untouched.

CREATE INDEX idx_match_decisions_hash_target_decided
ON match_decisions (ed2k_hash, target_id, decided_at);

CREATE INDEX idx_file_verifications_hash_verified
ON file_verifications (ed2k_hash, verified_at);
