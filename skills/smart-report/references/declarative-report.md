# 声明式报告契约

`get_report_spec()` 返回完整 JSON Schema。Spec 以查询名连接筛选、指标和图表。参数名和值可随账期变化，报告版本保存实际渲染的事实。

## 示例

以下示例假定已授权表 `sales`，管理员已在场景登记官方指标 `attainment`，它的原始尺度为 `decimal`，允许按 `region` 和 `month` 组织数据。常用模板需要一条总体计划和一条区域计划，二者在同一读取批次执行。正式事实已有比率字段时，计划可改为 `fields` 直接投影。

```json
{
  "title": "销售执行情况",
  "queries": {
    "total": {"table":"sales","metrics":["attainment"],"parameters":{"month":"period"}},
    "regions": {"table":"sales","metrics":["attainment"],"dimensions":["region"],"parameters":{"month":"period"}}
  },
  "filters": [{"id":"region","label":"区域","query":"regions","field":"region"}],
  "metrics": {
    "attainment": {
      "sources":[{"query":"total","field":"attainment"},{"query":"regions","field":"attainment","by":{"region":"region"}}],
      "format":{"kind":"rate","scale":"decimal","signal":"attainment","threshold":"1","warning_threshold":"0.95"}
    }
  },
  "sections":[{
    "title":"目标达成",
    "narrative":"当前范围的完成率为 {{attainment}}。",
    "cards":[{"title":"完成率","type":"kpi","metric":"attainment"}]
  }]
}
```

调用 `preview_report_spec(template_id=..., parameters={"period":"2026-09"})`。如先要检查行级证据，将 `queries` 和 `parameters` 传给 `query_report_data`。预览的 `bindings` 应提供该次查询的每个别名及返回的快照 ID。快照有 24 小时有效期。预览验收后的 HTML 按自身有效期可直接提交。

## 绑定规则

- `filters.default=null` 表示全部。指标以所属筛选维度当前选择的集合匹配 `sources[].by`。空 `by` 可绑定正式总计；`where` 可准确选中同表中的正式总计行。没有匹配项或匹配多行时显示缺数。完全未出现在该指标 `sources[].by` 中的筛选器对该指标不生效，报告须说明其固定口径。
- 卡片（表格与图表）同样支持单一 `query`/`by`/`where`，或多粒度切换 `sources: [{"query":..., "by":..., "where":...}]`（不能与顶层 `query`/`by`/`where` 同时出现）。当全局筛选切换到「全部」（`null`）时，图表可自动命中汇总源或 `where` 指定的总计行，避免多区域事实混入同一 X 轴引起类别重复。
- 正文可使用 `{{metric}}` 绑定 `format.kind="text"` 指标展示动态账期、机构名等文本事实；文本与标题放行明确日历年份/月份、列表序号、通用业务代号（如 `B2B`、`Q3`、`Top10`）及常见抽象结构量词，其余业务数字均须通过数据槽位绑定。
- 查询计划 `QueryPlan` 支持：
  - `where` 值支持标量（`=` / `IS NULL`）、数组（`IN`，`[...]` 中包含 `null` 时等价 `IN (...) OR IS NULL`）或条件对象 `{"in": [...], "not_in": [...], "ne": ..., "gt": ..., "gte": ..., "lt": ..., "lte": ...}`；
  - `parameters` 支持标量参数键（`"month": "period"`）或条件绑定对象（如 `{"gte": "start_month", "lte": "end_month"}`、`{"in": "months"}`）；
  - 受控维表关联 `joins: [{"table": "dim_org", "on": {"org_code": "code"}, "fields": ["org_name"], "type": "left"}]`，所有关联表同样先校验授权并自动注入行级条件；
  - `order_by: [{"field":"指标或字段","direction":"desc"}]` 对全量结果排序。表格及图表声明 `columns`，至多显示 `limit` 行/点。图表首列是文本类别，其余列是度量，类别重复或超过容量时会提示缩小范围。原值和定点数用于文本及状态，绘图坐标使用 JS Number。
- `format.kind` 支持 `number`、`rate`、`delta`、`rate_delta`、`text`。比率须明确 `scale=decimal|percent`。数字换算可设 `divisor` 与 `unit`（单位支持 `m²`、`CO₂` 等常见上/下标符号），精度为 0 至 6 位。 `signal=trend|attainment` 控制好坏，`direction=lower` 表示下降或低于阈值为好。预警阈值处于达标线的未达标一侧。正文可用 `{{metric.state}}` 随筛选显示状态。
- 更新结构化模板 `save_report_spec` 时可传入 `expected_version` 做并发版本检查；预览返回 `warnings` 会提示默认首屏空卡片、重复类别或全部筛选下均空图的问题。
- 旧结构化 Markdown 和历史自编 HTML 是历史格式。迁移历史模板时可新建声明式定义，并用原报告 ID 和版本追加新的可共享报告。

