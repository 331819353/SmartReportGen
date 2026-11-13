"""海尔 IAM 单点登录（默认认证方式）。

IAM 不是标准 OIDC，接口约定（与 report_generator_project 的 iam-idp 一致）：
  浏览器登录  GET  {HOST}/oauth/authorize?client_id&redirect_uri&response_type=code&scope&state
  换票        POST {HOST}/api/oauth/token/code   JSON {client_id, client_secret, code}，头 Application-Key: client_id
              （/oauth/token 是 IAM 前端页面路由，POST 会得到 405）
  读账号      GET  {HOST}/api/oauth/account/info  头 Access-Token: <access token>
  回包是海尔 resultCode 信封：resultCode=0 才成功，数据在 data 里；工号字段在不同接口里名字不同，
  且 account/info 的 account 是嵌套对象（见 account_of）。

IAM 的 access token 对本服务不透明：**只有向 IAM 核实过的工号才可信**，绝不从令牌本体解码身份。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from .config import settings

log = logging.getLogger(__name__)

ACCOUNT_FIELDS = ("accountNumber", "loginName", "userName", "account")
NAME_FIELDS = ("name", "realName", "userRealName", "displayName", "nickName", "nickname")


class IamError(RuntimeError):
    """IAM 明确拒绝（可把分类原因告诉用户）。"""


class IamUnavailable(RuntimeError):
    """网络失败、回包异常等内部问题（只给用户一句“暂时无法核实”）。"""


@dataclass
class IamAccount:
    emp_no: str
    name: str = ""


def missing_vars() -> list[str]:
    need = {"HAIER_IAM_HOST": settings.iam_host, "HAIER_IAM_CLIENT_ID": settings.iam_client_id,
            "HAIER_IAM_CLIENT_SECRET": settings.iam_client_secret}
    return [k for k, v in need.items() if not v]


def configured() -> bool:
    return not missing_vars()


def redirect_uri() -> str:
    return settings.iam_redirect_uri or f"{settings.public_base_url}/auth/iam/callback"


# ------------------------------------------------------------ 字段解析
def _first(src: dict, fields: tuple[str, ...]) -> str:
    for key in fields:
        raw = src.get(key)
        if isinstance(raw, dict):
            inner = _first(raw, fields)
            if inner:
                return inner
            continue
        v = str(raw or "").strip()
        if v:
            return v
    return ""


def account_of(src: dict | None) -> str:
    return _first(src or {}, ACCOUNT_FIELDS)


def name_of(src: dict | None) -> str:
    src = src or {}
    name = _first(src, NAME_FIELDS)
    if not name and isinstance(src.get("account"), dict):
        name = _first(src["account"], NAME_FIELDS)
    return name


def unwrap(payload: dict, what: str):
    if not isinstance(payload, dict):
        raise IamUnavailable(f"{what}：IAM 回包格式异常")
    code = payload.get("resultCode", payload.get("code"))
    if code is not None and str(code) != "0":
        log.warning("IAM %s失败 resultCode=%s msg=%s", what, code, payload.get("resultMsg") or payload.get("message"))
        raise IamError(f"{what}失败（resultCode={code}）")
    return payload.get("data", payload)


# ------------------------------------------------------------ 登录流程
def authorize_url(state: str) -> str:
    host = (settings.iam_authorize_host or settings.iam_host).rstrip("/")
    q = {"client_id": settings.iam_client_id, "redirect_uri": redirect_uri(), "response_type": "code",
         "scope": settings.iam_scope, "state": state}
    return f"{host}/oauth/authorize?{urlencode(q)}"


def exchange_code(code: str) -> tuple[str, dict]:
    try:
        r = httpx.post(
            f"{settings.iam_host.rstrip('/')}/api/oauth/token/code",
            json={"client_id": settings.iam_client_id, "client_secret": settings.iam_client_secret, "code": code},
            headers={"Application-Key": settings.iam_client_id, "Accept": "application/json"},
            timeout=settings.iam_timeout,
        )
    except httpx.HTTPError as e:
        raise IamUnavailable(f"换票请求失败：{e}") from e
    if r.status_code in (400, 401, 403):
        raise IamError(f"换票被拒绝（HTTP {r.status_code}），授权码可能已使用或过期，请重新登录")
    if r.status_code >= 500:
        raise IamUnavailable(f"换票失败：HTTP {r.status_code}")
    try:
        body = r.json()
    except ValueError as e:
        raise IamUnavailable(f"换票失败：HTTP {r.status_code}，回包不是 JSON") from e
    data = unwrap(body, "换票")
    token = (data or {}).get("access_token") or (data or {}).get("accessToken") or (data or {}).get("token")
    if not token:
        raise IamError("换票成功但返回里没有 access_token")
    return str(token), data or {}


def expires_in(ticket: dict) -> int | None:
    """IAM 各版本的剩余秒数使用不同字段名，缺失时由 IAM 核验实际有效期。"""
    try:
        value = int(ticket.get("expires_in", ticket.get("expiresIn")))
    except (TypeError, ValueError, OverflowError):
        return None
    return value if value > 0 else None


def resolve_account(access_token: str) -> IamAccount:
    """拿 IAM access token 向 IAM 核实，返回工号与姓名。"""
    token = (access_token or "").strip()
    if not token:
        raise IamError("没有 access token")
    try:
        r = httpx.get(f"{settings.iam_host.rstrip('/')}/api/oauth/account/info",
                      headers={"Access-Token": token, "Accept": "application/json"}, timeout=settings.iam_timeout)
    except httpx.HTTPError as e:
        raise IamUnavailable(f"读取账号失败：{e}") from e
    # 真实 IAM 对无效 / 过期票直接回 HTTP 401（空响应体），不走 resultCode 信封
    if r.status_code in (401, 403):
        raise IamError("IAM 令牌无效或已过期")
    if r.status_code >= 500:
        raise IamUnavailable(f"读取账号失败：HTTP {r.status_code}")
    try:
        body = r.json()
    except ValueError as e:
        raise IamUnavailable(f"读取账号失败：HTTP {r.status_code}，回包不是 JSON") from e
    info = unwrap(body, "读取账号")
    emp = account_of(info if isinstance(info, dict) else {})
    if not emp:
        raise IamError("IAM 返回里找不到工号")
    return IamAccount(emp_no=emp, name=name_of(info if isinstance(info, dict) else {}))


# ------------------------------------------------------------ MCP 侧核实缓存
_cache: dict[str, tuple[float, IamAccount | None]] = {}
_lock = threading.Lock()


def verify_access_token(token: str) -> IamAccount:
    """MCP 收到 IAM 票时调用：成功结果缓存 IAM_TOKEN_CACHE_SECONDS，失败缓存 30 秒（防止反复打 IAM）。"""
    key = hashlib.sha256(token.encode()).hexdigest()
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            if hit[1] is None:
                raise IamError("IAM 令牌无效或已过期")
            return hit[1]
    try:
        acc = resolve_account(token)
    except IamError:
        with _lock:
            _cache[key] = (now + 30, None)
        raise
    with _lock:
        if len(_cache) > 5000:
            _cache.clear()
        _cache[key] = (now + settings.iam_token_cache_seconds, acc)
    return acc


# ------------------------------------------------------------ state（防 CSRF，回跳地址钉在票里）
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def pack_state(secret: str, next_path: str) -> str:
    body = _b64(json.dumps({"n": next_path, "t": int(time.time()), "r": _b64(hashlib.sha256(
        str(time.time_ns()).encode()).digest()[:9])}).encode())
    mac = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{mac}"


def unpack_state(secret: str, state: str) -> str:
    try:
        body, mac = state.split(".", 1)
    except ValueError as e:
        raise IamError("登录状态格式不对，请重新登录") from e
    good = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(good, mac):
        raise IamError("登录状态校验失败，请重新登录")
    p = json.loads(_unb64(body))
    if not isinstance(p.get("t"), int) or time.time() - p["t"] > 600 or p["t"] > time.time() + 30:
        raise IamError("登录已超时，请重新发起")
    return safe_next(p.get("n"))


def safe_next(path: str | None) -> str:
    """只允许站内相对路径，防止开放重定向。"""
    p = str(path or "/")
    if not p.startswith("/") or p.startswith("//") or "\\" in p:
        return "/"
    return p
