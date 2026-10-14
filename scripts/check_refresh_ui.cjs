// Replay the shipped freshness card against controlled research traces.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('src/filingcopilot/static/app.js','utf8');
const node=()=>({children:[],append(...items){this.children.push(...items);}});
const context=vm.createContext({el:(tag,cls,text)=>Object.assign(node(),{tag,className:cls,textContent:text}),stageNames:{annual_refresh:'检查最新年报',quarterly_preparation:'检查最新季报',updates_preparation:'检查最新公司公告'}});
vm.runInContext(source.slice(source.indexOf('function renderSourceRefresh('),source.indexOf('function renderDeadlineActions(')),context);
const failed={trace:[{stage:'annual_refresh',result:{checked:false,warning:'年报检查未完成',filings:[{filed:'2025-10-31',period_end:'2025-09-27'},{filed:'2024-11-01',period_end:'2024-09-28'}]}},{stage:'updates_preparation',result:{checked:false,warnings:['ReadTimeout；延后 60 秒重试']}}]};
const original=JSON.stringify(failed),root=node();context.renderSourceRefresh(root,failed);
assert.equal(root.children[0].children[0].textContent,'披露刷新未完成');
assert.ok(root.children[0].children[1].textContent.includes('新鲜度尚未确认'));
const rows=root.children[0].children[2].children.slice(1);assert.equal(rows.length,2);
assert.ok(rows[0].children.at(-1).textContent.includes('2025-10-31'));
assert.ok(rows[0].children.at(-1).textContent.includes('2025-09-27'));
assert.ok(rows[1].children[1].textContent.includes('ReadTimeout'));
assert.equal(JSON.stringify(failed),original,'display must not mutate frozen filings or answer');
const success=node();context.renderSourceRefresh(success,{trace:[{stage:'annual_refresh',result:{checked:true,warning:null}}]});assert.equal(success.children.length,0);
const legacy=node();context.renderSourceRefresh(legacy,{trace:[{stage:'quarterly_preparation',result:{warning:'旧包检查未完成'}}]});assert.equal(legacy.children.length,1);
const incomplete=node();context.renderSourceRefresh(incomplete,{trace:[{stage:'updates_preparation',result:{checked:true,warnings:['一份公告未取得原文']}}]});assert.equal(incomplete.children.length,1);
console.log(JSON.stringify({scope:'Controlled DOM and frozen traces; no real browser, provider or LLM',passed:5,checks:['failed checks display freshness gap and original failure','cached public and period dates are visible','rendering does not rewrite frozen data','successful checks have no failure card','legacy warnings and partially acquired updates remain visible']}));
