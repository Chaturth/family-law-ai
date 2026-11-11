"""Tiny encrypted document store on SQLite. Swap for Postgres later; the API stays the same."""
import datetime as dt
import sqlite3
import uuid
from contextlib import contextmanager
from . import security
from .config import settings

DB_PATH = settings.DATA_DIR / "app.db"


@contextmanager
def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    with _conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, kind TEXT NOT NULL,"
                  " case_id TEXT, created TEXT NOT NULL, payload BLOB NOT NULL)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_kind_case ON records(kind, case_id)")


def put(kind, data, case_id=None, id=None):
    id = id or uuid.uuid4().hex[:12]
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with _conn() as c:
        c.execute("INSERT INTO records VALUES (?,?,?,?,?)", (id, kind, case_id, now, security.enc(data)))
    return id


def _row(r):
    d = security.dec(r["payload"])
    d["id"], d["kind"], d["case_id"], d["created"] = r["id"], r["kind"], r["case_id"], r["created"]
    return d


def get(id, kind=None):
    with _conn() as c:
        r = c.execute("SELECT * FROM records WHERE id=?", (id,)).fetchone()
    if not r or (kind and r["kind"] != kind):
        return None
    return _row(r)


def update(id, patch):
    cur = get(id)
    if cur is None:
        return None
    meta = {k: cur.pop(k) for k in ("id", "kind", "case_id", "created")}
    cur.update(patch)
    with _conn() as c:
        c.execute("UPDATE records SET payload=? WHERE id=?", (security.enc(cur), id))
    return {**cur, **meta}


def list_(kind, case_id=None):
    q, a = "SELECT * FROM records WHERE kind=?", [kind]
    if case_id:
        q += " AND case_id=?"
        a.append(case_id)
    q += " ORDER BY created, rowid"
    with _conn() as c:
        rows = c.execute(q, a).fetchall()
    return [_row(r) for r in rows]
