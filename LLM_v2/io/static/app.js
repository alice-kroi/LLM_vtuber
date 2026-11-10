// LLM_vtuber V2 控制台前端（5 子界面版）

const API = `${location.protocol}//${location.host}`;
const WS_EVENTS = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/events`;

const $ = (id) => document.getElementById(id);

const MAX_EVENTS = 100;
const MAX_CONVERSATION = 30;

// ---------- 工具 ----------
function fmtTime(ts) {
  if (!ts) return '';
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false });
}
function fmtDuration(sec) {
  if (!sec && sec !== 0) return '-';
  if (sec < 60) return `${Math.floor(sec)}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m${Math.floor(sec % 60)}s`;
  return `${Math.floor(sec / 3600)}h`;
}
function escapeHtml(s) {
  if (s === null || s === undefined) return '';
  return String(s).replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  })[c]);
}
function shorten(s, n=80) {
  if (!s) return '';
  return s.length > n ? s.slice(0, n) + '…' : s;
}

// ---------- 状态缓存 ----------
const state = {
  domains: {},
  domainsList: [],
  brains: { will_engine: {}, streamer: {}, operator: {} },
  processes: null,
  memory: null,
  selectedDomain: null,
  currentPrompt: 'streamer',
  eventFilters: new Set(['user_message', 'tts_speak', 'state_updated', 'operator_done',
                        'operator_progress', 'perception_updated', 'will_engine_decided', 'domain_started']),
  logFilters: new Set(['INFO', 'WARNING', 'ERROR']),
};

// ============ 顶栏 ============
async function refreshTopbar() {
  try {
    const [stateR, domainsR] = await Promise.all([
      fetch(`${API}/api/state`).then(r => r.json()),
      fetch(`${API}/api/domains`).then(r => r.json()),
    ]);
    $('pill-persona').textContent = stateR.persona_name || '?';
    $('pill-mood').textContent = stateR.pad?.quadrant || '?';
    $('pill-energy').textContent = `${stateR.resources?.energy ?? '?'}/100`;
    const total = (domainsR.worlds || []).length;
    const active = (domainsR.worlds || []).filter(w => w.enabled).length;
    $('pill-domains').textContent = `${active}/${total}`;
  } catch (e) { console.warn('topbar refresh:', e); }
}

// ============ ⓪ 总览（3 脑在干啥 + 能力清单） ============
async function refreshOverview() {
  try {
    const [brainsR, domainsR, procR, stateR] = await Promise.all([
      fetch(`${API}/api/brains`).then(r => r.json()),
      fetch(`${API}/api/domains`).then(r => r.json()),
      fetch(`${API}/api/processes`).then(r => r.json()),
      fetch(`${API}/api/state`).then(r => r.json()),
    ]);
    renderOverviewBrains(brainsR);
    renderOverviewDomains(domainsR);
    renderOverviewStats(procR, stateR);
  } catch (e) { console.warn('overview refresh:', e); }
}

function renderOverviewBrains(b) {
  const el = $('ov-brains');
  if (!el) return;
  const we = b.will_engine || {};
  const st = b.streamer || {};
  const op = b.operator || {};

  // 思维脑动态
  let weStatus = '待命中';
  const running = we.task_monitor?.running || {};
  const runKeys = Object.keys(running);
  if (runKeys.length) {
    weStatus = runKeys.map(k => `${running[k].description || k} ${running[k].percent||0}%`).join('；');
  } else if (we.is_thinking) {
    weStatus = '思考中…';
  }

  // 主播脑动态
  let stStatus = '待命中';
  if (st.is_thinking) stStatus = '正在回复…';
  else if (st.last_input) stStatus = `对话：${shorten(st.last_input.text||'', 30)}`;

  // 操作脑动态
  let opStatus = '空闲';
  if (op.is_busy) opStatus = `执行：${shorten(op.running_task?.description || op.running_task?.tool || '', 40)}`;
  else if ((op.task_history||[]).length) {
    const last = op.task_history[op.task_history.length-1];
    opStatus = `最近：${last.result?.success?'✓':'✗'} ${shorten(last.task?.description || last.task?.tool || '', 30)}`;
  }

  el.innerHTML = `
    <div class="ov-brain">
      <div class="ov-brain-head">
        <span class="brain-icon">💭</span><b>思维脑</b>
        <span class="brain-status ${runKeys.length || we.is_thinking ? 'thinking' : 'ok'}">${runKeys.length ? '执行中' : (we.is_thinking?'思考中':'待命')}</span>
      </div>
      <div class="ov-brain-body">${escapeHtml(weStatus)}</div>
    </div>
    <div class="ov-brain">
      <div class="ov-brain-head">
        <span class="brain-icon">💬</span><b>主播脑</b>
        <span class="brain-status ${st.is_thinking ? 'thinking' : (st.last_input ? 'ok' : '')}">${st.is_thinking ? '回复中' : (st.last_input ? '对话中' : '待命')}</span>
      </div>
      <div class="ov-brain-body">${escapeHtml(stStatus)}</div>
    </div>
    <div class="ov-brain">
      <div class="ov-brain-head">
        <span class="brain-icon">🔧</span><b>操作脑</b>
        <span class="brain-status ${op.is_busy ? 'busy' : 'ok'}">${op.is_busy ? '执行中' : '空闲'}</span>
      </div>
      <div class="ov-brain-body">${escapeHtml(opStatus)}</div>
    </div>`;
}

function renderOverviewDomains(d) {
  const worlds = d.worlds || [];
  $('ov-domain-count').textContent = worlds.length;
  const el = $('ov-domains');
  el.innerHTML = worlds.map(w => {
    const cls = w.started ? 'started' : (w.enabled ? 'enabled' : 'disabled');
    return `<div class="ov-domain">
      <span class="ov-domain-name">${escapeHtml(w.label)}</span>
      <span class="ov-domain-id">${escapeHtml(w.id)}</span>
      <span class="ov-domain-tools">${w.tool_count || 0} 工具</span>
      <span class="dc-state ${cls}">${cls}</span>
    </div>`;
  }).join('') || '<div class="placeholder">无能力域</div>';
}

function renderOverviewStats(p, s) {
  const bs = p.bus_stats || {};
  $('ov-stats').innerHTML = `
    <div class="kv"><span>人格</span><b>${escapeHtml(s.persona_name || '?')}</b></div>
    <div class="kv"><span>心情</span><b>${escapeHtml(s.pad?.quadrant || '?')}</b></div>
    <div class="kv"><span>能量</span><b>${s.resources?.energy ?? '?'}/100</b></div>
    <div class="kv"><span>已发射事件</span><b>${bs.emitted ?? 0}</b></div>
    <div class="kv"><span>错误</span><b style="color:${bs.errors ? '#fc8181' : '#e2e8f0'}">${bs.errors ?? 0}</b></div>
    <div class="kv"><span>运行中进程</span><b>${(p.processes||[]).filter(x=>x.pid).length}</b></div>`;
}

// ============ ① 思维（3 脑） ============
async function refreshBrains() {
  try {
    const r = await fetch(`${API}/api/brains`).then(r => r.json());
    state.brains = r;
    renderWillEngine(r.will_engine);
    renderStreamer(r.streamer);
    renderOperator(r.operator);
  } catch (e) { console.warn('brains refresh:', e); }
}

function renderWillEngine(we) {
  if (!we) return;
  $('we-mode').textContent = we.mode || 'hybrid';
  $('we-count').textContent = we.decision_count || 0;
  $('we-idle').textContent = `${we.last_activity_age_sec || 0}s`;
  $('we-auto').textContent = we.autonomous ? 'on' : 'off';

  const status = $('we-status');
  status.textContent = we.is_thinking ? '思考中' : 'idle';
  status.className = 'brain-status' + (we.is_thinking ? ' thinking' : '');

  const decisionEl = $('we-decision');
  const dec = we.last_decision;
  if (dec && dec.type) {
    decisionEl.innerHTML = `
      <span class="action-type">${escapeHtml(dec.type)}</span>
      <span style="color:#a0aec0;font-size:11px;margin-left:6px;">${escapeHtml(dec.target || '')}</span>
      <div class="reasoning">${escapeHtml(dec.reasoning || '')}</div>`;
  } else {
    decisionEl.innerHTML = '<div class="placeholder">尚未做决策</div>';
  }

  // 任务监控（正在进行的活动）
  renderWillMonitor(we.task_monitor);
}

function renderWillMonitor(mon) {
  const el = $('we-monitor');
  if (!el) return;
  const running = mon?.running || {};
  const keys = Object.keys(running);
  if (!keys.length) {
    el.innerHTML = '<div class="placeholder">暂无运行中任务</div>';
    return;
  }
  el.innerHTML = '';
  keys.forEach(k => {
    const t = running[k];
    const pct = t.percent || 0;
    const div = document.createElement('div');
    div.className = 'monitor-task';
    div.innerHTML = `
      <span>${escapeHtml(t.description || k)}</span>
      <span class="mt-progress">${pct}%</span>
      <div style="flex:1;min-width:120px;">
        <div class="progress-bar"><div style="width:${pct}%"></div></div>
      </div>`;
    if (t.detail) {
      div.innerHTML += `<div class="mt-detail" style="flex-basis:100%;">${escapeHtml(t.detail)}</div>`;
    }
    el.appendChild(div);
  });
}

function renderStreamer(st) {
  if (!st) return;
  const status = $('st-status');
  if (st.is_thinking) {
    status.textContent = '思考中'; status.className = 'brain-status thinking';
  } else if (st.last_input) {
    status.textContent = '对话中'; status.className = 'brain-status ok';
  } else {
    status.textContent = '等待输入'; status.className = 'brain-status';
  }
  const inp = $('st-input');
  inp.innerHTML = st.last_input
    ? `<b style="color:#48bb78">[${escapeHtml(st.last_input.source || '?')}]</b> ${escapeHtml(shorten(st.last_input.text || '', 80))}`
    : '<div class="placeholder">等待用户输入…</div>';
  const resp = $('st-response');
  resp.innerHTML = st.last_response
    ? escapeHtml(st.last_response.text || '')
    : '<div class="placeholder">等待响应…</div>';
  // 同步到全局输入栏（显示最近一次 AI 回复）
  if (st.last_response && $('chatbar-status')) {
    chatbarMsg('bot', st.last_response.text || '');
  }

  const ul = $('st-conversation');
  ul.innerHTML = '';
  (st.conversation || []).slice(-MAX_CONVERSATION).reverse().forEach(c => {
    const li = document.createElement('li');
    const who = c.role === 'user' ? '👤' : '🤖';
    li.innerHTML = `<span style="color:#718096;margin-right:4px;">${who}</span>
      <span>${escapeHtml(shorten(c.text || '', 90))}</span>`;
    ul.appendChild(li);
  });
  if (!(st.conversation || []).length) {
    ul.innerHTML = '<li class="placeholder">暂无对话记录</li>';
  }
}

function renderOperator(op) {
  if (!op) return;
  const status = $('op-status');
  if (op.is_busy) {
    status.textContent = '执行中'; status.className = 'brain-status busy';
  } else {
    status.textContent = '空闲'; status.className = 'brain-status ok';
  }
  const cur = $('op-current');
  cur.innerHTML = op.running_task
    ? `<b style="color:#ed8936">▶</b> ${escapeHtml(op.running_task.description || op.running_task.tool || '?')}`
    : '<div class="placeholder">无执行中任务</div>';
  $('op-queue').textContent = op.queue_size || 0;
  $('op-total').textContent = op.task_history?.length || 0;

  const ul = $('op-list');
  ul.innerHTML = '';
  (op.task_history || []).slice(-20).reverse().forEach(t => {
    const li = document.createElement('li');
    const ok = t.result?.success;
    li.innerHTML = `<span class="${ok ? 'ok' : 'fail'}">${ok ? '✓' : '✗'}</span>
      <span style="margin-left:6px;">${escapeHtml(shorten(t.task?.description || t.task?.tool || '?', 60))}</span>
      <span style="color:#718096;font-size:10px;margin-left:8px;">${fmtTime(t.at)}</span>`;
    ul.appendChild(li);
  });
  if (!(op.task_history || []).length) {
    ul.innerHTML = '<li class="placeholder">暂无任务记录</li>';
  }
}

// ============ ② 能力 ============
async function refreshDomains() {
  try {
    const r = await fetch(`${API}/api/domains`).then(r => r.json());
    state.domainsList = r.worlds || [];
    renderDomainCards();
  } catch (e) { console.warn('domains refresh:', e); }
}

async function fetchDomainDetail(id) {
  try {
    return await fetch(`${API}/api/domains/${id}`).then(r => r.json());
  } catch (e) { return null; }
}

function renderDomainCards() {
  const search = ($('domain-search').value || '').toLowerCase();
  const list = state.domainsList.filter(d => {
    if (!search) return true;
    return d.id.toLowerCase().includes(search) || (d.label || '').toLowerCase().includes(search);
  });
  const cards = $('domain-cards');
  cards.innerHTML = '';
  list.forEach(d => {
    const card = document.createElement('div');
    const statusCls = d.started ? 'started' : (d.enabled ? 'enabled' : 'disabled');
    card.className = `domain-card ${d.enabled ? 'enabled' : 'disabled'} ${d.id === state.selectedDomain ? 'selected' : ''}`;
    card.dataset.id = d.id;
    card.innerHTML = `
      <div class="dc-header">
        <span class="dc-name">${escapeHtml(d.label)}</span>
        <span class="dc-type">${escapeHtml(d.type)}</span>
        <span class="dc-state ${statusCls}">${statusCls}</span>
      </div>
      <div class="dc-row">
        <span class="dc-count">${d.tool_count} 工具</span>
      </div>
      <div class="dc-actions">
        <button class="dc-btn primary" data-action="enable" ${d.enabled ? 'disabled' : ''}>启用</button>
        <button class="dc-btn danger" data-action="disable" ${!d.enabled ? 'disabled' : ''}>停用</button>
        <button class="dc-btn" data-action="restart" ${!d.enabled ? 'disabled' : ''}>重启</button>
      </div>`;
    cards.appendChild(card);
  });
}

async function showDomainDetail(id) {
  state.selectedDomain = id;
  renderDomainCards();
  const d = await fetchDomainDetail(id);
  const detail = $('cap-detail');
  if (!d) {
    detail.innerHTML = '<div class="placeholder">加载失败</div>';
    return;
  }
  const statusCls = d.signal_status?.status || '?';
  let toolsHtml = '';
  (d.tools || []).forEach(t => {
    const params = Object.keys(t.parameters?.properties || {}).join(', ');
    toolsHtml += `<li>
      <div class="tool-name">${escapeHtml(t.name)}</div>
      <div class="tool-desc">${escapeHtml(t.description || '')}</div>
      <div class="tool-params">params: ${escapeHtml(params || '(none)')}</div>
    </li>`;
  });
  detail.innerHTML = `
    <div class="card">
      <div class="card-title">${escapeHtml(d.label)} <span style="color:#718096;">(${escapeHtml(d.id)})</span></div>
      <div class="kv-grid">
        <div class="kv"><span>类型</span><b>${escapeHtml(d.type)}</b></div>
        <div class="kv"><span>状态</span><b>${escapeHtml(statusCls)}</b></div>
        <div class="kv"><span>工具数</span><b>${d.tool_count ?? 0}</b></div>
        <div class="kv"><span>启动方式</span><b>${d.signal_status?.launch ?? '?'}</b></div>
      </div>
    </div>
    <div class="card">
      <div class="card-title">🔧 工具列表（${(d.tools || []).length}）</div>
      <ul class="detail-tools">${toolsHtml || '<li class="placeholder">无工具</li>'}</ul>
    </div>
    <div class="card">
      <div class="card-title">📝 提示词</div>
      <pre class="detail-prompt">${escapeHtml(d.prompt || '(空)')}</pre>
    </div>`;
}

async function domainAction(id, action) {
  try {
    const r = await fetch(`${API}/api/domains/${id}/${action}`, { method: 'POST' });
    const body = await r.json();
    if (!r.ok || body.detail) throw new Error(body.detail || '未知错误');
    await Promise.all([refreshDomains(), refreshTopbar()]);
    if (state.selectedDomain) showDomainDetail(state.selectedDomain);
  } catch (e) { alert(`操作失败: ${e.message}`); }
}

$('domain-cards').addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-action]');
  const card = e.target.closest('.domain-card');
  if (!card) return;
  const id = card.dataset.id;
  if (btn) {
    domainAction(id, btn.dataset.action);
  } else {
    showDomainDetail(id);
  }
});
$('domain-search').addEventListener('input', renderDomainCards);

// 重发现：重扫描能力域目录并挂载新增域，返回后刷新全部域信号
$('btn-rediscover').addEventListener('click', async () => {
  const btn = $('btn-rediscover');
  btn.disabled = true; btn.textContent = '发现中…';
  try {
    const r = await fetch(`${API}/api/domains/rediscover`, { method: 'POST' });
    const body = await r.json();
    if (!r.ok || body.detail) throw new Error(body.detail || '未知错误');
    await Promise.all([refreshDomains(), refreshTopbar(), refreshProcesses(), refreshOverview()]);
    const newly = body.newly_registered || [];
    const failed = body.start_failed || [];
    let msg = newly.length ? `重发现完成：新挂载 ${newly.join('、')}` : '重发现完成：未发现新域';
    if (failed.length) msg += `；启动失败 ${failed.map(f => f.id).join('、')}`;
    alert(`${msg}（当前共 ${(body.worlds || []).length} 个域）`);
  } catch (e) {
    alert(`重发现失败: ${e.message}`);
  } finally {
    btn.disabled = false; btn.textContent = '重发现';
  }
});

// ============ ③ 数据与进程 ============
async function refreshProcesses() {
  try {
    const r = await fetch(`${API}/api/processes`).then(r => r.json());
    state.processes = r;

    // EventBus 统计
    const bs = r.bus_stats || {};
    $('data-busstats').innerHTML = `
      <div class="kv"><span>已发射事件</span><b>${bs.emitted ?? 0}</b></div>
      <div class="kv"><span>已分发</span><b>${bs.dispatched ?? 0}</b></div>
      <div class="kv"><span>错误</span><b style="color:${bs.errors ? '#fc8181' : '#e2e8f0'}">${bs.errors ?? 0}</b></div>
      <div class="kv"><span>队列</span><b>${bs.queue_size ?? 0}</b></div>`;

    // 能力域状态
    const worlds = r.worlds?.worlds || [];
    $('data-domains').innerHTML = worlds.map(w => `
      <div class="proc-item">
        <span>${escapeHtml(w.label)}</span>
        <span class="proc-state ${w.enabled ? 'running' : 'stopped'}">${w.enabled ? '激活' : '停用'}${w.started ? ' · started' : ''}</span>
      </div>`).join('') || '<div class="placeholder">无</div>';

    // 子进程
    const procs = r.processes || [];
    $('data-processes').innerHTML = procs.length ? procs.map(p => `
      <div class="proc-item">
        <span>${escapeHtml(p.label)} <span style="color:#718096;">(${escapeHtml(p.domain)})</span></span>
        ${p.pid ? `<span class="proc-pid">PID ${p.pid}</span>` : ''}
        ${p.degraded ? `<span class="proc-state degraded">degraded</span>` : ''}
        <span class="proc-state ${p.pid ? 'running' : 'stopped'}">${p.pid ? '运行中' : '未启动'}</span>
      </div>`).join('') : '<div class="placeholder">无子进程引擎（或尚未懒启动）</div>';

    // 最近消息
    const msgs = r.recent_messages || [];
    $('data-messages').innerHTML = msgs.length ? msgs.map(m => `
      <li><span style="color:#4299e1;font-weight:600;">[${escapeHtml(m.source)}]</span>
        <span style="margin-left:6px;">${escapeHtml(shorten(m.text, 80))}</span></li>`).join('')
      : '<li class="placeholder">暂无消息</li>';
  } catch (e) { console.warn('processes refresh:', e); }
}

// ============ ④ 记忆 ============
async function refreshMemory() {
  try {
    const r = await fetch(`${API}/api/memory`).then(r => r.json());
    state.memory = r;
    renderMemoryPersona(r);
    renderMemoryState(r);
    renderMemoryLorebook(r);
    renderMemoryPrompt(r);
    refreshMemoryKernel();
    refreshCoding();
  } catch (e) { console.warn('memory refresh:', e); }
}

function renderMemoryPersona(m) {
  const p = m?.persona || {};
  const el = $('mem-persona');
  const interests = (p.interests || []).map(i =>
    `${escapeHtml(i.name)} <span style="color:#718096;">${i.subtopics?.join('/') || ''}</span>`).join('<br>');
  el.innerHTML = `<div class="mem-persona-block">
    <p><span class="p-label">名字</span>：${escapeHtml(p.display_name || p.name)} ${escapeHtml((p.nicknames||[]).join(' / '))}</p>
    <p style="margin-top:6px;"><span class="p-label">性格</span>：${escapeHtml(p.personality_text || '')}</p>
    <p style="margin-top:6px;"><span class="p-label">背景</span>：${escapeHtml(p.backstory || '')}</p>
    <p style="margin-top:6px;"><span class="p-label">爱好</span><br>${interests || '无'}</p>
    <p style="margin-top:6px;"><span class="p-label">说话风格</span>：${escapeHtml(JSON.stringify(p.speaking_style || {}))}</p>
    <p style="margin-top:6px;"><span class="p-label">行为约束</span>：${escapeHtml(JSON.stringify(p.constraints || {}))}</p>
  </div>`;
}

function renderMemoryState(m) {
  const s = m?.state || {};
  $('mem-state').innerHTML = `
    <div class="kv-grid">
      <div class="kv"><span>情绪</span><b>${escapeHtml(s.quadrant || '?')}</b></div>
      <div class="kv"><span>P</span><b>${s.pad?.P ?? '?'}</b></div>
      <div class="kv"><span>A</span><b>${s.pad?.A ?? '?'}</b></div>
      <div class="kv"><span>D</span><b>${s.pad?.D ?? '?'}</b></div>
      <div class="kv"><span>能量</span><b>${s.energy ?? '?'}/100</b></div>
      <div class="kv"><span>专注</span><b>${s.focus ?? '?'}/100</b></div>
      <div class="kv"><span>社交</span><b>${s.social_need ?? '?'}/100</b></div>
      <div class="kv"><span>好奇</span><b>${s.curiosity ?? '?'}/100</b></div>
    </div>`;
  const evts = m?.state_events || [];
  $('mem-stateevents').innerHTML = evts.length ? evts.map(e => `
    <li>${escapeHtml(e.event_type)} <span style="color:#718096;font-size:10px;">P${e.delta_P>0?'+':''}${e.delta_P} E${e.delta_energy>0?'+':''}${e.delta_energy}</span>
      <span style="color:#4a5568;font-size:10px;margin-left:6px;">${escapeHtml(e.trigger||'')}</span></li>`).join('')
    : '<li class="placeholder">暂无状态事件</li>';
}

function renderMemoryLorebook(m) {
  const lb = m?.lorebook || [];
  $('mem-lorebook').innerHTML = lb.length ? lb.map(x => `
    <div class="lorebook-item">
      ${(x.keywords || []).map(k => `<span class="kw">${escapeHtml(k)}</span>`).join('')}
      <div class="content">${escapeHtml(x.content || '')}</div>
    </div>`).join('') : '<div class="placeholder">知识库为空</div>';
}

// ============ ④-记忆内核（persona/memory.db） ============
let memoryKernelEditing = false;

async function refreshMemoryKernel() {
  // 正在内联编辑时跳过自动刷新，避免输入框被重渲染清掉
  if (memoryKernelEditing) return;
  try {
    const r = await fetch(`${API}/api/memory/kernel`).then(r => r.json());
    if (!r.available) {
      const msg = r.error || '记忆内核未启用';
      $('mk-status').innerHTML = `<div class="placeholder">${escapeHtml(msg)}</div>`;
      return;
    }
    renderMemoryKernelStatus(r.status || {});
    renderMemoryKernelProposals(r.proposals || []);
    renderMemoryKernelItems(r.items || []);
    renderMemoryKernelTurns(r.turns || []);
    renderMemoryKernelAudit((r.status || {}).recent_audit || []);
  } catch (e) { console.warn('memory kernel refresh:', e); }
}

function renderMemoryKernelStatus(st) {
  const pathEl = $('mk-path');
  if (pathEl) pathEl.textContent = st.db_path || '';
  const buckets = st.items || [];
  const total = buckets.reduce((n, x) => n + (x.n || 0), 0);
  const active = buckets.filter(x => x.status === 'active')
    .reduce((n, x) => n + (x.n || 0), 0);
  const pending = (st.proposals || []).find(x => x.status === 'pending');
  $('mk-status').innerHTML = `
    <div class="kv"><span>轮次摘要</span><b>${st.turn_summaries ?? 0}</b></div>
    <div class="kv"><span>长期条目</span><b>${active}/${total}</b></div>
    <div class="kv"><span>待确认</span><b>${pending?.n ?? 0}</b></div>
    <div class="kv"><span>审计记录</span><b>${st.audit_rows ?? 0}</b></div>
    <div class="kv"><span>项目键</span><b>${escapeHtml(st.project_key || '')}</b></div>
    <div class="kv"><span>摘要模型</span><b>${st.summary_enabled ? 'on' : 'off'}</b></div>`;
}

function renderMemoryKernelProposals(list) {
  const box = $('mk-proposals');
  if (!box) return;
  box.innerHTML = list.length ? list.map(p => `
    <div class="mk-row" data-pid="${escapeHtml(p.id)}">
      <div class="cs-head"><b>${escapeHtml(p.kind || '')}</b>
        <span class="cs-meta">${escapeHtml(p.created_at || '')} · 出现 ${p.frequency || 1} 次</span></div>
      <div class="cs-goal">${escapeHtml(p.text || '')}</div>
      ${p.reason ? `<div class="cs-meta">${escapeHtml(p.reason)}</div>` : ''}
      <div class="mk-actions">
        <button class="mk-accept">采纳</button>
        <button class="mk-edit">编辑后采纳</button>
        <button class="mk-reject">拒绝</button>
      </div>
    </div>`).join('') : '<div class="placeholder">没有待确认建议</div>';
}

function renderMemoryKernelItems(items) {
  const box = $('mk-items');
  if (!box) return;
  $('mk-items-count').textContent = `${items.length} 条`;
  box.innerHTML = items.length ? items.map(it => `
    <div class="mk-row">
      <div class="cs-head"><b>${escapeHtml(it.track || '')}</b>
        <span class="cs-meta">${escapeHtml(it.scope || '')}${
          it.scope_key ? ':' + escapeHtml(it.scope_key) : ''} · 重要度 ${it.importance ?? '-'} · 用到 ${it.use_count || 0} 次</span></div>
      <div class="cs-goal">${escapeHtml(it.text || '')}</div>
    </div>`).join('') : '<div class="placeholder">还没有长期记忆条目</div>';
}

function renderMemoryKernelTurns(turns) {
  const box = $('mk-turns');
  if (!box) return;
  $('mk-turns-count').textContent = `${turns.length} 条`;
  box.innerHTML = turns.length ? turns.map(t => `
    <div class="mk-row">
      <div class="cs-head"><b>${escapeHtml(t.source || '')}</b>
        <span class="cs-meta">${escapeHtml(t.created_at || '')} · ${escapeHtml(t.visibility || '')} · ${escapeHtml(t.user_id || '')}</span></div>
      <div class="cs-goal">${escapeHtml(t.summary || '')}</div>
      <details><summary>原始对话</summary>
        <div class="cs-meta">用户：${escapeHtml(t.user_text || '')}</div>
        <div class="cs-meta">回答：${escapeHtml(t.assistant_text || '')}</div>
      </details>
    </div>`).join('') : '<div class="placeholder">还没有轮次摘要</div>';
}

function renderMemoryKernelAudit(rows) {
  const box = $('mk-audit');
  if (!box) return;
  box.innerHTML = rows.length ? rows.map(a => `
    <li><span class="cs-meta">${escapeHtml(a.created_at || '')}</span>
      <b>${escapeHtml(a.action || '')}</b> ${escapeHtml(shorten(a.result || '', 90))}</li>`).join('')
    : '<li class="placeholder">暂无审计</li>';
}

async function searchMemoryKernel() {
  const q = ($('mk-search-q')?.value || '').trim();
  const box = $('mk-search-results');
  if (!q) { box.innerHTML = '<div class="placeholder">请输入关键词</div>'; return; }
  box.innerHTML = '<div class="placeholder">搜索中…</div>';
  try {
    const r = await fetch(
      `${API}/api/memory/kernel/search?q=${encodeURIComponent(q)}&limit=20`
    ).then(r => r.json());
    const rows = r.results || [];
    box.innerHTML = rows.length ? rows.map(x => `
      <div class="mk-row">
        <div class="cs-head"><b>${escapeHtml(x.kind || '')}</b>
          <span class="cs-meta">相关度 ${x.score ?? '-'} · ${escapeHtml(x.created_at || '')}</span></div>
        <div class="cs-goal">${escapeHtml(x.text || '')}</div>
      </div>`).join('') : '<div class="placeholder">没有匹配结果</div>';
  } catch (e) {
    box.innerHTML = `<div class="cs-err">搜索失败：${escapeHtml(e)}</div>`;
  }
}

async function decideMemoryProposal(pid, action, text) {
  try {
    const r = await fetch(`${API}/api/memory/kernel/decide`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: pid, action, text: text || '' }),
    }).then(r => r.json());
    if (!r.success) alert(r.error || '操作失败');
    memoryKernelEditing = false;
    refreshMemoryKernel();
  } catch (e) {
    memoryKernelEditing = false;
    alert(`操作失败：${e}`);
  }
}

if ($('mk-proposals')) {
  $('mk-proposals').addEventListener('click', (e) => {
    const row = e.target.closest('.mk-row');
    if (!row) return;
    const pid = row.dataset.pid;
    if (e.target.classList.contains('mk-accept')) {
      decideMemoryProposal(pid, 'accept');
    } else if (e.target.classList.contains('mk-reject')) {
      decideMemoryProposal(pid, 'reject');
    } else if (e.target.classList.contains('mk-edit')) {
      enterProposalEdit(row);
    } else if (e.target.classList.contains('mk-save')) {
      const val = (row.querySelector('.mk-edit-input')?.value || '').trim();
      if (!val) { alert('内容不能为空'); return; }
      decideMemoryProposal(pid, 'edit', val);
    } else if (e.target.classList.contains('mk-cancel')) {
      memoryKernelEditing = false;
      refreshMemoryKernel();
    }
  });
}

function enterProposalEdit(row) {
  const goal = row.querySelector('.cs-goal');
  if (!goal) return;
  memoryKernelEditing = true;
  const input = document.createElement('textarea');
  input.className = 'mk-edit-input';
  input.rows = 2;
  input.value = goal.textContent || '';
  goal.replaceWith(input);
  const actions = row.querySelector('.mk-actions');
  if (actions) {
    actions.innerHTML = '<button class="mk-save">保存</button>'
      + '<button class="mk-cancel">取消</button>';
  }
  input.focus();
}
if ($('btn-mk-refresh')) {
  $('btn-mk-refresh').addEventListener('click', refreshMemoryKernel);
}
if ($('btn-mk-search')) {
  $('btn-mk-search').addEventListener('click', searchMemoryKernel);
}
if ($('mk-search-q')) {
  $('mk-search-q').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') searchMemoryKernel();
  });
}
if ($('btn-mk-add')) {
  $('btn-mk-add').addEventListener('click', async () => {
    const track = $('mk-add-track').value;
    const text = ($('mk-add-text').value || '').trim();
    const reason = ($('mk-add-reason').value || '').trim();
    const st = $('mk-add-status');
    if (!text) { st.textContent = '内容不能为空'; return; }
    try {
      const r = await fetch(`${API}/api/memory/kernel/add`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ track, text, reason }),
      }).then(r => r.json());
      st.textContent = r.success
        ? `已加入待确认队列（${(r.proposal_id || '').slice(0, 8)}）`
        : (r.error || '添加失败');
      if (r.success) {
        $('mk-add-text').value = '';
        $('mk-add-reason').value = '';
        refreshMemoryKernel();
      }
    } catch (e) { st.textContent = `添加失败：${e}`; }
  });
}


// ============ ④-能力 登记表可视化 ============
async function refreshCapabilities() {
  const box = $('mem-capability-list');
  if (!box) return;
  try {
    const d = await fetch(`${API}/api/capabilities`).then(r => r.json());
    renderCapabilities(d);
  } catch (e) { console.warn('capability refresh:', e); }
}

function renderCapabilities(d) {
  const box = $('mem-capability-list');
  if (!box) return;
  const caps = d?.capabilities || [];
  const cap = $('cap-summary');
  if (cap) cap.textContent = `自动准入上限 ${d?.auto_risk || '?'} · 共 ${caps.length} 个`;

  box.innerHTML = caps.length ? caps.map(c => {
    const color = c.status === 'active' ? '#68d391'
      : c.status === 'staging' ? '#f6e05e'
      : c.status === 'pending_review' ? '#f6ad55' : '#fc8181';
    const st = c.stats || {};
    const rate = st.calls ? Math.round((st.success / st.calls) * 100) : null;
    const g3 = c.gates?.g3_tools || {};
    const exec = (g3.executed || []).map(e => `${e.tool}${e.ok ? ' ✓' : ' ✗'}`).join(', ');
    const skip = (g3.skipped || []).length;
    return `<div class="coding-session" data-cap="${escapeHtml(c.id)}">
      <div class="cs-head"><b>${escapeHtml(c.label || c.id)}</b>
        <span style="color:${color}">${escapeHtml(c.status || '?')}</span>
        <span class="cs-meta">${escapeHtml(c.risk || '')} · ${st.calls || 0} 次调用${
          rate === null ? '' : ` · ${rate}% 成功`}</span></div>
      <div class="cs-goal">${escapeHtml(c.id)} · 来源 ${escapeHtml(c.source || '?')}${
        c.session_id ? ` · 会话 #${c.session_id}` : ''}</div>
      <div class="cs-meta">G1 ${c.gates?.g1_static?.passed ? '✓' : '✗'} ·
        G2 ${c.gates?.g2_smoke?.passed ? '✓' : '✗'} ·
        G3 ${g3.passed ? '✓' : '✗'}${exec ? ` (${escapeHtml(exec)})` : ''}${
        skip ? `，跳过 ${skip}` : ''}</div>
      ${c.reason ? `<div class="cs-err">${escapeHtml(c.reason)}</div>` : ''}
    </div>`;
  }).join('') : '<div class="placeholder">还没有自生成能力</div>';
}


const codingState = { sessionId: null, data: null };

async function refreshCoding(sessionId) {
  const sid = (sessionId === undefined) ? codingState.sessionId : sessionId;
  try {
    const q = sid ? `?session_id=${encodeURIComponent(sid)}` : '';
    const r = await fetch(`${API}/api/coding${q}`).then(r => r.json());
    codingState.data = r;
    if (r && r.session_id) codingState.sessionId = r.session_id;
    renderCoding(r);
  } catch (e) { console.warn('coding refresh:', e); }
}

function renderCoding(d) {
  const box = $('mem-coding-sessions');
  if (!box) return;
  const ss = d?.sessions || [];
  box.innerHTML = ss.length ? ss.map(s => {
    const active = s.id === d.session_id;
    const color = s.status === 'done' ? '#68d391'
      : (s.status === 'running' ? '#f6e05e' : '#fc8181');
    return `<div class="coding-session${active ? ' active' : ''}" data-sid="${s.id}">
      <div class="cs-head"><b>#${s.id}</b>
        <span style="color:${color}">${escapeHtml(s.status || '?')}</span>
        <span class="cs-meta">${s.rounds ?? 0} 轮 / ${s.tool_calls ?? 0} 次工具</span></div>
      <div class="cs-goal">${escapeHtml(s.goal || '')}</div>
      <div class="cs-meta">${escapeHtml(s.started_at || '')} · ${escapeHtml(s.model || '')}</div>
      ${s.final_error ? `<div class="cs-err">${escapeHtml(s.final_error)}</div>` : ''}
    </div>`;
  }).join('') : '<div class="placeholder">暂无编程会话</div>';

  box.querySelectorAll('.coding-session').forEach(el => {
    el.addEventListener('click', () => refreshCoding(parseInt(el.dataset.sid, 10)));
  });

  const sidEl = $('mem-coding-sid');
  if (sidEl) sidEl.textContent = d?.session_id ? `（会话 #${d.session_id}）` : '';
  const steps = d?.steps || [];
  const ul = $('mem-coding-steps');
  if (ul) {
    ul.innerHTML = steps.length ? steps.map(st => `
      <li><b>r${st.round_no}</b>
        <span class="cs-meta">${escapeHtml(st.phase || '')}</span>
        ${escapeHtml(st.tool || '')}
        <span style="color:${st.success ? '#68d391' : '#fc8181'}">${st.success ? '✓' : '✗'}</span>
        <span class="cs-meta">${escapeHtml(st.detail || '')}</span></li>`).join('')
      : '<li class="placeholder">暂无步骤（或会话尚未开始）</li>';
  }
}

