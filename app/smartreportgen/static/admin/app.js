/* SmartReportGen 管理台（Vue 3，无构建） */
const { createApp, reactive, ref, computed, onMounted, onBeforeUnmount, watch, nextTick } = Vue;

// ------------------------------------------------------------ 工具
const toasts = reactive([]);
function toast(msg, kind = '') {
  const t = { id: Math.random(), msg, kind };
  toasts.push(t);
  setTimeout(() => { const i = toasts.indexOf(t); if (i >= 0) toasts.splice(i, 1); }, kind === 'err' ? 7000 : 3000);
}
async function api(path, opts = {}) {
  const init = { method: opts.method || 'GET', headers: {}, credentials: 'same-origin' };
  if (opts.body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.body); }
  const r = await fetch(path, init);
  const ct = r.headers.get('content-type') || '';
  const data = ct.includes('json') ? await r.json() : await r.text();
  if (!r.ok) {
    const msg = (data && data.detail) ? (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)) : String(data).slice(0, 300);
    if (r.status === 401 && !path.startsWith('/api/auth/')) state.needLogin = true;
    throw new Error(msg);
  }
  return data;
}
async function run(fn, okMsg) {
  try { const r = await fn(); if (okMsg) toast(okMsg, 'ok'); return r; } catch (e) { toast(e.message, 'err'); throw e; }
}
const fmtTime = s => s ? String(s).replace('T', ' ').slice(0, 16) : '';
const isAdmin = () => !!(state.me && state.me.is_admin);
const creator = x => x.created_by ? `${x.creator_name || ''}${x.creator_name ? '（' + x.created_by + '）' : x.created_by}` : '管理员令牌 / 系统';
const fmtSize = n => n == null ? '' : n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : (n / 1024).toFixed(0) + ' KB';
const DS_LABEL = { mysql: 'MySQL', postgresql: 'PostgreSQL', oracle: 'Oracle', starrocks: 'StarRocks', api: 'HTTP API' };
const DEFAULT_PORT = { mysql: 3306, postgresql: 5432, oracle: 1521, starrocks: 9030 };
function jsonOrThrow(txt, what) {
  if (!txt || !txt.trim()) return {};
  try { const v = JSON.parse(txt); if (typeof v !== 'object' || Array.isArray(v)) throw 0; return v; }
  catch (e) { throw new Error(what + ' 不是合法的 JSON 对象'); }
}
const pretty = v => JSON.stringify(v || {}, null, 2);

const state = reactive({ page: location.hash.slice(1) || 'home', meta: null, needLogin: false, me: null, authCfg: null });
window.addEventListener('hashchange', () => { state.page = location.hash.slice(1) || 'home'; });

