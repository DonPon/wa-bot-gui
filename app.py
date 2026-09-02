import json
import logging
import os
import random
import re
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, date, timedelta, time as dtime
from pathlib import Path
from typing import Optional

import wa_bot
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

from seeds import init_db

load_dotenv()
TOKEN = os.environ.get("TOKEN")

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data.db"
STATIC_DIR = BASE_DIR / "static"
LOG_FILE = BASE_DIR / "wa-scheduler.log"

WEEKDAY_LABELS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]

GRACE_MINUTES = int(os.environ.get("GRACE_MINUTES", "120"))
TICK_SECONDS = float(os.environ.get("TICK_SECONDS", "900"))
SEND_GRACE_MINUTES = int(os.environ.get("SEND_GRACE_MINUTES", "60"))


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("wa-scheduler")
_fh = logging.FileHandler(LOG_FILE)
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(_fh)

stop_event = threading.Event()
scheduler_started = None
last_tick = None


def require_token(x_token: str = Header(default="")):
    if x_token != TOKEN:
        raise HTTPException(status_code=401, detail="token inválido")


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def persona_exists(key: str) -> bool:
    conn = db()
    try:
        return conn.execute("SELECT 1 FROM contacts WHERE key=?", (key,)).fetchone() is not None
    finally:
        conn.close()


class ContactIn(BaseModel):
    key: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=100)
    jid: str = Field(min_length=1, max_length=200)

    @field_validator("key")
    @classmethod
    def _key(cls, v):
        v = v.strip().lower()
        if not re.fullmatch(r"[a-z0-9_-]+", v):
            raise ValueError("clave inválida (solo minúsculas, números, _ y -)")
        return v


class MuestraIn(BaseModel):
    persona: str
    text: str = Field(min_length=1, max_length=2000)
    enabled: bool = True

    @field_validator("persona")
    @classmethod
    def _persona(cls, v):
        if not persona_exists(v):
            raise ValueError(f"persona no existe: {v}")
        return v


class JobIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    persona: str
    time: str
    days_mode: str = "fixed"
    weekdays: list[int] = Field(default_factory=list)
    random_per_week: Optional[int] = None
    dry_run: bool = False
    enabled: bool = True
    delay_enabled: bool = False
    delay_min: int = 1
    delay_max: int = 60

    @field_validator("persona")
    @classmethod
    def _persona(cls, v):
        if not persona_exists(v):
            raise ValueError(f"persona no existe: {v}")
        return v

    @field_validator("time")
    @classmethod
    def _time(cls, v):
        try:
            datetime.strptime(v, "%H:%M")
        except ValueError:
            raise ValueError("time debe tener formato HH:MM")
        return v

    @field_validator("days_mode")
    @classmethod
    def _mode(cls, v):
        if v not in ("fixed", "random"):
            raise ValueError("days_mode debe ser 'fixed' o 'random'")
        return v

    @field_validator("weekdays")
    @classmethod
    def _weekdays(cls, v):
        for d in v:
            if not 0 <= d <= 6:
                raise ValueError("weekdays debe estar entre 0 (lun) y 6 (dom)")
        return sorted(set(v))

    @field_validator("random_per_week")
    @classmethod
    def _k(cls, v):
        if v is not None and not 1 <= v <= 7:
            raise ValueError("random_per_week debe estar entre 1 y 7")
        return v

    @field_validator("delay_min", "delay_max")
    @classmethod
    def _delay(cls, v):
        if not 0 <= v <= 1440:
            raise ValueError("el retardo debe estar entre 0 y 1440 minutos")
        return v

    @model_validator(mode="after")
    def _delay_range(self):
        if self.delay_enabled and self.delay_max < self.delay_min:
            raise ValueError("delay_max debe ser mayor o igual a delay_min")
        return self


