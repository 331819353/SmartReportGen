/* 唯一展示入口：必须原文内联；业务值由源端直接提供，这里只处理显示和状态。 */
(() => {
  'use strict';
  const contract = JSON.parse(document.getElementById('srg-display-contract').textContent);
  const root = document.documentElement;
  const states = ['up', 'down', 'neutral', 'met', 'miss'];
  if (!['percent-only', 'semantic'].includes(contract.rate_symbols)) throw new Error('未确认率值符号约定');
  for (const state of states) {
    const color = contract.colors[state];
    if (!/^#[\da-f]{6}$/i.test(color)) throw new Error(`状态色无效：${state}`);
    root.style.setProperty(`--srg-${state}`, color);
  }
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const precision = digits => Number.isInteger(digits) && digits >= 0 && digits <= 6;
  const number = (value, digits, signed) => new Intl.NumberFormat('zh-CN', {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
    signDisplay: signed ? 'exceptZero' : 'auto'
  }).format(Object.is(value, -0) ? 0 : value);

  function fmtNum(value, { digits = 2, divisor = 1, unit = '', signed = false } = {}) {
    // divisor 只能使用模板中已确认的单位换算系数；未知单位不使用默认推断。
    if (!finite(value) || !finite(divisor) || divisor <= 0 || !precision(digits)) return '—';
    return number(value / divisor, digits, signed) + unit;
  }

  function fmtPct(value, { scale, digits = 2, signed = false } = {}) {
    // decimal: 0.0978；percent: 9.78。必须显式传入尺度，不能按值猜测。
    if (!finite(value) || !['decimal', 'percent'].includes(scale) || !precision(digits)) return '—';
    return number(scale === 'decimal' ? value * 100 : value, digits, signed) + '%';
  }

  function fmtDelta(value, { kind, scale, digits = 2, divisor = 1, unit = '' } = {}) {
    // 只格式化源端差额，不接收两个业务值做减法；率差的语义由 kind 明确保留。
    if (kind === 'number') return fmtNum(value, { digits, divisor, unit, signed: true });
    if (kind !== 'rate') return '—';
    const label = fmtPct(value, { scale, digits, signed: true });
    if (label === '—' || contract.rate_symbols === 'percent-only') return label;
    return label.slice(0, -1) + 'pp';
  }

  function metricState(value, { kind, scale, threshold } = {}) {
    // 非唯一事实在取数绑定处置为 null；未知尺度及缺阈值不能残留旧判断。
    if (!finite(value) || !['decimal', 'percent', 'number'].includes(scale)) return 'neutral';
    if (kind === 'trend') return value > 0 ? 'up' : value < 0 ? 'down' : 'neutral';
    if (kind === 'attainment' && scale !== 'number' && finite(threshold)) return value >= threshold ? 'met' : 'miss';
    return 'neutral';
  }

  function stateColor(state) {
    return contract.colors[states.includes(state) ? state : 'neutral'];
  }

  function applyMetric(element, text, state = 'neutral') {
    // 每次移除全部历史状态；空值仍有中性色，不能沿用上一次的红绿判断。
    element.classList.remove(...states.map(value => `srg-${value}`));
    element.classList.add(`srg-${text === '—' || !states.includes(state) ? 'neutral' : state}`);
    element.textContent = text;
  }

  window.SrgDisplay = Object.freeze({ fmtNum, fmtPct, fmtDelta, metricState, stateColor, applyMetric });
})();
