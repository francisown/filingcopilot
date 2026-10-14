// Exercise the actual export click and API helper without provider or model calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/filingcopilot/static/app.js', 'utf8');
const raw = '{"manifest":{"payload_sha256":"retained"},"research":{"trace":[{"delay_seconds":60.0,"large_count":9007199254740993,"value":-0.0}],"question":"现金投入与亚马逊"}}';
const fixture = {id:'run/raw', mode:'cashflow', status:'partial', question:'现金投入', answer:[], evidence:[], gaps:[], trace:[], as_of:'2026-10-09', elapsed_ms:1, usage:{total_tokens:0}};
const nodes = new Map(), requests = [], downloads = [], blobs = [];
let jsonReads = 0, textReads = 0, ok = true;
function node(tag='div') {
  return {tag, children:[], disabled:false, classList:{remove(){}},
    get firstElementChild(){return this.children[0];},
    append(...items){this.children.push(...items);}, replaceChildren(...items){this.children=items;},
    addEventListener(type, fn){this[type]=fn;}, click(){downloads.push(this.download);}};
}
const context = vm.createContext({
  $: selector => {if(!nodes.has(selector))nodes.set(selector,node());return nodes.get(selector);},
  el: (tag, cls, text) => Object.assign(node(tag), {className:cls, textContent:text}),
  state:{packageSupport:true}, labels:{partial:'部分回答'},
  displayResearchMarket(){}, renderCompanyUpdates(){}, renderCapexBasis(){}, addAuditButton(){}, addReferencedFigures(){},
  Blob: class {constructor(parts, options){this.parts=parts;this.options=options;}},
  URL:{createObjectURL(blob){blobs.push(blob);return 'blob:retained';},revokeObjectURL(){}},
  setTimeout(){}, fetch: async (path, options) => {
    requests.push({path, options});return {ok,
      async text(){textReads++;return raw;},
      async json(){jsonReads++;return ok?JSON.parse(raw):{detail:'研究记录不存在。'};}};
  },
});
vm.runInContext(source.match(/^async function api\(.*$/m)[0],context);
vm.runInContext(source.slice(source.indexOf('const labels ='),source.indexOf('const stageNames =')),context);
vm.runInContext(source.match(/^function renderResult\(.*$/m)[0],context);
const exportButton = () => nodes.get('#result').children.flatMap(n=>n.children).find(n=>n.textContent==='导出研究包 ↓'||n.textContent==='导出回答 JSON ↓');
async function check() {
  context.renderResult(fixture);await exportButton().click();
  assert.equal(blobs[0].parts.join(''),raw,'download must keep the server JSON unchanged, including 60.0 and integers above 2^53');
  assert.equal(jsonReads,0,'successful package export must not pass through JavaScript numbers');
  assert.equal(textReads,1);assert.equal(requests[0].path,'/api/runs/run%2Fraw/package');
  assert.equal(downloads[0],'run/raw-package.json');assert.equal(exportButton().disabled,false);
  ok=false;await exportButton().click();
  assert.equal(downloads.length,1,'failed export must not download an error as a research package');
  assert.equal(exportButton().disabled,false);assert.equal(nodes.get('#result').children.at(-1).textContent,'研究记录不存在。');
  context.state.packageSupport=false;context.renderResult(fixture);await exportButton().click();
  assert.equal(JSON.parse(blobs[1].parts.join('')).id,fixture.id);
  assert.equal(requests.length,2,'legacy answer export requires no package API');
  context.renderResult({...fixture,mode:'agent',research_type:'stock',question:'股票怎么样？结合近期公告。'});
  assert.equal(nodes.get('#result').firstElementChild.firstElementChild.textContent,'股票研究简报','notice keyword must not replace the mixed stock assessment heading');
  console.log(JSON.stringify({scope:'Controlled DOM and HTTP text; no real browser or LLM',passed:4,checks:['package download retains server JSON bytes and numeric spelling','HTTP failure preserves error and permits retry without a download','legacy answer-only export remains available','mixed notice keyword retains the stock brief heading']}));
}
check().catch(error=>{console.error(error);process.exitCode=1;});
