"""管理后台 REST API + 管理页面 + 报告浏览。"""
from __future__ import annotations

import html
import logging
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import auth, db, design, embedding, iam, permission, repo, seed, services
from .config import settings
from .connectors import ConnectorError, get_connector
from .renderer import RenderError, render, standalone, validate
from .template_parser import TemplateError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("smartreportgen.api")
STATIC = Path(__file__).with_name("static")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    need = db.init_db()
    repo.load_custom_words()
    seed.seed_if_needed()
    seed.migrate()
    if need:
        log.info("向量模型/维度变化，重建向量：%s", repo.reindex_all())
    else:
        stale = repo.reindex_all(only_stale=True)
        if stale["datasets"] or stale["style_rules"]:
            log.info("补齐未向量化记录：%s", stale)
    if settings.iam_enabled and not iam.configured():
        log.warning("AUTH_MODE=iam 但 IAM 未配置齐全（缺 %s），IAM 单点登录不可用", "、".join(iam.missing_vars()))
    if open_mode():
        log.warning("既未配置 IAM 也未设置 ADMIN_TOKEN：管理台与管理 API 处于开发模式（无鉴权），请勿暴露到公网")
    yield


app = FastAPI(title=settings.app_name, version="1.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


# ------------------------------------------------------------------ 认证：IAM 会话 / 管理员令牌
ADMIN_COOKIE = "r2h_token"
SELF_PATHS = ("/api/me", "/api/auth/", "/api/meta")


def open_mode() -> bool:
    """既没有管理员令牌也没有可用的 IAM：开发模式，管理端不鉴权（启动日志会告警）。"""
    return not settings.admin_token and not (settings.iam_enabled and iam.configured())


def console_principal(request: Request) -> auth.Principal | None:
    tok = request.headers.get("authorization", "").removeprefix("Bearer ").strip() or request.cookies.get(ADMIN_COOKIE, "")
    if settings.admin_token and tok and secrets.compare_digest(tok, settings.admin_token):
        return auth.Principal(emp_no=None, name="管理员令牌", is_admin=True, via="admin_token")
    if settings.iam_enabled:
        return auth.verify_session(request.cookies.get(auth.SESSION_COOKIE))
    return None


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    request.state.principal = None
    request.state.share_ok = False
    # 报告页面可凭 MCP 返回链接中的查看凭证 ?t= 免登录访问（仅限该报告）
    m = re.match(r"^/reports/(\d+)(/download)?$", path)
    if m and auth.verify_share(int(m.group(1)), request.query_params.get("t")):
        request.state.share_ok = True
        return await call_next(request)
    if not (path.startswith("/api/") or path.startswith("/reports/")):
        return await call_next(request)
    if open_mode():
        request.state.principal = auth.Principal(emp_no=None, name="开发模式", is_admin=True, via="open")
        return await call_next(request)
    p = console_principal(request)
    if path.startswith("/api/auth/"):
        request.state.principal = p
        return await call_next(request)
    if not p:
        return JSONResponse({"detail": "未登录或登录已过期"}, status_code=401)
    request.state.principal = p
    if path.startswith(SELF_PATHS) or path.startswith("/reports/"):
        return await call_next(request)
    if not p.is_admin:
        return JSONResponse({"detail": "需要管理员权限；如需开通请联系管理员"}, status_code=403)
    return await call_next(request)


@app.exception_handler(repo.NotFound)
async def _nf(_r, e):
    return JSONResponse({"detail": str(e)}, status_code=404)


for _exc in (repo.Invalid, ConnectorError, RenderError, TemplateError, RuntimeError, auth.AuthError):
    app.add_exception_handler(_exc, lambda _r, e: JSONResponse({"detail": str(e)}, status_code=400))


def J(obj) -> JSONResponse:
    return JSONResponse(jsonable_encoder(obj))


# ------------------------------------------------------------------ 页面
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "admin" / "index.html")


@app.get("/healthz", include_in_schema=False)
def healthz():
    db.fetchone("SELECT 1")
    return {"ok": True}


def _cookie_secure() -> bool:
    return settings.public_base_url.startswith("https://")


def _me(p: auth.Principal | None) -> dict:
    if not p:
        return {"logged_in": False}
    return {"logged_in": True, "emp_no": p.emp_no, "name": p.name, "is_admin": p.is_admin, "via": p.via,
            "registered": p.registered, "roles": p.roles}


