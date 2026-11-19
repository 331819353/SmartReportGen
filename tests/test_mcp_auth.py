"""保护 MCP 的 IAM Bearer 入口，使用模拟 IAM 和员工身份避免访问外部服务。"""
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from smartreportgen import auth, iam, mcp_server
from smartreportgen.api import app as api_app


class McpAuthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = cls.enterClassContext(TestClient(mcp_server.build_app()))

    def test_alternative_credentials_are_rejected_before_iam(self):
        with patch.object(iam, "verify_access_token") as verify:
            for headers in (
                {},
                {"X-IAM-Access-Token": "test-token"},
                {"Access-Token": "test-token"},
                {"Cookie": f"{auth.SESSION_COOKIE}=test-session"},
                {"Authorization": "Basic test-token"},
            ):
                with self.subTest(headers=headers):
                    response = self.client.post("/mcp", headers=headers)
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response.headers["WWW-Authenticate"], 'Bearer realm="smartreportgen"')
            verify.assert_not_called()

    def test_bearer_calls_iam_and_passes_verified_identity_to_tool(self):
        principal = auth.Principal("test-employee", registered=False)
        with patch.object(iam, "configured", return_value=True), \
                patch.object(iam, "verify_access_token", return_value=iam.IamAccount(principal.emp_no)) as verify, \
                patch.object(auth, "principal_for", return_value=principal):
            # 真实 JSON-RPC 入口应复用中间件核实的身份，不重复鉴权。
            response = self.client.post("/mcp", headers={
                "Authorization": "Bearer test-token",
                "Accept": "application/json, text/event-stream",
            }, json={
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "whoami", "arguments": {}},
            })
            self.assertEqual(response.status_code, 200)
            identity = json.loads(response.json()["result"]["content"][0]["text"])
            self.assertEqual(identity["emp_no"], principal.emp_no)
            verify.assert_called_once_with("test-token")

    def test_iam_rejection_and_outage_have_distinct_status_codes(self):
        for error, status in ((iam.IamError("令牌无效"), 401), (iam.IamUnavailable("连接失败"), 503)):
            with self.subTest(status=status), patch.object(iam, "configured", return_value=True), \
                    patch.object(iam, "verify_access_token", side_effect=error):
                response = self.client.post("/mcp", headers={"Authorization": "Bearer test-token"})
                self.assertEqual(response.status_code, status)

    def test_mcp_oauth_routes_are_removed(self):
        self.assertEqual(self.client.get("/.well-known/oauth-protected-resource").status_code, 404)
        self.assertEqual(self.client.get("/.well-known/oauth-protected-resource/mcp").status_code, 404)
        # 仅测试路由，不启动管理 API 的数据库初始化。
        with patch.object(iam, "configured", return_value=True):
            api = TestClient(api_app)
            self.assertEqual(api.get("/.well-known/oauth-authorization-server").status_code, 404)
            self.assertEqual(api.get("/auth/mcp/authorize").status_code, 404)
            self.assertEqual(api.post("/auth/mcp/token").status_code, 404)
            self.assertNotIn("mcp_oauth", api.get("/api/auth/config").json())