function renderMemoryPrompt(m) {
  const prompts = m?.prompts || {};
  const text = prompts[state.currentPrompt] || '(无)';
  $('mem-prompt').textContent = text;
}

// ============ ⑤ 日志 ============
async function refreshLogs() {
  try {
    const r = await fetch(`${API}/api/logs?limit=100`).then(r => r.json());
    const ul = $('log-list');
    if (!ul) return;
    ul.innerHTML = '';
    (r.logs || []).forEach(l => {
      if (!state.logFilters.has(l.level)) return;
      const li = document.createElement('li');
      li.className = `log-${l.level}`;
      li.innerHTML = `<span class="ev-time">${fmtTime(l.timestamp)}</span>
        <span class="log-source">${escapeHtml(l.source)}</span>
        <span>${escapeHtml(l.message)}</span>`;
      ul.appendChild(li);
    });
    ul.scrollTop = ul.scrollHeight;
  } catch (e) { console.warn('logs refresh:', e); }
}

function appendEvent(eventType, payload) {
  const li = document.createElement('li');
  li.className = `ev-${eventType}`;
  let body = '';
  const ts = payload?.timestamp || (Date.now() / 1000);
  if (eventType === 'user_message') body = `[${escapeHtml(payload.source||'?')}] ${escapeHtml(payload.text)}`;
  else if (eventType === 'tts_speak') body = escapeHtml(payload.text || '');
  else if (eventType === 'state_updated') {
    const s = payload.state || {};
    body = `pad=${s.quadrant || (s.pad||{}).quadrant || '?'} E=${s.energy ?? '?'} F=${s.focus ?? '?'}`;
  } else if (eventType === 'perception_updated') {
    body = `${payload.source_capability||'?'}: ${escapeHtml(JSON.stringify(payload.perception||{}).slice(0,60))}`;
  } else if (eventType === 'operator_done') {
    body = `${escapeHtml(payload.task?.tool||'?')} ok=${payload.result?.success?'✓':'✗'}`;
  } else if (eventType === 'operator_progress') {
    const p = payload.progress || {};
    body = `${escapeHtml(payload.task?.tool||'?')} ${p.percent ?? '?'}% [${p.stage||''}] ${escapeHtml(p.detail||'')}`;
  } else if (eventType === 'will_engine_decided') {
    body = `${payload.type||'?'} → ${escapeHtml(payload.target||'')}`;
  } else if (eventType.startsWith('domain_')) {
    body = escapeHtml(JSON.stringify(payload).slice(0, 80));
  } else {
    body = escapeHtml(JSON.stringify(payload).slice(0, 80));
  }
  li.innerHTML = `<span class="ev-time">${fmtTime(ts)}</span>
    <span class="ev-name">${escapeHtml(eventType)}</span>
    <div style="margin-top:2px;color:#a0aec0;">${body}</div>`;
  $('event-log').appendChild(li);
  $('event-log').scrollTop = $('event-log').scrollHeight;
  while ($('event-log').children.length > MAX_EVENTS) $('event-log').removeChild($('event-log').firstChild);
}

