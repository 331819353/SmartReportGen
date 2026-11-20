"""SmartReportGen MCP 服务（Streamable HTTP，路径 /mcp）。

认证：每个请求通过 Authorization: Bearer 携带海尔 IAM access token；经 IAM 核实的工号用于查询权限表，
      决定可检索的业务场景与可访问的数据表。数据访问一律默认拒绝。
工具按智能报告生成流程组织：
  1. search_business_scenarios → get_dataset_tables
  2. describe_tables / describe_table / run_query
  3. 设计全局筛选、联动与结论指标着色（Agent 分析与设计，无独立 MCP 工具）
  4. get_style_rules
  5. save_report_spec / save_free_report_template / get_report_template
  6. 模块编排 → preview_report_spec / preview_free_report → save_report
  7. get_report / revise_report
"""
from __future__ import annotations

import contextvars
import logging
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from hashlib import sha256
from typing import Any, Literal

import uvicorn
from fastapi.encoders import jsonable_encoder
from pydantic import Field
from typing import Annotated
from mcp.server.fastmcp import Context, FastMCP
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from psycopg.errors import UniqueViolation

from . import auth, db, design, repo, report_browser, report_data, report_free, report_pipeline, report_queries, report_spec, services
from .auth import AuthError, Principal
from .config import settings
from .connectors import ConnectorError
from .permission import Access, PermissionDenied, access_for, source_scene_tables
from .scenario_filters import effective_filters
from .renderer import RenderError, validate as validate_md
from .report_document import inline_legacy_echarts, report_document_error, skill_dir
from .template_parser import TemplateError
from .telemetry import measure, observe_tool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("smartreportgen.mcp")

SKILL_DIR = skill_dir()
SKILL_FILE = SKILL_DIR / "SKILL.md"
SPEC_FILE = SKILL_DIR / "references" / "template-spec.md"
EXAMPLE_FILE = SKILL_DIR / "references" / "example-report.html"


def _skill_body() -> str:
    """SKILL.md 去掉 frontmatter 的正文。"""
    text = SKILL_FILE.read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:].lstrip()
    return text

INSTRUCTIONS = """SmartReportGen 智能报告 MCP。用 get_skill_guide 获取从报告分流到验收保存的主流程。
先区分新建与修订。修订用报告链接或 list_reports 定位，多个匹配候选先请用户确认，再按 get_report 返回的 revision 锁定基准。
新建用 search_business_scenarios 匹配已授权场景，复用适用场景。缺少适用场景时，可用 list_scene_datasources 查看开放数据源与固定范围并配合 save_business_scenario 协助建场景，或给出 scenario_management_url 引导用户配置。
已知场景后优先调用 prepare_report_context 一步加载分析思路、表画像、生效样式与模板契约，再以 query_report_data 同批次取数或核对证据。
派生采用场景负责人登记的官方指标，多组报告数据使用同批快照。结构化模板用 save_report_spec（可选 expected_version）。自由版式用 save_free_report_template 保存 HTML/CSS 和受管绑定，
用 patch_free_report_template 按模块修改。用对应的 preview_report_spec 或 preview_free_report 整页验收，核对返回 warnings 后提交验收通过的 preview_id。
采用海尔 IAM，场景及事实来源受当前员工授权约束。"""

mcp = FastMCP("smartreportgen", instructions=INSTRUCTIONS, stateless_http=True, json_response=True)


def _tool():
    """同步业务函数在线程池运行，避免慢查询阻塞整个 MCP 事件循环。"""
    def register(fn):
        observed = observe_tool(fn)

        @wraps(fn)
        async def dispatch(*args, **kwargs):
            # FastMCP 直接调用同步函数；仅标记为同步工具无法自动获得线程池隔离。
            return await run_in_threadpool(observed, *args, **kwargs)

        mcp.tool()(dispatch)
        # 保留同步入口供应用内部和单元测试调用；对外注册的是上面的异步入口。
        return observed
    return register


_EXPECTED = (repo.NotFound, repo.Invalid, ConnectorError, RenderError, TemplateError, RuntimeError, ValueError,
             PermissionDenied, AuthError)


def _ok(obj: Any) -> Any:
    return jsonable_encoder(obj)


def _err(e: Exception) -> dict:
    out = {"ok": False, "error": str(e)}
    if isinstance(e, PermissionDenied):
        out["error_type"] = "permission_denied"
    if isinstance(e, repo.ReportConflict):
        out["error_type"] = "conflict"
    return out


# ------------------------------------------------------------ 身份与权限
def _principal(ctx: Context) -> Principal:
    req = getattr(ctx.request_context, "request", None)
    if req is None:
        raise AuthError("无法获取请求上下文")
    p = req.scope.get("state", {}).get("principal")
    # 正常 HTTP 入口复用中间件已核实的身份，直接调用工具时也使用同一 Bearer 校验。
    return p if p is not None else auth.verify_bearer(req.headers.get("authorization"))


def _access(ctx: Context) -> Access:
    p = _principal(ctx)
    if not p.registered:
        raise PermissionDenied(f"工号 {p.emp_no} 未在权限表中登记或已停用，请联系管理员开通")
    return access_for(p)


def _require_dataset(acc: Access, dataset_id: int) -> None:
    if not acc.can_dataset(dataset_id):
        raise PermissionDenied(f"{acc.principal.label} 无权访问该业务场景（dataset_id={dataset_id}）")


def _guard(acc: Access):
    def g(dataset: dict | None) -> set[str] | None:
        if dataset:
            _require_dataset(acc, dataset["id"])
            return None if acc.all else acc.tables.get(dataset["id"], set())
        if not acc.all:
            raise PermissionDenied("请为查询关联有权限的业务场景，并使用该场景的数据源")
        return None
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
        "semantic_metrics": {key: metric for key, metric in ds.get("semantic_metrics", {}).items()
                             if acc.can_table(ds["id"], metric["table"])},
        "tables": [{"table_name": t["table_name"], "table_description": t["table_description"],
                    "row_filters": effective_filters(t)} for t in tables],
    }
    if hidden:
        out["unauthorized_table_count"] = hidden
    return out


# ============================== 身份 ==============================
@_tool()
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


