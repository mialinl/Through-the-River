"""导入：把一份导出拆成一窗一个 json，放到工作台。

认得的格式：
  claude.ai 官方导出   zip / 解压的文件夹 / conversations.json
  Kelivo 备份          zip / 解压的文件夹 / database/kelivo.db

同一个窗口靠 uuid 认：没变的跳过，聊多了的换成新版本，改过名的不留两份。
导出里没有了的窗口（在 app 里删掉的）不动，工作台里照样留着。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from . import store
from .sources import claude_export, kelivo

SQLITE_MAGIC = b"SQLite format 3\x00"
UNKNOWN = "的格式还不认识（不是 claude.ai 导出，也不是 Kelivo 备份）。发给 CC 看看结构，加一个对应的导入。"


def read_export(path: Path) -> tuple[str, bytes]:
    """找到导出里真正装着对话的那个文件，返回 (格式, 内容)。"""
    if path.is_dir():
        for name, kind in (("conversations.json", "claude"), ("kelivo.db", "kelivo")):
            found = sorted(path.rglob(name))
            if found:
                return kind, found[0].read_bytes()
        raise SystemExit(f"{path} 这个文件夹里没找到 conversations.json 或 kelivo.db")
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            for want, kind in (("conversations.json", "claude"), ("kelivo.db", "kelivo")):
                hits = [n for n in names if n.rsplit("/", 1)[-1] == want]
                if hits:
                    return kind, z.read(hits[0])
        raise SystemExit(f"{path.name}{UNKNOWN}")
    data = path.read_bytes()
    return ("kelivo" if data.startswith(SQLITE_MAGIC) else "claude"), data


def archive_raw(data: bytes, raw_dir: Path, kind: str) -> tuple[Path, bool]:
    digest = hashlib.sha256(data).hexdigest()[:12]
    if raw_dir.exists():
        for p in raw_dir.glob(f"*-{digest}.*"):
            return p, False
    prefix, ext = ("kelivo-backup", "db") if kind == "kelivo" else ("claude-export", "json")
    dest = raw_dir / f"{prefix}-{datetime.now().strftime('%Y%m%d')}-{digest}.{ext}"
    store.write_bytes(dest, data)
    return dest, True


def _claude_windows(data: bytes, path: Path, label: Optional[str]) -> Iterator[tuple[bool, Optional[dict]]]:
    try:
        obj = json.loads(data)
    except ValueError:
        raise SystemExit(f"{path.name}{UNKNOWN}")
    if not claude_export.looks_like(obj):
        raise SystemExit(f"{path.name}{UNKNOWN}")
    for conv in sorted(obj, key=lambda c: c.get("created_at") or ""):
        if claude_export.is_shell(conv):
            yield True, None
        else:
            yield False, claude_export.to_window(conv, label)


def _kelivo_windows(data: bytes, path: Path, label: Optional[str]) -> Iterator[tuple[bool, Optional[dict]]]:
    fd, tmp = tempfile.mkstemp(suffix=".db")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
        try:
            if not kelivo.looks_like(con):
                raise SystemExit(f"{path.name}{UNKNOWN}")
            yield from kelivo.windows(con, label)
        finally:
            con.close()
    finally:
        os.unlink(tmp)


@dataclass
class SplitReport:
    kind: str = ""
    raw_path: Optional[Path] = None
    raw_new: bool = False
    rows: list = field(default_factory=list)  # (status, filename, window)
    shells: int = 0


def split(export: Path, data_dir: Path, account_label: Optional[str] = None,
          tz: str = store.DEFAULT_TZ) -> SplitReport:
    paths = store.Paths(data_dir)
    report = SplitReport()
    kind, data = read_export(export)
    report.kind = kind
    source = _kelivo_windows if kind == "kelivo" else _claude_windows
    windows = list(source(data, export, account_label))  # 先整份读完，格式不对就不动工作台

    report.raw_path, report.raw_new = archive_raw(data, paths.raw, kind)
    bench = paths.workbench
    bench.mkdir(parents=True, exist_ok=True)

    for shell, w in windows:
        if shell:
            report.shells += 1
            continue
        fname = store.window_filename(w, tz)
        existing = store.files_for_uuid(bench, w["uuid"])

        status = "new"
        if existing:
            prev = store.read_json(existing[0])
            if (prev.get("message_count") == w["message_count"]
                    and prev.get("last_message_at") == w["last_message_at"]
                    and prev.get("name") == w["name"]):
                report.rows.append(("unchanged", existing[0].name, w))
                continue
            if account_label is None and prev.get("account_label"):
                w["account_label"] = prev["account_label"]
            status = "updated"

        store.write_json(bench / fname, w)
        for p in existing:
            if p.name != fname:
                p.unlink()
        report.rows.append((status, fname, w))

    return report