@app.get("/api/auth/config")
def auth_config(request: Request):
    return {
        "mode": settings.auth_mode, "open_mode": open_mode(),
        "iam_configured": settings.iam_enabled and iam.configured(),
        "iam_missing": iam.missing_vars() if settings.iam_enabled else [],
        "admin_token_login": bool(settings.admin_token),
        "self_service_tokens": settings.self_service_tokens and settings.iam_enabled,
        "token_max_days": settings.token_max_days,
        "me": _me(request.state.principal),
    }


@app.get("/api/auth/check")
def auth_check(request: Request):
    """兼容旧版管理台：是否需要登录、当前是否已登录。"""
    return {"required": not open_mode(), "ok": open_mode() or request.state.principal is not None}


@app.post("/api/auth/token-login")
def token_login(data: dict = Body(...)):
    """应急入口：用 ADMIN_TOKEN 登录管理台（写 HttpOnly Cookie）。"""
    tok = str(data.get("token") or "")
    if not settings.admin_token or not secrets.compare_digest(tok, settings.admin_token):
        return JSONResponse({"detail": "管理员令牌不正确或未启用"}, status_code=401)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(ADMIN_COOKIE, tok, httponly=True, samesite="strict", secure=_cookie_secure(), max_age=86400 * 7)
    return resp


@app.get("/auth/iam/login", include_in_schema=False)
def iam_login(next: str = "/"):
    if not settings.iam_enabled:
        return HTMLResponse(_auth_page("未启用 IAM 单点登录", "当前 AUTH_MODE 不是 iam。"), status_code=404)
    if not iam.configured():
        return HTMLResponse(_auth_page("IAM 单点登录未配置", "缺少配置：" + "、".join(iam.missing_vars())),
                            status_code=503)
    state = iam.pack_state(auth.jwt_secret(), iam.safe_next(next))
    return RedirectResponse(iam.authorize_url(state), status_code=302, headers={"Cache-Control": "no-store"})


@app.get("/auth/iam/callback", include_in_schema=False)
def iam_callback(code: str = "", state: str = ""):
    try:
        if not code:
            raise iam.IamError("回调缺少 code")
        nxt = iam.unpack_state(auth.jwt_secret(), state)
        token, body = iam.exchange_code(code)
        try:
            acc = iam.resolve_account(token)
        except iam.IamError:
            emp = iam.account_of(body)
            if not emp:
                raise
            acc = iam.IamAccount(emp_no=emp, name=iam.name_of(body))
        p = auth.principal_for(acc.emp_no, acc.name, via="session", register="iam_login")
    except iam.IamError as e:
        return HTMLResponse(_auth_page("单点登录失败", str(e)), status_code=400)
    except iam.IamUnavailable as e:
        log.error("IAM 回调失败：%s", e)
        return HTMLResponse(_auth_page("单点登录失败", "IAM 暂时无法核实，请稍后重试"), status_code=502)
    log.info("IAM 登录：%s %s（管理员=%s，已登记=%s）", p.emp_no, p.name, p.is_admin, p.registered)
    resp = RedirectResponse(nxt, status_code=302, headers={"Cache-Control": "no-store"})
    resp.set_cookie(auth.SESSION_COOKIE, auth.issue_session(acc.emp_no), httponly=True, samesite="lax",
                    secure=_cookie_secure(), max_age=settings.session_hours * 3600)
    return resp


@app.post("/auth/logout", include_in_schema=False)
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.SESSION_COOKIE)
    resp.delete_cookie(ADMIN_COOKIE)
    return resp


def _auth_page(title: str, msg: str) -> str:
    return (f'<!doctype html><meta charset="utf-8"><title>{html.escape(title)}</title>'
            f'<body style="font-family:system-ui,sans-serif;background:#EEF4FE;color:#17396D;display:grid;place-items:center;'
            f'height:100vh;margin:0"><div style="background:#fff;border:1px solid #DCE7F8;border-radius:16px;padding:28px 32px;'
            f'max-width:460px"><h2 style="margin:0 0 8px">{html.escape(title)}</h2><p style="color:#5F7DA8">{html.escape(msg)}</p>'
            f'<a href="/" style="color:#2168F3">返回管理台</a></div></body>')


# ------------------------------------------------------------------ 当前用户（自助）
@app.get("/api/me")
def me(request: Request):
    return _me(request.state.principal)


@app.get("/api/me/permissions")
def my_permissions(request: Request):
    p = request.state.principal
    if not p or not p.registered:
        return {"registered": False, "all": False, "datasets": []}
    return J({"registered": True, **permission.describe(permission.access_for(p))})


@app.get("/api/me/tokens")
def my_tokens(request: Request):
    p = request.state.principal
    return J(repo.list_tokens(p.emp_no) if p and p.emp_no else [])


