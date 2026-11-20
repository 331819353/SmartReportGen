"""验证轻量探查的查询成本、字段缓存与并发隔离；只使用内存夹具。"""
import asyncio
import json
import threading
import time
import unittest
from unittest.mock import patch

from smartreportgen import mcp_server, repo, search
from smartreportgen.auth import Principal
from smartreportgen.config import settings
from smartreportgen.connectors import get_connector
from smartreportgen.connectors.api import APIConnector
from smartreportgen.connectors.base import Connector
from smartreportgen.permission import Access
from smartreportgen.telemetry import measure, observe_tool


class MetadataConnector(Connector):
    """模拟数据库字段接口，避免性能用例接触真实数据库或业务数据。"""
    type = "postgresql"

    def __init__(self):
        super().__init__({}, "")
        self.reads = 0

    def columns(self, table):
        self.reads += 1
        return [{"name": "value", "type": "integer", "comment": table}]


class WorkflowPerformanceTest(unittest.TestCase):
    def setUp(self):
        self.source = {"type": "api", "name": "内存测试源"}
        self.connector = APIConnector(self.source, "")
        records = {"Facts": [{"id": i, "region": "华东", "value": i} for i in range(250)] + [
            {"id": 999, "region": "华北", "value": 9999},
        ]}
        self.connector.fetch = records.__getitem__
        self.dataset = {"tables": [{"table_name": "Facts", "table_description": "测试事实", "row_filters": [
            {"column": "region", "values": ["华东"]},
        ]}]}
        self.access = Access(Principal("probe-reader"), tables={1: {"facts"}})
        self.enterContext(patch.object(mcp_server, "_access", return_value=self.access))
        self.enterContext(patch.object(mcp_server.repo, "get_dataset", return_value=self.dataset))
        self.enterContext(patch.object(mcp_server.services, "datasource_for_dataset", return_value=self.source))
        self.enterContext(patch.object(mcp_server.services, "get_connector", return_value=self.connector))

    def test_scene_search_miss_returns_the_configured_management_entry(self):
        # MCP 可以单独发布，场景配置链接必须使用管理台的域名和子路径。
        empty = {"hits": [], "warnings": [], "query_tokens": ["新增场景"]}
        with patch.object(settings, "public_base_url", "https://portal.example/reports-tools"), \
                patch.object(settings, "mcp_public_url", "https://mcp.example/mcp"), \
                patch.object(mcp_server.repo, "search_datasets", return_value=empty) as search:
            result = mcp_server.search_business_scenarios("新增场景", None)
        self.assertTrue(result["ok"])
        self.assertEqual(result["hits"], [])
        self.assertIn("有权限", result["tip"])
        self.assertEqual(result["scenario_management_url"], "https://portal.example/reports-tools/#datasets")
        search.assert_called_once_with("新增场景", 3, dataset_ids=[1])

    def test_scenario_search_uses_synonyms_and_bundles_unambiguous_context(self):
        # 业务分析思路和指标别名都应进入离线检索，零相似度哈希结果不会冒充命中。
        ds = {"id": 1, "business_scenario": "经营看板", "business_description": "", "tags": [], "tables": [],
              "analysis_approach": "按月查看营业收入", "semantic_metrics": {
                  "delivery_rate": {"table": "Facts", "description": "准时交付率"}}}
        index = repo._dataset_index(ds, [])
        self.assertIn("营收", index["kw_b"])
        self.assertIn("按时交付率", index["kw_c"])
        with patch.object(settings, "embedding_provider", "hash"), patch.object(search, "embed_query"), \
                patch.object(search, "_keyword", return_value=[]) as keyword, \
                patch.object(search, "_vector", return_value=[{"id": 1, "score": 0}]):
            self.assertEqual(search.hybrid("dataset", "营收")['hits'], [])
            self.assertIn("营业收入", keyword.call_args.args[1])
        result = {"hits": [{"dataset": ds, "relevance": 0.9}], "query_tokens": ["营收"], "warnings": []}
        with patch.object(repo, "search_datasets", return_value=result) as find, \
                patch.object(mcp_server, "prepare_report_context", return_value={"ok": True}) as prepare:
            response = mcp_server.search_business_scenarios("营收", None, top_k=1, include_context="structured")
        self.assertTrue(response["context"]["ok"])
        find.assert_called_once_with("营收", 2, dataset_ids=[1])
        prepare.assert_called_once_with(1, None, "营收", mode="structured")

    def test_light_probe_skips_full_scans_and_limits_sample(self):
        with patch.object(self.connector, "query", wraps=self.connector.query) as query:
            light = mcp_server.describe_table(1, "Facts", None)
            self.assertEqual(query.call_count, 1)
            self.assertNotIn("COUNT(", query.call_args.args[0].upper())
            self.assertNotIn("MIN(", query.call_args.args[0].upper())
        self.assertTrue(light["ok"])
        self.assertEqual(light["profile_sample_size"], 100)
        self.assertEqual(len(light["sample_rows"]), 5)
        self.assertIsNone(light["row_count"])
        self.assertEqual(light["row_count_status"], "not_requested")
        self.assertEqual(light["extrema_status"], "not_requested")
        self.assertTrue(all("full_max" not in column for column in light["profile"]))
        # 完整模式仍只统计授权的华东范围，不能因优化混入华北极值。
        with patch.object(self.connector, "query", wraps=self.connector.query) as query:
            full = mcp_server.describe_table(1, "Facts", None, mode="full")
            self.assertEqual(query.call_count, 3)
        self.assertEqual(full["row_count"], 250)
        self.assertEqual(full["row_count_status"], "exact")
        value = next(column for column in full["profile"] if column["column"] == "value")
        self.assertEqual((value["full_min"], value["full_max"]), (0, 249))

    def test_zero_sample_does_not_run_hidden_sample_query(self):
        with patch.object(self.connector, "query", wraps=self.connector.query) as query:
            result = mcp_server.describe_table(1, "Facts", None, sample_rows=0)
            query.assert_not_called()
        self.assertEqual(result["profile_sample_size"], 0)
        self.assertEqual(result["sample_rows"], [])
        self.assertTrue(result["columns"])
        explicit = mcp_server.describe_table(1, "Facts", None, sample_rows=0, profile_rows=7)
        self.assertEqual(explicit["profile_sample_size"], 7)
        self.assertEqual(explicit["sample_rows"], [])

    def test_batch_profiles_preserve_scope_and_check_grants_before_reading(self):
        result = mcp_server.describe_tables(1, None, include_profile=True, profile_rows=12)
        item = result["tables"][0]
        self.assertEqual(item["profile_sample_size"], 12)
        self.assertTrue(all(row[1] == "华东" for row in item["sample_rows"]))
        self.assertEqual(item["row_count_status"], "not_requested")
        self.assertIn("describe.sample", result["timing"]["stages"])
        self.access.tables[1].clear()
        with patch.object(self.connector, "cached_columns") as columns, patch.object(self.connector, "query") as query:
            denied = mcp_server.describe_tables(1, None, ["Facts"], include_profile=True)
            self.assertEqual(denied["error_type"], "permission_denied")
            columns.assert_not_called()
            query.assert_not_called()


