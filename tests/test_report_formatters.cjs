/* 直接执行交付模块，验证格式化、原始阈值、缺数及历史状态清理，无业务数据访问。 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const moduleText = fs.readFileSync(path.join(__dirname, '../skills/smart-report/assets/report-formatters.js'), 'utf8');

function load(mode) {
  const contract = {rate_symbols: mode, colors: {
    up: '#047857', down: '#B91C1C', neutral: '#475569', met: '#047857', miss: '#B91C1C'
  }};
  const properties = {};
  const context = {window: {}, document: {
    getElementById: () => ({textContent: JSON.stringify(contract)}),
    documentElement: {style: {setProperty: (key, value) => { properties[key] = value; }}}
  }};
  vm.runInNewContext(moduleText, context);
  return {api: context.window.SrgDisplay, properties};
}

const {api: f, properties} = load('percent-only');
assert.equal(f.fmtPct(0.0978, {scale: 'decimal'}), '9.78%');
assert.equal(f.fmtPct(9.78, {scale: 'percent'}), '9.78%');
assert.equal(f.fmtDelta(-3.66, {kind: 'rate', scale: 'percent'}), '-3.66%');
assert.equal(f.fmtDelta(-0.0366, {kind: 'rate', scale: 'decimal'}), '-3.66%');
assert.equal(f.fmtNum(120000, {divisor: 10000, unit: '万元'}), '12.00万元');
assert.equal(f.fmtNum(0, {digits: 0}), '0');
assert.equal(f.fmtPct(-0, {scale: 'percent', signed: true}), '0.00%');
for (const value of [null, undefined, NaN, Infinity, '', '9.78']) {
  assert.equal(f.fmtPct(value, {scale: 'percent'}), '—');
  assert.equal(f.metricState(value, {kind: 'trend', scale: 'percent'}), 'neutral');
}
assert.equal(f.fmtPct(0.8), '—');
assert.equal(f.fmtDelta(3.66, {scale: 'percent'}), '—');
assert.equal(f.metricState(0.8, {kind: 'trend'}), 'neutral');
for (const [value, state] of [[1, 'up'], [-1, 'down'], [0, 'neutral']]) {
  assert.equal(f.metricState(value, {kind: 'trend', scale: 'number'}), state);
}
for (const [value, state] of [[1.001, 'met'], [1, 'met'], [0.99997, 'miss']]) {
  assert.equal(f.metricState(value, {kind: 'attainment', scale: 'decimal', threshold: 1}), state);
}
assert.equal(f.metricState(1, {kind: 'attainment', scale: 'decimal'}), 'neutral');
assert.equal(f.stateColor('up'), properties['--srg-up']);
assert.equal(f.stateColor('unknown'), properties['--srg-neutral']);
const classes = new Set(['metric', 'srg-down']);
const element = {textContent: '', classList: {
  remove: (...names) => names.forEach(name => classes.delete(name)),
  add: name => classes.add(name)
}};
f.applyMetric(element, '+3.66%', 'up');
assert.deepEqual([...classes].sort(), ['metric', 'srg-up']);
f.applyMetric(element, '—', 'up');
assert.equal(element.textContent, '—');
assert.deepEqual([...classes].sort(), ['metric', 'srg-neutral']);
const {api: semantic} = load('semantic');
assert.equal(semantic.fmtDelta(-3.66, {kind: 'rate', scale: 'percent'}), '-3.66pp');
assert.equal(semantic.fmtPct(9.78, {scale: 'percent'}), '9.78%');
console.log('展示模块验证通过：格式、尺度、正负零、阈值、缺数与状态清理');
