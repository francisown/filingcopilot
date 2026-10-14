// Controlled inline research-job progress check; no browser, provider or model calls.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const code=fs.readFileSync('src/filingcopilot/static/company-workspace.js','utf8');
const progressNodes=[];
function node(tag='div',cls='',text=''){
  const classes=new Set(cls.split(' ').filter(Boolean)),attrs={},listeners={};let value=text;
  const result={tag,children:[],disabled:false,dataset:{},
    classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x)},
    append(...children){this.children.push(...children)},replaceChildren(...children){this.children=children},
    setAttribute(k,v){attrs[k]=v},removeAttribute(k){delete attrs[k]},getAttribute:k=>attrs[k],
    addEventListener(name,fn){(listeners[name]??=[]).push(fn)},async fire(name){await Promise.all((listeners[name]||[]).map(fn=>fn({type:name})))},focus(){},
    get firstElementChild(){return this.children[0]},get textContent(){return value},set textContent(next){value=String(next);this.history??=[];this.history.push(value)},
    click(){return this.fire('click')}};
  if(cls.includes('company-inline-progress'))progressNodes.push(result);
  return result;
}
const nodes=new Map();const select=s=>{if(!nodes.has(s))nodes.set(s,node());return nodes.get(s)};
select('#ticker').value='AAPL';select('#as-of').value='2026-10-08';
const translated={
  '研究已排队，等待执行。':'Research is queued and waiting to run.',
  '正在检索负债章节':'Retrieving the liabilities section',
  '正在核验数字':'Verifying figures',
  '正在生成本节文字总结…':'Generating this section…',
  ' 已等待 ':' · waited ',
  ' 秒':' seconds'
};
let getCount=0;const requests=[];
const context={
  $:select,el:node,URLSearchParams,Promise,Set,Map,crypto:{randomUUID:()=> 'progress-test-key'},
  document:{querySelectorAll:()=>[]},setTimeout,clearTimeout,state:{health:{research_jobs:true}},
  FilingI18n:{translate:value=>translated[value]||value},
  api:async(path,options)=>{
    requests.push({path,options});
    if(path==='/api/research/plan')return {ready:true};
    if(path==='/api/research/jobs')return {id:'job-progress',status:'running',elapsed_ms:0,events:[{stage:'queued',message:'研究已排队，等待执行。'}]};
    if(path==='/api/research/jobs/job-progress'){
      getCount++;
      if(getCount===1)return {id:'job-progress',status:'running',elapsed_ms:2300,events:[
        {stage:'queued',message:'研究已排队，等待执行。'},
        {stage:'evidence',message:'正在检索负债章节'}
      ]};
      if(getCount===2)return {id:'job-progress',status:'completed',elapsed_ms:3100,events:[
        {stage:'queued',message:'研究已排队，等待执行。'},
        {stage:'evidence',message:'正在检索负债章节'},
        {stage:'verify',message:'正在核验数字'}
      ],result:{answer:[],evidence:[],gaps:[]}};
    }
    throw new Error('Unexpected '+path);
  },
  setCompanyInlineBusy(){},
};
vm.createContext(context);vm.runInContext(code,context);
const host=node('div','company-inline-summary hidden'),button=node('button','primary','生成本节文字总结');
const section={key:'debt',title:'负债',module:{question:'What is the company debt position?'}};
context.runInlineCompanyResearch({ticker:'AAPL',as_of:'2026-10-08',mode:'agent',depth:'brief',module:'debt'},host,button,section,()=>{}).then(()=>{
  const history=progressNodes.flatMap(item=>item.history||[]).join('\n');
  assert.match(history,/Research is queued and waiting to run/);
  assert.match(history,/Retrieving the liabilities section/);
  assert.match(history,/Retrieving the liabilities section → Verifying figures/,'latest events should form a readable trail');
  assert.match(history,/waited 2 seconds/);
  assert.equal(progressNodes[0].getAttribute('role'),'status');
  assert.equal(progressNodes[0].getAttribute('aria-live'),'polite');
  assert.equal(requests.filter(r=>r.path.includes('/api/research/jobs/')).length,2);
  console.log(JSON.stringify({checks:7,status:'passed',scope:'Controlled inline company research job events, latest trail, English translation and aria-live status; no live provider or model'}));
}).catch(error=>{console.error(error);process.exitCode=1});
