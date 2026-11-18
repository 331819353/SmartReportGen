# AGENTS.md

本仓库是 **SmartReportGen**：智能报告生成服务（MCP + Skill）。本文件供 Codex、Cursor、Copilot 等读取 AGENTS.md 的 agent 使用。

## 生成报告

当用户要求“生成/做一份 …分析报告 / 经营报告 / HTML 报告”时：

1. 使用技能 **smart-report**：`skills/smart-report/SKILL.md`（在 `.agents/skills/`、`.claude/skills/` 下均有指向它的软链接）。
   若当前 agent 不能加载技能，读取该文件全文并严格按其流程执行；也可以调用 MCP 工具 `get_skill_guide` 获取同样的内容。
2. 所有数据访问只通过 MCP 服务 **smartreportgen**（`http://localhost:18600/mcp`）。
   Codex CLI 可通过 `codex mcp login smartreportgen` 登录海尔 IAM 并自动携带 IAM access token。使用 Bearer 配置的客户端可传入 海尔 IAM access token。
   不要直连数据库，不要编造数据；遇到 `permission_denied` 不要尝试绕过，应告知用户申请权限。
3. 多表场景先逐表探查和查询，核实每张表可用的指标、粒度及覆盖范围，再逐项将场景分析思路映射到指标、来源表、查询和支撑数值。用 `run_query` 的 `tables_used` 复核来源。业务 Markdown 模板必须记录逐表指标、各分析项的证据和缺口，以及筛选和样式。调用 `save_report_template(template_type="business", ...)` 保存，并通过 `get_report_template` 读取指定版本。用 `run_query(snapshot=true)` 获取各组报告数据的快照 ID。Agent 编写内联 CSS、JavaScript 与页面资源的 HTML，每组数据使用一个快照引用。通过 `preview_report_html` 下载展开后的 HTML，验证离线打开、筛选和着色后用 `save_report` 提交含引用的文档并关联模板版本，服务端保存展开的单文件。
4. 保存新报告及修订版本前，按技能 R-01–R-10 和 `skills/smart-report/references/display-contract.md` 执行源 HTML 审计、展开文件审计及六类展示面/边界复核。使用 `skills/smart-report/assets/audit_report.py`；任一检查失败不得调用 `save_report` / `revise_report`。低优先级条目及 Agent 判断不能豁免高优先级展示要求。
5. 修改已有报告时，用 `get_report(include_html=true, omit_snapshot_data=true)` 读取用户选中的版本。用户明确指定版本时传入 `version`，没有选择时默认读取最新版。预览和 `revise_report` 均使用读取的 `revision` 作为 `base_version`，以便复用原数据并在同一报告下追加版本。维护已有结构化模板时可调用 `get_template_spec`。

各 agent 的 MCP 接入方式见 `docs/DEPLOYMENT.md` 的「接入 Agent」一节。管理台 HTTP 接入见 `docs/HTTP_API.md`。

## 开发本仓库

- 服务代码：`app/smartreportgen/`（FastAPI 管理 API + MCP 服务）。部署与配置：`docs/DEPLOYMENT.md`。
- 修改后执行 `docker compose build && docker compose up -d`。已保存的报告是快照，变更报告内容时保存新版本。
- 技能只维护 `skills/smart-report/` 这一份；镜像构建时会被复制进 MCP 服务，修改后需要重新构建。
- 调试 MCP：`docker compose exec -T -e MCP_TOKEN=<令牌> mcp python - call <tool> '<json参数>' < scripts/mcp_call.py`
