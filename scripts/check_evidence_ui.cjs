// Actual evidence renderers through controlled DOM doubles; no provider or model.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function node(tag='div',cls='',text=''){
  const classes=new Set(cls.split(' '));return {tag,className:cls,textContent:text,children:[],attributes:{},style:{},disabled:false,
    classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x)},
    append(...children){this.children.push(...children);},replaceChildren(...children){this.children=children;},
    setAttribute(k,v){this.attributes[k]=String(v);},addEventListener(type,fn){this[type]=fn;},click(){this.clickHandler?.();}};
}
const flatten=n=>[n,...n.children.flatMap(flatten)],text=n=>String(n.textContent)+n.children.map(text).join('');
const nodes=new Map(),select=s=>{if(!nodes.has(s))nodes.set(s,node());return nodes.get(s);};
let language='en';const app=fs.readFileSync('src/filingcopilot/static/app.js','utf8');
const context=vm.createContext({$:select,el:node,FilingI18n:{language:()=>language,translate:s=>s},addVersionButton(){},
  evidencePeriod:()=> 'FY2025',cashCapexNote:()=>'',AbortController,URLSearchParams,setTimeout,clearTimeout,
  document:{createElementNS:(_,tag)=>node(tag)},companyInput:node('input'),companyState:{},state:{}});
vm.runInContext(fs.readFileSync('src/filingcopilot/static/format.js','utf8'),context);
vm.runInContext(app.slice(app.indexOf('function sourceUi'),app.indexOf('function closeDrawer')),context);
vm.runInContext(fs.readFileSync('src/filingcopilot/static/market.js','utf8'),context);
const checks=[],check=(name,fn)=>{fn();checks.push(name);};
const fact={id:'f',kind:'fact',ticker:'TEST',label:'Revenue',value:'12000000.1234',unit:'USD',start:'2025-01-01',end:'2025-12-31',
  source_url:'https://data.sec.gov/test.json',locator:'facts.us-gaap.Revenue[42]',concept:'us-gaap:Revenue',
  filing:{form:'10-K',filed:'2026-02-01',period_end:'2025-12-31',source_url:'https://www.sec.gov/Archives/report.htm'}};
check('structured raw API link is secondary and inside a collapsed technical disclosure',()=>{
  const before=JSON.stringify(fact);context.showEvidence(fact);const root=select('#drawer-content');
  assert.ok(!root.children.some(n=>n.tag==='a'&&n.href===fact.source_url));
  const technical=root.children.find(n=>n.className==='source-technical');
  assert.equal(technical.open,undefined);assert.ok(flatten(technical).some(n=>n.tag==='a'&&n.href===fact.source_url));
  assert.ok(root.children.some(n=>n.tag==='a'&&n.href===fact.filing.source_url));
  assert.match(text(root),/Financial fact \(structured data\)/);assert.equal(JSON.stringify(fact),before);
});
check('highlighting preserves whitespace and escapes source HTML instead of executing it',()=>{
  const raw='  Revenue <script>alert(1)</script> rose to 12.  ',quote=node();context.sourceHighlight(quote,raw,['Revenue','12']);
  assert.equal(text(quote),raw);assert.ok(!flatten(quote).some(n=>n.tag==='script'));
});
check('highlights only complete numbers rather than substrings of different amounts',()=>{
  const quote=node();context.sourceHighlight(quote,'12 120 12.3 1,212 -12 12',['12']);
  assert.equal(flatten(quote).filter(n=>n.tag==='mark').length,2);
});
check('number highlights allow sentence punctuation while rejecting decimals, scientific suffixes and signed differences',()=>{
  const raw='12. Revenue was 12, while 12.3, 120, 12e3 and −12 differ.',quote=node();context.sourceHighlight(quote,raw,['12']);
  const positions=[];let offset=0;for(const child of quote.children){if(child.tag==='mark')positions.push(offset);offset+=text(child).length;}
  assert.deepEqual(positions,[0,raw.indexOf('12,')]);
});
check('language switch redraws reader labels while keeping verbatim source and evidence intact',()=>{
  const passage={...fact,kind:'passage',section:'Business',text:'  Revenue increased with demand.  '};const before=JSON.stringify(passage);
  context.showEvidence(passage);language='zh';context.refreshSourceLanguage();assert.match(text(select('#drawer-content')),/来源与期间/);
  assert.equal(text(flatten(select('#drawer-content')).find(n=>n.tag==='blockquote')),passage.text);
  language='en';context.refreshSourceLanguage();assert.match(text(select('#drawer-content')),/Source and period/);assert.equal(JSON.stringify(passage),before);
});
check('daily-source table paginates stored prices, keeps missing adjustments and original precision',()=>{
  const snapshot={ticker:'TEST',name:'Test',currency:'USD',exchange:'NASDAQ',basis:'adjusted_close',quote_value:'111.12345',retrieved_at:'2026-01-26T20:00:00Z',source_url:'https://provider.test/raw',warnings:[],
    points:Array.from({length:45},(_,i)=>({date:new Date(Date.UTC(2026,0,1+i)).toISOString().slice(0,10),close:String(100+i)+'.12345',adjusted:i===44?null:String(90+i)+'.12345'}))};
  const before=JSON.stringify(snapshot);context.showMarketEvidence({kind:'market',snapshot});const root=select('#drawer-content');
  const body=flatten(root).find(n=>n.tag==='tbody');assert.equal(body.children.length,5);assert.equal(body.children.at(-1).children.at(-1).textContent,'—');
  const older=flatten(root).find(n=>n.tag==='button'&&n.textContent==='Older dates');older.click();assert.equal(body.children.length,20);older.click();assert.equal(body.children.length,20);assert.equal(older.disabled,true);
  assert.ok(!root.children.some(n=>n.tag==='a'&&n.href===snapshot.source_url));assert.equal(JSON.stringify(snapshot),before);
});
check('quote timestamp uses exchange day and does not substitute fetch day, curve end or an unsupported precise time',()=>{
  const s={ticker:'TEST',currency:'USD',exchange:'NASDAQ',exchange_timezone:'America/New_York',quote_type:'provider_quote',quote_value:'100',quote_at:Date.UTC(2026,0,2,1,30)/1000,basis:'adjusted_close',retrieved_at:'2026-01-03T02:00:00Z',points:[{date:'2025-12-31',close:'99',adjusted:'98'}],warnings:[]};
  const before=JSON.stringify(s);context.showMarketEvidence({kind:'market',snapshot:s});
  assert.match(text(select('#drawer-content')),/2026-01-01 20:30:00 \(America\/New_York\)/);
  assert.equal(JSON.stringify(s),before);
  const historical={...s,basis:'split_adjusted_close',quote_type:'historical_close'};context.showMarketEvidence({kind:'market',snapshot:historical});
  assert.match(text(select('#drawer-content')),/2026-01-01 \(America\/New_York\)/);assert.doesNotMatch(text(select('#drawer-content')),/20:30:00/);
  context.showMarketEvidence({kind:'market',snapshot:{...s,quote_at:null}});assert.match(text(select('#drawer-content')),/Quote date not supplied/);
  context.showMarketEvidence({kind:'market',snapshot:{...s,exchange_timezone:'Invalid\/Timezone'}});assert.match(text(select('#drawer-content')),/Quote date not supplied/);
});
console.log(JSON.stringify({passed:checks.length,checks,scope:'Controlled DOM; exact source preservation and reader routing, not source truth or model accuracy'}));
