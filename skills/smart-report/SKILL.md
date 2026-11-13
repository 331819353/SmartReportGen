---
name: smart-report
description: 智能报告生成（SmartReportGen）。当用户要求生成分析报告、经营报告、数据报告、看板式报告或 HTML 报告，或要求查看、切换、修改已有报告的版本时使用。通过 smartreportgen MCP 核实数据并保存业务 Markdown 模板，由 Agent 编写、验证并保存内联 CSS、JavaScript 和数据的交互式单文件 HTML。
---

# 智能报告生成（SmartReportGen）

依赖 MCP 服务 `smartreportgen`（Streamable HTTP，默认 `http://localhost:18600/mcp`）。
若工具不可用，提示用户先部署服务并为所用 agent 注册 MCP（见部署文档 `docs/DEPLOYMENT.md`「接入 Agent」），不要凭空编造数据。

本技能与 agent 无关（Claude Code、Codex、Cursor 等均可使用）。没有技能机制的 agent 可调用 MCP 工具 `get_skill_guide` 获取本文全文。

**认证与权限**：Codex CLI 可用 `codex mcp login smartreportgen` 通过浏览器登录海尔 IAM，并自动携带 IAM access token。使用 Bearer 配置的客户端可传入 海尔 IAM access token。工号决定可访问的场景和表。
- 检索、取表、探查、查询、校验、渲染都只覆盖**已授权**的场景与表；任何 SQL（含 JOIN / 子查询）引用未授权表都会被拒绝。
- 收到 `error_type: permission_denied` 时**不要改写 SQL 绕过**，直接告诉用户缺少哪张表/哪个场景的权限、应向管理员申请；可在已授权范围内给出降级方案（例如去掉依赖该表的章节）。
- 401 / 令牌无效或过期时，按客户端的认证方式重新登录或更新令牌。Codex CLI 使用 `codex mcp login smartreportgen`。
- 普通员工可以读取和修改本人创建的模板，并运行本人创建的结构化模板。分享权限决定能否查看或编辑报告各版本，数据查询仍按员工的数据权限执行。

每份新报告先将需求、已核实的数据证据、分析与展示设计整理为业务 Markdown 模板，保存到模板库。用少量查询证据分析，用服务端 JSON 快照为 HTML 提供完整的交互数据。Agent 依据模板编写 HTML，预览验证后保存到报告库并关联模板版本。
最终回复给出需海尔 IAM 登录的报告链接、核心结论摘要和需要用户确认的口径假设。

## 流程总览

| 步骤 | 目的 | MCP 工具 |
|---|---|---|
| a | 解析需求 → 检索已授权场景 → 获取分析思路和已授权的数据表 | `search_business_scenarios` → `get_dataset_tables` |
| b | 探查各表 → 按分析思路核对覆盖范围 → 获取结论证据和报告数据快照 | `describe_tables`、`describe_table`、`run_query` |
| c | 设计筛选联动与结论指标着色 | 确定交互与指标口径 |
| d | 获取样式约束（合集）→ 默认遵守，必要时按判断调整，其余自由发挥 | `get_style_rules` |
| d 与 e 之间 | 整理并保存业务 Markdown 模板 | `save_report_template(template_type="business")` → `get_report_template(version=...)` |
| e | 编写引用快照的 HTML，下载预览验证离线打开、筛选和着色 → 保存报告 | `preview_report_html` → `save_report` |
| f | 查看或修订已有报告 | `get_report`、`revise_report` |

---

## a. 解析需求，锁定业务场景

1. 先从用户需求中提炼：**分析对象、时间范围、关注维度、关注指标、对比方式（同比/环比/目标）、受众**。缺失项不必追问，采用合理默认并在最终回复中说明。
2. 调用 `search_business_scenarios(query=用户原话, top_k=3)`。
   - 取第 1 名；若返回 `tip` 提示相关度接近或偏低，阅读各候选的 business_description 判断，仍无法确定时**向用户确认**再继续。
   - 无命中：可能是业务术语不同，也可能是**没有该场景权限**（检索只覆盖已授权场景）。换术语重试一次，仍无则告知用户并停止。
