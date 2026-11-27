"""验证场景与模板的分享、独立授权以及管理台预览链接。"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from smartreportgen import api, auth, db, iam, mcp_server, repo, report_queries
from smartreportgen.auth import Principal
from smartreportgen.config import settings
from smartreportgen.connectors.api import APIConnector
from smartreportgen.permission import access_for


@pytest.fixture
def shared_resources(monkeypatch):
    url = os.environ.get("SRG_TEST_DATABASE_URL")
    if not url:
        pytest.skip("需要独立 PostgreSQL 测试库（SRG_TEST_DATABASE_URL）")
    monkeypatch.setattr(settings, "database_url", url)
    monkeypatch.setattr(settings, "public_base_url", "http://admin.test")
    db.init_db()
    suffix = uuid4().hex[:8]
    owner, member, outsider, admin = (Principal(f"{role}-{suffix}", is_admin=role == "admin")
                                     for role in ("owner", "member", "outsider", "admin"))
    policy = {"scenario_tables": [{"table_name": "facts", "employees": [owner.emp_no, member.emp_no],
                                   "row_filters": [{"column": "region", "values": ["华东"]}]}]}
    with db.conn() as c, c.transaction():
        for principal in (owner, member, outsider, admin):
            c.execute("INSERT INTO permission.employee(emp_no,name,is_admin) VALUES (%s,%s,%s)",
                      (principal.emp_no, principal.emp_no, principal.is_admin))
        source_id = c.execute("INSERT INTO datasource.datasource(name,type,config) VALUES (%s,'api',%s) RETURNING id",
                              (suffix, db.jsonb(policy))).fetchone()["id"]
        system_id = c.execute(
            "INSERT INTO dataset.dataset(business_scenario,business_description,datasource_id) "
            "VALUES (%s,'系统分析',%s) RETURNING id", (f"系统场景 {suffix}", source_id),
        ).fetchone()["id"]
        c.execute("INSERT INTO dataset.dataset_table(dataset_id,table_name) VALUES (%s,'facts'),(%s,'detail')",
                  (system_id, system_id))
    scene = repo.create_dataset({"business_scenario": f"个人场景 {suffix}", "business_description": "经营分析",
                                 "datasource_id": source_id, "tables": [{"table_name": "facts"}]}, owner)
    template = repo.save_template({"name": f"关联模板 {suffix}", "markdown": "# 关联指南", "dataset_id": scene["id"],
                                   "created_by": owner.emp_no})
    standalone = repo.save_template({"name": f"独立模板 {suffix}", "markdown": "# 独立指南", "created_by": owner.emp_no})
    tokens = {f"Bearer {p.emp_no}": p for p in (owner, member, outsider, admin)}
    monkeypatch.setattr(api.auth, "verify_bearer", tokens.__getitem__)
    monkeypatch.setattr(api.iam, "configured", lambda: True)
    yield TestClient(api.app), scene, system_id, template, standalone, owner, member, outsider, admin
    db.pool().close()
    db._pool = None


def _headers(principal):
    return {"Authorization": f"Bearer {principal.emp_no}"}


def test_scene_share_levels_and_source_revocation(shared_resources, monkeypatch):
    client, scene, system, template, _, owner, member, outsider, admin = shared_resources
    path = f"/api/datasets/{scene['id']}"
    own, received, other, root = map(_headers, (owner, member, outsider, admin))

    def share(emp_no, level, url=path, manager=own):
        return client.post(url + "/shares", headers=manager, json={"emp_no": emp_no, "access_level": level})

    assert client.get(path, headers=received).status_code == 404
    assert share(outsider.emp_no, "view").status_code == 200
    assert access_for(outsider).tables == {scene["id"]: {"facts"}}
    assert not client.get(f"/api/datasources/{scene['datasource_id']}", headers=other).json()["can_build_scene"]
    # 用内存数据验证分享带来的查询权限仍限定在场景内，并应用数据源固定行范围。
    connector = APIConnector(repo.get_datasource(scene["datasource_id"]), "")
    connector.fetch = lambda _: [{"region": "华东", "amount": 10}, {"region": "华西", "amount": 999}]
    monkeypatch.setattr(mcp_server.services, "get_connector", lambda _: connector)
    monkeypatch.setattr(mcp_server, "_access", lambda _: access_for(outsider))
    result = mcp_server.run_query(scene["id"], "SELECT SUM(amount) FROM facts", None)
    assert result["ok"] and result["rows"] == [[10]], result
    assert mcp_server.run_query(system, "SELECT SUM(amount) FROM facts", None)["error_type"] == "permission_denied"
    assert share("unknown", "view").status_code == 400
    assert client.get(path + "/share-recipients", headers=own, params={"q": member.emp_no}).json() == [
        {"emp_no": member.emp_no, "name": member.emp_no}]
    assert share(member.emp_no, "view").status_code == 200
    assert client.get(path, headers=received).json()["access_level"] == "view"
    assert client.get(path, headers=received).headers["cache-control"] == "no-store"
    assert client.get(path + "/shares", headers=received).status_code == 403
    assert client.put(path, headers=received, json={"analysis_approach": "无权写入"}).status_code == 403
    assert client.get(f"/api/templates/{template['id']}", headers=received).status_code == 403
    assert not any(row["id"] == template["id"] for row in client.get("/api/templates", headers=received).json())

    assert share(member.emp_no, "edit").status_code == 200
    assert client.put(path, headers=received, json={"tables": [{"table_name": "detail"}]}).status_code == 403
    assert client.put(path, headers=received, json={"enabled": False}).status_code == 403
    assert client.put(path, headers=received, json={"analysis_approach": "按渠道核对"}).json()["analysis_approach"] == "按渠道核对"
    assert client.delete(path, headers=received).status_code == 403
    upload = client.put(path + "/analysis-file", headers=received, files={"file": ("guide.md", b"# Guide")})
    assert upload.status_code == 200, upload.text
    assert client.get(path + "/analysis-file?download=true", headers=received).text == "# Guide"
    monkeypatch.setattr(mcp_server, "_access", lambda _: access_for(member))
    # MCP 读取后原样回传表配置时，固定源范围仍保留在数据源一侧。
    tables = mcp_server.get_dataset_tables(scene["id"], None)["tables"]
    updated = mcp_server.save_business_scenario(scene["business_scenario"], "MCP 更新", scene["datasource_id"],
                                                tables, None, dataset_id=scene["id"])
    assert updated["ok"], updated
    assert repo.get_dataset(scene["id"])["tables"][0]["row_filters"] == []
    assert client.delete(path + "/analysis-file", headers=received).status_code == 200
    assert share(member.emp_no, "view").status_code == 200
    assert client.put(path, headers=received, json={"analysis_approach": "已降级"}).status_code == 403
    assert share(member.emp_no, "edit").status_code == 200

    # 撤销创建人的来源授权后，即使接收人仍可引用源表，已有场景分享也会停止授权。
    db.execute("UPDATE datasource.datasource SET config=%s WHERE id=%s",
               (db.jsonb({"scenario_tables": [{"table_name": "facts", "employees": [member.emp_no]}]}),
                scene["datasource_id"]))
    assert client.get(path, headers=received).status_code == 404
    assert client.delete(path + "/analysis-file", headers=received).status_code == 403
    assert client.delete(path + f"/shares/{member.emp_no}", headers=own).status_code == 200
    assert [row["emp_no"] for row in client.get(path + "/shares", headers=own).json()] == [outsider.emp_no]
    assert client.get(path, headers=other).status_code == 404

    # 系统场景分享自动授予所有场景表，撤销后只保留独立授权的表。
    system_path = f"/api/datasets/{system}"
    assert share(member.emp_no, "edit", system_path, root).status_code == 200
    assert access_for(member).tables[system] == {"facts", "detail"}
    assert client.get(system_path, headers=received).json()["access_level"] == "edit"
    db.execute("INSERT INTO permission.table_grant(subject_type,subject,dataset_id,table_name) "
               "VALUES ('employee',%s,%s,'facts')", (member.emp_no, system))
    assert client.delete(system_path + f"/shares/{member.emp_no}", headers=root).status_code == 200
    assert client.get(system_path, headers=received).json()["access_level"] == "view"
    assert access_for(member).tables[system] == {"facts"}


def test_template_share_and_versioned_edits(shared_resources, monkeypatch):
    client, scene, _, template, standalone, owner, member, outsider, _ = shared_resources
    own, received, other = map(_headers, (owner, member, outsider))
    linked_path = f"/api/templates/{template['id']}"
    standalone_path = f"/api/templates/{standalone['id']}"
    share = {"emp_no": member.emp_no, "access_level": "view"}
    assert client.post(linked_path + "/shares", headers=own, json=share).status_code == 403
    assert client.post(f"/api/datasets/{scene['id']}/shares", headers=own, json=share).status_code == 200
    assert client.post(linked_path + "/shares", headers=own, json=share).status_code == 200
    assert client.get(linked_path, headers=received).json()["access_level"] == "view"
    assert client.post(standalone_path + "/shares", headers=own, json=share).status_code == 200
    assert {t["id"]: t["access_level"] for t in client.get("/api/templates", headers=received).json()} == {
        template["id"]: "view", standalone["id"]: "view"}
    body = {"markdown": "# 更新指南", "expected_version": 1}
    assert client.put(linked_path, headers=received, json=body).status_code == 403
    assert client.get(linked_path + "/versions/1", headers=other).status_code == 403
    assert client.get(linked_path + "?download=true", headers=received).text == template["markdown"]

    share["access_level"] = "edit"
    assert client.post(linked_path + "/shares", headers=own, json=share).status_code == 200
    assert client.put(linked_path, headers=received, json={**body, "dataset_id": None}).status_code == 403
    saved = client.put(linked_path, headers=received, json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2 and saved.json()["access_level"] == "edit"
    assert client.put(linked_path, headers=received, json=body).status_code == 409
    assert client.delete(linked_path, headers=received).status_code == 403
    assert client.post(linked_path + "/shares", headers=received, json=share).status_code == 403
    assert client.get(linked_path + "/versions/1", headers=received).json()["markdown"] == template["markdown"]

    monkeypatch.setattr(mcp_server, "_access", lambda _: access_for(member))
    assert mcp_server.get_report_template(template["id"], None)["access_level"] == "edit"
    revised = mcp_server.save_report_template("MCP 指南", "# MCP 指南", None, template_id=template["id"], expected_version=2)
    assert revised["ok"] and revised["version"] == 3, revised
    assert client.delete(f"/api/datasets/{scene['id']}/shares/{member.emp_no}", headers=own).status_code == 200
    assert client.get(linked_path + "?download=true", headers=received).status_code == 403
    assert client.delete(standalone_path + f"/shares/{member.emp_no}", headers=own).status_code == 200
    assert client.get(standalone_path, headers=received).status_code == 403


def test_claim_links_for_scene_template_and_report(shared_resources):
    client, scene, _, template, standalone, owner, member, outsider, _ = shared_resources
    own, received, other = map(_headers, (owner, member, outsider))
    scene_id = scene["id"]
    scene_links = f"/api/share-links/datasets/{scene_id}"
    linked_template = f"/api/share-links/templates/{template['id']}"

    def issue(path, level="view", **kwargs):
        response = client.post(path, headers=own, json={"access_level": level, **kwargs})
        assert response.status_code == 201, response.text
        return response.json()

    def claim(link, headers):
        return client.post("/api/share-links/claim", headers=headers, json={"token": urlsplit(link["url"]).fragment.split("/")[1]})

    scope = report_queries.scope_for(scene_id, scene, repo.get_datasource(scene["datasource_id"]), ["facts"])
    with db.conn() as c, c.transaction():
        report_id = c.execute("INSERT INTO report.report(name,html,created_by,revision) "
                              "VALUES ('分享报告','',%s,2) RETURNING id", (owner.emp_no,)).fetchone()["id"]
        for version in (1, 2):
            c.execute("INSERT INTO report.report_version(report_id,version,name,html,meta,created_by) "
                      "VALUES (%s,%s,'分享报告','<p>报告</p>',%s,%s)",
                      (report_id, version, db.jsonb({"scopes": [scope]}), owner.emp_no))
    report = issue(f"/api/share-links/reports/{report_id}", version=1)
    linked = issue(linked_template, "edit")
    invite = issue(scene_links, "edit")
    expires = datetime.fromisoformat(invite["expires_at"])
    assert 86300 < (expires - datetime.now(timezone.utc)).total_seconds() <= 86400
    assert client.get(scene_links, headers=own).json() == [invite]
    assert client.get(scene_links, headers=received).status_code == 403
    assert claim(invite, {}).status_code == 401
    assert claim(linked, other).status_code == 403
    assert claim(report, other).status_code == 403

    # 同一链接可供多人领取。没有来源目录权限的员工也获得按场景隔离的数据权限。
    assert claim(invite, other).json() == {"kind": "datasets", "url": f"/#datasets/{scene_id}", "access_level": "edit"}
    assert claim(invite, received).status_code == 200
    assert access_for(outsider).tables == {scene_id: {"facts"}}
    assert claim(linked, other).json()["access_level"] == "edit"
    assert claim(report, other).json()["url"] == f"/reports/{report_id}?version=1"
    assert client.get(f"/api/reports/{report_id}?version=1", headers=other).status_code == 200
    assert client.post(scene_links, headers=other, json={}).status_code == 403

    share = f"/api/datasets/{scene_id}/shares"
    assert client.post(share, headers=own, json={"emp_no": outsider.emp_no, "access_level": "view"}).status_code == 200
    assert claim(invite, other).json()["access_level"] == "view"
    assert client.delete(share + f"/{outsider.emp_no}", headers=own).status_code == 200
    assert claim(invite, other).status_code == 403
    assert client.get(f"/api/reports/{report_id}?version=1", headers=other).status_code == 403
    assert client.delete(scene_links + f"/{invite['id']}", headers=own).status_code == 200
    assert claim(invite, received).status_code == 410
    assert access_for(member).can_table(scene_id, "facts")

    # 到期不影响已经领取的模板权限，列表亦不再暴露过期链接。
    solo_path = f"/api/share-links/templates/{standalone['id']}"
    solo = issue(solo_path)
    assert claim(solo, other).status_code == 200
    db.execute("UPDATE permission.share_link SET expires_at=clock_timestamp() WHERE id=%s", (solo["id"],))
    assert claim(solo, received).status_code == 410
    assert client.get(solo_path, headers=own).json() == []
    assert client.get(f"/api/templates/{standalone['id']}", headers=other).status_code == 200

    # 登录表单用 POST 发送凭证，IAM 的 state 只包含 Cookie 绑定的加密回跳。
    login_client = TestClient(api.app, base_url="http://admin.test")
    token = urlsplit(linked["url"]).fragment.split("/")[1]
    login = login_client.post("/auth/iam/share-login", data={"token": token}, follow_redirects=False)
    assert login.status_code == 303, login.text
    assert token not in login.headers["location"]
    oauth_state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
    assert token.encode() not in iam._unb64(oauth_state.split(".")[0])
    assert iam.unpack_state(login.cookies[auth.LOGIN_COOKIE], oauth_state) == f"/#claim/{token}"
    with pytest.raises(iam.IamError):
        iam.unpack_state("另一浏览器", oauth_state)


def test_admin_sharing_and_preview_in_chromium(shared_resources, tmp_path):
    from playwright.sync_api import expect, sync_playwright

    client, scene, _, _, standalone, owner, member, outsider, _ = shared_resources
    chromium = Path(os.environ.get("SRG_CHROMIUM_PATH", "/usr/bin/chromium"))
    if not chromium.is_file():
        pytest.skip("需要 Chromium 验证管理台流程")
    origin = "http://admin.test"
    current = owner

    def handle(route):
        req = route.request
        headers = _headers(current) if current else {}
        if content_type := req.headers.get("content-type"):
            headers["Content-Type"] = content_type
        response = client.request(req.method, req.url.removeprefix(origin), headers=headers, content=req.post_data_buffer)
        route.fulfill(status=response.status_code, content_type=response.headers.get("content-type"), body=response.content)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(chromium), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.add_init_script("Object.defineProperty(navigator, 'share', {value: async payload => { window.sentShare = payload; }});")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route(origin + "/**", handle)
        for kind, record, label in (("datasets", scene, "业务场景"), ("templates", standalone, "报告模板")):
            current = owner
            page.goto(f"{origin}/#{kind}")
            page.get_by_role("row").filter(has_text=record.get("name", record.get("business_scenario"))).get_by_role(
                "button", name="分享", exact=True).click()
            dialog = page.get_by_role("dialog", name=f"分享{label}")
            dialog.get_by_role("searchbox").fill(member.emp_no)
            dialog.get_by_role("button", name=member.emp_no).click()
            dialog.get_by_role("combobox", name="权限").select_option("edit" if kind == "datasets" else "view")
            dialog.get_by_role("button", name="保存权限").click()
            expect(dialog.get_by_role("button", name=f"撤销 {member.emp_no} 的权限")).to_be_visible()
            link = dialog.get_by_role("textbox", name=f"{label}链接").input_value()
            assert link == f"{origin}/#{kind}/{record['id']}"
            page.screenshot(path=str(tmp_path / f"{kind}-desktop.png"))
            page.keyboard.press("Escape")

            # 未登录入口保留资源链接；登录后直接预览并按权限显示编辑入口。
            current = None
            page.goto(link)
            page.reload()
            login = page.get_by_role("link", name="使用海尔账号登录").get_attribute("href")
            assert parse_qs(urlsplit(login).query)["next"] == [f"/#{kind}/{record['id']}"]
            current = member
            page.reload()
            if kind == "datasets":
                preview = page.get_by_role("dialog", name=scene["business_scenario"])
                preview.get_by_role("button", name="编辑业务场景").click()
                editor = page.get_by_role("dialog", name="编辑业务场景")
                editor.get_by_label("分析思路", exact=False).fill("浏览器协作编辑")
                editor.get_by_role("button", name="保存业务场景").click()
                expect(preview.get_by_text("浏览器协作编辑")).to_be_visible()
            else:
                expect(page.get_by_role("region", name="模板阅读")).to_be_visible()
                expect(page.get_by_role("button", name="编辑模板")).to_have_count(0)
                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path=str(tmp_path / "template-mobile-preview.png"))
                assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
                current = owner
                page.reload()
                page.get_by_role("region", name="模板阅读").get_by_role("button", name="分享", exact=True).click()
                dialog = page.get_by_role("dialog", name="分享报告模板")
                dialog.get_by_role("button", name=f"修改 {member.emp_no} 的权限").click()
                dialog.get_by_role("combobox", name="权限").select_option("edit")
                dialog.get_by_role("button", name="保存权限").click()
                expect(dialog.get_by_text("可编辑", exact=True)).to_be_visible()
                page.screenshot(path=str(tmp_path / "template-mobile-sharing.png"))
                current = member
                page.reload()
                page.get_by_role("button", name="编辑模板").click()
                page.get_by_role("textbox", name="模板名称").fill("浏览器模板协作")
                page.get_by_role("button", name="保存模板").click()
                expect(page.get_by_role("region", name="模板阅读").get_by_text("浏览器模板协作", exact=True).first).to_be_visible()

        current = owner
        page.goto(f"{origin}/#datasets")
        page.reload()
        page.get_by_role("row").filter(has_text=scene["business_scenario"]).get_by_role("button", name="分享").click()
        dialog = page.get_by_role("dialog", name="分享业务场景")
        dialog.get_by_role("button", name="手动输入工号").click()
        dialog.get_by_role("textbox", name="接收人工号").fill(outsider.emp_no)
        dialog.get_by_role("button", name="保存权限").click()
        expect(dialog.get_by_role("button", name=f"撤销 {outsider.emp_no} 的权限")).to_be_visible()
        assert access_for(outsider).can_table(scene["id"], "facts")

        dialog.get_by_role("button", name="24 小时领取链接").click()
        dialog.get_by_role("combobox", name="领取权限").select_option("edit")
        dialog.get_by_role("button", name="生成领取链接").click()
        link = dialog.get_by_role("textbox", name="领取链接").input_value()
        assert urlsplit(link).fragment.startswith("claim/")
        dialog.get_by_role("button", name="发送链接").click()
        assert page.evaluate("window.sentShare.url") == link
        page.screenshot(path=str(tmp_path / "mobile-claim-sharing.png"))
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth")

        current = None
        page.goto(link)
        page.reload()
        expect(page.get_by_role("heading", name="登录后领取分享")).to_be_visible()
        expect(page.get_by_role("button", name="使用海尔账号登录")).to_be_visible()
        assert page.locator('form[method="post"]').get_attribute("action") == f"{origin}/auth/iam/share-login"
        current = outsider
        page.reload()
        claim_button = page.get_by_role("button", name="领取并打开")
        expect(claim_button).to_be_visible()
        page.screenshot(path=str(tmp_path / "mobile-claim-page.png"))
        claim_button.click()
        expect(page.get_by_role("dialog", name=scene["business_scenario"]).get_by_role("button", name="编辑业务场景")).to_be_visible()
        assert not errors
        browser.close()
