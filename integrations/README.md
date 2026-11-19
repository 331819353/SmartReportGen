# Agent 接入配置示例

技能（`skills/smart-report/`）与 MCP 服务（`smartreportgen`）都不绑定特定 agent。本目录是各 agent 的 MCP 配置模板；
MCP 仅接受 `Authorization: Bearer <海尔 IAM access token>`。客户端或宿主从海尔 IAM 取得令牌，再通过 `SMARTREPORT_TOKEN` 环境变量或客户端凭证输入配置 Bearer 请求头。
详细步骤见[部署文档第 10 节：接入 Agent](../docs/DEPLOYMENT.md#10-接入-agent)。报告流程见[技能流程总览](../skills/smart-report/SKILL.md#流程总览)，新报告按第 1–6 步执行，查看或修订已有报告使用第 7 步。

| Agent | MCP 配置放在 | 本目录模板 | 技能目录 | 验证情况 |
|---|---|---|---|---|
| Claude Code | 通过 `claude mcp add --header` 配置 Bearer | 见部署文档 10.1 | `.claude/skills/`（仓库已链接）或 `~/.claude/skills/` | 已实测 |
| Codex | `~/.codex/config.toml` 或项目 `.codex/config.toml` | 见部署文档 10.2 | `.agents/skills/`（仓库已链接）或 `~/.codex/skills/`、`~/.agents/skills/` | 已核对 CLI 参数 |
| Cursor | `.cursor/mcp.json` 或 `~/.cursor/mcp.json` | `cursor.mcp.json` | 无技能机制时读 `AGENTS.md`，或调 `get_skill_guide` | 未实测 |
| VS Code Copilot | `.vscode/mcp.json` | `vscode.mcp.json`（首次使用时提示输入令牌） | 同上 | 未实测 |
| Gemini CLI | `~/.gemini/settings.json` 或项目 `.gemini/settings.json` | `gemini.settings.json` | 同上 | 未实测 |
| OpenCode | 项目 `opencode.json` | `opencode.json` | `.agents/skills/` 或 `.claude/skills/` | 未实测 |
| 其他 MCP 客户端 | 按客户端要求 | `generic.mcp.json` | 调 `get_skill_guide` 或使用 MCP 提示词 `smart_report` | — |

没有技能机制的 agent，有三种方式获得完整流程：
1. 读取 `AGENTS.md` → 指向 `skills/smart-report/SKILL.md`；
2. 调用 MCP 工具 `get_skill_guide`（MCP 服务的初始化说明也会提示 agent 这样做）；
3. 使用 MCP 提示词 `smart_report(requirement)`（支持 MCP prompts 的客户端，通常显示为斜杠命令）。
