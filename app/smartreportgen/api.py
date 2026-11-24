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

from fastapi import Body, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.openapi.docs import get_swagger_ui_html, get_swagger_ui_oauth2_redirect_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from markdown_it import MarkdownIt
from psycopg.errors import ForeignKeyViolation, UniqueViolation
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from . import auth, db, design, embedding, iam, permission, repo, report_browser, report_data, report_draft, report_pipeline, scenario_batch, seed, services
from .config import settings
from .connectors import ConnectorError, get_connector
from .report_document import inline_legacy_echarts, skill_dir

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("smartreportgen.api")
STATIC = Path(__file__).with_name("static")
DOCS = Path(__file__).with_name("docs")
if not (DOCS / "HTTP_API.md").is_file():
    DOCS = Path(__file__).resolve().parents[2] / "docs"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    need = db.init_db()
    repo.migrate_report_versions()
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
    try:
        yield
    finally:
        report_browser.close_pool()


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
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/docs", include_in_schema=False)
def api_docs():
    # 保留 Swagger 原有交互，在生成页上追加管理台主题和本地导航。
    page = get_swagger_ui_html(
        openapi_url=settings.public_path("/openapi.json"),
        title=f"{settings.app_name} - Swagger UI",
        oauth2_redirect_url=settings.public_path("/docs/oauth2-redirect"),
    ).body.decode("utf-8")
    page = page.replace("<html>", '<html lang="zh-CN">', 1).replace("<head>", f"<head>{_admin_page_head()}", 1)
    page = page.replace("</head>", f"{_admin_stylesheet()}</head>", 1)
    page = page.replace("<body>", f'<body class="api-docs">{_api_nav("docs")}', 1)
    return HTMLResponse(page, headers={"Cache-Control": "no-cache"})


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
        if path.startswith(("/reports/", "/api/reports", "/api/report-drafts", "/api/templates")):
            response.headers["Cache-Control"] = "no-store"
        return response
    return JSONResponse({"detail": "需要管理员权限；如需开通请联系管理员"}, status_code=403)


# 普通用户可访问的接口（默认拒绝）；接口内部再按“有权限 / 本人创建”过滤
USER_ALLOW = [(m, re.compile(rx)) for m, rx in [
    ("GET", r"^/api/meta$"),
    ("GET", r"^/api/datasources(/\d+(/tables|/columns|/values)?)?$"),
    ("GET", r"^/api/datasets(/search|/template|/export|/\d+)?$"), ("GET", r"^/api/scenarios$"),
    ("POST", r"^/api/datasets(/import|/batch)?$"), ("PUT", r"^/api/datasets/\d+$"), ("DELETE", r"^/api/datasets/\d+$"),
    ("GET", r"^/api/style-tokens$"), ("GET", r"^/api/style-rules(/resolve|/\d+)?$"),
    ("POST", r"^/api/style-rules(/effective|/preview)?$"), ("PUT", r"^/api/style-rules/\d+$"),
    ("DELETE", r"^/api/style-rules/\d+$"),
    ("GET", r"^/api/templates(/\d+(/versions/\d+)?)?$"), ("POST", r"^/api/templates$"),
    ("PUT", r"^/api/templates/\d+$"), ("DELETE", r"^/api/templates/\d+$"),
    ("GET", r"^/api/reports(/\d+(/shares|/share-recipients|/versions)?)?$"), ("PUT", r"^/api/reports/\d+(/selection)?$"),
    ("GET", r"^/api/report-drafts$"), ("POST", r"^/api/reports/(merge|\d+/shares)$"), ("DELETE", r"^/api/reports/\d+(/shares/[^/]+)?$"),
]]


def _user_allowed(method: str, path: str) -> bool:
    return any(method == m and rx.match(path) for m, rx in USER_ALLOW)


@app.exception_handler(repo.NotFound)
async def _nf(_r, e):
    return JSONResponse({"detail": str(e)}, status_code=404)


