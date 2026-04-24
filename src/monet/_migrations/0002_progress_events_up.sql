-- Phase 3: typed progress events table for split-plane data plane.
CREATE TABLE IF NOT EXISTS typed_progress_events (
    event_id     BIGSERIAL    PRIMARY KEY,
    run_id       TEXT         NOT NULL,
    task_id      TEXT         NOT NULL,
    agent_id     TEXT         NOT NULL,
    event_type   TEXT         NOT NULL,
    payload      JSONB,
    trace_id     TEXT,
    timestamp_ms BIGINT       NOT NULL,
    created_at   TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_tpe_run_event ON typed_progress_events (run_id, event_id);
CREATE INDEX IF NOT EXISTS ix_tpe_run_type  ON typed_progress_events (run_id, event_type);
