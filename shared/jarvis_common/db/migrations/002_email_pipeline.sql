CREATE TABLE IF NOT EXISTS email_messages (
    id UUID PRIMARY KEY,
    account_key TEXT NOT NULL,
    gmail_id TEXT NOT NULL,
    thread_id TEXT,
    from_addr TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    received_at TIMESTAMPTZ,
    labels TEXT[] NOT NULL DEFAULT '{}',
    body_snippet TEXT NOT NULL DEFAULT '',
    is_school BOOLEAN NOT NULL DEFAULT FALSE,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (account_key, gmail_id)
);

CREATE INDEX IF NOT EXISTS idx_email_messages_received_at ON email_messages (received_at DESC);
CREATE INDEX IF NOT EXISTS idx_email_messages_is_school ON email_messages (is_school);

CREATE TABLE IF NOT EXISTS email_summaries (
    id UUID PRIMARY KEY,
    period_start TIMESTAMPTZ NOT NULL,
    period_end TIMESTAMPTZ NOT NULL,
    cadence TEXT NOT NULL,
    summary_text TEXT NOT NULL,
    message_ids UUID[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    retention_until TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_email_summaries_created_at ON email_summaries (created_at DESC);

CREATE TABLE IF NOT EXISTS extracted_events (
    id UUID PRIMARY KEY,
    message_id UUID REFERENCES email_messages(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    start_time TIMESTAMPTZ,
    end_time TIMESTAMPTZ,
    location TEXT,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
    approval_id UUID,
    calendar_scope TEXT NOT NULL DEFAULT 'family_shared',
    raw_excerpt TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_at TIMESTAMPTZ,
    review_actor_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_extracted_events_status ON extracted_events (status);