@app.post("/api/me/tokens")
def my_token_issue(request: Request, data: dict = Body(default={})):
    p = request.state.principal
    if not (settings.self_service_tokens and settings.iam_enabled):
        raise repo.Invalid("未开启自助签发令牌，请联系管理员")
    if not p or not p.emp_no:
        raise repo.Invalid("请使用 IAM 单点登录后再签发个人令牌")
    if not p.registered:
        raise repo.Invalid("你的工号未登记或已停用，请联系管理员")
    days = int(data.get("days") or min(30, settings.token_max_days))
    return J(auth.issue_token(p.emp_no, days, (data.get("note") or "自助签发")[:100], max_days=settings.token_max_days))


@app.post("/api/me/tokens/{jti}/revoke")
def my_token_revoke(request: Request, jti: str):
    p = request.state.principal
    row = db.fetchone("SELECT emp_no FROM permission.access_token WHERE jti=%s", (jti,))
    if not row or not p or (row["emp_no"] != p.emp_no and not p.is_admin):
        raise repo.NotFound("令牌不存在")
    repo.revoke_token(jti)
    return {"ok": True}


@app.get("/api/meta")
def meta():
    return J({
        "datasource_types": [
            {"value": "mysql", "label": "MySQL"}, {"value": "postgresql", "label": "PostgreSQL"},
            {"value": "oracle", "label": "Oracle"}, {"value": "starrocks", "label": "StarRocks"},
            {"value": "api", "label": "HTTP API"},
        ],
        "embedding": embedding.info(),
        "mcp_url": services.mcp_endpoint(),
        "public_base_url": settings.public_base_url,
        "stats": repo.stats(),
        "auth": not open_mode(),
        "auth_mode": settings.auth_mode,
        "app_name": settings.app_name,
        "jwt": {"emp_claim": settings.jwt_emp_claim, "issuer": settings.jwt_issuer, "audience": settings.jwt_audience,
                "algorithms": settings.jwt_algorithms, "external_public_key": bool(settings.jwt_public_key),
                "admin_token_for_mcp": bool(settings.admin_token and settings.mcp_allow_admin_token)},
    })


# ------------------------------------------------------------------ 数据源
@app.get("/api/datasources")
def ds_list():
    return J(repo.list_datasources())


@app.post("/api/datasources")
def ds_create(data: dict = Body(...)):
    return J(repo.create_datasource(data))


@app.get("/api/datasources/{ds_id}")
def ds_get(ds_id: int):
    return J(repo.get_datasource(ds_id))


@app.put("/api/datasources/{ds_id}")
def ds_update(ds_id: int, data: dict = Body(...)):
    return J(repo.update_datasource(ds_id, data))


@app.delete("/api/datasources/{ds_id}")
def ds_delete(ds_id: int):
    repo.delete_datasource(ds_id)
    return {"ok": True}


@app.post("/api/datasources/test")
def ds_test_unsaved(data: dict = Body(...)):
    """测试未保存的配置；若带 id 且未填写 secret，则沿用已保存的密钥。"""
    from . import crypto

    row = {"id": None, "name": data.get("name") or "test", "type": data.get("type"), "config": data.get("config") or {},
           "enabled": True, "secret": crypto.encrypt(data.get("secret")) if data.get("secret") else None}
    if data.get("id") and not data.get("secret"):
        row["secret"] = repo.get_datasource(int(data["id"]), with_secret=True)["secret"]
    conn = get_connector(row)
    try:
        return J(conn.test())
    finally:
        conn.close()


@app.post("/api/datasources/{ds_id}/test")
def ds_test(ds_id: int):
    return J(get_connector(repo.get_datasource(ds_id, with_secret=True)).test())


@app.get("/api/datasources/{ds_id}/tables")
def ds_tables(ds_id: int, schema: str | None = None):
    return J(get_connector(repo.get_datasource(ds_id, with_secret=True)).list_tables(schema))


@app.get("/api/datasources/{ds_id}/describe")
def ds_describe(ds_id: int, table: str, sample_rows: int = 10):
    return J(services.describe_table(repo.get_datasource(ds_id, with_secret=True), table, sample_rows))


@app.post("/api/datasources/{ds_id}/query")
def ds_query(ds_id: int, data: dict = Body(...)):
    return J(services.run_query(repo.get_datasource(ds_id, with_secret=True), data.get("sql", ""), data.get("limit", 200)))


# ------------------------------------------------------------------ 数据集
@app.get("/api/datasets")
def dataset_list():
    return J(repo.list_datasets())


