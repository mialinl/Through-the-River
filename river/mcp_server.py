"""River 的 MCP 服务：river_windows / river_search / river_read / river_tail。

两种跑法：
  python3 -m river serve --stdio      本机给 Claude Code / Claude Desktop 用，不需要鉴权
  python3 -m river serve              HTTP（/mcp），给 claude.ai 连接器用，带 OAuth 密码页

环境变量：
  RIVER_DATA         数据目录（Docker 里是 /data）
  RIVER_PUBLIC_URL   对外地址，比如 https://river.example.com（开 OAuth 必填）
  RIVER_PASSWORD     授权页密码（开 OAuth 必填）
  RIVER_HUMAN_NAME   原文里人类那一方显示成什么名字（默认「用户」）
  RIVER_TZ           显示时间用的时区（默认 Asia/Shanghai）
"""
from __future__ import annotations

import functools
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from . import index, store

INSTRUCTIONS = """对话原文全量库，一字未改。Ombre 是挑过的记忆，这里是原话，拿不准谁说了什么时来对质。
开新窗：先 breath，再 river_tail 接上一窗结尾。只搜正文，不含 thinking 和工具返回。"""


def _tz() -> str:
    return os.environ.get("RIVER_TZ", store.DEFAULT_TZ)


def _who(sender: str) -> str:
    if sender == "human":
        return os.environ.get("RIVER_HUMAN_NAME", "用户")
    if sender == "assistant":
        return "Claude"
    return sender or "?"


def _time(iso: Optional[str]) -> str:
    dt = store.to_local(iso, _tz())
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "?"


def _marks(tools: str, flags: str) -> str:
    out = ""
    if tools:
        out += f" [调用了 {tools.replace(',', '、')}]"
    if flags:
        out += f" [⚑ 这条被系统打了 {flags} 标记]"
    return out


def _render(rows, max_chars: int, focus: Optional[int] = None) -> str:
    parts, used = [], 0
    for r in rows:
        text = r["text"] or "（这条没有正文）"
        mark = " ◀ 命中的这条" if focus is not None and r["i"] == focus else ""
        head = f"#{r['i']} {_time(r['created_at'])} {_who(r['sender'])}{_marks(r['tools'] or '', r['flags'] or '')}{mark}"
        room = max_chars - used
        if len(text) > room and room <= 200 and parts:
            parts.append(f"……到字数上限了，从 #{r['i']} 往后没显示。调大 max_chars 或缩小范围再读。")
            break
        if len(text) > room:
            text = text[:room] + f"……（这条太长，截在这里，全文 {len(r['text'])} 字）"
        parts.append(f"{head}\n{text}")
        used += len(text)
    return "\n\n".join(parts)


def _paths() -> store.Paths:
    return store.Paths(Path(os.environ.get("RIVER_DATA", "data")).expanduser())


