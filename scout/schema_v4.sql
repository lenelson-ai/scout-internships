BEGIN IMMEDIATE;
CREATE TABLE job_merges (
 duplicate_job_id INTEGER PRIMARY KEY REFERENCES jobs(id),
 canonical_job_id INTEGER NOT NULL REFERENCES jobs(id),
 merged_at TEXT NOT NULL, reason TEXT NOT NULL,
 CHECK(duplicate_job_id <> canonical_job_id)
);
INSERT INTO schema_version VALUES(4);
COMMIT;
