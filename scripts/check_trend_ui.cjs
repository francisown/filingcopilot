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
  const nodes=new Map(),calls=[],pending=[];
  const select=s=>{if(!nodes.has(s))nodes.set(s,node());return nodes.get(s);};
  select('#as-of').value='2026-10-08';select('#comparison-period').value='5Y';select('#comparison-scale').value='linear';
  const context=vm.createContext({$:select,el:(tag,cls,text)=>{const n=node(tag);n.className=cls;n.textContent=text??'';return n;},svgEl:(tag,attrs,text)=>{const n=node(tag);n.attributes=attrs;n.textContent=text??'';return n;},state:{health:{}},periodLabels:{'1Y':'1年','5Y':'5年',MAX:'全部'},showEvidence(){},ResizeObserver:class {observe(){}},setTimeout,clearTimeout,
    api:async(path,options)=>{const request=deferred();calls.push({path,options});pending.push(request);return request.promise;}});
  vm.runInContext(fs.readFileSync('src/filingcopilot/static/format.js','utf8'),context);
  vm.runInContext(fs.readFileSync('src/filingcopilot/static/comparison.js','utf8'),context);
  return {context,select,calls,pending,run:s=>vm.runInContext(s,context)};
}
function trendHarness(){const h=harness();h.select('#trend-metric').value='revenue';h.select('#trend-years').value='5';h.context.sourceClicks=[];h.context.showEvidence=e=>h.context.sourceClicks.push(e.id);vm.runInContext(fs.readFileSync('src/filingcopilot/static/trend.js','utf8'),h.context);return h;}
function annualSample(){
 const values=['100500000','0',null,'-50000000','200500000'],evidence=[];
 const rows=values.map((value,i)=>{const year=2021+i,id='rev'+year;if(value!==null)evidence.push({id,kind:'fact',ticker:'AMZN',metric:'revenue',fiscal_year:year,value,unit:'USD',start:(year-1)+'-12-29',end:year+'-12-28',filing:{filed:'2026-02-06',form:'10-K',accession:'test-'+year}});return {fiscal_year:year,basis_id:value===null?null:id,cells:value===null?{}:{revenue:id},missing:value===null?['revenue']:[],issues:[]};});
 return {id:'run-trend-fixture',mode:'trend',as_of:'2026-10-08',elapsed_ms:10,status:'partial',question:'AMZN annual financial trends',evidence,gaps:['Missing years are not zero'],trace:[{stage:'financial_trend',result:{ticker:'AMZN',years:5,as_of:'2026-10-08',latest_fiscal_year:2025,rows,note:'Explicit source annual windows'}}]};
}
async function check(){
 const h=trendHarness(),fixture=annualSample(),raw=JSON.stringify(fixture);h.context.renderTrend(fixture,true);
 const bars=()=>h.select('#trend-chart').children.filter(n=>n.tag==='rect');
 assert.equal(bars().length,4,'missing FY remains a gap, not a zero bar');assert.equal(bars()[1].attributes.height,1,'observed zero remains a visible zero marker');
 assert.equal(bars()[2].attributes.fill,'#ac5960','negative annual profit can render below zero');
 const zero=h.select('#trend-chart').children.find(n=>n.attributes['data-kind']==='zero-baseline');assert.equal(Number(bars()[2].attributes.y),Number(zero.attributes.y1));
 const body=h.select('#trend-table').children[1];assert.equal(body.children[0].children[1].children[0].textContent,'1 亿 USD');assert.equal(body.children[0].children[2].children[0].textContent,'0 USD');assert.equal(body.children[0].children[3].children[0].textContent,'—');
 bars()[0].click();assert.equal(h.context.sourceClicks[0],'rev2021');
 h.select('#trend-chart').onkeydown({key:'Home',preventDefault(){}});assert.match(h.select('#trend-tooltip').textContent,/FY2021/);assert.match(h.select('#trend-tooltip').textContent,/2020-12-29 — 2021-12-28/);
 h.select('#trend-chart').onkeydown({key:'End',preventDefault(){}});assert.match(h.select('#trend-tooltip').textContent,/FY2025/);
 h.select('#trend-chart').onpointerdown({clientX:-10});assert.match(h.select('#trend-tooltip').textContent,/FY2021/);h.select('#trend-chart').onpointerdown({clientX:1000});assert.match(h.select('#trend-tooltip').textContent,/FY2025/);
 assert.equal(JSON.stringify(fixture),raw,'graph interactions preserve frozen source precision and metadata');assert.equal(h.calls.length,0);
 h.select('#trend-metric').value='free_cash_flow';h.select('#trend-metric').change();assert.equal(h.select('#trend-chart').classList.contains('hidden'),true);assert.equal(h.select('#trend-chart').onkeydown,null);assert.equal(h.select('#trend-chart').onpointerdown,null);assert.match(h.select('#trend-tooltip').textContent,/切换指标/);
 const fx=annualSample();fx.evidence.find(e=>e.fiscal_year===2024).unit='GBP';h.select('#trend-metric').value='revenue';h.context.renderTrend(fx,true);assert.equal(bars().length,3,'different currencies do not share a series');assert.equal(h.select('#trend-unit').disabled,false);
 h.select('#trend-unit').value='GBP';h.select('#trend-unit').change();assert.equal(bars().length,1);assert.match(h.select('#trend-chart-note').textContent,/未换汇/);assert.match(h.select('#trend-tooltip').textContent,/当前选择 GBP/);assert.equal(h.calls.length,0,'metric and currency toggles are local');
 const r=trendHarness(),waiting=r.context.runTrend();assert.equal(JSON.parse(r.calls[0].options.body).years,5);assert.equal(r.select('#trend-run').disabled,true);const frozen=annualSample();frozen.id='run-frozen';r.context.renderTrend(frozen,true);r.pending[0].resolve(annualSample());await waiting;assert.equal(r.run('trendState.result.id'),'run-frozen');
 const late=trendHarness(),pending=late.context.runTrend();late.select('#trend-query').value='Microsoft';late.select('#trend-query').input();late.pending[0].resolve(annualSample());await pending;assert.equal(late.run('trendState.selected'),null);assert.equal(late.select('#trend-result').classList.contains('hidden'),true);assert.equal(late.select('#trend-run').disabled,true);
 const search=trendHarness();search.select('#trend-query').value='AMZN';const lookup=search.context.searchTrend();search.select('#trend-query').value='NVDA';search.select('#trend-query').input();search.pending[0].resolve({match_count:1,candidates:[{ticker:'AMZN'}]});await lookup;assert.equal(search.select('#trend-matches').children.length,0);
 const failure=trendHarness();failure.context.renderTrend(annualSample());failure.context.api=async()=>{throw Error('controlled offline')};await failure.context.runTrend();assert.match(failure.select('#trend-status').textContent,/已有趋势记录仍可查看/);assert.equal(failure.select('#trend-run').disabled,false);assert.equal(failure.run('trendState.result.id'),'run-trend-fixture');
 const history=trendHarness(),app=fs.readFileSync('src/filingcopilot/static/app.js','utf8');history.context.labels={partial:'部分回答'};vm.runInContext(app.slice(app.indexOf('const labels ='),app.indexOf('const stageNames =')),history.context);history.context.changeView=name=>assert.equal(name,'trend');history.context.clearCompanySelection=()=>assert.fail('History trend must not change single-stock controls');vm.runInContext(app.slice(app.indexOf('function renderResult'),app.indexOf('let researchTimer')),history.context);const start=app.indexOf('async function loadHistory');vm.runInContext(app.slice(start,app.indexOf('\n',start)),history.context);history.select('#mode').value='agent';const load=history.context.loadHistory();history.pending[0].resolve([annualSample()]);await load;history.select('#history').children[0].click();assert.equal(history.select('#mode').value,'agent');assert.equal(history.run('trendState.result.id'),'run-trend-fixture');
 console.log(JSON.stringify({checks:15,status:'passed',scope:'Actual annual trend rendering through DOM/network doubles; negative/zero/gap/currency/citation/race/history checks. Real browser/source acceptance is separate.'}));
}
check().catch(e=>{console.error(e);process.exitCode=1;});