// ============ WS ============
function connectWS() {
  const ws = new WebSocket(WS_EVENTS);
  ws.onopen = () => {
    $('pill-ws').textContent = '在线';
    $('pill-ws').classList.remove('gray'); $('pill-ws').classList.add('green');
  };
  ws.onmessage = (e) => {
    try {
      const evt = JSON.parse(e.data);
      if (state.eventFilters.has(evt.event_type)) appendEvent(evt.event_type, evt.payload || {});
      if (evt.event_type === 'state_updated') { refreshTopbar(); refreshBrains(); refreshOverview(); }
      else if (evt.event_type === 'will_engine_decided' || evt.event_type === 'operator_progress' ||
               evt.event_type === 'task_started' || evt.event_type === 'task_done' || evt.event_type === 'task_failed') {
        refreshBrains(); refreshOverview();
      }
      else if (evt.event_type === 'user_message') { refreshBrains(); refreshOverview(); }
      else if (evt.event_type.startsWith('domain_')) { refreshDomains(); refreshTopbar(); refreshProcesses(); refreshOverview(); }
    } catch (err) { console.warn('WS parse:', err); }
  };
  ws.onclose = () => {
    $('pill-ws').textContent = '离线';
    $('pill-ws').classList.remove('green'); $('pill-ws').classList.add('gray');
    setTimeout(connectWS, 3000);
  };
  ws.onerror = () => ws.close();
}

