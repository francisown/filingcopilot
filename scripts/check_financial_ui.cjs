// Presentation checks run exact decimal strings and chart interaction through the real frontend.
// DOM/network doubles are used here; real provider/browser acceptance is recorded separately.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const checks = [];
function check(name, fn) { fn(); checks.push(name); }
function node(tag = 'div') {
  const classes = new Set();
  return {
    tag, textContent: '', value: '', children: [], attributes: {}, dataset: {}, disabled: false, options: [],
    clientWidth: 700,
    classList: {
      add: x => classes.add(x), remove: x => classes.delete(x), contains: x => classes.has(x),
      toggle: (x, force) => {if (force ?? !classes.has(x)) classes.add(x); else classes.delete(x);},
    },
    setAttribute(k, v) {this.attributes[k] = String(v);},
    append(...children) {this.children.push(...children);},
    replaceChildren(...children) {this.children = children;},
    querySelectorAll() {return this.children.filter(x => x.tag === 'button');},
    addEventListener(type, fn) {this[type] = fn;}, focus() {},
    getBoundingClientRect: () => ({left: 0, width: 700}),
  };
}
const nodes = new Map(), calls = [];
const select = s => {if (!nodes.has(s)) nodes.set(s, node()); return nodes.get(s);};
const el = (tag, cls, text) => {const n = node(tag); n.textContent = text ?? ''; n.className = cls; return n;};
const context = vm.createContext({
  $: select, el, state: {}, AbortController, URLSearchParams,
  companyInput: select('#company-query'), companyState: {selected: null},
  document: {createElementNS: (_, tag) => node(tag)},
  localStorage: {setItem() {}}, setTimeout, clearTimeout,
  api: async path => {
    calls.push(path);
    if (path.startsWith('/api/capabilities')) return capabilities;
    if (path.startsWith('/api/market/valuation')) return {found: false};
    if (path.startsWith('/api/market/history')) return sample(new URLSearchParams(path.split('?')[1]).get('period'));
    if (path === '/api/research/plan') return {ready: true, uses_model: false, suggestions: [], message: '直接工具计算'};
    throw new Error('Unexpected API call: ' + path);
  },
});
vm.runInContext(fs.readFileSync('src/filingcopilot/static/format.js', 'utf8'), context);
const format = context.displayDecimal;
check('XBRL display units are readable without altering source values or units', () => {
  const evidence={value:'59431000000.12345',unit:'iso4217:USD'},before=JSON.stringify(evidence);
  assert.equal(context.displayScaledAmount(evidence),'594.31 亿 USD');
  assert.equal(context.displayEvidenceAmount({value:'12',unit:'xbrli:shares'}),'12 股');
  assert.equal(context.displayEvidenceAmount({value:'0.0084',unit:'xbrli:pure'}),'0.01 无量纲');
  assert.equal(JSON.stringify(evidence),before);
});
check('exact decimal rounding matches backend half-even display at ties', () => {
  assert.equal(format('1.005',2,2), '1.00'); assert.equal(format('-1.005',2,2), '−1.00');
  assert.equal(format('1.015',2,2), '1.02'); assert.equal(format('2.345',2,2), '2.34');
  assert.equal(format('48.648752492493850987654'), '48.65');
});
check('large integer precision survives display grouping and rounding', () => {
  assert.equal(format('9007199254740993.995'), '9,007,199,254,740,994');
});
check('short decimals stay compact while quote prices retain two places', () => {
  assert.equal(format('12.0000'), '12'); assert.equal(format('12.10'), '12.1');
  assert.equal(format('12', 2, 2), '12.00');
});
check('nonzero small amounts remain visible and zero has no negative sign', () => {
  assert.equal(format('0.00000001'), '<0.01');
  assert.equal(format('-0.00000001'), '介于 −0.01 与 0');
  assert.equal(format('-0.000'), '0');
});
check('scientific decimals are bounded and missing values are not zero', () => {
  assert.equal(format('1.2345e3'), '1,234.5'); assert.equal(format('1e-8'), '<0.01');
  for (const v of [null, undefined, '', NaN, Infinity, '1e1000']) assert.equal(format(v), '—');
});
check('evidence is rounded for display while raw precision is kept in disclosure', () => {
  const evidence = {value: '48.648752492493850987654', unit: '%'};
  const original = JSON.stringify(evidence), shown = context.sourceValue(evidence);
  assert.equal(shown.textContent, '48.65%');
  assert.equal(shown.children[0].tag, 'details');
  assert.equal(shown.children[0].children[1].textContent, evidence.value + ' %');
  assert.equal(JSON.stringify(evidence), original);
});

