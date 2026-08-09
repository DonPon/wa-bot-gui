"""Migra contactos.json y muestras-*.txt hacia la base de datos (data.db).

Modos:
  Por defecto (upsert): agrega o actualiza contactos y muestras sin borrar nada.
  --sync: además deja las muestras de cada persona exactamente como el archivo
          (borra las muestras de esa persona que ya no están en el archivo).

Uso:
  uv run python migrate_db.py
  uv run python migrate_db.py --sync
"""
import argparse
import json
import sys
from datetime import datetime

from app import BASE_DIR, db
from seeds import init_db

CONTACTOS_JSON = BASE_DIR / "contactos.json"


def read_contacts():
    if not CONTACTOS_JSON.exists():
        print(f"[!] No existe {CONTACTOS_JSON}", file=sys.stderr)
        return []
    try:
        data = json.loads(CONTACTOS_JSON.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[!] No se pudo leer {CONTACTOS_JSON}: {e}", file=sys.stderr)
        return []
    return [
        {"key": k, "name": k.capitalize(), "jid": v}
        for k, v in data.items()
        if isinstance(v, str) and v.strip()
    ]


def read_muestras():
    muestras = {}
    for f in sorted(BASE_DIR.glob("muestras-*.txt")):
        persona = f.stem.split("-", 1)[1] if "-" in f.stem else ""
        if not persona:
            continue
        try:
            lines = [
                ln.strip()
                for ln in f.read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ]
        except Exception as e:
            print(f"[!] No se pudo leer {f}: {e}", file=sys.stderr)
            continue
        muestras[persona] = lines
    return muestras


def migrate_contacts(conn):
    now = datetime.now().isoformat()
    inserted = updated = 0
    for c in read_contacts():
        row = conn.execute(
            "SELECT id FROM contacts WHERE key=?", (c["key"],)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE contacts SET name=?, jid=?, updated_at=? WHERE id=?",
                (c["name"], c["jid"], now, row["id"]),
            )
            updated += 1
        else:
            conn.execute(
                "INSERT INTO contacts (key, name, jid, created_at, updated_at)"
                " VALUES (?,?,?,?,?)",
                (c["key"], c["name"], c["jid"], now, now),
            )
            inserted += 1
    return inserted, updated


def migrate_muestras(conn, sync=False):
    now = datetime.now().isoformat()
    added = removed = 0
    for persona, lines in read_muestras().items():
        file_set = set(lines)
        existing = {
            r["text"]
            for r in conn.execute(
                "SELECT text FROM muestras WHERE persona=?", (persona,)
            ).fetchall()
        }
        if sync:
            gone = [t for t in existing if t not in file_set]
            for t in gone:
                conn.execute(
                    "DELETE FROM muestras WHERE persona=? AND text=?", (persona, t)
                )
                removed += 1
            existing = existing.difference(gone)
        for t in lines:
            if t in existing:
                continue
            conn.execute(
                "INSERT INTO muestras (persona, text, enabled, created_at, updated_at)"
                " VALUES (?,?,1,?,?)",
                (persona, t, now, now),
            )
            added += 1
    return added, removed


def main():
    parser = argparse.ArgumentParser(
        description="Migra contactos y muestras desde los archivos hacia la BD"
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Refleja las muestras de cada persona exactamente al archivo"
        " (borra las que ya no estén en él)",
    )
    args = parser.parse_args()

    init_db(db)
    conn = db()
    try:
        ic, uc = migrate_contacts(conn)
        am, rm = migrate_muestras(conn, sync=args.sync)
        conn.commit()
    finally:
        conn.close()

    print(f"Base de datos: {BASE_DIR / 'data.db'}")
    print(f"Contactos: {ic} insertados, {uc} actualizados.")
    print(
        f"Muestras: {am} agregadas, {rm} eliminadas"
        + (" (modo sync)" if args.sync else "")
    )


if __name__ == "__main__":
    main()