// ============ 全局对话输入栏 ============
function chatbarMsg(role, text, meta = '') {
  const el = $('chatbar-status');
  if (!el) return;
  el.title = text || '';
  el.innerHTML = `<span class="cb-role ${role}">${role === 'user' ? '👤 你' : '🤖 维尔薇'}</span>
    <span class="cb-text">${escapeHtml(shorten(text, 70))}</span>
    ${meta ? `<span class="cb-meta">${meta}</span>` : ''}`;
}

// ============ 主播态按需抓帧 ============
const VISION_MAX_WIDTH = 1024;
const VISION_JPEG_QUALITY = 0.75;
const visionState = {
  streams: {},
  videos: {},
  isActive(source) {
    return !!this.streams[source];
  },
  async start(source) {
    if (this.streams[source]) return;
    let stream;
    if (source === 'screen') {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
        throw new Error('当前浏览器不支持屏幕共享');
      }
      stream = await navigator.mediaDevices.getDisplayMedia({
        video: { frameRate: 5 },
        audio: false,
      });
    } else {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        throw new Error('当前浏览器不支持摄像头');
      }
      stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      });
    }

    const video = $(`vision-video-${source}`);
    if (!video) {
      stream.getTracks().forEach(t => t.stop());
      throw new Error('预览元素不存在');
    }
    video.srcObject = stream;
    // 先显示预览，避免 video 在 display:none 下拿不到元数据
    const strip = $('vision-strip');
    if (strip) strip.hidden = false;
    const thumb = $(`vision-thumb-${source}`);
    if (thumb) thumb.hidden = false;
    try {
      await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('画面初始化超时')), 8000);
        const done = () => { clearTimeout(timer); resolve(); };
        if (video.readyState >= 2) { done(); return; }
        video.onloadedmetadata = done;
      });
    } catch (e) {
      stream.getTracks().forEach(t => t.stop());
      video.srcObject = null;
      this.render();
      throw e;
    }
    try { await video.play(); } catch (e) { /* 自动播放被拦时忽略，抓帧仍可用 */ }

    this.streams[source] = stream;
    this.videos[source] = video;
    const track = stream.getVideoTracks()[0];
    if (track) track.onended = () => this.stop(source);
    this.render();
  },
  stop(source) {
    const stream = this.streams[source];
    if (stream) stream.getTracks().forEach(t => t.stop());
    delete this.streams[source];
    delete this.videos[source];
    const video = $(`vision-video-${source}`);
    if (video) video.srcObject = null;
    this.render();
  },
  render() {
    const strip = $('vision-strip');
    if (strip) strip.hidden = Object.keys(this.streams).length === 0;
    ['screen', 'camera'].forEach(src => {
      const thumb = $(`vision-thumb-${src}`);
      const btn = $(`vision-btn-${src}`);
      if (thumb) thumb.hidden = !this.streams[src];
      if (btn) btn.classList.toggle('active', !!this.streams[src]);
    });
  },
  async captureFrame(source) {
    const video = this.videos[source];
    if (!video || !this.streams[source] || video.readyState < 2) return null;

    let bitmap = null;
    try {
      if (typeof window.ImageCapture === 'function') {
        const track = this.streams[source].getVideoTracks()[0];
        if (track) bitmap = await new ImageCapture(track).grabFrame();
      }
    } catch (e) { bitmap = null; }

    const srcW = bitmap ? bitmap.width : video.videoWidth;
    const srcH = bitmap ? bitmap.height : video.videoHeight;
    if (!srcW || !srcH) return null;

    const scale = Math.min(1, VISION_MAX_WIDTH / srcW);
    const width = Math.max(1, Math.round(srcW * scale));
    const height = Math.max(1, Math.round(srcH * scale));
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;
    if (bitmap) ctx.drawImage(bitmap, 0, 0, width, height);
    else ctx.drawImage(video, 0, 0, width, height);

    return {
      source,
      data: canvas.toDataURL('image/jpeg', VISION_JPEG_QUALITY),
      mime_type: 'image/jpeg',
    };
  },
  async captureAll() {
    const images = [];
    for (const source of ['screen', 'camera']) {
      if (!this.streams[source]) continue;
      try {
        const img = await this.captureFrame(source);
        if (img) images.push(img);
      } catch (e) {
        console.warn(`capture ${source} failed:`, e);
      }
    }
    return images;
  },
};

