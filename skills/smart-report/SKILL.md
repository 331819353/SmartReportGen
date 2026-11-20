---
name: smart-report
description: 智能报告生成（SmartReportGen）。按业务场景新建、重跑或修订经营与分析报告。通过 smartreportgen MCP 取证，从报告模板分轮编排内容，由服务端生成、验收并保存交互式离线 HTML。
---

# 智能报告生成

业务人员通过自然语言提交需求、审阅预览和提出修改。业务数据统一通过 `smartreportgen` MCP 获取，权限由海尔 IAM 和系统授权确认。遇到 `permission_denied`，说明缺少的权限及其对分析的影响。

## 1. 按需求选择路线

### 修改已有报告

1. 有报告链接或 ID 时直接读取。否则用 `list_reports(keyword=...)` 查找与需求匹配的可访问报告。唯一明确候选直接使用。多个候选都符合需求时列出名称、当前版本和链接，请用户确认后开始修改。
2. 调用 `get_report(report_id, version, include_source=true)`。用户指定版本时显式传入，省略时读取该员工已选版本或最新版。记下实际返回的 `revision` 作为 `base_version`。需要模板时使用 `get_report_template(template_id, report_id, report_version)`。
3. 纯样式或换图直接进入第 3 步，基准快照已有所需事实时复用原数据。改期间、指标、粒度或数据范围时回到第 2 步核实受影响的取数和绑定。历史自编 HTML 可用 `preview_report_html` 查看静态诊断。迁移后走正式发布。

### 新建报告

1. 场景未知时，用原始需求调用 `search_business_scenarios(query, include_context="structured"|"free")`。按业务对象、分析口径和数据范围判断是否匹配已有授权场景。高相关且无歧义的首个结果会附第 2 步所需 `context`。确定使用该场景后直接复用。用户已明确指定 `dataset_id` 时直接用第 2 步的 `prepare_report_context` 核对授权并读取上下文。
2. **只有找不到适用场景时才进入「构建业务场景」分支。** 可换场景名称或业务术语再检索一次。若已知场景存在但当前员工缺少访问权，引导申请授权。否则可先调用 `list_scene_datasources` 查看当前账号已获开放的数据源、表和固定行范围，征得用户确认后通过 `save_business_scenario` 直接协助构建业务场景与官方指标；或给出工具返回的 `scenario_management_url` 引导用户打开「业务场景 → 新建业务场景」（支持可视化配置官方指标）。
3. 场景确定后，数据源固定的行范围持续生效，随即从第 2 步开始设计。缺少可引用数据源时，联系管理员配置数据源或开放所需表。

### 按模板重跑

确认模板版本和新账期，复用第 2 步的设计，再按新参数同批取数并走第 3 至 5 步。

## 2. 根据分析思路、表元数据和样式设计模板

1. **一步加载上下文。** 复用检索返回的 `context`，或调用 `prepare_report_context(dataset_id, mode="structured"|"free")` 一次获取业务场景（`analysis_approach`、授权表、固定行范围、`semantic_metrics`）、前 10 张授权表的轻量画像、生效样式（含 `style_id`）及模板契约。表数量超过 10 张且需要查看更多候选时，再用 `describe_tables(include_profile=true, offset=...)` 翻页查看。单表需要全表统计时才调用 `describe_table(mode="full")`。
2. **确认事实和联动。** 上游已有正式汇总值时，优先按期间、组织等维度筛选、直取所需字段，例如总体值、目标、同比和完成率。缺少预聚合口径时，使用场景负责人登记且允许当前分组的官方公式（支持 `when`/`denominator_when` 跨行条件聚合，过滤字段独立于分组 `dimensions`）。`QueryPlan` 支持区间（`gte`/`lte`）、`in`/`not_in`、`ne` 及受控维表 `joins`。探查阶段可用 `run_query` 核实字段、值域、唯一性及实际引用的 `tables_used`；**正式报告所需的单组或多组快照统一用 `query_report_data(..., sample_rows=200)` 一次按同批次获取**（≤200 行且未触发上限时直接复用其 `sample_rows` 与 `snapshot_id` 传入 `bindings`）。当前筛选缺少同口径唯一事实时，相关结论、数字和状态一起显示缺数。结构化正文用 `{{metric}}`（支持 `format.kind="text"` 绑定账期、名称等事实），自由版式正文用 `<srg-val>` 绑定数字。多粒度筛选下需要切换总计与明细的图表/表格，可为卡片配置 `sources` 或 `where`。
3. **落实生效规则。** 使用 `prepare_report_context` 返回的 `style` 或单独调用 `get_style_rules(..., include_css=false)`。读取 `reporting_rules` 的跨场景事实、展示和交付规则，并将展示默认值和 `specified` 的样式按当前用户明确要求、场景规则、通用规则、内置默认的优先级应用。用 `format` 声明精度、单位、比例尺度和判定阈值。按需求配置主题、卡片、图例和状态色。
4. **保存设计结果。** 按需求选模板。标准卡片和图表用 `save_report_spec`（更新协作模板可传 `expected_version`）。需要自定义版式、按模块拼装或更细的 ECharts 视觉时，用 `save_free_report_template`，保存页面骨架、分模块 HTML/CSS 和受管数据槽位。两类模板都保存取数计划、字段绑定、筛选联动和展示规则。使用返回的 `template_id` 与版本进入第 3 步。用 `business_summary` 记录用途和重要口径，有未覆盖的分析项或权限限制时在交付中说明。

