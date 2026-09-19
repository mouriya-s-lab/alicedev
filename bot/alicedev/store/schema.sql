-- alicedev DuckDB schema. The plugin process is the single writer; all changes
-- go through Store's asyncio.Lock. schema_version gates future migrations.

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    migration   TEXT PRIMARY KEY,
    applied_at  TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
);

CREATE TABLE IF NOT EXISTS sessions (
    session_ref       TEXT PRIMARY KEY,
    chat_key          TEXT NOT NULL,
    template          TEXT NOT NULL,
    name              TEXT NOT NULL,
    provider          TEXT,
    model             TEXT,
    thinking          TEXT,
    agent_id          TEXT,
    workspace_id      TEXT,
    server_id         TEXT,
    status            TEXT NOT NULL,
    created_by        TEXT NOT NULL,
    created_at        TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),
    last_activity_at  TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
);

CREATE TABLE IF NOT EXISTS chat_current_sessions (
    chat_key             TEXT PRIMARY KEY,
    current_session_ref  TEXT NOT NULL,
    updated_at           TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
);

CREATE TABLE IF NOT EXISTS messages (
    msg_ref              TEXT PRIMARY KEY,
    session_ref          TEXT NOT NULL,
    chat_key             TEXT NOT NULL,
    platform_message_id  TEXT,
    sender_key           TEXT,
    text                 TEXT NOT NULL,
    created_at           TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),
    UNIQUE (chat_key, platform_message_id)
);

CREATE TABLE IF NOT EXISTS reply_deliveries (
    reply_id              TEXT PRIMARY KEY,
    session_ref           TEXT NOT NULL,
    msgs                  JSON NOT NULL,
    payload_sha256        TEXT NOT NULL,
    state                 TEXT NOT NULL,          -- claimed | sent | failed
    platform_message_ids  JSON,
    created_at            TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),
    updated_at            TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
);

CREATE TABLE IF NOT EXISTS outbound (
    platform_message_id  TEXT PRIMARY KEY,
    chat_key             TEXT NOT NULL,
    session_ref          TEXT NOT NULL,
    created_at           TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
);

CREATE TABLE IF NOT EXISTS requirements (
    id           BIGINT PRIMARY KEY,
    chat_key     TEXT NOT NULL,
    session_ref  TEXT,
    author_key   TEXT NOT NULL,
    author_name  TEXT NOT NULL,
    text         TEXT NOT NULL,
    images       JSON,
    status       TEXT NOT NULL DEFAULT 'open',
    created_at   TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
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
    session_ref     TEXT,
    source_path     TEXT NOT NULL,
    published_path  TEXT NOT NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
);

CREATE TABLE IF NOT EXISTS tokens_issued (
    token_id     TEXT PRIMARY KEY,
    session_ref  TEXT,
    user_key     TEXT NOT NULL,
    issued_by    TEXT NOT NULL,
    target       TEXT NOT NULL,
    issued_at    TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),
    expires_at   TIMESTAMP
);

-- Monotonic id allocator for tables with BIGINT surrogate keys (requirements,
-- favorites). DuckDB sequences are used through nextval() in the repositories.
CREATE SEQUENCE IF NOT EXISTS seq_requirements START 1;
CREATE SEQUENCE IF NOT EXISTS seq_favorites START 1;