// 模板和报告库复用同一个生成入口，生成后刷新报告库。
const reportDraft = ref(null);
const reportRevision = ref(0);
function startReport(template = null) { reportDraft.value = { template }; }

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
  props: { title: String, wide: Boolean, compact: Boolean, busy: Boolean }, emits: ['close'],
  template: `<dialog ref="dialog" class="drawer" :class="{wide, compact}" :aria-label="title" @cancel.prevent="close" @click.self="backdrop">
    <div class="drawer-head"><h2>{{title}}</h2><button class="x" type="button" aria-label="关闭" :disabled="busy" @click="close" v-html="icon('close')"></button></div>
    <div v-if="toasts.length" class="msgs dialog-feedback" aria-live="polite"><div v-for="t in toasts" :key="t.id" class="msg" :class="t.kind">{{t.msg}}</div></div>
    <div class="drawer-body"><slot/></div>
    <div class="drawer-foot" v-if="$slots.foot"><slot name="foot"/></div></dialog>`,
  setup(props, { emit }) {
    const dialog = ref(null);
    // 原生弹窗管理焦点与背景交互，关闭后焦点回到触发按钮。
    onMounted(() => dialog.value.showModal());
    onBeforeUnmount(() => dialog.value.close());
    function close() { if (!props.busy) emit('close'); }
    function backdrop(e) {
      const { left, right, top, bottom } = dialog.value.getBoundingClientRect();
      if (e.clientX < left || e.clientX > right || e.clientY < top || e.clientY > bottom) close();
    }
    return { dialog, close, backdrop, icon, toasts };
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
            <a :href="'/reports/'+r.id" target="_blank" rel="noopener">
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
      { page: 'mine', label: '我的令牌', icon: 'key', description: '查看个人权限和接入指引', visible: canVisitMine() }
    ].filter(entry => entry.visible !== false));
    return { ...reports, admin, entries, fmtTime, icon };
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
      <p class="form-intro">报告会按生成时的数据保存，可在报告库中查看或下载。</p>
      <div class="collection-state" v-if="loading" role="status">正在加载模板…</div>
      <div class="msg err" v-else-if="loadError" role="alert">模板加载失败。<button class="link" type="button" @click="load">重试</button></div>
      <div class="collection-state" v-else-if="!list.length">
        还没有可用模板。先创建模板，或通过已接入的 AI 助手提交分析需求。
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
        <div v-if="error" class="msg err" role="alert">{{error}}</div>
      </template>
    </form>
    <template #foot>
      <template v-if="result">
        <a class="btn" href="#reports" @click="$emit('close')">前往报告库</a>
        <a class="btn primary" :href="'/reports/'+result.id" target="_blank" rel="noopener">查看报告</a>
      </template>
      <template v-else>
        <button class="btn" :disabled="busy" @click="$emit('close')">取消</button>
        <button class="btn primary" type="submit" form="generate-report-form" :disabled="!selected || busy || loading || !!loadError">{{busy ? '正在生成…' : '生成报告'}}</button>
      </template>
    </template>
  </Drawer>`,
  setup(props) {
    const { list, loading, loadError, load } = useCollection('/api/templates', props.template ? [props.template] : []);
    const selectedId = ref(props.template?.id ?? null), name = ref(''), busy = ref(false), error = ref(''), result = ref(null);
    const selected = computed(() => list.value.find(t => t.id === selectedId.value));
    watch(selectedId, () => { error.value = ''; });
    async function generate() {
      if (!selected.value || busy.value || loading.value || loadError.value) return;
      busy.value = true;
      error.value = '';
      try {
        result.value = await api('/api/templates/' + selectedId.value + '/render', { method: 'POST', body: name.value ? { name: name.value } : {} });
        reportRevision.value += 1;
      } catch (e) { error.value = e.message; }
      finally { busy.value = false; }
    }
    return { list, loading, loadError, load, selectedId, selected, name, busy, error, result, generate, admin: computed(isAdmin), icon };
  }
};

// ------------------------------------------------------------ 数据源
function emptyDs() {
  return { id: null, name: '', type: 'mysql', description: '', enabled: true, secret: '', has_secret: false,
    host: '', port: '', database: '', username: '', service_name: '', sid: '', base_url: '', records_path: '',
    auth_header: 'Authorization', auth_scheme: 'Bearer', headersTxt: '{}', endpointsTxt: '{}', extraTxt: '{}' };
}
const DB_KEYS = ['host', 'port', 'database', 'username', 'service_name', 'sid'];
const API_KEYS = ['base_url', 'records_path', 'auth_header', 'auth_scheme'];
function dsToForm(d) {
  const f = emptyDs(), cfg = { ...(d.config || {}) };
  Object.assign(f, { id: d.id, name: d.name, type: d.type, description: d.description, enabled: d.enabled, has_secret: d.has_secret });
  [...DB_KEYS, ...API_KEYS].forEach(k => { if (cfg[k] != null) { f[k] = cfg[k]; delete cfg[k]; } });
  f.headersTxt = pretty(cfg.headers); delete cfg.headers;
  f.endpointsTxt = pretty(cfg.endpoints); delete cfg.endpoints;
  f.extraTxt = pretty(cfg);
  return f;
}
function formToDs(f) {
  const cfg = jsonOrThrow(f.extraTxt, '高级配置');
  if (f.type === 'api') {
    API_KEYS.forEach(k => { if (f[k] !== '' && f[k] != null) cfg[k] = f[k]; });
    cfg.headers = jsonOrThrow(f.headersTxt, '请求头');
    cfg.endpoints = jsonOrThrow(f.endpointsTxt, '接口定义');
  } else {
    DB_KEYS.forEach(k => { if (f[k] !== '' && f[k] != null) cfg[k] = k === 'port' ? Number(f[k]) : f[k]; });
  }
  const body = { name: f.name, type: f.type, description: f.description, enabled: f.enabled, config: cfg };
  if (f.secret) body.secret = f.secret;
  return body;
}
const DsBrowser = {
  components: { Drawer, DataTable }, props: ['ds'], emits: ['close'],
  template: `<Drawer :title="'浏览：' + ds.name" wide @close="$emit('close')">
    <div class="subtabs"><button :class="{on:tab==='tables'}" @click="tab='tables'">数据表</button><button :class="{on:tab==='sql'}" @click="tab='sql'">SQL 查询</button></div>
    <div v-if="tab==='tables'" style="display:grid;grid-template-columns:260px 1fr;gap:16px">
      <div><input class="in" v-model="kw" placeholder="筛选表名…" style="margin-bottom:8px">
        <div style="max-height:62vh;overflow:auto">
          <div v-for="t in shown" class="hit" style="padding:8px 10px;cursor:pointer;margin-bottom:6px" :style="cur===t.name?'border-color:var(--primary)':''" @click="open(t.name)">
            <div class="mono">{{t.name}}</div><div class="small muted" v-if="t.comment">{{t.comment}}</div></div>
          <div v-if="!tables.length" class="muted small">{{loading ? '加载中…' : '无数据表'}}</div></div></div>
      <div v-if="desc">
        <div class="actions" style="margin-bottom:10px"><b class="mono">{{desc.table}}</b><span class="badge">行数 {{desc.row_count ?? '未知'}}</span><span class="small muted">{{desc.profile_basis}}</span></div>
        <h4 style="margin:6px 0">字段画像</h4>
        <div class="scroll-x" style="max-height:300px"><table class="mini"><thead><tr><th>字段</th><th>类型</th><th>注释</th><th>推断</th><th>空值率</th><th>去重</th><th>范围 / 高频值</th></tr></thead>
          <tbody><tr v-for="p in profileRows"><td class="mono">{{p.column}}</td><td class="small">{{p.type}}</td><td>{{p.comment}}</td><td><span class="badge">{{p.kind}}</span></td>
          <td>{{p.null_ratio==null?'':(p.null_ratio*100).toFixed(1)+'%'}}</td><td>{{p.distinct}}</td>
          <td class="small">{{p.min!=null ? p.min+' ~ '+p.max : ''}}{{p.top_values ? p.top_values.slice(0,5).map(x=>x.value).join('、') : ''}}</td></tr></tbody></table></div>
        <h4 style="margin:14px 0 6px">样本数据</h4><DataTable :columns="desc.sample_columns" :rows="desc.sample_rows" :max="260"/>
      </div>
      <div v-else class="empty">选择左侧数据表查看结构与数据画像</div>
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
    const shown = computed(() => tables.value.filter(t => !kw.value || t.name.toLowerCase().includes(kw.value.toLowerCase())));
    const profileRows = computed(() => {
      if (!desc.value) return [];
      const types = Object.fromEntries((desc.value.columns || []).map(c => [c.name, c]));
      return desc.value.profile.map(p => ({ ...p, type: (types[p.column] || {}).type || '', comment: p.comment || (types[p.column] || {}).comment || '' }));
    });
    onMounted(async () => { loading.value = true; try { tables.value = await run(() => api(`/api/datasources/${props.ds.id}/tables`)); } finally { loading.value = false; } });
    async function open(name) { cur.value = name; desc.value = null; desc.value = await run(() => api(`/api/datasources/${props.ds.id}/describe?table=${encodeURIComponent(name)}&sample_rows=8`)); if (!sql.value) sql.value = `SELECT * FROM ${name}`; }
    async function query() { loading.value = true; try { res.value = await run(() => api(`/api/datasources/${props.ds.id}/query`, { method: 'POST', body: { sql: sql.value, limit: 500 } })); } finally { loading.value = false; } }
    return { tab, tables, kw, shown, desc, cur, open, loading, sql, res, query, profileRows };
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
        <div class="row2" v-if="form.type==='oracle'"><div class="field"><label>SID <small>未填服务名时使用</small></label><input class="in" v-model="form.sid"></div><div></div></div>
        <div class="row2"><div class="field"><label>用户名</label><input class="in" v-model="form.username"></div>
          <div class="field"><label>密码</label><input class="in" type="password" v-model="form.secret" :placeholder="form.has_secret?'已设置，留空则不修改':''" autocomplete="new-password"></div></div>
        <div class="field"><label>高级配置 <small>JSON：charset / sslmode / search_path / schema 等</small></label><textarea class="in code" v-model="form.extraTxt" rows="3"></textarea></div>
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
      <div class="field"><label>描述</label><textarea class="in" v-model="form.description" rows="2"></textarea></div>
      <label class="check"><input type="checkbox" v-model="form.enabled"> 启用</label>
      <div class="msg" :class="testRes.ok?'ok':'err'" v-if="testRes" style="margin-top:12px">{{testRes.text}}</div>
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
      busy.value = true;
      try {
        const body = run(() => formToDs(form.value));
        const b = await body;
        await run(() => form.value.id ? api(`/api/datasources/${form.value.id}`, { method: 'PUT', body: b }) : api('/api/datasources', { method: 'POST', body: b }), '已保存');
        form.value = null; load();
      } finally { busy.value = false; }
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
const Datasets = {
  components: { Drawer },
  template: `<div>
    <div class="page-head"><div><h1>业务场景</h1><p>按业务主题找数据，查看每个场景可用于分析的数据表。</p></div>
      <div class="actions" v-if="admin"><button class="btn" @click="reindex">重建检索索引</button><button class="btn primary" @click="edit()">新建业务场景</button></div></div>
    <section class="card list-panel" aria-label="业务场景列表" :aria-busy="loading">
      <div class="list-tools">
        <label class="search-field"><span class="ico" v-html="icon('search')"></span><input type="search" v-model="kw" placeholder="搜索业务场景、描述或标签" aria-label="搜索业务场景"></label>
        <span class="list-count" v-if="!loading && !loadError" aria-live="polite">{{shown.length}} 个场景</span>
      </div>
      <div v-if="loading" class="collection-state" role="status">正在加载业务场景…</div>
      <div v-else-if="loadError" class="collection-state" role="alert">业务场景加载失败。<button class="link" @click="load">重试</button></div>
      <div v-else-if="!shown.length" class="collection-state">
        <span class="empty-icon" v-html="icon('set')"></span><h2>{{kw.trim() ? '未找到匹配的场景' : '暂无可用的业务场景'}}</h2>
        <p>{{kw.trim() ? '可以改用业务主题或常见指标搜索。' : admin ? '新建业务场景并添加该主题下的数据表。' : '如需使用业务数据，请联系管理员开通权限。'}}</p>
        <button v-if="kw.trim()" class="btn" @click="kw=''">清除搜索</button>
        <button v-else-if="admin" class="btn" @click="edit()">新建业务场景</button>
        <a v-else-if="hasMine" class="btn" href="#mine">查看我的数据权限</a>
      </div>
      <div v-else class="scroll-x" tabindex="0" aria-label="业务场景表格">
        <table class="tbl scene-table" role="table"><thead><tr><th scope="col">业务场景</th><th scope="col">分析范围</th><th scope="col">数据表</th><th v-if="admin" scope="col">状态</th><th scope="col">操作</th></tr></thead>
          <tbody><tr v-for="d in shown" :key="d.id">
            <td><b>{{d.business_scenario}}</b><div class="small muted">{{(d.tags||[]).join(' · ')}}</div></td>
            <td class="scene-description"><div class="clip">{{d.business_description || '—'}}</div></td><td data-label="数据表">{{d.table_count}} 张</td>
            <td v-if="admin" data-label="状态"><span class="badge" :class="!d.enabled ? '' : d.vectorized ? 'green' : 'amber'">{{!d.enabled ? '停用' : d.vectorized ? '可用' : '索引待更新'}}</span></td>
            <td><div class="links"><button class="link" @click="view(d)">查看场景</button><template v-if="admin"><button class="link" @click="edit(d)">编辑</button><button class="link danger" @click="del(d)">删除</button></template></div></td>
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
      <h4 style="margin:16px 0 8px">数据表 <small class="muted" v-if="!admin">（仅显示你有权限的表）</small></h4>
      <table class="tbl card"><thead><tr><th scope="col">数据表名</th><th scope="col">数据表描述</th></tr></thead>
        <tbody><tr v-for="t in detail.tables"><td class="mono">{{t.table_name}}</td><td>{{t.table_description}}</td></tr>
        <tr v-if="!detail.tables.length"><td colspan="2" class="empty">无</td></tr></tbody></table>
    </Drawer>
    <Drawer v-if="form" :title="form.id ? '编辑业务场景' : '新建业务场景'" wide @close="form=null">
      <div class="row2"><div class="field"><label>业务场景</label><input class="in" v-model="form.business_scenario" placeholder="如：零售销售经营分析"></div>
        <div class="field"><label>数据源</label><select class="in" v-model="form.datasource_id"><option :value="null" disabled>请选择</option><option v-for="s in sources" :value="s.id">{{s.name}}（{{DS_LABEL[s.type]}}）</option></select></div></div>
      <div class="field"><label for="scene-description">业务描述 <small>说明数据范围、指标及适用的分析问题</small></label><textarea id="scene-description" class="in" v-model="form.business_description" rows="4"></textarea></div>
      <div class="field"><label for="scene-approach">分析思路 <small>选填</small></label>
        <p class="field-help" id="scene-approach-help">说明先看什么、怎么比较、需要验证哪些假设。也可补充报告应回答的问题、适合的图表和需要切换的筛选条件。</p>
        <textarea id="scene-approach" class="in" v-model="form.analysis_approach" rows="4" aria-describedby="scene-approach-help" placeholder="例如：先比较各渠道的销售增速，再找出毛利下降的主要品类，核对促销前后的变化。"></textarea></div>
      <div class="field"><label>标签 <small>用逗号分隔，帮助同事查找场景</small></label><input class="in" v-model="form.tagsTxt"></div>
      <div class="field"><label>数据表 <small>{{form.tables.length}} 张</small></label>
        <table class="tbl tbl-editor card"><thead><tr><th style="width:36%">数据表名</th><th>数据表描述</th><th style="width:60px"></th></tr></thead>
          <tbody><tr v-for="(t,i) in form.tables"><td><input class="in mono" v-model="t.table_name" placeholder="schema.table"></td>
            <td><input class="in" v-model="t.table_description"></td><td><button class="link danger" @click="form.tables.splice(i,1)">移除</button></td></tr></tbody></table>
        <div class="actions" style="margin-top:8px"><button class="btn sm" @click="form.tables.push({table_name:'',table_description:''})">+ 手动添加</button>
          <button class="btn sm" @click="pick" :disabled="!form.datasource_id">从数据源选择…</button></div>
        <div v-if="picker" class="card card-pad" style="margin-top:10px;max-height:300px;overflow:auto">
          <input class="in" v-model="pickKw" placeholder="筛选…" style="margin-bottom:8px">
          <label v-for="t in pickShown" class="check" style="display:flex;padding:4px 0"><input type="checkbox" :checked="has(t.name)" @change="togglePick(t)"> <span class="mono">{{t.name}}</span> <span class="muted small">{{t.comment}}</span></label>
          <div v-if="!picker.length" class="muted small">加载中或无表</div>
        </div></div>
      <label class="check"><input type="checkbox" v-model="form.enabled"> 启用（停用后不参与检索）</label>
      <template #foot><button class="btn" @click="form=null">取消</button><button class="btn primary" @click="save" :disabled="busy">保存业务场景</button></template>
    </Drawer>
  </div>`,
  setup() {
    const scenes = useCollection('/api/datasets');
    const { load } = scenes;
    const sources = ref([]), form = ref(null), busy = ref(false), q = ref(''), sr = ref(null), picker = ref(null), pickKw = ref(''), detail = ref(null);
    const kw = ref(''), searching = ref(false), searchError = ref('');
    const shown = computed(() => scenes.list.value.filter(d => matches(kw.value, d.business_scenario, d.business_description, ...(d.tags || []))));
    const admin = computed(isAdmin);
    async function view(d) { detail.value = await run(() => api('/api/datasets/' + d.id)); }
    onMounted(async () => { if (admin.value) sources.value = await run(() => api('/api/datasources')); });
    async function edit(d) {
      picker.value = null;
      if (d) { const full = await run(() => api(`/api/datasets/${d.id}`)); form.value = { ...full, tagsTxt: (full.tags || []).join(', '), tables: full.tables.map(t => ({ ...t })) }; }
      else form.value = { id: null, business_scenario: '', business_description: '', analysis_approach: '', datasource_id: null, tagsTxt: '', tables: [], enabled: true };
    }
    async function save() {
      busy.value = true;
      const f = form.value;
      const body = { business_scenario: f.business_scenario, business_description: f.business_description, analysis_approach: f.analysis_approach, datasource_id: f.datasource_id,
        tags: f.tagsTxt.split(/[,，]/).map(s => s.trim()).filter(Boolean), enabled: f.enabled, tables: f.tables.filter(t => t.table_name.trim()) };
      try { await run(() => f.id ? api(`/api/datasets/${f.id}`, { method: 'PUT', body }) : api('/api/datasets', { method: 'POST', body }), '已保存'); form.value = null; load(); }
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
    async function pick() { picker.value = []; picker.value = await run(() => api(`/api/datasources/${form.value.datasource_id}/tables`)); }
    const pickShown = computed(() => (picker.value || []).filter(t => !pickKw.value || t.name.includes(pickKw.value)));
    const has = n => form.value.tables.some(t => t.table_name === n);
    function togglePick(t) { const i = form.value.tables.findIndex(x => x.table_name === t.name); if (i >= 0) form.value.tables.splice(i, 1); else form.value.tables.push({ table_name: t.name, table_description: t.comment || '' }); }
    return { ...scenes, admin, detail, view, shown, kw, sources, form, busy, q, sr, searching, searchError, picker, pickKw, pickShown, edit, save, del, search, reindex, pick, has, togglePick, DS_LABEL, icon, hasMine: computed(canVisitMine) };
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
            <td><div class="links"><button class="link" @click="view(t)">查看</button><button class="link" @click="gen(t)">生成报告</button>
              <button class="link danger" @click="del(t)">删除</button></div></td>
          </tr></tbody>
        </table></div>
      </section>
    </template>
    <template v-else-if="!editing">
      <div class="page-head">
        <div><h1 ref="templateHeading" tabindex="-1">{{form.name}}</h1><p>{{form.business_scenario || '未关联业务场景'}} · v{{form.version}}</p></div>
        <div class="actions"><button class="btn ghost" @click="form=null">返回模板列表</button>
          <button class="btn" @click="edit(form)">编辑模板</button><button class="btn primary" @click="gen(form)">生成报告</button></div>
      </div>
      <div class="card card-pad template-brief">
        <h2>模板用途</h2>
        <p class="business-copy">{{form.business_summary || form.analysis_goal || form.user_requirement || '暂未填写用途说明。'}}</p>
        <template v-if="form.business_summary && form.analysis_goal && form.analysis_goal !== form.business_summary">
          <h2>分析目标</h2><p class="business-copy">{{form.analysis_goal}}</p>
        </template>
        <template v-if="form.summary?.sections?.some(Boolean)">
          <h2>报告章节</h2>
          <ol><li v-for="(title, i) in form.summary.sections.filter(Boolean)" :key="i">{{title}}</li></ol>
        </template>
      </div>
    </template>
    <template v-else>
      <div class="page-head"><h1 ref="templateHeading" tabindex="-1">{{form.id ? '编辑模板' : '新建模板'}}</h1>
        <div class="actions"><button class="btn ghost" :disabled="busy" @click="cancelEdit">取消</button>
          <button v-if="technical" class="btn" @click="save(true)" :disabled="busy || !form.markdown.trim()">保存并生成…</button>
          <button class="btn primary" @click="save(false)" :disabled="busy || !form.markdown.trim()">保存模板</button></div></div>
      <div class="card card-pad meta-bar">
        <div class="field"><label for="template-name">模板名称</label><input id="template-name" class="in" v-model="form.name" placeholder="留空时使用正文标题"></div>
        <div class="field"><label for="template-scenario">业务场景</label>
          <select id="template-scenario" class="in" v-model="form.dataset_id"><option :value="null">选择默认取数的业务场景</option>
            <option v-if="form.dataset_id && !datasets.some(d => d.id === form.dataset_id)" :value="form.dataset_id">原场景（当前不可选）</option>
            <option v-for="d in datasets" :key="d.id" :value="d.id">{{d.business_scenario}}</option></select></div>
        <div class="field"><label for="template-requirement">原始分析需求 <small>选填</small></label><input id="template-requirement" class="in" v-model="form.user_requirement" placeholder="填写需要回答的业务问题"></div>
        <div class="field template-summary-field"><label for="template-business-summary">业务说明 <small>选填</small></label>
          <textarea id="template-business-summary" class="in" v-model="form.business_summary" rows="4" placeholder="例如：用于月度销售复盘，比较各渠道的销售额和毛利率，找出变化最大的区域和品类。"></textarea></div>
      </div>
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
      form.value = t ? { ...t } : { id: null, name: '', dataset_id: null, user_requirement: '', business_summary: '', markdown: '', version: 1 };
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
        user_requirement: f.user_requirement, business_summary: f.business_summary };
      try {
        const t = await run(() => f.id ? api(`/api/templates/${f.id}`, { method: 'PUT', body }) : api('/api/templates', { method: 'POST', body }), '模板已保存');
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
const Reports = {
  template: `<div>
    <div class="page-head"><div><h1>报告库</h1><p>{{admin ? '查看所有用户已生成的报告。' : '查看你已生成的报告。'}}下载 HTML 文件后可离线打开。</p></div>
      <button class="btn primary" @click="create"><span class="ico" v-html="icon('plus')"></span>生成报告</button></div>
    <section class="card list-panel" aria-label="报告列表" :aria-busy="loading">
      <div class="list-tools">
        <label class="search-field"><span class="ico" v-html="icon('search')"></span><input type="search" v-model="kw" placeholder="搜索报告名称或来源模板" aria-label="搜索报告"></label>
        <span v-if="!loading && !loadError" class="list-count" aria-live="polite">{{shown.length}} 份报告</span>
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
        <thead><tr><th scope="col">报告名称</th><th scope="col">来源模板</th><th scope="col" v-if="admin">创建人</th><th scope="col">生成时间</th><th scope="col">操作</th></tr></thead>
        <tbody><tr v-for="r in shown" :key="r.id">
          <td><a class="record-name" :href="'/reports/'+r.id" target="_blank" rel="noopener">{{r.name}}</a>
            <div class="small muted">{{fmtSize(r.size)}}<span v-if="r.meta?.queries"> · 查询结果 {{queryRows(r)}} 行</span></div>
            <details v-if="r.meta?.warnings?.length" class="small report-warnings"><summary>{{r.meta.warnings.length}} 条生成提示</summary>
              <ul><li v-for="(w,i) in r.meta.warnings" :key="i">{{w}}</li></ul></details></td>
          <td data-label="来源模板">{{r.template_name || '—'}} <span class="badge" v-if="r.template_version">v{{r.template_version}}</span></td>
          <td v-if="admin" data-label="创建人">{{creator(r)}}</td><td data-label="生成时间"><time :datetime="r.created_at">{{fmtTime(r.created_at)}}</time></td>
          <td><div class="links"><a :href="'/reports/'+r.id" target="_blank" rel="noopener">查看</a><a :href="'/reports/'+r.id+'/download'">下载 HTML</a>
            <button class="link danger" @click="del(r)">删除</button></div></td>
        </tr></tbody>
      </table></div>
    </section>
  </div>`,
  setup() {
    const reports = useCollection('/api/reports');
    const kw = ref('');
    const shown = computed(() => reports.list.value.filter(r => matches(kw.value, r.name, r.template_name, r.creator_name, r.created_by)));
    const queryRows = r => Object.values(r.meta.queries).reduce((total, q) => total + (q.rows || 0), 0);
    watch(reportRevision, reports.load);
    async function del(r) {
      if (!confirm('删除报告「' + r.name + '」？')) return;
      await run(() => api('/api/reports/' + r.id, { method: 'DELETE' }), '已删除');
      reports.load();
    }
    return { ...reports, admin: computed(isAdmin), creator, shown, kw, del, create: () => startReport(), fmtTime, fmtSize, queryRows, icon };
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
          <p class="small muted" style="margin:0 0 8px">业务场景规则 ≥ 通用规则 合并为一份，作为约束交给智能体；同类要求中排在前面的优先。智能体可按自己的判断调整，没有涉及的方面由智能体自由决定。</p>
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

// ------------------------------------------------------------ 权限与令牌
const Perms = {
  components: { Drawer },
  template: `<div>
    <div class="page-head"><div><h1>员工与授权</h1><p>为员工或角色开放业务场景和数据表，也可在这里管理访问令牌。</p></div></div>
    <div class="subtabs"><button v-for="t in tabs" :class="{on:tab===t[0]}" @click="tab=t[0]">{{t[1]}}</button></div>

    <template v-if="tab==='emp'">
      <div class="actions" style="margin-bottom:12px"><button class="btn primary" @click="editEmp()">新增员工</button></div>
      <div class="card scroll-x" tabindex="0" aria-label="数据表格"><table class="tbl"><thead><tr><th>工号</th><th>姓名</th><th>部门</th><th>角色</th><th>类型</th><th>注册来源</th><th>状态</th><th>操作</th></tr></thead>
        <tbody><tr v-for="e in emps"><td class="mono">{{e.emp_no}}</td><td>{{e.name}}</td><td>{{e.department}}</td>
          <td><span v-for="r in e.roles" class="badge" style="margin-right:4px">{{r}}</span><span v-if="e.direct_grants" class="small muted">+{{e.direct_grants}} 项直授</span></td>
          <td><span class="badge" :class="e.is_admin?'red':''">{{e.is_admin?'管理员（全部数据）':'普通用户'}}</span></td>
          <td class="small">{{({manual:'管理员登记', iam_login:'IAM 登录', mcp:'首次调用 MCP'})[e.registered_via] || e.registered_via}}</td>
          <td><span class="dot" :class="{on:e.enabled}"></span>{{e.enabled?'启用':'停用'}}</td>
          <td><div class="links"><button class="link" @click="issueFor(e)">签发令牌</button><button class="link" @click="checkEmp(e.emp_no)">查看权限</button>
            <button class="link" @click="editEmp(e)">编辑</button><button class="link danger" @click="delEmp(e)">删除</button></div></td></tr>
          <tr v-if="!emps.length"><td colspan="8" class="empty">暂无员工</td></tr></tbody></table></div>
    </template>

    <template v-if="tab==='grant'">
      <div class="card card-pad" style="margin-bottom:14px">
        <h3>新增授权</h3>
        <div class="row3" style="grid-template-columns:150px 1fr 1.4fr">
          <div class="field"><label>授权对象</label><div class="seg"><button :class="{on:g.subject_type==='role'}" @click="g.subject_type='role'">角色</button><button :class="{on:g.subject_type==='employee'}" @click="g.subject_type='employee'">员工</button></div></div>
          <div class="field"><label>{{g.subject_type==='role'?'角色编码':'员工工号'}}</label>
            <input v-if="g.subject_type==='role'" class="in" v-model="g.subject" list="role-list" placeholder="如 user（全体普通用户）">
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

    <template v-if="tab==='token'">
      <div class="card card-pad" style="margin-bottom:14px">
        <h3>签发 MCP 访问令牌</h3>
        <div class="row3" style="grid-template-columns:1.2fr 120px 1.5fr auto;align-items:end">
          <div class="field"><label>员工</label><select class="in" v-model="tk.emp_no"><option value="" disabled>选择员工</option><option v-for="e in emps.filter(e=>e.enabled)" :value="e.emp_no">{{e.emp_no}} {{e.name}}</option></select></div>
          <div class="field"><label>有效期（天）</label><input class="in" type="number" v-model.number="tk.days"></div>
          <div class="field"><label>备注</label><input class="in" v-model="tk.note" placeholder="如：张敏的 Claude Code"></div>
          <div class="field"><button class="btn primary" @click="issue" :disabled="!tk.emp_no">签发</button></div>
        </div>
        <div v-if="issued" class="msg ok" style="margin-top:4px">令牌已签发（仅显示这一次，请立即复制给 {{issued.emp_no}}）：
          <pre class="block" style="margin-top:8px">{{issued.token}}</pre>
          <div class="small" style="margin-top:8px">Claude Code 注册命令：</div>
          <pre class="block" style="margin-top:4px">claude mcp add --transport http smartreportgen {{mcpUrl}} --header "Authorization: Bearer {{issued.token}}"</pre>
          <div class="actions" style="margin-top:8px"><button class="btn sm" @click="copy(issued.token)">复制令牌</button><button class="btn sm" @click="copy(cmd)">复制命令</button></div></div>
        <div class="small muted" style="margin-top:8px">令牌为 JWT，工号位于 claim「{{jwt.emp_claim}}」，iss={{jwt.issuer}}，aud={{jwt.audience}}。{{jwt.external_public_key ? '已配置外部公钥，也接受企业 SSO 签发的令牌。' : '如需接入企业 SSO 签发的令牌，配置 JWT_PUBLIC_KEY。'}}</div>
      </div>
      <div class="card scroll-x" tabindex="0" aria-label="数据表格"><table class="tbl"><thead><tr><th>员工</th><th>备注</th><th>签发时间</th><th>过期时间</th><th>最近使用</th><th>状态</th><th>操作</th></tr></thead>
        <tbody><tr v-for="t in tokens"><td><span class="mono">{{t.emp_no}}</span> {{t.name}}</td><td>{{t.note}}</td><td class="small">{{fmtTime(t.created_at)}}</td><td class="small">{{fmtTime(t.expires_at)}}</td><td class="small">{{fmtTime(t.last_used_at) || '-'}}</td>
          <td><span class="badge" :class="t.revoked?'red':t.expired?'amber':'green'">{{t.revoked?'已吊销':t.expired?'已过期':'有效'}}</span></td>
          <td><button class="link danger" v-if="!t.revoked" @click="revoke(t)">吊销</button></td></tr>
          <tr v-if="!tokens.length"><td colspan="7" class="empty">暂无令牌</td></tr></tbody></table></div>
    </template>

    <template v-if="tab==='check'">
      <div class="card card-pad">
        <div class="actions"><select class="in" v-model="chk" style="width:260px"><option value="" disabled>选择员工</option><option v-for="e in emps" :value="e.emp_no">{{e.emp_no}} {{e.name}}</option></select><button class="btn primary" @click="checkEmp(chk)" :disabled="!chk">查询有效权限</button></div>
        <div v-if="chkRes" style="margin-top:14px">
          <div class="msg warn" v-if="!chkRes.registered">该工号未登记或已停用：MCP 调用将被拒绝访问任何数据。</div>
          <div class="msg ok" v-else-if="chkRes.all">管理员：可访问全部业务场景与数据表。</div>
          <div v-for="d in chkRes.datasets" class="hit" v-if="!chkRes.all"><h4>{{d.business_scenario}} <span class="score">dataset_id={{d.dataset_id}}</span></h4>
            <div><span v-for="t in d.tables" class="badge" style="margin:2px 6px 2px 0"><span class="mono">{{t}}</span></span></div></div>
          <div v-if="chkRes.registered && !chkRes.all && !chkRes.datasets.length" class="muted">没有任何数据表权限。</div>
        </div>
      </div>
    </template>

    <Drawer v-if="emp" :title="emp._new ? '新增员工' : '编辑员工 ' + emp.emp_no" @close="emp=null">
      <div class="row2"><div class="field"><label>工号</label><input class="in mono" v-model="emp.emp_no" :disabled="!emp._new"></div>
        <div class="field"><label>姓名</label><input class="in" v-model="emp.name"></div></div>
      <div class="row2"><div class="field"><label>部门</label><input class="in" v-model="emp.department"></div>
        <div class="field"><label>角色 <small>逗号分隔，按角色授权时使用</small></label><input class="in" v-model="emp.rolesTxt"></div></div>
      <label class="check" style="margin-right:20px"><input type="checkbox" v-model="emp.is_admin"> 管理员（可访问全部数据；不勾选即普通用户）</label>
      <label class="check"><input type="checkbox" v-model="emp.enabled"> 启用</label>
      <template #foot><button class="btn" @click="emp=null">取消</button><button class="btn primary" @click="saveEmp">保存</button></template>
    </Drawer>
  </div>`,
  setup() {
    const tabs = [['emp', '员工'], ['grant', '表授权'], ['token', '访问令牌'], ['check', '权限查询']];
    const tab = ref('emp'), emps = ref([]), grants = ref([]), tokens = ref([]), datasets = ref([]), emp = ref(null);
    const g = reactive({ subject_type: 'role', subject: '', dataset_id: null, tables: ['*'] });
    const tk = reactive({ emp_no: '', days: 90, note: '' }), issued = ref(null), chk = ref(''), chkRes = ref(null), dsDetail = ref(null);
    const jwt = computed(() => (state.meta && state.meta.jwt) || {});
    const mcpUrl = computed(() => state.meta ? state.meta.mcp_url : '');
    const cmd = computed(() => issued.value ? `claude mcp add --transport http smartreportgen ${mcpUrl.value} --header "Authorization: Bearer ${issued.value.token}"` : '');
    const roles = computed(() => Array.from(new Set(emps.value.flatMap(e => e.roles || []))));
    const dsTables = computed(() => dsDetail.value ? dsDetail.value.tables : []);
    watch(() => g.dataset_id, async id => { dsDetail.value = id ? await api('/api/datasets/' + id) : null; });
    const load = async () => {
      [emps.value, grants.value, tokens.value, datasets.value] = await Promise.all([api('/api/employees'), api('/api/grants'), api('/api/tokens'), api('/api/datasets')]);
    };
    onMounted(load);
    function editEmp(e) { emp.value = e ? { ...e, rolesTxt: (e.roles || []).join(', ') } : { _new: true, emp_no: '', name: '', department: '', rolesTxt: '', is_admin: false, enabled: true }; }
    async function saveEmp() {
      const e = emp.value, body = { emp_no: e.emp_no, name: e.name, department: e.department, roles: e.rolesTxt.split(/[,，\s]+/).filter(Boolean), is_admin: e.is_admin, enabled: e.enabled };
      await run(() => e._new ? api('/api/employees', { method: 'POST', body }) : api('/api/employees/' + encodeURIComponent(e.emp_no), { method: 'PUT', body }), '已保存');
      emp.value = null; load();
    }
    async function delEmp(e) { if (!confirm(`删除员工 ${e.emp_no}？其直授权限会删除、令牌会被吊销。`)) return; await run(() => api('/api/employees/' + encodeURIComponent(e.emp_no), { method: 'DELETE' }), '已删除'); load(); }
    function toggleT(t) { const i = g.tables.indexOf(t); if (i >= 0) g.tables.splice(i, 1); else g.tables.push(t); }
    async function addGrant() { const r = await run(() => api('/api/grants', { method: 'POST', body: { ...g } })); toast(`新增 ${r.added} 条授权`, 'ok'); load(); }
    async function delGrant(x) { if (!confirm('撤销该授权？')) return; await run(() => api('/api/grants/' + x.id, { method: 'DELETE' }), '已撤销'); load(); }
    function issueFor(e) { tab.value = 'token'; tk.emp_no = e.emp_no; issued.value = null; }
    async function issue() {
      if (issuing.value) return;
      issuing.value = true;
      try {
        issued.value = await run(() => api('/api/me/tokens', { method: 'POST', body: { days: days.value, note: note.value } }), '令牌已创建');
        await load();
      } finally { issuing.value = false; }
    }
    async function revoke(t) { if (!confirm(`吊销 ${t.emp_no} 的令牌？使用该令牌的 MCP 客户端将立即失效。`)) return; await run(() => api(`/api/tokens/${t.jti}/revoke`, { method: 'POST' }), '已吊销'); load(); }
    async function checkEmp(no) { tab.value = 'check'; chk.value = no; chkRes.value = await run(() => api('/api/permissions/check?emp_no=' + encodeURIComponent(no))); }
    function copy(text) { navigator.clipboard.writeText(text).then(() => toast('已复制', 'ok'), () => toast('复制失败，请手动选择复制', 'err')); }
    return { tabs, tab, emps, grants, tokens, datasets, emp, g, tk, issued, chk, chkRes, jwt, mcpUrl, cmd, roles, dsTables,
      editEmp, saveEmp, delEmp, toggleT, addGrant, delGrant, issueFor, issue, revoke, checkEmp, copy, fmtTime };
  }
};

// ------------------------------------------------------------ 根组件
const Login = {
  template: `<main class="login-shell">
    <div class="card login">
      <a class="brand" href="/"><span class="brand-icon" v-html="icon('rep')"></span><span>报告管理台<small>SmartReportGen</small></span></a>
      <h1>登录报告管理台</h1><p class="login-intro">登录后可查看报告、模板及已授权的业务数据。</p>
      <template v-if="cfg">
        <template v-if="cfg.mode==='iam'">
          <a v-if="cfg.iam_configured" class="btn primary big" href="/auth/iam/login?next=/">使用海尔账号登录 <span class="ico" v-html="icon('arrow')"></span></a>
          <div v-else class="msg warn" role="status">海尔单点登录暂不可用，请联系系统管理员。</div>
        </template>
        <details v-if="cfg.admin_token_login" :open="cfg.mode!=='iam'" class="fallback">
          <summary>管理员令牌登录</summary>
          <form @submit.prevent="login">
            <div class="field"><label for="admin-token">管理员令牌</label><input id="admin-token" class="in" type="password" v-model="tok" autocomplete="current-password" required></div>
            <div class="msg err" v-if="error" role="alert">{{error}}</div>
            <button class="btn" :disabled="busy || !tok">{{busy ? '正在登录…' : '登录'}}</button>
          </form>
        </details>
        <div class="msg err" v-if="cfg.mode!=='iam' && !cfg.admin_token_login" role="alert">登录方式暂不可用，请联系系统管理员。</div>
      </template>
    </div>
  </main>`,
  setup() {
    const tok = ref(''), busy = ref(false), error = ref('');
    const cfg = computed(() => state.authCfg);
    async function login() {
      if (busy.value || !tok.value) return;
      busy.value = true;
      error.value = '';
      try {
        await api('/api/auth/token-login', { method: 'POST', body: { token: tok.value } });
        location.reload();
      } catch (e) { error.value = e.message; }
      finally { busy.value = false; }
    }
    return { tok, cfg, busy, error, login, icon };
  }
};

// ------------------------------------------------------------ 我的令牌（所有登录员工）
const Mine = {
  template: `<div>
    <div class="page-head"><div><h1>我的令牌</h1><p>连接 Codex 或 Claude Code，并查看 AI 助手可以使用的业务数据。</p></div></div>
    <div class="grid g2">
      <div class="card card-pad">
        <h2>账号信息</h2>
        <dl class="kv" v-if="state.me"><dt>工号</dt><dd class="mono">{{state.me.emp_no || '（管理员令牌登录）'}}</dd><dt>姓名</dt><dd>{{state.me.name || '-'}}</dd>
          <dt>身份</dt><dd><span class="badge" :class="state.me.is_admin?'red':''">{{state.me.is_admin ? '管理员' : '员工'}}</span> <span class="badge" v-if="!state.me.registered" style="margin-left:4px">未登记 / 已停用</span></dd>
          <dt>登录方式</dt><dd>{{({session:'海尔 IAM 单点登录', admin_token:'管理员令牌', open:'开发模式'})[state.me.via] || state.me.via}}</dd></dl>
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
        <p class="muted">个人令牌用于验证身份，使用你已有的数据权限。</p>
        <template v-if="canIssue">
          <div class="row2"><div class="field"><label for="token-days">有效期</label><select id="token-days" class="in" v-model.number="days"><option v-for="d in dayOpts" :value="d">{{d}} 天</option></select></div>
            <div class="field"><label for="token-note">用途备注 <small>选填</small></label><input id="token-note" class="in" v-model="note" placeholder="如：我的 Codex"></div></div>
          <button class="btn primary" :disabled="issuing" @click="issue">{{issuing ? '正在创建…' : '创建个人令牌'}}</button>
          <div v-if="issued" class="msg ok issued-token">令牌仅在创建后显示一次，请复制并妥善保存。
            <pre class="block">{{issued.token}}</pre>
            <div class="actions"><button class="btn sm" @click="copy(issued.token)">复制令牌</button><button class="btn sm" @click="copy('export SMARTREPORT_TOKEN=' + issued.token)">复制环境变量命令</button></div>
          </div>
          <details class="fallback">
            <summary>查看接入步骤</summary>
            <p>在启动 AI 助手的终端中，把个人令牌设为环境变量：</p>
            <pre class="block">export SMARTREPORT_TOKEN=&lt;个人令牌&gt;</pre>
            <p>Codex</p>
            <pre class="block">codex mcp add smartreportgen --url {{mcpUrl}} --bearer-token-env-var SMARTREPORT_TOKEN</pre>
            <p>Claude Code</p>
            <pre class="block">claude mcp add --transport http smartreportgen {{mcpUrl}} --header "Authorization: Bearer $SMARTREPORT_TOKEN"</pre>
          </details>
        </template>
        <div v-else class="msg warn">{{whyNot}}</div>
      </div>
    </div>
    <div class="card scroll-x token-list"><div class="card-pad"><h2>已创建的令牌</h2></div><table class="tbl"><thead><tr><th>备注</th><th>签发时间</th><th>过期时间</th><th>最近使用</th><th>状态</th><th>操作</th></tr></thead>
      <tbody><tr v-for="t in tokens"><td>{{t.note}}</td><td class="small">{{fmtTime(t.created_at)}}</td><td class="small">{{fmtTime(t.expires_at)}}</td><td class="small">{{fmtTime(t.last_used_at) || '-'}}</td>
        <td><span class="badge" :class="t.revoked?'red':t.expired?'amber':'green'">{{t.revoked?'已吊销':t.expired?'已过期':'有效'}}</span></td>
        <td><button class="link danger" v-if="!t.revoked && !t.expired" @click="revoke(t)">吊销</button></td></tr>
        <tr v-if="!tokens.length"><td colspan="6" class="empty">还没有个人令牌</td></tr></tbody></table></div>
  </div>`,
  setup() {
    const tokens = ref([]), perm = ref(null), issued = ref(null), note = ref(''), days = ref(30), issuing = ref(false);
    const maxDays = computed(() => (state.authCfg && state.authCfg.token_max_days) || 90);
    // 部署可配置更短的签发上限，确保下拉框始终包含可提交的有效期。
    const dayOpts = computed(() => [...new Set([7, 30, 60, 90, 180, 365, maxDays.value])].filter(d => d <= maxDays.value).sort((a,b) => a-b));
    days.value = Math.min(days.value, maxDays.value);
    const mcpUrl = computed(() => state.meta ? state.meta.mcp_url : '');
    const canIssue = computed(() => state.authCfg && state.authCfg.self_service_tokens && state.me && state.me.emp_no && state.me.registered);
    const whyNot = computed(() => !state.authCfg || !state.authCfg.self_service_tokens ? '未开启自助签发，请联系管理员签发令牌。'
      : !(state.me && state.me.emp_no) ? '请使用海尔 IAM 单点登录后签发个人令牌（管理员令牌登录没有工号）。' : '你的工号未登记或已停用，请联系管理员。');
    const load = async () => { [tokens.value, perm.value] = await Promise.all([api('/api/me/tokens'), api('/api/me/permissions')]); };
    onMounted(load);
    async function issue() { issued.value = await run(() => api('/api/me/tokens', { method: 'POST', body: { days: days.value, note: note.value } }), '令牌已签发'); load(); }
    async function revoke(t) { if (!confirm('吊销这枚令牌？使用它的 agent 将立即无法访问。')) return; await run(() => api(`/api/me/tokens/${t.jti}/revoke`, { method: 'POST' }), '已吊销'); load(); }
    function copy(text) { navigator.clipboard.writeText(text).then(() => toast('已复制', 'ok'), () => toast('复制失败，请手动选择复制', 'err')); }
    return { state, tokens, perm, issued, note, days, dayOpts, mcpUrl, canIssue, whyNot, issuing, issue, revoke, copy, fmtTime };
  }
};

const PAGES = { home: Home, datasources: Datasources, datasets: Datasets, templates: Templates, reports: Reports, styles: Styles, perms: Perms, mine: Mine };
// 页面地址继续使用原来的 hash，现有收藏和深链接可以直接打开。
const NAV_GROUPS = [
  { title: '报告', items: [['home', '管理总览', 'home'], ['reports', '报告库', 'rep'], ['templates', '报告模板', 'tpl']] },
  { title: '资源', items: [['datasets', '业务场景', 'set'], ['styles', '报告样式', 'sty']] },
  { title: '账号', items: [['mine', '我的令牌', 'key']] },
  { title: '系统管理', admin: true, items: [['datasources', '数据源', 'ds'], ['perms', '员工与授权', 'perm']] }
];
function canVisitMine() { return !!(state.me?.emp_no || state.authCfg?.mode === 'iam'); }

createApp({
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
          <button v-if="state.me.via!=='open'" type="button" class="btn ghost logout" @click="logout">退出登录</button>
        </div>
      </header>
      <main id="main-content" ref="main" class="main" tabindex="-1"><component :is="page" :key="current[0]"/></main>
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
        if (!cfg.open_mode && !cfg.me.logged_in) { state.needLogin = true; return; }
        state.me = cfg.me.logged_in ? cfg.me : { emp_no: null, name: '开发模式', is_admin: true, via: 'open', registered: true };
        loadMeta().catch(e => toast(e.message, 'err'));
      } catch (e) { bootError.value = e.message; }
    }
    onMounted(init);
    return { state, page, groups, currentGroup, current, person, menuOpen, main, reportDraft, bootError, init, icon, toasts, logout };
  }
}).mount('#app');
