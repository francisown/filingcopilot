// Controlled DOM exercises; actual browser acceptance is recorded separately.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const code=fs.readFileSync('src/filingcopilot/static/company-workspace.js','utf8');
assert.match(code,/company-inline-summary/);
assert.match(code,/company-raw-materials/);
assert.match(code,/本节总结未生成/);
assert.match(code,/company-inline-boundary/);
assert.match(code,/module\.question/);
assert.match(code,/module:section\.key/);
assert.match(code,/busy-button/);
assert.match(code,/companyModuleNumericRules/);
assert.match(code,/companyModuleNumberEntries/);
assert.match(code,/companyDeterministicConclusion/);
assert.match(code,/globalThis\.document/);
assert.match(code,/已有总结正在生成中/);
assert.match(code,/startAllModuleSummaries/);
assert.match(code,/停止批量生成/);
assert.match(code,/doneSections/);
assert.match(code,/companyWorkspaceNeedsLoad/);
assert.match(code,/summaryCache/);
assert.doesNotMatch(code,/questions\[section\.key\]/,'per-module questions must come from the page contract, not a local map');
assert.doesNotMatch(code,/button\.disabled=busy/,'section buttons must not be globally disabled while one summary runs');
assert.doesNotMatch(code,/input',companyWorkspaceReset/,'typing in the desk search box must not stop a running generation');
assert.doesNotMatch(code,/startFullCompanySummaryInline/,'the old six-part inline action is replaced by the all-modules batch');
const appCode=fs.readFileSync('src/filingcopilot/static/app.js','utf8');
assert.ok(appCode.includes("companyWorkspaceNeedsLoad!=='function'"),'view switching must not reload the company page while it is already loaded');
assert.match(appCode,/statusReasonLabels/,'research badges must expose an explicit incomplete-run reason');
assert.match(appCode,/result\.status_reason/,'saved status_reason must drive the visible badge');
const focused=code.slice(code.indexOf('function startFocusedCompanySummary'),code.indexOf('function renderCompanyWorkspaceSections'));
assert.doesNotMatch(focused,/changeView\(['"]desk['"]\)/,'Focused section summaries must stay on the company page');
function node(tag='div',cls='',text=''){
  const classes=new Set(cls.split(' ')),listeners={};
  return {tag,textContent:text,value:'',children:[],disabled:false,dataset:{},checked:false,
    classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x)},
    append(...children){this.children.push(...children);},replaceChildren(...children){this.children=children;},
    setAttribute(){},removeAttribute(){},
    get firstElementChild(){return this.children[0];},
    addEventListener(name,fn){(listeners[name]??=[]).push(fn);},
    async fire(name){await Promise.all((listeners[name]||[]).map(fn=>fn({type:name})));},
    dispatchEvent(event){return this.fire(event.type);},
    focus(){},scrollIntoView(){},click(){return this.fire('click');}};
}
function harness(api){
  const nodes=new Map(),calls=[],evidence=[],views=[],requests=[];
  const select=s=>{if(!nodes.has(s))nodes.set(s,node());return nodes.get(s);};
  select('#ticker').value='AAPL';select('#as-of').value='2026-10-08';select('#company-view').classList.add('hidden');
  const context={$:select,el:node,AbortController,URLSearchParams,setTimeout,clearTimeout,Promise,Set,Map,Event,
    document:{querySelectorAll:()=>[]},
    state:{researchRunning:false},comparisonState:{selected:[],busy:false},comparisonSelection(){},
    sourceValue:e=>node('div','',e.value+' '+e.unit),displayDecimal:value=>Number(value).toFixed(2),displayScaledAmount:e=>Number(e.value).toFixed(2)+' '+e.unit,showEvidence:e=>evidence.push(e),
    changeView:view=>views.push(view),updateQuestionPlan(){},api:async(path,options)=>{calls.push(path);requests.push({path,body:options?.body});return api(path,options);}};
  vm.createContext(context);vm.runInContext(code,context);return {context,select,calls,evidence,views,requests};
}
const text=n=>[n.textContent,...n.children.map(text)].join(' ');
function responses(path){
  if(path.includes('research-page'))return {ticker:'AAPL',annual_overview:{fiscal_year:2025,evidence:[{id:'f1',kind:'fact',fiscal_year:2025}]},sections:[{key:'business',title:'商业模式',status:'ready',source_dates:['2025-10-31'],evidence_ids:['p1'],module:{kind:'analysis',question:'What is the company business model: how it makes money?',boundary:'本段只归纳已披露的业务内容；未披露的分部结构、收入占比与客户集中度不作推断，未结构化的分部金额另行核验。',empty_gap:'未取得业务章节原文；可先“检查最新披露”再重试本节。'}},{key:'risk',title:'风险',status:'gap',source_dates:[],evidence_ids:[],gap:'暂无原文'},{key:'audit',title:'审计报告原文',status:'ready',source_dates:['2025-10-31'],evidence_ids:['p1'],module:{kind:'factual',question:'What does the audit report state about the opinion?',boundary:'本段是审计报告原文的归位整理，不是自动审计结论；不同年度、审计师与报告对象不得合并陈述。',empty_gap:'未取得审计报告原文；不生成审计解释，可先“检查最新披露”再重试。'}}],evidence:[{id:'p1',kind:'passage',text:'Observed business description'},{id:'f1',kind:'fact',label:'收入',value:'123.456',unit:'USD',end:'2025-09-27'}]};
  if(path.includes('/peers'))return {peers:['MSFT','AMZN'],group_label:'候选同行',basis:'注册组',note:'并非业务完全可比'};
  if(path.includes('/capabilities'))return {filings_supported:true,annual_only:false};
  if(path.includes('research/plan'))return {ready:true,message:'按问题读取年度事实，并检索申报原文。'};
  if(path.includes('/api/research'))return {answer:[{kind:'analysis',index:0,text:'公司通过已披露的两条业务线获取收入。',evidence_ids:['p1','f1','c1'],verification:'inference_review'},{kind:'analysis',index:1,text:'客户结构以企业客户为主。',evidence_ids:['p1'],verification:'inference_review'},{kind:'excerpt',index:2,text:'原始文本摘录。',evidence_ids:['p1'],verification:'exact_source_excerpt'}],evidence:[{id:'p1',kind:'passage',text:'Observed business description'},{id:'f1',kind:'fact',label:'经营活动现金流',value:'100',unit:'USD',fiscal_year:2025},{id:'c1',kind:'calculation',label:'经营活动现金流同比增长率',value:'24.57',unit:'%',fiscal_year:2025}],gaps:['分部金额未结构化核验']};
  if(path.includes('/catalog'))return {filings:[{id:'a1',ticker:'AAPL',form:'10-K',filed:'2025-10-31',period_end:'2025-09-27'},{id:'m1',ticker:'MSFT',form:'10-K',filed:'2025-07-30',period_end:'2025-06-30'}]};
  if(path.includes('quarterly-sequence'))return {periods:[{period_end:'2025-09-30',facts:[{id:'q1',metric:'revenue'}]}],evidence:[{id:'q1',metric:'revenue',value:'12.345',unit:'USD'}],missing:['仅有一个期间；缺失不补零']};
  if(path.includes('/statements'))return {rows:[{id:'s1',label:'Segment revenue',value:'10.1234',unit:'USD',end:'2025-09-30',dimensions:{'axis':'member'},dimension_labels:{member:'云服务'}},{id:'s2',label:'Cash',end:'2025-09-30',dimensions:{}}],evidence:[{id:'s1',kind:'statement_fact',value:'10.1234',unit:'USD'},{id:'s2',kind:'statement_fact',value:'99',unit:'USD'}],missing:[]};
  if(path.includes('/dcf-inputs'))return {found:true,ticker:'AAPL',as_of:'2026-10-08',currency:'USD',inputs:{base_free_cash_flow:'70',shares_outstanding:'10',source_evidence_ids:['ocf','capex','shares']},source_validation:{shares_date:'2025-09-27'},evidence:[{id:'ocf',label:'OCF',value:'100',unit:'USD'}],note:'不是目标价'};
  if(path.includes('/valuation/dcf'))return {currency:'USD',base_case:{implied_value_per_share:'123.4567'},user_assumptions:{forecast_years:5,growth_pct:'0',discount_pct:'10',terminal_growth_pct:'2'},sensitivity:[{growth_pct:'5',discount_pct:'10',implied_value_per_share:'123.4567'}],note:'不是目标价'};
  throw new Error('Unexpected '+path);
}
async function check(){
  const h=harness(responses);await h.context.loadCompanyWorkspace();
  assert.ok(h.calls.some(path=>path.includes('include_market=false')));assert.ok(!h.calls.some(path=>path.includes('/market/')));
  assert.match(text(h.select('#company-key-figures')),/123.46 USD/);assert.doesNotMatch(text(h.select('#company-key-figures')),/123.456/);
  assert.match(text(h.select('#company-source-sections')),/暂无原文/);assert.equal(h.select('#company-statement-filing').children.length,2);
  const peerInput=h.select('#company-peers-options').children[0].children[0];peerInput.checked=true;await peerInput.fire('change');await h.select('#company-peers-compare').fire('click');
  assert.equal(h.context.comparisonState.selected.map(p=>p.ticker).join(','),'AAPL,MSFT');assert.equal(h.views.at(-1),'comparison');assert.ok(!h.calls.some(path=>path.includes('/comparison')),'selection alone must not submit a comparison');
  await h.context.loadCompanyWorkspaceQuarters();assert.match(text(h.select('#company-quarters-table')),/2025-09-30/);assert.match(text(h.select('#company-quarters-table')),/未披露/);assert.match(h.select('#company-quarters-status').textContent,/不补零/);
  await h.context.loadCompanyWorkspaceStatements();h.select('#company-statement-dimensions').value='segments';h.context.renderCompanyWorkspaceStatements();assert.match(text(h.select('#company-statements-table')),/云服务/);assert.doesNotMatch(text(h.select('#company-statements-table')),/Cash/);
  const retry=harness(responses);await retry.context.loadCompanyWorkspace();const before=retry.calls.length;await retry.context.loadCompanyWorkspace(true,'risk');assert.equal(retry.calls.length,before+1);assert.match(retry.calls.at(-1),/section=risk/);
  let resolve;const pending=new Promise(r=>resolve=r);const stale=harness(path=>path.includes('research-page')?pending:responses(path));const loading=stale.context.loadCompanyWorkspace();stale.select('#ticker').value='AMZN';stale.context.companyWorkspaceReset();resolve(responses('research-page'));await loading;assert.equal(stale.select('#company-source-sections').children.length,0);assert.match(stale.select('#company-workspace-title').textContent,/AMZN/);
  const outage=harness(path=>path.includes('/peers')?Promise.reject(new Error('peer source offline')):responses(path));await outage.context.loadCompanyWorkspace();assert.ok(outage.select('#company-source-sections').children.length);assert.match(outage.select('#company-workspace-status').textContent,/部分来源/);
  let releasePeer;const waitingPeer=new Promise(resolve=>releasePeer=resolve),progressive=harness(path=>path.includes('/peers')?waitingPeer:responses(path));const progressiveLoad=progressive.context.loadCompanyWorkspace();
  // Flush the resolved source promises without releasing the independently pending peer source.
  for(let i=0;i<8;i++)await Promise.resolve();
  try{
    assert.ok(progressive.select('#company-source-sections').children.length,'ready company sources must render before a slow peer request completes');
    assert.match(text(progressive.select('#company-key-figures')),/123.46 USD/);assert.equal(progressive.select('#company-statement-filing').children.length,2,'the report catalog must also load independently');
    assert.match(progressive.select('#company-workspace-status').textContent,/仍在读取.*同行/);assert.doesNotMatch(progressive.select('#company-workspace-status').textContent,/资料读取完成/);
    assert.equal(progressive.select('#company-workspace-load').disabled,true,'remaining requests must still be identified as pending');
  }finally{releasePeer(responses('/peers'));await progressiveLoad;}
  assert.equal(progressive.select('#company-peers-options').children.length,2);assert.equal(progressive.select('#company-workspace-load').disabled,false);assert.match(progressive.select('#company-workspace-status').textContent,/资料读取完成/);
  let rejectLatePeer;const latePeer=new Promise((_,reject)=>rejectLatePeer=reject),replaced=harness(path=>path.includes('/peers')?latePeer:responses(path));const oldLoad=replaced.context.loadCompanyWorkspace();for(let i=0;i<8;i++)await Promise.resolve();replaced.select('#ticker').value='AMZN';replaced.context.companyWorkspaceReset();rejectLatePeer(new Error('old supplier failed'));await oldLoad;
  assert.equal(replaced.select('#company-source-sections').children.length,0);assert.equal(replaced.select('#company-workspace-status').textContent,'','a late supplier error must not replace a new company status');assert.equal(replaced.select('#company-peers-note').textContent,'');
  let releaseOldPeer;const oldPeer=new Promise(resolve=>releaseOldPeer=resolve);let peerRequests=0;const superseded=harness(path=>path.includes('/peers')&&++peerRequests===1?oldPeer:responses(path));const supersededLoad=superseded.context.loadCompanyWorkspace();for(let i=0;i<8;i++)await Promise.resolve();await superseded.context.loadCompanyWorkspace();releaseOldPeer({peers:['STALE'],note:'old source'});await supersededLoad;
  assert.doesNotMatch(text(superseded.select('#company-peers-options')),/STALE/);assert.match(superseded.select('#company-workspace-status').textContent,/资料读取完成/,'an older load must not overwrite a newer load in the same company scope');
  h.select('#company-statement-filing').value='a1';await h.context.loadCompanyWorkspace();assert.equal(h.select('#company-statement-filing').value,'a1','refreshing the catalog must preserve an available selected report');
  let releaseQuarters,releaseCapabilities;const activeQuarters=new Promise(resolve=>releaseQuarters=resolve),waitingCapabilities=new Promise(resolve=>releaseCapabilities=resolve),independent=harness(path=>path.includes('quarterly-sequence')?activeQuarters:path.includes('/capabilities')?waitingCapabilities:responses(path));const sourcesLoading=independent.context.loadCompanyWorkspace(),quartersLoading=independent.context.loadCompanyWorkspaceQuarters();releaseCapabilities(responses('/capabilities'));await sourcesLoading;
  try{assert.equal(independent.select('#company-quarters-refresh').disabled,true,'capabilities must not enable a second quarter refresh while the first is pending');}finally{releaseQuarters(responses('quarterly-sequence'));await quartersLoading;}
  assert.equal(independent.select('#company-quarters-refresh').disabled,false);
  await h.select('#company-historical-pe').fire('click');assert.equal(h.select('#mode').value,'reference');assert.match(h.select('#question').value,/历史年度市盈率/);
  await h.context.loadCompanyDcfInputs();assert.equal(h.select('#company-dcf-form').classList.contains('hidden'),false);h.select('#company-dcf-years').value='5';h.select('#company-dcf-growth-grid').value='-5,0,5';h.select('#company-dcf-discount-grid').value='8,10,12';await h.context.runCompanyDcf();assert.match(text(h.select('#company-dcf-result')),/123.46 USD/);assert.doesNotMatch(text(h.select('#company-dcf-result')),/123.4567/);assert.match(text(h.select('#company-dcf-result')),/本次计算/);
  let resolveStatement;const oldStatement=new Promise(r=>resolveStatement=r);const changed=harness(path=>path.includes('/statements')?oldStatement:responses(path));const reading=changed.context.loadCompanyWorkspaceStatements();changed.select('#company-statement-type').value='balance';await changed.select('#company-statement-type').fire('change');resolveStatement(responses('/statements'));await reading;assert.equal(changed.select('#company-statements-table').children.length,0,'a late statement must not replace a newly selected report');assert.equal(changed.select('#company-statements-load').disabled,false);
  const app=fs.readFileSync('src/filingcopilot/static/app.js','utf8');vm.runInContext(app.slice(app.indexOf('function sourceUi'),app.indexOf('function showEvidence')),h.context);h.context.showStatementEvidence({ticker:'AAPL',label:'Segment revenue',value:'12',unit:'USD',end:'2025-09-27',text:'Segment revenue grew to USD 12 as services expanded.',kind:'statement_fact',filing:{form:'10-K',filed:'2025-10-31',source_url:'https://www.sec.gov/filing'},dimensions:{'us-gaap:ProductAxis':'a:ServicesMember'},dimension_labels:{'us-gaap:ProductAxis':'ServicesMember'},source_url:'https://www.sec.gov/source'});assert.match(text(h.select('#drawer-content')),/us-gaap:ProductAxis：ServicesMember/);assert.match(text(h.select('#drawer-content')),/财务事实（结构化数据）/);assert.doesNotMatch(text(h.select('#drawer-content')),/ServicesMember：/);assert.match(text(h.select('#drawer-content')),/查看技术细节/);assert.match(text(h.select('#drawer-content')),/Segment revenue\s+grew/);const excerpt=h.select('#drawer-content').children.find(n=>n.classList?.contains('source-readable-excerpt'));const hasHighlight=n=>n?.classList?.contains('source-highlight')||(n?.children||[]).some(hasHighlight);assert.ok(hasHighlight(excerpt));
  const businessSection={key:'business',title:'商业模式',status:'ready',evidence_ids:['p1'],module:{kind:'analysis',question:'What is the company business model: how it makes money?',boundary:'本段只归纳已披露的业务内容；未披露的分部结构、收入占比与客户集中度不作推断，未结构化的分部金额另行核验。',empty_gap:'未取得业务章节原文；可先“检查最新披露”再重试本节。'}};
  const summaryCard=h.select('#company-source-sections').children[0],summaryButton=summaryCard.children[0].children[2].children[0];
  await h.context.startFocusedCompanySummary(businessSection,summaryCard,summaryButton);
  const summary=text(summaryCard.children[2]),count=(value,re)=>(value.match(re)||[]).length;
  assert.match(summary,/固定格式：结论 → 证据与期间 → 边界 → 待补信息/);
  assert.match(summary,/分析经审查/);assert.match(summary,/公司通过已披露的两条业务线获取收入。/);assert.match(summary,/客户结构以企业客户为主。/);
  assert.match(summary,/边界/);assert.match(summary,/本段只归纳已披露的业务内容/);
  assert.match(summary,/待补信息/);assert.match(summary,/分部金额未结构化核验/);
  assert.match(summary,/关键数字/,'cited verified numbers must stay visible');
  assert.match(summary,/100\.00 USD/);assert.match(summary,/24\.57%/);assert.match(summary,/经营活动现金流同比增长率/);
  assert.equal(count(summary,/分析基于所引证据，经模型审查；判断仍可人工复核/g),1,'two analysis statements must share one review note');
  assert.equal(count(summary,/原文逐字匹配/g),1);
  assert.equal(count(summary,/结论/g),2,'one section label plus the fixed-format caption');
  assert.equal(summaryButton.disabled,false);assert.equal(summaryButton.classList.contains('busy-button'),false,'the busy mark must clear after completion');
  const deterministicHost=node('div','company-inline-summary hidden');
  h.context.renderInlineCompanySummary(deterministicHost,{answer:[],evidence:[{id:'growth-1',kind:'calculation',operation:'growth',label:'营业收入同比增长率',value:'12.5',unit:'%',fiscal_year:2025}],gaps:[]},{key:'cashflow',title:'现金流与资本投入',status:'partial',evidence_ids:[],module:{kind:'analysis',boundary:'现金流边界',empty_gap:'暂无现金流材料'} });
  assert.match(text(deterministicHost),/可确认结论/,'typed growth evidence must yield a deterministic directional conclusion when prose is absent');
  assert.match(text(deterministicHost),/同比增长/);
  const ratioHost=node('div','company-inline-summary hidden');
  h.context.renderInlineCompanySummary(ratioHost,{answer:[],evidence:[{id:'ratio-1',kind:'calculation',operation:'ratio',label:'营业利润率',value:'12.5',unit:'%',fiscal_year:2025}],gaps:[]},{key:'operating_change',title:'经营变化',status:'partial',evidence_ids:[],module:{kind:'analysis',boundary:'经营边界',empty_gap:'暂无经营材料'} });
  assert.match(text(ratioHost),/已按绑定输入计算为 12.50%/,'typed ratios must yield a concrete deterministic conclusion when prose is absent');
  const summaryBody=JSON.parse(h.requests.filter(r=>r.path==='/api/research').at(-1).body);
  assert.equal(summaryBody.module,'business','the focused summary must send its module key');assert.equal(summaryBody.question,businessSection.module.question);assert.equal(summaryBody.depth,'brief');
  const auditSection={key:'audit',title:'审计报告原文',module:{kind:'factual',question:'What does the audit report state about the opinion?',boundary:'本段是审计报告原文的归位整理，不是自动审计结论；不同年度、审计师与报告对象不得合并陈述。',empty_gap:'未取得审计报告原文；不生成审计解释，可先“检查最新披露”再重试。'}};
  let releaseResearch;const researchGate=new Promise(resolve=>releaseResearch=resolve);let firstResearch=true;
  const busy=harness(path=>{if(path==='/api/research'&&firstResearch){firstResearch=false;return researchGate.then(()=>responses(path));}return responses(path);});
  const busyHostA=node('div','company-inline-summary hidden'),busyHostB=node('div','company-inline-summary hidden'),busyButtonA=node('button','primary','生成本节文字总结'),busyButtonB=node('button','primary','生成本节文字总结');
  const runningA=busy.context.startFocusedCompanySummary(businessSection,{querySelector:s=>s==='.company-inline-summary'?busyHostA:null},busyButtonA);
  assert.equal(busyButtonA.disabled,true,'the running button carries its own busy state');assert.equal(busyButtonA.textContent,'正在生成本节总结…');assert.equal(busyButtonA.classList.contains('busy-button'),true);
  await busy.context.startFocusedCompanySummary(auditSection,{querySelector:s=>s==='.company-inline-summary'?busyHostB:null},busyButtonB);
  assert.match(text(busyHostB),/已有总结正在生成中（商业模式）/);
  assert.equal(busyButtonB.disabled,false,'other section buttons stay clickable so the page never looks frozen');
  releaseResearch();await runningA;
  assert.equal(busyButtonA.disabled,false);assert.equal(busyButtonA.textContent,'生成本节文字总结');assert.equal(busyButtonA.classList.contains('busy-button'),false);
  assert.match(text(busyHostA),/公司通过已披露的两条业务线获取收入。/);
  const auditCard=h.select('#company-source-sections').children[2];
  await h.context.startFocusedCompanySummary(auditSection,auditCard,auditCard.children[0].children[2].children[0]);
  assert.match(text(auditCard.children[2]),/整理/,'factual modules label their items as restatement');assert.match(text(auditCard.children[2]),/不是自动审计结论/);
  const failed=harness(path=>path==='/api/research'?{answer:[],evidence:[],gaps:['模型请求失败，研究已中止；已核验内容保留，未核验内容不作结论。']}:responses(path));
  const failedHost=node('div','company-inline-summary hidden');
  await failed.context.startFocusedCompanySummary(businessSection,{querySelector:s=>s==='.company-inline-summary'?failedHost:null},node('button','primary','生成本节文字总结'));
  const failedText=text(failedHost);
  assert.match(failedText,/具体原因见下方缺口/);assert.doesNotMatch(failedText,/未取得业务章节原文/,'a failed model run must not claim the section lacks sources');
  assert.match(failedText,/待补信息/);assert.match(failedText,/模型请求失败/);
  h.select('#company-workspace-report').textContent='生成全部模块总结';
  const researchPosts=()=>h.requests.filter(r=>r.path==='/api/research').length;
  const postsBefore=researchPosts();
  const batchRun=h.context.startAllModuleSummaries();
  assert.equal(h.select('#company-workspace-report').textContent,'停止批量生成');
  await batchRun;
  assert.equal(researchPosts()-postsBefore,1,'already-delivered sections are skipped; the remaining section is generated');
  assert.equal(h.select('#company-workspace-report').textContent,'生成全部模块总结');
  assert.match(text(h.select('#company-report-inline-result')),/全部模块总结已生成/);
  assert.match(text(h.select('#company-source-sections').children[1].children[2]),/公司通过已披露的两条业务线获取收入。/,'each card shows its full text as soon as it finishes');
  const totalCalls=h.requests.length;
  await h.context.startAllModuleSummaries();
  assert.equal(h.requests.length,totalCalls,'a completed batch re-run starts no further research');
  let releaseBatch;const batchGate=new Promise(resolve=>releaseBatch=resolve);let firstBatch=true;
  const stopping=harness(path=>{if(path==='/api/research'&&firstBatch){firstBatch=false;return batchGate.then(()=>responses(path));}return responses(path);});
  await stopping.context.loadCompanyWorkspace();
  stopping.select('#company-workspace-report').textContent='生成全部模块总结';
  const stopRun=stopping.context.startAllModuleSummaries();
  assert.equal(stopping.select('#company-workspace-report').textContent,'停止批量生成');
  await stopping.context.startAllModuleSummaries();
  assert.equal(stopping.select('#company-workspace-report').textContent,'停止中…');
  releaseBatch();await stopRun;
  assert.equal(stopping.select('#company-workspace-report').textContent,'生成全部模块总结');
  assert.match(text(stopping.select('#company-report-inline-result')),/已停止批量生成/);
  assert.equal(stopping.requests.filter(r=>r.path==='/api/research').length,1,'stopping leaves completed items and skips the rest');
  await h.context.loadCompanyWorkspace();
  assert.equal(h.context.companyWorkspaceNeedsLoad(),false);
  assert.match(text(h.select('#company-source-sections').children[0].children[2]),/公司通过已披露的两条业务线获取收入。/,'a rebuilt card restores the delivered summary from the session cache');
  let releaseMid;const midGate=new Promise(resolve=>releaseMid=resolve);let firstMid=true;
  const rebuild=harness(path=>{if(path==='/api/research'&&firstMid){firstMid=false;return midGate.then(()=>responses(path));}return responses(path);});
  await rebuild.context.loadCompanyWorkspace();
  const businessCard=rebuild.select('#company-source-sections').children[0],businessHost=businessCard.children[2],businessButton=businessCard.children[0].children[2].children[0];
  const midRun=rebuild.context.startFocusedCompanySummary(businessSection,{querySelector:s=>s==='.company-inline-summary'?businessHost:null},businessButton);
  await rebuild.context.loadCompanyWorkspace();
  releaseMid();await midRun;
  const rebuiltCard=rebuild.select('#company-source-sections').children[0];
  assert.match(text(rebuiltCard.children[2]),/公司通过已披露的两条业务线获取收入。/,'an in-flight result lands in the rebuilt card');
  const rebuiltButton=rebuiltCard.children[0].children[2].children[0];
  assert.equal(rebuiltButton.disabled,false);assert.equal(rebuiltButton.textContent,'生成本节文字总结','the rebuilt button is restored instead of staying busy');
  assert.equal(rebuild.context.companyWorkspaceNeedsLoad(),false);
  rebuild.select('#ticker').value='AMZN';
  assert.equal(rebuild.context.companyWorkspaceNeedsLoad(),true,'a different company needs a reload');
  console.log(JSON.stringify({scope:'Company source workspace, incremental sources, pending suppliers, scoped partial failure, citations, rounded values, manual peers, quarters, dimensions, stale-response rejection, module fixed-format summaries, single-flight busy presentation, one-article grouping, honest failure reasons, resumable all-module batch generation with cooperative stop, view-switch safety and rebuilt-card restore',passed:true}));
}
check().catch(error=>{console.error(error);process.exitCode=1;});