# ============================== 1. 场景检索 ==============================
@_tool()
def search_business_scenarios(query: str, ctx: Context, top_k: int = 3,
                              include_context: Literal["structured", "free"] | None = None) -> dict:
    """根据用户需求，在【当前员工有权限】的业务场景中做混合检索（向量 + 关键词，RRF 融合），
    返回最匹配的业务场景、分析思路及已授权表，并给出管理台业务场景入口。query 宜使用用户的原始需求。
    include_context 可在首个结果相关且与下一名有足够差距时一并返回其模板设计上下文。"""
    try:
        acc = _access(ctx)
        requested = max(1, min(top_k, 10))
        # 即使只返回一名也要用第二名判断歧义，避免自动加载误匹配的表画像。
        res = repo.search_datasets(query, max(2, requested) if include_context else requested, dataset_ids=acc.dataset_ids())
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
        tip = ("没有命中你有权限的业务场景。可换用业务术语检索，或从已授权的数据源构建场景。缺少来源授权时请联系管理员。"
               if not acc.all else "没有命中任何业务场景：请换用业务术语重新检索，或在管理台维护数据集。")
    elif hits[0]["scores"]["relevance"] < 0.25:
        tip = "最高相关度偏低，命中可能不准确，请核对业务描述，必要时向用户确认业务场景。" + (
            "也可能是你没有所需业务场景的权限（检索只覆盖已授权的场景），如需请联系管理员申请。" if not acc.all else "")
    elif len(hits) >= 2 and hits[1]["scores"]["relevance"] >= 0.8 * hits[0]["scores"]["relevance"]:
        tip = "前两名相关度接近，请结合业务描述判断，必要时向用户确认业务场景。"
    # 管理台和 MCP 可以使用不同的公网地址，配置场景应跳到管理台的实际部署子路径。
    result = {"ok": True, "query_tokens": res["query_tokens"], "hits": hits[:requested], "warnings": res["warnings"], "tip": tip,
              "scenario_management_url": f"{settings.public_base_url}/#datasets"}
    if include_context and hits and not tip:
        result["context"] = prepare_report_context(hits[0]["dataset_id"], ctx, query, mode=include_context)
    return _ok(result)


@_tool()
def get_dataset_tables(dataset_id: int, ctx: Context) -> dict:
    """获取业务场景的描述、分析思路、已授权的表或视图、各表取值范围及数据源信息。"""
    try:
        acc = _access(ctx)
        _require_dataset(acc, dataset_id)
        return _ok({"ok": True, **_dataset_brief(repo.get_dataset(dataset_id), acc)})
    except _EXPECTED as e:
        return _err(e)


@_tool()
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


@_tool()
def list_scene_datasources(ctx: Context) -> dict:
    """列出当前员工可用于构建业务场景的数据源及开放表清单（含固定行范围）。"""
    try:
        acc = _access(ctx)
        items = []
        for row in repo.list_datasources():
            if not row.get("enabled", True):
                continue
            catalog = source_scene_tables(row) if acc.all else source_scene_tables(row, acc.principal)
            if not acc.all and not catalog:
                continue
            tables = [{"table_name": name, "description": entry.get("description", ""),
                       "row_filters": entry.get("row_filters", [])} for name, entry in catalog.items()]
            items.append({"datasource_id": row["id"], "name": row["name"], "type": row["type"],
                          "description": row.get("description", ""), "tables": tables, "all_tables": acc.all})
        return _ok({"ok": True, "datasources": items, "scenario_management_url": f"{settings.public_base_url}/#datasets"})
    except _EXPECTED as e:
        return _err(e)


@_tool()
def save_business_scenario(
    business_scenario: str,
    business_description: str,
    datasource_id: int,
    tables: list[dict[str, Any]],
    ctx: Context,
    dataset_id: int | None = None,
    analysis_approach: str = "",
    tags: list[str] | None = None,
    semantic_metrics: dict[str, Any] | None = None,
) -> dict:
    """按当前员工数据源授权新建或更新个人业务场景（含分析思路、逐表筛选和官方指标）。"""
    try:
        acc = _access(ctx)
        payload: dict[str, Any] = {
            "business_scenario": business_scenario,
            "business_description": business_description,
            "datasource_id": datasource_id,
            "tables": tables,
            "enabled": True,
        }
        if dataset_id is None:
            payload["analysis_approach"] = analysis_approach
            payload["tags"] = tags or []
            payload["semantic_metrics"] = semantic_metrics or {}
        else:
            if analysis_approach:
                payload["analysis_approach"] = analysis_approach
            if tags is not None:
                payload["tags"] = tags
            if semantic_metrics is not None:
                payload["semantic_metrics"] = semantic_metrics
        try:
            saved = (repo.update_dataset(dataset_id, payload, acc.principal) if dataset_id is not None
                     else repo.create_dataset(payload, acc.principal))
        except UniqueViolation as exc:
            raise repo.Invalid("业务场景名称已存在，请更换名称或传入 dataset_id 更新") from exc
        refreshed = access_for(acc.principal)
        return _ok({"ok": True, **_dataset_brief(saved, refreshed)})
    except _EXPECTED as e:
        return _err(e)