## 3. 基于模板多轮拼装 HTML，按模块调整

- **先搭骨架，再补各块。** 模型按需求安排结论、指标卡、图表和表格。每轮聚焦受影响的章节或卡片，保持同一筛选口径与数据绑定。业务人员通过对话提出修改，模型负责更新内部模板定义。
- **结构化模板。** 按章节和卡片调整 `spec`。报告纯样式调整可在结构化预览传 `theme_overrides`。
- **自由版式模板。** 先建页面骨架、模块和全局绑定。每轮只通过 `patch_free_report_template(template_id, expected_version, modules)` 提交受影响的已有模块。增删模块、改全局 CSS 或更换受控取数计划时保存完整模板。按返回的新版本继续迭代。冲突时先读取最新版本再合入当前模块。正文和卡片数字由 `<srg-val m="指标引用">` 注入，图表和表格由 `<srg-output m="模块内输出引用">` 注入。原生 ECharts 声明式视觉选项由服务端验证，数据、标签格式和状态由内置运行时提供。[自由版式契约](references/free-report.md)
- **复用事实。** 新报告已有同批快照时预览传 `bindings`。纯展示修订传 `report_id` 和实际读取的 `base_version`，预览省略 `bindings` 与 `parameters`。新账期传 `parameters` 同批重取。增加基准中未投影的字段时按当前权限重新取数。
- **换图示例。** 同口径期初、期末和各项增减额已在基准快照时，可以只把图表模块的输出换成瀑布图并复用数据。期初期末用灰色放首尾，上涨用绿色放左、标签在下，下降用红色放右、标签在上。

## 4. 正式发布前验收

结构化模板调用 `preview_report_spec`，自由版式调用 `preview_free_report`。新建时可填模板 ID 和版本，修订时填 `report_id` 和修订分支锁定的 `base_version`。服务端检查字段与数据范围，审计源和展开后的 HTML，并用 Chromium 核对正文、卡片、表格、图表、筛选与重置、状态颜色、桌面和窄屏、离线运行。全部受测状态均无有效卡片或指标时，预览会阻断发布。请核查返回的 `warnings` 及 `verification.empty_on_default` / `always_empty_cards` / `always_missing_metrics`，确认没有因筛选映射遗漏或类别重复导致的空图。业务归因和有歧义的口径仍需根据证据核实。验收发现问题时回到相应环节修正，重新预览。通过 `get_preview_file(preview_id, offset, chars)` 可以在已登录的 MCP 通道分页读取正式预览。

## 5. 锁定验收结果并保存版本

- 保存第 4 步验收通过的 `preview_id`。新发布用 `save_report(name, preview_id)`。修订用 `revise_report(report_id, name, base_version, preview_id)` 追加版本。协作时可用 `expected_version` 检查最新版本是否已变化。内容修改后重新验收，使用新的预览 ID。
- 交付报告 ID、版本、固定链接、离线下载链接、主要结论和口径缺口。分享前，接收人须已登记并拥有该版本的来源数据权限。读取和下载时复核当前授权。

## 参考

- [自由版式模板和模块更新](references/free-report.md)
- [声明式模板和取数计划](references/declarative-report.md)
- [事实及数据范围约定](references/fact-contract.md)
- [展示契约与服务端验收](references/display-contract.md)
