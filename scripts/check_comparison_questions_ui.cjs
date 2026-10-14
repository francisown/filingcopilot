// Execute the shipped research click handler with controlled DOM/network doubles.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {createHash}=require('node:crypto');
const app=fs.readFileSync('src/filingcopilot/static/app.js','utf8');
const market=fs.readFileSync('src/filingcopilot/static/market.js','utf8');
const planner=market.slice(market.indexOf('let questionPlanGeneration='),market.indexOf("$('#question').addEventListener('input'"));
const handler=app.slice(app.indexOf("$('#research').addEventListener('click',async()=>{"),app.indexOf('async function loadHistory()'));
function harness(plan,jobs=true){
  const nodes=new Map(),calls=[],requests=[],rendered=[],errors=[];let lookups=0;
  const select=s=>{if(!nodes.has(s)){const hidden=new Set();nodes.set(s,{value:'',textContent:'',classList:{add:k=>hidden.add(k),remove:k=>hidden.delete(k),contains:k=>hidden.has(k),toggle:(k,on)=>on?hidden.add(k):hidden.delete(k)},replaceChildren(...items){this.children=items;},append(...items){this.children.push(...items);},scrollIntoView(){},focus(){},addEventListener(t,fn){this[t]=fn;}});}return nodes.get(s);};
  select('#question').value='比较微软和苹果的 TTM 每股收益和市盈率，近五年走势';select('#ticker').value='NVDA';select('#as-of').value='2026-10-10';select('#mode').value='agent';
  const context=vm.createContext({$:select,state:{health:{research_jobs:jobs}},companyState:{selected:{ticker:'NVDA',filings_supported:true}},
    el:(tag,cls,text)=>({tag,text,addEventListener(){}}),
    researchBusy(value){context.state.researchRunning=value;},researchError(message){errors.push(message);},
    researchJobApi:async(path,options)=>{calls.push({path,body:JSON.parse(options.body)});return plan;},
    ensureCompany:async()=>{lookups++;},marketQuestion:()=>false,updateQuestionPlan:async()=>{},
    startResearchJob:async request=>requests.push(request),renderResult:result=>rendered.push(result),
    api:async(path,options)=>{calls.push({path,body:JSON.parse(options.body)});return path==='/api/research/plan'?plan:{id:'run-question',mode:'comparison'};},
  });
  vm.runInContext(handler,context);return {select,context,calls,requests,rendered,errors,lookups:()=>lookups};
}
async function check(){
  const multiple=harness({ready:true,kind:'company_comparison'});await multiple.select('#research').click();
  assert.equal(multiple.lookups(),0,'an unrelated or unresolved desk selection must not gate explicit companies');
  assert.equal(multiple.requests.length,1);assert.equal(multiple.requests[0].auto_acquire,false);
  assert.equal(multiple.requests[0].question,multiple.select('#question').value);assert.equal(multiple.requests[0].ticker,'NVDA');
  const blocked=harness({ready:false,kind:'company_comparison'});await blocked.select('#research').click();
  assert.equal(blocked.lookups(),0);assert.equal(blocked.requests.length,0);assert.equal(blocked.context.state.researchRunning,false);
  const single=harness({ready:true,kind:'stock'});await single.select('#research').click();
  assert.equal(single.lookups(),1);assert.equal(single.requests[0].auto_acquire,true);
  const legacy=harness({ready:true,kind:'company_comparison'},false);await legacy.select('#research').click();
  assert.equal(legacy.lookups(),0);assert.equal(legacy.calls[1].path,'/api/research');assert.equal(legacy.rendered[0].mode,'comparison');
  assert.equal(legacy.context.state.researchRunning,false);assert.ok([multiple,blocked,single,legacy].every(h=>!h.errors.length));
  function unresolved(plan){const h=harness(plan);h.select('#company-query').value='unresolved company';h.context.companyInput=h.select('#company-query');h.context.companyState.selected=null;vm.runInContext(planner,h.context);return h;}
  const named=unresolved({ready:true,kind:'company_comparison',message:'Explicit MSFT AAPL scope',uses_model:false});await named.context.updateQuestionPlan();
  assert.equal(named.select('#research').disabled,false);assert.equal(named.calls.length,1);assert.equal(named.select('#question-plan').children[0].text,'Explicit MSFT AAPL scope');
  const ordinary=unresolved({ready:true,kind:'stock',message:'Single company'});await ordinary.context.updateQuestionPlan();
  assert.equal(ordinary.select('#research').disabled,true);assert.match(ordinary.select('#question-plan').textContent,/选择上市市场/);
  const empty=unresolved({ready:true,kind:'stock'});empty.select('#question').value='';await empty.context.updateQuestionPlan();
  assert.equal(empty.select('#research').disabled,true);assert.equal(empty.calls.length,0);
  const unavailable=unresolved({ready:true,kind:'company_comparison'});unavailable.context.api=async()=>{throw new Error('disconnected');};await unavailable.context.updateQuestionPlan();
  assert.equal(unavailable.select('#research').disabled,true);assert.match(unavailable.select('#question-plan').textContent,/检查未完成.*重试/);
  const index=fs.readFileSync('src/filingcopilot/static/index.html','utf8');
  for(const filename of ['app','market']){const digest=createHash('sha256').update(fs.readFileSync('src/filingcopilot/static/'+filename+'.js','utf8').replace(/\r\n/g,'\n')).digest('hex').slice(0,10);const tag=index.match(new RegExp('src="/static/'+filename+'\\.js\\?v=[^"]*?-([a-f0-9]{10})"'));assert.ok(tag,filename+' must have a content cache key');assert.equal(tag[1],digest,filename+' cache key must change with shipped source');}
  console.log(JSON.stringify({passed:10,scope:'shipped click handler/planner DOM doubles and two content cache keys; live browser acceptance separate',checks:['explicit list bypasses unrelated single-company lookup','blocked preflight does not start a job','single-company research keeps identity preparation','synchronous service path renders comparison','explicit question enables planner despite unresolved unrelated search','ordinary unresolved company remains gated','empty unresolved company does not call planner','planner connection failure keeps button disabled with retry','research entry script uses matching content cache key','question planner script uses matching content cache key']}));
}
check().catch(error=>{console.error(error);process.exitCode=1;});
