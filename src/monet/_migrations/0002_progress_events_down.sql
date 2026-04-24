-- Phase 3 rollback: drop typed progress events table.
DROP INDEX IF EXISTS ix_tpe_run_type;
DROP INDEX IF EXISTS ix_tpe_run_event;
DROP TABLE IF EXISTS typed_progress_events;
