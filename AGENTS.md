# AGENTS.md

本仓库是 **SmartReportGen**：智能报告生成服务（MCP + Skill）。本文件供 Codex、Cursor、Copilot 等读取 AGENTS.md 的 agent 使用。

## 生成报告

当用户要求“生成/做一份 …分析报告 / 经营报告 / HTML 报告”时，使用技能 **smart-report**：[SKILL.md](skills/smart-report/SKILL.md)（在 `.agents/skills/`、`.claude/skills/` 下均有指向它的软链接）。若当前 agent 不能加载技能，读取该文件全文并严格按其流程执行；也可以调用 MCP 工具 `get_skill_guide` 获取同样的内容。

所有数据访问只通过 MCP 服务 **smartreportgen**（`http://localhost:18600/mcp`）。Codex CLI 可通过 `codex mcp login smartreportgen` 登录海尔 IAM 并自动携带 IAM access token。使用 Bearer 配置的客户端可传入海尔 IAM access token。不要直连数据库，不要编造数据；遇到 `permission_denied` 不要尝试绕过，应告知用户申请权限。

以下编号与[技能流程总览](skills/smart-report/SKILL.md#流程总览)一致；新报告按第 1–6 步执行，第 7 步用于查看或修订已有报告：

1. **解析需求与业务场景。** 用 `search_business_scenarios` 检索已授权场景，再用 `get_dataset_tables` 获取分析思路和已授权的数据表。
2. **探查数据与核实证据。** 多表场景先用 `describe_tables(include_profile=true)` 分页批量探查，再逐表直接事实查询，核实每张表可用的指标、粒度及覆盖范围；已有字段和画像不再逐张重复探查，必要时才用 `describe_table(mode="full")` 补充完整技术统计。逐项将场景分析思路映射到指标、来源表、查询和支撑数值，用 `run_query` 的 `tables_used` 复核来源。用 `run_query(snapshot=true, sample_rows=200)` 获取各组报告数据的快照 ID；≤200 行且 `sample_limited=false`、`truncated=false` 时可直接以单次返回作为全量核数证据，避免重复取数。
3. **设计全局筛选与联动。** 同一筛选状态选择同口径的直接事实，同步更新结论、数字和颜色；缺失、重复或比例尺度未知时显示缺数并清除旧状态。
4. **获取规则与样式。** 调用 `get_style_rules(..., include_css=false)` 获取规则与 `style_id`，记录生效规则及优先级。低优先级条目及 Agent 判断不能豁免高优先级展示要求。
5. **保存业务模板。** 业务 Markdown 模板以紧凑表格记录逐表指标、各分析项的证据和缺口，以及筛选和样式。调用 `save_report_template(template_type="business", ...)` 保存并直接依据返回的 `template_id` 和版本开发；仅读取既有/历史模板时调用 `get_report_template`。
6. **编写、验证和保存 HTML。** Agent 编写轻量源 HTML（可用 `<style data-srg-style="style_id"></style>`、`<script data-srg-vendor="echarts"></script>`、`<script id="srg-formatters" data-srg-asset="report-formatters.js"></script>` 与 `<script type="application/json" id="..." data-srg-snapshot="..."></script>`）。保存新报告及修订版本前，按技能 R-01–R-10 和[展示契约与门禁](skills/smart-report/references/display-contract.md)执行源 HTML 审计、展开文件审计及六类展示面/边界复核，使用 `skills/smart-report/assets/audit_report.py`。通过 `preview_report_html` 下载展开后的 HTML，集中验证离线打开、筛选和着色；任一检查失败不得调用 `save_report` / `revise_report`。全部通过后用 `save_report(..., preview_id=...)`（或提交相同源 HTML）并关联模板版本，服务端保存展开的单文件。
7. **查看或修订已有报告。** 用 `get_report(include_html=true, omit_snapshot_data=true)` 读取用户选中的版本。用户明确指定版本时传入 `version`，没有选择时默认读取最新版。`omit_snapshot_data=true` 会自动折叠快照数据及内嵌的内置样式/ECharts/格式化模块。修订按第 6 步完成全部门禁；预览和 `revise_report` 均使用读取的 `revision` 作为 `base_version`（可用 `preview_id` 提交），以便复用原数据并在同一报告下追加版本。维护已有结构化模板时可调用 `get_template_spec`。

各 agent 的 MCP 接入方式见[部署文档第 10 节：接入 Agent](docs/DEPLOYMENT.md#10-接入-agent)。管理台 HTTP 接入见[HTTP 接入说明](docs/HTTP_API.md)。

## 开发本仓库

- 服务代码：`app/smartreportgen/`（FastAPI 管理 API + MCP 服务）。部署与配置：`docs/DEPLOYMENT.md`。
- 修改后执行 `docker compose build && docker compose up -d`。已保存的报告是快照，变更报告内容时保存新版本。
- 技能只维护 `skills/smart-report/` 这一份；镜像构建时会被复制进 MCP 服务，修改后需要重新构建。
- 调试 MCP：`docker compose exec -T -e MCP_TOKEN=<令牌> mcp python - call <tool> '<json参数>' < scripts/mcp_call.py`
