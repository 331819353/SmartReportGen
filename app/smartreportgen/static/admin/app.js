/* SmartReportGen 管理台（Vue 3，无构建） */
const { createApp, reactive, ref, computed, onMounted, watch, nextTick } = Vue;

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
function go(p) { location.hash = p; }

const ICONS = {
  home: '<path d="M3 10.5 10 4l7 6.5V17a1 1 0 0 1-1 1h-4v-5H8v5H4a1 1 0 0 1-1-1z"/>',
  ds: '<ellipse cx="10" cy="5" rx="6.5" ry="2.5"/><path d="M3.5 5v10c0 1.4 2.9 2.5 6.5 2.5s6.5-1.1 6.5-2.5V5M3.5 10c0 1.4 2.9 2.5 6.5 2.5s6.5-1.1 6.5-2.5"/>',
  set: '<rect x="3" y="3" width="14" height="14" rx="2"/><path d="M3 8h14M8 8v9"/>',
  tpl: '<path d="M5 2.5h7l3.5 3.5v11.5h-10.5z"/><path d="M12 2.5V6h3.5M7.5 10h5M7.5 13h5"/>',
  rep: '<rect x="3" y="3" width="14" height="14" rx="2"/><path d="M7 14v-3M10 14V7M13 14v-5"/>',
  key: '<circle cx="7" cy="12.5" r="3.5"/><path d="M9.5 10 16 3.5M13.5 6l2 2M11.8 7.7l1.5 1.5"/>',
  perm: '<rect x="4" y="9" width="12" height="8.5" rx="1.8"/><path d="M7 9V6.5a3 3 0 0 1 6 0V9M10 12.5v2"/>',
  sty: '<circle cx="10" cy="10" r="7"/><circle cx="7" cy="8" r="1.2"/><circle cx="11" cy="6.5" r="1.2"/><circle cx="13.5" cy="10" r="1.2"/><path d="M10 17a2 2 0 0 1 0-4h1.5"/>'
};
const icon = k => `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${ICONS[k]}</svg>`;

// ------------------------------------------------------------ 通用组件
const Drawer = {
  props: ['title', 'wide'], emits: ['close'],
  template: `<div class="drawer-mask" @mousedown.self="$emit('close')"><div class="drawer" :class="{wide}">
    <div class="drawer-head"><h2>{{title}}</h2><button class="x" @click="$emit('close')">×</button></div>
    <div class="drawer-body"><slot/></div>
    <div class="drawer-foot" v-if="$slots.foot"><slot name="foot"/></div></div></div>`
};
const DataTable = {
  props: ['columns', 'rows', 'max'],
  template: `<div class="scroll-x" :style="{maxHeight: (max||360)+'px'}"><table class="mini"><thead><tr><th v-for="c in columns">{{c}}</th></tr></thead>
    <tbody><tr v-for="r in rows"><td v-for="v in r">{{v === null ? 'NULL' : v}}</td></tr></tbody></table></div>`
};

// ------------------------------------------------------------ 概览
const Home = {
  template: `<div>
    <div class="page-head"><div><h1>概览</h1><p>智能报告生成：MCP 工具 + Skill 编排，元数据存储于 PostgreSQL / pgvector</p></div></div>
    <div class="grid g4" v-if="m">
      <div class="card stat" v-for="s in statList" @click="go(s.page)" style="cursor:pointer"><div class="k">{{s.label}}</div><div class="v">{{m.stats[s.key]}}</div></div>
    </div>
    <div class="grid g2" style="margin-top:14px" v-if="m">
      <div class="card card-pad">
        <h3>MCP 接入</h3>
        <dl class="kv"><dt>MCP 地址</dt><dd><code>{{m.mcp_url}}</code></dd><dt>传输方式</dt><dd>Streamable HTTP</dd>
          <dt>鉴权</dt><dd>Bearer 令牌（JWT，含员工工号）· 按工号做表级权限控制</dd></dl>
        <p class="small muted" style="margin:12px 0 6px">在「权限与令牌」中为员工签发令牌后，Claude Code 中注册：</p>
        <pre class="block">claude mcp add --transport http smartreportgen {{m.mcp_url}} --header "Authorization: Bearer &lt;员工令牌&gt;"</pre>
      </div>
      <div class="card card-pad">
        <h3>检索与向量化</h3>
        <dl class="kv"><dt>向量提供方</dt><dd>{{m.embedding.provider}}</dd><dt>模型</dt><dd>{{m.embedding.model}}</dd><dt>维度</dt><dd>{{m.embedding.dim}}</dd>
          <dt>检索方式</dt><dd>pgvector 余弦相似度 + jieba 关键词（IDF 加权）→ RRF 融合</dd></dl>
        <div class="msg warn" style="margin-top:12px" v-if="m.embedding.provider==='hash'">当前为离线哈希向量（无语义理解）。生产环境建议在 .env 配置 EMBEDDING_PROVIDER=openai 接入 bge-m3 等中文向量模型。</div>
      </div>
      <div class="card card-pad" style="grid-column:1/-1">
        <h3>生成流程</h3>
        <ol style="margin:0;padding-left:20px;line-height:2">
          <li><b>解析需求</b> → <code>search_business_scenarios</code> 在有权限的业务场景中混合检索 → <code>get_dataset_tables</code> 获取有权限的数据表</li>
          <li><b>探查数据</b> → <code>describe_table</code> / <code>run_query</code>，确定分析目标、章节（维度视角）、结论与支撑图表、自适应布局</li>
          <li><b>全局筛选</b> → 设计多个级联筛选、预设场景与图表点击联动</li>
          <li><b>样式</b> → <code>get_style_rules</code> 获取通用规则（设计令牌）与场景私有规则，渲染时自动生效</li>
          <li><b>存储</b> → <code>validate_report_template</code> → <code>save_report_template</code> → <code>render_report_html</code> 生成并存储 HTML</li>
        </ol>
      </div>
    </div>
  </div>`,
  setup() {
    const m = computed(() => state.meta);
    const statList = [{ key: 'datasources', label: '数据源', page: 'datasources' }, { key: 'datasets', label: '数据集', page: 'datasets' },
      { key: 'templates', label: '报告模板', page: 'templates' }, { key: 'reports', label: '报告', page: 'reports' }, { key: 'style_rules', label: '样式规则', page: 'styles' },
      { key: 'employees', label: '已登记员工', page: 'perms' }];
    onMounted(loadMeta);
    return { m, statList, go };
  }
};
async function loadMeta() { state.meta = await api('/api/meta'); }

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
    <div class="page-head"><div><h1>数据源</h1><p>支持 MySQL、PostgreSQL、Oracle、StarRocks 与 HTTP API；密码/令牌加密存储，建议使用只读账号</p></div>
      <div class="actions"><button class="btn primary" @click="edit()">新建数据源</button></div></div>
    <div class="card"><table class="tbl"><thead><tr><th>名称</th><th>类型</th><th>连接</th><th>描述</th><th>状态</th><th>操作</th></tr></thead>
      <tbody><tr v-for="d in list"><td><b>{{d.name}}</b></td><td><span class="badge blue">{{DS_LABEL[d.type]}}</span></td>
        <td class="mono small">{{addr(d)}}</td><td><div class="clip">{{d.description}}</div></td>
        <td><span class="dot" :class="{on:d.enabled}"></span>{{d.enabled?'启用':'停用'}}</td>
        <td><div class="links"><button class="link" @click="test(d)">测试</button><button class="link" @click="browse=d">浏览</button>
          <button class="link" @click="edit(d)">编辑</button><button class="link danger" @click="del(d)">删除</button></div></td></tr>
        <tr v-if="!list.length"><td colspan="6" class="empty">暂无数据源</td></tr></tbody></table></div>
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
    const addr = d => d.type === 'api' ? (d.config.base_url || '') : `${d.config.host || ''}:${d.config.port || DEFAULT_PORT[d.type]}/${d.config.database || d.config.service_name || d.config.sid || ''}`;
    function edit(d) { testRes.value = null; form.value = d ? dsToForm(d) : emptyDs(); }
    async function save() {
      busy.value = true;
      try {
        const body = run(() => formToDs(form.value));
        const b = await body;
        await run(() => form.value.id ? api(`/api/datasources/${form.value.id}`, { method: 'PUT', body: b }) : api('/api/datasources', { method: 'POST', body: b }), '已保存');
        form.value = null; load(); loadMeta();
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
    async function del(d) { if (!confirm(`删除数据源「${d.name}」？`)) return; await run(() => api(`/api/datasources/${d.id}`, { method: 'DELETE' }), '已删除'); load(); loadMeta(); }
    return { list, form, browse, busy, testRes, addr, edit, save, testForm, test, del, DS_LABEL, DEFAULT_PORT };
  }
};