for _exc in (repo.Invalid, ConnectorError, RuntimeError, auth.AuthError,
             report_data.ReportDataError, ValueError, ValidationError):
    app.add_exception_handler(_exc, lambda _r, e: JSONResponse({"detail": str(e)}, status_code=400))


@app.exception_handler(report_browser.ReportVerifyError)
async def _verify_failed(_r, exc):
    return JSONResponse({"detail": str(exc), "error_type": "verify_failed", "failed_stage": exc.stage,
                         "diagnostics": exc.diagnostics}, status_code=400)


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
def _admin_asset_url(filename: str) -> str:
    # 入口与辅助页随镜像更新，按文件内容标记版本，避免继续使用旧缓存。
    version = sha256((STATIC / "admin" / filename).read_bytes()).hexdigest()[:12]
    return f"{settings.public_path(f'/static/admin/{filename}')}?v={version}"


def _admin_page_head() -> str:
    return ('<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta name="theme-color" content="#E6ECF5">')


def _admin_stylesheet() -> str:
    return f'<link rel="stylesheet" href="{_admin_asset_url("app.css")}">'


def _api_nav(current: str) -> str:
    links = (
        ("home", "/", "管理台"),
        ("guide", "/api-guide", "HTTP 接入说明"),
        ("docs", "/docs", "接口调试"),
        ("openapi", "/openapi.json", "OpenAPI JSON"),
    )
    items = []
    for key, path, label in links:
        active = ' aria-current="page"' if key == current else ''
        items.append(f'<a class="btn" href="{settings.public_path(path)}"{active}>{label}</a>')
    return f'<nav class="card docs-nav" aria-label="接入文档导航">{"".join(items)}</nav>'


@cache
def _admin_index_html() -> str:
    page = (STATIC / "admin" / "index.html").read_text(encoding="utf-8")
    for filename in ("app.css", "app.js", "template-editor.bundle.css", "template-editor.bundle.js"):
        page = page.replace(f'"/static/admin/{filename}"', f'"{_admin_asset_url(filename)}"')
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
    markdown = markdown.replace("../skills/smart-report/references/html-assembly.md",
                                settings.public_path("/api-guide/html-assembly"))
    body = renderer.render(markdown)
    return f'''<!doctype html><html lang="zh-CN"><head>{_admin_page_head()}
<title>SmartReportGen HTTP 接入说明</title>{_admin_stylesheet()}</head>
<body class="api-guide">{_api_nav("guide")}<main class="card docs-content">{body}</main></body></html>'''


@app.get("/api-guide", response_class=HTMLResponse, include_in_schema=False)
def api_guide():
    return HTMLResponse(_api_guide_html(), headers={"Cache-Control": "no-cache"})


@app.get("/api-guide/html-assembly", include_in_schema=False)
def api_html_assembly():
    guide = skill_dir() / "references" / "html-assembly.md"
    return Response(guide.read_text(encoding="utf-8"), media_type="text/plain")


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
    return {"mode": "iam", "iam_configured": iam.configured(), "me": _me(request.state.principal)}


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
    # 登录错误与报告访问提示复用登录页外壳，错误文案仍按纯文本转义。
    return (f'<!doctype html><html lang="zh-CN"><head>{_admin_page_head()}'
            f'<title>{html.escape(title)}</title>{_admin_stylesheet()}</head>'
            f'<body><main class="login-shell"><div class="card login login-error">'
            f'<h1>{html.escape(title)}</h1><p class="login-intro">{html.escape(msg)}</p>'
            f'<a class="btn primary" href="{settings.public_path("/")}">返回管理台</a>'
            f'</div></main></body></html>')


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
                 "templates": len(repo.list_templates(owner=me_no, allowed_datasets=acc.dataset_ids())), "reports": len(repo.list_reports(_p(request))),
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
    selectable = {row["id"] for row in rows if permission.source_scene_tables(row, _p(request))}
    visible = ids | selectable
    return J([{**{k: row[k] for k in DS_PUBLIC}, "can_build_scene": row["id"] in selectable}
              for row in rows if row["id"] in visible])


