"""验证子智能体并行拼装所需的槽位保序、局部读取、重复 DOM id 归因与浏览器修复闭环。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from smartreportgen import mcp_server, repo, report_browser, report_data, report_draft, report_pipeline, report_queries
from smartreportgen.auth import Principal
from smartreportgen.permission import Access


class ParallelSubagentDraftTest(unittest.TestCase):
    def setUp(self):
        self.work = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(mcp_server.settings, "report_work_dir", self.work))
        self.owner = Principal("parallel-author")
        self.access = Access(self.owner, tables={1: {"facts"}})
        self.source = {"id": 12, "type": "api", "name": "并行测试源", "enabled": True, "updated_at": "v1"}
        self.dataset = {
            "id": 1,
            "datasource_id": 12,
            "business_scenario": "区域经营分析",
            "enabled": True,
            "tables": [{"table_name": "facts", "table_description": "事实", "row_filters": []}],
        }
        self.scope = report_queries.scope_for(1, self.dataset, self.source, ["facts"])
        self.enterContext(patch.object(mcp_server, "_access", return_value=self.access))
        self.enterContext(patch.object(report_queries, "access_for", return_value=self.access))
        self.enterContext(patch.object(report_pipeline, "access_for", return_value=self.access))
        self.enterContext(patch.object(report_draft, "access_for", return_value=self.access))
        self.enterContext(patch.object(repo, "get_dataset", return_value=self.dataset))
        self.enterContext(patch.object(repo, "get_datasource", return_value=self.source))
        self.snapshot_id = report_data.save_query(
            self.owner.emp_no,
            ["region", "month", "amount"],
            [
                ["华东", "1月", {"$decimal": "120.50"}],
                ["华南", "1月", {"$decimal": "85.25"}],
                ["华东", "2月", {"$decimal": "140.00"}],
                ["华南", "2月", {"$decimal": "92.75"}],
            ],
            4,
            origin={"scope": self.scope},
        )["snapshot_id"]

    def test_pre_slotted_parallel_upserts_preserve_order_titles_and_scoped_reads(self):
        slots = [{"section_id": f"s{i}", "title": f"第 {i} 章"} for i in range(6)]
        draft = mcp_server.create_html_draft(
            "并行槽位报告",
            None,
            dataset_id=1,
            snapshots={"facts": self.snapshot_id},
            shared_js="SRG.filters = { state: '华东' };",
            sections=slots,
        )
        self.assertTrue(draft["ok"], draft)
        draft_id = draft["draft_id"]
        self.assertEqual([item["section_id"] for item in draft["sections"]], [f"s{i}" for i in range(6)])

        # 模拟 6 个子智能体逆序/乱序并发填充预置槽位，不重复传 title，也不传 after_section_id。
        order = [5, 2, 0, 4, 1, 3]
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(
                pool.map(
                    lambda idx: mcp_server.upsert_html_section(
                        draft_id,
                        None,
                        section_id=f"s{idx}",
                        html=f"<p>章节 {idx} 内容</p>",
                        js=f"const n{idx} = {idx};",
                    ),
                    order,
                )
            )
        self.assertTrue(all(item["ok"] for item in results), results)

        status = mcp_server.get_html_draft(draft_id, None)
        self.assertTrue(status["ok"], status)
        self.assertEqual([(item["section_id"], item["title"]) for item in status["sections"]],
                         [(f"s{i}", f"第 {i} 章") for i in range(6)])

        subset = mcp_server.get_html_draft(draft_id, None, include_code=True, section_ids=["s4", "s1"])
        self.assertTrue(subset["ok"], subset)
        self.assertEqual([item["section_id"] for item in subset["sections"]], ["s1", "s4"])
        self.assertEqual(subset["sections"][0]["html"], "<p>章节 1 内容</p>")
        self.assertEqual(subset["sections"][1]["js"], "const n4 = 4;")
        self.assertNotIn("shared_js", subset)

        shared = mcp_server.get_html_draft(draft_id, None, include_code=True, section_id="__shared__")
        self.assertTrue(shared["ok"], shared)
        self.assertEqual(shared["sections"], [])
        self.assertEqual(shared["shared_js"], "SRG.filters = { state: '华东' };")

        both = mcp_server.get_html_draft(draft_id, None, include_code=True, section_id="s1", section_ids=["s2"])
        self.assertFalse(both["ok"])
        malformed = mcp_server.get_html_draft(draft_id, None, include_code=True, section_ids=[{}])
        self.assertIn("章节标识必须为字符串", malformed["error"])
        missing = mcp_server.get_html_draft(draft_id, None, include_code=True, section_id="not-exists")
        self.assertFalse(missing["ok"])
        self.assertIn("草稿中没有该章节", missing["error"])

        # 新建和批量更新共用保留标识规则。失败的批次不得写入前面已处理的章节。
        invalid_slots = [{"section_id": "extra"}, {"section_id": "__shared__"}]
        invalid = mcp_server.create_html_draft("冲突槽位", None, dataset_id=1, sections=invalid_slots)
        self.assertIn("保留给页面骨架与共享脚本", invalid["error"])
        invalid = mcp_server.upsert_html_section(draft_id, None, sections=invalid_slots)
        self.assertIn("保留给页面骨架与共享脚本", invalid["error"])
        after = mcp_server.get_html_draft(draft_id, None)
        self.assertEqual(after["revision"], status["revision"])
        self.assertEqual(after["sections"], status["sections"])

    def test_duplicate_dom_ids_report_exact_colliding_sections_without_breaking_templates_or_legacy(self):
        shell = (
            '<div class="srg-page"><header><select id="region"><option>华东</option></select></header>'
            '<main id="srg-main"><!--SRG_SECTIONS--></main></div>'
        )
        draft = mcp_server.create_html_draft(
            "ID 冲突检测",
            None,
            dataset_id=1,
            snapshots={"facts": self.snapshot_id},
            shell_html=shell,
            sections=[
                {
                    "section_id": "overview",
                    "html": '<select id="region"><option>华东</option></select><output id="kpi">100</output>',
                },
                {
                    "section_id": "trend",
                    "html": '<output id="kpi">200</output><template id="facts"><div id="tpl-only">A</div></template>',
                },
                {
                    "section_id": "details",
                    "html": '<div id="row-1">1</div><div id="row-1">2</div><template><div id="tpl-only">B</div></template>',
                },
            ],
        )
        self.assertTrue(draft["ok"], draft)
        with patch.object(report_browser, "verify_html", return_value={"status": "passed"}) as verify_mock:
            failed = mcp_server.verify_html_draft(None, draft_id=draft["draft_id"])
            verify_mock.assert_not_called()

        self.assertFalse(failed["ok"], failed)
        self.assertEqual(failed["error_type"], "verify_failed")
        self.assertEqual(failed["failed_stage"], "html.embed_and_check")
        dups = {item["id"]: item["section_ids"] for item in failed["diagnostics"]["duplicate_ids"]}
        self.assertEqual(dups, {
            "region": ["__shared__", "overview"],
            "kpi": ["overview", "trend"],
            "row-1": ["details"],
            "facts": ["trend", "__shared__"],
        })
        self.assertNotIn("tpl-only", dups)
        self.assertEqual(failed["diagnostics"]["section_ids"], ["__shared__", "overview", "trend", "details"])

        # 历史整页报告扩展模式不得被旧页面中的历史重复元素 id 阻塞。
        legacy_html = (
            "<!doctype html><html><head><style>body{margin:0}</style></head>"
            '<body><main><div id="legacy-dup">1</div><div id="legacy-dup">2</div></main>'
            "<script>window.ok = true;</script></body></html>"
        )
        base_report = {
            "id": 88,
            "revision": 1,
            "access_level": "edit",
            "html": legacy_html,
            "meta": {"source": "compiled_spec", "scopes": [self.scope], "dataset_id": 1, "single_file": True},
            "dataset_id": 1,
            "template_id": None,
            "template_version": None,
        }
        with patch.object(repo, "get_report", return_value=base_report), \
                patch.object(report_browser, "verify_html", return_value={"status": "passed"}):
            legacy_draft = mcp_server.create_html_draft("历史扩展", None, report_id=88, base_version=1)
            self.assertTrue(legacy_draft["ok"], legacy_draft)
            mcp_server.upsert_html_section(legacy_draft["draft_id"], None, "ext", "<p id='ext-note'>补充</p>")
            verified = mcp_server.verify_html_draft(None, draft_id=legacy_draft["draft_id"])
            self.assertTrue(verified["ok"], verified)

            # 扩展章节新增冲突时仍需归因，不能因历史整页含有重复 ID 而跳过。
            mcp_server.upsert_html_section(legacy_draft["draft_id"], None, "ext", "<p id='legacy-dup'>补充</p>")
            failed_extension = mcp_server.verify_html_draft(None, draft_id=legacy_draft["draft_id"])
            self.assertEqual(failed_extension["diagnostics"]["duplicate_ids"],
                             [{"id": "legacy-dup", "count": 3, "section_ids": ["__shared__", "ext"]}])

    def test_dynamic_duplicate_ids_are_attributed_after_filter_smoke(self):
        if not Path(os.environ.get("SRG_CHROMIUM_PATH", "/usr/bin/chromium")).is_file():
            self.skipTest("需要部署镜像中的 Chromium")
        self.addCleanup(report_browser.close_pool)
        draft = mcp_server.create_html_draft(
            "筛选后生成的 ID", None, dataset_id=1, use_echarts=False,
            sections=[
                {"section_id": "overview", "html": '<p id="total">总览</p>'},
                {"section_id": "detail", "html": '<label>区域 <select><option>华东</option><option>华南</option></select></label>',
                 "js": """const root = SRG.section('detail');
                 root.querySelector('select').addEventListener('change', () =>
                   root.insertAdjacentHTML('beforeend', '<output id="total">详情</output>'));"""},
            ],
        )
        failure = mcp_server.verify_html_draft(None, draft_id=draft["draft_id"])
        self.assertEqual(failure["failed_stage"], "chromium.verify", failure)
        self.assertEqual(failure["diagnostics"]["section_ids"], ["overview", "detail"])
        self.assertEqual(failure["diagnostics"]["duplicate_ids"],
                         [{"id": "total", "count": 2, "section_ids": ["overview", "detail"]}])
        self.assertEqual(failure["diagnostics"]["control"]["section_id"], "detail")

    def test_srg_section_helper_and_multi_section_parallel_repair_in_chromium(self):
        if not Path(os.environ.get("SRG_CHROMIUM_PATH", "/usr/bin/chromium")).is_file():
            self.skipTest("需要部署镜像中的 Chromium")
        self.addCleanup(report_browser.close_pool)

        shell = (
            '<div class="srg-page"><header><h1>区域经营</h1>'
            '<label>区域 <select id="filter-region"><option>华东</option><option>华南</option></select></label>'
            '</header><main id="srg-main"><!--SRG_SECTIONS--></main></div>'
        )
        shared_js = """
        const select = document.getElementById('filter-region');
        const listeners = [];
        SRG.filters = {
          subscribe(fn) { listeners.push(fn); fn(select.value); }
        };
        select.addEventListener('change', () => listeners.forEach(fn => fn(select.value)));
        """
        draft = mcp_server.create_html_draft(
            "多章节并行修复验证",
            None,
            dataset_id=1,
            snapshots={"facts": self.snapshot_id},
            shell_html=shell,
            shared_js=shared_js,
            sections=[
                {"section_id": "overview", "title": "概览"},
                {"section_id": "trend", "title": "趋势"},
            ],
        )
        self.assertTrue(draft["ok"], draft)
        draft_id = draft["draft_id"]

        # 两个章节同时注入运行期错误，校验可一次性识别两个故障章节 ID。
        mcp_server.upsert_html_section(
            draft_id,
            None,
            sections=[
                {
                    "section_id": "overview",
                    "html": '<h2>概览</h2><output data-role="total">—</output>',
                    "js": "throw new Error('概览脚本异常');",
                },
                {
                    "section_id": "trend",
                    "html": '<h2>趋势</h2><div data-role="chart" style="height:260px"></div>',
                    "js": "throw new Error('趋势脚本异常');",
                },
            ],
        )
        failed = mcp_server.verify_html_draft(None, draft_id=draft_id)
        self.assertFalse(failed["ok"], failed)
        self.assertEqual(failed["failed_stage"], "chromium.verify")
        self.assertEqual(failed["diagnostics"]["section_ids"], ["overview", "trend"])

        # 按诊断出来的 section_ids 并行修复，且使用 SRG.section() 避免内层元素 id。
        repairs = {
            "overview": (
                '<h2>概览</h2><output data-role="total">—</output>',
                """
                const root = SRG.section('overview');
                const out = root.querySelector('[data-role="total"]');
                const rows = SRG.rows('facts');
                SRG.filters.subscribe(region => {
                  const total = rows.filter(r => r.region === region)
                    .reduce((acc, r) => acc + SRG.num(r.amount), 0);
                  out.textContent = total.toFixed(2);
                });
                """,
            ),
            "trend": (
                '<h2>趋势</h2><div data-role="chart" style="height:260px"></div>',
                """
                const root = SRG.section('trend');
                const dom = root.querySelector('[data-role="chart"]');
                const rows = SRG.rows('facts');
                const chart = SRG.initChart(dom, {
                  animation: false,
                  xAxis: {type: 'category', data: []},
                  yAxis: {type: 'value'},
                  series: [{type: 'bar', data: []}]
                });
                SRG.filters.subscribe(region => {
                  const pts = rows.filter(r => r.region === region);
                  chart.setOption({
                    xAxis: {data: pts.map(r => r.month)},
                    series: [{data: pts.map(r => SRG.num(r.amount))}]
                  });
                });
                """,
            ),
        }
        with ThreadPoolExecutor(max_workers=2) as pool:
            repair_results = list(
                pool.map(
                    lambda sid: mcp_server.upsert_html_section(
                        draft_id, None, section_id=sid, html=repairs[sid][0], js=repairs[sid][1],
                    ),
                    failed["diagnostics"]["section_ids"],
                )
            )
        self.assertTrue(all(item["ok"] for item in repair_results), repair_results)

        persisted = []

        def fake_save(name, html, template_id, template_version, meta, owner, *, source):
            persisted.append((name, html, meta, source))
            return {
                "id": 901,
                "revision": 1,
                "meta": meta,
                "template_id": template_id,
                "template_version": template_version,
            }

        with patch.object(repo, "save_report", side_effect=fake_save):
            saved = mcp_server.save_report("并行修复验收报告", None, draft_id=draft_id)
        self.assertTrue(saved["ok"], saved)
        self.assertEqual(saved["report_id"], 901)
        self.assertEqual(saved["verification"]["status"], "passed")
        self.assertEqual(saved["verification"]["chart_count"], 1)
        self.assertEqual(saved["verification"]["interactive_controls_tested"], 1)
        self.assertEqual(len(persisted), 1)


if __name__ == "__main__":
    unittest.main()
