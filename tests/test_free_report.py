"""自由版式的绑定边界、模块版本和 Chromium 渲染回归。"""
from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import patch

from smartreportgen import mcp_server, repo, report_browser, report_free, report_spec

from test_declarative_report import waterfall_fixture


def free_fixture():
    structured, facts = waterfall_fixture()
    card = structured.sections[0].cards[0].model_dump(mode="json")
    card["columns"][1]["format"]["unit"] = "万元"
    card["option"] = {"grid": {"left": "5%", "right": "4%"}, "textStyle": {"fontSize": 13}}
    return {
        "title": "经营变动分析",
        "layout": '<main class="board"><header><h1>经营变动分析</h1></header>'
                  '<srg-module m="summary"></srg-module><srg-module m="bridge"></srg-module></main>',
        "css": ".board{display:grid;gap:16px;padding:12px;min-width:0}",
        "metrics": {"closing": {"sources": [{"query": "facts", "field": card["columns"][1]["field"],
                                                "where": {card["columns"][0]["field"]: "期末"}}],
                                "format": card["columns"][1]["format"]}},
        "colors": structured.colors,
        "modules": {
            "summary": {"html": '<section class="paper"><p>期末余额<srg-val m="closing" badge></srg-val></p></section>',
                        "css": ".paper, .paper:hover {border-radius: 15px}"},
            "bridge": {"html": '<article><h2>变动桥接</h2><srg-output m="detail"></srg-output></article>',
                       "outputs": {"detail": card}},
        },
    }, facts


class FreeReportTest(unittest.TestCase):
    def test_free_layout_controls_projection_and_css_scope(self):
        raw, facts = free_fixture()
        spec = report_free.FreeReport.model_validate(raw)
        restored, dataset_id = report_spec.decode_template(report_spec.encode_template(spec, 91))
        self.assertEqual((restored, dataset_id), (spec, 91))
        source, page, _ = report_spec.compile_report(spec, {"facts": facts}, 91, {})
        self.assertIn('<srg-val class="srg-badge" data-metric="closing">', source)
        self.assertIn(':where([data-srg-module="summary"]) .paper:hover', source)
        self.assertIn('"left":"5%"', page)
        self.assertIn('id="card-bridge-detail"', page)

    def test_reject_unmanaged_html_css_and_chart_facts(self):
        edits = [
            {"css": '.paper::after{content:"一亿元"}'},
            {"css": r'.paper{background:u\72l(https://example.test/image)}'},
            {"layout": '<div><script>throw 0</script></div>'},
            {"layout": '<div><p>增长 &#49;&#48;%</p></div>'},
            {"layout": '<div><p>涨幅为百分之十二</p></div>'},
        ]
        for edit in edits:
            raw, _ = free_fixture()
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                report_free.FreeReport.model_validate({**raw, **edit})
        raw, _ = free_fixture()
        raw["modules"]["bridge"]["outputs"]["detail"]["option"]["series"] = [{"data": [45]}]
        with self.assertRaisesRegex(ValueError, "series.0.data"):
            report_free.FreeReport.model_validate(raw)

    def test_patch_preserves_other_modules_and_passes_expected_version(self):
        raw, _ = free_fixture()
        spec = report_free.FreeReport.model_validate(raw)
        current = {"id": 5, "name": "经营变动", "created_by": "demo", "version": 2,
                   "markdown": report_spec.encode_template(spec, 91)}
        replacement = spec.modules["bridge"].model_copy(update={
            "html": '<article class="updated"><srg-output m="detail"></srg-output></article>'})
        # 通过 MCP 入口验证只合入指定模块，最终仍由数据库锁下检查版本。
        with patch.object(mcp_server, "_access"), patch.object(mcp_server, "_check_owner"), \
                patch.object(repo, "get_template", return_value=current), \
                patch.object(mcp_server, "_store_free_template", return_value={"ok": True}) as save:
            self.assertTrue(mcp_server.patch_free_report_template(5, 2, {"bridge": replacement}, None)["ok"])
            updated = save.call_args.args[3]
            self.assertEqual(updated.layout, spec.layout)
            self.assertEqual(updated.modules["summary"], spec.modules["summary"])
            self.assertEqual(updated.modules["bridge"], replacement)
            self.assertEqual(save.call_args.args[-1], 2)
            self.assertEqual(mcp_server.patch_free_report_template(5, 1, {"bridge": replacement}, None)["error_type"],
                             "conflict")

    @unittest.skipUnless(Path(os.getenv("SRG_CHROMIUM_PATH", "/usr/bin/chromium")).is_file(), "需安装 Chromium")
    def test_browser_checks_free_slots_and_rejects_hidden_values(self):
        raw, facts = free_fixture()
        # 真实 Chromium 读取数字、画布和提示文字，也识别隐藏的绑定节点。
        for hidden in (False, True):
            with self.subTest(hidden=hidden):
                spec = report_free.FreeReport.model_validate(
                    {**raw, "css": raw["css"] + (" srg-val.srg-badge{display:none}" if hidden else "")})
                _, page, _ = report_spec.compile_report(spec, {"facts": facts}, 91, {})
                if hidden:
                    with self.assertRaisesRegex(ValueError, "不可见"):
                        report_browser.verify(page, spec, {"facts": facts}, 91)
                else:
                    self.assertEqual(report_browser.verify(page, spec, {"facts": facts}, 91)["status"], "passed")
