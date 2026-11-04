BEGIN IMMEDIATE;
CREATE TABLE source_probes (
 id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sources(id), checked_at TEXT NOT NULL,
 status TEXT NOT NULL, response_status INTEGER, final_url TEXT, provider_hint TEXT, evidence TEXT, error TEXT
);
CREATE INDEX probes_by_source ON source_probes(source_id,id);
CREATE TABLE job_verifications (
 id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id), source_id INTEGER NOT NULL REFERENCES sources(id),
 checked_at TEXT NOT NULL, outcome TEXT NOT NULL, evidence TEXT NOT NULL,
 UNIQUE(job_id,source_id,checked_at)
);
CREATE TABLE coverage_audits (
 id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, completed_at TEXT,
 baseline_active_legacy_jobs TEXT NOT NULL, report TEXT
);
INSERT INTO schema_version VALUES(3);
COMMIT;
