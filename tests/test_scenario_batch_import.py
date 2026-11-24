"""覆盖业务场景批量导入（Excel 模板/导出/预检/导入、JSON 批量接口、MCP 工具与权限边界）。"""
from __future__ import annotations

import io
import json
import unittest
import zipfile
from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import patch

import openpyxl
from fastapi.testclient import TestClient

from smartreportgen import api, auth, iam, mcp_server, repo, scenario_batch
from smartreportgen.auth import Principal
from smartreportgen.connectors.base import Connector
from smartreportgen.permission import Access


class _TypedConnector(Connector):
    type = "postgresql"

    def __init__(self):
        super().__init__({"id": 10, "name": "核心数仓", "type": "postgresql"}, "")
        self.tables_catalog = [
            {"schema": "public", "table": "sales_fact", "name": "public.sales_fact", "comment": "销售明细", "kind": "table"},
            {"schema": "public", "table": "dim_region", "name": "public.dim_region", "comment": "区域维表", "kind": "table"},
            {"schema": "public", "table": "secret_hr", "name": "public.secret_hr", "comment": "薪资表", "kind": "table"},
        ]
        self.cols = {
            "public.sales_fact": [
                {"name": "status", "source_name": "Status", "type": "bigint", "nullable": False, "comment": "状态"},
                {"name": "region", "source_name": "region", "type": "character varying", "nullable": True, "comment": "区域"},
                {"name": "code", "source_name": "code", "type": "varchar(20)", "nullable": True, "comment": "编码"},
                {"name": "active", "source_name": "active", "type": "boolean", "nullable": False, "comment": "有效"},
                {"name": "rate", "source_name": "rate", "type": "numeric(10,2)", "nullable": True, "comment": "比率"},
                {"name": "amount", "source_name": "amount", "type": "numeric(18,2)", "nullable": True, "comment": "金额"},
                {"name": "target", "source_name": "target", "type": "numeric(18,2)", "nullable": True, "comment": "目标"},
                {"name": "month", "source_name": "month", "type": "varchar(10)", "nullable": True, "comment": "月份"},
            ],
            "public.dim_region": [
                {"name": "region", "source_name": "region", "type": "varchar(32)", "nullable": False, "comment": "区域"},
            ],
        }

    def list_tables(self, schema=None):
        if schema:
            return [t for t in self.tables_catalog if t["schema"] == schema]
        return list(self.tables_catalog)

    def columns(self, table):
        return deepcopy(self.cols.get(table, []))


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakeStore:
    """内存元数据库模拟，覆盖 dataset、dataset_table、employee、table_grant。"""

    def __init__(self):
        self.datasources = [
            {
                "id": 10,
                "name": "核心数仓",
                "type": "postgresql",
                "config": {
                    "host": "db.local",
                    "database": "bi",
                    "scenario_tables": [
                        {
                            "table_name": "public.sales_fact",
                            "description": "销售明细",
                            "roles": ["analyst"],
                            "row_filters": [{"column": "region", "values": ["华东", "华南"]}],
                        },
                        {
                            "table_name": "public.dim_region",
                            "description": "区域维表",
                            "employees": ["E200"],
                        },
                    ],
                },
                "description": "主业务数仓",
                "enabled": True,
                "secret": None,
                "has_secret": False,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            },
            {
                "id": 11,
                "name": "财务专库",
                "type": "mysql",
                "config": {"host": "fin.local", "database": "fin", "scenario_tables": []},
                "description": "仅管理员",
                "enabled": True,
                "secret": None,
                "has_secret": False,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            },
        ]
        self.employees = {
            "ADMIN1": {"emp_no": "ADMIN1", "name": "管理员", "roles": ["admin"], "is_admin": True, "enabled": True},
            "E200": {"emp_no": "E200", "name": "张分析", "roles": ["analyst"], "is_admin": False, "enabled": True},
            "E201": {"emp_no": "E201", "name": "李运营", "roles": ["ops"], "is_admin": False, "enabled": True},
        }
        self.datasets: dict[int, dict] = {}
        self.tables: dict[int, list[dict]] = {}
        self.grants: list[dict] = []
        self._next_ds_id = 1
        self._next_table_id = 1
        self._next_grant_id = 1
        self.reindexed_batches: list[list[int]] = []

    def snapshot(self):
        return (deepcopy(self.datasets), deepcopy(self.tables), deepcopy(self.grants))

    def list_datasources(self):
        return [{k: v for k, v in d.items() if k != "secret"} for d in self.datasources]

    def get_datasource(self, ds_id: int, with_secret: bool = False):
        for d in self.datasources:
            if d["id"] == ds_id:
                return deepcopy(d) if with_secret else {k: v for k, v in d.items() if k != "secret"}
        raise repo.NotFound("数据源不存在")

    def list_datasets(self, q: str | None = None):
        out = []
        for ds_id in sorted(self.datasets):
            d = self.datasets[ds_id]
            if q and q.strip():
                term = q.strip().lower()
                if (term not in d["business_scenario"].lower()
                        and term not in d["business_description"].lower()
                        and not any(term in t.lower() for t in d["tags"])):
                    continue
            src = self.get_datasource(d["datasource_id"])
            out.append({
                "id": d["id"],
                "business_scenario": d["business_scenario"],
                "business_description": d["business_description"],
                "datasource_id": d["datasource_id"],
                "tags": list(d["tags"]),
                "enabled": d["enabled"],
                "owner_emp_no": d["owner_emp_no"],
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "vectorized": True,
                "datasource_name": src["name"],
                "datasource_type": src["type"],
                "table_count": len(self.tables.get(ds_id, [])),
            })
        return out

    def get_dataset(self, ds_id: int):
        d = self.datasets.get(ds_id)
        if not d:
            raise repo.NotFound("数据集不存在")
        src = self.get_datasource(d["datasource_id"])
        return {
            **deepcopy(d),
            "datasource_name": src["name"],
            "datasource_type": src["type"],
            "vectorized": True,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "tables": deepcopy(self.tables.get(ds_id, [])),
        }

    def fetchall(self, sql: str, params=None):
        if "FROM dataset.dataset" in sql and "dataset_table" not in sql:
            return [deepcopy(self.datasets[k]) for k in sorted(self.datasets)]
        if "FROM dataset.dataset_table" in sql:
            rows = []
            for ds_id in sorted(self.tables):
                for t in self.tables[ds_id]:
                    rows.append({**deepcopy(t), "dataset_id": ds_id})
            return rows
        if "FROM permission.employee" in sql:
            return [{"emp_no": k} for k in sorted(self.employees)]
        if "FROM permission.table_grant" in sql:
            ds_ids = set(params[0]) if params else set()
            return [deepcopy(g) for g in self.grants if g["dataset_id"] in ds_ids and g["table_name"] == "*"]
        return []

    def fetchone(self, sql: str, params=None):
        if "FROM permission.employee WHERE emp_no=%s" in sql:
            return deepcopy(self.employees.get(params[0]))
        return None

    def add_grants(self, data: dict):
        st, subj, ds_id, tables = data["subject_type"], data["subject"], int(data["dataset_id"]), data["tables"]
        added = 0
        for t in (["*"] if "*" in tables else tables):
            if any(g["subject_type"] == st and g["subject"] == subj and g["dataset_id"] == ds_id and g["table_name"] == t
                   for g in self.grants):
                continue
            self.grants.append({
                "id": self._next_grant_id,
                "subject_type": st,
                "subject": subj,
                "dataset_id": ds_id,
                "table_name": t,
            })
            self._next_grant_id += 1
            added += 1
        return {"added": added}

    @contextmanager
    def conn(self):
        store = self

        class _Tx:
            def __enter__(self_tx):
                self_tx._snap = store.snapshot()
                return self_tx

            def __exit__(self_tx, exc_type, exc, tb):
                if exc_type is not None:
                    store.datasets, store.tables, store.grants = self_tx._snap

        class _Conn:
            def transaction(self_c):
                return _Tx()

            def execute(self_c, sql: str, params=None):
                if sql.startswith("INSERT INTO dataset.dataset("):
                    sc, desc, app, ds_id, tags, enabled, metrics, owner = params
                    new_id = store._next_ds_id
                    store._next_ds_id += 1
                    store.datasets[new_id] = {
                        "id": new_id,
                        "business_scenario": sc,
                        "business_description": desc,
                        "analysis_approach": app,
                        "datasource_id": ds_id,
                        "tags": list(tags),
                        "enabled": enabled,
                        "semantic_metrics": deepcopy(metrics.obj if hasattr(metrics, "obj") else metrics),
                        "owner_emp_no": owner,
                    }
                    return _FakeCursor([{"id": new_id}])
                if sql.startswith("UPDATE dataset.dataset SET "):
                    sc, desc, app, ds_id, tags, enabled, metrics, target_id = params
                    cur = store.datasets[target_id]
                    cur.update({
                        "business_scenario": sc,
                        "business_description": desc,
                        "analysis_approach": app,
                        "datasource_id": ds_id,
                        "tags": list(tags),
                        "enabled": enabled,
                        "semantic_metrics": deepcopy(metrics.obj if hasattr(metrics, "obj") else metrics),
                    })
                    return _FakeCursor([])
                if sql.startswith("DELETE FROM dataset.dataset_table"):
                    store.tables[params[0]] = []
                    return _FakeCursor([])
                if sql.startswith("INSERT INTO dataset.dataset_table"):
                    ds_id, tname, tdesc, sort, rf = params
                    tid = store._next_table_id
                    store._next_table_id += 1
                    store.tables.setdefault(ds_id, []).append({
                        "id": tid,
                        "table_name": tname,
                        "table_description": tdesc,
                        "sort": sort,
                        "row_filters": deepcopy(rf.obj if hasattr(rf, "obj") else rf),
                    })
                    return _FakeCursor([])
                if sql.startswith("INSERT INTO permission.table_grant"):
                    st, subj, ds_id, tbls = params
                    inserted = []
                    for t in tbls:
                        if any(g["subject_type"] == st and g["subject"] == subj
                               and g["dataset_id"] == ds_id and g["table_name"] == t for g in store.grants):
                            continue
                        gid = store._next_grant_id
                        store._next_grant_id += 1
                        store.grants.append({
                            "id": gid, "subject_type": st, "subject": subj, "dataset_id": ds_id, "table_name": t,
                        })
                        inserted.append({"id": gid})
                    return _FakeCursor(inserted)
                return _FakeCursor([])

        yield _Conn()

    def save_tables(self, c, dataset_id: int, tables: list[dict]):
        c.execute("DELETE FROM dataset.dataset_table WHERE dataset_id=%s", (dataset_id,))
        for i, t in enumerate(tables):
            c.execute(
                "INSERT INTO dataset.dataset_table(dataset_id, table_name, table_description, sort, row_filters) "
                "VALUES (%s,%s,%s,%s,%s)",
                (dataset_id, t["table_name"], t.get("table_description") or "", i, t["row_filters"]),
            )