@app.post("/api/datasets")
def dataset_create(data: dict = Body(...)):
    return J(repo.create_dataset(data))


@app.get("/api/datasets/search")
def dataset_search(q: str, top_k: int = 5):
    return J(repo.search_datasets(q, top_k))


@app.post("/api/datasets/reindex")
def dataset_reindex():
    return J(repo.reindex_all())


@app.get("/api/datasets/{dataset_id}")
def dataset_get(dataset_id: int):
    return J(repo.get_dataset(dataset_id))


@app.put("/api/datasets/{dataset_id}")
def dataset_update(dataset_id: int, data: dict = Body(...)):
    return J(repo.update_dataset(dataset_id, data))


@app.delete("/api/datasets/{dataset_id}")
def dataset_delete(dataset_id: int):
    repo.delete_dataset(dataset_id)
    return {"ok": True}


@app.get("/api/scenarios")
def scenarios():
    return J([r["business_scenario"] for r in repo.list_datasets()])


# ------------------------------------------------------------------ 样式规则
@app.get("/api/style-rules")
def rule_list():
    return J(repo.list_rules())


@app.get("/api/style-tokens")
def style_tokens():
    """样式编辑器元数据：令牌分组与中文说明、规则条目类别与示例、内置默认值、生效顺序。"""
    defaults, _ = design.resolve()
    return J({"groups": design.TOKEN_GROUPS, "categories": design.ITEM_CATEGORIES, "defaults": defaults,
              "precedence": design.PRECEDENCE})


@app.post("/api/style-rules/effective")
def style_effective(data: dict = Body(...)):
    """某业务场景的最终生效样式（每项标明来源）。exclude_id + draft 用于编辑时计算“继承值”与草稿效果。"""
    return J(repo.effective_style(data.get("scenario") or None, data.get("exclude_id"), data.get("draft")))


@app.post("/api/style-rules/preview", response_class=HTMLResponse)
def style_preview(data: dict = Body(...)):
    from .style_preview import render_preview

    draft = data.get("draft")
    if draft is not None:
        draft = {**draft, "theme": repo._clean_theme(draft.get("theme") or {})}
    return HTMLResponse(render_preview(data.get("scenario") or None, data.get("exclude_id"), draft))


@app.get("/api/style-rules/resolve")
def rule_resolve(scenario: str | None = None, q: str | None = None):
    return J(repo.style_rules_for(scenario, q))


@app.post("/api/style-rules")
def rule_create(data: dict = Body(...)):
    return J(repo.create_rule(data))


@app.get("/api/style-rules/{rule_id}")
def rule_get(rule_id: int):
    return J(repo.get_rule(rule_id))


@app.put("/api/style-rules/{rule_id}")
def rule_update(rule_id: int, data: dict = Body(...)):
    return J(repo.update_rule(rule_id, data))


@app.delete("/api/style-rules/{rule_id}")
def rule_delete(rule_id: int):
    repo.delete_rule(rule_id)
    return {"ok": True}


# ------------------------------------------------------------------ 报告模板
@app.get("/api/templates")
def tpl_list(q: str | None = None):
    return J(repo.list_templates(q))


@app.post("/api/templates")
def tpl_create(data: dict = Body(...)):
    return J(repo.save_template(data))


@app.post("/api/templates/validate")
def tpl_validate(data: dict = Body(...)):
    return J(validate(data.get("markdown", ""), data.get("dataset_id"), bool(data.get("execute", True))))


@app.post("/api/templates/preview", response_class=HTMLResponse)
def tpl_preview(data: dict = Body(...)):
    try:
        html, _meta = render(data.get("markdown", ""), data.get("dataset_id"))
    except (RenderError, TemplateError, ConnectorError, repo.Invalid) as e:
        raise HTTPException(400, str(e)) from e
    return HTMLResponse(html)


@app.get("/api/templates/{tpl_id}")
def tpl_get(tpl_id: int):
    t = repo.get_template(tpl_id)
    t["versions"] = repo.template_versions(tpl_id)
    return J(t)


@app.put("/api/templates/{tpl_id}")
def tpl_update(tpl_id: int, data: dict = Body(...)):
    return J(repo.save_template(data, tpl_id))


@app.delete("/api/templates/{tpl_id}")
def tpl_delete(tpl_id: int):
    repo.delete_template(tpl_id)
    return {"ok": True}