3. 调用 `get_dataset_tables(dataset_id)` 获取场景的分析思路（`analysis_approach`）、**你有权限的**表或视图、各表取值范围（`row_filters`）及数据源类型（决定 SQL 方言：postgresql / mysql / starrocks / oracle / api=DuckDB）。场景取数自动应用这些条件；把影响分析口径的范围写进报告。
   若返回 `unauthorized_table_count > 0`，说明还有表无权访问：只用已授权的表设计报告，并在最终回复中说明哪些分析因权限受限未覆盖。

## b. 探查数据，确定分析目标与报告结构

**先探查，后设计；结论必须来自查询结果，禁止编造数字。**

1. 把场景的 `analysis_approach` 作为探查和设计的重要依据。结合用户关注的问题，确定先查哪些指标、对比哪些对象、验证哪些假设。若场景未填写分析思路，就从用户需求与实际数据出发。
2. **先逐表核实可用指标。** 用 `describe_tables` 获取授权表清单和字段，按 `next_offset` 翻完所有页。依次处理每张表或视图，先用 `describe_table` 核实字段、数据粒度、主键或候选关联键、时间和维度范围及缺失情况，再用只引用该表的少量汇总查询核实可直接使用或计算的指标、口径、单位及有效覆盖范围。例如分别核实实绩表的销量与目标表的目标量。记录每张表提供的指标和已核实的汇总值。确实无关或无法使用的表，记录原因及对分析的影响。前几行样本用于核对字段和行结构，汇总结论由完整范围上的查询支持。
3. **再逐项映射分析思路并核实结论。** 将场景的 `analysis_approach` 和用户问题拆为可验证的分析项。为每一项标明所需指标、来源表及字段、统计口径、查询和支持结论的具体数值，用 `tables_used` 核对查询实际覆盖的表。需要跨表计算时，基于逐表结果验证关联键、关联基数、未匹配记录及关联前后的汇总，再用 JOIN、CTE 或同口径的独立汇总计算；需要时先将各表聚合到共同粒度。每项标明「证据齐全」或「有缺口」，注明缺失的表、字段、期间或权限及影响；只有证据齐全的分析项可写成确定性结论。
4. 对报告需要的每组数据调用 `run_query(snapshot=true, sample_rows=5)`，记录每个 `snapshot_id`、列、行数、来源表和用途。有样本结构后可把 `sample_rows` 设为 0。按筛选与图表所需粒度保留同比的本期和基期值、达成率的实际和目标值。`truncated=true` 时未创建快照，需调整聚合或按互不重叠且能覆盖全量的范围分批，并核对汇总结果。快照有效期为 24 小时。

## c. 全局筛选与联动

- 按受众会切换的分析口径选择筛选器。用同一份筛选状态计算图表与结论，覆盖页面初始加载、筛选切换及重置。不同查询的筛选字段需要映射时显式处理，目标粒度无法支持某些筛选时注明适用范围。
- 结论正文中的同比和达成率都绑定实时指标。每次筛选后，从当前数据先聚合分子与分母，再算同比 `(本期 / 基期) - 1` 与达成率 `实际 / 目标`。同一次刷新更新数字、颜色和对应的达标或升降描述。
- 同比按原始数值判定，正值用 `color-trend-up`（默认绿），负值用 `color-trend-down`（默认红），零为中性色。达成率达到或超过业务阈值用达标色（默认绿），低于阈值用未达标色（默认红）。优先用用户或场景给出的阈值，缺省用 `1.0`（100%）。用未舍入数值比较，按展示精度格式化。用正负号、涨跌或达标文字补充颜色含义。
- 空结果、无有效分子分母或分母为零时，显示 `—` 并清除该指标之前的颜色及状态。静态结论数字若只适用于某个固定口径，须标明该口径。

## d. 样式设计

生效顺序为 **你的决定 ≥ 业务场景规则 ≥ 通用规则 ≥ 内置经营报告样式**。默认主题提供排版、页面布局和配色。根据数据和受众决定如何呈现报告。

正式报告的布局、图表、筛选维度和配色由 Agent 根据用户需求、数据和适用的样式规则决定。

