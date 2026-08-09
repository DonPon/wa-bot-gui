import json
from datetime import datetime
from pathlib import Path

SEED_DIR = Path(__file__).resolve().parent

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  persona TEXT NOT NULL,
  time TEXT NOT NULL,
  days_mode TEXT NOT NULL DEFAULT 'fixed',
  weekdays TEXT NOT NULL DEFAULT '[]',
  random_per_week INTEGER NOT NULL DEFAULT 0,
  dry_run INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  week_rolls TEXT NOT NULL DEFAULT '{}',
  last_run_date TEXT,
  delay_enabled INTEGER NOT NULL DEFAULT 0,
  delay_min INTEGER NOT NULL DEFAULT 1,
  delay_max INTEGER NOT NULL DEFAULT 60,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL,
  trigger TEXT NOT NULL,
  scheduled_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  exit_code INTEGER,
  status TEXT NOT NULL DEFAULT 'running',
  output TEXT,
  command TEXT,
  send_at TEXT,
  delay_seconds INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  key TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  jid TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS muestras (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  persona TEXT NOT NULL,
  text TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_muestras_persona ON muestras(persona);
CREATE TABLE IF NOT EXISTS one_time (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  persona TEXT NOT NULL,
  text TEXT NOT NULL,
  send_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  dry_run INTEGER NOT NULL DEFAULT 0,
  delay_enabled INTEGER NOT NULL DEFAULT 0,
  delay_min INTEGER NOT NULL DEFAULT 1,
  delay_max INTEGER NOT NULL DEFAULT 60,
  result TEXT,
  sent_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_one_time_status ON one_time(status);
"""


def _migrate_jobs(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "delay_enabled" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN delay_enabled INTEGER NOT NULL DEFAULT 0")
    if "delay_min" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN delay_min INTEGER NOT NULL DEFAULT 1")
    if "delay_max" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN delay_max INTEGER NOT NULL DEFAULT 60")


def _migrate_runs(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)").fetchall()}
    if "send_at" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN send_at TEXT")
    if "delay_seconds" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN delay_seconds INTEGER NOT NULL DEFAULT 0")


def seed_contacts(conn):
    if conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]:
        return
    now = datetime.now().isoformat()
    contacts = []
    json_path = SEED_DIR / "contactos.json"
    if json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            contacts = [
                {"key": k, "name": k.capitalize(), "jid": v}
                for k, v in data.items()
                if isinstance(v, str) and v.strip()
            ]
        except Exception:
            contacts = []

    for c in contacts:
        conn.execute(
            "INSERT INTO contacts (key, name, jid, created_at, updated_at)"
            " VALUES (?,?,?,?,?)",
            (c["key"], c["name"], c["jid"], now, now),
        )


def seed_muestras(conn):
    if conn.execute("SELECT COUNT(*) FROM muestras").fetchone()[0]:
        return
    now = datetime.now().isoformat()
    for f in sorted(SEED_DIR.glob("muestras-*.txt")):
        persona = f.stem.split("-", 1)[1] if "-" in f.stem else ""
        if not persona:
            continue
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            conn.execute(
                "INSERT INTO muestras (persona, text, enabled, created_at, updated_at)"
                " VALUES (?,?,1,?,?)",
                (persona, line, now, now),
            )


def init_db(make_conn):
    conn = make_conn()
    try:
        conn.executescript(SCHEMA)
        _migrate_jobs(conn)
        _migrate_runs(conn)
        seed_contacts(conn)
        seed_muestras(conn)
        conn.commit()
    finally:
        conn.close()
