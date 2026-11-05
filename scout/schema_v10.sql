BEGIN IMMEDIATE;
CREATE TABLE platform_workflow_runs(run_id TEXT NOT NULL,run_attempt INTEGER NOT NULL,observed_at TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(run_id,run_attempt));
CREATE TABLE platform_observations(id INTEGER PRIMARY KEY,observed_at TEXT NOT NULL,success INTEGER NOT NULL,payload TEXT NOT NULL);
INSERT INTO schema_version(version) VALUES(10);
COMMIT;
