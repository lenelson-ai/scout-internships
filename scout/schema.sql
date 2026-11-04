PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_version VALUES (1);
CREATE TABLE IF NOT EXISTS companies (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, normalized_name TEXT NOT NULL UNIQUE,
 domain TEXT, careers_url TEXT, industry TEXT, company_type TEXT, company_size TEXT,
 fortune_rank INTEGER, active INTEGER NOT NULL DEFAULT 1,
 last_checked_at TEXT, last_successful_check_at TEXT, last_job_seen_at TEXT,
 consecutive_failures INTEGER NOT NULL DEFAULT 0, health_status TEXT NOT NULL DEFAULT 'UNCONFIGURED',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
 ats_type TEXT NOT NULL, ats_identifier TEXT NOT NULL DEFAULT '', careers_url TEXT,
 active INTEGER NOT NULL DEFAULT 1, interval_seconds INTEGER NOT NULL DEFAULT 21600 CHECK(interval_seconds >= 60),
 last_checked_at TEXT, last_successful_check_at TEXT, last_job_seen_at TEXT,
 consecutive_failures INTEGER NOT NULL DEFAULT 0, health_status TEXT NOT NULL DEFAULT 'NEVER_CHECKED',
 jobs_returned INTEGER, last_response_status INTEGER, last_error TEXT,
 UNIQUE(company_id, ats_type, ats_identifier)
);
CREATE TABLE IF NOT EXISTS jobs (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
 external_job_id TEXT, title TEXT NOT NULL, normalized_title TEXT NOT NULL,
 description TEXT NOT NULL DEFAULT '', locations TEXT NOT NULL, normalized_locations TEXT NOT NULL,
 remote_status TEXT, employment_type TEXT, season TEXT, graduation_year_eligibility TEXT,
 function TEXT NOT NULL, sub_function TEXT, industry TEXT,
 source_type TEXT, source_url TEXT, canonical_apply_url TEXT,
 posted_at TEXT, posted_at_confidence TEXT NOT NULL, first_seen_at TEXT NOT NULL,
 last_seen_at TEXT NOT NULL, closed_at TEXT, deadline TEXT, active INTEGER NOT NULL DEFAULT 1,
 classification_confidence REAL NOT NULL, internship_confidence REAL NOT NULL,
 geography_status TEXT NOT NULL, needs_review INTEGER NOT NULL, classification_reasons TEXT NOT NULL,
 repost_of INTEGER REFERENCES jobs(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_freshness ON jobs(active, posted_at, first_seen_at);
CREATE INDEX IF NOT EXISTS jobs_identity ON jobs(company_id, external_job_id);
CREATE INDEX IF NOT EXISTS jobs_url ON jobs(company_id, canonical_apply_url);
CREATE INDEX IF NOT EXISTS jobs_fallback ON jobs(company_id, normalized_title, normalized_locations);
CREATE TABLE IF NOT EXISTS source_jobs (
 source_id INTEGER NOT NULL REFERENCES sources(id), identity TEXT NOT NULL,
 job_id INTEGER NOT NULL REFERENCES jobs(id), external_job_id TEXT, source_url TEXT,
 canonical_apply_url TEXT, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
 present INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(source_id, identity)
);
CREATE TABLE IF NOT EXISTS crawl_runs (
 id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sources(id), started_at TEXT NOT NULL,
 finished_at TEXT NOT NULL, status TEXT NOT NULL, authoritative INTEGER NOT NULL,
 jobs_returned INTEGER, matching_jobs INTEGER, response_status INTEGER, error TEXT
);
CREATE TABLE IF NOT EXISTS legacy_records (
 record_id TEXT PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id), payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS company_imports (
 company_id INTEGER NOT NULL REFERENCES companies(id), origin TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(company_id, origin)
);
