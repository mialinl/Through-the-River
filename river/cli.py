from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .split import split


def _default_data_dir() -> Path:
    return Path(os.environ.get("RIVER_DATA", Path(__file__).resolve().parent.parent / "data"))


def cmd_split(args: argparse.Namespace) -> None:
    export = Path(args.export).expanduser()
    if not export.exists():
        # 在 ~/ 后面又拖进了完整路径，会变成 /Users/名字/Users/名字/...
        home = str(Path.home())
        doubled = str(export)
        if doubled.startswith(home + home):
            export = Path(doubled[len(home):])
    if not export.exists():
        sys.exit(f"找不到文件：{export}")
    data_dir = Path(args.data).expanduser() if args.data else _default_data_dir()
    r = split(export, data_dir, account_label=args.label, tz=args.tz)

    print(f"原文存档：{r.raw_path}{'' if r.raw_new else '（之前存过，没有重复存）'}")
    print(f"窗口目录：{data_dir / 'windows'}\n")
    if r.rows:
        print(f"{'状态':<10}{'窗口':<6}{'条数':>6}{'去掉重roll':>10}  文件")
        for status, fname, w in r.rows:
            label = {"new": "新增", "updated": "更新", "unchanged": "没变"}[status]
            print(f"{label:<10}{(w['window'] or '-'):<6}{w['message_count']:>6}{w['dropped_rerolls']:>10}  {fname}")
    counts = {s: sum(1 for row in r.rows if row[0] == s) for s in ("new", "updated", "unchanged")}
    print(f"\n新增 {counts['new']} 窗，更新 {counts['updated']} 窗，没变 {counts['unchanged']} 窗；"
          f"跳过空壳（删掉的窗口）{r.shells} 个。")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="river", description="Through the River 全量记忆库")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("split", help="把 claude.ai 导出拆成一窗一个 json")
    s.add_argument("export", help="conversations.json，或者官方导出的 zip")
    s.add_argument("--data", help="数据目录（默认是仓库里的 data/，也可以用环境变量 RIVER_DATA）")
    s.add_argument("--label", help="给这份导出的账号起个名字，比如 主号、旧号")
    s.add_argument("--tz", default="Asia/Shanghai", help="文件名里的日期按哪个时区算（默认 Asia/Shanghai）")
    s.set_defaults(func=cmd_split)

    args = p.parse_args(argv)
    args.func(args)
