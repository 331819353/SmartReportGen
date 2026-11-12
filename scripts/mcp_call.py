"""MCP 调试客户端：列出工具或调用一个工具。

用法（在 mcp 容器内运行，无需本机安装依赖）：
  docker compose exec -T mcp python - list < scripts/mcp_call.py
  docker compose exec -T mcp python - call search_business_scenarios '{"query":"分析各区域销售"}' < scripts/mcp_call.py
  # 参数也可以是 @文件（容器内路径），或 '-' 表示从环境变量 MCP_ARGS 读取
环境变量：MCP_URL（默认 http://localhost:8001/mcp）、MCP_TOKEN
"""
import asyncio
import json
import os
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main(argv: list[str]) -> None:
    url = os.getenv("MCP_URL", "http://localhost:8001/mcp")
    headers = {"Authorization": f"Bearer {os.environ['MCP_TOKEN']}"} if os.getenv("MCP_TOKEN") else None
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
