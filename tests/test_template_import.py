"""报告模板一键导入（关联业务场景）、批量与版本规则及 .md 导出测试。"""
from __future__ import annotations

import copy
import json
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from fastapi.testclient import TestClient

from smartreportgen import api, auth, iam, repo
from smartreportgen.auth import Principal
from smartreportgen.permission import Access


class _TemplateImportStore:
    def __init__(self):
        self.datasets: dict[int, dict] = {
            1: {
                "id": 1,
                "business_scenario": "华东零售分析",
                "enabled": True,
                "owner_emp_no": "E200",
            },
            2: {
                "id": 2,
                "business_scenario": "集团财务总览",
                "enabled": True,
                "owner_emp_no": None,
            },
        }
        self.templates: dict[int, dict] = {}
        self.versions: dict[tuple[int, int], dict] = {}
        self.next_id = 10

    def _template_row(self, tpl_id: int) -> dict:
        row = copy.deepcopy(self.templates[tpl_id])
        ds = self.datasets.get(row["dataset_id"])
        row["business_scenario"] = ds["business_scenario"] if ds else None
        row["creator_name"] = {"E200": "张分析", "E201": "李审计", "ADMIN1": "管理员"}.get(row["created_by"], "")
        return row


class _FakeConn:
    def __init__(self, store: _TemplateImportStore):
        self.store = store
        self._snapshot = None

    @contextmanager
    def transaction(self):
        self._snapshot = (
            copy.deepcopy(self.store.templates),
            copy.deepcopy(self.store.versions),
            self.store.next_id,
        )
        try:
            yield self
        except Exception:
            self.store.templates, self.store.versions, self.store.next_id = self._snapshot
            raise

    def execute(self, sql: str, params=()):
        return _FakeCursor(self.store, sql, params)


class _FakeCursor:
    def __init__(self, store: _TemplateImportStore, sql: str, params=()):
        self.store = store
        self.rows: list[dict] = []
        self._run(sql, params)

    def _run(self, sql: str, params):
        norm = " ".join(sql.split())
        if norm.startswith("SELECT id, business_scenario") and "FROM dataset.dataset WHERE id=%s" in norm:
            ds_id = int(params[0])
            ds = self.store.datasets.get(ds_id)
            if ds:
                self.rows = [copy.deepcopy(ds)]
            return

        if "FROM report_template.report_template WHERE dataset_id=%s AND name=%s AND created_by=%s" in norm:
            dataset_id, name, created_by = params
            matches = [
                t for t in self.store.templates.values()
                if t["dataset_id"] == dataset_id and t["name"] == name and t["created_by"] == created_by
            ]
            matches.sort(key=lambda r: (r["updated_at"], r["id"]), reverse=True)
            if matches:
                self.rows = [{"id": matches[0]["id"]}]
            return

        if norm.startswith("SELECT * FROM report_template.report_template WHERE id=%s"):
            tpl_id = int(params[0])
            tpl = self.store.templates.get(tpl_id)
            if tpl:
                self.rows = [copy.deepcopy(tpl)]
            return

        if norm.startswith("INSERT INTO report_template.report_template("):
            name, dataset_id, user_req, goal, summary_txt, md, summary_json, created_by, tpl_type = params
            tpl_id = self.store.next_id
            self.store.next_id += 1
            summary_obj = copy.deepcopy(summary_json.obj) if hasattr(summary_json, "obj") else copy.deepcopy(summary_json)
            self.store.templates[tpl_id] = {
                "id": tpl_id,
                "name": name,
                "dataset_id": dataset_id,
                "user_requirement": user_req,
                "analysis_goal": goal,
                "business_summary": summary_txt,
                "markdown": md,
                "summary": summary_obj,
                "version": 1,
                "created_by": created_by,
                "template_type": tpl_type,
                "created_at": "2026-11-26T10:00:00+00:00",
                "updated_at": f"2026-11-26T10:00:{tpl_id:02d}+00:00",
            }
            self.rows = [{"id": tpl_id}]
            return

        if norm.startswith("UPDATE report_template.report_template SET"):
            name, dataset_id, user_req, goal, summary_txt, md, summary_json, version, tpl_id = params
            tpl = self.store.templates[int(tpl_id)]
            summary_obj = copy.deepcopy(summary_json.obj) if hasattr(summary_json, "obj") else copy.deepcopy(summary_json)
            tpl.update({
                "name": name,
                "dataset_id": dataset_id,
                "user_requirement": user_req,
                "analysis_goal": goal,
                "business_summary": summary_txt,
                "markdown": md,
                "summary": summary_obj,
                "version": version,
                "template_type": "business",
                "updated_at": f"2026-11-26T11:{version:02d}:00+00:00",
            })
            return

        if norm.startswith("INSERT INTO report_template.report_template_version"):
            tpl_id, version, md = params
            key = (int(tpl_id), int(version))
            if key not in self.store.versions:
                self.store.versions[key] = {
                    "template_id": int(tpl_id),
                    "version": int(version),
                    "markdown": md,
                    "created_at": f"2026-11-26T11:{int(version):02d}:00+00:00",
                }
            return

        if "FROM report_template.report_template t LEFT JOIN dataset.dataset d" in norm:
            tpl_id = int(params[0])
            if tpl_id in self.store.templates:
                self.rows = [self.store._template_row(tpl_id)]
            return

        raise AssertionError(f"未模拟的 SQL: {norm}")

    def fetchone(self):
        return self.rows[0] if self.rows else None


