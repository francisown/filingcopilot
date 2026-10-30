// Exercise the shipped renderer on source-bound periods and frozen research.
// Controlled DOM only: this does not audit financial or model correctness.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const crypto = require('node:crypto');
const path = require('node:path');
const vm = require('node:vm');
const legacyIndex = process.argv.indexOf('--legacy-packages');
if (legacyIndex >= 0 && !process.argv[legacyIndex + 1]) throw new Error('--legacy-packages needs an archive directory');
const legacy = legacyIndex >= 0;
const fixtureRoot = legacy ? process.argv[legacyIndex + 1] : 'tests/fixtures/claim-periods';
const source = fs.readFileSync('src/filingcopilot/static/app.js', 'utf8');
const checks = [], nodes = new Map();
function node(tag='div', cls='', text='') {
  return {tag, className:cls, textContent:text, children:[], attributes:{},
    classList:{remove(){}},
    get firstElementChild(){return this.children[0];},
    append(...items){this.children.push(...items);}, replaceChildren(...items){this.children=items;},
    setAttribute(key,value){this.attributes[key]=value;}, addEventListener(type,fn){this[type]=fn;}};
}
const context = vm.createContext({
  $:selector=>{if(!nodes.has(selector))nodes.set(selector,node());return nodes.get(selector);},
  el:node, state:{packageSupport:true}, labels:{partial:'部分回答',answered:'回答完成'}, stageNames:{},
  displayResearchMarket(){}, renderCompanyUpdates(){}, renderCapexBasis(){}, renderReviewDisclosure(){}, renderEvidenceFootnotes(){},
  addAuditButton(){}, addReferencedFigures(){}, renderSourceRefresh(){}, renderDeadlineActions(){},
  changeView(){}, renderTrend(){}, renderComparison(){},
});
vm.runInContext(source.slice(source.indexOf('function claimPeriodLabels('),source.indexOf('function showEvidence(')),context);
vm.runInContext(source.slice(source.indexOf('const labels ='),source.indexOf('const stageNames =')),context);
vm.runInContext(source.match(/^function noticeDateLabel.*$/m)[0],context);
vm.runInContext(source.match(/^function displayGap.*$/m)[0],context);
vm.runInContext(source.match(/^function renderResult.*$/m)[0],context);
function check(name,fn){fn();checks.push(name);}
function labels(...evidence){
  const ids=evidence.map((e,i)=>'e'+i);
  return [...context.claimPeriodLabels({evidence_ids:ids},new Map(ids.map((id,i)=>[id,evidence[i]])))];
}
const fact=(start,end,fy=2025,form='10-K')=>({kind:'fact',ticker:'TEST',start,end,fiscal_year:fy,filing:{form}});
const calc=(operation,...inputs)=>({kind:'calculation',ticker:'TEST',operation,inputs,fiscal_year:2026});
const annual=fact('2025-01-01','2025-12-31');
const current=fact('2026-01-01','2026-06-30',2026,'10-Q');
const previous=fact('2025-01-01','2025-06-30',2025,'10-Q');
const ttm=calc('trailing_sum',annual,current,previous);
check('comparison keeps actual current and previous FY dates once each',()=>{
  assert.deepEqual(labels(calc('growth',annual,fact('2024-01-01','2024-12-31',2024)),annual),[
    'TEST · FY2025 2025-01-01 至 2025-12-31','TEST · FY2024 2024-01-01 至 2024-12-31']);
});
check('fiscal year label does not come from end-date calendar year',()=>{
  assert.deepEqual(labels(fact('2025-10-01','2026-09-30',2026,'20-F')),['TEST · FY2026 2025-10-01 至 2026-09-30']);
});
check('single quarter and YTD remain separate even with the same end date',()=>{
  assert.deepEqual(labels(fact('2026-04-01','2026-06-30',2026,'10-Q'),current),[
    'TEST · 单季度 2026-04-01 至 2026-06-30','TEST · 年内累计 2026-01-01 至 2026-06-30']);
});
check('fiscal-week TTM uses the day after prior YTD, not calendar July',()=>{
  const w=calc('trailing_sum',fact('2024-09-29','2025-09-27'),fact('2025-09-28','2026-06-27',2026,'10-Q'),fact('2024-09-29','2025-06-28',2025,'10-Q'));
  assert.deepEqual(labels(w),['TEST · TTM 2025-06-29 至 2026-06-27']);
});
check('trailing derived cash flow displays one TTM window rather than component FY/YTD',()=>{
  const cash=calc('trailing_sum',...ttm.inputs.map(e=>calc('free_cash_flow',e,e)));
  assert.deepEqual(labels(ttm,cash),['TEST · TTM 2025-07-01 至 2026-06-30']);
});
check('annual-only trailing fallback keeps the complete reported annual window',()=>{
  assert.deepEqual(labels(calc('trailing_sum',annual)),['TEST · TTM 2025-01-01 至 2025-12-31']);
});
check('mismatched trailing margin shows both windows and the mismatch',()=>{
  assert.deepEqual(labels(calc('ratio',ttm,calc('trailing_sum',annual))),[
    'TEST · TTM 期间不一致','TEST · TTM 2025-07-01 至 2026-06-30','TEST · TTM 2025-01-01 至 2025-12-31']);
});
check('malformed trailing inputs are visible rather than fabricated from FY',()=>{
  for(const e of [calc('trailing_sum'),calc('trailing_sum',current),calc('trailing_sum',annual,current),calc('trailing_sum',annual,current,{...previous,end:'2025-02-30'}),calc('trailing_sum',annual,{...current,start:'2026-02-01'},previous)]){
    assert.deepEqual(labels(e),['TEST · TTM 期间资料不完整']);
  }
});
check('point-in-time financial facts are not labeled as an annual flow',()=>{
  assert.deepEqual(labels({...annual,start:null}),['TEST · 时点 2025-12-31']);
});
check('missing/invalid financial dates avoid FYundefined and calendar guesses',()=>{
  assert.deepEqual(labels({...annual,end:'2025-02-30'}),['TEST · 财务期间资料不完整']);
  assert.deepEqual(labels({...annual,start:'2026-01-01'}),['TEST · 财务期间资料不完整']);
  assert.deepEqual(labels({...annual,fiscal_year:undefined}),['TEST · 年度 2025-01-01 至 2025-12-31']);
});
check('legacy calculation without inputs retains a qualified metadata label',()=>{
  assert.deepEqual(labels({kind:'calculation',fiscal_year:2025}),['财年标签 FY2025（未保留日期范围）']);
});
check('supplier TTM label remains distinct from a SEC-derived full window',()=>{
  const v={kind:'valuation',ticker:'TEST',snapshot:{period_end_label:'2026-06-30'}};
  assert.deepEqual(labels(ttm,calc('provider_ttm_pe',v)),[
    'TEST · TTM 2025-07-01 至 2026-06-30','TEST · 供应商 TTM 期末标签 2026-06-30']);
});
check('quote day uses exchange timezone across a UTC date boundary',()=>{
  const m={kind:'market',ticker:'TEST',snapshot:{quote_at:Date.parse('2026-10-10T01:00:00Z')/1000,exchange_timezone:'America/New_York',quote_type:'provider_quote'}};
  assert.deepEqual(labels(m),['TEST · 报价 2026-10-09（交易所日）']);
  assert.deepEqual(labels({...m,snapshot:{...m.snapshot,quote_type:'historical_close'}}),['TEST · 历史收盘 2026-10-09（交易所日）']);
  assert.deepEqual(labels({...m,snapshot:{...m.snapshot,exchange_timezone:'invalid'}}),['TEST · 报价日期未注明']);
});
check('filing notice dates and uncited evidence do not become financial periods',()=>{
  assert.deepEqual(labels({kind:'passage',ticker:'TEST',filing:{form:'8-K',period_end:'2026-09-14',filed:'2026-09-14'}}),[]);
  assert.deepEqual([...context.claimPeriodLabels({evidence_ids:['missing']},new Map([['uncited',annual]]))],[]);
});
check('two companies with the same reporting window remain distinguishable',()=>{
  assert.equal(labels(annual,{...annual,ticker:'OTHER'}).length,2);
});
function all(root){return [root,...root.children.flatMap(all)];}
function render(result){nodes.get('#result')?.replaceChildren();context.renderResult(result);return nodes.get('#result');}
function periodRows(root){return all(root).filter(n=>n.className==='claim-periods');}
function periodText(row){return row.children.slice(1).map(n=>n.textContent);}
check('actual result renderer attaches periods to analysis only without changing result',()=>{
  const result={id:'r',mode:'agent',status:'partial',research_type:'stock',question:'经营表现',as_of:'2026-10-10',elapsed_ms:1,usage:{},trace:[],gaps:[],
    evidence:[{...annual,id:'annual'}],answer:[{kind:'analysis',text:'本年度经营改善。',evidence_ids:['annual']},{kind:'fact',text:'事实',evidence_ids:['annual']}]};
  const before=JSON.stringify(result),root=render(result),rows=periodRows(root);
  assert.equal(rows.length,1);assert.equal(rows[0].attributes['aria-label'],'所引数据期间');
  assert.deepEqual(periodText(rows[0]),['TEST · FY2025 2025-01-01 至 2025-12-31']);
  assert.equal(JSON.stringify(result),before);
});
const read=name=>JSON.parse(fs.readFileSync(path.join(fixtureRoot,name),'utf8'));
check('frozen AMZN annual, TTM, valuation and notices render distinct periods',()=>{
  const result=read('mixed-accepted-0.43-package.json').research,root=render(result),rows=periodRows(root);
  assert.equal(rows.length,3);
  assert.deepEqual(periodText(rows[0]),['AMZN · FY2025 2025-01-01 至 2025-12-31','AMZN · FY2024 2024-01-01 至 2024-12-31']);
  assert.deepEqual(periodText(rows[1]),['AMZN · TTM 2025-07-01 至 2026-06-30']);
  assert.deepEqual(periodText(rows[2]),['AMZN · 报价 2026-10-09（交易所日）','AMZN · FY2025 2025-01-01 至 2025-12-31','AMZN · 供应商 TTM 期末标签 2026-06-30']);
  assert.ok(all(root).some(n=>n.textContent===result.answer.find(c=>c.kind==='narrative').text));
});
check('frozen META separates single-quarter comparison from half-year cash flow',()=>{
  const rows=periodRows(render(read('brief-meta-0.42-package.json').research));
  assert.deepEqual(periodText(rows[0]),['META · 单季度 2026-04-01 至 2026-06-30','META · 单季度 2025-04-01 至 2025-06-30']);
  assert.deepEqual(periodText(rows[1]),['META · 年内累计 2026-01-01 至 2026-06-30','META · 年内累计 2025-01-01 至 2025-06-30']);
});
const manifest=read(legacy?'mixed-compatibility-0.43.json':'manifest.json');
const names=[...new Set([...Object.keys(manifest.old_packages),'mixed-accepted-0.43-package.json','mixed-real-0.43-package.json'])];
let analysisClaims=0, periodGroups=0;
for(const name of names){
  const file=path.join(fixtureRoot,name),raw=fs.readFileSync(file),package=JSON.parse(raw),before=JSON.stringify(package);
  const root=render(package.research);
  assert.equal(JSON.stringify(package),before,name+' renderer mutated research');
  assert.ok(fs.readFileSync(file).equals(raw),name+' frozen bytes changed');
  const shown=all(root).map(n=>n.textContent||'').join('\n');
  assert.ok(!/FYundefined|Invalid Date|\bNaN\b/.test(shown),name+' invalid displayed period');
  const expected=manifest.old_packages[name];
  if(expected)assert.equal(crypto.createHash('sha256').update(raw).digest('hex'),expected.sha256,name);
  analysisClaims+=package.research.answer.filter(c=>c.kind==='analysis').length;
  periodGroups+=periodRows(root).length;
}
console.log(JSON.stringify({scope:legacy?'Controlled shipped renderer on original archived packages; no new audit or model review':'Controlled shipped renderer on three curated display fixtures; not full package replay or model review',passed:checks.length,checks,compatibility:{packages:names.length,analysisClaims,periodGroups,bytesUnchanged:true,originalArchive:legacy}}));