// ------------------------------------------------------------ 数据集
const Datasets = {
  components: { Drawer },
  template: `<div>
    <div class="page-head"><div><h1>数据集</h1><p>一个数据集即一个业务场景：业务描述参与混合检索，数据表供报告取数</p></div>
      <div class="actions"><button class="btn" @click="reindex">重建检索索引</button><button class="btn primary" @click="edit()">新建数据集</button></div></div>
    <div class="card card-pad" style="margin-bottom:14px">
      <div class="actions"><input class="in" v-model="q" placeholder="输入一段报告需求，测试混合检索命中的业务场景，例如：分析今年各区域和渠道的销售增长与毛利变化" style="flex:1;min-width:280px" @keyup.enter="search">
        <button class="btn primary" @click="search">检索</button></div>
      <div v-if="sr" style="margin-top:12px">
        <div class="small muted" style="margin-bottom:8px">分词：{{sr.query_tokens.join(' / ') || '（无）'}}</div>
        <div class="msg warn" v-for="w in sr.warnings">{{w}}</div>
        <div v-for="(h,i) in sr.hits" class="hit"><h4><span class="badge" :class="i===0?'green':''">#{{i+1}}</span>{{h.dataset.business_scenario}}<span class="score">RRF {{h.rrf}}</span></h4>
          <div class="score">向量 {{h.vector_score ?? '-'}}（第{{h.vector_rank ?? '-'}}） · 关键词 {{h.keyword_score ?? '-'}}（第{{h.keyword_rank ?? '-'}}） · 命中 {{(h.matched_terms||[]).join('、') || '-'}}</div>
          <p>{{h.dataset.business_description}}</p></div>
        <div v-if="!sr.hits.length" class="muted small">没有命中</div>
      </div>
    </div>
    <div class="card"><table class="tbl"><thead><tr><th>业务场景</th><th>业务描述</th><th>数据源</th><th class="num">数据表</th><th>向量</th><th>操作</th></tr></thead>
      <tbody><tr v-for="d in list"><td><b>{{d.business_scenario}}</b><div class="small muted">{{(d.tags||[]).join(' · ')}}</div></td>
        <td><div class="clip">{{d.business_description}}</div></td><td>{{d.datasource_name}} <span class="badge">{{DS_LABEL[d.datasource_type]}}</span></td>
        <td class="num">{{d.table_count}}</td><td><span class="badge" :class="d.vectorized?'green':'amber'">{{d.vectorized?'已向量化':'未向量化'}}</span></td>
        <td><div class="links"><button class="link" @click="edit(d)">编辑</button><button class="link danger" @click="del(d)">删除</button></div></td></tr>
        <tr v-if="!list.length"><td colspan="6" class="empty">暂无数据集</td></tr></tbody></table></div>
    <Drawer v-if="form" :title="form.id ? '编辑数据集' : '新建数据集'" wide @close="form=null">
      <div class="row2"><div class="field"><label>业务场景</label><input class="in" v-model="form.business_scenario" placeholder="如：零售销售经营分析"></div>
        <div class="field"><label>数据源</label><select class="in" v-model="form.datasource_id"><option :value="null" disabled>请选择</option><option v-for="s in sources" :value="s.id">{{s.name}}（{{DS_LABEL[s.type]}}）</option></select></div></div>
      <div class="field"><label>业务描述 <small>越具体越利于检索：覆盖范围、指标、维度、适用分析</small></label><textarea class="in" v-model="form.business_description" rows="4"></textarea></div>
      <div class="field"><label>标签 <small>逗号分隔，作为高权重关键词</small></label><input class="in" v-model="form.tagsTxt"></div>
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
      <template #foot><button class="btn" @click="form=null">取消</button><button class="btn primary" @click="save" :disabled="busy">保存并建立索引</button></template>
    </Drawer>
  </div>`,
  setup() {
    const list = ref([]), sources = ref([]), form = ref(null), busy = ref(false), q = ref(''), sr = ref(null), picker = ref(null), pickKw = ref('');
    const load = async () => { list.value = await api('/api/datasets'); };
    onMounted(async () => { load(); sources.value = await api('/api/datasources'); });
    async function edit(d) {
      picker.value = null;
      if (d) { const full = await run(() => api(`/api/datasets/${d.id}`)); form.value = { ...full, tagsTxt: (full.tags || []).join(', '), tables: full.tables.map(t => ({ ...t })) }; }
      else form.value = { id: null, business_scenario: '', business_description: '', datasource_id: null, tagsTxt: '', tables: [], enabled: true };
    }
    async function save() {
      busy.value = true;
      const f = form.value;
      const body = { business_scenario: f.business_scenario, business_description: f.business_description, datasource_id: f.datasource_id,
        tags: f.tagsTxt.split(/[,，]/).map(s => s.trim()).filter(Boolean), enabled: f.enabled, tables: f.tables.filter(t => t.table_name.trim()) };
      try { await run(() => f.id ? api(`/api/datasets/${f.id}`, { method: 'PUT', body }) : api('/api/datasets', { method: 'POST', body }), '已保存'); form.value = null; load(); loadMeta(); }
      finally { busy.value = false; }
    }
    async function del(d) { if (!confirm(`删除数据集「${d.business_scenario}」？`)) return; await run(() => api(`/api/datasets/${d.id}`, { method: 'DELETE' }), '已删除'); load(); loadMeta(); }
    async function search() { if (!q.value.trim()) return; sr.value = await run(() => api(`/api/datasets/search?q=${encodeURIComponent(q.value)}&top_k=5`)); }
    async function reindex() { const r = await run(() => api('/api/datasets/reindex', { method: 'POST' })); toast(`已重建：数据集 ${r.datasets}，样式规则 ${r.style_rules}，未向量化 ${r.not_vectorized}`, 'ok'); load(); }
    async function pick() { picker.value = []; picker.value = await run(() => api(`/api/datasources/${form.value.datasource_id}/tables`)); }
    const pickShown = computed(() => (picker.value || []).filter(t => !pickKw.value || t.name.includes(pickKw.value)));
    const has = n => form.value.tables.some(t => t.table_name === n);
    function togglePick(t) { const i = form.value.tables.findIndex(x => x.table_name === t.name); if (i >= 0) form.value.tables.splice(i, 1); else form.value.tables.push({ table_name: t.name, table_description: t.comment || '' }); }
    return { list, sources, form, busy, q, sr, picker, pickKw, pickShown, edit, save, del, search, reindex, pick, has, togglePick, DS_LABEL };
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
      <div class="page-head"><div><h1>报告模板</h1><p>Markdown 模板：章节（维度视角）→ 本节结论 → 子结论 → 支撑图表；含查询、度量、全局筛选与主题</p></div>
        <div class="actions"><input class="in" v-model="kw" placeholder="搜索模板…" style="width:200px" @keyup.enter="load"><button class="btn primary" @click="edit()">新建模板</button></div></div>
      <div class="card"><table class="tbl"><thead><tr><th>名称</th><th>业务场景</th><th>结构</th><th class="num">版本</th><th>更新时间</th><th>操作</th></tr></thead>
        <tbody><tr v-for="t in list"><td><b>{{t.name}}</b><div class="small muted clip">{{t.user_requirement}}</div></td><td>{{t.business_scenario || '-'}}</td>
          <td class="small">{{(t.summary.sections||[]).length}} 章 · {{t.summary.chart_count}} 图 · {{t.summary.filter_count}} 筛选</td><td class="num">v{{t.version}}</td><td class="small">{{fmtTime(t.updated_at)}}</td>
          <td><div class="links"><button class="link" @click="edit(t)">编辑</button><button class="link" @click="gen(t)">生成报告</button><button class="link danger" @click="del(t)">删除</button></div></td></tr>
          <tr v-if="!list.length"><td colspan="6" class="empty">暂无模板。通常由 Skill 通过 MCP 自动生成，也可手动新建。</td></tr></tbody></table></div>
    </template>
    <template v-else>
      <div class="page-head"><div><h1>{{form.id ? '编辑模板 · v' + form.version : '新建模板'}}</h1><p>保存时自动校验语法；“校验”会实际执行查询并检查字段</p></div>
        <div class="actions"><button class="btn ghost" @click="form=null;load()">← 返回列表</button><button class="btn" @click="doValidate" :disabled="busy">校验</button><button class="btn" @click="preview" :disabled="busy">预览</button>
          <button class="btn" @click="save(false)" :disabled="busy">保存</button><button class="btn primary" @click="save(true)" :disabled="busy">保存并生成报告</button></div></div>
      <div class="meta-bar"><input class="in" v-model="form.name" placeholder="模板名称（默认取标题）">
        <select class="in" v-model="form.dataset_id"><option :value="null">关联数据集（决定默认数据源）</option><option v-for="d in datasets" :value="d.id">{{d.business_scenario}}</option></select>
        <input class="in" v-model="form.user_requirement" placeholder="用户原始需求"></div>
      <div class="editor"><div class="pane"><textarea class="in code" v-model="form.markdown" spellcheck="false" @keydown.tab.prevent="tab"></textarea></div>
        <div class="right"><div class="subtabs"><button :class="{on:side==='check'}" @click="side='check'">校验结果</button><button :class="{on:side==='preview'}" @click="side='preview'">预览</button>
          <button v-if="form.versions && form.versions.length" :class="{on:side==='ver'}" @click="side='ver'">版本 ({{form.versions.length}})</button></div>
          <div class="body" v-show="side==='check'">
            <div v-if="!vr" class="muted small">点击“校验”执行模板中的全部查询并检查字段引用。</div>
            <div v-else class="msgs">
              <div class="msg ok" v-if="vr.ok">校验通过：{{vr.summary.sections.length}} 个章节，{{vr.summary.chart_count}} 个图表，{{vr.summary.filter_count}} 个筛选器</div>
              <div class="msg err" v-for="e in vr.errors">{{e}}</div><div class="msg warn" v-for="w in vr.warnings">{{w}}</div>
              <template v-if="vr.queries"><h4 style="margin:10px 0 4px">查询</h4>
                <div v-for="(q,k) in vr.queries" class="hit"><b class="mono">{{k}}</b> <span class="small muted">{{q.datasource}} · {{q.rows}} 行 · {{q.ms}} ms</span>
                  <div class="small mono muted" style="margin-top:4px">{{q.columns.join(', ')}}</div></div></template>
              <template v-if="vr.filters"><h4 style="margin:10px 0 4px">筛选器取值</h4>
                <div v-for="(f,k) in vr.filters" class="small"><b class="mono">{{k}}</b>：{{f.distinct}} 个取值 · {{f.examples.join('、')}}</div></template>
            </div></div>
          <div class="body" v-show="side==='preview'" style="padding:0"><iframe v-if="previewHtml" :srcdoc="previewHtml"></iframe><div v-else class="empty">点击“预览”渲染</div></div>
          <div class="body" v-show="side==='ver'"><div v-for="v in form.versions" class="hit" style="display:flex;justify-content:space-between;align-items:center"><span>v{{v.version}} · {{fmtTime(v.created_at)}} · {{fmtSize(v.size)}}</span><button class="link" @click="loadVer(v.version)">载入编辑器</button></div></div>
        </div></div>
    </template>
  </div>`,
  setup() {
    const list = ref([]), form = ref(null), datasets = ref([]), busy = ref(false), vr = ref(null), side = ref('check'), previewHtml = ref(''), kw = ref('');
    const load = async () => { list.value = await api('/api/templates' + (kw.value ? '?q=' + encodeURIComponent(kw.value) : '')); };
    onMounted(async () => { load(); datasets.value = await api('/api/datasets'); });
    async function edit(t) {
      vr.value = null; previewHtml.value = ''; side.value = 'check';
      form.value = t ? await run(() => api(`/api/templates/${t.id}`)) : { id: null, name: '', dataset_id: datasets.value[0] ? datasets.value[0].id : null, user_requirement: '', markdown: STARTER, version: 1 };
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
      const f = form.value, body = { name: f.name, markdown: f.markdown, dataset_id: f.dataset_id, user_requirement: f.user_requirement };
      try {
        const t = await run(() => f.id ? api(`/api/templates/${f.id}`, { method: 'PUT', body }) : api('/api/templates', { method: 'POST', body }), '模板已保存');
        form.value = await api(`/api/templates/${t.id}`);
        if (andRender) { const r = await run(() => api(`/api/templates/${t.id}/render`, { method: 'POST', body: {} }), '报告已生成'); window.open(`/reports/${r.id}`, '_blank'); }
        loadMeta();
      } finally { busy.value = false; }
    }
    async function gen(t) { const r = await run(() => api(`/api/templates/${t.id}/render`, { method: 'POST', body: {} }), '报告已生成'); window.open(`/reports/${r.id}`, '_blank'); loadMeta(); }
    async function del(t) { if (!confirm(`删除模板「${t.name}」？已生成的报告会保留。`)) return; await run(() => api(`/api/templates/${t.id}`, { method: 'DELETE' }), '已删除'); load(); loadMeta(); }
    async function loadVer(v) { const r = await run(() => api(`/api/templates/${form.value.id}/versions/${v}`)); form.value.markdown = r.markdown; toast(`已载入 v${v}，保存后成为新版本`); }
    function tab(e) { const ta = e.target, s = ta.selectionStart; form.value.markdown = ta.value.slice(0, s) + '  ' + ta.value.slice(ta.selectionEnd); nextTick(() => { ta.selectionStart = ta.selectionEnd = s + 2; }); }
    return { list, form, datasets, busy, vr, side, previewHtml, kw, load, edit, doValidate, preview, save, gen, del, loadVer, tab, fmtTime, fmtSize };
  }
};

// ------------------------------------------------------------ 报告
const Reports = {
  template: `<div>
    <div class="page-head"><div><h1>报告</h1><p>由模板渲染的 HTML 报告（数据快照内嵌，支持筛选联动）；可下载内联 ECharts 的离线单文件</p></div></div>
    <div class="card"><table class="tbl"><thead><tr><th>报告名称</th><th>来源模板</th><th class="num">大小</th><th>数据行</th><th>生成时间</th><th>操作</th></tr></thead>
      <tbody><tr v-for="r in list"><td><b>{{r.name}}</b><div class="small muted" v-if="(r.meta.warnings||[]).length">{{r.meta.warnings.length}} 条提示</div></td>
        <td>{{r.template_name || '-'}} <span class="badge" v-if="r.template_version">v{{r.template_version}}</span></td><td class="num">{{fmtSize(r.size)}}</td>
        <td class="small">{{rows(r)}}</td><td class="small">{{fmtTime(r.created_at)}}</td>
        <td><div class="links"><a :href="'/reports/'+r.id" target="_blank">查看</a><a :href="'/reports/'+r.id+'/download'">下载离线版</a><button class="link danger" @click="del(r)">删除</button></div></td></tr>
        <tr v-if="!list.length"><td colspan="6" class="empty">暂无报告</td></tr></tbody></table></div>
  </div>`,
  setup() {
    const list = ref([]);
    const load = async () => { list.value = await api('/api/reports'); };
    onMounted(load);
    const rows = r => Object.values((r.meta && r.meta.queries) || {}).reduce((a, q) => a + (q.rows || 0), 0) || '-';
    async function del(r) { if (!confirm(`删除报告「${r.name}」？`)) return; await run(() => api(`/api/reports/${r.id}`, { method: 'DELETE' }), '已删除'); load(); loadMeta(); }
    return { list, del, rows, fmtTime, fmtSize };
  }
};

// ------------------------------------------------------------ 样式规则（面向业务人员的可视化编辑）
const LEVEL_LABEL = { default: '内置默认', general: '通用规则', scenario: '业务场景规则' };
const isColor = v => typeof v === 'string' && /^#[0-9a-f]{3,8}$/i.test(v);
function showVal(t, v) {
  if (v == null) return '—';
  if (t.type === 'font') return `${v.size}px · ${({400: '常规', 500: '中等', 600: '半粗', 700: '粗'})[v.weight] || v.weight}`;
  if (t.type === 'px') return `${v}px`;
  if (t.type === 'bool') return v ? '是' : '否';
  if (t.type === 'choice') return ((t.options || []).find(o => o.value === v) || {}).label || v;
  if (t.type === 'palette') return `${v.length} 种颜色`;
  return String(v);
}
const TokenControl = {
  props: ['t', 'value', 'readonly'], emits: ['set'],
  template: `<div class="tok-ctl">
    <template v-if="t.type==='color'"><input type="color" :value="hex(value)" :disabled="readonly" @input="$emit('set', $event.target.value.toUpperCase())">
      <input class="in mono sm" :value="value" :disabled="readonly" @change="onText($event.target.value)"></template>
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
    const sizes = [12, 13, 14, 15, 16, 18, 20, 22, 24, 28, 32, 36, 40, 44, 48];
    const weights = [[400, '常规'], [500, '中等'], [600, '半粗'], [700, '粗']];
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
    <details v-for="(g,gi) in meta.groups" class="tok-group" :open="gi<2">
      <summary><b>{{g.label}}</b><span class="muted small">{{g.desc}}</span><span class="badge blue" v-if="ownCount(g)">本规则调整 {{ownCount(g)}} 项</span></summary>
      <div v-for="t in g.tokens.filter(t=>showAdvanced || !t.advanced)" class="tok-row" :class="{own: own && t.key in own}">
        <div class="tok-label"><b>{{t.label}}</b><span v-if="t.desc">{{t.desc}}</span></div>
        <TokenControl :t="t" :value="effective[t.key]" :readonly="readonly" @set="v => $emit('set', t.key, v)"/>
        <div class="tok-src">
          <template v-if="own && t.key in own"><span class="badge blue">本规则设置</span><button v-if="!readonly" class="link small" @click="$emit('reset', t.key)">恢复继承</button></template>
          <span v-else class="src" :class="sources[t.key] && sources[t.key].level">{{srcText(t.key)}}</span>
        </div>
      </div>
    </details>
  </div>`,
  setup(props) {
    const ownCount = g => props.own ? g.tokens.filter(t => t.key in props.own).length : 0;
    const srcText = k => { const s = props.sources && props.sources[k]; if (!s) return '内置默认'; return s.level === 'default' ? '内置默认' : (props.own ? '继承：' : '') + `${LEVEL_LABEL[s.level]}「${s.rule}」`; };
    return { ownCount, srcText };
  }
};
const Styles = {
  components: { TokenGroups },
  template: `<div>
    <template v-if="!form && !viewSc">
      <div class="page-head"><div><h1>样式规则</h1><p>决定报告长什么样：用通俗的“规则条目”告诉智能体怎么设计报告，用可视化的“视觉样式”统一配色与版式</p></div>
        <div class="actions"><button class="btn primary" @click="edit()">新建规则</button></div></div>
      <div class="chain card card-pad">
        <div class="chain-steps"><span class="step">内置默认</span><i>→</i><span class="step">通用规则<small>对所有报告生效</small></span><i>→</i><span class="step strong">业务场景规则<small>只对该场景生效</small></span></div>
        <p class="small muted" style="margin:8px 0 0">{{meta ? meta.precedence : ''}}。同一项设置，右边的覆盖左边的。</p>
        <div class="actions" style="margin-top:12px"><span class="small">查看最终效果：</span>
          <button class="btn sm" @click="openView('')">通用（不区分场景）</button>
          <button v-for="sc in scenarios" class="btn sm" @click="openView(sc)">{{sc}}</button></div>
      </div>
      <div v-for="grp in grouped" style="margin-top:18px">
        <h3 class="grp-title">{{grp.title}} <span class="muted small">{{grp.sub}}</span></h3>
        <div class="rule-grid">
          <div v-for="r in grp.rules" class="card rule-card" :class="{off: !r.enabled}">
            <div class="rule-head"><b>{{r.name}}</b><span class="badge" :class="r.enabled?'green':''">{{r.enabled?'启用':'停用'}}</span></div>
            <ul class="rule-items"><li v-for="it in r.items.slice(0,4)"><span class="cat">{{catLabel(it.category)}}</span>{{it.text}}</li>
              <li v-if="r.items.length>4" class="muted small">… 共 {{r.items.length}} 条规则</li>
              <li v-if="!r.items.length" class="muted small">没有规则条目</li></ul>
            <div class="rule-foot"><div class="swatches"><span v-for="c in colors(r.theme)" class="swatch" :style="{background:c}"></span></div>
              <span class="small muted">{{Object.keys(r.theme||{}).length ? '视觉调整 ' + Object.keys(r.theme).length + ' 项' : '沿用上一级视觉样式'}}</span>
              <span class="spacer"></span><button class="link" @click="edit(r)">编辑</button><button class="link danger" @click="del(r)">删除</button></div>
          </div>
          <div v-if="!grp.rules.length" class="empty card">暂无规则</div>
        </div>
      </div>
    </template>

    <template v-else-if="viewSc !== null && !form">
      <div class="page-head"><div><h1>最终效果 · {{viewSc || '通用'}}</h1><p>该范围内所有规则叠加后的样式；每一项都标明了来自哪一级规则</p></div>
        <div class="actions"><button class="btn ghost" @click="viewSc=null">← 返回</button></div></div>
      <div class="style-editor"><div class="pane-left">
        <div class="card card-pad" v-if="viewRules.length"><h3>生效的规则条目</h3>
          <div v-for="r in viewRules" style="margin-bottom:10px"><div class="small muted">{{r.scope==='general'?'通用规则':'业务场景规则'}}「{{r.name}}」</div>
            <ul class="rule-items"><li v-for="it in r.items"><span class="cat">{{catLabel(it.category)}}</span>{{it.text}}</li></ul></div></div>
        <div class="card card-pad" style="margin-top:12px" v-if="eff"><h3>视觉样式</h3><TokenGroups :meta="meta" :effective="eff.theme" :sources="eff.sources" :readonly="true"/></div>
      </div>
      <div class="pane-right"><div class="preview-box"><div class="preview-bar">示例报告预览</div><iframe :srcdoc="previewHtml"></iframe></div></div></div>
    </template>

    <template v-else-if="form">
      <div class="page-head"><div><h1>{{form.id ? '编辑样式规则' : '新建样式规则'}}</h1><p>右侧为示例报告的实时预览</p></div>
        <div class="actions"><button class="btn ghost" @click="close">← 返回</button><button class="btn primary" @click="save">保存</button></div></div>
      <div class="card card-pad meta-row">
        <div class="field"><label>规则名称</label><input class="in" v-model="form.name" placeholder="如：零售销售报告风格"></div>
        <div class="field"><label>适用范围</label><div class="seg"><button :class="{on:form.scope==='general'}" @click="form.scope='general'">通用规则（所有报告）</button><button :class="{on:form.scope==='scenario'}" @click="form.scope='scenario'">业务场景规则</button></div></div>
        <div class="field" v-if="form.scope==='scenario'"><label>业务场景</label><select class="in" v-model="form.business_scenario"><option v-for="s in scenarios" :value="s">{{s}}</option></select></div>
        <div class="field" v-else><label>预览场景</label><select class="in" v-model="previewSc"><option value="">不区分场景</option><option v-for="s in scenarios" :value="s">{{s}}</option></select></div>
        <div class="field"><label>状态</label><label class="check" style="height:34px"><input type="checkbox" v-model="form.enabled"> 启用</label></div>
      </div>
      <div class="style-editor">
        <div class="pane-left">
          <div class="subtabs"><button :class="{on:tab==='items'}" @click="tab='items'">规则条目（{{form.items.length}}）</button><button :class="{on:tab==='theme'}" @click="tab='theme'">视觉样式（调整 {{Object.keys(form.theme).length}} 项）</button></div>
          <div v-show="tab==='items'">
            <p class="small muted" style="margin:0 0 10px">用一句话写一条要求，智能体生成报告时会遵守。不知道怎么写时，点类别下面的“示例”直接添加再修改。</p>
            <div v-for="c in meta.categories" class="cat-block">
              <div class="cat-head"><b>{{c.label}}</b><span class="muted small">{{c.hint}}</span></div>
              <div v-for="(it,i) in form.items" v-show="it.category===c.key" class="item-row">
                <textarea class="in" rows="1" v-model="it.text" @input="grow($event)" :placeholder="'写一条' + c.label + '要求'"></textarea>
                <select class="in sm" v-model="it.category" title="移到其他类别"><option v-for="cc in meta.categories" :value="cc.key">{{cc.label}}</option></select>
                <button class="link danger" @click="form.items.splice(i,1)">删除</button></div>
              <div class="cat-actions"><button class="btn sm" @click="add(c.key)">+ 添加</button>
                <button v-for="ex in c.examples" class="ex-chip" @click="add(c.key, ex)" :title="ex">示例：{{ex}}</button></div>
            </div>
          </div>
          <div v-show="tab==='theme'">
            <p class="small muted" style="margin:0 0 10px">只改需要调整的项；没改的项沿用上一级（{{form.scope==='scenario' ? '通用规则与内置默认' : '内置默认'}}），并标明来源。</p>
            <label class="check small" style="margin-bottom:8px"><input type="checkbox" v-model="advanced"> 显示高级选项</label>
            <TokenGroups v-if="inherited" :meta="meta" :effective="effective" :sources="inherited.sources" :own="form.theme" :show-advanced="advanced" @set="setTok" @reset="resetTok"/>
          </div>
        </div>
        <div class="pane-right"><div class="preview-box"><div class="preview-bar">示例报告预览 <span class="muted small">{{previewing ? '更新中…' : '改动后自动更新'}}</span></div><iframe :srcdoc="previewHtml"></iframe></div></div>
      </div>
    </template>
  </div>`,
  setup() {
    const list = ref([]), scenarios = ref([]), meta = ref(null), form = ref(null), tab = ref('items'), advanced = ref(false);
    const inherited = ref(null), previewHtml = ref(''), previewing = ref(false), previewSc = ref('');
    const viewSc = ref(null), eff = ref(null), viewRules = ref([]);
    const load = async () => { list.value = await api('/api/style-rules'); };
    onMounted(async () => { [meta.value, scenarios.value] = await Promise.all([api('/api/style-tokens'), api('/api/scenarios')]); load(); });
    const catLabel = k => ((meta.value && meta.value.categories.find(c => c.key === k)) || {}).label || '其他';
    const colors = t => { t = t || {}; return Object.entries(t).flatMap(([k, v]) => Array.isArray(v) ? v : isColor(v) ? [v] : []).slice(0, 8); };
    const grouped = computed(() => {
      const g = [{ title: '通用规则', sub: '对所有报告生效', rules: list.value.filter(r => r.scope === 'general') }];
      const scs = Array.from(new Set(scenarios.value.concat(list.value.filter(r => r.scope === 'scenario').map(r => r.business_scenario))));
      scs.forEach(sc => g.push({ title: '业务场景：' + sc, sub: '只对该场景生效，优先于通用规则', rules: list.value.filter(r => r.scope === 'scenario' && r.business_scenario === sc) }));
      return g;
    });
    const scopeScenario = () => form.value.scope === 'scenario' ? form.value.business_scenario : previewSc.value;
    const effective = computed(() => {
      if (!inherited.value) return {};
      const out = { ...inherited.value.theme };
      Object.assign(out, form.value.theme);
      return out;
    });
    async function loadInherited() {
      if (!form.value) return;
      inherited.value = await api('/api/style-rules/effective', { method: 'POST', body: { scenario: scopeScenario() || null, exclude_id: form.value.id || null } });
      if (form.value.scope === 'general' && !previewSc.value) {
        // 编辑通用规则时，“继承”只来自内置默认与其他通用规则
      }
    }
    let timer = null;
    function schedulePreview() {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        if (!form.value) return;
        previewing.value = true;
        try {
          previewHtml.value = await api('/api/style-rules/preview', { method: 'POST', body: {
            scenario: scopeScenario() || null, exclude_id: form.value.id || null,
            draft: { scope: form.value.scope, name: form.value.name, theme: form.value.theme } } });
        } catch (e) { toast(e.message, 'err'); } finally { previewing.value = false; }
      }, 450);
    }
    watch(() => form.value && JSON.stringify(form.value.theme), schedulePreview);
    watch(() => form.value && [form.value.scope, form.value.business_scenario, previewSc.value].join('|'), () => { loadInherited(); schedulePreview(); });
    function legacy(content) {
      return (content || '').split('\n').map(l => /^\s*(?:[-*]|\d+[.、])\s+(.+)$/.exec(l)).filter(Boolean).map(m => ({ category: 'other', text: m[1].replace(/\*\*|`/g, '').trim() }));
    }
    async function edit(r) {
      tab.value = 'items'; previewHtml.value = '';
      if (r) {
        const items = (r.items && r.items.length) ? r.items.map(x => ({ ...x })) : legacy(r.content);
        if (!(r.items && r.items.length) && items.length) toast('已把原有的文字说明拆成条目（类别为“其他”），请调整类别后保存');
        form.value = { id: r.id, name: r.name, scope: r.scope, business_scenario: r.business_scenario || scenarios.value[0] || '', enabled: r.enabled, priority: r.priority, items, theme: { ...(r.theme || {}) } };
      } else {
        form.value = { id: null, name: '', scope: 'scenario', business_scenario: scenarios.value[0] || '', enabled: true, priority: 100, items: [], theme: {} };
      }
      await loadInherited(); schedulePreview();
    }
    function close() { form.value = null; inherited.value = null; load(); }
    function add(cat, text) { form.value.items.push({ category: cat, text: text || '' }); }
    function grow(e) { e.target.style.height = 'auto'; e.target.style.height = e.target.scrollHeight + 'px'; }
    function setTok(k, v) { form.value.theme = { ...form.value.theme, [k]: v }; }
    function resetTok(k) { const t = { ...form.value.theme }; delete t[k]; form.value.theme = t; }
    async function save() {
      const f = form.value;
      const body = { name: f.name, scope: f.scope, business_scenario: f.business_scenario, enabled: f.enabled, priority: f.priority,
        items: f.items.filter(i => i.text.trim()), theme: f.theme };
      await run(() => f.id ? api(`/api/style-rules/${f.id}`, { method: 'PUT', body }) : api('/api/style-rules', { method: 'POST', body }), '已保存，新生成的报告将使用该样式');
      close(); loadMeta();
    }
    async function del(r) { if (!confirm(`删除样式规则「${r.name}」？`)) return; await run(() => api(`/api/style-rules/${r.id}`, { method: 'DELETE' }), '已删除'); load(); loadMeta(); }
    async function openView(sc) {
      viewSc.value = sc; eff.value = null; previewHtml.value = '';
      const [e, rs, html] = await Promise.all([
        api('/api/style-rules/effective', { method: 'POST', body: { scenario: sc || null } }),
        api('/api/style-rules/resolve' + (sc ? '?scenario=' + encodeURIComponent(sc) : '')),
        api('/api/style-rules/preview', { method: 'POST', body: { scenario: sc || null } })]);
      eff.value = e; viewRules.value = [...rs.general_rules, ...rs.scenario_rules]; previewHtml.value = html;
    }
    return { list, scenarios, meta, form, tab, advanced, inherited, effective, previewHtml, previewing, previewSc, viewSc, eff, viewRules,
      catLabel, colors, grouped, edit, close, add, grow, setTok, resetTok, save, del, openView };
  }
};

