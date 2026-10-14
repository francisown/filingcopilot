const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const nodes=new Map(),buttons=[],blobs=[],saved=new Map(),events={};
function node(tag='DIV'){return {tagName:tag,textContent:'',children:[],listeners:{},value:'',checked:false,disabled:false,dataset:{},classes:new Set(),append(...x){this.children.push(...x);},replaceChildren(...x){this.children=x;},setAttribute(){},addEventListener(k,fn){this.listeners[k]=fn;},click(){this.listeners.click?.();},classList:{toggle(){}}};}
for(const id of ['sample','reviewer','human','status','position','question','answer','progress','previous','next','export','evidence'])nodes.set(id,node());
nodes.get('sample').textContent=JSON.stringify({sample_sha256:'fixture',rows:[{blind_id:'a',question:'Question',answer:'Claim',label:null,evidence:[{text:'Source',source_url:'https://www.sec.gov/source'}]},{blind_id:'b',answer:'Claim B',label:null,evidence:[]}]});
for(const label of ['support','unsupported','unclear']){const b=node('BUTTON');b.dataset.label=label;buttons.push(b);}
const context={document:{getElementById:id=>nodes.get(id),querySelectorAll:()=>buttons,createElement:node,addEventListener:(k,fn)=>events[k]=fn},localStorage:{getItem:k=>saved.get(k),setItem:(k,v)=>saved.set(k,v)},URL,Blob,setTimeout:()=>{},console};
context.URL={createObjectURL:b=>{blobs.push(b);return 'blob:fixture';},revokeObjectURL(){}};
// URL parsing uses a real URL constructor with the same object-URL helpers.
context.URL=Object.assign(URL,context.URL);vm.createContext(context);vm.runInContext(fs.readFileSync('src/filingcopilot/static/blind-review.js','utf8'),context);
assert.match(nodes.get('progress').textContent,/0 \/ 2/);nodes.get('export').click();assert.equal(blobs.length,0);
buttons[0].click();assert.match(nodes.get('progress').textContent,/1 \/ 2/);
nodes.get('next').click();assert.equal(nodes.get('answer').textContent,'Claim B');
events.keydown({target:{tagName:'INPUT'},key:'2',preventDefault(){}});assert.match(nodes.get('progress').textContent,/1 \/ 2/);
nodes.get('reviewer').value='fixture-only';nodes.get('human').checked=true;nodes.get('export').click();assert.equal(blobs.length,1);
blobs[0].text().then(raw=>{const data=JSON.parse(raw);assert.equal(data.human_reviewer,'fixture-only');assert.equal(data.rows[0].label,'support');assert.equal(data.rows[1].label,null);assert.equal(data.rows[0].answer,'Claim');console.log('Blind review UI: 8 controlled cases; empty labels, human declaration, navigation, shortcut guard and partial export.');}).catch(e=>{console.error(e);process.exitCode=1;});
