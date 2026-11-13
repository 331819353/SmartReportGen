"""用现有海尔 IAM 回调完成员工登录，为 MCP 客户端提供授权码与 PKCE 换票。"""
from __future__ import annotations

import base64
import hashlib
import re
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, RedirectResponse

from . import auth, crypto, db, iam
from .config import settings

router = APIRouter()
CLIENT_ID = "smartreportgen-cli"
STATE_PREFIX = "mcp."
NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
PKCE_VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}")
PKCE_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")


def enabled() -> bool:
    return iam.configured()


def issuer() -> str:
    return settings.public_base_url.rstrip("/")


def resource_metadata_path() -> str:
    return "/.well-known/oauth-protected-resource" + urlsplit(settings.mcp_public_url).path.rstrip("/")


def resource_metadata_url() -> str:
    resource = urlsplit(settings.mcp_public_url)
    return f"{resource.scheme}://{resource.netloc}{resource_metadata_path()}"


def resource_metadata(_request: Request):
    if not enabled():
        return _error("server_error", "海尔 IAM 登录未启用", 404)
    return JSONResponse({
        "resource": settings.mcp_public_url,
        "authorization_servers": [issuer()],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["mcp"],
    })


def _error(error: str, description: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": error, "error_description": description}, status_code=status, headers=NO_STORE)


@router.get("/.well-known/oauth-authorization-server", include_in_schema=False)
def authorization_metadata():
    if not enabled():
        return _error("server_error", "海尔 IAM 登录未启用", 404)
    return {
        "issuer": issuer(),
        "authorization_endpoint": issuer() + "/auth/mcp/authorize",
        "token_endpoint": issuer() + "/auth/mcp/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["mcp"],
        "authorization_response_iss_parameter_supported": True,
    }


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _cookie_name(state: str) -> str:
    return "srg_mcp_" + _digest(state)[:20]


def _valid_redirect(uri: str) -> bool:
    # Codex 使用本机回调，可选独立端口和实例 ID。禁止跳转到第三方域名或任意路径。
    match = re.fullmatch(r"http://(?:127\.0\.0\.1|\[::1\])(?::([1-9][0-9]{0,4}))?/callback(?:/[A-Za-z0-9_-]{1,128})?", uri)
    return bool(match and (match[1] is None or int(match[1]) <= 65535))


def _valid_resource(resource: str) -> bool:
    return resource in ("", settings.mcp_public_url)


@router.get("/auth/mcp/authorize", include_in_schema=False)
def authorize(request: Request):
    if not enabled():
        return _error("server_error", "海尔 IAM 登录未启用", 404)
    q = request.query_params
    if q.get("client_id") != CLIENT_ID:
        return _error("invalid_client", "未知的 MCP OAuth 客户端")
    redirect_uri = q.get("redirect_uri", "")
    client_state = q.get("state", "")
    challenge = q.get("code_challenge", "")
    if (not _valid_redirect(redirect_uri) or not 1 <= len(client_state) <= 512
            or q.get("response_type") != "code" or q.get("code_challenge_method") != "S256"
            or not PKCE_CHALLENGE.fullmatch(challenge)):
        return _error("invalid_request", "需要合法的本机回调、state 和 S256 PKCE")
    if q.get("scope", "") not in ("", "mcp"):
        return _error("invalid_scope", "MCP 仅支持 mcp 权限范围")
    if not _valid_resource(q.get("resource", "")):
        return _error("invalid_target", "令牌只能用于当前 MCP 地址")

    state = STATE_PREFIX + secrets.token_urlsafe(32)
    browser = secrets.token_urlsafe(32)
    db.execute("DELETE FROM permission.mcp_oauth_flow WHERE expires_at <= now()")
    db.execute(
        "INSERT INTO permission.mcp_oauth_flow(state_hash, browser_hash, redirect_uri, client_state, challenge, expires_at) "
        "VALUES (%s,%s,%s,%s,%s,now() + interval '10 minutes')",
        (_digest(state), _digest(browser), redirect_uri, client_state, challenge),
    )
    response = RedirectResponse(iam.authorize_url(state), status_code=302, headers=NO_STORE)
    response.set_cookie(_cookie_name(state), browser, httponly=True, secure=issuer().startswith("https://"),
                        samesite="lax", path="/auth/iam/callback", max_age=600)
    return response


def complete_iam(request: Request, code: str, state: str, error: str):
    """现有 IAM 回调按 state 前缀分流，IAM 客户端和已登记回调地址继续共用。"""
    browser = request.cookies.get(_cookie_name(state))
    if not browser or not enabled():
        return _error("invalid_request", "登录状态已失效，请从 MCP 客户端重新登录")
    state_hash = _digest(state)
    # 先原子领取浏览器回调，防止重复回调并发消耗同一个 IAM 授权码。
    flow = db.fetchone(
        "UPDATE permission.mcp_oauth_flow SET browser_hash=NULL "
        "WHERE state_hash=%s AND browser_hash=%s AND expires_at > now() RETURNING redirect_uri, client_state",
        (state_hash, _digest(browser)),
    )
    if not flow:
        return _error("invalid_request", "登录状态已失效，请从 MCP 客户端重新登录")

    def redirect(params: dict) -> RedirectResponse:
        response = RedirectResponse(flow["redirect_uri"] + "?" + urlencode({
            **params, "state": flow["client_state"], "iss": issuer(),
        }), status_code=302, headers=NO_STORE)
        response.delete_cookie(_cookie_name(state), path="/auth/iam/callback")
        return response

    try:
        if error or not code:
            raise iam.IamError("海尔 IAM 登录未完成")
        iam_token, ticket = iam.exchange_code(code)
        account = iam.resolve_account(iam_token)
        principal = auth.principal_for(account.emp_no, account.name, via="iam", register="mcp")
        if not principal.registered:
            raise iam.IamError("员工未登记或已停用，请联系管理员")
    except (iam.IamError, iam.IamUnavailable) as exc:
        db.execute("DELETE FROM permission.mcp_oauth_flow WHERE state_hash=%s", (state_hash,))
        if isinstance(exc, iam.IamError):
            return redirect({"error": "access_denied", "error_description": str(exc)})
        return redirect({"error": "temporarily_unavailable", "error_description": "海尔 IAM 暂时无法核实，请重试"})

    # 海尔 IAM 的 client_secret 仅由服务端持有。浏览器只带回一次性授权码，IAM 票经 PKCE 换票交给客户端。
    seconds = iam.expires_in(ticket)
    iam_expiry = int(time.time()) + seconds if seconds is not None else None
    grant = secrets.token_urlsafe(32)
    updated = db.fetchone(
        "UPDATE permission.mcp_oauth_flow SET code_hash=%s, emp_no=%s, iam_token=%s, "
        "iam_expires_at=to_timestamp(%s), "
        "expires_at=now() + interval '2 minutes' "
        "WHERE state_hash=%s AND expires_at > now() RETURNING state_hash",
        (_digest(grant), principal.emp_no, crypto.encrypt(iam_token), iam_expiry, state_hash),
    )
    if not updated:
        return _error("invalid_request", "登录状态已使用或过期，请重新登录")
    return redirect({"code": grant})


@router.post("/auth/mcp/token", include_in_schema=False)
async def token(request: Request):
    if not enabled():
        return _error("server_error", "海尔 IAM 登录未启用", 404)
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/x-www-form-urlencoded":
        return _error("invalid_request", "需要表单编码")
    try:
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 8192:
                return _error("invalid_request", "换票参数过长")
            raw.extend(chunk)
        form = parse_qs(raw.decode(), keep_blank_values=True, max_num_fields=16)
    except (UnicodeError, ValueError):
        return _error("invalid_request", "换票参数格式不正确")
    if any(len(values) != 1 for values in form.values()):
        return _error("invalid_request", "换票参数重复")
    return await run_in_threadpool(_exchange, {key: values[0] for key, values in form.items()})


def _exchange(form: dict[str, str]):
    if form.get("client_id") != CLIENT_ID:
        return _error("invalid_client", "未知的 MCP OAuth 客户端")
    if form.get("grant_type") != "authorization_code":
        return _error("unsupported_grant_type", "请使用浏览器重新登录")
    if not _valid_resource(form.get("resource", "")):
        return _error("invalid_target", "令牌只能用于当前 MCP 地址")
    code, verifier = form.get("code", ""), form.get("code_verifier", "")
    if not PKCE_CHALLENGE.fullmatch(code) or not PKCE_VERIFIER.fullmatch(verifier):
        return _error("invalid_grant", "授权码或 PKCE 校验失败")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    # 先匹配所有绑定条件，再原子消费授权码，错误 verifier 不会使有效登录失效。
    row = db.fetchone(
        "DELETE FROM permission.mcp_oauth_flow "
        "WHERE code_hash=%s AND redirect_uri=%s AND challenge=%s AND expires_at > now() "
        "RETURNING emp_no, iam_token, iam_expires_at",
        (_digest(code), form.get("redirect_uri"), challenge),
    )
    if not row:
        return _error("invalid_grant", "授权码无效、已使用或已过期")
    if not auth.principal_for(row["emp_no"]).registered:
        return _error("invalid_grant", "员工未登记或已停用")
    credentials = {"access_token": crypto.decrypt(row["iam_token"]), "token_type": "Bearer", "scope": "mcp"}
    if row["iam_expires_at"] is not None:
        remaining = int(row["iam_expires_at"].timestamp() - time.time())
        if remaining <= 0:
            return _error("invalid_grant", "海尔 IAM 令牌已过期，请重新登录")
        credentials["expires_in"] = remaining
    return JSONResponse(credentials, headers=NO_STORE)
