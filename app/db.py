"""Хранилище на SQLite (stdlib sqlite3, WAL).

Таблицы:
  chats  — чаты
  items  — элементы ленты чата (сообщение пользователя / ссылка на видео)
  videos — метаданные и статус генерации видео
Медиафайлы (mp3/mp4) лежат в data/media/, метаданные — здесь.
"""
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
MEDIA_DIR = DATA_DIR / "media"
DB_PATH = DATA_DIR / "app.db"

DATA_DIR.mkdir(exist_ok=True)
MEDIA_DIR.mkdir(exist_ok=True)

_write_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chats(
    id      TEXT PRIMARY KEY,
    title   TEXT NOT NULL DEFAULT '',
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS items(
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id  TEXT NOT NULL REFERENCES chats(id),
    kind     TEXT NOT NULL,          -- 'user' | 'video'
    text     TEXT,
    video_id TEXT,
    created  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_chat ON items(chat_id);
CREATE TABLE IF NOT EXISTS videos(
    id         TEXT PRIMARY KEY,
    problem    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',   -- pending|ready|error
    stage      TEXT NOT NULL DEFAULT '',          -- человекочитаемый этап для UI
    error      TEXT,
    title      TEXT,
    html       TEXT,
    steps_json TEXT,
    created    TEXT NOT NULL
);
"""


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init() -> None:
    with _write_lock, conn() as c:
        c.executescript(_SCHEMA)


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------- чаты ----------

def create_chat(title: str = "") -> dict:
    import uuid
    cid = uuid.uuid4().hex[:12]
    with _write_lock, conn() as c:
        c.execute("INSERT INTO chats(id,title,created) VALUES(?,?,?)", (cid, title, now()))
    return {"id": cid, "title": title, "created": now(), "items": []}


def get_chat(cid: str) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM chats WHERE id=?", (cid,)).fetchone()
        if not row:
            return None
        items = c.execute(
            "SELECT kind,text,video_id FROM items WHERE chat_id=? ORDER BY id", (cid,)
        ).fetchall()
    return {
        "id": row["id"],
        "title": row["title"] or "",
        "created": row["created"],
        "items": [
            {"type": i["kind"], **({"text": i["text"]} if i["kind"] == "user" else {"video_id": i["video_id"]})}
            for i in items
        ],
    }


def append_item(cid: str, kind: str, text: str | None = None, video_id: str | None = None) -> None:
    with _write_lock, conn() as c:
        c.execute(
            "INSERT INTO items(chat_id,kind,text,video_id,created) VALUES(?,?,?,?,?)",
            (cid, kind, text, video_id, now()),
        )


def set_chat_title_if_empty(cid: str, title: str) -> None:
    with _write_lock, conn() as c:
        c.execute("UPDATE chats SET title=? WHERE id=? AND (title='' OR title IS NULL)", (title[:70], cid))


def list_chats() -> list[dict]:
    with conn() as c:
        rows = c.execute(
            """SELECT ch.id, ch.title, ch.created,
                      SUM(CASE WHEN it.kind='video' THEN 1 ELSE 0 END) AS n_videos
               FROM chats ch LEFT JOIN items it ON it.chat_id=ch.id
               GROUP BY ch.id ORDER BY ch.created DESC"""
        ).fetchall()
        pending_ids = {r[0] for r in c.execute(
            "SELECT DISTINCT v.id FROM videos v JOIN items it ON it.video_id=v.id "
            "WHERE v.status IN ('pending')"
        ).fetchall()}
        chat_pending = {r[0] for r in c.execute(
            "SELECT DISTINCT it.chat_id FROM items it JOIN videos v ON v.id=it.video_id "
            "WHERE v.status='pending'"
        ).fetchall()}
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "title": r["title"] or "Новый лист",
            "created": r["created"],
            "n_videos": r["n_videos"] or 0,
            "pending": r["id"] in chat_pending,
        })
    return out


# ---------- видео ----------

def create_video(vid: str, problem: str) -> None:
    with _write_lock, conn() as c:
        c.execute(
            "INSERT INTO videos(id,problem,status,created) VALUES(?,?,?,?)",
            (vid, problem, "pending", now()),
        )


def update_video(vid: str, **fields) -> None:
    if not fields:
        return
    keys = ",".join(f"{k}=?" for k in fields)
    with _write_lock, conn() as c:
        c.execute(f"UPDATE videos SET {keys} WHERE id=?", (*fields.values(), vid))


def pending_videos() -> list[dict]:
    with conn() as c:
        rows = c.execute("SELECT id FROM videos WHERE status='pending'").fetchall()
    return [dict(r) for r in rows]


def get_video(vid: str) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    return d


def get_steps(vid: str) -> list | None:
    import json
    with conn() as c:
        row = c.execute("SELECT steps_json FROM videos WHERE id=?", (vid,)).fetchone()
    if not row or not row["steps_json"]:
        return None
    try:
        return json.loads(row["steps_json"])
    except json.JSONDecodeError:
        return None


def get_html(vid: str) -> str | None:
    with conn() as c:
        row = c.execute("SELECT html FROM videos WHERE id=?", (vid,)).fetchone()
    return row["html"] if row else None
