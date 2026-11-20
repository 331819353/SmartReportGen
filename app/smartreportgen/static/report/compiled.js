/* 声明式报告运行时。原值用于显示和状态判断，图形坐标才转换为 Number。 */
(() => {
  'use strict';
  const {spec, data} = JSON.parse(document.getElementById('srg-data').textContent);
  const d = window.SrgDisplay;
  const rows = Object.fromEntries(Object.entries(data).map(([key, block]) => [key,
    block.rows.map(row => Object.fromEntries(block.columns.map((field, index) => [field, row[index]])))
  ]));
  const selected = {};
  const charts = new Map();
  const token = value => JSON.stringify(value);
  const exact = value => value?.$decimal ?? value;
  const numeric = value => typeof value === 'number' || typeof value?.$decimal === 'string';
  const threshold = value => value == null ? null : {$decimal: value};
  const stateLabels = {up: '向好', down: '承压', neutral: '无变化', met: '达标', warning: '关注', miss: '未达标'};
  const label = value => value == null ? '—' : String(exact(value));

  function format(value, fmt) {
    if (value == null) return '—';
    if (fmt.kind === 'text') return numeric(value) ? d.fmtNum(value, {digits: 0}) : label(value);
    const args = {digits: fmt.digits, divisor: fmt.divisor, unit: fmt.unit, signed: fmt.signed, scale: fmt.scale};
    if (fmt.kind === 'rate') return d.fmtPct(value, args);
    if (fmt.kind === 'delta' || fmt.kind === 'rate_delta') {
      return d.fmtDelta(value, {...args, kind: fmt.kind === 'delta' ? 'number' : 'rate'});
    }
    return d.fmtNum(value, args);
  }

  function state(value, fmt) {
    return d.metricState(value, {kind: fmt.signal, scale: fmt.scale, direction: fmt.direction,
      threshold: threshold(fmt.threshold), warningThreshold: threshold(fmt.warning_threshold)});
  }

  function match(row, by) {
    return Object.entries(by || {}).every(([key, name]) =>
      selected[key] == null || token(row[name]) === token(selected[key]));
  }

  function whereMatch(row, where) {
    return Object.entries(where || {}).every(([key, value]) => token(row[key]) === token(value));
  }

  // 一个指标按激活的筛选键选择对应粒度。缺少已登记粒度或同口径多行时显示缺数。
  function metricValue(metric) {
    const axis = new Set(metric.sources.flatMap(source => Object.keys(source.by)));
    const active = [...axis].filter(key => selected[key] != null);
    const binding = metric.sources.find(source => Object.keys(source.by).length === active.length &&
      active.every(key => Object.hasOwn(source.by, key)));
    if (!binding) return null;
    const facts = rows[binding.query].filter(row => match(row, binding.by) && whereMatch(row, binding.where));
    return facts.length === 1 ? facts[0][binding.field] : null;
  }

  function cardFacts(card) {
    const sources = card.sources?.length ? card.sources : null;
    if (!sources) {
      return rows[card.query].filter(row => match(row, card.by) && whereMatch(row, card.where));
    }
    const axis = new Set(sources.flatMap(source => Object.keys(source.by)));
    const active = [...axis].filter(key => selected[key] != null);
    const binding = sources.find(source => Object.keys(source.by).length === active.length &&
      active.every(key => Object.hasOwn(source.by, key)));
    if (!binding) return [];
    return rows[binding.query].filter(row => match(row, binding.by) && whereMatch(row, binding.where));
  }

  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function table(container, card, matches) {
    // 复用共享表格容器的浅色边框与滚动样式，保留声明式卡片的高度设置。
    const scroll = element('div', null, 'rf-table-wrap srg-table');
    const view = element('table', null, 'rf-table');
    const headings = view.createTHead().insertRow();
    for (const column of card.columns) headings.append(element('th', column.label, column.format.kind === 'text' ? null : 'num'));
    const body = view.createTBody();
    for (const fact of matches.slice(0, card.limit)) {
      const row = body.insertRow();
      for (const {field, format: fmt} of card.columns) {
        const value = fact[field], cell = row.insertCell();
        if (fmt.kind !== 'text') cell.classList.add('num');
        d.applyMetric(cell, format(value, fmt), state(value, fmt));
      }
    }
    scroll.append(view);
    container.append(scroll);
    if (matches.length > card.limit) {
      const note = element('p', '当前仅显示前 ' + d.fmtNum(card.limit, {digits: 0}) + ' 行');
      container.append(note);
    }
  }

  function chartHost(container, card) {
    const host = element('div', null, 'srg-chart');
    host.role = 'img';
    host.setAttribute('aria-label', card.title);
    container.append(host);
    const instance = echarts.init(host, null, {renderer: 'canvas'});
    charts.set(container, instance);
    return instance;
  }

  function chartAppearance(options, look) {
    if (!look) return options;
    // 合并经服务端校验的装饰性选项。原始数据、标签函数及状态色由运行时保留。
    const {grid, textStyle, backgroundColor, series = []} = look;
    if (grid && options.grid) Object.assign(options.grid, grid);
    if (textStyle) options.textStyle = {...options.textStyle, ...textStyle};
    if (backgroundColor) options.backgroundColor = backgroundColor;
    series.forEach((style, index) => {
      const target = options.series[index];
      for (const [key, value] of Object.entries(style)) {
        target[key] = value && !Array.isArray(value) && typeof value === 'object'
          ? {...target[key], ...value} : value;
      }
    });
    return options;
  }

  function showWaterfall(container, card, matches) {
    const [category, measure] = card.columns, fmt = measure.format, config = card.waterfall;
    const points = matches.map(row => ({
      name: label(row[category.field]), raw: row[measure.field],
      role: row[config.role_field ?? category.field]
    }));
    const opening = points.filter(point => point.role === config.opening);
    const closing = points.filter(point => point.role === config.closing);
    const changes = points.filter(point => config.role_field ? point.role === config.change
      : point.role !== config.opening && point.role !== config.closing);
    const tone = point => state(point.raw, {...fmt, signal: 'trend', direction: 'higher'});
    const order = {up: 0, neutral: 1, down: 2};
    // 同一正负组保持源数据顺序；只重排已核实的原值，避免改变桥接结论。
    changes.sort((left, right) => order[tone(left)] - order[tone(right)]);
    const path = [...opening, ...changes, ...closing];
    const positions = opening.length === 1 && closing.length === 1 && path.length === points.length
      && path.length <= card.limit && new Set(path.map(point => point.name)).size === path.length
      ? d.bridgeCoordinates(opening[0].raw, changes.map(point => point.raw), closing[0].raw)
      : null;
    if (positions === null) {
      const note = element('p', '瀑布图缺少唯一的期初、期末或可对账的增减项。');
      note.dataset.empty = 'true';
      container.append(note);
      return;
    }
    const css = getComputedStyle(document.documentElement);
    const text = css.getPropertyValue('--color-text-secondary').trim();
    const fontSize = Number.parseFloat(css.getPropertyValue('--rre-card-font-label-size'));
    const data = path.map((point, index) => {
      const endpoint = index === 0 || index === path.length - 1;
      const direction = endpoint ? 'neutral' : tone(point);
      const labelFormat = {...fmt, kind: fmt.kind === 'rate' && !endpoint ? 'rate_delta' : fmt.kind, signed: !endpoint};
      return {name: point.name, value: [index, ...positions[index].map(
          value => value * (fmt.scale === 'decimal' ? 100 : 1) / fmt.divisor)],
        displayed: format(point.raw, labelFormat),
        kind: endpoint ? 'total' : direction,
        itemStyle: {color: direction === 'neutral' ? config.total_color : d.stateColor(direction)},
        label: {position: direction === 'up' ? 'bottom' : 'top'}};
    });
    const options = {
      animation: false, textStyle: {color: text, fontSize},
      grid: {left: '4%', right: '4%', bottom: 36, top: 34, containLabel: true},
      xAxis: {type: 'category', data: path.map(point => point.name),
        axisLabel: {showMinLabel: true, showMaxLabel: true, width: 82, overflow: 'truncate'}},
      yAxis: {type: 'value', axisLabel: {formatter: value => format(value, {
        ...fmt, divisor: 1, scale: fmt.scale === 'decimal' ? 'percent' : fmt.scale
      })}},
      tooltip: {trigger: 'item', renderMode: 'richText', formatter: point => point.name + ' ' + point.data.displayed},
      series: [{type: 'custom', name: measure.label, data, encode: {x: 0, y: [1, 2]},
        renderItem: (params, api) => {
          const index = params.dataIndex, point = data[index];
          const [x, yStart] = api.coord([index, point.value[1]]);
          const [, yEnd] = api.coord([index, point.value[2]]);
          const y = Math.min(yStart, yEnd), bottom = Math.max(yStart, yEnd);
          const width = Math.min(54, api.size([1, 0])[0] * 0.56);
          const children = [{type: 'rect',
            shape: {x: x - width / 2, y, width, height: Math.max(2, bottom - y)},
            style: {fill: point.itemStyle.color}}];
          if (index) {
            const [prevX, prevY] = api.coord([index - 1, data[index - 1].value[2]]);
            children.unshift({type: 'line', shape: {x1: prevX + width / 2, x2: x - width / 2, y1: prevY, y2: prevY},
              style: {stroke: text, opacity: 0.55, lineDash: [3, 3]}});
          }
          if (config.labels) {
            const below = point.label.position === 'bottom';
            children.push({type: 'text', style: {
              x, y: below ? bottom + 5 : y - 5, text: point.displayed, align: 'center',
              verticalAlign: below ? 'top' : 'bottom', fontSize, fill: text}});
          }
          return {type: 'group', children};
        }
      }]
    };
    chartAppearance(options, card.option);
    const chart = chartHost(container, card);
    if (path.length > 8) {
      chart.setOption(options);
    } else {
      // 窄屏且柱数较少时折行显示类别，确保期初、期末及各变动项都能读到。
      const compact = value => Array.from(value).reduce((text, char, index) =>
        text + (index > 0 && index % 3 === 0 ? '\n' : '') + char, '');
      chart.setOption({baseOption: options, media: [
        {query: {maxWidth: 420}, option: {xAxis: {axisLabel: {interval: 0, formatter: compact, width: 40}}}},
        {option: {xAxis: {axisLabel: {interval: 'auto', formatter: null, width: 82}}}}
      ]});
    }
  }

  function showChart(container, card, matches) {
    const [x, ...fields] = card.columns;
    const categories = matches.map(row => label(row[x.field]));
    // 禁止把重复粒度图形化，避免将多条事实伪装成同一个点。
    if (new Set(categories).size !== categories.length || matches.length > card.limit) {
      container.append(element('p', '类别不唯一或超出图表容量，请缩小筛选范围。'));
      container.lastChild.dataset.empty = 'true';
      return;
    }
    const look = card.chart ?? {};
    const radial = card.type === 'pie' || card.type === 'donut';
    const horizontal = look.orientation === 'horizontal';
    const css = getComputedStyle(document.documentElement);
    const color = name => css.getPropertyValue(name).trim();
    const pixels = name => Number.parseFloat(color(name));
    const accent = color('--color-brand');
    const labelStyle = {color: color('--color-text-secondary'), fontSize: pixels('--rre-card-font-label-size')};
    const split = look.grid ?? spec.chart_defaults.chart_grid;
    const axes = ['primary', 'secondary'].filter(axis => fields.some(col => col.axis === axis));
    const visual = (value, fmt) => {
      if (!numeric(value)) return null;
      // 展示单位用于图形坐标，状态与标签继续读取未经舍入的原值。
      const point = Number(exact(value)) * (fmt.scale === 'decimal' ? 100 : 1) / fmt.divisor;
      return Number.isFinite(point) ? point : null;
    };
    const series = fields.map(col => {
      const type = col.series ?? (radial ? 'pie' : card.type === 'area' ? 'line' : card.type);
      const style = {name: col.label, type,
        label: {...labelStyle, show: Boolean(look.labels),
          position: type === 'bar' ? horizontal ? 'right' : 'top' : radial ? 'outside' : 'top',
          formatter: point => point.data.displayed}};
      if (radial) {
        // v1 的饼图默认保留原环形外观，实心饼图把内径设为零。
        style.radius = [String(look.inner_radius ?? 45) + '%', String(look.outer_radius ?? 70) + '%'];
      } else {
        const axisIndex = axes.indexOf(col.axis);
        if (horizontal) style.xAxisIndex = axisIndex;
        else style.yAxisIndex = axisIndex;
        if (type === 'bar') {
          style.barMaxWidth = pixels('--rre-card-bar-max');
          if (look.stacked) style.stack = 'series';
        } else {
          style.smooth = Boolean(look.smooth);
          style.showSymbol = (look.show_points ?? true) || Boolean(look.labels);
          style.lineStyle = {width: pixels('--rre-chart-line-width')};
          if (card.type === 'area') style.areaStyle = {opacity: 0.18};
        }
      }
      // 分类颜色可以换，表达好坏的点始终使用同一状态映射。
      style.data = matches.map((row, index) => {
        const value = row[col.field], classification = state(value, col.format);
        return {name: categories[index], value: visual(value, col.format),
          displayed: format(value, col.format),
          ...(type === 'bar' && visual(value, col.format) < 0 ? {label: {position: horizontal ? 'left' : 'bottom'}} : {}),
          ...(classification !== 'neutral' ? {itemStyle: {color: d.stateColor(classification)}} : {})};
      });
      return style;
    });
    if (!series.some(item => item.data.some(point => point.value !== null))) {
      // 有分类但所有度量缺失时，空画布会让用户误以为图表已经核对过数据。
      const note = element('p', '当前筛选无可绘制数值');
      note.dataset.empty = 'true';
      container.append(note);
      return;
    }
    const instance = chartHost(container, card);
    const legendPosition = look.legend ?? 'auto';
    const legendVisible = legendPosition === 'auto' ? fields.length > 1 : legendPosition !== 'hidden';
    const legend = {show: legendVisible, type: 'scroll', textStyle: labelStyle,
      data: radial ? categories : fields.map(col => col.label)};
    const optionsFor = position => {
      const bottom = position === 'bottom';
      const right = position === 'right';
      const legendLayout = right ? {right: 0, top: 'middle', orient: 'vertical', width: 92}
        : {top: bottom ? 'auto' : 0, bottom: bottom ? 0 : 'auto', orient: 'horizontal', left: 'center'};
      if (radial) {
        return {legend: {...legend, ...legendLayout}, series: [{center: [right ? '42%' : '50%', '50%']}]};
      }
      return {
        legend: {...legend, ...legendLayout},
        grid: {left: horizontal && look.labels ? 42 : '4%',
          right: right ? 108 : horizontal && look.labels ? 56 : '4%', bottom: bottom ? 33 : 20,
          top: legendVisible && !bottom && !right ? 32 : 14, containLabel: true}
      };
    };
    // 窄卡片将右侧图例移到底部，给双轴和长类别留出绘图区。
    const position = legendVisible ? legendPosition : 'hidden';
    const base = optionsFor(position);
    const chartOptions = {animation: false, textStyle: labelStyle, ...base,
      color: look.palette?.length ? look.palette : (radial || fields.length > 1 ? spec.chart_defaults['chart-palette-categorical'] : [accent]),
      tooltip: {trigger: 'item', renderMode: 'richText', formatter: p => p.name + '\n' + p.seriesName + ' ' + p.data.displayed},
      series: radial ? series.map((item, index) => ({...item, ...base.series[index]})) : series};
    if (!radial) {
      const category = {type: 'category', data: categories, axisLabel: {...labelStyle, width: 90, overflow: 'truncate'}};
      const values = axes.map((axis, index) => {
        const fmt = fields.find(col => col.axis === axis).format;
        const display = {...fmt, scale: fmt.scale === 'decimal' ? 'percent' : fmt.scale, divisor: 1};
        return {type: 'value', position: horizontal ? 'bottom' : index ? 'right' : 'left',
          axisLabel: {...labelStyle, formatter: value => format(value, display)},
          splitLine: {show: index === 0 && split !== 'none', lineStyle: {type: split === 'dashed' ? 'dashed' : 'solid'}}};
      });
      chartOptions.xAxis = horizontal ? values : category;
      chartOptions.yAxis = horizontal ? category : values;
    }
    chartAppearance(chartOptions, card.option);
    if (position === 'right') {
      const mobile = optionsFor('bottom');
      if (card.option?.grid && mobile.grid) Object.assign(mobile.grid, card.option.grid);
      if (radial && card.option?.series[0]?.center) {
        mobile.series[0].center = card.option.series[0].center;
        base.series[0].center = card.option.series[0].center;
      }
      if (card.option?.grid && base.grid) Object.assign(base.grid, card.option.grid);
      instance.setOption({baseOption: chartOptions, media: [
        {query: {maxWidth: 420}, option: mobile},
        {option: base}
      ]});
    } else {
      instance.setOption(chartOptions);
    }
  }

  function refresh() {
    const metrics = new Map();
    for (const [key, item] of Object.entries(spec.metrics)) {
      const value = metricValue(item);
      metrics.set(key, {text: format(value, item.format), state: state(value, item.format)});
    }
    for (const node of document.querySelectorAll('[data-metric]')) {
      const result = metrics.get(node.dataset.metric);
      const fmt = spec.metrics[node.dataset.metric].format;
      const status = fmt.signal === 'trend' || result.state !== 'neutral' ? stateLabels[result.state] : '—';
      const text = node.dataset.metricState && result.text !== '—' ? status : result.text;
      d.applyMetric(node, text, result.state);
    }
    for (const [key, card] of Object.entries(spec.outputs)) {
      const container = document.getElementById(key);
      charts.get(container)?.dispose();
      charts.delete(container);
      container.replaceChildren();
      const matches = cardFacts(card);
      if (!matches.length) {
        container.append(element('p', '当前筛选无数据'));
        container.lastChild.dataset.empty = 'true';
      } else if (card.type === 'table') {
        table(container, card, matches);
      } else if (card.type === 'waterfall') {
        showWaterfall(container, card, matches);
      } else {
        showChart(container, card, matches);
      }
    }
    document.documentElement.dataset.srgReady = 'true';
  }

  const controls = new Map();
  for (const filter of spec.filters) {
    const values = new Map(rows[filter.query].map(row => [token(row[filter.field]), row[filter.field]]));
    const select = document.getElementById('filter-' + filter.id);
    const all = element('option', '全部');
    all.value = 'null';
    select.append(all);
    for (const [key, value] of values) {
      if (value == null) continue;
      const option = element('option', label(value));
      option.value = key;
      select.append(option);
    }
    const fallback = values.has(token(filter.default)) ? filter.default : null;
    controls.set(filter.id, {select, fallback});
    select.value = token(fallback);
    selected[filter.id] = fallback;
    select.addEventListener('change', () => {
      selected[filter.id] = JSON.parse(select.value);
      refresh();
    });
  }
  document.getElementById('srg-reset')?.addEventListener('click', () => {
    for (const [key, {select, fallback}] of controls.entries()) {
      select.value = token(fallback);
      selected[key] = fallback;
    }
    refresh();
  });
  addEventListener('resize', () => { for (const chart of charts.values()) chart.resize(); });
  refresh();
})();