@app.post("/api/datasources")
def ds_create(request: Request, data: dict = Body(...)):
    _require_admin(request)
    return J(repo.create_datasource(data))


@app.get("/api/datasources/{ds_id}")
def ds_get(request: Request, ds_id: int):
    r = repo.get_datasource(ds_id)
    if _is_admin(request):
        return J(r)
    selectable = bool(permission.source_scene_tables(r, _p(request)))
    if not selectable and ds_id not in _visible_datasource_ids(request):
        raise repo.NotFound("数据源不存在")
    return J({**{k: r[k] for k in DS_PUBLIC}, "can_build_scene": selectable})


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


def _source_table_for_scene(source: dict, table: str, principal: auth.Principal) -> dict:
    item = permission.source_scene_tables(source, principal).get(table)
    if item is None:
        raise permission.PermissionDenied("该表尚未授权用于创建业务场景")
    return {"table_name": table, "source_row_filters": item.get("row_filters", [])}


@app.get("/api/datasources/{ds_id}/tables")
def ds_tables(request: Request, ds_id: int, schema: str | None = None):
    if _is_admin(request):
        return J(get_connector(repo.get_datasource(ds_id, with_secret=True)).list_tables(schema))
    source = repo.get_datasource(ds_id)
    catalog = permission.source_scene_tables(source, _p(request))
    return J([{"name": name, "table": name, "kind": "table", "comment": item.get("description", ""),
               "source_row_filters": item.get("row_filters", [])} for name, item in catalog.items()])


@app.get("/api/datasources/{ds_id}/describe")
def ds_describe(request: Request, ds_id: int, table: str, sample_rows: int = 10):
    _require_admin(request)
    return J(services.describe_table(repo.get_datasource(ds_id, with_secret=True), table, sample_rows))


@app.get("/api/datasources/{ds_id}/columns")
def ds_columns(request: Request, ds_id: int, table: str):
    """场景过滤编辑器只读取字段，不必扫描行数或数据画像。"""
    source = repo.get_datasource(ds_id, with_secret=True)
    if not _is_admin(request):
        _source_table_for_scene(source, table, _p(request))
    return J(get_connector(source).columns(table))


@app.get("/api/datasources/{ds_id}/values")
def ds_values(request: Request, ds_id: int, table: str = Query(min_length=1, max_length=512),
              column: str = Query(min_length=1, max_length=256), q: str = Query("", max_length=120),
              limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=10000)):
    """编辑场景时读取当前账号有权看到的实际候选值。"""
    if "\x00" in table or "\x00" in column or "\x00" in q:
        raise repo.Invalid("表名、字段和搜索词不能包含空字符")
    source = repo.get_datasource(ds_id, with_secret=True)
    if _is_admin(request):
        result = get_connector(source).distinct_values(table, column, q, limit, offset)
    else:
        item = _source_table_for_scene(source, table, _p(request))
        result = services.source_scene_values(source, item, column, q, limit, offset)
    result["next_offset"] = offset + limit if result.pop("has_more") and offset + limit <= 10000 else None
    return J(result)


@app.post("/api/datasources/{ds_id}/query")
def ds_query(request: Request, ds_id: int, data: dict = Body(...)):
    _require_admin(request)
    return J(services.run_query(repo.get_datasource(ds_id, with_secret=True), data.get("sql", ""), data.get("limit", 200)))


# ------------------------------------------------------------------ 数据集（普通用户管理本人创建的场景）
def _scoped_dataset(request: Request, ds: dict) -> dict:
    if _is_admin(request):
        return ds
    principal = _p(request)
    own = ds["owner_emp_no"] == principal.emp_no
    acc = _access(request)
    if own:
        # 已停用的个人场景仍可编辑，表选择器只展示当前仍有来源授权的元数据。
        source = repo.get_datasource(ds["datasource_id"])
        eligible = permission.source_scene_tables(source, principal)
        tables = [table for table in ds["tables"] if table["table_name"] in eligible]
    else:
        tables = [table for table in ds["tables"] if acc.can_table(ds["id"], table["table_name"])]
    names = {table["table_name"] for table in tables}
    metrics = {key: metric for key, metric in ds.get("semantic_metrics", {}).items() if metric["table"] in names}
    return {**ds, "tables": tables, "semantic_metrics": metrics, "table_count": len(tables), "read_only": not own}


