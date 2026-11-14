-- MindOS core schema (PostgreSQL 16 + pgvector)
-- Run via Alembic in app; this file is the architecture reference.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE integration_accounts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider        TEXT NOT NULL, -- google
    scopes          TEXT[] NOT NULL,
    tokens_enc      BYTEA NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active', -- active | needs_reauth
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE artifacts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source          TEXT NOT NULL, -- gmail | gcal | manual | file
    external_id     TEXT,
    title           TEXT,
    occurred_at     TIMESTAMPTZ,
    raw_path        TEXT,          -- vault relative path
    content_hash    TEXT NOT NULL,
    metadata        JSONB NOT NULL DEFAULT '{}',
    tsv             TSVECTOR,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, source, external_id)
);

CREATE INDEX artifacts_tsv_idx ON artifacts USING GIN (tsv);
CREATE INDEX artifacts_user_time_idx ON artifacts (user_id, occurred_at DESC);

CREATE TABLE artifact_chunks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id     UUID NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    ord             INT NOT NULL,
    text            TEXT NOT NULL,
    token_count     INT,
    chunker_version TEXT NOT NULL,
    UNIQUE (artifact_id, ord)
);

CREATE TABLE chunk_embeddings (
    chunk_id        UUID PRIMARY KEY REFERENCES artifact_chunks(id) ON DELETE CASCADE,
    embedding_model TEXT NOT NULL,
    embedding       vector(384),   -- bge-small 384-dim
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX chunk_embeddings_hnsw ON chunk_embeddings
    USING hnsw (embedding vector_cosine_ops);

CREATE TABLE entities (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL, -- person | project | topic | event
    canonical_name  TEXT NOT NULL,
    aliases         TEXT[] NOT NULL DEFAULT '{}',
    metadata        JSONB NOT NULL DEFAULT '{}',
    UNIQUE (user_id, kind, canonical_name)
);

CREATE TABLE edges (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    src_entity_id   UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    rel             TEXT NOT NULL, -- related_to | promised | attends | owns
    dst_entity_id   UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    evidence_chunk  UUID REFERENCES artifact_chunks(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE commitments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'open', -- open | done | cancelled
    actor           TEXT,
    counterparty    TEXT,
    due_at          TIMESTAMPTZ,
    summary         TEXT NOT NULL,
    confidence      REAL NOT NULL,
    extraction_mode TEXT NOT NULL, -- live | degraded | rules
    source_chunk_id UUID REFERENCES artifact_chunks(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX commitments_open_idx ON commitments (user_id, due_at)
    WHERE status = 'open';

CREATE TABLE tensions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tension_type    TEXT NOT NULL, -- time_drift | subject_mismatch | duplicate_commitment
    severity        TEXT NOT NULL DEFAULT 'medium',
    status          TEXT NOT NULL DEFAULT 'open', -- open | resolved | dismissed
    artifact_ids    UUID[] NOT NULL,
    description     TEXT NOT NULL,
    resolution      JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE memories (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL, -- fact | preference | decision
    content         TEXT NOT NULL,
    source          TEXT NOT NULL, -- user | agent_proposed
    approved        BOOLEAN NOT NULL DEFAULT false,
    evidence        JSONB NOT NULL DEFAULT '[]',
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE chat_sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title           TEXT NOT NULL DEFAULT 'New chat',
    thread_id       UUID NOT NULL DEFAULT gen_random_uuid(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX chat_sessions_user_updated_idx ON chat_sessions (user_id, updated_at DESC);

CREATE TABLE chat_messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role            TEXT NOT NULL, -- user | assistant | system
    content         TEXT NOT NULL,
    intent          TEXT, -- converse | plan | execute
    metadata        JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX chat_messages_session_created_idx ON chat_messages (session_id, created_at);

CREATE TABLE outbox_events (
    id              BIGSERIAL PRIMARY KEY,
    event_type      TEXT NOT NULL,
    payload         JSONB NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    attempts        INT NOT NULL DEFAULT 0,
    available_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX outbox_poll_idx ON outbox_events (status, available_at)
    WHERE status = 'pending';

CREATE TABLE llm_usage_events (
    id              BIGSERIAL PRIMARY KEY,
    trace_id        TEXT NOT NULL,
    provider        TEXT NOT NULL,
    model           TEXT NOT NULL,
    input_tokens    INT NOT NULL,
    output_tokens   INT NOT NULL,
    usd_estimate    NUMERIC(10, 6) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE llm_cache (
    cache_key       TEXT PRIMARY KEY,
    response_body   JSONB NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL
);

CREATE TABLE audit_log (
    id              BIGSERIAL PRIMARY KEY,
    user_id         UUID REFERENCES users(id),
    action          TEXT NOT NULL,
    resource_type   TEXT NOT NULL,
    resource_id     TEXT,
    detail          JSONB NOT NULL DEFAULT '{}',
    trace_id        TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
