"""Versioned SQLite schema migrations."""

SCHEMA_VERSION = 7

MIGRATION_1 = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    source_session_id TEXT NOT NULL,
    owner_key TEXT NOT NULL,
    scope TEXT NOT NULL,
    subject TEXT NOT NULL,
    target_context_id TEXT,
    item_id TEXT,
    created_at TEXT NOT NULL,
    event_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_events_session_created
    ON events (source_session_id, created_at, id);
CREATE INDEX IF NOT EXISTS ix_events_owner_subject
    ON events (owner_key, scope, subject, created_at);

CREATE TABLE IF NOT EXISTS context_items (
    id TEXT PRIMARY KEY,
    source_event_id TEXT NOT NULL UNIQUE,
    owner_key TEXT NOT NULL,
    scope TEXT NOT NULL,
    kind TEXT NOT NULL,
    subject TEXT,
    status TEXT NOT NULL,
    supersedes TEXT,
    created_at TEXT NOT NULL,
    item_json TEXT NOT NULL,
    FOREIGN KEY (source_event_id) REFERENCES events(id),
    FOREIGN KEY (supersedes) REFERENCES context_items(id)
);

CREATE INDEX IF NOT EXISTS ix_context_items_owner_status
    ON context_items (owner_key, status, created_at, id);
CREATE INDEX IF NOT EXISTS ix_context_items_subject
    ON context_items (owner_key, scope, subject, status);

