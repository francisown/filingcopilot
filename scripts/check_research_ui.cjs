// Offline browser-logic tests with controlled DOM/network doubles, no provider or LLM calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/filingcopilot/static/app.js', 'utf8');
const functions = source.slice(source.indexOf('let researchTimer='), source.indexOf("$('#research').addEventListener('click',async()=>{"));

function harness(responses) {
  const nodes = new Map(), saved = new Map(), rendered = [], calls = [];
  function node() {
    const hidden = new Set(['hidden']);
    return {
      disabled: false, textContent: '', value: '', options: [],
      classList: {add: x => hidden.add(x), remove: x => hidden.delete(x), contains: x => hidden.has(x), toggle: (x, value) => value ? hidden.add(x) : hidden.delete(x)},
      replaceChildren(...children) { this.children = children; },
      addEventListener(type, fn) { this[type] = fn; }, scrollIntoView() {},
      prepend(option) { this.options.unshift(option); },
    };
  }
  const select = selector => {if (!nodes.has(selector)) nodes.set(selector, node()); return nodes.get(selector);};
  const context = {
    $: select, state: {health:{research_session:'current-session'}}, document: {querySelectorAll: () => [select('#research')]},
    crypto:{randomUUID:()=> 'browser-request-test-0001'},
    localStorage: {getItem: k => saved.get(k) || null, setItem: (k, v) => saved.set(k, v), removeItem: k => saved.delete(k)},
    setInterval: () => 1, clearInterval: () => {},
    setTimeout: (fn, duration) => {if (duration === 1000) Promise.resolve().then(fn);return 1;}, clearTimeout: () => {},
    Date, Promise, AbortController,
    el: (tag, cls, text) => ({tag, text}),
    renderResult: result => rendered.push(result),
    api: async (path, options) => {
      calls.push({path, method: options?.method || 'GET',headers:options?.headers,body:options?.body,savedAtCall:[...saved.values()]});
      const response = responses.shift();
      if (response instanceof Error) throw response;
      assert.ok(response, 'unexpected extra API call');
      return response;
    },
  };
  vm.createContext(context);vm.runInContext(functions, context);
  return {context, select, saved, rendered, calls};
}

