# Agent 接入配置示例

技能（`skills/smart-report/`）与 MCP 服务（`smartreportgen`）都不绑定特定 agent。本目录是各 agent 的 MCP 配置模板；
Codex CLI 可运行 `codex mcp login smartreportgen` 完成海尔 IAM 登录。下方 Bearer 配置示例从环境变量 `SMARTREPORT_TOKEN` 读取令牌或由客户端交互输入。令牌应存放在本机的凭证存储中。
令牌来源：客户端或 agent 宿主从海尔 IAM 取得 access token（服务端向 IAM 核实）。
若宿主把 IAM 票放在自定义头里，可配置为 `X-IAM-Access-Token: <IAM 票>`（如 Codex 的 `env_http_headers`）。
详细步骤见 [`docs/DEPLOYMENT.md`](../docs/DEPLOYMENT.md) 的「接入 Agent」一节。

| Agent | MCP 配置放在 | 本目录模板 | 技能目录 | 验证情况 |
|---|---|---|---|---|
| Claude Code | 项目 `.mcp.json`（仓库已带） | — | `.claude/skills/`（仓库已链接）或 `~/.claude/skills/` | 已实测 |
| Codex | 项目 `.codex/config.toml`（仓库已带）或 `~/.codex/config.toml` | — | `.agents/skills/`（仓库已链接）或 `~/.codex/skills/`、`~/.agents/skills/` | 已验证 IAM 登录及 MCP 调用 |
| Cursor | `.cursor/mcp.json` 或 `~/.cursor/mcp.json` | `cursor.mcp.json` | 无技能机制时读 `AGENTS.md`，或调 `get_skill_guide` | 未实测 |
| VS Code Copilot | `.vscode/mcp.json` | `vscode.mcp.json`（首次使用时提示输入令牌） | 同上 | 未实测 |
| Gemini CLI | `~/.gemini/settings.json` 或项目 `.gemini/settings.json` | `gemini.settings.json` | 同上 | 未实测 |
| OpenCode | 项目 `opencode.json` | `opencode.json` | `.agents/skills/` 或 `.claude/skills/` | 未实测 |
| 其他 MCP 客户端 | 按客户端要求 | `generic.mcp.json` | 调 `get_skill_guide` 或使用 MCP 提示词 `smart_report` | — |

没有技能机制的 agent，有三种方式获得完整流程：
1. 读取 `AGENTS.md` → 指向 `skills/smart-report/SKILL.md`；
2. 调用 MCP 工具 `get_skill_guide`（MCP 服务的初始化说明也会提示 agent 这样做）；
3. 使用 MCP 提示词 `smart_report(requirement)`（支持 MCP prompts 的客户端，通常显示为斜杠命令）。
