const $ = s => document.querySelector(s);
const state = {catalog: [], runs: [], result: null, health: {}, connection: null, packageSupport: false, runtimeBlocked: false};
const labels = {answered: '回答完成', partial: '部分回答', abstained: '证据不足', error: '研究中止'};
const statusReasonLabels = {complete: '', budget_expired: '原因：时间预算耗尽', cancelled: '原因：研究已取消', source_or_model_error: '原因：模型或数据源请求失败', review_incomplete: '原因：独立核验未完成', evidence_gap: '原因：资料仍有缺口', no_supported_evidence: '原因：当前证据不足', reused_evidence: '原因：沿用上一轮证据'};
function resultStatusLabel(result){const base=labels[result.status]||result.status;const key=result.status_reason||((result.trace||[]).some(t=>t.stage==='deadline')?'budget_expired':(result.trace||[]).some(t=>t.stage==='cancelled')?'cancelled':'');const reason=statusReasonLabels[key];return reason&&result.status!=='answered'?base+' · '+reason:base;}
const stageNames = {reason: 'Agent 选择下一步', tool: '研究工具', verify: '证据核验', semantic_review: '独立证据与分析审查', semantic_review_cached: '复用本轮未变化内容的审查', retained_verified_partial: '保留先前有效的部分分析', semantic_review_error: '该批审查未完成，相关判断已扣留', model_error: '模型请求中止', tool_skipped: '提交后已跳过', company_lookup: '识别公司', company_preparation: '准备年报', quarterly_preparation: '检查最新季报', annual_refresh: '检查最新年报', updates_preparation: '检查最新公司公告', external_updates_preparation: '读取电话会、新闻或交易所公告', ttm_preparation: '准备 TTM 财务', stock_source_preparation: '准备经营与风险原文', basic_stock_brief: '核验基础股票简报', historical_valuation: '重建历史市盈率',module_budget:'资料型模块预算',cancelled:'研究已取消',deadline:'本轮时间预算用完',model_stopped:'停止后续模型调用',execution_events:'执行阶段与连接记录',evidence:'读取研究资料',step_stopped:'步骤因停止而结束',step_error:'步骤未完成',refresh_incomplete:'披露刷新暂未完成',sec_deferred:'同一失败来源短暂延后'};
function el(tag, cls, text) { const n = document.createElement(tag); if(cls) n.className = cls; if(text != null) n.textContent = text; return n; }
function requireMatchingRuntime(health){const expected=document.querySelector('meta[name="application-version"]')?.getAttribute?.('content');if(health.runtime?.needs_restart||(expected&&health.version!==expected)){state.runtimeBlocked=true;const badge=$('.version'),status=$('#provider-status');if(badge)badge.textContent='页面 '+(expected||'未知')+' · 服务 '+(health.version||'未知');if(status)status.textContent='版本不匹配，请重启服务';for(const id of ['#research','#company-report','#find-company']){const button=$(id);if(button)button.disabled=true;}throw new Error('当前页面版本 '+(expected||'未知')+' 与运行服务 '+(health.version||'未知')+' 不匹配。请重新启动服务后刷新页面；尚未开始研究。');}}
async function api(path, options, rawText=false) { if(typeof FilingI18n!=='undefined')options=FilingI18n.request(path,options); if(state.runtimeBlocked&&!['/api/health','/api/connection'].includes(path))throw new Error('页面与运行服务版本不匹配，请重启服务并刷新页面。');const r = await fetch(path, options); if(rawText&&r.ok)return r.text(); const data = await r.json(); if(!r.ok) throw Object.assign(new Error(typeof data.detail === 'string' ? data.detail : '请求未完成，请检查输入。'),{status:r.status,marketStatus:data.market_status}); return data; }
function changeView(name) { document.querySelectorAll('.view').forEach(v => v.classList.add('hidden')); $('#'+name+'-view').classList.remove('hidden'); document.querySelectorAll('.nav').forEach(n => n.classList.toggle('active',n.dataset.view===name)); $('#breadcrumb').textContent='工作空间 / '+({desk:'研究工作台',company:'公司研究',watchlist:'自选观察列表',comparison:'公司对比',trend:'财务趋势',library:'财报资料库',history:'研究记录',connect:'接入其他助手'})[name]; if(name==='company'&&typeof loadCompanyWorkspace==='function'&&(typeof companyWorkspaceNeedsLoad!=='function'||companyWorkspaceNeedsLoad())) loadCompanyWorkspace(); if(name==='history') loadHistory(); if(name==='watchlist') loadWatchlist(); window.scrollTo(0,0); }
document.querySelectorAll('.nav').forEach(n => n.addEventListener('click',()=>changeView(n.dataset.view)));
document.querySelectorAll('.starters button').forEach(b=>b.addEventListener('click',()=>{clearCompanySelection();$('#question').value=b.dataset.q;$('#ticker').value=b.dataset.ticker;$('#ticker').dispatchEvent(new Event('change'));$('#question').focus();updateQuestionPlan();}));
$('#mode').addEventListener('change',()=>$('#mode-note').textContent=$('#mode').value==='agent'?'Agent 按问题选择工具，核验数字与原文，再形成回答。':'不调用模型；行情与最新披露查询会按需联网检查，数字由已保存的原始证据和工具计算生成。');
$('#as-of').value = new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,10);
state.initialAsOf=$('#as-of').value;
$('#as-of').addEventListener('change',loadCatalog);
function noticeDateLabel(form){return form?.startsWith('6-K')?'报告标注日期':'事件日期';}
async function loadCatalog(){ const data=await api('/api/catalog?as_of='+encodeURIComponent($('#as-of').value)); state.catalog=data.filings; const counts=[new Set(data.filings.map(f=>f.ticker)).size,data.filings.length,data.fact_count,data.passage_count]; $('#stats').replaceChildren(...counts.map((x,i)=>{const d=el('div');d.append(el('strong','',x.toLocaleString()),el('span','',['公司','公开申报','财务事实','原文段落'][i]));return d;})); $('#library').replaceChildren(...data.filings.map(f=>{const card=el('article','filing-card');card.append(el('div','ticker',f.ticker),el('h3','',f.company),el('p','',f.form+(/^[68]-K/.test(f.form)?' · '+noticeDateLabel(f.form)+' ':' · 报告截至 ')+f.period_end+'\n公开日期 '+f.filed));const link=el('a','','查看 SEC 原始申报 ↗');link.href=f.source_url;link.target='_blank';link.rel='noopener noreferrer';card.append(link);return card;})); }
function evidencePeriod(e){if(e.kind==='valuation')return '供应商 TTM 期末标签 '+e.snapshot.period_end_label;if(e.operation==='provider_ttm_pe')return evidencePeriod(e.inputs[1]);if(['ratio','net_cash_capex'].includes(e.operation)&&e.inputs?.length===2&&e.inputs.every(x=>x.operation==='trailing_sum')){const periods=e.inputs.map(evidencePeriod);return periods[0]===periods[1]?periods[0]:'TTM 期间不一致';}if(e.operation==='trailing_sum'){const fact=x=>x.kind==='fact'?x:fact(x.inputs[0]);const last=fact(e.inputs.length===1?e.inputs[0]:e.inputs[1]);const first=fact(e.inputs.length===1?e.inputs[0]:e.inputs[2]);const start=e.inputs.length===1?first.start:new Date(Date.parse(first.end)+86400000).toISOString().slice(0,10);return 'TTM '+start+' 至 '+last.end;}const findFact=x=>x.kind==='fact'?x:x.kind==='calculation'?x.inputs.map(findFact).find(Boolean):null;const fact=findFact(e);if(fact?.filing?.form?.startsWith('10-Q')){const days=(Date.parse(fact.end)-Date.parse(fact.start))/86400000;return (days<=105?'单季度':'年内累计')+' '+fact.start+' 至 '+fact.end;}return 'FY'+e.fiscal_year;}
function cashCapexNote(e){if(e.metric==='ads_diluted_eps'||(e.operation==='annual_pe'&&e.inputs?.[1]?.metric==='ads_diluted_eps'))return '美元 ADS 稀释 EPS 是 SEC 年报单独披露的 ADS 股份类别值，采用该年报固定汇率便利换算。未从已四舍五入的普通股 EPS 推算；不是当前汇率重算、TTM 或预测估值。';if(e.metric==='cash_capex'||(e.operation==='trailing_sum'&&e.inputs?.[0]?.metric==='cash_capex'))return '现金 PP&E 购置毛额，尚未扣除出售及激励收款；不同于净资本开支，也不包含全部资本投入。';if(e.operation==='net_cash_capex')return '现金 PP&E 购置净额＝购置毛额−出售及激励收款。请分别查看两项来源；未包含非现金取得资产、融资租赁本金、收购及其他投资。';return ''; }
function claimPeriodLabels(claim,evidence){
  const labels=new Set();
  const day=value=>typeof value==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(value)&&Number.isFinite(Date.parse(value))&&new Date(value).toISOString().slice(0,10)===value?value:null;
  const span=w=>(Date.parse(w.end)-Date.parse(w.start))/86400000;
  const next=value=>new Date(Date.parse(value)+86400000).toISOString().slice(0,10);
  const window=e=>{
    if(e?.kind==='fact')return day(e.start)&&day(e.end)&&e.start<=e.end?{start:e.start,end:e.end}:null;
    if(e?.kind!=='calculation'||!['ratio','free_cash_flow','net_cash_capex'].includes(e.operation)||!e.inputs?.length)return null;
    const windows=e.inputs.map(window);
    return windows.every(w=>w&&w.start===windows[0]?.start&&w.end===windows[0]?.end)?windows[0]:null;
  };
  const trailing=e=>{
    const inputs=e.inputs||[],windows=inputs.map(window);
    if(windows.some(w=>!w)||![1,3].includes(windows.length))return null;
    const [annual,current,previous]=windows;
    if(span(annual)<330||span(annual)>400)return null;
    if(windows.length===1)return annual;
    if(current.start!==next(annual.end)||previous.start!==annual.start||previous.end>=annual.end||current.end<=annual.end||Math.abs(span(current)-span(previous))>7)return null;
    const derived={start:next(previous.end),end:current.end};
    return span(derived)>=330&&span(derived)<=400?derived:null;
  };
  const add=(ticker,label)=>labels.add((ticker?ticker+' · ':'')+label);
  const visit=(e,ticker)=>{
    if(!e)return;
    ticker=e.ticker||ticker;
    if(e.kind==='fact'){
      const end=day(e.end),start=day(e.start);
      if(!end||e.start!=null&&!start||start&&start>end){add(ticker,'财务期间资料不完整');return;}
      if(!start){add(ticker,'时点 '+end);return;}
      const days=span({start,end}),form=e.filing?.form;
      const fy=Number.isInteger(e.fiscal_year)?'FY'+e.fiscal_year:'年度';
      const label=['10-K','10-K/A','20-F','20-F/A'].includes(form)&&days>=330&&days<=400?fy:['10-Q','10-Q/A'].includes(form)&&days>=70&&days<=105?'单季度':['10-Q','10-Q/A'].includes(form)&&((days>=150&&days<=210)||(days>=240&&days<=300))?'年内累计':'财务期间';
      add(ticker,label+' '+start+' 至 '+end);return;
    }
    if(e.kind==='valuation'){add(ticker,day(e.snapshot?.period_end_label)?'供应商 TTM 期末标签 '+e.snapshot.period_end_label:'供应商 TTM 期间标签未注明');return;}
    if(e.kind==='market'){
      const s=e.snapshot;
      if(!Number.isFinite(s?.quote_at)||!s?.exchange_timezone){add(ticker,'报价日期未注明');return;}
      try{
        const parts=new Intl.DateTimeFormat('en-US',{timeZone:s.exchange_timezone,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date(s.quote_at*1000));
        const part=type=>parts.find(p=>p.type===type).value;
        add(ticker,(s.quote_type==='historical_close'?'历史收盘':'报价')+' '+part('year')+'-'+part('month')+'-'+part('day')+'（交易所日）');
      }catch{add(ticker,'报价日期未注明');}return;
    }
    if(e.kind!=='calculation')return;
    if(e.operation==='trailing_sum'){
      const w=trailing(e);add(ticker,w?'TTM '+w.start+' 至 '+w.end:'TTM 期间资料不完整');return;
    }
    if(['ratio','net_cash_capex'].includes(e.operation)&&e.inputs?.length===2&&e.inputs.every(x=>x.operation==='trailing_sum')){
      const windows=e.inputs.map(trailing);
      if(windows.every(Boolean)&&(windows[0].start!==windows[1].start||windows[0].end!==windows[1].end))add(ticker,'TTM 期间不一致');
    }
    if(e.inputs?.length)e.inputs.forEach(x=>visit(x,ticker));
    else if(Number.isInteger(e.fiscal_year))add(ticker,'财年标签 FY'+e.fiscal_year+'（未保留日期范围）');
  };
  claim.evidence_ids.forEach(id=>visit(evidence.get(id)));
  return [...labels];
}
function renderClaimPeriods(row,claim,evidence){
  const labels=claimPeriodLabels(claim,evidence);
  if(!labels.length)return;
  const periods=el('div','claim-periods');periods.setAttribute('aria-label','所引数据期间');
  periods.append(el('span','claim-period-caption','所引数据期间'));
  labels.forEach(label=>periods.append(el('span','claim-period',label)));
  row.append(periods);
}
// Evidence drawers should answer a human question first: what does this item
// mean, which period does it cover, and where can I verify it?  Keep the raw
// locator and payload available, but place them behind a technical disclosure.
// Source text itself is never translated or rewritten.
function sourceUi(zh,en){return typeof FilingI18n!=='undefined'&&FilingI18n.language?.()==='en'?en:zh;}
function openEvidenceDrawer(){
  const drawer=$('#drawer'),doc=globalThis.document;
  if(drawer.classList.contains('hidden')){
    openEvidenceDrawer.opener=doc?.activeElement;
    openEvidenceDrawer.background=[...(doc?.querySelectorAll?.('main,.sidebar')||[])].map(node=>({node,inert:node.inert}));
    openEvidenceDrawer.background.forEach(({node})=>{node.inert=true;});
    if(doc?.body?.style){openEvidenceDrawer.overflow=doc.body.style.overflow;doc.body.style.overflow='hidden';}
  }
  drawer.classList.remove('hidden');$('#drawer-overlay').classList.remove('hidden');
  if(typeof drawer.contains==='function'&&!drawer.contains(doc?.activeElement))$('#close-drawer')?.focus?.({preventScroll:true});
}
function evidenceDrawerKeydown(event){
  const drawer=$('#drawer');if(drawer.classList.contains('hidden'))return;
  if(event.key==='Escape'){event.preventDefault();closeDrawer();return;}
  if(event.key!=='Tab')return;
  const focusable=[...drawer.querySelectorAll('button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled]),summary,[tabindex]:not([tabindex="-1"])')].filter(node=>{
    if(!node.getClientRects().length)return false;
    for(let ancestor=node.parentElement;ancestor&&ancestor!==drawer;ancestor=ancestor.parentElement){if(ancestor.tagName==='DETAILS'&&!ancestor.open&&node!==ancestor.querySelector(':scope > summary'))return false;}
    return true;
  });
  const first=focusable[0]||$('#close-drawer'),last=focusable.at(-1)||first,active=document.activeElement;
  if(!drawer.contains(active)||event.shiftKey&&active===first||!event.shiftKey&&active===last){event.preventDefault();(event.shiftKey?last:first).focus({preventScroll:true});}
}
function rememberSourceEvidence(e){globalThis.__filingcopilotSourceEvidence=e;}
function sourceEscape(value){return String(value??'').replace(/[.*+?^${}()|[\]\\]/g,'\\$&');}
function sourceHighlight(parent,text,needles=[]){
  const value=String(text??'');
  if(!value.trim()){parent.append(el('p','verification-note',sourceUi('原始来源没有返回可读摘录；请打开对应申报查看上下文。','The source did not return a readable excerpt; open the filing for the surrounding context.')));return;}
  const terms=[...new Set(needles.map(x=>String(x??'').trim()).filter(x=>x.length>=2))].sort((a,b)=>b.length-a.length);
  if(!terms.length){parent.append(el('span','',value));return;}
  const pattern=new RegExp('('+terms.map(term=>/^[+-]?\d[\d,]*(?:\.\d+)?$/.test(term)?'(?<![A-Za-z0-9_.,+−-])'+sourceEscape(term)+'(?![A-Za-z0-9_]|[.,]\\d)':sourceEscape(term)).join('|')+')','gi');let last=0,match;
  while((match=pattern.exec(value))){
    if(match.index>last)parent.append(el('span','',value.slice(last,match.index)));
    parent.append(el('mark','source-highlight',match[0]));last=match.index+match[0].length;
  }
  if(last<value.length)parent.append(el('span','',value.slice(last)));
}
function sourceExcerpt(root,text,needles=[]){
  const box=el('section','source-readable-excerpt');
  box.append(el('h3','',sourceUi('与本条数据相关的原文','Relevant source text')));
  const quote=el('blockquote','source-text');sourceHighlight(quote,text,needles);box.append(quote);
  root.append(box);
}
function sourceTechnical(root,rows=[],links=[]){
  const details=el('details','source-technical');details.append(el('summary','',sourceUi('查看技术细节','View technical details')));
  rows.filter(([,value])=>value!=null&&String(value)!=='').forEach(([label,value])=>{const row=el('p','');row.append(el('strong','',label),el('span','',String(value)));details.append(row);});
  links.filter(([url])=>url).forEach(([url,label])=>{const link=el('a','cite',label);link.href=url;link.target='_blank';link.rel='noopener noreferrer';details.append(link);});
  root.append(details);
}
function refreshSourceLanguage(){
  const evidence=globalThis.__filingcopilotSourceEvidence;
  if(evidence?.kind==='market'&&typeof showMarketEvidence==='function')showMarketEvidence(evidence);
  else if(evidence)showEvidence(evidence);
}
function sourceFilingSummary(e){
  const filing=e.filing||{};
  if(!filing.form)return null;
  const box=el('section','source-readable-summary');
  box.append(el('h3','',sourceUi('来源与期间','Source and period')));
  const period=e.start&&e.end?e.start+' → '+e.end:(e.end||e.period_end||'');
  const rows=[
    [sourceUi('申报类型','Filing'),filing.form],
    [sourceUi('公开日期','Published'),filing.filed],
    [sourceUi('报告期间','Reporting period'),period],
    [sourceUi('数据类型','Evidence type'),['fact','statement_fact'].includes(e.kind)?sourceUi('财务事实（结构化数据）','Financial fact (structured data)'):sourceUi('原文摘录','Source excerpt')]
  ];
  const grid=el('div','source-meta-grid');rows.filter(([,value])=>value).forEach(([label,value])=>{const item=el('div');item.append(el('span','',label),el('strong','',value));grid.append(item);});box.append(grid);return box;
}
function sourceReadableFact(root,e){
  const summary=sourceFilingSummary(e);if(summary)root.append(summary);
  const amount=sourceValue(e),explanation=el('p','source-readable-lead');
  explanation.append(el('strong','',e.label||e.metric||sourceUi('该财务指标','This financial metric')));
  explanation.append(el('span','',sourceUi(' 的申报数值为 ',' was reported as ')));
  explanation.append(el('strong','',displayScaledAmount(e)));
  if(e.start||e.end)explanation.append(el('span','',sourceUi('，覆盖上述报告期间。',' for the reporting period shown above.')));
  root.append(explanation,amount);
  if(e.text)sourceExcerpt(root,e.text,[e.label,e.metric,e.value,displayDecimal(e.value)]);
  else root.append(el('p','verification-note',sourceUi('该项目来自 SEC XBRL 结构化事实；下方技术细节保留原始指标定位。对应申报链接可查看财务表格上下文。','This item comes from a structured SEC XBRL fact. The technical locator is retained below; open the filing link for the surrounding financial statement context.')));
}
function sourceReadableNarrative(root,e){
  const summary=sourceFilingSummary(e);if(summary)root.append(summary);
  if(e.text)sourceExcerpt(root,e.text,[e.title,e.section,e.label,e.ticker]);
  else root.append(el('p','verification-note',sourceUi('没有可读原文摘录。','No readable source excerpt was returned.')));
}
function showStatementEvidence(e){
  rememberSourceEvidence(e);
  const root=$('#drawer-content');root.replaceChildren(el('h2','',e.label||e.concept),el('p','',e.ticker+' · '+e.filing.form+' · 公开于 '+e.filing.filed));sourceReadableFact(root,e);root.append(el('h3','','实际期间'),el('p','',(e.start?e.start+' 至 ':'')+e.end));
  const dimensions=Object.entries(e.dimensions||{});
  if(dimensions.length){root.append(el('h3','','分部与维度'));dimensions.forEach(([axis,member])=>root.append(el('p','',axis+'：'+(e.dimension_labels?.[axis]||e.dimension_labels?.[member]||member))));}
  sourceTechnical(root,[['XBRL concept',e.concept],['Source locator',e.locator],['Source URL',e.source_url]],[[e.source_url,'打开结构化数据（技术） ↗']]);
  if(e.filing.source_url){const link=el('a','cite','查看对应申报 ↗');link.href=e.filing.source_url;link.target='_blank';link.rel='noopener noreferrer';root.append(link);}
  openEvidenceDrawer();
}
function showEvidence(e){
  rememberSourceEvidence(e);
  if(e.kind==='statement_fact'){showStatementEvidence(e);return;}
  if(e.kind==='market'){showMarketEvidence(e);return;}
  if(e.kind==='valuation'){showValuationEvidence(e);return;}
  const root=$('#drawer-content');root.replaceChildren();
  if(e.kind==='external_update'){
    root.append(el('h2','',e.title||sourceUi('公司更新','Company update')),el('p','',e.ticker+' · '+e.source_name+' · '+sourceUi('可用 ','available ')+(e.available_at||'').replace('T',' ').replace('Z',' UTC')));
    root.append(el('p','verification-note',e.publication_basis==='first_observed'?sourceUi('来源未提供公开时间；这里只证明首次取得时间。','The source provides no public timestamp; this only proves when it was first observed locally.'):sourceUi('公开时间来自来源；外部资料不等同 SEC 官方公告。','The publication time comes from the source; external material is not an SEC filing.')));
    sourceExcerpt(root,e.text,[e.title,e.source_name,e.ticker]);
    const a=el('a','cite',sourceUi('打开外部原文 ↗','Open external source ↗'));a.href=e.source_url;a.target='_blank';a.rel='noopener noreferrer';root.append(a);
    sourceTechnical(root,[['Source URL',e.source_url],['Content SHA256',e.content_sha256]]);
    openEvidenceDrawer();return;
  }
  root.append(el('h2','',e.kind==='calculation'?e.label:e.kind==='fact'?e.label:e.section||sourceUi('来源证据','Source evidence')));
  const basisNote=cashCapexNote(e);if(basisNote)root.append(el('p','verification-note',basisNote));
  if(e.kind==='calculation'){
    root.append(el('p','',e.ticker+' · '+evidencePeriod(e)),sourceValue(e),el('h3','',sourceUi('人类可读的计算','Readable calculation')));
    const formula=e.operation==='annual_pe'&&e.inputs?.[1]?.metric==='ads_diluted_eps'?sourceUi('当前美元 ADS 报价 ÷ 年报便利换算美元 ADS 稀释 EPS','Current USD ADS quote ÷ filing convenience-converted diluted USD ADS EPS'):e.operation==='provider_ttm_pe'?sourceUi('当前供应商报价 ÷ 供应商 TTM 稀释每股收益','Current supplier quote ÷ supplier TTM diluted EPS'):e.operation==='trailing_sum'?(e.inputs.length===1?sourceUi('最近已披露完整财年的流量（截至所示报告期末）','Most recently disclosed complete fiscal-year flow through the reported period end'):sourceUi('最近完整年度 + 本年累计 − 上年同期累计','Latest complete year + current year-to-date − prior-year comparable period')):e.formula;
    root.append(el('p','source-readable-lead',formula));
    const inputs=el('div','source-input-list');(e.inputs||[]).forEach(x=>{const row=el('div','source-input-row');row.append(el('strong','',x.kind==='market'?sourceUi('行情价格','Market price'):x.kind==='valuation'?sourceUi('供应商 TTM EPS','Supplier TTM EPS'):(x.label||sourceUi('输入事实','Input fact'))),el('span','',x.kind==='market'&&x.snapshot?displayDecimal(x.snapshot.quote_value,2,2)+' '+x.snapshot.currency:x.kind==='valuation'&&x.snapshot?displayDecimal(x.snapshot.value)+' '+x.snapshot.currency:displayEvidenceAmount(x)));const b=el('button','cite',sourceUi('查看这项输入','View input'));b.addEventListener('click',()=>showEvidence(x));row.append(b);inputs.append(row);});root.append(inputs);
    sourceTechnical(root,[['Operation',e.operation],['Formula',e.formula],['Evidence ID',e.id]]);
  }else{
    root.append(el('p','',e.ticker+' · '+(e.filing?.form||'')+' · '+sourceUi('公开于 ','published ')+(e.filing?.filed||'')));
    if(e.filing?.form?.startsWith('6-K'))root.append(el('p','verification-note',sourceUi('SEC 报告标注日期 '+e.filing.period_end+'；不作为事件发生日。','SEC report date '+e.filing.period_end+'; this is not treated as the event date.')));
    if(e.kind==='fact'){sourceReadableFact(root,e);root.append(el('h3','',sourceUi('原始指标定位','Original metric location')));sourceTechnical(root,[['XBRL concept',e.concept],['Source locator',e.locator],['Source URL',e.source_url]],[[e.source_url,sourceUi('打开结构化数据（技术） ↗','Open structured data (technical) ↗')]]);}
    else{sourceReadableNarrative(root,e);sourceTechnical(root,[['Source locator',e.locator],['Source URL',e.source_url]]);if(e.source_url){const link=el('a','cite',sourceUi('打开原始证据 ↗','Open source evidence ↗'));link.href=e.source_url;link.target='_blank';link.rel='noopener noreferrer';root.append(link);}}
    if(e.kind==='fact'&&e.filing?.source_url){const f=el('a','cite',sourceUi('查看对应申报 ↗','Open corresponding filing ↗'));f.href=e.filing.source_url;f.target='_blank';f.rel='noopener noreferrer';root.append(f);addVersionButton(root,e);}
  }
  openEvidenceDrawer();
}
function closeDrawer(){
  const wasOpen=!$('#drawer').classList.contains('hidden');$('#drawer').classList.add('hidden');$('#drawer-overlay').classList.add('hidden');
  if(!wasOpen)return;
  (openEvidenceDrawer.background||[]).forEach(({node,inert})=>{node.inert=inert;});openEvidenceDrawer.background=[];
  if(document.body?.style)document.body.style.overflow=openEvidenceDrawer.overflow||'';
  const opener=openEvidenceDrawer.opener,live=opener?.isConnected?opener:opener?.id?document.getElementById(opener.id):null;
  openEvidenceDrawer.opener=null;if(live&&!live.disabled)live.focus({preventScroll:true});
}
$('#close-drawer').addEventListener('click',closeDrawer);$('#drawer-overlay').addEventListener('click',closeDrawer);document.addEventListener('keydown',evidenceDrawerKeydown);
function resetResearchContext(){if(typeof renderResearchPreview==='function')renderResearchPreview({});state.result=null;$('#result').classList.add('hidden');$('#error').classList.add('hidden');closeDrawer();updateQuestionPlan();}
$('#ticker').addEventListener('change',resetResearchContext);$('#as-of').addEventListener('change',resetResearchContext);
function addReferencedFigures(figures,result,evidence){
  const rendered=new Set(result.answer.filter(c=>['fact','calculation'].includes(c.kind)).flatMap(c=>c.evidence_ids));
  const prepared=(result.trace||[]).filter(t=>t.stage==='ttm_preparation').flatMap(t=>t.result?.evidence?.map(e=>e.id)||[]);
  const ids=[...new Set([...result.answer.flatMap(c=>c.evidence_ids),...prepared])];
  const items=ids.filter(id=>!rendered.has(id)).map(id=>evidence.get(id)).filter(e=>e&&['fact','calculation'].includes(e.kind));
  items.forEach(e=>{
    const row=el('div','answer-item');
    const value=displayEvidenceAmount(e);
    row.append(el('div','',e.ticker+' '+evidencePeriod(e)+' '+e.label+'：'+value));
    const button=el('button','cite',e.kind==='calculation'?'查看公式与输入':'查看财务出处');
    button.addEventListener('click',()=>showEvidence(e));
    row.append(button,el('p','verification-note',e.kind==='calculation'?'结果由代码计算':'数值来自申报事实'));
    figures.append(row);
  });
}
function renderCapexBasis(root,result){
  const definition=result.trace.map(t=>t.result?.definition_note).filter(Boolean).at(-1);
  if(definition)root.append(el('p','verification-note',definition));
  const basis=result.trace.map(t=>t.result?.capex_basis).filter(Boolean).at(-1);
  if(!basis)return;
  const map=new Map();const retain=e=>{if(e?.id&&!map.has(e.id))map.set(e.id,e);e?.inputs?.forEach(retain);};result.evidence.forEach(retain);
  const detail=el('details','stock-figures');detail.append(el('summary','','现金 PP&E 购置：毛额与净额'));
  for(const [key,label] of [['gross_id','购置毛额'],['receipts_id','出售及激励收款'],['net_id','购置净额'],['net_growth_id','净额同比']]){
    const e=map.get(basis[key]);if(!e)continue;
    const row=el('div','answer-item');row.append(el('div','',label+' · '+evidencePeriod(e)+'：'+displayEvidenceAmount(e)));
    const b=el('button','cite','查看'+label+'出处');b.addEventListener('click',()=>showEvidence(e));row.append(b);detail.append(row);
  }
  const missingNet=basis.net_id&&!map.has(basis.net_id);
  detail.append(el('p','verification-note',missingNet?'本研究包未保留购置净额计算，可重新查询毛额和净额；毛额不能代替净额。':basis.net_id?'购置净额＝购置毛额−出售及激励收款；毛额与净额采用各自同比。均不代表全部资本投入，未包含非现金取得资产、融资租赁本金、收购及其他投资。':'仅有购置毛额，未取得完整收款调整；净额未计算，不把缺值当零。'));
  if(missingNet){const query=el('button','secondary','查询毛额与净额');query.addEventListener('click',()=>{$('#question').value='最新自由现金流和现金资本开支毛额、净额及同比是多少？';updateQuestionPlan();$('#question').focus();});detail.append(query);}
  root.append(detail);
}
function displayGap(text){const labels={cashflow_overview:'现金流资料',previous_id:'上期数据',current_id:'本期数据',growth_id:'同比数据',input_ids:'计算输入',evidence_id:'证据编号'};return text.replace(/\b(cashflow_overview|previous_id|current_id|growth_id|input_ids|evidence_id)\b/g,key=>labels[key]).replace(/计算\s*ID/g,'计算结果');}
function renderCompanyUpdates(root,result,compact=false){
  const overview=result.trace.find(t=>t.tool==='get_company_updates')?.result;
  if(!overview)return;
  if(compact){const disclosure=el('details','stock-figures');disclosure.append(el('summary','','查看近期公司公告与覆盖范围（'+overview.events.length+' 次申报）'));root.append(disclosure);root=disclosure;}
  root.append(el('p','verification-note',overview.scope));
  const list=el(compact?'div':'details','stock-figures');if(!compact)list.append(el('summary','','查看本次公告清单（'+overview.events.length+' 次申报）'));
  overview.events.forEach(event=>{
    const row=el('div','answer-item');row.append(el('strong','','公开于 '+event.filed+' · '+(event.date_basis==='sec_report_date'?'报告标注日期 '+event.report_date:'事件日期 '+event.event_date)));
    event.documents.forEach(f=>{const a=el('a','cite',f.form+' 原文 ↗');a.href=f.source_url;a.target='_blank';a.rel='noopener noreferrer';row.append(a);});
    if(event.excerpts?.length){const original=el('details','notice-original');original.append(el('summary','','查看本次摘要依据的原文摘录'));event.excerpts.forEach(p=>original.append(el('blockquote','source-text',p.text)));row.append(original);}
    if(!event.evidence_ids.length)row.append(el('p','verification-note','原文未提取到可读段落；可直接查看原始文件。'));
    list.append(row);
  });
  const external=overview.sections||{};
  for(const [key,label] of [['transcripts','业绩电话会逐字稿'],['news','独立新闻'],['hkex_en','HKEXnews 英文公告'],['hkex_zh','HKEXnews 中文公告'],['cninfo','巨潮资讯中文公告']]){
    const items=external[key]||[];
    if(!items.length)continue;
    const box=el('details','stock-figures');box.open=!!compact;
    box.append(el('summary','',label+'（'+items.length+' 条；独立来源）'));
    box.append(el('p','verification-note',key.startsWith('hkex')?'HKEXnews 官方交易所披露；中英文版本分开保存，不重建年报财务指标。':key==='cninfo'?'巨潮资讯官方中文公告；年报表格指标将在后续管线处理。':overview.external_scope||'外部资料按公开时间筛选；不等同 SEC 官方公告或已核验财务事实。'));
    items.forEach(item=>{
      const row=el('div','answer-item');
      const available=(item.available_at||'').replace('T',' ').replace('+00:00',' UTC');
      row.append(el('strong','',item.source_name+' · 可用 '+available));
      const link=el('a','cite','打开来源 ↗');link.href=item.source_url;link.target='_blank';link.rel='noopener noreferrer';row.append(link);
      row.append(el('h4','',item.title),el('p','source-text',item.text));
      row.append(el('p','verification-note',item.publication_basis==='first_observed'?'来源未提供公开时间；只证明本机首次取得时间，不回填事件日。':'公开时间来自来源；逐字稿/新闻与官方公告分区。'));
      box.append(row);
    });
    root.append(box);
  }
  root.append(list,el('p','verification-note',overview.next_step));
  if(state.health?.agent_ready&&result.mode==='updates'&&overview.found){const b=el('button','secondary notice-summary','生成中文摘要');b.disabled=!!state.researchRunning;b.addEventListener('click',()=>{clearCompanySelection();$('#ticker').value=overview.ticker;$('#ticker').dispatchEvent(new Event('change'));$('#as-of').value=result.as_of;$('#as-of').dispatchEvent(new Event('change'));$('#mode').value='agent';$('#mode').dispatchEvent(new Event('change'));$('#question').value=typeof FilingI18n==='undefined'?'近期公司公告中文摘要':FilingI18n.translate('近期公司公告中文摘要');updateQuestionPlan();$('#question').focus();});root.append(b,el('p','verification-note','摘要逐条引用官方公告并经过模型审查；先填入问题，点击开始研究后生成。'));}
}
function renderSourceRefresh(root,result){
  const incomplete=result.trace.filter(t=>['annual_refresh','quarterly_preparation','updates_preparation','external_updates_preparation'].includes(t.stage)&&t.result&&(t.result.checked===false||t.result.warning||t.result.warnings?.length));
  if(!incomplete.length)return;
  const box=el('section','source-refresh');
  box.append(el('strong','','披露刷新未完成'),el('p','','研究使用已获取资料；来源新鲜度尚未确认。可稍后重新运行本问题刷新资料。'));
  const details=el('details');details.append(el('summary','','查看刷新原因与已有披露日期'));
  incomplete.forEach(t=>{
    const row=el('div','refresh-row');row.append(el('strong','',stageNames[t.stage]));
    const warnings=[...(t.result.warnings||[]),t.result.warning];warnings.filter(Boolean).forEach(w=>row.append(el('p','',w)));
    const filings=t.result.filings||[];
    if(filings.length){const recent=[...filings].sort((a,b)=>b.filed.localeCompare(a.filed))[0];row.append(el('p','verification-note','已有披露公开于 '+recent.filed+' · 报告期末 '+recent.period_end));}
    details.append(row);
  });box.append(details);root.append(box);
}
function renderDeadlineActions(root,result){
  const deadline=result.trace.find(t=>t.stage==='deadline');
  const tickers=[...new Set(result.evidence.map(e=>e.ticker).filter(Boolean))];
  const ticker=deadline.focus_ticker||(tickers.length===1?tickers[0]:null);
  const box=el('section','research-deadline');
  const count=(result.answer||[]).length;
  box.append(el('strong','','本轮时间已用完'),el('p','',`本轮预算 ${deadline.time_budget_seconds||state.health.research_time_budget_seconds||420} 秒。${count?`保留了 ${count} 项通过核验的结果；未完成审查的判断不作结论。`:'尚未形成通过核验的研究结论；已获取资料仍保存在本地。'}`));
  const stoppedStep=result.trace.find(t=>t.stage==='execution_events')?.events?.slice().reverse().find(e=>e.source_stage);
  if(stoppedStep)box.append(el('p','','停止阶段：'+(stageNames[stoppedStep.source_stage]||'资料准备')+'。'));
  if(ticker){
    box.append(el('p','','选择一个具体问题继续查询；资料沿用本地缓存。点击后可编辑问题，再开始研究。'));
    const actions=el('div','deadline-actions');
    const pending=el('p','','正在匹配这只证券可继续查询的问题。');box.append(pending,actions);
    api('/api/capabilities?'+new URLSearchParams({ticker})).then(capabilities=>{
      if(box.isConnected===false)return;
      const prices=[['查询所选日期价格',`${ticker} 所选日期股价是多少？`,result.as_of],['查询五年涨跌与回撤',`${ticker} 过去五年股价涨跌和最大回撤是多少？`,result.as_of]];
      const annual=capabilities.annual_only===true||capabilities.annual_text_only===true;
      const annualFigures=capabilities.annual_financial_metrics?.length>0?[['查询年度营收与利润率',`${ticker} 年报营收同比和毛利率是多少？`,result.as_of]]:[];
      if(capabilities.annual_cashflow===true)annualFigures.push(['查询年度自由现金流',`${ticker} 年报自由现金流、现金资本开支和同比是多少？`,result.as_of]);
      const choices=annual?[...annualFigures,['查询年报风险原文',`${ticker} 年报披露了哪些主要风险？`,result.as_of],...prices]:capabilities.filings_supported!==true?prices:[['查询最新季度',`${ticker} 最近季度营业收入、营业利润同比和利润率？`,result.as_of],['查询 TTM 现金流',`${ticker} TTM 自由现金流及购置毛额和净额？`,result.as_of],['查询当前 TTM 估值',`${ticker} 当前 TTM EPS 市盈率是多少？`,state.health.as_of_default||result.as_of]];
      pending.textContent='以下问题使用当前已接入的数据能力。';
      choices.forEach(([label,question,asOf])=>{
      const button=el('button','secondary',label);button.type='button';
      button.addEventListener('click',()=>{clearCompanySelection();changeView('desk');$('#ticker').value=ticker;$('#question').value=question;$('#as-of').value=asOf;$('#mode').value='reference';['#ticker','#as-of','#mode'].forEach(id=>$(id).dispatchEvent(new Event('change')));$('#question').focus();$('#question').scrollIntoView({behavior:'smooth',block:'center'});});actions.append(button);
      });
    }).catch(()=>{pending.textContent='未取得可继续查询的问题列表，请从研究提问区选择已支持的问题。';});
  }else box.append(el('p','','请先选择公司，再从研究提问区选择可用的问题。已获取资料保存在本地。'));
  root.append(box);
}
function renderContrarian(root,result){const c=result.contrarian;if(!c||c.status==='not_run')return;const box=el('section','contrarian-section');box.append(el('h3','','反证'));if(c.status==='found'&&c.items?.length){c.items.forEach(item=>{const row=el('div','answer-item contrarian-item');row.append(el('blockquote','source-text',item.text));const refs=el('div','citations');(item.evidence_ids||[]).forEach((id,i)=>{const e=(result.evidence||[]).find(x=>x.id===id);const b=el('button','cite',(e?.ticker||'证据')+' · 原文 '+(i+1));b.addEventListener('click',()=>e&&showEvidence(e));refs.append(b);});row.append(refs,el('p','verification-note','反证原文逐字匹配'));box.append(row);});}else{box.append(el('p','verification-note',c.note||'本轮未检索到反证。'));}root.append(box);}

function renderFollowup(root,result){
  if(!result.id||!result.evidence?.length)return;
  const panel=el('section','followup-panel');
  panel.append(el('h3','','继续追问'),el('p','company-help','默认沿用本轮证据，不重复采集；需要检查新披露时勾选“刷新并生成新版本”。'));
  if(result.followup?.version_status==='changed'){
    const before=result.followup.attribution?.before_run_id||result.followup.research_id,after=result.followup.attribution?.after_run_id||result.id;
    const attribution=el('button','cite','查看版本归因');
    attribution.addEventListener('click',async()=>{attribution.disabled=true;try{const diff=await api('/api/runs/'+encodeURIComponent(after)+'/attribution?before_run_id='+encodeURIComponent(before));panel.append(el('pre','followup-attribution',JSON.stringify(diff,null,2)));}catch(error){panel.append(el('p','error',error.message));}finally{attribution.disabled=false;}});
    panel.append(el('p','verification-note','新资料已生成独立版本；旧结论仍保留。'),attribution);
  }
  const row=el('div','followup-row'),input=document.createElement('input');input.type='text';input.maxLength=500;input.placeholder='例如：为什么会这样？还有哪些风险？';
  const refresh=el('label'),check=document.createElement('input');check.type='checkbox';refresh.append(check,' 刷新资料并生成新版本');
  const button=el('button','secondary','提交追问');
  button.addEventListener('click',async()=>{const question=input.value.trim();if(question.length<2){input.focus();return;}button.disabled=true;try{const next=await api('/api/research/followup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({research_id:result.id,question,as_of:result.as_of,mode:result.mode==='agent'?'agent':'reference',refresh_sources:check.checked})});renderResult(next);root.scrollIntoView({behavior:'smooth',block:'start'});}catch(error){panel.append(el('p','error',error.message));}finally{button.disabled=false;}});
  row.append(input,refresh,button);panel.append(row);root.append(panel);
}

function renderResult(result,presentationOnly=false){ const originalLanguage=result.language||'zh';if(typeof renderResearchPreview==='function')renderResearchPreview({});if(result.mode==='trend'){changeView('trend');renderTrend(result,true);return;}if(result.mode==='comparison'){changeView('comparison');renderComparison(result,true);return;}state.result=result;if(!presentationOnly)displayResearchMarket(result);const root=$('#result');root.replaceChildren();root.classList.remove('hidden');const head=el('div','result-header');head.append(el('h2','','研究结果'),el('span','badge '+result.status,resultStatusLabel(result)));root.append(head);if(result.trace.some(t=>['annual_refresh','quarterly_preparation','updates_preparation','external_updates_preparation'].includes(t.stage)))renderSourceRefresh(root,result);if(result.trace.some(t=>t.stage==='deadline'))renderDeadlineActions(root,result);const stock=result.research_type==='stock';if(!stock)renderCompanyUpdates(root,result);const cashBrief=result.mode==='agent'&&/自由现金流|(?:^|[^A-Za-z0-9_])FCF(?=$|[^A-Za-z0-9_])|free cash flow/i.test(result.question)&&result.answer.some(c=>['analysis','narrative'].includes(c.kind));const brief=(stock&&result.mode!=='market')||cashBrief;if(stock)head.firstElementChild.textContent=result.mode==='market'?'行情与估值查询':'股票研究简报';if(cashBrief)head.firstElementChild.textContent='现金流研究简报';if(!stock&&(result.mode==='updates'||/新闻|公告|最近.{0,6}消息|latest news/i.test(result.question)))head.firstElementChild.textContent='公司公告与动态';const figures=el('details','stock-figures');figures.append(el('summary','','查看支撑判断的财务数字与计算'));const evidence=new Map(result.evidence.map(e=>[e.id,e]));let claims=brief?[...result.answer].sort((a,b)=>(b.kind==='analysis')-(a.kind==='analysis')):[...result.answer];if(!stock&&result.trace.some(t=>t.tool==='get_company_updates')){const publicDay=c=>c.evidence_ids.map(id=>evidence.get(id)?.filing?.filed||'').sort().at(-1)||'';claims.sort((a,b)=>publicDay(b).localeCompare(publicDay(a)));}claims.forEach(c=>{const row=el('div','answer-item'+(c.kind==='analysis'?' analysis-item':''));if(result.mode==='company_report')row.setAttribute('data-claim-index',String(c.index));if(c.kind==='analysis'){row.append(el('strong','claim-label','研究判断'));renderClaimPeriods(row,c,evidence);}const notices=[...new Map(c.evidence_ids.map(id=>evidence.get(id)?.filing).filter(f=>/^[68]-K/.test(f?.form||'')).map(f=>[f.accession,f])).values()];notices.forEach(f=>row.append(el('strong','claim-label','公开于 '+f.filed+' · '+noticeDateLabel(f.form)+' '+f.period_end)));row.append(el(c.kind==='excerpt'?'blockquote':'div','claim-text',c.text));const refs=el('div','citations');c.evidence_ids.forEach((id,i)=>{const e=evidence.get(id);const b=el('button','cite',(e?.ticker||'证据')+' · '+(e?.kind==='calculation'?'工具计算':e?.kind==='fact'?'财务事实':e?.kind==='market'?'行情证据':e?.kind==='valuation'?'供应商 EPS':'原文')+' '+(i+1));b.addEventListener('click',()=>e&&showEvidence(e));refs.append(b);});row.append(refs,el('p','verification-note',(c.kind==='valuation'?'供应商数字经代码提取；EPS 方法未独立核验':({deterministic:'数字由代码生成',exact_source_excerpt:'原文逐字匹配',semantic_review:'叙述经模型审查，完整含义仍可人工复核',inference_review:'分析基于所引证据，经模型审查；判断仍可人工复核'})[c.verification]||'查看证据与核验类别')));(brief&&['fact','calculation'].includes(c.kind)?figures:root).append(row);});if(result.mode==='company_report'&&typeof organizeCompanyReport==='function'){head.firstElementChild.textContent='完整公司研究';organizeCompanyReport(result,root);}if(result.mode==='scenarios'&&typeof renderScenarios==='function'){head.firstElementChild.textContent='估值情景 · 显式假设';renderScenarios(result,root);}if(result.trace.some(t=>t.stage==='historical_valuation')&&typeof renderHistoricalValuation==='function'){head.firstElementChild.textContent='历史估值参照';renderHistoricalValuation(result,root);}if(brief)addReferencedFigures(figures,result,evidence);if(brief&&figures.children.length>1)root.append(figures);if(stock)renderCompanyUpdates(root,result,true);if(typeof renderContrarian==='function')renderContrarian(root,result);const ttmNotes=[...new Set(result.trace.map(t=>t.result?.ttm_note||(t.tool==='get_ttm_financial_fact'?t.result?.note:null)).filter(Boolean))];ttmNotes.forEach(note=>root.append(el('p','verification-note',note)));renderCapexBasis(root,result);const visibleGaps=result.trace.some(t=>t.stage==='deadline')?result.gaps.filter(g=>!g.startsWith('本轮研究时间预算已用完；')):result.gaps;if(visibleGaps.length){const gaps=el('div','gaps');gaps.append(el('strong','',stock?'尚需补齐的信息':'证据边界'));visibleGaps.forEach(g=>gaps.append(el('p','',displayGap(g))));root.append(gaps);}const meta=el('div','result-meta');meta.append(el('span','',result.mode==='agent'?'Agent 研究':result.mode==='market'?'行情工具查询':result.mode==='updates'?'公司公告工具查询':result.mode==='ttm'?'TTM 财务工具查询':result.mode==='quarterly'?'季报工具查询':result.mode==='cashflow'?'现金流工具查询':result.mode==='followup'?'继续追问':'无模型研究'),el('span','','知悉日期 '+result.as_of),el('span','',(result.elapsed_ms/1000).toFixed(1)+' 秒'),el('span','',(result.usage.total_tokens||0).toLocaleString()+' tokens'));const exportButton=el('button','secondary',state.packageSupport?'导出研究包 ↓':'导出回答 JSON ↓');exportButton.addEventListener('click',async()=>{exportButton.disabled=true;try{const data=state.packageSupport?await api('/api/runs/'+encodeURIComponent(result.id)+'/package',undefined,true):JSON.stringify(result,null,2);const url=URL.createObjectURL(new Blob([data],{type:'application/json'}));const a=el('a');a.href=url;a.download=result.id+'-package.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){root.append(el('p','error',e.message));}finally{exportButton.disabled=false;}});meta.append(exportButton);root.append(meta);if(typeof attachInlineTranslation==='function')attachInlineTranslation(root,result,head);if(typeof FilingI18n!=='undefined'&&originalLanguage!==FilingI18n.language())root.append(el('p','verification-note','已保存的回答保留生成时的语言。切换语言后，新研究使用当前界面语言。'));addAuditButton(root,meta,result);if(typeof renderFollowup==='function')renderFollowup(root,result);const detail=el('details');detail.append(el('summary','','查看研究步骤与工具调用（'+result.trace.length+' 条）'));result.trace.forEach((t,i)=>{const row=el('div','trace-row');row.append(el('span','',String(i+1).padStart(2,'0')),el('strong','',t.tool||stageNames[t.stage]||t.stage),el('span','',(t.duration_ms!=null?(t.duration_ms/1000).toFixed(1)+' 秒':'')+(t.accepted_claims!=null?' · '+t.accepted_claims+' 项通过':'')+(t.attempts>1?' · 重试 '+(t.attempts-1)+' 次':'')));detail.append(row);});root.append(detail); }
const inlineTranslationCache=new Map();
function attachInlineTranslation(root,result,head){
  if(!result||result.language==='zh'||!result.answer?.length)return;
  document.querySelectorAll('.inline-translation-panel').forEach(node=>node.remove());
  const button=el('button','secondary inline-translation-button','中文');
  button.type='button';button.dataset.i18nSkip='true';
  button.setAttribute('aria-label','查看当前回答的中文翻译');
  button.setAttribute('aria-expanded','false');
  head.append(button);
  let panel=null;
  const close=()=>{
    if(panel)panel.hidden=true;
    button.setAttribute('aria-expanded','false');
    button.textContent='中文';
  };
  const render=translations=>{
    panel=el('aside','inline-translation-panel');
    panel.dataset.i18nSkip='true';panel.setAttribute('role','dialog');panel.setAttribute('aria-label','当前回答中文翻译');
    const bar=el('div','inline-translation-head');
    bar.append(el('h3','','中文对照 · 当前回答'));
    const x=el('button','inline-translation-close','×');x.type='button';x.setAttribute('aria-label','关闭中文翻译');x.addEventListener('click',close);
    bar.append(x);panel.append(bar);
    const items=[
      {label:'问题',text:result.question},
      ...(result.answer||[]).map((claim,index)=>({label:'回答 '+(index+1),text:claim.text||''})),
      ...(result.gaps||[]).map((gap,index)=>({label:'证据边界 '+(index+1),text:displayGap(gap)})),
    ];
    items.forEach((item,index)=>{
      const row=el('div','inline-translation-row');
      row.append(el('strong','',item.label),el('p','',translations[index]||''));
      panel.append(row);
    });
    panel.append(el('p','inline-translation-note','机器翻译，仅用于快速阅读；英文原文和证据原文保持不变。'));
    document.body.append(panel);
    button.textContent='隐藏中文';button.setAttribute('aria-expanded','true');
    const onKey=event=>{if(event.key==='Escape'){document.removeEventListener('keydown',onKey);close();}};
    document.addEventListener('keydown',onKey);
  };
  button.addEventListener('click',async()=>{
    if(panel&&!panel.hidden){close();return;}
    const items=[
      result.question,
      ...(result.answer||[]).map(claim=>claim.text||''),
      ...(result.gaps||[]).map(gap=>displayGap(gap)),
    ];
    const cached=inlineTranslationCache.get(result.id);
    if(cached){render(cached);return;}
    button.disabled=true;button.textContent='翻译中…';
    try{
      const data=await api('/api/translate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({texts:items,target:'zh'})});
      const translations=data.translations||[];
      if(translations.length!==items.length)throw new Error('译文数量不一致');
      inlineTranslationCache.set(result.id,translations);
      render(translations);
    }catch(error){
      if(panel)panel.remove();
      panel=el('aside','inline-translation-panel');
      panel.dataset.i18nSkip='true';
      const bar=el('div','inline-translation-head');bar.append(el('h3','','中文对照 · 当前回答'));
      const x=el('button','inline-translation-close','×');x.type='button';x.addEventListener('click',close);bar.append(x);panel.append(bar);
      panel.append(el('p','error','中文翻译暂不可用：'+error.message));
      document.body.append(panel);
      button.textContent='中文';
    }finally{button.disabled=false;}
  });
}
let researchTimer=null;
const researchStorage='filingcopilot.activeResearch';
async function researchJobApi(path,options,timeoutMessage='研究进度连接超时，可重新连接或请求取消。'){
  const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),10000);
  try{return await api(path,{...options,signal:controller.signal});}
  catch(error){if(error.name==='AbortError')throw new Error(timeoutMessage);throw error;}
  finally{clearTimeout(timeout);}
}
function researchBusy(active){
  state.researchRunning=active;
  $('#busy').classList.toggle('hidden',!active);
  document.querySelectorAll('#research,#company-report,#scenario-run,#company-query,#find-company,#question,#ticker,#as-of,#mode,.starters button,#question-presets button,#company-matches button,.company-source-actions button,.company-section-heading-actions button').forEach(n=>n.disabled=active);
  if(!active){clearInterval(researchTimer);researchTimer=null;$('#cancel-research').classList.add('hidden');$('#resume-research').classList.add('hidden');$('#leave-research').classList.add('hidden');}
}
function researchError(message){$('#error').textContent=message;$('#error').classList.remove('hidden');}
function forgetResearchJob(){state.researchJob=null;state.researchPending=null;try{localStorage.removeItem(researchStorage);}catch(_){}researchBusy(false);}
function rememberResearchJob(saved){state.researchPending=saved;try{localStorage.setItem(researchStorage,JSON.stringify(saved));}catch(_){} }
function showPendingResearch(message){
  researchBusy(true);state.researchJob=null;
  $('#busy-title').textContent='启动回执尚未确认';$('#busy-detail').textContent=message+' 可确认这次请求的已有任务；不会重复提交研究。';
  $('#cancel-research').classList.add('hidden');$('#resume-research').textContent='确认研究进度';$('#resume-research').classList.remove('hidden');$('#leave-research').classList.remove('hidden');
}
async function startResearchJob(request){
  if(typeof FilingI18n!=='undefined'&&!request.language)request={...request,language:FilingI18n.language()};
  const saved={key:crypto.randomUUID(),session:state.health.research_session,request};
  rememberResearchJob(saved);
  try{
    const headers={'Content-Type':'application/json','Idempotency-Key':saved.key};if(saved.session)headers['X-Research-Session']=saved.session;
    const job=await researchJobApi('/api/research/jobs',{method:'POST',headers,body:JSON.stringify(request)},'研究启动回执超时。');
    rememberResearchJob({...saved,id:job.id});state.researchJob=job.id;await watchResearchJob(job);
  }catch(error){
    if(error.status>=400&&error.status<500){forgetResearchJob();throw error;}
    showPendingResearch(error.message);await resumeResearchJob();
  }
}
function renderResearchPreview(job){
  const root=$('#research-preview');if(!root)return;
  const preview=job.preview;
  if(['completed','cancelled','timed_out','failed'].includes(job.status)||preview?.schema!=='filingcopilot.financial-preview/v1'||!preview.claims?.length){root.replaceChildren();root.classList.add('hidden');return;}
  const evidence=new Map((preview.evidence||[]).map(item=>[item.id,item]));
  root.replaceChildren(el('h3','',preview.ticker+' · 先看已核验的年度数字'),el('p','company-help',preview.note));
  const figures=el('div','preview-figures');
  for(const claim of preview.claims){
    const row=el('article','preview-figure');row.append(el('p','claim-text',claim.text));
    for(const id of claim.evidence_ids||[]){
      const source=evidence.get(id);if(!source)continue;
      const button=el('button','citation',source.filing?.form+' · 公开于 '+source.filing?.filed);
      button.addEventListener('click',()=>showEvidence(source));row.append(button);
    }
    figures.append(row);
  }
  const usage=job.usage||{};
  root.append(figures,el('p','company-help','本轮已完成模型调用 '+(usage.model_calls||0)+' 次 · 已记录 '+new Intl.NumberFormat('zh-CN').format(usage.total_tokens||0)+' tokens。最终解释与复核状态将在研究完成后显示。'));
  root.classList.remove('hidden');
}
function showResearchJob(job){
  renderResearchPreview(job);
  state.researchJob=job.id;
  state.researchConnectionLost=false;
  state.researchElapsed=job.elapsed_ms;state.researchObserved=Date.now();
  $('#elapsed').textContent=Math.floor(job.elapsed_ms/1000)+' 秒';
  const events=job.events||[];
  const last=events.at(-1);
  $('#busy-title').textContent=job.status==='timing_out'?'本轮时间已用完，正在保存':job.status==='cancelling'?'正在取消研究':job.status==='queued'?'研究等待执行':'研究正在进行';
  $('#busy-detail').textContent=job.status==='timing_out'?'已停止后续操作与重试，等待在途请求返回后保存已核验内容。':job.status==='cancelling'?'已请求取消。当前网络或模型请求返回后，停止新增调用并保留已核验内容。':last?.message||'正在执行研究。';
  if(job.status==='running'&&job.remaining_seconds!=null)$('#busy-detail').textContent+=' 本轮剩余 '+Math.ceil(job.remaining_seconds)+' 秒。';
  $('#research-progress').replaceChildren(...events.slice(-6).map(event=>{
    const text=Math.floor(event.elapsed_ms/1000)+' 秒 · '+event.message+(event.accepted_claims!=null?' 已核验 '+event.accepted_claims+' 项。':'')+(event.model_calls!=null?' 已完成 '+event.model_calls+' 次模型调用。':'');
    return el('li','',text);
  }));
  $('#cancel-research').classList.remove('hidden');
  $('#cancel-research').disabled=['cancelling','timing_out'].includes(job.status);
  $('#cancel-research').textContent=job.status==='timing_out'?'等待保存':job.status==='cancelling'?'已请求取消':'取消研究';
  $('#leave-research').classList.add('hidden');$('#resume-research').textContent='重新连接进度';
}
async function watchResearchJob(job){
  researchBusy(true);$('#resume-research').classList.add('hidden');$('#busy').scrollIntoView({behavior:'smooth',block:'nearest'});
  showResearchJob(job);
  clearInterval(researchTimer);
  researchTimer=setInterval(()=>$('#elapsed').textContent=(state.researchConnectionLost?'本页等待 ':'')+Math.floor(((state.researchElapsed||0)+Date.now()-(state.researchObserved||Date.now()))/1000)+' 秒',1000);
  let failures=0;
  while(state.researchJob===job.id){
    if(['completed','cancelled','timed_out','failed'].includes(job.status)){
      forgetResearchJob();
      if(job.result){renderResult(job.result);$('#result').scrollIntoView({behavior:'smooth',block:'start'});}
      else researchError(job.error?.message||(job.status==='cancelled'?'排队中的研究已取消，未发起模型调用。':'研究未完成，请重试。'));
      return;
    }
    await new Promise(resolve=>setTimeout(resolve,1000));
    try{job=await researchJobApi('/api/research/jobs/'+encodeURIComponent(job.id));failures=0;showResearchJob(job);}
    catch(error){
      failures++;
      if(error.message.includes('任务不存在')){forgetResearchJob();researchError(error.message);return;}
      state.researchConnectionLost=true;$('#busy-title').textContent='研究进度连接中断';$('#busy-detail').textContent='研究可能仍在后台进行。下方保留最后收到的步骤，重新连接可读取最新进度或已保存结果。';
      if(failures>=3){$('#resume-research').classList.remove('hidden');return;}
    }
  }
}
$('#cancel-research').addEventListener('click',async()=>{
  if(!state.researchJob)return;
  $('#cancel-research').disabled=true;
  try{const job=await researchJobApi('/api/research/jobs/'+encodeURIComponent(state.researchJob)+'/cancel',{method:'POST'});showResearchJob(job);if(!$('#resume-research').classList.contains('hidden'))await watchResearchJob(job);}
  catch(error){researchError('取消请求未送达：'+error.message);$('#cancel-research').disabled=false;}
});
$('#resume-research').addEventListener('click',()=>resumeResearchJob());
$('#leave-research').addEventListener('click',()=>{
  if(state.researchJob||!state.researchPending)return;
  forgetResearchJob();researchError('已结束本页等待，未取消可能已启动的后台任务。请先在研究记录查看结果，再决定是否重新研究。');
});
async function resumeResearchJob(){
  if(state.researchReconnecting)return;
  let saved=state.researchPending;try{saved=saved||JSON.parse(localStorage.getItem(researchStorage)||'null');}catch(_){}
  if(!saved?.id&&!saved?.key)return;
  state.researchPending=saved;state.researchJob=saved.id||null;
  if(saved.request){if(saved.request.ticker&&![...$('#ticker').options].some(o=>o.value===saved.request.ticker)){const option=el('option','',saved.request.ticker);option.value=saved.request.ticker;$('#ticker').prepend(option);}$('#question').value=saved.request.question;$('#ticker').value=saved.request.ticker||'';$('#as-of').value=saved.request.as_of;$('#mode').value=saved.request.mode;}
  researchBusy(true);
  state.researchReconnecting=true;$('#resume-research').disabled=true;
  try{
    const path=saved.id?'/api/research/jobs/'+encodeURIComponent(saved.id):'/api/research/jobs/recover?key='+encodeURIComponent(saved.key);
    const headers=saved.session?{'X-Research-Session':saved.session}:{};
    const job=await researchJobApi(path,{headers});rememberResearchJob({...saved,id:job.id});state.researchJob=job.id;
    $('#error').classList.add('hidden');await watchResearchJob(job);
  }catch(error){
    if(error.status===409||error.message.includes('任务不存在')){forgetResearchJob();researchError(error.message);}
    else if(!saved.id)showPendingResearch(error.message);
    else{researchError('无法恢复进度：'+error.message);$('#resume-research').classList.remove('hidden');}
  }finally{state.researchReconnecting=false;$('#resume-research').disabled=false;}
}
$('#research').addEventListener('click',async()=>{
  if(state.researchRunning)return;
  const question=$('#question').value.trim();if(question.length<2){$('#question').focus();return;}
  if(typeof renderResearchPreview==='function')renderResearchPreview({});$('#error').classList.add('hidden');$('#result').classList.add('hidden');$('#research-progress').replaceChildren();
  $('#busy-title').textContent='正在准备研究';$('#busy-detail').textContent='正在检查问题范围和公司身份。';researchBusy(true);
  try{
    const request={question,ticker:$('#ticker').value||null,as_of:$('#as-of').value,mode:$('#mode').value};
    const plan=await researchJobApi('/api/research/plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)},'研究范围检查连接超时，请检查连接后重新开始。');
    if(!plan.ready){researchBusy(false);await updateQuestionPlan();$('#question-plan').scrollIntoView({behavior:'smooth',block:'center'});return;}
    if(plan.kind!=='company_comparison')await ensureCompany(question,!state.health.research_jobs,['benchmark','updates'].includes(plan.kind));
    request.ticker=$('#ticker').value||null;
    request.auto_acquire=!!companyState.selected&&!marketQuestion(question)&&!['benchmark','company_comparison'].includes(plan.kind)&&companyState.selected.filings_supported!==false;
    if(!state.health.research_jobs){renderResult(await api('/api/research',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)}));researchBusy(false);return;}
    await startResearchJob(request);
  }catch(error){researchError(error.message);if(!state.researchJob&&!state.researchPending)researchBusy(false);}
});
async function loadHistory(){state.runs=await api('/api/runs');if(!state.runs.length){$('#history').replaceChildren(el('p','intro','还没有研究记录。完成第一项研究后，它会保存在这里。'));return;}$('#history').replaceChildren(...state.runs.map(r=>{const card=el('article','history-card');card.tabIndex=0;const text=el('div');text.append(el('h3','history-question',r.question),el('p','',r.as_of+' · '+(r.mode==='agent'?'Agent':r.mode==='trend'?'财务趋势':r.mode==='comparison'?'公司对比':r.mode==='updates'?'公告查询':r.mode==='ttm'?'TTM 查询':r.mode==='quarterly'?'季报查询':r.mode==='cashflow'?'现金流查询':r.mode==='market'?'行情查询':r.mode==='followup'?'继续追问':'无模型研究')+' · '+r.evidence.length+' 项证据'));card.append(text,el('span','badge '+r.status,resultStatusLabel(r)));const open=()=>{if(['comparison','trend'].includes(r.mode)){renderResult(r);return;}clearCompanySelection();changeView('desk');$('#question').value=r.question;const tickers=[...new Set(r.evidence.map(e=>e.ticker).filter(Boolean))];$('#ticker').value=r.trace.find(t=>t.stage==='deadline')?.focus_ticker||(tickers.length===1?tickers[0]:'');$('#ticker').dispatchEvent(new Event('change'));$('#as-of').value=r.as_of;$('#mode').value=['market','quarterly','cashflow','ttm','updates','followup'].includes(r.mode)?'reference':r.mode;$('#mode').dispatchEvent(new Event('change'));$('#as-of').dispatchEvent(new Event('change'));renderResult(r);$('#result').scrollIntoView({behavior:'smooth'});};card.addEventListener('click',open);card.addEventListener('keydown',e=>{if(e.key==='Enter')open();});return card;}));}
$('#copy-mcp').addEventListener('click',async()=>{await navigator.clipboard.writeText(JSON.stringify(state.connection,null,2));$('#copy-mcp').textContent='已复制 ✓';setTimeout(()=>$('#copy-mcp').textContent='复制配置',1800);});
async function init(){try{const [health,connection]=await Promise.all([api('/api/health'),api('/api/connection')]);requireMatchingRuntime(health);state.health=health;state.connection=connection;if(typeof comparisonSelection==='function')comparisonSelection();if(health.as_of_default){if($('#as-of').value===state.initialAsOf)$('#as-of').value=health.as_of_default;$('#as-of').max=health.as_of_default;}$('#company-discovery').classList.toggle('hidden',!health.company_discovery);const version=(health.version||'0.0').split('.').map(Number);state.packageSupport=version[0]>0||(version[0]===0&&version[1]>=3);$('.version').textContent='FILINGCOPILOT / '+(health.version||'');$('#ticker').replaceChildren(...(health.companies||[]).map(c=>{const o=el('option','',c.name+' · '+c.ticker);o.value=c.ticker;return o;}),el('option','','全部公司'));$('#ticker').lastElementChild.value='';$('#provider-status').textContent=health.agent_ready?'Agent 已就绪':'离线证据库已就绪';if(!health.agent_ready){$('#mode').value='reference';$('#mode').dispatchEvent(new Event('change'));}$('#mcp-config').textContent=JSON.stringify(connection,null,2);await loadCatalog();let saved='';try{saved=localStorage.getItem('filingcopilot.ticker')||'';}catch(_){}if($('#company-query').value.trim())$('#ticker').value='';else if(saved&&/^[A-Z0-9^][A-Z0-9.^=-]{0,14}$/.test(saved)){if(![...$('#ticker').options].some(o=>o.value===saved)){const o=el('option','',saved);o.value=saved;$('#ticker').prepend(o);}$('#ticker').value=saved;}else if([...$('#ticker').options].some(o=>o.value==='AMZN'))$('#ticker').value='AMZN';if(health.research_jobs)resumeResearchJob();await initMarket();}catch(e){$('#error').textContent=e.message;$('#error').classList.remove('hidden');}}
init();

function addVersionButton(root,e){
  if(!state.packageSupport||e.filing?.form?.startsWith('10-Q'))return;
  const button=el('button','secondary version-button','比较申报版本');root.append(button);
  button.addEventListener('click',async()=>{
    button.disabled=true;
    try{
      const query=new URLSearchParams({ticker:e.ticker,metric:e.metric,fiscal_year:e.fiscal_year,as_of:state.result?.as_of||$('#as-of').value});
      const data=await api('/api/facts/versions?'+query);
      if(!root.contains(button))return;
      root.querySelector('.version-list')?.remove();
      const list=el('section','version-list');list.append(el('h3','','知悉日期内的申报版本'),el('p','',data.note));
      if(data.selection==='ambiguous')list.append(el('p','error','最新申报存在冲突值，未自动选择。'));
      data.versions.forEach(v=>{
        const x=v.evidence;const row=el('div','version-row');
        const role=({original_period:'原年度申报',amendment:'修订申报',later_comparative:'后续申报比较数据'})[v.role];
        row.append(el('strong','',x.filing.filed+' · '+role),el('p','',displayEvidenceAmount(x)+(x.id===data.selected_id?' · 当前选用':'')),el('p','',x.filing.accession));
        if(v.comparison==='changed_value')row.append(el('p','version-change','相同期间数值发生变化，原因未判定。'));
        else if(v.comparison==='same_value')row.append(el('p','','相同期间数值一致。'));
        const open=el('button','cite','查看这个版本的证据');open.addEventListener('click',()=>showEvidence(x));row.append(open);list.append(row);
      });
      if(!data.versions.length)list.append(el('p','','此知悉日期前没有可用版本。'));
      root.append(list);
    }catch(error){if(root.contains(button))root.append(el('p','error',error.message));}finally{button.disabled=false;}
  });
}
function addAuditButton(root,meta,result){
  if(!state.packageSupport)return;
  const badge=el('span','small-tag','可离线复核');
  const button=el('button','secondary','查看离线复核报告');meta.append(badge,button);
  button.addEventListener('click',async()=>{
    button.disabled=true;button.textContent='正在重算与比对…';
    try{
      const report=await api('/api/runs/'+encodeURIComponent(result.id)+'/audit');
      if(!root.contains(button))return;
      root.querySelector('.audit-report')?.remove();
      const panel=el('section','audit-report');panel.append(el('h3','',report.status==='consistent'?'已执行的一致性检查通过':'发现不一致，请检查报告'));
      badge.textContent=report.status==='consistent'?'本研究通过离线复核':'离线复核发现问题';
      panel.append(el('p','',(report.numeric_claims_recomputed?report.numeric_claims_recomputed+' 项数字句检查 · ':'')+report.checks.filter(c=>c.code==='calculation'&&c.status==='pass').length+' 项绑定计算重算 · '+report.source_evidence_checked+' 项本地来源比对 · '+report.checks_failed+' 项失败'));
      panel.append(el('p','',report.narrative_claims_not_rechecked+' 项叙述或分析未重新审查完整语义；本次复核不调用模型。'));
      panel.append(el('p','',report.raw_sources.filings_checked+' 份原始申报哈希检查 · '+report.raw_sources.facts_checked+' 条原始 XBRL 比对 · '+report.raw_sources.missing+' 项缓存缺失'));
      if(report.valuation_sources?.checked||report.valuation_sources?.missing)panel.append(el('p','',report.valuation_sources.checked+' 份供应商 EPS 原始响应重提取 · '+report.valuation_sources.missing+' 份缓存缺失；未独立验证供应商 EPS 方法'));if(report.market_sources?.requested)panel.append(el('p','',report.market_sources.checked+' 份行情原始响应重提取 · '+report.market_sources.missing+' 份行情缓存缺失'));if(report.source_validation!=='matched')panel.append(el('p','gaps','未完成全部本地来源匹配。'));
      const details=el('details');details.append(el('summary','','查看逐项复核结果'));
      report.checks.forEach(c=>details.append(el('p',c.status==='fail'?'version-change':'',({pass:'通过',fail:'失败',not_checked:'未检查'})[c.status]+' · '+c.detail+(c.evidence_id?' ['+c.evidence_id+']':''))));
      panel.append(details);root.append(panel);
    }catch(error){if(root.contains(button))root.append(el('p','error',error.message));}finally{button.disabled=false;button.textContent='查看离线复核报告';}
  });
}

// Redraw only cached presentation; do not acquire sources or launch research on a language switch.
if(typeof FilingI18n!=='undefined')window.addEventListener('filingcopilot:language-change',()=>{
  if(typeof refreshSourceLanguage==='function'&&!$('#drawer')?.classList.contains('hidden'))refreshSourceLanguage();
  if(state.researchRunning)return;
  if(typeof marketState!=='undefined'&&marketState.data)displayMarket(marketState.data,marketState.scope);
  const root=$('#result');
  if(state.result&&root&&!root.classList.contains('hidden')){
    const inputs=[...root.querySelectorAll('input,textarea,select')].map(n=>({value:n.value,checked:n.checked}));
    const details=[...root.querySelectorAll('details')].map(n=>n.open);
    renderResult(state.result,true);
    [...root.querySelectorAll('input,textarea,select')].forEach((n,i)=>{if(inputs[i]){n.value=inputs[i].value;n.checked=inputs[i].checked;}});
    [...root.querySelectorAll('details')].forEach((n,i)=>n.open=!!details[i]);
  }
});
