# AGENTS.md

本仓库是 **SmartReportGen**：智能报告生成服务（MCP + Skill）。本文件供 Codex、Cursor、Copilot 等读取 AGENTS.md 的 agent 使用。

## 生成报告

当用户要求“生成/做一份 …分析报告 / 经营报告 / HTML 报告”时，使用技能 **smart-report**：[SKILL.md](skills/smart-report/SKILL.md)（在 `.agents/skills/`、`.claude/skills/` 下均有指向它的软链接）。若当前 agent 不能加载技能，读取该文件全文并严格按其流程执行；也可以调用 MCP 工具 `get_skill_guide` 获取同样的内容。

所有数据访问只通过 MCP 服务 **smartreportgen**（`http://localhost:18600/mcp`）。MCP 仅接受 `Authorization: Bearer <海尔 IAM access token>`。Codex 可用 `--bearer-token-env-var SMARTREPORT_TOKEN` 读取已从海尔 IAM 取得的令牌。不要直连数据库，不要编造数据；遇到 `permission_denied` 不要尝试绕过，应告知用户申请权限。

以 [smart-report 主流程](skills/smart-report/SKILL.md) 为准。收到需求后先区分新建与修订。修订从报告库定位基准，多个匹配候选先请用户确认。新建优先复用已有授权场景。只有找不到适用场景时，才通过 `search_business_scenarios` 返回的管理台入口引导普通用户选择管理员已配置并授权的数据源，自行配置分析思路和所需表，然后继续新建报告。

已知场景后用 `prepare_report_context` 读取分析思路、表画像、生效样式与 Markdown 模板指南摘要。用 `get_report_template` 读指南，用 `run_query(snapshot=true)` 取得正式事实快照。调用 `create_html_draft` 初始化数据块和筛选逻辑，可传 `sections` 预置有序章节槽位。分轮或派发子智能体并行调用 `upsert_html_section` 拼装原生 HTML/CSS/JS，按 `diagnostics.section_ids` 修复可独立处理的章节问题。默认把 `draft_id` 交给 `save_report` 或 `revise_report`，服务端通过离线 Chromium 校验后保存。需先审阅时用 `verify_html_draft`，确认后提交 `preview_id`。修订用实际读取的 `revision` 作为 `base_version`，用同时返回的 `latest_version` 作为 `expected_version` 检查并发修改。纯展示复用基准快照，换账期时重新取数并更换绑定。

各 agent 的 MCP 接入方式见[部署文档第 10 节：接入 Agent](docs/DEPLOYMENT.md#10-接入-agent)。管理台 HTTP 接入见[HTTP 接入说明](docs/HTTP_API.md)。

## 开发本仓库

- 服务代码：`app/smartreportgen/`（FastAPI 管理 API + MCP 服务）。部署与配置：`docs/DEPLOYMENT.md`。
- 修改后执行 `docker compose build && docker compose up -d`。已保存的报告是快照，变更报告内容时保存新版本。
- 技能只维护 `skills/smart-report/` 这一份；镜像构建时会被复制进 MCP 服务，修改后需要重新构建。
- 调试 MCP：在当前终端导出 `SMARTREPORT_TOKEN` 后，运行 `docker compose exec -T -e SMARTREPORT_TOKEN mcp python - call <tool> '<json参数>' < scripts/mcp_call.py`。
