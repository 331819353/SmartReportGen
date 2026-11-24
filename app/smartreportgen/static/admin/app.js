/* SmartReportGen 管理台（Vue 3，无构建） */
const { createApp, reactive, ref, computed, onMounted, onBeforeUnmount, watch, nextTick } = Vue;

// ------------------------------------------------------------ 工具
const BASE_PATH = (window.__SRG_BASE_PATH__ || '').replace(/\/$/, '');
const appPath = path => BASE_PATH && path.startsWith('/') ? BASE_PATH + path : path;
const toasts = reactive([]);
function toast(msg, kind = '') {
  // 顶层弹窗只展示自身操作触发的通知，避免前一个任务的消息混入新弹窗。
  const dialog = document.activeElement?.closest('dialog[open]') || [...document.querySelectorAll('dialog[open]')].at(-1) || null;
  const t = { id: Math.random(), msg, kind, dialog };
  toasts.push(t);
  setTimeout(() => { const i = toasts.indexOf(t); if (i >= 0) toasts.splice(i, 1); }, kind === 'err' ? 7000 : 3000);
}
async function api(path, opts = {}) {
  const init = { method: opts.method || 'GET', headers: {}, credentials: 'same-origin', signal: opts.signal };
  if (opts.body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.body); }
  const r = await fetch(appPath(path), init);
  if (r.ok && opts.download) return r.blob();
  const ct = r.headers.get('content-type') || '';
  const data = ct.includes('json') ? await r.json() : await r.text();
  if (!r.ok) {
    const msg = (data && data.detail) ? (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)) : String(data).slice(0, 300);
    if (r.status === 401 && !path.startsWith('/api/auth/')) state.needLogin = true;
    throw Object.assign(new Error(msg), { status: r.status });
  }
  return data;
}
async function run(fn, okMsg) {
  try { const r = await fn(); if (okMsg) toast(okMsg, 'ok'); return r; } catch (e) { toast(e.message, 'err'); throw e; }
}
const fmtTime = s => s ? String(s).replace('T', ' ').slice(0, 16) : '';
const isAdmin = () => !!(state.me && state.me.is_admin);
const creator = x => x.created_by ? `${x.creator_name || ''}${x.creator_name ? '（' + x.created_by + '）' : x.created_by}` : '系统';
const fmtSize = n => n == null ? '' : n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : (n / 1024).toFixed(0) + ' KB';
const DS_LABEL = { mysql: 'MySQL', postgresql: 'PostgreSQL', oracle: 'Oracle', starrocks: 'StarRocks', api: 'HTTP API' };
const OBJECT_LABEL = { table: '表', view: '视图', materialized_view: '物化视图' };
const DEFAULT_PORT = { mysql: 3306, postgresql: 5432, oracle: 1521, starrocks: 9030 };
function jsonOrThrow(txt, what) {
  if (!txt || !txt.trim()) return {};
  try { const v = JSON.parse(txt); if (!v || typeof v !== 'object' || Array.isArray(v)) throw 0; return v; }
  catch (e) { throw new Error(`${what}须为 JSON 对象，键名和字符串请使用英文双引号。`); }
}
const pretty = v => JSON.stringify(v || {}, null, 2);

const state = reactive({ page: location.hash.slice(1) || 'home', meta: null, needLogin: false, me: null, authCfg: null });
window.addEventListener('hashchange', () => { state.page = location.hash.slice(1) || 'home'; });

// 报告版本变动后刷新管理总览与报告库。
const reportRevision = ref(0);
const reportPath = r => appPath('/reports/' + r.id);
const reportVersionPath = (r, action = '') => reportPath(r) + action + '?version=' + r.revision;

function matches(kw, ...fields) {
  const term = kw.trim().toLocaleLowerCase();
  return !term || fields.some(value => String(value ?? '').toLocaleLowerCase().includes(term));
}

// 加载失败时保留上次结果，并在列表内提供重试入口。
function useCollection(url, initial = []) {
  const list = ref(initial), loading = ref(true), loadError = ref('');
  async function load() {
    loading.value = true;
    loadError.value = '';
    try { list.value = await api(url); }
    catch (e) { loadError.value = e.message; }
    finally { loading.value = false; }
  }
  onMounted(load);
  return { list, loading, loadError, load };
}

const ICONS = {
  home: '<path d="M3 10.5 10 4l7 6.5V17a1 1 0 0 1-1 1h-4v-5H8v5H4a1 1 0 0 1-1-1z"/>',
  ds: '<ellipse cx="10" cy="5" rx="6.5" ry="2.5"/><path d="M3.5 5v10c0 1.4 2.9 2.5 6.5 2.5s6.5-1.1 6.5-2.5V5M3.5 10c0 1.4 2.9 2.5 6.5 2.5s6.5-1.1 6.5-2.5"/>',
  set: '<rect x="3" y="3" width="14" height="14" rx="2"/><path d="M3 8h14M8 8v9"/>',
  tpl: '<path d="M5 2.5h7l3.5 3.5v11.5h-10.5z"/><path d="M12 2.5V6h3.5M7.5 10h5M7.5 13h5"/>',
  rep: '<rect x="3" y="3" width="14" height="14" rx="2"/><path d="M7 14v-3M10 14V7M13 14v-5"/>',
  key: '<circle cx="7" cy="12.5" r="3.5"/><path d="M9.5 10 16 3.5M13.5 6l2 2M11.8 7.7l1.5 1.5"/>',
  perm: '<rect x="4" y="9" width="12" height="8.5" rx="1.8"/><path d="M7 9V6.5a3 3 0 0 1 6 0V9M10 12.5v2"/>',
  sty: '<circle cx="10" cy="10" r="7"/><circle cx="7" cy="8" r="1.2"/><circle cx="11" cy="6.5" r="1.2"/><circle cx="13.5" cy="10" r="1.2"/><path d="M10 17a2 2 0 0 1 0-4h1.5"/>',
  plus: '<path d="M10 4v12M4 10h12"/>',
  arrow: '<path d="M4 10h12m-5-5 5 5-5 5"/>',
  back: '<path d="M16 10H4m5 5-5-5 5-5"/>',
  pencil: '<path d="m4 12.5 8.6-8.6a2 2 0 0 1 2.8 2.8l-8.6 8.6L4 16zM11.5 5l3 3"/>',
  search: '<circle cx="8.5" cy="8.5" r="4.5"/><path d="m12 12 4 4"/>',
  indent: '<path d="M4 5h12M8 10h8M8 15h8M4 10h.01M4 15h.01"/>',
  chevron: '<path d="m7 8 3 3 3-3"/>',
  bold: '<path d="M6.5 10h4.4a2.5 2.5 0 0 0 0-5H6.5v10h5a2.5 2.5 0 0 0 0-5h-.6"/>',
  italic: '<path d="M9 5h6M5 15h6m1-10-4 10"/>',
  strike: '<path d="M13.5 6.5c-.5-1-1.8-1.5-3.5-1.5-2 0-3.5.8-3.5 2.5M3.5 10h13m-3.2 2.4c0 1.7-1.4 2.6-3.5 2.6-1.9 0-3.2-.6-3.8-1.7"/>',
  inlineCode: '<path d="m7.5 6-4 4 4 4m5-8 4 4-4 4"/>',
  link: '<path d="m8.8 11.2 2.4-2.4M7 13l-1 1a2.8 2.8 0 0 1-4-4l3.2-3.2a2.8 2.8 0 0 1 4 0M13 7l1-1a2.8 2.8 0 0 1 4 4l-3.2 3.2a2.8 2.8 0 0 1-4 0"/>',
  task: '<rect x="3.5" y="3.5" width="13" height="13" rx="2.5"/><path d="m6.5 10 2.3 2.3 4.7-4.7"/>',
  outdent: '<path d="M4 5h12M4 15h12M11 10h5m-9-2.5L4.5 10 7 12.5"/>',
  indentMore: '<path d="M4 5h12M4 15h12M11 10h5m-12-2.5L6.5 10 4 12.5"/>',
  divider: '<path d="M3.5 10h13"/>',
  bullets: '<path d="M7.5 5h9m-9 5h9m-9 5h9M3.5 5h.01m-.01 5h.01m-.01 5h.01"/>',
  ordered: '<path d="M9 6h7m-7 8h7M3.5 4l1-.8V8M3 8h3m-2.8 4.5c.3-1.5 3-1.5 3 0 0 .7-.6 1.4-1.5 2.2L3 16h3.5"/>',
  quote: '<path d="M8 7c-2.4 1-3.6 2.4-3.6 4.7 0 1.3.7 2.3 1.8 2.3s1.9-.7 1.9-1.8c0-1.2-1-1.9-2.1-1.9H5m10.5-3.3c-2.4 1-3.6 2.4-3.6 4.7 0 1.3.7 2.3 1.8 2.3s1.9-.7 1.9-1.8c0-1.2-1-1.9-2.1-1.9h-1"/>',
  code: '<path d="m7 6-4 4 4 4m6-8 4 4-4 4M11.5 4.5 8.5 15.5"/>',
  table: '<rect x="3" y="4" width="14" height="12" rx="1.2"/><path d="M3 8h14M3 12h14M10 4v12"/>',
  undo: '<path d="m7 4-4 4 4 4M3 8h8a5 5 0 0 1 5 5v2"/>',
  redo: '<path d="m13 4 4 4-4 4m4-4H9a5 5 0 0 0-5 5v2"/>',
  menu: '<path d="M3 5h14M3 10h14M3 15h14"/>',
  close: '<path d="m5 5 10 10M5 15 15 5"/>',
  check: '<path d="m4 10 4 4 8-8"/>',
  alert: '<path d="M9.1 3.7 2.7 15a1 1 0 0 0 .9 1.5h12.8a1 1 0 0 0 .9-1.5L10.9 3.7a1 1 0 0 0-1.8 0Z"/><path d="M10 8v3.5m0 2.2h.01"/>'
};
const icon = k => `<svg aria-hidden="true" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${ICONS[k]}</svg>`;

// ------------------------------------------------------------ 通用组件
const Drawer = {
  props: { title: String, wide: Boolean, compact: Boolean, busy: Boolean, confirm: Boolean, backdropClose: { type: Boolean, default: true } },
  emits: ['close'],
  template: `<dialog ref="dialog" class="drawer" :class="{wide, compact, 'confirm-drawer': confirm}" :aria-label="title"
    :role="confirm ? 'alertdialog' : null" :aria-describedby="confirm ? 'confirm-dialog-message' : null" :aria-busy="busy"
    @cancel.prevent="close" @pointerdown="onPointerDown" @pointercancel="clearBackdropPress" @click.self="backdrop" @close="onDialogClose">
    <div class="drawer-head"><h2>{{title}}</h2><button class="btn x" type="button" aria-label="关闭" :disabled="busy" @click="close" v-html="icon('close')"></button></div>
    <div class="msgs dialog-feedback" aria-live="polite"><div v-for="t in feedback" :key="t.id" class="msg" :class="t.kind">{{t.msg}}</div></div>
    <div class="drawer-body"><slot/></div>
    <div class="drawer-foot" v-if="$slots.foot"><slot name="foot"/></div></dialog>`,
  setup(props, { emit }) {
    const dialog = ref(null);
    const feedback = computed(() => toasts.filter(t => t.dialog === dialog.value));
    let backdropPress = false, unmounting = false;
    // 原生弹窗管理焦点与背景交互，关闭后焦点回到触发按钮。
    onMounted(() => dialog.value.showModal());
    onBeforeUnmount(() => { unmounting = true; if (dialog.value?.open) dialog.value.close(); });
    function close() { if (!props.busy) emit('close'); }
    function outside(e) {
      const { left, right, top, bottom } = dialog.value.getBoundingClientRect();
      return e.clientX < left || e.clientX > right || e.clientY < top || e.clientY > bottom;
    }
    function onPointerDown(e) {
      backdropPress = props.backdropClose && e.button === 0 && e.target === dialog.value && outside(e);
    }
    function clearBackdropPress() { backdropPress = false; }
    function backdrop(e) {
      const hit = props.backdropClose && backdropPress && e.target === dialog.value && outside(e);
      backdropPress = false;
      if (hit) close();
    }
    function onDialogClose() {
      if (!unmounting && dialog.value?.isConnected && !dialog.value.open) dialog.value.showModal();
    }
    return { dialog, close, onPointerDown, clearBackdropPress, backdrop, onDialogClose, icon, feedback };
  }
};
const confirmState = reactive({
  open: false,
  title: '请确认',
  message: '',
  confirmText: '确认',
  cancelText: '取消',
  danger: true,
});
let confirmResolve = null;
function askConfirm({ title = '请确认', message = '', confirmText = '确认', cancelText = '取消', danger = true } = {}) {
  if (confirmResolve) {
    const prev = confirmResolve;
    confirmResolve = null;
    prev(false);
  }
  Object.assign(confirmState, { open: true, title, message, confirmText, cancelText, danger });
  return new Promise(resolve => { confirmResolve = resolve; });
}
function settleConfirm(ok) {
  confirmState.open = false;
  const resolve = confirmResolve;
  confirmResolve = null;
  if (resolve) resolve(!!ok);
}
const ConfirmDialog = {
  components: { Drawer },
  template: `<Drawer v-if="confirmState.open" :title="confirmState.title" compact confirm @close="settleConfirm(false)">
    <div class="confirm-content">
      <span class="confirm-icon" :class="{danger: confirmState.danger}" v-html="icon(confirmState.danger ? 'alert' : 'check')"></span>
      <p id="confirm-dialog-message" class="confirm-dialog-msg">{{confirmState.message}}</p>
    </div>
    <template #foot>
      <button type="button" class="btn" autofocus @click="settleConfirm(false)">{{confirmState.cancelText}}</button>
      <button type="button" class="btn" :class="confirmState.danger ? 'danger-solid' : 'primary'" @click="settleConfirm(true)">{{confirmState.confirmText}}</button>
    </template>
  </Drawer>`,
  setup() {
    // 危险操作弹窗默认聚焦取消，键盘确认需要主动切换到确认按钮。
    onBeforeUnmount(() => { if (confirmState.open) settleConfirm(false); });
    return { confirmState, settleConfirm, icon };
  }
};
const DataTable = {
  props: ['columns', 'rows', 'max'],
  template: `<div class="scroll-x table-well" tabindex="0" aria-label="查询结果" :style="{maxHeight: (max||360)+'px'}"><table class="mini"><thead><tr><th v-for="c in columns">{{c}}</th></tr></thead>
    <tbody><tr v-for="r in rows"><td v-for="v in r">{{v === null ? 'NULL' : v}}</td></tr></tbody></table></div>`
};

// ------------------------------------------------------------ 管理总览
const Home = {
  template: `<div class="workbench">
    <div class="page-head"><h1>管理总览</h1></div>
    <div class="work-grid">
      <section class="card work-panel" aria-labelledby="resources-title">
        <header class="panel-head"><h2 id="resources-title">管理入口</h2></header>
        <div class="resource-list">
          <a class="btn entry" v-for="entry in entries" :key="entry.page" :href="'#'+entry.page">
            <span class="resource-icon" v-html="icon(entry.icon)"></span>
            <span><b>{{entry.label}}</b><small>{{entry.description}}</small></span>
            <span class="ico" v-html="icon('arrow')"></span>
          </a>
        </div>
      </section>
      <section class="card work-panel" aria-labelledby="recent-title" :aria-busy="loading">
        <header class="panel-head"><div><h2 id="recent-title">最近报告</h2><p>{{admin ? '所有用户最近生成的报告' : '你最近生成的报告'}}</p></div></header>
        <div v-if="loading && !list.length" class="collection-state" role="status">正在加载报告…</div>
        <div v-else-if="loadError" class="collection-state" role="alert">报告加载失败。<button type="button" class="btn link" @click="load">重试</button></div>
        <div v-else-if="!list.length" class="recent-empty">
          <span class="empty-icon" v-html="icon('tpl')"></span>
          <div><b>还没有报告</b><p>生成的报告会出现在这里，可随时查看或下载。</p></div>
        </div>
        <ul v-else class="recent-list">
          <li v-for="r in list.slice(0,5)" :key="r.id">
            <a class="btn entry" :href="reportPath(r)" target="_blank" rel="noopener">
              <span class="resource-icon" v-html="icon('rep')"></span>
              <span class="recent-info"><b>{{r.name}}</b><small v-if="r.template_name && r.template_name !== r.name">{{r.template_name}}</small></span>
              <time :datetime="r.created_at">{{fmtTime(r.created_at)}}</time>
              <span class="ico" v-html="icon('arrow')"></span>
            </a>
          </li>
        </ul>
      </section>
    </div>
  </div>`,
  setup() {
    const reports = useCollection('/api/reports');
    // 首页与侧栏使用同样的管理权限，避免员工看到无法打开的管理入口。
    const admin = computed(isAdmin);
    const entries = computed(() => [
      { page: 'datasources', label: '数据源', icon: 'ds', description: '维护连接和取数配置', visible: admin.value },
      { page: 'datasets', label: '业务场景', icon: 'set', description: admin.value ? '维护业务描述、分析思路和可用数据' : '查看已授权的业务范围、分析思路和数据' },
      { page: 'templates', label: '报告模板', icon: 'tpl', description: '查看模板用途和报告章节' },
      { page: 'reports', label: '报告库', icon: 'rep', description: '查阅和下载已有报告' },
      { page: 'styles', label: '报告样式', icon: 'sty', description: '预览报告版式和设置样式规则' },
      { page: 'perms', label: '员工与授权', icon: 'perm', description: '维护员工和数据访问权限', visible: admin.value },
      { page: 'mine', label: '我的权限与接入', icon: 'key', description: '查看个人权限和接入指引', visible: canVisitMine() }
    ].filter(entry => entry.visible !== false));
    watch(reportRevision, reports.load);
    return { ...reports, admin, entries, fmtTime, icon, reportPath };
  }
};

async function loadMeta() { state.meta = await api('/api/meta'); }