@app.get("/api/datasets")
def dataset_list(request: Request, q: str | None = None):
    rows = repo.list_datasets(q=q)
    if _is_admin(request):
        return J(rows)
    acc = _access(request)
    employee = _p(request).emp_no
    return J([{**d, "table_count": len(acc.tables.get(d["id"], ())), "read_only": d["owner_emp_no"] != employee}
              for d in rows if d["owner_emp_no"] == employee or (d["enabled"] and acc.can_dataset(d["id"]))])


@app.post("/api/datasets")
def dataset_create(request: Request, data: dict = Body(...)):
    ds = repo.create_dataset(data, _p(request))
    return J(_scoped_dataset(request, ds))


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


_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx_attachment(content: bytes, name: str) -> Response:
    fname = quote(f"{name}.xlsx", safe="")
    return Response(
        content,
        media_type=_XLSX_MIME,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}", "Cache-Control": "no-store"},
    )


@app.get("/api/datasets/template")
def dataset_template(request: Request):
    return _xlsx_attachment(scenario_batch.build_template_xlsx(_p(request)), "业务场景导入模板")


@app.get("/api/datasets/export")
def dataset_export(request: Request, q: str | None = None, ids: str | None = None):
    parsed_ids: list[int] | None = None
    if ids is not None and ids.strip():
        parts = [p.strip() for p in ids.split(",") if p.strip()]
        if not parts or any(not p.isdigit() or int(p) < 1 for p in parts):
            raise repo.Invalid("ids 须为逗号分隔的正整数")
        parsed_ids = [int(p) for p in parts]
    return _xlsx_attachment(scenario_batch.export_scenarios_xlsx(_p(request), q=q, ids=parsed_ids), "业务场景导出")


@app.post("/api/datasets/import")
async def dataset_import(
    request: Request,
    file: UploadFile = File(...),
    dry_run: bool = Query(False),
    mode: Literal["upsert", "create_only"] = Query("upsert"),
):
    raw = await file.read(scenario_batch.MAX_EXCEL_BYTES + 1)
    res = await run_in_threadpool(
        scenario_batch.import_scenarios_xlsx, raw, _p(request), mode=mode, dry_run=dry_run
    )
    if not dry_run and not res["ok"]:
        return JSONResponse(
            jsonable_encoder({"detail": f"批量导入校验未通过（共 {res['summary']['error_count']} 处错误）", **res}),
            status_code=400,
        )
    return J(res)


@app.post("/api/datasets/batch")
async def dataset_batch(request: Request, data: dict = Body(...)):
    mode = data.get("mode", "upsert")
    dry_run = data.get("dry_run", False)
    if type(dry_run) is not bool:
        raise repo.Invalid("dry_run 须为布尔值")
    res = await run_in_threadpool(
        scenario_batch.batch_save_scenarios, data.get("scenarios"), _p(request), mode=mode, dry_run=dry_run
    )
    if not dry_run and not res["ok"]:
        return JSONResponse(
            jsonable_encoder({"detail": f"批量保存校验未通过（共 {res['summary']['error_count']} 处错误）", **res}),
            status_code=400,
        )
    return J(res)


@app.get("/api/datasets/{dataset_id}")
def dataset_get(request: Request, dataset_id: int):
    dataset = repo.get_dataset(dataset_id)
    if not _is_admin(request) and dataset["owner_emp_no"] != _p(request).emp_no and not _access(request).can_dataset(dataset_id):
        raise repo.NotFound("数据集不存在")
    return J(_scoped_dataset(request, dataset))


@app.put("/api/datasets/{dataset_id}")
def dataset_update(request: Request, dataset_id: int, data: dict = Body(...)):
    return J(_scoped_dataset(request, repo.update_dataset(dataset_id, data, _p(request))))


