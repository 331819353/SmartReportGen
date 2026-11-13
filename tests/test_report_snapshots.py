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


if __name__ == "__main__":
    unittest.main()
