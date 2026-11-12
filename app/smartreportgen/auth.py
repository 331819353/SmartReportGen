"""MCP 令牌认证：JWT 中携带员工工号；支持管理台签发（HS256）与企业 SSO 签发（RS256/ES256 公钥验签）。"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import logging
import secrets
import time
import uuid
from dataclasses import dataclass, field

import jwt

from . import db, iam
from .config import settings

log = logging.getLogger(__name__)


class AuthError(PermissionError):
    pass


@dataclass
class Principal:
    emp_no: str | None
    name: str = ""
    is_admin: bool = False
    via: str = "jwt"                 # jwt | admin_token
    registered: bool = True          # 工号是否在权限表中登记且启用
    roles: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return "管理员令牌" if self.via == "admin_token" else f"{self.emp_no}（{self.name or '未登记'}）"


_secret_cache: str | None = None


def jwt_secret() -> str:
    global _secret_cache
    if _secret_cache:
        return _secret_cache
    sec = settings.jwt_secret or db.get_setting("generated_jwt_secret") or ""
    if not sec:
        sec = secrets.token_urlsafe(48)
        db.set_setting("generated_jwt_secret", sec)
        log.warning("未配置 JWT_SECRET，已自动生成并保存在数据库中；生产环境请显式配置")
    _secret_cache = sec
    return sec


def issue_token(emp_no: str, days: int = 90, note: str = "", max_days: int | None = None) -> dict:
    if max_days and days > max_days:
        raise AuthError(f"有效期不能超过 {max_days} 天")
    emp = db.fetchone("SELECT * FROM permission.employee WHERE emp_no=%s", (emp_no,))
    if not emp:
        raise AuthError(f"工号 {emp_no} 未在员工表中登记")
    now = int(time.time())
    exp = now + int(days) * 86400
    jti = uuid.uuid4().hex
    claims = {
        "iss": settings.jwt_issuer, "aud": settings.jwt_audience, "sub": emp_no, "jti": jti, "iat": now, "exp": exp,
        settings.jwt_emp_claim: emp_no, settings.jwt_name_claim: emp["name"],
    }
    token = jwt.encode(claims, jwt_secret(), algorithm="HS256")
    db.execute(
        "INSERT INTO permission.access_token(jti, emp_no, note, expires_at) VALUES (%s,%s,%s,to_timestamp(%s))",
        (jti, emp_no, note, exp),
    )
    return {"token": token, "jti": jti, "emp_no": emp_no, "expires_at": dt.datetime.fromtimestamp(exp).isoformat()}


def _decode(raw: str) -> dict:
    header = jwt.get_unverified_header(raw)
    alg = header.get("alg", "")
    if alg not in settings.jwt_algorithms and alg != "HS256":
        raise AuthError(f"不支持的签名算法 {alg}")
    if alg.startswith("HS"):
        key = jwt_secret()
    else:
        if not settings.jwt_public_key:
            raise AuthError("未配置 JWT_PUBLIC_KEY，无法验证非对称签名令牌")
        key = settings.jwt_public_key
    opts = {"require": ["exp"]}
    kwargs = {}
    if settings.jwt_audience:
        kwargs["audience"] = settings.jwt_audience
    if settings.jwt_issuer:
        kwargs["issuer"] = settings.jwt_issuer
    try:
        return jwt.decode(raw, key, algorithms=[alg], options=opts, leeway=30, **kwargs)
    except jwt.ExpiredSignatureError as e:
        raise AuthError("令牌已过期") from e
    except jwt.PyJWTError as e:
        raise AuthError(f"令牌无效：{e}") from e


def principal_for(emp_no: str, name: str = "", via: str = "jwt", register: str | None = None) -> Principal:
    """按工号查权限表构造身份。register 非空时为首次出现的已核实身份自动注册：
    - "mcp"：首次调用 MCP，一律注册为普通用户（默认角色 DEFAULT_USER_ROLE，无管理员权限）；
    - "iam_login"：IAM 登录管理台，IAM_BOOTSTRAP_ADMINS 中的工号首次登录为管理员，其余同样为普通用户。
    已存在的员工不改变角色；已停用的员工不会被重新启用。"""
    emp = db.fetchone("SELECT * FROM permission.employee WHERE emp_no=%s", (emp_no,))
    if not emp and register and settings.iam_auto_register:
        boot = register == "iam_login" and emp_no in settings.iam_bootstrap_admins
        roles = [settings.default_user_role] if settings.default_user_role and not boot else []
        db.execute("INSERT INTO permission.employee(emp_no, name, department, roles, is_admin, registered_via) "
                   "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (emp_no) DO NOTHING",
                   (emp_no, name or "", "", roles, boot, register))
        if boot:
            db.set_setting(f"bootstrap_admin:{emp_no}", "created")
        log.info("自动注册员工 %s（来源 %s，%s）", emp_no, register, "引导管理员" if boot else "普通用户")
        emp = db.fetchone("SELECT * FROM permission.employee WHERE emp_no=%s", (emp_no,))
    elif emp and register == "iam_login" and emp_no in settings.iam_bootstrap_admins and not emp["is_admin"] \
            and emp["registered_via"] == "mcp" and not db.get_setting(f"bootstrap_admin:{emp_no}"):
        # 引导管理员若先经 MCP 被注册为普通用户，首次 IAM 登录管理台时提升一次（之后管理员如有降级，不再自动提升）
        db.execute("UPDATE permission.employee SET is_admin=true, updated_at=now() WHERE emp_no=%s", (emp_no,))
        db.set_setting(f"bootstrap_admin:{emp_no}", "promoted")
        log.info("引导管理员 %s 首次登录管理台，已提升为管理员", emp_no)
        emp = db.fetchone("SELECT * FROM permission.employee WHERE emp_no=%s", (emp_no,))
    if emp and register and name and not emp["name"]:
        db.execute("UPDATE permission.employee SET name=%s, updated_at=now() WHERE emp_no=%s", (name, emp_no))
    if not emp or not emp["enabled"]:
        return Principal(emp_no=emp_no, name=name, via=via, registered=False)
    return Principal(emp_no=emp_no, name=emp["name"] or name, is_admin=emp["is_admin"], via=via,
                     roles=list(emp["roles"] or []))


def _looks_like_jwt(raw: str) -> bool:
    if raw.count(".") != 2:
        return False
    try:
        return "alg" in jwt.get_unverified_header(raw)
    except jwt.PyJWTError:
        return False


def verify_iam_token(raw: str) -> Principal:
    if not settings.iam_enabled or not settings.mcp_accept_iam_token:
        raise AuthError("未启用 IAM 令牌认证")
    if not iam.configured():
        raise AuthError(f"IAM 未配置齐全（缺 {'、'.join(iam.missing_vars())}），无法核实 IAM 令牌")
    try:
        acc = iam.verify_access_token(raw)
    except iam.IamError as e:
        raise AuthError(f"IAM 令牌未通过核实：{e}") from e
    except iam.IamUnavailable as e:
        log.error("IAM 核实失败：%s", e)
        raise AuthError("IAM 暂时无法核实令牌，请稍后重试") from e
    return principal_for(acc.emp_no, acc.name, via="iam", register="mcp")


def verify_bearer(authorization: str | None, iam_token: str | None = None) -> Principal:
    """MCP 认证：管理员令牌（可关）→ 本系统 JWT（员工令牌）→ IAM access token（向 IAM 核实）。
    iam_token 为 Access-Token / X-IAM-Access-Token 头中的 IAM 票（agent 宿主注入时使用）。"""
    raw = (authorization or "").strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    if not raw and iam_token:
        return verify_iam_token(iam_token.strip())
    if not raw:
        raise AuthError("缺少令牌：请携带 Authorization: Bearer <令牌>（员工令牌或 IAM access token）")
    if settings.admin_token and settings.mcp_allow_admin_token and secrets.compare_digest(raw, settings.admin_token):
        return Principal(emp_no=None, name="admin", is_admin=True, via="admin_token")
    if not _looks_like_jwt(raw):
        return verify_iam_token(raw)
    try:
        claims = _decode(raw)
    except AuthError as e:
        # 非本系统签发（签名/签发方/受众不符）的 JWT 也可能是 IAM 票：交给 IAM 核实；过期等明确错误直接返回
        if settings.iam_enabled and settings.mcp_accept_iam_token and iam.configured() and "过期" not in str(e):
            try:
                return verify_iam_token(raw)
            except AuthError:
                pass
        raise
    emp_no = str(claims.get(settings.jwt_emp_claim) or "").strip()
    if not emp_no:
        raise AuthError(f"令牌中缺少员工工号（claim: {settings.jwt_emp_claim}）")
    jti = claims.get("jti")
    if jti:
        row = db.fetchone("SELECT revoked FROM permission.access_token WHERE jti=%s", (jti,))
        if row and row["revoked"]:
            raise AuthError("令牌已被吊销")
        if row:
            db.execute("UPDATE permission.access_token SET last_used_at=now() WHERE jti=%s", (jti,))
    # 本系统或企业 SSO 签发且验签通过的令牌：首次调用 MCP 同样自动注册为普通用户
    return principal_for(emp_no, str(claims.get(settings.jwt_name_claim) or ""), via="jwt", register="mcp")


# ------------------------------------------------------------ 管理台会话（IAM 登录后的 HttpOnly Cookie）
SESSION_COOKIE = "srg_session"
_SESSION_AUD = "smartreportgen-console"


def issue_session(emp_no: str) -> str:
    now = int(time.time())
    return jwt.encode({"iss": settings.jwt_issuer, "aud": _SESSION_AUD, "sub": emp_no, "typ": "session",
                       "iat": now, "exp": now + settings.session_hours * 3600}, jwt_secret(), algorithm="HS256")


def verify_session(cookie: str | None) -> Principal | None:
    """会话只携带工号；是否管理员、是否启用每次都按权限表实时判断（撤权即时生效）。"""
    if not cookie:
        return None
    try:
        claims = jwt.decode(cookie, jwt_secret(), algorithms=["HS256"], audience=_SESSION_AUD,
                            issuer=settings.jwt_issuer, options={"require": ["exp", "sub"]})
    except jwt.PyJWTError:
        return None
    if claims.get("typ") != "session":
        return None
    return principal_for(str(claims["sub"]), via="session")


# ------------------------------------------------------------ 报告分享凭证
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def share_token(report_id: int, days: int | None = None) -> str:
    exp = int(time.time()) + int(days or settings.report_share_days) * 86400
    msg = f"r{report_id}.{exp}".encode()
    sig = hmac.new(jwt_secret().encode(), msg, hashlib.sha256).digest()[:18]
    return f"{exp}.{_b64(sig)}"


def verify_share(report_id: int, token: str | None) -> bool:
    if not token or "." not in token:
        return False
    exp_s, sig = token.split(".", 1)
    if not exp_s.isdigit() or int(exp_s) < time.time():
        return False
    msg = f"r{report_id}.{exp_s}".encode()
    good = _b64(hmac.new(jwt_secret().encode(), msg, hashlib.sha256).digest()[:18])
    return secrets.compare_digest(good, sig)
