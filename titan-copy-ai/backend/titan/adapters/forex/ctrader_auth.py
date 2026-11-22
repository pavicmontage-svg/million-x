"""cTrader Open API OAuth helper: authorisation link, code -> token exchange, token refresh, account list.

Endpoints (help.ctrader.com/open-api/account-authentication):
  authorise  https://id.ctrader.com/my/settings/openapi/grantingaccess/?client_id=&redirect_uri=&scope=trading&product=web
  token      GET https://openapi.ctrader.com/apps/token  (grant_type=authorization_code | refresh_token)
             -> {accessToken, tokenType, expiresIn (~30 days), refreshToken}
  accounts   ProtoOAApplicationAuthReq(2100) then ProtoOAGetAccountListByAccessTokenReq(2149)
             -> ProtoOACtidTraderAccount {ctidTraderAccountId, isLive, traderLogin, brokerTitleShort}

The authorisation code is valid for about one minute, so the exchange happens as soon as the browser
returns (callback) or the user pastes the address shown by the browser.
"""
from __future__ import annotations

import asyncio
import json
import time
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from ..base import AdapterError, AdapterUnavailable

AUTH_URL = "https://id.ctrader.com/my/settings/openapi/grantingaccess/"
TOKEN_URL = "https://openapi.ctrader.com/apps/token"
WS = {"demo": "wss://demo.ctraderapi.com:5036", "live": "wss://live.ctraderapi.com:5036"}


def authorize_url(client_id: str, redirect_uri: str, state: str | None = None) -> str:
    q = {"client_id": client_id, "redirect_uri": redirect_uri, "scope": "trading", "product": "web"}
    if state:
        q["state"] = state
    return AUTH_URL + "?" + urlencode(q)


def code_from(text: str) -> str:
    """Accepts the raw code or the full address the browser was redirected to."""
    text = text.strip()
    if "code=" in text:
        q = parse_qs(urlparse(text).query or text.split("?", 1)[-1])
        if q.get("code"):
            return q["code"][0]
    return text


def _token(params: dict, http: httpx.Client | None = None) -> dict:
    c = http or httpx.Client(timeout=20)
    try:
        r = c.get(TOKEN_URL, params=params)
    except httpx.HTTPError as e:
        raise AdapterUnavailable(f"cTrader token service unreachable: {e}") from e
    try:
        d = r.json()
    except json.JSONDecodeError as e:
        raise AdapterError(f"cTrader token service HTTP {r.status_code}: {r.text[:200]}") from e
    if r.status_code >= 400 or d.get("errorCode") or not d.get("accessToken"):
        raise AdapterError(f"cTrader token error: {d.get('errorCode') or r.status_code} {d.get('description') or ''}".strip())
    return {"access_token": d["accessToken"], "refresh_token": d.get("refreshToken"),
            "expires_at": time.time() + float(d.get("expiresIn") or 2_628_000)}


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str, http=None) -> dict:
    return _token({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
                   "client_id": client_id, "client_secret": client_secret}, http)


def refresh(client_id: str, client_secret: str, refresh_token: str, http=None) -> dict:
    return _token({"grant_type": "refresh_token", "refresh_token": refresh_token,
                   "client_id": client_id, "client_secret": client_secret}, http)


async def _accounts(client_id: str, client_secret: str, access_token: str, env: str) -> list[dict]:
    import websockets

    async with websockets.connect(WS[env], ping_interval=None, open_timeout=15) as ws:
        async def call(pt: int, payload: dict, expect: int) -> dict:
            await ws.send(json.dumps({"clientMsgId": f"a{pt}", "payloadType": pt, "payload": payload}))
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), 15))
                if msg.get("payloadType") in (50, 2142):
                    p = msg.get("payload", {})
                    raise AdapterError(f"cTrader {p.get('errorCode')}: {p.get('description')}")
                if msg.get("payloadType") == expect:
                    return msg.get("payload", {})

        await call(2100, {"clientId": client_id, "clientSecret": client_secret}, 2101)
        res = await call(2149, {"accessToken": access_token}, 2150)
    return [{"account_id": int(a["ctidTraderAccountId"]), "is_live": bool(a.get("isLive")),
             "trader_login": a.get("traderLogin"), "broker": a.get("brokerTitleShort")}
            for a in res.get("ctidTraderAccount", [])]


def list_accounts(client_id: str, client_secret: str, access_token: str, env: str = "demo") -> list[dict]:
    try:
        return asyncio.run(_accounts(client_id, client_secret, access_token, env))
    except (AdapterError, AdapterUnavailable):
        raise
    except Exception as e:
        raise AdapterUnavailable(f"cTrader Open API unreachable: {type(e).__name__}: {e}") from e


def resolve_account_id(cred: dict, env: str) -> int:
    """`account_id` may be the internal ctidTraderAccountId or the login number shown in cTrader;
    when it is empty and the token covers a single account of this environment, that account is used."""
    given = str(cred.get("account_id") or "").strip()
    accounts = list_accounts(cred["client_id"], cred["client_secret"], cred["access_token"], env)
    want_live = env == "live"
    for a in accounts:
        if given and given in (str(a["account_id"]), str(a["trader_login"])):
            return a["account_id"]
    same_env = [a for a in accounts if a["is_live"] == want_live]
    if not given and len(same_env) == 1:
        return same_env[0]["account_id"]
    shown = ", ".join(f"{a['trader_login']} ({'live' if a['is_live'] else 'demo'}, {a['broker']})" for a in accounts)
    raise AdapterError(f"account {given or '(empty)'} not found for this token; available: {shown or 'none'}")