## 修改样式和图形

- 单份报告使用 `theme` 设置主题色、页面底色、字体字号、封面、目录、吸顶筛选、卡片间距或表格斑马纹。完整令牌列表见 `get_report_spec().theme_tokens`。按用户明确要求覆盖相应令牌，其他取值继承已生效的场景与通用规则。仅改主题时可用 `preview_report_spec(report_id, base_version, theme_overrides={...})` 复用原数据。
- 章节和卡片按数组顺序排列。卡片 `span` 以 12 栅格确定宽度，`height` 以像素指定图表高度、表格滚动高度或指标卡最小内容高度。窄屏自动重排。
- 常规图表支持柱、折线、面积、饼图、环图和柱线组合。柱图支持横向和堆叠。用 `chart` 配置图例位置、数据标签、网格、分类色板、平滑曲线和环形半径。右侧图例会在窄卡片上移至底部。饼图设置 `inner_radius: 0` 为实心，历史默认保留环形。组合图在列上配置 `series: "bar" | "line"`，需要不同单位时用 `axis: "secondary"`。
- 改图时读取基准版本 Spec，保留原数据绑定并提交更新后的 `spec`。版式修订省略 `parameters` 和 `bindings`，服务端复用基准 HTML 内已交付的快照字段并重新验收。新增原版本没有交付的字段时需按当前权限重新取数。

### 期初至期末瀑布图

卡片使用 `type: "waterfall"`。绑定同一口径下的名称列和数值列，需包含权威的期初、期末及各项增减额。若名称列直接包含「期初」和「期末」，可省略 `waterfall`。有独立角色字段时，可按下面的格式指定实际值。期初和期末分别为灰色首尾柱。上涨项从左起排列，使用报告的 `up` 色，数值在柱下。下降项排列在右侧，使用 `down` 色，数值在柱上。要落实绿涨红跌，可在报告 `colors` 设 `up: "#047857"` 和 `down: "#B91C1C"`。

```json
{
  "title": "收入变动瀑布图",
  "type": "waterfall",
  "query": "income_bridge",
  "span": 12,
  "height": 360,
  "columns": [
    {"field":"item","label":"变动项","format":{"kind":"text"}},
    {"field":"amount","label":"金额","format":{"digits":0,"unit":"万元"}}
  ],
  "waterfall": {
    "role_field":"role",
    "opening":"opening",
    "change":"delta",
    "closing":"closing",
    "total_color":"#6B7280"
  }
}
```

同一增减组保留取数计划的行顺序，零变动居中。浮动柱的几何使用精确十进制路径校验。若期初或期末缺失、重复，或增减额与两端对不上，卡片显示明确提示。柱标签和工具提示读取源端值。率值瀑布图的两端用百分比，增减项按报告的率差符号设置显示，源值必须使用同一比例尺度。

## 官方指标与数据连接

有数据集写权限的场景负责人通过 `POST /api/datasets`、`PUT /api/datasets/{id}` 或 MCP `save_business_scenario` 登记 `semantic_metrics`（管理台支持可视化编辑与 JSON 源码切换）。键是指标名，每项固定 `table`、`field`、`op`、允许的分组 `dimensions`、`scale` 和含义。公式类型包括 `sum`、`avg`、`min`、`max`、`count_distinct`、`ratio_of_sums`、`difference_of_sums`、`growth_of_sums`。后三者额外指定 `denominator`。可选设 `when` 与 `denominator_when`（如分别匹配实际行与目标行 / 本期行与同期行）实现跨行指标聚合；`where` / `parameters` 过滤字段默认放行该表全部合法字段（与分组 `dimensions` 解耦，或通过 `filter_dimensions` 显式限定白名单）。增长率是两个聚合同粒度值的差与基准之比。比率或增长率零分母返回空值。官方公式按照数据源类型执行聚合和服务端十进制运算；任意表达式仍不可进入报告计划。

HTTP API 来源的单页端点须声明 `complete: true` 或提供可信总数。游标分页在 `pagination` 中设 `cursor_param`、`next_cursor_path`，可选设 `total_path` 与 `max_pages`。每页返回游标字段，末页为 null 或空串。也可用 `page_param` 或 `offset_param` 与 `total_path` 配置页码或偏移分页，并按接口传 `page_size_param`、`page_size` 和 `start_page`。多页固定快照用 `revision_path`，或者明确声明数据内容不可变 `immutable: true`。同一版本号须由上游保证对应同一批数据，多接口报告要求共同版本号。字段可用 `column_types` 显式声明，例如 `{"amount":"DECIMAL(28, 12)"}`。
