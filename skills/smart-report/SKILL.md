---
name: smart-report
description: 按业务场景新建、重跑或修订经营与分析报告。通过 smartreportgen MCP 获取授权快照，参考 Markdown 指南分轮拼装 HTML，并用 Chromium 校验和保存交互式离线报告。
---

# 智能报告生成

业务数据统一通过 `smartreportgen` MCP 获取。服务通过海尔 IAM 核实员工身份并按授权取数。收到 `permission_denied` 时说明缺少的授权及其影响，引导用户申请权限。

## 1. 确定新建或修订

- 新建或重跑时优先复用已有授权业务场景。场景未知时用原始需求调用 `search_business_scenarios(query, include_context=true)`。已知场景时调用 `prepare_report_context(dataset_id)`。高相关且无歧义的检索结果附相同上下文，含分析思路、授权表、行范围、官方指标、相关指南、前 10 张表的轻量画像和生效样式。
- 修订时用链接或 `list_reports` 定位，再用 `get_report(report_id, version, include_source=true)` 读取。多个候选匹配时请用户确认。记录实际返回的 `revision` 作为 `base_version`。只改名称或说明时直接调用 `revise_report`，省略正文。
- 找不到适用场景时可换业务术语检索。需要新建场景时，提供工具返回的 `scenario_management_url`，引导用户从已开放的数据源配置。缺少所需表权限时联系管理员。

## 2. 查阅指南并取数

从上下文的 `templates` 或 `list_report_templates(keyword, dataset_id)` 找匹配指南。用 `get_report_template(template_id, version)` 读取章节、指标、取数和展示要求。用户要求沉淀分析方法时，通过 `save_report_template` 保存 Markdown 指南，更新时可传 `expected_version`。

用 `run_query(dataset_id, sql, snapshot=true, sample_rows=50)` 获取汇总与明细快照，确认粒度、范围、比较基准、单位和缺失。优先读取正式汇总值，按确认口径在源端派生。已登记的官方指标可用 `query_report_data` 同批取数。纯展示修订沿用基准快照，换账期或口径时重取相关数据。[事实取数契约](references/fact-contract.md)

## 3. 初始化 HTML 草稿骨架

调用 `create_html_draft`，传标题、`dataset_id`、`snapshots`（数据块 ID 到 `snapshot_id` 的映射）及可选 `style_id`、指南版本。全局 CSS、含 `<!--SRG_SECTIONS-->` 的骨架和筛选脚本放到 `head_css`、`shell_html`、`shared_js`。修订时传 `report_id` 和 `base_version` 恢复章节和基准数据。

## 4. 分轮拼装章节

根据内容分 2–5 轮调用 `upsert_html_section`，例如先做结论和核心指标卡，再加趋势与结构图，最后补明细表和经营建议。用 `SRG.rows(blockId)` 取数，`SRG.num(value)` 解包绘图数值，`SRG.initChart(element, option)` 初始化 ECharts。各展示面使用同一筛选状态，缺失数据给出可见解释。可用 `get_html_draft` 读进度和章节源码。[HTML 拼装示例](references/html-assembly.md) · [展示契约](references/display-contract.md)

## 5. 校验入库并交付

默认调用 `save_report(name, draft_id=...)`，修订调用 `revise_report(report_id, name, base_version=..., draft_id=...)`。服务端展开单文件，按需运行 Chromium 校验并保存版本。用户要求先审阅时，用 `verify_html_draft(draft_id=...)` 提供预览链接，确认后提交 `preview_id`。失败时查看 `failed_stage`、`diagnostics.section_id` 和 `timing`，修复对应章节再重试。交付版本、查看和下载链接，以及会影响使用的提示。
