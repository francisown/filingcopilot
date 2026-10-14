// Exercise company input, selection and delayed market responses together.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const company = fs.readFileSync('src/filingcopilot/static/company.js','utf8');
const market = fs.readFileSync('src/filingcopilot/static/market.js','utf8');
function deferred(){let resolve,reject;const promise=new Promise((r,j)=>{resolve=r;reject=j;});return {promise,resolve,reject};}
function harness(capabilities={questions:[],filings_supported:true,annual_only:false},valuationResponse=null,options={}){
  const nodes=new Map(), calls=[], displayed=[], response=deferred(),snapshotResponse=deferred();
  function select(key){
    if(!nodes.has(key)){
      const classes=new Set(), listeners={};
      nodes.set(key,{value:'',textContent:'',disabled:false,options:[],children:[],dataset:{},
        classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x),toggle:(x,on)=>on?classes.add(x):classes.delete(x)},
        addEventListener(type,fn){(listeners[type]??=[]).push(fn);},
        dispatchEvent(event){for(const fn of listeners[event.type]||[])fn(event);},
        attributes:{},setAttribute(key,value){this.attributes[key]=String(value);},querySelectorAll(){return [];},
        replaceChildren(...items){this.children=items;},append(...items){this.children.push(...items);},
        prepend(option){this.options.unshift(option);},
      });
    }
    return nodes.get(key);
  }
  const context={$:select,AbortController,URLSearchParams,Promise,Event,
    state:{health:{company_discovery:true,as_of_default:'2026-10-09'}},
    el:(tag,cls,text)=>({tag,text,textContent:text||'',value:'',children:[],append(...items){this.children.push(...items);},replaceChildren(...items){this.children=items;},addEventListener(type,fn){this[type]=fn;}}),
    localStorage:{setItem(){}},setTimeout:options.setTimeout||setTimeout,clearTimeout:options.clearTimeout||clearTimeout,
    displayMarket:data=>displayed.push(data),renderCapabilities(){},updateQuestionPlan(){},
    marketQuestion:()=>true,loadCatalog:async()=>{},
    api:async(path,request={})=>{calls.push(path);if(options.api)return options.api(path,request);if(path.startsWith('/api/market/history'))return response.promise;
      if(path.startsWith('/api/capabilities'))return capabilities;
      if(path.startsWith('/api/market/valuation'))return valuationResponse?valuationResponse.promise:{found:false};
      if(path.startsWith('/api/runs/'))return snapshotResponse.promise;
      throw new Error('unexpected API '+path);},
  };
  vm.createContext(context);
  vm.runInContext(market.slice(0,market.indexOf('function svgEl')),context);
  context.displayMarket=(data,scope=null)=>{context.nextMarket=data;context.nextScope=scope||{ticker:data.snapshot.ticker,asOf:data.snapshot.as_of,period:data.snapshot.period};vm.runInContext('marketState.data=nextMarket;marketState.scope=nextScope',context);displayed.push(data);select('#market-content').classList.remove('hidden');};
  const resetStart=market.indexOf('function resetMarket');
  if(resetStart>=0)vm.runInContext(market.slice(resetStart,market.indexOf('async function loadMarket')),context);
  vm.runInContext(market.slice(market.indexOf('async function loadMarket'),market.indexOf('function marketQuestion')),context);
  vm.runInContext(market.slice(market.indexOf('async function displayResearchMarket'),market.indexOf('let questionPlanGeneration')),context);
  vm.runInContext(company,context);
  vm.runInContext(market.slice(market.indexOf('let questionPlanGeneration'),market.indexOf("$('#question').addEventListener")),context);
  select('#ticker').options=[{value:'META'},{value:'',textContent:'全部公司'}];select('#ticker').value='META';select('#as-of').value='2026-10-09';
  select('#ticker').addEventListener('change',context.loadMarket);
  return {context,select,calls,displayed,response,snapshotResponse};
}
async function check(){
  const h=harness();const pending=h.context.loadMarket();
  h.select('#company-query').value='台积电';h.select('#company-query').dispatchEvent(new Event('input'));
  h.response.resolve({snapshot:{ticker:'META'},statistics:{}});await pending;
  assert.equal(h.displayed.length,0,'a delayed META response must not appear during TSMC lookup');
  assert.equal(h.select('#ticker').value,'','research scope must not silently remain META');
  assert.equal(h.select('#market-content').classList.contains('hidden'),true);
  assert.equal(h.select('#market-refresh').disabled,true);
  assert.equal(h.select('#research').disabled,true,'unresolved company must not launch research on the previous ticker');
  assert.equal(h.select('#question-presets').classList.contains('hidden'),true);
  assert.equal(h.select('#ticker').options.at(-1).textContent,'等待选择上市证券');
  assert.match(h.select('#market-status').textContent,/选择|查找/);
  const callCount=h.calls.length;
  h.select('#company-query').dispatchEvent(new Event('change'));
  assert.equal(h.calls.length,callCount,'blurring the input must not create a second lookup');
  const picked=harness();picked.select('#company-query').value='TSMC';
  await picked.context.selectCompany({ticker:'TSM',name:'台积电',filings_supported:false,filing_note:'20-F 尚未接入'},'TSMC');
  assert.equal(picked.select('#ticker').value,'TSM');
  assert.equal(picked.select('#research').disabled,false);
  assert.ok(picked.calls.some(path=>path.includes('ticker=TSM')));
  assert.ok(!picked.calls.some(path=>path.includes('/prepare')),'quote selection needs no annual acquisition');
  assert.doesNotMatch(picked.select('#company-progress').textContent,/已接入|已就绪/,'unreturned quotes must not be declared ready');
  picked.response.resolve({snapshot:{ticker:'TSM'},statistics:{}});
  const failed=harness();failed.select('#company-query').value='TSMC';
  vm.runInContext("companyState.selected={ticker:'TSMC34.SA',query:'TSMC'}",failed.context);
  failed.select('#ticker').value='TSMC34.SA';
  const request=failed.context.loadMarket();failed.response.reject(new Error('TSMC34.SA 行情源连接未完成，无需下载财报'));await request;
  assert.equal(failed.select('#market-content').classList.contains('hidden'),true);
  assert.equal(failed.select('#market-refresh').disabled,false);
  assert.equal(failed.select('#market-refresh').textContent,'重试行情');
  assert.match(failed.select('#market-name').textContent,/TSMC34.SA/);
  const frozen=harness();frozen.select('#ticker').value='TSM';
  const oldResult={id:'old-meta',evidence:[{kind:'market',ticker:'META'}]};frozen.context.state.result=oldResult;
  await frozen.context.displayResearchMarket(oldResult);
  assert.equal(frozen.calls.length,0,'an old META research snapshot must not replace a TSM chart');
  const restored=harness(),liveRequest=restored.context.loadMarket();
  const saved={id:'saved-meta',evidence:[{kind:'market',ticker:'META'}]};restored.context.state.result=saved;
  const savedRequest=restored.context.displayResearchMarket(saved);
  restored.snapshotResponse.resolve({snapshot:{ticker:'META',period:'5Y'},statistics:{},frozen:true});await savedRequest;
  assert.equal(restored.select('#market-refresh').disabled,false,'a saved snapshot must release the interrupted live-load refresh control');
  assert.equal(restored.select('#market-refresh').textContent,'刷新行情');
  assert.equal(restored.select('#market-panel').attributes['aria-busy'],'false');
  restored.response.resolve({snapshot:{ticker:'META',period:'1Y'},statistics:{}});await liveRequest;
  assert.equal(restored.displayed.length,1);assert.equal(restored.displayed[0].frozen,true,'late live response must not replace the saved snapshot');
  const unavailable=harness();unavailable.context.state.result=saved;
  const missingSnapshot=unavailable.context.displayResearchMarket(saved);unavailable.snapshotResponse.reject(new Error('snapshot unavailable'));await missingSnapshot;
  assert.equal(unavailable.select('#market-refresh').disabled,false);assert.match(unavailable.select('#market-status').textContent,/回答仍已保存.*刷新/);
  const swapped=harness();swapped.context.state.result=saved;
  const staleSnapshot=swapped.context.displayResearchMarket(saved);
  swapped.select('#ticker').value='TSM';const newLive=swapped.context.loadMarket();
  swapped.snapshotResponse.reject(new Error('old snapshot unavailable'));await staleSnapshot;
  assert.equal(swapped.select('#market-refresh').disabled,true,'an obsolete snapshot must not finish the newer live request');
  assert.equal(swapped.select('#market-panel').attributes['aria-busy'],'true');assert.match(swapped.select('#market-status').textContent,/正在连接行情源/);
  swapped.response.resolve({snapshot:{ticker:'TSM'},statistics:{}});await newLive;
  assert.equal(swapped.displayed.length,1);assert.equal(swapped.displayed[0].snapshot.ticker,'TSM');assert.equal(swapped.select('#market-refresh').disabled,false);
  const native=harness({questions:[],filings_supported:true,annual_only:true});native.select('#ticker').value='TSM';
  const nativeRequest=native.context.loadMarket();native.response.resolve({snapshot:{ticker:'TSM'},statistics:{}});await nativeRequest;
  assert.ok(native.calls.some(path=>path.includes('/api/market/valuation')),'reported annual ADS valuation is available');
  assert.ok(!native.calls.some(path=>path.includes('provider_ttm')),'do not advertise or request an unsupported TTM share basis');
  const eps=deferred(),cold=harness({questions:[],filings_supported:true,annual_only:true},eps);
  const coldRequest=cold.context.loadMarket();cold.response.resolve({snapshot:{ticker:'META'},statistics:{}});await coldRequest;
  assert.equal(cold.displayed.length,1,'price chart must render before missing annual EPS acquisition finishes');
  assert.match(cold.select('#valuation-preview').children[0].children[0].text,/自动获取.*股价图/);
  eps.resolve({found:false,reason:'连接 SEC 超时；检查网络后重试。'});for(let i=0;i<12;i++)await Promise.resolve();
  assert.match(cold.select('#valuation-preview').children[0].children[0].text,/年度估值.*网络/);
  assert.equal(cold.select('#valuation-preview').children[0].children[1].text,'重试年度估值');
  assert.equal(cold.displayed.length,1,'missing annual EPS must not hide a usable chart');
  const checksBefore=cold.calls.filter(path=>path.includes('/api/market/valuation')).length;
  await cold.select('#valuation-preview').children[0].children[1].click();
  assert.ok(cold.calls.filter(path=>path.includes('/api/market/valuation')).length>checksBefore,'retry must invoke annual valuation again');
  const lateEPS=deferred(),late=harness({questions:[],filings_supported:true,annual_only:true},lateEPS);
  const lateRequest=late.context.loadMarket();late.response.resolve({snapshot:{ticker:'META'},statistics:{}});await lateRequest;
  late.select('#company-query').value='台积电';late.select('#company-query').dispatchEvent(new Event('input'));
  const before=late.select('#valuation-preview').children;
  lateEPS.resolve({found:false,reason:'旧 META 估值错误'});await Promise.resolve();await Promise.resolve();await Promise.resolve();
  assert.equal(late.select('#valuation-preview').children,before,'late valuation must not overwrite a different company selection');
  const priceOnly=harness({questions:[],filings_supported:false,annual_only:false});priceOnly.context.marketQuestion=()=>false;priceOnly.select('#company-query').value='HK company';priceOnly.select('#ticker').value='0700.HK';vm.runInContext("companyState.selected={ticker:'0700.HK',query:'HK company',filings_supported:false}",priceOnly.context);
  await priceOnly.context.ensureCompany('近五年跑赢大盘了吗',true,true);assert.equal(priceOnly.select('#ticker').value,'0700.HK');assert.ok(!priceOnly.calls.some(path=>path.includes('/prepare')),'explicit benchmark capability does not require annual preparation');
  const sec=harness();sec.context.marketQuestion=()=>false;sec.select('#company-query').value='Apple';vm.runInContext("companyState.selected={ticker:'AAPL',query:'Apple',filings_supported:true}",sec.context);await sec.context.ensureCompany('近五年跑赢大盘了吗',true,true);assert.ok(!sec.calls.some(path=>path.includes('/prepare')),'already selected SEC listing must also bypass the lookup preparation path');assert.equal(sec.select('#ticker').value,'AAPL');
  console.log('Company / market selection, chart-first acquisition, retry, benchmark and delayed-response checks passed (12 scenarios).');
}
module.exports={harness,deferred};
if(require.main===module)check().catch(error=>{console.error(error);process.exitCode=1;});