# ============================== 2. 数据探查 ==============================
def _describe_tables_page(acc: Access, dataset_id: int, *, ds: dict | None = None,
                          table_names: list[str] | None = None, offset: int = 0, limit: int = 10,
                          include_profile: bool = False, sample_rows: int = 5,
                          profile_rows: int | None = None, refresh_schema: bool = False) -> dict:
    ds = ds or repo.get_dataset(dataset_id)
    scope = {table["table_name"]: table for table in ds["tables"]}
    names = [name for name in scope if acc.can_table(dataset_id, name)]
    if table_names is not None:
        names = list(dict.fromkeys(table_names))
        for name in names:
            if name not in scope or not acc.can_table(dataset_id, name):
                raise PermissionDenied(f"数据表或视图不在授权的场景范围内：{name}")
    offset = max(0, offset)
    stop = offset + max(1, min(limit, 20))
    ds_row = services.datasource_for_dataset(ds)
    conn = services.get_connector(ds_row)
    allowed = None if acc.all else acc.tables.get(dataset_id, set())
    sample_rows = max(0, min(sample_rows, 50))
    profile_rows = max(0, min(profile_rows if profile_rows is not None else (100 if sample_rows else 0), 2000))

    def _inspect_table(name: str) -> dict:
        item = {"table_name": name, "table_description": scope[name]["table_description"]}
        try:
            if include_profile:
                item.update(services.describe_table(ds_row, name, sample_rows, profile_rows,
                                                    dataset=ds, allowed=allowed, include_count=False,
                                                    include_extrema=False, refresh_schema=refresh_schema))
            else:
                item["columns"] = conn.cached_columns(name, refresh=refresh_schema)
        except ConnectorError as exc:
            # 单表元数据失败时保留错误和其他表的结构，便于定位尚未覆盖的分析。
            item["profile_error" if include_profile else "columns_error"] = str(exc)
        return item

    page_names = names[offset:stop]
    if len(page_names) > 1:
        with ThreadPoolExecutor(max_workers=min(4, len(page_names))) as pool:
            futures = [pool.submit(contextvars.copy_context().run, _inspect_table, name) for name in page_names]
            tables = [future.result() for future in futures]
    else:
        tables = [_inspect_table(name) for name in page_names]
    return {"tables": tables, "total": len(names), "next_offset": stop if stop < len(names) else None}


@_tool()
def describe_tables(dataset_id: int, ctx: Context, table_names: list[str] | None = None,
                    offset: int = 0, limit: int = 10, include_profile: bool = False,
                    sample_rows: int = 5, profile_rows: int | None = None, refresh_schema: bool = False) -> dict:
    """批量查看授权表字段；include_profile=true 一并返回各表的轻量样本画像，省去逐表工具往返。
    轻量画像默认读取最多 100 行，不查总行数和全范围最值；不能当作业务覆盖证据。
    sample_rows=0 且未指定 profile_rows 时不读取样本。按 next_offset 翻完授权表；单表完整统计用 describe_table(mode='full')。
    数据库字段结构短期缓存，可用 refresh_schema=true 刷新；权限每次重新校验。"""
    try:
        acc = _access(ctx)
        _require_dataset(acc, dataset_id)
        return _ok({"ok": True, **_describe_tables_page(
            acc, dataset_id, table_names=table_names, offset=offset, limit=limit,
            include_profile=include_profile, sample_rows=sample_rows, profile_rows=profile_rows,
            refresh_schema=refresh_schema)})
    except _EXPECTED as e:
        return _err(e)


@_tool()
def prepare_report_context(dataset_id: int, ctx: Context, query: str = "",
                           mode: Literal["structured", "free"] = "structured",
                           sample_rows: int = 5, profile_rows: int = 100) -> dict:
    """一步加载业务场景（分析思路、授权表、官方指标）、前 10 张表轻量画像、生效样式（含 style_id）与模板契约。"""
    try:
        acc = _access(ctx)
        _require_dataset(acc, dataset_id)
        ds = repo.get_dataset(dataset_id)
        tables_page = _describe_tables_page(
            acc, dataset_id, ds=ds, offset=0, limit=10, include_profile=True,
            sample_rows=sample_rows, profile_rows=profile_rows,
        )
        style = _resolve_style(acc.principal, ds["business_scenario"], query=query, include_css=False)
        contract = get_free_report_spec() if mode == "free" else get_report_spec()
        return _ok({
            "ok": True,
            "scenario": _dataset_brief(ds, acc),
            "tables": tables_page["tables"],
            "total_tables": tables_page["total"],
            "next_offset": tables_page["next_offset"],
            "style": style,
            "contract": contract,
        })
    except _EXPECTED as e:
        return _err(e)


@_tool()
def describe_table(dataset_id: int, table_name: str, ctx: Context, sample_rows: int = 5,
                   mode: Literal["light", "full"] = "light", profile_rows: int | None = None,
                   refresh_schema: bool = False) -> dict:
    """查看授权表字段、样本及画像。默认 light 读取最多 100 行，跳过总行数和全范围最值。
    full 恢复完整探查（默认 2,000 行画像、COUNT 和数值/日期最值），仅在需要这些技术统计时使用。
    light 下 sample_rows=0 且未指定 profile_rows 时仅取元数据；profile_rows 可指定 0–2,000。
    row_count_status/extrema_status 明确统计是否执行；轻量结果及样本不能证明全量业务覆盖。"""
    try:
        acc = _access(ctx)
        _require_dataset(acc, dataset_id)
        ds = repo.get_dataset(dataset_id)
        if table_name not in {t["table_name"] for t in ds["tables"]}:
            raise repo.Invalid(f"表 {table_name} 不属于业务场景「{ds['business_scenario']}」")
        if not acc.can_table(dataset_id, table_name):
            raise PermissionDenied(f"{acc.principal.label} 无权访问数据表：{table_name}")
        ds_row = services.datasource_for_dataset(ds)
        allowed = None if acc.all else acc.tables.get(dataset_id, set())
        sample_rows = max(0, min(sample_rows, 50))
        if mode not in ("light", "full"):
            raise repo.Invalid("探查模式必须为 light 或 full")
        default_rows = (2000 if mode == "full" else 100) if sample_rows else 0
        rows = profile_rows if profile_rows is not None else default_rows
        return _ok({"ok": True, **services.describe_table(ds_row, table_name, sample_rows,
                                                          max(0, min(rows, 2000)), dataset=ds, allowed=allowed,
                                                          include_count=mode == "full", include_extrema=mode == "full",
                                                          refresh_schema=refresh_schema)})
    except _EXPECTED as e:
        return _err(e)