def _log_call(tool: str, args: dict, chars: int, ms: int, error: str = "") -> None:
    """每次工具调用记一行到 data/calls.log（重建容器不会丢），同时打到 docker logs。"""
    rec = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tool": tool,
           "args": args, "chars": chars, "ms": ms}
    if error:
        rec["error"] = error
    line = json.dumps(rec, ensure_ascii=False)
    print("river call " + line, file=sys.stderr, flush=True)
    try:
        with open(_paths().root / "calls.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _safe(fn):
    """索引层用 SystemExit 报「找不到」这类话，这里转成普通返回文本；顺便记账。"""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        start, out, err = time.monotonic(), "", ""
        try:
            out = fn(*a, **kw)
        except SystemExit as e:
            out = str(e)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            raise
        finally:
            _log_call(fn.__name__, kw, len(out), int((time.monotonic() - start) * 1000), err)
        return out
    return wrapper


def river_windows() -> str:
    """列出所有窗口：编号、名字、起止时间、条数、说明。"""
    rows = index.windows(_paths())
    if not rows:
        return "河里还没有窗口。"
    lines = []
    for w in rows:
        lines.append(f"{w['window'] or '-'}窗「{w['name']}」 {_time(w['created_at'])} → {_time(w['last_message_at'])}"
                     f"，{w['message_count']} 条（uuid {w['uuid'][:8]}）")
        if w["note"]:
            lines.append(f"    说明：{w['note']}")
    return "\n".join(lines)


def river_search(query: str, window: str = "", date_from: str = "", date_to: str = "",
                 who: str = "", limit: int = 8, newest_first: bool = False) -> str:
    """搜原文。query 空格隔开=同时出现；window 窗口编号；date_from/date_to 为 YYYY-MM-DD；
    who 为 human 或 assistant。返回 [窗 #条号]，接着用 river_read(window, at=条号) 看上下文。"""
    sender = {"human": "human", "user": "human", "assistant": "assistant", "claude": "assistant"}.get(
        who.strip().lower()) if who else None
    hits, total = index.search(_paths(), query, window=window or None, date_from=date_from or None,
                               date_to=date_to or None, sender=sender, limit=max(1, min(limit, 50)),
                               newest_first=newest_first, tz=_tz())
    if not hits:
        return f"没搜到「{query}」。换个说法、少几个字，或者去掉日期和窗口限制再试。"
    terms = query.split()
    lines = [f"[{h.window or '-'}窗 #{h.i}] {_time(h.created_at)} {_who(h.sender)}{_marks(h.tools, h.flags)}：{h.snippet(terms)}"
             for h in hits]
    more = f"，这里显示前 {len(hits)} 条" if total > len(hits) else ""
    return "\n".join(lines) + f"\n\n一共 {total} 条命中{more}。"


def river_read(window: str, at: int = -1, around: int = 3, max_chars: int = 3000) -> str:
    """逐字读原文：第 at 条前后各 around 条（at 不填从开头读），最多 max_chars 字。"""
    p = _paths()
    if at is None or at < 0:
        w, rows = index.read(p, window, at=around, around=around)
    else:
        w, rows = index.read(p, window, at=at, around=around)
    head = f"—— {w['window'] or '-'}窗「{w['name']}」——"
    if w["note"]:
        head += f"\n说明：{w['note']}"
    if not rows:
        return head + "\n这个范围里没有消息。"
    return head + "\n\n" + _render(rows, max(200, min(max_chars, 12000)), focus=at if at is not None and at >= 0 else None)


def river_tail(window: str = "", chars: int = 2000) -> str:
    """读一窗结尾约 chars 字（不填 window = 最近那一窗），开新窗接前情用。"""
    chars = max(200, min(chars, 8000))
    w, rows = index.tail_chars(_paths(), window or None, chars=chars)
    rows = [dict(r) for r in rows]
    # 最早那条可能很长：只留它的后半截，保证最新的几条完整
    newer = sum(len(r["text"] or "") for r in rows[1:])
    if rows and newer + len(rows[0]["text"] or "") > chars:
        keep = chars - newer
        if keep <= 0 and len(rows) > 1:
            rows = rows[1:]
        else:
            rows[0]["text"] = "……" + (rows[0]["text"] or "")[-max(keep, 1):]
    head = f"—— {w['window'] or '-'}窗「{w['name']}」的结尾 ——"
    if w["note"]:
        head += f"\n说明：{w['note']}"
    return head + "\n\n" + _render(rows, max_chars=chars + 10)


TOOLS = [river_windows, river_search, river_read, river_tail]


def build_server(host: str, port: int, auth: bool) -> FastMCP:
    # 无状态：不在内存里记会话，容器重启、隧道重连后第一次调用也不会因为旧会话失效而报错
    kwargs = dict(instructions=INSTRUCTIONS, host=host, port=port, stateless_http=True)
    provider = None
    if auth:
        from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions

        from .oauth import RiverOAuth

        public = os.environ["RIVER_PUBLIC_URL"].rstrip("/")
        provider = RiverOAuth(_paths().root / "oauth.json", os.environ["RIVER_PASSWORD"], public)
        kwargs.update(
            auth_server_provider=provider,
            auth=AuthSettings(
                issuer_url=public,
                resource_server_url=f"{public}/mcp",
                client_registration_options=ClientRegistrationOptions(enabled=True),
                revocation_options=RevocationOptions(enabled=True),
                validate_token_resource=False,
            ),
            # 走 Cloudflare 隧道时 Host 是你的域名；这里有 OAuth 挡着，不再按 Host 拦
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
    mcp = FastMCP("river", **kwargs)
    for fn in TOOLS:
        mcp.tool()(_safe(fn))

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request):
        return PlainTextResponse("ok")

    if provider is not None:
        mcp.custom_route("/login", methods=["GET", "POST"])(provider.login_page)
    return mcp


def serve(stdio: bool, host: str, port: int) -> None:
    if stdio:
        build_server(host, port, auth=False).run("stdio")
        return
    has_auth = bool(os.environ.get("RIVER_PASSWORD") and os.environ.get("RIVER_PUBLIC_URL"))
    loopback = host in ("127.0.0.1", "localhost", "::1")
    if not has_auth and not loopback:
        sys.exit("对外监听必须设 RIVER_PASSWORD 和 RIVER_PUBLIC_URL（开 OAuth），不然谁都能读你的原文。")
    if not has_auth:
        print(f"没开鉴权，只在本机 http://{host}:{port}/mcp 能连。", file=sys.stderr)
    build_server(host, port, auth=has_auth).run("streamable-http")