class OneTimeIn(BaseModel):
    persona: str
    text: str = Field(min_length=1, max_length=2000)
    send_at: str
    dry_run: bool = False
    delay_enabled: bool = False
    delay_min: int = 1
    delay_max: int = 60

    @field_validator("persona")
    @classmethod
    def _persona(cls, v):
        if not persona_exists(v):
            raise ValueError(f"persona no existe: {v}")
        return v

    @field_validator("send_at")
    @classmethod
    def _send_at(cls, v):
        try:
            datetime.fromisoformat(v)
        except ValueError:
            raise ValueError("send_at debe tener formato ISO (YYYY-MM-DDTHH:MM:SS)")
        return v

    @field_validator("delay_min", "delay_max")
    @classmethod
    def _delay(cls, v):
        if not 0 <= v <= 1440:
            raise ValueError("el retardo debe estar entre 0 y 1440 minutos")
        return v

    @model_validator(mode="after")
    def _delay_range(self):
        if self.delay_enabled and self.delay_max < self.delay_min:
            raise ValueError("delay_max debe ser mayor o igual a delay_min")
        return self


def week_key(d: date) -> str:
    return (d - timedelta(days=d.weekday())).isoformat()


def parse_hhmm(s: str):
    h, m = s.split(":")
    return int(h), int(m)


def pick_random_days(k: int) -> list[int]:
    """Elige k días (0=lun ... 6=dom) al azar, variando cada semana.

    Evita dos días seguidos cuando es posible (k <= 4). Para k >= 5 los
    reparte de forma uniforme a lo largo de la semana.
    """
    if k <= 0:
        return []
    if k >= 5:
        # muchos días: repartir de forma uniforme por la semana
        step = 7 / min(k, 7)
        start = random.randrange(7)
        return sorted({int((start + i * step) % 7) for i in range(min(k, 7))})
    # escoger k días sin dos seguidos sobre los 7 (método "posiciones + i")
    positions = sorted(random.sample(range(7 - k + 1), k))
    return [positions[i] + i for i in range(k)]


def ensure_rolls(job) -> dict:
    if job["days_mode"] != "random":
        return {}
    k = min(job["random_per_week"] or 0, 7)
    today = date.today()
    weeks = [week_key(today + timedelta(days=7 * i)) for i in range(4)]
    rolls = json.loads(job["week_rolls"]) if job["week_rolls"] else {}
    changed = False
    for wk in weeks:
        if wk not in rolls and k > 0:
            rolls[wk] = pick_random_days(k)
            changed = True
    if changed:
        conn = db()
        try:
            conn.execute(
                "UPDATE jobs SET week_rolls=? WHERE id=?",
                (json.dumps(rolls), job["id"]),
            )
            conn.commit()
        finally:
            conn.close()
    return rolls


def weekday_eligible(job, d: date) -> bool:
    if job["days_mode"] == "fixed":
        return d.weekday() in set(json.loads(job["weekdays"]))
    rolls = ensure_rolls(job)
    return d.weekday() in rolls.get(week_key(d), set())


def next_run(job):
    if not job["enabled"]:
        return None
    hh, mm = parse_hhmm(job["time"])
    now = datetime.now()
    today = now.date()
    for offset in range(25):
        d = today + timedelta(days=offset)
        if not weekday_eligible(job, d):
            continue
        when = datetime.combine(d, dtime(hh, mm))
        if offset == 0:
            if when > now:
                return when
            if job["last_run_date"] != today.isoformat() and now - when <= timedelta(
                minutes=GRACE_MINUTES
            ):
                return when
            continue
        return when
    return None


def due_now(row) -> bool:
    if not row["enabled"]:
        return False
    today = date.today()
    if row["last_run_date"] == today.isoformat():
        return False
    if not weekday_eligible(row, today):
        return False
    hh, mm = parse_hhmm(row["time"])
    scheduled = datetime.combine(today, dtime(hh, mm))
    now = datetime.now()
    return now >= scheduled and now - scheduled <= timedelta(minutes=GRACE_MINUTES)


def launch(conn, job, trigger: str, scheduled_at: str):
    delay_seconds = 0
    if job["delay_enabled"]:
        delay_seconds = random.randint(
            int(job["delay_min"]) * 60, int(job["delay_max"]) * 60
        )
    send_at = datetime.now() + timedelta(seconds=delay_seconds)
    cur = conn.execute(
        "INSERT INTO runs (job_id, trigger, scheduled_at, started_at, status, send_at, delay_seconds)"
        " VALUES (?,?,?,?, 'pending', ?, ?)",
        (job["id"], trigger, scheduled_at, None, send_at.isoformat(), delay_seconds),
    )
    run_id = cur.lastrowid
    if delay_seconds:
        logger.info(
            "job %s: run %s programado (%s) con retardo de %s min, envío aprox. %s",
            job["id"],
            run_id,
            trigger,
            round(delay_seconds / 60, 1),
            send_at.isoformat(),
        )
    else:
        logger.info(
            "job %s: run %s programado (%s), envío inmediato", job["id"], run_id, trigger
        )