@_tool()
def run_query(dataset_id: int, sql: str, ctx: Context, limit: int | None = None,
              snapshot: bool = False, sample_rows: int = 5) -> dict:
    """在业务场景上执行只读 SQL（单条 SELECT/WITH），只能引用该场景中当前员工有权限的表或视图。
    场景的逐表取值条件由服务端在 JOIN、聚合前自动应用。
    SQL 方言与数据源类型一致（API 使用 DuckDB）。返回实际引用的 tables_used，列名统一转小写。
    普通模式默认返回至多 200 行；按完整业务口径选择源端正式指标和证据行，不自行聚合补算业务值。
    snapshot=true 将完整 SQL 结果保存为 JSON，由报告使用 snapshot_id 引用。
    MCP 仅返回行数和少量明细样本（sample_rows 0–200），≤200 行的正式查询可一次同时取得完整证据与快照。
    truncated=true 表示超过返回行数上限，未创建快照；请缩小到所需正式指标或完整分批查询。
    truncated=false 只说明返回上限未截断 SQL 结果，业务覆盖仍需检查查询口径、SQL 自带的行数限制和数据源覆盖。"""
    try:
        acc = _access(ctx)
        _require_dataset(acc, dataset_id)
        ds = repo.get_dataset(dataset_id)
        ds_row = services.datasource_for_dataset(ds)
        allowed = None if acc.all else acc.tables.get(dataset_id, set())
        result = services.run_query(ds_row, sql, limit, dataset=ds, allowed=allowed, snapshot=snapshot)
        if snapshot:
            rows = result.pop("rows")
            if result["truncated"]:
                # 截断结果不能以快照交付，防止联动指标把不完整的数据当成全量。
                del result["columns"]
                result["warning"] = "查询结果超过行数上限，未保存快照。请缩小到所需正式指标或按完整口径分批查询。"
            else:
                with measure("snapshot.write"):
                    result.update(report_data.save_query(acc.principal.emp_no, result["columns"], rows,
                                                         max(0, min(sample_rows, 200)),
                                                         origin={"scope": report_queries.scope_for(dataset_id, ds, ds_row,
                                                                                                  result["tables_used"]),
                                                                 "consistency": "single_statement", "scales": {}}
                                                         if result.get("source_complete", True) else None))
                if result.get("source_complete") is False:
                    result["warning"] = "API 未确认全量，该快照仅供探查，正式报告使用已配置完整合同的取数计划。"
        return _ok({"ok": True, **result})
    except _EXPECTED as e:
        return _err(e)


@_tool()
def get_skill_guide(include_example: bool = False,
                    reference: Literal["fact-contract", "green-energy-september-facts", "display-contract", "declarative-report", "free-report"] | None = None,
                    asset: Literal["report-formatters.js", "audit_report.py"] | None = None) -> str:
    """返回 smart-report 技能全文：报告生成与修订流程、分析方法、权限及交付要求。
    适用于没有本地技能机制的 agent；开始生成报告前调用一次。include_example=true 时附带可运行的 HTML 示例。
    reference 可附带取数、展示及模板契约。旧绿能参考标识返回现行取数契约和历史复现说明。
    asset 单独返回格式化模块或可执行审计脚本的完整原文，供没有本地技能的 agent 保存到同一目录；不可与其他选项并用。"""
    if asset is not None:
        # 固定资产白名单，不开放客户端任意路径读取，也不在返回代码前混入技能正文。
        if asset not in {"report-formatters.js", "audit_report.py"}:
            raise ValueError("不支持的技能资产")
        if reference is not None or include_example:
            raise ValueError("asset 不能与 reference 或 include_example 同时使用")
        return (SKILL_DIR / "assets" / asset).read_text(encoding="utf-8")
    text = _skill_body()
    if reference == "green-energy-september-facts":
        # 已分发的客户端仍可调用旧标识。历史附件只有在用户指定基准后才用于核对。
        text += "\n\n历史案例已归档。复现时先确认用户指定的报告版本和原始资料，再按授权取得同口径事实。"
        reference = "fact-contract"
    if reference is not None:
        # 仅允许已维护的参考名，不能把客户端参数当作任意文件路径读取。
        files = {"fact-contract": "fact-contract.md", "display-contract": "display-contract.md",
                 "declarative-report": "declarative-report.md", "free-report": "free-report.md"}
        if reference not in files:
            raise ValueError("不支持的技能参考文档")
        text += "\n\n---\n\n" + (SKILL_DIR / "references" / files[reference]).read_text(encoding="utf-8")
    if include_example:
        text += "\n\n## 附：筛选联动与结论动态着色的 HTML 示例\n\n```html\n" + EXAMPLE_FILE.read_text(encoding="utf-8") + "```\n"
    return text


@mcp.prompt(name="smart_report", description="按 SmartReportGen 流程，根据需求生成交互式 HTML 分析报告")
def smart_report_prompt(requirement: str) -> str:
    return (_skill_body() + "\n\n---\n\n## 本次需求\n\n" + requirement +
            "\n\n请严格按以上流程，使用 smartreportgen MCP 工具完成报告并交付验收通过的版本。")


@_tool()
def get_template_spec() -> str:
    """返回服务端结构化报告模板的 Markdown 语法。维护结构化模板时使用。直接编写 HTML 报告时参考 get_skill_guide。"""
    return SPEC_FILE.read_text(encoding="utf-8")


# ============================== 4. 样式规则 ==============================
def _resolve_style(principal: Principal, business_scenario: str, query: str = "", include_css: bool = True) -> dict:
    res = repo.style_rules_for(business_scenario, query or None, owner=principal.emp_no)
    builtin = design.builtin_style(res["specified"]["theme"])
    if include_css:
        try:
            builtin["style_id"] = report_data.save_style(principal.emp_no, builtin["css"])
        except report_data.ReportDataError:
            pass
    else:
        css = builtin.pop("css")
        builtin["style_id"] = report_data.save_style(principal.emp_no, css)
    rules = [{"id": r["id"], "name": r["name"], "scope": r["scope"],
              **({"relevance": r["relevance"]} if r.get("relevance") else {})}
             for r in res["general_rules"] + res["scenario_rules"]]
    return {
        "ok": True,
        "business_scenario": res["business_scenario"],
        "precedence": res["precedence"],
        "reporting_rules": res["reporting_rules"],
        "specified": res["specified"],
        # 自编 HTML 可以直接复用内置组件样式；指定主题和展示要求仍通过原有字段返回。
        "builtin_style": builtin,
        "rules": rules,
        "note": ("reporting_rules 提供跨场景的事实、展示和交付规则。事实与交付要求独立生效，展示默认值按 precedence 应用。"
                 "specified 合并了业务场景、通用及内置样式。直接编写 HTML 时，请将适用的 theme 和 items 落实到 CSS、图表配置与脚本。"
                 "采用内置样式时，可以内联 builtin_style.css 并参考组件说明；同类卡片复用 CSS 与图表配置。"
                 "items 各类别内排在前面的优先，请落实到健康度、升降色、数字格式与文案。"
                 "当前用户明确展示要求优先，低层许可不能豁免高层禁止；不得以智能体判断放宽数字格式、单位、口径、缺失处理和状态色。"
                 "章节、图表类型、筛选维度及未指定的装饰样式由用户需求和数据证据决定。"
                 "保存前按技能执行源文档审计、展开后六类展示面及边界复核和最终门禁，失败不得保存。"
                 "使用服务端结构化模板渲染时，theme 会自动应用。"),
        "warnings": res["warnings"],
    }


