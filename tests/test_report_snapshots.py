"""用两张内存表验证多表取数、完整 JSON 快照和版本数据复用。"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from fastapi.testclient import TestClient

from smartreportgen import mcp_server, report_data
from smartreportgen.api import app
from smartreportgen.auth import Principal
from smartreportgen.config import settings
from smartreportgen.connectors.api import APIConnector
from smartreportgen.permission import Access


def block(name, ref):
    return f'<script id="{name}" type="application/json" data-srg-snapshot="{ref}"></script>'


def document(*blocks):
    return ('<!doctype html><html><head><meta charset="utf-8"><style>body{margin:0}</style></head><body>'
            + '\n'.join(blocks) + '<script>document.body.dataset.ready = "1";</script></body></html>')


class ReportSnapshotsTest(unittest.TestCase):
    def setUp(self):
        self.work = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(settings, "report_work_dir", self.work))
        self.owner = Principal("snapshot-owner")
        self.access = Access(self.owner, tables={1: {"orders", "targets"}})
        self.source = {"type": "api", "name": "测试数据源"}
        self.dataset = {"tables": [
            {"table_name": "Orders", "table_description": "实际", "row_filters": [
                {"column": "region", "values": ["华东"]},
            ]},
            {"table_name": "Targets", "table_description": "目标", "row_filters": []},
        ]}
        self.connector = APIConnector(self.source, "")
        records = {
            "Orders": [{"id": i, "region": "华东", "value": i} for i in range(1, 12)] + [
                {"id": 99, "region": "华南", "value": 999},
            ],
            "Targets": [{"id": 1, "expected": 5}, {"id": 2, "expected": 4}],
        }
        self.connector.fetch = records.__getitem__
        self.enterContext(patch.object(mcp_server, "_access", return_value=self.access))
        self.enterContext(patch.object(mcp_server.repo, "get_dataset", return_value=self.dataset))
        self.enterContext(patch.object(mcp_server.services, "datasource_for_dataset", return_value=self.source))
        self.enterContext(patch.object(mcp_server.services, "get_connector", return_value=self.connector))

    def test_multi_table_inventory_and_join_enforce_same_scope(self):
        first = mcp_server.describe_tables(1, None, limit=1)
        second = mcp_server.describe_tables(1, None, offset=first["next_offset"])
        self.assertEqual([first["tables"][0]["table_name"], second["tables"][0]["table_name"]], ["Orders", "Targets"])
        self.assertIsNone(second["next_offset"])
        self.assertNotIn("rows", second["tables"][0])
        sql = "SELECT SUM(Orders.value), SUM(Targets.expected) FROM Orders JOIN Targets USING (id)"
        joined = mcp_server.run_query(1, sql, None)
        self.assertCountEqual(joined["tables_used"], ["Orders", "Targets"])
        self.assertEqual(joined["rows"], [[3, 9]])
        self.access.tables[1].remove("targets")
        self.assertEqual(mcp_server.describe_tables(1, None)["total"], 1)
        self.assertEqual(mcp_server.describe_tables(1, None, ["Targets"])["error_type"], "permission_denied")
        denied = mcp_server.run_query(1, sql, None, snapshot=True)
        self.assertEqual(denied["error_type"], "permission_denied")
        self.assertEqual(list(Path(self.work).iterdir()), [])

    def test_multiple_snapshots_render_and_preview_is_private(self):
        orders = mcp_server.run_query(1, "SELECT * FROM Orders ORDER BY id", None, snapshot=True, sample_rows=2)
        targets = mcp_server.run_query(1, "SELECT * FROM Targets", None, snapshot=True, sample_rows=0)
        self.assertEqual(orders["row_count"], 11)
        self.assertEqual(len(orders["sample_rows"]), 2)
        self.assertEqual(targets["sample_rows"], [])
        self.assertNotIn("rows", orders)
        draft = document(block("orders", orders["snapshot_id"]), block("targets", targets["snapshot_id"]))
        expected = report_data.embed(draft, self.owner.emp_no)
        preview = mcp_server.preview_report_html(draft, None)
        self.assertTrue(preview["ok"], preview)
        self.assertNotIn("html", preview)
        self.assertNotIn(orders["snapshot_id"], expected)
        self.assertIn('[11,"华东",11]', expected)
        self.assertIn("[[1,5],[2,4]]", expected)
        with patch("smartreportgen.api.auth.verify_bearer") as auth:
            # 不启动应用生命周期或访问业务数据库。仅用模拟 IAM 身份测试实际 HTTP 路由。
            client = TestClient(app)
            path = "/reports/preview/" + preview["url"].rsplit("/", 1)[-1]
            auth.return_value = self.owner
            response = client.get(path, params={"download": True}, headers={"Authorization": "Bearer test"})
            self.assertEqual(response.text, expected)
            self.assertIn("attachment", response.headers["Content-Disposition"])
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            auth.return_value = Principal("another-employee", is_admin=True)
            self.assertEqual(client.get(path, headers={"Authorization": "Bearer test"}).status_code, 404)
            self.assertEqual(client.get(path).status_code, 401)

    def test_refuse_truncated_query_and_keep_small_sample(self):
        result = mcp_server.run_query(1, "SELECT * FROM Orders", None, limit=3, snapshot=True)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["row_count"], 3)
        self.assertNotIn("snapshot_id", result)
        self.assertNotIn("rows", result)
        self.assertEqual(list(Path(self.work).iterdir()), [])
        payload = "中" * 10000
        snapshot = report_data.save_query(self.owner.emp_no, ["text"], [[payload]], 1)
        self.assertTrue(snapshot["sample_limited"])
        self.assertEqual(snapshot["sample_rows"], [])
        file = next(Path(self.work).glob("*.json"))
        self.assertEqual(json.loads(file.read_text())["rows"], [[payload]])

    def test_escape_json_and_reuse_after_snapshot_expires(self):
        dangerous = "</script><script>alert(1)</script>"
        snapshot = report_data.save_query(self.owner.emp_no, ["text"], [[dangerous]], 1)
        draft = document(block("data", snapshot["snapshot_id"]))
        with self.assertRaisesRegex(report_data.ReportDataError, "快照不可用"):
            report_data.embed(draft, "other")
        expanded = report_data.embed(draft, self.owner.emp_no)
        self.assertNotIn(dangerous, expanded)
        self.assertIn(r"\u003c/script>", expanded)
        skeleton = report_data.for_edit(expanded)
        self.assertIn(block("data", "base"), skeleton)
        ref = UUID(snapshot["snapshot_id"])
        path = next(Path(self.work).glob(f"*-{ref.hex}.json"))
        past = time.time() - report_data.TTL_SECONDS - 1
        os.utime(path, (past, past))
        with self.assertRaisesRegex(report_data.ReportDataError, "已过期"):
            report_data.embed(draft, self.owner.emp_no)
        self.assertEqual(report_data.embed(skeleton, "editor", expanded), expanded)
        with self.assertRaisesRegex(report_data.ReportDataError, "基准版本没有"):
            report_data.embed(skeleton, "editor")

    def test_reject_snapshot_id_conflicts_during_preview_and_save(self):
        snapshot = report_data.save_query(self.owner.emp_no, ["value"], [[42]], 0)
        data = block("forecast", snapshot["snapshot_id"])
        for draft in (
            document('<section id="fore&#99;ast">预测</section>', data),
            document(data, '<input id="forecast" />'),
        ):
            with self.subTest(draft=draft):
                preview = mcp_server.preview_report_html(draft, None)
                saved = mcp_server.save_report("测试报告", draft, None)
                self.assertFalse(preview["ok"])
                self.assertEqual(saved, preview)
                self.assertIn("数据块 id 与页面元素重复：forecast", preview["error"])
        # 静态 template 的普通元素不与主文档争用 id；数据块本身必须在主文档中。
        valid = document('<template><section id="forecast"></section></template>', data)
        self.assertTrue(mcp_server.preview_report_html(valid, None)["ok"])
        inert = document(f"<template>{data}</template>")
        self.assertIn("数据块位于 template 内", mcp_server.preview_report_html(inert, None)["error"])

    def test_can_correct_an_existing_snapshot_id_conflict(self):
        # 旧报告的已内联数据仍可读出，修订时可保留数据块 id 并修正冲突元素。
        embedded = block("forecast", "embedded").replace("></script>", '>{"columns":["value"],"rows":[[42]]}</script>')
        old = document('<section id="forecast"></section>', embedded)
        revised = report_data.for_edit(old).replace('<section id="forecast">', '<section id="forecast-section">')
        self.assertEqual(report_data.embed(revised, "editor", old),
                         old.replace('<section id="forecast">', '<section id="forecast-section">'))

    def test_placeholder_style_vendor_asset_embed_and_for_edit(self):
        with patch.object(mcp_server, "_principal", return_value=self.owner), \
                patch.object(mcp_server.repo, "style_rules_for", return_value={
                    "business_scenario": "测试场景", "precedence": "p", "reporting_rules": [],
                    "specified": {"theme": {"color-brand": "#123456"}, "theme_sources": {}, "items": []},
                    "general_rules": [], "scenario_rules": [], "warnings": [],
                }):
            compact = mcp_server.get_style_rules("测试场景", None, include_css=False)
        self.assertTrue(compact["ok"], compact)
        self.assertNotIn("css", compact["builtin_style"])
        style_id = compact["builtin_style"]["style_id"]
        snap = report_data.save_query(self.owner.emp_no, ["v"], [[7]], 0)["snapshot_id"]
        draft = (
            f'<!doctype html><html><head><meta charset="utf-8">'
            f'<style data-srg-style="{style_id}"></style>'
            f'<style>.srg-up{{color:var(--srg-up)}}</style></head><body>'
            f'<script type="application/json" id="d" data-srg-snapshot="{snap}"></script>'
            f'<script data-srg-vendor="echarts"></script>'
            f'<script id="srg-formatters" data-srg-asset="report-formatters.js"></script>'
            f'<script>const x = 1;</script></body></html>'
        )
        expanded = report_data.embed(draft, self.owner.emp_no)
        self.assertIn('data-srg-style="embedded"', expanded)
        self.assertIn("#123456", expanded)
        self.assertIn('data-srg-vendor="embedded"', expanded)
        self.assertIn('echarts', expanded)
        self.assertIn('data-srg-asset="embedded"', expanded)
        self.assertIn("SrgDisplay", expanded)
        skeleton = report_data.for_edit(expanded)
        self.assertIn('data-srg-style="base"', skeleton)
        self.assertIn('data-srg-snapshot="base"', skeleton)
        self.assertIn('data-srg-vendor="echarts"', skeleton)
        self.assertIn('data-srg-asset="report-formatters.js"', skeleton)
        self.assertEqual(report_data.embed(skeleton, "editor", expanded), expanded)
        # 基准存在多个无 id 的内嵌样式块时，未指定 id 的 data-srg-style="base" 应拒绝。
        ambiguous_base = expanded.replace("</head>", '<style data-srg-style="embedded">body{margin:1px}</style></head>')
        with self.assertRaisesRegex(report_data.ReportDataError, "样式块"):
            report_data.embed(skeleton, "editor", ambiguous_base)

    def test_preview_id_save_and_revise_reuse_and_isolation(self):
        snap = mcp_server.run_query(1, "SELECT * FROM Targets", None, snapshot=True, sample_rows=0)
        draft = document(block("targets", snap["snapshot_id"]))
        preview = mcp_server.preview_report_html(draft, None)
        self.assertTrue(preview["ok"], preview)
        self.assertIn("preview_id", preview)
        self.assertEqual(len(preview["source_sha256"]), 64)
        self.assertEqual(len(preview["html_sha256"]), 64)
        # 跨工号不能复用他人 preview_id。
        other_access = Access(Principal("other-user"), tables={1: {"orders", "targets"}})
        with patch.object(mcp_server, "_access", return_value=other_access):
            denied = mcp_server.save_report("盗用预览", ctx=None, preview_id=preview["preview_id"])
            self.assertFalse(denied["ok"])
            self.assertIn("预览源码不可用", denied["error"])
        # 使用 preview_id 保存报告，传给 repo.save_report 的正是同一份源 HTML。
        with patch.object(mcp_server.repo, "save_report", return_value={
            "id": 99, "revision": 1, "meta": {"html_bytes": preview["html_bytes"]},
        }) as save_mock:
            saved = mcp_server.save_report("新报告", ctx=None, preview_id=preview["preview_id"])
        self.assertTrue(saved["ok"], saved)
        self.assertEqual(save_mock.call_args.args[1], draft)
        # 传参互斥校验。
        both = mcp_server.save_report("冲突", html=draft, ctx=None, preview_id=preview["preview_id"])
        neither = mcp_server.save_report("缺失", ctx=None)
        self.assertFalse(both["ok"])
        self.assertFalse(neither["ok"])

        # 基于已有报告预览后的 preview_id 只能用于 revise_report，且校验 report_id 与 base_version。
        expanded = report_data.embed(draft, self.owner.emp_no)
        base_report = {"id": 99, "revision": 2, "access_level": "edit", "html": expanded, "meta": {"single_file": True}}
        revised_draft = document(block("targets", "base")).replace("</script>", "</script><!--v3-->", 1)
        with patch.object(mcp_server.repo, "get_report", return_value=base_report):
            rev_preview = mcp_server.preview_report_html(revised_draft, None, report_id=99, base_version=2)
        self.assertTrue(rev_preview["ok"], rev_preview)
        wrong_save = mcp_server.save_report("误调新建", ctx=None, preview_id=rev_preview["preview_id"])
        self.assertFalse(wrong_save["ok"])
        self.assertIn("revise_report", wrong_save["error"])
        mismatch_report = mcp_server.revise_report(100, "错报告", None, preview_id=rev_preview["preview_id"])
        mismatch_ver = mcp_server.revise_report(99, "错版本", None, base_version=1, preview_id=rev_preview["preview_id"])
        self.assertFalse(mismatch_report["ok"])
        self.assertFalse(mismatch_ver["ok"])
        with patch.object(mcp_server.services, "revise_report", return_value={
            "id": 99, "revision": 3, "url": "u", "content_url": "c", "download_url": "d",
        }) as rev_mock:
            ok_rev = mcp_server.revise_report(99, "修订报告", None, preview_id=rev_preview["preview_id"])
        self.assertTrue(ok_rev["ok"], ok_rev)
        self.assertEqual(rev_mock.call_args.kwargs["html"], revised_draft)
        self.assertEqual(rev_mock.call_args.kwargs["base_version"], 2)

    def test_snapshot_query_returns_up_to_200_sample_rows_without_truncation(self):
        big_connector = APIConnector(self.source, "")
        big_rows = {"Orders": [{"id": i, "region": "华东", "value": i} for i in range(1, 101)]}
        big_connector.fetch = big_rows.__getitem__
        with patch.object(mcp_server.services, "get_connector", return_value=big_connector):
            res = mcp_server.run_query(1, "SELECT id, region, value FROM Orders ORDER BY id",
                                       None, snapshot=True, sample_rows=100)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["row_count"], 100)
        self.assertEqual(len(res["sample_rows"]), 100)
        self.assertFalse(res["sample_limited"])


if __name__ == "__main__":
    unittest.main()
