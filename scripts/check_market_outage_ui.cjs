// Execute the real price renderer with controlled DOM nodes; live browser checks are separate.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const code=fs.readFileSync('src/filingcopilot/static/market.js','utf8');
const render=code.match(/^function marketSourceLabel.*$/m)[0]+'\n'+code.match(/^function marketBasisLabel.*$/m)[0]+'\n'+code.slice(code.indexOf('function displayMarket('),code.indexOf('function drawMarketChart('));
function node(tag='div',text=''){
  const classes=new Set();
  return {tag,textContent:text,children:[],classList:{add:n=>classes.add(n),remove:n=>classes.delete(n),toggle:(n,on)=>{if(on)classes.add(n);else classes.delete(n);}},
    append(...items){this.children.push(...items);},replaceChildren(...items){this.children=items;},addEventListener(type,fn){this[type]=fn;}};
}
function run(stale,frozen,availability){
  const nodes=new Map(),select=q=>{if(!nodes.has(q))nodes.set(q,node());return nodes.get(q);};
  const context={marketState:{},$:select,marketNumber:x=>String(x),el:(tag,cls,text)=>node(tag,text),drawMarketChart:()=>{},Number};
  vm.createContext(context);vm.runInContext(render,context);
  context.displayMarket({frozen,availability,snapshot:{ticker:'AAPL',name:'Apple',currency:'USD',period:'5Y',as_of:'2026-10-09',retrieved_at:'2026-10-10T03:25:00Z',quote_value:'102',quote_type:'provider_quote',exchange:'NASDAQ',basis:'close',stale,warnings:[],source_url:'https://query1.finance.yahoo.com/v8/finance/chart/AAPL'},statistics:{change_pct:'2',max_drawdown_pct:'0',trading_days:3,start:'2026-10-07',end:'2026-10-09',quote_at:'2026-10-09T16:00:00-04:00'}});
  return {source:select('#market-source').children.map(n=>n.textContent).join('\n'),status:select('#market-status').textContent};
}
const availability={message:'AAPL：行情供应商暂时限流。约 45 秒后重试行情；无需先下载财报。'};
const current=run(true,false,availability);assert.match(current.source,/45 秒.*保存的行情，尚未更新/);assert.match(current.status,/已保存行情.*03:25/);
const old=run(true,true,availability);assert.doesNotMatch(old.source,/45 秒/);assert.match(old.status,/研究时保存/);
assert.doesNotMatch(run(false,false,availability).source,/45 秒/);
assert.match(run(true,false,null).source,/抓取时间/);
console.log(JSON.stringify({checks:4,scope:'Controlled real renderer; no browser/network/financial accuracy inference'}));