// ------------------------------------------------------------ 数据源
function emptyDs() {
  return { id: null, name: '', type: 'mysql', description: '', enabled: true, secret: '', has_secret: false,
    host: '', port: '', database: '', username: '', service_name: '', sid: '', schema: '', base_url: '', records_path: '',
    auth_header: 'Authorization', auth_scheme: 'Bearer', headersTxt: '{}', endpointsTxt: '{}', extraTxt: '{}', scenarioTablesTxt: '[]' };
}
const DB_KEYS = ['host', 'port', 'database', 'username', 'service_name', 'sid'];
const API_KEYS = ['base_url', 'records_path', 'auth_header', 'auth_scheme'];
function dsToForm(d) {
  const f = emptyDs(), cfg = { ...(d.config || {}) };
  Object.assign(f, { id: d.id, name: d.name, type: d.type, description: d.description, enabled: d.enabled, has_secret: d.has_secret });
  [...DB_KEYS, ...API_KEYS].forEach(k => { if (cfg[k] != null) { f[k] = cfg[k]; delete cfg[k]; } });
  // 已保存在高级配置中的 Oracle Schema 自动回填到独立输入框。
  if (d.type === 'oracle') { f.schema = cfg.schema || ''; delete cfg.schema; }
  f.headersTxt = pretty(cfg.headers); delete cfg.headers;
  f.endpointsTxt = pretty(cfg.endpoints); delete cfg.endpoints;
  // 场景选表授权单独呈现，连接参数和密钥仍由数据源编辑器维护。
  f.scenarioTablesTxt = pretty(cfg.scenario_tables || []); delete cfg.scenario_tables;
  f.extraTxt = pretty(cfg);
  return f;
}
function formToDs(f) {
  const cfg = jsonOrThrow(f.extraTxt, '高级配置');
  try {
    const tables = JSON.parse(f.scenarioTablesTxt.trim() || '[]');
    if (!Array.isArray(tables)) throw new Error();
    cfg.scenario_tables = tables;
  } catch (e) {
    throw new Error('可建场景的表须填写 JSON 数组。');
  }
  if (f.type === 'api') {
    API_KEYS.forEach(k => { if (f[k] !== '' && f[k] != null) cfg[k] = f[k]; });
    cfg.headers = jsonOrThrow(f.headersTxt, '请求头');
    cfg.endpoints = jsonOrThrow(f.endpointsTxt, '接口定义');
  } else {
    DB_KEYS.forEach(k => { if (f[k] !== '' && f[k] != null) cfg[k] = k === 'port' ? Number(f[k]) : f[k]; });
    // 专用字段优先，留空时仍允许通过旧的高级配置方式填写。
    if (f.type === 'oracle' && f.schema) cfg.schema = f.schema;
  }
  const body = { name: f.name, type: f.type, description: f.description, enabled: f.enabled, config: cfg };
  if (f.secret) body.secret = f.secret;
  return body;
}
const DsBrowser = {
  components: { Drawer, DataTable }, props: ['ds'], emits: ['close'],
  template: `<Drawer :title="'浏览：' + ds.name" wide @close="$emit('close')">
    <div class="subtabs" role="group" aria-label="数据源浏览方式"><button type="button" class="btn toggle" :aria-pressed="tab==='tables'" @click="tab='tables'">表和视图</button><button type="button" class="btn toggle" :aria-pressed="tab==='sql'" @click="tab='sql'">SQL 查询</button></div>
    <div v-if="tab==='tables'" class="source-browser">
      <div><input class="in" v-model="kw" type="search" aria-label="搜索表或视图" placeholder="搜索表或视图">
        <div class="source-object-list" :aria-busy="loadingTables">
          <button v-for="t in shown" :key="t.name" type="button" class="btn source-object" :aria-pressed="cur===t.name" @click="open(t.name)">
            <span class="source-object-name"><span class="mono">{{t.name}}</span><span class="badge">{{OBJECT_LABEL[t.kind] || '表'}}</span></span>
            <span class="small muted" v-if="t.comment">{{t.comment}}</span>
          </button>
          <div v-if="tableError" class="msg err" role="alert">{{tableError}} <button type="button" class="btn link" @click="loadTables">重试</button></div>
          <div v-else-if="!shown.length" class="muted small" role="status">{{loadingTables ? '正在加载…' : kw ? '没有匹配的表或视图' : '无可浏览的表或视图'}}</div></div></div>
      <div v-if="desc">
        <div class="actions" style="margin-bottom:10px"><b class="mono">{{desc.table}}</b><span class="badge">行数 {{desc.row_count ?? '未知'}}</span><span class="small muted">{{desc.profile_basis}}</span></div>
        <h4 style="margin:6px 0">字段画像</h4>
        <div class="scroll-x table-well" tabindex="0" aria-label="字段画像" style="max-height:300px"><table class="mini"><thead><tr><th>字段</th><th>类型</th><th>注释</th><th>推断</th><th>空值率</th><th>去重</th><th>范围 / 高频值</th></tr></thead>
          <tbody><tr v-for="p in profileRows"><td class="mono">{{p.column}}</td><td class="small">{{p.type}}</td><td>{{p.comment}}</td><td><span class="badge">{{p.kind}}</span></td>
          <td>{{p.null_ratio==null?'':(p.null_ratio*100).toFixed(1)+'%'}}</td><td>{{p.distinct}}</td>
          <td class="small">{{p.min!=null ? p.min+' ~ '+p.max : ''}}{{p.top_values ? p.top_values.slice(0,5).map(x=>x.value).join('、') : ''}}</td></tr></tbody></table></div>
        <h4 style="margin:14px 0 6px">样本数据</h4><DataTable :columns="desc.sample_columns" :rows="desc.sample_rows" :max="260"/>
      </div>
      <div v-else-if="profileError" class="msg err" role="alert">{{profileError}} <button type="button" class="btn link" @click="open(cur)">重试</button></div>
      <div v-else class="empty" role="status">{{profileLoading ? '正在读取字段画像…' : '选择表或视图，查看字段和数据画像'}}</div>
    </div>
    <div v-else>
      <label class="field"><span>只读 SQL</span><textarea class="in code" v-model="sql" rows="8" placeholder="SELECT ..."></textarea></label>
      <div class="actions" style="margin:10px 0"><button type="button" class="btn primary" @click="query" :disabled="querying">{{querying ? '正在执行…' : '执行（只读）'}}</button>
        <span class="small muted" v-if="res">返回 {{res.row_count}} 行{{res.truncated?'（已截断）':''}} · 方言 {{res.dialect}}</span></div>
      <div v-if="queryError" class="msg err" role="alert">{{queryError}}</div>
      <DataTable v-if="res" :columns="res.columns" :rows="res.rows" :max="480"/>
    </div>
  </Drawer>`,
  setup(props) {
    const tab = ref('tables'), kw = ref(''), desc = ref(null), cur = ref('');
    const profileLoading = ref(false), profileError = ref('');
    const sql = ref(''), res = ref(null), querying = ref(false), queryError = ref('');
    const path = `/api/datasources/${props.ds.id}`;
    const { list: tables, loading: loadingTables, loadError: tableError, load: loadTables } = useCollection(path + '/tables');
    const shown = computed(() => tables.value.filter(t => matches(kw.value, t.name, t.comment, OBJECT_LABEL[t.kind])));
    const profileRows = computed(() => {
      if (!desc.value) return [];
      const types = Object.fromEntries((desc.value.columns || []).map(c => [c.name, c]));
      return desc.value.profile.map(p => ({ ...p, type: (types[p.column] || {}).type || '', comment: p.comment || (types[p.column] || {}).comment || '' }));
    });
    let profileRequest = 0;
    async function open(name) {
      const request = ++profileRequest;
      cur.value = name; desc.value = null;
      profileError.value = ''; profileLoading.value = true;
      try {
        const data = await api(path + `/describe?table=${encodeURIComponent(name)}&sample_rows=8`);
        // 即使切回同一对象，也只接受最后一次请求返回的画像和错误。
        if (request !== profileRequest) return;
        desc.value = data;
        if (!sql.value) sql.value = `SELECT * FROM ${name}`;
      } catch (e) { if (request === profileRequest) profileError.value = e.message; }
      finally { if (request === profileRequest) profileLoading.value = false; }
    }
    async function query() {
      querying.value = true; queryError.value = ''; res.value = null;
      try { res.value = await api(path + '/query', { method: 'POST', body: { sql: sql.value, limit: 500 } }); }
      catch (e) { queryError.value = e.message; }
      finally { querying.value = false; }
    }
    return { tab, kw, shown, desc, cur, open, loadingTables, tableError, loadTables, profileLoading, profileError,
      sql, res, query, querying, queryError, profileRows, OBJECT_LABEL };
  }
};
const Datasources = {
  components: { Drawer, DsBrowser },
  template: `<div>
    <div class="page-head"><div><h1>数据源</h1><p>{{admin ? '管理业务场景使用的数据连接，检查连接是否可用。' : '查看已授权业务场景使用的数据连接。'}}</p></div>
      <div class="actions"><button type="button" v-if="admin" class="btn primary" @click="edit()">新建数据源</button><span v-else class="badge">只读 · 仅显示你有权限的数据源</span></div></div>
    <div v-if="loading" class="card collection-state" role="status">正在加载数据源…</div>
    <div v-else-if="loadError" class="card collection-state" role="alert">数据源加载失败。<button type="button" class="btn link" @click="load">重试</button></div>
    <div v-else class="card scroll-x" tabindex="0" aria-label="数据源表格"><table class="tbl record-table" role="table"><thead><tr><th scope="col">名称</th><th scope="col">类型</th><th scope="col" v-if="admin">连接</th><th scope="col">描述</th><th scope="col">状态</th><th scope="col" v-if="admin">操作</th></tr></thead>
      <tbody><tr v-for="d in list"><td><b>{{d.name}}</b></td><td data-label="类型"><span class="badge blue">{{DS_LABEL[d.type]}}</span></td>
        <td v-if="admin" class="mono small record-wide" data-label="连接">{{addr(d)}}</td><td class="record-wide" data-label="描述"><div class="clip">{{d.description}}</div></td>
        <td data-label="状态"><span class="dot" :class="{on:d.enabled}"></span>{{d.enabled?'启用':'停用'}}</td>
        <td v-if="admin" class="record-actions"><div class="links"><button type="button" class="btn link" @click="test(d)" :disabled="testing.has(d.id)">{{testing.has(d.id) ? '测试中…' : '测试'}}</button><button type="button" class="btn link" @click="browse=d">浏览</button>
          <button type="button" class="btn link" @click="edit(d)">编辑</button><button type="button" class="btn link danger" @click="del(d)">删除</button></div></td></tr>
        <tr v-if="!list.length"><td colspan="6" class="empty">{{admin ? '暂无数据源' : '暂无你有权限的数据源，如需开通请联系管理员'}}</td></tr></tbody></table></div>
    <Drawer v-if="form" :title="form.id ? '编辑数据源' : '新建数据源'" :busy="!!busy" @close="form=null">
      <fieldset class="plain-fs" :disabled="busy">
      <div class="row2"><div class="field"><label for="ds-name">名称</label><input id="ds-name" class="in" v-model="form.name"></div>
        <div class="field"><label for="ds-type">类型</label><select id="ds-type" class="in" v-model="form.type"><option v-for="(l,k) in DS_LABEL" :value="k">{{l}}</option></select></div></div>
      <template v-if="form.type!=='api'">
        <div class="row3"><div class="field"><label for="ds-host">主机</label><input id="ds-host" class="in" v-model="form.host" placeholder="10.0.0.12"></div>
          <div class="field"><label for="ds-port">端口</label><input id="ds-port" class="in" v-model="form.port" :placeholder="String(DEFAULT_PORT[form.type])"></div>
          <div class="field" v-if="form.type!=='oracle'"><label for="ds-db">数据库</label><input id="ds-db" class="in" v-model="form.database"></div>
          <div class="field" v-else><label for="ds-service">服务名 <small>或 SID</small></label><input id="ds-service" class="in" v-model="form.service_name" placeholder="ORCLPDB1"></div></div>
        <div class="row2" v-if="form.type==='oracle'">
          <div class="field"><label for="ds-sid">SID <small>未填服务名时使用</small></label><input id="ds-sid" class="in" v-model="form.sid"></div>
          <div class="field"><label for="oracle-schema">Schema <small>选填</small></label>
            <input id="oracle-schema" class="in" v-model.trim="form.schema" aria-describedby="oracle-schema-hint">
            <div id="oracle-schema-hint" class="hint">留空时使用登录用户的默认 Schema。</div></div>
        </div>
        <div class="row2"><div class="field"><label for="ds-user">用户名</label><input id="ds-user" class="in" v-model="form.username"></div>
          <div class="field"><label for="ds-password">密码</label><input id="ds-password" class="in" type="password" v-model="form.secret" :placeholder="form.has_secret?'已设置，留空则不修改':''" autocomplete="new-password"></div></div>
        <div class="field"><label for="datasource-extra">高级配置 <small>选填，JSON</small></label>
          <textarea id="datasource-extra" class="in code" v-model="form.extraTxt" rows="3"></textarea></div>
      </template>
      <template v-else>
        <div class="field"><label for="ds-base-url">Base URL</label><input id="ds-base-url" class="in" v-model="form.base_url" placeholder="https://api.example.com/v1"></div>
        <div class="row3"><div class="field"><label for="ds-record-path">记录路径 <small>如 data.items</small></label><input id="ds-record-path" class="in" v-model="form.records_path"></div>
          <div class="field"><label for="ds-auth-header">鉴权请求头</label><input id="ds-auth-header" class="in" v-model="form.auth_header"></div>
          <div class="field"><label for="ds-auth-scheme">前缀</label><input id="ds-auth-scheme" class="in" v-model="form.auth_scheme" placeholder="Bearer"></div></div>
        <div class="field"><label for="ds-api-key">令牌 / API Key</label><input id="ds-api-key" class="in" type="password" v-model="form.secret" :placeholder="form.has_secret?'已设置，留空则不修改':'可选'" autocomplete="new-password"></div>
        <div class="field"><label for="ds-endpoints">接口定义 endpoints <small>逻辑表名 → {path, method, params, body, records_path, description}</small></label>
          <textarea id="ds-endpoints" class="in code" v-model="form.endpointsTxt" rows="7"></textarea>
          <div class="hint">数据集中的“数据表名”填写逻辑表名；查询时接口数据载入 DuckDB，以 DuckDB SQL 方言查询。</div></div>
        <div class="row2"><div class="field"><label for="ds-headers">公共请求头 <small>JSON</small></label><textarea id="ds-headers" class="in code" v-model="form.headersTxt" rows="3"></textarea></div>
          <div class="field"><label for="ds-other">其他配置 <small>timeout / cache_seconds / method</small></label><textarea id="ds-other" class="in code" v-model="form.extraTxt" rows="3"></textarea></div></div>
      </template>
      <details class="field">
        <summary class="btn disclosure">开放给员工构建业务场景</summary>
        <label for="datasource-scene-tables">可引用的表和授权范围</label>
        <p class="field-help" id="datasource-scene-tables-help">为表或视图指定可引用的工号或角色。固定行范围会应用到员工基于此表创建的场景。</p>
        <textarea id="datasource-scene-tables" class="in code" v-model="form.scenarioTablesTxt" rows="5" spellcheck="false"
          aria-describedby="datasource-scene-tables-help" placeholder='[{"table_name":"public.sales","roles":["user"],"row_filters":[]}]'></textarea>
      </details>
      <div class="field"><label for="ds-description">描述</label><textarea id="ds-description" class="in" v-model="form.description" rows="2"></textarea></div>
      <label class="check"><input type="checkbox" v-model="form.enabled"> 启用</label>
      </fieldset>
      <div class="msg" :class="testRes.ok?'ok':'err'" :role="testRes.ok?'status':'alert'" v-if="testRes" style="margin-top:12px">{{testRes.text}}</div>
      <template #foot><button type="button" class="btn" @click="testForm" :disabled="busy">{{busy==='test' ? '正在测试…' : '测试连接'}}</button><span class="spacer"></span>
        <button type="button" class="btn" @click="form=null" :disabled="busy">取消</button><button type="button" class="btn primary" @click="save" :disabled="busy">{{busy==='save' ? '正在保存…' : '保存'}}</button></template>
    </Drawer>
    <DsBrowser v-if="browse" :ds="browse" @close="browse=null"/>
  </div>`,
  setup() {
    const sources = useCollection('/api/datasources');
    const { load } = sources;
    // 保存和测试复用表单锁，空闲用 null，确保原生 disabled 布尔属性会被移除。
    const form = ref(null), browse = ref(null), busy = ref(null), testRes = ref(null), testing = reactive(new Set());
    const admin = computed(isAdmin);
    const addr = d => !d.config ? '' : d.type === 'api' ? (d.config.base_url || '') : `${d.config.host || ''}:${d.config.port || DEFAULT_PORT[d.type]}/${d.config.database || d.config.service_name || d.config.sid || ''}`;
    function edit(d) { testRes.value = null; form.value = d ? dsToForm(d) : emptyDs(); }
    async function save() {
      busy.value = 'save'; testRes.value = null;
      try {
        const body = formToDs(form.value);
        const id = form.value.id;
        await api(id ? `/api/datasources/${id}` : '/api/datasources', { method: id ? 'PUT' : 'POST', body });
        toast('已保存', 'ok');
        form.value = null; load();
      } catch (e) { testRes.value = { ok: false, text: e.message }; } finally { busy.value = null; }
    }
    async function testForm() {
      busy.value = 'test'; testRes.value = null;
      try {
        const b = formToDs(form.value); b.id = form.value.id;
        const r = await api('/api/datasources/test', { method: 'POST', body: b });
        testRes.value = { ok: true, text: `连接成功 · ${r.version} · ${r.latency_ms} ms` };
      } catch (e) { testRes.value = { ok: false, text: e.message }; } finally { busy.value = null; }
    }
    async function test(d) {
      // 各行独立反馈，测试期间锁住同一个数据源的重复点击。
      testing.add(d.id);
      try {
        const r = await api(`/api/datasources/${d.id}/test`, { method: 'POST' });
        toast(`「${d.name}」连接成功：${r.version}（${r.latency_ms} ms）`, 'ok');
      } catch (e) { toast(e.message, 'err'); }
      finally { testing.delete(d.id); }
    }
    async function del(d) {
      if (!await askConfirm({ title: '删除数据源', message: `确认删除数据源「${d.name}」？`, confirmText: '删除' })) return;
      await run(() => api(`/api/datasources/${d.id}`, { method: 'DELETE' }), '已删除'); load();
    }
    return { ...sources, admin, form, browse, busy, testRes, testing, addr, edit, save, testForm, test, del, DS_LABEL, DEFAULT_PORT };
  }
};

// ------------------------------------------------------------ 数据集
let sceneTableKey = 0;
const METRIC_OPS = [
  { value: 'sum', label: '求和（sum）' },
  { value: 'avg', label: '均值（avg）' },
  { value: 'min', label: '最小值（min）' },
  { value: 'max', label: '最大值（max）' },
  { value: 'count_distinct', label: '去重计数（count_distinct）' },
  { value: 'ratio_of_sums', label: '总和比率（ratio_of_sums）' },
  { value: 'difference_of_sums', label: '总和差额（difference_of_sums）' },
  { value: 'growth_of_sums', label: '增长率（growth_of_sums）' },
];
const BINARY_METRIC_OPS = new Set(['ratio_of_sums', 'difference_of_sums', 'growth_of_sums']);
const RATIO_METRIC_OPS = new Set(['ratio_of_sums', 'growth_of_sums']);

function metricDraftFromEntry(name = '', m = {}) {
  const extra = {};
  // 高级条件可以在 JSON 模式维护；切回表单时保留它们。
  for (const key of ['when', 'denominator_when', 'when_parameters', 'denominator_parameters']) {
    if (m[key] && Object.keys(m[key]).length) extra[key] = m[key];
  }
  return {
    id: ++sceneTableKey,
    name,
    table: m.table || '',
    op: m.op || 'sum',
    field: m.field || '',
    denominator: m.denominator || '',
    dimsTxt: (m.dimensions || []).join(', '),
    filterDimsTxt: Array.isArray(m.filter_dimensions) ? m.filter_dimensions.join(', ') : '',
    hasFilterDims: Array.isArray(m.filter_dimensions),
    scale: m.scale || 'number',
    description: m.description || '',
    extra,
  };
}
function metricsToDrafts(raw = {}) {
  return Object.entries(raw || {}).map(([name, m]) => metricDraftFromEntry(name, m || {}));
}
function draftsToMetrics(drafts = []) {
  const out = {};
  for (const row of drafts) {
    const name = (row.name || '').trim();
    if (!name) throw new Error('请填写每项官方指标的英文标识（如 revenue），或移除空白项。');
    if (!/^[a-z][a-z0-9_]{0,47}$/.test(name)) throw new Error(`指标标识「${name}」须以小写字母开头，仅含小写字母、数字和下划线。`);
    if (Object.hasOwn(out, name)) throw new Error(`官方指标标识重复：${name}`);
    const table = (row.table || '').trim();
    const field = (row.field || '').trim();
    if (!table || !field) throw new Error(`官方指标「${name}」须选择数据表并填写分子/主字段。`);
    const entry = {
      table,
      op: row.op || 'sum',
      field,
      dimensions: (row.dimsTxt || '').split(/[,，]/).map(s => s.trim()).filter(Boolean),
      scale: RATIO_METRIC_OPS.has(row.op) ? 'decimal' : (row.scale || 'number'),
      description: row.description || '',
    };
    if (BINARY_METRIC_OPS.has(entry.op)) {
      const den = (row.denominator || '').trim();
      if (!den) throw new Error(`官方指标「${name}」采用 ${entry.op} 公式，须填写分母字段。`);
      entry.denominator = den;
    }
    if (row.hasFilterDims || (row.filterDimsTxt || '').trim()) {
      entry.filter_dimensions = (row.filterDimsTxt || '').split(/[,，]/).map(s => s.trim()).filter(Boolean);
    }
    if (row.extra?.when) entry.when = row.extra.when;
    if (row.extra?.when_parameters) entry.when_parameters = row.extra.when_parameters;
    if (BINARY_METRIC_OPS.has(entry.op)) {
      if (row.extra?.denominator_when) entry.denominator_when = row.extra.denominator_when;
      if (row.extra?.denominator_parameters) entry.denominator_parameters = row.extra.denominator_parameters;
    }
    out[name] = entry;
  }
  return out;
}
function sceneTableDraft(table = {}) {
  return { table_name: '', table_description: '', ...table, key: ++sceneTableKey, expanded: false,
    columns: null, loadingColumns: false, columnError: '', row_filters: (table.row_filters || []).map(rule => ({
      column: rule.column, values: [...rule.values]
    })) };
}
function sceneFormSnapshot(f) {
  if (!f) return '';
  let metrics = f.metricsTxt || '{}';
  if (f.metricsMode === 'visual') {
    metrics = JSON.stringify((f.metricDrafts || []).map(m => ({
      name: m.name, table: m.table, op: m.op, field: m.field, denominator: m.denominator,
      dimsTxt: m.dimsTxt, filterDimsTxt: m.filterDimsTxt, scale: m.scale, description: m.description, extra: m.extra,
    })));
  }
  return JSON.stringify({
    business_scenario: f.business_scenario || '',
    datasource_id: f.datasource_id ?? null,
    business_description: f.business_description || '',
    analysis_approach: f.analysis_approach || '',
    tagsTxt: f.tagsTxt || '',
    metrics,
    enabled: !!f.enabled,
    tables: (f.tables || []).map(t => ({
      table_name: t.table_name || '',
      table_description: t.table_description || '',
      row_filters: (t.row_filters || []).map(r => ({ column: r.column || '', values: [...(r.values || [])] }))
    }))
  });
}
const filterValueLabel = value => value === null ? 'NULL（空值）' : value === '' ? '空字符串' : String(value);

const SceneFilterValues = {
  props: { source: Number, table: String, column: String, modelValue: Array },
  emits: ['update:modelValue'],
  template: `<div class="scene-values">
    <div class="scene-values-label">保留值 <span class="muted small">已选 {{modelValue.length}} 项</span></div>
    <ul v-if="modelValue.length" class="scene-selected-values" aria-label="已选保留值">
      <li v-for="value in modelValue" :key="key(value)">
        <button type="button" class="btn sm scene-value-chip" :aria-label="'取消选择 ' + label(value)" @click="toggle(value)">
          <span>{{label(value)}}</span><span class="ico" v-html="icon('close')"></span>
        </button>
      </li>
    </ul>
    <p v-if="!ready" class="field-help">先选择字段，再勾选要保留的值。</p>
    <template v-else>
      <input type="search" class="in" v-model="search" maxlength="120" :aria-label="'搜索 ' + column + ' 的候选值'" placeholder="搜索候选值">
      <div v-if="options.length" class="scene-value-options" role="group" :aria-label="column + ' 的候选值'" :aria-busy="loading">
        <label v-for="value in options" :key="key(value)" class="check">
          <input type="checkbox" :checked="selected(value)" :disabled="modelValue.length >= 100 && !selected(value)" @change="toggle(value)">
          <span>{{label(value)}}</span>
        </label>
      </div>
      <p v-if="modelValue.length >= 100" class="field-help" role="status">每个字段最多保留 100 个值。</p>
      <p v-if="loading" class="field-help" role="status">正在读取候选值…</p>
      <div v-else-if="error" class="msg err" role="alert">{{error}} <button type="button" class="btn link scene-values-action" @click="load(failedOffset)">重试</button></div>
      <button v-else-if="nextOffset !== null" type="button" class="btn link scene-values-action" @click="load(nextOffset)">加载更多候选值</button>
      <p v-else-if="!options.length" class="field-help" role="status">{{search.trim() ? '没有匹配的候选值。' : '当前字段没有可选值。'}}</p>
    </template>
  </div>`,
  setup(props, { emit }) {
    const options = ref([]), search = ref(''), loading = ref(false), nextOffset = ref(null), error = ref(''), failedOffset = ref(0);
    const ready = computed(() => !!(props.source && props.table && props.column));
    let controller, timer;
    const key = value => JSON.stringify(value);
    // 旧表单以文本存数字和布尔值；单列类型固定，相同字面值应显示为已选，且空值仍独立。
    const sameValue = (a, b) => a === b || (a !== null && b !== null && String(a) === String(b));
    const selected = value => props.modelValue.some(v => sameValue(v, value));
    function toggle(value) {
      if (selected(value)) emit('update:modelValue', props.modelValue.filter(v => !sameValue(v, value)));
      else if (props.modelValue.length < 100) emit('update:modelValue', [...props.modelValue, value]);
    }
    function cancel() { clearTimeout(timer); controller?.abort(); }
    async function load(offset = 0) {
      cancel();
      const request = controller = new AbortController();
      loading.value = true; error.value = ''; failedOffset.value = offset;
      const params = new URLSearchParams({ table: props.table, column: props.column, q: search.value.trim(), offset });
      try {
        const result = await api(`/api/datasources/${props.source}/values?${params}`, { signal: request.signal });
        if (request.signal.aborted) return;
        options.value = [...new Set([...(offset ? options.value : []), ...result.values])];
        nextOffset.value = result.next_offset;
      } catch (e) { if (!request.signal.aborted) error.value = e.message; }
      finally { if (!request.signal.aborted) loading.value = false; }
    }
    // 搜索换页只更新候选列表，已选值仍由父表单保存；取消旧请求避免串用字段或搜索结果。
    watch(search, () => {
      cancel();
      options.value = []; nextOffset.value = null; error.value = ''; loading.value = ready.value;
      if (ready.value) timer = setTimeout(() => load(), 350);
    });
    onMounted(() => { if (ready.value) load(); });
    onBeforeUnmount(cancel);
    return { options, search, ready, loading, nextOffset, error, failedOffset, load, toggle, selected, key, label: filterValueLabel, icon };
  }
};

