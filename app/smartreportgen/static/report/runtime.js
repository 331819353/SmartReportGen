/* SmartReportGen 报告运行时：全局筛选（级联 / 预设 / 图表点击联动）、客户端聚合、ECharts 渲染。 */
(function () {
  'use strict';
  var R = JSON.parse(document.getElementById('rf-data').textContent);
  var T = R.theme;  // 设计令牌
  var M = R.measures;
  var F = R.filters || [];
  var C = {
    text: T['color-text-primary'], muted: T['color-text-secondary'], border: T['color-border-card'],
    surface: T['color-bg-card'], page: T['color-bg-page'], brand: T['color-brand'], inverse: T['color-text-inverse'],
    positive: T['color-status-positive'], warning: T['color-status-warning'], negative: T['color-status-negative'],
    up: T['color-trend-up'], down: T['color-trend-down']
  };
  var PAL = T['chart-palette-categorical'], SEQ = T['chart-palette-sequential'], DIV = T['chart-palette-diverging'];
  function FT(role) { return T['rre-card-font-' + role]; }
  function SZ(k) { return T['rre-card-' + k]; }
  var LBL = Math.max(12, FT('label').size);
  var FAMILY = T['font-family'];

  // ------------------------------------------------------------------ 格式化
  function num(x, d) {
    return Number(x).toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d });
  }
  function autoCN(v, compact) {
    var a = Math.abs(v);
    if (a >= 1e8) return num(v / 1e8, compact ? 1 : 2) + '亿';
    if (a >= 1e4) return num(v / 1e4, compact ? 1 : 2) + '万';
    if (Number.isInteger(v)) return num(v, 0);
    return num(v, a < 1 ? 3 : compact ? 1 : 2);
  }
  function fmt(v, f, compact) {
    if (v == null || (typeof v === 'number' && !isFinite(v))) return '–';
    if (typeof v !== 'number') return String(v);
    f = f || 'auto';
    var a = Math.abs(v);
    switch (f) {
      case 'raw': return String(v);
      case 'int': return num(Math.round(v), 0);
      case 'float1': return num(v, 1);
      case 'float2': return num(v, 2);
      case 'percent': {
        var pv = v * 100;
        return (compact && Math.abs(pv - Math.round(pv)) < 1e-6 ? Math.round(pv) : pv.toFixed(1)) + '%';
      }
      case 'percent0': return Math.round(v * 100) + '%';
      case 'pp': return (v >= 0 ? '+' : '') + (v * 100).toFixed(Math.abs(v) < 0.01 ? 2 : 1) + 'pp';
      case 'wan': return num(v / 1e4, compact ? (a >= 1e6 ? 0 : 1) : 2) + '万';
      case 'yi': return num(v / 1e8, compact ? 1 : 2) + '亿';
      case 'money': return '¥' + autoCN(v, compact);
      case 'auto': return autoCN(v, compact);
      default:
        if (/^0(\.0+)?%?$/.test(f)) {
          var d = (f.split('.')[1] || '').replace('%', '').length;
          return /%$/.test(f) ? (v * 100).toFixed(d) + '%' : num(v, d);
        }
        return autoCN(v, compact);
    }
  }
  function fmtM(key, v, compact, override) {
    var m = M[key] || {};
    var s = fmt(v, override || m.format, compact);
    return !compact && m.unit && s !== '–' ? s + m.unit : s;
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // ------------------------------------------------------------------ 数据与度量
  var Q = {};
  Object.keys(R.data).forEach(function (qid) {
    var d = R.data[qid], cols = d.columns;
    Q[qid] = {
      cols: cols,
      colset: new Set(cols),
      rows: d.rows.map(function (r) {
        var o = {};
        for (var i = 0; i < cols.length; i++) o[cols[i]] = r[i];
        return o;
      })
    };
  });
  Object.keys(M).forEach(function (k) {
    var m = M[k];
    if (!/^[\sa\[\]\d.+\-*/()]*$/.test(m.js)) throw new Error('非法度量表达式 ' + k);
    m.fn = new Function('a', '"use strict";return (' + m.js + ');');
  });
  function aggOne(op, f, rows) {
    var i, r, v, n = 0;
    if (op === 'count') {
      if (f === '*') return rows.length;
      for (i = 0; i < rows.length; i++) { v = rows[i][f]; if (v != null && v !== '') n++; }
      return n;
    }
    if (op === 'count_distinct') {
      var s = new Set();
      for (i = 0; i < rows.length; i++) { v = rows[i][f]; if (v != null && v !== '') s.add(v); }
      return s.size;
    }
    var sum = 0, mn = Infinity, mx = -Infinity;
    for (i = 0; i < rows.length; i++) {
      r = rows[i][f];
      if (r == null || r === '') continue;
      v = +r;
      if (isNaN(v)) continue;
      n++; sum += v;
      if (v < mn) mn = v;
      if (v > mx) mx = v;
    }
    if (op === 'sum') return sum;
    if (!n) return null;
    if (op === 'avg') return sum / n;
    if (op === 'min') return mn;
    return mx;
  }
  function measure(key, rows) {
    var m = M[key];
    if (!m || !rows) return null;
    var a = m.aggs.map(function (x) { return aggOne(x[0], x[1], rows); });
    if (a.some(function (v) { return v == null; })) return null;
    var v;
    try { v = m.fn(a); } catch (e) { return null; }
    return typeof v === 'number' && isFinite(v) ? v : null;
  }
  function cmp(a, b) {
    var na = +a, nb = +b;
    if (a !== '' && b !== '' && !isNaN(na) && !isNaN(nb)) return na - nb;
    a = String(a); b = String(b);
    return a < b ? -1 : a > b ? 1 : 0;
  }
  function groupBy(rows, f) {
    var m = new Map();
    for (var i = 0; i < rows.length; i++) {
      var v = rows[i][f];
      var k = v == null || v === '' ? '(空)' : String(v);
      var a = m.get(k);
      if (!a) m.set(k, (a = []));
      a.push(rows[i]);
    }
    return m;
  }
  var TIME_RE = /^(\d{4}([-/.]\d{1,2}){0,2}([ T].*)?|\d{4}\s*(Q[1-4]|H[12]|W\d{1,2})|\d{4}年(\d{1,2}月)?|\d{1,2}月|Q[1-4])$/i;
  function timeLike(keys) {
    return keys.length > 1 && keys.every(function (k) { return TIME_RE.test(k) || (!isNaN(+k) && k !== '(空)'); });
  }

  // ------------------------------------------------------------------ 筛选
  var state = {};
  function colFor(f, qid) {
    if (!Q[qid] || (f.exclude && f.exclude.indexOf(qid) >= 0)) return null;
    var c = (f.targets && f.targets[qid]) || f.field;
    return Q[qid].colset.has(c) ? c : null;
  }
  function isActive(f, v) {
    if (v == null) return false;
    if (f.type === 'select') return v !== '';
    if (f.type === 'multi') return v.length > 0;
    return (v[0] != null && v[0] !== '') || (v[1] != null && v[1] !== '');
  }
  function cut(a, b) { var n = Math.min(a.length, b.length); return [a.slice(0, n), b.slice(0, n)]; }
  function test(f, val, v) {
    if (val == null) return false;
    var s = String(val);
    if (f.type === 'select') return s === String(v);
    if (f.type === 'multi') return v.indexOf(s) >= 0;
    var lo = v[0], hi = v[1], p;
    if (f.type === 'date_range') {
      if (lo) { p = cut(s, String(lo)); if (p[0] < p[1]) return false; }
      if (hi) { p = cut(s, String(hi)); if (p[0] > p[1]) return false; }
      return true;
    }
    if (lo != null && lo !== '' && cmp(val, lo) < 0) return false;
    if (hi != null && hi !== '' && cmp(val, hi) > 0) return false;
    return true;
  }
  function applyFilters(qid, rows, list) {
    for (var i = 0; i < list.length; i++) {
      var f = list[i], c = colFor(f, qid), v = state[f.id];
      if (!c || !isActive(f, v)) continue;
      rows = rows.filter(function (r) { return test(f, r[c], v); });
    }
    return rows;
  }
  var cache = {};
  function rowsFor(qid, skipId) {
    var key = qid + '|' + (skipId || '') + '|' + JSON.stringify(state);
    if (cache[key]) return cache[key];
    var list = F.filter(function (f) { return f.id !== skipId; });
    return (cache[key] = applyFilters(qid, Q[qid] ? Q[qid].rows : [], list));
  }
  function optionsFor(f, idx) {
    if (Array.isArray(f.options)) return f.options.map(String);
    var qids = f.options_query && Q[f.options_query] ? [f.options_query] : Object.keys(Q).filter(function (q) { return colFor(f, q); });
    var set = new Set();
    qids.forEach(function (q) {
      var col = colFor(f, q) || f.field;
      var rows = f.cascade ? applyFilters(q, Q[q].rows, F.slice(0, idx)) : Q[q].rows;
      for (var i = 0; i < rows.length; i++) {
        var v = rows[i][col];
        if (v != null && v !== '') set.add(String(v));
      }
    });
    return Array.from(set).sort(cmp);
  }
  function initialValue(f, opts) {
    var d = f.default;
    var m = typeof d === 'string' && /^last:(\d+)$/.exec(d);
    if (f.type === 'select') {
      if (d === 'latest') return opts.length ? opts[opts.length - 1] : '';
      if (d === 'first') return opts.length ? opts[0] : '';
      return d == null ? '' : String(d);
    }
    if (f.type === 'multi') return Array.isArray(d) ? d.map(String) : d != null && d !== '' ? [String(d)] : [];
    if (m) return [opts[Math.max(0, opts.length - +m[1])] || '', opts[opts.length - 1] || ''];
    if (Array.isArray(d)) return [d[0] == null ? '' : String(d[0]), d[1] == null ? '' : String(d[1])];
    return ['', ''];
  }

  // ------------------------------------------------------------------ 筛选栏 UI
  var bar = document.querySelector('#rf-filterbar .rf-filterbar-inner');
  var controls = {};
  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }
  function buildBar() {
    if (!F.length && !(R.presets || []).length) { document.getElementById('rf-filterbar').style.display = 'none'; return; }
    var row = el('div', 'rf-filters');
    F.forEach(function (f, idx) {
      var w = el('div', 'rf-f rf-f-' + f.type);
      w.appendChild(el('label', 'rf-f-label', esc(f.label)));
      var ctl = el('div', 'rf-f-ctl');
      w.appendChild(ctl);
      row.appendChild(w);
      controls[f.id] = { f: f, idx: idx, box: ctl };
    });
    bar.appendChild(row);
    var tools = el('div', 'rf-tools');
    (R.presets || []).forEach(function (p) {
      var b = el('button', 'rf-btn rf-preset', esc(p.name));
      b.type = 'button';
      b.onclick = function () { applyPreset(p); };
      tools.appendChild(b);
    });
    var reset = el('button', 'rf-btn rf-reset', '重置');
    reset.type = 'button';
    reset.onclick = function () { F.forEach(function (f) { state[f.id] = undefined; }); initState(); changed(); };
    tools.appendChild(reset);
    bar.appendChild(tools);
    document.addEventListener('click', function (e) {
      document.querySelectorAll('.rf-ms.open').forEach(function (ms) { if (!ms.contains(e.target)) ms.classList.remove('open'); });
    });
  }
  function selectEl(opts, value, placeholder, onchange) {
    var s = el('select', 'rf-select');
    var html = placeholder != null ? '<option value="">' + esc(placeholder) + '</option>' : '';
    opts.forEach(function (o) { html += '<option value="' + esc(o) + '">' + esc(o) + '</option>'; });
    s.innerHTML = html;
    s.value = value == null ? '' : value;
    s.onchange = function () { onchange(s.value); };
    return s;
  }
  function multiEl(c, opts) {
    var f = c.f, v = state[f.id] || [];
    var box = el('div', 'rf-ms');
    var text = !v.length ? esc(f.placeholder) : v.length <= 2 ? esc(v.join('、')) : esc(v[0]) + ' 等 ' + v.length + ' 项';
    var btn = el('button', 'rf-ms-btn' + (v.length ? ' on' : ''), '<span>' + text + '</span><i>▾</i>');
    btn.type = 'button';
    var pop = el('div', 'rf-ms-pop');
    var search = el('input', 'rf-ms-search');
    search.placeholder = '搜索…';
    var acts = el('div', 'rf-ms-acts', '<a data-a="all">全选</a><a data-a="none">清空</a>');
    var list = el('div', 'rf-ms-list');
    function draw() {
      var q = search.value.trim().toLowerCase();
      list.innerHTML = opts.filter(function (o) { return !q || o.toLowerCase().indexOf(q) >= 0; }).map(function (o) {
        return '<label class="rf-ms-item"><input type="checkbox" value="' + esc(o) + '"' + (v.indexOf(o) >= 0 ? ' checked' : '') + '><span>' + esc(o) + '</span></label>';
      }).join('') || '<div class="rf-ms-empty">无可选项</div>';
    }
    draw();
    search.oninput = draw;
    list.onchange = function (e) {
      var val = e.target.value;
      var cur = (state[f.id] || []).slice();
      var i = cur.indexOf(val);
      if (e.target.checked && i < 0) cur.push(val);
      if (!e.target.checked && i >= 0) cur.splice(i, 1);
      state[f.id] = cur;
      changed(f.id);
    };
    acts.onclick = function (e) {
      var a = e.target.getAttribute('data-a');
      if (!a) return;
      state[f.id] = a === 'all' ? opts.slice() : [];
      changed(f.id);
    };
    btn.onclick = function (e) {
      e.stopPropagation();
      var open = box.classList.contains('open');
      document.querySelectorAll('.rf-ms.open').forEach(function (x) { x.classList.remove('open'); });
      if (!open) { box.classList.add('open'); search.focus(); }
    };
    pop.appendChild(search); pop.appendChild(acts); pop.appendChild(list);
    box.appendChild(btn); box.appendChild(pop);
    return box;
  }
  var reopen = null;
  function drawControls() {
    F.forEach(function (f, idx) {
      var c = controls[f.id];
      var opts = optionsFor(f, idx);
      c.opts = opts;
      var v = state[f.id];
      // 级联：上级变化后剔除失效取值
      if (f.type === 'multi' && v && v.length) state[f.id] = v = v.filter(function (x) { return opts.indexOf(x) >= 0; });
      if (f.type === 'select' && v && opts.indexOf(v) < 0) state[f.id] = v = '';
      c.box.innerHTML = '';
      if (f.type === 'select') {
        c.box.appendChild(selectEl(opts, v, f.placeholder, function (x) { state[f.id] = x; changed(); }));
      } else if (f.type === 'multi') {
        var ms = multiEl(c, opts);
        c.box.appendChild(ms);
        if (reopen === f.id) ms.classList.add('open');
      } else if (f.type === 'range') {
        v = v || ['', ''];
        c.box.appendChild(selectEl(opts, v[0], '起', function (x) { state[f.id] = [x, (state[f.id] || [])[1] || '']; changed(); }));
        c.box.appendChild(el('span', 'rf-sep', '至'));
        c.box.appendChild(selectEl(opts, v[1], '止', function (x) { state[f.id] = [(state[f.id] || [])[0] || '', x]; changed(); }));
      } else {
        v = v || ['', ''];
        ['起', '止'].forEach(function (ph, i) {
          var inp = el('input', 'rf-date');
          inp.type = 'date';
          inp.value = v[i] || '';
          if (opts.length) { inp.min = String(opts[0]).slice(0, 10); inp.max = String(opts[opts.length - 1]).slice(0, 10); }
          inp.onchange = function () { var cur = (state[f.id] || ['', '']).slice(); cur[i] = inp.value; state[f.id] = cur; changed(); };
          if (i === 1) c.box.appendChild(el('span', 'rf-sep', '至'));
          c.box.appendChild(inp);
        });
      }
      c.box.parentNode.classList.toggle('rf-f-on', isActive(f, state[f.id]));
    });
    reopen = null;
  }
  function initState() {
    F.forEach(function (f, idx) {
      if (state[f.id] === undefined) state[f.id] = initialValue(f, optionsFor(f, idx));
    });
  }
  function applyPreset(p) {
    F.forEach(function (f) { state[f.id] = undefined; });
    F.forEach(function (f, idx) {
      if (p.values[f.id] !== undefined) {
        var d = p.values[f.id];
        state[f.id] = initialValue({ type: f.type, default: d }, optionsFor(f, idx));
      } else state[f.id] = initialValue(f, optionsFor(f, idx));
    });
    document.querySelectorAll('.rf-preset').forEach(function (b) { b.classList.toggle('on', b.textContent === p.name); });
    changed();
  }
  var pending = false;
  function changed(keepOpen) {
    cache = {};
    reopen = keepOpen || null;
    drawControls();
    if (pending) return;
    pending = true;
    requestAnimationFrame(function () { pending = false; renderAll(); });
  }

  // ------------------------------------------------------------------ 图表
  var charts = [];
  document.querySelectorAll('[data-chart]').forEach(function (node) {
    var spec = R.charts[node.getAttribute('data-chart')];
    if (spec) charts.push({ spec: spec, node: node, body: node.querySelector('.rf-chart-body'), inst: null });
  });
  function linkFilter(spec) {
    if (!spec.x || !spec.query) return null;
    for (var i = 0; i < F.length; i++) {
      var f = F[i];
      if ((f.type === 'multi' || f.type === 'select') && colFor(f, spec.query) === spec.x) return f;
    }
    return null;
  }
  function toggle(f, val) {
    val = String(val);
    if (f.type === 'select') state[f.id] = state[f.id] === val ? '' : val;
    else {
      var cur = (state[f.id] || []).slice(), i = cur.indexOf(val);
      if (i >= 0) cur.splice(i, 1); else cur.push(val);
      state[f.id] = cur;
    }
    changed();
  }
  function selected(f, k) {
    if (!f || !isActive(f, state[f.id])) return true;
    return f.type === 'select' ? state[f.id] === k : state[f.id].indexOf(k) >= 0;
  }
  function axisStyle() {
    return {
      axisLine: { lineStyle: { color: C.border } },
      axisTick: { show: false },
      axisLabel: { color: C.muted, fontSize: LBL, hideOverlap: true, margin: SZ('axis-gap') },
      splitLine: { lineStyle: { color: C.border, type: 'dashed' } },
      nameTextStyle: { color: C.muted, fontSize: LBL },
      nameGap: SZ('axis-title-gap') + LBL + SZ('axis-gap')
    };
  }
  function base() {
    return {
      color: PAL,
      textStyle: { fontFamily: FAMILY, color: C.text },
      animationDuration: 450,
      grid: { left: 8, right: 16, top: 34, bottom: 6, containLabel: true },
      tooltip: {
        confine: true, backgroundColor: C.surface, borderColor: C.border, borderWidth: 1,
        textStyle: { color: C.text, fontSize: LBL }, extraCssText: 'box-shadow:' + T['shadow-card'] + ';border-radius:12px;'
      },
      legend: { top: 0, type: 'scroll', icon: 'roundRect', itemWidth: 12, itemHeight: 8, itemGap: SZ('legend-gap'), textStyle: { color: C.muted, fontSize: LBL } }
    };
  }
  function orderCats(spec, gm, valueOf) {
    var cats = Array.from(gm.keys());
    var isTime = timeLike(cats);
    var sort = spec.sort || (isTime || /line|area/.test(spec.type) ? 'x_asc' : 'value_desc');
    if (sort === 'x_asc') cats.sort(cmp);
    else if (sort === 'x_desc') cats.sort(function (a, b) { return cmp(b, a); });
    else if (sort !== 'none') {
      var vals = {};
      cats.forEach(function (k) { vals[k] = valueOf(k); });
      cats.sort(function (a, b) { var d = (vals[b] || 0) - (vals[a] || 0); return sort === 'value_asc' ? -d : d; });
    }
    if (spec.top && cats.length > spec.top) cats = sort === 'x_asc' && isTime ? cats.slice(-spec.top) : cats.slice(0, spec.top);
    return cats;
  }
  function tipFormatter(keys) {
    return function (ps) {
      ps = Array.isArray(ps) ? ps : [ps];
      var h = '<div style="font-weight:600;margin-bottom:4px">' + esc(ps[0].axisValueLabel || ps[0].name) + '</div>';
      ps.forEach(function (p) {
        var v = p.value && typeof p.value === 'object' ? p.value.value : p.value;
        if (Array.isArray(v)) v = v[1];
        h += '<div style="display:flex;justify-content:space-between;gap:16px"><span>' + p.marker + esc(p.seriesName) +
          '</span><b>' + esc(fmtM(keys[p.seriesIndex] || keys[0], v)) + '</b></div>';
      });
      return h;
    };
  }

  function cartesian(c, rows, lf) {
    var s = c.spec, horiz = s.type === 'hbar';
    var gm = groupBy(rows, s.x);
    var y0 = s.y[0].key;
    var cats = orderCats(s, gm, function (k) { return measure(y0, gm.get(k)); });
    var areaish = /area/.test(s.type), stack = s.stack;
    var defType = /line|area/.test(s.type) ? 'line' : 'bar';
    var series = [], keys = [], hasRight = false;
    function mk(name, data, type, key, axis) {
      var isBar = type === 'bar';
      var o = {
        name: name, type: type, data: data, yAxisIndex: axis === 'right' ? 1 : 0,
        emphasis: { focus: 'series' }, connectNulls: true
      };
      if (horiz) { delete o.yAxisIndex; }
      if (isBar) {
        var rr = T['rre-data-radius'];
        o.barMaxWidth = SZ('bar-max');
        o.itemStyle = { borderRadius: horiz ? [0, rr, rr, 0] : [rr, rr, 0, 0] };
      } else {
        o.smooth = s.smooth; o.symbolSize = cats.length > 24 ? 4 : 6; o.showSymbol = cats.length <= 36;
        o.lineStyle = { width: 2.4 };
        if (areaish) o.areaStyle = { opacity: stack ? 0.55 : 0.14 };
      }
      if (stack && (isBar || areaish)) { o.stack = 'total'; if (isBar) o.itemStyle.borderRadius = 0; }
      var showLabel = s.labels != null ? !!s.labels : isBar && !stack && cats.length <= 12 && (s.series ? false : s.y.length === 1);
      if (showLabel) {
        o.label = {
          show: true, position: isBar ? (horiz ? 'right' : 'top') : 'top', color: C.muted, fontSize: LBL,
          formatter: function (p) { var v = p.value && typeof p.value === 'object' ? p.value.value : p.value; return fmtM(key, v, true); }
        };
      }
      keys.push(key);
      series.push(o);
    }
    function dataFor(fn) {
      return cats.map(function (k) {
        var v = fn(k);
        return lf && !selected(lf, k) ? { value: v, itemStyle: { opacity: 0.28 } } : v;
      });
    }
    if (s.series) {
      var sm = groupBy(rows, s.series);
      var names = Array.from(sm.keys());
      if (timeLike(names)) names.sort(cmp);
      else names.sort(function (a, b) { return (measure(y0, sm.get(b)) || 0) - (measure(y0, sm.get(a)) || 0); });
      var lim = s.series_limit || 8;
      var rest = names.length > lim ? names.slice(lim - 1) : [];
      if (rest.length) names = names.slice(0, lim - 1).concat(['其他']);
      names.forEach(function (sn) {
        var pick = sn === '其他' && rest.length ? function (r) { return rest.indexOf(String(r[s.series])) >= 0; } : function (r) { return String(r[s.series] == null ? '(空)' : r[s.series]) === sn; };
        mk(sn, dataFor(function (k) {
          var rr = gm.get(k).filter(pick);
          return rr.length ? measure(y0, rr) : null;
        }), s.y[0].as || defType, y0, s.y[0].axis);
      });
    } else {
      s.y.forEach(function (y) {
        if (y.axis === 'right') hasRight = true;
        var type = y.as || (s.type === 'combo' ? (keys.length === 0 ? 'bar' : 'line') : defType);
        mk(M[y.key].name, dataFor(function (k) { return measure(y.key, gm.get(k)); }), type, y.key, y.axis);
        if (y.color) series[series.length - 1].itemStyle = Object.assign(series[series.length - 1].itemStyle || {}, { color: y.color });
      });
      if (s.type === 'combo' && !hasRight && s.y.length > 1) {
        // combo 默认：第一个度量柱状（左轴），其余折线（右轴）
        series.forEach(function (o, i) { if (i > 0) { o.yAxisIndex = 1; } });
        hasRight = true;
      }
    }
    var leftKey = keys[0];
    var rightKey = keys[series.findIndex(function (o) { return o.yAxisIndex === 1; })] || leftKey;
    var catAxis = Object.assign(axisStyle(), {
      type: 'category', data: cats, boundaryGap: defType === 'bar' || s.type === 'combo', name: s.x_label || ''
    });
    catAxis.splitLine = { show: false };
    var avgLen = cats.reduce(function (a, k) { return a + k.length; }, 0) / (cats.length || 1);
    if (!horiz && cats.length > 6 && avgLen * cats.length > 42) catAxis.axisLabel.rotate = 30;
    if (horiz) catAxis.inverse = true;
    function valAxis(key, extra) {
      return Object.assign(axisStyle(), {
        type: 'value', name: extra && extra.name || '', scale: defType === 'line' && !stack && s.type !== 'combo',
        axisLabel: Object.assign(axisStyle().axisLabel, { formatter: function (v) { return fmtM(key, v, true); } })
      }, extra && extra.right ? { splitLine: { show: false } } : {});
    }
    var opt = base();
    opt.tooltip.trigger = 'axis';
    opt.tooltip.axisPointer = { type: defType === 'bar' ? 'shadow' : 'line', lineStyle: { color: C.border } };
    opt.tooltip.formatter = tipFormatter(keys);
    var legend = s.legend != null ? !!s.legend : series.length > 1;
    opt.legend.show = legend;
    opt.grid.top = legend ? 34 : 16;
    if (horiz) {
      opt.yAxis = catAxis;
      opt.xAxis = valAxis(leftKey);
      opt.grid.right = 40;
    } else {
      opt.xAxis = catAxis;
      opt.yAxis = hasRight ? [valAxis(leftKey, { name: s.y_label }), valAxis(rightKey, { right: true })] : valAxis(leftKey, { name: s.y_label });
    }
    if (s.zoom || (!horiz && cats.length > 40)) {
      opt.dataZoom = [{ type: 'inside' }, { type: 'slider', height: 16, bottom: 4, borderColor: C.border, fillerColor: 'rgba(0,0,0,.05)' }];
      opt.grid.bottom = 30;
    }
    // 有正有负的单系列柱：按正负使用发散色阶两端着色，负值柱圆角翻转
    if (!s.series && series.length === 1 && series[0].type === 'bar' && !stack) {
      var vals = series[0].data.map(function (d) { return d && typeof d === 'object' ? d.value : d; });
      if (vals.some(function (v) { return v < 0; }) && vals.some(function (v) { return v > 0; })) {
        var r2 = T['rre-data-radius'];
        series[0].data = series[0].data.map(function (d, i) {
          var v = vals[i], neg = v < 0;
          var st = { color: neg ? DIV[0] : DIV[DIV.length - 1], borderRadius: neg ? (horiz ? [r2, 0, 0, r2] : [0, 0, r2, r2]) : (horiz ? [0, r2, r2, 0] : [r2, r2, 0, 0]) };
          if (d && typeof d === 'object') { d.itemStyle = Object.assign({}, st, d.itemStyle); return d; }
          return { value: d, itemStyle: st };
        });
      }
    }
    opt.series = series;
    return opt;
  }

  function categorical(c, rows, lf) {
    var s = c.spec, key = s.y[0].key;
    var gm = groupBy(rows, s.x);
    var cats = Array.from(gm.keys());
    var vals = {};
    cats.forEach(function (k) { vals[k] = measure(key, gm.get(k)); });
    cats.sort(function (a, b) { return (vals[b] || 0) - (vals[a] || 0); });
    if (s.sort === 'x_asc') cats.sort(cmp);
    var items = cats;
    var data;
    if (s.top && cats.length > s.top) {
      items = cats.slice(0, s.others ? s.top - 1 : s.top);
      data = items.map(function (k) { return { name: k, value: vals[k] }; });
      if (s.others) {
        var restRows = [];
        cats.slice(s.top - 1).forEach(function (k) { restRows = restRows.concat(gm.get(k)); });
        data.push({ name: '其他', value: measure(key, restRows) });
      }
    } else data = items.map(function (k) { return { name: k, value: vals[k] }; });
    data.forEach(function (d) { if (lf && !selected(lf, d.name)) d.itemStyle = { opacity: 0.3 }; });
    var opt = base();
    opt.tooltip.trigger = 'item';
    if (s.type === 'pie' || s.type === 'donut') {
      var total = data.reduce(function (a, d) { return a + (d.value || 0); }, 0);
      opt.tooltip.formatter = function (p) {
        return p.marker + esc(p.name) + '<br><b>' + esc(fmtM(key, p.value)) + '</b>　' + (total ? (p.value / total * 100).toFixed(1) + '%' : '');
      };
      var narrow = c.body.clientWidth < 440;
      opt.legend = Object.assign(opt.legend, { show: data.length <= 10, top: 'auto', bottom: 0 });
      opt.series = [{
        type: 'pie', data: data, radius: s.type === 'donut' ? ['46%', '70%'] : [0, '70%'], center: ['50%', data.length <= 10 ? '45%' : '50%'],
        itemStyle: { borderColor: C.surface, borderWidth: 2, borderRadius: s.type === 'donut' ? 4 : 0 },
        label: { show: true, color: C.muted, fontSize: LBL, formatter: narrow && data.length <= 10 ? '{d}%' : '{b}\n{d}%' },
        labelLine: { length: 8, length2: 8 }, minAngle: 2, avoidLabelOverlap: true
      }];
      if (s.type === 'donut') {
        opt.graphic = [{
          type: 'text', left: 'center', top: data.length <= 10 ? '39%' : '44%',
          style: { text: fmtM(key, total, true) + '\n' + (M[key].name || ''), fill: C.text, fontSize: FT('read').size, fontWeight: FT('read').weight, textAlign: 'center', lineHeight: 22 }
        }];
        if (M[key].aggs.length > 1 || M[key].aggs[0][0] !== 'sum') opt.graphic = [];
      }
    } else if (s.type === 'funnel') {
      opt.tooltip.formatter = function (p) { return p.marker + esc(p.name) + '<br><b>' + esc(fmtM(key, p.value)) + '</b>'; };
      opt.legend.show = false;
      opt.series = [{
        type: 'funnel', data: data, left: '8%', right: '8%', top: 8, bottom: 8, sort: s.sort === 'none' ? 'none' : 'descending', gap: 2,
        label: { position: 'inside', color: '#fff', formatter: function (p) { return p.name + '  ' + fmtM(key, p.value, true); } },
        itemStyle: { borderColor: C.surface, borderWidth: 1 }
      }];
    } else {
      opt.tooltip.formatter = function (p) { return esc(p.name) + '<br><b>' + esc(fmtM(key, p.value)) + '</b>'; };
      opt.legend.show = false;
      opt.series = [{
        type: 'treemap', data: data, roam: false, nodeClick: false, breadcrumb: { show: false }, top: 0, left: 0, right: 0, bottom: 0,
        label: { formatter: function (p) { return p.name + '\n' + fmtM(key, p.value, true); }, fontSize: LBL },
        itemStyle: { borderColor: C.surface, borderWidth: 2, gapWidth: 2 }, colorMappingBy: 'index'
      }];
    }
    return opt;
  }

  function scatter(c, rows) {
    var s = c.spec, kx = s.y[0].key, ky = s.y[1].key, ks = s.y[2] && s.y[2].key;
    var gm = groupBy(rows, s.group);
    var pts = [];
    gm.forEach(function (rr, k) {
      var p = [measure(kx, rr), measure(ky, rr)];
      if (ks) p.push(measure(ks, rr));
      if (p[0] != null && p[1] != null) pts.push({ name: k, value: p });
    });
    var maxS = ks ? Math.max.apply(null, pts.map(function (p) { return Math.abs(p.value[2] || 0); }).concat([1])) : 1;
    var opt = base();
    opt.legend.show = false;
    opt.grid.top = 20;
    opt.tooltip.trigger = 'item';
    opt.tooltip.formatter = function (p) {
      return '<b>' + esc(p.name) + '</b><br>' + esc(M[kx].name) + '：' + esc(fmtM(kx, p.value[0])) + '<br>' + esc(M[ky].name) + '：' +
        esc(fmtM(ky, p.value[1])) + (ks ? '<br>' + esc(M[ks].name) + '：' + esc(fmtM(ks, p.value[2])) : '');
    };
    opt.xAxis = Object.assign(axisStyle(), { type: 'value', scale: true, name: M[kx].name, nameLocation: 'middle', nameGap: 24, axisLabel: Object.assign(axisStyle().axisLabel, { formatter: function (v) { return fmtM(kx, v, true); } }) });
    opt.yAxis = Object.assign(axisStyle(), { type: 'value', scale: true, name: M[ky].name, axisLabel: Object.assign(axisStyle().axisLabel, { formatter: function (v) { return fmtM(ky, v, true); } }) });
    opt.series = [{
      type: 'scatter', data: pts,
      symbolSize: function (v) { return ks ? 8 + 34 * Math.sqrt(Math.abs(v[2] || 0) / maxS) : 10; },
      itemStyle: { opacity: 0.75, borderColor: C.surface, borderWidth: 1 },
      label: { show: pts.length <= 15, position: 'top', formatter: '{b}', color: C.muted, fontSize: LBL }
    }];
    return opt;
  }

  function heatmap(c, rows) {
    var s = c.spec, key = s.y[0].key;
    var gx = groupBy(rows, s.x), xs = Array.from(gx.keys()).sort(cmp);
    var ys = Array.from(groupBy(rows, s.series).keys()).sort(cmp);
    var data = [], mn = Infinity, mx = -Infinity;
    xs.forEach(function (xk, i) {
      var sub = groupBy(gx.get(xk), s.series);
      ys.forEach(function (yk, j) {
        var rr = sub.get(yk);
        var v = rr ? measure(key, rr) : null;
        if (v != null) { mn = Math.min(mn, v); mx = Math.max(mx, v); }
        data.push([i, j, v]);
      });
    });
    var opt = base();
    opt.legend.show = false;
    opt.grid = { left: 8, right: 16, top: 10, bottom: 40, containLabel: true };
    opt.tooltip.trigger = 'item';
    opt.tooltip.formatter = function (p) { return esc(xs[p.value[0]]) + ' · ' + esc(ys[p.value[1]]) + '<br><b>' + esc(fmtM(key, p.value[2])) + '</b>'; };
    var xa = Object.assign(axisStyle(), { type: 'category', data: xs, splitArea: { show: false } });
    xa.splitLine = { show: false };
    var ya = Object.assign(axisStyle(), { type: 'category', data: ys });
    ya.splitLine = { show: false };
    opt.xAxis = xa; opt.yAxis = ya;
    opt.visualMap = {
      min: isFinite(mn) ? mn : 0, max: isFinite(mx) ? mx : 1, calculable: true, orient: 'horizontal', left: 'center', bottom: 0,
      itemHeight: 120, itemWidth: 10, textStyle: { color: C.muted, fontSize: 11 },
      inRange: { color: SEQ },
      formatter: function (v) { return fmtM(key, v, true); }
    };
    opt.series = [{
      type: 'heatmap', data: data,
      label: { show: xs.length * ys.length <= 120, fontSize: LBL, formatter: function (p) { return fmtM(key, p.value[2], true); } },
      itemStyle: { borderColor: C.surface, borderWidth: 2, borderRadius: 3 }
    }];
    return opt;
  }

  function gauge(c, rows) {
    var s = c.spec, key = s.y[0].key, v = measure(key, rows);
    var f = M[key].format || '';
    var max = typeof s.max === 'number' ? s.max : (s.max && M[s.max] ? measure(s.max, rows) : (/percent|%/.test(f) ? 1 : niceMax(v)));
    var main = c.main || C.brand;
    var opt = base();
    opt.legend.show = false;
    opt.tooltip.show = false;
    opt.series = [{
      type: 'gauge', min: 0, max: max || 1, startAngle: 210, endAngle: -30, radius: '92%', center: ['50%', '58%'],
      progress: { show: true, width: 14, roundCap: true, itemStyle: { color: main } },
      axisLine: { roundCap: true, lineStyle: { width: 14, color: [[1, mix(C.surface, main, 0.14)]] } },
      axisTick: { show: false }, splitLine: { show: false }, pointer: { show: false },
      axisLabel: { show: false }, title: { offsetCenter: [0, '36%'], color: C.muted, fontSize: LBL },
      detail: {
        valueAnimation: true, offsetCenter: [0, '-2%'], fontSize: FT('hero').size, fontWeight: FT('hero').weight, color: C.text,
        formatter: function (x) { return fmtM(key, x); }
      },
      data: [{ value: v == null ? 0 : v, name: M[key].name }]
    }];
    return opt;
  }

  // 子弹图：实际（主色细条）+ 目标（深色刻线）+ 背景区间；有 x 时每个类目一行
  function bullet(c, rows) {
    var s = c.spec, ka = s.y[0].key, kt = s.y[1].key, main = c.main || C.brand, rr = T['rre-data-radius'];
    var cats, pairs;
    if (s.x) {
      var gm = groupBy(rows, s.x);
      cats = orderCats(Object.assign({}, s, { sort: s.sort || 'value_desc' }), gm, function (k) {
        var a = measure(ka, gm.get(k)), t = measure(kt, gm.get(k));
        return a != null && t ? a / t : 0;
      });
      pairs = cats.map(function (k) { return [measure(ka, gm.get(k)), measure(kt, gm.get(k))]; });
    } else {
      cats = [M[ka].name];
      pairs = [[measure(ka, rows), measure(kt, rows)]];
    }
    var rawMax = Math.max.apply(null, pairs.map(function (p) { return Math.max(p[0] || 0, p[1] || 0); }).concat([0])) * 1.05 || 1;
    var step = Math.pow(10, Math.floor(Math.log10(rawMax))) / 2;
    var maxv = Math.ceil(rawMax / step) * step;
    var opt = base();
    opt.legend.show = false;
    opt.grid = { left: 8, right: 64, top: 8, bottom: 8, containLabel: true };
    opt.tooltip.trigger = 'axis';
    opt.tooltip.axisPointer = { type: 'none' };
    opt.tooltip.formatter = function (ps) {
      var i = ps[0].dataIndex, p = pairs[i];
      return '<b>' + esc(cats[i]) + '</b><br>' + esc(M[ka].name) + '：' + esc(fmtM(ka, p[0])) + '<br>' + esc(M[kt].name) + '：' +
        esc(fmtM(kt, p[1])) + '<br>达成：' + (p[1] ? (p[0] / p[1] * 100).toFixed(1) + '%' : '–');
    };
    var ya = Object.assign(axisStyle(), { type: 'category', data: cats, inverse: true });
    ya.axisLine = { show: false };
    ya.splitLine = { show: false };
    var xa = Object.assign(axisStyle(), { type: 'value', max: maxv, axisLabel: Object.assign(axisStyle().axisLabel, { formatter: function (v) { return fmtM(ka, v, true); } }) });
    opt.yAxis = ya;
    opt.xAxis = xa;
    var bw = Math.min(SZ('bar-max'), 12);
    opt.series = [
      { type: 'bar', data: cats.map(function () { return maxv; }), barWidth: SZ('bar-max'), barGap: '-100%', silent: true, z: 1,
        itemStyle: { color: mix(C.surface, main, 0.1), borderRadius: rr }, tooltip: { show: false } },
      { type: 'bar', name: M[ka].name, data: pairs.map(function (p) { return p[0]; }), barWidth: bw, z: 2,
        itemStyle: { color: main, borderRadius: rr },
        label: { show: true, position: 'right', distance: 10, color: C.text, fontSize: LBL,
          formatter: function (p) { var t = pairs[p.dataIndex][1]; return t ? (p.value / t * 100).toFixed(1) + '%' : ''; } } },
      { type: 'scatter', name: M[kt].name, data: pairs.map(function (p) { return p[1]; }), symbol: 'rect',
        symbolSize: [3, SZ('bar-max')], itemStyle: { color: C.text }, z: 3 }
    ];
    return opt;
  }


  function niceMax(v) {
    if (!v || v <= 0) return 1;
    var p = Math.pow(10, Math.floor(Math.log10(v)));
    return Math.ceil(v * 1.2 / p) * p;
  }
  function mix(a, b, t) {
    function rgb(h) {
      h = String(h).replace('#', '');
      if (h.length === 3) h = h.split('').map(function (x) { return x + x; }).join('');
      var n = parseInt(h.slice(0, 6), 16);
      return isNaN(n) ? [255, 255, 255] : [(n >> 16) & 255, (n >> 8) & 255, n & 255];
    }
    var x = rgb(a), y = rgb(b);
    return 'rgb(' + x.map(function (v, i) { return Math.round(v + (y[i] - v) * t); }).join(',') + ')';
  }

  // 健康度：只有 kpi / gauge / bullet 判整卡好坏；未声明 status 的卡为“蓝·不判”
  var STATUS = { positive: ['✓', '向好'], warning: ['!', '关注'], negative: ['✕', '承压'] };
  function statusOf(s, rows) {
    var st = s.status;
    if (!st) return null;
    var v;
    if (st.measure) v = measure(st.measure, rows);
    else if (s.type === 'bullet') {
      var a = measure(s.y[0].key, rows), t = measure(s.y[1].key, rows);
      v = a != null && t ? a / t : null;
    } else v = measure(s.y[0].key, rows);
    if (v == null) return null;
    var up = st.direction !== 'down';
    if (up ? v >= st.good : v <= st.good) return 'positive';
    if (st.warn != null && (up ? v >= st.warn : v <= st.warn)) return 'warning';
    return 'negative';
  }
  function applyStatus(c, lvl) {
    var chip = c.node.querySelector('.rf-status');
    if (!lvl) {
      c.node.removeAttribute('data-status');
      if (chip) chip.hidden = true;
      c.main = C.brand;
      return;
    }
    c.node.setAttribute('data-status', lvl);
    c.main = C[lvl];
    if (chip) {
      var lb = (c.spec.status.labels || {})[lvl] || STATUS[lvl][1];
      chip.innerHTML = '<i>' + STATUS[lvl][0] + '</i>' + esc(lb);
      chip.hidden = false;
    }
  }
  function heroHTML(key, v) {
    var str = fmtM(key, v);
    var m = /^([¥$]?[-+]?[\d,]+(?:\.\d+)?)(.*)$/.exec(str);
    return m ? '<b>' + esc(m[1]) + '</b>' + (m[2] ? '<em>' + esc(m[2]) + '</em>' : '') : '<b>' + esc(str) + '</b>';
  }
  function factHTML(sb, rows) {
    var sv = measure(sb.key, rows), cls = '', arrow = '', txt = fmtM(sb.key, sv);
    if (sb.trend && sv != null && sv !== 0) {
      arrow = sv > 0 ? '▲ ' : '▼ ';
      if (sv > 0 && txt.charAt(0) !== '+') txt = '+' + txt;
      // 增减按指标方向上色：up=越大越好，down=越小越好，none=不着色
      if (sb.trend === 'up') cls = sv > 0 ? 'up' : 'down';
      else if (sb.trend === 'down') cls = sv > 0 ? 'down' : 'up';
    }
    return '<div class="rf-fact"><span>' + esc(M[sb.key].name) + '</span><b class="' + cls + '">' + arrow + esc(txt) + '</b></div>';
  }
  function renderKPI(c, rows) {
    var s = c.spec, key = s.y[0].key, v = measure(key, rows);
    var facts = (s.sub || []).map(function (sb) { return factHTML(sb, rows); }).join('');
    c.body.innerHTML = '<div class="rf-hero">' + heroHTML(key, v) + '</div>' + (facts ? '<div class="rf-facts">' + facts + '</div>' : '');
  }


  function renderTable(c, rows) {
    var s = c.spec, head = s.headers || {}, out = [], cols = [];
    function label(f) {
      if (head[f]) return head[f];
      var flt = F.filter(function (x) { return x.field === f; })[0];
      return flt ? flt.label : f;
    }
    if (s.columns.length && !s.y.length) {
      cols = s.columns.map(function (f) { return { label: label(f), get: function (r) { return r[f]; }, num: false }; });
      out = rows.slice(0, s.page_size || s.top || 200);
    } else {
      var dims = s.dims.length ? s.dims : s.x ? [s.x] : [];
      var gm = new Map();
      rows.forEach(function (r) {
        var k = dims.map(function (d) { return r[d]; }).join('\u0001');
        var a = gm.get(k);
        if (!a) gm.set(k, (a = []));
        a.push(r);
      });
      gm.forEach(function (rr) {
        var o = { _r: rr };
        dims.forEach(function (d) { o[d] = rr[0][d]; });
        s.y.forEach(function (y) { o[y.key] = measure(y.key, rr); });
        out.push(o);
      });
      var sk = s.y[0] && s.y[0].key;
      if (s.sort === 'x_asc') out.sort(function (a, b) { return cmp(a[dims[0]], b[dims[0]]); });
      else if (s.sort === 'x_desc') out.sort(function (a, b) { return cmp(b[dims[0]], a[dims[0]]); });
      else if (sk && s.sort !== 'none') out.sort(function (a, b) { var d = (b[sk] || 0) - (a[sk] || 0); return s.sort === 'value_asc' ? -d : d; });
      out = out.slice(0, s.top || s.page_size || 500);
      cols = dims.map(function (d) { return { label: label(d), get: function (r) { return r[d]; }, num: false }; })
        .concat(s.y.map(function (y, i) {
          return { label: M[y.key].name, key: y.key, get: function (r) { return r[y.key]; }, num: true, bar: i === 0 && s.bar_in_cell };
        }));
    }
    var maxBar = 0;
    cols.forEach(function (col) { if (col.bar) out.forEach(function (r) { maxBar = Math.max(maxBar, Math.abs(col.get(r) || 0)); }); });
    var h = '<div class="rf-table-wrap"><table class="rf-table"><thead><tr><th class="rf-rank">#</th>' +
      cols.map(function (col) { return '<th class="' + (col.num ? 'num' : '') + '">' + esc(col.label) + '</th>'; }).join('') + '</tr></thead><tbody>';
    out.forEach(function (r, i) {
      h += '<tr><td class="rf-rank">' + (i + 1) + '</td>' + cols.map(function (col) {
        var v = col.get(r);
        if (!col.num) return '<td>' + esc(v == null ? '–' : v) + '</td>';
        var bar = col.bar && maxBar ? '<i class="rf-cellbar" style="width:' + (Math.abs(v || 0) / maxBar * 100).toFixed(1) + '%"></i>' : '';
        return '<td class="num">' + bar + '<span>' + esc(fmtM(col.key, v)) + '</span></td>';
      }).join('') + '</tr>';
    });
    if (!out.length) h += '<tr><td colspan="' + (cols.length + 1) + '" class="rf-empty-cell">当前筛选条件下无数据</td></tr>';
    c.body.innerHTML = h + '</tbody></table></div>';
  }

  function renderChart(c) {
    var s = c.spec;
    var lf = /bar|line|area|combo|pie|donut|funnel|treemap/.test(s.type) || s.type === 'hbar' ? linkFilter(s) : null;
    var linkActive = lf && isActive(lf, state[lf.id]);
    var rows = s.query ? rowsFor(s.query, lf ? lf.id : null) : [];
    var hint = c.node.querySelector('.rf-link-hint');
    if (hint) hint.hidden = !lf;
    if (/^(kpi|gauge|bullet)$/.test(s.type)) applyStatus(c, statusOf(s, rowsFor(s.query)));
    if (s.type === 'kpi') return renderKPI(c, rowsFor(s.query));
    if (s.type === 'table') return renderTable(c, rowsFor(s.query));
    var host = c.body;
    if (s.type === 'bullet' && !s.x) {
      // 单指标子弹图：上方主值 + 读数，下方图形
      if (!c.host) {
        c.body.innerHTML = '<div class="rf-bullet-top"></div><div class="rf-bullet-chart"></div>';
        c.host = c.body.lastChild;
        c.host.style.height = Math.max(60, c.body.clientHeight - 120) + 'px';
      }
      host = c.host;
      var bt = c.body.firstChild, a0 = measure(s.y[0].key, rows), t0 = measure(s.y[1].key, rows);
      bt.innerHTML = '<div class="rf-hero">' + heroHTML(s.y[0].key, a0) + '</div><div class="rf-facts">' +
        '<div class="rf-fact"><span>' + esc(M[s.y[1].key].name) + '</span><b>' + esc(fmtM(s.y[1].key, t0)) + '</b></div>' +
        '<div class="rf-fact"><span>达成率</span><b>' + (t0 ? (a0 / t0 * 100).toFixed(1) + '%' : '–') + '</b></div></div>';
    }
    var empty = c.node.querySelector('.rf-empty');
    if (!rows.length) {
      if (c.inst) c.inst.clear();
      if (!empty) { empty = el('div', 'rf-empty', '当前筛选条件下无数据'); c.node.appendChild(empty); }
      return;
    }
    if (empty) empty.remove();
    if (!c.inst) {
      c.inst = echarts.init(host, null, { renderer: 'canvas' });
      c.inst.on('click', function (p) {
        var f = linkFilter(s);
        if (f && p.name != null && p.componentType === 'series') toggle(f, p.name);
      });
      if (window.ResizeObserver) new ResizeObserver(function () { c.inst && c.inst.resize(); }).observe(host);
    }
    var opt;
    try {
      if (/^(bar|hbar|stacked_bar|line|area|stacked_area|combo)$/.test(s.type)) opt = cartesian(c, rows, linkActive ? lf : null);
      else if (/^(pie|donut|funnel|treemap)$/.test(s.type)) opt = categorical(c, rows, linkActive ? lf : null);
      else if (s.type === 'scatter') opt = scatter(c, rows);
      else if (s.type === 'heatmap') opt = heatmap(c, rows);
      else if (s.type === 'gauge') opt = gauge(c, rows);
      else if (s.type === 'bullet') opt = bullet(c, rows);
      c.inst.setOption(opt, true);
    } catch (e) {
      console.error('图表渲染失败', s.id, e);
      c.body.innerHTML = '<div class="rf-empty">图表渲染失败：' + esc(e.message) + '</div>';
      c.inst = null;
    }
  }

  function updateMetrics() {
    document.querySelectorAll('.rf-metric[data-m]').forEach(function (n) {
      var k = n.getAttribute('data-m'), m = M[k];
      if (!m) return;
      n.textContent = fmtM(k, measure(k, rowsFor(m.query)), false, n.getAttribute('data-fmt') || null);
    });
  }
  function renderAll() {
    charts.forEach(renderChart);
    updateMetrics();
  }

  // ------------------------------------------------------------------ 目录高亮
  function tocSpy() {
    var links = document.querySelectorAll('.rf-toc a');
    if (!links.length || !window.IntersectionObserver) return;
    var io = new IntersectionObserver(function (ents) {
      ents.forEach(function (e) {
        if (e.isIntersecting) links.forEach(function (a) { a.classList.toggle('on', a.getAttribute('href') === '#' + e.target.id); });
      });
    }, { rootMargin: '-20% 0px -70% 0px' });
    document.querySelectorAll('.rf-section').forEach(function (s) { io.observe(s); });
  }

  buildBar();
  initState();
  drawControls();
  renderAll();
  tocSpy();
  window.addEventListener('beforeprint', function () { charts.forEach(function (c) { c.inst && c.inst.resize(); }); });
  window.__rf = { state: state, rowsFor: rowsFor, measure: measure, render: renderAll };
})();
