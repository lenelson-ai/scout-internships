BEGIN IMMEDIATE;
ALTER TABLE companies ADD COLUMN canonical_company_id INTEGER REFERENCES companies(id);
ALTER TABLE companies ADD COLUMN coverage_tier INTEGER NOT NULL DEFAULT 3;
ALTER TABLE companies ADD COLUMN coverage_score REAL NOT NULL DEFAULT 0;
ALTER TABLE companies ADD COLUMN coverage_reason TEXT NOT NULL DEFAULT '[]';
ALTER TABLE companies ADD COLUMN must_cover INTEGER NOT NULL DEFAULT 0;
ALTER TABLE companies ADD COLUMN target_functions TEXT NOT NULL DEFAULT '[]';
ALTER TABLE companies ADD COLUMN employer_category TEXT NOT NULL DEFAULT 'UNKNOWN';
ALTER TABLE companies ADD COLUMN last_priority_review_at TEXT;
CREATE TABLE employer_datasets (
 id TEXT PRIMARY KEY, source_name TEXT NOT NULL, source_type TEXT NOT NULL, source_url TEXT,
 source_category TEXT NOT NULL, independent_family TEXT NOT NULL, status TEXT NOT NULL, metadata TEXT NOT NULL
);
CREATE TABLE employer_dataset_snapshots (
 id INTEGER PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES employer_datasets(id),
 sha256 TEXT NOT NULL, path TEXT NOT NULL, fetched_at TEXT NOT NULL, parser_version TEXT NOT NULL,
 UNIQUE(dataset_id,sha256)
);
CREATE TABLE raw_organizations (
 id INTEGER PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES employer_datasets(id), external_id TEXT NOT NULL,
 name TEXT NOT NULL, normalized_name TEXT NOT NULL, domain TEXT, parent_name TEXT, metadata TEXT NOT NULL,
 company_id INTEGER REFERENCES companies(id), resolution_status TEXT NOT NULL,
 first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
 last_snapshot_id INTEGER REFERENCES employer_dataset_snapshots(id), active INTEGER NOT NULL DEFAULT 1,
 UNIQUE(dataset_id,external_id)
);
CREATE TABLE company_aliases (
 company_id INTEGER NOT NULL REFERENCES companies(id), alias TEXT NOT NULL, normalized_alias TEXT NOT NULL,
 reason TEXT NOT NULL, evidence_url TEXT, PRIMARY KEY(company_id,normalized_alias)
);
CREATE INDEX alias_names ON company_aliases(normalized_alias);
CREATE TABLE company_domains (
 company_id INTEGER NOT NULL REFERENCES companies(id), domain TEXT NOT NULL, evidence TEXT NOT NULL,
 PRIMARY KEY(company_id,domain)
);
CREATE TABLE company_entities (
 raw_organization_id INTEGER PRIMARY KEY REFERENCES raw_organizations(id),
 company_id INTEGER NOT NULL REFERENCES companies(id), entity_type TEXT NOT NULL,
 parent_organization TEXT, recruiting_brand TEXT, resolution_reason TEXT NOT NULL
);
CREATE TABLE company_sources (
 company_id INTEGER NOT NULL REFERENCES companies(id), raw_organization_id INTEGER NOT NULL REFERENCES raw_organizations(id),
 discovered_at TEXT NOT NULL, source_metadata TEXT NOT NULL,
 PRIMARY KEY(company_id,raw_organization_id)
);
CREATE TABLE company_merges (
 duplicate_company_id INTEGER PRIMARY KEY REFERENCES companies(id), canonical_company_id INTEGER NOT NULL REFERENCES companies(id),
 reason TEXT NOT NULL, evidence TEXT NOT NULL, merged_at TEXT NOT NULL
);
CREATE TABLE entity_resolution_cases (
 id INTEGER PRIMARY KEY, raw_organization_id INTEGER REFERENCES raw_organizations(id),
 case_key TEXT UNIQUE NOT NULL, candidate_company_ids TEXT NOT NULL, reason TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'OPEN', created_at TEXT NOT NULL, resolution TEXT
);
CREATE TABLE recruiting_evidence (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), evidence_type TEXT NOT NULL,
 evidence_key TEXT NOT NULL, source_url TEXT, observed_at TEXT NOT NULL, metadata TEXT NOT NULL,
 UNIQUE(company_id,evidence_type,evidence_key)
);
CREATE TABLE coverage_overrides (
 company_id INTEGER PRIMARY KEY REFERENCES companies(id), coverage_tier INTEGER CHECK(coverage_tier IN (1,2,3)),
 must_cover INTEGER, reason TEXT NOT NULL, evidence_url TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE coverage_override_history (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), coverage_tier INTEGER,
 must_cover INTEGER, reason TEXT NOT NULL, evidence_url TEXT, changed_at TEXT NOT NULL
);
CREATE TABLE career_mapping_attempts (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), attempted_at TEXT NOT NULL,
 input_url TEXT, status TEXT NOT NULL, careers_url TEXT, provider TEXT, evidence TEXT, error TEXT
);
CREATE TABLE source_equivalences (
 source_id INTEGER PRIMARY KEY REFERENCES sources(id), canonical_source_id INTEGER NOT NULL REFERENCES sources(id),
 reason TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE universe_manifests (
 id INTEGER PRIMARY KEY, generated_at TEXT NOT NULL, policy_sha256 TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,
 payload TEXT NOT NULL, UNIQUE(manifest_sha256)
);
CREATE TABLE universe_manifest_alerts (
 id INTEGER PRIMARY KEY, manifest_id INTEGER NOT NULL REFERENCES universe_manifests(id), company_id INTEGER NOT NULL REFERENCES companies(id),
 reason TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE source_inventory (
 source_id INTEGER NOT NULL REFERENCES sources(id), identity TEXT NOT NULL, company_id INTEGER NOT NULL REFERENCES companies(id),
 external_job_id TEXT, canonical_url TEXT, normalized_payload TEXT NOT NULL,
 first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, present INTEGER NOT NULL DEFAULT 1,
 classification TEXT NOT NULL, job_id INTEGER REFERENCES jobs(id),
 PRIMARY KEY(source_id,identity)
);
CREATE INDEX inventory_employer ON source_inventory(company_id,external_job_id);
CREATE TABLE benchmark_jobs (
 id INTEGER PRIMARY KEY, benchmark_key TEXT UNIQUE NOT NULL, employer TEXT NOT NULL, title TEXT NOT NULL, location TEXT,
 canonical_url TEXT, external_job_id TEXT, posted_at TEXT, benchmark_source TEXT NOT NULL, source_url TEXT,
 verified_at TEXT NOT NULL, expected_category TEXT NOT NULL, metadata TEXT NOT NULL,
 company_id INTEGER REFERENCES companies(id), scout_job_id INTEGER REFERENCES jobs(id), miss_reason TEXT,
 scout_first_seen_at TEXT, evaluated_at TEXT
);
INSERT INTO schema_version VALUES(5);
COMMIT;