class TemplateImportTest(unittest.TestCase):
    def setUp(self):
        self.store = _TemplateImportStore()
        self.admin = Principal("ADMIN1", name="管理员", is_admin=True, registered=True, roles=["admin"])
        self.user = Principal("E200", name="张分析", is_admin=False, registered=True, roles=["analyst"])
        self.other = Principal("E201", name="李审计", is_admin=False, registered=True, roles=["analyst"])

        @contextmanager
        def _fake_conn():
            yield _FakeConn(self.store)

        def _fake_fetchone(sql: str, params=()):
            return _FakeCursor(self.store, sql, params).fetchone()

        def _fake_get_template(tpl_id: int):
            if tpl_id not in self.store.templates:
                raise repo.NotFound("报告模板不存在")
            return repo._template_view(self.store._template_row(tpl_id))

        def _fake_get_version(tpl_id: int, version: int):
            v = self.store.versions.get((tpl_id, version))
            if not v:
                raise repo.NotFound("模板版本不存在")
            return repo._template_view(copy.deepcopy(v))

        def _fake_versions(tpl_id: int):
            rows = [
                {"version": v["version"], "created_at": v["created_at"], "size": len(v["markdown"])}
                for (tid, _), v in self.store.versions.items()
                if tid == tpl_id
            ]
            rows.sort(key=lambda r: r["version"], reverse=True)
            return rows

        def _fake_access(principal: Principal):
            if principal.is_admin:
                return Access(principal, all=True)
            if principal.emp_no == "E200":
                return Access(principal, all=False, tables={1: {"public.sales_fact"}})
            if principal.emp_no == "E201":
                return Access(principal, all=False, tables={1: {"public.sales_fact"}})
            return Access(principal, all=False, tables={})

        self.enterContext(patch.object(repo.db, "conn", side_effect=_fake_conn))
        self.enterContext(patch.object(repo.db, "fetchone", side_effect=_fake_fetchone))
        self.enterContext(patch.object(repo, "get_template", side_effect=_fake_get_template))
        self.enterContext(patch.object(repo, "get_template_version", side_effect=_fake_get_version))
        self.enterContext(patch.object(repo, "template_versions", side_effect=_fake_versions))
        self.enterContext(patch.object(repo.permission if hasattr(repo, "permission") else api.permission, "access_for", side_effect=_fake_access))
        self.enterContext(patch("smartreportgen.permission.access_for", side_effect=_fake_access))

    def test_parse_markdown_frontmatter_legacy_and_validation(self):
        # BOM + Windows 换行 + Frontmatter 提取
        content = (
            "\ufeff---\r\n"
            "title: '华东月度复盘模板'\r\n"
            "business_summary: \"覆盖 GMV 与毛利率\"\r\n"
            "analysis_goal: 拆解品类增长\r\n"
            "---\r\n"
            "# 正文标题将被 Frontmatter 名称覆盖\r\n\r\n"
            "## 一、核心指标\r\n- GMV\r\n"
        ).encode("utf-8")
        parsed = repo.parse_template_markdown_file(r"C:\exports\monthly.MD", content)
        self.assertEqual(parsed["source_file"], "monthly.MD")
        self.assertEqual(parsed["name"], "华东月度复盘模板")
        self.assertEqual(parsed["business_summary"], "覆盖 GMV 与毛利率")
        self.assertEqual(parsed["analysis_goal"], "拆解品类增长")
        self.assertTrue(parsed["markdown"].startswith("# 正文标题将被 Frontmatter 名称覆盖"))
        self.assertEqual(parsed["summary"]["sections"], ["一、核心指标"])

        # 普通 Markdown 分隔线不应被误判为 Frontmatter
        plain_hr = "---\n\n## 第一章\n正文\n"
        parsed_hr = repo.parse_template_markdown_file("销售周报.markdown", plain_hr)
        self.assertEqual(parsed_hr["name"], "销售周报")
        self.assertEqual(parsed_hr["markdown"], plain_hr)

        # 无一级标题回退为文件名去后缀
        no_h1 = repo.parse_template_markdown_file("渠道洞察指南.md", "## 渠道对比\n- 线上\n")
        self.assertEqual(no_h1["name"], "渠道洞察指南")

        # 历史 JSON 模板自动转换为 Markdown
        legacy_json = json.dumps({
            "smartreportgen_spec": {
                "title": "旧版经营模板",
                "sections": [{"id": "s1", "title": "总览"}],
            }
        }, ensure_ascii=False)
        parsed_legacy = repo.parse_template_markdown_file("legacy.md", legacy_json)
        self.assertEqual(parsed_legacy["name"], "旧版经营模板")
        self.assertIn("# 旧版经营模板", parsed_legacy["markdown"])

        # 非法输入拦截
        for bad_name, bad_body in (
            ("notes.txt", b"# txt"),
            (".md", b"# empty stem"),
            ("bad\x00.md", b"# bad"),
            ("empty.md", b"  \r\n\t "),
            ("null.md", b"# a\x00b"),
            ("gbk.md", "中文".encode("gbk")),
            ("oversize.md", b"a" * (repo.MAX_TEMPLATE_FILE_BYTES + 1)),
        ):
            with self.assertRaises(repo.Invalid, msg=bad_name):
                repo.parse_template_markdown_file(bad_name, bad_body)

    def test_import_permissions_upsert_isolation_and_atomicity(self):
        def _fake_verify(token):
            return {
                "Bearer admin-tok": self.admin,
                "Bearer user-tok": self.user,
                "Bearer other-tok": self.other,
            }.get(token) or (_ for _ in ()).throw(auth.AuthError("无效令牌"))

        with patch.object(api.auth, "verify_bearer", side_effect=_fake_verify), \
                patch.object(iam, "configured", return_value=True):
            client = TestClient(api.app)

            # 1. 未传或非法 dataset_id 返回 400 中文提示
            no_ds = client.post(
                "/api/templates/import",
                headers={"Authorization": "Bearer user-tok"},
                files=[("files", ("t1.md", b"# T1\n\n## S1\n", "text/markdown"))],
            )
            self.assertEqual(no_ds.status_code, 400)
            self.assertIn("业务场景", no_ds.json()["detail"])

            bad_ds = client.post(
                "/api/templates/import",
                headers={"Authorization": "Bearer user-tok"},
                data={"dataset_id": "abc"},
                files=[("files", ("t1.md", b"# T1\n\n## S1\n", "text/markdown"))],
            )
            self.assertEqual(bad_ds.status_code, 400)

            # 2. 普通用户导入未授权场景 2 返回 403；管理员导入不存在场景返回 404
            forbidden = client.post(
                "/api/templates/import",
                headers={"Authorization": "Bearer user-tok"},
                data={"dataset_id": "2"},
                files=[("files", ("t1.md", b"# T1\n\n## S1\n", "text/markdown"))],
            )
            self.assertEqual(forbidden.status_code, 403)

            missing_ds = client.post(
                "/api/templates/import",
                headers={"Authorization": "Bearer admin-tok"},
                data={"dataset_id": "999"},
                files=[("files", ("t1.md", b"# T1\n\n## S1\n", "text/markdown"))],
            )
            self.assertEqual(missing_ds.status_code, 404)

            # 3. 多文件一键导入并关联场景 1 + items_meta 覆盖名称与业务说明
            batch_resp = client.post(
                "/api/templates/import",
                headers={"Authorization": "Bearer user-tok"},
                data={
                    "dataset_id": "1",
                    "mode": "upsert",
                    "items_meta": json.dumps([
                        {"name": "零售日报模板", "business_summary": "每日销售追踪"},
                        {"business_summary": "按渠道毛利分析"},
                    ], ensure_ascii=False),
                },
                files=[
                    ("files", ("daily.md", b"# Original\n\n## Overview\nok\n", "text/markdown")),
                    ("files", ("channel.md", b"## Channel\nok\n", "text/markdown")),
                ],
            )
            self.assertEqual(batch_resp.status_code, 200, batch_resp.text)
            payload = batch_resp.json()
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["dataset_id"], 1)
            self.assertEqual(payload["business_scenario"], "华东零售分析")
            self.assertEqual(payload["summary"], {"total": 2, "create_count": 2, "update_count": 0})
            t1, t2 = payload["templates"]
            self.assertEqual((t1["name"], t1["version"], t1["action"]), ("零售日报模板", 1, "create"))
            self.assertEqual(t1["business_summary"], "每日销售追踪")
            self.assertEqual((t2["name"], t2["version"], t2["action"]), ("channel", 1, "create"))

            # 4. 同人同名 upsert 升版至 v2，且不传 business_summary 时保留原说明
            up_resp = client.post(
                "/api/templates/import",
                headers={"Authorization": "Bearer user-tok"},
                data={"dataset_id": "1", "mode": "upsert"},
                files=[("file", ("daily_v2.md", b"# \xe9\x9b\xb6\xe5\x94\xae\xe6\x97\xa5\xe6\x8a\xa5\xe6\xa8\xa1\xe6\x9d\xbf\n\n## Overview\nv2\n", "text/markdown"))],
            )
            self.assertEqual(up_resp.status_code, 200, up_resp.text)
            up_data = up_resp.json()
            self.assertEqual(up_data["summary"], {"total": 1, "create_count": 0, "update_count": 1})
            self.assertEqual(up_data["templates"][0]["id"], t1["id"])
            self.assertEqual(up_data["templates"][0]["version"], 2)
            self.assertEqual(up_data["templates"][0]["business_summary"], "每日销售追踪")

            # 5. 其他人同场景导入同名模板时，不会覆盖 E200 的模板，而是为 E201 新建 v1
            other_resp = client.post(
                "/api/templates/import",
                headers={"Authorization": "Bearer other-tok"},
                data={"dataset_id": "1", "mode": "upsert"},
                files=[("file", ("other.md", b"# \xe9\x9b\xb6\xe5\x94\xae\xe6\x97\xa5\xe6\x8a\xa5\xe6\xa8\xa1\xe6\x9d\xbf\n\n## Mine\nv1\n", "text/markdown"))],
            )
            self.assertEqual(other_resp.status_code, 200, other_resp.text)
            other_tpl = other_resp.json()["templates"][0]
            self.assertNotEqual(other_tpl["id"], t1["id"])
            self.assertEqual((other_tpl["version"], other_tpl["action"]), (1, "create"))

            # 6. mode=create 即使本人同名也始终新建独立模板
            create_resp = client.post(
                "/api/templates/import",
                headers={"Authorization": "Bearer user-tok"},
                data={"dataset_id": "1", "mode": "create"},
                files=[("file", ("copy.md", b"# \xe9\x9b\xb6\xe5\x94\xae\xe6\x97\xa5\xe6\x8a\xa5\xe6\xa8\xa1\xe6\x9d\xbf\n\n## Copy\n", "text/markdown"))],
            )
            self.assertEqual(create_resp.status_code, 200)
            copy_tpl = create_resp.json()["templates"][0]
            self.assertNotEqual(copy_tpl["id"], t1["id"])
            self.assertEqual((copy_tpl["version"], copy_tpl["action"]), (1, "create"))

            # 7. 多文件含坏文件时整批原子回滚（不写入任何新模板）
            count_before = len(self.store.templates)
            atomic_fail = client.post(
                "/api/templates/import",
                headers={"Authorization": "Bearer user-tok"},
                data={"dataset_id": "1", "mode": "create"},
                files=[
                    ("files", ("good.md", b"# Good Template\n\n## S1\n", "text/markdown")),
                    ("files", ("bad.txt", b"invalid ext", "text/plain")),
                ],
            )
            self.assertEqual(atomic_fail.status_code, 400)
            self.assertEqual(len(self.store.templates), count_before)

            # 8. 单批 upsert 同名冲突拦截
            dup_fail = client.post(
                "/api/templates/import",
                headers={"Authorization": "Bearer user-tok"},
                data={"dataset_id": "1", "mode": "upsert"},
                files=[
                    ("files", ("a.md", b"# Same Name\n\n## S1\n", "text/markdown")),
                    ("files", ("b.md", b"# Same Name\n\n## S2\n", "text/markdown")),
                ],
            )
            self.assertEqual(dup_fail.status_code, 400)
            self.assertEqual(len(self.store.templates), count_before)

            # 9. 下载模板 .md（最新版及指定历史版本），管理员可在任意场景导入与下载
            dl_latest = client.get(
                f"/api/templates/{t1['id']}?download=true",
                headers={"Authorization": "Bearer user-tok"},
            )
            self.assertEqual(dl_latest.status_code, 200)
            self.assertIn("text/markdown", dl_latest.headers["content-type"])
            self.assertIn("## Overview\nv2", dl_latest.text)

            dl_v1 = client.get(
                f"/api/templates/{t1['id']}?download=true&version=1",
                headers={"Authorization": "Bearer user-tok"},
            )
            self.assertEqual(dl_v1.status_code, 200)
            self.assertIn("## Overview\nok", dl_v1.text)

            adm_imp = client.post(
                "/api/templates/import",
                headers={"Authorization": "Bearer admin-tok"},
                data={"dataset_id": "2"},
                files=[("file", ("finance.md", b"# \xe8\xb4\xa2\xe5\x8a\xa1\xe6\x80\xbb\xe8\xa7\x88\n\n## P&L\n", "text/markdown"))],
            )
            self.assertEqual(adm_imp.status_code, 200)
            fin_id = adm_imp.json()["templates"][0]["id"]
            no_perm_dl = client.get(
                f"/api/templates/{fin_id}?download=true",
                headers={"Authorization": "Bearer user-tok"},
            )
            self.assertEqual(no_perm_dl.status_code, 403)

    def test_admin_ui_select_md_and_import_in_chromium(self):
        import os
        from pathlib import Path
        import smartreportgen

        chromium = Path(os.environ.get("SRG_CHROMIUM_PATH", "/usr/bin/chromium"))
        if not chromium.is_file():
            self.skipTest("需要部署镜像中的 Chromium")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("需要 playwright")

        assets = Path(next(iter(smartreportgen.__path__))) / "static"
        index_html = (assets / "admin" / "index.html").read_text(encoding="utf-8")

        def _fake_verify(token):
            if token == "Bearer user-tok":
                return self.user
            raise auth.AuthError("无效令牌")

        def _fake_list_templates(q=None, owner=None, dataset_id=None, *, allowed_datasets=None):
            rows = [self.store._template_row(tid) for tid in sorted(self.store.templates, reverse=True)]
            for r in rows:
                r["template_type"] = "business"
            return rows

        def _fake_list_datasets(q=None):
            return [
                {
                    "id": 1,
                    "business_scenario": "华东零售分析",
                    "business_description": "零售分析",
                    "datasource_id": 10,
                    "tags": [],
                    "enabled": True,
                    "owner_emp_no": "E200",
                    "table_count": 2,
                    "vectorized": True,
                },
                {
                    "id": 2,
                    "business_scenario": "集团财务总览",
                    "business_description": "财务",
                    "datasource_id": 10,
                    "tags": [],
                    "enabled": True,
                    "owner_emp_no": None,
                    "table_count": 1,
                    "vectorized": True,
                },
            ]

        with patch.object(api.auth, "verify_bearer", side_effect=_fake_verify), \
                patch.object(iam, "configured", return_value=True), \
                patch.object(repo, "list_templates", side_effect=_fake_list_templates), \
                patch.object(repo, "list_datasets", side_effect=_fake_list_datasets):
            client = TestClient(api.app)
            headers = {"Authorization": "Bearer user-tok"}
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=str(chromium), args=["--no-sandbox"])
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda exc: errors.append(f"PAGEERROR: {exc}"))
                page.on("console", lambda msg: errors.append(f"CONSOLE[{msg.type}]: {msg.text}")
                        if msg.type == "error" else None)

                def _route(route):
                    req = route.request
                    url_path = req.url.split("http://admin.test", 1)[1]
                    path_only = url_path.split("?", 1)[0]
                    if path_only == "/":
                        return route.fulfill(content_type="text/html", body=index_html)
                    if path_only.startswith("/static/"):
                        fp = assets / path_only[len("/static/"):]
                        ct = "text/javascript" if fp.suffix == ".js" else "text/css"
                        return route.fulfill(content_type=ct, path=fp)
                    if path_only == "/api/auth/config":
                        return route.fulfill(content_type="application/json", body=json.dumps({
                            "mode": "iam",
                            "iam_configured": True,
                            "me": {
                                "logged_in": True,
                                "emp_no": self.user.emp_no,
                                "name": self.user.name,
                                "is_admin": False,
                                "registered": True,
                                "roles": self.user.roles,
                            },
                        }))
                    if path_only == "/api/meta":
                        return route.fulfill(content_type="application/json", body=json.dumps({"stats": {}}))
                    if req.method == "GET":
                        resp = client.get(url_path, headers=headers)
                    else:
                        post_data = req.post_data_buffer or b""
                        req_headers = {**headers}
                        if ct := req.headers.get("content-type"):
                            req_headers["Content-Type"] = ct
                        resp = client.request(req.method, url_path, headers=req_headers, content=post_data)
                    return route.fulfill(
                        status=resp.status_code,
                        content_type=resp.headers.get("content-type", "application/json"),
                        body=resp.content,
                    )

                page.route("http://admin.test/**", _route)
                page.goto("http://admin.test/#templates")
                page.get_by_role("button", name="一键导入").first.click()
                drawer = page.locator("dialog.drawer[open]")
                self.assertEqual(drawer.count(), 1)

                # 模拟文件选择框取消事件冒泡，抽屉不应关闭
                page.evaluate("""() => {
                    const input = document.querySelector('dialog.drawer input[type="file"]');
                    input.dispatchEvent(new Event('cancel', { bubbles: true }));
                }""")
                self.assertEqual(drawer.count(), 1)

                # 上传 .md 文件后抽屉必须保持打开，并正确渲染预览行
                md_bytes = "---\ntitle: 华东零售月报指南\nbusiness_summary: 月度销售指标汇总\n---\n# 正文\n\n## 一、销量\n## 二、毛利\n".encode("utf-8")
                drawer.locator('input[type="file"]').set_input_files({
                    "name": "east_retail.md",
                    "mimeType": "text/markdown",
                    "buffer": md_bytes,
                })
                page.wait_for_selector("table.tpl-import-table tbody tr")
                self.assertEqual(drawer.count(), 1)
                self.assertEqual(errors, [])
                row_text = page.locator("table.tpl-import-table tbody tr").inner_text()
                self.assertIn("east_retail.md", row_text)
                self.assertIn("2 个章节", row_text)
                self.assertEqual(page.locator("table.tpl-import-table input").nth(0).input_value(), "华东零售月报指南")
                self.assertEqual(page.locator("table.tpl-import-table input").nth(1).input_value(), "月度销售指标汇总")

                # 未选择业务场景时提交按钮禁用
                confirm_btn = drawer.get_by_role("button", name="确认导入")
                self.assertTrue(confirm_btn.is_disabled())

                # 选择关联业务场景后点击确认导入
                drawer.locator("#tpl-import-scenario").select_option("1")
                self.assertFalse(confirm_btn.is_disabled())
                confirm_btn.click()

                page.wait_for_selector("table.record-table tbody tr")
                self.assertEqual(drawer.count(), 0)
                self.assertEqual(errors, [])
                list_text = page.locator("table.record-table tbody").inner_text()
                self.assertIn("华东零售月报指南", list_text)
                self.assertIn("华东零售分析", list_text)
                browser.close()


if __name__ == "__main__":
    unittest.main()