const job = {id: 'test-job', status: 'running', elapsed_ms: 100, events: [{stage: 'verify', message: '正在核验。', elapsed_ms: 100}], result: null};
const result = {id: 'saved-result', answer: [], trace: [], status: 'abstained'};
async function check() {
  const periodContext = vm.createContext({Date});
  vm.runInContext(source.slice(source.indexOf('function evidencePeriod('), source.indexOf('function showEvidence(')), periodContext);
  const fact = (start,end) => ({kind:'fact', start, end, fiscal_year:2026, filing:{form:'10-Q'}});
  const trailing = (yearly=false) => ({kind:'calculation', operation:'trailing_sum', inputs:yearly?[{...fact('2025-07-01','2026-06-30'),filing:{form:'10-K'}}]:[fact('2024-09-29','2025-09-27'),fact('2025-09-28','2026-06-27'),fact('2024-09-29','2025-06-28')]});
  assert.equal(periodContext.evidencePeriod({operation:'ratio',inputs:[trailing(),trailing()]}),'TTM 2025-06-29 至 2026-06-27');
  assert.equal(periodContext.evidencePeriod({operation:'ratio',inputs:[trailing(true),trailing(true)]}),'TTM 2025-07-01 至 2026-06-30');
  assert.equal(periodContext.evidencePeriod({operation:'ratio',inputs:[trailing(),trailing(true)]}),'TTM 期间不一致');
  const dropped = harness([new Error('temporary'), new Error('temporary'), new Error('temporary'), {...job, status: 'completed', result}]);
  let observedReads=0;dropped.context.Date={now:()=>++observedReads};
  dropped.context.state.researchJob = job.id;
  dropped.saved.set('filingcopilot.activeResearch', JSON.stringify({id: job.id}));
  await dropped.context.watchResearchJob(job);
  assert.equal(dropped.select('#resume-research').classList.contains('hidden'), false);
  assert.equal(dropped.context.state.researchRunning, true);
  assert.equal(dropped.rendered.length, 0);
  assert.equal(dropped.saved.size, 1, 'lost connection must not discard the task ID');
  assert.equal(observedReads,1,'failed polling must not reset the last observed elapsed time');
  assert.equal(dropped.select('#busy-title').textContent,'研究进度连接中断');
  await dropped.context.resumeResearchJob();
  assert.equal(dropped.context.state.researchRunning, false);
  assert.equal(dropped.rendered[0].id, result.id);
  assert.equal(dropped.saved.size, 0);

  const missing = harness([new Error('研究任务不存在或服务已重启。已完成结果可在研究记录中查看。')]);
  missing.context.state.researchJob = job.id;
  missing.saved.set('filingcopilot.activeResearch', JSON.stringify({id: job.id}));
  await missing.context.watchResearchJob(job);
  assert.equal(missing.context.state.researchRunning, false);
  assert.equal(missing.saved.size, 0);
  assert.ok(missing.select('#error').textContent.includes('研究记录'));

  const cancel = harness([{...job, status: 'cancelled', result}]);
  cancel.context.state.researchJob = job.id;
  cancel.select('#resume-research').classList.remove('hidden');
  await cancel.select('#cancel-research').click();
  assert.equal(cancel.calls[0].method, 'POST');
  assert.equal(cancel.context.state.researchRunning, false);
  assert.equal(cancel.rendered.length, 1);

  const deadline = harness([{...job, status:'timed_out', result}]);
  deadline.context.state.researchJob=job.id;
  await deadline.context.watchResearchJob(job);
  assert.equal(deadline.rendered.length,1);assert.equal(deadline.context.state.researchRunning,false);
  const waiting = harness([]);
  waiting.context.showResearchJob({...job,status:'timing_out',remaining_seconds:0});
  assert.ok(waiting.select('#busy-title').textContent.includes('时间已用完'));assert.equal(waiting.select('#cancel-research').disabled,true);
  waiting.context.showResearchJob({...job,remaining_seconds:10.1});
  assert.ok(waiting.select('#busy-detail').textContent.includes('剩余 11 秒'));

  const timedOut = harness([Object.assign(new Error('timeout'), {name: 'AbortError'})]);
  await assert.rejects(timedOut.context.researchJobApi('/api/research/jobs/test'), /连接超时/);
  const request={question:'AMZN 五年股价',ticker:'AMZN',as_of:'2026-10-10',mode:'reference'};
  const ackLost=harness([Object.assign(new Error('gateway unavailable'),{status:503}),{...job,status:'completed',result}]);
  await ackLost.context.startResearchJob(request);
  assert.equal(ackLost.calls[0].method,'POST');
  assert.equal(JSON.parse(ackLost.calls[0].savedAtCall[0]).key,'browser-request-test-0001','persist before the first POST');
  assert.equal(ackLost.calls[0].headers['Idempotency-Key'],'browser-request-test-0001');
  assert.equal(ackLost.calls[0].headers['X-Research-Session'],'current-session');
  assert.equal(ackLost.calls[1].method,'GET');assert.ok(ackLost.calls[1].path.includes('/recover?key='));
  assert.equal(ackLost.rendered.length,1);assert.equal(ackLost.saved.size,0);

  const uncertain=harness([new Error('connection lost'),new Error('still disconnected'),{...job,status:'completed',result}]);
  await uncertain.context.startResearchJob(request);
  assert.equal(uncertain.context.state.researchRunning,true);assert.equal(uncertain.saved.size,1);
  assert.equal(uncertain.select('#cancel-research').classList.contains('hidden'),true,'unknown job cannot be claimed cancelled');
  assert.equal(uncertain.select('#leave-research').classList.contains('hidden'),false);
  await uncertain.context.resumeResearchJob();
  assert.equal(uncertain.calls.filter(c=>c.method==='POST').length,1,'reconnect must never submit new research');
  assert.equal(uncertain.rendered.length,1);assert.equal(uncertain.saved.size,0);

  const pendingReload=harness([{...job,status:'completed',result}]);
  pendingReload.saved.set('filingcopilot.activeResearch',JSON.stringify({key:'browser-request-test-0001',session:'current-session',request}));
  await pendingReload.context.resumeResearchJob();
  assert.equal(pendingReload.select('#question').value,request.question);assert.equal(pendingReload.calls[0].method,'GET');assert.equal(pendingReload.rendered.length,1);

  const neverAccepted=harness([new Error('connection lost'),Object.assign(new Error('未找到启动记录；不会再次提交。'),{status:404})]);
  await neverAccepted.context.startResearchJob(request);
  assert.equal(neverAccepted.context.state.researchRunning,true);assert.equal(neverAccepted.saved.size,1);
  await neverAccepted.select('#leave-research').click();
  assert.equal(neverAccepted.saved.size,0);assert.equal(neverAccepted.context.state.researchRunning,false);
  assert.ok(neverAccepted.select('#error').textContent.includes('未取消'));assert.equal(neverAccepted.calls.length,2);

  const full=harness([Object.assign(new Error('队列已满'),{status:429})]);
  await assert.rejects(full.context.startResearchJob(request),/队列已满/);
  assert.equal(full.saved.size,0);assert.equal(full.context.state.researchRunning,false);assert.equal(full.calls.length,1);
  const restarted=harness([Object.assign(new Error('服务已重启，请查看研究记录。'),{status:409})]);
  restarted.saved.set('filingcopilot.activeResearch',JSON.stringify({key:'browser-request-test-0001',session:'old-session',request}));
  await restarted.context.resumeResearchJob();assert.equal(restarted.saved.size,0);assert.equal(restarted.context.state.researchRunning,false);assert.ok(restarted.select('#error').textContent.includes('研究记录'));
  console.log(JSON.stringify({scope: 'controlled DOM and network doubles; no real browser fault injection', checks: ['three polling failures retain task and show reconnect', 'reconnect delivers one saved result', 'missing/restarted job clears stale task with history action', 'cancel from disconnected state uses POST and displays retained result', 'request timeout has actionable message', 'TTM ratios preserve fiscal-week and annual fallback windows', 'mismatched trailing windows are visible', 'deadline completion delivers saved result and stops polling', 'in-flight expiry has distinct saving state', 'remaining budget is visible','lost acknowledgement recovers original job without a second POST','uncertain start retains request and offers read-only reconnect','page reload restores a pending request before acknowledgement','unknown start can end page waiting without claiming cancellation','definitive queue rejection clears pending request','restarted server clears pending session with history action'], passed: 16}));
}
check().catch(error => {console.error(error);process.exitCode = 1;});
