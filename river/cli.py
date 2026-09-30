from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import index, store, workbench
from .split import split

WHO = {"human": "你", "assistant": "Claude"}
WHO_ARG = {"你": "human", "me": "human", "human": "human", "claude": "assistant", "Claude": "assistant",
           "assistant": "assistant"}


def _paths(args: argparse.Namespace) -> store.Paths:
    root = Path(args.data).expanduser() if args.data else Path(
        os.environ.get("RIVER_DATA", Path(__file__).resolve().parent.parent / "data"))
    return store.Paths(root)


def _fmt_time(iso: str) -> str:
    dt = store.to_local(iso)
    return dt.strftime("%m-%d %H:%M") if dt else "?"


def _publish_and_index(paths: store.Paths) -> None:
    rows = workbench.publish(paths)
    label = {"new": "发布", "updated": "更新", "unchanged": "没变", "hidden": "撤下（藏起来了）",
             "hidden-already": "藏着"}
    changed = [(s, m) for s, m in rows if s not in ("unchanged", "hidden-already")]
    for s, m in changed:
        print(f"  {label[s]}  {m.get('window') or '-':<6}{m.get('name')}")
    if not changed:
        print("  发布区没有要变的。")
    n_win, n_msg, has_fts = index.build(paths)
    note = "" if has_fts else "（这台机器的 SQLite 太老，没有中文全文索引，搜索会慢一点）"
    print(f"索引已更新：{n_win} 窗，{n_msg} 条消息。{note}")


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
    paths = _paths(args)
    r = split(export, paths.root, account_label=args.label, tz=args.tz)
    if args.hide_new:
        for status, _, w in r.rows:
            if status == "new":
                workbench.edit(paths, w["uuid"], hidden=True)

    print(f"原文存档：{r.raw_path}{'' if r.raw_new else '（之前存过，没有重复存）'}")
    print(f"工作台：{paths.workbench}\n")
    if r.rows:
        print(f"{'状态':<6}{'窗口':<6}{'条数':>6}{'去掉重roll':>10}  文件")
        for status, fname, w in r.rows:
            label = {"new": "新增", "updated": "更新", "unchanged": "没变"}[status]
            print(f"{label:<6}{(w['window'] or '-'):<6}{w['message_count']:>6}{w['dropped_rerolls']:>10}  {fname}")
    counts = {s: sum(1 for row in r.rows if row[0] == s) for s in ("new", "updated", "unchanged")}
    print(f"\n新增 {counts['new']} 窗，更新 {counts['updated']} 窗，没变 {counts['unchanged']} 窗；"
          f"跳过空壳（删掉的窗口）{r.shells} 个。")
    if args.hide_new and counts["new"]:
        print(f"\n新增的 {counts['new']} 窗先藏着了。python3 -m river list 看看，"
              "想要的用 python3 -m river edit 编号或uuid --show 放出来，再 publish。")
    if args.no_publish:
        print("\n这次只放到了工作台。整理好了跑 python3 -m river publish 发布。")
        return
    print("\n发布：")
    _publish_and_index(paths)


def cmd_list(args: argparse.Namespace) -> None:
    rows = workbench.listing(_paths(args))
    if not rows:
        print("工作台是空的。先跑 python3 -m river split 导出文件.zip")
        return
    state = {"yes": "已发布", "no": "未发布", "stale": "有改动未发布"}
    for m in rows:
        flag = "藏着" if m["hidden"] else state[m["published"]]
        span = f"{_fmt_time(m['messages'][0]['created_at'])} → {_fmt_time(m['last_message_at'])}" if m["messages"] else ""
        print(f"{m.get('window') or '-':<6}{m.get('name'):<20}{m['message_count']:>6} 条  {span}  "
              f"[{flag}]  uuid {m['uuid'][:8]}")
        if m.get("note"):
            print(f"      说明：{m['note']}")


def cmd_edit(args: argparse.Namespace) -> None:
    paths = _paths(args)
    if len(args.window) > 1 and (args.number or args.name or args.note is not None):
        sys.exit("编号、名字、说明一次只能改一窗；一次改好几窗只能用 --hide / --show / --label。")
    hidden = True if args.hide else (False if args.show else None)
    for ref in args.window:
        m = workbench.edit(paths, ref, window=args.number, name=args.name, note=args.note,
                           hidden=hidden, account_label=args.label)
        state = "（藏着）" if hidden else ""
        print(f"改好了：{m.get('window') or '-'}  {m.get('name')}{state}")
        if m.get("note") and len(args.window) == 1:
            print(f"说明：{m['note']}")
    print("还在工作台上。确认好了跑 python3 -m river publish 发布。")


def cmd_publish(args: argparse.Namespace) -> None:
    _publish_and_index(_paths(args))