@app.delete("/api/datasets/{dataset_id}")
def dataset_delete(request: Request, dataset_id: int):
    repo.delete_dataset(dataset_id, _p(request))
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
    """样式编辑器元数据：内置主题、只读报告规则、展示类别与样式生效顺序。"""
    builtin, _ = design.resolve()
    return J({"groups": design.TOKEN_GROUPS, "categories": design.ITEM_CATEGORIES, "defaults": builtin,
              "retired_analysis_categories": design.RETIRED_ANALYSIS_CATEGORIES,
              "suggested": builtin,
              "reporting_rules": design.builtin_reporting_rules(),
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


# ------------------------------------------------------------------ Markdown 报告模板与草稿
@app.get("/api/templates")
def tpl_list(request: Request, q: str | None = None, dataset_id: int | None = None):
    acc = _access(request)
    return J(repo.list_templates(q, owner=_owner_filter(request), dataset_id=dataset_id,
                                 allowed_datasets=acc.dataset_ids()))


@app.post("/api/templates")
def tpl_create(request: Request, data: dict = Body(...)):
    _require_dataset(request, data.get("dataset_id"))
    return J(repo.save_template({**data, "created_by": _p(request).emp_no}))


@app.get("/api/templates/{tpl_id}")
def tpl_get(request: Request, tpl_id: int):
    template = repo.get_template(tpl_id)
    repo.require_template_access(template, _access(request))
    template["versions"] = repo.template_versions(tpl_id)
    return J(template)


@app.put("/api/templates/{tpl_id}")
def tpl_update(request: Request, tpl_id: int, data: dict = Body(...)):
    tpl = repo.get_template(tpl_id)
    _check_owner(request, tpl.get("created_by"), "报告模板")
    _require_dataset(request, data.get("dataset_id", tpl["dataset_id"]))
    expected_version = data.get("expected_version")
    if expected_version is not None and (type(expected_version) is not int or expected_version < 1):
        raise repo.Invalid("expected_version 须为正整数")
    return J(repo.save_template(
        {key: value for key, value in data.items() if key not in ("created_by", "expected_version")},
        tpl_id, expected_version=expected_version,
    ))


@app.delete("/api/templates/{tpl_id}")
def tpl_delete(request: Request, tpl_id: int):
    _check_owner(request, repo.get_template(tpl_id).get("created_by"), "报告模板")
    repo.delete_template(tpl_id)
    return {"ok": True}


@app.get("/api/templates/{tpl_id}/versions/{version}")
def tpl_version(request: Request, tpl_id: int, version: int):
    repo.require_template_access(repo.get_template(tpl_id), _access(request))
    return J(repo.get_template_version(tpl_id, version))


@app.get("/api/report-drafts")
def report_drafts(request: Request):
    principal = _p(request)
    return J(report_draft.list_drafts(principal.emp_no, is_admin=principal.is_admin))



# ------------------------------------------------------------------ 报告（创建人、管理员及被分享的员工）
class ReportUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    html: str | None = None
    draft_id: str | None = None
    dataset_id: int | None = Field(default=None, ge=1)
    template_id: int | None = Field(default=None, ge=1, strict=True)
    template_version: int | None = Field(default=None, ge=1, strict=True)
    preview_id: str | None = None
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
    base_version, expected = data.base_version or data.revision, data.expected_version or data.revision
    return J(services.revise_report(report_id, _p(request), data.name, html=data.html, draft_id=data.draft_id,
                                   preview_id=data.preview_id, dataset_id=data.dataset_id,
                                   template_id=data.template_id, template_version=data.template_version,
                                   base_version=base_version, change_note=data.change_note, expected_version=expected))


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
    try:
        _, content = report_pipeline.load_preview(_p(request), str(ref), certified=True)
    except report_data.ReportDataError as exc:
        raise repo.NotFound("预览不存在、已过期或无权访问") from exc
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
    html = inline_legacy_echarts(r["html"]) if standalone_file and not r["meta"].get("single_file") else r["html"]
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