class ScenarioBatchImportTest(unittest.TestCase):
    def setUp(self):
        self.store = _FakeStore()
        self.connector = _TypedConnector()
        self.admin = Principal("ADMIN1", name="管理员", is_admin=True, registered=True, roles=["admin"])
        self.user = Principal("E200", name="张分析", is_admin=False, registered=True, roles=["analyst"])

        self.enterContext(patch.object(scenario_batch.repo, "list_datasources", side_effect=self.store.list_datasources))
        self.enterContext(patch.object(scenario_batch.repo, "get_datasource", side_effect=self.store.get_datasource))
        self.enterContext(patch.object(scenario_batch.repo, "list_datasets", side_effect=self.store.list_datasets))
        self.enterContext(patch.object(scenario_batch.repo, "get_dataset", side_effect=self.store.get_dataset))
        self.enterContext(patch.object(scenario_batch.repo, "_save_tables", side_effect=self.store.save_tables))
        self.enterContext(patch.object(
            scenario_batch.repo, "reindex_datasets", side_effect=lambda ids: self.store.reindexed_batches.append(list(ids))
        ))
        self.enterContext(patch.object(scenario_batch.services, "get_connector", return_value=self.connector))
        self.enterContext(patch.object(scenario_batch.db, "fetchall", side_effect=self.store.fetchall))
        self.enterContext(patch.object(scenario_batch.db, "conn", side_effect=self.store.conn))
        self.enterContext(patch.object(mcp_server.db, "fetchone", side_effect=self.store.fetchone))
        self.enterContext(patch.object(mcp_server.repo, "add_grants", side_effect=self.store.add_grants))

    def test_template_generation_admin_and_user(self):
        admin_wb = openpyxl.load_workbook(io.BytesIO(scenario_batch.build_template_xlsx(self.admin)))
        self.assertEqual(admin_wb.sheetnames, ["业务场景", "场景数据表", "官方指标", "填写说明"])
        for sheet_name in ("业务场景", "场景数据表", "官方指标"):
            self.assertEqual(admin_wb[sheet_name].max_row, 1)
        guide_text = "\n".join(str(cell) for row in admin_wb["填写说明"].iter_rows(values_only=True) for cell in row if cell)
        self.assertIn("核心数仓", guide_text)
        self.assertIn("财务专库", guide_text)
        admin_wb.close()

        user_wb = openpyxl.load_workbook(io.BytesIO(scenario_batch.build_template_xlsx(self.user)))
        user_guide = "\n".join(str(cell) for row in user_wb["填写说明"].iter_rows(values_only=True) for cell in row if cell)
        self.assertIn("核心数仓", user_guide)
        self.assertIn("public.sales_fact", user_guide)
        self.assertNotIn("财务专库", user_guide)
        user_wb.close()

    def test_shorthand_row_filters_and_column_type_coercion(self):
        rules = scenario_batch.parse_row_filters_cell(
            'Status=1; region=华东, NULL, "A,B"; code="001"; Status=2'
        )
        self.assertEqual(rules, [
            {"column": "Status", "values": ["1", "2"]},
            {"column": "region", "values": ["华东", None, "A,B"]},
            {"column": "code", "values": ["001"]},
        ])

        res = scenario_batch.batch_save_scenarios(
            [{
                "business_scenario": "类型感知测试",
                "business_description": "校验简明表达式按字段类型转换",
                "datasource_name": "核心数仓",
                "tables": [{
                    "table_name": "public.sales_fact",
                    "row_filters": 'Status=1,2; active=true; rate=0.75; code="001"; region=华东,NULL',
                }],
            }],
            self.admin,
        )
        self.assertTrue(res["ok"], res)
        saved = self.store.get_dataset(res["items"][0]["dataset_id"])
        rf = {r["column"]: r["values"] for r in saved["tables"][0]["row_filters"]}
        self.assertEqual(rf["Status"], [1, 2])
        self.assertEqual(rf["active"], [True])
        self.assertEqual(rf["rate"], [0.75])
        self.assertEqual(rf["code"], ["001"])
        self.assertEqual(rf["region"], ["华东", None])

    def test_export_parse_and_import_roundtrip(self):
        seed_res = scenario_batch.batch_save_scenarios(
            [{
                "business_scenario": "华东销售达成",
                "business_description": "跟踪华东区销售达成率",
                "analysis_approach": "先看月度趋势再下钻区域",
                "datasource_name": "核心数仓",
                "tags": ["销售", "达成"],
                "enabled": False,
                "grant_roles": ["analyst"],
                "grant_employees": ["E201"],
                "tables": [
                    {
                        "table_name": "public.sales_fact",
                        "table_description": "销售事实",
                        "row_filters": [{"column": "region", "values": ["华东", None]}, {"column": "Status", "values": [1]}],
                    },
                    {"table_name": "public.dim_region", "table_description": "区域维表", "row_filters": []},
                ],
                "semantic_metrics": {
                    "attainment": {
                        "table": "public.sales_fact",
                        "op": "ratio_of_sums",
                        "field": "amount",
                        "denominator": "target",
                        "dimensions": ["region", "month"],
                        "filter_dimensions": [],
                        "when": {"active": True},
                        "scale": "decimal",
                        "description": "达成率",
                    }
                },
            }],
            self.admin,
        )
        self.assertTrue(seed_res["ok"], seed_res)
        exported = scenario_batch.export_scenarios_xlsx(self.admin)

        parsed_items, wb_errors = scenario_batch.parse_scenarios_xlsx(exported)
        self.assertEqual(wb_errors, [])
        self.assertEqual(len(parsed_items), 1)
        self.assertFalse(parsed_items[0]["enabled"])
        self.assertEqual(parsed_items[0]["grant_roles"], ["analyst"])
        self.assertEqual(parsed_items[0]["grant_employees"], ["E201"])
        self.assertEqual(parsed_items[0]["semantic_metrics"]["attainment"]["filter_dimensions"], [])
        self.assertEqual(parsed_items[0]["semantic_metrics"]["attainment"]["when"], {"active": True})

        # 清空后从导出文件恢复，验证完整无损往返
        self.store.datasets.clear()
        self.store.tables.clear()
        self.store.grants.clear()

        imp = scenario_batch.import_scenarios_xlsx(exported, self.admin, mode="create_only", dry_run=False)
        self.assertTrue(imp["ok"], imp)
        self.assertEqual(imp["summary"]["create_count"], 1)
        self.assertEqual(imp["summary"]["grants_added"], 2)
        restored = self.store.get_dataset(imp["items"][0]["dataset_id"])
        self.assertEqual(restored["business_scenario"], "华东销售达成")
        self.assertFalse(restored["enabled"])
        self.assertEqual(len(restored["tables"]), 2)
        self.assertEqual(restored["tables"][0]["row_filters"], [
            {"column": "region", "values": ["华东", None]},
            {"column": "Status", "values": [1]},
        ])
        self.assertEqual(restored["semantic_metrics"]["attainment"]["when"], {"active": True})

    def test_permissions_validation_and_atomicity(self):
        # 管理员先创建一个共享场景
        admin_seed = scenario_batch.batch_save_scenarios(
            [{
                "business_scenario": "公司级销售看板",
                "business_description": "全公司销售数据",
                "datasource_name": "核心数仓",
                "tables": [{"table_name": "public.sales_fact"}],
            }],
            self.admin,
        )
        self.assertTrue(admin_seed["ok"])

        # 普通用户：不能覆盖共享场景、不能选未开放表、不能配置授权
        denied = scenario_batch.batch_save_scenarios(
            [
                {
                    "business_scenario": "公司级销售看板",
                    "business_description": "企图覆盖",
                    "datasource_name": "核心数仓",
                    "tables": [{"table_name": "public.sales_fact"}],
                },
                {
                    "business_scenario": "越权场景",
                    "business_description": "尝试引用薪资表和授权",
                    "datasource_name": "核心数仓",
                    "tables": [{"table_name": "public.secret_hr"}],
                    "grant_roles": ["ops"],
                },
            ],
            self.user,
            mode="upsert",
        )
        self.assertFalse(denied["ok"])
        self.assertEqual(len(denied["items"]), 2)
        self.assertIn("无权覆盖", " ".join(denied["items"][0]["errors"]))
        err_text_2 = " ".join(denied["items"][1]["errors"])
        self.assertIn("已授权开放的表", err_text_2)
        self.assertIn("仅管理员可配置", err_text_2)

        # 整批原子性：1 条合法 + 1 条错误时，0 条写入
        before_count = len(self.store.datasets)
        partial = scenario_batch.batch_save_scenarios(
            [
                {
                    "business_scenario": "合法个人场景",
                    "business_description": "有效场景",
                    "datasource_name": "核心数仓",
                    "tables": [{"table_name": "public.sales_fact"}],
                },
                {
                    "business_scenario": "合法个人场景",
                    "business_description": "同批重名",
                    "datasource_name": "核心数仓",
                    "tables": [{"table_name": "public.sales_fact"}],
                },
            ],
            self.user,
        )
        self.assertFalse(partial["ok"])
        self.assertEqual(len(self.store.datasets), before_count)

        # create_only 同名拦截与管理员无表告警
        dup = scenario_batch.batch_save_scenarios(
            [{
                "business_scenario": "公司级销售看板",
                "business_description": "重复新建",
                "datasource_name": "核心数仓",
                "tables": [{"table_name": "public.sales_fact"}],
            }],
            self.admin,
            mode="create_only",
        )
        self.assertFalse(dup["ok"])
        self.assertIn("已存在", dup["items"][0]["errors"][0])

        warn_only = scenario_batch.batch_save_scenarios(
            [{
                "business_scenario": "仅框架场景",
                "business_description": "先建说明，稍后补表",
                "datasource_name": "核心数仓",
                "tables": [],
            }],
            self.admin,
            dry_run=True,
        )
        self.assertTrue(warn_only["ok"])
        self.assertEqual(warn_only["summary"]["warning_count"], 1)
        self.assertIn("未配置关联数据表", warn_only["items"][0]["warnings"][0])

    def test_excel_orphan_rows_missing_metrics_sheet_and_bad_files(self):
        # 1. 孤立子表行阻止写入
        wb = openpyxl.Workbook()
        ws_s = wb.active
        ws_s.title = "业务场景"
        ws_s.append(["业务场景*", "数据源*", "业务描述*"])
        ws_s.append(["有效场景", "核心数仓", "描述"])
        ws_t = wb.create_sheet("场景数据表")
        ws_t.append(["业务场景*", "表名*"])
        ws_t.append(["有效场景", "public.sales_fact"])
        ws_t.append(["不存在的场景", "public.sales_fact"])
        buf = io.BytesIO()
        wb.save(buf)
        res = scenario_batch.import_scenarios_xlsx(buf.getvalue(), self.admin, dry_run=False)
        self.assertFalse(res["ok"])
        self.assertEqual(len(self.store.datasets), 0)
        self.assertTrue(any("不存在的场景" in e["message"] for e in res["errors"]))

        # 2. 不含「官方指标」Sheet 的 Excel 更新时，保留原场景的官方指标
        scenario_batch.batch_save_scenarios(
            [{
                "business_scenario": "保留指标场景",
                "business_description": "原始描述",
                "datasource_name": "核心数仓",
                "tables": [{"table_name": "public.sales_fact"}],
                "semantic_metrics": {
                    "total_amount": {
                        "table": "public.sales_fact", "op": "sum", "field": "amount",
                        "dimensions": ["region"], "scale": "number", "description": "总金额",
                    }
                },
            }],
            self.admin,
        )
        wb2 = openpyxl.Workbook()
        ws2 = wb2.active
        ws2.title = "业务场景"
        ws2.append(["业务场景*", "数据源*", "业务描述*"])
        ws2.append(["保留指标场景", "核心数仓", "更新后的描述"])
        buf2 = io.BytesIO()
        wb2.save(buf2)
        upd = scenario_batch.import_scenarios_xlsx(buf2.getvalue(), self.admin, mode="upsert", dry_run=False)
        self.assertTrue(upd["ok"], upd)
        cur = self.store.get_dataset(upd["items"][0]["dataset_id"])
        self.assertEqual(cur["business_description"], "更新后的描述")
        self.assertIn("total_amount", cur["semantic_metrics"])
        self.assertEqual(len(cur["tables"]), 1)

        # 3. 非法文件与压缩炸弹防护
        with self.assertRaises(repo.Invalid):
            scenario_batch.import_scenarios_xlsx(b"not-an-xlsx", self.admin)
        with patch.object(scenario_batch, "MAX_UNCOMPRESSED_ZIP_BYTES", 10):
            zbuf = io.BytesIO()
            with zipfile.ZipFile(zbuf, "w") as zf:
                zf.writestr("xl/workbook.xml", "x" * 100)
            with self.assertRaises(repo.Invalid):
                scenario_batch.import_scenarios_xlsx(zbuf.getvalue(), self.admin)

    def test_fastapi_routes_and_user_allow(self):
        def _fake_verify(token):
            if token == "Bearer admin-tok":
                return self.admin
            if token == "Bearer user-tok":
                return self.user
            raise auth.AuthError("无效令牌")

        with patch.object(api.auth, "verify_bearer", side_effect=_fake_verify), \
                patch.object(iam, "configured", return_value=True):
            client = TestClient(api.app)

            # 普通用户通过 USER_ALLOW 下载模板、导出与批量导入
            tpl_resp = client.get("/api/datasets/template", headers={"Authorization": "Bearer user-tok"})
            self.assertEqual(tpl_resp.status_code, 200)
            self.assertIn("spreadsheetml.sheet", tpl_resp.headers["content-type"])

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "业务场景"
            ws.append(["业务场景*", "数据源*", "业务描述*"])
            ws.append(["个人经营场景", "核心数仓", "普通员工导入的场景"])
            ws_t = wb.create_sheet("场景数据表")
            ws_t.append(["业务场景*", "表名*", "行过滤条件"])
            ws_t.append(["个人经营场景", "public.sales_fact", "Status=1"])
            xbuf = io.BytesIO()
            wb.save(xbuf)
            xlsx_bytes = xbuf.getvalue()

            dry = client.post(
                "/api/datasets/import?dry_run=true",
                headers={"Authorization": "Bearer user-tok"},
                files={"file": ("scenes.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            )
            self.assertEqual(dry.status_code, 200, dry.text)
            self.assertTrue(dry.json()["ok"])
            self.assertEqual(len(self.store.datasets), 0)

            imp = client.post(
                "/api/datasets/import?dry_run=false",
                headers={"Authorization": "Bearer user-tok"},
                files={"file": ("scenes.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            )
            self.assertEqual(imp.status_code, 200, imp.text)
            self.assertEqual(imp.json()["summary"]["create_count"], 1)

            exp = client.get("/api/datasets/export", headers={"Authorization": "Bearer user-tok"})
            self.assertEqual(exp.status_code, 200)
            self.assertEqual(client.get("/api/datasets/export?ids=bad", headers={"Authorization": "Bearer user-tok"}).status_code, 400)

            # 管理员通过 /api/datasets/batch 接口批量创建与错误返回 400
            batch_ok = client.post(
                "/api/datasets/batch",
                headers={"Authorization": "Bearer admin-tok"},
                json={
                    "mode": "upsert",
                    "scenarios": [{
                        "business_scenario": "接口批量场景",
                        "business_description": "来自 JSON batch 接口",
                        "datasource_name": "核心数仓",
                        "tables": [{"table_name": "public.dim_region"}],
                        "grant_roles": ["analyst"],
                    }],
                },
            )
            self.assertEqual(batch_ok.status_code, 200, batch_ok.text)
            self.assertEqual(batch_ok.json()["summary"]["grants_added"], 1)

            batch_bad = client.post(
                "/api/datasets/batch",
                headers={"Authorization": "Bearer user-tok"},
                json={"scenarios": [{"business_scenario": "缺数据源", "business_description": "x"}]},
            )
            self.assertEqual(batch_bad.status_code, 400)
            self.assertFalse(batch_bad.json()["ok"])

    def test_mcp_tools_and_batch_reindex(self):
        admin_acc = Access(self.admin, all=True)
        user_acc = Access(self.user, all=False, tables={})

        # list_datasource_tables: 管理员 vs 普通用户
        with patch.object(mcp_server, "_access", return_value=admin_acc):
            adm_tbls = mcp_server.list_datasource_tables(
                10, None, keyword="sales", include_columns=True
            )
        self.assertTrue(adm_tbls["ok"], adm_tbls)
        self.assertEqual([t["table_name"] for t in adm_tbls["tables"]], ["public.sales_fact"])
        self.assertTrue(any(c["source_name"] == "Status" for c in adm_tbls["tables"][0]["columns"]))

        with patch.object(mcp_server, "_access", return_value=user_acc):
            usr_tbls = mcp_server.list_datasource_tables(10, None, include_columns=True)
            self.assertTrue(usr_tbls["ok"], usr_tbls)
            self.assertEqual([t["table_name"] for t in usr_tbls["tables"]], ["public.sales_fact", "public.dim_region"])
            self.assertEqual(
                mcp_server.list_datasource_tables(10, None, table_names=["public.secret_hr"])["error_type"],
                "permission_denied",
            )
            self.assertEqual(mcp_server.list_datasource_tables(11, None)["error_type"], "permission_denied")

        role_only_acc = Access(Principal("E202", registered=True, roles=["analyst"]), all=False, tables={})
        with patch.object(mcp_server, "_access", return_value=role_only_acc):
            role_tbls = mcp_server.list_datasource_tables(10, None)
            self.assertTrue(role_tbls["ok"], role_tbls)
            self.assertEqual([t["table_name"] for t in role_tbls["tables"]], ["public.sales_fact"])

        # save_business_scenario 附带授权
        with patch.object(mcp_server, "_access", return_value=admin_acc), \
                patch.object(mcp_server.repo, "create_dataset", side_effect=lambda payload, p: {
                    "id": 88, **payload, "datasource_name": "核心数仓", "datasource_type": "postgresql",
                    "tables": [{"table_name": "public.sales_fact", "table_description": "", "row_filters": []}],
                }), \
                patch.object(mcp_server, "access_for", return_value=admin_acc):
            saved = mcp_server.save_business_scenario(
                "MCP单条授权场景", "描述", 10, [{"table_name": "public.sales_fact"}], None,
                grant_roles=["analyst"], grant_employees=["E200"],
            )
            self.assertTrue(saved["ok"], saved)
            self.assertEqual(saved["grants_added"], 2)

            unreg = mcp_server.save_business_scenario(
                "未登记员工", "描述", 10, [{"table_name": "public.sales_fact"}], None,
                grant_employees=["NO_SUCH_EMP"],
            )
            self.assertFalse(unreg["ok"])

        with patch.object(mcp_server, "_access", return_value=user_acc):
            denied = mcp_server.save_business_scenario(
                "普通用户授权越权", "描述", 10, [{"table_name": "public.sales_fact"}], None,
                grant_roles=["analyst"],
            )
            self.assertEqual(denied["error_type"], "permission_denied")

        # batch_save_business_scenarios
        with patch.object(mcp_server, "_access", return_value=admin_acc):
            mcp_batch = mcp_server.batch_save_business_scenarios(
                [{
                    "business_scenario": "对话批量场景",
                    "business_description": "来自 MCP 对话批量导入",
                    "datasource_name": "核心数仓",
                    "tables": [{"table_name": "public.sales_fact", "row_filters": "Status=1"}],
                    "grant_roles": ["analyst"],
                }],
                None,
            )
            self.assertTrue(mcp_batch["ok"], mcp_batch)
            self.assertEqual(mcp_batch["summary"]["create_count"], 1)

            mcp_fail = mcp_server.batch_save_business_scenarios(
                [{"business_scenario": "缺字段", "business_description": ""}],
                None,
                dry_run=False,
            )
            self.assertFalse(mcp_fail["ok"])
            self.assertEqual(mcp_fail["error_type"], "validation_failed")

class ReindexDatasetsOrderTest(unittest.TestCase):
    def test_reindex_datasets_adds_words_before_tokenizing(self):
        ds_rows = [
            {
                "id": 1,
                "business_scenario": "独创专有超长场景词甲",
                "business_description": "观察独创专有超长场景词甲的走势",
                "analysis_approach": "",
                "tags": ["独创标签词乙"],
                "semantic_metrics": {"custom_metric_丙": {"table": "t1", "description": "自定义指标"}},
            }
        ]
        table_rows = [{"dataset_id": 1, "table_name": "t1", "table_description": "表一", "sort": 0}]
        calls: list[str] = []

        def _fake_fetchall(sql, params=None):
            if "FROM dataset.dataset WHERE" in sql:
                return ds_rows
            if "FROM dataset.dataset_table WHERE" in sql:
                return table_rows
            return []

        def _fake_add_words(words):
            calls.append("add_words")
            self.assertIn("独创专有超长场景词甲", words)
            self.assertIn("独创标签词乙", words)
            self.assertIn("custom_metric_丙", words)

        def _fake_index(ds, tables):
            calls.append("dataset_index")
            return {"kw_a": ["a"], "kw_b": ["b"], "kw_c": ["c"], "kw_all": ["a", "b", "c"], "text": "idx"}

        updates = []

        @contextmanager
        def _fake_conn():
            class _Tx:
                def __enter__(self): return self
                def __exit__(self, *args): pass

            class _C:
                def transaction(self): return _Tx()
                def execute(self, sql, params=None): updates.append((sql, params))

            yield _C()

        with patch.object(repo.db, "fetchall", side_effect=_fake_fetchall), \
                patch.object(repo, "add_words", side_effect=_fake_add_words), \
                patch.object(repo, "_dataset_index", side_effect=_fake_index), \
                patch.object(repo, "embed_many", return_value=[[0.1, 0.2]]) as embed_mock, \
                patch.object(repo.db, "conn", side_effect=_fake_conn):
            repo.reindex_datasets([1, 1])

        self.assertEqual(calls, ["add_words", "dataset_index"])
        embed_mock.assert_called_once_with(["idx"])
        self.assertEqual(len(updates), 1)


if __name__ == "__main__":
    unittest.main()
