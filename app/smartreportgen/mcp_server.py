"""SmartReportGen MCP 服务（Streamable HTTP，路径 /mcp）。

认证：每个请求必须携带 Authorization: Bearer <JWT>；JWT 中的员工工号用于查询权限表（permission schema），
      决定可检索的业务场景与可访问的数据表。数据访问一律默认拒绝。
工具按智能报告生成流程组织：
  a. search_business_scenarios → get_dataset_tables
  b. describe_table / run_query → get_template_spec
  d. get_style_rules
  e. validate_report_template → save_report_template → render_report_html（生成并存储 HTML）
  f. save_report / get_report
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import uvicorn
from fastapi.encoders import jsonable_encoder
from mcp.server.fastmcp import Context, FastMCP
from starlette.responses import JSONResponse

from . import auth, db, repo, services
from .auth import AuthError, Principal
from .config import settings
from .connectors import ConnectorError
from .permission import Access, PermissionDenied, access_for, check_sql
from .renderer import RenderError, render, validate as validate_md
from .template_parser import TemplateError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("smartreportgen.mcp")

SKILL_DIR = Path(__file__).with_name("docs") / "skill"
SKILL_FILE = SKILL_DIR / "SKILL.md"
SPEC_FILE = SKILL_DIR / "references" / "template-spec.md"
EXAMPLE_FILE = SKILL_DIR / "references" / "example-retail.md"


def _skill_body() -> str:
    """SKILL.md 去掉 frontmatter 的正文。"""
    text = SKILL_FILE.read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:].lstrip()
    return text

INSTRUCTIONS = """SmartReportGen 智能报告 MCP（需令牌认证：员工令牌或海尔 IAM access token；数据按员工工号做表级权限控制）。
若你所在的 agent 没有加载 smart-report 技能（Skill），请先调用 get_skill_guide 获取完整的生成流程与方法，再按流程执行。推荐流程：
1) search_business_scenarios(用户需求) 在你有权限的业务场景中混合检索 → get_dataset_tables(dataset_id) 获取数据表和场景的分析思路；
2) 结合用户需求和场景的 analysis_approach，用 describe_table / run_query 验证数据与分析假设，确定分析目标；调用 get_template_spec 了解模板语法；
3) 根据用户需求、场景分析思路和查询证据，决定报告的章节、结论、图表、布局及筛选；
4) get_style_rules(业务场景) 获取视觉样式和展示要求 specified（业务场景 ≥ 通用 ≥ 内置 合并），默认遵守，未指定的项由你决定；
5) validate_report_template 校验直至无错误 → save_report_template 存储模板 → render_report_html 生成并存储 HTML。
遇到“无权访问”时不要尝试绕过，应告知用户需要申请哪些表的权限。"""

mcp = FastMCP("smartreportgen", instructions=INSTRUCTIONS, stateless_http=True, json_response=True)

_EXPECTED = (repo.NotFound, repo.Invalid, ConnectorError, RenderError, TemplateError, RuntimeError, ValueError,
             PermissionDenied, AuthError)


def _ok(obj: Any) -> Any:
    return jsonable_encoder(obj)


def _err(e: Exception) -> dict:
    out = {"ok": False, "error": str(e)}
    if isinstance(e, PermissionDenied):
        out["error_type"] = "permission_denied"
    return out


# ------------------------------------------------------------ 身份与权限
def _principal(ctx: Context) -> Principal:
    req = getattr(ctx.request_context, "request", None)
    if req is None:
        raise AuthError("无法获取请求上下文")
    p = req.scope.get("state", {}).get("principal")
    return p if p is not None else auth.verify_bearer(
        req.headers.get("authorization"), req.headers.get("x-iam-access-token") or req.headers.get("access-token"))


def _access(ctx: Context) -> Access:
    p = _principal(ctx)
    if not p.registered:
        raise PermissionDenied(f"工号 {p.emp_no} 未在权限表中登记或已停用，请联系管理员开通")
    return access_for(p)


def _require_dataset(acc: Access, dataset_id: int) -> None:
    if not acc.can_dataset(dataset_id):
        raise PermissionDenied(f"{acc.principal.label} 无权访问该业务场景（dataset_id={dataset_id}）")


def _guard(acc: Access):
    def g(ds_row: dict, sql: str) -> None:
        check_sql(acc, ds_row, sql)
    return g


def _owner(acc: Access) -> str | None:
    """非管理员只能看到/修改自己创建的模板与报告。"""
    return None if acc.all else (acc.principal.emp_no or "")


def _check_owner(acc: Access, created_by: str | None, what: str) -> None:
    if not acc.all and created_by != acc.principal.emp_no:
        raise PermissionDenied(f"无权操作他人创建的{what}")


def _dataset_brief(ds: dict, acc: Access) -> dict:
    tables = [t for t in ds.get("tables", []) if acc.can_table(ds["id"], t["table_name"])]
    hidden = len(ds.get("tables", [])) - len(tables)
    out = {
        "dataset_id": ds["id"], "business_scenario": ds["business_scenario"],
        "business_description": ds["business_description"], "analysis_approach": ds["analysis_approach"],
        "tags": ds.get("tags") or [],
        "datasource": {"id": ds.get("datasource_id"), "name": ds.get("datasource_name"), "type": ds.get("datasource_type")},
        "tables": [{"table_name": t["table_name"], "table_description": t["table_description"]} for t in tables],
    }
    if hidden:
        out["unauthorized_table_count"] = hidden
    return out


# ============================== 身份 ==============================
@mcp.tool()
def whoami(ctx: Context) -> dict:
    """返回当前令牌对应的员工身份，以及有权限的业务场景与数据表。"""
    try:
        p = _principal(ctx)
        from .permission import describe
        return _ok({"ok": True, "emp_no": p.emp_no, "name": p.name, "is_admin": p.is_admin, "via": p.via,
                    "registered": p.registered, "roles": p.roles,
                    "access": describe(access_for(p)) if p.registered else {"all": False, "datasets": []}})
    except _EXPECTED as e:
        return _err(e)


# ============================== a. 场景检索 ==============================
@mcp.tool()
def search_business_scenarios(query: str, ctx: Context, top_k: int = 3) -> dict:
    """根据用户需求，在【当前员工有权限】的业务场景中做混合检索（向量 + 关键词，RRF 融合），
    返回最匹配的业务场景、分析思路及其中有权限的数据表。query 直接传入用户的原始需求描述效果最好。"""
    try:
        acc = _access(ctx)
        res = repo.search_datasets(query, max(1, min(top_k, 10)), dataset_ids=acc.dataset_ids())
    except _EXPECTED as e:
        return _err(e)
    hits = []
    for h in res["hits"]:
        hits.append({
            **_dataset_brief(h["dataset"], acc),
            "scores": {k: h.get(k) for k in ("rrf", "relevance", "vector_score", "vector_rank", "keyword_score",
                                             "keyword_rank")},
            "matched_terms": h.get("matched_terms", []),
        })
    tip = ""
    if not hits:
        tip = ("没有命中你有权限的业务场景：可换用业务术语重新检索；若确需该数据，请联系管理员申请权限。"
               if not acc.all else "没有命中任何业务场景：请换用业务术语重新检索，或在管理台维护数据集。")
    elif hits[0]["scores"]["relevance"] < 0.25:
        tip = "最高相关度偏低，命中可能不准确，请核对业务描述，必要时向用户确认业务场景。" + (
            "也可能是你没有所需业务场景的权限（检索只覆盖已授权的场景），如需请联系管理员申请。" if not acc.all else "")
    elif len(hits) >= 2 and hits[1]["scores"]["relevance"] >= 0.8 * hits[0]["scores"]["relevance"]:
        tip = "前两名相关度接近，请结合业务描述判断，必要时向用户确认业务场景。"
    return _ok({"ok": True, "query_tokens": res["query_tokens"], "hits": hits, "warnings": res["warnings"], "tip": tip})


@mcp.tool()
def get_dataset_tables(dataset_id: int, ctx: Context) -> dict:
    """获取某业务场景的业务描述、分析思路、当前员工有权限的数据表及数据源信息（类型决定 SQL 方言）。"""
    try:
        acc = _access(ctx)
        _require_dataset(acc, dataset_id)
        return _ok({"ok": True, **_dataset_brief(repo.get_dataset(dataset_id), acc)})
    except _EXPECTED as e:
        return _err(e)


@mcp.tool()
def list_business_scenarios(ctx: Context) -> dict:
    """列出当前员工有权限的业务场景（数据集）概要。"""
    try:
        acc = _access(ctx)
    except _EXPECTED as e:
        return _err(e)
    return _ok({"ok": True, "datasets": [
        {"dataset_id": d["id"], "business_scenario": d["business_scenario"], "datasource": d["datasource_name"],
         "datasource_type": d["datasource_type"],
         "table_count": d["table_count"] if acc.all else len(acc.tables.get(d["id"], ()))}
        for d in repo.list_datasets() if d["enabled"] and acc.can_dataset(d["id"])]})


# ============================== b. 数据探查 ==============================
@mcp.tool()
def describe_table(dataset_id: int, table_name: str, ctx: Context, sample_rows: int = 5) -> dict:
    """查看【有权限】的数据表结构与数据情况：字段名/类型/注释、总行数、样本行、列画像（类型、空值率、去重数、最值、高频取值）。"""
    try:
        acc = _access(ctx)
        _require_dataset(acc, dataset_id)
        ds = repo.get_dataset(dataset_id)
        if table_name not in {t["table_name"] for t in ds["tables"]}:
            raise repo.Invalid(f"表 {table_name} 不属于业务场景「{ds['business_scenario']}」")
        if not acc.can_table(dataset_id, table_name):
            raise PermissionDenied(f"{acc.principal.label} 无权访问数据表：{table_name}")
        ds_row = services.datasource_for_dataset(dataset_id)
        return _ok({"ok": True, **services.describe_table(ds_row, table_name, max(0, min(sample_rows, 50)))})
    except _EXPECTED as e:
        return _err(e)


@mcp.tool()
def run_query(dataset_id: int, sql: str, ctx: Context, limit: int = 200) -> dict:
    """在业务场景对应的数据源上执行只读 SQL（单条 SELECT/WITH），只能引用当前员工有权限的表（含跨表 JOIN 的每一张表）。
    SQL 方言与数据源类型一致（api 类型数据源使用 DuckDB 方言，表名为接口逻辑名）。结果列名统一转小写。"""
    try:
        acc = _access(ctx)
        _require_dataset(acc, dataset_id)
        ds_row = services.datasource_for_dataset(dataset_id)
        check_sql(acc, ds_row, sql)
        return _ok({"ok": True, **services.run_query(ds_row, sql, limit)})
    except _EXPECTED as e:
        return _err(e)


@mcp.tool()
def get_skill_guide(include_example: bool = False) -> str:
    """返回 smart-report 技能全文：报告生成的完整流程（a-f）、分析与设计方法、权限处理与最终回复格式。
    适用于没有本地技能机制的 agent；开始生成报告前调用一次。include_example=true 时附带完整示例模板。"""
    text = _skill_body() + "\n\n> 模板语法请调用 get_template_spec 获取。\n"
    if include_example:
        text += "\n\n## 附：完整示例模板（零售销售经营分析）\n\n" + EXAMPLE_FILE.read_text(encoding="utf-8")
    return text


@mcp.prompt(name="smart_report", description="按 SmartReportGen 流程，根据需求生成交互式 HTML 分析报告")
def smart_report_prompt(requirement: str) -> str:
    return (_skill_body() + "\n\n---\n\n## 本次需求\n\n" + requirement +
            "\n\n请严格按以上流程，使用 smartreportgen MCP 工具完成报告并按“最终回复格式”答复。")


@mcp.tool()
def get_template_spec() -> str:
    """返回报告模板（markdown）完整语法：front matter（queries/measures/filters/presets/theme）、
    章节/汇总结论/子结论结构、chart 图表块全部类型与参数、健康度与升降色、布局规则。编写模板前必须阅读。"""
    return SPEC_FILE.read_text(encoding="utf-8")


# ============================== d. 样式规则 ==============================
@mcp.tool()
def get_style_rules(business_scenario: str, ctx: Context, query: str = "") -> dict:
    """检索该业务场景的指定样式：你本人为该场景配置的样式 ≥ 管理员配置的通用规则 ≥ 内置经营报告主题，合并为一份 specified，
    作为展示约束交给你：specified.theme 为视觉样式取值（渲染时自动生效），specified.items 为好坏判定、涨跌颜色、数字格式、
    文案等展示要求。章节、图表类型和筛选条件由用户需求、场景分析思路及数据证据决定。
    最终生效顺序：你的决定（模板 theme 与模板写法）≥ 业务场景规则 ≥ 通用规则 ≥ 内置样式——默认遵守约束，确有理由时可按自己的判断调整。"""
    try:
        p = _principal(ctx)
        res = repo.style_rules_for(business_scenario, query or None, owner=p.emp_no)
    except _EXPECTED as e:
        return _err(e)
    rules = [{"id": r["id"], "name": r["name"], "scope": r["scope"],
              **({"relevance": r["relevance"]} if r.get("relevance") else {})}
             for r in res["general_rules"] + res["scenario_rules"]]
    return _ok({
        "ok": True,
        "business_scenario": res["business_scenario"],
        "precedence": res["precedence"],
        "specified": res["specified"],
        "rules": rules,
        "note": ("specified 是检索出的样式约束（业务场景规则 ≥ 通用规则 ≥ 内置样式 合并的结果）：theme 在渲染时自动生效，"
                 "模板中无需重复；items 是展示要求，各类别内排在前面的优先，请落实到健康度、升降色、数字格式与文案。"
                 "章节、图表类型和筛选条件由用户需求、场景分析思路及数据证据决定。默认遵守展示约束，"
                 "确有理由时（如用户明确要求、报告内容需要）可在模板 theme 中覆盖或不采纳某条要求，并在最终回复中说明。"
                 "未指定的视觉样式与展示要求由你自由决定（可用样式项见 get_template_spec「主题」）。"),
        "warnings": res["warnings"],
    })


# ============================== e/f. 模板与报告 ==============================
@mcp.tool()
def validate_report_template(markdown: str, ctx: Context, dataset_id: int | None = None, execute: bool = True) -> dict:
    """校验报告模板：语法、图表参数、度量表达式，并（execute=true）实际执行全部查询（按权限校验每条 SQL），
    检查字段引用、返回每个查询的行数/列/样本与筛选器取值预览。保存前应校验到 errors 为空。"""
    try:
        acc = _access(ctx)
        if dataset_id:
            _require_dataset(acc, dataset_id)
        return _ok(validate_md(markdown, dataset_id, execute, guard=_guard(acc), owner=acc.principal.emp_no))
    except _EXPECTED as e:
        return _err(e)


@mcp.tool()
def save_report_template(name: str, markdown: str, ctx: Context, dataset_id: int | None = None,
                         user_requirement: str = "", analysis_goal: str = "", template_id: int | None = None,
                         business_summary: str | None = None) -> dict:
    """存储报告模板（markdown）。business_summary 用通俗语言说明模板适用的问题、分析内容和对比口径，供业务人员在管理台阅读。
    传 template_id 则更新已有模板，源码变化时递增版本号。"""
    try:
        acc = _access(ctx)
        if dataset_id:
            _require_dataset(acc, dataset_id)
        data = {"name": name, "markdown": markdown, "dataset_id": dataset_id,
                "user_requirement": user_requirement, "analysis_goal": analysis_goal}
        if template_id:  # 更新时只覆盖调用方实际提供的字段
            _check_owner(acc, repo.get_template(template_id).get("created_by"), "报告模板")
            data = {k: v for k, v in data.items() if k == "markdown" or v not in (None, "")}
        else:
            data["created_by"] = acc.principal.emp_no
        # 省略时兼容旧客户端，显式传空字符串可以清除说明。
        if business_summary is not None:
            data["business_summary"] = business_summary
        t = repo.save_template(data, template_id)
    except _EXPECTED as e:
        return _err(e)
    return _ok({"ok": True, "template_id": t["id"], "version": t["version"], "name": t["name"], "summary": t["summary"]})


@mcp.tool()
def get_report_template(template_id: int, ctx: Context) -> dict:
    """读取本人创建的报告模板（含 markdown 全文），用于复用或修改。"""
    try:
        acc = _access(ctx)
        t = repo.get_template(template_id)
        _check_owner(acc, t.get("created_by"), "报告模板")
        return _ok({"ok": True, **t})
    except _EXPECTED as e:
        return _err(e)


@mcp.tool()
def list_report_templates(ctx: Context, keyword: str = "") -> dict:
    """列出本人创建的报告模板（可按名称/需求/业务场景关键字过滤），便于复用历史模板。"""
    try:
        acc = _access(ctx)
    except _EXPECTED as e:
        return _err(e)
    rows = repo.list_templates(keyword or None, owner=_owner(acc))
    return _ok({"ok": True, "templates": [
        {"template_id": r["id"], "name": r["name"], "business_scenario": r["business_scenario"], "version": r["version"],
         "user_requirement": r["user_requirement"], "updated_at": r["updated_at"], "summary": r["summary"]} for r in rows[:50]]})


def _report_links(report_id: int) -> dict:
    t = auth.share_token(report_id)
    base = f"{settings.public_base_url}/reports/{report_id}"
    return {"url": f"{base}?t={t}", "download_url": f"{base}/download?t={t}",
            "link_expires_in_days": settings.report_share_days}


@mcp.tool()
def render_report_html(template_id: int, ctx: Context, report_name: str = "") -> dict:
    """基于本人创建的报告模板执行查询（按权限校验每条 SQL）并生成 HTML 报告页面，同时存储到报告库。
    返回 report_id 与带查看凭证的访问 URL。"""
    try:
        acc = _access(ctx)
        tpl = repo.get_template(template_id)
        _check_owner(acc, tpl.get("created_by"), "报告模板")
        html, meta = render(tpl["markdown"], dataset_id=tpl.get("dataset_id"), guard=_guard(acc),
                            owner=tpl.get("created_by"))
        r = repo.save_report(report_name or tpl["name"], html, template_id, tpl["version"], meta,
                             created_by=acc.principal.emp_no)
    except _EXPECTED as e:
        return _err(e)
    return _ok({"ok": True, "report_id": r["id"], "name": r["name"], **_report_links(r["id"]),
                "html_bytes": r["size"], "warnings": meta.get("warnings", []), "queries": meta.get("queries")})


@mcp.tool()
def save_report(name: str, html: str, ctx: Context, template_id: int | None = None) -> dict:
    """存储一份完整的 HTML 报告页面（例如对 render_report_html 的结果做了改写后）。"""
    try:
        acc = _access(ctx)
        ver = None
        if template_id:
            t = repo.get_template(template_id)
            _check_owner(acc, t.get("created_by"), "报告模板")
            ver = t["version"]
        r = repo.save_report(name, html, template_id, ver, {"source": "mcp.save_report"}, created_by=acc.principal.emp_no)
    except _EXPECTED as e:
        return _err(e)
    return _ok({"ok": True, "report_id": r["id"], **_report_links(r["id"]), "html_bytes": r["size"]})


@mcp.tool()
def get_report(report_id: int, ctx: Context, include_html: bool = False) -> dict:
    """读取本人生成的报告信息；include_html=true 时返回 HTML 全文（可能较大）。"""
    try:
        acc = _access(ctx)
        r = repo.get_report(report_id, with_html=include_html)
        _check_owner(acc, r.get("created_by"), "报告")
    except _EXPECTED as e:
        return _err(e)
    return _ok({"ok": True, **r, **_report_links(report_id)})


# ============================== 启动 ==============================
class TokenAuth:
    """纯 ASGI 中间件：/mcp 请求必须携带有效令牌，解析出的身份放入 scope["state"]["principal"]。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/mcp"):
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            try:
                principal = auth.verify_bearer(headers.get("authorization"),
                                               headers.get("x-iam-access-token") or headers.get("access-token"))
            except AuthError as e:
                resp = JSONResponse({"error": "unauthorized", "message": str(e)}, status_code=401,
                                    headers={"WWW-Authenticate": 'Bearer realm="smartreportgen"'})
                await resp(scope, receive, send)
                return
            scope.setdefault("state", {})["principal"] = principal
        await self.app(scope, receive, send)


def build_app():
    mcp.settings.host = "0.0.0.0"
    mcp.settings.port = settings.mcp_port
    try:  # 新版 SDK 默认开启 DNS rebinding 防护，仅允许 localhost Host；容器内需放开（认证由令牌负责）
        from mcp.server.transport_security import TransportSecuritySettings

        mcp.settings.transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    except ImportError:
        pass
    app = mcp.streamable_http_app()

    async def health(_req):
        return JSONResponse({"ok": True})

    app.add_route("/healthz", health)
    app.add_middleware(TokenAuth)
    return app


def main() -> None:
    db.init_db()
    repo.load_custom_words()
    auth.jwt_secret()
    uvicorn.run(build_app(), host="0.0.0.0", port=settings.mcp_port, log_level="info")


if __name__ == "__main__":
    main()