async function toggleVision(source) {
  const label = source === 'screen' ? '屏幕' : '摄像头';
  try {
    if (visionState.isActive(source)) {
      visionState.stop(source);
      chatbarMsg('sys', `已关闭${label}`);
    } else {
      await visionState.start(source);
      chatbarMsg('sys', `✓ 已开启${label}，发消息时抓一帧`);
    }
  } catch (e) {
    chatbarMsg('sys', `✗ 开启${label}失败: ${e.message || e}`);
  }
}
$('vision-btn-screen').addEventListener('click', () => toggleVision('screen'));
$('vision-btn-camera').addEventListener('click', () => toggleVision('camera'));

async function sendText() {
  const text = $('chat-input').value.trim();
  const hasStream = Object.keys(visionState.streams).length > 0;
  if (!text && !hasStream) return;

  const source = $('chat-source').value;
  $('chat-send').disabled = true;
  let images = [];
  try {
    images = await visionState.captureAll();
  } catch (e) {
    console.warn('capture all failed:', e);
  }
  if (!text && images.length === 0) {
    $('chat-send').disabled = false;
    return;
  }

  const outgoingText = text || '（看看这个）';
  const meta = `[${fmtTime(Date.now()/1000)} · ${source}${images.length ? ` · 图${images.length}` : ''}]`;
  chatbarMsg('user', outgoingText, meta);
  $('chat-input').value = '';
  try {
    const r = await fetch(`${API}/api/input`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text: outgoingText,
        user: 'web_console',
        source,
        images,
        metadata: { ui: 'web_console_v3' },
      }),
    });
    const body = await r.json();
    if (r.ok && body.success) {
      chatbarMsg('sys', `✓ 已发送（→ ${body.routed_event}${body.image_count ? ` · 图${body.image_count}` : ''}）`);
    } else {
      chatbarMsg('sys', `✗ 失败: ${JSON.stringify(body)}`);
    }
  } catch (e) {
    chatbarMsg('sys', `✗ 网络错误: ${e.message}`);
  } finally {
    $('chat-send').disabled = false;
    $('chat-input').focus();
  }
}
$('chat-form').addEventListener('submit', (e) => { e.preventDefault(); sendText(); });
$('chat-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    sendText();
  }
  // 自动增高
  setTimeout(() => { e.target.style.height = 'auto'; e.target.style.height = Math.min(e.target.scrollHeight, 90) + 'px'; }, 0);
});

