"""给 claude.ai 连接器用的最小 OAuth：动态注册 + 一个密码页。

只有一个人用，所以：
- 授权时跳到 /login，输对 RIVER_PASSWORD 就发授权码
- client、token 存在 data/oauth.json，重启不用重新授权
- 输错密码有冷却，一分钟最多试 5 次
"""
from __future__ import annotations

import asyncio
import hmac
import html
import secrets
import time
from pathlib import Path
from typing import Optional

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from . import store

ACCESS_TTL = 24 * 3600
REFRESH_TTL = 90 * 24 * 3600
CODE_TTL = 300
PENDING_TTL = 600


class RiverOAuth(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    def __init__(self, state_file: Path, password: str, public_url: str):
        self.file = state_file
        self.password = password
        self.public_url = public_url.rstrip("/")
        self.pending: dict[str, tuple[float, str, AuthorizationParams]] = {}
        self.codes: dict[str, AuthorizationCode] = {}
        self.failures: list[float] = []
        s = store.read_json(state_file) if state_file.exists() else {}
        self.clients: dict = s.get("clients", {})
        self.access: dict = s.get("access", {})
        self.refresh: dict = s.get("refresh", {})

    def _save(self) -> None:
        now = time.time()
        self.access = {k: v for k, v in self.access.items() if (v.get("expires_at") or now + 1) > now}
        self.refresh = {k: v for k, v in self.refresh.items() if (v.get("expires_at") or now + 1) > now}
        store.write_json(self.file, {"clients": self.clients, "access": self.access, "refresh": self.refresh})
        try:
            self.file.chmod(0o600)
        except OSError:
            pass

    # —— 客户端注册 ——
    async def get_client(self, client_id: str) -> Optional[OAuthClientInformationFull]:
        c = self.clients.get(client_id)
        return OAuthClientInformationFull.model_validate(c) if c else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self.clients[client_info.client_id] = client_info.model_dump(mode="json")
        self._save()

    # —— 授权：先跳到密码页 ——
    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        now = time.time()
        self.pending = {k: v for k, v in self.pending.items() if v[0] > now}
        req = secrets.token_urlsafe(24)
        self.pending[req] = (now + PENDING_TTL, client.client_id, params)
        return f"{self.public_url}/login?req={req}"

    async def login_page(self, request: Request) -> Response:
        req = request.query_params.get("req", "")
        if request.method == "GET":
            return self._page(req)

        form = await request.form()
        req = str(form.get("req", ""))
        entry = self.pending.get(req)
        if not entry or entry[0] < time.time():
            return self._page("", "这个授权请求过期了，回 Claude 重新连接一次。", status=400)

        now = time.time()
        self.failures = [t for t in self.failures if t > now - 60]
        if len(self.failures) >= 5:
            return self._page(req, "试太多次了，等一分钟再来。", status=429)
        if not hmac.compare_digest(str(form.get("password", "")).encode(), self.password.encode()):
            self.failures.append(now)
            await asyncio.sleep(1)
            return self._page(req, "密码不对。", status=401)

        del self.pending[req]
        _, client_id, params = entry
        code = secrets.token_urlsafe(32)
        self.codes[code] = AuthorizationCode(
            code=code, scopes=params.scopes or [], expires_at=now + CODE_TTL, client_id=client_id,
            code_challenge=params.code_challenge, redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )
        return RedirectResponse(construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state),
                                status_code=302)

    def _page(self, req: str, error: str = "", status: int = 200) -> HTMLResponse:
        err = f'<p class="err">{html.escape(error)}</p>' if error else ""
        form = "" if not req else f"""
<form method="post" action="/login">
  <input type="hidden" name="req" value="{html.escape(req)}">
  <input type="password" name="password" placeholder="密码" autofocus required>
  <button type="submit">连接</button>
</form>"""
        return HTMLResponse(f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>River</title>
<style>
body{{font-family:-apple-system,system-ui,sans-serif;background:#f4f1ea;color:#2b2a27;display:flex;
min-height:100vh;align-items:center;justify-content:center;margin:0;padding:16px}}
main{{max-width:320px;width:100%}} h1{{font-weight:500;font-size:22px;margin:0 0 4px}}
p{{color:#6b675f;margin:0 0 20px}} .err{{color:#b3452c}}
input,button{{font:inherit;width:100%;box-sizing:border-box;padding:10px 12px;border-radius:8px;
border:1px solid #cfc9bc;margin-bottom:10px;background:#fff}}
button{{background:#3d5a6c;color:#fff;border:none;cursor:pointer}}
@media (prefers-color-scheme:dark){{body{{background:#1d1f21;color:#e6e3dc}}p{{color:#a19d94}}
input{{background:#2a2c2f;color:#e6e3dc;border-color:#44474b}}}}
</style></head><body><main><h1>Through the River</h1><p>Claude 想连上这条河。</p>{err}{form}</main></body></html>""",
                            status_code=status)

    # —— 授权码 → token ——
    async def load_authorization_code(self, client, authorization_code: str) -> Optional[AuthorizationCode]:
        c = self.codes.get(authorization_code)
        if c and c.client_id == client.client_id and c.expires_at > time.time():
            return c
        return None

    async def exchange_authorization_code(self, client, authorization_code: AuthorizationCode) -> OAuthToken:
        self.codes.pop(authorization_code.code, None)
        return self._issue(client.client_id, authorization_code.scopes, authorization_code.resource)

    async def load_refresh_token(self, client, refresh_token: str) -> Optional[RefreshToken]:
        r = self.refresh.get(refresh_token)
        if not r or r["client_id"] != client.client_id:
            return None
        if r.get("expires_at") and r["expires_at"] < time.time():
            return None
        return RefreshToken(token=refresh_token, **r)

    async def exchange_refresh_token(self, client, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        if refresh_token.token not in self.refresh:
            raise TokenError("invalid_grant", "refresh token 已经用过了")
        del self.refresh[refresh_token.token]
        return self._issue(client.client_id, scopes or refresh_token.scopes, getattr(refresh_token, "resource", None))

    async def load_access_token(self, token: str) -> Optional[AccessToken]:
        a = self.access.get(token)
        if not a or (a.get("expires_at") and a["expires_at"] < time.time()):
            return None
        return AccessToken(token=token, **a)

    async def revoke_token(self, token) -> None:
        self.access.pop(token.token, None)
        self.refresh.pop(token.token, None)
        self._save()

    def _issue(self, client_id: str, scopes: list[str], resource: Optional[str]) -> OAuthToken:
        now = int(time.time())
        at, rt = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.access[at] = {"client_id": client_id, "scopes": scopes, "expires_at": now + ACCESS_TTL,
                           "resource": resource}
        self.refresh[rt] = {"client_id": client_id, "scopes": scopes, "expires_at": now + REFRESH_TTL,
                            "resource": resource}
        self._save()
        return OAuthToken(access_token=at, token_type="Bearer", expires_in=ACCESS_TTL, refresh_token=rt,
                          scope=" ".join(scopes) if scopes else None)

