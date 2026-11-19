/* 唯一展示入口：必须原文内联；业务值由查询快照提供，这里只处理显示和状态。 */
(() => {
  'use strict';
  const contract = JSON.parse(document.getElementById('srg-display-contract').textContent);
  const root = document.documentElement;
  const states = ['up', 'down', 'neutral', 'met', 'miss', 'warning'];
  if (!['percent-only', 'semantic'].includes(contract.rate_symbols)) throw new Error('未确认率值符号约定');
  // 旧版展示契约没有预警色，保留原报告的离线兼容。
  const colors = {warning: '#A16207', ...contract.colors};
  for (const state of states) {
    const color = colors[state];
    if (!/^#[\da-f]{6}$/i.test(color)) throw new Error(`状态色无效：${state}`);
    root.style.setProperty(`--srg-${state}`, color);
  }
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const precision = digits => Number.isInteger(digits) && digits >= 0 && digits <= 6;
  // Decimal 和超出 JS 安全范围的整数由服务端标记；业务文本永远不会被隐式转成数值。
  function exact(value) {
    const written = typeof value === 'number' && finite(value) ? String(value) : value?.$decimal;
    if (typeof written !== 'string') return null;
    const parts = /^([+-]?)(\d+)(?:\.(\d+))?(?:e([+-]?\d+))?$/i.exec(written);
    if (!parts) return null;
    const fraction = parts[3] || '';
    const exponent = Number(parts[4] || 0) - fraction.length;
    if (Math.abs(exponent) > 1000 || written.length > 1024) return null;
    const n = BigInt(parts[1] + parts[2] + fraction);
    return exponent < 0 ? [n, 10n ** BigInt(-exponent)] : [n * 10n ** BigInt(exponent), 1n];
  }

  function compare(left, right) {
    const a = exact(left), b = exact(right);
    if (!a || !b) return null;
    const diff = a[0] * b[1] - b[0] * a[1];
    return diff > 0n ? 1 : diff < 0n ? -1 : 0;
  }

  function bridgeCoordinates(opening, changes, closing) {
    // 这里只计算浮动柱的起止位置。先按十进制原值核对平衡，期末和标签仍取源值。
    const values = [opening, ...changes, closing].map(exact);
    if (values.some(value => value === null)) return null;
    const magnitude = values.reduce((max, [, den]) => den > max ? den : max, 1n);
    const units = values.map(([num, den]) => num * (magnitude / den));
    const digits = String(magnitude).length - 1;
    const coordinate = value => Number(String(value) + 'e-' + digits);
    let run = units[0];
    const geometry = [[0, coordinate(run)]];
    for (const delta of units.slice(1, -1)) {
      const from = coordinate(run);
      run += delta;
      geometry.push([from, coordinate(run)]);
    }
    if (run !== units.at(-1)) return null;
    geometry.push([0, coordinate(units.at(-1))]);
    return geometry.flat().every(finite) ? geometry : null;
  }

  function precise(value, digits, signed, multiplier = 1, divisor = 1) {
    const parts = exact(value), unit = exact(divisor);
    if (!parts || !unit || unit[0] <= 0n) return null;
    const numerator = parts[0] * BigInt(multiplier) * unit[1] * 10n ** BigInt(digits);
    const denominator = parts[1] * unit[0];
    const absolute = numerator < 0n ? -numerator : numerator;
    const rounded = (2n * absolute + denominator) / (2n * denominator);
    const padded = String(rounded).padStart(digits + 1, '0');
    const whole = digits ? padded.slice(0, -digits) : padded;
    const fraction = digits ? '.' + padded.slice(-digits) : '';
    const sign = rounded === 0n ? '' : numerator < 0n ? '-' : signed ? '+' : '';
    return sign + new Intl.NumberFormat('zh-CN').format(BigInt(whole)) + fraction;
  }

  function fmtNum(value, { digits = 2, divisor = 1, unit = '', signed = false } = {}) {
    // divisor 只能使用模板中已确认的单位换算系数；未知单位不使用默认推断。
    if (!finite(divisor) || divisor <= 0 || !precision(digits)) return '—';
    const text = precise(value, digits, signed, 1, divisor);
    return text === null ? '—' : text + unit;
  }

  function fmtPct(value, { scale, digits = 2, signed = false } = {}) {
    // decimal: 0.0978；percent: 9.78。必须显式传入尺度，不能按值猜测。
    if (!['decimal', 'percent'].includes(scale) || !precision(digits)) return '—';
    const multiplier = scale === 'decimal' ? 100 : 1;
    const text = precise(value, digits, signed, multiplier);
    return text === null ? '—' : text + '%';
  }

  function fmtDelta(value, { kind, scale, digits = 2, divisor = 1, unit = '' } = {}) {
    // 只格式化源端差额，不接收两个业务值做减法；率差的语义由 kind 明确保留。
    if (kind === 'number') return fmtNum(value, { digits, divisor, unit, signed: true });
    if (kind !== 'rate') return '—';
    const label = fmtPct(value, { scale, digits, signed: true });
    if (label === '—' || contract.rate_symbols === 'percent-only') return label;
    return label.slice(0, -1) + 'pp';
  }

  function metricState(value, { kind, scale, threshold, warningThreshold, direction = 'higher' } = {}) {
    if (!['decimal', 'percent', 'number'].includes(scale) || !['higher', 'lower'].includes(direction)) return 'neutral';
    // up/down 表示好坏色的映射；格式化始终使用原始正负号。
    const order = direction === 'lower' ? -1 : 1;
    const zero = compare(value, 0);
    if (zero === null) return 'neutral';
    if (kind === 'trend') return zero === 0 ? 'neutral' : zero * order > 0 ? 'up' : 'down';
    if (kind !== 'attainment') return 'neutral';
    const met = compare(value, threshold);
    if (met === null) return 'neutral';
    if (met * order >= 0) return 'met';
    const warning = compare(value, warningThreshold);
    return warning !== null && warning * order >= 0 ? 'warning' : 'miss';
  }

  function stateColor(state) {
    return colors[states.includes(state) ? state : 'neutral'];
  }

  function applyMetric(element, text, state = 'neutral') {
    // 每次移除全部历史状态；空值仍有中性色，不能沿用上一次的红绿判断。
    element.classList.remove(...states.map(value => `srg-${value}`));
    const active = text === '—' || !states.includes(state) ? 'neutral' : state;
    element.classList.add(`srg-${active}`);
    if (element.dataset) {
      const semantics = {up: 'positive', met: 'positive', down: 'negative', miss: 'negative', warning: 'warning'};
      element.dataset.state = semantics[active] || 'neutral';
    }
    element.textContent = text;
  }

  window.SrgDisplay = Object.freeze({ fmtNum, fmtPct, fmtDelta, metricState, stateColor, applyMetric, bridgeCoordinates });
})();