const Datasets = {
  components: { Drawer, SceneFilterValues },
  template: `<div>
    <div class="page-head"><div><h1>业务场景</h1><p>按业务主题找数据，查看每个场景可用于分析的数据表。</p></div>
      <div class="actions"><button type="button" v-if="admin" class="btn" @click="reindex">重建检索索引</button><button type="button" class="btn primary" @click="edit()">新建业务场景</button></div></div>
    <section class="card list-panel" aria-label="业务场景列表" :aria-busy="loading">
      <div class="list-tools">
        <label class="search-field"><span class="ico" v-html="icon('search')"></span><input type="search" v-model="kw" placeholder="搜索业务场景、描述或标签" aria-label="搜索业务场景"></label>
        <span class="list-count" v-if="!loading && !loadError" aria-live="polite">{{shown.length}} 个场景</span>
      </div>
      <div v-if="loading" class="collection-state" role="status">正在加载业务场景…</div>
      <div v-else-if="loadError" class="collection-state" role="alert">业务场景加载失败。<button type="button" class="btn link" @click="load">重试</button></div>
      <div v-else-if="!shown.length" class="collection-state">
        <span class="empty-icon" v-html="icon('set')"></span><h2>{{kw.trim() ? '未找到匹配的场景' : '暂无可用的业务场景'}}</h2>
        <p>{{kw.trim() ? '可以改用业务主题或常见指标搜索。' : '可新建业务场景，选择授权的数据源并填写分析思路。'}}</p>
        <button type="button" v-if="kw.trim()" class="btn" @click="kw=''">清除搜索</button>
        <button type="button" v-else class="btn" @click="edit()">新建业务场景</button>
      </div>
      <div v-else class="scroll-x" tabindex="0" aria-label="业务场景表格">
        <table class="tbl record-table scene-table" role="table"><thead><tr><th scope="col">业务场景</th><th scope="col">分析范围</th><th scope="col">数据表</th><th v-if="admin" scope="col">状态</th><th scope="col">操作</th></tr></thead>
          <tbody><tr v-for="d in shown" :key="d.id">
            <td><b>{{d.business_scenario}}</b><div class="small muted">{{(d.tags||[]).join(' · ')}}</div></td>
            <td class="scene-description"><div class="clip">{{d.business_description || '—'}}</div></td><td data-label="数据表">{{d.table_count}} 张</td>
            <td v-if="admin" data-label="状态"><span class="badge" :class="!d.enabled ? '' : d.vectorized ? 'green' : 'amber'">{{!d.enabled ? '停用' : d.vectorized ? '可用' : '索引待更新'}}</span></td>
            <td class="record-actions"><div class="links"><button type="button" class="btn link" @click="view(d)">查看场景</button><template v-if="admin || d.read_only === false"><button type="button" class="btn link" @click="edit(d)">编辑</button><button type="button" class="btn link danger" @click="del(d)">删除</button></template></div></td>
          </tr></tbody>
        </table>
      </div>
    </section>
    <details class="card card-pad discovery">
      <summary class="btn disclosure">按分析需求匹配业务场景</summary>
      <p class="muted">描述要回答的业务问题，查找可能用到的数据。</p>
      <form class="query-form" @submit.prevent="search">
        <input class="in" v-model="q" aria-label="分析需求" placeholder="例如：分析各渠道的销售增长与毛利变化">
        <button class="btn primary" type="submit" :disabled="searching || !q.trim()">{{searching ? '正在匹配…' : '匹配场景'}}</button>
      </form>
      <div v-if="searchError" class="msg err" role="alert">{{searchError}}</div>
      <div v-if="sr" class="match-results" aria-live="polite">
        <div class="msg warn" v-for="w in sr.warnings">{{w}}</div>
        <div v-for="h in sr.hits" :key="h.dataset.id" class="hit"><h4>{{h.dataset.business_scenario}}</h4>
          <p>{{h.dataset.business_description}}</p><button type="button" class="btn link" @click="view(h.dataset)">查看场景</button>
          <details v-if="admin"><summary class="btn disclosure small muted">查看匹配依据</summary>
            <p class="score">关键词 {{(h.matched_terms||[]).join('、') || '—'}} · 综合分 {{h.rrf}} · 向量 {{h.vector_score ?? '—'}} · 关键词分 {{h.keyword_score ?? '—'}}</p>
          </details>
        </div>
        <p v-if="!sr.hits.length" class="muted">暂无匹配的场景。试试换一个业务主题或缩短描述。</p>
      </div>
    </details>
    <p class="small muted legacy-link" v-if="!admin"><a class="btn link" href="#datasources">查看这些场景使用的数据源</a></p>
    <Drawer v-if="detail" :title="detail.business_scenario" @close="detail=null">
      <dl class="kv"><dt>业务描述</dt><dd class="business-copy">{{detail.business_description}}</dd>
        <dt>分析思路</dt><dd class="business-copy">{{detail.analysis_approach || '暂未填写'}}</dd>
        <dt>标签</dt><dd>{{(detail.tags||[]).join('、') || '-'}}</dd>
        <dt>数据源</dt><dd>{{detail.datasource_name}}（{{DS_LABEL[detail.datasource_type]}}）</dd></dl>
      <h4 style="margin:16px 0 8px">表和视图 <small class="muted" v-if="!admin">（仅显示你有权限的对象）</small></h4>
      <div class="scroll-x table-well" tabindex="0" aria-label="场景表和视图"><table class="tbl"><thead><tr><th scope="col">名称</th><th scope="col">描述</th><th scope="col">取值范围</th></tr></thead>
        <tbody><tr v-for="t in detail.tables" :key="t.id"><td class="mono">{{t.table_name}}</td><td>{{t.table_description}}</td><td>
          <div v-for="r in t.row_filters" :key="r.column" class="scene-filter-summary"><b>{{r.column}}</b>：{{r.values.map(filterValueLabel).join('、')}}</div>
          <div v-for="r in t.source_row_filters" :key="'source-' + r.column" class="scene-filter-summary"><b>数据源 · {{r.column}}</b>：{{r.values.map(filterValueLabel).join('、')}}</div>
          <span v-if="!t.row_filters?.length && !t.source_row_filters?.length" class="muted">不限制</span></td></tr>
        <tr v-if="!detail.tables.length"><td colspan="3" class="empty">无</td></tr></tbody></table></div>
    </Drawer>
    <Drawer v-if="form" :title="form.id ? '编辑业务场景' : '新建业务场景'" wide :busy="busy" :backdrop-close="false" @close="closeForm">
      <div class="row2"><div class="field"><label for="scene-name">业务场景</label><input id="scene-name" class="in" v-model="form.business_scenario" placeholder="如：零售销售经营分析"></div>
        <div class="field"><label for="scene-source">数据源</label>
          <select id="scene-source" class="in" v-model="form.datasource_id" @change="changeSource">
            <option :value="null" disabled>请选择</option>
            <option v-if="form.datasource_id && !sources.some(s => s.id === form.datasource_id)" :value="form.datasource_id" disabled>原数据源当前不可引用</option>
            <option v-for="s in sources" :key="s.id" :value="s.id">{{s.name}}（{{DS_LABEL[s.type]}}）</option>
          </select>
          <p v-if="!sources.length" class="field-help">暂无可引用的数据源。请联系管理员配置数据源及可引用的表。</p></div></div>
      <div class="field"><label for="scene-description">业务描述 <small>说明数据范围、指标及适用的分析问题</small></label><textarea id="scene-description" class="in" v-model="form.business_description" rows="4"></textarea></div>
      <div class="field"><label for="scene-approach">分析思路 <small>选填</small></label>
        <p class="field-help" id="scene-approach-help">说明先看什么、怎么比较、需要验证哪些假设。也可补充报告应回答的问题、适合的图表和需要切换的筛选条件。</p>
        <textarea id="scene-approach" class="in" v-model="form.analysis_approach" rows="4" aria-describedby="scene-approach-help" placeholder="例如：先比较各渠道的销售增速，再找出毛利下降的主要品类，核对促销前后的变化。"></textarea></div>
      <div class="field"><label for="scene-tags">标签 <small>用逗号分隔，帮助同事查找场景</small></label><input id="scene-tags" class="in" v-model="form.tagsTxt"></div>
      <div class="field">
        <div class="scene-metrics-head">
          <label>官方指标 <small>登记聚合公式和允许分组维度（选填）</small></label>
          <div class="seg" role="group" aria-label="官方指标编辑模式">
            <button type="button" class="btn toggle" :aria-pressed="form.metricsMode === 'visual'" @click="switchMetricsMode('visual')">可视化编辑</button>
            <button type="button" class="btn toggle" :aria-pressed="form.metricsMode === 'json'" @click="switchMetricsMode('json')">JSON 源码</button>
          </div>
        </div>
        <div v-if="form.metricsError" class="msg err" role="alert">{{form.metricsError}}</div>
        <div v-if="form.metricsMode === 'visual'" class="scene-metrics-panel">
          <p class="field-help">供模型在明细表上做受控求和、比率和增长率计算，所有聚合均先应用数据权限与行过滤。</p>
          <div v-for="(m, idx) in form.metricDrafts" :key="m.id" class="scene-metric-row">
            <div class="scene-metric-grid">
              <label>指标标识 <input class="in mono" v-model.trim="m.name" placeholder="如 attainment"></label>
              <label>所属表
                <select class="in mono" v-model="m.table">
                  <option value="" disabled>选择表</option>
                  <option v-if="m.table && !form.tables.some(t => t.table_name === m.table)" :value="m.table">{{m.table}}</option>
                  <option v-for="t in form.tables" :key="t.key" :value="t.table_name">{{t.table_name}}</option>
                </select>
              </label>
              <label>公式类型
                <select class="in" v-model="m.op" @change="onMetricOpChange(m)">
                  <option v-for="op in METRIC_OPS" :key="op.value" :value="op.value">{{op.label}}</option>
                </select>
              </label>
              <label>{{BINARY_METRIC_OPS.has(m.op) ? '分子字段' : '度量字段'}} <input class="in mono" v-model.trim="m.field" placeholder="如 actual"></label>
              <label v-if="BINARY_METRIC_OPS.has(m.op)">分母字段 <input class="in mono" v-model.trim="m.denominator" placeholder="如 target"></label>
              <label>比例尺度
                <select class="in" v-model="m.scale" :disabled="RATIO_METRIC_OPS.has(m.op)">
                  <option value="number">数值（number）</option>
                  <option value="decimal">小数比例（decimal）</option>
                  <option value="percent">百分数（percent）</option>
                </select>
              </label>
              <label>允许分组维度 <input class="in mono" v-model="m.dimsTxt" placeholder="逗号分隔，如 region, month"></label>
              <label>说明 <input class="in" v-model="m.description" placeholder="如 实际/目标完成率"></label>
            </div>
            <div class="scene-metric-foot">
              <span v-if="m.extra?.when || m.extra?.denominator_when" class="badge blue">含条件聚合（when）</span>
              <button class="btn link danger" type="button" @click="form.metricDrafts.splice(idx, 1)">移除指标</button>
            </div>
          </div>
          <div class="actions"><button class="btn sm" type="button" @click="addMetric" :disabled="form.metricDrafts.length >= 100">添加官方指标</button></div>
        </div>
        <div v-else>
          <textarea id="scene-metrics" class="in mono" aria-label="官方指标 JSON 源码" v-model="form.metricsTxt" rows="6" spellcheck="false"></textarea>
          <div style="margin-top:8px"><a class="btn link" :href="appPath('/api-guide')" target="_blank" rel="noopener">查看配置字段</a></div>
        </div>
      </div>
      <section class="field" aria-labelledby="scene-tables-label"><h3 id="scene-tables-label" class="scene-tables-label">表和视图 <small class="muted">{{form.tables.length}} 项</small></h3>
        <p class="field-help">按需设置各表的取值范围。场景取数时自动应用这些条件。</p>
        <div v-for="(t,i) in form.tables" :key="t.key" class="scene-table-entry">
          <div class="scene-table-fields">
            <label>名称 <input class="in mono" v-model.trim="t.table_name" :readonly="!admin" placeholder="schema.table" @change="changeTable(t)"></label>
            <label>描述 <input class="in" v-model="t.table_description"></label>
            <button class="btn link danger" type="button" @click="form.tables.splice(i,1)" :aria-label="'移除 ' + (t.table_name || '第 ' + (i+1) + ' 项')">移除</button>
          </div>
          <p v-if="t.source_row_filters?.length" class="field-help">
            数据源固定范围 · {{t.source_row_filters.map(r => r.column + '：' + r.values.map(filterValueLabel).join('、')).join('；')}}
          </p>
          <button class="btn link scene-filter-toggle" type="button" :aria-expanded="t.expanded" :aria-controls="'scene-filters-' + t.key"
            :disabled="!form.datasource_id || !t.table_name" @click="toggleFilters(t)">数据过滤 · {{t.row_filters.length ? t.row_filters.length + ' 个字段' : '无附加筛选'}}</button>
          <div v-show="t.expanded" :id="'scene-filters-' + t.key" class="scene-filter-editor">
            <p class="field-help">选择字段后，从表中的实际取值里勾选。不同字段的条件须同时满足。</p>
            <p v-if="t.loadingColumns" class="small muted" role="status">正在读取字段…</p>
            <div v-if="t.columnError" class="msg err" role="alert">{{t.columnError}} <button class="btn link" type="button" @click="loadColumns(t)">重试</button></div>
            <div v-for="(rule,j) in t.row_filters" :key="rule" class="scene-filter-rule">
              <div><label :for="'scene-column-' + t.key + '-' + j">字段</label>
                <select :id="'scene-column-' + t.key + '-' + j" class="in" :value="rule.column" @change="changeRuleColumn(rule, $event.target.value)" :disabled="!t.columns?.length">
                  <option value="" disabled>选择字段</option>
                  <option v-if="rule.column && !t.columns?.some(c => (c.source_name || c.name) === rule.column)" :value="rule.column">{{rule.column}}</option>
                  <option v-for="c in t.columns" :key="c.source_name || c.name" :value="c.source_name || c.name">{{c.source_name || c.name}}{{c.comment ? ' · ' + c.comment : ''}}</option>
                </select>
              </div>
              <SceneFilterValues v-if="t.expanded" :key="JSON.stringify([form.datasource_id, t.table_name, rule.column])"
                :source="form.datasource_id" :table="t.table_name" :column="rule.column" v-model="rule.values" />
              <button class="btn link danger" type="button" @click="t.row_filters.splice(j,1)" :aria-label="'移除筛选条件 ' + (rule.column || (j+1))">移除</button>
            </div>
            <button class="btn sm" type="button" @click="addRule(t)" :disabled="!t.columns?.some(c => !t.row_filters.some(r => r.column === (c.source_name || c.name))) || t.row_filters.length >= 20">添加字段条件</button>
          </div>
        </div>
        <div class="actions scene-table-actions"><button v-if="admin" class="btn sm" type="button" @click="addTable()">手动添加</button>
          <button class="btn sm" type="button" @click="pick" :disabled="!sources.some(s => s.id === form.datasource_id)">从数据源选择…</button></div>
        <div v-if="picker" class="scene-source-picker" :aria-busy="pickLoading">
          <input class="in" v-model="pickKw" type="search" aria-label="搜索数据源中的表或视图" placeholder="搜索表名、视图名或描述">
          <label v-for="t in pickShown" :key="t.name" class="check scene-pick-item"><input type="checkbox" :checked="has(t.name)" @change="togglePick(t)">
            <span class="mono">{{t.name}}</span><span class="badge">{{OBJECT_LABEL[t.kind] || '表'}}</span><span class="muted small">{{t.comment}}</span></label>
          <div v-if="pickError" class="msg err" role="alert">{{pickError}} <button class="btn link" type="button" @click="pick">重试</button></div>
          <div v-else-if="!pickShown.length" class="small muted" role="status">{{pickLoading ? '正在加载…' : pickKw ? '没有匹配的表或视图' : '数据源没有可选的表或视图'}}</div>
        </div>
      </section>
      <label class="check"><input type="checkbox" v-model="form.enabled"> 启用（停用后不参与检索）</label>
      <div v-if="saveError" class="msg err" role="alert">{{saveError}}</div>
      <template #foot><button class="btn" type="button" @click="closeForm" :disabled="busy">取消</button><button class="btn primary" type="button" @click="save" :disabled="busy">{{busy ? '正在保存…' : '保存业务场景'}}</button></template>
    </Drawer>
  </div>`,
  setup() {
    const scenes = useCollection('/api/datasets');
    const { load } = scenes;
    const sources = ref([]), form = ref(null), busy = ref(false), q = ref(''), sr = ref(null), picker = ref(null), pickKw = ref(''), detail = ref(null);
    const kw = ref(''), searching = ref(false), searchError = ref(''), saveError = ref('');
    const pickLoading = ref(false), pickError = ref('');
    const initialSnapshot = ref('');
    const formDirty = computed(() => !!form.value && sceneFormSnapshot(form.value) !== initialSnapshot.value);
    const shown = computed(() => scenes.list.value.filter(d => matches(kw.value, d.business_scenario, d.business_description, ...(d.tags || []))));
    const admin = computed(isAdmin);
    async function view(d) { detail.value = await run(() => api('/api/datasets/' + d.id)); }
    onMounted(async () => {
      const available = await run(() => api('/api/datasources'));
      sources.value = available.filter(source => admin.value || source.can_build_scene);
    });
    async function edit(d) {
      picker.value = null; pickKw.value = ''; saveError.value = '';
      if (d) {
        const full = await run(() => api(`/api/datasets/${d.id}`));
        form.value = { ...full, tagsTxt: (full.tags || []).join(', '), metricsTxt: pretty(full.semantic_metrics),
          metricDrafts: metricsToDrafts(full.semantic_metrics), metricsMode: 'visual', metricsError: '',
          tables: full.tables.map(sceneTableDraft) };
      } else {
        form.value = { id: null, business_scenario: '', business_description: '', analysis_approach: '',
          datasource_id: null, tagsTxt: '', metricsTxt: '{}', metricDrafts: [], metricsMode: 'visual',
          metricsError: '', tables: [], enabled: true };
      }
      initialSnapshot.value = sceneFormSnapshot(form.value);
    }
    function switchMetricsMode(mode) {
      const f = form.value;
      if (!f || f.metricsMode === mode) return;
      f.metricsError = '';
      try {
        if (mode === 'json') {
          f.metricsTxt = pretty(draftsToMetrics(f.metricDrafts));
        } else {
          f.metricDrafts = metricsToDrafts(jsonOrThrow(f.metricsTxt, '官方指标'));
        }
        f.metricsMode = mode;
      } catch (e) { f.metricsError = e.message; }
    }
    function onMetricOpChange(m) {
      if (RATIO_METRIC_OPS.has(m.op)) m.scale = 'decimal';
      if (!BINARY_METRIC_OPS.has(m.op)) m.denominator = '';
    }
    function addMetric() {
      const firstTable = form.value?.tables?.[0]?.table_name || '';
      form.value.metricDrafts.push(metricDraftFromEntry('', { table: firstTable, op: 'sum', scale: 'number' }));
    }
    async function closeForm() {
      if (busy.value) return;
      if (!formDirty.value || await askConfirm({ title: '放弃修改', message: '放弃未保存的业务场景修改？', confirmText: '放弃修改', cancelText: '继续编辑', danger: true })) form.value = null;
    }
    async function save() {
      busy.value = true; saveError.value = '';
      const f = form.value;
      try {
        const tables = f.tables.map(t => {
          if (!t.table_name.trim()) throw new Error('请为每个表或视图填写名称，或移除空白项。');
          const row_filters = t.row_filters.map(r => {
            if (!r.column || !r.values.length) throw new Error(`${t.table_name}：请选择字段并勾选保留值，或移除这条条件。`);
            return { column: r.column, values: r.values };
          });
          return { table_name: t.table_name, table_description: t.table_description, row_filters };
        });
        const semantic_metrics = f.metricsMode === 'visual' ? draftsToMetrics(f.metricDrafts) : jsonOrThrow(f.metricsTxt, '官方指标');
        const body = { business_scenario: f.business_scenario, business_description: f.business_description, analysis_approach: f.analysis_approach,
          datasource_id: f.datasource_id, tags: f.tagsTxt.split(/[,，]/).map(s => s.trim()).filter(Boolean), enabled: f.enabled, tables, semantic_metrics };
        await api(f.id ? `/api/datasets/${f.id}` : '/api/datasets', { method: f.id ? 'PUT' : 'POST', body });
        toast('已保存', 'ok'); form.value = null; load();
      } catch (e) { saveError.value = e.message; }
      finally { busy.value = false; }
    }
    async function del(d) {
      if (!await askConfirm({ title: '删除业务场景', message: `确认删除业务场景「${d.business_scenario}」？`, confirmText: '删除' })) return;
      await run(() => api(`/api/datasets/${d.id}`, { method: 'DELETE' }), '已删除'); load();
    }
    async function search() {
      const query = q.value.trim();
      if (!query || searching.value) return;
      searching.value = true;
      searchError.value = '';
      sr.value = null;
      try { sr.value = await api('/api/datasets/search?q=' + encodeURIComponent(query) + '&top_k=5'); }
      catch (e) { searchError.value = e.message; }
      finally { searching.value = false; }
    }
    async function reindex() { const r = await run(() => api('/api/datasets/reindex', { method: 'POST' })); toast(`已重建：数据集 ${r.datasets}，样式规则 ${r.style_rules}，未向量化 ${r.not_vectorized}`, 'ok'); load(); }
    async function pick() {
      const draft = form.value, source = draft.datasource_id;
      picker.value = []; pickError.value = ''; pickLoading.value = true;
      const current = () => draft === form.value && source === draft.datasource_id;
      try { const data = await api(`/api/datasources/${source}/tables`); if (current()) picker.value = data; }
      catch (e) { if (current()) pickError.value = e.message; }
      finally { if (current()) pickLoading.value = false; }
    }
    const pickShown = computed(() => (picker.value || []).filter(t => matches(pickKw.value, t.name, t.comment, OBJECT_LABEL[t.kind])));
    const has = n => form.value.tables.some(t => t.table_name === n);
    function addTable(table) { form.value.tables.push(sceneTableDraft(table)); }
    function togglePick(t) { const i = form.value.tables.findIndex(x => x.table_name === t.name); if (i >= 0) form.value.tables.splice(i, 1); else addTable({ table_name: t.name, table_description: t.comment || '', source_row_filters: t.source_row_filters || [] }); }
    function changeSource() {
      picker.value = null; pickKw.value = '';
      // 普通员工按选中的数据源重新挑表，避免带入上一个连接的目录或行范围。
      if (admin.value) form.value.tables.forEach(changeTable);
      else form.value.tables = [];
    }
    function changeTable(t) {
      t.columns = null; t.columnError = ''; t.loadingColumns = false;
      // 表或数据源换了，旧值不再代表当前字段的候选；保留条件位置，提示用户重新勾选。
      t.row_filters.forEach(r => { r.values = []; });
      if (t.expanded && t.table_name && form.value.datasource_id) loadColumns(t);
    }
    async function loadColumns(t) {
      const draft = form.value, source = draft.datasource_id, table = t.table_name;
      const current = () => draft === form.value && source === draft.datasource_id && table === t.table_name;
      t.loadingColumns = true; t.columnError = '';
      try {
        const fields = await api(`/api/datasources/${source}/columns?table=${encodeURIComponent(table)}`);
        if (current()) {
          t.columns = fields;
          if (!fields.length) t.columnError = '没有读到字段，请检查源表或视图。';
        }
      } catch (e) { if (current()) t.columnError = e.message; }
      finally { if (current()) t.loadingColumns = false; }
    }
    function toggleFilters(t) {
      t.expanded = !t.expanded;
      if (t.expanded && !t.columns && !t.loadingColumns) loadColumns(t);
    }
    function changeRuleColumn(rule, column) {
      // 显式改选字段时清除旧字段的值；重新打开编辑器仍保留已保存的原值。
      if (rule.column !== column) { rule.column = column; rule.values = []; }
    }
    function addRule(t) { t.row_filters.push({ column: '', values: [] }); }
    return { ...scenes, admin, detail, view, shown, kw, sources, form, busy, q, sr, searching, searchError, saveError,
      picker, pickKw, pickShown, pickLoading, pickError, edit, closeForm, save, del, search, reindex, pick, has, togglePick,
      addTable, changeSource, changeTable, loadColumns, toggleFilters, changeRuleColumn, addRule,
      switchMetricsMode, onMetricOpChange, addMetric, METRIC_OPS, BINARY_METRIC_OPS, RATIO_METRIC_OPS,
      filterValueLabel, DS_LABEL, OBJECT_LABEL, icon, appPath, hasMine: computed(canVisitMine) };
  }
};