class MetadataCacheTest(unittest.TestCase):
    def test_cache_expiry_refresh_and_returned_value_isolation(self):
        connector = MetadataConnector()
        with patch.object(settings, "schema_cache_seconds", 10), \
                patch("smartreportgen.connectors.base.monotonic", return_value=0) as now:
            first = connector.cached_columns("Facts")
            first[0]["type"] = "changed"
            self.assertEqual(connector.cached_columns("Facts")[0]["type"], "integer")
            self.assertEqual(connector.reads, 1)
            connector.cached_columns("Facts", refresh=True)
            self.assertEqual(connector.reads, 2)
            now.return_value = 11
            connector.cached_columns("Facts")
            self.assertEqual(connector.reads, 3)
        with patch.object(settings, "schema_cache_seconds", 0):
            connector.cached_columns("Facts")
            connector.cached_columns("Facts")
        self.assertEqual(connector.reads, 5)

    def test_changed_datasource_does_not_reuse_old_metadata(self):
        first, second = MetadataConnector(), MetadataConnector()
        source = {"id": 101, "type": "postgresql", "updated_at": "v1"}
        with patch("smartreportgen.connectors._cache", {}), \
                patch("smartreportgen.connectors.crypto.decrypt", return_value=""), \
                patch("smartreportgen.connectors.SQLConnector", side_effect=[first, second]):
            get_connector(source).cached_columns("Facts")
            self.assertIs(get_connector(source), first)
            replacement = get_connector({**source, "updated_at": "v2"})
            self.assertIs(replacement, second)
            replacement.cached_columns("Facts")
            self.assertEqual(second.reads, 1)


class ToolTimingTest(unittest.TestCase):
    def test_timing_isolated_and_logs_do_not_include_arguments_or_error_messages(self):
        @observe_tool
        def fake(sql, fail=False):
            with measure("query.execute"):
                if fail:
                    raise ValueError("private-error-content")
                return {"ok": True}

        with self.assertLogs("smartreportgen.timing", level="INFO") as logs:
            first = fake("private-sql-content")
            with self.assertRaises(ValueError):
                fake("private-sql-content", fail=True)
            second = fake("another-private-query")
        self.assertNotEqual(first["timing"]["request_id"], second["timing"]["request_id"])
        self.assertEqual(second["timing"]["stages"]["query.execute"]["calls"], 1)
        self.assertNotIn("private", " ".join(logs.output))
        events = [json.loads(record.getMessage()) for record in logs.records]
        self.assertEqual([event["status"] for event in events], ["ok", "error", "ok"])