const periods = ['1M', '6M', '1Y', '3Y', '5Y', '10Y', 'MAX'];
const capabilities = {
  scope: '已支持', pending: [], chart_periods: periods,
  questions: ['当前股价是多少？','过去五年股价走势？','年度市盈率？','营收？','股票怎么样？结合经营表现和价格分析。','最新现金流怎么样？','最新公司公告？','TTM 毛利率、营业利润率和净利率是多少？'],
};
function sample(period = '5Y', one = false) {
  const points = [{date: '2021-10-08', at: 1633651200, close: '100', adjusted: '100'},
    {date: '2024-10-08', at: 1728345600, close: '120', adjusted: '120'},
    {date: '2026-10-08', at: 1791417600, close: '110', adjusted: '110'}];
  return {snapshot: {ticker: 'TEST', name: 'Test', currency: 'USD', exchange: 'TEST', basis: 'adjusted_close',
    quote_value: '110', quote_type: 'historical_close', retrieved_at: '2026-10-09T01:00:00Z',
    period, points: one ? [points[0]] : points, warnings: [], source_url: 'https://example.test'},
    statistics: {quote_at: '2026-10-08T20:00:00Z', trading_days: one ? 1 : 3, change_pct: '10', max_drawdown_pct: '-8.333333333', start: points[0].date, end: one ? points[0].date : points.at(-1).date}};
}
vm.runInContext(fs.readFileSync('src/filingcopilot/static/market.js', 'utf8'), context);
check('supplier EPS drawer preserves raw precision, source and normalized-period warning', () => {
  const evidence={kind:'valuation',ticker:'AAPL',snapshot:{ticker:'AAPL',value:'8.72001',currency:'USD',period_end_label:'2026-06-30',retrieved_at:'2026-10-09T09:00:00+08:00',source_sha256:'a'.repeat(64),source_url:'https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/AAPL',stale:true}};
  const before=JSON.stringify(evidence);context.showValuationEvidence(evidence);
  const drawer=select('#drawer-content');
  assert.equal(drawer.children[1].textContent,'8.72 USD/股');
  assert.ok(drawer.children.some(n=>n.textContent.includes('日历季度标准化')));
  assert.ok(drawer.children.some(n=>n.textContent.includes('陈旧缓存')));
  const technical=drawer.children.find(n=>n.tag==='details'&&n.className==='source-technical');
  assert.equal(technical.children.at(-1).href,evidence.snapshot.source_url);
  assert.ok(!drawer.children.some(n=>n.tag==='a'&&n.href===evidence.snapshot.source_url),'raw supplier response belongs inside technical details');
  assert.equal(JSON.stringify(evidence),before);
});
check('annual and supplier TTM multiples remain distinct with bound calculation evidence', () => {
  const root=select('#valuation-preview');root.replaceChildren();
  context.displayAnnualPE({fiscal_year:2025,value:'35.5678',input_ids:['price','annual']},'2026-10-09');
  const calc={operation:'provider_ttm_pe',value:'20.54321',inputs:[{}, {snapshot:{value:'12.43',currency:'USD',period_end_label:'2026-06-30'}}]};
  let opened=null;context.showEvidence=e=>{opened=e;};context.displayProviderPE(calc);
  assert.equal(root.children.length,2);
  assert.equal(root.children[0].children[0].textContent,'年度市盈率 35.57 倍');
  assert.equal(root.children[1].children[0].textContent,'供应商 TTM 市盈率 20.54 倍');
  assert.ok(root.children[1].children[1].textContent.includes('期末标签 2026-06-30'));
  root.children[1].children.at(-1).click();assert.equal(opened,calc);
});
check('figures supporting an analysis also round P/E without changing calculation precision', () => {
  const app=fs.readFileSync('src/filingcopilot/static/app.js','utf8');
  vm.runInContext(app.match(/^function evidencePeriod\(e\).*$/m)[0],context);
  vm.runInContext(app.match(/^function addReferencedFigures[\s\S]*?^}/m)[0],context);
  const calc={id:'pe',kind:'calculation',operation:'provider_ttm_pe',ticker:'AMZN',label:'供应商TTM口径市盈率',value:'20.48189863234111021',unit:'倍',inputs:[{}, {kind:'valuation',snapshot:{period_end_label:'2026-06-30'}}]};
  const before=JSON.stringify(calc),figures=node('details');
  context.addReferencedFigures(figures,{answer:[{kind:'analysis',evidence_ids:['pe']}]},new Map([['pe',calc]]));
  assert.ok(figures.children[0].children[0].textContent.endsWith('20.48 倍'));
  assert.equal(JSON.stringify(calc),before);
});
check('prepared uncited TTM figures retain dates, precision and working evidence drawers', () => {
  const fact={kind:'fact',start:'2026-01-01',end:'2026-06-30'};
  const trailing={id:'ttm',kind:'calculation',operation:'trailing_sum',ticker:'AMZN',label:'净利润',value:'999.12345',unit:'USD',inputs:[fact,fact,{kind:'fact',start:'2025-01-01',end:'2025-06-30'}]};
  const before=JSON.stringify(trailing),figures=node('details');
  const result={answer:[],trace:[{stage:'ttm_preparation',result:{evidence:[{id:'ttm'},{id:'unavailable'}]}}]};
  let opened=null;context.showEvidence=e=>{opened=e;};
  context.addReferencedFigures(figures,result,new Map([['ttm',trailing]]));
  assert.equal(figures.children.length,1);
  assert.match(figures.children[0].children[0].textContent,/TTM 2025-07-01 至 2026-06-30.*999.12/);
  figures.children[0].children[1].click();assert.equal(opened,trailing);
  assert.equal(JSON.stringify(trailing),before);
  result.answer=[{kind:'calculation',evidence_ids:['ttm']}];
  const duplicate=node('details');context.addReferencedFigures(duplicate,result,new Map([['ttm',trailing]]));
  assert.equal(duplicate.children.length,0);
});
select('#chart-scale').value = 'linear'; select('#ticker').value = 'TEST'; select('#as-of').value = '2026-10-09';
const data = sample(), original = JSON.stringify(data);
context.displayMarket(data);
check('price-page source opens the readable frozen snapshot without a provider request',()=>{
  const source=select('#market-source').children.find(n=>n.tag==='button');
  assert.ok(source,'source entry must be a local evidence button, not a raw API link');
  const count=calls.length;source.click();
  assert.match(select('#drawer-content').children[0].textContent,/TEST/);
  assert.equal(calls.length,count);assert.equal(JSON.stringify(data),original);
});
check('chart rendering keeps source and statistics untouched', () => {
  assert.equal(JSON.stringify(data), original);
  assert.equal(select('#market-price').textContent, '110.00');
  assert.equal(select('#market-return').textContent, '+10.00%');
});
check('newer quote and older daily curve disclose distinct coverage without adding a point', () => {
  const lag=sample();lag.snapshot.quote_type='provider_quote';
  lag.snapshot.points.at(-1).date='2026-10-07';lag.statistics.end='2026-10-07';
  lag.frozen=true;
  const before=JSON.stringify(lag);context.displayMarket(lag);
  const note=select('#chart-coverage-note');
  assert.match(note.textContent,/2026-10-07/);
  assert.match(note.textContent,/2026-10-08/);
  assert.match(note.textContent,/尚未包含/);
  assert.equal(note.classList.contains('hidden'),false);
  assert.equal(JSON.stringify(lag),before);
  context.displayMarket(data);
  assert.equal(note.classList.contains('hidden'),true);
});
check('coverage notice preserves stale/frozen messages and clears for historical quotes', () => {
  const lag=sample();lag.snapshot.quote_type='provider_quote';lag.statistics.end='2026-10-07';
  lag.snapshot.stale=true;context.displayMarket(lag);
  assert.match(select('#market-status').textContent,/已保存行情/);
  assert.equal(select('#market-status').classList.contains('company-failed'),true);
  assert.match(select('#chart-coverage-note').textContent,/尚未包含/);
  lag.frozen=true;context.displayMarket(lag);
  assert.match(select('#market-status').textContent,/研究时保存/);
  lag.snapshot.quote_type='historical_close';context.displayMarket(lag);
  assert.equal(select('#chart-coverage-note').classList.contains('hidden'),true);
  assert.equal(select('#chart-coverage-note').textContent,'');
  context.displayMarket(data);
});
check('gross/net capital drawer notes and TTM net period stay distinct', () => {
  const app=fs.readFileSync('src/filingcopilot/static/app.js','utf8');
  vm.runInContext(app.match(/^function cashCapexNote\(e\).*$/m)[0],context);
  vm.runInContext(app.match(/^function renderCapexBasis[\s\S]*?^}/m)[0],context);
  const gross={id:'g',kind:'fact',metric:'cash_capex',ticker:'AMZN',value:'120',unit:'USD',start:'2026-01-01',end:'2026-06-30',filing:{form:'10-Q'}};
  const receipts={...gross,id:'r',metric:'ppe_sale_incentive_proceeds',value:'20'};
  const net={id:'n',kind:'calculation',operation:'net_cash_capex',ticker:'AMZN',value:'100',unit:'USD',inputs:[gross,receipts]};
  assert.match(context.cashCapexNote(gross),/毛额.*尚未扣除/);
  assert.match(context.cashCapexNote(net),/净额＝购置毛额/);
  const trailing={operation:'trailing_sum',inputs:[{kind:'fact',start:'2025-01-01',end:'2025-12-31'},gross,{kind:'fact',start:'2025-01-01',end:'2025-06-30'}]};
  assert.equal(context.evidencePeriod({...net,inputs:[trailing,trailing]}),'TTM 2025-07-01 至 2026-06-30');
  const result={evidence:[gross,receipts,net],trace:[{result:{capex_basis:{gross_id:'g',receipts_id:'r',net_id:'n'}}}]};
  const before=JSON.stringify(result),root=node();let opened=null;context.showEvidence=e=>{opened=e;};
  context.renderCapexBasis(root,result);
  assert.equal(root.children[0].children[0].textContent,'现金 PP&E 购置：毛额与净额');
  assert.match(root.children[0].children[3].children[0].textContent,/购置净额.*100/);
  root.children[0].children[3].children[1].click();assert.equal(opened,net);
  assert.equal(JSON.stringify(result),before);
});
check('frozen partial capex records show retained inputs and an explicit net-query action', () => {
  const gross={id:'g-old',kind:'fact',metric:'cash_capex',ticker:'AMZN',value:'120',unit:'USD',start:'2026-01-01',end:'2026-06-30',filing:{form:'10-Q'}};
  const receipts={...gross,id:'r-old',metric:'ppe_sale_incentive_proceeds',value:'20'};
  const result={evidence:[{id:'fcf-old',kind:'calculation',inputs:[gross,receipts]}],trace:[{result:{capex_basis:{gross_id:'g-old',receipts_id:'r-old',net_id:'net-missing'}}}]};
  const before=JSON.stringify(result),root=node();context.renderCapexBasis(root,result);
  const rows=root.children[0].children;
  assert.ok(rows.some(n=>n.children[0]?.textContent?.includes('购置毛额')));
  assert.ok(rows.some(n=>n.children[0]?.textContent?.includes('出售及激励收款')));
  assert.ok(rows.some(n=>n.textContent.includes('未保留购置净额计算')));
  rows.find(n=>n.textContent==='查询毛额与净额').click();
  assert.match(select('#question').value,/毛额、净额/);
  assert.equal(JSON.stringify(result),before,'historical evidence must remain unchanged');
});
check('keyboard chart navigation exposes first, last, and adjacent trade dates', () => {
  const svg = select('#price-chart'), key = key => svg.onkeydown({key, preventDefault() {}});
  key('Home'); assert.match(select('#chart-tooltip').textContent, /2021-10-08/);
  key('ArrowRight'); assert.match(select('#chart-tooltip').textContent, /2024-10-08/);
  key('End'); assert.match(select('#chart-tooltip').textContent, /2026-10-08/);
  key('ArrowRight'); assert.match(select('#chart-tooltip').textContent, /2026-10-08/);
});
check('pointer and touch selection clamps at the chart boundaries', () => {
  const svg = select('#price-chart');
  svg.onpointermove({clientX: -100}); assert.match(select('#chart-tooltip').textContent, /2021-10-08/);
  svg.onpointerdown({clientX: 1000}); assert.match(select('#chart-tooltip').textContent, /2026-10-08/);
});
check('logarithmic scale keeps navigation and currency labels', () => {
  select('#chart-scale').value = 'log'; context.drawMarketChart();
  assert.match(select('#chart-tooltip').textContent, /110\.00 USD/);
  for (const path of select('#price-chart').children.filter(x => x.tag === 'path')) assert.doesNotMatch(path.attributes.d, /NaN|Infinity/);
});
check('long-history linear scale never presents a negative price tick', () => {
  select('#chart-scale').value = 'linear';
  const history = sample('MAX'); history.snapshot.points[0].close = history.snapshot.points[0].adjusted = '0.01';
  context.displayMarket(history);
  for (const label of select('#price-chart').children.filter(x => x.tag === 'text')) assert.doesNotMatch(label.textContent, /−/);
  assert.equal(select('#chart-history-note').classList.contains('hidden'),false);
  assert.match(select('#chart-history-note').textContent,/行情源可用日线.*起点可能晚于上市日期/);
});
check('one trade date has no fabricated return or drawdown', () => {
  context.displayMarket(sample('1M', true));
  assert.equal(select('#chart-history-note').classList.contains('hidden'),true);
  assert.equal(select('#market-return').textContent, '—');
  assert.equal(select('#market-metrics').children[1].children[1].textContent, '—');
  for (const path of select('#price-chart').children.filter(x => x.tag === 'path')) assert.doesNotMatch(path.attributes.d, /NaN|Infinity/);
});
check('financial presets are compact without dropping other supported questions', () => {
  context.renderCapabilities(capabilities);
  const shown = select('#question-presets').children;
  assert.equal(shown.filter(x => x.tag === 'button').length, 4);
  const extra = shown.find(x => x.tag === 'details').children[1].children;
  assert.equal(shown.length - 1 + extra.length, capabilities.questions.length);
  assert.ok(shown.some(x => /TTM/.test(x.textContent)));
});
check('price-only listings advertise questions matching the actual filing capability', () => {
  context.renderCapabilities({...capabilities,filings_supported:false,questions:capabilities.questions.slice(0,2)});
  assert.match(select('.research-intro').textContent,/财报来源尚未接入/);
  assert.match(select('#question').placeholder,/股价/);
  assert.doesNotMatch(select('#question').placeholder,/经营表现/);
  assert.equal(select('#question-presets').children.length,2);
  context.renderCapabilities(capabilities);
});
check('foreign annual text presets and help do not promise numeric financial research', () => {
  context.renderCapabilities({...capabilities,filings_supported:true,annual_text_only:true,
    questions:['现在股价是多少？','过去五年股价涨跌和最大回撤是多少？','年报披露了哪些主要风险？','年报如何描述公司的主要业务与竞争环境？']});
  assert.match(select('.research-intro').textContent,/财务数字仍待接入/);
  assert.match(select('#question').placeholder,/年报/);
  assert.doesNotMatch(select('#question').placeholder,/利润增长|现金流/);
  assert.match(select('.research-hint p').textContent,/20-F.*6-K/);
  assert.equal(select('#question-presets').children.length,4);
  context.renderCapabilities(capabilities);
});
async function run() {
  check('reported ADS valuation displays share basis and convenience currency separately from TTM', () => {
    const evidence={value:'43.910834132',unit:'倍',fiscal_year:2025,input_ids:['market','eps'],formula:'provider USD ADS quote / reported annual diluted ADS EPS (filing convenience USD)'};
    const before=JSON.stringify(evidence);select('#valuation-preview').replaceChildren();context.displayAnnualPE(evidence,'2026-10-09');
    assert.match(select('#valuation-preview').children[0].children[0].textContent,/43\.91 倍/);
    assert.match(select('#valuation-preview').children[0].children[1].textContent,/美元 ADS EPS.*便利换算.*非 TTM/);
    assert.equal(JSON.stringify(evidence),before);
    context.renderCapabilities({...capabilities,filings_supported:true,annual_only:true,annual_financial_metrics:['ads_diluted_eps'],annual_ads_valuation:true,questions:['年度美元 ADS EPS 口径市盈率是多少？']});
    assert.match(select('.research-hint p').textContent,/单独披露.*便利换算.*不是 TTM/);
    assert.equal(context.sourceValue({value:'10.43',unit:'USD/ADS'}).textContent,'10.43 USD/ADS');
    context.renderCapabilities(capabilities);
  });
  check('native annual cashflow prompts show gross payments and pending net adjustments', () => {
    context.renderCapabilities({...capabilities,filings_supported:true,annual_only:true,annual_financial_metrics:['revenue','operating_cash_flow','cash_capex'],annual_cashflow:true,questions:['现在股价是多少？','过去五年股价涨跌和最大回撤是多少？','年报自由现金流、现金资本开支和同比是多少？','年报营收同比和毛利率是多少？']});
    assert.match(select('.research-intro').textContent,/自由现金流/);
    assert.match(select('.research-hint p').textContent,/毛额.*未抵减政府补助.*净资本开支/);
    assert.ok(select('#question-presets').children.some(x=>/自由现金流/.test(x.textContent)));
    context.renderCapabilities(capabilities);
  });
  check('native annual financial prompts distinguish ordinary EPS from ADS valuation', () => {
    context.renderCapabilities({...capabilities,filings_supported:true,annual_only:true,annual_text_only:false,annual_reporting_currency:'TWD',annual_financial_metrics:['revenue','diluted_eps'],questions:['现在股价是多少？','过去五年股价涨跌和最大回撤是多少？','年报营收同比和毛利率是多少？','年报普通股稀释每股收益是多少？','年报披露了哪些主要风险？','年报如何描述主要业务？']});
    assert.match(select('.research-intro').textContent,/新台币.*普通股/);
    assert.match(select('#question').placeholder,/营收同比.*普通股/);
    assert.match(select('.research-hint p').textContent,/20-F.*6-K.*ADS/);
    const shown=select('#question-presets').children;
    assert.equal(shown.filter(x=>x.tag==='button').length,4);
    assert.ok(shown.some(x=>/普通股/.test(x.textContent)));
    context.renderCapabilities(capabilities);
  });
  select('#mode').value = 'agent'; select('#question').value = 'TTM 利润率';
  await context.updateQuestionPlan();
  assert.match(select('#mode-note').textContent, /无需调用模型/);
  assert.equal(select('#question-plan').children.length, 1);
  select('#question-plan').classList.add('company-failed'); select('#question').value = '';
  await context.updateQuestionPlan();
  assert.equal(select('#question-plan').classList.contains('company-failed'), false);
  checks.push('query plan explains tool-only execution and clears stale blocked styling');
  await context.initMarket();
  for (const period of periods) {
    const buttons = select('#chart-periods').children, b = buttons.find(x => x.dataset.period === period);
    b.click(); await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    assert.ok(calls.some(path => path.includes('/api/market/history?') && new URLSearchParams(path.split('?')[1]).get('period') === period));
    assert.equal(b.attributes['aria-pressed'], 'true');
    assert.equal(buttons.filter(x => x.attributes['aria-pressed'] === 'true').length, 1);
  }
  checks.push('all seven chart buttons request the selected period and update selection');
  console.log(JSON.stringify({passed: checks.length, checks, scope: 'exact decimal display and real frontend functions using controlled DOM/network doubles; not provider/model accuracy'}));
}
run().catch(error => {console.error(error); process.exitCode = 1;});