1. 调用 `get_style_rules(business_scenario, query=用户需求)`，得到 `specified`——业务场景规则（当前用户本人为该场景配置的）
   ≥ 通用规则（管理员配置的）≥ 内置样式 按优先级合并的约束：
   - `specified.theme` 提供视觉样式取值，例如封面、目录、章节面板、图表配色和表格排版。编写 HTML 时将适用取值落实到 CSS 和图表配置。
   - `specified.items`：展示要求（好坏判定 / 涨跌颜色 / 数字格式 / 文案要求 / 其他展示要求），各类别内业务场景规则的条目排在前面，同类冲突时以排在前面的为准。
   - **默认页面布局**：标题背景和正文使用同一个最大宽度（`max_width`）与页面左右边距（`rre-page-pad`），整块等宽、居中、左右边缘对齐。背景应用于标题块，不铺满视口。`header_align` 只控制标题块内的文字对齐。
   - 开启目录（`toc`）时，宽屏将目录放在正文左侧的留白中，不占正文宽度。可用宽度至少达到 `max_width + 2 × (rre-page-pad + rre-toc-width + rre-toc-gap)` 时才使用侧栏；留白不足时将目录放到标题下方、正文之前，目录过长可在目录内部滚动。参考 [默认布局 HTML 示例](references/example-report.html)。
   - 内置页面布局中，指标卡、图表卡和表格卡在同一行时按最高的卡片等高拉伸，保留卡片间距并分配行内剩余宽度。单张卡片占满所在行，最后一行和窄屏换行后也遵循此规则。可用 `flex-wrap`、`align-items: stretch` 和卡片的 `flex-grow` 实现。
   - 同一报告内同类卡片跨章节复用同一套 CSS 类和图表基础配置。所有表格共用表头样式、行高、隔行底色及文字列和数值列的对齐规则。所有柱状图共用柱宽上限、圆角规则、坐标轴、网格、图例、标签和配色规则。其他同类卡片按同样方式复用。排序、单位、轴范围及正负、状态、分类配色按数据含义设置。
   - 章节安排、图表类型和筛选条件在 b、c 节决定。样式条目中涉及这些分析决策的文字不作为展示约束。
2. **默认遵守约束，确有理由时按自己的判断调整**：例如用户在对话中明确提出的要求（如“红涨绿跌”）、某条要求不适合本报告的数据。
   在 HTML 的样式或交互中落实调整。
   所有偏离约束的地方都要在最终回复的“口径与假设”中说明。
3. 未指定的视觉样式与展示要求由你按报告内容决定。结论正文的状态颜色要与页面底色保持足够对比度。
   保持整份报告一致，例如数据密集的报告减小卡片内边距与表格行高，面向管理层的概览让指标卡与头部居中（`kpi_align: center`、`header_align: center`）。

## d 与 e 之间. 编写并保存业务报告模板

1. 用业务人员能读懂的 Markdown 整理 a–d 的结果。写清报告用途、受众和业务问题。多表场景保留逐表指标清单（来源表、粒度、指标、覆盖范围、限制）和分析项与证据的映射（分析项、指标与来源、查询或口径、支撑数值、结论或缺口）。模板保存前核对两件事：场景分析思路与用户要求的每一项都有映射，每个确定性结论都有可复核的查询和数值。再按章节记录展示方式、默认筛选范围、联动和结论颜色规则。
2. 用 `save_report_template(name, markdown=全文, template_type="business", dataset_id, user_requirement, analysis_goal, business_summary)` 保存。完整文档放在 `markdown`，`business_summary` 只填一段用于模板列表的用途说明。用普通 Markdown 标题、段落、列表和表格表达，数据查询仍通过 MCP 执行。
3. 保存成功后，用 `get_report_template(template_id, version=返回的版本号)` 读取该版本，作为下一步的开发依据。后续如果发现口径或设计需要变更，先更新该模板并取得新版本，再依据该版本开发。

## e. 编写、验证和保存单文件 HTML

