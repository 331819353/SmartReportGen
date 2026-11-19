"""MCP 调试客户端：列出工具或调用一个工具。

用法（在当前终端导出海尔 IAM access token 为 SMARTREPORT_TOKEN，再在 mcp 容器中运行）：
  docker compose exec -T -e SMARTREPORT_TOKEN mcp python - list < scripts/mcp_call.py
  docker compose exec -T -e SMARTREPORT_TOKEN mcp python - call search_business_scenarios '{"query":"分析各区域销售"}' < scripts/mcp_call.py
  # 参数也可以是 @文件（容器内路径），或 '-' 表示从环境变量 MCP_ARGS 读取
环境变量：SMARTREPORT_TOKEN（必填）、MCP_URL（默认 http://localhost:8001/mcp）
"""
import asyncio
import json
import os
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main(argv: list[str]) -> None:
    url = os.getenv("MCP_URL", "http://localhost:8001/mcp")
    # 调试请求使用与外部客户端相同的 IAM Bearer 认证。
    token = os.getenv("SMARTREPORT_TOKEN", "").strip()
    if not token:
        raise SystemExit("请在 SMARTREPORT_TOKEN 环境变量中提供海尔 IAM access token")
    headers = {"Authorization": f"Bearer {token}"}
    async with streamablehttp_client(url, headers=headers) as (r, w, _):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            if not argv or argv[0] == "list":
                print(f"# server: {init.serverInfo.name}\n# instructions:\n{init.instructions}\n")
                for t in (await s.list_tools()).tools:
                    params = ", ".join(t.inputSchema.get("properties", {}).keys())
                    print(f"- {t.name}({params})\n    {t.description.strip().splitlines()[0]}")
                return
            name = argv[1]
            raw = argv[2] if len(argv) > 2 else "{}"
            if raw == "-":
                raw = os.environ["MCP_ARGS"]
            elif raw.startswith("@"):
                raw = open(raw[1:], encoding="utf-8").read()
            res = await s.call_tool(name, json.loads(raw))
            for c in res.content:
                text = getattr(c, "text", "")
                try:
                    print(json.dumps(json.loads(text), ensure_ascii=False, indent=2))
                except (ValueError, TypeError):
                    print(text)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
