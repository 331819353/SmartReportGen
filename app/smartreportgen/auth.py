"""统一验证海尔 IAM 身份，并管理管理台的服务端浏览器会话。"""
from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass, field

from . import crypto, db, iam
from .config import settings

log = logging.getLogger(__name__)


class AuthError(PermissionError):
    pass


class AuthUnavailable(AuthError):
    pass


@dataclass
class Principal:
    emp_no: str
    name: str = ""
    is_admin: bool = False
    via: str = "iam"
    registered: bool = True  # 工号已登记并启用
    roles: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.emp_no}（{self.name or '未登记'}）"


def principal_for(emp_no: str, name: str = "", via: str = "iam", register: str | None = None) -> Principal:
    """按工号查权限表构造身份。register 非空时为首次出现的已核实身份自动注册：
    - "mcp"：首次使用 IAM Bearer 接入 HTTP / MCP，一律注册为普通用户（默认角色 DEFAULT_USER_ROLE，无管理员权限）；
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
    elif (emp and register == "iam_login" and emp_no in settings.iam_bootstrap_admins and not emp["is_admin"]
          and emp["enabled"] and emp["registered_via"] == "mcp" and not db.get_setting(f"bootstrap_admin:{emp_no}")):
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


def verify_iam_token(raw: str, *, via: str = "iam", register: str | None = "mcp") -> Principal:
    if not iam.configured():
        raise AuthUnavailable("海尔 IAM 未配置，无法核实访问令牌")
    try:
        account = iam.verify_access_token(raw)
    except iam.IamError as exc:
        raise AuthError(str(exc)) from exc
    except iam.IamUnavailable as exc:
        log.warning("IAM 核实失败：%s", exc)
        raise AuthUnavailable("海尔 IAM 暂时无法核实访问令牌") from exc
    return principal_for(account.emp_no, account.name, via=via, register=register)


def verify_bearer(authorization: str | None) -> Principal:
    """从标准 Authorization 请求头读取海尔 IAM 原票，再按工号核验身份。"""
    if authorization is None:
        raise AuthError("缺少海尔 IAM access token")
    scheme, _, raw = authorization.strip().partition(" ")
    if scheme.lower() != "bearer" or not raw.strip():
        raise AuthError("请使用 Authorization: Bearer <IAM access token>")
    return verify_iam_token(raw.strip())


# 浏览器 Cookie 只是会话 ID。IAM 原票留在服务端加密保存，不能用会话 ID 调 HTTP Bearer 或 MCP。
SESSION_COOKIE = "srg_iam_session"
LOGIN_COOKIE = "srg_iam_login"


def cookie_path(path: str = "/") -> str:
    """Cookie 的浏览器侧路径必须包含反向代理的公开前缀。"""
    return settings.public_path(path)


def _session_digest(session_id: str) -> str:
    return hashlib.sha256(session_id.encode()).hexdigest()


def start_session(emp_no: str, iam_token: str, seconds: int) -> str:
    session_id = secrets.token_urlsafe(32)
    ciphertext = crypto.encrypt(iam_token)
    with db.conn() as connection, connection.transaction():
        connection.execute("DELETE FROM permission.iam_session WHERE expires_at <= now()")
        connection.execute(
            "INSERT INTO permission.iam_session(id_hash, emp_no, iam_token, expires_at) "
            "VALUES (%s,%s,%s,now() + %s * interval '1 second')",
            (_session_digest(session_id), emp_no, ciphertext, seconds),
        )
    return session_id


def verify_session(session_id: str | None) -> Principal | None:
    if not session_id:
        return None
    row = db.fetchone(
        "SELECT emp_no, iam_token FROM permission.iam_session WHERE id_hash=%s AND expires_at > now()",
        (_session_digest(session_id),),
    )
    if not row:
        return None
    try:
        iam_token = crypto.decrypt(row["iam_token"])
    except RuntimeError:
        # 密钥轮换后清理旧会话，引导用户用 IAM 重新登录。
        end_session(session_id)
        return None
    try:
        principal = verify_iam_token(iam_token, via="session", register=None)
    except AuthUnavailable:
        raise
    except AuthError:
        end_session(session_id)
        return None
    return principal if principal.emp_no == row["emp_no"] else None


def end_session(session_id: str | None) -> None:
    if session_id:
        db.execute("DELETE FROM permission.iam_session WHERE id_hash=%s", (_session_digest(session_id),))