// ------------------------------------------------------------ 权限与令牌
const Perms = {
  components: { Drawer },
  template: `<div>
    <div class="page-head"><div><h1>权限与令牌</h1><p>MCP 必须携带令牌访问；令牌中的员工工号决定可检索的业务场景与可访问的数据表（默认拒绝）</p></div></div>
    <div class="subtabs"><button v-for="t in tabs" :class="{on:tab===t[0]}" @click="tab=t[0]">{{t[1]}}</button></div>

    <template v-if="tab==='emp'">
      <div class="actions" style="margin-bottom:12px"><button class="btn primary" @click="editEmp()">新增员工</button></div>
      <div class="card"><table class="tbl"><thead><tr><th>工号</th><th>姓名</th><th>部门</th><th>角色</th><th>类型</th><th>注册来源</th><th>状态</th><th>操作</th></tr></thead>
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
      <div class="card"><table class="tbl"><thead><tr><th>授权对象</th><th>业务场景</th><th>数据表</th><th>授权时间</th><th>操作</th></tr></thead>
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
      <div class="card"><table class="tbl"><thead><tr><th>员工</th><th>备注</th><th>签发时间</th><th>过期时间</th><th>最近使用</th><th>状态</th><th>操作</th></tr></thead>
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
      emp.value = null; load(); loadMeta();
    }
    async function delEmp(e) { if (!confirm(`删除员工 ${e.emp_no}？其直授权限会删除、令牌会被吊销。`)) return; await run(() => api('/api/employees/' + encodeURIComponent(e.emp_no), { method: 'DELETE' }), '已删除'); load(); loadMeta(); }
    function toggleT(t) { const i = g.tables.indexOf(t); if (i >= 0) g.tables.splice(i, 1); else g.tables.push(t); }
    async function addGrant() { const r = await run(() => api('/api/grants', { method: 'POST', body: { ...g } })); toast(`新增 ${r.added} 条授权`, 'ok'); load(); }
    async function delGrant(x) { if (!confirm('撤销该授权？')) return; await run(() => api('/api/grants/' + x.id, { method: 'DELETE' }), '已撤销'); load(); }
    function issueFor(e) { tab.value = 'token'; tk.emp_no = e.emp_no; issued.value = null; }
    async function issue() { issued.value = await run(() => api('/api/tokens', { method: 'POST', body: { ...tk } }), '令牌已签发'); load(); }
    async function revoke(t) { if (!confirm(`吊销 ${t.emp_no} 的令牌？使用该令牌的 MCP 客户端将立即失效。`)) return; await run(() => api(`/api/tokens/${t.jti}/revoke`, { method: 'POST' }), '已吊销'); load(); }
    async function checkEmp(no) { tab.value = 'check'; chk.value = no; chkRes.value = await run(() => api('/api/permissions/check?emp_no=' + encodeURIComponent(no))); }
    function copy(text) { navigator.clipboard.writeText(text).then(() => toast('已复制', 'ok'), () => toast('复制失败，请手动选择复制', 'err')); }
    return { tabs, tab, emps, grants, tokens, datasets, emp, g, tk, issued, chk, chkRes, jwt, mcpUrl, cmd, roles, dsTables,
      editEmp, saveEmp, delEmp, toggleT, addGrant, delGrant, issueFor, issue, revoke, checkEmp, copy, fmtTime };
  }
};

// ------------------------------------------------------------ 根组件
const Login = {
  template: `<div class="card login">
    <div class="brand-row"><i>S</i><div><h2>SmartReportGen</h2><p class="muted small">智能报告生成 · 管理台</p></div></div>
    <template v-if="cfg">
      <template v-if="cfg.mode==='iam'">
        <a class="btn primary big" :class="{disabled: !cfg.iam_configured}" :href="cfg.iam_configured ? '/auth/iam/login?next=/' : null">使用海尔 IAM 单点登录</a>
        <div class="msg warn small" v-if="!cfg.iam_configured" style="margin-top:10px">IAM 单点登录未配置（缺 {{cfg.iam_missing.join('、')}}），请联系运维；管理员可使用下方应急登录。</div>
        <p class="small muted" style="margin:10px 0 0">登录后可在「我的令牌」中为 Claude Code、Codex 等 agent 签发个人 MCP 令牌。</p>
      </template>
      <details v-if="cfg.admin_token_login" :open="cfg.mode!=='iam'" class="fallback">
        <summary>{{cfg.mode==='iam' ? '管理员应急登录（ADMIN_TOKEN）' : '管理员令牌登录'}}</summary>
        <input class="in" type="password" v-model="tok" placeholder="ADMIN_TOKEN" @keyup.enter="login" style="margin-top:10px">
        <button class="btn" style="width:100%;margin-top:10px" @click="login">登录</button>
      </details>
      <div class="msg err small" v-if="cfg.mode!=='iam' && !cfg.admin_token_login" style="margin-top:10px">未配置任何登录方式。</div>
    </template>
  </div>`,
  setup() {
    const tok = ref(''), cfg = ref(null);
    onMounted(async () => { cfg.value = await api('/api/auth/config'); });
    async function login() { await run(() => api('/api/auth/token-login', { method: 'POST', body: { token: tok.value } })); location.reload(); }
    return { tok, cfg, login };
  }
};

// ------------------------------------------------------------ 我的令牌（所有登录员工）
const Mine = {
  template: `<div>
    <div class="page-head"><div><h1>我的令牌</h1><p>为你使用的 agent（Claude Code、Codex 等）签发个人 MCP 令牌；令牌只代表你本人，权限与你的数据权限一致</p></div></div>
    <div class="grid g2">
      <div class="card card-pad">
        <h3>我的身份</h3>
        <dl class="kv" v-if="state.me"><dt>工号</dt><dd class="mono">{{state.me.emp_no || '（管理员令牌登录）'}}</dd><dt>姓名</dt><dd>{{state.me.name || '-'}}</dd>
          <dt>身份</dt><dd><span class="badge" :class="state.me.is_admin?'red':''">{{state.me.is_admin ? '管理员' : '员工'}}</span> <span class="badge" v-if="!state.me.registered" style="margin-left:4px">未登记 / 已停用</span></dd>
          <dt>登录方式</dt><dd>{{({session:'海尔 IAM 单点登录', admin_token:'管理员令牌', open:'开发模式'})[state.me.via] || state.me.via}}</dd></dl>
        <h3 style="margin-top:18px">我的数据权限</h3>
        <div v-if="perm">
          <div class="msg ok" v-if="perm.all">管理员：可访问全部业务场景与数据表。</div>
          <div class="msg warn" v-else-if="!perm.registered">工号未登记或已停用，暂无数据权限，请联系管理员开通。</div>
          <div v-else-if="!perm.datasets.length" class="msg warn">暂无任何数据表权限，请联系管理员授权。</div>
          <div v-else v-for="d in perm.datasets" class="hit" style="padding:10px 12px"><b>{{d.business_scenario}}</b>
            <div style="margin-top:4px"><span v-for="t in d.tables" class="badge" style="margin:2px 6px 2px 0"><span class="mono">{{t}}</span></span></div></div>
        </div>
      </div>
      <div class="card card-pad">
        <h3>签发个人 MCP 令牌</h3>
        <template v-if="canIssue">
          <div class="row2"><div class="field"><label>有效期</label><select class="in" v-model.number="days"><option v-for="d in dayOpts" :value="d">{{d}} 天</option></select></div>
            <div class="field"><label>备注</label><input class="in" v-model="note" placeholder="如：我的 Codex"></div></div>
          <button class="btn primary" @click="issue">签发</button>
          <div v-if="issued" class="msg ok" style="margin-top:12px">令牌仅显示这一次，请立即配置到 agent：
            <pre class="block" style="margin-top:8px">{{issued.token}}</pre>
            <div class="small" style="margin:10px 0 4px">① 设置环境变量（在启动 agent 的终端中）</div>
            <pre class="block">export SMARTREPORT_TOKEN={{issued.token}}</pre>
            <div class="small" style="margin:10px 0 4px">② Claude Code</div>
            <pre class="block">claude mcp add --transport http smartreportgen {{mcpUrl}} --header "Authorization: Bearer $SMARTREPORT_TOKEN"</pre>
            <div class="small" style="margin:10px 0 4px">③ Codex</div>
            <pre class="block">codex mcp add smartreportgen --url {{mcpUrl}} --bearer-token-env-var SMARTREPORT_TOKEN</pre>
            <div class="actions" style="margin-top:8px"><button class="btn sm" @click="copy(issued.token)">复制令牌</button><button class="btn sm" @click="copy('export SMARTREPORT_TOKEN=' + issued.token)">复制 export 命令</button></div></div>
        </template>
        <div v-else class="msg warn">{{whyNot}}</div>
      </div>
    </div>
    <div class="card" style="margin-top:14px"><table class="tbl"><thead><tr><th>备注</th><th>签发时间</th><th>过期时间</th><th>最近使用</th><th>状态</th><th>操作</th></tr></thead>
      <tbody><tr v-for="t in tokens"><td>{{t.note}}</td><td class="small">{{fmtTime(t.created_at)}}</td><td class="small">{{fmtTime(t.expires_at)}}</td><td class="small">{{fmtTime(t.last_used_at) || '-'}}</td>
        <td><span class="badge" :class="t.revoked?'red':t.expired?'amber':'green'">{{t.revoked?'已吊销':t.expired?'已过期':'有效'}}</span></td>
        <td><button class="link danger" v-if="!t.revoked && !t.expired" @click="revoke(t)">吊销</button></td></tr>
        <tr v-if="!tokens.length"><td colspan="6" class="empty">还没有个人令牌</td></tr></tbody></table></div>
  </div>`,
  setup() {
    const tokens = ref([]), perm = ref(null), issued = ref(null), note = ref(''), days = ref(30);
    const maxDays = computed(() => (state.authCfg && state.authCfg.token_max_days) || 90);
    const dayOpts = computed(() => [7, 30, 60, 90, 180, 365].filter(d => d <= maxDays.value));
    const mcpUrl = computed(() => state.meta ? state.meta.mcp_url : '');
    const canIssue = computed(() => state.authCfg && state.authCfg.self_service_tokens && state.me && state.me.emp_no && state.me.registered);
    const whyNot = computed(() => !state.authCfg || !state.authCfg.self_service_tokens ? '未开启自助签发，请联系管理员签发令牌。'
      : !(state.me && state.me.emp_no) ? '请使用海尔 IAM 单点登录后签发个人令牌（管理员令牌登录没有工号）。' : '你的工号未登记或已停用，请联系管理员。');
    const load = async () => { [tokens.value, perm.value] = await Promise.all([api('/api/me/tokens'), api('/api/me/permissions')]); };
    onMounted(load);
    async function issue() { issued.value = await run(() => api('/api/me/tokens', { method: 'POST', body: { days: days.value, note: note.value } }), '令牌已签发'); load(); }
    async function revoke(t) { if (!confirm('吊销这枚令牌？使用它的 agent 将立即无法访问。')) return; await run(() => api(`/api/me/tokens/${t.jti}/revoke`, { method: 'POST' }), '已吊销'); load(); }
    function copy(text) { navigator.clipboard.writeText(text).then(() => toast('已复制', 'ok'), () => toast('复制失败，请手动选择复制', 'err')); }
    return { state, tokens, perm, issued, note, days, dayOpts, mcpUrl, canIssue, whyNot, issue, revoke, copy, fmtTime };
  }
};

const PAGES = { home: Home, datasources: Datasources, datasets: Datasets, templates: Templates, reports: Reports, styles: Styles, perms: Perms, mine: Mine };
const ADMIN_NAV = [['home', '概览', 'home'], ['datasources', '数据源', 'ds', 'datasources'], ['datasets', '数据集', 'set', 'datasets'],
  ['templates', '报告模板', 'tpl', 'templates'], ['reports', '报告', 'rep', 'reports'], ['styles', '样式规则', 'sty', 'style_rules'],
  ['perms', '权限与令牌', 'perm', 'employees']];
const MINE_NAV = ['mine', '我的令牌', 'key'];

createApp({
  components: { Login },
  template: `<Login v-if="state.needLogin"/>
  <div class="shell" v-else-if="state.me">
    <aside class="side"><div class="brand"><i>S</i><div>SmartReportGen<small>智能报告生成</small></div></div>
      <nav class="nav"><a v-for="n in nav" :class="{on: state.page===n[0]}" @click="go(n[0])"><span v-html="icon(n[2])"></span>{{n[1]}}<span class="n" v-if="n[3] && state.meta">{{state.meta.stats[n[3]]}}</span></a></nav>
      <div class="side-foot">
        <div class="user"><b>{{state.me.name || state.me.emp_no || '管理员'}}</b><span>{{state.me.emp_no || ''}} · {{state.me.is_admin ? '管理员' : '员工'}}</span></div>
        <button v-if="state.me.via!=='open'" class="logout" @click="logout">退出登录</button>
        <div v-if="state.meta" style="margin-top:8px">MCP · <span class="mono">{{state.meta.mcp_url.replace(/^https?:\\/\\//,'')}}</span></div></div></aside>
    <main class="main"><component :is="page" :key="state.page"/></main>
  </div>
  <div class="toasts"><div v-for="t in toasts" :key="t.id" class="toast" :class="t.kind">{{t.msg}}</div></div>`,
  setup() {
    const nav = computed(() => {
      const me = state.me || {};
      const mine = me.emp_no || (state.authCfg && state.authCfg.mode === 'iam') ? [MINE_NAV] : [];
      return me.is_admin ? [...ADMIN_NAV, ...mine] : [MINE_NAV];
    });
    const page = computed(() => {
      const allowed = nav.value.map(n => n[0]);
      return PAGES[allowed.includes(state.page) ? state.page : allowed[0]] || Mine;
    });
    async function logout() { await api('/auth/logout', { method: 'POST' }); location.hash = ''; location.reload(); }
    onMounted(async () => {
      try {
        const cfg = await api('/api/auth/config');
        state.authCfg = cfg;
        if (!cfg.open_mode && !cfg.me.logged_in) { state.needLogin = true; return; }
        state.me = cfg.me.logged_in ? cfg.me : { emp_no: null, name: '开发模式', is_admin: true, via: 'open', registered: true };
        if (!state.me.is_admin && !location.hash) state.page = 'mine';
        await loadMeta();
      } catch (e) { toast(e.message, 'err'); }
    });
    return { state, page, nav, go, icon, toasts, logout };
  }
}).mount('#app');
