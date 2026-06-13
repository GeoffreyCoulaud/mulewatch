-- local.db — migration 0001 : état opérationnel (spec data-model §5 ; spec MVP §12).
-- JAMAIS fusionné : seul catalog.db traverse la frontière du nœud (invariant §11).
-- L'index UNIQUE partiel rend l'enqueue idempotent (au plus UNE tâche active par hash).

CREATE TABLE node_runtime (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE verification_tasks (
    id INTEGER PRIMARY KEY,
    ed2k_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'in_progress', 'done', 'dead_letter')),
    attempts INTEGER NOT NULL DEFAULT 0,
    enqueued_at TEXT NOT NULL,
    claimed_at TEXT,
    lease_until TEXT
);

CREATE UNIQUE INDEX idx_verification_tasks_active_hash
ON verification_tasks (ed2k_hash)
WHERE status IN ('pending', 'in_progress');

-- Le scan du claim (plus ancienne tâche pending) doit coûter selon la profondeur
-- ACTIVE de la file, pas selon l'historique all-time.
CREATE INDEX idx_verification_tasks_pending
ON verification_tasks (enqueued_at)
WHERE status = 'pending';

CREATE TABLE downloads (
    ed2k_hash TEXT PRIMARY KEY,
    target_id TEXT NOT NULL,
    state TEXT NOT NULL,
    queued_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE scheduler_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
