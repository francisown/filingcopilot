const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const text=fs.readFileSync('src/filingcopilot/static/app.js','utf8'),start=text.indexOf('function requireMatchingRuntime('),end=text.indexOf('async function api',start);
const c={state:{runtimeBlocked:false},$:()=>({disabled:false}),fetch:()=>{throw new Error('must not fetch');},document:{querySelector:()=>({getAttribute:()=> '1.0.4'})}};vm.createContext(c);vm.runInContext(text.slice(start,text.indexOf('function changeView',end)),c);
c.requireMatchingRuntime({version:'1.0.4'});assert.throws(()=>c.requireMatchingRuntime({version:'0.48.0'}),/不匹配/);
assert.throws(()=>c.requireMatchingRuntime({version:'1.0.4',runtime:{needs_restart:true}}),/不匹配/);
assert.equal(c.state.runtimeBlocked,true);
c.document.querySelector=()=>null;c.requireMatchingRuntime({version:'old'});
assert.rejects(c.api('/api/research',{method:'POST'}),/版本不匹配/).then(()=>console.log('Runtime UI: 5 controlled cases; matching/stale/drift versions, old-page compatibility and blocked programmatic request.')).catch(e=>{console.error(e);process.exitCode=1;});