// ============ 意志脑 ============
async function refreshWill() {
  try {
    const w = await fetch(`${API}/api/will`).then(r => r.json());
    if (!w.available) {
      $('will-identity').textContent = 'WillSoul 不可用（检查 will_engine 段或数据库）';
      return;
    }
    $('will-signature').textContent = w.signature || '';
    $('will-identity').textContent = w.identity || '（未配置 will_engine 段）';
    $('will-replay').textContent = w.replay || '（暂无决策记忆）';
    $('will-knowledge').textContent = w.knowledge || '（能力清单不可用）';

    const exps = w.experience || [];
    $('will-experience').innerHTML = exps.length
      ? `<div class="kv-grid">${exps.map(e => `
        <div class="kv"><span>${escapeHtml(e.action_type)}</span>
          <b>${e.successes}/${e.attempts}｜${e.avg_outcome >= 0 ? '+' : ''}${e.avg_outcome}</b></div>`).join('')}</div>`
      : '<div class="placeholder">暂无经验（完成一次任务后出现）</div>';

    const bonds = w.bonds || [];
    $('will-bonds').innerHTML = bonds.length
      ? `<div class="kv-grid">${bonds.map(b => `
        <div class="kv"><span>${escapeHtml(b.user_id)}</span>
          <b>熟悉 ${b.familiarity}｜亲近 ${b.attachment}｜${b.interactions} 次</b></div>`).join('')}</div>`
      : '<div class="placeholder">还没有常驻的人（多聊几次就会出现）</div>';

    $('will-routine').textContent = (w.routine || [])
      .map(r => `${r.time}　${r.action}　${r.prompt || ''}`).join('\n') || '（未配置作息）';
    $('will-mode').textContent =
      `自主执行：${w.autonomous_execute ? '开' : '关'}｜` +
      `安静时段：${(w.quiet_hours || []).join(' - ') || '无'}｜` +
      `当前${w.is_quiet ? '处于' : '不在'}安静时段`;

    const rows = w.decisions || [];
    $('will-decisions').innerHTML = rows.length
      ? `<ul class="plain-list">${rows.map(d => {
          const done = (d.success === null || d.success === undefined);
          const res = done ? '未完成' : (d.success ? '成功' : '失败');
          const cls = done ? '' : (d.success ? 'ok' : 'fail');
          const target = d.action_target ? `（${escapeHtml(d.action_target)}）` : '';
          return `<li><span class="cs-meta">${escapeHtml(d.decided_at || '')}</span>
            ${escapeHtml(d.action_type || '')}${target} → <b class="${cls}">${res}</b>
            <br><span class="cs-meta">${escapeHtml((d.reasoning || '').slice(0, 70))}</span></li>`;
        }).join('')}</ul>`
      : '<div class="placeholder">暂无决策</div>';
  } catch (e) { console.warn('will refresh:', e); }
}