1. 根据已保存版本的业务模板，编写包含 `<!doctype html>`、`<html>`、`<head>` 和 `<body>` 的完整文档，内联 CSS、JavaScript、必要的图表库和图像。每份查询结果写一个空数据标签 `<script type="application/json" id="sales" data-srg-snapshot="实际返回的 snapshot_id"></script>`。多个结果使用不同 `id`，将这些标签放在读取它们的脚本之前。服务端将完整的 `{columns, rows}` 写进对应标签。页面通过 `JSON.parse(document.getElementById('sales').textContent)` 读取，以 `columns` 与 `rows` 对应字段。页面运行时不需要访问 JSON 文件、CDN 或接口，动态文案使用 `textContent` 等文本 API。
2. 调用 `preview_report_html(html=含快照引用的完整文档)`，用返回的 `url` 在浏览器预览，或用 `download_url` 下载展开后的 HTML，再以离线方式打开。在桌面和窄屏分别检查卡片行是否等高并铺满可用宽度。切换每个筛选维度，检查多条件组合及重置，核对图表、指标卡、表格和结论是否按适用范围一起更新。用 `run_query` 的汇总结果复核各表来源的数字。至少验证同比为正、负、零，达成率高于、低于、刚好达到阈值，以及空结果和分母为零时的文案、符号和实际颜色。正式数据缺少边界样本时可用独立测试数据验证计算，正式报告只使用已核实的数据。
   在报告实际展示容器中检查宽屏和窄屏：标题背景与正文左右边缘对齐、整页无横向溢出，目录在侧栏与标题下方正确切换，目录链接定位到对应章节。对照不同章节中的同类卡片，检查表格排版与同类图表样式在初始和筛选状态下一致。
3. 调用 `save_report(name, html=预览时提交的含引用文档, template_id, template_version)`。服务端保存展开后的离线单文件，报告版本不依赖临时 JSON 的有效期。传入实际用于开发的模板 ID 和版本，并用返回的 `url` 和 `download_url` 交付报告。创建人和管理员可在报告库按员工工号分享，接收人使用海尔 IAM 登录。

服务端的 `validate_report_template` 和 `render_report_html` 用于维护和运行已有的结构化模板。

## f. 修改已有报告

1. 用已有报告链接或 `list_reports(keyword=...)` 定位 `report_id`。候选不明确时，用标题、来源模板和当前版本让用户确认。`list_report_versions(report_id)` 可查看版本、基准版本和修改说明。
2. 调用 `get_report(report_id, include_html=true, omit_snapshot_data=true)` 读取该员工选中的版本，未选择时读取最新版。用户指定版本或给出带 `?version=` 的链接时，显式传入 `version`。用户要求切换默认版本时调用 `select_report_version(report_id, version)`，省略 `version` 可恢复跟随最新版。
3. 以返回的 `revision` 锁定本次修改的基准。有模板读取权限时，用该报告返回的 `template_id` 和 `template_version` 读取对应版本的模板。仅修改文字、样式或交互时，保留 `data-srg-snapshot="base"` 的空标签和对应 `id`，服务端从基准版本复用数据。刷新或更改数据时，用 `run_query(snapshot=true)` 重新取数，换成新快照 ID。旧报告中手工内联的数据会随原有 HTML 返回。
4. 按 e 节验证修改后的文件，预览时传 `report_id` 和 `base_version=读取的 revision`。调用 `revise_report(report_id, name, base_version=读取的 revision, html=预览时提交的文档)`，可用 `change_note` 记录改动。保存会分配新版本号并保留历史。用户从 v2 修改已有 v1–v4 的报告，结果是基于 v2 的 v5。
5. 回复中给出实际基准版本、新版本号、改动摘要和返回的固定版本链接。

## 最终回复格式

```
已生成报告：<标题>
- 报告链接：<url>（离线单文件：<download_url>）
- 报告：#<report_id> v<version>，业务场景：<场景>
- 业务模板：#<template_id> v<template_version>（在报告模板库查看）
- 核心结论：摘要，附支持结论的关键数字
- 口径与假设：时间范围、同比口径、未覆盖的维度/数据缺口、偏离样式约束之处及理由
- 权限说明（如有）：因无权访问而未纳入的表/分析
```

## 参考

- `references/example-report.html` 是可直接离线打开的默认布局示例，也演示筛选联动、结论动态着色和边界行为。需要时可调用 MCP `get_skill_guide(include_example=true)` 获取。
- 维护已有结构化模板时，参考 `references/template-spec.md`（MCP `get_template_spec`）和 `references/example-retail.md`。
