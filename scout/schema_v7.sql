BEGIN IMMEDIATE;
CREATE TABLE recruiting_scope_reviews(company_id INTEGER PRIMARY KEY REFERENCES companies(id),state TEXT NOT NULL CHECK(state IN ('VALIDATED','UNRESOLVED')),required_source_ids TEXT NOT NULL,evidence TEXT NOT NULL,reviewed_at TEXT NOT NULL);
CREATE TABLE recruiting_scope_history(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),state TEXT NOT NULL,required_source_ids TEXT NOT NULL,evidence TEXT NOT NULL,reviewed_at TEXT NOT NULL);
INSERT INTO schema_version VALUES(7);
COMMIT;
