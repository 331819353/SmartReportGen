# Codex CLI 容器客户端（端到端测试）

在 Docker 中运行 Codex CLI + smart-report 技能 + MCP 客户端，连接本仓库的 SmartReportGen MCP，模拟员工用 Codex 生成报告。
所有服务都在 compose 的 `codex` profile 中，平时的 `docker compose up -d` 不会启动它们。

```
codex 容器（Codex CLI 0.167.2，技能在 ~/.codex/skills 与 ~/.agents/skills）
   ├── 模型：Responses API ──► llm-bridge（LiteLLM，协议转换）──► AI 网关 Chat Completions（DeepSeek-V4-Flash）
   └── MCP：Streamable HTTP + Bearer 员工令牌 ──► mcp:8001/mcp
```

为什么需要 llm-bridge：当前 Codex 只支持 `wire_api = "responses"`（`chat` 已移除），而 AI 网关只提供 `/v1/chat/completions`。
LiteLLM 设置了 `use_chat_completions_api: true`，把 Responses 请求转换为 Chat Completions。网关密钥只在 llm-bridge 中，codex 容器拿不到。

| 文件 | 作用 |
|---|---|
| `Dockerfile` | Codex CLI 镜像：安装 `@openai/codex`，复制技能、`AGENTS.md`、`config.toml` |
| `config.toml` | Codex 配置：模型提供方（llm-bridge）、MCP 服务 `smartreportgen`（令牌来自 `SMARTREPORT_TOKEN`） |
| `llm-bridge.Dockerfile`、`litellm.yaml` | 模型协议转换代理 |
| `out/` | 挂载到容器的 `/home/agent/work/out`，可以让 agent 把文件写到这里（不进版本库） |

## 使用

1. `.env` 中需要有 `AI_BASE_URL`、`AI_API_KEY`（与服务共用），以及 `LITELLM_MASTER_KEY`（llm-bridge 访问密钥，`sk-` 加随机串）：
   ```bash
   echo "LITELLM_MASTER_KEY=sk-$(openssl rand -hex 24)" >> .env
   ```
2. 构建并启动模型桥：
   ```bash
   docker compose --profile codex build llm-bridge codex
   ```
   ```bash
   docker compose --profile codex up -d llm-bridge
   ```
3. 准备员工令牌（员工用 IAM 登录管理台 →「我的令牌」签发），只放在当前 shell：
   ```bash
   export SMARTREPORT_TOKEN=<员工令牌>
   ```
4. 运行（`</dev/null` 很重要：非交互运行时 Codex 会等待标准输入）：
   ```bash
   docker compose --profile codex run --rm -T codex mcp list </dev/null
   ```
   ```bash
   docker compose --profile codex run --rm -T codex exec --skip-git-repo-check "调用 whoami，告诉我当前工号和可访问的业务场景" </dev/null
   ```
   ```bash
   docker compose --profile codex run --rm -T codex exec --skip-git-repo-check "帮我做一份今年的零售销售经营分析报告，重点看各大区、渠道的销售额、毛利率和同比" </dev/null
   ```
   交互式：`docker compose --profile codex run --rm codex`（不带参数进入 Codex TUI）。
5. 用完停止模型桥：`docker compose --profile codex stop llm-bridge`。

## 换模型

在 `litellm.yaml` 的 `model_list` 中增加网关上的模型（需要支持函数调用），并修改 `config.toml` 的 `model`，然后重新构建两个镜像。
Codex 对非 OpenAI 模型会提示 “Model metadata … not found”，可以忽略。