// ------------------------------------------------------------ 报告模板
// 只在查看或编辑模板正文时加载本地 Markdown 编辑器。
const loadTemplateEditor = () => import(window.__SRG_TEMPLATE_EDITOR_URL__);

const TemplateDocument = {
  props: { keepOutline: Boolean, trackReading: Boolean },
  template: `<div ref="documentRoot" class="template-document"
    :class="{'has-outline': keepOutline || anchors.length, 'is-collapsed': !expanded}"
    :style="{'--template-sticky-height': stickyHeight + 'px'}">
    <aside v-if="keepOutline || anchors.length" class="template-outline" aria-label="文档目录">
      <div class="template-outline-panel">
        <button type="button" class="btn template-outline-toggle" :aria-expanded="expanded"
          :aria-controls="expanded ? 'template-outline-links' : null" @click="expanded=!expanded">
          <span class="ico" v-html="icon('indent')"></span>目录<span class="ico outline-chevron" v-html="icon('chevron')"></span>
        </button>
        <nav v-if="expanded" id="template-outline-links" aria-label="章节导航">
          <ol v-if="anchors.length"><li v-for="(anchor, index) in anchors" :key="index">
            <button type="button" class="template-outline-link" :style="{'--outline-level': anchor.level}"
              :aria-current="index === active ? 'location' : null" @click="jump(index)">{{anchor.label}}</button>
          </li></ol>
          <p v-else class="template-outline-empty">添加标题后，章节会显示在这里。</p>
        </nav>
      </div>
    </aside>
    <div class="template-document-main"><div ref="content" class="template-document-content"><slot/></div></div>
  </div>`,
  setup(props) {
    const documentRoot = ref(null), content = ref(null), anchors = ref([]), active = ref(0), stickyHeight = ref(0);
    const narrow = window.matchMedia('(max-width: 960px)');
    const expanded = ref(!narrow.matches);
    let nodes = [], headingsObserver, toolbarObserver, frame = 0, jumpScrollY = null;
    function refreshOutline() {
      // 从已排版的正文取标题，编辑与历史版本切换后会自动同步。目录使用位置区分同名章节。
      const headings = content.value.querySelectorAll(
        '.template-reading-title, :is(.template-reader-body, .ProseMirror) > :is(h1, h2, h3, h4, h5, h6)'
      );
      const previous = nodes[active.value];
      nodes = [...headings].filter(node => node.textContent.trim());
      const base = nodes.reduce((level, node) => Math.min(level, Number(node.tagName[1])), 6);
      const next = nodes.map(node => ({ label: node.textContent.trim(), level: Math.min(3, Number(node.tagName[1]) - base) }));
      if (next.length !== anchors.value.length || next.some((item, i) => item.label !== anchors.value[i].label || item.level !== anchors.value[i].level)) {
        anchors.value = next;
      }
      active.value = Math.max(0, nodes.indexOf(previous));
    }
    async function jump(index) {
      const target = nodes[index];
      if (!target) return;
      active.value = index;
      if (narrow.matches) {
        expanded.value = false;
        await nextTick();
        documentRoot.value?.querySelector('.template-outline-toggle')?.focus({ preventScroll: true });
      }
      // 滚动到固定工具栏下缘，保持标题完整露出。
      window.scrollBy({ top: target.getBoundingClientRect().top - stickyHeight.value - 20 });
      jumpScrollY = window.scrollY;
    }
    function trackSection() {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        // 短文末尾可能滚不到视口顶部，点击过的章节保留高亮，直到用户继续滚动。
        if (jumpScrollY === window.scrollY) return;
        jumpScrollY = null;
        const marker = stickyHeight.value + Math.min(140, window.innerHeight * 0.18);
        let index = 0;
        for (const [i, node] of nodes.entries()) {
          if (node.getBoundingClientRect().top > marker) break;
          index = i;
        }
        active.value = index;
      });
    }
    function onWidthChange(event) { expanded.value = !event.matches; }
    onMounted(() => {
      refreshOutline();
      headingsObserver = new MutationObserver(refreshOutline);
      headingsObserver.observe(content.value, { childList: true, characterData: true, subtree: true });
      const toolbar = documentRoot.value.closest('[data-template-workspace]')?.querySelector('[data-template-toolbar]');
      if (toolbar) {
        toolbarObserver = new ResizeObserver(() => { stickyHeight.value = toolbar.offsetHeight; });
        toolbarObserver.observe(toolbar);
      }
      narrow.addEventListener('change', onWidthChange);
      if (props.trackReading) window.addEventListener('scroll', trackSection, { passive: true });
    });
    onBeforeUnmount(() => {
      headingsObserver?.disconnect();
      toolbarObserver?.disconnect();
      narrow.removeEventListener('change', onWidthChange);
      window.removeEventListener('scroll', trackSection);
      cancelAnimationFrame(frame);
    });
    return { documentRoot, content, anchors, active, expanded, stickyHeight, jump, icon };
  }
};

const TemplateGuidePreview = {
  props: { markdown: String, name: String, loadingVersion: Number },
  template: `<article class="template-prose template-reader" aria-label="模板正文" @click="followFootnote">
    <h1 v-if="preview?.title" class="template-reading-title" v-html="preview.title"></h1>
    <h1 v-else class="template-reading-title">{{name}}</h1>
    <slot/>
    <p v-if="loadingVersion" class="template-reading-status muted" role="status">正在读取 v{{loadingVersion}} 正文…</p>
    <div v-else-if="preview" class="template-reader-body" v-html="preview.body"></div>
    <template v-else-if="failed">
      <p class="msg warn" role="status">正文排版加载失败，刷新后可重试。</p><pre class="template-fallback">{{markdown}}</pre>
    </template>
    <p v-else class="muted" role="status">正在加载正文…</p>
  </article>`,
  setup(props) {
    const render = ref(null), failed = ref(false);
    const preview = computed(() => {
      if (!render.value || props.loadingVersion) return null;
      // 先整篇渲染，确保链接和脚注仍能互相引用。再从已清理的 HTML 中取出正文首个一级标题。
      const fragment = document.createElement('template');
      fragment.innerHTML = render.value(props.markdown);
      const first = fragment.content.firstElementChild;
      const title = first?.tagName === 'H1' && first.textContent.trim() ? first.innerHTML : '';
      if (title) first.remove();
      return { title, body: fragment.innerHTML };
    });
    onMounted(async () => {
      try { render.value = (await loadTemplateEditor()).renderGuide; }
      catch { failed.value = true; }
    });
    function followFootnote(event) {
      const link = event.target.closest('a[href^="#"]');
      if (!link) return;
      // 正文的锚点只在文章内跳转，避免覆盖管理台路由所用的 URL 哈希。
      event.preventDefault();
      const fragment = link.getAttribute('href').slice(1);
      const target = event.currentTarget.querySelector('#' + CSS.escape('user-content-' + fragment));
      target?.focus({ preventScroll: true });
      target?.scrollIntoView({ block: 'nearest' });
    }
    return { preview, failed, followFootnote };
  }
};

const TemplateGuideEditor = {
  components: { TemplateDocument },
  props: { modelValue: String, disabled: Boolean },
  emits: ['update:modelValue'],
  template: `<div class="template-editor" data-template-workspace @keydown.capture="handleEditorKeys">
    <div class="template-editor-chrome" data-template-toolbar>
      <div class="template-guide-head">
        <h2>模板正文</h2>
        <div class="template-editor-modes" role="group" aria-label="正文编辑模式">
          <button type="button" class="btn" :disabled="disabled" :aria-pressed="!sourceMode" @click="sourceMode=false">可视化编辑</button>
          <button type="button" class="btn" :disabled="disabled" :aria-pressed="sourceMode" @click="sourceMode=true">Markdown</button>
        </div>
      </div>
      <template v-if="!sourceMode">
        <div class="template-toolbar" role="group" aria-label="正文格式">
          <div class="template-history-tools">
            <button type="button" class="btn" title="撤销" aria-label="撤销" :disabled="disabled || !selection.undo" @mousedown.prevent @click="command('undo')"><span class="ico" v-html="icon('undo')"></span></button>
            <button type="button" class="btn" title="重做" aria-label="重做" :disabled="disabled || !selection.redo" @mousedown.prevent @click="command('redo')"><span class="ico" v-html="icon('redo')"></span></button>
          </div>
          <select class="in" aria-label="段落样式" :value="selection.heading || 0" :disabled="disabled || !ready || selection.code || selection.table"
            @change="command('heading', Number($event.target.value))">
            <option :value="0">正文</option>
            <option v-for="level in 6" :key="level" :value="level">标题 {{level}}</option>
          </select>
          <div v-for="(group, index) in formatGroups" :key="index" class="template-format-group">
            <button v-for="item in group" :key="item.key" type="button" class="btn" :title="item.label" :aria-label="item.label"
              :disabled="disabled || !ready || formatDisabled(item)"
              :aria-pressed="item.action ? null : !!selection[item.key]" @mousedown.prevent @click="item.key === 'link' ? openLink() : command(item.key)">
              <span class="ico" v-html="icon(item.icon || item.key)"></span>
            </button>
          </div>
        </div>
        <form v-if="linkOpen" class="template-toolbar template-context-tools" aria-label="编辑链接" @submit.prevent="applyLink"
          @keydown.esc.prevent="closeLink">
          <label for="template-link-url">链接地址</label>
          <input ref="linkInput" id="template-link-url" class="in" v-model="linkUrl" placeholder="https://…" autocomplete="off"
            :aria-invalid="!!linkError" @input="linkError=''">
          <button type="submit" class="btn" :disabled="disabled">应用</button>
          <button v-if="selection.link" type="button" class="btn" :disabled="disabled" @click="removeLink">移除链接</button>
          <button type="button" class="btn link" @click="closeLink">取消</button>
          <span v-if="linkError" class="template-tool-error" role="alert">{{linkError}}</span>
        </form>
        <div v-if="selection.table" class="template-toolbar template-table-tools" role="group" aria-label="表格操作">
          <div v-for="(group, index) in tableActions" :key="index" class="template-format-group">
            <button v-for="item in group" :key="item.key" type="button" class="btn" :disabled="disabled || selection[item.blocked]"
              :title="item.hint" @mousedown.prevent @click="command(item.key)">{{item.label}}</button>
          </div>
          <label class="template-context-field">列对齐
            <select class="in" :value="selection.tableAlign" :disabled="disabled" @change="command('tableAlign', $event.target.value)">
              <option value="left">左对齐</option><option value="center">居中</option><option value="right">右对齐</option>
            </select>
          </label>
          <button type="button" class="btn link danger" :disabled="disabled" @mousedown.prevent @click="command('deleteTable')">删除表格</button>
          <button type="button" class="btn link" :disabled="disabled" @mousedown.prevent @click="command('exitTable')">退出表格</button>
        </div>
        <div v-if="selection.code" class="template-toolbar template-context-tools" role="group" aria-label="代码块操作">
          <label class="template-context-field">代码语言
            <select class="in" :value="selection.codeLanguage" :disabled="disabled" @change="command('codeLanguage', $event.target.value)">
              <option value="">纯文本</option>
              <option v-for="language in codeLanguages" :key="language" :value="language">{{language}}</option>
              <option v-if="selection.codeLanguage && !codeLanguages.includes(selection.codeLanguage)" :value="selection.codeLanguage">{{selection.codeLanguage}}</option>
            </select>
          </label>
          <button type="button" class="btn" @mousedown.prevent @click="copyCode">复制代码</button>
          <button type="button" class="btn link" :disabled="disabled" @mousedown.prevent @click="command('exitCode')">退出代码块</button>
        </div>
      </template>
    </div>
    <TemplateDocument :keep-outline="!sourceMode">
      <slot name="properties"/>
      <p v-if="notice" class="msg warn template-editor-notice" role="status">{{notice}}</p>
      <textarea v-if="sourceMode" class="in mono template-source" :value="modelValue" :disabled="disabled" rows="14" aria-label="Markdown 正文"
        @input="$emit('update:modelValue', $event.target.value)"></textarea>
      <template v-else>
        <p v-if="!ready" class="template-reader muted" role="status">正在打开可视化编辑器…</p>
        <div ref="visualRoot" class="template-prose template-sheet" :class="{'template-sheet-empty': ready && !modelValue.trim()}"
          :aria-busy="!ready" v-show="ready" @focusout="dismissSlash"></div>
      </template>
    </TemplateDocument>
    <Teleport to="body">
      <div v-if="slashItems.length && slashPosition && !disabled" ref="slashMenu" class="template-slash-menu" :style="slashPosition"
        role="group" aria-label="快速插入">
        <button v-for="(item, index) in slashItems" :key="item.key" type="button" class="template-slash-option"
          :class="{active: index === slashIndex}" @pointerenter="slashIndex=index" @mousedown.prevent @click="chooseSlash(item)">
          <span class="ico" v-html="icon(item.icon)"></span>{{item.label}}
        </button>
      </div>
    </Teleport>
  </div>`,
  setup(props, { emit }) {
    const sourceMode = ref(false), visualRoot = ref(null), ready = ref(false), notice = ref(''), selection = ref({});
    const linkOpen = ref(false), linkInput = ref(null), linkUrl = ref(''), linkError = ref('');
    const slashPosition = ref(null), slashIndex = ref(0), slashMenu = ref(null), slashDismissed = ref(false);
    const codeLanguages = ['sql', 'python', 'javascript', 'typescript', 'json', 'html', 'css', 'bash', 'yaml', 'markdown'];
    const formatGroups = [
      [{ key: 'bold', label: '加粗' }, { key: 'italic', label: '斜体' }, { key: 'strike', label: '删除线' },
        { key: 'inlineCode', label: '行内代码' }, { key: 'link', label: '链接' }],
      [{ key: 'bullet', icon: 'bullets', label: '项目列表', block: true }, { key: 'ordered', label: '编号列表', block: true },
        { key: 'task', label: '任务列表', block: true }, { key: 'outdent', label: '减少缩进', action: true },
        { key: 'indent', icon: 'indentMore', label: '增加缩进', action: true }],
      [{ key: 'quote', label: '引用', block: true }, { key: 'code', label: '代码块', block: true },
        { key: 'divider', label: '分隔线', action: true, block: true },
        { key: 'table', label: '插入表格', action: true, block: true }],
    ];
    const tableActions = [
      [{ key: 'addRowBefore', label: '添加上方行', blocked: 'aboveHeader', hint: '在当前行上方插入；表头保持首行' },
        { key: 'addRow', label: '添加下方行' }, { key: 'deleteRow', label: '删除行', blocked: 'cannotDeleteRow', hint: '删除表头后，下一行成为表头' }],
      [{ key: 'addColBefore', label: '添加左侧列' }, { key: 'addCol', label: '添加右侧列' },
        { key: 'deleteCol', label: '删除列', blocked: 'minCols', hint: '保留至少一列' }],
    ];
    const slashCommands = [
      { key: 'h1', command: 'heading', value: 1, icon: 'tpl', label: '标题 1', terms: 'heading h1 biaoti' },
      { key: 'h2', command: 'heading', value: 2, icon: 'tpl', label: '标题 2', terms: 'heading h2 biaoti' },
      { key: 'h3', command: 'heading', value: 3, icon: 'tpl', label: '标题 3', terms: 'heading h3 biaoti' },
      { key: 'bullet', icon: 'bullets', label: '项目列表', terms: '无序列表 bullet list wxlb' },
      { key: 'ordered', icon: 'ordered', label: '编号列表', terms: '有序列表 numbered list yxlb' },
      { key: 'task', icon: 'task', label: '任务列表', terms: 'todo task rw' },
      { key: 'quote', icon: 'quote', label: '引用', terms: 'quote yy' },
      { key: 'code', icon: 'code', label: '代码块', terms: 'code dmk' },
      { key: 'table', icon: 'table', label: '表格', terms: 'table bg' },
      { key: 'divider', icon: 'divider', label: '分隔线', terms: 'divider fgx' },
    ];
    const slashItems = computed(() => {
      if (!selection.value.slash || slashDismissed.value || !ready.value) return [];
      const query = selection.value.slash.query.trim().toLowerCase();
      return slashCommands.filter(item => (item.label + ' ' + item.terms).includes(query));
    });
    let editor, generation = 0, editorMarkdown;
    function formatDisabled(item) {
      if (item.key === 'indent' || item.key === 'outdent') return !selection.value[item.key];
      return (selection.value.code && item.key !== 'code') || (selection.value.table && item.block);
    }
    function command(name, value, fromSlash) {
      linkOpen.value = false;
      const applied = editor?.command(name, value, fromSlash);
      if (applied === false && !['undo', 'redo'].includes(name)) toast('当前选区无法应用此格式。', 'warn');
      return applied;
    }
    async function openLink() {
      linkUrl.value = selection.value.link || '';
      linkError.value = '';
      linkOpen.value = true;
      await nextTick();
      linkInput.value?.select();
    }
    function closeLink() { linkOpen.value = false; editor?.focus(); }
    function removeLink() { command('link', ''); }
    function applyLink() {
      const url = linkUrl.value.trim();
      try {
        if (!url || !['https:', 'http:', 'mailto:', 'tel:'].includes(new URL(url, document.baseURI).protocol)) throw new Error();
      } catch { linkError.value = '请输入有效的链接地址。'; return; }
      command('link', url);
    }
    async function copyCode() {
      try { await navigator.clipboard.writeText(editor.codeText()); toast('已复制代码', 'ok'); }
      catch { toast('无法访问剪贴板，请选中代码后复制。', 'warn'); }
    }
    function placeSlash() {
      if (!slashItems.value.length || !editor) { slashPosition.value = null; return; }
      const rect = editor.slashRect();
      if (rect.bottom < 0 || rect.top > window.innerHeight) { slashPosition.value = null; return; }
      const roomBelow = window.innerHeight - rect.bottom - 12;
      const roomAbove = rect.top - 12;
      const below = roomBelow >= 245 || roomBelow >= roomAbove;
      slashPosition.value = {
        left: Math.max(8, Math.min(rect.left, window.innerWidth - 228)) + 'px',
        [below ? 'top' : 'bottom']: (below ? rect.bottom + 6 : window.innerHeight - rect.top + 6) + 'px',
        maxHeight: Math.min(292, below ? roomBelow : roomAbove) + 'px',
      };
    }
    function dismissSlash() { slashDismissed.value = true; slashPosition.value = null; }
    function chooseSlash(item) {
      command(item.command || item.key, item.value, true);
      dismissSlash();
    }
    function handleEditorKeys(event) {
      if (props.disabled || event.isComposing || !event.target.closest('.ProseMirror')) return;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k' && !selection.value.code) {
        event.preventDefault();
        event.stopPropagation();
        openLink();
        return;
      }
      if (!slashItems.value.length || !slashPosition.value || !['ArrowDown', 'ArrowUp', 'Enter', 'Escape'].includes(event.key)) return;
      event.preventDefault();
      event.stopPropagation();
      if (event.key === 'Escape') dismissSlash();
      else if (event.key === 'Enter') chooseSlash(slashItems.value[slashIndex.value]);
      else {
        slashIndex.value = (slashIndex.value + (event.key === 'ArrowDown' ? 1 : -1) + slashItems.value.length) % slashItems.value.length;
        nextTick(() => slashMenu.value?.querySelector('.active')?.scrollIntoView({ block: 'nearest' }));
      }
    }
    function dispose() {
      generation++;
      editor?.destroy();
      editor = null;
      linkOpen.value = false;
      dismissSlash();
    }
    async function mountVisual() {
      dispose();
      const current = generation;
      ready.value = false;
      selection.value = {};
      notice.value = '';
      await nextTick();
      if (!visualRoot.value || current !== generation) return;
      const root = document.createElement('div');
      visualRoot.value.replaceChildren(root);
      editorMarkdown = props.modelValue;
      try {
        const { createGuideEditor } = await loadTemplateEditor();
        if (current !== generation) return;
        const instance = await createGuideEditor(root, editorMarkdown, markdown => {
          if (current !== generation) return;
          editorMarkdown = markdown;
          emit('update:modelValue', markdown);
        }, selected => {
          if (current !== generation) return;
          const previous = selection.value.slash;
          selection.value = selected;
          if (!selected.slash || previous?.to !== selected.slash.to || previous?.query !== selected.slash.query) {
            slashDismissed.value = false;
            slashIndex.value = 0;
          }
          nextTick(placeSlash);
        });
        // 切换模式、加载历史或离开页面后，迟到的编辑器不能覆盖当前正文。
        if (current !== generation) { instance?.destroy(); return; }
        if (!instance) {
          notice.value = '此正文含有引用式链接，请在 Markdown 模式编辑以保留链接定义。';
          sourceMode.value = true;
          return;
        }
        editor = instance;
        editor.setDisabled(props.disabled);
        ready.value = true;
      } catch {
        if (current !== generation) return;
        notice.value = '可视化编辑器无法打开此正文，可以继续使用 Markdown 编辑。';
        sourceMode.value = true;
      }
    }
    watch(sourceMode, source => { if (source) dispose(); else mountVisual(); });
    watch(() => props.modelValue, markdown => {
      // 富文本输入刚同步回来的值无需重载，历史正文和外部替换需要新编辑会话。
      if (!sourceMode.value && markdown !== editorMarkdown) mountVisual();
    });
    watch(() => props.disabled, disabled => editor?.setDisabled(disabled));
    onMounted(() => {
      mountVisual();
      window.addEventListener('resize', placeSlash);
      window.addEventListener('scroll', placeSlash, true);
    });
    onBeforeUnmount(() => {
      dispose();
      window.removeEventListener('resize', placeSlash);
      window.removeEventListener('scroll', placeSlash, true);
    });
    return { sourceMode, visualRoot, ready, notice, selection, formatGroups, tableActions, icon,
      command, codeLanguages, formatDisabled, linkOpen, linkInput, linkUrl, linkError, openLink, closeLink, removeLink, applyLink,
      copyCode, slashItems, slashPosition, slashIndex, slashMenu, chooseSlash, dismissSlash, handleEditorKeys };
  }
};

