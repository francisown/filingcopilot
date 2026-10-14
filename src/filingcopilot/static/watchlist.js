// Local preferences do not modify frozen research. Quotes retain source dates and currencies.
const watchState={items:[],limit:30,quotes:new Map(),rows:new Map(),selected:new Set(),revision:0,lookup:0,quoteGeneration:0,controllers:new Set(),busy:false,loading:false};
function updateWatchCurrent(){const ticker=$('#ticker').value,unresolved=companyInput.value.trim()&&!companyState.selected,saved=watchState.items.some(i=>i.ticker===ticker);$('#watch-save-current').disabled=watchState.busy||!ticker||unresolved||saved;$('#watch-save-current').textContent=saved?'已加入自选 ☆':'加入自选 ☆';$('#market-compare-benchmark').disabled=!ticker||unresolved;}
function updateWatchControls(){
  $('#watch-count').textContent=watchState.items.length+' / '+watchState.limit;$('#watch-search').disabled=watchState.busy;$('#watch-refresh').disabled=watchState.busy||!watchState.items.length;$('#watch-refresh').textContent=watchState.loading?'停止刷新':'刷新行情';
  $('#watch-compare').textContent='对比所选（'+watchState.selected.size+'）';$('#watch-compare').disabled=watchState.busy||watchState.selected.size<2;
  $('#watch-rows').querySelectorAll('button').forEach(b=>b.disabled=watchState.busy||(b.dataset.retry==='true'&&watchState.loading));
  $('#watch-rows').querySelectorAll('input[type="checkbox"]').forEach(c=>c.disabled=watchState.busy||!c.checked&&watchState.selected.size>=4);
  $('#watch-matches').querySelectorAll('button').forEach(b=>b.disabled=watchState.busy||watchState.items.some(i=>i.ticker===b.dataset.ticker));updateWatchCurrent();
}
function renderWatchQuote(item){
  const row=watchState.rows.get(item.ticker);if(!row)return;const o=watchState.quotes.get(item.ticker)||{},data=o.data,s=data?.snapshot,stats=data?.statistics;
  row.market.textContent=s?.exchange||(item.exchange&&item.exchange!=='SEC'?item.exchange:'市场资料待行情返回');row.quote.replaceChildren();row.change.replaceChildren();row.time.replaceChildren();
  if(s){row.quote.append(el('strong','watch-price',displayDecimal(s.quote_value,2,2)),el('span','',s.currency));const n=Number(stats.change_pct);row.change.append(el('strong',stats.trading_days>1?(n>=0?'positive':'negative'):'',stats.trading_days>1?(n>0?'+':'')+displayDecimal(stats.change_pct)+'%':'—'),el('small','',stats.start+' — '+stats.end));
    row.time.append(el('span','',stats.quote_at.replace('T',' ')),el('small','',s.stale?'缓存 · 抓取于 '+s.retrieved_at:'抓取于 '+s.retrieved_at));const button=el('button','watch-source','查看行情来源');button.type='button';button.id='watch-source-'+s.ticker;button.addEventListener('click',()=>showMarketEvidence({kind:'market',ticker:s.ticker,snapshot:s}));row.time.append(button);
  }else{row.quote.append(el('strong','watch-price','—'));row.change.append(el('span','','—'));}
  if(o.pending)row.time.append(el('span','company-help','正在读取行情…'));
  if(o.error)row.time.append(el('span','watch-error',o.error+(s?' 当前仍显示上次取得的快照。':' 可点击重试，其他证券继续可用。')));
  if(s?.stale)row.time.append(el('span','watch-error','本次使用缓存，未确认行情源已更新。'));
  if(!s&&!o.pending&&!o.error)row.time.append(el('span','company-help','尚未读取行情，点击刷新或打开行情。'));
}
function renderWatchlist(){
  watchState.selected=new Set([...watchState.selected].filter(t=>watchState.items.some(i=>i.ticker===t)));watchState.rows.clear();const root=$('#watch-rows');root.replaceChildren();$('#watch-empty').classList.toggle('hidden',!!watchState.items.length);$('#watch-table').classList.toggle('hidden',!watchState.items.length);
  watchState.items.forEach(item=>{const tr=el('tr'),checkCell=el('td'),check=el('input');check.type='checkbox';check.checked=watchState.selected.has(item.ticker);check.setAttribute('aria-label','选择 '+item.ticker+' 加入公司对比');check.addEventListener('change',()=>{if(check.checked){if(watchState.selected.size>=4){check.checked=false;$('#watch-status').textContent='一次最多对比四只证券。';return;}watchState.selected.add(item.ticker);}else watchState.selected.delete(item.ticker);updateWatchControls();});checkCell.append(check);
    const identity=el('td'),market=el('small');identity.append(el('strong','watch-ticker',item.ticker),el('span','',item.name),market);if(item.product)identity.append(el('small','',item.product));const quote=el('td','watch-quote'),change=el('td','watch-change'),time=el('td','watch-time'),actions=el('td','watch-actions');
    const open=el('button','secondary','行情');open.setAttribute('aria-label','打开 '+item.ticker+' 行情');open.addEventListener('click',()=>openWatchMarket(item));actions.append(open);
    const benchmark=el('button','secondary','基准对比');benchmark.setAttribute('aria-label','打开 '+item.ticker+' ETF 基准对比');benchmark.addEventListener('click',()=>openWatchComparison([item],item.ticker==='SPY'?'QQQ':'SPY'));actions.append(benchmark);
    if(item.capabilities?.financial_trend?.enabled){const trend=el('button','secondary','财务趋势');trend.setAttribute('aria-label','打开 '+item.ticker+' 财务趋势');trend.addEventListener('click',()=>openWatchTrend(item));actions.append(trend);}else actions.append(el('small','',item.capabilities?.filings_supported===false?'财务来源未接入':'年度数字未接入'));
    const retry=el('button','secondary','重试行情');retry.dataset.retry='true';retry.setAttribute('aria-label','重试 '+item.ticker+' 行情');retry.addEventListener('click',()=>refreshWatchQuotes(true,[item]));const remove=el('button','watch-remove','移除');remove.setAttribute('aria-label','移除自选 '+item.ticker);remove.addEventListener('click',()=>mutateWatchlist('DELETE',item.ticker));actions.append(retry,remove);
    tr.append(checkCell,identity,quote,change,time,actions);root.append(tr);watchState.rows.set(item.ticker,{market,quote,change,time});renderWatchQuote(item);
  });updateWatchControls();
}
function stopWatchQuotes(){watchState.quoteGeneration++;for(const c of watchState.controllers)c.abort();watchState.controllers.clear();watchState.loading=false;for(const item of watchState.items){const q=watchState.quotes.get(item.ticker);if(q?.pending){q.pending=false;q.error='已停止本轮刷新。';renderWatchQuote(item);}}updateWatchControls();}
async function refreshWatchQuotes(force=false,items=watchState.items){
  stopWatchQuotes();if(!items.length)return;const generation=watchState.quoteGeneration,queue=[...items];watchState.loading=true;updateWatchControls();
  async function worker(){while(queue.length&&generation===watchState.quoteGeneration){const item=queue.shift(),prior=watchState.quotes.get(item.ticker)||{},observation={data:prior.data,error:null,pending:true};watchState.quotes.set(item.ticker,observation);renderWatchQuote(item);const controller=new AbortController();watchState.controllers.add(controller);const timer=setTimeout(()=>controller.abort(),45000);
    try{const data=await api('/api/market/history?'+new URLSearchParams({ticker:item.ticker,period:'1M',...(force?{refresh:'true'}:{})}),{signal:controller.signal});
      if(generation!==watchState.quoteGeneration)continue;if(data.snapshot.ticker!==item.ticker)throw new Error('返回的证券与所选代码不一致，未替换报价。');if(item.currency&&data.snapshot.currency!==item.currency)throw new Error('返回币种与上市登记不一致，请核对行情来源。');observation.data=data;
    }catch(e){if(generation===watchState.quoteGeneration)observation.error=e.name==='AbortError'?'本次行情读取超时，请稍后重试。':e.message;}
    finally{clearTimeout(timer);watchState.controllers.delete(controller);if(generation===watchState.quoteGeneration){observation.pending=false;renderWatchQuote(item);}}
  }}
  await Promise.all(Array.from({length:Math.min(3,queue.length)},worker));if(generation===watchState.quoteGeneration){watchState.loading=false;updateWatchControls();}
}
async function loadWatchlist(withQuotes=true){
  if(watchState.busy)return;const revision=++watchState.revision;
  try{const result=await api('/api/watchlist');if(revision!==watchState.revision)return;watchState.items=result.items;watchState.limit=result.limit;renderWatchlist();$('#watch-status').textContent='已读取本机保存的 '+result.items.length+' 只证券；列表不包含持仓或交易。';if(withQuotes)refreshWatchQuotes();}
  catch(e){if(revision===watchState.revision)$('#watch-status').textContent=e.message+' 保存的列表未删除，可重新打开此页重试。';}
}
async function mutateWatchlist(method,ticker){
  if(watchState.busy)return;watchState.busy=true;const revision=++watchState.revision;stopWatchQuotes();updateWatchControls();const current=$('#ticker').value===ticker;$('#watch-status').textContent=method==='DELETE'?'正在移除 '+ticker+'…':'正在核对并保存 '+ticker+'…';
  try{const result=await api('/api/watchlist'+(method==='DELETE'?'/'+encodeURIComponent(ticker):''),method==='DELETE'?{method}:{method,headers:{'Content-Type':'application/json'},body:JSON.stringify({ticker})});if(revision!==watchState.revision)return;watchState.items=result.items;watchState.limit=result.limit;renderWatchlist();$('#watch-status').textContent=method==='DELETE'?'已移除 '+ticker+'，需要时可重新添加。':'已将 '+ticker+' 保存到本机自选列表。';if(current&&$('#ticker').value===ticker)$('#watch-current-status').textContent=method==='DELETE'?'已从自选列表移除。':'已保存到自选列表，可从左侧导航打开。';if(!$('#watchlist-view').classList.contains('hidden'))refreshWatchQuotes();}
  catch(e){$('#watch-status').textContent=e.message+' 列表保留，可重试。';if(current&&$('#ticker').value===ticker)$('#watch-current-status').textContent=e.message+' 可稍后重试加入自选。';}
  finally{watchState.busy=false;updateWatchControls();}
}
async function searchWatchlist(){
  const query=$('#watch-query').value.trim();if(!query||watchState.busy)return;const generation=++watchState.lookup;$('#watch-search').disabled=true;const root=$('#watch-matches');root.replaceChildren(el('p','company-help','正在查找明确的上市证券…'));
  try{const result=await api('/api/stocks/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query,translate:false})});if(generation!==watchState.lookup)return;root.replaceChildren(el('p','company-help',result.match_count?'选择上市代码与币种后添加。':result.message||'未匹配到证券，请输入明确代码重试。'));result.candidates.forEach(c=>{const b=el('button','company-candidate','添加 '+c.name+' · '+c.ticker+(c.exchange&&c.exchange!=='SEC'?' · '+c.exchange:'')+(c.currency?' · '+c.currency:''));b.dataset.ticker=c.ticker;b.addEventListener('click',()=>mutateWatchlist('POST',c.ticker));root.append(b);});}
  catch(e){if(generation===watchState.lookup)root.replaceChildren(el('p','error',e.message+' 可尝试明确的股票代码。'));}
  finally{if(generation===watchState.lookup)updateWatchControls();}
}
function watchCanNavigate(){if(state.researchRunning){$('#watch-status').textContent='研究仍在执行，请等待完成或取消后切换研究公司。';return false;}return true;}
function openWatchMarket(item){if(!watchCanNavigate())return;changeView('desk');clearCompanySelection();$('#as-of').value=state.health.as_of_default||$('#as-of').value;selectCompany(item,'',false);loadCatalog().catch(()=>{});}
function openWatchTrend(item){if(!watchCanNavigate()||!item.capabilities?.financial_trend?.enabled)return;trendState.generation++;trendState.lookup++;trendState.busy=false;trendState.selected=item;$('#trend-query').value='';$('#trend-matches').replaceChildren();$('#trend-result').classList.add('hidden');$('#trend-as-of').value=state.health.as_of_default||$('#as-of').value;changeView('trend');trendControls();runTrend();}
function openWatchComparison(items,benchmark=''){if(!watchCanNavigate()||items.length<(benchmark?1:2))return;comparisonState.generation++;comparisonState.lookup++;comparisonState.busy=false;comparisonState.selected=items;$('#comparison-benchmark').value=benchmark;if(benchmark)$('#comparison-refresh-sources').checked=false;$('#comparison-query').value='';$('#comparison-matches').replaceChildren();$('#comparison-result').classList.add('hidden');$('#comparison-as-of').value=state.health.as_of_default||$('#as-of').value;changeView('comparison');comparisonSelection();runComparison();}
function compareWatchSelection(){openWatchComparison(watchState.items.filter(i=>watchState.selected.has(i.ticker)));}
$('#watch-search').addEventListener('click',searchWatchlist);$('#watch-query').addEventListener('keydown',e=>{if(e.key==='Enter')searchWatchlist();});$('#watch-query').addEventListener('input',()=>{watchState.lookup++;$('#watch-matches').replaceChildren();updateWatchControls();});
$('#watch-refresh').addEventListener('click',()=>watchState.loading?stopWatchQuotes():refreshWatchQuotes(true));$('#watch-compare').addEventListener('click',compareWatchSelection);$('#watch-save-current').addEventListener('click',()=>{if(!$('#watch-save-current').disabled)mutateWatchlist('POST',$('#ticker').value);});
$('#ticker').addEventListener('change',()=>{$('#watch-current-status').textContent='';updateWatchCurrent();});companyInput.addEventListener('input',()=>{$('#watch-current-status').textContent='';updateWatchCurrent();});loadWatchlist(false);
$('#market-compare-benchmark').addEventListener('click',()=>{const ticker=$('#ticker').value;if(!$('#market-compare-benchmark').disabled)openWatchComparison([{ticker,name:companyState.selected?.name||ticker}],ticker==='SPY'?'QQQ':'SPY');});
