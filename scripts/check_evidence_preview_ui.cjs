// Evidence-first research preview: controlled DOM doubles, no provider or LLM call.
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

const longText='  Risk factors include supply constraints,\ncomponent shortages and   geopolitical exposure. '+ 'Additional disclosure sentence. '.repeat(15);
const sources=[
  {id:'passage-1',ticker:'AAPL',section:'Risk Factors',text:longText,filing:{form:'10-K',filed:'2025-10-31'}},
  {id:'passage-2',ticker:'AAPL',section:'MD&A',text:'Services revenue increased year over year.',filing:{form:'10-K',filed:'2025-10-31'}},
  {id:'passage-3',ticker:'AAPL',section:'Liquidity',text:'Cash and equivalents remained stable.',filing:{form:'10-K',filed:'2025-10-31'}},
  {id:'passage-4',ticker:'AAPL',section:'Segment Results',text:'Segment detail is available.',filing:{form:'10-K',filed:'2025-10-31'}},
];
const preview={schema:'filingcopilot.evidence-preview/v1',ticker:'AAPL',note:'已找到可用于回答的原文和计算；解释仍在生成。',evidence:sources,calculations:[
  {id:'calc-1',label:'Revenue FY2025',value:'416161000000',unit:'USD'},
  {id:'calc-2',label:'Gross margin FY2025',value:'0.469',unit:'ratio'},
]};
context.renderResearchPreview({id:'evidence-job',status:'running',preview});
assert.equal(root.classList.contains('hidden'),false,'evidence preview is visible while running');
assert.equal(root.children[0].text,'AAPL · 先看已找到的证据');
assert.equal(root.children[1].text,preview.note);
const list=root.children[2];
assert.equal(list.className,'preview-sources');
assert.equal(list.children.length,3,'only three source items are shown');
const [tickerNode,sectionNode]=list.children[0].children[0].children;
assert.equal(tickerNode.text,'AAPL');
assert.equal(sectionNode.text,'Risk Factors');
const excerpt=list.children[0].children[1];
assert.equal(excerpt.className,'source-text preview-source-text');
assert.ok(excerpt.text.length<=281,'excerpt respects the character budget');
assert.ok(excerpt.text.endsWith('…'),'long excerpt gets an ellipsis');
assert.ok(!excerpt.text.includes('\n')&&!excerpt.text.includes('   '),'excerpt text is collapsed for the compact card');
const citation=list.children[0].children[2];
assert.equal(citation.text,'10-K · 公开于 2025-10-31');
citation.listeners.click();
assert.equal(shown[0],sources[0],'citation receives the full evidence object, not the excerpt');
assert.equal(shown[0].text,longText);
assert.match(root.children[3].text,/^原文共 4 项，此处显示前 3 项。$/);
assert.equal(root.children[4].text,'已找到 2 项计算；数值随解释一并给出。');

context.renderResearchPreview({status:'queued',preview:{schema:'filingcopilot.evidence-preview/v1',ticker:'MSFT',calculations:[{id:'calc-3',label:'Revenue FY2026',value:'1',unit:'USD'}]}});
assert.equal(root.classList.contains('hidden'),false,'calculation-only preview stays visible');
assert.equal(root.children[0].text,'MSFT · 先看已找到的证据');
assert.equal(root.children[1].text,'已找到可用于回答的原文和计算；解释仍在生成。');
assert.equal(root.children.length,3,'no source list is rendered without evidence');
assert.equal(root.children[2].text,'已找到 1 项计算；数值随解释一并给出。');

context.renderResearchPreview({status:'running',preview:{schema:'filingcopilot.evidence-preview/v1',ticker:'AAPL',evidence:[],calculations:[]}});
assert.equal(root.classList.contains('hidden'),true,'empty evidence preview is hidden');
assert.equal(root.children.length,0);

context.renderResearchPreview({status:'cancelled',preview});
assert.equal(root.classList.contains('hidden'),true,'terminal status clears the evidence preview');
assert.equal(root.children.length,0);

context.renderResearchPreview({status:'running',preview:{schema:'filingcopilot.financial-preview/v1',ticker:'AAPL',note:'已缓存，未确认最新',claims:[{text:'AAPL FY2025 revenue',evidence_ids:['f']}],evidence:[{id:'f',filing:{form:'10-K',filed:'2025-10-31'}}]},usage:{model_calls:0,total_tokens:0}});
assert.equal(root.classList.contains('hidden'),false,'financial preview remains available on the same panel');
assert.equal(root.children[2].children[0].children[0].text,'AAPL FY2025 revenue');
assert.match(root.children[3].text,/模型调用 0/);
console.log('Evidence preview UI: 5 controlled cases passed; three-source cap, excerpt budget/ellipsis, full-object citation, calculation summary, empty and terminal clearing, financial branch coexistence.');
