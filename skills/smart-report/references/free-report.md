# 自由版式报告

自由版式用 HTML/CSS 表达结构和视觉。业务人员提供自然语言需求，Agent 编写版式与数据槽位。两类模板共用场景授权、同批快照、官方指标、浏览器验收、发布和版本历史。

## 模板与模块

- `get_free_report_spec` 返回字段定义。用 `save_free_report_template(name, dataset_id, free_report)` 保存首版。
- `free_report` 继承 `queries`、`filters`、`metrics`、`theme` 和生效展示规则，页面使用 `layout`、`css` 与 `modules`。
- `layout` 放普通 HTML 容器，通过 `<srg-module m="bridge"></srg-module>` 放置每个模块一次。存在全局筛选时，布局须放一个 `<srg-filters></srg-filters>`。
- 每个模块有 `html`、可选 `css` 和 `outputs`。模块 CSS 仅选择该模块的内部元素。页面级 CSS 可排版多个模块，支持 Grid、Flexbox、响应式条件和页面级关键帧。HTML 使用显式闭合的语义元素。脚本、事件、外部资源、行内样式、CSS 生成文字及手填业务数字会被拒绝。
- 在正文、结论、卡片或任意文字节点中使用 `<srg-val m="closing" badge></srg-val>`。可用 `state` 显示绑定指标的状态文案；可以重复使用同一个指标。日期、账期等事实可使用 `format.kind="text"`。表格和图表通过 `<srg-output m="detail"></srg-output>` 引用模块内的同名输出。
- 系统注入当前筛选对应的事实、精度、单位、比例尺度、状态和缺数。官方字段按快照声明校验比例尺度。推荐把生效颜色写入 `colors` 或主题令牌，CSS 不覆盖语义颜色。

布局示例

```html
<main class="overview">
  <header><h1>经营变动分析</h1><srg-filters></srg-filters></header>
  <srg-module m="summary"></srg-module>
  <srg-module m="bridge"></srg-module>
</main>
```

模块内容示例

```html
<article class="bridge-panel">
  <h2>期初到期末</h2>
  <srg-output m="detail"></srg-output>
</article>
```

## 图表、修订和发布

- `outputs.detail` 声明图表类型、查询别名、字段、筛选映射与格式，沿用结构化报告的表格和既有图形类型。可选 `option` 接受原生 ECharts 视觉属性，支持网格边距、背景、文字、系列边框与阴影、环形半径、中心和折线符号等。装饰性百分比和色值直接写配置。数据、业务文本、提示格式、数据变换和自定义回调由系统运行时管理。瀑布图保留系统绘制的柱子、桥接位置、颜色和标签口径。
- 源快照包含期初、期末和变动项时，同一套字段可把 `type` 从 `table` 改成 `waterfall`。需要角色字段时通过 `waterfall.role_field/opening/closing/change` 指定字段和标记。系统以十进制定点规则核对首尾等式，按正向、零和负向分组保留组内来源顺序。
- 只改已有模块时调用 `patch_free_report_template(template_id, expected_version, modules)`，提交要替换的完整模块及其 CSS 和输出，其他模块原样保留。新增模块、修改全局样式和绑定时，用 `save_free_report_template` 提交完整模板及 `expected_version`。读取历史模板时用 `get_report_template`。
- 用 `preview_free_report(template_id, template_version, report_id, base_version)` 验收基于原数据的修订，省略 `bindings` 和 `parameters`；改账期则传新的 `parameters`。如基准版本未保留新模块所需的字段，按当前权限重新取数。验收结果的 `snapshot_source` 显示 `reused_base`、`captured` 或 `provided`，`timing` 返回工具及各阶段的毫秒耗时。
- 按原流程用 `save_report` 或 `revise_report` 提交已验收的 `preview_id`。正式验收覆盖数据槽、表格、图表、数据筛选、显示状态以及桌面、窄屏和离线运行。小筛选空间逐组合验证，大空间按代表状态抽查。预览验收后修改内容须重新预览。
