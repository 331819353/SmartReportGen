"""覆盖 HTML 草稿、真实离线浏览器、持久化版本和当前数据权限。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from markdown_it import MarkdownIt

from smartreportgen import db, mcp_server as mcp, repo, report_browser, report_data, report_document, report_draft
from smartreportgen import report_pipeline, report_queries, services
from smartreportgen import style_preview
from smartreportgen.api import app
from smartreportgen.auth import Principal
from smartreportgen.config import settings
from smartreportgen.connectors.api import APIConnector
from smartreportgen.permission import Access, PermissionDenied, access_for

OWNER = Principal("draft-author")
DATASET = {"id": 91, "datasource_id": 12, "business_scenario": "营业额", "enabled": True,
           "tables": [{"table_name": "facts", "row_filters": [{"column": "region", "values": ["华东"]}]}]}
SOURCE = {"id": 12, "type": "api", "enabled": True, "name": "测试接口", "updated_at": "fixture"}
SCOPE = report_queries.scope_for(91, DATASET, SOURCE, ["facts"])


@pytest.fixture(autouse=True)
def work_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "report_work_dir", str(tmp_path))


@pytest.fixture(scope="module")
def chromium():
    if not Path(os.environ.get("SRG_CHROMIUM_PATH", "/usr/bin/chromium")).is_file():
        pytest.skip("需要部署镜像中的 Chromium")
    yield
    report_browser.close_pool()


@pytest.fixture
def facts(monkeypatch):
    access = Access(OWNER, tables={91: {"facts"}})
    monkeypatch.setattr(mcp, "_access", lambda ctx: access)
    monkeypatch.setattr(report_queries, "access_for", lambda principal: access)
    monkeypatch.setattr(report_pipeline, "access_for", lambda principal: access)
    monkeypatch.setattr(report_draft, "access_for", lambda principal: access)
    monkeypatch.setattr(repo, "get_dataset", lambda dataset_id: DATASET)
    monkeypatch.setattr(repo, "get_datasource", lambda source_id: SOURCE)
    ref = report_data.save_query(OWNER.emp_no, ["month", "amount"], [["1月", {"$decimal": "123.45"}],
                                                                  ["2月", {"$decimal": "250.25"}]],
                                 2, origin={"scope": SCOPE})["snapshot_id"]
    return access, ref


def _populate(draft_id):
    for section_id, html, js in (
        ("overview", '<h1>月度报告</h1><output id="amount">—</output>',
         """const rows = SRG.rows('facts');
         document.querySelector('#amount').textContent = rows[0].amount.$decimal;"""),
        ("trend", '<h2>趋势</h2><div id="trend-chart" data-chart style="height:260px"></div>',
         """const rows = SRG.rows('facts');
         if (SRG.num(rows[0].amount) !== 123.45) throw Error('Decimal 无法用于绘图');
         SRG.initChart('trend-chart', {animation:false, xAxis:{type:'category',data:rows.map(r=>r.month)},
          yAxis:{type:'value'}, series:[{type:'bar',data:rows.map(r=>SRG.num(r.amount))}]});"""),
        ("details", '<h2>明细</h2><label>月份 <select id="period"><option>1月</option><option>2月</option></select></label>'
                    '<table style="width:750px"><thead><tr><th>月份</th></tr></thead><tbody><tr><td>1月</td></tr></tbody></table>',
         """const rows = SRG.rows('facts');
         document.querySelector('#period').addEventListener('change', e => {
           const row = rows.find(r => r.month === e.target.value);
           document.querySelector('#amount').textContent = row.amount.$decimal;
         });"""),
    ):
        result = mcp.upsert_html_section(draft_id, None, section_id, html, js=js)
        assert result["ok"], result


def test_aggregate_snapshot_keeps_table_and_row_scope(facts, monkeypatch):
    connector = APIConnector(SOURCE, "")
    records = {"facts": [
        {"region": "华东", "amount": 11}, {"region": "华北", "amount": 999}, {"region": "华东", "amount": 12}
    ]}
    connector._fetch_pages = lambda table: (records[table], "fixture", True)
    monkeypatch.setattr(services, "get_connector", lambda source: connector)
    monkeypatch.setattr(services, "datasource_for_dataset", lambda dataset: SOURCE)
    try:
        result = mcp.run_query(91, "SELECT region, SUM(amount) * 2 AS total FROM facts GROUP BY region ORDER BY total LIMIT 2",
                               None, snapshot=True)
        assert result["ok"], result
        assert result["sample_rows"] == [["华东", 46]]
        payload = report_data.read_snapshot(OWNER.emp_no, result["snapshot_id"])
        assert payload["origin"]["scope"] == SCOPE
        report_queries.validate_scopes([SCOPE], OWNER)
        facts[0].tables.clear()
        with pytest.raises(PermissionDenied):
            report_queries.validate_scopes([SCOPE], OWNER)
        denied = mcp.run_query(91, "SELECT SUM(amount) FROM facts", None, snapshot=True)
        assert denied["error_type"] == "permission_denied" and denied["timing"]["request_id"]
    finally:
        connector.close()


def test_scope_rechecks_row_filters_and_physical_target(facts, monkeypatch):
    source = {"id": 12, "type": "postgresql", "enabled": True, "updated_at": "v1",
              "config": {"host": "db.internal", "port": 5432, "database": "bi"}}
    scope = report_queries.scope_for(91, DATASET, source, ["facts"])
    rotated = {**source, "updated_at": "v2"}
    monkeypatch.setattr(repo, "get_datasource", lambda id: rotated)
    report_queries.validate_scopes([scope], OWNER)
    old = {k: v for k, v in scope.items() if k not in {"source_target", "source_target_version"}}
    with pytest.raises(PermissionDenied):
        report_queries.validate_scopes([old], OWNER)
    rotated["config"] = {**source["config"], "host": "other.internal"}
    with pytest.raises(PermissionDenied):
        report_queries.validate_scopes([scope], OWNER)
    rotated["config"] = source["config"]
    monkeypatch.setattr(repo, "get_dataset", lambda id: {**DATASET, "tables": [{"table_name": "facts", "row_filters": []}]})
    with pytest.raises(PermissionDenied):
        report_queries.validate_scopes([scope], OWNER)


@pytest.mark.usefixtures("chromium")
def test_style_preview_still_uses_active_theme_in_chromium(monkeypatch):
    monkeypatch.setattr(repo, "effective_style", lambda *args: {"theme": {
        "header_style": "plain", "filter_sticky": True, "toc": False, "color-brand": "#123456",
    }})
    page = style_preview.render_preview("样式测试")
    assert "rf-header-plain" in page and "rf-filter-sticky" in page and "#123456" in page
    assert '<nav class="rf-toc"' not in page
    proof = report_browser.verify_html(page, expected_sections=["metrics", "charts", "details"])
    assert proof["chart_count"] == 5 and proof["interactive_controls_tested"] == 1


def test_official_metric_batch_still_uses_one_source_revision_and_authorized_range(monkeypatch):
    url = "https://example.invalid/facts"
    source = {**SOURCE, "config": {"endpoints": {"facts": {
        "path": url, "records_path": "items", "revision_path": "version",
        "pagination": {"cursor_param": "cursor", "next_cursor_path": "next", "total_path": "total"},
        "column_types": {"actual": "DECIMAL(28, 20)", "target": "DECIMAL(28, 20)"},
    }}}}
    metric = {"table": "facts", "op": "ratio_of_sums", "field": "actual", "denominator": "target",
              "dimensions": ["region"], "scale": "decimal"}
    dataset = {**DATASET, "semantic_metrics": {"attainment": metric}}
    pages = iter(httpx.Response(200, request=httpx.Request("GET", url), json=page) for page in (
        {"items": [{"region": "华东", "actual": "0.99999999999999999999", "target": 1}], "next": "two", "total": 2, "version": 7},
        {"items": [{"region": "华北", "actual": 1000, "target": 1}], "next": None, "total": 2, "version": 7},
    ))
    connector = APIConnector(source, "")
    monkeypatch.setattr(report_queries, "get_connector", lambda _: connector)
    monkeypatch.setattr("smartreportgen.connectors.api.httpx.request", lambda *a, **kw: next(pages))
    try:
        queries = {"overall": report_queries.QueryPlan(table="facts", metrics=["attainment"]),
                   "by_region": report_queries.QueryPlan(table="facts", dimensions=["region"], metrics=["attainment"])}
        result = report_queries.capture(91, dataset, source, {"facts"}, OWNER.emp_no, queries, {}, 100)
        assert result["by_region"]["sample_rows"] == [["华东", {"$decimal": "0.99999999999999999999"}]]
        with pytest.raises(PermissionDenied):
            report_queries.capture(91, dataset, source, set(), OWNER.emp_no, queries, {}, 100)
    finally:
        connector.close()


def test_batch_is_atomic_and_parallel_updates_keep_order_and_isolation(facts):
    draft_id = mcp.create_html_draft("并行更新", None, dataset_id=91, snapshots={"facts": facts[1]})["draft_id"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda i: mcp.upsert_html_section(
            draft_id, None, f"s{i}", f"<h2>章节 {i}</h2>"), range(8)))
    assert all(r["ok"] for r in results)
    status = mcp.get_html_draft(draft_id, None)
    assert status["revision"] == 9
    assert {item["section_id"] for item in status["sections"]} == {f"s{i}" for i in range(8)}
    error = mcp.upsert_html_section(draft_id, None, sections=[
        {"section_id": "s1", "delete": True}, {"section_id": "invalid id"},
    ])
    assert error["failed_stage"] == "draft.upsert"
    assert mcp.get_html_draft(draft_id, None)["revision"] == 9
    moved = mcp.upsert_html_section(draft_id, None, sections=[{"section_id": "s5", "index": 0}])
    assert moved["sections"][0]["section_id"] == "s5"
    assert mcp.get_html_draft(draft_id, None, True, "s5")["sections"][0]["html"] == "<h2>章节 5</h2>"
    assert report_draft.list_drafts("another-worker") == []
    assert report_draft.list_drafts("admin", is_admin=True)[0]["owner"] == OWNER.emp_no


@pytest.mark.usefixtures("chromium")
def test_runtime_diagnostics_repair_and_reuse_preview(facts, monkeypatch):
    draft_id = mcp.create_html_draft("月报", None, dataset_id=91, snapshots={"facts": facts[1]})["draft_id"]
    _populate(draft_id)
    mcp.upsert_html_section(draft_id, None, "broken", "<h2>待修复</h2>", js="throw new Error('章节异常');")
    failure = mcp.save_report("月报", None, draft_id=draft_id)
    assert failure["error_type"] == "verify_failed", failure
    assert failure["failed_stage"] == "chromium.verify"
    assert failure["diagnostics"]["section_id"] == "broken"
    assert "章节异常" in failure["diagnostics"]["errors"][0]["message"]
    assert {"chromium.acquire_slot", "chromium.launch", "chromium.load", "chromium.inspect"} <= failure["timing"]["stages"].keys()
    status = mcp.get_html_draft(draft_id, None)
    assert status["status"] == "verify_failed" and status["last_error"]["diagnostics"]["section_id"] == "broken"

    mcp.upsert_html_section(draft_id, None, sections=[{
        "section_id": "broken", "js": "", "html": '<h2>外链</h2><img src="https://assets.invalid/sample.png">',
    }])
    failed = mcp.verify_html_draft(None, draft_id=draft_id)
    assert failed["diagnostics"]["section_id"] == "broken", failed
    assert failed["diagnostics"]["external_requests"][0]["url"] == "https://assets.invalid/sample.png"

    mcp.upsert_html_section(draft_id, None, "broken", "<h2>说明</h2>")
    preview = mcp.verify_html_draft(None, draft_id=draft_id)
    assert preview["ok"], preview
    assert preview["verification"]["chart_count"] == preview["verification"]["interactive_controls_tested"] == 1
    assert any("390px" in warning for warning in preview["warnings"])
    mcp.upsert_html_section(draft_id, None, "broken", "<h2>最新版说明</h2>")
    stale = mcp.save_report("月报", None, preview_id=preview["preview_id"])
    assert stale["error_type"] == "conflict"
    latest = mcp.verify_html_draft(None, draft_id=draft_id)
    assert latest["ok"], latest
    calls = []

    def save(name, html, template_id, template_version, meta, owner, *, source):
        calls.append((meta, source))
        return {"id": 7, "revision": 1, "meta": meta, "template_id": template_id, "template_version": template_version}

    monkeypatch.setattr(repo, "save_report", save)
    saved = mcp.save_report("月报", None, preview_id=latest["preview_id"])
    assert saved["ok"] and len(calls) == 1
    assert "chromium.verify" not in saved["timing"]["stages"]
    assert calls[0][0]["scopes"] == [SCOPE]
    assert json.loads(calls[0][1]["markdown"])["smartreportgen_html_draft"]["sections"][-1]["section_id"] == "broken"
    assert mcp.get_html_draft(draft_id, None)["status"] == "saved"

    # 已验收的预览在权限撤销后无法入库或读取。
    facts[0].tables.clear()
    assert mcp.save_report("月报", None, preview_id=latest["preview_id"])["error_type"] == "permission_denied"
    assert mcp.get_preview_file(latest["preview_id"], None)["error_type"] == "permission_denied"
    assert len(calls) == 1


@pytest.mark.usefixtures("chromium")
@pytest.mark.parametrize(("html", "js", "key"), [
    ('<h1>筛选</h1><select id="selector"><option>1</option><option>2</option></select>',
     "document.querySelector('select').onchange = () => { throw Error('筛选异常'); };", "control"),
    ('<h1>图表</h1><div data-chart id="blank-chart"></div>', "", "invalid_charts"),
    ('<h1>外链</h1><div id="asset"></div>',
     "const img = document.createElement('img'); img.src='https://assets.invalid/dynamic.png';"
     "document.getElementById('asset').append(img);", "external_requests"),
    ('<h1>语法</h1>', 'const no = ;', "errors"),
])
def test_runtime_errors_find_section_and_trigger(facts, html, js, key):
    draft = mcp.create_html_draft("诊断", None, dataset_id=91, use_echarts=False)
    mcp.upsert_html_section(draft["draft_id"], None, "target", html, js=js)
    result = mcp.verify_html_draft(None, draft_id=draft["draft_id"])
    assert not result["ok"] and result["failed_stage"] == "chromium.verify", result
    assert result["diagnostics"]["section_id"] == "target", json.dumps(result, ensure_ascii=False)
    assert result["diagnostics"][key], result


def test_unauthorized_or_modified_preview_cannot_publish(facts):
    page = "<html>content</html>"
    proof = {"source": "assembled_html", "scopes": [SCOPE],
             "verification": {"status": "passed", "html_sha256": sha256(page.encode()).hexdigest(),
                              "source_sha256": sha256(page.encode()).hexdigest()}}
    ref = str(report_data.save_preview(OWNER.emp_no, page, source_html=page, proof=proof))
    with pytest.raises(report_data.ReportDataError):
        report_pipeline.load_preview(Principal("outsider"), ref, certified=True)
    legacy_ref = str(report_data.save_preview(OWNER.emp_no, page, source_html=page, proof={**proof, "source": "compiled_spec"}))
    with pytest.raises(repo.Invalid, match="当前 Chromium"):
        report_pipeline.load_preview(OWNER, legacy_ref, certified=True)
    report_data._write_ref(OWNER.emp_no, UUID(ref), "html", "updated")
    with pytest.raises(repo.Invalid, match="预览证据"):
        report_pipeline.load_preview(OWNER, ref, certified=True)


@pytest.mark.usefixtures("chromium")
def test_same_runtime_resource_failure_is_attributed_to_both_sections(facts):
    # 两个章节各自从脚本生成同一外部地址，诊断需保留两个独立的修复入口。
    def section(section_id):
        return {"section_id": section_id, "html": "<p>测试资源依赖</p>",
                "js": f"""const img = document.createElement('img');
                img.src = 'https://assets.invalid/shared.png';
                SRG.section('{section_id}').append(img);"""}

    draft = mcp.create_html_draft("重复外部依赖", None, dataset_id=91, use_echarts=False,
                                  sections=[section("first"), section("second")])
    result = mcp.verify_html_draft(None, draft_id=draft["draft_id"])
    assert result["error_type"] == "verify_failed", result
    assert set(result["diagnostics"]["section_ids"]) == {"first", "second"}, result


@pytest.fixture
def postgres(monkeypatch):
    url = os.environ.get("SRG_TEST_DATABASE_URL")
    if not url:
        pytest.skip("设置 SRG_TEST_DATABASE_URL 可运行独立 PostgreSQL 测试")
    monkeypatch.setattr(settings, "database_url", url)
    db.init_db()
    suffix = uuid4().hex[:8]
    author, editor, viewer, outsider = (Principal(f"{role}-{suffix}") for role in ("author", "editor", "viewer", "outsider"))
    with db.conn() as conn, conn.transaction():
        source = conn.execute("INSERT INTO datasource.datasource(name,type) VALUES (%s,'api') RETURNING id",
                              (suffix,)).fetchone()["id"]
        dataset = conn.execute("INSERT INTO dataset.dataset(business_scenario,datasource_id) VALUES (%s,%s) RETURNING id",
                               (suffix, source)).fetchone()["id"]
        conn.execute("INSERT INTO dataset.dataset_table(dataset_id,table_name) VALUES (%s,'facts')", (dataset,))
        for who in (author, editor, viewer, outsider):
            conn.execute("INSERT INTO permission.employee(emp_no) VALUES (%s)", (who.emp_no,))
        for who in (author, editor, viewer):
            conn.execute("INSERT INTO permission.table_grant(subject_type,subject,dataset_id,table_name) "
                         "VALUES ('employee',%s,%s,'facts')", (who.emp_no, dataset))
    identity = [author]
    monkeypatch.setattr(mcp, "_access", lambda ctx: access_for(identity[0]))
    monkeypatch.setattr("smartreportgen.api.auth.verify_bearer", lambda authorization: identity[0])
    yield identity, author, editor, viewer, outsider, dataset
    db.pool().close()
    db._pool = None


def test_legacy_free_template_preserves_layout_and_bindings_as_literal_code():
    # 源码含围栏和看似章节的行时，仍完整显示在代码块中。
    layout = '<main class="layout">\n<srg-module m="bridge"></srg-module>\n</main>'
    module = '<article>\n~~~~\n## 源码里的井号\n<srg-output m="bridge_chart"></srg-output>\n</article>'
    css = ".layout { display:grid; }"
    raw = json.dumps({"dataset_id": 12, "smartreportgen_free_html": {
        "title": "历史自由版式", "layout": layout, "css": css,
        "modules": {"bridge": {
            "title": "变动桥接", "html": module, "css": ".bridge { padding: 16px; }",
            "outputs": {"bridge_chart": {
                "title": "收入变动瀑布图", "type": "waterfall",
                "columns": [{"field": "amount", "label": "金额", "format": {"unit": "万元"}}],
                "waterfall": {"role_field": "stage", "opening": "期初", "closing": "期末"},
            }},
        }},
        "theme": {"filter_sticky": False},
    }}, ensure_ascii=False)
    guide = repo.normalize_template_markdown(raw)
    parser = MarkdownIt("commonmark")
    fences = [token.content for token in parser.parse(guide) if token.type == "fence"]
    assert layout + "\n" in fences and module + "\n" in fences and css + "\n" in fences
    assert ".bridge { padding: 16px; }\n" in fences
    assert any(json.loads(content) == {"role_field": "stage", "opening": "期初", "closing": "期末"}
               for content in fences if content.startswith("{"))
    assert all(text in guide for text in ("收入变动瀑布图", "bridge_chart", "万元", '"filter_sticky": false'))
    assert repo._template_summary(guide, "")["sections"] == ["页面布局", "变动桥接", "展示规范", "历史定义信息"]
    rendered = parser.render(guide)
    assert "&lt;srg-output" in rendered and "<srg-output" not in rendered


@pytest.mark.usefixtures("chromium")
def test_postgres_templates_versions_download_merge_and_iam_rechecks(postgres):
    login, owner, editor, viewer, outsider, dataset = postgres
    markdown = "# 经营模板指南\n\n## 核心结论\n\n## 明细"
    created = mcp.save_report_template("经营模板", markdown, None, dataset, business_summary="份额对比")
    assert created["ok"], created
    tpl_id = created["template_id"]
    assert created["sections"] == ["核心结论", "明细"]
    changed = mcp.save_report_template("经营模板", markdown + "\n\n## 趋势", None, template_id=tpl_id, expected_version=1)
    assert changed["version"] == 2
    assert mcp.save_report_template("经营模板", markdown, None, template_id=tpl_id, expected_version=1)["error_type"] == "conflict"
    login[0] = editor
    assert mcp.get_report_template(tpl_id, None, version=1)["markdown"] == markdown
    assert mcp.list_report_templates(None, "经营", dataset)["templates"][0]["template_id"] == tpl_id
    assert mcp.save_report_template("越权", markdown, None, template_id=tpl_id)["error_type"] == "permission_denied"
    login[0] = outsider
    assert mcp.list_report_templates(None, dataset_id=dataset)["templates"] == []
    assert mcp.get_report_template(tpl_id, None)["error_type"] == "permission_denied"
    login[0] = owner

    legacy_schema = json.dumps({"dataset_id": dataset, "smartreportgen_spec": {
        "title": "历史模板",
        "queries": {"sales": {"table": "facts", "fields": ["amount"], "parameters": {"month": "period"}}},
        "filters": [{"id": "region", "label": "区域", "query": "sales", "field": "region", "default": None}],
        "metrics": {"sales_total": {
            "sources": [{"query": "sales", "field": "amount", "by": {"region": "region"}}],
            "format": {"scale": "number", "unit": "元"},
        }},
        "sections": [{"title": "历史概览", "narrative": "本期销售额为 {{sales_total}}。", "cards": [
            {"title": "销售额卡片", "type": "kpi", "metric": "sales_total", "height": 240},
        ]}],
    }}, ensure_ascii=False)
    row = db.fetchone("INSERT INTO report_template.report_template(name,markdown,created_by,template_type,dataset_id) "
                      "VALUES (%s,%s,%s,'structured',%s) RETURNING id", ("历史", legacy_schema, owner.emp_no, dataset))
    db.execute("INSERT INTO report_template.report_template_version(template_id,version,markdown) VALUES (%s,1,%s)",
               (row["id"], legacy_schema))
    legacy_template = mcp.get_report_template(row["id"], None, version=1)
    assert legacy_template["markdown"].startswith("# 历史模板")
    guide = legacy_template["markdown"]
    assert {"取数逻辑", "筛选与联动", "指标口径", "历史概览"} <= set(legacy_template["summary"]["sections"])
    assert all(text in guide for text in ('"month": "period"', "默认值** ` null `", '"region": "region"',
                                          "{{sales_total}}", "销售额卡片", "` 240 `"))
    assert db.fetchone(
        "SELECT markdown FROM report_template.report_template_version WHERE template_id=%s AND version=1",
        (row["id"],),
    )["markdown"] == legacy_schema
    # HTTP 查看页与 MCP 读取统一从授权模板版本得到同一份完整指南。
    client = TestClient(app)
    headers = {"Authorization": "Bearer test"}
    assert client.get(f'/api/templates/{row["id"]}', headers=headers).json()["markdown"] == guide
    assert client.get(f'/api/templates/{row["id"]}/versions/1', headers=headers).json()["markdown"] == guide
    legacy_free = repo.normalize_template_markdown(json.dumps({"smartreportgen_free_html": {
        "title": "历史自由版式", "modules": {"overview": {"title": "概览"}, "detail": {"title": "明细"}},
    }}))
    assert "## 概览" in legacy_free and "## 明细" in legacy_free

    # 完整 HTML 也可用业务场景权限通过一步验收，允许简单的静态报告。
    raw = mcp.save_report("静态说明", None, html="<!doctype html><html><head></head><body><h1>经营说明</h1></body></html>",
                          dataset_id=dataset)
    assert raw["ok"] and raw["verification"]["engine"] == "chromium", raw

    ds = repo.get_dataset(dataset)
    scope = report_queries.scope_for(dataset, ds, repo.get_datasource(ds["datasource_id"]), ["facts"])
    ref = report_data.save_query(owner.emp_no, ["month", "amount"], [["1月", {"$decimal": "123.45"}],
                                 ["2月", {"$decimal": "250.25"}]], 0, origin={"scope": scope})["snapshot_id"]
    theme_id = report_data.save_style(owner.emp_no, ":root{--color-brand:#123456}")
    draft = mcp.create_html_draft("经营报告", None, dataset, {"facts": ref}, style_id=theme_id,
                                  template_id=tpl_id, template_version=1)
    assert draft["ok"], draft
    _populate(draft["draft_id"])
    saved = mcp.save_report("经营报告", None, draft_id=draft["draft_id"])
    assert saved["ok"], saved
    assert saved["version"] == 1 and "chromium.verify" in saved["timing"]["stages"]
    assert any("390px" in warning for warning in saved["warnings"])
    rid = saved["report_id"]
    r1 = repo.get_report(rid, owner, version=1, with_source=True)
    assert r1["meta"]["source"] == "assembled_html"
    assert "data-srg-style" in r1["html"]
    assert db.fetchone("SELECT length(html) AS size FROM report.report_version WHERE report_id=%s", (rid,))["size"] < len(r1["html"]) / 2

    repo.set_report_share(rid, owner, editor.emp_no, "edit")
    repo.set_report_share(rid, owner, viewer.emp_no, "view")
    assert not repo.get_report(rid, viewer, with_source=True)["source_available"]
    login[0] = editor
    edit = mcp.create_html_draft("修订", None, report_id=rid, base_version=1)
    assert edit["snapshots"] == {"facts": "base"}
    assert [x["section_id"] for x in edit["sections"]] == ["overview", "trend", "details"]
    competing_edit = mcp.create_html_draft("修订", None, report_id=rid, base_version=1)
    edits = [edit, competing_edit]
    for draft in edits:
        mcp.upsert_html_section(draft["draft_id"], None, "overview", '<h1>修订概览</h1><output id="amount">—</output>')
    # 独立草稿可同时验收，入库时由报告版本锁阻止同一 expected_version 被提交两次。
    with ThreadPoolExecutor(max_workers=2) as pool:
        previews = list(pool.map(lambda draft: mcp.verify_html_draft(None, draft_id=draft["draft_id"]), edits))
        assert all(preview["ok"] for preview in previews), previews
        revisions = list(pool.map(lambda draft: mcp.revise_report(
            rid, "修订", None, base_version=1, draft_id=draft["draft_id"], expected_version=1,
        ), edits))
    assert sum(item["ok"] for item in revisions) == 1, revisions
    winner = next(i for i, item in enumerate(revisions) if item["ok"])
    edit, preview, revised = edits[winner], previews[winner], revisions[winner]
    assert revisions[1 - winner]["error_type"] == "conflict", revisions
    assert revised["ok"] and revised["version"] == 2, revised
    assert "chromium.verify" not in revised["timing"]["stages"]

    # 模拟旧模板已编译的成品与其聚合数据块，修订时继续提供单独可用的旧快照查询。
    login[0] = owner
    packed = json.dumps({"data": {"legacy_fact": {"columns": ["amount"], "rows": [[12]]}}})
    legacy_page = ("<!doctype html><html><head>"
                   """<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'">"""
                   '<style>body{margin:0}</style></head><body><main><h1>历史成品</h1></main>'
                   f'<script id="srg-data" type="application/json" data-srg-snapshot="embedded">{packed}</script>'
                   '<script>window.legacyLoaded = true;</script><script data-srg-vendor="echarts"></script></body></html>')
    legacy_page = report_data.embed(legacy_page, owner.emp_no)
    historic = repo.save_report("旧版", legacy_page, None, None,
                                {"source": "compiled_spec", "scopes": [scope], "dataset_id": dataset,
                                 "verification": {"status": "passed", "html_sha256": sha256(legacy_page.encode()).hexdigest()}},
                                owner.emp_no, source={"markdown": legacy_schema, "dataset_id": dataset})
    legacy_id = historic["id"]
    legacy = mcp.create_html_draft("历史报告修订", None, report_id=legacy_id, base_version=1)
    assert legacy["snapshots"] == {"srg-data": "base", "legacy_fact": "base"}
    mcp.upsert_html_section(legacy["draft_id"], None, "extension", "<h2>新增说明</h2>",
                            js="if (SRG.rows('legacy_fact')[0].amount !== 12 || !legacyLoaded) throw Error('基准失败')")
    result = mcp.revise_report(legacy_id, "历史报告修订", None, 1, draft_id=legacy["draft_id"])
    assert result["ok"] and result["version"] == 2, json.dumps(result, ensure_ascii=False)
    assert repo.get_report(legacy_id, owner, version=1)["html"] == legacy_page

    for report_id, version in ((rid, 2), (legacy_id, 1), (legacy_id, 2)):
        download = client.get(f"/reports/{report_id}/download?version={version}", headers=headers)
        assert download.status_code == 200
        assert download.text == repo.get_report(report_id, owner, version=version)["html"]
        assert "attachment" in download.headers["content-disposition"]
    merged = client.post("/api/reports/merge", headers=headers, json={
        "reports": [{"id": rid, "version": 2}, {"id": legacy_id, "version": 2}],
    })
    assert merged.status_code == 200
    assert 'id="shared-echarts"' in merged.text and len(merged.text) < len(r1["html"]) * 1.4
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get("SRG_CHROMIUM_PATH", "/usr/bin/chromium"))
        page = browser.new_page(offline=True)
        page.set_content(merged.text)
        first = page.frame_locator('iframe[title="修订"]')
        assert first.locator("#amount").text_content() == "123.45"
        first.locator("#period").select_option(index=1)
        assert first.locator("#amount").text_content() == "250.25"
        assert first.locator("canvas").count() > 0
        assert page.frame_locator('iframe[title="历史报告修订"]').get_by_text("新增说明").count() == 1
        browser.close()

    login[0] = editor
    assert client.get(f"/reports/{rid}/download?version=2", headers=headers).status_code == 200
    assert client.get("/api/report-drafts", headers=headers).json()[0]["draft_id"] == edit["draft_id"]
    db.execute("DELETE FROM permission.table_grant WHERE subject=%s AND dataset_id=%s", (editor.emp_no, dataset))
    assert client.get(f"/reports/{rid}/download?version=2", headers=headers).status_code == 403
    assert mcp.get_preview_file(preview["preview_id"], None)["error_type"] == "permission_denied"
    login[0] = owner
    with pytest.raises(PermissionDenied):
        repo.set_report_share(rid, owner, outsider.emp_no, "view")
