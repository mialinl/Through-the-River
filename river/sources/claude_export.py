"""claude.ai 官方导出（conversations.json）→ 统一的窗口格式。

导出里每个对话是一棵消息树：重 roll / 编辑会在同一个父消息下长出多个子消息。
这里只保留主线——从最后一条消息顺着 parent_message_uuid 往回走到根。
"""
from __future__ import annotations

import re
from typing import Any, Optional

ROOT_PARENT = "00000000-0000-4000-8000-000000000000"
SOURCE = "claude.ai"


def is_shell(conv: dict) -> bool:
    """删掉的窗口在导出里只剩空壳：消息还在，内容全空。"""
    for m in conv.get("chat_messages") or []:
        if m.get("content") or (m.get("text") or "").strip():
            return False
    return True


def main_line(messages: list[dict]) -> list[dict]:
    """从最新一条消息往回走到根，返回按时间正序的主线。"""
    if not messages:
        return []
    if not any(m.get("parent_message_uuid") for m in messages):
        # 旧格式导出没有父子关系，也就没有分支：按原来的顺序
        return list(messages)
    by_id = {m["uuid"]: m for m in messages}
    leaf = max(messages, key=lambda m: m.get("created_at") or "")
    chain = []
    seen = set()
    cur: Optional[dict] = leaf
    while cur is not None and cur["uuid"] not in seen:
        seen.add(cur["uuid"])
        chain.append(cur)
        parent = cur.get("parent_message_uuid")
        cur = by_id.get(parent) if parent and parent != ROOT_PARENT else None
    chain.reverse()
    return chain


def window_number(name: str) -> Optional[str]:
    """从对话名里认窗口编号：「💬 14」→ 14，「14.5with opus5」→ 14.5。"""
    m = re.search(r"\d+(?:\.\d+)?", name or "")
    return m.group(0) if m else None


def _result_item(x: dict) -> dict:
    t = x.get("type")
    if t == "text":
        return {"type": "text", "text": x.get("text", "")}
    if t == "knowledge":
        return {"type": "knowledge", "title": x.get("title"), "url": x.get("url"), "text": x.get("text", "")}
    if t == "image":
        return {"type": "image", "file_uuid": x.get("file_uuid")}
    if t == "local_resource":
        return {"type": "file", "name": x.get("name"), "file_path": x.get("file_path")}
    return x


def _block(b: dict) -> Optional[dict]:
    t = b.get("type")
    if t == "text":
        return {"type": "text", "text": b.get("text", "")}
    if t == "thinking":
        return {"type": "thinking", "text": b.get("thinking", "")}
    if t == "tool_use":
        return {"type": "tool_use", "id": b.get("id"), "name": b.get("name"), "input": b.get("input")}
    if t == "tool_result":
        content = b.get("content")
        items = [_result_item(x) for x in content] if isinstance(content, list) else content
        return {
            "type": "tool_result",
            "tool_use_id": b.get("tool_use_id"),
            "name": b.get("name"),
            "is_error": bool(b.get("is_error")),
            "content": items,
        }
    if t == "flag":
        return {"type": "flag", "flag": b.get("flag")}
    return {"type": t or "unknown", "raw": b}


def _message(m: dict, i: int) -> dict:
    blocks = [nb for nb in (_block(b) for b in m.get("content") or []) if nb]
    # 消息自带的 text 字段会把工具调用写成占位符，正文只从 text 块拼；
    # 旧格式导出没有 content 块，才退回用 text 字段
    if blocks:
        text = "\n\n".join(b["text"] for b in blocks if b["type"] == "text" and b["text"])
    else:
        text = m.get("text") or ""
        if text:
            blocks = [{"type": "text", "text": text}]
    out: dict[str, Any] = {
        "i": i,
        "uuid": m["uuid"],
        "sender": m.get("sender"),
        "created_at": m.get("created_at"),
        "text": text,
        "blocks": blocks,
    }
    flags = [b["flag"] for b in blocks if b["type"] == "flag"]
    if flags:
        out["flags"] = flags
    attachments = [
        {"file_name": a.get("file_name"), "text": a.get("extracted_content") or ""}
        for a in m.get("attachments") or []
    ]
    if attachments:
        out["attachments"] = attachments
    files = [f.get("file_name") for f in m.get("files") or []]
    if files:
        out["files"] = files
    return out


def to_window(conv: dict, account_label: Optional[str] = None) -> dict:
    all_msgs = conv.get("chat_messages") or []
    line = main_line(all_msgs)
    account = conv.get("account") or {}
    return {
        "schema": 1,
        "source": SOURCE,
        "account": account.get("uuid") if isinstance(account, dict) else account,
        "account_label": account_label,
        "uuid": conv["uuid"],
        "name": conv.get("name") or "",
        "window": window_number(conv.get("name") or ""),
        "created_at": conv.get("created_at"),
        "updated_at": conv.get("updated_at"),
        "last_message_at": line[-1].get("created_at") if line else None,
        "message_count": len(line),
        "dropped_rerolls": len(all_msgs) - len(line),
        "messages": [_message(m, i) for i, m in enumerate(line)],
    }
