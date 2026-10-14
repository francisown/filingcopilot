// Deterministic browser request timing; deliberately includes APIs that ignore AbortSignal.
// Real provider and real browser checks are recorded separately.
const assert=require('node:assert/strict');
const {harness,deferred}=require('./check_company_market_ui.cjs');
async function flush(){for(let i=0;i<25;i++)await Promise.resolve();}
function clock(){
  let now=0,id=0;const tasks=new Map();
  return {setTimeout(fn,ms){tasks.set(++id,{fn,at:now+ms});return id;},clearTimeout(key){tasks.delete(key);},
    async advance(ms){const end=now+ms;for(;;){const ready=[...tasks.entries()].filter(([,t])=>t.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!ready)break;now=ready[1].at;tasks.delete(ready[0]);ready[1].fn();await flush();}now=end;await flush();},pending:()=>tasks.size};
}
function sample(ticker='META',asOf='2026-10-09',period='5Y'){return {snapshot:{ticker,as_of:asOf,period,retrieved_at:'2026-10-09T01:00:00Z'},statistics:{}};}
const caps={questions:[],filings_supported:true,annual_only:false};
function setup(api){const time=clock(),h=harness(caps,null,{...time,api});return {...h,time};}
const checks=[],failures=[];
async function check(name,fn){try{await fn();checks.push(name);}catch(error){failures.push({name,error});console.error(name+': '+error.message);}}
async function run(){
  await check('a stalled price body releases loading controls after 45 seconds',async()=>{
    const pending=deferred(),h=setup(path=>path.startsWith('/api/capabilities')?caps:pending.promise);
    const request=h.context.loadMarket();await flush();await h.time.advance(45000);
    assert.equal(h.select('#market-refresh').disabled,false,'stalled price request must not leave refresh disabled indefinitely');
    assert.match(h.select('#market-status').textContent,/超时.*重试/);await request;assert.equal(h.time.pending(),0);
    pending.resolve(sample());await flush();assert.equal(h.displayed.length,0,'late timeout response is discarded');
  });
  await check('same company, cutoff and period retain the old chart during failed refresh',async()=>{
    const h=setup(path=>path.startsWith('/api/capabilities')?caps:Promise.reject(new Error('连接中断'))),old=sample();
    h.context.displayMarket(old);const request=h.context.loadMarket(true);
    assert.equal(h.select('#market-content').classList.contains('hidden'),false,'refresh must retain the usable chart');
    await request;assert.match(h.select('#market-status').textContent,/保留上次.*重试/);
  });
  await check('capability lookup cannot keep a returned price chart busy',async()=>{
    const pending=deferred(),h=setup(path=>path.startsWith('/api/capabilities')?pending.promise:sample());
    const request=h.context.loadMarket();await flush();
    assert.equal(h.displayed.length,1);assert.equal(h.select('#market-refresh').disabled,false,'capability lookup must not hold quote controls');
    await h.time.advance(10000);await request;assert.equal(h.time.pending(),0);
  });
  await check('completed TTM valuation is shown before stalled annual EPS finishes',async()=>{
    const annual=deferred(),h=setup(path=>path.startsWith('/api/capabilities')?caps:path.startsWith('/api/market/history')?sample():path.includes('provider_ttm')?{found:true,evidence:{value:'21.11'}}:annual.promise);
    h.context.displayProviderPE=(e,target=h.select('#valuation-preview'))=>target.append({text:'TTM '+e.value});
    await h.context.loadMarket();await flush();
    const texts=()=>h.select('#valuation-preview').children.flatMap(n=>[n.text,n.textContent,...(n.children||[]).map(c=>c.text||c.textContent)]).join(' ');
    assert.match(texts(),/TTM 21.11/,'annual acquisition must not hide returned TTM');
    await h.time.advance(45000);assert.match(texts(),/年度估值.*超时/);assert.match(texts(),/TTM 21.11/);assert.equal(h.time.pending(),0);
  });
  await check('manual stop aborts price and capability requests without requiring their cooperation',async()=>{
    const pending=deferred(),signals=[],h=setup((path,request)=>{signals.push(request.signal);return pending.promise;});
    const request=h.context.loadMarket();await flush();assert.equal(h.select('#market-stop').disabled,false);
    h.context.stopMarketWaiting();await flush();await request;
    assert.equal(h.select('#market-refresh').disabled,false);assert.equal(h.select('#market-stop').disabled,true);
    assert.match(h.select('#market-status').textContent,/停止本页等待.*服务器可能仍/);
    assert.ok(signals.length===2&&signals.every(s=>s.aborted));assert.equal(h.time.pending(),0);
    pending.resolve(sample());await flush();assert.equal(h.displayed.length,0);
  });
  await check('stop preserves returned TTM and chart while disclosing unfinished annual acquisition',async()=>{
    const annual=deferred(),h=setup(path=>path.startsWith('/api/capabilities')?caps:path.startsWith('/api/market/history')?sample():path.includes('provider_ttm')?{found:true,evidence:{value:'21.11'}}:annual.promise);
    h.context.displayProviderPE=(e,target)=>target.append({text:'TTM '+e.value});
    await h.context.loadMarket();await flush();h.context.stopMarketWaiting();await flush();
    assert.equal(h.displayed.length,1);assert.equal(h.select('#market-content').classList.contains('hidden'),false);
    const slots=h.select('#valuation-preview').children;assert.match(slots[0].children[0].text,/年度估值.*停止本页等待/);assert.equal(slots[1].children[0].text,'TTM 21.11');
    assert.equal(h.time.pending(),0);annual.resolve({found:true,evidence:{value:'999'}});await flush();assert.match(slots[0].children[0].text,/停止本页等待/);
  });
  await check('TTM connection failure is explicit while the annual result stays usable',async()=>{
    const h=setup(path=>path.startsWith('/api/capabilities')?caps:path.startsWith('/api/market/history')?sample():path.includes('provider_ttm')?Promise.reject(new Error('连接中断')):{found:true,evidence:{value:'36.60'}});
    h.context.displayAnnualPE=(e,asOf,target)=>target.append({text:'年度 '+e.value});
    await h.context.loadMarket();await flush();const slots=h.select('#valuation-preview').children;
    assert.equal(slots[0].children[0].text,'年度 36.60');assert.match(slots[1].children[0].text,/TTM.*连接中断/);assert.equal(slots[1].children[1].text,'重试供应商 TTM 市盈率');assert.equal(h.time.pending(),0);
  });
  for(const change of ['ticker','cutoff','period'])await check('old chart is hidden on '+change+' change',async()=>{
    const pending=deferred(),h=setup(path=>path.startsWith('/api/capabilities')?caps:pending.promise);h.context.displayMarket(sample());
    if(change==='ticker')h.select('#ticker').value='AMZN';if(change==='cutoff')h.select('#as-of').value='2026-09-01';
    if(change==='period')require('node:vm').runInContext("marketState.period='1Y'",h.context);
    const request=h.context.loadMarket();await flush();assert.equal(h.select('#market-content').classList.contains('hidden'),true);
    h.context.stopMarketWaiting();await request;assert.equal(h.time.pending(),0);
  });
  await check('exchange cutoff difference still retains the same requested date',async()=>{
    const pending=deferred(),h=setup(path=>path.startsWith('/api/capabilities')?caps:pending.promise);
    h.context.displayMarket(sample('META','2026-10-08'),{ticker:'META',asOf:'2026-10-09',period:'5Y'});
    const request=h.context.loadMarket(true);await flush();assert.equal(h.select('#market-content').classList.contains('hidden'),false);
    h.context.stopMarketWaiting();await request;assert.match(h.select('#market-status').textContent,/保留上次.*2026-10-09T01/);
  });
  await check('changing company aborts old requests and old timers cannot reset new controls',async()=>{
    const pending=deferred(),h=setup(()=>pending.promise);const old=h.context.loadMarket();await flush();
    await h.time.advance(20000);h.select('#ticker').value='AMZN';const next=h.context.loadMarket();await flush();await old;
    await h.time.advance(25000);assert.equal(h.select('#market-refresh').disabled,true);assert.match(h.select('#market-name').textContent,/AMZN/);
    h.context.stopMarketWaiting();await next;assert.equal(h.time.pending(),0);
  });
  await check('saved snapshot has the same complete-response timeout and retains research answer',async()=>{
    const pending=deferred(),h=setup(()=>pending.promise),saved={id:'saved',evidence:[{kind:'market',ticker:'META'}]};h.context.state.result=saved;
    const request=h.context.displayResearchMarket(saved);await h.time.advance(45000);await request;
    assert.equal(h.select('#market-refresh').disabled,false);assert.match(h.select('#market-status').textContent,/回答仍已保存.*快照.*超时/);assert.equal(h.time.pending(),0);
    pending.resolve({...sample(),frozen:true});await flush();assert.equal(h.displayed.length,0);
  });
  if(failures.length)throw new Error(failures.length+' controlled loading scenarios failed');
  console.log('Market request timeout, retained snapshots, independent valuations and cancellation checks passed ('+checks.length+' scenarios).');
}
run().catch(error=>{console.error(error);process.exitCode=1;});
