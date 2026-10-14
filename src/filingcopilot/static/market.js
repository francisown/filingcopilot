function marketSourceLabel(s){return s.source_name||((s.basis==='split_adjusted_close')?'Twelve Data':'Yahoo Finance');}
function marketBasisLabel(s){return s.basis==='adjusted_close'?'供应商当前复权收盘价':s.basis==='split_adjusted_close'?'拆股调整收盘价，未按股息调整':'供应商 Close 收盘价';}
function marketUi(zh,en){return typeof FilingI18n!=='undefined'&&FilingI18n.language?.()==='en'?en:zh;}
function marketQuoteDate(snapshot){
  const timestamp=Number(snapshot?.quote_at),zone=snapshot?.exchange_timezone;
  if(!Number.isFinite(timestamp)||timestamp<=0||typeof zone!=='string'||!zone)return marketUi('报价日期未提供','Quote date not supplied');
  try{
    const parts=new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit',...(snapshot.quote_type==='provider_quote'?{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}:{})}).formatToParts(new Date(timestamp*1000));
    const get=type=>parts.find(p=>p.type===type)?.value;
    const date=[get('year'),get('month'),get('day')].join('-');
    const time=snapshot.quote_type==='provider_quote'?[get('hour'),get('minute'),get('second')].join(':'):'';
    return date+(time?' '+time:'')+' ('+zone+')';
  }catch{return marketUi('报价日期未提供','Quote date not supplied');}
}
// Charts use source-bound daily observations; presentation never changes backend statistics.
const marketState = {generation: 0, controller: null, operation: null, scope: null, period: '5Y', data: null, index: 0};
const periodLabels = {'1M':'1个月','6M':'6个月','1Y':'1年','3Y':'3年','5Y':'5年','10Y':'10年','MAX':'全部'};
function svgEl(tag, attributes={}, text=null){const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v] of Object.entries(attributes))n.setAttribute(k,v);if(text!=null)n.textContent=text;return n;}
function marketNumber(value, digits=2){return displayDecimal(value,digits,digits);}
function pricePoints(snapshot){return snapshot.points.map(p=>({date:p.date,at:p.at,value:Number(snapshot.basis==='adjusted_close'?p.adjusted:p.close)}));}
function displayMarket(data,scope=null){
  marketState.data=data;marketState.scope=scope||{ticker:data.snapshot.ticker,asOf:data.snapshot.as_of,period:data.snapshot.period};
  const s=data.snapshot, stats=data.statistics;
  $('#market-content').classList.remove('hidden');
  $('#market-name').textContent=s.name+' · '+s.ticker;
  $('#market-price').textContent=marketNumber(s.quote_value);$('#market-currency').textContent=s.currency;
  const interval=$('#market-return'), change=Number(stats.change_pct);
  interval.textContent=stats.trading_days>1?(change>0?'+':'')+marketNumber(stats.change_pct)+'%':'—';
  interval.classList.toggle('positive',stats.trading_days>1&&change>=0);interval.classList.toggle('negative',stats.trading_days>1&&change<0);
  $('#valuation-preview').replaceChildren();
  $('#market-time').textContent=(s.quote_type==='historical_close'?'历史收盘价':'行情报价，可能延迟')+' · '+(s.basis==='split_adjusted_close'?stats.quote_at.slice(0,10)+' · 按日期收盘价（无精确时刻）':stats.quote_at.replace('T',' '))+' · '+s.exchange;
  $('#market-status').textContent=data.frozen?'研究时保存的行情快照；点刷新可读取新行情。':s.stale?'当前使用已保存行情，抓取时间 '+s.retrieved_at:'行情与长期走势已就绪。';
  $('#market-status').classList.toggle('company-failed',s.stale);
  const coverage=$('#chart-coverage-note'), quoteDay=stats.quote_at.slice(0,10);
  const quoteAhead=s.quote_type==='provider_quote'&&quoteDay>stats.end;
  coverage.classList.toggle('hidden',!quoteAhead);
  coverage.textContent=quoteAhead?'日线截至 '+stats.end+'；报价截至 '+quoteDay+'。曲线尚未包含报价交易日的日线，区间变化和回撤按所示日线范围计算。':'';
  const history=$('#chart-history-note');history.classList.toggle('hidden',s.period!=='MAX');
  history.textContent='“全部”显示行情源可用日线（'+stats.start+' 至 '+stats.end+'）；起点可能晚于上市日期，不代表从上市首日起的完整记录。';
  const rows=[['区间变化',stats.trading_days>1?marketNumber(stats.change_pct)+'%':'—'],['最大回撤',stats.trading_days>1?marketNumber(stats.max_drawdown_pct)+'%':'—'],['历史低点',stats.low==null?'—':marketNumber(stats.low)+' '+s.currency],['历史高点',stats.high==null?'—':marketNumber(stats.high)+' '+s.currency],['交易日',stats.trading_days.toLocaleString()],['区间',stats.start+' — '+stats.end]];
  $('#market-metrics').replaceChildren(...rows.map(([label,value],i)=>{const n=el('div');n.append(el('span','',label),el('strong',i===0?(Number(stats.change_pct)>=0?'positive':'negative'):'',value));return n;}));
  const source=$('#market-source');source.replaceChildren(el('p','company-help','抓取时间 '+s.retrieved_at+' · '+marketBasisLabel(s)+' · 区间变化未年化。'));
  s.warnings.forEach(w=>source.append(el('p','company-help',w)));
  if(!data.frozen&&s.stale&&data.availability?.message)source.append(el('p','company-help',data.availability.message+' 以下为保存的行情，尚未更新。'));
  const button=el('button','cite','查看行情来源与日线价格');button.type='button';button.id='market-source-open';button.addEventListener('click',()=>showMarketEvidence({kind:'market',ticker:s.ticker,snapshot:s}));source.append(button);
  drawMarketChart();
}
function drawMarketChart(){
  if(!marketState.data)return;
  const s=marketState.data.snapshot, points=pricePoints(s), svg=$('#price-chart');
  const w=Math.max(320,Math.round(svg.clientWidth||900)),h=w<500?260:310,left=65,right=25,top=20,bottom=45;
  svg.setAttribute('viewBox','0 0 '+w+' '+h);
  const log=$('#chart-scale').value==='log', transform=v=>log?Math.log(v):v;
  let min=Math.min(...points.map(p=>transform(p.value))),max=Math.max(...points.map(p=>transform(p.value)));
  const pad=(max-min)*.08||Math.abs(max)*.08||1;min-=pad;max+=pad;
  if(!log)min=Math.max(0,min);
  const first=points[0].at,last=points.at(-1).at;
  const x=p=>left+(p.at-first)/(last-first||1)*(w-left-right);
  const y=p=>top+(max-transform(p.value))/(max-min)*(h-top-bottom);
  svg.replaceChildren();
  svg.setAttribute('aria-label',s.ticker+' '+periodLabels[s.period]+' '+s.currency+' '+marketBasisLabel(s)+'。左右方向键查看交易日，Home / End 查看首末日。');
  for(let i=0;i<=4;i++){
    const yy=top+i/4*(h-top-bottom),value=max-i/4*(max-min), actual=log?Math.exp(value):value;
    svg.append(svgEl('line',{x1:left,x2:w-right,y1:yy,y2:yy,stroke:'#e2e8f0','stroke-width':1}),svgEl('text',{x:left-10,y:yy+4,'text-anchor':'end',fill:'#64748b','font-size':12},marketNumber(actual,actual>=100?0:2)));
  }
  const ticks=w<500?2:4;
  for(let i=0;i<=ticks;i++){
    const point=points[Math.round(i/ticks*(points.length-1))];
    const label=s.period==='1M'||s.period==='6M'?point.date.slice(5):point.date.slice(0,7);
    svg.append(svgEl('text',{x:x(point),y:h-15,'text-anchor':i===0?'start':i===ticks?'end':'middle',fill:'#64748b','font-size':12},label));
  }
  const path=points.map((p,i)=>(i?'L':'M')+x(p).toFixed(2)+','+y(p).toFixed(2)).join(' ');
  svg.append(svgEl('path',{d:path+' L'+x(points.at(-1))+','+(h-bottom)+' L'+x(points[0])+','+(h-bottom)+' Z',fill:'#dbeafe',opacity:.6}),svgEl('path',{d:path,fill:'none',stroke:'#2563eb','stroke-width':2,'vector-effect':'non-scaling-stroke'}));
  const cursor=svgEl('line',{x1:0,x2:0,y1:top,y2:h-bottom,stroke:'#7b9ecf','stroke-dasharray':'4 4'}),dot=svgEl('circle',{cx:0,cy:0,r:4,fill:'#2563eb'});
  svg.append(cursor,dot);
  function hover(index){
    marketState.index=Math.max(0,Math.min(points.length-1,index));const p=points[marketState.index];
    cursor.setAttribute('x1',x(p));cursor.setAttribute('x2',x(p));dot.setAttribute('cx',x(p));dot.setAttribute('cy',y(p));
    $('#chart-tooltip').textContent=p.date+' · '+marketNumber(p.value)+' '+s.currency+' · '+marketBasisLabel(s);
  }
  svg.onpointermove=event=>{
    const bounds=svg.getBoundingClientRect(),position=(event.clientX-bounds.left)/bounds.width*w;
    const target=first+(position-left)/(w-left-right)*(last-first);
    let lo=0,hi=points.length-1;
    while(lo<hi){const mid=Math.floor((lo+hi)/2);if(points[mid].at<target)lo=mid+1;else hi=mid;}
    hover(lo>0&&Math.abs(points[lo-1].at-target)<Math.abs(points[lo].at-target)?lo-1:lo);
  };
  svg.onpointerdown=svg.onpointermove;
  svg.onkeydown=event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();hover(event.key==='Home'?0:event.key==='End'?points.length-1:marketState.index+(event.key==='ArrowLeft'?-1:1));}};
  hover(points.length-1);
}
function resetMarket(message='请先选择股票，再读取行情。'){
  marketState.generation++;marketState.controller?.abort();marketState.data=null;marketState.scope=null;
  $('#market-content').classList.add('hidden');$('#market-panel').setAttribute('aria-busy','false');
  $('#market-name').textContent='选择上市市场';$('#market-status').textContent=message;
  $('#market-status').classList.remove('company-failed');$('#market-refresh').disabled=true;
  $('#market-refresh').textContent='刷新行情';
  $('#market-stop').classList.add('hidden');$('#market-stop').disabled=true;
  const all=[...$('#ticker').options].find(o=>o.value==='');if(all)all.textContent=companyInput.value.trim()?'等待选择上市证券':'全部公司';
}
function beginMarketOperation(){
  const generation=++marketState.generation;marketState.controller?.abort();
  const controller=new AbortController(),operation={generation,controller,pending:0};
  marketState.controller=controller;marketState.operation=operation;return operation;
}
function updateMarketWaiting(operation){
  if(operation.generation!==marketState.generation)return;
  const waiting=operation.pending>0&&!operation.controller.signal.aborted;
  $('#market-stop').classList.toggle('hidden',!waiting);$('#market-stop').disabled=!waiting;
}
function marketRequest(path,operation,label,timeout=45000){
  // Bound the complete response, including a body that keeps streaming. Each
  // resource has its own clock; changing scope or stopping aborts all children.
  return new Promise((resolve,reject)=>{
    const controller=new AbortController(),signal=operation.controller.signal;
    let settled=false,timer;
    operation.pending++;updateMarketWaiting(operation);
    function finish(error,value){
      if(settled)return;settled=true;clearTimeout(timer);signal.removeEventListener('abort',abort);
      operation.pending--;updateMarketWaiting(operation);if(error)reject(error);else resolve(value);
    }
    function abort(){finish(signal.reason||new Error('已停止等待'));controller.abort();}
    if(signal.aborted){abort();return;}
    signal.addEventListener('abort',abort,{once:true});
    timer=setTimeout(()=>{finish(new Error(label+'等待超时（'+timeout/1000+' 秒），请重试。'));controller.abort();},timeout);
    Promise.resolve().then(()=>api(path,{signal:controller.signal})).then(value=>finish(null,value),error=>finish(error));
  });
}
function stopMarketWaiting(){
  const operation=marketState.operation;if(!operation||!operation.pending)return;
  operation.controller.abort(new Error('已停止本页等待，可点刷新重试；服务器可能仍在获取并保存公开资料。'));
  if(marketState.data){$('#market-status').textContent='已停止本页尚未完成资料的等待；保留已返回行情。可刷新重试，服务器可能仍在保存资料。';$('#market-status').classList.add('company-failed');}
}
function loadMarketValuations(operation,ticker,asOf,capabilities){
  if(operation.generation!==marketState.generation||operation.controller.signal.aborted)return;
  const root=$('#valuation-preview');root.replaceChildren();
  if(!capabilities){root.append(el('p','company-help','证券研究能力暂未返回，价格图仍可查看。刷新可重新检查估值范围。'));return;}
  if(capabilities.filings_supported===false)return;
  const bases=['annual'];if(asOf===state.health?.as_of_default&&!capabilities.annual_only)bases.push('provider_ttm');
  for(const basis of bases){
    const annual=basis==='annual',label=annual?'年度估值':'供应商 TTM 市盈率',slot=el('div','valuation-slot');root.append(slot);
    slot.append(el('p','company-help',annual?'正在读取年度 EPS；缺少年报时会自动获取，股价图可先查看。':'正在读取供应商 TTM 每股收益，股价图可先查看。'));
    const query=new URLSearchParams({ticker,as_of:asOf});if(!annual)query.set('basis',basis);
    marketRequest('/api/market/valuation?'+query,operation,label).then(value=>{
      if(operation.generation!==marketState.generation)return;
      slot.replaceChildren();
      if(value.found){if(annual)displayAnnualPE(value.evidence,asOf,slot);else displayProviderPE(value.evidence,slot);}
      else slot.append(el('p','company-help',label+'暂未就绪：'+String(value.reason||'每股收益来源未返回，请重试。').replace('TTM P/E requires the same equity and currency','价格与每股收益的证券或币种不一致，不能组合计算。')));
      if(value.warning)slot.append(el('p','company-help',value.warning));
      if(!value.found||value.warning)addRetry();
    },error=>{
      if(operation.generation!==marketState.generation)return;
      slot.replaceChildren(el('p','company-help',label+'暂未就绪：'+error.message));addRetry();
    });
    function addRetry(){const retry=el('button','cite','重试'+label);retry.addEventListener('click',()=>loadMarket());slot.append(retry);}
  }
}
async function loadMarket(refresh=false){
  if(typeof updateWatchCurrent==='function')updateWatchCurrent();
  const ticker=$('#ticker').value, asOf=$('#as-of').value;
  if(companyInput.value.trim()&&!companyState.selected){resetMarket('请先查找公司，再在候选中选择上市市场和币种。行情无需下载财报。');return;}
  const operation=beginMarketOperation(),generation=operation.generation;
  const scope=marketState.scope,retained=marketState.data&&scope?.ticker===ticker&&scope.asOf===asOf&&scope.period===marketState.period;
  if(!retained){marketState.data=null;marketState.scope=null;$('#market-content').classList.add('hidden');}
  if(!ticker){resetMarket();updateQuestionPlan();const current=marketState.generation;try{const capabilities=await api('/api/capabilities');if(current===marketState.generation)renderCapabilities(capabilities);}catch(_){}return;}
  $('#market-panel').setAttribute('aria-busy','true');$('#market-refresh').disabled=true;$('#market-refresh').textContent='读取中…';
  if(!retained)$('#market-name').textContent=ticker+' · 正在读取行情';
  $('#market-status').textContent='正在连接行情源，获取价格与长期走势。无需下载财报。最多等待 45 秒，可停止本页等待。'+(retained?' 保留上次行情，抓取于 '+marketState.data.snapshot.retrieved_at+'。':'');$('#market-status').classList.remove('company-failed');
  const capabilityRequest=marketRequest('/api/capabilities?'+new URLSearchParams({ticker}),operation,'证券研究能力',10000).then(capabilities=>{if(generation===marketState.generation){renderCapabilities(capabilities);updateQuestionPlan();}return capabilities;}).catch(()=>null);
  $('#chart-periods').querySelectorAll('button').forEach(b=>{b.classList.toggle('active',b.dataset.period===marketState.period);b.setAttribute('aria-pressed',String(b.dataset.period===marketState.period));});
  try{
    const query=new URLSearchParams({ticker,as_of:asOf,period:marketState.period});
    if(refresh===true)query.set('refresh','true');
    const data=await marketRequest('/api/market/history?'+query,operation,'行情');
    if(generation!==marketState.generation)return;
    displayMarket(data,{ticker,asOf,period:marketState.period});try{localStorage.setItem('filingcopilot.ticker',ticker);}catch(_){/* Storage may be disabled. */}
    $('#valuation-preview').replaceChildren(el('p','company-help','正在检查估值范围，股价图可先查看。'));
    capabilityRequest.then(capabilities=>loadMarketValuations(operation,ticker,asOf,capabilities));
  }catch(error){if(generation===marketState.generation){
    if(!retained)$('#market-name').textContent=ticker;
    $('#market-status').textContent=error.message+(retained?' 保留上次行情，抓取于 '+marketState.data.snapshot.retrieved_at+'；可刷新重试。':'');
    $('#market-status').classList.add('company-failed');$('#market-refresh').textContent='重试行情';operation.controller.abort();
  }}
  finally{if(generation===marketState.generation){$('#market-panel').setAttribute('aria-busy','false');$('#market-refresh').disabled=false;if(marketState.data)$('#market-refresh').textContent='刷新行情';}}
}
function marketQuestion(text){return /股价|行情|走势图|趋势图|历史价格|涨跌|回撤|涨了|跌了|price|stock chart|drawdown|return/i.test(text)&&!/营收|营业|净利润|现金流|估值|市盈率|风险|管理层|年报|为什么|原因|怎么样|值得|revenue|valuation|risk|why/i.test(text);}
function displayAnnualPE(e,asOf,target=$('#valuation-preview')){
  const row=el('div','valuation-row');row.append(el('strong','','年度市盈率 '+marketNumber(e.value)+' 倍'),el('span','','FY'+e.fiscal_year+(e.formula?.includes('ADS')?' SEC 美元 ADS EPS · 年报便利换算 · 非 TTM':' SEC 年度稀释 EPS · 非 TTM')));
  const b=el('button','cite','查看 EPS 出处');b.addEventListener('click',async()=>{try{showEvidence(await api('/api/evidence/'+encodeURIComponent(e.input_ids[1])+'?as_of='+encodeURIComponent(asOf)));}catch(error){row.append(el('p','error',error.message));}});row.append(b);target.append(row);
}
function displayProviderPE(e,target=$('#valuation-preview')){
  const row=el('div','valuation-row'),s=e.inputs[1].snapshot;
  row.append(el('strong','','供应商 TTM 市盈率 '+marketNumber(e.value)+' 倍'),el('span','','Yahoo TTM 稀释 EPS '+marketNumber(s.value)+' '+s.currency+'/股 · 期末标签 '+s.period_end_label));
  const b=el('button','cite','查看口径与计算');b.addEventListener('click',()=>showEvidence(e));row.append(b);target.append(row);
}
function showValuationEvidence(e){
  if(typeof rememberSourceEvidence==='function')rememberSourceEvidence(e);
  const root=$('#drawer-content'),s=e.snapshot;
  root.replaceChildren(el('h2','',s.ticker+' '+marketUi('供应商 TTM 稀释 EPS','Supplier TTM diluted EPS')),sourceValue({value:s.value,unit:s.currency+'/股'}),
    el('p','source-readable-lead',marketUi('这个数字表示供应商口径的过去十二个月稀释每股收益。它可以和同一币种、同一证券口径的价格组合计算 TTM 市盈率。','This is the supplier’s trailing-twelve-month diluted EPS. It can be combined with a same-currency, same-security price to calculate supplier-basis TTM P/E.')),
    el('p','',marketUi('供应商期末标签 ','Supplier period-end label ')+s.period_end_label+' · '+marketUi('获取于 ','retrieved ')+(s.retrieved_at||'')),
    el('p','verification-note',marketUi('Yahoo Finance TTM 口径；期末标签可能按日历季度标准化，不代表公司的实际财季末。未逐季重建 SEC 加权稀释股数或独立核对供应商拆股调整；不是预测 EPS。','Yahoo Finance TTM basis; the period-end label may be normalized to a calendar quarter and may not be the company’s fiscal quarter end. SEC diluted share counts and supplier split adjustments were not rebuilt independently; this is not a forecast EPS.')));
  root.append(marketSourceDetails(s));
  if(s.stale)root.append(el('p','gaps',marketUi('更新失败，当前为原获取时间的陈旧缓存，不据此计算当前市盈率。','Refresh failed; this is a stale cached value and is not used as a current P/E.')));
  if(typeof openEvidenceDrawer==='function')openEvidenceDrawer();else{$('#drawer').classList.remove('hidden');$('#drawer-overlay').classList.remove('hidden');}
}
async function displayResearchMarket(result){
  if(!result.evidence.some(e=>e.kind==='market'||e.kind==='calculation'&&e.operation==='annual_pe'))return;
  const ticker=$('#ticker').value,asOf=$('#as-of').value;
  if(!ticker||(companyInput.value.trim()&&!companyState.selected)||!result.evidence.some(e=>e.kind==='market'&&e.ticker===ticker))return;
  const operation=beginMarketOperation(),generation=operation.generation;
  $('#market-panel').setAttribute('aria-busy','true');$('#market-refresh').disabled=true;$('#market-refresh').textContent='读取研究快照…';
  $('#market-status').textContent='正在读取研究时保存的行情快照。';$('#market-status').classList.remove('company-failed');
  try{
    const data=await marketRequest('/api/runs/'+encodeURIComponent(result.id)+'/market',operation,'研究行情快照');
    if(generation!==marketState.generation||state.result?.id!==result.id||data.snapshot.ticker!==ticker||$('#ticker').value!==ticker||$('#as-of').value!==asOf)return;
    marketState.period=data.snapshot.period;
    $('#chart-periods').querySelectorAll('button').forEach(b=>{b.classList.toggle('active',b.dataset.period===marketState.period);b.setAttribute('aria-pressed',String(b.dataset.period===marketState.period));});
    displayMarket(data,{ticker,asOf,period:data.snapshot.period});
    const pe=result.evidence.find(e=>e.kind==='calculation'&&e.operation==='annual_pe');if(pe)displayAnnualPE(pe,result.as_of);
    const ttm=result.evidence.find(e=>e.kind==='calculation'&&e.operation==='provider_ttm_pe');if(ttm)displayProviderPE(ttm);
  }catch(error){
    if(generation===marketState.generation&&state.result?.id===result.id){$('#market-status').textContent='研究回答仍已保存；'+error.message+' 可点刷新重新读取行情。';$('#market-status').classList.add('company-failed');}
  }finally{
    if(generation===marketState.generation){$('#market-panel').setAttribute('aria-busy','false');$('#market-refresh').disabled=false;$('#market-refresh').textContent='刷新行情';}
  }
}
let questionPlanGeneration=0, questionPlanTimer;
function showResearchMode(usesModel=null){$('#mode-note').textContent=usesModel===false?'本次由数据工具直接查询和计算，无需调用模型；缺少资料时会自动检查公开来源。':$('#mode').value==='agent'?'Agent 按问题选择工具，核验数字与原文，再形成回答。':'不调用模型；行情与最新披露查询会按需联网检查，数字由原始证据和工具计算生成。';}
async function updateQuestionPlan(){
  const question=$('#question').value.trim(), generation=++questionPlanGeneration;
  const unresolved=companyInput.value.trim()&&!companyState.selected;
  $('#question-presets').classList.toggle('hidden',!!unresolved);
  if(!state.researchRunning)$('#research').disabled=!!unresolved;
  if(unresolved&&question.length<2){$('#question-plan').textContent='请先查找公司并选择上市市场，再选择研究问题。';$('#question-plan').classList.remove('company-failed');return;}
  if(question.length<2){$('#question-plan').textContent='选一个问题，查看本次研究会做什么。';$('#question-plan').classList.remove('company-failed');showResearchMode();return;}
  try{
    const plan=await api('/api/research/plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question,ticker:$('#ticker').value||null,as_of:$('#as-of').value,mode:$('#mode').value})});
    if(generation!==questionPlanGeneration)return;
    if(unresolved&&plan.kind!=='company_comparison'){$('#research').disabled=true;$('#question-plan').textContent='请先查找公司并选择上市市场，再选择研究问题。';$('#question-plan').classList.remove('company-failed');return;}
    if(!state.researchRunning)$('#research').disabled=!plan.ready;
    const root=$('#question-plan');root.replaceChildren(el('span','',plan.message));root.classList.toggle('company-failed',!plan.ready);
    if(plan.ready&&plan.uses_model&&plan.time_budget_seconds)root.append(el('span','',` 本轮时间预算 ${plan.time_budget_seconds} 秒，到期保存已核验部分，可继续查询具体问题。`));
    if(plan.ready&&plan.budget){
      const b=plan.budget;
      const calls=`预计 ${b.estimated_model_calls} 次模型、${b.estimated_tool_calls} 次工具调用`;
      const tokens=b.estimated_total_tokens?`，约 ${Number(b.estimated_total_tokens).toLocaleString()} token`:'';
      const cost=b.estimated_cost_usd==null?'，模型成本未配置价格':`，模型成本约 $${Number(b.estimated_cost_usd).toFixed(4)}`;
      root.append(el('span','plan-budget',`执行预算：${calls}${tokens}；延迟约 ${b.estimated_latency_seconds} 秒${cost}。${b.cost_basis||''}`));
    }
    showResearchMode(plan.ready?plan.uses_model:null);
    if(!plan.ready)plan.suggestions.forEach(q=>{const b=el('button','cite',q);b.addEventListener('click',()=>{$('#question').value=typeof FilingI18n==='undefined'?q:FilingI18n.translate(q);updateQuestionPlan();});root.append(' ',b);});
  }catch(_){if(generation===questionPlanGeneration&&unresolved){$('#question-plan').textContent='研究范围检查未完成，请重试，或先选择明确的公司。';$('#question-plan').classList.add('company-failed');}/* The research endpoint independently enforces the same capability boundary. */}
}
$('#question').addEventListener('input',()=>{clearTimeout(questionPlanTimer);questionPlanTimer=setTimeout(updateQuestionPlan,250);});
$('#mode').addEventListener('change',updateQuestionPlan);
function marketSourceDetails(s){
  const details=el('details','source-technical');details.append(el('summary','',marketUi('查看技术细节','View technical details')));
  const hash=el('p','');hash.append(el('strong','',marketUi('原始响应 SHA256','Raw response SHA256')),el('span','',s.source_sha256||'—'));details.append(hash);
  if(s.source_url){const link=el('a','cite',marketUi('打开原始接口响应 ↗','Open raw API response ↗'));link.href=s.source_url;link.target='_blank';link.rel='noopener noreferrer';details.append(link);}
  return details;
}
function marketSourceTable(root,s){
  const points=s.points||[];if(!points.length)return;
  const box=el('section','source-prices'),wrap=el('div','source-table-wrap'),table=el('table'),body=el('tbody'),header=el('tr');
  const adjusted=s.basis==='adjusted_close';
  box.append(el('h3','',marketUi('逐日价格','Daily prices')));
  const closeLabel=s.basis==='split_adjusted_close'?marketUi('拆股调整收盘价','Split-adjusted close'):marketUi('供应商 Close','Supplier Close');
  for(const label of [marketUi('日期','Date'),closeLabel+(s.currency?' ('+s.currency+')':''),...(adjusted?[marketUi('复权收盘价','Adjusted close')+(s.currency?' ('+s.currency+')':'')]:[])]){const th=el('th','',label);th.scope='col';header.append(th);}
  const head=el('thead');head.append(header);table.append(head,body);wrap.append(table);box.append(wrap);
  const controls=el('div','source-page-controls'),older=el('button','secondary',marketUi('较早日期','Older dates')),newer=el('button','secondary',marketUi('较新日期','Newer dates')),range=el('span','');
  older.type=newer.type='button';range.setAttribute('role','status');controls.append(older,range,newer);box.append(controls);root.append(box);
  const size=20,lastPage=Math.floor((points.length-1)/size);let page=lastPage;
  function render(){
    const start=page*size,end=Math.min(points.length,start+size);
    body.replaceChildren(...points.slice(start,end).map(point=>{const row=el('tr');row.append(el('td','',point.date),el('td','',marketNumber(point.close)));if(adjusted)row.append(el('td','',point.adjusted==null?'—':marketNumber(point.adjusted)));return row;}));
    range.textContent=points[start].date+' → '+points[end-1].date;older.disabled=page===0;newer.disabled=page===lastPage;
  }
  older.addEventListener('click',()=>{if(page>0){page--;render();}});newer.addEventListener('click',()=>{if(page<lastPage){page++;render();}});render();
}
function showMarketEvidence(e){
  if(typeof rememberSourceEvidence==='function')rememberSourceEvidence(e);
  const root=$('#drawer-content'),s=e.snapshot,first=s.points?.[0],last=s.points?.at(-1);
  root.replaceChildren(el('h2','',s.ticker+' '+marketUi('行情证据','Market evidence')),el('p','',marketSourceLabel(s)+' · '+s.currency+' · '+(s.exchange||'—')),
    el('p','source-readable-lead',marketUi('日线记录用于走势图和区间统计。报价和日线可能来自不同交易日；日期与价格分别保留。','Daily observations support the price chart and range statistics. The quote and daily series can end on different trading days; their dates and prices remain separate.')));
  const quote=el('p','source-readable-lead');quote.append(el('strong','',marketUi('本次保存的报价：','Saved quote: ')),el('strong','',marketNumber(s.quote_value)+' '+s.currency));root.append(quote);
  if(first&&last)root.append(el('p','',first.date+' → '+last.date+' · '+s.points.length+' '+marketUi('个交易日','trading days')));
  root.append(el('p','',marketUi('报价时间：','Quote time: ')+marketQuoteDate(s)),el('p','',marketUi('价格口径：','Price basis: ')+marketBasisLabel(s)),el('p','',marketUi('抓取时间 ','Retrieved ')+(s.retrieved_at||'—')));
  (s.warnings||[]).forEach(w=>root.append(el('p','verification-note',w)));
  if(s.stale)root.append(el('p','gaps',marketUi('当前为保存的旧行情，尚未确认已更新。','This is a saved, stale snapshot; a successful update has not been confirmed.')));
  marketSourceTable(root,s);root.append(marketSourceDetails(s));
  root.append(el('h3','',marketUi('可复算公式','Reproducible formulas')),el('p','',marketUi('区间变化 =（末日价格 ÷ 首日价格 − 1）× 100；最大回撤 = 各交易日价格相对于此前最高价格的最小变化。全量日线随研究包保存。','Range return = (last price ÷ first price − 1) × 100; maximum drawdown is the minimum daily change from the prior running high. The full daily series is retained in the research package.')));
  if(typeof openEvidenceDrawer==='function')openEvidenceDrawer();else{$('#drawer').classList.remove('hidden');$('#drawer-overlay').classList.remove('hidden');}
}
function renderCapabilities(capabilities){
  const pricesOnly=capabilities.filings_supported===false;
  const annualText=capabilities.annual_text_only===true;
  const annualNumbers=capabilities.annual_only===true&&capabilities.annual_financial_metrics?.length>0;
  const annualCash=annualNumbers&&capabilities.annual_cashflow===true;
  const annualADS=annualNumbers&&capabilities.annual_ads_valuation===true;
  const reportingCurrency=({CNY:'人民币',TWD:'新台币',USD:'美元'})[capabilities.annual_reporting_currency]||capabilities.annual_reporting_currency||'报表原币';
  $('.research-intro').textContent=pricesOnly?'这只证券当前提供价格、长期涨跌和回撤查询；财报来源尚未接入。':annualCash?'可查询年报'+reportingCurrency+'财务数字、自由现金流及同比，以及价格、业务与披露风险。':annualNumbers?'可查询年报'+reportingCurrency+'营收、同比、利润率、普通股每股收益，以及价格、业务与披露风险。':annualText?'可查询价格与长期走势，以及年报原文中的业务描述和披露风险。财务数字仍待接入。':'选择一个问题，或写下你关心的经营表现。';
  $('#question').placeholder=pricesOnly?'例如：现在股价是多少？过去五年涨跌和最大回撤是多少？':annualNumbers?'例如：年报营收同比和毛利率是多少？普通股稀释每股收益是多少？':annualText?'例如：年报披露了哪些主要风险？公司的主要业务是什么？':'例如：这家公司经营表现如何？利润增长是否转化为现金流？';
  $('.research-hint strong').textContent=pricesOnly?'查看价格与历史表现':'从价格，走向公司基本面';
  $('.research-hint p').textContent=pricesOnly?'选择上方价格问题查看区间涨跌和回撤。当前证券的财报来源尚未接入。':annualCash?'年度数字从原始 20-F 提取并由代码计算，可展开出处。自由现金流采用经营现金流减现金 PP&E 支付毛额，未抵减政府补助。季度 6-K、净资本开支与美元 ADS 估值仍待接入。':annualNumbers?'年度数字从原始 20-F 提取并由代码计算，可展开出处。季度 6-K、自由现金流与美元 ADS 估值仍待接入；普通股 EPS 不混用于 ADS 估值。':annualText?'自动获取 20-F 年报原文后可查询业务与披露风险，并展开出处；财务数字、6-K 季度公告与完整估值仍待接入。':'在研究提问中查看营收、利润率、现金流或近期公告。每个结果都可展开出处。';
  if(annualADS){$('.research-intro').textContent+=' 也可查询年报美元 ADS EPS 口径的年度市盈率。';$('.research-hint p').textContent='年度数字与现金流从原始 20-F 提取。年度市盈率使用原件单独披露的美元 ADS 稀释 EPS，美元采用年报固定汇率的便利换算，不是 TTM 或当前汇率重算。现金流为购置毛额口径；季度、净资本开支和 TTM ADS 口径仍待接入。';}
  if(capabilities.company_updates&&capabilities.update_form==='6-K'){$('.research-intro').textContent+=' 可直接查近 90 天的 6-K 官方公告。';$('.research-hint p').textContent=$('.research-hint p').textContent.replace('季度、净资本开支和 TTM ADS 口径仍待接入。','净资本开支和 TTM ADS 口径仍待接入。')+' 6-K 公告提供原文摘录及日期，无需先下载年报；季度财务计算与 TTM 重建仍待接入。';}
  $('#capability-note').textContent=capabilities.scope+' 正在扩展：'+capabilities.pending.join('、')+'。';
  const questions=[...capabilities.questions];
  const preferred=[/股票.*怎么样/,/TTM.*利润率/i,/现金流/,/市盈率/,/公告/];
  const featured=[];
  preferred.forEach(pattern=>{if(featured.length>=4)return;const index=questions.findIndex(q=>pattern.test(q));if(index>=0)featured.push(...questions.splice(index,1));});
  while(featured.length<Math.min(4,capabilities.questions.length))featured.push(questions.shift());
  const button=q=>{const b=el('button','secondary',q);b.disabled=!!state.researchRunning;b.addEventListener('click',()=>{if(q==='近期公司公告中文摘要'){$('#mode').value='agent';$('#mode').dispatchEvent(new Event('change'));}$('#question').value=typeof FilingI18n==='undefined'?q:FilingI18n.translate(q);$('#question').focus();updateQuestionPlan();});return b;};
  const root=$('#question-presets');root.replaceChildren(...featured.map(button));
  if(questions.length){const more=el('details','preset-more'),list=el('div');list.append(...questions.map(button));more.append(el('summary','','更多研究问题（'+questions.length+'）'),list);root.append(more);}
}
async function initMarket(){
  const capabilities=await api('/api/capabilities?'+new URLSearchParams({ticker:$('#ticker').value}));
  renderCapabilities(capabilities);
  $('#chart-periods').replaceChildren(...capabilities.chart_periods.map(period=>{const b=el('button',period===marketState.period?'active':'',periodLabels[period]);b.dataset.period=period;b.setAttribute('aria-pressed',String(period===marketState.period));b.addEventListener('click',()=>{marketState.period=period;loadMarket();});return b;}));
  await loadMarket();
}
$('#ticker').addEventListener('change',loadMarket);$('#as-of').addEventListener('change',loadMarket);
$('#market-refresh').addEventListener('click',()=>loadMarket(true));$('#chart-scale').addEventListener('change',drawMarketChart);
$('#market-stop').addEventListener('click',stopMarketWaiting);
if(typeof ResizeObserver!=='undefined')new ResizeObserver(drawMarketChart).observe($('#price-chart'));
