// Disclosure monitoring queue: one filing-level case, evidence-first brief.
const monitorState = {cases: [], selected: null, detail: null, busy: false, detailRequest: 0};

function monitorSetStatus(text) {
  const node = $('#monitor-status');
  if (node) node.textContent = text;
}

function monitorSetBusy(flag) {
  monitorState.busy = flag;
  for (const id of ['#monitor-scan', '#monitor-refresh', '#monitor-status-filter']) {
    const node = $(id);
    if (node) node.disabled = flag;
  }
  const button = $('#monitor-scan');
  if (button) button.textContent = flag ? '扫描中…' : '扫描自选公司';
}

async function loadMonitoring() {
  monitorSetStatus('正在读取本机监控队列…');
  try {
    const [cases, runs] = await Promise.all([
      api('/api/monitoring/cases' + (($('#monitor-status-filter') || {}).value ? '?status=' + encodeURIComponent($('#monitor-status-filter').value) : '')),
      api('/api/monitoring/runs?limit=5'),
    ]);
    monitorState.cases = cases.cases || [];
    renderMonitorCases();
    renderMonitorRuns(runs.runs || []);
    const policy = await api('/api/monitoring/policies').catch(() => ({policies: []}));
    const count = (policy.policies || []).length;
    monitorSetStatus(`本机监控 ${count} 只证券；当前队列 ${monitorState.cases.length} 个案件。`);
  } catch (error) {
    monitorSetStatus(error.message + ' 可稍后重试。');
  }
}

function monitorStatusLabel(status) {
  return ({pending_review: '待审核', approved: '已确认', edited: '已修改', dismissed: '已忽略', needs_followup: '需跟进'})[status] || status;
}

function renderMonitorCases() {
  const root = $('#monitor-cases');
  if (!root) return;
  root.replaceChildren();
  const count = $('#monitor-count');
  if (count) count.textContent = monitorState.cases.length;
  if (!monitorState.cases.length) {
    root.append(el('p', 'company-help', '队列为空。点击“扫描自选公司”读取最新申报；首次扫描可能较慢。'));
    return;
  }
  for (const item of monitorState.cases) {
    const card = el('button', 'monitor-case');
    card.type = 'button';
    if (monitorState.selected === item.id) card.classList.add('active');
    const head = el('span', 'monitor-case-head');
    head.append(el('strong', '', item.ticker), el('span', 'small-tag', item.form + ' · ' + item.period_end));
    const priority = el('span', 'monitor-priority ' + item.priority, item.priority === 'high' ? '高' : item.priority === 'medium' ? '中' : '低');
    head.append(priority);
    card.append(head);
    card.append(el('span', 'monitor-case-title', item.title || item.filing_id));
    const meta = el('span', 'monitor-case-meta');
    meta.append(el('span', 'monitor-status ' + item.status, monitorStatusLabel(item.status)));
    meta.append(el('span', '', (item.events || []).map(event => event.label).filter(Boolean).slice(0, 3).join(' · ')));
    card.append(meta);
    card.addEventListener('click', () => openMonitorCase(item.id));
    root.append(card);
  }
}

function renderMonitorRuns(runs) {
  const root = $('#monitor-runs');
  if (!root) return;
  root.replaceChildren();
  if (!runs.length) return;
  const details = el('details', 'monitor-run-details');
  details.append(el('summary', '', '最近扫描运行'));
  for (const run of runs) {
    const payload = run.payload || {};
    details.append(el('p', 'company-help', `${run.id.slice(0, 18)}… · ${run.status} · 新文件 ${payload.new_filings || 0} · 案件 ${payload.cases_created || 0} · 警告 ${(payload.warnings || []).length}`));
  }
  root.append(details);
}

async function scanMonitoring() {
  if (monitorState.busy) return;
  monitorSetBusy(true);
  monitorSetStatus('正在扫描：读取 SEC 索引、准备申报、计算差异并生成简报。首次运行可能需要几分钟…');
  try {
    const result = await api('/api/monitoring/scan', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        bootstrap: true,
        generate_brief: true,
        use_model: !!($('#monitor-use-model') || {}).checked,
      }),
    });
    const warning = (result.warnings || []).length ? ` 警告：${result.warnings.slice(0, 2).join('；')}` : '';
    monitorSetStatus(`扫描完成：新文件 ${result.new_filings}，新案件 ${result.cases_created}，事件 ${result.events_created}，抑制 ${result.suppressed}。${warning}`);
    await loadMonitoring();
  } catch (error) {
    monitorSetStatus(error.message + ' 扫描未完成，已有案件保留。');
  } finally {
    monitorSetBusy(false);
  }
}

async function openMonitorCase(caseId) {
  const request = ++monitorState.detailRequest;
  monitorState.selected = caseId;
  monitorState.detail = null;
  renderMonitorCases();
  const root = $('#monitor-detail');
  root.replaceChildren(el('p', 'company-help', '正在读取案件…'));
  try {
    const detail = await api('/api/monitoring/cases/' + encodeURIComponent(caseId));
    // A slower response for an older selection must never replace the case
    // that is currently displayed.
    if (request !== monitorState.detailRequest || monitorState.selected !== caseId) return;
    monitorState.detail = detail;
    renderMonitorDetail(detail);
  } catch (error) {
    if (request === monitorState.detailRequest) {
      root.replaceChildren(el('p', 'error', error.message));
    }
  }
}

