"""验证内置报告规则可发现、与视觉覆盖隔离，并能通过 MCP 读取配套参考。"""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from smartreportgen import api, design, mcp_server, repo
from smartreportgen.auth import Principal


class BuiltinReportingRulesTest(unittest.TestCase):
    def setUp(self):
        # 禁止读取元数据库：用虚构的规则层验证真实的合并与对外接口。
        self.layers = self.enterContext(patch.object(repo, "_rule_layers", return_value=([], [])))
        self.enterContext(patch.object(mcp_server, "_principal", return_value=Principal("rule-reader")))

    def test_builtin_rules_available_without_configured_rules(self):
        result = repo.style_rules_for(None)
        self.assertEqual(result["general_rules"], [])
        self.assertEqual(result["scenario_rules"], [])
        self.assertEqual(result["specified"]["items"], [])
        self.assertEqual(result["reporting_rules"], design.builtin_reporting_rules())
        ids = [rule["id"] for rule in result["reporting_rules"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue({"direct_facts", "missing_facts", "standalone_html"}.issubset(ids))

    def test_visual_overrides_do_not_replace_reporting_rules(self):
        configured = {"id": 1, "name": "测试样式", "scope": "scenario", "items": [
            {"category": "number", "text": "显示两位小数"},
        ], "theme": {"color-brand": "#123456"}}
        self.layers.return_value = ([configured], [("scenario", configured["name"], configured["theme"])])
        result = mcp_server.get_style_rules("测试场景", None)
        self.assertTrue(result["ok"])
        self.assertEqual(result["specified"]["theme"]["color-brand"], "#123456")
        self.assertEqual(result["specified"]["items"][0]["source"], "业务场景规则「测试样式」")
        self.assertEqual(result["reporting_rules"], design.builtin_reporting_rules())
        # 对外返回值可修改，但不能污染下次请求或内置常量。
        result["reporting_rules"][0]["text"] = "已改动"
        self.assertNotEqual(result["reporting_rules"], design.builtin_reporting_rules())

    def test_http_metadata_and_mcp_expose_same_readonly_rules(self):
        metadata = json.loads(api.style_tokens().body)
        result = mcp_server.get_style_rules("任意业务场景", None)
        self.assertEqual(metadata["reporting_rules"], result["reporting_rules"])
        self.assertTrue(all(r["level"] == "builtin" for r in metadata["reporting_rules"]))

    def test_mcp_guide_can_read_maintained_references(self):
        # 容器使用镜像内的技能；本地运行时定位仓库唯一来源，禁止依赖私人下载目录。
        skill_dir = mcp_server.SKILL_DIR
        if not skill_dir.exists():
            skill_dir = Path(__file__).resolve().parents[1] / "skills" / "smart-report"
        with patch.object(mcp_server, "SKILL_DIR", skill_dir), \
                patch.object(mcp_server, "SKILL_FILE", skill_dir / "SKILL.md"):
            body = mcp_server.get_skill_guide()
            for reference in ("fact-contract", "green-energy-september-facts"):
                with self.subTest(reference=reference):
                    contents = (skill_dir / "references" / f"{reference}.md").read_text(encoding="utf-8")
                    self.assertEqual(mcp_server.get_skill_guide(reference=reference), body + "\n\n---\n\n" + contents)
            with self.assertRaises(ValueError):
                mcp_server.get_skill_guide(reference="../../config")


if __name__ == "__main__":
    unittest.main()