def cmd_search(args: argparse.Namespace) -> None:
    query = " ".join(args.query)
    sender = WHO_ARG.get(args.who) if args.who else None
    if args.who and not sender:
        sys.exit("--who 只能是 你 或 claude")
    hits, total = index.search(_paths(args), query, window=args.window, date_from=args.date_from,
                               date_to=args.date_to, sender=sender, limit=args.limit,
                               newest_first=args.newest)
    if not hits:
        print("没搜到。")
        return
    terms = query.split()
    for h in hits:
        print(f"[{h.window or '-'}窗 #{h.i}] {_fmt_time(h.created_at)} {WHO.get(h.sender, h.sender)}：{h.snippet(terms)}")
    more = f"，只显示了 {len(hits)} 条（--limit 调多一点）" if total > len(hits) else ""
    print(f"\n一共 {total} 条{more}。看上下文：python3 -m river read 窗口编号 --at #后面的数字")


def cmd_read(args: argparse.Namespace) -> None:
    tail = args.tail is not None
    w, rows = index.read(_paths(args), args.window, at=args.at,
                         around=args.tail if tail else args.around, tail=tail)
    print(f"—— {w['window'] or '-'}窗 {w['name']} ——")
    if w["note"]:
        print(f"说明：{w['note']}")
    for r in rows:
        mark = " ◀" if args.at is not None and r["i"] == args.at else ""
        print(f"\n#{r['i']} {_fmt_time(r['created_at'])} {WHO.get(r['sender'], r['sender'])}{mark}\n{r['text']}")


def cmd_serve(args: argparse.Namespace) -> None:
    try:
        from .mcp_server import serve
    except ImportError:
        sys.exit("MCP 服务要先装依赖：python3 -m pip install -r requirements.txt（需要 Python 3.10 以上）")
    os.environ["RIVER_DATA"] = str(_paths(args).root)
    serve(stdio=args.stdio, host=args.host, port=args.port)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="river", description="Through the River 全量记忆库")
    p.add_argument("--data", help="数据目录（默认是仓库里的 data/，也可以用环境变量 RIVER_DATA）")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("split", help="把 claude.ai 导出拆到工作台，然后发布")
    s.add_argument("export", help="conversations.json，或者官方导出的 zip")
    s.add_argument("--label", help="给这份导出的账号起个名字，比如 主号、旧号")
    s.add_argument("--tz", default=store.DEFAULT_TZ, help="文件名里的日期按哪个时区算")
    s.add_argument("--no-publish", action="store_true", help="只放到工作台，先不发布")
    s.add_argument("--hide-new", action="store_true", help="这次新增的窗口先全部藏着，想要哪窗再放出来")
    s.set_defaults(func=cmd_split)

    s = sub.add_parser("list", help="看工作台里有哪些窗口")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("edit", help="改一窗的编号、名字、说明，或者藏起来")
    s.add_argument("window", nargs="+", help="窗口编号（15、14.5）或 uuid 开头几位，藏/放可以一次写好几个")
    s.add_argument("--number", help="改窗口编号")
    s.add_argument("--name", help="改显示名字")
    s.add_argument("--note", help="写这一窗的说明（传空字符串 \"\" 就是删掉）")
    s.add_argument("--label", help="改账号名字")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--hide", action="store_true", help="藏起来，不发布")
    g.add_argument("--show", action="store_true", help="取消隐藏")
    s.set_defaults(func=cmd_edit)

    s = sub.add_parser("publish", help="把工作台发布到 windows/ 并更新索引")
    s.set_defaults(func=cmd_publish)

    s = sub.add_parser("search", help="搜原文")
    s.add_argument("query", nargs="+", help="关键词，几个词用空格隔开就是要同时出现")
    s.add_argument("--window", help="只搜某一窗")
    s.add_argument("--from", dest="date_from", help="从哪天开始，比如 2026-09-01")
    s.add_argument("--to", dest="date_to", help="到哪天为止（包含这天）")
    s.add_argument("--who", help="只看谁说的：你 / claude")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--newest", action="store_true", help="新的在前")
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("read", help="读一窗里的一段原文")
    s.add_argument("window", help="窗口编号或 uuid 开头几位")
    s.add_argument("--at", type=int, help="从第几条附近开始读（搜索结果里 # 后面的数字）")
    s.add_argument("--around", type=int, default=5, help="前后各读几条（默认 5）")
    s.add_argument("--tail", type=int, help="读最后几条")
    s.set_defaults(func=cmd_read)

    s = sub.add_parser("serve", help="启动 MCP 服务")
    s.add_argument("--stdio", action="store_true", help="用 stdio（本机 Claude Code / Desktop）")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=18002)
    s.set_defaults(func=cmd_serve)

    args = p.parse_args(argv)
    args.func(args)
