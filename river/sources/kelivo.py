"""Kelivo（API 前端）备份 → 统一的窗口格式。

Kelivo 的备份是一个 zip，聊天记录在 database/kelivo.db（SQLite）：
  conversation_rows   一行一个对话
  message_rows        一行一条消息；重 roll / 编辑出来的几个版本共用一个 group_id，
                      对话的 version_selections_json 记着每组最后选的是哪个版本
  message_part_rows   消息的各个部分：text / reasoning / tool_call / image / file
时间戳是微秒。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Iterator, Optional

SOURCE = "kelivo"
TABLES = {"conversation_rows", "message_rows", "message_part_rows"}


def looks_like(con: sqlite3.Connection) -> bool:
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return TABLES <= names


def _iso(v: Optional[int]) -> Optional[str]:
    if not v:
        return None
    secs = v / 1e6 if v > 1e14 else (v / 1e3 if v > 1e11 else v)
    return datetime.fromtimestamp(secs, timezone.utc).isoformat().replace("+00:00", "Z")


def _window_number(title: str) -> Optional[str]:
    from .claude_export import window_number
    return window_number(title)


def _main_line(rows: list, selections: dict) -> list:
    """按顺序排，同一组的几个版本只留选中的那个（没记录就留最新的）。"""
    groups: dict = {}
    for r in rows:
        if r["group_id"]:
            groups.setdefault(r["group_id"], []).append(r)
    keep = set()
    for gid, vs in groups.items():
        want = selections.get(gid)
        pick = next((v for v in vs if v["version"] == want), None) or max(vs, key=lambda v: v["version"])
        keep.add(pick["id"])
    return [r for r in rows if not r["group_id"] or r["id"] in keep]


def _blocks(parts: list) -> tuple[list, list]:
    blocks, files = [], []
    for kind, payload in parts:
        if kind == "text":
            blocks.append({"type": "text", "text": payload})
        elif kind == "reasoning":
            blocks.append({"type": "thinking", "text": payload})
        elif kind == "tool_call":
            try:
                p = json.loads(payload)
            except ValueError:
                p = {}
            blocks.append({"type": "tool_use", "id": p.get("id"), "name": p.get("name"), "input": p.get("arguments")})
            blocks.append({"type": "tool_result", "tool_use_id": p.get("id"), "name": p.get("name"),
                           "is_error": False, "content": [{"type": "text", "text": p.get("content") or ""}]})
        elif kind in ("image", "file"):
            try:
                p = json.loads(payload)
            except ValueError:
                p = {}
            name = p.get("name") or (p.get("uri") or "").rsplit("/", 1)[-1]
            files.append(name)
            blocks.append({"type": kind, "name": name, "mime": p.get("mime")})
        else:
            blocks.append({"type": kind, "raw": payload})
    return blocks, files


def windows(con: sqlite3.Connection, account_label: Optional[str] = None) -> Iterator[tuple[bool, Optional[dict]]]:
    """逐个对话产出 (是不是空壳, 窗口)。"""
    con.row_factory = sqlite3.Row
    for c in con.execute("SELECT * FROM conversation_rows ORDER BY created_at").fetchall():
        rows = con.execute("SELECT * FROM message_rows WHERE conversation_id=? ORDER BY message_order",
                           (c["id"],)).fetchall()
        if not rows:
            yield True, None
            continue
        try:
            selections = json.loads(c["version_selections_json"] or "{}")
        except ValueError:
            selections = {}
        line = _main_line(rows, selections)
        messages = []
        for i, r in enumerate(line):
            parts = con.execute("SELECT kind, payload FROM message_part_rows WHERE revision_id=? ORDER BY ordinal",
                                (r["id"],)).fetchall()
            blocks, files = _blocks(parts)
            m = {
                "i": i,
                "uuid": r["id"],
                "sender": "human" if r["role"] == "user" else r["role"],
                "created_at": _iso(r["timestamp"]),
                "text": "\n\n".join(b["text"] for b in blocks if b["type"] == "text" and b["text"]),
                "blocks": blocks,
            }
            if r["model_id"]:
                m["model"] = r["model_id"]
            if files:
                m["files"] = files
            messages.append(m)
        if not any(m["text"] for m in messages):
            yield True, None
            continue
        title = c["title"] or ""
        yield False, {
            "schema": 1,
            "source": SOURCE,
            "account": None,
            "account_label": account_label,
            "uuid": c["id"],
            "name": title,
            "window": _window_number(title),
            "created_at": _iso(c["created_at"]),
            "updated_at": _iso(c["updated_at"]),
            "last_message_at": messages[-1]["created_at"],
            "message_count": len(messages),
            "dropped_rerolls": len(rows) - len(line),
            "messages": messages,
        }
