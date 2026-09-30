"""把整个账号的导出拆成一窗一个 json，放到工作台。

同一个窗口靠 uuid 认：没变的跳过，聊多了的换成新版本，改过名的不留两份。
导出里没有了的窗口（在 app 里删掉的）不动，工作台里照样留着。
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import store
from .sources import claude_export


def read_export(path: Path) -> bytes:
    """接受官方导出的整个 zip、解压出来的文件夹，或者里面的 conversations.json。"""
    if path.is_dir():
        found = sorted(path.rglob("conversations.json"))
        if not found:
            raise SystemExit(f"{path} 这个文件夹里没找到 conversations.json")
        return found[0].read_bytes()
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.rsplit("/", 1)[-1] == "conversations.json"]
            if not names:
                raise SystemExit(f"{path} 里没有 conversations.json")
            return z.read(names[0])
    return path.read_bytes()


def parse(data: bytes, path: Path) -> list:
    """认格式：现在认得 claude.ai 官方导出，认不出来就停下，不乱导。"""
    try:
        obj = json.loads(data)
    except ValueError:
        raise SystemExit(f"{path.name} 不是 json 文件，看不懂。发给 CC 看看是什么格式。")
    if claude_export.looks_like(obj):
        return obj
    raise SystemExit(f"{path.name} 的格式还不认识（不是 claude.ai 的导出）。发给 CC 看看结构，加一个对应的导入。")


def archive_raw(data: bytes, raw_dir: Path) -> tuple[Path, bool]:
    digest = hashlib.sha256(data).hexdigest()[:12]
    if raw_dir.exists():
        for p in raw_dir.glob(f"*-{digest}.json"):
            return p, False
    dest = raw_dir / f"claude-export-{datetime.now().strftime('%Y%m%d')}-{digest}.json"
    store.write_bytes(dest, data)
    return dest, True


@dataclass
class SplitReport:
    raw_path: Optional[Path] = None
    raw_new: bool = False
    rows: list = field(default_factory=list)  # (status, filename, window)
    shells: int = 0


def split(export: Path, data_dir: Path, account_label: Optional[str] = None,
          tz: str = store.DEFAULT_TZ) -> SplitReport:
    paths = store.Paths(data_dir)
    report = SplitReport()
    data = read_export(export)
    convs = parse(data, export)

    report.raw_path, report.raw_new = archive_raw(data, paths.raw)
    bench = paths.workbench
    bench.mkdir(parents=True, exist_ok=True)

    for conv in sorted(convs, key=lambda c: c.get("created_at") or ""):
        if claude_export.is_shell(conv):
            report.shells += 1
            continue
        w = claude_export.to_window(conv, account_label)
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