@_tool()
def get_style_rules(business_scenario: str, ctx: Context, query: str = "", include_css: bool = True) -> dict:
    """检索该业务场景的指定样式：你本人为该场景配置的样式 ≥ 管理员配置的通用规则 ≥ 内置经营报告主题，合并为一份 specified，
    作为展示约束交给你：specified.theme 为视觉样式取值（渲染时自动生效），specified.items 为好坏判定、涨跌颜色、数字格式、
    文案等展示要求。章节、图表类型和筛选条件由用户需求、场景分析思路及数据证据决定。
    reporting_rules 提供跨场景的事实、展示和交付规则。事实与交付要求独立生效，展示默认值按下方优先级应用。
    builtin_style 提供 style_id（可通过 <style data-srg-style="style_id"></style> 由服务端展开）及组件说明；
    传 include_css=false 可省略 22KB 内置 CSS 原文以减少上下文占用。
    展示顺序：当前用户明确要求 > 业务场景规则 > 通用规则 > 内置默认值。
    低优先级条目及智能体判断不能豁免高优先级规则；仅章节、图表、筛选维度及未指定装饰样式可裁量。
    将生效要求落实到全部展示面，按技能门禁通过后保存。"""
    try:
        p = _principal(ctx)
        return _ok(_resolve_style(p, business_scenario, query=query, include_css=include_css))
    except _EXPECTED as e:
        return _err(e)


# ============================== 5–7. 模板与报告 ==============================
@_tool()
def validate_report_template(markdown: str, ctx: Context, dataset_id: int | None = None, execute: bool = True) -> dict:
    """校验旧结构化模板的语法、图表参数、度量表达式。execute=true 时按权限执行查询并检查字段引用。
    新报告的模板及服务端验收流程使用 get_report_spec 与 preview_report_spec。"""
    try:
        acc = _access(ctx)
        if dataset_id:
            _require_dataset(acc, dataset_id)
        return _ok(validate_md(markdown, dataset_id, execute, guard=_guard(acc), owner=acc.principal.emp_no))
    except _EXPECTED as e:
        return _err(e)


@_tool()
def save_report_template(name: str, markdown: str, ctx: Context, dataset_id: int | None = None,
                         user_requirement: str = "", analysis_goal: str = "", template_id: int | None = None,
                         business_summary: str | None = None,
                         template_type: Literal["business", "structured"] | None = None) -> dict:
    """维护历史 Markdown 和旧结构化源码。新报告使用 save_report_spec 保存声明式模板。"""
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
        if template_type is not None:
            data["template_type"] = template_type
        t = repo.save_template(data, template_id)
    except _EXPECTED as e:
        return _err(e)
    return _ok({"ok": True, "template_id": t["id"], "version": t["version"], "name": t["name"],
                "template_type": t["template_type"], "summary": t["summary"]})


@_tool()
def get_report_template(template_id: int, ctx: Context, version: int | None = None,
                        report_id: int | None = None, report_version: int | None = None) -> dict:
    """读取本人的模板，或指定有编辑权限的报告和版本，读取该版本实际关联的声明式模板。"""
    try:
        acc = _access(ctx)
        if report_id is not None:
            t = repo.template_from_report(template_id, acc.principal, report_id, report_version)
            if version is not None and t["version"] != version:
                raise repo.Invalid("所请求的模板版本与报告关联版本不一致")
        else:
            t = repo.get_template(template_id)
            _check_owner(acc, t.get("created_by"), "报告模板")
            if version is not None:
                t = {**{key: t[key] for key in ("id", "template_type")}, **repo.get_template_version(template_id, version)}
        if decoded := report_spec.decode_template(t["markdown"]):
            spec, dataset_id = decoded
            key = "free_report" if isinstance(spec, report_free.FreeReport) else "spec"
            return _ok({"ok": True, **{k: v for k, v in t.items() if k != "markdown"},
                        key: spec.model_dump(mode="json"), "dataset_id": dataset_id})
        return _ok({"ok": True, **t})
    except _EXPECTED as e:
        return _err(e)


@_tool()
def get_report_spec() -> dict:
    """查看声明式报告及官方指标查询计划的 JSON 契约。"""
    theme_groups = [{**group, "tokens": [token for token in group["tokens"] if token["key"] in design.BUILTIN_TOKENS]}
                    for group in design.TOKEN_GROUPS if group["key"] != "advanced"]
    return {"schema": report_spec.ReportSpec.model_json_schema(), "theme_tokens": theme_groups,
            "guide": (SKILL_DIR / "references" / "declarative-report.md").read_text(encoding="utf-8")}


@_tool()
def save_report_spec(name: str, dataset_id: int, spec: report_spec.ReportSpec, ctx: Context,
                     template_id: int | None = None, expected_version: int | None = None,
                     business_summary: str = "") -> dict:
    """保存有固定场景、字段和筛选映射的声明式模板；传 template_id 更新本人的模板版本，可用 expected_version 检查并发版本。"""
    try:
        acc = _access(ctx)
        _require_dataset(acc, dataset_id)
        if template_id is not None:
            _check_owner(acc, repo.get_template(template_id)["created_by"], "报告模板")
        elif expected_version is not None:
            raise repo.Invalid("指定 expected_version 时须同时提供 template_id")
        content = report_spec.encode_template(spec, dataset_id)
        row = repo.save_template({"name": name, "dataset_id": dataset_id, "markdown": content,
                                  "business_summary": business_summary, "created_by": acc.principal.emp_no,
                                  "template_type": "business"}, template_id, expected_version=expected_version)
        return _ok({"ok": True, "template_id": row["id"], "version": row["version"], "summary": row["summary"]})
    except _EXPECTED as e:
        return _err(e)