@app.get("/api/templates/{tpl_id}/versions/{version}")
def tpl_version(tpl_id: int, version: int):
    row = db.fetchone(
        "SELECT version, markdown, created_at FROM report_template.report_template_version WHERE template_id=%s AND version=%s",
        (tpl_id, version),
    )
    if not row:
        raise repo.NotFound("版本不存在")
    return J(row)


@app.post("/api/templates/{tpl_id}/render")
def tpl_render(tpl_id: int, data: dict = Body(default={})):
    return J(services.render_template_to_report(tpl_id, (data or {}).get("name")))


# ------------------------------------------------------------------ 报告
@app.get("/api/reports")
def report_list(template_id: int | None = None):
    return J(repo.list_reports(template_id))


@app.get("/api/reports/{report_id}")
def report_get(report_id: int):
    r = repo.get_report(report_id, with_html=False)
    r["url"] = f"{settings.public_base_url}/reports/{report_id}"
    return J(r)


@app.delete("/api/reports/{report_id}")
def report_delete(report_id: int):
    repo.delete_report(report_id)
    return {"ok": True}


def _can_view_report(request: Request, r: dict) -> bool:
    if request.state.share_ok:
        return True
    p = request.state.principal
    return bool(p and (p.is_admin or (p.emp_no and r.get("created_by") == p.emp_no)))


@app.get("/reports/{report_id}", response_class=HTMLResponse, include_in_schema=False)
def report_view(request: Request, report_id: int):
    r = repo.get_report(report_id)
    if not _can_view_report(request, r):
        return HTMLResponse(_auth_page("无权查看该报告", "只有报告创建人与管理员可以查看，或使用带查看凭证的分享链接。"), status_code=403)
    return HTMLResponse(r["html"])


@app.get("/reports/{report_id}/download", include_in_schema=False)
def report_download(request: Request, report_id: int, standalone_file: bool = Query(True, alias="standalone")):
    r = repo.get_report(report_id)
    if not _can_view_report(request, r):
        return HTMLResponse(_auth_page("无权下载该报告", "只有报告创建人与管理员可以下载。"), status_code=403)
    html = standalone(r["html"]) if standalone_file else r["html"]
    fname = quote(f"{r['name']}.html")
    return Response(html, media_type="text/html; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"})


# ------------------------------------------------------------------ 权限：员工 / 授权 / 令牌
@app.get("/api/employees")
def emp_list():
    return J(repo.list_employees())


@app.post("/api/employees")
def emp_create(data: dict = Body(...)):
    return J(repo.save_employee(data))


@app.put("/api/employees/{emp_no}")
def emp_update(emp_no: str, data: dict = Body(...)):
    return J(repo.save_employee(data, emp_no))


@app.delete("/api/employees/{emp_no}")
def emp_delete(emp_no: str):
    repo.delete_employee(emp_no)
    return {"ok": True}


@app.get("/api/grants")
def grant_list():
    return J(repo.list_grants())


@app.post("/api/grants")
def grant_add(data: dict = Body(...)):
    return J(repo.add_grants(data))


@app.delete("/api/grants/{grant_id}")
def grant_delete(grant_id: int):
    repo.delete_grant(grant_id)
    return {"ok": True}


@app.get("/api/tokens")
def token_list(emp_no: str | None = None):
    return J(repo.list_tokens(emp_no))


@app.post("/api/tokens")
def token_issue(data: dict = Body(...)):
    """签发 MCP 访问令牌（JWT，含员工工号）。令牌明文只返回这一次。"""
    days = int(data.get("days") or 90)
    if not 1 <= days <= 3650:
        raise repo.Invalid("有效期需在 1-3650 天之间")
    return J(auth.issue_token((data.get("emp_no") or "").strip(), days, data.get("note") or ""))


@app.post("/api/tokens/{jti}/revoke")
def token_revoke(jti: str):
    repo.revoke_token(jti)
    return {"ok": True}


@app.get("/api/permissions/check")
def perm_check(emp_no: str):
    emp = db.fetchone("SELECT * FROM permission.employee WHERE emp_no=%s", (emp_no,))
    if not emp:
        return J({"registered": False, "all": False, "datasets": []})
    p = auth.Principal(emp_no=emp_no, name=emp["name"], is_admin=emp["is_admin"], registered=emp["enabled"],
                       roles=list(emp["roles"] or []))
    return J({"registered": emp["enabled"], "employee": emp, **permission.describe(permission.access_for(p))})


# ------------------------------------------------------------------ 演示 API 数据源
@app.get("/demo-api/tickets", include_in_schema=False)
def demo_tickets():
    items = seed.demo_tickets()
    return {"code": 0, "message": "ok", "data": {"total": len(items), "items": items}}
