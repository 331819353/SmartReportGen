"""管理后台 REST API + 管理页面 + 报告浏览。"""
from __future__ import annotations

import html
import json
import logging
import re
import secrets
from contextlib import asynccontextmanager
from functools import cache
from hashlib import sha256
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlencode, urlsplit
from uuid import UUID

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.openapi.docs import get_swagger_ui_html, get_swagger_ui_oauth2_redirect_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from markdown_it import MarkdownIt
from psycopg.errors import ForeignKeyViolation, UniqueViolation
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import auth, db, design, embedding, iam, mcp_oauth, permission, repo, report_data, seed, services
from .config import settings
from .connectors import ConnectorError, get_connector
from .renderer import RenderError, render, standalone, validate
from .template_parser import TemplateError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("smartreportgen.api")
STATIC = Path(__file__).with_name("static")
DOCS = Path(__file__).with_name("docs")
if not (DOCS / "HTTP_API.md").is_file():
    DOCS = Path(__file__).resolve().parents[2] / "docs"


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
    if not iam.configured():
        log.warning("海尔 IAM 未配置齐全（缺 %s），受保护的接口不可用", "、".join(iam.missing_vars()))
    yield


app = FastAPI(
    title=settings.app_name,
    version="1.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    description=(
        "管理台 HTTP API，使用海尔 IAM access token。"
        f"完整示例见 [HTTP 接入说明]({settings.public_path('/api-guide')})。"
    ),
)
app.include_router(mcp_oauth.router)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/docs", include_in_schema=False)
def api_docs():
    return get_swagger_ui_html(
        openapi_url=settings.public_path("/openapi.json"),
        title=f"{settings.app_name} - Swagger UI",
        oauth2_redirect_url=settings.public_path("/docs/oauth2-redirect"),
    )


@app.get("/docs/oauth2-redirect", include_in_schema=False)
def api_docs_oauth2_redirect():
    return get_swagger_ui_oauth2_redirect_html()


def _openapi():
    if app.openapi_schema is None:
        schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
        # FastAPI 的鉴权在中间件中执行，显式声明 Bearer 供 Swagger 和生成客户端使用。
        components = schema.setdefault("components", {})
        components["securitySchemes"] = {"HaierIAM": {"type": "http", "scheme": "bearer",
                                                     "description": "海尔 IAM 签发的原始 access token"}}
        schema["externalDocs"] = {"description": "HTTP 接入说明", "url": settings.public_path("/api-guide")}
        schema["servers"] = [{"url": settings.public_base_url}]
        for path, operations in schema["paths"].items():
            if path.startswith(("/api/", "/reports/")) and not path.startswith("/api/auth/"):
                for operation in operations.values():
                    operation["security"] = [{"HaierIAM": []}]
                    for status, description in (("401", "IAM 身份缺失或令牌无效"), ("403", "没有操作权限"),
                                                ("503", "IAM 核验暂不可用")):
                        operation["responses"].setdefault(status, {"description": description})
        app.openapi_schema = schema
    return app.openapi_schema


app.openapi = _openapi


# ------------------------------------------------------------------ 认证：海尔 IAM Bearer / 管理台浏览器会话
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    request.state.principal = None
    if not (path.startswith("/api/") or path.startswith("/reports/")):
        return await call_next(request)
    auth_config_path = path in ("/api/auth/config", "/api/auth/check")
    authorization = request.headers.get("authorization")
    try:
        # 显式 Bearer 优先，错误的 Bearer 不回退到浏览器 Cookie。
        if authorization is not None:
            principal = await run_in_threadpool(auth.verify_bearer, authorization)
        else:
            principal = await run_in_threadpool(auth.verify_session, request.cookies.get(auth.SESSION_COOKIE))
    except auth.AuthUnavailable as exc:
        if auth_config_path and authorization is None:
            principal = None
        else:
            return JSONResponse({"detail": str(exc)}, status_code=503)
    except auth.AuthError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401,
                            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'})
    request.state.principal = principal
    if auth_config_path:
        return await call_next(request)
    if not principal:
        # 点击报告链接时引导浏览器完成 IAM 登录，程序调用继续收到标准 401。
        if (authorization is None and request.method == "GET"
                and re.fullmatch(r"/reports/(?:\d+(?:/download|/content)?|preview/[\da-f-]{36})", path)
                and "text/html" in request.headers.get("accept", "")):
            target = settings.public_path(path + (f"?{request.url.query}" if request.url.query else ""))
            return RedirectResponse(settings.public_path("/auth/iam/login") + "?next=" + quote(target, safe=""),
                                    status_code=302)
        return JSONResponse({"detail": "未登录或 IAM access token 已失效"}, status_code=401,
                            headers={"WWW-Authenticate": 'Bearer realm="smartreportgen"'})
    if request.method == "GET" and path in ("/api/me", "/api/me/permissions"):
        return await call_next(request)
    if not principal.registered:
        return JSONResponse({"detail": "工号未登记或已停用"}, status_code=403)
    if principal.is_admin or path.startswith("/reports/") or _user_allowed(request.method, path):
        response = await call_next(request)
        # 每次打开报告都重新校验分享权限，避免撤销后浏览器继续使用缓存。
        if path.startswith(("/reports/", "/api/reports")):
            response.headers["Cache-Control"] = "no-store"
        return response
    return JSONResponse({"detail": "需要管理员权限；如需开通请联系管理员"}, status_code=403)