@_tool()
def get_free_report_spec() -> dict:
    """查看自由 HTML/CSS、模块、受管数据槽位和 ECharts 声明式视觉选项。"""
    return {"schema": report_free.FreeReport.model_json_schema(),
            "guide": (SKILL_DIR / "references" / "free-report.md").read_text(encoding="utf-8")}


def _store_free_template(acc, name, dataset_id, free_report, template_id=None,
                         expected_version=None, business_summary=None):
    _require_dataset(acc, dataset_id)
    data = {"name": name, "dataset_id": dataset_id, "markdown": report_spec.encode_template(free_report, dataset_id),
            "created_by": acc.principal.emp_no, "template_type": "business"}
    if business_summary is not None:
        data["business_summary"] = business_summary
    row = repo.save_template(data, template_id, expected_version=expected_version)
    return _ok({"ok": True, "template_id": row["id"], "version": row["version"], "summary": row["summary"]})


@_tool()
def save_free_report_template(name: str, dataset_id: int, free_report: report_free.FreeReport, ctx: Context,
                              template_id: int | None = None, expected_version: int | None = None,
                              business_summary: str | None = None) -> dict:
    """保存自由版式模板。改页面骨架、绑定或全局 CSS 时提交完整定义。更新模板须提供当前版本。"""
    try:
        acc = _access(ctx)
        if (template_id is None) != (expected_version is None):
            raise repo.Invalid("更新自由模板时须同时提供模板 ID 和 expected_version")
        if template_id is not None:
            current = repo.get_template(template_id)
            _check_owner(acc, current["created_by"], "报告模板")
            if current["dataset_id"] != dataset_id:
                raise repo.Invalid("自由模板的业务场景固定，改用其他场景时请另建模板")
        return _store_free_template(acc, name, dataset_id, free_report, template_id,
                                    expected_version, business_summary)
    except _EXPECTED as e:
        return _err(e)


@_tool()
def patch_free_report_template(template_id: int, expected_version: int,
                               modules: dict[report_queries.Key, report_free.Module], ctx: Context) -> dict:
    """只提交要替换的现有模块，保留布局、其他模块和全局绑定。版本已变化时返回 conflict。"""
    try:
        acc = _access(ctx)
        current = repo.get_template(template_id)
        _check_owner(acc, current["created_by"], "报告模板")
        if current["version"] != expected_version:
            raise repo.ReportConflict("报告模板已被修改，请读取最新版本后再提交")
        decoded = report_spec.decode_template(current["markdown"])
        if not decoded or not isinstance(decoded[0], report_free.FreeReport):
            raise repo.Invalid("只能补丁更新自由版式模板")
        original, dataset_id = decoded
        if not modules or modules.keys() - original.modules.keys():
            raise repo.Invalid("模块补丁须包含已有模块，增删模块请更新完整模板")
        content = original.model_dump(mode="json")
        content["modules"].update({key: module.model_dump(mode="json") for key, module in modules.items()})
        updated = report_free.FreeReport.model_validate(content)
        # 数据库事务持有模板行锁时再次校验版本，防止并发编辑覆盖对方的模块。
        return _store_free_template(acc, current["name"], dataset_id, updated, template_id, expected_version)
    except _EXPECTED as e:
        return _err(e)


@_tool()
def query_report_data(dataset_id: int, queries: dict[report_queries.Key, report_queries.QueryPlan], ctx: Context,
                      parameters: dict[report_queries.Key, report_queries.ParamValue] | None = None,
                      sample_rows: Annotated[int, Field(ge=0, le=200)] = 5) -> dict:
    """同一批次取数，可信结果可用于多快照报告。只允许投影字段或调用场景登记的官方指标。"""
    try:
        acc = _access(ctx)
        _require_dataset(acc, dataset_id)
        if not 1 <= len(queries) <= 16:
            raise repo.Invalid("每一批取数包含 1 至 16 条计划")
        dataset = repo.get_dataset(dataset_id)
        source = services.datasource_for_dataset(dataset)
        if not dataset.get("enabled", True):
            raise PermissionDenied("该业务场景已停用")
        results = report_queries.capture(dataset_id, dataset, source,
                     None if acc.all else acc.tables.get(dataset_id, set()), acc.principal.emp_no,
                     queries, parameters or {}, settings.query_max_rows, sample_rows)
        return _ok({"ok": True, "queries": results})
    except _EXPECTED as e:
        return _err(e)


@_tool()
def preview_report_spec(ctx: Context, template_id: int | None = None, template_version: int | None = None,
                        spec: report_spec.ReportSpec | None = None, dataset_id: int | None = None,
                        bindings: dict[report_queries.Key, str] | None = None,
                        parameters: dict[report_queries.Key, report_queries.ParamValue] | None = None,
                        report_id: int | None = None, base_version: int | None = None,
                        theme_overrides: dict[str, Any] | None = None) -> dict:
    """编译并验收离线报告。用 theme_overrides 和报告基准版本直接复用原数据调整样式；换图可提交编辑后的 spec。"""
    return _preview_report(ctx, report_spec.ReportSpec, spec, dataset_id, template_id, template_version,
                           bindings, parameters, report_id, base_version, theme_overrides)


@_tool()
def preview_free_report(ctx: Context, template_id: int | None = None, template_version: int | None = None,
                        free_report: report_free.FreeReport | None = None, dataset_id: int | None = None,
                        bindings: dict[report_queries.Key, str] | None = None,
                        parameters: dict[report_queries.Key, report_queries.ParamValue] | None = None,
                        report_id: int | None = None, base_version: int | None = None) -> dict:
    """整页验收自由版式。修订时指定基准并省略取数参数及绑定可复用原数据，重跑传新账期参数。"""
    return _preview_report(ctx, report_free.FreeReport, free_report, dataset_id, template_id, template_version,
                           bindings, parameters, report_id, base_version, None)