// ============ Tab 切换 ============
function switchMainTab(tab) {
  document.querySelectorAll('.mtab[data-tab]').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.toggle('active', p.id === `tab-${tab}`));
  // 惰性刷新
  if (tab === 'overview') refreshOverview();
  if (tab === 'tasks') refreshTasks();
  if (tab === 'data') refreshProcesses();
  if (tab === 'memory') refreshMemory();
  if (tab === 'will') refreshWill();
  if (tab === 'logs') refreshLogs();
}
document.querySelectorAll('.mtab[data-tab]').forEach(tab => {
  tab.addEventListener('click', () => switchMainTab(tab.dataset.tab));
});

// 意志脑：刷新 / 导出
if ($('btn-will-refresh')) {
  $('btn-will-refresh').addEventListener('click', refreshWill);
}
if ($('btn-will-export')) {
  $('btn-will-export').addEventListener('click', async () => {
    try {
      const r = await fetch(`${API}/api/will/export`, { method: 'POST' }).then(r => r.json());
      $('will-export-status').textContent = r.success
        ? `已导出：${r.path}` : `导出失败：${r.error || '未知错误'}`;
    } catch (e) {
      $('will-export-status').textContent = `导出失败：${e}`;
    }
  });
}

// 思维子 tab
document.querySelectorAll('.btab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.btab').forEach(b => b.classList.toggle('active', b === btn));
    document.querySelectorAll('.brain-view').forEach(v => v.classList.toggle('active', v.id === `view-${btn.dataset.brain}`));
  });
});

// 记忆子 tab
document.querySelectorAll('.mtab[data-mtab]').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.mtab[data-mtab]').forEach(b => b.classList.toggle('active', b === btn));
    document.querySelectorAll('.mview').forEach(v => v.classList.toggle('active', v.id === `mview-${btn.dataset.mtab}`));
    if (btn.dataset.mtab === 'coding') refreshCoding(codingState.sessionId);
    if (btn.dataset.mtab === 'capability') refreshCapabilities();
    if (btn.dataset.mtab === 'state') refreshMemory();
    if (btn.dataset.mtab === 'kernel') refreshMemoryKernel();
  });
});

// 提示词切换
document.querySelectorAll('.ptab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.ptab').forEach(b => b.classList.toggle('active', b === btn));
    state.currentPrompt = btn.dataset.prompt;
    renderMemoryPrompt(state.memory);
  });
});

// 日志过滤
document.querySelectorAll('.log-filters input[type=checkbox]').forEach(cb => {
  cb.addEventListener('change', () => {
    if (cb.checked) state.logFilters.add(cb.value);
    else state.logFilters.delete(cb.value);
    refreshLogs();
  });
});
// 事件过滤
document.querySelectorAll('.event-filters input[type=checkbox]').forEach(cb => {
  cb.addEventListener('change', () => {
    if (cb.checked) state.eventFilters.add(cb.value);
    else state.eventFilters.delete(cb.value);
  });
});

// ============ 启动 ============
$('btn-refresh').addEventListener('click', () => {
  refreshTopbar(); refreshBrains(); refreshDomains(); refreshProcesses(); refreshMemory(); refreshOverview();
});

refreshTopbar();
refreshBrains();
refreshDomains();
refreshLogs();
refreshOverview();
connectWS();
setInterval(refreshBrains, 5000);
setInterval(refreshDomains, 10000);
setInterval(refreshLogs, 5000);
setInterval(refreshProcesses, 8000);
setInterval(refreshOverview, 8000);
// 编程面板：仅在该子页可见时刷新（保持当前选中的会话）
setInterval(() => {
  const v = document.getElementById('mview-coding');
  if (v && v.classList.contains('active')) refreshCoding(codingState.sessionId);
}, 4000);
// 能力面板：仅在该子页可见时刷新（准入过程中能看到状态变化）
setInterval(() => {
  const v = document.getElementById('mview-capability');
  if (v && v.classList.contains('active')) refreshCapabilities();
}, 4000);
// 记忆内核面板：仅在该子页可见时刷新
setInterval(() => {
  const v = document.getElementById('mview-kernel');
  if (v && v.classList.contains('active')) refreshMemoryKernel();
}, 8000);
// ============ 🛰️ 任务总览 ============
const TK_PHASE = {
  running: '进行中', done: '完成', failed: '失败',
  blocked: '阻塞', pending: '待开始',
};

function fmtBytes(n) {
  n = Number(n) || 0;
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1073741824) return `${(n / 1048576).toFixed(1)} MB`;
  return `${(n / 1073741824).toFixed(2)} GB`;
}

