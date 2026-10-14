// Company-specific reporting currency must reach the actual question hints.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/filingcopilot/static/market.js', 'utf8');
const nodes = new Map();
const select = key => {
  if (!nodes.has(key)) nodes.set(key, {textContent:'',replaceChildren(){}});
  return nodes.get(key);
};
const context = {$:select,state:{},el:()=>({addEventListener(){}})};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function renderCapabilities'),source.indexOf('async function initMarket')),context);
const capabilities = {filings_supported:true,annual_only:true,annual_financial_metrics:['revenue'],annual_cashflow:true,scope:'annual',pending:[],questions:[]};
context.renderCapabilities({...capabilities,annual_reporting_currency:'CNY'});
assert.match(select('.research-intro').textContent,/人民币/);
assert.doesNotMatch(select('.research-intro').textContent,/新台币/);
context.renderCapabilities({...capabilities,annual_reporting_currency:'TWD'});
assert.match(select('.research-intro').textContent,/新台币/);
context.renderCapabilities({...capabilities,annual_reporting_currency:'EUR'});
assert.match(select('.research-intro').textContent,/EUR/);
context.renderCapabilities(capabilities);
assert.match(select('.research-intro').textContent,/报表原币/);
console.log('Reporting-currency hints: CNY/TWD/other/missing passed');