class ToolConcurrencyTest(unittest.IsolatedAsyncioTestCase):
    async def test_slow_tool_does_not_block_other_tools_and_stage_context_is_private(self):
        entered, release = threading.Event(), threading.Event()
        access = Access(Principal("concurrency-reader"), tables={1: {"facts"}})
        dataset = {"tables": [{"table_name": "Facts", "table_description": "夹具", "row_filters": []}]}

        def slow_describe(*args, **kwargs):
            with measure("test.blocked_probe"):
                entered.set()
                if not release.wait(3):
                    raise AssertionError("慢工具阻塞了事件循环，未收到并发释放信号")
            return {"columns": []}

        with patch.object(mcp_server, "_access", return_value=access), \
                patch.object(mcp_server.repo, "get_dataset", return_value=dataset), \
                patch.object(mcp_server.services, "datasource_for_dataset", return_value={}), \
                patch.object(mcp_server.services, "get_connector", return_value=MetadataConnector()), \
                patch.object(mcp_server.services, "describe_table", side_effect=slow_describe):
            slow = mcp_server.mcp._tool_manager.get_tool("describe_table")
            quick = mcp_server.mcp._tool_manager.get_tool("describe_tables")
            task = asyncio.create_task(slow.run({"dataset_id": 1, "table_name": "Facts"}))
            try:
                self.assertTrue(await asyncio.wait_for(asyncio.to_thread(entered.wait, 1), 2))
                result = await asyncio.wait_for(quick.run({"dataset_id": 1}), 1)
                self.assertTrue(result["ok"])
                self.assertNotIn("test.blocked_probe", result["timing"]["stages"])
            finally:
                release.set()
                finished = await task
            self.assertIn("test.blocked_probe", finished["timing"]["stages"])

    async def test_registration_preserves_public_schema(self):
        tools = {tool.name: tool for tool in await mcp_server.mcp.list_tools()}
        schema = tools["describe_table"].inputSchema["properties"]
        self.assertTrue({"dataset_id", "table_name", "mode", "profile_rows"}.issubset(schema))
        self.assertFalse({"ctx", "args", "kwargs"}.intersection(schema))
        for tool_name, expected_keys in (
            ("save_report", {"name", "html", "preview_id", "template_id", "template_version"}),
            ("save_report_spec", {"name", "dataset_id", "spec", "template_id", "expected_version"}),
            ("revise_report", {"report_id", "name", "base_version", "html", "preview_id"}),
            ("get_style_rules", {"business_scenario", "query", "include_css"}),
            ("prepare_report_context", {"dataset_id", "query", "mode", "sample_rows", "profile_rows"}),
            ("list_scene_datasources", set()),
            ("save_business_scenario", {"business_scenario", "business_description", "datasource_id", "tables", "semantic_metrics"}),
        ):
            props = tools[tool_name].inputSchema["properties"]
            self.assertTrue(expected_keys.issubset(props), tool_name)
            self.assertFalse({"ctx", "args", "kwargs"}.intersection(props), tool_name)

    def test_prepare_report_context_bundles_scenario_style_and_contract(self):
        dataset = {"id": 1, "business_scenario": "测试场景", "business_description": "描述",
                   "analysis_approach": "先看总量", "datasource_id": 2, "datasource_name": "源",
                   "datasource_type": "api", "tags": ["测试"], "semantic_metrics": {},
                   "tables": [{"table_name": "Facts", "table_description": "测试事实", "row_filters": []}]}
        access = Access(Principal("context-reader"), tables={1: {"facts"}})
        style_stub = {"business_scenario": "测试场景", "precedence": "p", "reporting_rules": [],
                      "specified": {"theme": {"color-brand": "#123456"}, "theme_sources": {}, "items": []},
                      "general_rules": [], "scenario_rules": [], "warnings": []}
        with patch.object(mcp_server, "_access", return_value=access), \
                patch.object(mcp_server.repo, "get_dataset", return_value=dataset), \
                patch.object(mcp_server.services, "datasource_for_dataset", return_value={"type": "api", "name": "源"}), \
                patch.object(mcp_server.services, "get_connector", return_value=MetadataConnector()), \
                patch.object(mcp_server.services, "describe_table", return_value={"columns": [{"name": "v"}], "sample_rows": []}), \
                patch.object(mcp_server.repo, "style_rules_for", return_value=style_stub), \
                patch.object(mcp_server.report_data, "save_style", return_value="style-uuid"):
            res = mcp_server.prepare_report_context(1, None, mode="structured")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["scenario"]["analysis_approach"], "先看总量")
        self.assertEqual(res["total_tables"], 1)
        self.assertEqual(res["style"]["builtin_style"]["style_id"], "style-uuid")
        self.assertNotIn("css", res["style"]["builtin_style"])
        self.assertIn("schema", res["contract"])

    def test_list_scene_datasources_and_save_business_scenario(self):
        reader = Access(Principal("E100", roles=["analyst"]), tables={})
        sources = [
            {"id": 10, "name": "开放库", "type": "postgresql", "enabled": True, "description": "可用",
             "config": {"scenario_tables": [
                 {"table_name": "sales", "description": "销售", "roles": ["analyst"],
                  "row_filters": [{"column": "region", "values": ["华东"]}]},
                 {"table_name": "secret", "description": "保密", "roles": ["finance"]},
             ]}},
            {"id": 11, "name": "停用库", "type": "postgresql", "enabled": False, "config": {"scenario_tables": []}},
        ]
        with patch.object(mcp_server, "_access", return_value=reader), \
                patch.object(mcp_server.repo, "list_datasources", return_value=sources):
            listed = mcp_server.list_scene_datasources(None)
        self.assertTrue(listed["ok"], listed)
        self.assertEqual(len(listed["datasources"]), 1)
        self.assertEqual([t["table_name"] for t in listed["datasources"][0]["tables"]], ["sales"])
        created_ds = {"id": 55, "business_scenario": "新场景", "business_description": "说明",
                      "analysis_approach": "口径", "datasource_id": 10, "datasource_name": "开放库",
                      "datasource_type": "postgresql", "tags": ["经营"], "semantic_metrics": {},
                      "tables": [{"table_name": "sales", "table_description": "销售",
                                  "source_row_filters": [{"column": "region", "values": ["华东"]}], "row_filters": []}]}
        after_access = Access(reader.principal, tables={55: {"sales"}})
        with patch.object(mcp_server, "_access", return_value=reader), \
                patch.object(mcp_server.repo, "create_dataset", return_value=created_ds) as create_mock, \
                patch.object(mcp_server, "access_for", return_value=after_access):
            saved = mcp_server.save_business_scenario("新场景", "说明", 10, [{"table_name": "sales"}], None,
                                                      analysis_approach="口径", tags=["经营"])
        self.assertTrue(saved["ok"], saved)
        self.assertEqual(saved["dataset_id"], 55)
        self.assertEqual(saved["tables"][0]["row_filters"], [{"column": "region", "values": ["华东"]}])
        create_mock.assert_called_once()

    def test_describe_tables_runs_page_concurrently_preserving_order_and_timing(self):
        names = ["T1", "T2", "T3", "T4"]
        dataset = {"tables": [{"table_name": n, "table_description": n, "row_filters": []} for n in names]}
        access = Access(Principal("parallel-reader"), tables={1: {n.lower() for n in names}})
        barrier = threading.Barrier(4, timeout=2.0)

        def fake_describe(ds_row, table, *args, **kwargs):
            with measure("describe.sample"):
                barrier.wait()
                time.sleep(0.02)
            return {"columns": [{"name": table.lower()}], "sample_rows": [[table]]}

        with patch.object(mcp_server, "_access", return_value=access), \
                patch.object(mcp_server.repo, "get_dataset", return_value=dataset), \
                patch.object(mcp_server.services, "datasource_for_dataset", return_value={}), \
                patch.object(mcp_server.services, "get_connector", return_value=MetadataConnector()), \
                patch.object(mcp_server.services, "describe_table", side_effect=fake_describe):
            res = mcp_server.describe_tables(1, None, include_profile=True)
        self.assertTrue(res["ok"], res)
        self.assertEqual([t["table_name"] for t in res["tables"]], names)
        self.assertEqual(res["timing"]["stages"]["describe.sample"]["calls"], 4)

    def test_api_connector_reuses_cached_ndjson_across_queries(self):
        conn = APIConnector({"type": "api", "name": "cache-test", "config": {"base_url": "http://example.invalid",
                             "cache_seconds": 60, "records_path": "items", "endpoints": {"orders": {"path": "/orders"}}}}, "")

        class FakeResp:
            def raise_for_status(self):
                return None

            def json(self):
                return {"items": [{"id": 1, "amt": 10}, {"id": 2, "amt": 20}]}

        with patch("smartreportgen.connectors.api.httpx.request", return_value=FakeResp()), \
                patch("smartreportgen.connectors.api.json.dumps", wraps=json.dumps) as dumps_mock:
            first = conn.query("SELECT SUM(amt) FROM orders", 10)
            self.assertEqual(dumps_mock.call_count, 2)
            second = conn.query("SELECT COUNT(*) FROM orders", 10)
            self.assertEqual(dumps_mock.call_count, 2)
        self.assertEqual(first["rows"], [[30]])
        self.assertEqual(second["rows"], [[2]])


if __name__ == "__main__":
    unittest.main()
