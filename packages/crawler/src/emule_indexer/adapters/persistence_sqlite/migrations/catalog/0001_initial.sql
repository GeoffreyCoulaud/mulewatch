-- catalog.db — migration 0001 : schéma complet (spec data-model §5 ; spec MVP §11).
-- Append-only IMPOSÉ PAR LA BASE : triggers BEFORE UPDATE / BEFORE DELETE sur CHAQUE
-- table → RAISE(ABORT). Tient contre UPDATE/DELETE/UPSERT dans tous les cas ; contre
-- INSERT OR REPLACE seulement si la connexion pose PRAGMA recursive_triggers=ON (nos
-- connexions le font ; un outil tiers sur PRAGMA par défaut peut passer outre, comme
-- il peut DROP TRIGGER). Clé contenu = hash eD2k (hex minuscule 32, canon v0.5.0).
-- Timestamps ISO-8601 UTC en TEXT ; raw_meta = JSON liste de paires (ordre + doublons).

CREATE TABLE files (
    ed2k_hash TEXT PRIMARY KEY,
    size_bytes INTEGER NOT NULL,
    aich_hash TEXT,
    CHECK (LENGTH(ed2k_hash) = 32 AND ed2k_hash NOT GLOB '*[^0-9a-f]*')
);

CREATE TABLE file_observations (
    id INTEGER PRIMARY KEY,
    ed2k_hash TEXT NOT NULL REFERENCES files (ed2k_hash),
    filename TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    source_count INTEGER NOT NULL,
    complete_source_count INTEGER NOT NULL,
    media_length_sec INTEGER,
    bitrate_kbps INTEGER,
    codec TEXT,
    file_type TEXT,
    raw_meta TEXT NOT NULL,
    keyword TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    node_id TEXT NOT NULL
);

CREATE INDEX idx_file_observations_ed2k_hash ON file_observations (ed2k_hash);
CREATE INDEX idx_file_observations_observed_at ON file_observations (observed_at);

CREATE TABLE sources (
    user_hash TEXT PRIMARY KEY,
    client_name TEXT,
    client_version TEXT
);

CREATE TABLE source_observations (
    id INTEGER PRIMARY KEY,
    user_hash TEXT REFERENCES sources (user_hash),
    ed2k_hash TEXT NOT NULL REFERENCES files (ed2k_hash),
    ip TEXT,
    port INTEGER,
    nickname TEXT,
    client_name TEXT,
    client_version TEXT,
    country TEXT,
    id_type TEXT,
    has_complete_file INTEGER CHECK (has_complete_file IN (0, 1)),
    origin TEXT,
    raw_meta TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    node_id TEXT NOT NULL
);

CREATE INDEX idx_source_observations_ed2k_hash ON source_observations (ed2k_hash);
CREATE INDEX idx_source_observations_user_hash ON source_observations (user_hash);

CREATE TABLE match_decisions (
    id INTEGER PRIMARY KEY,
    ed2k_hash TEXT NOT NULL REFERENCES files (ed2k_hash),
    target_id TEXT NOT NULL,
    rule_name TEXT NOT NULL,
    tier TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    node_id TEXT NOT NULL
);

CREATE INDEX idx_match_decisions_ed2k_hash ON match_decisions (ed2k_hash);

CREATE TABLE file_verifications (
    id INTEGER PRIMARY KEY,
    ed2k_hash TEXT NOT NULL REFERENCES files (ed2k_hash),
    verdict TEXT NOT NULL,
    real_meta TEXT,
    checks TEXT,
    verified_at TEXT NOT NULL,
    node_id TEXT NOT NULL
);

-- ATTENTION migrations futures : une migration de type rebuild (CREATE nouvelle table /
-- INSERT … SELECT / DROP / ALTER … RENAME) supprime les triggers AVEC l'ancienne table —
-- toute migration de ce type DOIT les recréer (rien n'échouera bruyamment sinon).

CREATE TRIGGER files_no_update
BEFORE UPDATE ON files
BEGIN
    SELECT RAISE(ABORT, 'files est append-only');
END;

CREATE TRIGGER files_no_delete
BEFORE DELETE ON files
BEGIN
    SELECT RAISE(ABORT, 'files est append-only');
END;

CREATE TRIGGER file_observations_no_update
BEFORE UPDATE ON file_observations
BEGIN
    SELECT RAISE(ABORT, 'file_observations est append-only');
END;

CREATE TRIGGER file_observations_no_delete
BEFORE DELETE ON file_observations
BEGIN
    SELECT RAISE(ABORT, 'file_observations est append-only');
END;

CREATE TRIGGER sources_no_update
BEFORE UPDATE ON sources
BEGIN
    SELECT RAISE(ABORT, 'sources est append-only');
END;

CREATE TRIGGER sources_no_delete
BEFORE DELETE ON sources
BEGIN
    SELECT RAISE(ABORT, 'sources est append-only');
END;

CREATE TRIGGER source_observations_no_update
BEFORE UPDATE ON source_observations
BEGIN
    SELECT RAISE(ABORT, 'source_observations est append-only');
END;

CREATE TRIGGER source_observations_no_delete
BEFORE DELETE ON source_observations
BEGIN
    SELECT RAISE(ABORT, 'source_observations est append-only');
END;

CREATE TRIGGER match_decisions_no_update
BEFORE UPDATE ON match_decisions
BEGIN
    SELECT RAISE(ABORT, 'match_decisions est append-only');
END;

CREATE TRIGGER match_decisions_no_delete
BEFORE DELETE ON match_decisions
BEGIN
    SELECT RAISE(ABORT, 'match_decisions est append-only');
END;

CREATE TRIGGER file_verifications_no_update
BEFORE UPDATE ON file_verifications
BEGIN
    SELECT RAISE(ABORT, 'file_verifications est append-only');
END;

CREATE TRIGGER file_verifications_no_delete
BEFORE DELETE ON file_verifications
BEGIN
    SELECT RAISE(ABORT, 'file_verifications est append-only');
END;