def _preview_report(ctx, model, spec, dataset_id, template_id, template_version,
                    bindings, parameters, report_id, base_version, theme_overrides):
    try:
        acc = _access(ctx)
        base = template = None
        if theme_overrides is not None and (report_id is None or any(value is not None for value in (
                spec, template_id, template_version, dataset_id, bindings, parameters))):
            raise repo.Invalid("theme_overrides 只和报告 ID、基准版本一起使用")
        if base_version is not None and report_id is None:
            raise repo.Invalid("指定基准版本时须同时指定报告 ID")
        if report_id is not None:
            base = repo.get_report(report_id, acc.principal, with_html=False, with_source=True, version=base_version)
            if base["access_level"] not in ("manage", "edit"):
                raise PermissionDenied("无权编辑该报告")
        if template_id is not None and spec is None:
            t = repo.get_template(template_id)
            if acc.all or t["created_by"] == acc.principal.emp_no:
                if template_version is not None:
                    t = {**t, **repo.get_template_version(template_id, template_version)}
            elif base:
                t = repo.template_from_report(template_id, acc.principal, base["id"], base["revision"])
                if template_version is not None and t["version"] != template_version:
                    raise PermissionDenied("只能使用协作版本关联的模板版本")
            else:
                raise PermissionDenied("无权访问该模板")
            decoded = report_spec.decode_template(t["markdown"])
            if not decoded or not isinstance(decoded[0], model):
                raise repo.Invalid("模板类型与预览工具不匹配，请使用该类型的预览入口")
            spec, dataset_id = decoded
            template = t
        elif template_id is not None or template_version is not None:
            raise repo.Invalid("声明式内容和模板 ID 只能提供一种")
        elif spec is None and base and base.get("markdown"):
            decoded = report_spec.decode_template(base["markdown"])
            if decoded and isinstance(decoded[0], model):
                spec, dataset_id = decoded
                if theme_overrides is not None:
                    # 局部样式修订复用基准快照，衍生定义随新报告版本保存。
                    theme = {**spec.theme, **report_spec.resolve_report_theme(theme_overrides)}
                    spec = spec.model_copy(update={"theme": theme})
                elif base.get("template_id") is not None:
                    template = {"id": base["template_id"], "version": base["template_version"]}
        if spec is None or dataset_id is None:
            raise repo.Invalid("请提供对应类型的模板 ID、报告基准或当前场景的完整定义")
        _require_dataset(acc, dataset_id)
        return _ok({"ok": True, **report_pipeline.preview(spec, dataset_id, acc.principal, bindings=bindings,
                        parameters=parameters, template=template, base=base, requested_report_id=report_id)})
    except _EXPECTED as e:
        return _err(e)


@_tool()
def get_preview_file(preview_id: str, ctx: Context, offset: Annotated[int, Field(ge=0)] = 0,
                     chars: Annotated[int, Field(ge=1, le=24000)] = 12000) -> dict:
    """通过已登录的 MCP 读取验收预览，按字符分页，无需向 Shell 或浏览器传 IAM 令牌。"""
    try:
        _, page = report_pipeline.load_preview(_access(ctx).principal, preview_id, certified=True)
        end = min(offset + chars, len(page))
        return _ok({"ok": True, "html": page[offset:end], "offset": offset,
                    "next_offset": end if end < len(page) else None, "total_chars": len(page),
                    "html_sha256": sha256(page.encode()).hexdigest()})
    except _EXPECTED as e:
        return _err(e)


@_tool()
def list_report_templates(ctx: Context, keyword: str = "") -> dict:
    """列出本人创建的报告模板（可按名称/需求/业务场景关键字过滤），便于复用历史模板。"""
    try:
        acc = _access(ctx)
    except _EXPECTED as e:
        return _err(e)
    rows = repo.list_templates(keyword or None, owner=_owner(acc))
    return _ok({"ok": True, "templates": [
        {"template_id": r["id"], "name": r["name"], "business_scenario": r["business_scenario"], "version": r["version"],
         "template_type": r["template_type"],
         "user_requirement": r["user_requirement"], "updated_at": r["updated_at"], "summary": r["summary"]} for r in rows[:50]]})


@_tool()
def render_report_html(template_id: int, ctx: Context, report_name: str = "",
                       parameters: dict[report_queries.Key, report_queries.ParamValue] | None = None,
                       template_version: Annotated[int, Field(ge=1)] | None = None) -> dict:
    """以当前权限执行本人模板。声明式模板传账期参数，服务端验收后直接保存报告。"""
    try:
        acc = _access(ctx)
        tpl = repo.get_template(template_id)
        _check_owner(acc, tpl.get("created_by"), "报告模板")
        if template_version is not None:
            tpl = {**tpl, **repo.get_template_version(template_id, template_version)}
        r = services.render_template_to_report(tpl, report_name, acc.principal, parameters)
    except _EXPECTED as e:
        return _err(e)
    return _ok({"ok": True, "report_id": r["id"], "name": r["name"], "version": r["revision"],
                **repo.report_links(r["id"], r["revision"]), "html_bytes": r["meta"]["html_bytes"],
                "warnings": r["warnings"], "queries": r["meta"].get("queries")})


@_tool()
def preview_report_html(html: str, ctx: Context, report_id: int | None = None,
                        base_version: int | None = None) -> dict:
    """诊断旧单文件 HTML 的静态结构；正式交付使用 preview_report_spec。"""
    try:
        principal = _access(ctx).principal
        if base_version is not None and report_id is None:
            raise repo.Invalid("指定基准版本时须同时指定报告 ID")
        base = None
        if report_id is not None:
            base = repo.get_report(report_id, principal, version=base_version)
            if base["access_level"] not in ("manage", "edit"):
                raise PermissionDenied("无权编辑该报告")
        with measure("preview.embed"):
            page = report_data.embed(html, principal.emp_no, base["html"] if base else "")
        if base and not base["meta"].get("single_file"):
            # 与保存修订版一致，旧版内置 ECharts 引用也可在预览中离线运行。
            page = inline_legacy_echarts(page)
        with measure("preview.validate"):
            if error := report_document_error(page):
                raise repo.Invalid(error)
            audit = report_spec.audit_module()
            diagnostics = audit.audit_html(html) + audit.audit_html(page, expanded=True)
        with measure("preview.write"):
            ref = report_data.save_preview(
                principal.emp_no,
                page,
                source_html=html,
                report_id=base["id"] if base else None,
                requested_report_id=report_id,
                base_version=base["revision"] if base else None,
            )
        url = f"{settings.public_base_url}/reports/preview/{ref}"
    except _EXPECTED as e:
        return _err(e)
    return _ok({
        "ok": True,
        "preview_id": str(ref),
        "draft_only": True,
        "static_audit": list(dict.fromkeys(diagnostics)),
        "url": url,
        "download_url": f"{url}?download=true",
        "html_bytes": len(page.encode("utf-8")),
        "source_sha256": sha256(html.encode("utf-8")).hexdigest(),
        "html_sha256": sha256(page.encode("utf-8")).hexdigest(),
        "expires_in_seconds": report_data.TTL_SECONDS,
    })


