// Frozen 8-K/6-K notices exercise the shipped date display and original-source list.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('src/filingcopilot/static/app.js','utf8');
const node=(tag,cls,text)=>({tag,className:cls,textContent:text,children:[],listeners:{},append(...items){this.children.push(...items);},addEventListener(name,fn){this.listeners[name]=fn;}});
const fields=Object.fromEntries(['#ticker','#as-of','#mode','#question'].map(id=>[id,{value:'',events:[],dispatchEvent(e){this.events.push(e.type);},focus(){this.focused=true;}}]));
let cleared=0,planned=0;
const context=vm.createContext({el:node,state:{health:{},researchRunning:false},$:id=>fields[id],Event:class {constructor(type){this.type=type;}},clearCompanySelection(){cleared++;},updateQuestionPlan(){planned++;}});
vm.runInContext(source.slice(source.indexOf('function noticeDateLabel('),source.indexOf('async function loadCatalog(')),context);
vm.runInContext(source.slice(source.indexOf('function renderCompanyUpdates('),source.indexOf('function renderSourceRefresh(')),context);
const fixture=foreign=>({trace:[{tool:'get_company_updates',result:{scope:foreign?'6-K excerpts':'8-K releases',next_step:'Open original',events:[{filed:'2026-10-08',event_date:foreign?null:'2026-10-07',...(foreign?{report_date:'2026-09-30',date_basis:'sec_report_date'}:{}),documents:[{form:foreign?'6-K':'8-K',source_url:'https://www.sec.gov/Archives/exact.htm'}],evidence_ids:['source']}]}}]});
const flatten=n=>[n?.textContent||'',...(n?.children||[]).flatMap(flatten)].join(' ');
for(const foreign of [false,true]){const f=fixture(foreign),before=JSON.stringify(f),root=node('section');context.renderCompanyUpdates(root,f);const text=flatten(root);
 assert.ok(text.includes('公开于 2026-10-08'));
 assert.ok(text.includes(foreign?'报告标注日期 2026-09-30':'事件日期 2026-10-07'));
 if(foreign){assert.ok(!text.includes('事件日期')&&!text.includes('null'));}
 assert.equal(root.children[1].children[1].children[1].href,'https://www.sec.gov/Archives/exact.htm');
 assert.equal(JSON.stringify(f),before);
}
assert.equal(context.noticeDateLabel('6-K EX-99.1'),'报告标注日期');
assert.equal(context.noticeDateLabel('8-K/A'),'事件日期');
const unreadable=fixture(true);unreadable.trace[0].result.events[0].evidence_ids=[];const root=node('section');context.renderCompanyUpdates(root,unreadable);assert.ok(flatten(root).includes('直接查看原始文件'));
const walk=n=>[n,...(n.children||[]).flatMap(walk)];
const summary=fixture(true);summary.mode='updates';Object.assign(summary.trace[0].result,{found:true,ticker:'TSM'});summary.as_of='2026-10-08';summary.trace[0].result.events[0].excerpts=[{text:'Exact original source text.'}];
context.state.health.agent_ready=true;
const ready=node('section');context.renderCompanyUpdates(ready,summary);
assert.ok(flatten(ready).includes('Exact original source text.'));
const action=walk(ready).find(n=>n.tag==='button'&&n.textContent==='生成中文摘要');assert.ok(action&&!action.disabled);action.listeners.click();
assert.equal(fields['#ticker'].value,'TSM');assert.equal(fields['#as-of'].value,'2026-10-08');assert.equal(fields['#mode'].value,'agent');assert.equal(fields['#question'].value,'近期公司公告中文摘要');assert.equal(cleared,1);assert.equal(planned,1);assert.ok(fields['#question'].focused);
context.FilingI18n={translate:()=> 'Summarize recent company updates'};action.listeners.click();
assert.equal(fields['#question'].value,'Summarize recent company updates');
context.state.researchRunning=true;const busy=node('section');context.renderCompanyUpdates(busy,summary);assert.ok(walk(busy).find(n=>n.tag==='button').disabled);
context.state.health.agent_ready=false;const offline=node('section');context.renderCompanyUpdates(offline,summary);assert.ok(!walk(offline).some(n=>n.tag==='button'));
context.state.health.agent_ready=true;summary.mode='agent';const generated=node('section');context.renderCompanyUpdates(generated,summary);assert.ok(!walk(generated).some(n=>n.tag==='button'));
summary.mode='updates';summary.trace[0].result.found=false;const empty=node('section');context.renderCompanyUpdates(empty,summary);assert.ok(!walk(empty).some(n=>n.tag==='button'));
console.log(JSON.stringify({passed:10,scope:'Controlled date/source/original rendering, localized summary action, no auto-start, busy/offline/empty states; real browser/model separately'}));
