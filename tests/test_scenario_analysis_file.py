"""业务场景 .md 分析思路文件上传、预览、下载、删除及 MCP 上下文透出测试。"""
from __future__ import annotations

import copy
import unittest
from contextlib import contextmanager, nullcontext
from unittest.mock import patch

from fastapi.testclient import TestClient

from smartreportgen import api, auth, iam, mcp_server, repo
from smartreportgen.auth import Principal
from smartreportgen.permission import Access


class _SceneStore:
    def __init__(self):
        self.datasets: dict[int, dict] = {
            1: {
                "id": 1,
                "business_scenario": "华东零售分析",
                "business_description": "零售订单销售与毛利复盘",
                "analysis_approach": "先看总量再分渠道",
                "analysis_file_name": None,
                "analysis_file_markdown": None,
                "analysis_file_updated_at": None,
                "datasource_id": 10,
                "datasource_name": "核心数仓",
                "datasource_type": "postgresql",
                "tags": ["零售"],
                "enabled": True,
                "owner_emp_no": "E200",
                "semantic_metrics": {},
                "tables": [
                    {
                        "id": 101,
                        "table_name": "public.sales_fact",
                        "table_description": "销售明细",
                        "sort": 0,
                        "row_filters": [],
                    }
                ],
                "created_at": "2026-11-25T00:00:00+00:00",
                "updated_at": "2026-11-25T00:00:00+00:00",
                "vectorized": True,
            },
            2: {
                "id": 2,
                "business_scenario": "公司级经营看板",
                "business_description": "全公司经营指标汇总",
                "analysis_approach": "全局指标汇总",
                "analysis_file_name": "company_guide.md",
                "analysis_file_markdown": "# 经营分析思路\n\n- 先核对口径\n",
                "analysis_file_updated_at": "2026-11-25T01:00:00+00:00",
                "datasource_id": 10,
                "datasource_name": "核心数仓",
                "datasource_type": "postgresql",
                "tags": ["经营"],
                "enabled": True,
                "owner_emp_no": None,
                "semantic_metrics": {},
                "tables": [
                    {
                        "id": 102,
                        "table_name": "public.sales_fact",
                        "table_description": "销售明细",
                        "sort": 0,
                        "row_filters": [],
                    }
                ],
                "created_at": "2026-11-25T00:00:00+00:00",
                "updated_at": "2026-11-25T00:00:00+00:00",
                "vectorized": True,
            },
        }
        self.reindexed: list[int] = []

    def get_dataset(self, dataset_id: int) -> dict:
        if dataset_id not in self.datasets:
            raise repo.NotFound("数据集不存在")
        return copy.deepcopy(self.datasets[dataset_id])

    def execute(self, sql: str, params=None) -> None:
        if "SET analysis_file_name=%s" in sql:
            name, md, ds_id = params
            ds = self.datasets[ds_id]
            ds["analysis_file_name"] = name
            ds["analysis_file_markdown"] = md
            ds["analysis_file_updated_at"] = "2026-11-25T12:00:00+00:00"
        elif "SET analysis_file_name=NULL" in sql:
            (ds_id,) = params
            ds = self.datasets[ds_id]
            ds["analysis_file_name"] = None
            ds["analysis_file_markdown"] = None
            ds["analysis_file_updated_at"] = None

    def reindex_dataset(self, dataset_id: int) -> None:
        self.reindexed.append(dataset_id)


