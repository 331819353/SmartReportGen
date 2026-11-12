# Agent 接入配置示例

技能（`skills/smart-report/`）与 MCP 服务（`smartreportgen`）都不绑定特定 agent。本目录是各 agent 的 MCP 配置模板；
令牌一律从环境变量 `SMARTREPORT_TOKEN` 读取（或由客户端交互输入），不要把令牌写进会提交的文件。
令牌来源：员工用海尔 IAM 登录管理台 →「我的令牌」自助签发；或直接使用 agent 宿主持有的 IAM access token（服务端向 IAM 核实）。
若宿主把 IAM 票放在自定义头里，可配置为 `X-IAM-Access-Token: <IAM 票>`（如 Codex 的 `env_http_headers`）。
详细步骤见 [`docs/DEPLOYMENT.md`](../docs/DEPLOYMENT.md) 的「接入 Agent」一节。

| Agent | MCP 配置放在 | 本目录模板 | 技能目录 | 验证情况 |
|---|---|---|---|---|
| Claude Code | 项目 `.mcp.json`（仓库已带） | — | `.claude/skills/`（仓库已链接）或 `~/.claude/skills/` | 已实测 |
| Codex | 项目 `.codex/config.toml`（仓库已带）或 `~/.codex/config.toml` | — | `.agents/skills/`（仓库已链接）或 `~/.codex/skills/`、`~/.agents/skills/` | 配置按官方文档编写；鉴权头与服务端已实测，未在 Codex 内实跑 |
| Cursor | `.cursor/mcp.json` 或 `~/.cursor/mcp.json` | `cursor.mcp.json` | 无技能机制时读 `AGENTS.md`，或调 `get_skill_guide` | 未实测 |
| VS Code Copilot | `.vscode/mcp.json` | `vscode.mcp.json`（首次使用时提示输入令牌） | 同上 | 未实测 |
| Gemini CLI | `~/.gemini/settings.json` 或项目 `.gemini/settings.json` | `gemini.settings.json` | 同上 | 未实测 |
| OpenCode | 项目 `opencode.json` | `opencode.json` | `.agents/skills/` 或 `.claude/skills/` | 未实测 |
| 其他 MCP 客户端 | 按客户端要求 | `generic.mcp.json` | 调 `get_skill_guide` 或使用 MCP 提示词 `smart_report` | — |

没有技能机制的 agent，有三种方式获得完整流程：
1. 读取 `AGENTS.md` → 指向 `skills/smart-report/SKILL.md`；
2. 调用 MCP 工具 `get_skill_guide`（MCP 服务的初始化说明也会提示 agent 这样做）；
3. 使用 MCP 提示词 `smart_report(requirement)`（支持 MCP prompts 的客户端，通常显示为斜杠命令）。
