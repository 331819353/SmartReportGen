# AGENTS.md

本仓库是 **SmartReportGen**：智能报告生成服务（MCP + Skill）。本文件供 Codex、Cursor、Copilot 等读取 AGENTS.md 的 agent 使用。

## 生成报告

当用户要求“生成/做一份 …分析报告 / 经营报告 / HTML 报告”时：

1. 使用技能 **smart-report**：`skills/smart-report/SKILL.md`（在 `.agents/skills/`、`.claude/skills/` 下均有指向它的软链接）。
   若当前 agent 不能加载技能，读取该文件全文并严格按其流程执行；也可以调用 MCP 工具 `get_skill_guide` 获取同样的内容。
2. 所有数据访问只通过 MCP 服务 **smartreportgen**（`http://localhost:18600/mcp`）。
   Codex CLI 可通过 `codex mcp login smartreportgen` 登录海尔 IAM 并自动携带 IAM access token。使用 Bearer 配置的客户端可传入 海尔 IAM access token。
   不要直连数据库，不要编造数据；遇到 `permission_denied` 不要尝试绕过，应告知用户申请权限。
3. 模板语法：MCP 工具 `get_template_spec`，或 `skills/smart-report/references/template-spec.md`。

各 agent 的 MCP 接入方式见 `docs/DEPLOYMENT.md` 的「接入 Agent」一节。管理台 HTTP 接入见 `docs/HTTP_API.md`。

## 开发本仓库

- 服务代码：`app/smartreportgen/`（FastAPI 管理 API + MCP 服务）。部署与配置：`docs/DEPLOYMENT.md`。
- 修改后执行 `docker compose build && docker compose up -d`。报告的 CSS/JS 在渲染时内联，旧报告需重新渲染才生效。
- 技能只维护 `skills/smart-report/` 这一份；镜像构建时会被复制进 MCP 服务，修改后需要重新构建。
- 调试 MCP：`docker compose exec -T -e MCP_TOKEN=<令牌> mcp python - call <tool> '<json参数>' < scripts/mcp_call.py`