CREATE TABLE IF NOT EXISTS state_assertions (
    context_item_id TEXT PRIMARY KEY,
    owner_key TEXT NOT NULL,
    scope TEXT NOT NULL,
    subject TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (context_item_id) REFERENCES context_items(id) ON DELETE CASCADE,
    UNIQUE (owner_key, scope, subject)
);
"""

MIGRATION_2 = """
CREATE TABLE IF NOT EXISTS compilation_traces (
    id TEXT PRIMARY KEY,
    source_session_id TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    trace_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_compilation_traces_session_created
    ON compilation_traces (source_session_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_compilation_traces_fingerprint
    ON compilation_traces (request_fingerprint);
"""

MIGRATION_3 = """
CREATE TABLE IF NOT EXISTS conversation_entries (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    source_session_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    source_trace_id TEXT NOT NULL,
    searchable_text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    entry_json TEXT NOT NULL,
    UNIQUE (source_session_id, content_hash)
);

CREATE INDEX IF NOT EXISTS ix_conversation_entries_session_created
    ON conversation_entries (source_session_id, created_at, id);

CREATE TABLE IF NOT EXISTS conversation_entry_owners (
    entry_id TEXT NOT NULL,
    owner_key TEXT NOT NULL,
    PRIMARY KEY (entry_id, owner_key),
    FOREIGN KEY (entry_id) REFERENCES conversation_entries(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_conversation_entry_owners_key
    ON conversation_entry_owners (owner_key, entry_id);

CREATE VIRTUAL TABLE IF NOT EXISTS conversation_entries_fts USING fts5(
    searchable_text,
    content='conversation_entries',
    content_rowid='rowid',
    tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS conversation_entries_ai AFTER INSERT ON conversation_entries BEGIN
    INSERT INTO conversation_entries_fts(rowid, searchable_text)
    VALUES (new.rowid, new.searchable_text);
END;

CREATE TRIGGER IF NOT EXISTS conversation_entries_ad AFTER DELETE ON conversation_entries BEGIN
    INSERT INTO conversation_entries_fts(conversation_entries_fts, rowid, searchable_text)
    VALUES ('delete', old.rowid, old.searchable_text);
END;

CREATE TRIGGER IF NOT EXISTS conversation_entries_au AFTER UPDATE ON conversation_entries BEGIN
    INSERT INTO conversation_entries_fts(conversation_entries_fts, rowid, searchable_text)
    VALUES ('delete', old.rowid, old.searchable_text);
    INSERT INTO conversation_entries_fts(rowid, searchable_text)
    VALUES (new.rowid, new.searchable_text);
END;
"""

MIGRATION_4 = """
DROP TRIGGER IF EXISTS conversation_entries_ai;
DROP TRIGGER IF EXISTS conversation_entries_ad;
DROP TRIGGER IF EXISTS conversation_entries_au;
DROP TABLE IF EXISTS conversation_entries_fts;
DROP INDEX IF EXISTS ix_conversation_entries_session_created;
DROP INDEX IF EXISTS ix_conversation_entry_owners_key;

ALTER TABLE conversation_entry_owners RENAME TO conversation_entry_owners_legacy;
ALTER TABLE conversation_entries RENAME TO conversation_entries_legacy;

CREATE TABLE conversation_entries (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    source_session_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    position INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    source_trace_id TEXT NOT NULL,
    searchable_text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    entry_json TEXT NOT NULL,
    UNIQUE (source_session_id, request_id, direction, position)
);

CREATE INDEX ix_conversation_entries_session_created
    ON conversation_entries (source_session_id, created_at, id);
CREATE INDEX ix_conversation_entries_request
    ON conversation_entries (source_session_id, request_id, direction, position);

CREATE TABLE conversation_entry_owners (
    entry_id TEXT NOT NULL,
    owner_key TEXT NOT NULL,
    PRIMARY KEY (entry_id, owner_key),
    FOREIGN KEY (entry_id) REFERENCES conversation_entries(id) ON DELETE CASCADE
);

CREATE INDEX ix_conversation_entry_owners_key
    ON conversation_entry_owners (owner_key, entry_id);

INSERT INTO conversation_entries (
    id, source_session_id, request_id, direction, position, content_hash,
    source_trace_id, searchable_text, created_at, entry_json
)
SELECT
    id, source_session_id, source_trace_id, 'input', rowid, content_hash,
    source_trace_id, searchable_text, created_at, entry_json
FROM conversation_entries_legacy;

INSERT INTO conversation_entry_owners (entry_id, owner_key)
SELECT entry_id, owner_key FROM conversation_entry_owners_legacy;

DROP TABLE conversation_entry_owners_legacy;
DROP TABLE conversation_entries_legacy;

CREATE VIRTUAL TABLE conversation_entries_fts USING fts5(
    searchable_text,
    content='conversation_entries',
    content_rowid='rowid',
    tokenize='trigram'
);

CREATE TRIGGER conversation_entries_ai AFTER INSERT ON conversation_entries BEGIN
    INSERT INTO conversation_entries_fts(rowid, searchable_text)
    VALUES (new.rowid, new.searchable_text);
END;

CREATE TRIGGER conversation_entries_ad AFTER DELETE ON conversation_entries BEGIN
    INSERT INTO conversation_entries_fts(conversation_entries_fts, rowid, searchable_text)
    VALUES ('delete', old.rowid, old.searchable_text);
END;

CREATE TRIGGER conversation_entries_au AFTER UPDATE ON conversation_entries BEGIN
    INSERT INTO conversation_entries_fts(conversation_entries_fts, rowid, searchable_text)
    VALUES ('delete', old.rowid, old.searchable_text);
    INSERT INTO conversation_entries_fts(rowid, searchable_text)
    VALUES (new.rowid, new.searchable_text);
END;

INSERT INTO conversation_entries_fts(conversation_entries_fts) VALUES ('rebuild');
"""

MIGRATION_5 = """
CREATE TABLE IF NOT EXISTS turn_observations (
    owner_key TEXT NOT NULL,
    source_session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    PRIMARY KEY (owner_key, source_session_id, turn_id)
);

CREATE INDEX IF NOT EXISTS ix_turn_observations_owner
    ON turn_observations (owner_key, observed_at);

CREATE TABLE IF NOT EXISTS context_turn_leases (
    context_item_id TEXT PRIMARY KEY,
    owner_key TEXT NOT NULL,
    ttl_turns INTEGER NOT NULL CHECK (ttl_turns >= 1),
    consumed_turns INTEGER NOT NULL DEFAULT 0 CHECK (consumed_turns >= 0),
    FOREIGN KEY (context_item_id) REFERENCES context_items(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_context_turn_leases_owner
    ON context_turn_leases (owner_key, consumed_turns);

INSERT OR IGNORE INTO context_turn_leases (
    context_item_id, owner_key, ttl_turns, consumed_turns
)
SELECT
    id, owner_key, CAST(json_extract(item_json, '$.ttl_turns') AS INTEGER), 0
FROM context_items
WHERE json_extract(item_json, '$.ttl_turns') IS NOT NULL;
"""

MIGRATION_6 = """
CREATE TABLE IF NOT EXISTS mutation_requests (
    source_session_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    event_json TEXT NOT NULL,
    item_json TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (source_session_id, request_id, operation)
);

CREATE INDEX IF NOT EXISTS ix_mutation_requests_created
    ON mutation_requests (created_at);
"""

MIGRATION_7 = """
CREATE TABLE IF NOT EXISTS execution_traces (
    trace_id TEXT PRIMARY KEY,
    source_session_id TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    execution_json TEXT NOT NULL,
    FOREIGN KEY (trace_id) REFERENCES compilation_traces(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_execution_traces_session_completed
    ON execution_traces (source_session_id, completed_at DESC, trace_id DESC);
"""