@_tool()
def save_report(name: str, html: str | None = None, ctx: Context = None, template_id: int | None = None,
                template_version: int | None = None, preview_id: str | None = None) -> dict:
    """保存服务端已验收的声明式预览。可提供匹配的模板 ID 和版本作为额外确认。"""
    try:
        if html is not None or preview_id is None:
            raise repo.Invalid("新报告请提交正式模板预览工具返回且已验收的 preview_id")
        r = report_pipeline.persist(name, _access(ctx).principal, preview_id,
                                    template_id=template_id, template_version=template_version)
    except _EXPECTED as e:
        return _err(e)
    return _ok({"ok": True, "report_id": r["id"], "version": r["revision"],
                "template_id": r["template_id"], "template_version": r["template_version"],
                **repo.report_links(r["id"], r["revision"]), "html_bytes": r["meta"]["html_bytes"]})


@_tool()
def list_reports(ctx: Context, keyword: str = "", template_id: int | None = None) -> dict:
    """查找可访问的报告，每份报告返回一项，含当前版本及最新版本号。"""
    try:
        rows = repo.list_reports(_access(ctx).principal, template_id, keyword)
    except _EXPECTED as e:
        return _err(e)
    return _ok({"ok": True, "reports": [
        {"report_id": r["id"], "name": r["name"], "current_version": r["current_version"],
         "latest_version": r["latest_version"], "access_level": r["access_level"],
         "template_id": r["template_id"], "template_name": r["template_name"], "created_by": r["created_by"],
         **repo.report_links(r["id"])} for r in rows[:50]
    ], "total": len(rows)})


@_tool()
def get_report(report_id: int, ctx: Context, include_html: bool = False, version: int | None = None,
               include_source: bool = False, omit_snapshot_data: bool = False) -> dict:
    """读取可访问的报告。显式 version 优先，省略时读取本账号的当前选择或最新版。
    include_html 返回该版本的 HTML；include_source 返回有权限读取的版本 Markdown 源码。
    同时传 omit_snapshot_data=true 时，快照数据替换为可复用的基准引用，避免把它们读入模型。"""
    try:
        r = repo.get_report(report_id, _access(ctx).principal, with_html=include_html,
                            version=version, with_source=include_source)
        if include_html and omit_snapshot_data:
            r["html"] = report_data.for_edit(r["html"])
        if include_source and r.get("markdown") and (decoded := report_spec.decode_template(r["markdown"])):
            key = "free_report" if isinstance(decoded[0], report_free.FreeReport) else "spec"
            r[key] = decoded[0].model_dump(mode="json")
            del r["markdown"]
    except _EXPECTED as e:
        return _err(e)
    return _ok({"ok": True, **r, **repo.report_links(r["id"], r["revision"])})


@_tool()
def list_report_versions(report_id: int, ctx: Context) -> dict:
    """列出报告版本、基准版本、修改人、修改说明及当前选择。"""
    try:
        return _ok({"ok": True, **repo.list_report_versions(report_id, _access(ctx).principal)})
    except _EXPECTED as e:
        return _err(e)


@_tool()
def select_report_version(report_id: int, ctx: Context, version: int | None = None) -> dict:
    """确认本账号后续查看和修改使用的版本。version 留空恢复跟随最新版本。"""
    try:
        r = repo.select_report_version(report_id, _access(ctx).principal, version)
    except _EXPECTED as e:
        return _err(e)
    return _ok({"ok": True, **r, **repo.report_links(r["id"], r["revision"])})


@_tool()
def revise_report(report_id: int, name: str, ctx: Context, base_version: int | None = None,
                  html: str | None = None, markdown: str | None = None, change_note: str = "",
                  expected_version: int | None = None, preview_id: str | None = None) -> dict:
    """追加同一报告的新版本。正文变更提交已验收的 preview_id；只改名称或说明时可省略正文。"""
    try:
        acc = _access(ctx)
        with measure("report.revise"):
            if preview_id is not None:
                if html is not None or markdown is not None:
                    raise repo.Invalid("预览 ID 须单独提交")
                r = report_pipeline.persist(name, acc.principal, preview_id, report_id=report_id,
                                            base_version=base_version, change_note=change_note,
                                            expected_version=expected_version)
            else:
                r = services.revise_report(report_id, acc.principal, name, base_version=base_version,
                                           html=html, markdown=markdown, change_note=change_note,
                                           expected_version=expected_version)
    except _EXPECTED as e:
        return _err(e)
    return _ok({"ok": True, "report_id": r["id"], **r})


# ============================== 启动 ==============================
class TokenAuth:
    """MCP 仅接受 IAM Bearer 令牌，核实后的员工身份放入请求 state 供工具复用。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/mcp"):
            authorization = Headers(scope=scope).get("authorization")
            # 只记录 Authorization 请求头是否存在，避免把 IAM 令牌写入日志。
            log.info("MCP authorization_present=%s", authorization is not None)
            try:
                principal = await run_in_threadpool(auth.verify_bearer, authorization)
            except auth.AuthUnavailable as e:
                await JSONResponse({"error": "iam_unavailable", "message": str(e)}, status_code=503)(scope, receive, send)
                return
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
    repo.migrate_report_versions()
    repo.load_custom_words()
    try:
        uvicorn.run(build_app(), host="0.0.0.0", port=settings.mcp_port, log_level="info")
    finally:
        report_browser.close_pool()


if __name__ == "__main__":
    main()
