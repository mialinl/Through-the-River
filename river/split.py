"""把整个账号的导出拆成一窗一个 json。

  data/raw/       导出原样存档（按内容哈希去重，同一份导两次只存一次）
  data/windows/   一窗一个文件，文件名：本地日期_窗口名_uuid前8位.json

windows/ 是准的，之后的索引都从这里建。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from .sources import claude_export

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore


def read_export(path: Path) -> bytes:
    """接受 conversations.json，或者官方导出的整个 zip。"""
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.rsplit("/", 1)[-1] == "conversations.json"]
            if not names:
                raise SystemExit(f"{path} 里没有 conversations.json")
            return z.read(names[0])
    return path.read_bytes()


def archive_raw(data: bytes, raw_dir: Path) -> tuple[Path, bool]:
    digest = hashlib.sha256(data).hexdigest()[:12]
    raw_dir.mkdir(parents=True, exist_ok=True)
    for p in raw_dir.glob(f"*-{digest}.json"):
        return p, False
    stamp = datetime.now().strftime("%Y%m%d")
    dest = raw_dir / f"claude-export-{stamp}-{digest}.json"
    _write_bytes(dest, data)
    return dest, True


def _write_bytes(dest: Path, data: bytes) -> None:
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)


def local_date(iso: Optional[str], tz: str) -> str:
    if not iso:
        return "0000-00-00"
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if ZoneInfo is not None:
        dt = dt.astimezone(ZoneInfo(tz))
    return dt.strftime("%Y-%m-%d")


def safe_name(name: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", name.strip())
    return s.strip("_.")[:40] or "untitled"


def window_filename(w: dict, tz: str) -> str:
    return f"{local_date(w['created_at'], tz)}_{safe_name(w['name'])}_{w['uuid'][:8]}.json"


@dataclass
class SplitReport:
    raw_path: Optional[Path] = None
    raw_new: bool = False
    rows: list = field(default_factory=list)  # (status, filename, window)
    shells: int = 0


def split(export: Path, data_dir: Path, account_label: Optional[str] = None,
          tz: str = "Asia/Shanghai") -> SplitReport:
    report = SplitReport()
    data = read_export(export)
    convs = json.loads(data)
    if not isinstance(convs, list):
        raise SystemExit("看不懂这个文件：conversations.json 应该是一个列表")

    report.raw_path, report.raw_new = archive_raw(data, data_dir / "raw")
    win_dir = data_dir / "windows"
    win_dir.mkdir(parents=True, exist_ok=True)

    for conv in sorted(convs, key=lambda c: c.get("created_at") or ""):
        if claude_export.is_shell(conv):
            report.shells += 1
            continue
        w = claude_export.to_window(conv, account_label)
        fname = window_filename(w, tz)
        dest = win_dir / fname

        # 同一个窗口按 uuid 认，改过名就把旧文件换掉
        old = [p for p in win_dir.glob(f"*_{w['uuid'][:8]}.json") if p.name != fname]
        existing = dest if dest.exists() else (old[0] if old else None)

        status = "new"
        if existing is not None:
            prev = json.loads(existing.read_text(encoding="utf-8"))
            if prev.get("uuid") == w["uuid"]:
                if (prev.get("message_count") == w["message_count"]
                        and prev.get("last_message_at") == w["last_message_at"]
                        and prev.get("name") == w["name"]):
                    report.rows.append(("unchanged", existing.name, w))
                    continue
                if account_label is None and prev.get("account_label"):
                    w["account_label"] = prev["account_label"]
                status = "updated"

        payload = json.dumps(w, ensure_ascii=False, indent=1).encode("utf-8")
        _write_bytes(dest, payload)
        for p in old:
            if json.loads(p.read_text(encoding="utf-8")).get("uuid") == w["uuid"]:
                p.unlink()
        report.rows.append((status, fname, w))

    return report