def _finish_run(run_id: int, exit_code, status: str, output: str, command: str = None):
    conn = db()
    try:
        if command is None:
            conn.execute(
                "UPDATE runs SET finished_at=?, exit_code=?, status=?, output=? WHERE id=?",
                (datetime.now().isoformat(), exit_code, status, output, run_id),
            )
        else:
            conn.execute(
                "UPDATE runs SET finished_at=?, exit_code=?, status=?, output=?, command=?"
                " WHERE id=?",
                (datetime.now().isoformat(), exit_code, status, output, command, run_id),
            )
        conn.commit()
    finally:
        conn.close()
    logger.info("run %s: %s (exit=%s)", run_id, status, exit_code)


def send_worker(run_id: int):
    conn = db()
    try:
        run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    finally:
        conn.close()
    if not run:
        return
    conn = db()
    try:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (run["job_id"],)).fetchone()
    finally:
        conn.close()
    if not job:
        _finish_run(run_id, 1, "failed", "job no encontrado")
        return
    persona = job["persona"]
    conn = db()
    try:
        contact = conn.execute(
            "SELECT * FROM contacts WHERE key=?", (persona,)
        ).fetchone()
        muestras = conn.execute(
            "SELECT text FROM muestras WHERE persona=? AND enabled=1", (persona,)
        ).fetchall()
    finally:
        conn.close()
    lines = [r["text"].strip() for r in muestras if r["text"].strip()]
    if not contact:
        _finish_run(run_id, 1, "failed", f"no hay contacto con la clave '{persona}'")
        return
    if not lines:
        _finish_run(run_id, 1, "failed", f"no hay muestras activas para '{persona}'")
        return
    message = random.choice(lines)
    cmd_str = f'wacli send text --to {contact["jid"]} --message "{message}"'
    logger.info("run %s: envío a %s (persona=%s)", run_id, contact["jid"], persona)
    result = wa_bot.send_message(
        contact["jid"], message, dry_run=bool(job["dry_run"])
    )
    output = (
        f"persona={persona}\n"
        f"contacto={contact['name']} <{contact['jid']}>\n"
        f"mensaje={message}\n"
        f"delay={run['delay_seconds']}s\n"
        f"{result['output'] or ''}"
    )
    _finish_run(run_id, result["exit_code"], result["status"], output, cmd_str)


