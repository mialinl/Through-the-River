"""数据目录的布局和读写小工具。

  data/raw/                导出原样存档
  data/workbench/          工作台：split 拆出来的窗口（从导出生成，会被新导出覆盖）
  data/workbench/edits.json  你在工作台上改的东西（编号、名字、说明、藏起来），按窗口 uuid 记
  data/windows/            发布区：原文 + 你的修改合成的完整版本，搜索和 MCP 只读这里
  data/river.db            从 windows/ 建的搜索索引，随时能删掉重建
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

DEFAULT_TZ = "Asia/Shanghai"


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def raw(self) -> Path:
        return self.root / "raw"

    @property
    def workbench(self) -> Path:
        return self.root / "workbench"

    @property
    def edits(self) -> Path:
        return self.workbench / "edits.json"

    @property
    def windows(self) -> Path:
        return self.root / "windows"

    @property
    def db(self) -> Path:
        return self.root / "river.db"


def write_bytes(dest: Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)


def write_json(dest: Path, obj: Any) -> None:
    write_bytes(dest, json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8"))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def window_files(folder: Path) -> list[Path]:
    """一个目录里的窗口文件（不含 edits.json 这类别的东西）。"""
    if not folder.exists():
        return []
    return sorted(p for p in folder.glob("*_*.json") if not p.name.endswith(".tmp"))


def files_for_uuid(folder: Path, uuid: str) -> list[Path]:
    return [p for p in folder.glob(f"*_{uuid[:8]}.json") if read_json(p).get("uuid") == uuid] if folder.exists() else []


def to_local(iso: Optional[str], tz: str = DEFAULT_TZ) -> Optional[datetime]:
    if not iso:
        return None
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt.astimezone(ZoneInfo(tz)) if ZoneInfo is not None else dt


def local_date(iso: Optional[str], tz: str = DEFAULT_TZ) -> str:
    dt = to_local(iso, tz)
    return dt.strftime("%Y-%m-%d") if dt else "0000-00-00"


def safe_name(name: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", (name or "").strip())
    return s.strip("_.")[:40] or "untitled"


def window_filename(w: dict, tz: str = DEFAULT_TZ) -> str:
    return f"{local_date(w.get('created_at'), tz)}_{safe_name(w.get('name', ''))}_{w['uuid'][:8]}.json"