class ScenarioAnalysisFileTest(unittest.TestCase):
    def setUp(self):
        self.store = _SceneStore()
        self.admin = Principal("ADMIN1", name="管理员", is_admin=True, registered=True, roles=["admin"])
        self.user = Principal("E200", name="张分析", is_admin=False, registered=True, roles=["analyst"])
        self.other = Principal("E201", name="李审计", is_admin=False, registered=True, roles=["analyst"])

        self.enterContext(patch.object(repo, "get_dataset", side_effect=self.store.get_dataset))
        self.enterContext(patch.object(repo.db, "execute", side_effect=self.store.execute))
        self.enterContext(patch.object(repo, "reindex_dataset", side_effect=self.store.reindex_dataset))
        # 文件更新和权限变更共用场景行锁，测试用内存记录模拟行锁与提交。
        class Connection:
            transaction = staticmethod(nullcontext)

            def execute(conn, sql, params):
                if sql.startswith("SELECT id, owner_emp_no"):
                    ds = self.store.get_dataset(params[0])
                    return type("Row", (), {"fetchone": lambda _: ds})()
                self.store.execute(sql, params)

        @contextmanager
        def fake_conn():
            yield Connection()

        self.enterContext(patch.object(repo.db, "conn", side_effect=fake_conn))
        self.enterContext(patch.object(api.permission, "access_for", side_effect=lambda p, **_: Access(p, all=p.is_admin)))

    def test_repo_validation_replace_and_delete(self):
        # UTF-8 BOM、Windows 换行符和目录前缀都会被规范化
        saved = repo.save_dataset_analysis_file(
            1,
            r"C:\docs\华东思路.MD",
            "\ufeff# 第一步\r\n先看GMV\r\n".encode("utf-8"),
            self.user,
        )
        self.assertEqual(saved["analysis_file_name"], "华东思路.MD")
        self.assertEqual(saved["analysis_file_markdown"], "# 第一步\n先看GMV\n")
        self.assertEqual(self.store.reindexed, [1])

        # 重新上传直接替换原文件（每场景最多绑定 1 份）
        replaced = repo.save_dataset_analysis_file(1, "/tmp/新版思路.md", "## 新版\n- 按渠道拆解\n", self.user)
        self.assertEqual(replaced["analysis_file_name"], "新版思路.md")
        self.assertEqual(replaced["analysis_file_markdown"], "## 新版\n- 按渠道拆解\n")
        self.assertEqual(self.store.reindexed, [1, 1])

        # 管理员可操作任意场景；其他非本人用户无权修改或删除
        with self.assertRaises(repo.PermissionDenied):
            repo.save_dataset_analysis_file(1, "other.md", "# hijack", self.other)
        with self.assertRaises(repo.PermissionDenied):
            repo.save_dataset_analysis_file(2, "shared.md", "# hijack", self.user)
        with self.assertRaises(repo.PermissionDenied):
            repo.delete_dataset_analysis_file(2, self.user)

        # 异常格式拦截
        for bad_name, bad_body in (
            ("notes.txt", b"# text"),
            ("data.xlsx", b"# xlsx"),
            (".md", b"# empty stem"),
            ("bad\x00.md", b"# null"),
            ("empty.md", b"   \n\t  "),
            ("nullbyte.md", b"# ok\x00bad"),
            ("gbk.md", "中文".encode("gbk")),
            ("big.md", b"a" * (repo.MAX_ANALYSIS_FILE_BYTES + 1)),
        ):
            with self.assertRaises(repo.Invalid, msg=bad_name):
                repo.save_dataset_analysis_file(1, bad_name, bad_body, self.user)

        # 删除清空并触发重建索引
        cleared = repo.delete_dataset_analysis_file(1, self.user)
        self.assertIsNone(cleared["analysis_file_name"])
        self.assertIsNone(cleared["analysis_file_markdown"])
        self.assertIsNone(cleared["analysis_file_updated_at"])
        self.assertEqual(self.store.reindexed, [1, 1, 1])

    def test_dataset_index_includes_analysis_file(self):
        ds = self.store.get_dataset(2)
        idx = repo._dataset_index(ds, ds["tables"])
        self.assertIn("经营", idx["kw_b"])
        self.assertIn("口径", idx["kw_b"])
        self.assertIn("分析思路文件（company_guide.md）：", idx["text"])
        self.assertIn("# 经营分析思路", idx["text"])

    def test_fastapi_routes_and_permissions(self):
        def _fake_verify(token):
            return {
                "Bearer admin-tok": self.admin,
                "Bearer user-tok": self.user,
                "Bearer other-tok": self.other,
            }.get(token) or (_ for _ in ()).throw(auth.AuthError("无效令牌"))

        def _fake_access(principal, **_):
            if principal.is_admin:
                return Access(principal, all=True)
            if principal.emp_no == "E200":
                return Access(principal, all=False, tables={1: {"public.sales_fact"}, 2: {"public.sales_fact"}})
            return Access(principal, all=False, tables={})

        datasource = {
            "id": 10,
            "name": "核心数仓",
            "type": "postgresql",
            "enabled": True,
            "config": {"scenario_tables": [{"table_name": "public.sales_fact", "roles": ["analyst"]}]},
        }

        with patch.object(api.auth, "verify_bearer", side_effect=_fake_verify), \
                patch.object(iam, "configured", return_value=True), \
                patch.object(api.permission, "access_for", side_effect=_fake_access), \
                patch.object(api.repo, "get_datasource", return_value=datasource):
            client = TestClient(api.app)

            # 普通用户上传自己创建的场景 1 的 .md 文件
            put_resp = client.put(
                "/api/datasets/1/analysis-file",
                headers={"Authorization": "Bearer user-tok"},
                files={"file": ("retail_logic.md", "# 零售思路\n\n1. 先看毛利\n".encode("utf-8"), "text/markdown")},
            )
            self.assertEqual(put_resp.status_code, 200, put_resp.text)
            self.assertEqual(put_resp.json()["analysis_file_name"], "retail_logic.md")
            self.assertIn("# 零售思路", put_resp.json()["analysis_file_markdown"])

            # 非法的非 .md 文件返回 400
            bad_ext = client.put(
                "/api/datasets/1/analysis-file",
                headers={"Authorization": "Bearer user-tok"},
                files={"file": ("retail_logic.txt", b"hello", "text/plain")},
            )
            self.assertEqual(bad_ext.status_code, 400)

            # 本人读取与下载
            get_resp = client.get("/api/datasets/1/analysis-file", headers={"Authorization": "Bearer user-tok"})
            self.assertEqual(get_resp.status_code, 200, get_resp.text)
            self.assertFalse(get_resp.json()["read_only"])
            self.assertEqual(get_resp.json()["analysis_file_name"], "retail_logic.md")

            dl_resp = client.get("/api/datasets/1/analysis-file?download=true", headers={"Authorization": "Bearer user-tok"})
            self.assertEqual(dl_resp.status_code, 200)
            self.assertIn("text/markdown", dl_resp.headers["content-type"])
            self.assertIn("retail_logic.md", dl_resp.headers["content-disposition"])
            self.assertEqual(dl_resp.text, "# 零售思路\n\n1. 先看毛利\n")

            # 普通用户有场景 2 读权限但非创建人：可预览下载，不可修改或删除
            ro_get = client.get("/api/datasets/2/analysis-file", headers={"Authorization": "Bearer user-tok"})
            self.assertEqual(ro_get.status_code, 200)
            self.assertTrue(ro_get.json()["read_only"])
            ro_put = client.put(
                "/api/datasets/2/analysis-file",
                headers={"Authorization": "Bearer user-tok"},
                files={"file": ("hack.md", b"# x", "text/markdown")},
            )
            self.assertEqual(ro_put.status_code, 403)
            ro_del = client.delete("/api/datasets/2/analysis-file", headers={"Authorization": "Bearer user-tok"})
            self.assertEqual(ro_del.status_code, 403)

            # 无场景权限的第三方用户读场景 1 返回 404
            no_perm_get = client.get("/api/datasets/1/analysis-file", headers={"Authorization": "Bearer other-tok"})
            self.assertEqual(no_perm_get.status_code, 404)

            # 本人删除成功，再下载返回 404
            del_resp = client.delete("/api/datasets/1/analysis-file", headers={"Authorization": "Bearer user-tok"})
            self.assertEqual(del_resp.status_code, 200)
            self.assertTrue(del_resp.json()["ok"])
            dl_missing = client.get("/api/datasets/1/analysis-file?download=true", headers={"Authorization": "Bearer user-tok"})
            self.assertEqual(dl_missing.status_code, 404)

            # 管理员可替换共享场景 2 的文件
            adm_put = client.put(
                "/api/datasets/2/analysis-file",
                headers={"Authorization": "Bearer admin-tok"},
                files={"file": ("board_v2.md", b"# v2", "text/markdown")},
            )
            self.assertEqual(adm_put.status_code, 200)
            self.assertEqual(adm_put.json()["analysis_file_name"], "board_v2.md")

    def test_mcp_exposes_analysis_file(self):
        acc = Access(self.user, all=False, tables={1: {"public.sales_fact"}, 2: {"public.sales_fact"}})
        with patch.object(mcp_server, "_access", return_value=acc):
            without_file = mcp_server.get_dataset_tables(1, None)
            self.assertTrue(without_file["ok"])
            self.assertNotIn("analysis_file_name", without_file)
            self.assertNotIn("analysis_file_markdown", without_file)

            with_file = mcp_server.get_dataset_tables(2, None)
            self.assertTrue(with_file["ok"])
            self.assertEqual(with_file["analysis_file_name"], "company_guide.md")
            self.assertEqual(with_file["analysis_file_markdown"], "# 经营分析思路\n\n- 先核对口径\n")


if __name__ == "__main__":
    unittest.main()