function monitorSection(title, children) {
  const section = el('section', 'monitor-brief-section');
  section.append(el('h3', '', title));
  for (const child of children) section.append(child);
  return section;
}

function renderMonitorDetail(detail) {
  const root = $('#monitor-detail');
  root.replaceChildren();
  const item = detail.case;
  const brief = item.brief;
  if (!brief) {
    root.append(el('p', 'company-help', '该案件没有简报（扫描时关闭了简报生成）。'));
    return;
  }
  const head = el('div', 'monitor-detail-head');
  head.append(el('div', 'eyebrow', item.ticker + ' · ' + item.form + ' · ' + item.filed));
  head.append(el('h2', '', brief.headline));
  const approved = item.approved_version ? ` · 已批准版本 v${item.approved_version}` : '';
  head.append(el('p', 'company-help', `状态：${monitorStatusLabel(item.status)} · 优先级：${item.priority} · 报告期至 ${item.period_end} · 简报版本 v${item.brief_version || 1}${approved} · 生成方式：${brief.generator === 'model' ? '模型润色' : '模板'}`));
  root.append(head);

  const actions = el('div', 'monitor-actions');
  const approve = el('button', 'primary', '确认');
  approve.addEventListener('click', () => monitorReview('approve'));
  const dismiss = el('button', 'secondary', '忽略');
  dismiss.addEventListener('click', () => monitorReview('dismiss'));
  const edit = el('button', 'secondary', '编辑全文');
  edit.addEventListener('click', editMonitorBrief);
  const feedback = el('button', 'secondary', '反馈');
  feedback.addEventListener('click', monitorFeedback);
  const download = el('a', 'secondary', '导出 JSON');
  download.href = '/api/monitoring/cases/' + encodeURIComponent(item.id) + '/package';
  download.target = '_blank';
  download.rel = 'noopener noreferrer';
  actions.append(approve, dismiss, edit, feedback, download);
  root.append(actions);

  const happened = el('div', 'monitor-brief');
  happened.append(monitorSection('发生了什么', brief.what_happened.map(text => el('p', '', text))));
  const numbers = el('div', 'monitor-number-table');
  for (const row of brief.numbers || []) {
    const line = el('div', 'monitor-number-row');
    line.append(el('strong', '', row.label || row.metric));
    line.append(el('span', '', `${row.current_value} ${row.unit || ''}`));
    line.append(el('span', row.change_pct != null && row.change_pct < 0 ? 'negative' : 'positive', row.change_pct != null ? `${row.change_pct > 0 ? '+' : ''}${row.change_pct.toFixed(1)}%` : '—'));
    line.append(el('small', '', `前期 ${row.prior_value || '—'} · ${row.comparison_basis || ''}`));
    numbers.append(line);
  }
  happened.append(monitorSection('最大数字变化', numbers.children.length ? [numbers] : [el('p', 'company-help', '没有可比较的数字。')]));
  const risks = (brief.risks || []).map(row => el('p', '', `${row.section}：相似度 ${Number(row.similarity).toFixed(2)}；${row.excerpt}`));
  happened.append(monitorSection('风险与经营因素', risks.length ? risks : [el('p', 'company-help', '没有达到阈值的段落变化。')]));
  const why = (brief.why_matters || []).map(row => {
    const p = el('p', '', row.text + ' ');
    p.append(el('small', '', '[推断 · 来源 ' + (row.basis_evidence_ids || []).join(', ') + ']'));
    return p;
  });
  happened.append(monitorSection('为什么可能重要（推断）', why));
  const sources = (brief.sources || []).map(row => {
    const p = el('p', 'monitor-source');
    p.append(el('code', '', row.id));
    p.append(el('span', '', ` ${row.section || row.label || ''}：${(row.quote || '').slice(0, 220)}`));
    if (row.source_url) {
      const link = el('a', '', '原文 ↗');
      link.href = row.source_url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      p.append(link);
    }
    return p;
  });
  happened.append(monitorSection('证据', sources.length ? sources : [el('p', 'company-help', '没有可解析的证据。')]));
  happened.append(monitorSection('缺口', (brief.gaps || []).map(text => el('p', '', text))));
  happened.append(monitorSection('下一步检查', (brief.next_checks || []).map(text => el('p', '', text))));
  root.append(happened);

  if ((detail.reviews || []).length) {
    const reviews = el('details', 'monitor-reviews');
    reviews.append(el('summary', '', '审核记录'));
    for (const review of detail.reviews) {
      reviews.append(el('p', 'company-help', `${review.created_at} · ${review.actor} · ${review.action}${review.comment ? ' · ' + review.comment : ''}`));
    }
    root.append(reviews);
  }
}

