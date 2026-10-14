// Controlled real UI functions; this does not establish provider or model accuracy.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const code=fs.readFileSync('src/filingcopilot/static/research-extra.js','utf8');
function node(tag='div',cls='',text=''){
  return {tag,cls,textContent:text,value:'',disabled:false,children:[],classList:{add(){},remove(){}},
    append(...items){this.children.push(...items);},addEventListener(){},querySelector(){return null;}};
}
function harness(){
  const nodes=new Map(),calls=[],busy=[],results=[];
  const select=s=>{if(!nodes.has(s))nodes.set(s,node());return nodes.get(s);};
  const cases=[['Case A','0','10'],['Case B','10','20']].map(values=>({querySelector:s=>({value:values[['.scenario-name','.scenario-growth','.scenario-pe'].indexOf(s)]})}));
  const context={$:select,el:node,state:{researchRunning:false,health:{research_jobs:true}},
    document:{querySelectorAll:()=>cases},marketNumber:x=>Number(x).toFixed(2),
    researchBusy:on=>{busy.push(on);context.state.researchRunning=on;select('#scenario-run').disabled=on;},
    researchError:text=>select('#error').textContent=text,
    researchJobApi:async(path,options)=>{calls.push({path,body:JSON.parse(options.body)});return {ready:true};},
    startResearchJob:async request=>calls.push({request}),
    api:async(path,options)=>{calls.push({path,body:JSON.parse(options.body)});return {id:'run-scenario'};},
    renderResult:r=>results.push(r)};
  vm.createContext(context);vm.runInContext(code,context);return {context,select,calls,busy,results};
}
async function check(){
  const full=harness();full.select('#ticker').value='AMZN';full.select('#as-of').value='2026-10-10';full.select('#mode').value='agent';
  await full.context.startCompanyReport();assert.equal(full.calls[0].body.depth,'full');assert.equal(full.calls[0].body.auto_acquire,true);assert.equal(full.calls[1].request.ticker,'AMZN');
  await full.context.startCompanyReport();assert.equal(full.calls.length,2,'Do not submit twice while running');
  const empty=harness();await empty.context.startCompanyReport();assert.equal(empty.calls.length,0);assert.match(empty.select('#error').textContent,/选择/);
  const scenario=harness();scenario.select('#ticker').value='AMZN';scenario.select('#as-of').value='2026-10-10';scenario.select('#scenario-years').value='3';scenario.select('#scenario-discount').value='10';scenario.select('#scenario-basis').value='annual';
  await scenario.context.runScenarios();assert.equal(scenario.calls[0].path,'/api/valuation/scenarios');assert.deepEqual(scenario.busy,[true,false]);assert.equal(scenario.calls[0].body.cases[1].growth_pct,'10');assert.equal(scenario.calls[0].body.years,3);assert.equal(scenario.results[0].id,'run-scenario');
  const root=node();scenario.context.renderScenarios({trace:[{stage:'valuation_scenarios',result:{eps_period:'FY2025',currency:'USD',observed_price:'100.12345',note:'显式假设',price_reference:{period:'5Y',basis:'adjusted_close',start:'2021-10-08',end:'2026-10-08',trading_days:3,low:'80',p25:'90',median:'100',p75:'110',high:'120',note:'不是历史市盈率'},request:{years:3,discount_pct:'10'},formula:'EPS formula',rows:[{name:'Case A',growth_pct:'10',terminal_pe:'20',future_eps:'8.12345',discounted_value:'90.12345',price_difference_pct:'-9.87655'}]}}]},root);
  const text=n=>[n.textContent,...n.children.map(text)].join(' ');assert.match(text(root),/90.12 USD/);assert.doesNotMatch(text(root),/90.12345/);assert.match(text(root),/未计股息/);assert.match(text(root),/EPS formula/);
  assert.match(text(root),/历史价格参照/);assert.match(text(root),/低 80\.00/);assert.match(text(root),/不是历史市盈率/);
  console.log(JSON.stringify({checks:14,scope:'Controlled request routing, double-submit guard, explicit assumptions and rounded display; no model accuracy claim'}));
}
check().catch(error=>{console.error(error);process.exitCode=1;});
