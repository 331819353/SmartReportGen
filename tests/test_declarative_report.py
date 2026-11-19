"""验证声明式取数、验收凭证和旧客户端无法绕过门禁。"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

import httpx

from smartreportgen import mcp_server, repo, report_browser, report_data, report_pipeline, report_queries, report_spec
from smartreportgen.auth import Principal
from smartreportgen.config import settings
from smartreportgen.connectors.api import APIConnector
from smartreportgen.connectors.base import ConnectorError, norm_value
from smartreportgen.permission import Access, PermissionDenied


DATASET = {"id": 91, "datasource_id": 12, "business_scenario": "夹具", "enabled": True,
           "tables": [{"table_name": "facts", "row_filters": []}]}
SOURCE = {"id": 12, "type": "api", "enabled": True, "name": "测试接口", "updated_at": "fixture"}
PRINCIPAL = Principal("fixture-owner")
SCOPE = report_queries.scope_for(91, DATASET, SOURCE, ["facts"])


def fixture():
    fmt = {"kind": "rate", "scale": "decimal", "signal": "attainment",
           "threshold": "1", "warning_threshold": "0.95"}
    spec = report_spec.ReportSpec.model_validate({
        "title": "交付测试", "filters": [{"id": "region", "label": "区域", "query": "facts", "field": "region"}],
        "metrics": {"attainment": {"sources": [
            {"query": "facts", "field": "attainment", "where": {"region": None}},
            {"query": "facts", "field": "attainment", "by": {"region": "region"}},
        ], "format": fmt}},
        "sections": [{"title": "执行情况", "narrative": "完成率为 {{attainment}}。", "cards": [
            {"title": "完成率", "type": "kpi", "metric": "attainment"},
            {"title": "区域明细", "type": "table", "query": "facts", "by": {"region": "region"},
             "columns": [{"label": "区域", "field": "region", "format": {"kind": "text"}},
                         {"label": "完成率", "field": "attainment", "format": fmt}]},
            {"title": "区域对比", "type": "bar", "query": "facts", "by": {"region": "region"},
             "columns": [{"label": "区域", "field": "region", "format": {"kind": "text"}},
                         {"label": "完成率", "field": "attainment", "format": fmt}]},
        ]}],
    })
    payload = {"columns": ["region", "attainment", "internal"],
               "rows": [[None, norm_value(Decimal("0.99999999999999999999")), "private"],
                        ["华东", 1, "private"], ["华北", 0, "private"], ["华南", None, "private"],
                        ["华中", 1, "private"], ["华中", 2, "private"]],
               "origin": {"scope": SCOPE, "scales": {"attainment": "decimal"}}}
    return spec, payload


def presentation_fixture():
    spec, payload = fixture()
    raw = spec.model_dump(mode="json")
    raw["theme"] = {"color-brand": "#395C51", "color-bg-page": "#F5F5F0", "header_style": "plain",
                    "toc": False, "filter_sticky": True, "table_zebra": False, "rre-card-gap": 16,
                    "rre-page-font-title": {"size": 30, "weight": 700}}
    payload["rows"] = payload["rows"][:4]
    payload["columns"].append("actual")
    for row, amount in zip(payload["rows"], [345, 150, 75, 120]):
        row.append(amount)
    x, attainment = raw["sections"][0]["cards"][2]["columns"]
    actual = {"label": "销售量", "field": "actual"}
    raw["sections"][0]["cards"] = [
        {"title": "完成率", "type": "kpi", "metric": "attainment", "span": 12},
        {"title": "柱线组合", "type": "combo", "query": "facts", "span": 12, "height": 330,
         "chart": {"legend": "right", "palette": ["#395C51", "#A35B31"], "grid": "dashed"},
         "columns": [x, {**actual, "series": "bar"}, {**attainment, "series": "line", "axis": "secondary"}]},
        {"title": "横向柱状图", "type": "bar", "query": "facts", "chart": {"orientation": "horizontal", "labels": True},
         "columns": [x, actual]},
        {"title": "销售量环图", "type": "donut", "query": "facts", "chart": {"inner_radius": 52, "outer_radius": 68, "legend": "bottom"},
         "columns": [x, actual]},
        {"title": "销售量面积图", "type": "area", "query": "facts", "chart": {"smooth": True, "show_points": False},
         "columns": [x, actual]},
    ]
    return report_spec.ReportSpec.model_validate(raw), payload


def waterfall_fixture():
    spec = report_spec.ReportSpec.model_validate({
        "title": "收入变动分析", "colors": {"up": "#047857", "down": "#B91C1C"},
        "theme": {"header_style": "plain", "toc": False},
        "sections": [{"title": "期初至期末", "cards": [{
            "title": "收入变动瀑布图", "type": "waterfall", "query": "facts", "span": 12, "height": 360,
            "waterfall": {"role_field": "role", "opening": "opening", "change": "delta", "closing": "closing"},
            "columns": [{"label": "变动项", "field": "item", "format": {"kind": "text"}},
                        {"label": "金额", "field": "value", "format": {"digits": 0, "unit": "万"}}]
        }]}]
    })
    payload = {"columns": ["item", "value", "role"],
               "rows": [["折扣影响", -11, "delta"], ["期末", 105, "closing"], ["销量增加", 12, "delta"],
                        ["期初", 100, "opening"], ["退货增加", -4, "delta"], ["新渠道贡献", 8, "delta"]],
               "origin": {"scope": SCOPE, "scales": {"value": "number"}}}
    return spec, payload


class DeclarativeReportTest(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(settings, "report_work_dir", self.directory))

    def test_compiler_projects_fields_and_rejects_bare_numbers_and_inconsistent_batches(self):
        spec, payload = fixture()
        _, expanded, scopes = report_spec.compile_report(spec, {"facts": payload}, 91, {})
        self.assertNotIn('"private"', expanded)
        self.assertEqual(scopes, [SCOPE])
        raw = spec.model_dump(mode="json")
        raw["sections"][0]["narrative"] = "手写完成率为 100%。"
        with self.assertRaisesRegex(ValueError, "定量正文"):
            report_spec.ReportSpec.model_validate(raw)
        payload["columns"][1] = "wrong_field"
        with self.assertRaisesRegex(ValueError, "字段缺失"):
            report_spec.compile_report(spec, {"facts": payload}, 91, {})

    @unittest.skipUnless(Path(os.getenv("SRG_CHROMIUM_PATH", "/usr/bin/chromium")).is_file(), "需安装 Chromium")
    def test_browser_verifies_rendered_values_states_filters_and_no_network(self):
        spec, payload = fixture()
        _, expanded, _ = report_spec.compile_report(spec, {"facts": payload}, 91, {})
        proof = report_browser.verify(expanded, spec, {"facts": payload}, 91)
        self.assertEqual(proof["status"], "passed")
        self.assertEqual(proof["external_requests"], 0)
        self.assertGreaterEqual(proof["states_checked"], 4)

    @unittest.skipUnless(Path(os.getenv("SRG_CHROMIUM_PATH", "/usr/bin/chromium")).is_file(), "需安装 Chromium")
    def test_custom_theme_and_chart_geometry_in_chromium(self):
        from playwright.sync_api import sync_playwright

        spec, payload = presentation_fixture()
        _, expanded, _ = report_spec.compile_report(spec, {"facts": payload}, 91, {})
        proof = report_browser.verify(expanded, spec, {"facts": payload}, 91)
        self.assertEqual(proof["status"], "passed")
        with sync_playwright() as api:
            browser = api.chromium.launch(executable_path=os.getenv("SRG_CHROMIUM_PATH", "/usr/bin/chromium"))
            page = browser.new_page(viewport={"width": 1280, "height": 850})
            page.set_content(expanded)
            page.locator("html[data-srg-ready=true]").wait_for(state="attached")
            self.assertEqual(page.locator(".rf-toc").count(), 0)
            self.assertEqual(page.locator(".rf-main > .rf-filter-sticky").count(), 1)
            self.assertEqual(page.locator(".rf-title").evaluate("n=>getComputedStyle(n).fontSize"), "30px")
            self.assertEqual(page.locator("#card-1-1 .srg-chart").evaluate("n=>n.clientHeight"), 330)
            options = page.locator(".srg-chart").evaluate_all("""nodes => nodes.map(n => {
                const c = echarts.getInstanceByDom(n).getOption();
                return {types: c.series.map(s => s.type), axes: c.yAxis?.length, grid: c.yAxis?.[0].splitLine.lineStyle.type,
                        radius: c.series[0].radius, xType: c.xAxis?.[0].type, labels: c.series[0].label.show,
                        fill: c.series[0].areaStyle?.opacity, palette: c.color};
            })""")
            self.assertEqual(options[0]["types"], ["bar", "line"])
            self.assertEqual(options[0]["axes"], 2)
            self.assertEqual(options[0]["grid"], "dashed")
            self.assertEqual(options[0]["palette"], ["#395C51", "#A35B31"])
            self.assertEqual(options[1]["xType"], "value")
            self.assertTrue(options[1]["labels"])
            self.assertEqual(options[2]["radius"], ["52%", "68%"])
            self.assertEqual(options[3]["fill"], 0.18)
            page.set_viewport_size({"width": 390, "height": 850})
            page.evaluate("() => dispatchEvent(new Event('resize'))")
            self.assertEqual(page.locator(".srg-chart").first.evaluate(
                "n => echarts.getInstanceByDom(n).getOption().legend[0].bottom"), 0)

    @unittest.skipUnless(Path(os.getenv("SRG_CHROMIUM_PATH", "/usr/bin/chromium")).is_file(), "需安装 Chromium")
    def test_waterfall_reorders_deltas_and_checks_authoritative_endpoints(self):
        spec, payload = waterfall_fixture()
        _, page, _ = report_spec.compile_report(spec, {"facts": payload}, 91, {})
        evidence = report_browser.verify(page, spec, {"facts": payload}, 91)
        self.assertEqual(evidence["status"], "passed")
        card = spec.sections[0].cards[0]
        rows = [dict(zip(payload["columns"], row)) for row in payload["rows"]]
        expected = report_browser._waterfall_expected(card, rows, "semantic", spec.colors)
        self.assertEqual([row[:5] for row in expected], [
            ["期初", "total", "100万", "#6b7280", "top"], ["销量增加", "up", "+12万", "#047857", "bottom"],
            ["新渠道贡献", "up", "+8万", "#047857", "bottom"], ["折扣影响", "down", "-11万", "#b91c1c", "top"],
            ["退货增加", "down", "-4万", "#b91c1c", "top"], ["期末", "total", "105万", "#6b7280", "top"],
        ])
        payload["rows"][1][1] = 106
        _, invalid, _ = report_spec.compile_report(spec, {"facts": payload}, 91, {})
        self.assertIsNone(report_browser._waterfall_expected(card, [dict(zip(payload["columns"], row))
                          for row in payload["rows"]], "semantic", spec.colors))
        self.assertEqual(report_browser.verify(invalid, spec, {"facts": payload}, 91)["status"], "passed")

    def test_preexisting_template_and_preview_compare_with_new_presentation_defaults(self):
        spec, payload = fixture()
        raw = spec.model_dump(mode="json")
        raw.pop("theme")
        for card in raw["sections"][0]["cards"]:
            for key in ("span", "height", "chart", "waterfall"):
                card.pop(key)
        old = json.dumps({"smartreportgen_spec": raw, "dataset_id": 91}, ensure_ascii=False)
        proof = {"template_id": 15, "template_version": 1, "dataset_id": 91,
                 "spec_json": old, "source": "compiled_spec", "scopes": [SCOPE],
                 "verification": {}, "snapshot_origins": {"facts": payload["origin"]}}
        with patch.object(report_pipeline, "load_preview", return_value=({"report_id": None, "proof": proof}, "page")), \
                patch.object(repo, "get_template_version", return_value={"markdown": report_spec.encode_template(spec, 91)}), \
                patch.object(repo, "save_report") as save:
            report_pipeline.persist("历史预览", PRINCIPAL, "ref")
        save.assert_called_once()

    def test_theme_only_patch_reuses_verified_base_data(self):
        spec, payload = fixture()
        _, original_html, _ = report_spec.compile_report(spec, {"facts": payload}, 91, {})
        base = {"id": 500, "revision": 1, "access_level": "edit", "html": original_html,
                "markdown": report_spec.encode_template(spec, 91), "template_id": 99, "template_version": 1,
                "meta": {"snapshot_origins": {"facts": payload["origin"]},
                         "verification": {"html_sha256": sha256(original_html.encode()).hexdigest()}}}
        access = Access(PRINCIPAL, tables={91: {"facts"}})
        with patch.object(mcp_server, "_access", return_value=access), \
                patch.object(repo, "get_report", return_value=base), \
                patch.object(repo, "get_dataset", return_value=DATASET), \
                patch.object(repo, "style_rules_for", return_value={"specified": {"theme": {}}}), \
                patch.object(report_spec, "validate_scopes") as scopes, \
                patch.object(report_browser, "verify", return_value={"status": "passed"}), \
                patch.object(report_pipeline, "capture") as fetch:
            result = mcp_server.preview_report_spec(None, report_id=500, base_version=1,
                                                    theme_overrides={"color-brand": "#395C51", "toc": False})
            self.assertTrue(result["ok"], result)
            _, changed = report_pipeline.load_preview(PRINCIPAL, result["preview_id"], certified=True)
        fetch.assert_not_called()
        self.assertGreaterEqual(scopes.call_count, 2)
        def data(html):
            return json.loads(next(b.data for b in report_data._DataBlocks(html).blocks if b.fields.get("id") == "srg-data"))["data"]

        self.assertEqual(data(original_html), data(changed))
        self.assertIn("--color-brand:#395C51", changed)
        self.assertNotIn('<nav class="rf-toc"', changed)
        with self.assertRaisesRegex(ValueError, "内置样式令牌"):
            report_spec.resolve_report_theme({"custom_css": ".rf-card{display:none}"})

    def test_current_scope_rechecked_for_recipient_and_revocation(self):
        access = Access(PRINCIPAL, tables={91: {"facts"}})
        with patch.object(report_spec, "access_for", return_value=access), \
                patch.object(repo, "get_dataset", return_value=DATASET), \
                patch.object(repo, "get_datasource", return_value=SOURCE):
            report_spec.validate_scopes([SCOPE], PRINCIPAL)
            access.tables.clear()
            with self.assertRaises(PermissionDenied):
                report_spec.validate_scopes([SCOPE], PRINCIPAL)

    def test_drafts_and_tampered_previews_cannot_publish(self):
        ref = report_data.save_preview(PRINCIPAL.emp_no, "page", source_html="source")
        with patch.object(mcp_server, "_access", return_value=Access(PRINCIPAL)):
            self.assertFalse(mcp_server.save_report("草稿", ctx=None, preview_id=str(ref))["ok"])
        ref = report_data.save_preview(PRINCIPAL.emp_no, "tampered", source_html="source", proof={
            "verification": {"status": "passed", "static_audit": "passed", "html_sha256": "0"},
        })
        with self.assertRaisesRegex(repo.Invalid, "证据"):
            report_pipeline.load_preview(PRINCIPAL, str(ref), certified=True)

    def test_official_api_metrics_use_all_pages_and_scope_before_aggregation(self):
        url = "https://example.invalid/facts"
        cfg = {"endpoints": {"facts": {"path": url, "records_path": "items", "revision_path": "version",
               "pagination": {"cursor_param": "cursor", "next_cursor_path": "next", "total_path": "total"},
               "column_types": {"actual": "DECIMAL(28, 20)", "target": "DECIMAL(28, 20)"}}}}
        source = {**SOURCE, "config": cfg}
        metric = {"table": "facts", "op": "ratio_of_sums", "field": "actual", "denominator": "target",
                  "dimensions": ["region"], "scale": "decimal"}
        dataset = {**DATASET, "semantic_metrics": {"attainment": metric},
                   "tables": [{"table_name": "facts", "row_filters": [{"column": "region", "values": ["华东"]}]}]}
        pages = [httpx.Response(200, request=httpx.Request("GET", url), json=page) for page in (
            {"items": [{"region": "华东", "actual": "0.99999999999999999999", "target": 1}], "next": "next", "total": 2, "version": 7},
            {"items": [{"region": "华北", "actual": 1000, "target": 1}], "next": None, "total": 2, "version": 7},
        )]
        connector = APIConnector(source, "")
        self.addCleanup(connector.close)
        with patch.object(report_queries, "get_connector", return_value=connector), \
                patch("smartreportgen.connectors.api.httpx.request", side_effect=pages) as fetch:
            queries = {"overall": report_queries.QueryPlan(table="facts", metrics=["attainment"]),
                       "regions": report_queries.QueryPlan(table="facts", dimensions=["region"], metrics=["attainment"])}
            result = report_queries.capture(91, dataset, source, {"facts"}, PRINCIPAL.emp_no, queries, {}, 100)
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(result["overall"]["row_count"], 1)
            observed = result["regions"]["sample_rows"][0]
            self.assertEqual(observed, ["华东", {"$decimal": "0.99999999999999999999"}])
            with self.assertRaises(PermissionDenied):
                report_queries.capture(91, dataset, source, set(), PRINCIPAL.emp_no, queries, {}, 100)
            self.assertEqual(fetch.call_count, 2)
        with self.assertRaisesRegex(ConnectorError, "直接事实"):
            report_queries.direct_projection("select sum(actual) from facts", "api")
