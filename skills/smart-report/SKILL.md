---
name: smart-report
description: 按业务场景新建、重跑或修订经营与分析报告。通过 smartreportgen MCP 获取授权快照，参考 Markdown 指南分轮拼装 HTML，并用 Chromium 校验和保存交互式离线报告。
---

# 智能报告生成

业务数据统一通过 `smartreportgen` MCP 获取。服务通过海尔 IAM 核实员工身份并按授权取数。收到 `permission_denied` 时说明缺少的授权及其影响，引导用户申请权限。

## 1. 确定新建或修订

- 新建或重跑时优先复用已有授权业务场景。场景未知时用原始需求调用 `search_business_scenarios(query, include_context=true)`。已知场景时调用 `prepare_report_context(dataset_id)`。高相关且无歧义的检索结果附相同上下文，含分析思路、授权表、行范围、官方指标、相关指南、前 10 张表的轻量画像和生效样式。
- 修订时用链接或 `list_reports` 定位，再用 `get_report(report_id, version, include_source=true)` 读取。多个候选匹配时请用户确认。记录返回的 `revision`、`latest_version`、`template_id`、`template_version`（及 `template_latest_version`），其中 `revision` 和 `latest_version` 分别用作修订的 `base_version` 和 `expected_version`。只改名称或说明时直接调用 `revise_report`，省略正文。
- 找不到适用场景时可换业务术语检索。需要新建或批量配置业务场景时，可通过 `list_scene_datasources`、`list_datasource_tables` 查看可用数据源、表与字段结构，再调用 `save_business_scenario` 或 `batch_save_business_scenarios`（支持 `dry_run` 预检、按场景名 Upsert 及管理员同步配置授权）协助建场景；也可提供工具返回的 `scenario_management_url`，引导用户在管理台配置或通过 Excel 批量导入。缺少所需表权限时联系管理员。

## 2. 必备报告模板与取数

**报告模板是生成和修订报告的必备产物，不可绕过，且必须在报告入库前保存。** 从上下文的 `templates` 或 `list_report_templates(keyword, dataset_id)` 找匹配指南：

- **新建或重跑报告**：
  - 仅当已有 Markdown 模板的取数口径、指标公式、章节结构、图表绑定与筛选联动完全匹配本次需求时，才通过 `get_report_template(template_id, version)` 读取并直接沿用该 `template_id` 与 `version`。
  - 若无匹配模板、需要基于旧模板调整分析/章节/口径，或基准是历史声明式/自由版式（`structured`）模板，**必须**先调用 `save_report_template`（传入 `dataset_id`）保存一份完整的 Markdown 指南并取得新的 `(template_id, version)`，再开始草稿与报告入库。禁止先保存报告再补模板，也不允许不绑定模板直接保存报告。
- **修订报告（含修改报告正文、数据、章节、样式、筛选或口径）**：
  - 修改报告正文时，**必须同步更新对应的报告模板**：先用 `get_report_template` 读取原模板（参考 `template_latest_version`），对照本次修改调用 `save_report_template(template_id=..., expected_version=..., dataset_id=...)` 保存新版模板；若基准报告未绑定模板，或当前账号无权修改原模板（非创建人），则基于本次修订后的完整分析思路与结构新建一份归属当前场景的模板。随后把新版 `(template_id, template_version)` 传入草稿或 `revise_report`。仅修改报告名称或 `change_note` 时沿用原模板。
- **模板正文要求**：按场景写清适用范围与账期参数、取数来源和粒度、指标公式与单位、章节各自回答的问题、图表及明细绑定、筛选联动和验收标准。有明确复用价值的 SQL 或版式片段可用代码块保留。旧版定义转换出的查询、绑定、HTML/CSS 可作为实现参考，取数和报告实现仍遵循当前授权、展示契约和离线验收。复杂场景可并行读取指南样式与翻页探查多张大表（`describe_tables` / `describe_table(mode="full")`）。

用 `run_query(dataset_id, sql, snapshot=true, sample_rows=50)` 获取汇总与明细快照，确认粒度、范围、比较基准、单位和缺失。优先读取正式汇总值，按确认口径在源端派生。无交叉核对关系的独立模块可并发取数；需要相互勾稽的数据须用单条 SQL 或 `query_report_data` 同批取数。纯展示修订沿用基准快照，换账期或口径时重取相关数据。[事实取数契约](references/fact-contract.md)

## 3. 初始化 HTML 草稿骨架

调用 `create_html_draft`，传标题、`dataset_id`、`snapshots`（数据块 ID 到 `snapshot_id` 的映射）及可选 `style_id`、`sections`（预置有序章节槽位）。**新建报告必须传入已保存的 `template_id` 和 `template_version`**，使交付报告可追溯到编写 HTML 时使用的指南；修订时传 `report_id` 和 `base_version` 恢复章节和基准数据，并在本步或 `upsert_html_section` / `revise_report` 中传入同步更新后的 `template_id` 和 `template_version`。全局 CSS、含 `<!--SRG_SECTIONS-->` 的骨架和筛选脚本放到 `head_css`、`shell_html`、`shared_js`。多章节并行编写前，须在此步锁定数据块字段、筛选订阅结构、章节槽位及各章独占的元素 `id` 前缀。

## 4. 分轮或并行拼装章节

简单报告可分 2–5 轮串行调用 `upsert_html_section`；含多个图表或明细章节时，可在契约锁定后按章节派生子智能体并行编写（单次 `sections` 批量提交，或基于预置槽位仅传各自 `section_id` 并发更新）。用 `SRG.rows(blockId)` 取数，`SRG.num(value)` 解包绘图数值，`SRG.initChart(element, option)` 初始化 ECharts，`SRG.section(sectionId)` 定位当前章节容器并保持全页元素 `id` 唯一。各展示面使用同一筛选状态，缺失数据给出可见解释。可用 `get_html_draft`（支持 `section_ids` 与 `"__shared__"`）读进度和局部源码。[HTML 拼装示例](references/html-assembly.md) · [展示契约](references/display-contract.md) · [子智能体并行编排](references/parallel-subagents.md)

## 5. 校验入库并交付

默认调用 `save_report(name, draft_id=...)`，修订调用 `revise_report(report_id, name, base_version=..., expected_version=..., draft_id=..., template_id=..., template_version=...)`。服务端强校验模板存在、归属同一业务场景且正文修订已同步更新模板版本，然后展开单文件、按需运行 Chromium 校验并保存版本。需显式审阅时，调用 `verify_html_draft(draft_id=...)`，可同时对照快照复核业务口径，确认后提交 `preview_id`（可同时补传更新后的 `template_id` 和 `template_version`）。失败时查看 `failed_stage`、`diagnostics.section_ids`、重名详情 `diagnostics.duplicate_ids` 和 `timing`。各章独立问题可并行修复，主智能体处理 `__shared__` 并汇总重验，最后单次提交入库。多份独立报告可按报告级子智能体全链路并行生成。交付报告版本、绑定的模板 ID 及版本、查看和下载链接，以及会影响使用的提示。