const Templates = {
  components: { TemplateDocument, TemplateGuidePreview, TemplateGuideEditor },
  template: `<div>
    <template v-if="!form">
      <div class="page-head"><h1 ref="templateHeading" tabindex="-1">报告模板</h1>
        <button type="button" class="btn primary" @click="edit()"><span class="ico" v-html="icon('plus')"></span>新建模板</button></div>
      <section class="card list-panel" aria-label="模板列表" :aria-busy="loading">
        <div class="list-tools">
          <label class="search-field"><span class="ico" v-html="icon('search')"></span><input type="search" v-model="kw" placeholder="搜索模板名称、场景或用途" aria-label="搜索模板"></label>
          <span v-if="!loading && !loadError" class="list-count" aria-live="polite">{{shown.length}} 个模板</span>
        </div>
        <div v-if="loading" class="collection-state" role="status">正在加载模板…</div>
        <div v-else-if="loadError" class="collection-state" role="alert">模板加载失败。<button type="button" class="btn link" @click="load">重试</button></div>
        <div v-else-if="!shown.length" class="collection-state">
          <span class="empty-icon" v-html="icon(kw.trim() ? 'search' : 'tpl')"></span>
          <h2>{{kw.trim() ? '未找到匹配的模板' : '还没有报告模板'}}</h2>
          <p>{{kw.trim() ? '试试其他关键词。' : '可以新建模板，也可以在已接入的 AI 助手中提交分析需求，由助手保存模板。'}}</p>
          <button type="button" v-if="kw.trim()" class="btn" @click="kw=''">清除搜索</button>
          <button type="button" v-else class="btn" @click="edit()">新建模板</button>
        </div>
        <div v-else class="scroll-x" tabindex="0" aria-label="模板表格"><table class="tbl record-table report-table" role="table">
          <thead><tr><th scope="col">模板名称</th><th scope="col">业务场景</th><th scope="col" v-if="admin">创建人</th><th scope="col">更新时间</th><th scope="col">操作</th></tr></thead>
          <tbody><tr v-for="t in shown" :key="t.id">
            <td><b>{{t.name}}</b> <span class="badge">v{{t.version}}</span><div class="small muted clip">{{t.business_summary || t.analysis_goal || t.user_requirement}}</div></td>
            <td data-label="业务场景">{{t.business_scenario || '—'}}</td><td v-if="admin" data-label="创建人">{{creator(t)}}</td><td data-label="更新时间"><time :datetime="t.updated_at">{{fmtTime(t.updated_at)}}</time></td>
            <td class="record-actions"><div class="links"><button type="button" class="btn link" @click="view(t)">查看</button>
              <button type="button" v-if="canEdit(t)" class="btn link danger" @click="del(t)">删除</button></div></td>
          </tr></tbody>
        </table></div>
      </section>
    </template>
    <template v-else-if="!editing">
      <section class="template-reading-page" :class="{'template-reading-wide': wideReading}" aria-label="模板阅读" data-template-workspace>
        <header class="template-reading-bar" data-template-toolbar>
          <div class="template-reading-path">
            <button type="button" class="btn ghost" aria-label="返回模板列表" @click="closeDetail">
              <span class="ico" v-html="icon('back')"></span>报告模板
            </button>
            <span class="muted" aria-hidden="true">/</span>
            <b ref="templateHeading" tabindex="-1" :title="form.name">{{form.name}}</b>
          </div>
          <div class="template-reading-actions">
            <select v-if="form.versions?.length > 1" class="in" aria-label="正文版本" v-model="viewVersion" @change="readVersion">
              <option v-for="v in form.versions" :key="v.version" :value="v.version">v{{v.version}} · {{fmtTime(v.created_at)}}</option>
            </select>
            <span v-else class="template-reading-version">v{{viewVersion}}</span>
            <button type="button" class="btn template-reading-width" :aria-pressed="wideReading" @click="wideReading=!wideReading">宽页</button>
            <button type="button" v-if="canEdit(form)" class="btn primary" @click="edit(form)">
              <span class="ico" v-html="icon('pencil')"></span>编辑模板
            </button>
          </div>
        </header>
        <TemplateDocument track-reading :aria-busy="versionLoading">
          <TemplateGuidePreview :name="form.name" :markdown="viewMarkdown" :loading-version="versionLoading ? viewVersion : null">
            <div class="template-reading-meta">
              <span v-if="form.business_scenario">{{form.business_scenario}}</span>
              <time v-if="viewDate" :datetime="viewDate">{{fmtTime(viewDate)}}</time>
            </div>
            <dl v-if="form.business_summary || form.analysis_goal || form.user_requirement" class="template-brief" aria-label="模板信息">
              <div><dt>模板用途</dt><dd class="business-copy">{{form.business_summary || form.analysis_goal || form.user_requirement}}</dd></div>
              <div v-if="form.business_summary && form.analysis_goal && form.analysis_goal !== form.business_summary">
                <dt>分析目标</dt><dd class="business-copy">{{form.analysis_goal}}</dd>
              </div>
            </dl>
          </TemplateGuidePreview>
        </TemplateDocument>
      </section>
    </template>
    <template v-else>
      <div class="page-head template-workhead"><h1 ref="templateHeading" tabindex="-1">{{form.id ? '编辑模板' : '新建模板'}}</h1>
        <div class="actions"><button type="button" class="btn ghost" :disabled="busy" @click="cancelEdit">取消</button>
          <button type="button" class="btn primary" @click="save" :disabled="busy || versionLoading || !form.markdown.trim()">保存模板</button></div></div>
      <section class="card template-guide" aria-label="编辑模板正文" :aria-busy="busy || versionLoading">
        <TemplateGuideEditor v-model="form.markdown" :disabled="busy || versionLoading">
          <template #properties>
            <fieldset class="template-meta" aria-label="模板信息" :disabled="busy">
              <div class="field"><label for="template-name">模板名称</label><input id="template-name" class="in" v-model="form.name" placeholder="留空时使用正文标题"></div>
              <div class="field"><label for="template-scenario">业务场景</label>
                <select id="template-scenario" class="in" v-model="form.dataset_id"><option :value="null">不关联业务场景</option>
                  <option v-if="form.dataset_id && !datasets.some(d => d.id === form.dataset_id)" :value="form.dataset_id">原场景（当前不可选）</option>
                  <option v-for="d in datasets" :key="d.id" :value="d.id">{{d.business_scenario}}</option></select></div>
              <details class="template-extra">
                <summary>业务说明与分析目标 <span class="muted">选填</span></summary>
                <div class="field"><label for="template-requirement">原始分析需求</label><input id="template-requirement" class="in" v-model="form.user_requirement" placeholder="填写需要回答的业务问题"></div>
                <div class="field"><label for="template-goal">分析目标</label><input id="template-goal" class="in" v-model="form.analysis_goal"></div>
                <div class="field"><label for="template-business-summary">业务说明</label>
                  <textarea id="template-business-summary" class="in" v-model="form.business_summary" rows="3"></textarea></div>
              </details>
            </fieldset>
          </template>
        </TemplateGuideEditor>
        <div v-if="form.versions?.length" class="template-guide-foot">
          <div class="template-version-picker">
            <label for="template-load-version">历史正文</label>
            <select id="template-load-version" class="in" v-model="historyVersion" :disabled="busy || versionLoading">
              <option v-for="v in form.versions" :key="v.version" :value="v.version">v{{v.version}} · {{fmtTime(v.created_at)}}</option>
            </select>
          </div>
          <button type="button" class="btn" @click="loadVer" :disabled="busy || versionLoading">{{versionLoading ? '正在载入…' : '载入正文'}}</button>
        </div>
      </section>
    </template>
  </div>`,
  setup() {
    const templates = useCollection('/api/templates');
    const { load } = templates;
    const form = ref(null), datasets = ref([]), busy = ref(false), kw = ref('');
    const editing = ref(false), templateHeading = ref(null), viewVersion = ref(null), viewMarkdown = ref('');
    const versionLoading = ref(false), historyVersion = ref(null), wideReading = ref(false);
    const viewDate = computed(() => form.value?.versions?.find(v => v.version === viewVersion.value)?.created_at
      || form.value?.updated_at);
    let savedTemplate = null, loadedMarkdown = '', versionRequest = null;
    const shown = computed(() => templates.list.value.filter(t => matches(kw.value, t.name, t.business_scenario, t.business_summary, t.analysis_goal, t.user_requirement)));
    onMounted(async () => { datasets.value = await run(() => api('/api/datasets')); });
    async function view(t) {
      try { await showDetail(await run(() => api(`/api/templates/${t.id}`))); }
      catch { /* 请求错误由通知显示，列表可继续使用。 */ }
    }
    async function showDetail(t) {
      form.value = t;
      viewVersion.value = t.version;
      viewMarkdown.value = t.markdown;
      editing.value = false;
      await nextTick();
      window.scrollTo(0, 0);
      templateHeading.value?.focus({ preventScroll: true });
    }
    function stopVersionRequest() {
      versionRequest?.abort();
      versionRequest = null;
      versionLoading.value = false;
    }
    async function closeDetail() {
      stopVersionRequest();
      form.value = null;
      await nextTick();
      window.scrollTo(0, 0);
      templateHeading.value?.focus({ preventScroll: true });
    }
    async function readVersion() {
      stopVersionRequest();
      const t = form.value, version = viewVersion.value;
      if (version === t.version) { viewMarkdown.value = t.markdown; return; }
      const request = versionRequest = new AbortController();
      versionLoading.value = true;
      try {
        const result = await api(`/api/templates/${t.id}/versions/${version}`, { signal: request.signal });
        if (versionRequest === request) viewMarkdown.value = result.markdown;
      } catch (e) {
        if (versionRequest !== request) return;
        // 失败时恢复最新正文与对应版本，避免把旧内容显示为所选版本。
        viewVersion.value = t.version;
        viewMarkdown.value = t.markdown;
        toast(e.message, 'err');
      } finally {
        if (versionRequest === request) stopVersionRequest();
      }
    }
    const canEdit = t => isAdmin() || t.created_by === state.me?.emp_no;
    async function edit(t) {
      // 从最新版建立编辑副本。需要恢复历史内容时，可在编辑器内载入所选正文。
      stopVersionRequest();
      savedTemplate = t ? { ...t } : null;
      editing.value = true;
      form.value = t ? { ...t } : { id: null, name: '', dataset_id: null, user_requirement: '', analysis_goal: '', business_summary: '', markdown: '', version: 1 };
      historyVersion.value = form.value.version;
      loadedMarkdown = form.value.markdown;
      await nextTick();
      templateHeading.value?.focus();
    }
    async function cancelEdit() {
      stopVersionRequest();
      if (savedTemplate) await showDetail(savedTemplate);
      else { form.value = null; editing.value = false; }
    }
    async function save() {
      busy.value = true;
      const f = form.value, body = { name: f.name, markdown: f.markdown, dataset_id: f.dataset_id,
        user_requirement: f.user_requirement, analysis_goal: f.analysis_goal, business_summary: f.business_summary };
      // 带上开始编辑时的版本，避免覆盖别人刚保存的正文。
      if (f.id) body.expected_version = f.version;
      try {
        const t = await run(() => f.id
          ? api('/api/templates/' + f.id, { method: 'PUT', body })
          : api('/api/templates', { method: 'POST', body }), '模板已保存');
        // 保存响应已含刚提交的正文。沿用已知历史，避免后续读取失败时留在过期的编辑版本。
        const versions = f.versions || [];
        t.versions = versions.some(v => v.version === t.version)
          ? versions
          : [{ version: t.version, created_at: t.updated_at }, ...versions];
        await showDetail(t);
        load();
      } catch { /* 保留用户输入，提交错误由通知显示。 */ }
      finally { busy.value = false; }
    }
    async function del(t) {
      if (!await askConfirm({ title: '删除报告模板', message: `确认删除模板「${t.name}」？仍有关联报告的模板无法删除。`, confirmText: '删除' })) return;
      await run(() => api(`/api/templates/${t.id}`, { method: 'DELETE' }), '已删除'); load();
    }
    async function loadVer() {
      const f = form.value, version = historyVersion.value;
      if (f.markdown !== loadedMarkdown && !await askConfirm({
        title: '载入历史正文', message: '载入后会替换当前未保存的正文。', confirmText: '载入正文',
      })) return;
      const request = versionRequest = new AbortController();
      versionLoading.value = true;
      try {
        const result = await api(`/api/templates/${f.id}/versions/${version}`, { signal: request.signal });
        if (versionRequest !== request) return;
        loadedMarkdown = f.markdown = result.markdown;
        toast(`已载入 v${version} 正文`, 'ok');
      } catch (e) {
        if (versionRequest === request) toast(e.message, 'err');
      } finally {
        if (versionRequest === request) stopVersionRequest();
      }
    }
    onBeforeUnmount(stopVersionRequest);
    const admin = computed(isAdmin);
    return { ...templates, admin, creator, shown, form, datasets, busy, kw, editing, templateHeading,
      viewVersion, viewDate, viewMarkdown, versionLoading, historyVersion, wideReading, readVersion, canEdit, view, closeDetail,
      edit, cancelEdit, save, del, loadVer, fmtTime, icon };
  }
};

// ------------------------------------------------------------ 报告
const ReportSharing = {
  components: { Drawer }, props: ['report'], emits: ['close'],
  template: `<Drawer title="分享报告" compact :busy="busy" @close="$emit('close')">
    <p class="report-dialog-name">{{report.name}}</p>
    <div v-if="!manual && !selectedRecipient" class="report-recipient-picker">
      <div class="field">
        <label for="share-recipient-search">选择接收人</label>
        <input id="share-recipient-search" class="in" ref="searchInput" type="search" v-model="query"
          maxlength="100" placeholder="搜索姓名或工号" :disabled="busy" aria-controls="share-recipient-results">
      </div>
      <div id="share-recipient-results" :aria-busy="searching">
        <p v-if="searching" class="field-help" role="status">正在查找用户…</p>
        <p v-else-if="searchError" class="field-help" role="alert">用户列表加载失败。<button type="button" class="btn link" @click="search(query)">重试</button></p>
        <p v-else-if="!candidates.length" class="field-help" role="status">未找到可选用户。</p>
        <ul v-else class="report-recipient-results" aria-label="可选用户">
          <li v-for="person in candidates" :key="person.emp_no">
            <button type="button" class="btn recipient-option" :disabled="busy" @click="choose(person)">
              <b>{{person.name || person.emp_no}}</b><span v-if="person.name" class="small muted">{{person.emp_no}}</span>
            </button>
          </li>
        </ul>
      </div>
      <button type="button" class="btn link report-recipient-switch" @click="useManual">手动输入工号</button>
    </div>
    <form class="report-share-form" @submit.prevent="save">
      <div v-if="manual || selectedRecipient" class="field report-recipient-field">
        <label for="share-emp-no">接收人工号</label>
        <div class="report-recipient-input">
          <input id="share-emp-no" class="in" ref="recipient" v-model.trim="empNo" maxlength="256" :disabled="busy" :readonly="!manual" required>
          <button type="button" class="btn link report-recipient-switch" :disabled="busy" @click="useDirectory">{{manual ? '选择已有用户' : '更换'}}</button>
        </div>
        <p v-if="manual" class="field-help">请输入已登记并具备报告数据权限的工号。</p>
        <p v-else-if="selectedRecipient.name" class="field-help">{{selectedRecipient.name}}</p>
      </div>
      <template v-if="manual || selectedRecipient">
        <div class="field"><label for="share-access">权限</label><select id="share-access" class="in" v-model="level" :disabled="busy">
          <option value="view">可查看</option><option value="edit">可编辑</option></select></div>
        <button class="btn primary" :disabled="busy || loading || loadFailed || !empNo">{{busy ? '处理中…' : '保存权限'}}</button>
      </template>
    </form>
    <p class="field-help">接收人需用获授权的工号登录海尔 IAM 后打开链接。</p>
    <p v-if="error" class="msg err" role="alert">{{error}}</p>
    <section class="report-share-people" aria-labelledby="share-people-title" :aria-busy="loading || busy">
      <h3 id="share-people-title">已分享的用户</h3>
      <p v-if="loading" class="muted" role="status">正在加载…</p>
      <button v-else-if="loadFailed" type="button" class="btn link" @click="load">重试加载</button>
      <p v-else-if="!shares.length" class="muted">尚未分享给其他用户。</p>
      <ul v-else>
        <li v-for="s in shares" :key="s.emp_no">
          <div><b>{{s.name ? s.name + '（' + s.emp_no + '）' : s.emp_no}}</b>
            <div class="small muted">{{s.access_level === 'edit' ? '可编辑' : '可查看'}}{{!s.registered ? ' · 待登记' : s.enabled ? '' : ' · 已停用'}}</div></div>
          <div class="links"><button type="button" class="btn link" :aria-label="'修改 ' + s.emp_no + ' 的权限'" :disabled="busy || (s.registered && !s.enabled)" @click="choose(s)">修改</button>
            <button type="button" class="btn link danger" :aria-label="'撤销 ' + s.emp_no + ' 的权限'" :disabled="busy" @click="revoke(s)">撤销</button></div>
        </li>
      </ul>
    </section>
    <div v-if="url" class="field"><label for="share-url">报告链接</label>
      <input id="share-url" class="in" ref="link" :value="url" readonly @focus="$event.target.select()"></div>
    <template #foot>
      <button type="button" class="btn" :disabled="busy" @click="$emit('close')">关闭</button>
      <button type="button" class="btn" :disabled="!url || busy" @click="copyLink">复制链接</button>
    </template>
  </Drawer>`,
  setup(props) {
    const path = '/api/reports/' + props.report.id;
    const shares = ref([]), empNo = ref(''), level = ref('view'), url = ref('');
    const busy = ref(false), loading = ref(true), loadFailed = ref(false), error = ref('');
    const recipient = ref(null), link = ref(null), searchInput = ref(null);
    const manual = ref(false), selectedRecipient = ref(null), query = ref('');
    const candidates = ref([]), searching = ref(true), searchError = ref(false);
    let searchTimer, searchRevision = 0;
    // 较慢的旧请求不能覆盖新关键词的结果，手动输入也不依赖目录请求成功。
    async function search(term, revision = ++searchRevision) {
      searching.value = true;
      searchError.value = false;
      try {
        const people = await api(path + '/share-recipients?' + new URLSearchParams({ q: term.trim() }));
        if (revision === searchRevision) candidates.value = people;
      } catch {
        if (revision === searchRevision) searchError.value = true;
      } finally {
        if (revision === searchRevision) searching.value = false;
      }
    }
    watch(query, term => {
      clearTimeout(searchTimer);
      const revision = ++searchRevision;
      searching.value = true;
      searchTimer = setTimeout(() => search(term, revision), 250);
    });
    onBeforeUnmount(() => { clearTimeout(searchTimer); searchRevision++; });
    async function load() {
      loading.value = true;
      error.value = '';
      loadFailed.value = false;
      try {
        const [info, people] = await Promise.all([api(path + '?version=' + props.report.revision), api(path + '/shares')]);
        url.value = info.url;
        shares.value = people;
      } catch (e) { loadFailed.value = true; error.value = e.message; }
      finally { loading.value = false; }
    }
    onMounted(() => { load(); search(''); });
    function useManual() {
      manual.value = true;
      selectedRecipient.value = null;
      empNo.value = query.value.trim();
      level.value = 'view';
      nextTick(() => recipient.value?.focus());
    }
    function useDirectory() {
      manual.value = false;
      selectedRecipient.value = null;
      empNo.value = '';
      level.value = 'view';
      nextTick(() => searchInput.value?.focus());
    }
    function choose(person) {
      manual.value = false;
      selectedRecipient.value = person;
      empNo.value = person.emp_no;
      level.value = shares.value.find(s => s.emp_no === person.emp_no)?.access_level || 'view';
      nextTick(() => recipient.value?.focus());
    }
    async function save() {
      if (busy.value || loading.value || loadFailed.value || !empNo.value) return;
      busy.value = true;
      error.value = '';
      try {
        const saved = await api(path + '/shares', { method: 'POST', body: { emp_no: empNo.value, access_level: level.value } });
        shares.value = [...shares.value.filter(s => s.emp_no !== saved.emp_no), saved].sort((a, b) => a.emp_no.localeCompare(b.emp_no));
        query.value = '';
        useDirectory();
        toast(saved.registered ? '权限已保存' : '已按工号预授权，账号启用后生效', 'ok');
      } catch (e) { error.value = e.message; }
      finally { busy.value = false; }
    }
    async function revoke(share) {
      if (busy.value || !await askConfirm({ title: '撤销报告权限', message: '确认撤销 ' + share.emp_no + ' 的报告权限？', confirmText: '撤销' })) return;
      busy.value = true;
      error.value = '';
      try {
        await api(path + '/shares/' + encodeURIComponent(share.emp_no), { method: 'DELETE' });
        shares.value = shares.value.filter(s => s.emp_no !== share.emp_no);
        if (empNo.value === share.emp_no) useDirectory();
        toast('权限已撤销', 'ok');
      } catch (e) { error.value = e.message; }
      finally { busy.value = false; }
    }
    async function copyLink() {
      try { await navigator.clipboard.writeText(url.value); toast('链接已复制', 'ok'); }
      catch { link.value?.focus(); link.value?.select(); toast('链接已选中，可以手动复制'); }
    }
    return { shares, empNo, level, url, busy, loading, loadFailed, error, recipient, link, load, choose, save, revoke, copyLink,
      searchInput, manual, selectedRecipient, query, candidates, searching, searchError, search, useManual, useDirectory };
  }
};

const ReportEditor = {
  components: { Drawer }, props: ['report'], emits: ['close', 'saved'],
  template: `<Drawer title="编辑报告" wide :busy="busy" @close="close">
    <p v-if="error" class="msg err" role="alert">{{error}}</p>
    <p v-if="loading" class="muted" role="status">正在加载报告…</p>
    <button type="button" v-else-if="!form" class="btn link" @click="load">重试加载</button>
    <form v-else id="report-edit-form" @submit.prevent="save">
      <p class="form-intro">基于版本 {{form.base_version}} 编辑。保存后生成新版本。</p>
      <div class="field"><label for="report-edit-name">报告名称</label>
        <input id="report-edit-name" class="in" v-model.trim="form.name" required :disabled="busy"></div>
      <div class="field"><label for="report-edit-note">修改说明 <small>选填</small></label>
        <input id="report-edit-note" class="in" v-model.trim="form.change_note" maxlength="500" :disabled="busy"></div>
      <p class="field-help">正文修订可通过已接入的 AI 助手完成，修订正文时会同步更新关联的报告模板。</p>
      <a class="btn link" :href="preview" target="_blank" rel="noopener">打开基准版本</a>
    </form>
    <template #foot>
      <button type="button" class="btn" :disabled="busy" @click="close">取消</button>
      <button type="submit" class="btn primary" form="report-edit-form" :disabled="busy || !form || !dirty">{{busy ? '保存中…' : '保存为新版本'}}</button>
    </template>
  </Drawer>`,
  setup(props, { emit }) {
    const path = '/api/reports/' + props.report.id;
    const form = ref(null), original = ref(null), preview = ref('');
    const loading = ref(true), busy = ref(false), error = ref('');
    const dirty = computed(() => !!form.value && (form.value.name !== original.value.name || !!form.value.change_note));
    async function load() {
      loading.value = true;
      error.value = '';
      try {
        // 抽屉打开后锁定基准，即使另一个页面切换了版本也不会误编辑。
        const { name, revision, access_level, url } = await api(path + '?version=' + props.report.revision);
        if (access_level === 'view') throw new Error('当前账号无权编辑该报告');
        original.value = { name, base_version: revision, change_note: '' };
        form.value = { ...original.value };
        preview.value = url;
      } catch (e) { error.value = e.message; }
      finally { loading.value = false; }
    }
    onMounted(load);
    async function close() {
      if (busy.value) return;
      if (!dirty.value || await askConfirm({ title: '放弃修改', message: '放弃未保存的报告修改？', confirmText: '放弃修改', cancelText: '继续编辑', danger: true })) emit('close');
    }
    async function save() {
      if (!dirty.value || busy.value) return;
      busy.value = true;
      error.value = '';
      try {
        const saved = await api(path, { method: 'PUT', body: form.value });
        toast('已保存版本 ' + saved.revision, 'ok');
        emit('saved', saved);
      } catch (e) { error.value = e.message; }
      finally { busy.value = false; }
    }
    return { form, preview, loading, busy, error, dirty, load, close, save };
  }
};