# 普通用户可访问的接口（默认拒绝）；接口内部再按“有权限 / 本人创建”过滤
USER_ALLOW = [(m, re.compile(rx)) for m, rx in [
    ("GET", r"^/api/meta$"),
    ("GET", r"^/api/datasources(/\d+)?$"),
    ("GET", r"^/api/datasets(/search|/\d+)?$"), ("GET", r"^/api/scenarios$"),
    ("GET", r"^/api/style-tokens$"), ("GET", r"^/api/style-rules(/resolve|/\d+)?$"),
    ("POST", r"^/api/style-rules(/effective|/preview)?$"), ("PUT", r"^/api/style-rules/\d+$"),
    ("DELETE", r"^/api/style-rules/\d+$"),
    ("GET", r"^/api/templates(/\d+(/versions/\d+)?)?$"), ("POST", r"^/api/templates(/validate|/preview|/\d+/render)?$"),
    ("PUT", r"^/api/templates/\d+$"), ("DELETE", r"^/api/templates/\d+$"),
    ("GET", r"^/api/reports(/\d+(/shares|/share-recipients|/versions)?)?$"), ("PUT", r"^/api/reports/\d+(/selection)?$"),
    ("POST", r"^/api/reports/(merge|\d+/shares)$"), ("DELETE", r"^/api/reports/\d+(/shares/[^/]+)?$"),
]]


def _user_allowed(method: str, path: str) -> bool:
    return any(method == m and rx.match(path) for m, rx in USER_ALLOW)


@app.exception_handler(repo.NotFound)
async def _nf(_r, e):
    return JSONResponse({"detail": str(e)}, status_code=404)


for _exc in (repo.Invalid, ConnectorError, RenderError, TemplateError, RuntimeError, auth.AuthError,
             report_data.ReportDataError):
    app.add_exception_handler(_exc, lambda _r, e: JSONResponse({"detail": str(e)}, status_code=400))


@app.exception_handler(permission.PermissionDenied)
async def _denied(_r, exc):
    return JSONResponse({"detail": str(exc)}, status_code=403)


@app.exception_handler(repo.ReportConflict)
async def _report_conflict(_r, exc):
    return JSONResponse({"detail": str(exc)}, status_code=409)


@app.exception_handler(UniqueViolation)
@app.exception_handler(ForeignKeyViolation)
async def _resource_conflict(_r, exc):
    # 并发创建重名资源或引用已删除对象时，返回可处理的冲突，不泄露数据库异常详情。
    detail = "资源已存在" if isinstance(exc, UniqueViolation) else "资源引用冲突，请重新读取后提交"
    return JSONResponse({"detail": detail}, status_code=409)


def J(obj) -> JSONResponse:
    return JSONResponse(jsonable_encoder(obj))


# ------------------------------------------------------------------ 页面
@cache
def _admin_index_html() -> str:
    # 管理台资源随镜像发布。内容变动时换用新的资源 URL，避免浏览器沿用旧脚本或样式。
    admin = STATIC / "admin"
    page = (admin / "index.html").read_text(encoding="utf-8")
    for filename in ("app.css", "app.js"):
        version = sha256((admin / filename).read_bytes()).hexdigest()[:12]
        raw_url = f"/static/admin/{filename}"
        url = settings.public_path(raw_url)
        page = page.replace(f'"{raw_url}"', f'"{url}?v={version}"')
    page = page.replace('"/static/vendor/vue.global.prod.js"',
                        f'"{settings.public_path("/static/vendor/vue.global.prod.js")}"')
    page = page.replace('window.__SRG_BASE_PATH__ = "";',
                        f"window.__SRG_BASE_PATH__ = {json.dumps(settings.public_base_path)};")
    return page


@app.get("/", include_in_schema=False)
def index():
    return HTMLResponse(_admin_index_html(), headers={"Cache-Control": "no-cache"})


@app.get("/healthz")
def healthz():
    db.fetchone("SELECT 1")
    return {"ok": True}