function fmtClock(ts) {
  if (!ts) return '';
  const d = new Date(Number(ts) * 1000);
  if (isNaN(d.getTime())) return '';
  const p = n => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

async function refreshTasks() {
  try {
    const r = await fetch(`${API}/api/overview`).then(res => res.json());
    renderTasksView(r);
  } catch (e) {
    console.warn('tasks refresh:', e);
    const el = $('tk-tasks');
    if (el) el.innerHTML = `<div class="placeholder">加载失败：${escapeHtml(e.message || e)}</div>`;
  }
}

// 展开状态在 3s 轮询之间保持
const openTaskIds = new Set();

function toggleTaskDetail(taskId) {
  if (!taskId) return;
  const open = !openTaskIds.has(taskId);
  if (open) openTaskIds.add(taskId);
  else openTaskIds.delete(taskId);
  document.querySelectorAll('.tk-task').forEach(el => {
    if (el.dataset.taskId === taskId) el.classList.toggle('open', open);
  });
}

// 事件委托：面板每 3s 重绘，委托挂在 document 上才不会失效
document.addEventListener('click', (e) => {
  const head = e.target.closest && e.target.closest('.tk-task-head');
  if (!head) return;
  const row = head.closest('.tk-task');
  if (row && row.dataset.taskId) toggleTaskDetail(row.dataset.taskId);
});

function renderTasksView(r) {
  const rt = r.runtime || {};
  const tasks = r.tasks || [];
  const procs = r.processes || [];
  const storage = r.storage || { items: [], total_bytes: 0 };
  const conv = r.conversation || [];
  const llmRows = r.llm || [];

  const running = tasks.filter(t => t.phase === 'running').length;
  const liveProcs = procs.filter(p => p.pid).length;
  const mainLlm = llmRows.find(x => x.purpose === 'streamer') || llmRows[0] || null;

  // ---- KPI ----
  if ($('tk-hint')) {
    $('tk-hint').textContent =
      `运行 ${fmtDuration(rt.uptime_sec)} · ${rt.persona || '-'} · ${rt.quadrant || '-'}`;
  }
  $('tk-kpi').innerHTML = [
    ['进行中任务', running, `可见 ${tasks.length} 条`],
    ['活跃子进程', liveProcs, `已挂载 ${procs.length} 域`],
    ['对话条数', conv.length, `事件 ${rt.bus?.emitted ?? 0}`],
    ['存储占用', fmtBytes(storage.total_bytes), `${storage.items.length} 个观测点`],
    ['生效模型', mainLlm ? shorten(mainLlm.model, 22) : '未初始化',
      mainLlm ? (mainLlm.fallback ? '已降级' : '主模型') : '尚无调用'],
    ['操作脑队列', rt.operator_queue ?? 0, `事件错误 ${rt.bus?.errors ?? 0}`],
  ].map(([k, v, s]) => `
    <div class="kv"><span>${k}</span><b>${escapeHtml(String(v))}</b>
      <span style="color:#4a5568;">${escapeHtml(String(s))}</span></div>`).join('');

  // ---- 任务 ----
  const visibleIds = new Set(tasks.map(t => t.task_id));
  openTaskIds.forEach(id => { if (!visibleIds.has(id)) openTaskIds.delete(id); });
  if ($('tk-task-hint')) {
    const patrol = rt.patrol || {};
    const patrolText = patrol.at
      ? `思维脑巡检 ${Math.max(0, Math.round((Date.now() / 1000) - patrol.at))}s 前`
      : '思维脑待巡检';
    $('tk-task-hint').textContent =
      `共 ${tasks.length} 条 · ${patrolText} · 点击展开明细 · 关账由思维脑执行`;
  }
  $('tk-tasks').innerHTML = tasks.length ? tasks.map(t => {
    const phase = t.phase || 'pending';
    const pct = Math.max(0, Math.min(100, Number(t.percent) || 0));
    const meta = [t.source, t.owner, t.stage].filter(Boolean).join(' · ');
    const tid = t.task_id || '';
    const open = openTaskIds.has(tid);
    const events = t.events || [];
    const age = t.age_sec || 0;
    const ageText = age < 60 ? `${Math.max(0, Math.round(age))}s前`
      : age < 3600 ? `${Math.round(age / 60)}分钟前`
      : `${(age / 3600).toFixed(1)}小时前`;
    const timeline = events.length ? events.map(ev => `
        <div class="tk-ev">
          <span class="tk-ev-time">${fmtClock(ev.at)}</span>
          <span class="tk-ev-kind">${escapeHtml(ev.kind || ev.actor || '')}</span>
          <span class="tk-ev-detail">${escapeHtml(ev.detail || '')}</span>
        </div>`).join('') : '<div class="placeholder">暂无过程记录</div>';
    const plan = t.plan || [];
    const planHtml = plan.length ? `
        <div class="tk-ev-title" style="margin-top:10px;">思维脑计划（${plan.length}）</div>
        ${plan.map((p, i) => `
          <div class="tk-plan">
            <div class="tk-plan-goal">${i + 1}. ${escapeHtml(p.subgoal || '')}</div>
            <div class="tk-plan-meta">验收：${escapeHtml(p.acceptance || '-')}
              ${p.suggested_tools && p.suggested_tools.length ? '｜建议：' + escapeHtml(p.suggested_tools.join('/')) : ''}
              ${p.notes ? '｜' + escapeHtml(p.notes) : ''}
            </div>
          </div>`).join('')}` : '';
    return `
      <div class="tk-task ${open ? 'open' : ''}" data-task-id="${escapeHtml(tid)}">
        <div class="tk-task-head">
          <span class="tk-caret">▸</span>
          <span class="tk-badge ${escapeHtml(phase)}">${TK_PHASE[phase] || escapeHtml(phase)}</span>
          <span class="tk-task-title" title="${escapeHtml(t.title || '')}">${escapeHtml(t.title || '(无标题)')}</span>
          <span class="tk-task-meta">${pct}%${meta ? ' · ' + escapeHtml(meta) : ''} · ${ageText}</span>
        </div>
        <div class="tk-bar"><i style="width:${pct}%"></i></div>
        ${t.detail ? `<div class="tk-task-detail">${escapeHtml(t.detail)}</div>` : ''}
        <div class="tk-task-body">
          ${planHtml}
          <div class="tk-ev-title" style="margin-top:10px;">过程记录（${events.length}）</div>
          ${timeline}
        </div>
      </div>`;
  }).join('') : '<div class="placeholder">当前没有进行中的任务</div>';

  // ---- 子进程 ----
  if ($('tk-proc-hint')) {
    $('tk-proc-hint').textContent = `活跃 ${liveProcs} / 共 ${procs.length}`;
  }
  const sortedProcs = procs.slice().sort((a, b) => {
    const rank = p => (p.pid ? 0 : (p.started ? 1 : 2));
    return rank(a) - rank(b);
  });
  $('tk-procs').innerHTML = sortedProcs.length ? sortedProcs.map(p => {
    const cls = p.pid ? 'live' : (p.started ? 'lazy' : '');
    const state = p.pid ? `PID ${p.pid}` : (p.started ? '已启动' : '未启动');
    return `
      <div class="tk-proc">
        <span class="tk-dot ${cls}"></span>
        <span class="tk-proc-name">${escapeHtml(p.label || p.domain)}</span>
        <span class="tk-proc-sub">${escapeHtml(p.domain)} · ${state}${p.degraded ? ' · ' + escapeHtml(p.degraded) : ''}</span>
      </div>`;
  }).join('') : '<div class="placeholder">没有能力域</div>';

  // ---- 存储 ----
  const maxBytes = Math.max(1, ...storage.items.map(i => i.bytes || 0));
  if ($('tk-storage-hint')) {
    $('tk-storage-hint').textContent = `合计 ${fmtBytes(storage.total_bytes)}`;
  }
  $('tk-storage').innerHTML = storage.items.length ? storage.items.map(i => `
    <div class="tk-storage-row">
      <div class="tk-storage-head">
        <b>${escapeHtml(i.label)}</b>
        <span>${fmtBytes(i.bytes)}${i.files ? ` · ${i.files} 文件` : ''}${i.exists === false ? ' · 不存在' : ''}</span>
      </div>
      <div class="tk-bar"><i style="width:${Math.round((i.bytes || 0) / maxBytes * 100)}%"></i></div>
    </div>`).join('') : '<div class="placeholder">没有数据</div>';

  // ---- 对话 ----
  if ($('tk-chat-hint')) $('tk-chat-hint').textContent = `${conv.length} 条`;
  const chatEl = $('tk-chat');
  if (!conv.length) {
    chatEl.innerHTML = '<div class="placeholder">暂无对话</div>';
  } else {
    chatEl.innerHTML = conv.map(m => {
      const assistant = m.role === 'assistant';
      return `
        <div class="tk-chat-row ${assistant ? 'assistant' : 'user'}">
          <span class="tk-chat-role">${assistant ? '主播脑' : '用户'}</span>
          <span class="tk-chat-text" title="${escapeHtml(m.text || '')}">${escapeHtml(m.text || '')}</span>
          <span class="tk-chat-ts">${fmtClock(m.ts)}</span>
        </div>`;
    }).join('');
    chatEl.scrollTop = chatEl.scrollHeight;
  }
}

// 任务面板：仅在该页可见时刷新
setInterval(() => {
  const v = document.getElementById('tab-tasks');
  if (v && v.classList.contains('active')) refreshTasks();
}, 3000);

