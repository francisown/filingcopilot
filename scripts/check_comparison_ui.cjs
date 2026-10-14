// Real comparison rendering and races with DOM/network doubles. Live browser QA is separate.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function node(tag='div'){
  const classes=new Set();return {tag,value:'',textContent:'',children:[],dataset:{},attributes:{},disabled:false,checked:true,clientWidth:700,style:{setProperty(){}},
    classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x),toggle:(x,on)=>on?classes.add(x):classes.delete(x)},
    append(...items){this.children.push(...items);},replaceChildren(...items){this.children=items;},setAttribute(k,v){this.attributes[k]=String(v);},
    querySelectorAll(){return this.children.filter(n=>n.tag==='button');},addEventListener(t,fn){this[t]=fn;},getBoundingClientRect(){return {left:0,width:700};}};
}
function deferred(){let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};}
function harness(){
  const nodes=new Map(),calls=[],pending=[],sources=[];
  const select=s=>{if(!nodes.has(s))nodes.set(s,node());return nodes.get(s);};
  select('#as-of').value='2026-10-08';select('#comparison-period').value='5Y';select('#comparison-scale').value='linear';
  const context=vm.createContext({$:select,el:(tag,cls,text)=>{const n=node(tag);n.className=cls;n.textContent=text??'';return n;},svgEl:(tag,attrs,text)=>{const n=node(tag);n.attributes=attrs;n.textContent=text??'';return n;},state:{health:{version:'0.45.0'}},periodLabels:{'1Y':'1年','5Y':'5年',MAX:'全部'},showEvidence:key=>sources.push(key),ResizeObserver:class {observe(){}},setTimeout,clearTimeout,
    api:async(path,options)=>{const request=deferred();calls.push({path,options});pending.push(request);return request.promise;}});
  vm.runInContext(fs.readFileSync('src/filingcopilot/static/format.js','utf8'),context);
  vm.runInContext(fs.readFileSync('src/filingcopilot/static/comparison.js','utf8'),context);
  return {context,select,calls,pending,sources,run:s=>vm.runInContext(s,context)};
}
function sample(){
  const tickers=['MSFT','AMZN'],dates=['2026-10-05','2026-10-06','2026-10-07'];
  const evidence=tickers.flatMap((ticker,i)=>[{id:'market'+i,kind:'market',ticker,snapshot:{name:ticker,ticker,currency:'USD',quote_value:'123.456789',quote_at:1791316800,exchange_timezone:'UTC',stale:false}},
    {id:'rev'+i,kind:'fact',ticker,value:'100500000',unit:'USD',start:'2025-01-01',end:'2025-12-31',fiscal_year:2025,filing:{filed:'2026-02-01'}}]);
  return {id:'run-comparison-fixture',mode:'comparison',as_of:'2026-10-08',elapsed_ms:20,evidence,trace:[{stage:'company_comparison',result:{tickers,period:'5Y',note:'Source periods explicit',rows:tickers.map((ticker,i)=>({ticker,market_id:'market'+i,annual_basis_id:'rev'+i,cells:{revenue:'rev'+i},issues:[]})),chart:{found:true,basis:'adjusted_close',start:dates[0],end:dates[2],common_days:3,series:tickers.map((ticker,i)=>({ticker,currency:i?'GBP':'USD',change_pct:'20.1234567',omitted_days:0,points:dates.map((date,j)=>({date,value:String(100+j*10),price:String(50+j*5)}))}))}}}]};
}
async function check(){
  const h=harness();h.context.renderComparison(sample());
  assert.equal(h.context.comparisonAmount({value:'100500000',unit:'USD'}),'1 亿 USD');
  assert.equal(h.context.comparisonAmount({value:'101500000',unit:'USD'}),'1.02 亿 USD');
  assert.equal(h.context.comparisonAmount({value:'1.015e8',unit:'USD'}),'1.02 亿 USD');
  const body=h.select('#comparison-table').children[1];assert.equal(body.children[1].children[1].children[0].textContent,'1 亿 USD');
  assert.equal(body.children[2].children[1].children[0].textContent,'—','missing growth must not be zero');
  assert.equal(h.select('#comparison-legend').children[1].children[1].textContent,'20.12% · GBP');
  h.select('#comparison-chart').onkeydown({key:'End',preventDefault(){}});
  assert.equal(h.select('#comparison-tooltip').children[0].textContent,'2026-10-07');
  h.select('#comparison-chart').onkeydown({key:'Home',preventDefault(){}});
  assert.equal(h.select('#comparison-tooltip').children[0].textContent,'2026-10-05');
  const missing=sample();missing.trace[0].result.chart={found:false,reason:'No common dates',series:[]};h.context.renderComparison(missing);
  assert.equal(h.select('#comparison-chart').classList.contains('hidden'),true);
  assert.equal(h.select('#comparison-chart').onkeydown,null,'no stale handler on missing chart');
  assert.equal(h.select('#comparison-window').textContent,'No common dates');
  assert.equal(h.select('#comparison-scale').disabled,true,'missing series disables scale control');
  assert.equal(h.select('#comparison-chart').onpointerdown,null,'missing series clears touch inspection');
  // Equal percentage changes have equal log heights; all series retain their source values.
  const scaling=harness(),growth=sample();growth.trace[0].result.chart.series[0].points.forEach((point,i)=>point.value=String(100*2**i));
  const original=JSON.stringify(growth);scaling.context.renderComparison(growth,true);
  const pathPoints=()=>scaling.select('#comparison-chart').children.find(n=>n.tag==='path').attributes.d.match(/[ML][^ML]+/g).map(p=>p.slice(1).split(',').map(Number));
  const linear=pathPoints();assert.ok(Math.abs((linear[1][1]-linear[2][1])/(linear[0][1]-linear[1][1])-2)<.001,'linear spacing follows index changes');
  scaling.select('#comparison-chart').onkeydown({key:'End',preventDefault(){}});
  const beforeTooltip=scaling.select('#comparison-tooltip').children.map(n=>n.textContent);
  scaling.select('#comparison-scale').value='log';scaling.select('#comparison-scale').change();
  const log=pathPoints();assert.ok(Math.abs((log[0][1]-log[1][1])-(log[1][1]-log[2][1]))<.02,'log spacing follows percentage changes');
  assert.match(scaling.select('#comparison-chart').attributes['aria-label'],/对数刻度/);
  assert.match(scaling.select('#comparison-chart-note').textContent,/相同百分比变化/);
  assert.deepEqual(scaling.select('#comparison-tooltip').children.map(n=>n.textContent),beforeTooltip,'changing scale retains the inspected date and original prices');
  assert.equal(JSON.stringify(growth),original,'scale changes must not mutate frozen research or export values');assert.equal(scaling.calls.length,0,'scale changes must not request market data');
  const baseline=scaling.select('#comparison-chart').children.find(n=>n.attributes['data-kind']==='baseline');
  assert.equal(Number(baseline.attributes.y1).toFixed(2),log[0][1].toFixed(2),'index 100 baseline passes through every initial point');
  assert.equal(scaling.select('#comparison-chart').children.filter(n=>n.tag==='circle').length,2);
  scaling.select('#comparison-chart').onpointerdown({clientX:368});assert.equal(scaling.select('#comparison-tooltip').children[0].textContent,'2026-10-06','pointer-down selects nearest actual date');
  scaling.select('#comparison-scale').value='linear';scaling.select('#comparison-scale').change();assert.deepEqual(pathPoints(),linear,'returning to linear restores coordinates');
  // A flat series and very large long-history indexes must still render finite, readable axes.
  const constant=sample();constant.trace[0].result.chart.series.forEach(series=>series.points.forEach(p=>p.value='100'));scaling.select('#comparison-scale').value='log';scaling.context.renderComparison(constant);
  assert.ok(pathPoints().every(point=>point.every(Number.isFinite)),'flat log chart must have nonzero scale range');assert.equal(scaling.select('#comparison-scale').disabled,false);
  const large=sample();large.trace[0].result.chart.series[0].points[2].value='1000000';scaling.context.renderComparison(large);
  assert.ok(scaling.select('#comparison-chart').children.some(n=>n.tag==='text'&&n.textContent.includes('万')),'large axis labels should not overflow left margin');
  // A stored comparison opened while a request is pending must not be replaced by that request.
  const r=harness(),running=r.context.runComparison();assert.equal(r.select('#comparison-run').disabled,true);
  const frozen=sample();frozen.id='run-frozen';r.context.renderComparison(frozen,true);r.pending[0].resolve(sample());await running;
  assert.equal(r.run('comparisonState.result.id'),'run-frozen');
  assert.match(r.select('#comparison-status').textContent,/保存的对比快照/);
  const range=harness();range.context.renderComparison(sample());const button=range.select('#comparison-ranges').children[0];const waiting=button.click();
  assert.equal(JSON.parse(range.calls[0].options.body).refresh_sources,false,'changing chart range must not reacquire annual files');
  range.pending[0].resolve(sample());await waiting;
  const search=harness();search.select('#comparison-query').value='AAPL';const lookup=search.context.searchComparison();search.select('#comparison-query').value='MSFT';search.select('#comparison-query').input();search.pending[0].resolve({match_count:1,candidates:[{ticker:'AAPL'}]});await lookup;
  assert.equal(search.select('#comparison-matches').children.length,0,'stale search must not offer previous company');
  const history=harness(),app=fs.readFileSync('src/filingcopilot/static/app.js','utf8');history.context.labels={partial:'部分回答',answered:'回答完成'};
  vm.runInContext(app.slice(app.indexOf('const labels ='),app.indexOf('const stageNames =')),history.context);
  history.context.changeView=name=>assert.equal(name,'comparison');history.context.clearCompanySelection=()=>assert.fail('Opening comparison must not alter single-company research controls');
  vm.runInContext(app.slice(app.indexOf('function renderResult'),app.indexOf('let researchTimer')),history.context);
  const historyStart=app.indexOf('async function loadHistory');vm.runInContext(app.slice(historyStart,app.indexOf('\n',historyStart)),history.context);
  history.select('#mode').value='agent';const loading=history.context.loadHistory();const record=sample();record.question='Two-company comparison';record.status='answered';history.pending[0].resolve([record]);await loading;history.select('#history').children[0].click();
  assert.equal(history.select('#mode').value,'agent');assert.equal(history.run('comparisonState.result.id'),record.id);
  // Benchmark is a separate fifth series and source, never a financial column.
  const b=harness(),bundle=sample(),data=bundle.trace[0].result;
  data.benchmark={ticker:'SPY',name:'标普 500 ETF',currency:'USD',source_url:'https://www.ssga.com/spy',market_id:'benchmark-source',issues:[]};
  const fund={...data.chart.series[0],ticker:'SPY',market_id:'benchmark-source',change_pct:'10.987654321'};
  data.chart.series.push(fund);data.chart.relative=data.rows.map(row=>({ticker:row.ticker,benchmark:'SPY',market_id:row.market_id,benchmark_market_id:'benchmark-source',difference_pp:'9.135802379'}));
  bundle.evidence.push({id:'benchmark-source',kind:'market',ticker:'SPY',snapshot:{ticker:'SPY',currency:'USD',quote_value:'600.123456'}});
  const frozenBenchmark=JSON.stringify(bundle);b.context.renderComparison(bundle,true);
  assert.equal(b.select('#comparison-benchmark').value,'SPY');assert.equal(b.select('#comparison-benchmark-summary').classList.contains('hidden'),false);
  const summary=b.select('#comparison-benchmark-summary');assert.ok(summary.children.some(n=>n.textContent.includes('9.14')),'percentage points display at most two decimals');
  const source=summary.children.find(n=>n.tag==='button');source.click();assert.equal(b.sources.at(-1).id,'benchmark-source');
  assert.equal(b.select('#comparison-table').children[0].children[0].children.length,3,'fund must not become an annual financial column');
  const benchmarkPath=()=>b.select('#comparison-chart').children.find(n=>n.attributes['data-kind']==='benchmark');
  assert.ok(benchmarkPath().attributes['stroke-dasharray'],'benchmark curve has distinct dash style');
  assert.match(b.select('#comparison-legend').children.at(-1).children[0].textContent,/ETF/);
  b.select('#comparison-benchmark').value='QQQ';b.select('#comparison-benchmark').change();assert.equal(JSON.stringify(bundle),frozenBenchmark,'changing future input must not rewrite saved chart');
  b.context.renderComparison(sample(),true);assert.equal(b.select('#comparison-benchmark').value,'');assert.equal(summary.classList.contains('hidden'),true,'old v1 history clears benchmark controls and summary');
  const single=harness();single.run("comparisonState.selected=[{ticker:'MSFT'}];comparisonSelection()");assert.equal(single.select('#comparison-run').disabled,true);
  single.select('#comparison-benchmark').value='SPY';single.select('#comparison-benchmark').change();assert.equal(single.select('#comparison-run').disabled,false,'single listing becomes runnable only with benchmark');
  const request=single.context.runComparison();assert.equal(JSON.parse(single.calls[0].options.body).benchmark,'SPY');single.pending[0].resolve(bundle);await request;
  const ranged=b.context.renderComparison(bundle,true),rangeClick=b.select('#comparison-ranges').children[0].click();assert.equal(JSON.parse(b.calls[0].options.body).benchmark,'SPY');assert.equal(JSON.parse(b.calls[0].options.body).refresh_sources,false);b.pending[0].resolve(bundle);await rangeClick;
  const five=sample();five.trace[0].result.benchmark=data.benchmark;five.trace[0].result.chart.series.push({...fund,ticker:'NVDA'},{...fund,ticker:'AAPL'},fund);five.trace[0].result.chart.relative=[];b.context.renderComparison(five,true);
  assert.equal(b.select('#comparison-chart').children.filter(n=>n.tag==='circle').length,5);assert.equal(b.select('#comparison-legend').children.length,5);
  const failure=structuredClone(bundle);failure.trace[0].result.benchmark.market_id=null;failure.trace[0].result.benchmark.issues=[{message:'基准连接失败，请重试'}];failure.trace[0].result.chart={found:false,reason:'No common dates',series:[]};b.context.renderComparison(failure,true);assert.ok(b.select('#comparison-benchmark-summary').children.some(n=>n.textContent.includes('基准连接失败')));
  const prices=structuredClone(bundle);prices.trace[0].result.rows.forEach(row=>{row.annual_basis_id=null;row.cells={};});b.context.renderComparison(prices,true);assert.equal(b.select('#comparison-fundamentals').classList.contains('hidden'),true);assert.equal(b.select('#comparison-price-summary').classList.contains('hidden'),false,'price-only comparison must not show eleven empty financial rows');const quote=b.select('#comparison-price-summary').children.find(n=>n.tag==='button');quote.click();assert.equal(b.sources.at(-1).id,'market0');assert.match(quote.textContent,/123\.46 USD/);
  b.context.renderComparison(bundle,true);assert.equal(b.select('#comparison-fundamentals').classList.contains('hidden'),false);assert.equal(b.select('#comparison-price-summary').classList.contains('hidden'),true,'annual table returns when any company has reported figures');
  // Optional TTM has its own table and labels, independent of each annual FY.
  const valuation=harness(),valued=sample(),vdata=valued.trace[0].result;
  valuation.context.state.health.as_of_default='2026-10-08';
  vdata.schema_version='filingcopilot.company-comparison/v3';vdata.include_ttm_valuation=true;
  vdata.rows.forEach((row,i)=>{
    const eps={id:'ttm-eps'+i,kind:'valuation',ticker:row.ticker,snapshot:{value:'4.55555',currency:i?'EUR':'USD',period_end_label:i?'2026-09-30':'2026-06-30',retrieved_at:'2026-10-08T10:00:00.123456+08:00',stale:!!i}};
    const pe={id:'ttm-pe'+i,kind:'calculation',ticker:row.ticker,value:'27.12345678',unit:'倍',inputs:[valued.evidence.find(e=>e.id===row.market_id),eps]};
    valued.evidence.push(eps,pe);row.cells.provider_ttm_eps=eps.id;row.cells.provider_ttm_pe=pe.id;
  });
  const valuedBefore=JSON.stringify(valued);valuation.context.renderComparison(valued,true);
  assert.equal(valuation.select('#comparison-valuations').classList.contains('hidden'),false);
  const vbody=valuation.select('#comparison-valuation-table').children[1];
  assert.equal(vbody.children.length,3,'TTM rows must be separate from annual metrics');
  assert.equal(valuation.select('#comparison-table').children[1].children.length,12,'TTM must not inherit annual column dates');
  assert.equal(vbody.children[1].children[1].children[0].textContent,'4.56 USD/股');
  assert.equal(vbody.children[1].children[2].children[0].textContent,'4.56 EUR/股','supplier EPS currency must not come from price currency');
  assert.match(vbody.children[1].children[1].children[1].textContent,/2026-06-30/);
  assert.match(vbody.children[1].children[2].children[1].textContent,/2026-09-30/);
  assert.match(vbody.children[1].children[2].children[2].textContent,/陈旧缓存/);
  assert.ok(!vbody.children[1].children[1].children[2].textContent.includes('.123456'),'timestamp fractional seconds are compact for display');
  assert.equal(vbody.children[2].children[1].children[0].textContent,'27.12 倍');
  vbody.children[2].children[1].children[0].click();assert.equal(valuation.sources.at(-1).id,'ttm-pe0');
  assert.equal(JSON.stringify(valued),valuedBefore,'render must not round or change frozen EPS/calculation evidence');
  valuation.select('#comparison-ttm').checked=false;valuation.select('#comparison-ttm').change();assert.equal(JSON.stringify(valued),valuedBefore,'next-run option must not rewrite current snapshot');
  const failed=structuredClone(valued);delete failed.trace[0].result.rows[1].cells.provider_ttm_pe;valuation.context.renderComparison(failed,true);
  assert.equal(valuation.select('#comparison-valuation-table').children[1].children[2].children[2].children[0].textContent,'—','missing multiple must not become zero');
  const noAnnual=structuredClone(valued);noAnnual.trace[0].result.rows.forEach(row=>{row.annual_basis_id=null;row.cells={provider_ttm_eps:row.cells.provider_ttm_eps,provider_ttm_pe:row.cells.provider_ttm_pe};});valuation.context.renderComparison(noAnnual,true);
  assert.equal(valuation.select('#comparison-fundamentals').classList.contains('hidden'),true);
  assert.equal(valuation.select('#comparison-valuations').classList.contains('hidden'),false,'TTM does not require an annual download');
  const option=valuation.context.runComparison();assert.equal(JSON.parse(valuation.calls[0].options.body).include_ttm_valuation,true);valuation.pending[0].resolve(valued);await option;
  valuation.select('#comparison-as-of').value='2026-10-07';valuation.select('#comparison-as-of').change();
  assert.equal(valuation.select('#comparison-ttm').checked,false);assert.equal(valuation.select('#comparison-ttm').disabled,true,'historical query must not use current supplier EPS');
  assert.match(valuation.select('#comparison-ttm-help').textContent,/历史发布版本/);
  valuation.context.renderComparison(sample(),true);
  assert.equal(valuation.select('#comparison-valuations').classList.contains('hidden'),true,'old v1/v2 snapshot clears TTM view');
  assert.equal(valuation.select('#comparison-valuation-table').children.length,0,'old history must not retain previous TTM cells');
  const legacy=harness();legacy.context.state.health={version:'0.44.0',as_of_default:'2026-10-08'};legacy.run('comparisonSelection()');
  assert.equal(legacy.select('#comparison-ttm').disabled,true);assert.equal(legacy.select('#comparison-ttm').checked,false);
  assert.match(legacy.select('#comparison-ttm-help').textContent,/当前服务不支持/);
  const oldRequest=legacy.context.runComparison();assert.ok(!Object.hasOwn(JSON.parse(legacy.calls[0].options.body),'include_ttm_valuation'),'old server must receive its old request shape');legacy.pending[0].resolve(sample());await oldRequest;
  delete legacy.context.state.health.version;legacy.run('comparisonSelection()');assert.equal(legacy.select('#comparison-ttm').disabled,true,'wait for server support before enabling new fields');
  legacy.context.state.health.version='0.45.0';legacy.run('comparisonSelection()');assert.equal(legacy.select('#comparison-ttm').disabled,false,'new service enables the option after health arrives');
  console.log(JSON.stringify({checks:45,status:'passed',scope:'Actual comparison JavaScript/rounding/scales/touch/immutability/races/history/ETF sources/five curves/price-only/optional independently dated TTM/server version compatibility with DOM and network doubles; live provider/browser checks recorded separately.'}));
}
check().catch(e=>{console.error(e);process.exitCode=1;});
