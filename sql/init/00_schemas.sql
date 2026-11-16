-- Runs once, when the Postgres volume is created.
create schema if not exists silver;     -- loaded from the Spark Silver files
create schema if not exists analytics;  -- dbt Gold models
create schema if not exists ops;        -- pipeline and deployment metadata

create table if not exists ops.pipeline_runs (
    run_id              bigserial primary key,
    finished_at         timestamptz not null default now(),
    rows_read           integer,
    rows_silver         integer,
    rows_quarantined    integer,
    duplicates_removed  integer,
    quarantine_pct      numeric(6, 3),
    contract_version    text,
    errors_by_rule      jsonb
);

-- One row per Data Canary run (each Jenkins build that touches the pipeline).
create table if not exists ops.canary_runs (
    run_id         bigserial primary key,
    created_at     timestamptz not null default now(),
    build_id       text,
    git_sha        text,
    verdict        text check (verdict in ('PROMOTE', 'BLOCK')),
    score          numeric(5, 1),
    failed_checks  integer,
    report         jsonb
);