@cache
def _api_guide_html() -> str:
    # 页面直接渲染同一份仓库文档，避免在两处维护 HTTP 契约。
    renderer = MarkdownIt("commonmark").enable("table")
    markdown = (DOCS / "HTTP_API.md").read_text(encoding="utf-8")
    markdown = markdown.replace("../skills/smart-report/references/template-spec.md",
                                settings.public_path("/api-guide/template-spec"))
    body = renderer.render(markdown)
    home = settings.public_path("/")
    docs = settings.public_path("/docs")
    openapi = settings.public_path("/openapi.json")
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>SmartReportGen HTTP 接入说明</title>
<style>body{{max-width:70rem;margin:2rem auto;padding:0 1rem;font:16px/1.65 system-ui;color:#24334b}}
pre{{overflow:auto;padding:1rem;background:#f2f5fa}}table{{display:block;overflow:auto;border-collapse:collapse}}
td,th{{padding:.4rem .7rem;border:1px solid #dbe3ee}}th{{background:#f2f5fa}}a{{color:#235fba}}</style>
<nav><a href="{home}">管理台</a> · <a href="{docs}">接口调试</a> · <a href="{openapi}">OpenAPI JSON</a></nav>
<main>{body}</main></html>'''


@app.get("/api-guide", response_class=HTMLResponse, include_in_schema=False)
def api_guide():
    return _api_guide_html()


@app.get("/api-guide/template-spec", include_in_schema=False)
def api_template_spec():
    spec = DOCS / "skill" / "references" / "template-spec.md"
    if not spec.is_file():
        spec = DOCS.parent / "skills" / "smart-report" / "references" / "template-spec.md"
    return Response(spec.read_text(encoding="utf-8"), media_type="text/plain")


IAM_BROWSER_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def _cookie_secure() -> bool:
    return settings.public_base_url.startswith("https://")


def _me(p: auth.Principal | None) -> dict:
    if not p:
        return {"logged_in": False}
    return {"logged_in": True, "emp_no": p.emp_no, "name": p.name, "is_admin": p.is_admin, "via": p.via,
            "registered": p.registered, "roles": p.roles}


@app.get("/api/auth/config")
def auth_config(request: Request):
    return {"mode": "iam", "iam_configured": iam.configured(), "mcp_oauth": mcp_oauth.enabled(),
            "me": _me(request.state.principal)}


@app.get("/api/auth/check")
def auth_check(request: Request):
    """返回当前请求是否具有可用的 IAM 身份。"""
    principal = request.state.principal
    return {"required": True, "ok": bool(principal and principal.registered)}


@app.get("/auth/iam/login", response_class=HTMLResponse, responses={302: {"description": "跳转到海尔 IAM"}})
def iam_login(request: Request, next: str = ""):
    if not iam.configured():
        return HTMLResponse(_auth_page("海尔 IAM 暂不可用", "请联系系统管理员。"), status_code=503)
    next_path = iam.safe_next(next or settings.public_path("/"))
    callback = urlsplit(iam.redirect_uri())
    # 登录 Cookie 仅属于签发它的主机。先切换到配置中的回调主机，例如从 127.0.0.1 切换到 localhost。
    if callback.hostname and request.url.hostname != callback.hostname:
        query = urlencode({"next": next_path})
        login_path = settings.public_path("/auth/iam/login")
        return RedirectResponse(f"{callback.scheme}://{callback.netloc}{login_path}?{query}",
                                status_code=302, headers=IAM_BROWSER_HEADERS)
    # 用独立的短时 Cookie 绑定此次登录，避免其他浏览器的 IAM 回调替换当前用户会话。
    browser = secrets.token_urlsafe(32)
    state = iam.pack_state(browser, next_path)
    response = RedirectResponse(iam.authorize_url(state), status_code=302, headers=IAM_BROWSER_HEADERS)
    response.set_cookie(auth.LOGIN_COOKIE, browser, path=auth.cookie_path("/auth/iam/callback"), httponly=True,
                        samesite="lax", secure=_cookie_secure(), max_age=600)
    return response


@app.get("/auth/iam/callback", include_in_schema=False)
def iam_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if state.startswith(mcp_oauth.STATE_PREFIX):
        return mcp_oauth.complete_iam(request, code, state, error)
    browser = request.cookies.get(auth.LOGIN_COOKIE)
    try:
        # 单独识别未带回 Cookie 的回调，便于区分浏览器会话丢失和 IAM 授权失败。
        if error or not code:
            log.info("IAM 管理台回调未完成：IAM 返回错误或缺少授权码")
            raise iam.IamError("海尔 IAM 授权未完成，请从管理台重新登录")
        if not browser:
            log.info("IAM 管理台回调未完成：缺少登录 Cookie")
            raise iam.IamError("浏览器未带回登录凭据，请从管理台重新登录")
        nxt = iam.unpack_state(browser, state)
        token, ticket = iam.exchange_code(code)
        account = iam.resolve_account(token)
        principal = auth.principal_for(account.emp_no, account.name, via="session", register="iam_login")
    except iam.IamError as exc:
        return HTMLResponse(_auth_page("单点登录失败", str(exc)), status_code=400, headers=IAM_BROWSER_HEADERS)
    except iam.IamUnavailable as exc:
        log.warning("IAM 回调失败：%s", exc)
        return HTMLResponse(_auth_page("单点登录失败", "IAM 暂时无法核实，请稍后重试"),
                            status_code=503, headers=IAM_BROWSER_HEADERS)
    if not principal.registered:
        return HTMLResponse(_auth_page("工号未登记或已停用", "请联系系统管理员。"),
                            status_code=403, headers=IAM_BROWSER_HEADERS)
    seconds = settings.session_hours * 3600
    if lifetime := iam.expires_in(ticket):
        seconds = min(seconds, lifetime)
    session_id = auth.start_session(principal.emp_no, token, seconds)
    auth.end_session(request.cookies.get(auth.SESSION_COOKIE))
    response = RedirectResponse(nxt, status_code=302, headers=IAM_BROWSER_HEADERS)
    response.delete_cookie(auth.LOGIN_COOKIE, path=auth.cookie_path("/auth/iam/callback"))
    response.set_cookie(auth.SESSION_COOKIE, session_id, httponly=True, samesite="lax",
                        secure=_cookie_secure(), max_age=seconds, path=auth.cookie_path("/"))
    return response


@app.post("/auth/logout")
def logout(request: Request):
    auth.end_session(request.cookies.get(auth.SESSION_COOKIE))
    response = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
    response.delete_cookie(auth.SESSION_COOKIE, path=auth.cookie_path("/"))
    return response


def _auth_page(title: str, msg: str) -> str:
    return (f'<!doctype html><meta charset="utf-8"><title>{html.escape(title)}</title>'
            f'<body style="font-family:system-ui,sans-serif;background:#EEF4FE;color:#17396D;display:grid;place-items:center;'
            f'height:100vh;margin:0"><div style="background:#fff;border:1px solid #DCE7F8;border-radius:16px;padding:28px 32px;'
            f'max-width:460px"><h2 style="margin:0 0 8px">{html.escape(title)}</h2><p style="color:#5F7DA8">{html.escape(msg)}</p>'
            f'<a href="{settings.public_path("/")}" style="color:#2168F3">返回管理台</a></div></body>')


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


# ------------------------------------------------------------------ 角色与归属
def _p(request: Request) -> auth.Principal:
    return request.state.principal


def _is_admin(request: Request) -> bool:
    return bool(_p(request) and _p(request).is_admin)


def _require_admin(request: Request) -> None:
    if not _is_admin(request):
        raise HTTPException(403, "需要管理员权限")


def _access(request: Request) -> permission.Access:
    p = _p(request)
    return permission.access_for(p) if p.is_admin or p.registered else permission.Access(principal=p)


def _owner_filter(request: Request) -> str | None:
    """管理员看全部（None），普通用户只看本人。"""
    return None if _is_admin(request) else (_p(request).emp_no or "")


def _check_owner(request: Request, created_by: str | None, what: str) -> None:
    if not _is_admin(request) and created_by != _p(request).emp_no:
        raise HTTPException(403, f"只能操作本人创建的{what}")


def _guard(request: Request):
    """普通用户在管理台校验 / 预览 / 生成报告时，与 MCP 一样按其数据权限校验每条 SQL。"""
    if _is_admin(request):
        return None
    acc = _access(request)

    def guard(dataset):
        # 普通用户的取数权限来自场景；直填数据源不能绕过场景的逐表条件。
        if not dataset:
            raise permission.PermissionDenied("请为查询关联有权限的业务场景，并使用该场景的数据源")
        if not acc.can_dataset(dataset["id"]):
            raise permission.PermissionDenied("无权使用该业务场景（数据集）")
        return acc.tables.get(dataset["id"], set())

    return guard


def _require_dataset(request: Request, dataset_id) -> None:
    if dataset_id and not _is_admin(request) and not _access(request).can_dataset(int(dataset_id)):
        raise HTTPException(403, "无权使用该业务场景（数据集）")


def _scenario_dataset(name: str | None) -> dict | None:
    return db.fetchone("SELECT id FROM dataset.dataset WHERE business_scenario=%s", (name,)) if name else None


@app.get("/api/meta")
def meta(request: Request):
    if _is_admin(request):
        stats = repo.stats()
    else:
        acc = _access(request)
        me_no = _p(request).emp_no or ""
        ds = [d for d in repo.list_datasets() if d["enabled"] and acc.can_dataset(d["id"])]
        stats = {"datasources": len({d["datasource_id"] for d in ds}), "datasets": len(ds),
                 "templates": len(repo.list_templates(owner=me_no)), "reports": len(repo.list_reports(_p(request))),
                 "style_rules": len(repo.list_rules(owner=me_no, all_owners=False)), "employees": 0}
    return J({
        "datasource_types": [
            {"value": "mysql", "label": "MySQL"}, {"value": "postgresql", "label": "PostgreSQL"},
            {"value": "oracle", "label": "Oracle"}, {"value": "starrocks", "label": "StarRocks"},
            {"value": "api", "label": "HTTP API"},
        ],
        "embedding": embedding.info(),
        "mcp_url": services.mcp_endpoint(),
        "public_base_url": settings.public_base_url,
        "stats": stats,
        "auth": True,
        "auth_mode": "iam",
        "app_name": settings.app_name,
    })


# ------------------------------------------------------------------ 数据源（管理员管理；普通用户只读、只看有权限的）
DS_PUBLIC = ("id", "name", "type", "description", "enabled", "created_at", "updated_at")


def _visible_datasource_ids(request: Request) -> set[int]:
    acc = _access(request)
    return {d["datasource_id"] for d in repo.list_datasets() if d["enabled"] and acc.can_dataset(d["id"])}


@app.get("/api/datasources")
def ds_list(request: Request):
    rows = repo.list_datasources()
    if _is_admin(request):
        return J(rows)
    ids = _visible_datasource_ids(request)
    return J([{k: r[k] for k in DS_PUBLIC} for r in rows if r["id"] in ids])


@app.post("/api/datasources")
def ds_create(request: Request, data: dict = Body(...)):
    _require_admin(request)
    return J(repo.create_datasource(data))


@app.get("/api/datasources/{ds_id}")
def ds_get(request: Request, ds_id: int):
    r = repo.get_datasource(ds_id)
    if _is_admin(request):
        return J(r)
    if ds_id not in _visible_datasource_ids(request):
        raise repo.NotFound("数据源不存在")
    return J({k: r[k] for k in DS_PUBLIC})


@app.put("/api/datasources/{ds_id}")
def ds_update(request: Request, ds_id: int, data: dict = Body(...)):
    _require_admin(request)
    return J(repo.update_datasource(ds_id, data))


@app.delete("/api/datasources/{ds_id}")
def ds_delete(request: Request, ds_id: int):
    _require_admin(request)
    repo.delete_datasource(ds_id)
    return {"ok": True}


@app.post("/api/datasources/test")
def ds_test_unsaved(request: Request, data: dict = Body(...)):
    """测试未保存的配置；若带 id 且未填写 secret，则沿用已保存的密钥。"""
    from . import crypto

    _require_admin(request)
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
def ds_test(request: Request, ds_id: int):
    _require_admin(request)
    return J(get_connector(repo.get_datasource(ds_id, with_secret=True)).test())


@app.get("/api/datasources/{ds_id}/tables")
def ds_tables(request: Request, ds_id: int, schema: str | None = None):
    _require_admin(request)
    return J(get_connector(repo.get_datasource(ds_id, with_secret=True)).list_tables(schema))


@app.get("/api/datasources/{ds_id}/describe")
def ds_describe(request: Request, ds_id: int, table: str, sample_rows: int = 10):
    _require_admin(request)
    return J(services.describe_table(repo.get_datasource(ds_id, with_secret=True), table, sample_rows))


@app.get("/api/datasources/{ds_id}/columns")
def ds_columns(request: Request, ds_id: int, table: str):
    """场景过滤编辑器只读取字段，不必扫描行数或数据画像。"""
    _require_admin(request)
    return J(get_connector(repo.get_datasource(ds_id, with_secret=True)).columns(table))


@app.get("/api/datasources/{ds_id}/values")
def ds_values(request: Request, ds_id: int, table: str = Query(min_length=1, max_length=512),
              column: str = Query(min_length=1, max_length=256), q: str = Query("", max_length=120),
              limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=10000)):
    """编辑场景时读取指定字段的原始候选值；复用数据源浏览的管理员权限。"""
    _require_admin(request)
    if "\x00" in table or "\x00" in column or "\x00" in q:
        raise repo.Invalid("表名、字段和搜索词不能包含空字符")
    result = get_connector(repo.get_datasource(ds_id, with_secret=True)).distinct_values(table, column, q, limit, offset)
    result["next_offset"] = offset + limit if result.pop("has_more") and offset + limit <= 10000 else None
    return J(result)


@app.post("/api/datasources/{ds_id}/query")
def ds_query(request: Request, ds_id: int, data: dict = Body(...)):
    _require_admin(request)
    return J(services.run_query(repo.get_datasource(ds_id, with_secret=True), data.get("sql", ""), data.get("limit", 200)))


# ------------------------------------------------------------------ 数据集（管理员管理；普通用户只读、只看有权限的表）
def _scoped_dataset(request: Request, ds: dict) -> dict:
    if _is_admin(request):
        return ds
    acc = _access(request)
    tables = [t for t in ds.get("tables", []) if acc.can_table(ds["id"], t["table_name"])]
    return {**ds, "tables": tables, "table_count": len(tables), "read_only": True}


@app.get("/api/datasets")
def dataset_list(request: Request, q: str | None = None):
    rows = repo.list_datasets(q=q)
    if _is_admin(request):
        return J(rows)
    acc = _access(request)
    return J([{**d, "table_count": len(acc.tables.get(d["id"], ())), "read_only": True}
              for d in rows if d["enabled"] and acc.can_dataset(d["id"])])


@app.post("/api/datasets")
def dataset_create(request: Request, data: dict = Body(...)):
    _require_admin(request)
    return J(repo.create_dataset(data))


@app.get("/api/datasets/search")
def dataset_search(request: Request, q: str, top_k: int = 5):
    if _is_admin(request):
        return J(repo.search_datasets(q, top_k))
    res = repo.search_datasets(q, top_k, dataset_ids=_access(request).dataset_ids())
    res["hits"] = [{**h, "dataset": _scoped_dataset(request, h["dataset"])} for h in res["hits"]]
    return J(res)


@app.post("/api/datasets/reindex")
def dataset_reindex(request: Request):
    _require_admin(request)
    return J(repo.reindex_all())


@app.get("/api/datasets/{dataset_id}")
def dataset_get(request: Request, dataset_id: int):
    if not _is_admin(request) and not _access(request).can_dataset(dataset_id):
        raise repo.NotFound("数据集不存在")
    return J(_scoped_dataset(request, repo.get_dataset(dataset_id)))


@app.put("/api/datasets/{dataset_id}")
def dataset_update(request: Request, dataset_id: int, data: dict = Body(...)):
    _require_admin(request)
    return J(repo.update_dataset(dataset_id, data))


@app.delete("/api/datasets/{dataset_id}")
def dataset_delete(request: Request, dataset_id: int):
    _require_admin(request)
    repo.delete_dataset(dataset_id)
    return {"ok": True}


@app.get("/api/scenarios")
def scenarios(request: Request):
    rows = repo.list_datasets()
    if _is_admin(request):
        return J([r["business_scenario"] for r in rows])
    acc = _access(request)
    return J([r["business_scenario"] for r in rows if r["enabled"] and acc.can_dataset(r["id"])])


# ------------------------------------------------------------------ 样式规则
# 通用规则：管理员配置，所有人可查看；业务场景规则：用户为自己有权限的场景配置，只对本人的报告生效
@app.get("/api/style-rules")
def rule_list(request: Request):
    if _is_admin(request):
        return J(repo.list_rules())
    return J(repo.list_rules(owner=_p(request).emp_no or "", all_owners=False))


@app.get("/api/style-tokens")
def style_tokens():
    """样式编辑器元数据：内置主题、样式分类与说明、展示要求类别与生效顺序。"""
    builtin, _ = design.resolve()
    return J({"groups": design.TOKEN_GROUPS, "categories": design.ITEM_CATEGORIES, "defaults": builtin,
              "retired_analysis_categories": design.RETIRED_ANALYSIS_CATEGORIES,
              "suggested": builtin,
              "precedence": design.PRECEDENCE})


def _style_owner(request: Request, exclude_id) -> str | None:
    """预览 / 继承值按谁的业务场景规则计算：编辑某条场景规则时取其创建人，否则取当前用户。"""
    if exclude_id:
        r = repo.get_rule(int(exclude_id))
        if r["scope"] == "scenario":
            _check_owner(request, r.get("created_by"), "样式规则")
            return r.get("created_by")
    return _p(request).emp_no


@app.post("/api/style-rules/effective")
def style_effective(request: Request, data: dict = Body(...)):
    """某业务场景对当前用户最终生效的样式（每项标明来源）。exclude_id + draft 用于编辑时计算“继承值”与草稿效果。"""
    owner = _style_owner(request, data.get("exclude_id"))
    return J(repo.effective_style(data.get("scenario") or None, data.get("exclude_id"), data.get("draft"), owner))


@app.post("/api/style-rules/preview", response_class=HTMLResponse)
def style_preview(request: Request, data: dict = Body(...)):
    from .style_preview import render_preview

    owner = _style_owner(request, data.get("exclude_id"))
    draft = data.get("draft")
    if draft is not None:
        draft = {**draft, "theme": repo._clean_theme(draft.get("theme") or {})}
    return HTMLResponse(render_preview(data.get("scenario") or None, data.get("exclude_id"), draft, owner))


@app.get("/api/style-rules/resolve")
def rule_resolve(request: Request, scenario: str | None = None, q: str | None = None):
    return J(repo.style_rules_for(scenario, q, owner=_p(request).emp_no))


def _check_rule_write(request: Request, data: dict, existing: dict | None = None) -> None:
    scope = data.get("scope") or (existing or {}).get("scope")
    if scope == "general" or (existing and existing["scope"] == "general"):
        if not _is_admin(request):
            raise HTTPException(403, "通用规则由管理员配置；你可以为自己有权限的业务场景配置个人样式")
        return
    if existing:
        _check_owner(request, existing.get("created_by"), "样式规则")
    if not _p(request).emp_no:
        raise HTTPException(400, "业务场景样式属于个人配置，请使用 IAM 单点登录后再配置")
    sc = data.get("business_scenario") or (existing or {}).get("business_scenario")
    ds = _scenario_dataset(sc)
    if not ds:
        raise HTTPException(400, f"业务场景「{sc}」不存在")
    _require_dataset(request, ds["id"])


@app.post("/api/style-rules")
def rule_create(request: Request, data: dict = Body(...)):
    _check_rule_write(request, data)
    data = {**data, "created_by": _p(request).emp_no}
    return J(repo.create_rule(data))


@app.get("/api/style-rules/{rule_id}")
def rule_get(request: Request, rule_id: int):
    r = repo.get_rule(rule_id)
    if r["scope"] == "scenario":
        _check_owner(request, r.get("created_by"), "样式规则")
    return J(r)


@app.put("/api/style-rules/{rule_id}")
def rule_update(request: Request, rule_id: int, data: dict = Body(...)):
    existing = repo.get_rule(rule_id)
    _check_rule_write(request, data, existing)
    data = {k: v for k, v in data.items() if k != "created_by"}
    return J(repo.update_rule(rule_id, data))


@app.delete("/api/style-rules/{rule_id}")
def rule_delete(request: Request, rule_id: int):
    _check_rule_write(request, {}, repo.get_rule(rule_id))
    repo.delete_rule(rule_id)
    return {"ok": True}


# ------------------------------------------------------------------ 报告模板（每人只看自己的，管理员看全部）
@app.get("/api/templates")
def tpl_list(request: Request, q: str | None = None):
    return J(repo.list_templates(q, owner=_owner_filter(request)))


@app.post("/api/templates")
def tpl_create(request: Request, data: dict = Body(...)):
    _require_dataset(request, data.get("dataset_id"))
    return J(repo.save_template({**data, "created_by": _p(request).emp_no}))


@app.post("/api/templates/validate")
def tpl_validate(request: Request, data: dict = Body(...)):
    _require_dataset(request, data.get("dataset_id"))
    return J(validate(data.get("markdown", ""), data.get("dataset_id"), bool(data.get("execute", True)),
                      guard=_guard(request), owner=_p(request).emp_no))


@app.post("/api/templates/preview", response_class=HTMLResponse)
def tpl_preview(request: Request, data: dict = Body(...)):
    _require_dataset(request, data.get("dataset_id"))
    try:
        html, _meta = render(data.get("markdown", ""), data.get("dataset_id"), guard=_guard(request),
                             owner=data.get("owner") if _is_admin(request) and data.get("owner") else _p(request).emp_no)
    except (RenderError, TemplateError, ConnectorError, repo.Invalid) as e:
        raise HTTPException(400, str(e)) from e
    return HTMLResponse(html)


@app.get("/api/templates/{tpl_id}")
def tpl_get(request: Request, tpl_id: int):
    t = repo.get_template(tpl_id)
    _check_owner(request, t.get("created_by"), "报告模板")
    t["versions"] = repo.template_versions(tpl_id)
    return J(t)


@app.put("/api/templates/{tpl_id}")
def tpl_update(request: Request, tpl_id: int, data: dict = Body(...)):
    _check_owner(request, repo.get_template(tpl_id).get("created_by"), "报告模板")
    _require_dataset(request, data.get("dataset_id"))
    return J(repo.save_template({k: v for k, v in data.items() if k != "created_by"}, tpl_id))


@app.delete("/api/templates/{tpl_id}")
def tpl_delete(request: Request, tpl_id: int):
    _check_owner(request, repo.get_template(tpl_id).get("created_by"), "报告模板")
    repo.delete_template(tpl_id)
    return {"ok": True}


@app.get("/api/templates/{tpl_id}/versions/{version}")
def tpl_version(request: Request, tpl_id: int, version: int):
    _check_owner(request, repo.get_template(tpl_id).get("created_by"), "报告模板")
    return J(repo.get_template_version(tpl_id, version))


@app.post("/api/templates/{tpl_id}/render")
def tpl_render(request: Request, tpl_id: int, data: dict = Body(default={})):
    tpl = repo.get_template(tpl_id)
    _check_owner(request, tpl.get("created_by"), "报告模板")
    return J(services.render_template_to_report(tpl, (data or {}).get("name"), guard=_guard(request)))


# ------------------------------------------------------------------ 报告（创建人、管理员及被分享的员工）
class ReportUpdate(BaseModel):
    name: str
    html: str | None = None
    markdown: str | None = None
    base_version: int | None = Field(default=None, ge=1, strict=True)
    expected_version: int | None = Field(default=None, ge=1, strict=True)
    revision: int | None = Field(default=None, ge=1, strict=True)
    change_note: str = Field(default="", max_length=500)


class ReportSelection(BaseModel):
    version: int | None = Field(ge=1, strict=True)


class ReportShare(BaseModel):
    emp_no: str = Field(min_length=1, max_length=256)
    access_level: Literal["view", "edit"]


class ReportMergeSource(BaseModel):
    id: int = Field(ge=1, strict=True)
    version: int = Field(ge=1, strict=True)


class ReportMerge(BaseModel):
    name: str = Field(default="合并报告", min_length=1)
    reports: list[ReportMergeSource] = Field(min_length=2)


@app.get("/api/reports")
def report_list(request: Request, template_id: int | None = None, q: str | None = None):
    return J(repo.list_reports(_p(request), template_id, q))


@app.post("/api/reports/merge", response_class=HTMLResponse)
def report_merge(request: Request, data: ReportMerge):
    name = data.name.strip()
    content = services.merge_reports(_p(request), name, [(r.id, r.version) for r in data.reports])
    return _report_attachment(content, name)


@app.get("/api/reports/{report_id}")
def report_get(request: Request, report_id: int, include_html: bool = False, include_source: bool = False,
               version: int | None = Query(default=None, ge=1)):
    r = repo.get_report(report_id, _p(request), with_html=include_html, version=version, with_source=include_source)
    return J({**r, **repo.report_links(r["id"], r["revision"])})


@app.put("/api/reports/{report_id}")
def report_update(request: Request, report_id: int, data: ReportUpdate):
    # 旧客户端的 revision 同时锁定读取的正文与最新版本，新客户端可以从历史版本分支。
    if data.revision is not None and (data.base_version is not None or data.expected_version is not None):
        raise repo.Invalid("请使用 revision，或使用 base_version 和 expected_version")
    return J(services.revise_report(report_id, _p(request), data.name, html=data.html, markdown=data.markdown,
                                  base_version=data.base_version or data.revision, change_note=data.change_note,
                                  expected_version=data.expected_version or data.revision,
                                  guard=_guard(request) if data.markdown is not None else None))


@app.get("/api/reports/{report_id}/versions")
def report_versions(request: Request, report_id: int):
    return J(repo.list_report_versions(report_id, _p(request)))


@app.put("/api/reports/{report_id}/selection")
def report_selection(request: Request, report_id: int, data: ReportSelection):
    r = repo.select_report_version(report_id, _p(request), data.version)
    return J({**r, **repo.report_links(r["id"], r["revision"])})


@app.delete("/api/reports/{report_id}")
def report_delete(request: Request, report_id: int):
    repo.delete_report(report_id, _p(request))
    return {"ok": True}


@app.get("/api/reports/{report_id}/shares")
def report_shares(request: Request, report_id: int):
    return J(repo.list_report_shares(report_id, _p(request)))


@app.get("/api/reports/{report_id}/share-recipients")
def report_share_recipients(request: Request, report_id: int, q: str = Query(default="", max_length=100)):
    return J(repo.search_report_recipients(report_id, _p(request), q))


@app.post("/api/reports/{report_id}/shares")
def report_share(request: Request, report_id: int, data: ReportShare):
    return J(repo.set_report_share(report_id, _p(request), data.emp_no, data.access_level))


@app.delete("/api/reports/{report_id}/shares/{emp_no}")
def report_unshare(request: Request, report_id: int, emp_no: str):
    repo.revoke_report_share(report_id, _p(request), emp_no)
    return {"ok": True}


# 报告包含图表脚本，也可能包含协作者提交的 HTML。用不透明源隔离管理台会话。
REPORT_HTML_HEADERS = {"Content-Security-Policy": "sandbox allow-scripts", "Referrer-Policy": "no-referrer"}


def _report_attachment(content: str, name: str) -> Response:
    # 普通下载和合并下载使用一致的 UTF-8 文件名及隔离策略。
    fname = quote(f"{name}.html", safe="")
    return HTMLResponse(content, headers={**REPORT_HTML_HEADERS, "Content-Disposition": f"attachment; filename*=UTF-8''{fname}"})


@app.get("/reports/preview/{ref}", response_class=HTMLResponse)
def report_preview(request: Request, ref: UUID, download: bool = False):
    # 预览包含完整数据，必须按已认证工号读取，不能仅凭 URL 访问。
    content = report_data.read_preview(_p(request).emp_no, ref)
    if content is None:
        raise repo.NotFound("预览不存在、已过期或无权访问")
    if download:
        return _report_attachment(content, "报告预览")
    return HTMLResponse(content, headers=REPORT_HTML_HEADERS)


@app.get("/reports/{report_id}", response_class=HTMLResponse)
def report_view(request: Request, report_id: int, version: int | None = Query(default=None, ge=1)):
    # 浏览器打开带版本操作的外层页面；现有 HTTP 客户端仍可直接获取报告 HTML。
    if "text/html" not in request.headers.get("accept", ""):
        return report_content(request, report_id, version)
    try:
        r = repo.get_report(report_id, _p(request), with_html=False, version=version)
    except permission.PermissionDenied:
        return HTMLResponse(_auth_page("无权查看该报告", "请使用获授权的海尔账号登录，或联系报告创建人。"), status_code=403)
    # 固定版本链接和历史链接只改变本页预览，用户在页面上切换时才保存自己的选择。
    initial = r["revision"] if version is not None or report_id != r["id"] else ""
    page = _admin_index_html().replace('<div id="app"></div>',
        f'<div id="app" data-report-id="{r["id"]}" data-report-version="{initial}"></div>')
    return HTMLResponse(page, headers={"Vary": "Accept", "Referrer-Policy": "no-referrer"})


@app.get("/reports/{report_id}/content", response_class=HTMLResponse)
def report_content(request: Request, report_id: int, version: int | None = Query(default=None, ge=1)):
    try:
        r = repo.get_report(report_id, _p(request), version=version)
    except permission.PermissionDenied:
        return HTMLResponse(_auth_page("无权查看该报告", "请使用获授权的海尔账号登录，或联系报告创建人。"), status_code=403)
    return HTMLResponse(r["html"], headers={**REPORT_HTML_HEADERS, "Vary": "Accept"})


@app.get("/reports/{report_id}/download", response_class=HTMLResponse)
def report_download(request: Request, report_id: int, standalone_file: bool = Query(True, alias="standalone"),
                    version: int | None = Query(default=None, ge=1)):
    try:
        r = repo.get_report(report_id, _p(request), version=version)
    except permission.PermissionDenied:
        return HTMLResponse(_auth_page("无权下载该报告", "请使用获授权的海尔账号登录，或联系报告创建人。"), status_code=403)
    # 新报告下载与保存正文一致，旧报告才需要把站内图表库转换为内联脚本。
    html = standalone(r["html"]) if standalone_file and not r["meta"].get("single_file") else r["html"]
    return _report_attachment(html, r["name"])


# ------------------------------------------------------------------ 权限：员工 / 表授权
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


@app.get("/api/permissions/check")
def perm_check(emp_no: str):
    emp = db.fetchone("SELECT * FROM permission.employee WHERE emp_no=%s", (emp_no,))
    if not emp:
        return J({"registered": False, "all": False, "datasets": []})
    p = auth.Principal(emp_no=emp_no, name=emp["name"], is_admin=emp["is_admin"] and emp["enabled"], registered=emp["enabled"],
                       roles=list(emp["roles"] or []))
    return J({"registered": emp["enabled"], "employee": emp, **permission.describe(permission.access_for(p))})


# ------------------------------------------------------------------ 演示 API 数据源
@app.get("/demo-api/tickets", include_in_schema=False)
def demo_tickets():
    items = seed.demo_tickets()
    return {"code": 0, "message": "ok", "data": {"total": len(items), "items": items}}