const ReportVersions = {
  components: { Drawer }, props: ['report'], emits: ['close', 'selected'],
  template: `<Drawer title="报告版本" compact :busy="busy" @close="$emit('close')">
    <p class="report-dialog-name">{{report.name}}</p>
    <p v-if="error" class="msg err" role="alert">{{error}}</p>
    <p v-if="loading" class="muted" role="status">正在加载版本…</p>
    <button v-else-if="!history" type="button" class="btn link" @click="load">重试加载</button>
    <template v-else>
      <p class="form-intro">切换后，查看和编辑默认使用所选版本。</p>
      <ul class="report-versions-list" aria-label="版本历史">
        <li v-for="v in history.versions" :key="v.version">
          <div>
            <div class="report-version-label"><b>版本 {{v.version}}</b>
              <span v-if="v.version === report.revision" class="badge">正在查看</span>
              <span v-if="v.version === history.current_version && v.version !== report.revision" class="badge">当前选择</span>
              <span v-if="v.version === history.latest_version" class="small muted">最新</span></div>
            <div class="small muted"><time :datetime="v.created_at">{{fmtTime(v.created_at)}}</time> · {{creator(v)}}<span v-if="v.base_version"> · 基于版本 {{v.base_version}}</span><span v-if="v.template_id"> · 模板 {{v.template_name || ('#' + v.template_id)}}<template v-if="v.template_version"> v{{v.template_version}}</template></span></div>
            <div v-if="v.name !== report.name || v.change_note" class="report-version-note">
              <span v-if="v.name !== report.name">{{v.name}}</span><span v-if="v.name !== report.name && v.change_note"> · </span>{{v.change_note}}</div>
          </div>
          <button v-if="v.version !== history.current_version || v.version !== report.revision" type="button" class="btn link"
            :aria-label="'切换到版本 ' + v.version" :disabled="busy" @click="select(v.version)">切换</button>
        </li>
      </ul>
    </template>
    <template #foot>
      <button type="button" class="btn" :disabled="busy" @click="$emit('close')">关闭</button>
      <button v-if="history && (history.version_selected || report.revision !== history.latest_version)" type="button" class="btn primary" :disabled="busy" @click="select(null)">跟随最新版</button>
    </template>
  </Drawer>`,
  setup(props, { emit }) {
    const path = '/api/reports/' + props.report.id;
    const { list: history, loading, loadError, load } = useCollection(path + '/versions', null);
    const busy = ref(false), selectionError = ref('');
    const error = computed(() => selectionError.value || loadError.value);
    async function select(version) {
      if (busy.value) return;
      busy.value = true;
      selectionError.value = '';
      try {
        const selected = await api(path + '/selection', { method: 'PUT', body: { version } });
        toast(version === null ? '已切回最新版' : '已切换到版本 ' + selected.revision, 'ok');
        emit('selected', selected);
      } catch (e) { selectionError.value = e.message; }
      finally { busy.value = false; }
    }
    return { history, loading, busy, error, load, select, fmtTime, creator };
  }
};

const ReportMerge = {
  components: { Drawer }, props: ['reports'], emits: ['close', 'downloaded', 'update:reports'],
  template: `<Drawer title="调整合并顺序" compact :busy="busy" @close="$emit('close')">
    <form id="report-merge-form" @submit.prevent="download">
      <div class="field">
        <label for="report-merge-name">文件名称</label>
        <input id="report-merge-name" class="in" v-model.trim="name" :disabled="busy" required>
      </div>
      <p class="field-help">各份报告的标题和正文将按以下顺序从上到下拼接。</p>
      <ol class="report-merge-order" aria-label="合并顺序">
        <li v-for="(r, i) in reports" :key="r.id">
          <div class="report-merge-row">
            <div><b>{{r.name}}</b><div class="small muted">版本 {{r.revision}}</div></div>
            <div class="actions">
              <button type="button" class="btn" :id="'merge-up-' + r.id" :disabled="busy || i === 0"
                :aria-label="'上移 ' + r.name" @click="move(i, -1)">上移</button>
              <button type="button" class="btn" :id="'merge-down-' + r.id" :disabled="busy || i === reports.length - 1"
                :aria-label="'下移 ' + r.name" @click="move(i, 1)">下移</button>
            </div>
          </div>
        </li>
      </ol>
      <span class="report-merge-position" role="status">{{position}}</span>
      <p v-if="error" class="msg err" role="alert">{{error}}</p>
    </form>
    <template #foot>
      <button type="button" class="btn" :disabled="busy" @click="$emit('close')">返回选择</button>
      <button type="submit" class="btn primary" form="report-merge-form" :disabled="busy || !name">{{busy ? '正在合并…' : '合并并下载 HTML'}}</button>
    </template>
  </Drawer>`,
  setup(props, { emit }) {
    const name = ref('合并报告');
    const busy = ref(false), error = ref(''), position = ref('');
    function move(index, direction) {
      const target = index + direction;
      const ordered = [...props.reports];
      const [moved] = ordered.splice(index, 1);
      ordered.splice(target, 0, moved);
      // 用户返回列表增减勾选项时，保留已经调整的相对顺序。
      emit('update:reports', ordered);
      position.value = moved.name + ' 已移至第 ' + (target + 1) + ' 位';
      // 重排后保持焦点在同一份报告，到达边界时聚焦另一个可用方向。
      const focusUp = direction < 0 ? target > 0 : target === ordered.length - 1;
      nextTick(() => document.getElementById((focusUp ? 'merge-up-' : 'merge-down-') + moved.id)?.focus());
    }
    async function download() {
      if (busy.value || !name.value) return;
      busy.value = true;
      error.value = '';
      try {
        const reports = props.reports.map(r => ({ id: r.id, version: r.revision }));
        const file = await api('/api/reports/merge', { method: 'POST', body: { name: name.value, reports }, download: true });
        const url = URL.createObjectURL(file);
        const link = document.createElement('a');
        link.href = url;
        link.download = name.value + '.html';
        link.click();
        // 下载由浏览器异步接管，延迟释放临时 URL。
        setTimeout(() => URL.revokeObjectURL(url), 60_000);
        emit('downloaded');
      } catch (e) { error.value = e.message; }
      finally { busy.value = false; }
    }
    return { name, busy, error, position, move, download };
  }
};

const Reports = {
  components: { ReportSharing, ReportEditor, ReportVersions, ReportMerge },
  template: `<div>
    <div class="page-head"><div><h1>报告库</h1><p>{{admin ? '查看所有用户已生成的报告。' : '查看自己生成的报告和他人分享给你的报告。'}}</p></div>
      <div class="actions">
        <button type="button" class="btn" :class="{'is-active': selecting}" :aria-pressed="selecting" ref="mergeButton" :disabled="!selecting && (loading || !!loadError || list.length < 2)"
          @click="toggleMerge">{{selecting ? '取消选择' : '合并报告'}}</button>
      </div></div>
    <section class="card list-panel" aria-label="报告列表" :aria-busy="loading">
      <div class="list-tools">
        <label class="search-field"><span class="ico" v-html="icon('search')"></span><input type="search" v-model="kw" placeholder="搜索报告名称或来源模板" aria-label="搜索报告"></label>
        <div v-if="selecting" class="actions report-merge-tools">
          <span class="list-count" role="status">已选 {{picked.length}} 份<span v-if="picked.length < 2">，请至少选择 2 份</span></span>
          <button type="button" class="btn primary" :disabled="picked.length < 2 || loading || !!loadError" @click="merging=true">调整合并顺序</button>
        </div>
        <span v-else-if="!loading && !loadError" class="list-count" aria-live="polite">{{shown.length}} 份报告</span>
      </div>
      <div v-if="loading" class="collection-state" role="status">正在加载报告…</div>
      <div v-else-if="loadError" class="collection-state" role="alert">报告加载失败。<button type="button" class="btn link" @click="load">重试</button></div>
      <div v-else-if="!shown.length" class="collection-state">
        <span class="empty-icon" v-html="icon(kw.trim() ? 'search' : 'rep')"></span>
        <h2>{{kw.trim() ? '未找到匹配的报告' : '还没有报告'}}</h2>
        <p>{{kw.trim() ? '可以修改关键词，或查看全部报告。' : '在已接入的 AI 助手中提交分析需求即可生成报告。'}}</p>
        <button type="button" v-if="kw.trim()" class="btn" @click="kw=''">清除搜索</button>
        <a v-else class="btn" href="#templates">查看报告模板</a>
      </div>
      <div v-else class="scroll-x" tabindex="0" aria-label="报告表格"><table class="tbl record-table report-table" role="table">
        <thead><tr><th scope="col">报告名称</th><th scope="col">版本</th><th scope="col">来源模板</th><th scope="col" v-if="admin">创建人</th><th scope="col">版本时间</th><th scope="col">操作</th></tr></thead>
        <tbody><tr v-for="r in shown" :key="r.id" :class="{'report-picked': selecting && pickedIds.has(r.id)}">
          <td><div class="report-record">
            <label v-if="selecting" class="report-pick"><input type="checkbox" :checked="pickedIds.has(r.id)"
              :aria-label="'选择 ' + r.name" @change="pick(r, $event.target.checked)"></label>
            <div><a class="record-name" :href="reportVersionPath(r)" target="_blank" rel="noopener">{{r.name}}</a>
            <div v-if="r.access_level !== 'manage'" class="small muted">{{creator(r)}}分享 · {{r.access_level === 'edit' ? '可编辑' : '可查看'}}</div>
            <div class="small muted">{{fmtSize(r.size)}}<span v-if="r.meta?.queries"> · 查询结果 {{queryRows(r)}} 行</span></div>
            <details v-if="r.meta?.warnings?.length" class="small report-warnings"><summary class="btn disclosure">{{r.meta.warnings.length}} 条生成提示</summary>
              <ul><li v-for="(w,i) in r.meta.warnings" :key="i">{{w}}</li></ul></details></div></div></td>
          <td><button type="button" class="btn report-version-button" :aria-label="r.name + '，当前版本 ' + r.revision + '，切换版本'" @click="versions=r">版本 {{r.revision}}</button></td>
          <td data-label="来源模板">{{r.template_name || '—'}} <span class="badge" v-if="r.template_id && r.template_version">v{{r.template_version}}</span></td>
          <td v-if="admin" data-label="创建人">{{creator(r)}}</td><td data-label="版本时间"><time :datetime="r.version_created_at">{{fmtTime(r.version_created_at)}}</time></td>
          <td class="record-actions"><div class="links"><a class="btn link" :href="reportVersionPath(r)" target="_blank" rel="noopener">查看</a><a class="btn link" :href="reportVersionPath(r, '/download')">下载 HTML</a>
            <button type="button" v-if="r.access_level !== 'view'" class="btn link" @click="editing=r">编辑</button>
            <button type="button" v-if="r.access_level === 'manage'" class="btn link" @click="sharing=r">分享</button>
            <button type="button" v-if="r.access_level === 'manage'" class="btn link danger" @click="del(r)">删除</button></div></td>
        </tr></tbody>
      </table></div>
    </section>
    <ReportSharing v-if="sharing" :report="sharing" @close="sharing=null"/>
    <ReportEditor v-if="editing" :report="editing" @close="editing=null" @saved="saved"/>
    <ReportVersions v-if="versions" :report="versions" @close="versions=null" @selected="selected"/>
    <ReportMerge v-if="merging" v-model:reports="picked" @close="merging=false" @downloaded="merged"/>
  </div>`,
  setup() {
    const reports = useCollection('/api/reports');
    const kw = ref(''), sharing = ref(null), editing = ref(null), versions = ref(null);
    const selecting = ref(false), picked = ref([]), merging = ref(false), mergeButton = ref(null);
    const pickedIds = computed(() => new Set(picked.value.map(r => r.id)));
    const shown = computed(() => reports.list.value.filter(r => matches(kw.value, r.name, r.template_name, r.creator_name, r.created_by)));
    const queryRows = r => Object.values(r.meta.queries).reduce((total, q) => total + (q.rows || 0), 0);
    watch(reportRevision, reports.load);
    async function del(r) {
      if (!await askConfirm({ title: '删除报告', message: '确认删除报告「' + r.name + '」及其所有版本？', confirmText: '删除' })) return;
      await run(() => api('/api/reports/' + r.id, { method: 'DELETE' }), '已删除');
      reports.load();
    }
    function saved() { editing.value = null; reportRevision.value++; }
    function selected() { versions.value = null; reportRevision.value++; }
    function toggleMerge() {
      selecting.value = !selecting.value;
      if (!selecting.value) picked.value = [];
    }
    function pick(report, checked) {
      // 固定勾选时的版本，搜索和列表刷新不会静默换成另一个版本。
      if (checked) picked.value.push(report);
      else picked.value = picked.value.filter(r => r.id !== report.id);
    }
    function merged() {
      merging.value = false;
      toggleMerge();
      nextTick(() => mergeButton.value?.focus());
      toast('合并报告已生成', 'ok');
    }
    // 从独立的报告页返回时刷新该账号刚切换或保存的版本。
    onMounted(() => window.addEventListener('focus', reports.load));
    onBeforeUnmount(() => window.removeEventListener('focus', reports.load));
    return { ...reports, admin: computed(isAdmin), creator, shown, kw, sharing, editing, versions, saved, selected, del,
      selecting, picked, pickedIds, merging, mergeButton, toggleMerge, pick, merged,
      fmtTime, fmtSize, queryRows, icon, reportVersionPath };
  }
};

const ReportViewer = {
  components: { ReportEditor, ReportVersions, ConfirmDialog }, props: ['reportId', 'initialVersion'],
  template: `<main class="report-viewer" :aria-busy="loading">
    <header class="report-viewer-bar">
      <div class="report-viewer-heading"><a class="btn link" :href="appPath('/#reports')">报告库</a><h1>{{report?.name || '报告'}}</h1></div>
      <div v-if="report" class="links">
        <button type="button" class="btn report-version-button" :aria-label="'当前版本 ' + report.revision + '，切换版本'" @click="versions=true">版本 {{report.revision}}</button>
        <a class="btn" :href="reportVersionPath(report, '/download')">下载 HTML</a>
        <button v-if="report.access_level !== 'view'" type="button" class="btn primary" @click="editing=true">编辑报告</button>
      </div>
    </header>
    <p v-if="loading" class="collection-state" role="status">正在加载报告…</p>
    <div v-else-if="error" class="collection-state" role="alert">{{error}} <button type="button" class="btn link" @click="load">重试</button></div>
    <iframe v-else-if="report" :key="report.revision" class="report-viewer-frame" :title="report.name + ' · 版本 ' + report.revision"
      :src="reportVersionPath(report, '/content')" sandbox="allow-scripts" referrerpolicy="no-referrer"></iframe>
    <ReportVersions v-if="versions" :report="report" @close="versions=false" @selected="selected"/>
    <ReportEditor v-if="editing" :report="report" @close="editing=false" @saved="saved"/>
  </main>
  <div class="toasts" aria-live="polite"><div v-for="t in toasts" :key="t.id" class="toast" :class="t.kind">{{t.msg}}</div></div>
  <ConfirmDialog/>`,
  setup(props) {
    const path = '/api/reports/' + props.reportId;
    const report = ref(null), loading = ref(true), error = ref(''), versions = ref(false), editing = ref(false);
    const displayedVersion = ref(props.initialVersion);
    function apply(next) {
      report.value = next;
      document.title = next.name + ' · 版本 ' + next.revision;
    }
    async function load() {
      loading.value = true;
      error.value = '';
      try { apply(await api(path + (displayedVersion.value ? '?version=' + displayedVersion.value : ''))); }
      catch (e) {
        if (e.status === 401) {
          location.replace(appPath('/auth/iam/login?next=' + encodeURIComponent(location.pathname + location.search)));
          return;
        }
        error.value = e.message;
      } finally { loading.value = false; }
    }
    onMounted(load);
    function selected(next) {
      versions.value = false;
      apply(next);
      displayedVersion.value = next.version_selected ? next.revision : null;
      // 跟随最新版时保留动态链接，固定选择及编辑结果可直接分享具体版本。
      history.replaceState(null, '', next.version_selected ? reportVersionPath(next) : reportPath(next));
    }
    function saved(next) {
      editing.value = false;
      apply(next);
      displayedVersion.value = next.revision;
      history.replaceState(null, '', reportVersionPath(next));
    }
    return { report, loading, error, versions, editing, load, selected, saved, reportVersionPath, appPath, toasts };
  }
};

