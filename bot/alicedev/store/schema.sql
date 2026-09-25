-- alicedev DuckDB schema v3 (ARCHITECTURE §10). The plugin process is the
-- single writer; all changes go through Store's asyncio.Lock. A v2 database is
-- migrated once by Store.open() (legacy tables are renamed to v2_* first).

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    migration   TEXT PRIMARY KEY,
    applied_at  TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
);

CREATE SEQUENCE IF NOT EXISTS seq_sessions START 1;
CREATE SEQUENCE IF NOT EXISTS seq_favorites START 1;
CREATE SEQUENCE IF NOT EXISTS seq_outbox START 1;

-- User-visible sessions; `no` is the per-chat number shown as %n.
CREATE TABLE IF NOT EXISTS sessions (
    session_id     BIGINT PRIMARY KEY,
    chat_key       TEXT NOT NULL,
    no             INTEGER NOT NULL,
    scenario       TEXT NOT NULL,
    name           TEXT NOT NULL,
    created_by     TEXT NOT NULL,
    input          JSON NOT NULL,
    state          TEXT NOT NULL,
    workspace_id   TEXT,
    worktree_path  TEXT,
    base_sha       TEXT,
    data           JSON NOT NULL,
    created_at     TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),
    updated_at     TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),
    assigned_by    BIGINT,
    UNIQUE (chat_key, no)
);

CREATE TABLE IF NOT EXISTS chat_current_sessions (
    chat_key    TEXT PRIMARY KEY,
    session_id  BIGINT NOT NULL,
    updated_at  TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
);

-- One paseo agent per (session, agent state) entry. legacy_ref keeps the v2
-- `s_…` label of migrated agents resolvable for their replies.
CREATE TABLE IF NOT EXISTS agents (
    agent_ref         TEXT PRIMARY KEY,
    session_id        BIGINT NOT NULL,
    state             TEXT NOT NULL,
    provider          TEXT NOT NULL,
    agent_id          TEXT,
    workspace_id      TEXT,
    server_id         TEXT,
    status            TEXT NOT NULL,
    legacy_ref        TEXT UNIQUE,
    created_at        TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),
    last_activity_at  TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
);

-- Inbound messages injected into agents. agent_ref is NULL while the session
-- that a message opened has no agent yet (dedupe key for platform redelivery).
CREATE TABLE IF NOT EXISTS messages (
    msg_ref              TEXT PRIMARY KEY,
    agent_ref            TEXT,
    chat_key             TEXT NOT NULL,
    platform_message_id  TEXT,
    sender_key           TEXT,
    text                 TEXT NOT NULL,
    session_id           BIGINT,
    created_at           TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),
    sender_name          TEXT,
    content              TEXT,
    UNIQUE (chat_key, platform_message_id)
);

-- Outbound queue (§6). state: queued | sent | failed. A failed row with
-- next_attempt_at IS NULL is dead (out of retries).
CREATE TABLE IF NOT EXISTS outbox (
    reply_id              TEXT PRIMARY KEY,
    seq                   BIGINT NOT NULL,
    chat_key              TEXT NOT NULL,
    session_id            BIGINT,
    agent_ref             TEXT,
    msgs                  JSON NOT NULL,
    payload               JSON NOT NULL,
    payload_sha256        TEXT NOT NULL,
    state                 TEXT NOT NULL,
    attempts              INTEGER NOT NULL DEFAULT 0,
    next_attempt_at       TIMESTAMP,
    last_error            TEXT,
    platform_message_ids  JSON,
    created_at            TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),
    updated_at            TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
);

CREATE TABLE IF NOT EXISTS outbound (
    platform_message_id  TEXT PRIMARY KEY,
    chat_key             TEXT NOT NULL,
    session_id           BIGINT,
    created_at           TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
);

CREATE TABLE IF NOT EXISTS favorites (
    id                   BIGINT PRIMARY KEY,
    chat_key             TEXT NOT NULL,
    saver_key            TEXT NOT NULL,
    saver_name           TEXT NOT NULL,
    author_key           TEXT NOT NULL,
    author_name          TEXT NOT NULL,
    text                 TEXT NOT NULL,
    images               JSON,
    platform_message_id  TEXT,
    created_at           TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
);

CREATE TABLE IF NOT EXISTS reports (
    report_id       TEXT PRIMARY KEY,
    session_id      BIGINT,
    source_path     TEXT NOT NULL,
    published_path  TEXT NOT NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
);

CREATE TABLE IF NOT EXISTS tokens_issued (
    token_id     TEXT PRIMARY KEY,
    session_id   BIGINT,
    user_key     TEXT NOT NULL,
    issued_by    TEXT NOT NULL,
    target       TEXT NOT NULL,
    issued_at    TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),
    expires_at   TIMESTAMP
);
