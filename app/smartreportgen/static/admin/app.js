/* SmartReportGen 管理台（Vue 3，无构建） */
const { createApp, reactive, ref, computed, onMounted, onBeforeUnmount, watch, nextTick } = Vue;

// ------------------------------------------------------------ 工具
const BASE_PATH = (window.__SRG_BASE_PATH__ || '').replace(/\/$/, '');
const appPath = path => BASE_PATH && path.startsWith('/') ? BASE_PATH + path : path;
const toasts = reactive([]);
function toast(msg, kind = '') {
  const t = { id: Math.random(), msg, kind };
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

// 模板和报告库复用同一个生成入口，生成后刷新报告库。
const reportDraft = ref(null);
const reportRevision = ref(0);
function startReport(template = null) { reportDraft.value = { template }; }
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
  search: '<circle cx="8.5" cy="8.5" r="4.5"/><path d="m12 12 4 4"/>',
  menu: '<path d="M3 5h14M3 10h14M3 15h14"/>',
  close: '<path d="m5 5 10 10M5 15 15 5"/>',
  check: '<path d="m4 10 4 4 8-8"/>'
};
const icon = k => `<svg aria-hidden="true" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${ICONS[k]}</svg>`;

// ------------------------------------------------------------ 通用组件
const Drawer = {
  props: { title: String, wide: Boolean, compact: Boolean, busy: Boolean, backdropClose: { type: Boolean, default: true } },
  emits: ['close'],
  template: `<dialog ref="dialog" class="drawer" :class="{wide, compact}" :aria-label="title"
    @cancel.prevent="close" @pointerdown="onPointerDown" @pointercancel="clearBackdropPress" @click.self="backdrop" @close="onDialogClose">
    <div class="drawer-head"><h2>{{title}}</h2><button class="x" type="button" aria-label="关闭" :disabled="busy" @click="close" v-html="icon('close')"></button></div>
    <div v-if="toasts.length" class="msgs dialog-feedback" aria-live="polite"><div v-for="t in toasts" :key="t.id" class="msg" :class="t.kind">{{t.msg}}</div></div>
    <div class="drawer-body"><slot/></div>
    <div class="drawer-foot" v-if="$slots.foot"><slot name="foot"/></div></dialog>`,
  setup(props, { emit }) {
    const dialog = ref(null);
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
    return { dialog, close, onPointerDown, clearBackdropPress, backdrop, onDialogClose, icon, toasts };
  }
};
const DataTable = {
  props: ['columns', 'rows', 'max'],
  template: `<div class="scroll-x" :style="{maxHeight: (max||360)+'px'}"><table class="mini"><thead><tr><th v-for="c in columns">{{c}}</th></tr></thead>
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
          <a v-for="entry in entries" :key="entry.page" :href="'#'+entry.page">
            <span class="resource-icon" v-html="icon(entry.icon)"></span>
            <span><b>{{entry.label}}</b><small>{{entry.description}}</small></span>
            <span class="ico" v-html="icon('arrow')"></span>
          </a>
        </div>
      </section>
      <section class="card work-panel" aria-labelledby="recent-title" :aria-busy="loading">
        <header class="panel-head"><div><h2 id="recent-title">最近报告</h2><p>{{admin ? '所有用户最近生成的报告' : '你最近生成的报告'}}</p></div></header>
        <div v-if="loading && !list.length" class="collection-state" role="status">正在加载报告…</div>
        <div v-else-if="loadError" class="collection-state" role="alert">报告加载失败。<button class="link" @click="load">重试</button></div>
        <div v-else-if="!list.length" class="recent-empty">
          <span class="empty-icon" v-html="icon('tpl')"></span>
          <div><b>还没有报告</b><p>生成的报告会出现在这里，可随时查看或下载。</p></div>
        </div>
        <ul v-else class="recent-list">
          <li v-for="r in list.slice(0,5)" :key="r.id">
            <a :href="reportPath(r)" target="_blank" rel="noopener">
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

// ------------------------------------------------------------ 从模板生成报告
const GenerateReport = {
  components: { Drawer },
  props: ['template'],
  emits: ['close'],
  template: `<Drawer title="从模板生成报告" compact :busy="busy" @close="$emit('close')">
    <div v-if="result" class="generation-result" role="status">
      <span class="result-icon" v-html="icon('check')"></span><h3>报告已生成</h3>
      <p>{{result.name}}</p>
      <details v-if="result.warnings?.length" class="msg warn report-warnings">
        <summary>查看 {{result.warnings.length}} 条生成提示</summary><ul><li v-for="(w,i) in result.warnings" :key="i">{{w}}</li></ul>
      </details>
    </div>
    <form v-else id="generate-report-form" @submit.prevent="generate">
      <p class="form-intro">选择可在管理台运行的结构化模板。业务模板可交给已接入的 AI 助手开发报告。</p>
      <div class="collection-state" v-if="loading" role="status">正在加载模板…</div>
      <div class="msg err" v-else-if="loadError" role="alert">模板加载失败。<button class="link" type="button" @click="load">重试</button></div>
      <div class="collection-state" v-else-if="!list.length">
        还没有可运行的结构化模板。
        <div class="actions"><a class="btn" href="#templates" @click="$emit('close')">前往报告模板</a></div>
      </div>
      <template v-else>
        <div class="field"><label for="generate-template">报告模板</label>
          <select id="generate-template" class="in" v-model="selectedId" :disabled="busy" required>
            <option :value="null" disabled>请选择模板</option>
            <option v-for="t in list" :key="t.id" :value="t.id">{{t.name}}{{t.business_scenario ? ' · ' + t.business_scenario : ''}}{{admin && t.created_by ? ' · ' + (t.creator_name || t.created_by) : ''}}</option>
          </select></div>
        <div class="field"><label for="generate-name">报告名称 <small>选填</small></label>
          <input id="generate-name" class="in" v-model.trim="name" :placeholder="selected ? selected.name : '默认使用模板名称'" :disabled="busy"></div>
        <div v-if="selected" class="field"><label for="generate-params">取数参数 <small>有账期等模板参数时填写</small></label>
          <textarea id="generate-params" class="in mono" v-model="params" rows="3" :disabled="busy" placeholder='{"period":"2026-09"}'></textarea></div>
        <div v-if="error" class="msg err" role="alert">{{error}}</div>
      </template>
    </form>
    <template #foot>
      <template v-if="result">
        <a class="btn" href="#reports" @click="$emit('close')">前往报告库</a>
        <a class="btn primary" :href="appPath('/reports/'+result.id)" target="_blank" rel="noopener">查看报告</a>
      </template>
      <template v-else>
        <button class="btn" :disabled="busy" @click="$emit('close')">取消</button>
        <button class="btn primary" type="submit" form="generate-report-form" :disabled="!selected || busy || loading || !!loadError">{{busy ? '正在生成…' : '生成报告'}}</button>
      </template>
    </template>
  </Drawer>`,
  setup(props) {
    const templates = useCollection('/api/templates', props.template ? [props.template] : []);
    const { loading, loadError, load } = templates;
    // 此入口直接调用服务端渲染，只展示支持该流程的模板。
    const list = computed(() => templates.list.value.filter(t => t.summary?.declarative));
    const selectedId = ref(props.template?.id ?? null), name = ref(''), params = ref(''), busy = ref(false), error = ref(''), result = ref(null);
    const selected = computed(() => list.value.find(t => t.id === selectedId.value));
    watch(selectedId, () => { error.value = ''; });
    async function generate() {
      if (!selected.value || busy.value || loading.value || loadError.value) return;
      busy.value = true;
      error.value = '';
      try {
        result.value = await api('/api/templates/' + selectedId.value + '/render', { method: 'POST', body: { name: name.value, parameters: jsonOrThrow(params.value, '取数参数') } });
        reportRevision.value += 1;
      } catch (e) { error.value = e.message; }
      finally { busy.value = false; }
    }
    return { list, loading, loadError, load, selectedId, selected, name, params, busy, error, result, generate,
      admin: computed(isAdmin), icon, appPath };
  }
};

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
    <div class="subtabs"><button :class="{on:tab==='tables'}" @click="tab='tables'">表和视图</button><button :class="{on:tab==='sql'}" @click="tab='sql'">SQL 查询</button></div>
    <div v-if="tab==='tables'" class="source-browser">
      <div><input class="in" v-model="kw" type="search" aria-label="搜索表或视图" placeholder="搜索表或视图">
        <div class="source-object-list" :aria-busy="loading">
          <button v-for="t in shown" :key="t.name" type="button" class="hit source-object" :aria-pressed="cur===t.name" @click="open(t.name)">
            <span class="source-object-name"><span class="mono">{{t.name}}</span><span class="badge">{{OBJECT_LABEL[t.kind] || '表'}}</span></span>
            <span class="small muted" v-if="t.comment">{{t.comment}}</span>
          </button>
          <div v-if="!shown.length" class="muted small" role="status">{{loading ? '正在加载…' : kw ? '没有匹配的表或视图' : '无可浏览的表或视图'}}</div></div></div>
      <div v-if="desc">
        <div class="actions" style="margin-bottom:10px"><b class="mono">{{desc.table}}</b><span class="badge">行数 {{desc.row_count ?? '未知'}}</span><span class="small muted">{{desc.profile_basis}}</span></div>
        <h4 style="margin:6px 0">字段画像</h4>
        <div class="scroll-x" style="max-height:300px"><table class="mini"><thead><tr><th>字段</th><th>类型</th><th>注释</th><th>推断</th><th>空值率</th><th>去重</th><th>范围 / 高频值</th></tr></thead>
          <tbody><tr v-for="p in profileRows"><td class="mono">{{p.column}}</td><td class="small">{{p.type}}</td><td>{{p.comment}}</td><td><span class="badge">{{p.kind}}</span></td>
          <td>{{p.null_ratio==null?'':(p.null_ratio*100).toFixed(1)+'%'}}</td><td>{{p.distinct}}</td>
          <td class="small">{{p.min!=null ? p.min+' ~ '+p.max : ''}}{{p.top_values ? p.top_values.slice(0,5).map(x=>x.value).join('、') : ''}}</td></tr></tbody></table></div>
        <h4 style="margin:14px 0 6px">样本数据</h4><DataTable :columns="desc.sample_columns" :rows="desc.sample_rows" :max="260"/>
      </div>
      <div v-else class="empty">选择表或视图，查看字段和数据画像</div>
    </div>
    <div v-else>
      <textarea class="in code" v-model="sql" rows="8" placeholder="SELECT ..."></textarea>
      <div class="actions" style="margin:10px 0"><button class="btn primary" @click="query" :disabled="loading">执行（只读）</button>
        <span class="small muted" v-if="res">返回 {{res.row_count}} 行{{res.truncated?'（已截断）':''}} · 方言 {{res.dialect}}</span></div>
      <DataTable v-if="res" :columns="res.columns" :rows="res.rows" :max="480"/>
    </div>
  </Drawer>`,
  setup(props) {
    const tab = ref('tables'), tables = ref([]), kw = ref(''), desc = ref(null), cur = ref(''), loading = ref(false);
    const sql = ref(''), res = ref(null);
    const shown = computed(() => tables.value.filter(t => matches(kw.value, t.name, t.comment, OBJECT_LABEL[t.kind])));
    const profileRows = computed(() => {
      if (!desc.value) return [];
      const types = Object.fromEntries((desc.value.columns || []).map(c => [c.name, c]));
      return desc.value.profile.map(p => ({ ...p, type: (types[p.column] || {}).type || '', comment: p.comment || (types[p.column] || {}).comment || '' }));
    });
    onMounted(async () => { loading.value = true; try { tables.value = await run(() => api(`/api/datasources/${props.ds.id}/tables`)); } finally { loading.value = false; } });
    async function open(name) {
      cur.value = name; desc.value = null;
      const data = await run(() => api(`/api/datasources/${props.ds.id}/describe?table=${encodeURIComponent(name)}&sample_rows=8`));
      // 快速切换时，较晚返回的旧画像不能覆盖当前选择。
      if (cur.value === name) { desc.value = data; if (!sql.value) sql.value = `SELECT * FROM ${name}`; }
    }
    async function query() { loading.value = true; try { res.value = await run(() => api(`/api/datasources/${props.ds.id}/query`, { method: 'POST', body: { sql: sql.value, limit: 500 } })); } finally { loading.value = false; } }
    return { tab, kw, shown, desc, cur, open, loading, sql, res, query, profileRows, OBJECT_LABEL };
  }
};
const Datasources = {
  components: { Drawer, DsBrowser },
  template: `<div>
    <div class="page-head"><div><h1>数据源</h1><p>{{admin ? '管理业务场景使用的数据连接，检查连接是否可用。' : '查看已授权业务场景使用的数据连接。'}}</p></div>
      <div class="actions"><button v-if="admin" class="btn primary" @click="edit()">新建数据源</button><span v-else class="badge">只读 · 仅显示你有权限的数据源</span></div></div>
    <div class="card scroll-x" tabindex="0" aria-label="数据表格"><table class="tbl"><thead><tr><th>名称</th><th>类型</th><th v-if="admin">连接</th><th>描述</th><th>状态</th><th v-if="admin">操作</th></tr></thead>
      <tbody><tr v-for="d in list"><td><b>{{d.name}}</b></td><td><span class="badge blue">{{DS_LABEL[d.type]}}</span></td>
        <td v-if="admin" class="mono small">{{addr(d)}}</td><td><div class="clip">{{d.description}}</div></td>
        <td><span class="dot" :class="{on:d.enabled}"></span>{{d.enabled?'启用':'停用'}}</td>
        <td v-if="admin"><div class="links"><button class="link" @click="test(d)">测试</button><button class="link" @click="browse=d">浏览</button>
          <button class="link" @click="edit(d)">编辑</button><button class="link danger" @click="del(d)">删除</button></div></td></tr>
        <tr v-if="!list.length"><td colspan="6" class="empty">{{admin ? '暂无数据源' : '暂无你有权限的数据源，如需开通请联系管理员'}}</td></tr></tbody></table></div>
    <Drawer v-if="form" :title="form.id ? '编辑数据源' : '新建数据源'" @close="form=null">
      <div class="row2"><div class="field"><label>名称</label><input class="in" v-model="form.name"></div>
        <div class="field"><label>类型</label><select class="in" v-model="form.type"><option v-for="(l,k) in DS_LABEL" :value="k">{{l}}</option></select></div></div>
      <template v-if="form.type!=='api'">
        <div class="row3"><div class="field"><label>主机</label><input class="in" v-model="form.host" placeholder="10.0.0.12"></div>
          <div class="field"><label>端口</label><input class="in" v-model="form.port" :placeholder="String(DEFAULT_PORT[form.type])"></div>
          <div class="field" v-if="form.type!=='oracle'"><label>数据库</label><input class="in" v-model="form.database"></div>
          <div class="field" v-else><label>服务名 <small>或 SID</small></label><input class="in" v-model="form.service_name" placeholder="ORCLPDB1"></div></div>
        <div class="row2" v-if="form.type==='oracle'">
          <div class="field"><label>SID <small>未填服务名时使用</small></label><input class="in" v-model="form.sid"></div>
          <div class="field"><label for="oracle-schema">Schema <small>选填</small></label>
            <input id="oracle-schema" class="in" v-model.trim="form.schema" aria-describedby="oracle-schema-hint">
            <div id="oracle-schema-hint" class="hint">留空时使用登录用户的默认 Schema。</div></div>
        </div>
        <div class="row2"><div class="field"><label>用户名</label><input class="in" v-model="form.username"></div>
          <div class="field"><label>密码</label><input class="in" type="password" v-model="form.secret" :placeholder="form.has_secret?'已设置，留空则不修改':''" autocomplete="new-password"></div></div>
        <div class="field"><label for="datasource-extra">高级配置 <small>选填，JSON</small></label>
          <textarea id="datasource-extra" class="in code" v-model="form.extraTxt" rows="3"></textarea></div>
      </template>
      <template v-else>
        <div class="field"><label>Base URL</label><input class="in" v-model="form.base_url" placeholder="https://api.example.com/v1"></div>
        <div class="row3"><div class="field"><label>记录路径 <small>如 data.items</small></label><input class="in" v-model="form.records_path"></div>
          <div class="field"><label>鉴权请求头</label><input class="in" v-model="form.auth_header"></div>
          <div class="field"><label>前缀</label><input class="in" v-model="form.auth_scheme" placeholder="Bearer"></div></div>
        <div class="field"><label>令牌 / API Key</label><input class="in" type="password" v-model="form.secret" :placeholder="form.has_secret?'已设置，留空则不修改':'可选'" autocomplete="new-password"></div>
        <div class="field"><label>接口定义 endpoints <small>逻辑表名 → {path, method, params, body, records_path, description}</small></label>
          <textarea class="in code" v-model="form.endpointsTxt" rows="7"></textarea>
          <div class="hint">数据集中的“数据表名”填写逻辑表名；查询时接口数据载入 DuckDB，以 DuckDB SQL 方言查询。</div></div>
        <div class="row2"><div class="field"><label>公共请求头 <small>JSON</small></label><textarea class="in code" v-model="form.headersTxt" rows="3"></textarea></div>
          <div class="field"><label>其他配置 <small>timeout / cache_seconds / method</small></label><textarea class="in code" v-model="form.extraTxt" rows="3"></textarea></div></div>
      </template>
      <details class="field">
        <summary>开放给员工构建业务场景</summary>
        <label for="datasource-scene-tables">可引用的表和授权范围</label>
        <p class="field-help" id="datasource-scene-tables-help">为表或视图指定可引用的工号或角色。固定行范围会应用到员工基于此表创建的场景。</p>
        <textarea id="datasource-scene-tables" class="in code" v-model="form.scenarioTablesTxt" rows="5" spellcheck="false"
          aria-describedby="datasource-scene-tables-help" placeholder='[{"table_name":"public.sales","roles":["user"],"row_filters":[]}]'></textarea>
      </details>
      <div class="field"><label>描述</label><textarea class="in" v-model="form.description" rows="2"></textarea></div>
      <label class="check"><input type="checkbox" v-model="form.enabled"> 启用</label>
      <div class="msg" :class="testRes.ok?'ok':'err'" :role="testRes.ok?'status':'alert'" v-if="testRes" style="margin-top:12px">{{testRes.text}}</div>
      <template #foot><button class="btn" @click="testForm" :disabled="busy">测试连接</button><span class="spacer"></span>
        <button class="btn" @click="form=null">取消</button><button class="btn primary" @click="save" :disabled="busy">保存</button></template>
    </Drawer>
    <DsBrowser v-if="browse" :ds="browse" @close="browse=null"/>
  </div>`,
  setup() {
    const list = ref([]), form = ref(null), browse = ref(null), busy = ref(false), testRes = ref(null);
    const load = async () => { list.value = await api('/api/datasources'); };
    onMounted(load);
    const admin = computed(isAdmin);
    const addr = d => !d.config ? '' : d.type === 'api' ? (d.config.base_url || '') : `${d.config.host || ''}:${d.config.port || DEFAULT_PORT[d.type]}/${d.config.database || d.config.service_name || d.config.sid || ''}`;
    function edit(d) { testRes.value = null; form.value = d ? dsToForm(d) : emptyDs(); }
    async function save() {
      busy.value = true; testRes.value = null;
      try {
        const body = formToDs(form.value);
        const id = form.value.id;
        await api(id ? `/api/datasources/${id}` : '/api/datasources', { method: id ? 'PUT' : 'POST', body });
        toast('已保存', 'ok');
        form.value = null; load();
      } catch (e) { testRes.value = { ok: false, text: e.message }; } finally { busy.value = false; }
    }
    async function testForm() {
      busy.value = true; testRes.value = null;
      try {
        const b = formToDs(form.value); b.id = form.value.id;
        const r = await api('/api/datasources/test', { method: 'POST', body: b });
        testRes.value = { ok: true, text: `连接成功 · ${r.version} · ${r.latency_ms} ms` };
      } catch (e) { testRes.value = { ok: false, text: e.message }; } finally { busy.value = false; }
    }
    async function test(d) { const r = await run(() => api(`/api/datasources/${d.id}/test`, { method: 'POST' })); toast(`「${d.name}」连接成功：${r.version}（${r.latency_ms} ms）`, 'ok'); }
    async function del(d) { if (!confirm(`删除数据源「${d.name}」？`)) return; await run(() => api(`/api/datasources/${d.id}`, { method: 'DELETE' }), '已删除'); load(); }
    return { admin, list, form, browse, busy, testRes, addr, edit, save, testForm, test, del, DS_LABEL, DEFAULT_PORT };
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
  if (m.when && Object.keys(m.when).length) extra.when = m.when;
  if (m.denominator_when && Object.keys(m.denominator_when).length) extra.denominator_when = m.denominator_when;
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
    if (row.extra?.denominator_when) entry.denominator_when = row.extra.denominator_when;
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
      <div v-else-if="error" class="msg err" role="alert">{{error}} <button type="button" class="link scene-values-action" @click="load(failedOffset)">重试</button></div>
      <button v-else-if="nextOffset !== null" type="button" class="link scene-values-action" @click="load(nextOffset)">加载更多候选值</button>
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
      <div class="actions"><button v-if="admin" class="btn" @click="reindex">重建检索索引</button><button class="btn primary" @click="edit()">新建业务场景</button></div></div>
    <section class="card list-panel" aria-label="业务场景列表" :aria-busy="loading">
      <div class="list-tools">
        <label class="search-field"><span class="ico" v-html="icon('search')"></span><input type="search" v-model="kw" placeholder="搜索业务场景、描述或标签" aria-label="搜索业务场景"></label>
        <span class="list-count" v-if="!loading && !loadError" aria-live="polite">{{shown.length}} 个场景</span>
      </div>
      <div v-if="loading" class="collection-state" role="status">正在加载业务场景…</div>
      <div v-else-if="loadError" class="collection-state" role="alert">业务场景加载失败。<button class="link" @click="load">重试</button></div>
      <div v-else-if="!shown.length" class="collection-state">
        <span class="empty-icon" v-html="icon('set')"></span><h2>{{kw.trim() ? '未找到匹配的场景' : '暂无可用的业务场景'}}</h2>
        <p>{{kw.trim() ? '可以改用业务主题或常见指标搜索。' : '可新建业务场景，选择授权的数据源并填写分析思路。'}}</p>
        <button v-if="kw.trim()" class="btn" @click="kw=''">清除搜索</button>
        <button v-else class="btn" @click="edit()">新建业务场景</button>
      </div>
      <div v-else class="scroll-x" tabindex="0" aria-label="业务场景表格">
        <table class="tbl scene-table" role="table"><thead><tr><th scope="col">业务场景</th><th scope="col">分析范围</th><th scope="col">数据表</th><th v-if="admin" scope="col">状态</th><th scope="col">操作</th></tr></thead>
          <tbody><tr v-for="d in shown" :key="d.id">
            <td><b>{{d.business_scenario}}</b><div class="small muted">{{(d.tags||[]).join(' · ')}}</div></td>
            <td class="scene-description"><div class="clip">{{d.business_description || '—'}}</div></td><td data-label="数据表">{{d.table_count}} 张</td>
            <td v-if="admin" data-label="状态"><span class="badge" :class="!d.enabled ? '' : d.vectorized ? 'green' : 'amber'">{{!d.enabled ? '停用' : d.vectorized ? '可用' : '索引待更新'}}</span></td>
            <td><div class="links"><button class="link" @click="view(d)">查看场景</button><template v-if="admin || d.read_only === false"><button class="link" @click="edit(d)">编辑</button><button class="link danger" @click="del(d)">删除</button></template></div></td>
          </tr></tbody>
        </table>
      </div>
    </section>
    <details class="card card-pad discovery">
      <summary>按分析需求匹配业务场景</summary>
      <p class="muted">描述要回答的业务问题，查找可能用到的数据。</p>
      <form class="query-form" @submit.prevent="search">
        <input class="in" v-model="q" aria-label="分析需求" placeholder="例如：分析各渠道的销售增长与毛利变化">
        <button class="btn primary" type="submit" :disabled="searching || !q.trim()">{{searching ? '正在匹配…' : '匹配场景'}}</button>
      </form>
      <div v-if="searchError" class="msg err" role="alert">{{searchError}}</div>
      <div v-if="sr" class="match-results" aria-live="polite">
        <div class="msg warn" v-for="w in sr.warnings">{{w}}</div>
        <div v-for="h in sr.hits" :key="h.dataset.id" class="hit"><h4>{{h.dataset.business_scenario}}</h4>
          <p>{{h.dataset.business_description}}</p><button class="link" @click="view(h.dataset)">查看场景</button>
          <details v-if="admin"><summary class="small muted">查看匹配依据</summary>
            <p class="score">关键词 {{(h.matched_terms||[]).join('、') || '—'}} · 综合分 {{h.rrf}} · 向量 {{h.vector_score ?? '—'}} · 关键词分 {{h.keyword_score ?? '—'}}</p>
          </details>
        </div>
        <p v-if="!sr.hits.length" class="muted">暂无匹配的场景。试试换一个业务主题或缩短描述。</p>
      </div>
    </details>
    <p class="small muted legacy-link" v-if="!admin"><a href="#datasources">查看这些场景使用的数据源</a></p>
    <Drawer v-if="detail" :title="detail.business_scenario" @close="detail=null">
      <dl class="kv"><dt>业务描述</dt><dd class="business-copy">{{detail.business_description}}</dd>
        <dt>分析思路</dt><dd class="business-copy">{{detail.analysis_approach || '暂未填写'}}</dd>
        <dt>标签</dt><dd>{{(detail.tags||[]).join('、') || '-'}}</dd>
        <dt>数据源</dt><dd>{{detail.datasource_name}}（{{DS_LABEL[detail.datasource_type]}}）</dd></dl>
      <h4 style="margin:16px 0 8px">表和视图 <small class="muted" v-if="!admin">（仅显示你有权限的对象）</small></h4>
      <div class="scroll-x"><table class="tbl card"><thead><tr><th scope="col">名称</th><th scope="col">描述</th><th scope="col">取值范围</th></tr></thead>
        <tbody><tr v-for="t in detail.tables" :key="t.id"><td class="mono">{{t.table_name}}</td><td>{{t.table_description}}</td><td>
          <div v-for="r in t.row_filters" :key="r.column" class="scene-filter-summary"><b>{{r.column}}</b>：{{r.values.map(filterValueLabel).join('、')}}</div>
          <div v-for="r in t.source_row_filters" :key="'source-' + r.column" class="scene-filter-summary"><b>数据源 · {{r.column}}</b>：{{r.values.map(filterValueLabel).join('、')}}</div>
          <span v-if="!t.row_filters?.length && !t.source_row_filters?.length" class="muted">不限制</span></td></tr>
        <tr v-if="!detail.tables.length"><td colspan="3" class="empty">无</td></tr></tbody></table></div>
    </Drawer>
    <Drawer v-if="form" :title="form.id ? '编辑业务场景' : '新建业务场景'" wide :busy="busy" :backdrop-close="false" @close="closeForm">
      <div class="row2"><div class="field"><label>业务场景</label><input class="in" v-model="form.business_scenario" placeholder="如：零售销售经营分析"></div>
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
      <div class="field"><label>标签 <small>用逗号分隔，帮助同事查找场景</small></label><input class="in" v-model="form.tagsTxt"></div>
      <div class="field">
        <div class="scene-metrics-head">
          <label>官方指标 <small>登记聚合公式和允许分组维度（选填）</small></label>
          <div class="seg" role="group" aria-label="官方指标编辑模式">
            <button type="button" :class="{on: form.metricsMode === 'visual'}" @click="switchMetricsMode('visual')">可视化编辑</button>
            <button type="button" :class="{on: form.metricsMode === 'json'}" @click="switchMetricsMode('json')">JSON 源码</button>
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
              <button class="link danger" type="button" @click="form.metricDrafts.splice(idx, 1)">移除指标</button>
            </div>
          </div>
          <div class="actions"><button class="btn sm" type="button" @click="addMetric" :disabled="form.metricDrafts.length >= 100">添加官方指标</button></div>
        </div>
        <div v-else>
          <textarea id="scene-metrics" class="in mono" v-model="form.metricsTxt" rows="6" spellcheck="false"></textarea>
          <a :href="appPath('/api-guide')" target="_blank" rel="noopener">查看配置字段</a>
        </div>
      </div>
      <section class="field" aria-labelledby="scene-tables-label"><h3 id="scene-tables-label" class="scene-tables-label">表和视图 <small class="muted">{{form.tables.length}} 项</small></h3>
        <p class="field-help">按需设置各表的取值范围。场景取数时自动应用这些条件。</p>
        <div v-for="(t,i) in form.tables" :key="t.key" class="scene-table-entry">
          <div class="scene-table-fields">
            <label>名称 <input class="in mono" v-model.trim="t.table_name" :readonly="!admin" placeholder="schema.table" @change="changeTable(t)"></label>
            <label>描述 <input class="in" v-model="t.table_description"></label>
            <button class="link danger" type="button" @click="form.tables.splice(i,1)" :aria-label="'移除 ' + (t.table_name || '第 ' + (i+1) + ' 项')">移除</button>
          </div>
          <p v-if="t.source_row_filters?.length" class="field-help">
            数据源固定范围 · {{t.source_row_filters.map(r => r.column + '：' + r.values.map(filterValueLabel).join('、')).join('；')}}
          </p>
          <button class="link scene-filter-toggle" type="button" :aria-expanded="t.expanded" :aria-controls="'scene-filters-' + t.key"
            :disabled="!form.datasource_id || !t.table_name" @click="toggleFilters(t)">数据过滤 · {{t.row_filters.length ? t.row_filters.length + ' 个字段' : '无附加筛选'}}</button>
          <div v-show="t.expanded" :id="'scene-filters-' + t.key" class="scene-filter-editor">
            <p class="field-help">选择字段后，从表中的实际取值里勾选。不同字段的条件须同时满足。</p>
            <p v-if="t.loadingColumns" class="small muted" role="status">正在读取字段…</p>
            <div v-if="t.columnError" class="msg err" role="alert">{{t.columnError}} <button class="link" type="button" @click="loadColumns(t)">重试</button></div>
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
              <button class="link danger" type="button" @click="t.row_filters.splice(j,1)" :aria-label="'移除筛选条件 ' + (rule.column || (j+1))">移除</button>
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
          <div v-if="pickError" class="msg err" role="alert">{{pickError}} <button class="link" type="button" @click="pick">重试</button></div>
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
    function closeForm() {
      if (busy.value) return;
      if (!formDirty.value || confirm('放弃未保存的业务场景修改？')) form.value = null;
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
    async function del(d) { if (!confirm(`删除数据集「${d.business_scenario}」？`)) return; await run(() => api(`/api/datasets/${d.id}`, { method: 'DELETE' }), '已删除'); load(); }
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
const STARTER = `---
title: 新报告
subtitle: 数据范围说明
scenario: 零售销售经营分析
analysis_goal: 用一句话写清楚本报告要回答的问题
filters:
  - {id: month, label: 月份, type: range, field: month}
  - {id: region, label: 大区, type: multi, field: region}
queries:
  sales:
    sql: |
      SELECT to_char(order_date, 'YYYY-MM') AS month, region, sum(amount) AS amount, sum(profit) AS profit, count(*) AS orders
      FROM demo_retail.sales_order GROUP BY 1, 2
    measures:
      gmv: {expr: "sum(amount)", name: 销售额, format: wan}
      margin: {expr: "sum(profit)/sum(amount)", name: 毛利率, format: percent}
      orders: {expr: "sum(orders)", name: 订单数, format: int}
---

## 一、经营概览
> 期间销售额 {{gmv}}，毛利率 {{margin}}。

### 销售规模
\`\`\`chart
type: kpi
y: gmv
sub: [margin, orders]
\`\`\`
\`\`\`chart
type: line
title: 月度销售额趋势
x: month
y: gmv
note: 说明该图如何支撑结论
\`\`\`
`;
const Templates = {
  components: { Drawer },
  template: `<div>
    <template v-if="!form">
      <div class="page-head"><h1>报告模板</h1>
        <button class="btn primary" @click="edit()"><span class="ico" v-html="icon('plus')"></span>新建模板</button></div>
      <section class="card list-panel" aria-label="模板列表" :aria-busy="loading">
        <div class="list-tools">
          <label class="search-field"><span class="ico" v-html="icon('search')"></span><input type="search" v-model="kw" placeholder="搜索模板名称、场景或用途" aria-label="搜索模板"></label>
          <span v-if="!loading && !loadError" class="list-count" aria-live="polite">{{shown.length}} 个模板</span>
        </div>
        <div v-if="loading" class="collection-state" role="status">正在加载模板…</div>
        <div v-else-if="loadError" class="collection-state" role="alert">模板加载失败。<button class="link" @click="load">重试</button></div>
        <div v-else-if="!shown.length" class="collection-state">
          <span class="empty-icon" v-html="icon(kw.trim() ? 'search' : 'tpl')"></span>
          <h2>{{kw.trim() ? '未找到匹配的模板' : '还没有报告模板'}}</h2>
          <p>{{kw.trim() ? '试试其他关键词。' : '可以新建模板，也可以在已接入的 AI 助手中提交分析需求，由助手保存模板。'}}</p>
          <button v-if="kw.trim()" class="btn" @click="kw=''">清除搜索</button>
          <button v-else class="btn" @click="edit()">新建模板</button>
        </div>
        <div v-else class="scroll-x" tabindex="0" aria-label="模板表格"><table class="tbl report-table" role="table">
          <thead><tr><th scope="col">模板名称</th><th scope="col">业务场景</th><th scope="col" v-if="admin">创建人</th><th scope="col">更新时间</th><th scope="col">操作</th></tr></thead>
          <tbody><tr v-for="t in shown" :key="t.id">
            <td><b>{{t.name}}</b> <span class="badge">v{{t.version}}</span><div class="small muted clip">{{t.business_summary || t.analysis_goal || t.user_requirement}}</div></td>
            <td data-label="业务场景">{{t.business_scenario || '—'}}</td><td v-if="admin" data-label="创建人">{{creator(t)}}</td><td data-label="更新时间"><time :datetime="t.updated_at">{{fmtTime(t.updated_at)}}</time></td>
            <td><div class="links"><button class="link" @click="view(t)">查看</button><button v-if="t.summary?.declarative" class="link" @click="gen(t)">生成报告</button>
              <button class="link danger" @click="del(t)">删除</button></div></td>
          </tr></tbody>
        </table></div>
      </section>
    </template>
    <template v-else-if="!editing">
      <div class="page-head">
        <div><h1 ref="templateHeading" tabindex="-1">{{form.name}}</h1><p>{{form.business_scenario || '未关联业务场景'}} · v{{form.version}}</p></div>
        <div class="actions"><button class="btn ghost" @click="form=null">返回模板列表</button>
          <button class="btn" @click="edit(form)">编辑模板</button><button v-if="form.summary?.declarative" class="btn primary" @click="gen(form)">生成报告</button></div>
      </div>
      <div class="card card-pad template-brief">
        <h2>模板用途</h2>
        <p class="business-copy">{{form.business_summary || form.analysis_goal || form.user_requirement || '暂未填写用途说明。'}}</p>
        <template v-if="form.business_summary && form.analysis_goal && form.analysis_goal !== form.business_summary">
          <h2>分析目标</h2><p class="business-copy">{{form.analysis_goal}}</p>
        </template>
        <template v-if="form.template_type === 'business' && !form.summary?.declarative">
          <h2>业务模板正文</h2><p class="business-copy">{{form.markdown}}</p>
        </template>
        <template v-else-if="form.summary?.sections?.some(Boolean)">
          <h2>报告章节</h2>
          <ol><li v-for="(title, i) in form.summary.sections.filter(Boolean)" :key="i">{{title}}</li></ol>
        </template>
      </div>
    </template>
    <template v-else>
      <div class="page-head"><h1 ref="templateHeading" tabindex="-1">{{form.id ? '编辑模板' : '新建模板'}}</h1>
        <div class="actions"><button class="btn ghost" :disabled="busy" @click="cancelEdit">取消</button>
          <button v-if="form.summary?.declarative" class="btn" @click="save(true)" :disabled="busy || !form.markdown.trim()">保存并生成…</button>
          <button class="btn primary" @click="save(false)" :disabled="busy || !form.markdown.trim()">保存模板</button></div></div>
      <div class="card card-pad meta-bar">
        <div class="field"><label for="template-name">模板名称</label><input id="template-name" class="in" v-model="form.name" placeholder="留空时使用正文标题"></div>
        <div v-if="!form.id" class="field"><label for="template-type">模板类型</label>
          <select id="template-type" class="in" v-model="form.template_type" :disabled="busy"><option value="business">声明式模板或历史 Markdown</option><option value="structured">历史结构化模板</option></select></div>
        <div class="field"><label for="template-scenario">业务场景</label>
          <select id="template-scenario" class="in" v-model="form.dataset_id"><option :value="null">选择默认取数的业务场景</option>
            <option v-if="form.dataset_id && !datasets.some(d => d.id === form.dataset_id)" :value="form.dataset_id">原场景（当前不可选）</option>
            <option v-for="d in datasets" :key="d.id" :value="d.id">{{d.business_scenario}}</option></select></div>
        <div class="field"><label for="template-requirement">原始分析需求 <small>选填</small></label><input id="template-requirement" class="in" v-model="form.user_requirement" placeholder="填写需要回答的业务问题"></div>
        <div class="field template-summary-field"><label for="template-business-summary">业务说明 <small>选填</small></label>
          <textarea id="template-business-summary" class="in" v-model="form.business_summary" rows="4" placeholder="例如：用于月度销售复盘，比较各渠道的销售额和毛利率，找出变化最大的区域和品类。"></textarea></div>
      </div>
      <div v-if="form.template_type === 'business'" class="card card-pad template-brief">
        <div class="field"><label for="business-markdown">{{form.summary?.declarative ? '声明式模板（JSON）' : '业务模板正文（JSON 或 Markdown）'}}</label>
          <textarea id="business-markdown" class="in" v-model="form.markdown" rows="14" :disabled="busy"></textarea></div>
        <div v-for="v in form.versions" :key="v.version" class="hit"><span>v{{v.version}} · {{fmtTime(v.created_at)}}</span>
          <button class="link" @click="loadVer(v.version)" :disabled="busy">载入正文</button></div>
      </div>
      <template v-else>
      <div class="actions template-technical">
        <button class="btn" :aria-expanded="technical" aria-controls="template-source" :disabled="busy" @click="technical=!technical">{{technical ? '收起高级编辑' : form.id ? '高级编辑' : '编写模板源码'}}</button>
        <span v-if="!form.id && !technical" class="muted small">新建模板需要填写源码。</span>
      </div>
      <div id="template-source"><template v-if="technical">
      <div class="actions template-source-actions">
        <button v-if="!form.id && !form.markdown.trim()" class="btn" @click="form.markdown=starter">载入零售示例</button>
        <button class="btn" @click="doValidate" :disabled="busy || !form.markdown.trim()">检查数据</button>
        <button class="btn" @click="preview" :disabled="busy || !form.markdown.trim()">预览报告</button>
      </div>
      <div class="editor"><div class="pane"><label class="editor-label" for="template-markdown">模板源码（Markdown）</label>
        <textarea id="template-markdown" class="in code" v-model="form.markdown" :disabled="busy" spellcheck="false"></textarea></div>
        <div class="right"><div class="subtabs"><button :class="{on:side==='check'}" @click="side='check'">校验结果</button><button :class="{on:side==='preview'}" @click="side='preview'">预览</button>
          <button v-if="form.versions && form.versions.length" :class="{on:side==='ver'}" @click="side='ver'">版本 ({{form.versions.length}})</button></div>
          <div class="body" v-show="side==='check'">
            <div v-if="!vr" class="muted small">点击“检查数据”执行模板中的查询并检查字段。</div>
            <div v-else class="msgs">
              <div class="msg ok" v-if="vr.ok">校验通过：{{vr.summary.sections.length}} 个章节，{{vr.summary.chart_count}} 个图表，{{vr.summary.filter_count}} 个筛选器</div>
              <div class="msg err" v-for="e in vr.errors">{{e}}</div><div class="msg warn" v-for="w in vr.warnings">{{w}}</div>
              <template v-if="vr.queries"><h4 style="margin:10px 0 4px">查询</h4>
                <div v-for="(q,k) in vr.queries" class="hit"><b class="mono">{{k}}</b> <span class="small muted">{{q.datasource}} · {{q.rows}} 行 · {{q.ms}} ms</span>
                  <div class="small mono muted" style="margin-top:4px">{{q.columns.join(', ')}}</div></div></template>
              <template v-if="vr.filters"><h4 style="margin:10px 0 4px">筛选器取值</h4>
                <div v-for="(f,k) in vr.filters" class="small"><b class="mono">{{k}}</b>：{{f.distinct}} 个取值 · {{f.examples.join('、')}}</div></template>
            </div></div>
          <div class="body" v-show="side==='preview'" style="padding:0"><iframe v-if="previewHtml" title="报告模板预览" :srcdoc="previewHtml"></iframe><div v-else class="empty">点击“预览”渲染</div></div>
          <div class="body" v-show="side==='ver'"><div v-for="v in form.versions" class="hit" style="display:flex;justify-content:space-between;align-items:center"><span>v{{v.version}} · {{fmtTime(v.created_at)}} · {{fmtSize(v.size)}}</span><button class="link" @click="loadVer(v.version)">载入编辑器</button></div></div>
        </div></div></template></div>
      </template>
    </template>
  </div>`,
  setup() {
    const templates = useCollection('/api/templates');
    const { load } = templates;
    const form = ref(null), datasets = ref([]), busy = ref(false), vr = ref(null), side = ref('check'), previewHtml = ref(''), kw = ref('');
    const editing = ref(false), technical = ref(false), templateHeading = ref(null);
    let savedTemplate = null;
    const shown = computed(() => templates.list.value.filter(t => matches(kw.value, t.name, t.business_scenario, t.business_summary, t.analysis_goal, t.user_requirement)));
    onMounted(async () => { datasets.value = await run(() => api('/api/datasets')); });
    async function view(t) {
      form.value = await run(() => api(`/api/templates/${t.id}`));
      editing.value = false;
      await nextTick();
      templateHeading.value?.focus();
    }
    async function edit(t) {
      // 编辑使用副本，取消时还原已保存的业务说明与源码。
      savedTemplate = t ? { ...t } : null;
      editing.value = true;
      technical.value = false;
      vr.value = null; previewHtml.value = ''; side.value = 'check';
      form.value = t ? { ...t } : { id: null, name: '', dataset_id: null, user_requirement: '', business_summary: '', markdown: '', version: 1, template_type: 'business' };
      await nextTick();
      templateHeading.value?.focus();
    }
    async function cancelEdit() {
      form.value = savedTemplate;
      editing.value = false;
      await nextTick();
      templateHeading.value?.focus();
    }
    async function doValidate() {
      busy.value = true; side.value = 'check';
      try { vr.value = await run(() => api('/api/templates/validate', { method: 'POST', body: { markdown: form.value.markdown, dataset_id: form.value.dataset_id } })); } finally { busy.value = false; }
    }
    async function preview() {
      busy.value = true;
      try { previewHtml.value = await run(() => api('/api/templates/preview', { method: 'POST', body: { markdown: form.value.markdown, dataset_id: form.value.dataset_id } })); side.value = 'preview'; } finally { busy.value = false; }
    }
    async function save(andRender) {
      busy.value = true;
      const f = form.value, body = { name: f.name, markdown: f.markdown, dataset_id: f.dataset_id,
        user_requirement: f.user_requirement, business_summary: f.business_summary, template_type: f.template_type };
      try {
        const t = await run(() => {
          if (f.template_type === 'business' && f.markdown.trim().startsWith('{')) {
            const parsed = jsonOrThrow(f.markdown, '声明式模板');
            body.spec = parsed.smartreportgen_spec || parsed;
          }
          return f.id ? api(`/api/templates/${f.id}`, { method: 'PUT', body }) : api('/api/templates', { method: 'POST', body });
        }, '模板已保存');
        await view(t);
        load();
        if (andRender) startReport(t);
      } finally { busy.value = false; }
    }
    function gen(t) { startReport(t); }
    async function del(t) { if (!confirm(`删除模板「${t.name}」？已生成的报告会保留。`)) return; await run(() => api(`/api/templates/${t.id}`, { method: 'DELETE' }), '已删除'); load(); }
    async function loadVer(v) { const r = await run(() => api(`/api/templates/${form.value.id}/versions/${v}`)); form.value.markdown = r.markdown; toast(`已载入 v${v}，保存后成为新版本`); }
    const admin = computed(isAdmin);
    return { ...templates, admin, creator, shown, form, datasets, busy, vr, side, previewHtml, kw, editing, technical, templateHeading,
      starter: STARTER, view, edit, cancelEdit, doValidate, preview, save, gen, del, loadVer, fmtTime, fmtSize, icon };
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
        <p v-else-if="searchError" class="field-help" role="alert">用户列表加载失败。<button type="button" class="link" @click="search(query)">重试</button></p>
        <p v-else-if="!candidates.length" class="field-help" role="status">未找到可选用户。</p>
        <ul v-else class="report-recipient-results" aria-label="可选用户">
          <li v-for="person in candidates" :key="person.emp_no">
            <button type="button" :disabled="busy" @click="choose(person)">
              <b>{{person.name || person.emp_no}}</b><span v-if="person.name" class="small muted">{{person.emp_no}}</span>
            </button>
          </li>
        </ul>
      </div>
      <button type="button" class="link report-recipient-switch" @click="useManual">手动输入工号</button>
    </div>
    <form class="report-share-form" @submit.prevent="save">
      <div v-if="manual || selectedRecipient" class="field report-recipient-field">
        <label for="share-emp-no">接收人工号</label>
        <div class="report-recipient-input">
          <input id="share-emp-no" class="in" ref="recipient" v-model.trim="empNo" maxlength="256" :disabled="busy" :readonly="!manual" required>
          <button type="button" class="link report-recipient-switch" :disabled="busy" @click="useDirectory">{{manual ? '选择已有用户' : '更换'}}</button>
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
      <button v-else-if="loadFailed" type="button" class="link" @click="load">重试加载</button>
      <p v-else-if="!shares.length" class="muted">尚未分享给其他用户。</p>
      <ul v-else>
        <li v-for="s in shares" :key="s.emp_no">
          <div><b>{{s.name ? s.name + '（' + s.emp_no + '）' : s.emp_no}}</b>
            <div class="small muted">{{s.access_level === 'edit' ? '可编辑' : '可查看'}}{{!s.registered ? ' · 待登记' : s.enabled ? '' : ' · 已停用'}}</div></div>
          <div class="links"><button class="link" :aria-label="'修改 ' + s.emp_no + ' 的权限'" :disabled="busy || (s.registered && !s.enabled)" @click="choose(s)">修改</button>
            <button class="link danger" :aria-label="'撤销 ' + s.emp_no + ' 的权限'" :disabled="busy" @click="revoke(s)">撤销</button></div>
        </li>
      </ul>
    </section>
    <div v-if="url" class="field"><label for="share-url">报告链接</label>
      <input id="share-url" class="in" ref="link" :value="url" readonly @focus="$event.target.select()"></div>
    <template #foot>
      <button class="btn" :disabled="busy" @click="$emit('close')">关闭</button>
      <button class="btn" :disabled="!url || busy" @click="copyLink">复制链接</button>
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
      if (busy.value || !confirm('撤销 ' + share.emp_no + ' 的报告权限？')) return;
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
    <button v-else-if="!form" class="link" @click="load">重试加载</button>
    <form v-else id="report-edit-form" @submit.prevent="save">
      <p class="form-intro">基于版本 {{form.base_version}} 编辑。保存后生成新版本。</p>
      <div class="field"><label for="report-edit-name">报告名称</label>
        <input id="report-edit-name" class="in" v-model.trim="form.name" required :disabled="busy"></div>
      <div class="field"><label for="report-edit-note">修改说明 <small>选填</small></label>
        <input id="report-edit-note" class="in" v-model.trim="form.change_note" maxlength="500" :disabled="busy"></div>
      <div v-if="original.spec" class="field">
        <label for="report-edit-spec">声明式报告 JSON</label>
        <textarea id="report-edit-spec" class="in mono" v-model="form.spec" rows="14" spellcheck="false" :disabled="busy"></textarea>
        <p class="field-help">保存时使用基准版本的数据，服务端重新验收。</p>
      </div>
      <p v-else class="field-help">历史报告在此编辑名称和说明。正文可通过声明式模板迁移。</p>
      <a :href="preview" target="_blank" rel="noopener">打开基准版本</a>
    </form>
    <template #foot>
      <button class="btn" :disabled="busy" @click="close">取消</button>
      <button type="submit" class="btn primary" form="report-edit-form" :disabled="busy || !form || !dirty">{{busy ? '保存中…' : '保存为新版本'}}</button>
    </template>
  </Drawer>`,
  setup(props, { emit }) {
    const path = '/api/reports/' + props.report.id;
    const form = ref(null), original = ref(null), preview = ref('');
    const loading = ref(true), busy = ref(false), error = ref('');
    const dirty = computed(() => !!form.value && (form.value.name !== original.value.name || form.value.spec !== original.value.spec || !!form.value.change_note));
    async function load() {
      loading.value = true;
      error.value = '';
      try {
        // 抽屉打开后锁定基准，即使另一个页面切换了版本也不会误编辑。
        const { name, spec, revision, access_level, url } = await api(path + '?include_source=true&version=' + props.report.revision);
        if (access_level === 'view') throw new Error('当前账号无权编辑该报告');
        original.value = { name, spec: spec ? pretty(spec) : '', base_version: revision, change_note: '' };
        form.value = { ...original.value };
        preview.value = url;
      } catch (e) { error.value = e.message; }
      finally { loading.value = false; }
    }
    onMounted(load);
    function close() {
      if (!dirty.value || confirm('放弃未保存的报告修改？')) emit('close');
    }
    async function save() {
      if (!dirty.value || busy.value) return;
      busy.value = true;
      error.value = '';
      try {
        const { spec, ...body } = form.value;
        if (spec !== original.value.spec) body.spec = jsonOrThrow(spec, '声明式报告');
        const saved = await api(path, { method: 'PUT', body });
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
    <button v-else-if="!history" type="button" class="link" @click="load">重试加载</button>
    <template v-else>
      <p class="form-intro">切换后，查看和编辑默认使用所选版本。</p>
      <ul class="report-versions-list" aria-label="版本历史">
        <li v-for="v in history.versions" :key="v.version">
          <div>
            <div class="report-version-label"><b>版本 {{v.version}}</b>
              <span v-if="v.version === report.revision" class="badge">正在查看</span>
              <span v-if="v.version === history.current_version && v.version !== report.revision" class="badge">当前选择</span>
              <span v-if="v.version === history.latest_version" class="small muted">最新</span></div>
            <div class="small muted"><time :datetime="v.created_at">{{fmtTime(v.created_at)}}</time> · {{creator(v)}}<span v-if="v.base_version"> · 基于版本 {{v.base_version}}</span></div>
            <div v-if="v.name !== report.name || v.change_note" class="report-version-note">
              <span v-if="v.name !== report.name">{{v.name}}</span><span v-if="v.name !== report.name && v.change_note"> · </span>{{v.change_note}}</div>
          </div>
          <button v-if="v.version !== history.current_version || v.version !== report.revision" type="button" class="link"
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
        <button type="button" class="btn" ref="mergeButton" :disabled="!selecting && (loading || !!loadError || list.length < 2)"
          @click="toggleMerge">{{selecting ? '取消选择' : '合并报告'}}</button>
        <button class="btn primary" @click="create"><span class="ico" v-html="icon('plus')"></span>生成报告</button>
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
      <div v-else-if="loadError" class="collection-state" role="alert">报告加载失败。<button class="link" @click="load">重试</button></div>
      <div v-else-if="!shown.length" class="collection-state">
        <span class="empty-icon" v-html="icon(kw.trim() ? 'search' : 'rep')"></span>
        <h2>{{kw.trim() ? '未找到匹配的报告' : '还没有报告'}}</h2>
        <p>{{kw.trim() ? '可以修改关键词，或查看全部报告。' : '选择已有模板，使用当前数据生成第一份报告。'}}</p>
        <button v-if="kw.trim()" class="btn" @click="kw=''">清除搜索</button>
        <button v-else class="btn" @click="create">选择模板</button>
      </div>
      <div v-else class="scroll-x" tabindex="0" aria-label="报告表格"><table class="tbl report-table" role="table">
        <thead><tr><th scope="col">报告名称</th><th scope="col">版本</th><th scope="col">来源模板</th><th scope="col" v-if="admin">创建人</th><th scope="col">版本时间</th><th scope="col">操作</th></tr></thead>
        <tbody><tr v-for="r in shown" :key="r.id" :class="{'report-picked': selecting && pickedIds.has(r.id)}">
          <td><div class="report-record">
            <label v-if="selecting" class="report-pick"><input type="checkbox" :checked="pickedIds.has(r.id)"
              :aria-label="'选择 ' + r.name" @change="pick(r, $event.target.checked)"></label>
            <div><a class="record-name" :href="reportVersionPath(r)" target="_blank" rel="noopener">{{r.name}}</a>
            <div v-if="r.access_level !== 'manage'" class="small muted">{{creator(r)}}分享 · {{r.access_level === 'edit' ? '可编辑' : '可查看'}}</div>
            <div class="small muted">{{fmtSize(r.size)}}<span v-if="r.meta?.queries"> · 查询结果 {{queryRows(r)}} 行</span></div>
            <details v-if="r.meta?.warnings?.length" class="small report-warnings"><summary>{{r.meta.warnings.length}} 条生成提示</summary>
              <ul><li v-for="(w,i) in r.meta.warnings" :key="i">{{w}}</li></ul></details></div></div></td>
          <td><button type="button" class="btn report-version-button" :aria-label="r.name + '，当前版本 ' + r.revision + '，切换版本'" @click="versions=r">版本 {{r.revision}}</button></td>
          <td data-label="来源模板">{{r.template_name || '—'}} <span class="badge" v-if="r.template_version">v{{r.template_version}}</span></td>
          <td v-if="admin" data-label="创建人">{{creator(r)}}</td><td data-label="版本时间"><time :datetime="r.version_created_at">{{fmtTime(r.version_created_at)}}</time></td>
          <td><div class="links"><a :href="reportVersionPath(r)" target="_blank" rel="noopener">查看</a><a :href="reportVersionPath(r, '/download')">下载 HTML</a>
            <button v-if="r.access_level !== 'view'" class="link" @click="editing=r">编辑</button>
            <button v-if="r.access_level === 'manage'" class="link" @click="sharing=r">分享</button>
            <button v-if="r.access_level === 'manage'" class="link danger" @click="del(r)">删除</button></div></td>
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
      if (!confirm('删除报告「' + r.name + '」及其所有版本？')) return;
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
      create: () => startReport(), fmtTime, fmtSize, queryRows, icon, reportVersionPath };
  }
};

const ReportViewer = {
  components: { ReportEditor, ReportVersions }, props: ['reportId', 'initialVersion'],
  template: `<main class="report-viewer" :aria-busy="loading">
    <header class="report-viewer-bar">
      <div class="report-viewer-heading"><a class="link" :href="appPath('/#reports')">报告库</a><h1>{{report?.name || '报告'}}</h1></div>
      <div v-if="report" class="links">
        <button type="button" class="btn report-version-button" :aria-label="'当前版本 ' + report.revision + '，切换版本'" @click="versions=true">版本 {{report.revision}}</button>
        <a class="btn" :href="reportVersionPath(report, '/download')">下载 HTML</a>
        <button v-if="report.access_level !== 'view'" type="button" class="btn primary" @click="editing=true">编辑报告</button>
      </div>
    </header>
    <p v-if="loading" class="collection-state" role="status">正在加载报告…</p>
    <div v-else-if="error" class="collection-state" role="alert">{{error}} <button class="link" @click="load">重试</button></div>
    <iframe v-else-if="report" :key="report.revision" class="report-viewer-frame" :title="report.name + ' · 版本 ' + report.revision"
      :src="reportVersionPath(report, '/content')" sandbox="allow-scripts" referrerpolicy="no-referrer"></iframe>
    <ReportVersions v-if="versions" :report="report" @close="versions=false" @selected="selected"/>
    <ReportEditor v-if="editing" :report="report" @close="editing=false" @saved="saved"/>
  </main>
  <div class="toasts" aria-live="polite"><div v-for="t in toasts" :key="t.id" class="toast" :class="t.kind">{{t.msg}}</div></div>`,
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
    <template v-if="t.type==='color'"><input type="color" :value="hex(value)" :disabled="readonly" @input="$emit('set', $event.target.value.toUpperCase())">
      <input class="in mono sm" :value="value" :disabled="readonly" @change="onText($event.target.value)"></template>
    <template v-else-if="t.type==='color_auto'">
      <label class="check small"><input type="checkbox" :checked="!value" :disabled="readonly" @change="$emit('set', $event.target.checked ? '' : '#2168F3')"> 自动</label>
      <template v-if="value"><input type="color" :value="hex(value)" :disabled="readonly" @input="$emit('set', $event.target.value.toUpperCase())">
        <input class="in mono sm" :value="value" :disabled="readonly" @change="onText($event.target.value)"></template>
      <span v-else class="muted small">{{t.desc}}</span></template>
    <template v-else-if="t.type==='number'"><input class="in sm" type="number" :step="t.step || 0.1" :min="t.min" :max="t.max" :value="value" :disabled="readonly" @change="$emit('set', +$event.target.value)"><span class="muted small">{{t.min}} ~ {{t.max}}</span></template>
    <template v-else-if="t.type==='percent'"><input type="range" min="0" max="40" :value="value" :disabled="readonly" @input="$emit('set', +$event.target.value)"><span class="small mono">{{value}}%</span></template>
    <template v-else-if="t.type==='palette'"><div class="pal">
      <span v-for="(c,i) in value" class="pal-chip"><input type="color" :value="hex(c)" :disabled="readonly" @input="setPal(i,$event.target.value.toUpperCase())">
        <button v-if="!readonly && value.length>2" class="pal-x" title="移除" @click="delPal(i)">×</button></span>
      <button v-if="!readonly && value.length<12" class="pal-add" title="添加颜色" @click="addPal">+</button></div></template>
    <template v-else-if="t.type==='font'">
      <select class="in sm" :value="value.size" :disabled="readonly" @change="$emit('set', {size:+$event.target.value, weight:value.weight})"><option v-for="n in sizes" :value="n">{{n}}px</option></select>
      <select class="in sm" :value="value.weight" :disabled="readonly" @change="$emit('set', {size:value.size, weight:+$event.target.value})"><option v-for="w in weights" :value="w[0]">{{w[1]}}</option></select>
      <span class="font-sample" :style="{fontSize:value.size+'px', fontWeight:value.weight}">{{t.sample}}</span></template>
    <template v-else-if="t.type==='px'"><input class="in sm" type="number" min="0" :value="value" :disabled="readonly" @change="$emit('set', +$event.target.value)"><span class="muted small">px</span>
      <i class="px-bar" :style="{width: Math.min(160, value) + 'px'}" v-if="value < 999"></i><span class="muted small" v-else>全圆</span></template>
    <template v-else-if="t.type==='choice'"><div class="seg"><button v-for="o in t.options" :class="{on:value===o.value}" :disabled="readonly" @click="$emit('set', o.value)">{{o.label}}</button></div></template>
    <template v-else-if="t.type==='bool'"><label class="check"><input type="checkbox" :checked="value" :disabled="readonly" @change="$emit('set', $event.target.checked)"> 显示</label></template>
    <template v-else-if="t.type==='code'"><textarea class="in code" rows="3" :value="value" :disabled="readonly" @change="$emit('set', $event.target.value)"></textarea></template>
    <template v-else><input class="in sm" :value="value" :disabled="readonly" @change="$emit('set', $event.target.value)" style="flex:1"></template>
  </div>`,
  setup(props, { emit }) {
    const sizes = [12, 13, 14, 15, 16, 18, 20, 22, 24, 26, 28, 32, 36, 40, 44, 48, 56, 64, 72];
    const weights = [[300, '细'], [400, '常规'], [500, '中等'], [600, '半粗'], [700, '粗'], [800, '特粗']];
    const hex = v => { v = String(v || '#000000'); return /^#[0-9a-f]{6}$/i.test(v) ? v : /^#[0-9a-f]{3}$/i.test(v) ? '#' + v.slice(1).split('').map(x => x + x).join('') : '#000000'; };
    function onText(v) { v = v.trim(); if (isColor(v)) emit('set', v.toUpperCase()); else toast('请输入 #RRGGBB 格式的颜色', 'err'); }
    function setPal(i, c) { const a = props.value.slice(); a[i] = c; emit('set', a); }
    function delPal(i) { const a = props.value.slice(); a.splice(i, 1); emit('set', a); }
    function addPal() { emit('set', props.value.concat([props.value[props.value.length - 1] || '#2168F3'])); }
    return { sizes, weights, hex, onText, setPal, delPal, addPal };
  }
};
const TokenGroups = {
  components: { TokenControl },
  props: ['meta', 'effective', 'sources', 'own', 'readonly', 'showAdvanced'], emits: ['set', 'reset'],
  template: `<div>
    <div class="type-nav" role="tablist" aria-label="样式类型">
      <button v-for="g in groups" type="button" role="tab" :aria-selected="cur===g.key" :class="{on: cur===g.key}" @click="cur=g.key">{{g.label}}<b v-if="ownCount(g)">{{ownCount(g)}}</b></button>
    </div>
    <div v-for="g in groups" v-show="cur===g.key" class="tok-group" role="tabpanel">
      <div class="tok-group-head"><b>{{g.label}}</b><span class="muted small">{{g.desc}}</span>
        <span class="spacer"></span><span class="badge blue" v-if="ownCount(g)">本规则调整 {{ownCount(g)}} 项</span></div>
      <div v-for="t in g.tokens" class="tok-row" :class="{own: own && t.key in own}">
        <div class="tok-label"><b>{{t.label}} <span v-if="t.builtin" class="badge tiny">内置</span></b><span v-if="t.desc && t.type!=='color_auto'">{{t.desc}}</span></div>
        <TokenControl v-if="effective[t.key] !== undefined && effective[t.key] !== null" :t="t" :value="effective[t.key]" :readonly="readonly" @set="v => $emit('set', t.key, v)"/>
        <div v-else class="tok-ctl tok-unset"><span class="muted small">未设置 · 由智能体在生成报告时决定</span>
          <button v-if="!readonly" type="button" class="btn sm" @click="$emit('set', t.key, initial(t))">设置此项</button></div>
        <div class="tok-src">
          <template v-if="own && t.key in own"><span class="badge blue">本规则设置</span><button v-if="!readonly" class="link small" @click="$emit('reset', t.key)">{{t.builtin || inheritedSet(t.key) ? '恢复继承' : '取消设置'}}</button></template>
          <span v-else class="src" :class="sources[t.key] && sources[t.key].level">{{srcText(t.key)}}</span>
        </div>
      </div>
    </div>
  </div>`,
  setup(props) {
    // 按报告页面的组成部分分类；“高级”只在勾选显示高级选项时出现
    const groups = computed(() => props.meta.groups.filter(g => props.showAdvanced || g.key !== 'advanced'));
    const cur = ref(props.meta.groups[0].key);
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
        <div class="actions"><button class="btn primary" :disabled="!meta || (!admin && !scenarios.length)" @click="edit()">{{admin ? '新建样式' : '新建我的场景样式'}}</button></div></div>
      <section class="card card-pad style-overview" aria-labelledby="style-preview-title">
        <div><h2 id="style-preview-title">预览当前样式</h2>
          <p>同一项设置优先采用个人场景样式，其次是通用样式和系统默认。</p></div>
        <div class="style-preview-actions"><label class="field"><span>预览范围</span>
          <select class="in" v-model="viewChoice"><option value="">通用样式</option><option v-for="sc in scenarios" :key="sc" :value="sc">{{sc}}</option></select></label>
          <button class="btn" :disabled="!meta" @click="openView(viewChoice)">查看生效效果</button></div>
      </section>
      <div class="msg err" v-if="styleError" role="alert">{{styleError}} <button class="link" @click="loadStyles">重试</button></div>
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
              <span class="spacer"></span><template v-if="canEdit(r)"><button class="link" @click="edit(r)">编辑</button><button class="link danger" @click="del(r)">删除</button></template>
              <button v-else class="link" @click="edit(r, true)">查看</button></div>
          </div>
          <div v-if="!grp.rules.length" class="empty card">{{grp.empty}}</div>
        </div>
      </div></div>
    </template>

    <template v-else-if="viewSc !== null && !form">
      <div class="page-head"><div><h1>最终效果 · {{viewSc || '通用'}}</h1><p>查看此范围内实际采用的设置及其来源。</p></div>
        <div class="actions"><button class="btn ghost" @click="closeView">返回样式列表</button></div></div>
      <div v-if="viewError" class="msg err" role="alert">{{viewError}} <button class="link" @click="openView(viewSc)">重试</button></div>
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
        <div class="actions"><button class="btn ghost" :disabled="saving" @click="close">返回样式列表</button>
          <button class="btn style-preview-jump" @click="jumpToPreview">查看示例预览</button>
          <button v-if="!form.readonly" class="btn primary" :disabled="saving" @click="save">{{saving ? '正在保存…' : '保存样式'}}</button></div></div>
      <fieldset class="plain-fs" :disabled="form.readonly">
      <div class="card card-pad meta-row">
        <div class="field"><label for="style-name">样式名称</label><input id="style-name" class="in" v-model="form.name" placeholder="如：零售销售报告风格"></div>
        <div class="field" v-if="admin"><label>适用范围</label><div class="seg"><button :class="{on:form.scope==='general'}" @click="form.scope='general'">通用规则（所有报告）</button><button :class="{on:form.scope==='scenario'}" :disabled="!personalStyle" @click="form.scope='scenario'">个人场景样式</button></div></div>
        <div class="field" v-else><label>适用范围</label><div class="small" style="line-height:34px">{{form.scope==='general' ? '通用规则（管理员配置）' : '我的业务场景样式（只对我的报告生效）'}}</div></div>
        <div class="field" v-if="form.scope==='scenario'"><label for="style-scenario">业务场景</label><select id="style-scenario" class="in" v-model="form.business_scenario"><option value="" disabled>选择场景</option><option v-if="form.business_scenario && !scenarios.includes(form.business_scenario)" :value="form.business_scenario">{{form.business_scenario}}</option><option v-for="s in scenarios" :value="s">{{s}}</option></select></div>
        <div class="field" v-else><label for="preview-scenario">预览场景</label><select id="preview-scenario" class="in" v-model="previewSc"><option value="">不区分场景</option><option v-for="s in scenarios" :value="s">{{s}}</option></select></div>
        <div class="field"><label>状态</label><label class="check" style="height:34px"><input type="checkbox" v-model="form.enabled"> 启用</label></div>
      </div>
      </fieldset>
      <div class="msg err" v-if="saveError" role="alert">{{saveError}}</div>
      <div class="style-editor">
        <div class="pane-left">
          <div class="subtabs"><button :class="{on:tab==='items'}" @click="tab='items'">展示要求（{{form.items.length}}）</button><button :class="{on:tab==='theme'}" @click="tab='theme'">视觉样式（调整 {{Object.keys(form.theme).length}} 项）</button></div>
          <fieldset class="plain-fs" :disabled="form.readonly" v-show="tab==='items'">
            <p class="small muted" style="margin:0 0 10px">章节安排、选图建议和筛选条件可写在「业务场景」的分析思路中。</p>
            <div v-for="c in meta.categories" class="cat-block">
              <div class="cat-head"><b>{{c.label}}</b><span class="muted small">{{c.hint}}</span></div>
              <div v-for="(it,i) in form.items" v-show="it.category===c.key" class="item-row">
                <textarea class="in" rows="1" v-model="it.text" @input="grow($event)" :aria-label="c.label + '要求'" :placeholder="'写一条' + c.label + '要求'"></textarea>
                <select class="in sm" v-model="it.category" aria-label="要求类别"><option v-for="cc in meta.categories" :value="cc.key">{{cc.label}}</option></select>
                <button class="link danger" @click="form.items.splice(i,1)">删除</button></div>
              <div class="cat-actions" v-if="!form.readonly"><button class="btn sm" @click="add(c.key)">+ 添加</button>
                <button v-for="ex in c.examples" class="ex-chip" @click="add(c.key, ex)" :title="ex">示例：{{ex}}</button></div>
            </div>
          </fieldset>
          <details v-if="form.retiredItems.length" v-show="tab==='items'" class="card card-pad">
            <summary>已停用的分析要求（{{form.retiredItems.length}}）</summary>
            <ul class="rule-items"><li v-for="(it, i) in form.retiredItems" :key="i"><span class="cat">{{meta.retired_analysis_categories[it.category]}}</span>{{it.text}}</li></ul>
          </details>
          <div v-show="tab==='theme'">
            <p class="small muted" style="margin:0 0 10px">调整需要覆盖的样式项。其余设置沿用{{form.scope==='scenario' ? '通用规则或内置经营报告样式' : '内置经营报告样式'}}。</p>
            <label class="check small" style="margin-bottom:8px"><input type="checkbox" v-model="advanced"> 显示高级选项</label>
            <TokenGroups v-if="inherited" :meta="meta" :effective="effective" :sources="inherited.sources" :own="form.theme" :readonly="form.readonly" :show-advanced="advanced" @set="setTok" @reset="resetTok"/>
          </div>
        </div>
        <div class="pane-right" ref="previewPane" tabindex="-1"><div class="preview-box"><div class="preview-bar">示例报告预览 <span class="muted small">{{previewing ? '更新中…' : '改动后自动更新'}}</span></div><iframe title="示例报告预览" :srcdoc="previewHtml"></iframe></div></div>
      </div>
    </template>
    <!-- 内置事实规则只读展示，独立于可编辑样式；所有用户读取同一份服务端规则。 -->
    <details v-if="meta && meta.reporting_rules" class="card card-pad" style="margin-top:16px">
      <summary>内置报告规则（{{meta.reporting_rules.length}} 条）</summary>
      <p class="small muted">事实取数与离线交付规则独立生效，视觉样式不能覆盖。业务口径变更需由用户明确提出并记录。</p>
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
      if (!confirm('删除样式「' + r.name + '」？')) return;
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
    <div class="subtabs"><button v-for="t in tabs" :class="{on:tab===t[0]}" @click="tab=t[0]">{{t[1]}}</button></div>

    <template v-if="tab==='emp'">
      <div class="actions" style="margin-bottom:12px"><button class="btn primary" @click="editEmp()">新增员工</button></div>
      <div class="card scroll-x" tabindex="0" aria-label="数据表格"><table class="tbl"><thead><tr><th>工号</th><th>姓名</th><th>部门</th><th>数据授权角色</th><th>系统身份</th><th>注册来源</th><th>状态</th><th>操作</th></tr></thead>
        <tbody><tr v-for="e in emps"><td class="mono">{{e.emp_no}}</td><td>{{e.name}}</td><td>{{e.department}}</td>
          <td><span v-for="r in e.roles" class="badge" style="margin-right:4px">{{r}}</span><span v-if="e.direct_grants" class="small muted">+{{e.direct_grants}} 项直授</span></td>
          <td><span class="badge" :class="e.is_admin?'red':''">{{e.is_admin?'管理员（全部数据）':'普通用户'}}</span></td>
          <td class="small">{{({manual:'管理员登记', iam_login:'IAM 登录', mcp:'IAM 接入'})[e.registered_via] || e.registered_via}}</td>
          <td><span class="dot" :class="{on:e.enabled}"></span>{{e.enabled?'启用':'停用'}}</td>
          <td><div class="links"><button class="link" @click="checkEmp(e.emp_no)">查看权限</button>
            <button class="link" @click="editEmp(e)">编辑</button><button class="link danger" @click="delEmp(e)">删除</button></div></td></tr>
          <tr v-if="!emps.length"><td colspan="8" class="empty">暂无员工</td></tr></tbody></table></div>
    </template>

    <template v-if="tab==='grant'">
      <div class="card card-pad" style="margin-bottom:14px">
        <h3>新增授权</h3>
        <div class="row3" style="grid-template-columns:150px 1fr 1.4fr">
          <div class="field"><label>授权对象</label><div class="seg"><button :class="{on:g.subject_type==='role'}" @click="g.subject_type='role'">角色</button><button :class="{on:g.subject_type==='employee'}" @click="g.subject_type='employee'">员工</button></div></div>
          <div class="field"><label>{{g.subject_type==='role'?'角色编码':'员工工号'}}</label>
            <input v-if="g.subject_type==='role'" class="in" v-model="g.subject" list="role-list" placeholder="如 user（默认数据角色）">
            <select v-else class="in" v-model="g.subject"><option value="" disabled>选择员工</option><option v-for="e in emps" :value="e.emp_no">{{e.emp_no}} {{e.name}}</option></select>
            <datalist id="role-list"><option v-for="r in roles" :value="r"></option></datalist></div>
          <div class="field"><label>业务场景（数据集）</label><select class="in" v-model="g.dataset_id" @change="g.tables=['*']"><option :value="null" disabled>选择</option><option v-for="d in datasets" :value="d.id">{{d.business_scenario}}</option></select></div>
        </div>
        <div class="field" v-if="dsTables.length"><label>数据表</label>
          <div style="display:flex;flex-wrap:wrap;gap:6px 18px">
            <label class="check"><input type="checkbox" :checked="g.tables.includes('*')" @change="g.tables = $event.target.checked ? ['*'] : []"> <b>全部表（含今后新增）</b></label>
            <label v-for="t in dsTables" class="check" :style="g.tables.includes('*')?'opacity:.45':''"><input type="checkbox" :disabled="g.tables.includes('*')" :checked="g.tables.includes(t.table_name)" @change="toggleT(t.table_name)"> <span class="mono">{{t.table_name}}</span></label>
          </div></div>
        <button class="btn primary" @click="addGrant" :disabled="!g.subject || !g.dataset_id || !g.tables.length">授权</button>
      </div>
      <div class="card scroll-x" tabindex="0" aria-label="数据表格"><table class="tbl"><thead><tr><th>授权对象</th><th>业务场景</th><th>数据表</th><th>授权时间</th><th>操作</th></tr></thead>
        <tbody><tr v-for="x in grants"><td><span class="badge" :class="x.subject_type==='role'?'blue':'amber'">{{x.subject_type==='role'?'角色':'员工'}}</span> <span class="mono">{{x.subject}}</span> {{x.employee_name || ''}}</td>
          <td>{{x.business_scenario}}</td><td class="mono">{{x.table_name==='*' ? '全部表' : x.table_name}}</td><td class="small">{{fmtTime(x.created_at)}}</td>
          <td><button class="link danger" @click="delGrant(x)">撤销</button></td></tr>
          <tr v-if="!grants.length"><td colspan="5" class="empty">暂无授权</td></tr></tbody></table></div>
    </template>

    <template v-if="tab==='check'">
      <div class="card card-pad">
        <div class="actions"><select class="in" v-model="chk" style="width:260px"><option value="" disabled>选择员工</option><option v-for="e in emps" :value="e.emp_no">{{e.emp_no}} {{e.name}}</option></select><button class="btn primary" @click="checkEmp(chk)" :disabled="!chk">查询有效权限</button></div>
        <div v-if="chkRes" style="margin-top:14px">
          <div class="msg warn" v-if="!chkRes.registered">该工号未登记或已停用：HTTP 和 MCP 无法访问业务数据。</div>
          <div class="msg ok" v-else-if="chkRes.all">管理员：可访问全部业务场景与数据表。</div>
          <div v-for="d in chkRes.datasets" class="hit" v-if="!chkRes.all"><h4>{{d.business_scenario}} <span class="score">dataset_id={{d.dataset_id}}</span></h4>
            <div><span v-for="t in d.tables" class="badge" style="margin:2px 6px 2px 0"><span class="mono">{{t}}</span></span></div></div>
          <div v-if="chkRes.registered && !chkRes.all && !chkRes.datasets.length" class="muted">没有任何数据表权限。</div>
        </div>
      </div>
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
      <template #foot><button class="btn" :disabled="empSaving" @click="emp=null">取消</button>
        <button class="btn primary" :disabled="empSaving" @click="saveEmp">{{empSaving ? '保存中…' : '保存'}}</button></template>
    </Drawer>
  </div>`,
  setup() {
    const tabs = [['emp', '员工'], ['grant', '表授权'], ['check', '权限查询']];
    const tab = ref('emp'), emps = ref([]), grants = ref([]), datasets = ref([]), emp = ref(null);
    const empSaving = ref(false), empSaveError = ref('');
    const g = reactive({ subject_type: 'role', subject: '', dataset_id: null, tables: ['*'] });
    const chk = ref(''), chkRes = ref(null), dsDetail = ref(null);
    const roles = computed(() => Array.from(new Set(emps.value.flatMap(e => e.roles || []))));
    const dsTables = computed(() => dsDetail.value ? dsDetail.value.tables : []);
    watch(() => g.dataset_id, async id => { dsDetail.value = id ? await api('/api/datasets/' + id) : null; });
    const load = async () => {
      [emps.value, grants.value, datasets.value] = await Promise.all([api('/api/employees'), api('/api/grants'), api('/api/datasets')]);
    };
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
    async function delEmp(e) { if (!confirm(`删除员工 ${e.emp_no}？其直接授权和管理台会话会删除。`)) return; await run(() => api('/api/employees/' + encodeURIComponent(e.emp_no), { method: 'DELETE' }), '已删除'); load(); }
    function toggleT(t) { const i = g.tables.indexOf(t); if (i >= 0) g.tables.splice(i, 1); else g.tables.push(t); }
    async function addGrant() { const r = await run(() => api('/api/grants', { method: 'POST', body: { ...g } })); toast(`新增 ${r.added} 条授权`, 'ok'); load(); }
    async function delGrant(x) { if (!confirm('撤销该授权？')) return; await run(() => api('/api/grants/' + x.id, { method: 'DELETE' }), '已撤销'); load(); }
    async function checkEmp(no) { tab.value = 'check'; chk.value = no; chkRes.value = await run(() => api('/api/permissions/check?emp_no=' + encodeURIComponent(no))); }
    return { tabs, tab, emps, grants, datasets, emp, empSaving, empSaveError, g, chk, chkRes, roles, dsTables,
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
        <div v-if="perm">
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
    const perm = ref(null);
    const mcpUrl = computed(() => state.meta?.mcp_url);
    onMounted(async () => { perm.value = await run(() => api('/api/me/permissions')); });
    return { state, perm, mcpUrl, appPath };
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
  components: { Login, GenerateReport },
  template: `<Login v-if="state.needLogin"/>
  <div class="shell" v-else-if="state.me">
    <aside class="side">
      <div class="brand-area">
        <a class="brand" href="#home" @click="menuOpen=false"><span class="brand-icon" v-html="icon('rep')"></span>
          <span>报告管理台<small>SmartReportGen</small></span></a>
        <button class="x menu-toggle" type="button" aria-label="导航菜单" aria-controls="workspace-nav" :aria-expanded="menuOpen" @click="menuOpen=!menuOpen" v-html="icon(menuOpen ? 'close' : 'menu')"></button>
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
    <GenerateReport v-if="reportDraft" :template="reportDraft.template" @close="reportDraft=null"/>
  </div>
  <div v-else class="app-pending" role="status">
    <template v-if="bootError"><p>无法打开管理台。{{bootError}}</p><button class="btn" @click="init">重试</button></template>
    <template v-else>正在打开报告管理台…</template>
  </div>
  <div class="toasts" aria-live="polite"><div v-for="t in toasts" :key="t.id" class="toast" :class="t.kind">{{t.msg}}</div></div>`,
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
    return { state, page, pageKey, groups, currentGroup, current, person, menuOpen, main, reportDraft, bootError, init, icon, toasts, logout };
  }
}).mount('#app');
