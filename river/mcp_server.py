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
import os
import sys
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from . import index, store

INSTRUCTIONS = """River 是对话原文的全量库：每个窗口的原话，一个字没改过。
Ombre 记的是挑过、压过的记忆；拿不准当时到底谁说了什么、怎么说的，来这里翻原文对质。
开新窗时可以先 breath，再用 river_tail 读上一窗结尾，接上前情。
搜索只搜正文；thinking 和工具返回不在里面。"""


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
        if room <= 200 and parts:
            parts.append(f"……到字数上限了，从 #{r['i']} 往后没显示。调大 max_chars 或缩小范围再读。")
            break
        if len(text) > room:
            text = text[:room] + f"……（这条太长，截在这里，全文 {len(r['text'])} 字）"
        parts.append(f"{head}\n{text}")
        used += len(text)
    return "\n\n".join(parts)


def _paths() -> store.Paths:
    return store.Paths(Path(os.environ.get("RIVER_DATA", "data")).expanduser())


def _safe(fn):
    """索引层用 SystemExit 报「找不到」这类话，这里转成普通返回文本。"""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except SystemExit as e:
            return str(e)
    return wrapper


def river_windows() -> str:
    """列出河里所有窗口：编号、名字、起止时间、条数，以及写给这一窗的说明。

    想知道「那件事大概在哪一窗」时先看这个，再用 river_search 的 window 参数缩小范围。
    """
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
                 who: str = "", limit: int = 10, newest_first: bool = False) -> str:
    """按关键词搜原文，返回命中的那句和它在哪一窗第几条。

    query：关键词，几个词用空格隔开表示要同时出现。中文不用分词，直接写想找的那几个字。
    window：只搜某一窗（窗口编号，比如 "15"、"14.5"，或 uuid 开头几位）。
    date_from / date_to：日期范围，YYYY-MM-DD，两头都包含。
    who："human" 只看人类说的，"assistant" 只看 Claude 说的；空着就都看。
    limit：最多返回几条（默认 10，最多 50）。
    newest_first：新的在前（默认按时间从早到晚）。

    命中之后想看上下文，用 river_read(window=窗口编号, at=#后面的数字)。
    """
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


def river_read(window: str, at: int = -1, around: int = 5, max_chars: int = 8000) -> str:
    """读一窗里的一段原文，逐字返回，不摘要。

    window：窗口编号（"15"、"14.5"）或 uuid 开头几位。
    at：从第几条读起，读它前后各 around 条（搜索结果里 # 后面的数字）；不填就从这一窗开头读。
    around：前后各读几条（默认 5）。
    max_chars：这次最多返回多少字（默认 8000），超了会停下并告诉你从哪条接着读。
    """
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
    return head + "\n\n" + _render(rows, max_chars, focus=at if at is not None and at >= 0 else None)


def river_tail(window: str = "", chars: int = 3000) -> str:
    """读一窗的结尾，从最后一条往前取，够 chars 个字就停（按字数截，不按轮数）。

    不填 window 就读最后说过话的那一窗。开新窗时用它接上上一窗的前情。
    """
    w, rows = index.tail_chars(_paths(), window or None, chars=max(200, min(chars, 20000)))
    head = f"—— {w['window'] or '-'}窗「{w['name']}」的结尾 ——"
    if w["note"]:
        head += f"\n说明：{w['note']}"
    return head + "\n\n" + _render(rows, max_chars=max(chars, 200) * 2)


TOOLS = [river_windows, river_search, river_read, river_tail]


def build_server(host: str, port: int, auth: bool) -> FastMCP:
    kwargs = dict(instructions=INSTRUCTIONS, host=host, port=port)
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