async function monitorReview(action, extra = {}) {
  const shown = monitorState.detail;
  if (monitorState.busy || !monitorState.selected || !shown || shown.case.id !== monitorState.selected) return;
  const caseId = shown.case.id;
  const briefVersion = shown.case.brief_version || 1;
  monitorSetBusy(true);
  try {
    const body = {
      action,
      actor: 'local',
      reason_code: extra.reason_code || '',
      comment: extra.comment || '',
      brief_version: briefVersion,
    };
    if (extra.brief) body.brief = extra.brief;
    const detail = await api('/api/monitoring/cases/' + encodeURIComponent(caseId) + '/' + action, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (monitorState.selected !== caseId) return;
    monitorState.detail = detail;
    renderMonitorDetail(detail);
    await loadMonitoring();
    monitorSetStatus('审核动作已记录。');
  } catch (error) {
    monitorSetStatus(error.message);
  } finally {
    monitorSetBusy(false);
  }
}

function editMonitorBrief() {
  const detail = monitorState.detail;
  if (!detail || !detail.case.brief) return;
  const root = $('#monitor-detail');
  const oldPanel = root.querySelector('.monitor-edit-panel');
  if (oldPanel) oldPanel.remove();
  const brief = detail.case.brief;
  const panel = el('section', 'monitor-brief-section monitor-edit-panel');
  panel.append(el('h3', '', '编辑简报'));

  const field = (label, value, multiline = true) => {
    const wrap = el('label', 'monitor-edit-field');
    wrap.append(el('span', '', label));
    const input = document.createElement(multiline ? 'textarea' : 'input');
    input.className = 'monitor-edit-input';
    if (!multiline) input.type = 'text';
    input.value = value;
    wrap.append(input);
    return {wrap, input};
  };

  const headline = field('标题', brief.headline || '', false);
  const happened = field('发生了什么（每行一条）', (brief.what_happened || []).join('\n'));
  const why = field('为什么重要（每行一条，保留原顺序的来源）', (brief.why_matters || []).map(item => item.text).join('\n'));
  const numbers = field('数字行（JSON，可编辑 current/prior/unit）', JSON.stringify(brief.numbers || [], null, 2));
  const gaps = field('缺口（每行一条）', (brief.gaps || []).join('\n'));
  const checks = field('下一步检查（每行一条）', (brief.next_checks || []).join('\n'));
  panel.append(headline.wrap, happened.wrap, why.wrap, numbers.wrap, gaps.wrap, checks.wrap);

  const status = el('p', 'company-help');
  const actions = el('div', 'monitor-actions');
  const save = el('button', 'primary', '保存修改');
  const cancel = el('button', 'secondary', '取消');
  actions.append(save, cancel);
  panel.append(actions, status);
  root.append(panel);

  const lines = value => value.split('\n').map(item => item.trim()).filter(Boolean);
  cancel.addEventListener('click', () => panel.remove());
  save.addEventListener('click', async () => {
    let parsedNumbers;
    try {
      parsedNumbers = JSON.parse(numbers.input.value);
      if (!Array.isArray(parsedNumbers)) throw new Error('数字行必须是数组。');
    } catch (error) {
      status.textContent = '数字 JSON 无法解析：' + error.message;
      return;
    }
    const nextWhy = lines(why.input.value).map((text, index) => ({
      text,
      basis_evidence_ids: (brief.why_matters || [])[index]?.basis_evidence_ids || [],
    }));
    const updated = {
      ...brief,
      headline: headline.input.value.trim(),
      what_happened: lines(happened.input.value),
      why_matters: nextWhy,
      numbers: parsedNumbers,
      gaps: lines(gaps.input.value),
      next_checks: lines(checks.input.value),
    };
    await monitorReview('edit', {brief: updated, reason_code: 'full_edit'});
  });
}

async function monitorFeedback() {
  const comment = window.prompt('反馈（例如：数字对不上 / 误报 / 缺少证据）', '');
  if (!comment) return;
  await monitorReview('feedback', {comment, reason_code: 'analyst_note'});
}

async function saveMonitorPolicy() {
  const ticker = ($('#monitor-policy-ticker').value || '').trim().toUpperCase();
  const watch = ($('#monitor-policy-watch').value || '').split(',').map(value => value.trim()).filter(Boolean);
  if (!ticker) {
    $('#monitor-policy-status').textContent = '请输入代码。';
    return;
  }
  try {
    const result = await api('/api/monitoring/policies', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ticker, watch, materiality_floor: $('#monitor-policy-floor').value}),
    });
    $('#monitor-policy-status').textContent = `已保存 ${result.ticker}：${(result.watch || []).join(', ')}；最低重要性 ${result.materiality_floor}。`;
    await loadMonitoring();
  } catch (error) {
    $('#monitor-policy-status').textContent = error.message;
  }
}

$('#monitor-scan').addEventListener('click', scanMonitoring);
$('#monitor-refresh').addEventListener('click', loadMonitoring);
$('#monitor-status-filter').addEventListener('change', loadMonitoring);
$('#monitor-policy-save').addEventListener('click', saveMonitorPolicy);
