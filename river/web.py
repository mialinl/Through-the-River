"""本地网页：python3 -m river web

只监听 127.0.0.1，只在这台 Mac 上能打开。页面里带一个每次启动随机生成的口令，
改东西的请求必须带着它，别的网页没法偷偷替你改。只用 Python 自带的库。
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

from . import index, store, workbench
from .split import import_file

HTML = Path(__file__).with_name("webui.html")
MAX_UPLOAD = 1024 * 1024 * 1024  # 1GB


def _env_file_value(key: str) -> Optional[str]:
    """从仓库根目录的 .env 读一个值（网页不在 Docker 里跑，读不到容器的环境变量）。"""
    env = Path(__file__).resolve().parent.parent / ".env"
    if not env.exists():
        return None
    for line in env.read_text(encoding="utf-8").splitlines():
        k, _, v = line.partition("=")
        if k.strip() == key and v.strip():
            return v.strip()
    return None


class Cache:
    """按修改时间缓存 json 文件，窗口文件很大，不必每次重读。"""

    def __init__(self):
        self.data: dict = {}
        self.lock = threading.Lock()

    def load(self, path: Path) -> dict:
        mtime = path.stat().st_mtime_ns
        with self.lock:
            hit = self.data.get(path)
            if hit and hit[0] == mtime:
                return hit[1]
        obj = store.read_json(path)
        with self.lock:
            self.data[path] = (mtime, obj)
        return obj


class App:
    def __init__(self, paths: store.Paths, tz: str, human: str):
        self.paths = paths
        self.tz = tz
        self.human = human
        self.token = secrets.token_urlsafe(24)
        self.cache = Cache()
        self.write_lock = threading.Lock()

    # —— 读 ——
    def _time(self, iso: Optional[str]) -> str:
        dt = store.to_local(iso, self.tz)
        return dt.strftime("%Y-%m-%d %H:%M") if dt else ""

    def _bench(self) -> dict:
        out = {}
        for p in store.window_files(self.paths.workbench):
            w = self.cache.load(p)
            out[w["uuid"]] = w
        return out

    def _published_meta(self) -> dict:
        out = {}
        for p in store.window_files(self.paths.windows):
            w = self.cache.load(p)
            out[w["uuid"]] = {k: v for k, v in w.items() if k not in ("messages", "published_at")}
        return out

    def windows(self) -> list:
        edits = workbench.load_edits(self.paths)
        published = self._published_meta()
        rows = []
        for uuid, w in self._bench().items():
            e = edits.get(uuid, {})
            meta = {k: v for k, v in workbench.merged(w, e).items() if k != "messages"}
            if e.get("hidden"):
                state = "hidden"
            elif uuid not in published:
                state = "unpublished"
            elif published[uuid] != meta:
                state = "changed"
            else:
                state = "published"
            msgs = w["messages"]
            rows.append({
                "uuid": uuid, "window": meta.get("window"), "name": meta.get("name"),
                "original_name": meta.get("original_name"), "note": meta.get("note", ""),
                "source": meta.get("source"), "account_label": meta.get("account_label"),
                "message_count": meta.get("message_count"), "dropped_rerolls": meta.get("dropped_rerolls"),
                "first_at": self._time(msgs[0]["created_at"]) if msgs else "",
                "last_at": self._time(meta.get("last_message_at")),
                "state": state, "_sort": workbench.sort_key(meta),
            })
        rows.sort(key=lambda r: r.pop("_sort"))
        return rows

    def messages(self, uuid: str, offset: int, limit: int) -> dict:
        w = self._bench().get(uuid)
        if w is None:
            raise LookupError("工作台里没有这个窗口")
        msgs = w["messages"]
        offset = max(0, min(offset, len(msgs)))
        out = []
        for m in msgs[offset:offset + limit]:
            blocks = m.get("blocks", [])
            out.append({
                "i": m["i"],
                "who": "human" if m.get("sender") == "human" else "assistant",
                "time": self._time(m.get("created_at")),
                "text": m.get("text", ""),
                "thinking": [b.get("text", "") for b in blocks if b.get("type") == "thinking" and b.get("text")],
                "tools": [b.get("name") or "?" for b in blocks if b.get("type") == "tool_use"],
                "flags": m.get("flags", []),
                "files": m.get("files", []) + [a.get("file_name") for a in m.get("attachments", [])],
                "model": m.get("model"),
            })
        return {"total": len(msgs), "offset": offset, "messages": out}

    def search(self, q: dict) -> dict:
        query = (q.get("q") or "").strip()
        if not query:
            return {"total": 0, "hits": []}
        if not self.paths.db.exists():
            raise LookupError("还没有索引，先发布一次")
        sender = {"human": "human", "assistant": "assistant"}.get(q.get("who") or "")
        hits, total = index.search(self.paths, query, window=q.get("window") or None,
                                   date_from=q.get("from") or None, date_to=q.get("to") or None,
                                   sender=sender, limit=min(int(q.get("limit") or 50), 200), tz=self.tz)
        terms = query.split()
        return {"total": total, "hits": [{
            "uuid": h.window_uuid, "window": h.window, "name": h.window_name, "i": h.i,
            "who": "human" if h.sender == "human" else "assistant", "time": self._time(h.created_at),
            "snippet": h.snippet(terms, width=60),
        } for h in hits]}

    # —— 写 ——
    def _publish(self) -> dict:
        rows = workbench.publish(self.paths)
        n_win, n_msg, _ = index.build(self.paths)
        changed = [s for s, _ in rows if s not in ("unchanged", "hidden-already")]
        return {"changed": len(changed), "windows": n_win, "messages": n_msg}

    def edit(self, body: dict) -> dict:
        with self.write_lock:
            changes = {k: body[k] for k in ("window", "name", "note", "hidden") if k in body}
            workbench.edit(self.paths, body["uuid"], **changes)
        return {"ok": True}

    def publish(self) -> dict:
        with self.write_lock:
            return self._publish()

    def drop(self, body: dict) -> dict:
        with self.write_lock:
            bench = self._bench()
            targets = [bench[u] for u in body.get("uuids", []) if u in bench]
            workbench.drop(self.paths, targets)
            result = self._publish()
        return {"dropped": len(targets), **result}

    def import_upload(self, filename: str, rfile, length: int, label: Optional[str]) -> dict:
        suffix = Path(filename).suffix.lower() or ".json"
        with self.write_lock:
            tmpdir = tempfile.mkdtemp()
            try:
                dest = Path(tmpdir) / f"upload{suffix}"
                with open(dest, "wb") as f:
                    remaining = length
                    while remaining > 0:
                        chunk = rfile.read(min(remaining, 1 << 20))
                        if not chunk:
                            break
                        f.write(chunk)
                        remaining -= len(chunk)
                r, held = import_file(dest, self.paths.root, account_label=label or None, tz=self.tz)
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)
            result = self._publish()
        counts = {s: sum(1 for row in r.rows if row[0] == s) for s in ("new", "updated", "unchanged")}
        return {"kind": r.kind, **counts, "shells": r.shells, "skipped_dropped": r.dropped,
                "held": [w.get("name") for w in held], **result}


def make_handler(app: App, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # 安静一点
            pass

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, status: int = 200) -> None:
            self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _guard(self, write: bool) -> bool:
            if self.headers.get("Host") not in allowed_hosts:
                self._json({"error": "只能在本机打开"}, 403)
                return False
            if write and self.headers.get("X-River-Token") != app.token:
                self._json({"error": "口令不对，刷新一下页面再试"}, 403)
                return False
            return True

        def do_GET(self):
            if not self._guard(write=False):
                return
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path == "/":
                    page = HTML.read_text(encoding="utf-8")
                    page = page.replace("__RIVER_TOKEN__", app.token).replace(
                        "__RIVER_HUMAN__", json.dumps(app.human, ensure_ascii=False)[1:-1])
                    self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
                elif url.path == "/api/windows":
                    self._json(app.windows())
                elif url.path == "/api/messages":
                    self._json(app.messages(q["uuid"], int(q.get("offset", 0)), min(int(q.get("limit", 60)), 300)))
                elif url.path == "/api/search":
                    self._json(app.search(q))
                else:
                    self._json({"error": "没有这个地址"}, 404)
            except (LookupError, SystemExit, ValueError) as e:
                self._json({"error": str(e)}, 400)

        def do_POST(self):
            if not self._guard(write=True):
                return
            url = urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            try:
                if url.path == "/api/import":
                    if length <= 0 or length > MAX_UPLOAD:
                        raise ValueError("文件是空的或者太大了")
                    name = unquote(self.headers.get("X-Filename") or "upload.json")
                    label = unquote(self.headers.get("X-Label") or "")
                    self._json(app.import_upload(name, self.rfile, length, label))
                    return
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
                if url.path == "/api/edit":
                    self._json(app.edit(body))
                elif url.path == "/api/publish":
                    self._json(app.publish())
                elif url.path == "/api/drop":
                    self._json(app.drop(body))
                else:
                    self._json({"error": "没有这个地址"}, 404)
            except (LookupError, SystemExit, ValueError, KeyError) as e:
                self._json({"error": str(e)}, 400)

    return Handler


def serve(paths: store.Paths, port: int = 18003, open_browser: bool = True,
          tz: str = store.DEFAULT_TZ) -> None:
    human = os.environ.get("RIVER_HUMAN_NAME") or _env_file_value("RIVER_HUMAN_NAME") or "你"
    app = App(paths, tz, human)
    server = None
    for p in range(port, port + 10):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", p), make_handler(app, p))
            port = p
            break
        except OSError:
            continue
    if server is None:
        raise SystemExit(f"端口 {port} 到 {port + 9} 都被占了。")
    url = f"http://localhost:{port}/"
    print(f"River 网页开好了：{url}\n关掉的话在这个终端按 control + C。")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n关掉了。")
    finally:
        server.server_close()
