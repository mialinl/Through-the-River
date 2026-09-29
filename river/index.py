"""从 windows/ 建 SQLite 搜索索引，以及搜索、读原文。

中文没有空格分词，所以用 FTS5 的 trigram（三字切片）分词器：
三个字以上的词走全文索引，一两个字的词（「铁盒」）退回逐条扫描，量不大，扫得动。
只索引正文（text 块）；thinking、工具返回不进搜索，免得搜到的全是 breath 回显出来的记忆桶。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from . import store

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

SCHEMA = """
CREATE TABLE windows (
    uuid TEXT PRIMARY KEY, window TEXT, name TEXT, note TEXT, source TEXT,
    account_label TEXT, created_at TEXT, last_message_at TEXT, message_count INTEGER, file TEXT
);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY, uuid TEXT, window_uuid TEXT, i INTEGER,
    sender TEXT, created_at TEXT, text TEXT
);
CREATE INDEX messages_window ON messages(window_uuid, i);
CREATE INDEX messages_time ON messages(created_at);
"""
FTS = "CREATE VIRTUAL TABLE messages_fts USING fts5(text, content='messages', content_rowid='id', tokenize='trigram')"


def build(paths: store.Paths) -> tuple[int, int, bool]:
    """整个重建。返回 (窗口数, 消息数, 有没有全文索引)。"""
    tmp = paths.db.with_name(paths.db.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    try:
        con.execute(FTS)
        has_fts = True
    except sqlite3.OperationalError:
        has_fts = False  # 老版本 SQLite 没有 trigram，只能逐条扫描

    n_win = n_msg = 0
    for f in store.window_files(paths.windows):
        w = store.read_json(f)
        con.execute(
            "INSERT INTO windows VALUES (?,?,?,?,?,?,?,?,?,?)",
            (w["uuid"], w.get("window"), w.get("name"), w.get("note"), w.get("source"),
             w.get("account_label"), w.get("created_at"), w.get("last_message_at"),
             w.get("message_count"), f.name),
        )
        n_win += 1
        for m in w["messages"]:
            con.execute(
                "INSERT INTO messages (uuid, window_uuid, i, sender, created_at, text) VALUES (?,?,?,?,?,?)",
                (m["uuid"], w["uuid"], m["i"], m.get("sender"), m.get("created_at"), m.get("text") or ""),
            )
            n_msg += 1
    if has_fts:
        con.execute("INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')")
    con.commit()
    con.close()
    tmp.replace(paths.db)
    return n_win, n_msg, has_fts


def connect(paths: store.Paths) -> sqlite3.Connection:
    if not paths.db.exists():
        raise SystemExit("还没有索引。先跑一次 python3 -m river publish。")
    con = sqlite3.connect(paths.db)
    con.row_factory = sqlite3.Row
    return con


def _has_fts(con: sqlite3.Connection) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE name='messages_fts'").fetchone() is not None


def _utc_bound(day: str, tz: str, end: bool) -> str:
    d = datetime.strptime(day, "%Y-%m-%d")
    if end:
        d += timedelta(days=1)
    if ZoneInfo is not None:
        d = d.replace(tzinfo=ZoneInfo(tz))
    else:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def find_window(con: sqlite3.Connection, ref: str) -> sqlite3.Row:
    rows = con.execute("SELECT * FROM windows WHERE window = ?", (ref,)).fetchall()
    if not rows:
        rows = con.execute("SELECT * FROM windows WHERE uuid LIKE ?", (ref + "%",)).fetchall()
    if not rows:
        raise SystemExit(f"找不到「{ref}」这个窗口。")
    if len(rows) > 1:
        raise SystemExit(f"「{ref}」对上了好几个窗口，换成 uuid 开头几位再试。")
    return rows[0]


@dataclass
class Hit:
    window: Optional[str]
    window_name: str
    window_uuid: str
    i: int
    sender: str
    created_at: str
    text: str

    def snippet(self, terms: list[str], width: int = 50) -> str:
        low = self.text.lower()
        pos = min((p for p in (low.find(t.lower()) for t in terms) if p >= 0), default=0)
        start, end = max(0, pos - width), min(len(self.text), pos + width * 2)
        s = self.text[start:end].replace("\n", " ")
        for t in terms:
            s = _mark(s, t)
        return ("…" if start > 0 else "") + s + ("…" if end < len(self.text) else "")


def _mark(s: str, term: str) -> str:
    out, low, tl, k = [], s.lower(), term.lower(), 0
    while True:
        j = low.find(tl, k)
        if j < 0 or not tl:
            out.append(s[k:])
            return "".join(out)
        out.append(s[k:j] + "【" + s[j:j + len(term)] + "】")
        k = j + len(term)


def search(paths: store.Paths, query: str, window: Optional[str] = None,
           date_from: Optional[str] = None, date_to: Optional[str] = None,
           sender: Optional[str] = None, limit: int = 20, newest_first: bool = False,
           tz: str = store.DEFAULT_TZ) -> tuple[list[Hit], int]:
    """空格隔开的几个词要同时出现。返回 (前 limit 条, 总命中数)。"""
    terms = query.split()
    if not terms:
        raise SystemExit("要搜什么？")
    con = connect(paths)
    where, args = [], []
    if _has_fts(con) and all(len(t) >= 3 for t in terms):
        match = " AND ".join('"' + t.replace('"', '""') + '"' for t in terms)
        where.append("m.id IN (SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?)")
        args.append(match)
    else:
        for t in terms:
            where.append("m.text LIKE ? ESCAPE '\\'")
            args.append("%" + t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    if window:
        where.append("m.window_uuid = ?")
        args.append(find_window(con, window)["uuid"])
    if date_from:
        where.append("m.created_at >= ?")
        args.append(_utc_bound(date_from, tz, end=False))
    if date_to:
        where.append("m.created_at < ?")
        args.append(_utc_bound(date_to, tz, end=True))
    if sender:
        where.append("m.sender = ?")
        args.append(sender)

    cond = " AND ".join(where)
    total = con.execute(f"SELECT COUNT(*) FROM messages m WHERE {cond}", args).fetchone()[0]
    order = "DESC" if newest_first else "ASC"
    rows = con.execute(
        f"""SELECT m.*, w.window AS wnum, w.name AS wname FROM messages m
            JOIN windows w ON w.uuid = m.window_uuid
            WHERE {cond} ORDER BY m.created_at {order} LIMIT ?""",
        args + [limit],
    ).fetchall()
    con.close()
    hits = [Hit(r["wnum"], r["wname"], r["window_uuid"], r["i"], r["sender"], r["created_at"], r["text"])
            for r in rows]
    return hits, total


def read(paths: store.Paths, window: str, at: Optional[int] = None, around: int = 5,
         tail: bool = False) -> tuple[sqlite3.Row, list[sqlite3.Row]]:
    """读一窗里的一段原文：at 前后各 around 条；tail=True 读最后 around 条。"""
    con = connect(paths)
    w = find_window(con, window)
    if tail:
        rows = con.execute(
            "SELECT * FROM (SELECT * FROM messages WHERE window_uuid=? ORDER BY i DESC LIMIT ?) ORDER BY i",
            (w["uuid"], around),
        ).fetchall()
    else:
        center = at if at is not None else around
        rows = con.execute(
            "SELECT * FROM messages WHERE window_uuid=? AND i BETWEEN ? AND ? ORDER BY i",
            (w["uuid"], center - around, center + around),
        ).fetchall()
    con.close()
    return w, rows
