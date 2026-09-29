"""工作台：你的修改（edits.json）+ 发布。

修改按窗口 uuid 记，所以原文被新导出换掉以后，你写的编号、说明都还在。
发布时把原文和修改合成一个完整文件写进 windows/，搜索和 MCP 只读那里。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from . import store

EDITABLE = ("window", "name", "note", "hidden", "account_label")


def load_edits(paths: store.Paths) -> dict:
    return store.read_json(paths.edits) if paths.edits.exists() else {}


def save_edits(paths: store.Paths, edits: dict) -> None:
    store.write_json(paths.edits, edits)


def bench_windows(paths: store.Paths) -> list[dict]:
    return [store.read_json(p) for p in store.window_files(paths.workbench)]


def merged(w: dict, edit: Optional[dict]) -> dict:
    """原文 + 修改 → 发布版。原文的消息一个字不动。"""
    edit = edit or {}
    out = {k: v for k, v in w.items() if k != "messages"}
    out["original_name"] = w.get("name", "")
    for k in ("window", "name", "account_label"):
        if edit.get(k) not in (None, ""):
            out[k] = edit[k]
    out["note"] = edit.get("note") or ""
    out["messages"] = w["messages"]
    return out


def sort_key(w: dict) -> tuple:
    try:
        num = float(w.get("window") or "inf")
    except ValueError:
        num = float("inf")
    return (num, w.get("created_at") or "")


def resolve(paths: store.Paths, ref: str) -> dict:
    """用窗口编号（15、14.5）或 uuid 开头几位找到工作台里的窗口。"""
    edits = load_edits(paths)
    ws = bench_windows(paths)
    hits = [w for w in ws if merged(w, edits.get(w["uuid"])).get("window") == ref]
    if not hits:
        hits = [w for w in ws if w["uuid"].startswith(ref)]
    if not hits:
        raise SystemExit(f"工作台里没有「{ref}」这个窗口。用 python3 -m river list 看看有哪些。")
    if len(hits) > 1:
        names = "、".join(f"{w['uuid'][:8]}（{w.get('name')}）" for w in hits)
        raise SystemExit(f"「{ref}」对上了好几个窗口：{names}。换成 uuid 开头几位再试。")
    return hits[0]


def edit(paths: store.Paths, ref: str, **changes) -> dict:
    w = resolve(paths, ref)
    edits = load_edits(paths)
    e = edits.get(w["uuid"], {})
    for k, v in changes.items():
        if k not in EDITABLE or v is None:
            continue
        if v == "" or v is False:
            e.pop(k, None)
        else:
            e[k] = v
    if e:
        edits[w["uuid"]] = e
    else:
        edits.pop(w["uuid"], None)
    save_edits(paths, edits)
    return merged(w, e)


def listing(paths: store.Paths) -> list[dict]:
    """工作台里的每一窗（合上修改之后），带上是否已发布、发布版是否过期。"""
    edits = load_edits(paths)
    rows = []
    for w in bench_windows(paths):
        m = merged(w, edits.get(w["uuid"]))
        m["hidden"] = bool(edits.get(w["uuid"], {}).get("hidden"))
        pub = store.files_for_uuid(paths.windows, w["uuid"])
        if not pub:
            m["published"] = "no"
        else:
            p = store.read_json(pub[0])
            p.pop("published_at", None)
            m["published"] = "yes" if p == m_without_hidden(m) else "stale"
        rows.append(m)
    return sorted(rows, key=sort_key)


def m_without_hidden(m: dict) -> dict:
    return {k: v for k, v in m.items() if k not in ("hidden", "published")}


def publish(paths: store.Paths, tz: str = store.DEFAULT_TZ) -> list[tuple]:
    """把工作台合成发布版写进 windows/。返回 (状态, 窗口) 列表。

    藏起来的窗口会从 windows/ 撤下（工作台里还在，取消隐藏再发布就回来）。
    工作台里没有的窗口不动。
    """
    edits = load_edits(paths)
    rows = []
    for w in bench_windows(paths):
        e = edits.get(w["uuid"], {})
        existing = store.files_for_uuid(paths.windows, w["uuid"])
        m = merged(w, e)
        if e.get("hidden"):
            for p in existing:
                p.unlink()
            rows.append(("hidden" if existing else "hidden-already", m))
            continue
        fname = store.window_filename(m, tz)
        if existing:
            prev = store.read_json(existing[0])
            prev.pop("published_at", None)
            if prev == m and existing[0].name == fname:
                rows.append(("unchanged", m))
                continue
        out = dict(m)
        out["published_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        store.write_json(paths.windows / fname, out)
        for p in existing:
            if p.name != fname:
                p.unlink()
        rows.append(("updated" if existing else "new", m))
    return sorted(rows, key=lambda r: sort_key(r[1]))
