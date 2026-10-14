const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('src/filingcopilot/static/app.js','utf8');
const body=source.slice(source.indexOf('function renderResearchPreview('),source.indexOf('function showResearchJob('));
const nodes=new Map();const shown=[];
function node(tag='',className='',text=''){
  const hidden=new Set();return {tag,className,text,children:[],listeners:{},
    append(...children){this.children.push(...children);},replaceChildren(...children){this.children=children;},
    addEventListener(name,fn){this.listeners[name]=fn;},
    classList:{add:v=>hidden.add(v),remove:v=>hidden.delete(v),contains:v=>hidden.has(v)}};
}
const root=node();nodes.set('#research-preview',root);
const context=vm.createContext({$:s=>nodes.get(s),el:node,showEvidence:e=>shown.push(e),Map,Intl});
vm.runInContext(body,context);
context.renderResearchPreview({id:'one',status:'running',preview:{schema:'filingcopilot.financial-preview/v1',ticker:'AAPL',note:'已缓存，未确认最新',claims:[{text:'AAPL FY2025 revenue',evidence_ids:['f']}],evidence:[{id:'f',filing:{form:'10-K',filed:'2025-10-31'}}]},usage:{model_calls:0,total_tokens:0}});
assert.equal(root.classList.contains('hidden'),false);
const figure=root.children[2].children[0];
assert.equal(figure.children[0].text,'AAPL FY2025 revenue');
figure.children[1].listeners.click();assert.equal(shown[0].id,'f');
assert.match(root.children[3].text,/模型调用 0/);
context.renderResearchPreview({id:'two',preview:null});
assert.equal(root.classList.contains('hidden'),true);assert.equal(root.children.length,0);
context.renderResearchPreview({preview:{schema:'unknown',claims:[{text:'untrusted'}]}});
assert.equal(root.classList.contains('hidden'),true);
context.renderResearchPreview({status:'cancelled',preview:{schema:'filingcopilot.financial-preview/v1',claims:[{text:'old preview'}]}});
assert.equal(root.classList.contains('hidden'),true);
nodes.clear();context.renderResearchPreview({});
console.log('Preview UI: 7 controlled cases passed; source links, cache note, old-job/terminal clearing, unknown schema and old-page compatibility.');