// ------------------------------------------------------------ 样式规则（面向业务人员的可视化编辑）
const LEVEL_LABEL = { builtin: '内置样式', default: '内置样式', general: '通用规则', scenario: '业务场景规则', unset: '未设置' };
const isColor = v => typeof v === 'string' && /^#[0-9a-f]{3,8}$/i.test(v);
function showVal(t, v) {
  if (v == null) return '—';
  if (t.type === 'font') return `${v.size}px · ${({400: '常规', 500: '中等', 600: '半粗', 700: '粗'})[v.weight] || v.weight}`;
  if (t.type === 'px') return `${v}px`;
  if (t.type === 'bool') return v ? '是' : '否';
  if (t.type === 'choice') return ((t.options || []).find(o => o.value === v) || {}).label || v;
  if (t.type === 'palette') return `${v.length} 种颜色`;
  if (t.type === 'percent') return `${v}%`;
  if (t.type === 'color_auto') return v || '自动';
  return String(v);
}
const TokenControl = {
  props: ['t', 'value', 'readonly'], emits: ['set'],
  template: `<div class="tok-ctl">
    <template v-if="t.type==='color'"><input type="color" :aria-label="t.label" :value="hex(value)" :disabled="readonly" @input="$emit('set', $event.target.value.toUpperCase())">
      <input class="in mono sm" :aria-label="t.label + '（十六进制）'" :value="value" :disabled="readonly" @change="onText($event.target.value)"></template>
    <template v-else-if="t.type==='color_auto'">
      <label class="check small"><input type="checkbox" :checked="!value" :disabled="readonly" @change="$emit('set', $event.target.checked ? '' : '#2168F3')"> 自动</label>
      <template v-if="value"><input type="color" :aria-label="t.label" :value="hex(value)" :disabled="readonly" @input="$emit('set', $event.target.value.toUpperCase())">
        <input class="in mono sm" :aria-label="t.label + '（十六进制）'" :value="value" :disabled="readonly" @change="onText($event.target.value)"></template>
      <span v-else class="muted small">{{t.desc}}</span></template>
    <template v-else-if="t.type==='number'"><input class="in sm" :aria-label="t.label" type="number" :step="t.step || 0.1" :min="t.min" :max="t.max" :value="value" :disabled="readonly" @change="$emit('set', +$event.target.value)"><span class="muted small">{{t.min}} ~ {{t.max}}</span></template>
    <template v-else-if="t.type==='percent'"><input type="range" :aria-label="t.label" min="0" max="40" :value="value" :disabled="readonly" @input="$emit('set', +$event.target.value)"><span class="small mono">{{value}}%</span></template>
    <template v-else-if="t.type==='palette'">
      <div class="palette-editor">
        <div class="pal">
          <span v-for="(c,i) in value" class="pal-chip"><input type="color" :aria-label="t.label + ' · 颜色 ' + (i + 1)" :value="hex(c)" :disabled="readonly" @input="setPal(i,$event.target.value.toUpperCase())">
            <button type="button" v-if="!readonly && value.length>2" class="btn x danger" :aria-label="'移除颜色 ' + (i + 1)" @click="delPal(i)" v-html="icon('close')"></button></span>
        </div>
        <button type="button" v-if="!readonly && value.length<12" class="btn sm" @click="addPal"><span class="ico" v-html="icon('plus')"></span>添加颜色</button>
      </div>
    </template>
    <template v-else-if="t.type==='font'">
      <select class="in sm" :aria-label="t.label + ' · 字号'" :value="value.size" :disabled="readonly" @change="$emit('set', {size:+$event.target.value, weight:value.weight})"><option v-for="n in sizes" :value="n">{{n}}px</option></select>
      <select class="in sm" :aria-label="t.label + ' · 字重'" :value="value.weight" :disabled="readonly" @change="$emit('set', {size:value.size, weight:+$event.target.value})"><option v-for="w in weights" :value="w[0]">{{w[1]}}</option></select>
      <span class="font-sample" :style="{fontSize:value.size+'px', fontWeight:value.weight}">{{t.sample}}</span></template>
    <template v-else-if="t.type==='px'"><input class="in sm" :aria-label="t.label" type="number" min="0" :value="value" :disabled="readonly" @change="$emit('set', +$event.target.value)"><span class="muted small">px</span>
      <i class="px-bar" :style="{width: Math.min(160, value) + 'px'}" v-if="value < 999"></i><span class="muted small" v-else>全圆</span></template>
    <template v-else-if="t.type==='choice'"><div class="seg" role="group" :aria-label="t.label"><button v-for="o in t.options" type="button" class="btn toggle" :aria-pressed="value===o.value" :disabled="readonly" @click="$emit('set', o.value)">{{o.label}}</button></div></template>
    <template v-else-if="t.type==='bool'"><label class="check"><input type="checkbox" :checked="value" :disabled="readonly" @change="$emit('set', $event.target.checked)"> 显示</label></template>
    <template v-else-if="t.type==='code'"><textarea class="in code" :aria-label="t.label" rows="3" :value="value" :disabled="readonly" @change="$emit('set', $event.target.value)"></textarea></template>
    <template v-else><input class="in sm" :aria-label="t.label" :value="value" :disabled="readonly" @change="$emit('set', $event.target.value)" style="flex:1"></template>
  </div>`,
  setup(props, { emit }) {
    const sizes = [12, 13, 14, 15, 16, 18, 20, 22, 24, 26, 28, 32, 36, 40, 44, 48, 56, 64, 72];
    const weights = [[300, '细'], [400, '常规'], [500, '中等'], [600, '半粗'], [700, '粗'], [800, '特粗']];
    const hex = v => { v = String(v || '#000000'); return /^#[0-9a-f]{6}$/i.test(v) ? v : /^#[0-9a-f]{3}$/i.test(v) ? '#' + v.slice(1).split('').map(x => x + x).join('') : '#000000'; };
    function onText(v) { v = v.trim(); if (isColor(v)) emit('set', v.toUpperCase()); else toast('请输入 #RRGGBB 格式的颜色', 'err'); }
    function setPal(i, c) { const a = props.value.slice(); a[i] = c; emit('set', a); }
    function delPal(i) { const a = props.value.slice(); a.splice(i, 1); emit('set', a); }
    function addPal() { emit('set', props.value.concat([props.value[props.value.length - 1] || '#2168F3'])); }
    return { sizes, weights, hex, onText, setPal, delPal, addPal, icon };
  }
};
const TokenGroups = {
  components: { TokenControl },
  props: ['meta', 'effective', 'sources', 'own', 'readonly', 'showAdvanced'], emits: ['set', 'reset'],
  template: `<div>
    <div class="type-nav" role="group" aria-label="样式类型">
      <button v-for="g in groups" :key="g.key" type="button" class="btn toggle" :aria-pressed="cur===g.key"
        :aria-label="ownCount(g) ? g.label + '，已调整 ' + ownCount(g) + ' 项' : g.label" @click="cur=g.key">{{g.label}}<b v-if="ownCount(g)" aria-hidden="true">{{ownCount(g)}}</b></button>
    </div>
    <div v-for="g in groups" :key="g.key" v-show="cur===g.key" class="tok-group">
      <div class="tok-group-head"><b>{{g.label}}</b><span class="muted small">{{g.desc}}</span>
        <span class="spacer"></span><span class="badge blue" v-if="ownCount(g)">本规则调整 {{ownCount(g)}} 项</span></div>
      <div v-for="t in g.tokens" class="tok-row" :class="{own: own && t.key in own}">
        <div class="tok-label"><b>{{t.label}} <span v-if="t.builtin" class="badge tiny">内置</span></b><span v-if="t.desc && t.type!=='color_auto'">{{t.desc}}</span></div>
        <TokenControl v-if="effective[t.key] !== undefined && effective[t.key] !== null" :t="t" :value="effective[t.key]" :readonly="readonly" @set="v => $emit('set', t.key, v)"/>
        <div v-else class="tok-ctl"><span class="muted small">未设置 · 由智能体在生成报告时决定</span></div>
        <!-- 设置与恢复使用同一列，状态文字和可点击操作保持清楚的间隔。 -->
        <div v-if="own && t.key in own" class="tok-src">
          <span class="tok-own-label">本规则设置</span>
          <button type="button" v-if="!readonly" class="btn link" @click="$emit('reset', t.key)">{{t.builtin || inheritedSet(t.key) ? '恢复继承' : '取消设置'}}</button>
        </div>
        <div v-else-if="effective[t.key] !== undefined && effective[t.key] !== null" class="tok-src">
          <span class="src" :class="sources[t.key] && sources[t.key].level">{{srcText(t.key)}}</span>
        </div>
        <div v-else-if="!readonly" class="tok-src">
          <button type="button" class="btn link" @click="$emit('set', t.key, initial(t))">设置此项</button>
        </div>
      </div>
    </div>
  </div>`,
  setup(props) {
    // 按报告页面的组成部分分类；“高级”只在勾选显示高级选项时出现
    const groups = computed(() => props.meta.groups.filter(g => props.showAdvanced || g.key !== 'advanced'));
    const cur = ref(groups.value[0]?.key);
    // 关闭高级选项时回到首个可见类别，避免编辑区留在已隐藏的选项卡。
    watch(groups, visible => { if (!visible.some(g => g.key === cur.value)) cur.value = visible[0]?.key; });
    const ownCount = g => props.own ? g.tokens.filter(t => t.key in props.own).length : 0;
    const srcText = k => {
      const s = props.sources && props.sources[k];
      if (!s || s.level === 'unset') return '未设置';
      if (s.level === 'builtin' || s.level === 'default') return '内置样式';
      return (props.own ? '继承：' : '') + `${LEVEL_LABEL[s.level]}「${s.rule}」`;
    };
    // 继承链上（内置样式或其他规则）是否已有设置：决定“恢复继承”还是“取消设置”
    const inheritedSet = k => { const s = props.sources && props.sources[k]; return !!s && s.level !== 'unset'; };
    // “设置此项”的初始值：编辑器建议值（不是内置样式）
    const initial = t => {
      const v = props.meta.suggested ? props.meta.suggested[t.key] : undefined;
      if (v !== undefined && v !== '') return JSON.parse(JSON.stringify(v));
      return t.type === 'color_auto' ? '#2168F3' : t.type === 'bool' ? true : t.options ? t.options[0].value : '';
    };
    return { groups, cur, ownCount, srcText, inheritedSet, initial };
  }
};
const Styles = {
  components: { TokenGroups },
  template: `<div>
    <template v-if="!form && viewSc === null">
      <div class="page-head"><div><h1>报告样式</h1><p>设置报告外观、状态标记和数字格式，并预览效果。</p></div>
        <div class="actions"><button type="button" class="btn primary" :disabled="!meta || (!admin && !scenarios.length)" @click="edit()">{{admin ? '新建样式' : '新建我的场景样式'}}</button></div></div>
      <section class="card card-pad style-overview" aria-labelledby="style-preview-title">
        <div><h2 id="style-preview-title">预览当前样式</h2>
          <p>同一项设置优先采用个人场景样式，其次是通用样式和系统默认。</p></div>
        <div class="style-preview-actions"><label class="field"><span>预览范围</span>
          <select class="in" v-model="viewChoice"><option value="">通用样式</option><option v-for="sc in scenarios" :key="sc" :value="sc">{{sc}}</option></select></label>
          <button type="button" class="btn" :disabled="!meta" @click="openView(viewChoice)">查看生效效果</button></div>
      </section>
      <div class="msg err" v-if="styleError" role="alert">{{styleError}} <button type="button" class="btn link" @click="loadStyles">重试</button></div>
      <div class="collection-state" v-else-if="styleLoading" role="status">正在加载报告样式…</div>
      <div v-else>
      <div v-for="grp in grouped" :key="grp.title" class="style-section">
        <h2 class="grp-title">{{grp.title}} <span class="muted small">{{grp.sub}}</span></h2>
        <div class="rule-grid">
          <div v-for="r in grp.rules" class="card rule-card" :class="{off: !r.enabled}">
            <div class="rule-head"><b>{{r.name}}</b><span class="spacer"></span>
              <span class="badge blue" v-if="r.scope==='general'">管理员配置</span>
              <span class="badge amber" v-else-if="admin">{{ownerText(r)}}</span>
              <span class="badge" :class="r.enabled?'green':''">{{r.enabled?'启用':'停用'}}</span></div>
            <ul class="rule-items"><li v-for="it in r.items.slice(0,4)"><span class="cat">{{catLabel(it.category)}}</span>{{it.text}}</li>
              <li v-if="r.items.length>4" class="muted small">… 共 {{r.items.length}} 条规则</li>
              <li v-if="!r.items.length" class="muted small">没有展示要求</li>
              <li v-if="r.retiredItems.length" class="muted small">{{r.retiredItems.length}} 条旧分析要求已停用</li></ul>
            <div class="rule-foot"><div class="swatches"><span v-for="c in colors(r.theme)" class="swatch" :style="{background:c}"></span></div>
              <span class="small muted">{{typeSummary(r.theme)}}</span>
              <span class="spacer"></span><template v-if="canEdit(r)"><button type="button" class="btn link" @click="edit(r)">编辑</button><button type="button" class="btn link danger" @click="del(r)">删除</button></template>
              <button type="button" v-else class="btn link" @click="edit(r, true)">查看</button></div>
          </div>
          <div v-if="!grp.rules.length" class="empty card">{{grp.empty}}</div>
        </div>
      </div></div>
    </template>

    <template v-else-if="viewSc !== null && !form">
      <div class="page-head"><div><h1>最终效果 · {{viewSc || '通用'}}</h1><p>查看此范围内实际采用的设置及其来源。</p></div>
        <div class="actions"><button type="button" class="btn ghost" @click="closeView">返回样式列表</button></div></div>
      <div v-if="viewError" class="msg err" role="alert">{{viewError}} <button type="button" class="btn link" @click="openView(viewSc)">重试</button></div>
      <div v-else-if="viewLoading" class="collection-state" role="status">正在加载生效样式…</div>
      <div v-else class="style-editor"><div class="pane-left">
        <div class="card card-pad"><h3>展示要求（合并后）</h3>
          <p class="small muted" style="margin:0 0 8px">当前用户明确要求优先于业务场景、通用及内置默认值；同类要求中排在前面的优先。低层条目不能豁免高层要求；智能体仅可决定章节、选图、筛选维度及未指定的装饰样式。</p>
          <ul class="rule-items" v-if="viewRules.length"><li v-for="it in viewRules"><span class="cat">{{it.category_label}}</span>{{it.text}} <span class="src" :class="it.level">{{it.source}}</span></li></ul>
          <p v-else class="muted small">没有指定的展示要求。</p></div>
        <div class="card card-pad" style="margin-top:12px" v-if="eff"><h3>视觉样式</h3><TokenGroups :meta="meta" :effective="eff.theme" :sources="eff.sources" :readonly="true"/></div>
      </div>
      <div class="pane-right"><div class="preview-box"><div class="preview-bar">示例报告预览</div><iframe title="示例报告预览" :srcdoc="previewHtml"></iframe></div></div></div>
    </template>

    <template v-else-if="form">
      <div class="page-head"><div><h1>{{form.readonly ? '查看样式规则' : form.id ? '编辑样式规则' : '新建样式规则'}}</h1>
          <p>{{form.readonly ? '通用规则由管理员配置，你可以查看；如需个人调整，请新建“我的场景样式”' : '先确定适用范围，再填写展示要求或调整视觉样式。'}}</p></div>
        <div class="actions"><button type="button" class="btn ghost" :disabled="saving" @click="close">返回样式列表</button>
          <button type="button" class="btn style-preview-jump" @click="jumpToPreview">查看示例预览</button>
          <button type="button" v-if="!form.readonly" class="btn primary" :disabled="saving" @click="save">{{saving ? '正在保存…' : '保存样式'}}</button></div></div>
      <fieldset class="plain-fs" :disabled="form.readonly">
      <div class="card card-pad meta-row">
        <div class="field"><label for="style-name">样式名称</label><input id="style-name" class="in" v-model="form.name" placeholder="如：零售销售报告风格"></div>
        <div class="field" v-if="admin"><label>适用范围</label><div class="seg" role="group" aria-label="适用范围"><button type="button" class="btn toggle" :aria-pressed="form.scope==='general'" @click="form.scope='general'">通用规则（所有报告）</button><button type="button" class="btn toggle" :aria-pressed="form.scope==='scenario'" :disabled="!personalStyle" @click="form.scope='scenario'">个人场景样式</button></div></div>
        <div class="field" v-else><label>适用范围</label><div class="small" style="line-height:40px">{{form.scope==='general' ? '通用规则（管理员配置）' : '我的业务场景样式（只对我的报告生效）'}}</div></div>
        <div class="field" v-if="form.scope==='scenario'"><label for="style-scenario">业务场景</label><select id="style-scenario" class="in" v-model="form.business_scenario"><option value="" disabled>选择场景</option><option v-if="form.business_scenario && !scenarios.includes(form.business_scenario)" :value="form.business_scenario">{{form.business_scenario}}</option><option v-for="s in scenarios" :value="s">{{s}}</option></select></div>
        <div class="field" v-else><label for="preview-scenario">预览场景</label><select id="preview-scenario" class="in" v-model="previewSc"><option value="">不区分场景</option><option v-for="s in scenarios" :value="s">{{s}}</option></select></div>
        <div class="field"><label>状态</label><label class="check" style="height:40px"><input type="checkbox" v-model="form.enabled"> 启用</label></div>
      </div>
      </fieldset>
      <div class="msg err" v-if="saveError" role="alert">{{saveError}}</div>
      <div class="style-editor">
        <div class="pane-left">
          <div class="subtabs style-mode" role="group" aria-label="样式编辑类别"><button type="button" class="btn toggle" :aria-pressed="tab==='items'" @click="tab='items'">展示要求（{{form.items.length}}）</button><button type="button" class="btn toggle" :aria-pressed="tab==='theme'" @click="tab='theme'">视觉样式（调整 {{Object.keys(form.theme).length}} 项）</button></div>
          <fieldset class="plain-fs" :disabled="form.readonly" v-show="tab==='items'">
            <p class="small muted" style="margin:0 0 10px">章节安排、选图建议和筛选条件可写在「业务场景」的分析思路中。</p>
            <div v-for="c in meta.categories" class="cat-block">
              <div class="cat-head"><b>{{c.label}}</b><span class="muted small">{{c.hint}}</span></div>
              <div v-for="(it,i) in form.items" v-show="it.category===c.key" class="item-row">
                <textarea class="in" rows="1" v-model="it.text" @input="grow($event)" :aria-label="c.label + '要求'" :placeholder="'写一条' + c.label + '要求'"></textarea>
                <select class="in sm" v-model="it.category" aria-label="要求类别"><option v-for="cc in meta.categories" :value="cc.key">{{cc.label}}</option></select>
                <button type="button" class="btn link danger" @click="form.items.splice(i,1)">删除</button></div>
              <div class="cat-actions" v-if="!form.readonly"><button type="button" class="btn sm" @click="add(c.key)">+ 添加</button>
                <button type="button" v-for="ex in c.examples" class="btn ex-chip" @click="add(c.key, ex)" :title="ex">示例：{{ex}}</button></div>
            </div>
          </fieldset>
          <details v-if="form.retiredItems.length" v-show="tab==='items'" class="card card-pad">
            <summary class="btn disclosure">已停用的分析要求（{{form.retiredItems.length}}）</summary>
            <ul class="rule-items"><li v-for="(it, i) in form.retiredItems" :key="i"><span class="cat">{{meta.retired_analysis_categories[it.category]}}</span>{{it.text}}</li></ul>
          </details>
          <div v-show="tab==='theme'">
            <div class="style-theme-intro">
              <p class="small muted">调整需要覆盖的样式项。其余设置沿用{{form.scope==='scenario' ? '通用规则或内置经营报告样式' : '内置经营报告样式'}}。</p>
              <label class="check small"><input type="checkbox" v-model="advanced"> 显示高级选项</label>
            </div>
            <TokenGroups v-if="inherited" :meta="meta" :effective="effective" :sources="inherited.sources" :own="form.theme" :readonly="form.readonly" :show-advanced="advanced" @set="setTok" @reset="resetTok"/>
          </div>
        </div>
        <div class="pane-right" ref="previewPane" tabindex="-1"><div class="preview-box"><div class="preview-bar">示例报告预览 <span class="muted small">{{previewing ? '更新中…' : '改动后自动更新'}}</span></div><iframe title="示例报告预览" :srcdoc="previewHtml"></iframe></div></div>
      </div>
    </template>
    <!-- 只读展示服务端的跨场景规则，展示默认值按样式优先级应用。 -->
    <details v-if="meta && meta.reporting_rules" class="card card-pad" style="margin-top:16px">
      <summary class="btn disclosure">内置报告规则（{{meta.reporting_rules.length}} 条）</summary>
      <p class="small muted">事实与交付要求适用于所有报告。展示默认值按用户要求、业务场景规则和通用规则的优先级应用。</p>
      <ul class="rule-items"><li v-for="rule in meta.reporting_rules" :key="rule.id"><span class="cat">{{rule.label}}</span>{{rule.text}}</li></ul>
    </details>
  </div>`,
  setup() {
    const list = ref([]), scenarios = ref([]), meta = ref(null), form = ref(null), tab = ref('items'), advanced = ref(false);
    const inherited = ref(null), previewHtml = ref(''), previewing = ref(false), previewSc = ref(''), previewPane = ref(null);
    const viewSc = ref(null), viewChoice = ref(''), eff = ref(null), viewRules = ref([]);
    const styleLoading = ref(true), styleError = ref(''), viewLoading = ref(false), viewError = ref('');
    const saving = ref(false), saveError = ref('');
    async function loadStyles() {
      styleLoading.value = true;
      styleError.value = '';
      try {
        const [tokens, names, rules] = await Promise.all([api('/api/style-tokens'), api('/api/scenarios'), api('/api/style-rules')]);
        meta.value = tokens;
        scenarios.value = [...new Set(names)];
        // 旧分析条目只供查阅，编辑和保存请求仅使用生效的展示要求。
        const retired = item => Object.hasOwn(tokens.retired_analysis_categories || {}, item.category);
        list.value = rules.map(rule => ({
          ...rule, items: rule.items.filter(item => !retired(item)), retiredItems: rule.items.filter(retired)
        }));
      } catch (e) { styleError.value = e.message; }
      finally { styleLoading.value = false; }
    }
    onMounted(loadStyles);
    const catLabel = k => ((meta.value && meta.value.categories.find(c => c.key === k)) || {}).label || '其他';
    const colors = t => { t = t || {}; return Object.entries(t).flatMap(([k, v]) => Array.isArray(v) ? v : isColor(v) ? [v] : []).slice(0, 8); };
    // 卡片上按类型概括本规则调整了哪些部分，例如“调整：卡片 2 项 · 数据配色 1 项”
    const typeSummary = t => {
      const keys = Object.keys(t || {});
      if (!keys.length || !meta.value) return '沿用上一级视觉样式';
      const parts = meta.value.groups.map(g => [g.label, g.tokens.filter(x => keys.includes(x.key)).length]).filter(x => x[1]);
      return parts.length ? '调整：' + parts.map(x => `${x[0]} ${x[1]} 项`).join(' · ') : '沿用上一级视觉样式';
    };
    const admin = computed(isAdmin);
    const meNo = () => (state.me && state.me.emp_no) || null;
    const personalStyle = computed(() => !!meNo() && scenarios.value.length > 0);
    const canEdit = r => (admin.value && (r.scope === 'general' || !!meNo())) || (r.scope === 'scenario' && !!meNo() && r.created_by === meNo());
    const ownerText = r => r.created_by ? `${r.creator_name || r.created_by}的样式` : '无归属';
    const grouped = computed(() => {
      const g = [{ title: '通用规则', sub: admin.value ? '对所有报告生效' : '管理员配置，对所有报告生效（只读）', empty: '暂无通用规则，当前使用内置经营报告样式',
        rules: list.value.filter(r => r.scope === 'general') }];
      const scs = Array.from(new Set(scenarios.value.concat(list.value.filter(r => r.scope === 'scenario').map(r => r.business_scenario))));
      scs.forEach(sc => g.push({ title: '业务场景：' + sc, sub: admin.value ? '各人的个人样式，只对其本人的报告生效' : '我的样式，只对我生成的报告生效，优先于通用规则',
        empty: admin.value ? '暂无人配置' : '尚未配置，点击右上角“新建我的场景样式”', rules: list.value.filter(r => r.scope === 'scenario' && r.business_scenario === sc) }));
      return g;
    });
    const scopeScenario = () => form.value.scope === 'scenario' ? form.value.business_scenario : previewSc.value;
    const effective = computed(() => {
      if (!inherited.value) return {};
      const out = { ...inherited.value.theme };
      Object.assign(out, form.value.theme);
      return out;
    });
    let timer = null, inheritedRequest = 0, previewRequest = 0;
    // 更换场景或继续调整时，仅采用最后一次请求的预览和继承值。
    async function loadInherited() {
      const request = ++inheritedRequest;
      inherited.value = null;
      if (!form.value) return;
      try {
        const value = await api('/api/style-rules/effective', { method: 'POST', body: { scenario: scopeScenario() || null, exclude_id: form.value.id || null } });
        if (request === inheritedRequest) inherited.value = value;
      } catch (e) { if (request === inheritedRequest) toast(e.message, 'err'); }
    }
    function schedulePreview() {
      clearTimeout(timer);
      const request = ++previewRequest;
      previewing.value = !!form.value;
      if (!form.value) return;
      timer = setTimeout(async () => {
        try {
          const html = await api('/api/style-rules/preview', { method: 'POST', body: {
            scenario: scopeScenario() || null, exclude_id: form.value.id || null,
            draft: { scope: form.value.scope, name: form.value.name, theme: form.value.theme } } });
          if (request === previewRequest) previewHtml.value = html;
        } catch (e) { if (request === previewRequest) toast(e.message, 'err'); }
        finally { if (request === previewRequest) previewing.value = false; }
      }, 450);
    }
    watch(() => form.value && JSON.stringify(form.value.theme), schedulePreview);
    watch(() => form.value && [form.value.id, form.value.scope, form.value.business_scenario, previewSc.value].join('|'), () => { loadInherited(); schedulePreview(); });
    onBeforeUnmount(() => { clearTimeout(timer); inheritedRequest += 1; previewRequest += 1; });
    function legacy(content) {
      return (content || '').split('\n').map(l => /^\s*(?:[-*]|\d+[.、])\s+(.+)$/.exec(l)).filter(Boolean).map(m => ({ category: 'other', text: m[1].replace(/\*\*|`/g, '').trim() }));
    }
    function edit(r, readonly) {
      if (!meta.value) return;
      tab.value = 'items';
      advanced.value = false;
      previewSc.value = '';
      inherited.value = null;
      previewHtml.value = '';
      saveError.value = '';
      if (r) {
        const legacyItems = r.items.length || r.retiredItems.length ? [] : legacy(r.content);
        if (legacyItems.length) toast('已将原有说明拆成展示要求，请核对内容后保存');
        form.value = { id: r.id, name: r.name, scope: r.scope, business_scenario: r.business_scenario || scenarios.value[0] || '',
          enabled: r.enabled, priority: r.priority, items: r.items.map(x => ({ ...x })).concat(legacyItems),
          retiredItems: r.retiredItems, theme: { ...(r.theme || {}) }, readonly: !!readonly };
      } else {
        form.value = { id: null, name: '', scope: personalStyle.value ? 'scenario' : 'general', business_scenario: '', enabled: true, priority: 100, items: [], retiredItems: [], theme: {} };
      }
    }
    function close() { form.value = null; inherited.value = null; loadStyles(); }
    function add(cat, text) { form.value.items.push({ category: cat, text: text || '' }); }
    function grow(e) { e.target.style.height = 'auto'; e.target.style.height = e.target.scrollHeight + 'px'; }
    function setTok(k, v) { form.value.theme = { ...form.value.theme, [k]: v }; }
    function resetTok(k) { const t = { ...form.value.theme }; delete t[k]; form.value.theme = t; }
    async function save() {
      if (saving.value) return;
      const f = form.value;
      saveError.value = '';
      if (!f.name.trim()) { saveError.value = '请填写样式名称。'; return; }
      if (f.scope === 'scenario' && !f.business_scenario) { saveError.value = '请选择要应用此样式的业务场景。'; return; }
      const body = { name: f.name.trim(), scope: f.scope, business_scenario: f.business_scenario, enabled: f.enabled, priority: f.priority,
        items: f.items.filter(i => i.text.trim()), theme: f.theme };
      saving.value = true;
      try {
        await api(f.id ? '/api/style-rules/' + f.id : '/api/style-rules', { method: f.id ? 'PUT' : 'POST', body });
        toast('样式已保存，将应用于新生成的报告', 'ok');
        close();
      } catch (e) { saveError.value = e.message; }
      finally { saving.value = false; }
    }
    async function del(r) {
      if (!await askConfirm({ title: '删除样式规则', message: '确认删除样式「' + r.name + '」？', confirmText: '删除' })) return;
      await run(() => api('/api/style-rules/' + r.id, { method: 'DELETE' }), '已删除');
      loadStyles();
    }
    // 窄屏将预览排在设置之后，入口可直接把滚动位置和键盘焦点移到预览。
    function jumpToPreview() {
      previewPane.value?.scrollIntoView({ block: 'start' });
      previewPane.value?.focus({ preventScroll: true });
    }
    let viewRequest = 0;
    function closeView() { viewRequest += 1; viewSc.value = null; }
    async function openView(sc) {
      const request = ++viewRequest;
      viewSc.value = sc;
      viewLoading.value = true;
      viewError.value = '';
      try {
        const [e, rs, html] = await Promise.all([
          api('/api/style-rules/effective', { method: 'POST', body: { scenario: sc || null } }),
          api('/api/style-rules/resolve' + (sc ? '?scenario=' + encodeURIComponent(sc) : '')),
          api('/api/style-rules/preview', { method: 'POST', body: { scenario: sc || null } })]);
        if (request !== viewRequest) return;
        eff.value = e;
        viewRules.value = rs.specified.items;
        previewHtml.value = html;
      } catch (e) { if (request === viewRequest) viewError.value = e.message; }
      finally { if (request === viewRequest) viewLoading.value = false; }
    }
    return { typeSummary, admin, canEdit, personalStyle, ownerText, scenarios, meta, form, tab, advanced, inherited, effective, previewHtml, previewing, previewSc, viewSc, viewChoice, eff, viewRules,
      catLabel, colors, grouped, edit, close, add, grow, setTok, resetTok, save, del, openView, closeView, styleLoading, styleError, loadStyles, viewLoading, viewError, saving, saveError, previewPane, jumpToPreview };

  }
};

// ------------------------------------------------------------ 员工与授权
const Perms = {
  components: { Drawer },
  template: `<div>
    <div class="page-head"><div><h1>员工与授权</h1><p>管理普通用户和管理员，为员工或数据角色授权，并查询有效权限。</p></div></div>
    <div class="subtabs" role="group" aria-label="员工与授权功能"><button v-for="t in tabs" :key="t[0]" type="button" class="btn toggle" :aria-pressed="tab===t[0]" @click="tab=t[0]">{{t[1]}}</button></div>
    <div v-if="loading" class="card collection-state" role="status">正在加载员工和授权…</div>
    <div v-else-if="loadError" class="card collection-state" role="alert">员工和授权加载失败。<button type="button" class="btn link" @click="load">重试</button></div>
    <template v-else>

    <template v-if="tab==='emp'">
      <div class="actions" style="margin-bottom:12px"><button type="button" class="btn primary" @click="editEmp()">新增员工</button></div>
      <div class="card scroll-x" tabindex="0" aria-label="员工表格"><table class="tbl record-table" role="table"><thead><tr><th scope="col">工号</th><th scope="col">姓名</th><th scope="col">部门</th><th scope="col">数据授权角色</th><th scope="col">系统身份</th><th scope="col">注册来源</th><th scope="col">状态</th><th scope="col">操作</th></tr></thead>
        <tbody><tr v-for="e in emps"><td class="mono">{{e.emp_no}}</td><td data-label="姓名">{{e.name}}</td><td data-label="部门">{{e.department}}</td>
          <td class="record-wide" data-label="数据授权角色"><span v-for="r in e.roles" class="badge" style="margin-right:4px">{{r}}</span><span v-if="e.direct_grants" class="small muted">+{{e.direct_grants}} 项直授</span></td>
          <td data-label="系统身份"><span class="badge" :class="e.is_admin?'red':''">{{e.is_admin?'管理员（全部数据）':'普通用户'}}</span></td>
          <td class="small" data-label="注册来源">{{({manual:'管理员登记', iam_login:'IAM 登录', mcp:'IAM 接入'})[e.registered_via] || e.registered_via}}</td>
          <td data-label="状态"><span class="dot" :class="{on:e.enabled}"></span>{{e.enabled?'启用':'停用'}}</td>
          <td class="record-actions"><div class="links"><button type="button" class="btn link" @click="checkEmp(e.emp_no)">查看权限</button>
            <button type="button" class="btn link" @click="editEmp(e)">编辑</button><button type="button" class="btn link danger" @click="delEmp(e)">删除</button></div></td></tr>
          <tr v-if="!emps.length"><td colspan="8" class="empty">暂无员工</td></tr></tbody></table></div>
    </template>

    <template v-if="tab==='grant'">
      <div class="card card-pad" style="margin-bottom:14px">
        <h3>新增授权</h3>
        <div class="row3 grant-fields">
          <div class="field"><label>授权对象</label><div class="seg" role="group" aria-label="授权对象"><button type="button" class="btn toggle" :aria-pressed="g.subject_type==='role'" @click="g.subject_type='role'">角色</button><button type="button" class="btn toggle" :aria-pressed="g.subject_type==='employee'" @click="g.subject_type='employee'">员工</button></div></div>
          <div class="field"><label for="grant-subject">{{g.subject_type==='role'?'角色编码':'员工工号'}}</label>
            <input id="grant-subject" v-if="g.subject_type==='role'" class="in" v-model="g.subject" list="role-list" placeholder="如 user（默认数据角色）">
            <select v-else id="grant-subject" class="in" v-model="g.subject"><option value="" disabled>选择员工</option><option v-for="e in emps" :value="e.emp_no">{{e.emp_no}} {{e.name}}</option></select>
            <datalist id="role-list"><option v-for="r in roles" :value="r"></option></datalist></div>
          <div class="field"><label for="grant-dataset">业务场景（数据集）</label><select id="grant-dataset" class="in" v-model="g.dataset_id" @change="g.tables=['*']"><option :value="null" disabled>选择</option><option v-for="d in datasets" :value="d.id">{{d.business_scenario}}</option></select></div>
        </div>
        <div class="field" v-if="dsTables.length"><label>数据表</label>
          <div style="display:flex;flex-wrap:wrap;gap:6px 18px">
            <label class="check"><input type="checkbox" :checked="g.tables.includes('*')" @change="g.tables = $event.target.checked ? ['*'] : []"> <b>全部表（含今后新增）</b></label>
            <label v-for="t in dsTables" class="check" :style="g.tables.includes('*')?'opacity:.45':''"><input type="checkbox" :disabled="g.tables.includes('*')" :checked="g.tables.includes(t.table_name)" @change="toggleT(t.table_name)"> <span class="mono">{{t.table_name}}</span></label>
          </div></div>
        <button type="button" class="btn primary" @click="addGrant" :disabled="!g.subject || !g.dataset_id || !g.tables.length">授权</button>
      </div>
      <div class="card scroll-x" tabindex="0" aria-label="表授权表格"><table class="tbl record-table" role="table"><thead><tr><th scope="col">授权对象</th><th scope="col">业务场景</th><th scope="col">数据表</th><th scope="col">授权时间</th><th scope="col">操作</th></tr></thead>
        <tbody><tr v-for="x in grants"><td><span class="badge" :class="x.subject_type==='role'?'blue':'amber'">{{x.subject_type==='role'?'角色':'员工'}}</span> <span class="mono">{{x.subject}}</span> {{x.employee_name || ''}}</td>
          <td data-label="业务场景">{{x.business_scenario}}</td><td class="mono" data-label="数据表">{{x.table_name==='*' ? '全部表' : x.table_name}}</td><td class="small" data-label="授权时间">{{fmtTime(x.created_at)}}</td>
          <td class="record-actions"><button type="button" class="btn link danger" @click="delGrant(x)">撤销</button></td></tr>
          <tr v-if="!grants.length"><td colspan="5" class="empty">暂无授权</td></tr></tbody></table></div>
    </template>

    <template v-if="tab==='check'">
      <div class="card card-pad">
        <div class="actions"><select class="in" aria-label="选择要查询的员工" v-model="chk" @change="chkRes=null; checkError=''" :disabled="checking" style="width:min(260px,100%)"><option value="" disabled>选择员工</option><option v-for="e in emps" :value="e.emp_no">{{e.emp_no}} {{e.name}}</option></select><button type="button" class="btn primary" @click="checkEmp(chk)" :disabled="!chk || checking">{{checking ? '正在查询…' : '查询有效权限'}}</button></div>
        <div v-if="checkError" class="msg err check-result" role="alert">{{checkError}}</div>
        <div v-else-if="chkRes" class="check-result" aria-live="polite">
          <div class="msg warn" v-if="!chkRes.registered">该工号未登记或已停用：HTTP 和 MCP 无法访问业务数据。</div>
          <div class="msg ok" v-else-if="chkRes.all">管理员：可访问全部业务场景与数据表。</div>
          <div v-for="d in chkRes.datasets" class="hit" v-if="!chkRes.all"><h4>{{d.business_scenario}} <span class="score">dataset_id={{d.dataset_id}}</span></h4>
            <div><span v-for="t in d.tables" class="badge" style="margin:2px 6px 2px 0"><span class="mono">{{t}}</span></span></div></div>
          <div v-if="chkRes.registered && !chkRes.all && !chkRes.datasets.length" class="muted">没有任何数据表权限。</div>
        </div>
      </div>
    </template>
    </template>

    <Drawer v-if="emp" :title="emp._new ? '新增员工' : '编辑员工 ' + emp.emp_no" :busy="empSaving" @close="emp=null">
      <fieldset class="plain-fs" :disabled="empSaving">
        <div class="row2"><div class="field"><label for="emp-no">工号</label><input id="emp-no" class="in mono" v-model.trim="emp.emp_no" :disabled="!emp._new"></div>
          <div class="field"><label for="emp-name">姓名</label><input id="emp-name" class="in" v-model="emp.name"></div></div>
        <div class="field"><label for="emp-identity">系统身份</label>
          <select id="emp-identity" class="in" v-model="emp.is_admin" aria-describedby="emp-identity-hint">
            <option :value="false">普通用户</option><option :value="true">管理员</option>
          </select>
          <div id="emp-identity-hint" class="hint">启用的管理员可访问全部数据，并管理系统设置和员工授权。</div>
        </div>
        <div class="row2"><div class="field"><label for="emp-dept">部门</label><input id="emp-dept" class="in" v-model="emp.department"></div>
          <div class="field"><label for="emp-roles">数据授权角色</label><input id="emp-roles" class="in" v-model="emp.rolesTxt" aria-describedby="emp-roles-hint">
            <div id="emp-roles-hint" class="hint">用于分配数据表权限，多个角色用逗号分隔。</div></div></div>
        <label class="check"><input type="checkbox" v-model="emp.enabled"> 启用账号</label>
      </fieldset>
      <div v-if="empSaveError" class="msg err" role="alert" style="margin-top:12px">保存失败：{{empSaveError}}</div>
      <template #foot><button type="button" class="btn" :disabled="empSaving" @click="emp=null">取消</button>
        <button type="button" class="btn primary" :disabled="empSaving" @click="saveEmp">{{empSaving ? '保存中…' : '保存'}}</button></template>
    </Drawer>
  </div>`,
  setup() {
    const tabs = [['emp', '员工'], ['grant', '表授权'], ['check', '权限查询']];
    const tab = ref('emp'), emps = ref([]), grants = ref([]), datasets = ref([]), emp = ref(null);
    const loading = ref(true), loadError = ref(''), checking = ref(false), checkError = ref('');
    const empSaving = ref(false), empSaveError = ref('');
    const g = reactive({ subject_type: 'role', subject: '', dataset_id: null, tables: ['*'] });
    const chk = ref(''), chkRes = ref(null), dsDetail = ref(null);
    const roles = computed(() => Array.from(new Set(emps.value.flatMap(e => e.roles || []))));
    const dsTables = computed(() => dsDetail.value ? dsDetail.value.tables : []);
    watch(() => g.dataset_id, async id => { dsDetail.value = id ? await api('/api/datasets/' + id) : null; });
    async function load() {
      loading.value = true; loadError.value = '';
      try {
        [emps.value, grants.value, datasets.value] = await Promise.all([api('/api/employees'), api('/api/grants'), api('/api/datasets')]);
      } catch (e) { loadError.value = e.message; }
      finally { loading.value = false; }
    }
    onMounted(load);
    function editEmp(e) {
      empSaveError.value = '';
      emp.value = e ? { ...e, rolesTxt: (e.roles || []).join(', ') } : { _new: true, emp_no: '', name: '', department: '', rolesTxt: '', is_admin: false, enabled: true };
    }
    async function saveEmp() {
      if (empSaving.value) return;
      const e = emp.value, previous = emps.value.find(row => row.emp_no === e.emp_no);
      const rolesTxt = (previous?.roles || []).join(', ');
      const body = { emp_no: e.emp_no };
      // 系统身份独立提交；未编辑的数据角色和员工资料由服务端保留。
      for (const key of ['name', 'department', 'is_admin', 'enabled']) {
        if (e._new || e[key] !== previous?.[key]) body[key] = e[key];
      }
      if (e._new || e.rolesTxt !== rolesTxt) body.roles = e.rolesTxt.split(/[,，\s]+/).filter(Boolean);
      empSaving.value = true; empSaveError.value = '';
      try {
        const path = e._new ? '/api/employees' : '/api/employees/' + encodeURIComponent(e.emp_no);
        const saved = await api(path, { method: e._new ? 'POST' : 'PUT', body });
        // 用保存结果更新列表，保留更新接口不返回的直授计数。
        if (previous) Object.assign(previous, saved);
        const identityChanged = !e._new && 'is_admin' in body;
        toast(identityChanged ? (saved.is_admin ? '已设为管理员' : '已设为普通用户') : '已保存', 'ok');
        emp.value = null;
        if (saved.emp_no === state.me.emp_no) {
          Object.assign(state.me, { is_admin: saved.enabled && saved.is_admin, registered: saved.enabled, roles: saved.roles });
        }
        if (e._new) load().catch(err => toast(err.message, 'err'));
      } catch (err) { empSaveError.value = err.message; }
      finally { empSaving.value = false; }
    }
    async function delEmp(e) {
      if (!await askConfirm({ title: '删除员工', message: `确认删除员工 ${e.emp_no}？其直接授权和管理台会话会删除。`, confirmText: '删除' })) return;
      await run(() => api('/api/employees/' + encodeURIComponent(e.emp_no), { method: 'DELETE' }), '已删除'); load();
    }
    function toggleT(t) { const i = g.tables.indexOf(t); if (i >= 0) g.tables.splice(i, 1); else g.tables.push(t); }
    async function addGrant() { const r = await run(() => api('/api/grants', { method: 'POST', body: { ...g } })); toast(`新增 ${r.added} 条授权`, 'ok'); load(); }
    async function delGrant(x) {
      if (!await askConfirm({ title: '撤销授权', message: '确认撤销该数据表授权？', confirmText: '撤销' })) return;
      await run(() => api('/api/grants/' + x.id, { method: 'DELETE' }), '已撤销'); load();
    }
    async function checkEmp(no) {
      if (checking.value) return;
      // 查询切换员工时清除旧结果，避免把上一位员工的权限显示在新工号下。
      tab.value = 'check'; chk.value = no; chkRes.value = null; checkError.value = ''; checking.value = true;
      try { chkRes.value = await api('/api/permissions/check?emp_no=' + encodeURIComponent(no)); }
      catch (e) { checkError.value = e.message; }
      finally { checking.value = false; }
    }
    return { tabs, tab, emps, grants, datasets, emp, empSaving, empSaveError, g, chk, chkRes, roles, dsTables, loading, loadError, load, checking, checkError,
      editEmp, saveEmp, delEmp, toggleT, addGrant, delGrant, checkEmp, fmtTime };
  }
};

// ------------------------------------------------------------ 根组件
const Login = {
  template: `<main class="login-shell">
    <div class="card login">
      <a class="brand" :href="appPath('/')"><span class="brand-icon" v-html="icon('rep')"></span><span>报告管理台<small>SmartReportGen</small></span></a>
      <h1>登录报告管理台</h1><p class="login-intro">登录后可查看报告、模板及已授权的业务数据。</p>
      <template v-if="cfg">
        <a v-if="cfg.iam_configured" class="btn primary big" :href="appPath('/auth/iam/login?next=' + encodeURIComponent(BASE_PATH ? BASE_PATH + '/' : '/'))">使用海尔账号登录 <span class="ico" v-html="icon('arrow')"></span></a>
        <div v-else class="msg warn" role="status">海尔单点登录暂不可用，请联系系统管理员。</div>
      </template>
    </div>
  </main>`,
  setup() { return { cfg: computed(() => state.authCfg), icon, appPath, BASE_PATH }; }
};

// ------------------------------------------------------------ 我的权限与接入（所有登录员工）
const Mine = {
  template: `<div>
    <div class="page-head"><div><h1>我的权限与接入</h1><p>查看数据权限及 HTTP、MCP 接入方式。</p></div></div>
    <div class="grid g2">
      <div class="card card-pad">
        <h2>账号信息</h2>
        <dl class="kv" v-if="state.me"><dt>工号</dt><dd class="mono">{{state.me.emp_no || '-'}}</dd><dt>姓名</dt><dd>{{state.me.name || '-'}}</dd>
          <dt>身份</dt><dd><span class="badge" :class="state.me.is_admin?'red':''">{{state.me.is_admin ? '管理员' : '员工'}}</span> <span class="badge" v-if="!state.me.registered" style="margin-left:4px">未登记 / 已停用</span></dd>
          <dt>登录方式</dt><dd>{{({session:'海尔 IAM 单点登录', iam:'海尔 IAM'})[state.me.via] || state.me.via}}</dd></dl>
        <h2 class="account-section">我的数据权限</h2>
        <div v-if="loading" class="msg" role="status">正在加载数据权限…</div>
        <div v-else-if="loadError" class="msg err" role="alert">数据权限加载失败。<button type="button" class="btn link" @click="load">重试</button></div>
        <div v-else-if="perm">
          <div class="msg ok" v-if="perm.all">管理员：可访问全部业务场景与数据表。</div>
          <div class="msg warn" v-else-if="!perm.registered">工号未登记或已停用，暂无数据权限，请联系管理员开通。</div>
          <div v-else-if="!perm.datasets.length" class="msg warn">暂无任何数据表权限，请联系管理员授权。</div>
          <div v-else v-for="d in perm.datasets" class="hit" style="padding:10px 12px"><b>{{d.business_scenario}}</b>
            <div style="margin-top:4px"><span v-for="t in d.tables" class="badge" style="margin:2px 6px 2px 0"><span class="mono">{{t}}</span></span></div></div>
        </div>
      </div>
      <div class="card card-pad">
        <h2>接入 AI 助手</h2>
        <p>MCP 请求须通过 Authorization: Bearer 携带海尔 IAM access token。</p>
        <template v-if="mcpUrl">
          <p>将令牌设为启动 Codex 的终端环境变量 SMARTREPORT_TOKEN，再运行以下命令。</p>
          <pre class="block">codex mcp add smartreportgen --url {{mcpUrl}} --bearer-token-env-var SMARTREPORT_TOKEN</pre>
        </template>
        <div class="actions"><a class="btn" :href="appPath('/api-guide')" target="_blank" rel="noopener">HTTP 接入说明</a>
          <a class="btn" :href="appPath('/docs')" target="_blank" rel="noopener">接口调试</a></div>
      </div>
    </div>
  </div>`,
  setup() {
    const { list: perm, loading, loadError, load } = useCollection('/api/me/permissions', null);
    const mcpUrl = computed(() => state.meta?.mcp_url);
    return { state, perm, mcpUrl, appPath, loading, loadError, load };
  }
};

const PAGES = { home: Home, datasources: Datasources, datasets: Datasets, templates: Templates, reports: Reports, styles: Styles, perms: Perms, mine: Mine };
// 页面地址继续使用原来的 hash，现有收藏和深链接可以直接打开。
const NAV_GROUPS = [
  { title: '报告', items: [['home', '管理总览', 'home'], ['reports', '报告库', 'rep'], ['templates', '报告模板', 'tpl']] },
  { title: '资源', items: [['datasets', '业务场景', 'set'], ['styles', '报告样式', 'sty']] },
  { title: '账号', items: [['mine', '我的权限与接入', 'key']] },
  { title: '系统管理', admin: true, items: [['datasources', '数据源', 'ds'], ['perms', '员工与授权', 'perm']] }
];
function canVisitMine() { return !!state.me?.emp_no; }

const appRoot = document.getElementById('app');
if (appRoot.dataset.reportId) {
  createApp(ReportViewer, { reportId: appRoot.dataset.reportId, initialVersion: appRoot.dataset.reportVersion }).mount(appRoot);
} else createApp({
  components: { Login, ConfirmDialog },
  template: `<Login v-if="state.needLogin"/>
  <div class="shell" v-else-if="state.me">
    <a class="btn skip-link" href="#main-content" @click.prevent="main?.focus()">跳到主内容</a>
    <aside class="side">
      <div class="brand-area">
        <a class="brand" href="#home" @click="menuOpen=false"><span class="brand-icon" v-html="icon('rep')"></span>
          <span>报告管理台<small>SmartReportGen</small></span></a>
        <button class="btn x menu-toggle" type="button" aria-label="导航菜单" aria-controls="workspace-nav" :aria-expanded="menuOpen" @click="menuOpen=!menuOpen" v-html="icon(menuOpen ? 'close' : 'menu')"></button>
      </div>
      <nav id="workspace-nav" class="nav" :class="{expanded: menuOpen}" aria-label="管理台导航">
        <div class="nav-group" v-for="g in groups" :key="g.title">
          <div class="nav-label">{{g.title}}</div>
          <a v-for="n in g.items" :key="n[0]" :href="'#'+n[0]" :class="{on: current[0]===n[0]}" :aria-current="current[0]===n[0] ? 'page' : null" @click="menuOpen=false">
            <span class="ico" v-html="icon(n[2])"></span>{{n[1]}}</a>
        </div>
      </nav>
    </aside>
    <div class="workspace">
      <header class="topbar">
        <div class="location"><span>{{currentGroup.title}}</span><span class="location-sep" aria-hidden="true">/</span><b>{{current[1]}}</b></div>
        <div class="account">
          <span class="avatar" aria-hidden="true">{{person.slice(0,1)}}</span>
          <span class="account-name"><b :title="person">{{person}}</b><small>{{state.me.is_admin ? '管理员' : '员工'}}</small></span>
          <button type="button" class="btn ghost logout" @click="logout">退出登录</button>
        </div>
      </header>
      <main id="main-content" ref="main" class="main" tabindex="-1"><component :is="page" :key="pageKey"/></main>
    </div>
  </div>
  <div v-else class="app-pending" role="status">
    <template v-if="bootError"><p>无法打开管理台。{{bootError}}</p><button type="button" class="btn" @click="init">重试</button></template>
    <template v-else>正在打开报告管理台…</template>
  </div>
  <div class="toasts" aria-live="polite"><div v-for="t in toasts" :key="t.id" class="toast" :class="t.kind">{{t.msg}}</div></div>
  <ConfirmDialog/>`,
  setup() {
    const menuOpen = ref(false), main = ref(null), bootError = ref('');
    const groups = computed(() => NAV_GROUPS
      .filter(g => !g.admin || isAdmin())
      .map(g => ({ ...g, items: g.items.filter(n => n[0] !== 'mine' || canVisitMine()) }))
      .filter(g => g.items.length));
    // 普通员工从业务场景查看获授权的数据；旧版数据源链接仍可打开只读列表。
    const legacySource = ['datasources', '数据源', 'ds'];
    const currentGroup = computed(() => groups.value.find(g => g.items.some(n => n[0] === state.page))
      || (state.page === 'datasources' ? { title: '资源', items: [legacySource] } : groups.value[0]));
    const current = computed(() => currentGroup.value.items.find(n => n[0] === state.page) || groups.value[0].items[0]);
    const page = computed(() => PAGES[current.value[0]]);
    // 身份变化后重新查询当前用户的权限详情，其他页面保留原有编辑状态。
    const pageKey = computed(() => current.value[0] === 'mine' ? `mine:${isAdmin()}:${state.me?.registered}` : current.value[0]);
    const person = computed(() => state.me?.name || state.me?.emp_no || '管理员');
    watch(() => current.value[0], async () => {
      menuOpen.value = false;
      await nextTick();
      window.scrollTo(0, 0);
      main.value?.focus({ preventScroll: true });
    });
    async function logout() {
      await run(() => api('/auth/logout', { method: 'POST' }));
      location.replace(location.pathname);
    }
    async function init() {
      bootError.value = '';
      try {
        const cfg = await api('/api/auth/config');
        state.authCfg = cfg;
        if (!cfg.me.logged_in) { state.needLogin = true; return; }
        state.me = cfg.me;
        loadMeta().catch(e => toast(e.message, 'err'));
      } catch (e) { bootError.value = e.message; }
    }
    let refreshingIdentity = false;
    async function refreshIdentity() {
      if (!state.me || state.needLogin || refreshingIdentity) return;
      refreshingIdentity = true;
      try {
        // 管理员可能在另一个会话改过此账号的身份；回到窗口时用现有会话刷新导航。
        const me = await api('/api/me');
        const changed = me.is_admin !== state.me.is_admin || me.registered !== state.me.registered;
        state.me = me;
        if (changed && me.registered) loadMeta().catch(e => toast(e.message, 'err'));
      } catch { /* 暂时无法核验时保留界面；会话过期由 api() 统一处理。 */ }
      finally { refreshingIdentity = false; }
    }
    onMounted(() => { init(); window.addEventListener('focus', refreshIdentity); });
    onBeforeUnmount(() => window.removeEventListener('focus', refreshIdentity));
    return { state, page, pageKey, groups, currentGroup, current, person, menuOpen, main, bootError, init, icon, toasts, logout };
  }
}).mount('#app');