def one_time_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "persona": row["persona"],
        "text": row["text"],
        "send_at": row["send_at"],
        "status": row["status"],
        "dry_run": bool(row["dry_run"]),
        "delay_enabled": bool(row["delay_enabled"]),
        "delay_min": row["delay_min"],
        "delay_max": row["delay_max"],
        "result": row["result"],
        "sent_at": row["sent_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _finish_one_time(row_id: int, status: str, result: str):
    conn = db()
    try:
        conn.execute(
            "UPDATE one_time SET status=?, result=?, sent_at=?, updated_at=? WHERE id=?",
            (status, result, datetime.now().isoformat(), datetime.now().isoformat(), row_id),
        )
        conn.commit()
    finally:
        conn.close()
    logger.info("one-time %s: %s", row_id, status)


def send_one_time_worker(row_id: int):
    conn = db()
    try:
        row = conn.execute("SELECT * FROM one_time WHERE id=?", (row_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return
    delay_seconds = 0
    if row["delay_enabled"]:
        delay_seconds = random.randint(
            int(row["delay_min"]) * 60, int(row["delay_max"]) * 60
        )
    if delay_seconds:
        time.sleep(delay_seconds)
    conn = db()
    try:
        contact = conn.execute(
            "SELECT * FROM contacts WHERE key=?", (row["persona"],)
        ).fetchone()
    finally:
        conn.close()
    if not contact:
        _finish_one_time(row_id, "failed", f"no hay contacto con la clave '{row['persona']}'")
        return
    logger.info(
        "one-time %s: envío a %s (persona=%s, delay=%ss)",
        row_id, contact["jid"], row["persona"], delay_seconds,
    )
    result = wa_bot.send_message(
        contact["jid"], row["text"], dry_run=bool(row["dry_run"])
    )
    output = (
        f"persona={row['persona']}\n"
        f"contacto={contact['name']} <{contact['jid']}>\n"
        f"mensaje={row['text']}\n"
        f"delay={delay_seconds}s\n"
        f"{result['output'] or ''}"
    )
    _finish_one_time(row_id, result["status"], output)


def dispatch_due_one_time(conn):
    now = datetime.now()
    rows = conn.execute(
        "SELECT * FROM one_time WHERE status='pending' AND send_at <= ?",
        (now.isoformat(),),
    ).fetchall()
    for row in rows:
        send_at = datetime.fromisoformat(row["send_at"])
        overdue_min = (now - send_at).total_seconds() / 60.0
        if overdue_min > SEND_GRACE_MINUTES:
            reason = f"[omitido: el envío venció hace {int(overdue_min)} min]"
            conn.execute(
                "UPDATE one_time SET status='failed', result=?, updated_at=? WHERE id=?",
                (reason, now.isoformat(), row["id"]),
            )
            logger.info("one-time %s: %s", row["id"], reason)
        else:
            conn.execute(
                "UPDATE one_time SET status='running', updated_at=? WHERE id=?",
                (now.isoformat(), row["id"]),
            )
            threading.Thread(
                target=send_one_time_worker, args=(row["id"],), daemon=True
            ).start()


def dispatch_pending(conn):
    now = datetime.now()
    rows = conn.execute(
        "SELECT * FROM runs WHERE status='pending' AND send_at IS NOT NULL"
        " AND send_at <= ?",
        (now.isoformat(),),
    ).fetchall()
    for run in rows:
        send_at = datetime.fromisoformat(run["send_at"])
        overdue_min = (now - send_at).total_seconds() / 60.0
        if overdue_min > SEND_GRACE_MINUTES:
            reason = f"[omitido: el envío venció hace {int(overdue_min)} min]"
            conn.execute(
                "UPDATE runs SET status='failed', finished_at=?, exit_code=NULL, output=?"
                " WHERE id=?",
                (now.isoformat(), reason, run["id"]),
            )
            logger.info("run %s: %s", run["id"], reason)
        else:
            conn.execute(
                "UPDATE runs SET status='running', started_at=? WHERE id=?",
                (now.isoformat(), run["id"]),
            )
            threading.Thread(
                target=send_worker, args=(run["id"],), daemon=True
            ).start()


def _maybe_run(conn, job):
    today = date.today()
    if job["last_run_date"] == today.isoformat():
        return
    if not weekday_eligible(job, today):
        return
    hh, mm = parse_hhmm(job["time"])
    scheduled = datetime.combine(today, dtime(hh, mm))
    now = datetime.now()
    if now < scheduled:
        return
    if now - scheduled > timedelta(minutes=GRACE_MINUTES):
        return
    conn.execute(
        "UPDATE jobs SET last_run_date=? WHERE id=?",
        (today.isoformat(), job["id"]),
    )
    launch(conn, job, "schedule", scheduled.isoformat())


def tick():
    global last_tick
    last_tick = datetime.now()
    conn = db()
    try:
        jobs = conn.execute("SELECT * FROM jobs WHERE enabled=1").fetchall()
        for job in jobs:
            _maybe_run(conn, job)
        dispatch_pending(conn)
        dispatch_due_one_time(conn)
        conn.commit()
    finally:
        conn.close()


def scheduler_loop():
    while not stop_event.is_set():
        try:
            tick()
        except Exception:
            logger.exception("error en tick")
        stop_event.wait(TICK_SECONDS)


def mark_stale_running():
    conn = db()
    try:
        conn.execute(
            "UPDATE runs SET status='failed', finished_at=?,"
            " output=COALESCE(output,'') || '\n[interrumpido por reinicio del servidor]'"
            " WHERE status='running'",
            (datetime.now().isoformat(),),
        )
        conn.execute(
            "UPDATE one_time SET status='failed', result=?, updated_at=?"
            " WHERE status='running'",
            ("[interrumpido por reinicio del servidor]", datetime.now().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app_):
    global scheduler_started
    init_db(db)
    mark_stale_running()
    scheduler_started = datetime.now()
    t = threading.Thread(target=scheduler_loop, daemon=True, name="scheduler")
    t.start()
    logger.info(
        "scheduler iniciado (tick=%ss, grace=%smin)", TICK_SECONDS, GRACE_MINUTES
    )
    yield
    stop_event.set()
    t.join(timeout=TICK_SECONDS + 1)
    logger.info("scheduler detenido")


app = FastAPI(title="WA Bot Scheduler", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
api_router = APIRouter(dependencies=[Depends(require_token)])


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True}


def job_to_dict(row) -> dict:
    d = {
        "id": row["id"],
        "name": row["name"],
        "persona": row["persona"],
        "time": row["time"],
        "days_mode": row["days_mode"],
        "weekdays": json.loads(row["weekdays"]),
        "random_per_week": row["random_per_week"],
        "dry_run": bool(row["dry_run"]),
        "enabled": bool(row["enabled"]),
        "delay_enabled": bool(row["delay_enabled"]),
        "delay_min": row["delay_min"],
        "delay_max": row["delay_max"],
        "last_run_date": row["last_run_date"],
        "weekday_labels": [WEEKDAY_LABELS[w] for w in json.loads(row["weekdays"])],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    nxt = next_run(row)
    d["next_run"] = nxt.isoformat() if nxt else None
    d["due_now"] = due_now(row)
    return d


@api_router.get("/api/jobs")
def list_jobs():
    conn = db()
    try:
        rows = conn.execute("SELECT * FROM jobs ORDER BY time, name").fetchall()
        runs_map = {}
        for r in conn.execute(
            """
            SELECT r.* FROM runs r
            JOIN (SELECT job_id, MAX(id) AS mid FROM runs GROUP BY job_id) m
              ON r.id = m.mid
            """
        ).fetchall():
            runs_map[r["job_id"]] = dict(r)
        jobs = [job_to_dict(row) for row in rows]
        for j in jobs:
            last = runs_map.get(j["id"])
            if last:
                last["output"] = None
            j["last_run"] = last
        return jobs
    finally:
        conn.close()


@api_router.put("/api/jobs/{job_id}/toggle")
def toggle_job(job_id: int):
    conn = db()
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, "job no encontrado")
        conn.execute(
            "UPDATE jobs SET enabled=?, updated_at=? WHERE id=?",
            (0 if row["enabled"] else 1, datetime.now().isoformat(), job_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return job_to_dict(row)
    finally:
        conn.close()


@api_router.post("/api/jobs")
def create_job(job: JobIn):
    now = datetime.now().isoformat()
    conn = db()
    try:
        cur = conn.execute(
            """INSERT INTO jobs (name, persona, time, days_mode, weekdays, random_per_week,
               dry_run, enabled, week_rolls, delay_enabled, delay_min, delay_max,
               created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,'{}',?,?,?,?,?)""",
            (
                job.name,
                job.persona,
                job.time,
                job.days_mode,
                json.dumps(job.weekdays),
                job.random_per_week or 0,
                int(job.dry_run),
                int(job.enabled),
                int(job.delay_enabled),
                job.delay_min,
                job.delay_max,
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (cur.lastrowid,)).fetchone()
        if row["days_mode"] == "random":
            ensure_rolls(row)
        return job_to_dict(row)
    finally:
        conn.close()


@api_router.put("/api/jobs/{job_id}")
def update_job(job_id: int, job: JobIn):
    conn = db()
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, "job no encontrado")
        now = datetime.now().isoformat()
        schedule_changed = (
            row["days_mode"] != job.days_mode
            or (row["random_per_week"] or 0) != (job.random_per_week or 0)
            or json.loads(row["weekdays"]) != job.weekdays
        )
        rolls = "{}" if schedule_changed else row["week_rolls"]
        conn.execute(
            """UPDATE jobs SET name=?, persona=?, time=?, days_mode=?, weekdays=?,
               random_per_week=?, dry_run=?, enabled=?, week_rolls=?,
               delay_enabled=?, delay_min=?, delay_max=?, updated_at=?
               WHERE id=?""",
            (
                job.name,
                job.persona,
                job.time,
                job.days_mode,
                json.dumps(job.weekdays),
                job.random_per_week or 0,
                int(job.dry_run),
                int(job.enabled),
                rolls,
                int(job.delay_enabled),
                job.delay_min,
                job.delay_max,
                now,
                job_id,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if job.days_mode == "random":
            ensure_rolls(row)
        return job_to_dict(row)
    finally:
        conn.close()


@api_router.delete("/api/jobs/{job_id}")
def delete_job(job_id: int):
    conn = db()
    try:
        conn.execute("DELETE FROM runs WHERE job_id=?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@api_router.post("/api/jobs/{job_id}/run")
def run_now(job_id: int):
    conn = db()
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, "job no encontrado")
        conn.execute(
            "UPDATE jobs SET last_run_date=? WHERE id=?",
            (date.today().isoformat(), job_id),
        )
        conn.commit()
        launch(conn, row, "manual", datetime.now().isoformat())
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@api_router.get("/api/jobs/{job_id}/runs")
def job_runs(job_id: int, limit: int = Query(50, ge=1, le=200)):
    conn = db()
    try:
        rows = conn.execute(
            "SELECT * FROM runs WHERE job_id=? ORDER BY id DESC LIMIT ?",
            (job_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@api_router.get("/api/contacts")
def list_contacts():
    conn = db()
    try:
        rows = conn.execute("SELECT * FROM contacts ORDER BY key").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@api_router.post("/api/contacts")
def create_contact(c: ContactIn):
    now = datetime.now().isoformat()
    conn = db()
    try:
        existing = conn.execute("SELECT 1 FROM contacts WHERE key=?", (c.key,)).fetchone()
        if existing:
            raise HTTPException(400, "ya existe un contacto con esa clave")
        cur = conn.execute(
            "INSERT INTO contacts (key, name, jid, created_at, updated_at)"
            " VALUES (?,?,?,?,?)",
            (c.key, c.name, c.jid, now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM contacts WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)
    finally:
        conn.close()


@api_router.put("/api/contacts/{contact_id}")
def update_contact(contact_id: int, c: ContactIn):
    conn = db()
    try:
        row = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
        if not row:
            raise HTTPException(404, "contacto no encontrado")
        old_key = row["key"]
        if c.key != old_key:
            clash = conn.execute(
                "SELECT 1 FROM contacts WHERE key=? AND id<>?", (c.key, contact_id)
            ).fetchone()
            if clash:
                raise HTTPException(400, "ya existe un contacto con esa clave")
        conn.execute(
            "UPDATE contacts SET key=?, name=?, jid=?, updated_at=? WHERE id=?",
            (c.key, c.name, c.jid, datetime.now().isoformat(), contact_id),
        )
        conn.execute("UPDATE jobs SET persona=? WHERE persona=?", (c.key, old_key))
        conn.execute("UPDATE muestras SET persona=? WHERE persona=?", (c.key, old_key))
        conn.commit()
        row = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()


@api_router.delete("/api/contacts/{contact_id}")
def delete_contact(contact_id: int):
    conn = db()
    try:
        row = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
        if not row:
            raise HTTPException(404, "contacto no encontrado")
        used = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE persona=?", (row["key"],)
        ).fetchone()[0]
        if used:
            raise HTTPException(
                400, f"no se puede eliminar: hay {used} job(s) que usan esta persona"
            )
        conn.execute("DELETE FROM muestras WHERE persona=?", (row["key"],))
        conn.execute("DELETE FROM contacts WHERE id=?", (contact_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@api_router.get("/api/muestras")
def list_muestras():
    conn = db()
    try:
        rows = conn.execute("SELECT * FROM muestras ORDER BY persona, id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@api_router.post("/api/muestras")
def create_muestra(m: MuestraIn):
    now = datetime.now().isoformat()
    conn = db()
    try:
        cur = conn.execute(
            "INSERT INTO muestras (persona, text, enabled, created_at, updated_at)"
            " VALUES (?,?,?,?,?)",
            (m.persona, m.text, int(m.enabled), now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM muestras WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)
    finally:
        conn.close()


@api_router.put("/api/muestras/{muestra_id}")
def update_muestra(muestra_id: int, m: MuestraIn):
    conn = db()
    try:
        row = conn.execute("SELECT * FROM muestras WHERE id=?", (muestra_id,)).fetchone()
        if not row:
            raise HTTPException(404, "muestra no encontrada")
        conn.execute(
            "UPDATE muestras SET persona=?, text=?, enabled=?, updated_at=? WHERE id=?",
            (m.persona, m.text, int(m.enabled), datetime.now().isoformat(), muestra_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM muestras WHERE id=?", (muestra_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()


@api_router.delete("/api/muestras/{muestra_id}")
def delete_muestra(muestra_id: int):
    conn = db()
    try:
        conn.execute("DELETE FROM muestras WHERE id=?", (muestra_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@api_router.get("/api/one-time")
def list_one_time():
    conn = db()
    try:
        rows = conn.execute("SELECT * FROM one_time ORDER BY send_at DESC, id DESC").fetchall()
        return [one_time_to_dict(r) for r in rows]
    finally:
        conn.close()


@api_router.post("/api/one-time")
def create_one_time(m: OneTimeIn):
    now = datetime.now().isoformat()
    conn = db()
    try:
        cur = conn.execute(
            """INSERT INTO one_time (persona, text, send_at, status, dry_run,
               delay_enabled, delay_min, delay_max, created_at, updated_at)
               VALUES (?,?,?, 'pending', ?,?,?,?,?,?)""",
            (
                m.persona,
                m.text,
                m.send_at,
                int(m.dry_run),
                int(m.delay_enabled),
                m.delay_min,
                m.delay_max,
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM one_time WHERE id=?", (cur.lastrowid,)).fetchone()
        return one_time_to_dict(row)
    finally:
        conn.close()


@api_router.post("/api/one-time/{one_time_id}/cancel")
def cancel_one_time(one_time_id: int):
    conn = db()
    try:
        row = conn.execute("SELECT * FROM one_time WHERE id=?", (one_time_id,)).fetchone()
        if not row:
            raise HTTPException(404, "envío único no encontrado")
        if row["status"] != "pending":
            raise HTTPException(400, f"no se puede cancelar un envío en estado '{row['status']}'")
        now = datetime.now().isoformat()
        conn.execute(
            "UPDATE one_time SET status='cancelled', updated_at=? WHERE id=?",
            (now, one_time_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM one_time WHERE id=?", (one_time_id,)).fetchone()
        return one_time_to_dict(row)
    finally:
        conn.close()


@api_router.delete("/api/one-time/{one_time_id}")
def delete_one_time(one_time_id: int):
    conn = db()
    try:
        row = conn.execute("SELECT * FROM one_time WHERE id=?", (one_time_id,)).fetchone()
        if not row:
            raise HTTPException(404, "envío único no encontrado")
        if row["status"] == "running":
            raise HTTPException(400, "no se puede eliminar un envío en curso")
        conn.execute("DELETE FROM one_time WHERE id=?", (one_time_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@api_router.get("/api/status")
def status():
    conn = db()
    try:
        jobs_total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        jobs_enabled = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE enabled=1"
        ).fetchone()[0]
        running = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE status='running'"
        ).fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE status='pending'"
        ).fetchone()[0]
        contacts_total = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        muestras_total = conn.execute("SELECT COUNT(*) FROM muestras").fetchone()[0]
        one_time_total = conn.execute("SELECT COUNT(*) FROM one_time").fetchone()[0]
        one_time_pending = conn.execute(
            "SELECT COUNT(*) FROM one_time WHERE status='pending'"
        ).fetchone()[0]
    finally:
        conn.close()
    return {
        "scheduler": not stop_event.is_set(),
        "started_at": scheduler_started.isoformat() if scheduler_started else None,
        "last_tick": last_tick.isoformat() if last_tick else None,
        "tick_seconds": TICK_SECONDS,
        "grace_minutes": GRACE_MINUTES,
        "jobs_total": jobs_total,
        "jobs_enabled": jobs_enabled,
        "runs_running": running,
        "runs_pending": pending,
        "contacts_total": contacts_total,
        "muestras_total": muestras_total,
        "one_time_total": one_time_total,
        "one_time_pending": one_time_pending,
    }


app.include_router(api_router)


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8787"))
    uvicorn.run(app, host=host, port=port, log_level="info")
