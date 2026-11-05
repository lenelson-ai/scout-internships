from pathlib import Path
import sqlite3
from .model import now, normalize

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data/scout.sqlite3"


def connect(path=DEFAULT_DB):
    db = sqlite3.connect(str(path), timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    version_exists = db.execute("SELECT name FROM sqlite_master WHERE name='schema_version'").fetchone()
    if version_exists and db.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] not in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10):
        raise RuntimeError("Unsupported database schema version")
    db.executescript(Path(__file__).with_name("schema.sql").read_text())
    if db.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] < 2:
        db.executescript(Path(__file__).with_name("schema_v2.sql").read_text())
    if db.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] < 3:
        db.executescript(Path(__file__).with_name("schema_v3.sql").read_text())
    if db.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] < 4:
        db.executescript(Path(__file__).with_name("schema_v4.sql").read_text())
    if db.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] < 5:
        db.executescript(Path(__file__).with_name("schema_v5.sql").read_text())
    if db.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] < 6:
        db.executescript(Path(__file__).with_name("schema_v6.sql").read_text())
    if db.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] < 7:
        db.executescript(Path(__file__).with_name("schema_v7.sql").read_text())
    if db.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] < 8:
        db.executescript(Path(__file__).with_name("schema_v8.sql").read_text())
    if db.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] < 9:
        db.executescript(Path(__file__).with_name("schema_v9.sql").read_text())
    if db.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] < 10:
        db.executescript(Path(__file__).with_name("schema_v10.sql").read_text())
    return db


def company(db, name, **fields):
    name = name.strip()
    if not name:
        raise ValueError("Company name required")
    stamp = now()
    db.execute("INSERT OR IGNORE INTO companies(name,normalized_name,created_at,updated_at) VALUES(?,?,?,?)",
               (name, normalize(name), stamp, stamp))
    cid = db.execute("SELECT id FROM companies WHERE normalized_name=?", (normalize(name),)).fetchone()[0]
    cid = canonical_id(db, cid)
    allowed = {"domain", "careers_url", "industry", "company_type", "company_size", "fortune_rank"}
    for key, value in fields.items():
        if key in allowed and value not in (None, ""):
            db.execute(f"UPDATE companies SET {key}=?,updated_at=? WHERE id=?", (value, stamp, cid))
    return cid


def canonical_id(db, cid):
    seen=set()
    while cid not in seen:
        seen.add(cid)
        row=db.execute('SELECT canonical_company_id FROM companies WHERE id=?',(cid,)).fetchone()
        if not row or not row[0]:
            return cid
        cid=row[0]
    raise ValueError('Canonical employer cycle')


def source(db, cid, ats_type, identifier="", careers_url=None, interval_seconds=21600):
    db.execute("""INSERT INTO sources(company_id,ats_type,ats_identifier,careers_url,interval_seconds)
                  VALUES(?,?,?,?,?) ON CONFLICT(company_id,ats_type,ats_identifier) DO UPDATE SET
                  careers_url=COALESCE(excluded.careers_url,sources.careers_url),interval_seconds=excluded.interval_seconds""",
               (cid, ats_type.lower(), identifier, careers_url, int(interval_seconds)))
    return db.execute("SELECT id FROM sources WHERE company_id=? AND ats_type=? AND ats_identifier=?",
                      (cid, ats_type.lower(), identifier)).fetchone()[0]
