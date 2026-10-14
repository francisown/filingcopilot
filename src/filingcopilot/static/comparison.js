// Comparisons render frozen evidence IDs. Number is used only for SVG coordinates.
const comparisonState={selected:[{ticker:'MSFT',name:'Microsoft'},{ticker:'AMZN',name:'Amazon'}],result:null,generation:0,lookup:0,busy:false,index:0};
const comparisonColors=['#2463a5','#148878','#b47720','#7b579e'];
const comparisonMetrics=[['revenue','营业收入'],['revenue_growth','营收同比'],['operating_income','营业利润'],['operating_margin','营业利润率'],['net_income','净利润'],['net_margin','净利率'],['operating_cash_flow','经营现金流'],['operating_cash_flow_growth','经营现金流同比'],['cash_capex','现金 PP&E 购置毛额'],['free_cash_flow','自由现金流 · 购置毛额口径'],['annual_pe','年度 EPS 市盈率 · 非 TTM']];
const comparisonValuationMetrics=[['provider_ttm_eps','供应商 TTM 稀释 EPS'],['provider_ttm_pe','供应商 TTM 市盈率']];
$('#comparison-as-of').value=$('#as-of').value;
function comparisonData(result){return result.trace.find(t=>t.stage==='company_comparison')?.result;}
function comparisonSupportsTtm(){const [major,minor]=(state.health.version||'0.0').split('.').map(Number);return major>0||(major===0&&minor>=45);}
function comparisonSelection(){
  $('#comparison-selected').replaceChildren(...comparisonState.selected.map(candidate=>{
    const chip=el('div','comparison-chip');chip.append(el('strong','',candidate.ticker),el('span','',candidate.name||candidate.ticker));
    const remove=el('button','','×');remove.type='button';remove.setAttribute('aria-label','移除 '+candidate.ticker);
    remove.disabled=comparisonState.busy;remove.addEventListener('click',()=>{comparisonState.selected=comparisonState.selected.filter(c=>c.ticker!==candidate.ticker);comparisonSelection();});chip.append(remove);return chip;
  }));
  $('#comparison-run').disabled=comparisonState.busy||comparisonState.selected.length<($('#comparison-benchmark').value?1:2);
  $('#comparison-benchmark').disabled=comparisonState.busy;
  const current=$('#comparison-as-of').value===(state.health.as_of_default||new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,10));
  const ttmSupported=comparisonSupportsTtm();
  $('#comparison-ttm').disabled=comparisonState.busy||!current||!ttmSupported;
  if(!current||!ttmSupported)$('#comparison-ttm').checked=false;
  $('#comparison-ttm-help').textContent=!ttmSupported?'当前服务不支持 TTM 对比；走势与年度数据仍可使用，请打开新版工作台。':current?'可加入当前供应商 TTM EPS 与市盈率；原始响应和公式一并保存。':'历史日期仅比较已公开年度资料与走势；供应商 TTM 历史发布版本尚未接入。';
  $('#comparison-clear').disabled=comparisonState.busy||!comparisonState.selected.length;
  $('#comparison-search').disabled=comparisonState.busy||comparisonState.selected.length>=4;
  $('#comparison-ranges').querySelectorAll('button').forEach(b=>b.disabled=comparisonState.busy);
}
function addComparisonCandidate(candidate){
  if(comparisonState.busy||comparisonState.selected.length>=4)return;
  if(!comparisonState.selected.some(c=>c.ticker===candidate.ticker))comparisonState.selected.push(candidate);
  $('#comparison-matches').replaceChildren();$('#comparison-query').value='';comparisonState.lookup++;comparisonSelection();
  $('#comparison-status').textContent='已选 '+comparisonState.selected.map(c=>c.ticker).join(' / ')+'；两至四只证券，或单只证券加 ETF 基准。';
}
async function searchComparison(){
  const query=$('#comparison-query').value.trim();if(!query)return;
  const generation=++comparisonState.lookup;$('#comparison-search').disabled=true;
  const root=$('#comparison-matches');root.replaceChildren(el('p','company-help','正在查找明确的证券与上市市场…'));
  try{
    const data=await api('/api/stocks/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query,translate:false})});
    if(generation!==comparisonState.lookup)return;
    root.replaceChildren(el('p','company-help',data.match_count?'选择要比较的证券及上市市场。':data.message||'没有匹配结果；请尝试股票代码。'));
    data.candidates.forEach(candidate=>{
      const b=el('button','company-candidate',(candidate.name||candidate.ticker)+' · '+candidate.ticker+(candidate.exchange?' · '+candidate.exchange:'')+(candidate.currency?' · '+candidate.currency:'')+(candidate.product?' · '+candidate.product:''));
      b.type='button';b.disabled=comparisonState.selected.some(c=>c.ticker===candidate.ticker)||comparisonState.selected.length>=4;
      b.addEventListener('click',()=>addComparisonCandidate(candidate));root.append(b);
    });
  }catch(e){if(generation===comparisonState.lookup)root.replaceChildren(el('p','error',e.message+' 可使用股票代码重试。'));}
  finally{if(generation===comparisonState.lookup)comparisonSelection();}
}
function comparisonAmount(e){return displayScaledAmount(e);}
function comparisonEvidence(result){return new Map(result.evidence.map(e=>[e.id,e]));}
function renderComparisonValuations(data,evidence){
  const root=$('#comparison-valuations'),table=$('#comparison-valuation-table');table.replaceChildren();
  const requested=data.schema_version==='filingcopilot.company-comparison/v3'&&data.include_ttm_valuation===true;
  root.classList.toggle('hidden',!requested);if(!requested)return;
  const head=el('thead'),header=el('tr');header.append(el('th','','当前数据 / 独立期间标签'));
  data.rows.forEach(row=>{const th=el('th');th.scope='col';th.append(el('strong','',row.ticker));header.append(th);});head.append(header);
  const body=el('tbody');
  for(const [key,label] of [['quote','行情报价'],...comparisonValuationMetrics]){
    const tr=el('tr'),th=el('th','',label);th.scope='row';tr.append(th);
    data.rows.forEach(row=>{
      const td=el('td'),e=evidence.get(key==='quote'?row.market_id:row.cells[key]);
      if(e){
        const s=e.snapshot,value=key==='quote'?displayDecimal(s.quote_value,2,2)+' '+s.currency:key==='provider_ttm_eps'?displayDecimal(s.value)+' '+s.currency+'/股':comparisonAmount(e);
        const button=el('button','comparison-number',value);button.type='button';button.setAttribute('aria-label',row.ticker+' '+label+' '+value+' 查看来源');button.addEventListener('click',()=>showEvidence(e));td.append(button);
        if(key==='quote')td.append(el('small','','报价日期 '+new Date(s.quote_at*1000).toLocaleDateString(typeof FilingI18n==='undefined'?'zh-CN':FilingI18n.locale(),{timeZone:s.exchange_timezone})));
        else {const eps=key==='provider_ttm_eps'?s:e.inputs[1].snapshot;td.append(el('small','','供应商期末标签 '+eps.period_end_label));if(key==='provider_ttm_eps')td.append(el('small','',(eps.stale?'陈旧缓存 · ':'')+'EPS 获取 '+eps.retrieved_at.replace('T',' ').replace(/\.\d+(?=Z|[+-]\d{2}:\d{2}$)/,'')));}
      }else td.append(el('span','comparison-missing','—'),el('small','','未取得可用同口径输入；见缺失项'));
      tr.append(td);
    });body.append(tr);
  }table.append(head,body);
}
function renderComparison(result,frozen=false){
  const data=comparisonData(result);if(!data)return;
  if(frozen){comparisonState.generation++;comparisonState.busy=false;}
  comparisonState.result=result;comparisonState.index=0;state.result=result;
  comparisonState.selected=data.tickers.map(ticker=>({ticker,name:state.health.companies?.find(c=>c.ticker===ticker)?.name||ticker}));
  $('#comparison-as-of').value=result.as_of;$('#comparison-period').value=data.period;$('#comparison-benchmark').value=data.benchmark?.ticker||'';$('#comparison-ttm').checked=data.include_ttm_valuation===true;comparisonSelection();
  $('#comparison-result').classList.remove('hidden');$('#comparison-audit').replaceChildren();
  $('#comparison-status').textContent=(frozen?'已打开保存的对比快照；重新运行会建立新记录。':'对比结果已保存。')+' '+(result.elapsed_ms/1000).toFixed(1)+' 秒 · 0 次模型调用。';
  const evidence=comparisonEvidence(result),table=$('#comparison-table');table.replaceChildren();
  renderComparisonValuations(data,evidence);
  const head=el('thead'),header=el('tr');header.append(el('th','','指标 / 实际财年'));
  data.rows.forEach((row,i)=>{
    const th=el('th');th.scope='col';th.append(el('strong','',row.ticker));
    const market=evidence.get(row.market_id)?.snapshot,basis=evidence.get(row.annual_basis_id);
    th.append(el('span','',market?.name||comparisonState.selected[i].name));
    if(basis){th.append(el('span','',basis.start+' — '+basis.end),el('span','','FY'+basis.fiscal_year+' · 公开于 '+basis.filing.filed));}
    else th.append(el('span','','年度财务未取得'));
    header.append(th);
  });head.append(header);const body=el('tbody');
  for(const [key,label] of [['quote','行情报价'],...comparisonMetrics]){
    const tr=el('tr'),th=el('th','',label);th.scope='row';tr.append(th);
    data.rows.forEach(row=>{
      const td=el('td'),e=evidence.get(key==='quote'?row.market_id:row.cells[key]);
      if(e){const b=el('button','comparison-number',key==='quote'?displayDecimal(e.snapshot.quote_value)+' '+e.snapshot.currency:comparisonAmount(e));b.type='button';b.setAttribute('aria-label',row.ticker+' '+label+' '+b.textContent+' 查看来源');b.addEventListener('click',()=>showEvidence(e));td.append(b);
        if(key==='quote')td.append(el('small','',e.snapshot.stale?'保存的行情 · 抓取于 '+e.snapshot.retrieved_at:'报价日期 '+new Date(e.snapshot.quote_at*1000).toLocaleDateString(typeof FilingI18n==='undefined'?'zh-CN':FilingI18n.locale(),{timeZone:e.snapshot.exchange_timezone})));}
      else {td.append(el('span','comparison-missing','—'),el('small','','未取得同口径输入'));}
      tr.append(td);
    });body.append(tr);
  }table.append(head,body);
  const hasAnnual=data.rows.some(row=>row.annual_basis_id);
  $('#comparison-fundamentals').classList.toggle('hidden',!hasAnnual);
  const prices=$('#comparison-price-summary');prices.replaceChildren();prices.classList.toggle('hidden',hasAnnual);
  if(!hasAnnual){
    prices.append(el('strong','','行情比较'),el('p','company-help','本次已取得的走势和报价如下。年度财务资料尚未接入或未取得；查看行情无需下载年报。'));
    data.rows.forEach(row=>{const e=evidence.get(row.market_id);if(!e)return;const s=e.snapshot,b=el('button','comparison-number',row.ticker+' · '+displayDecimal(s.quote_value,2,2)+' '+s.currency+' · 查看报价来源');b.addEventListener('click',()=>showEvidence(e));prices.append(b);});
  }
  const issues=$('#comparison-issues');issues.replaceChildren();data.rows.forEach(row=>{
    if(!row.issues.length)return;const box=el('details','comparison-issue');box.append(el('summary','',row.ticker+' · 查看缺失项与资料刷新状态'));
    row.issues.forEach(issue=>{if(issue.missing?.length)box.append(el('p','company-help','未取得：'+issue.missing.map(key=>[...comparisonMetrics,...comparisonValuationMetrics].find(([id])=>id===key)?.[1]||key).join('、')));box.append(el('p','company-help',issue.message));});issues.append(box);
  });
  const peer=result.trace.find(t=>t.stage==='peer_selection')?.result;
  if(peer){const box=el('div','peer-selection-note');box.append(el('strong','',peer.group_label+' · 候选同行'),el('p','company-help',peer.ticker+' 对照 '+peer.peers.join(' / ')+' · 名录版本 '+peer.registry_version),el('p','company-help',peer.basis),el('p','company-help',peer.note));if(peer.omitted.length)box.append(el('p','company-help','尚未登记、未参与本次对比：'+peer.omitted.join(' / ')+'。可在公司对比中自行选择并准备。'));issues.prepend(box);}
  const actions=$('#comparison-record-actions');actions.replaceChildren(el('span','','知悉日期 '+result.as_of),el('span','','已保存对比 · '+data.rows.length+' 只证券'+(data.benchmark?' · '+data.benchmark.ticker+' 基准':'')));
  const refresh=el('button','secondary','刷新并重新对比');refresh.addEventListener('click',()=>runComparison(true));
  const download=el('button','secondary','导出对比研究包 ↓');download.addEventListener('click',async()=>{
    download.disabled=true;try{const raw=await api('/api/runs/'+encodeURIComponent(result.id)+'/package',undefined,true);const url=URL.createObjectURL(new Blob([raw],{type:'application/json'}));const link=el('a');link.href=url;link.download=result.id+'-package.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){$('#comparison-audit').append(el('p','error',e.message));}finally{download.disabled=false;}
  });
  const audit=el('button','secondary','复核对比数据');audit.addEventListener('click',async()=>{
    audit.disabled=true;try{const report=await api('/api/runs/'+encodeURIComponent(result.id)+'/audit');const root=$('#comparison-audit');root.replaceChildren(el('strong','',report.status==='consistent'?'数据与计算一致性复核通过':'复核发现不一致'));
      root.append(el('p','company-help',report.checks_passed+' 项检查通过 · '+report.checks_failed+' 项不一致；走势按原日线重算，表格检查指标和期间。'));
      if(report.source_validation!=='matched'||report.raw_sources.missing||report.market_sources.missing)root.append(el('p','company-help','部分原始来源未完成独立缓存比对；展开记录查看缺口。'));
      const details=el('details');details.append(el('summary','','查看复核记录'),el('pre','',JSON.stringify(report,null,2)));root.append(details);
    }catch(e){$('#comparison-audit').replaceChildren(el('p','error',e.message));}finally{audit.disabled=false;}
  });actions.append(refresh,download,audit);$('#comparison-method').textContent=data.note;
  $('#comparison-ranges').replaceChildren(...['1Y','5Y','MAX'].map(period=>{const b=el('button',period===data.period?'active':'',periodLabels[period]);b.dataset.period=period;b.type='button';b.setAttribute('aria-pressed',String(period===data.period));b.addEventListener('click',()=>{$('#comparison-period').value=period;runComparison(false,false);});return b;}));
  drawComparisonChart();
}
function comparisonSeriesColor(series,index,data){return series.ticker===data.benchmark?.ticker?'#64748b':comparisonColors[index];}
function renderComparisonBenchmark(data){
  const root=$('#comparison-benchmark-summary');root.replaceChildren();root.classList.toggle('hidden',!data.benchmark);if(!data.benchmark)return;
  const b=data.benchmark,e=comparisonEvidence(comparisonState.result).get(b.market_id),series=data.chart.series.find(s=>s.ticker===b.ticker);
  root.append(el('strong','','基准 · '+b.ticker+' '+b.name));
  const info=el('a','watch-source','基金官方说明 ↗');info.href=b.source_url;info.target='_blank';info.rel='noopener noreferrer';root.append(info);
  if(e){const source=el('button','cite',series?'区间涨跌 '+displayDecimal(series.change_pct)+'% · 查看行情来源':'基准报价 '+displayDecimal(e.snapshot.quote_value)+' '+e.snapshot.currency+' · 查看来源');source.addEventListener('click',()=>showEvidence(e));root.append(source);}
  for(const r of data.chart.relative||[]){const n=Number(r.difference_pp);root.append(el('span','benchmark-difference',r.ticker+' 相对 '+b.ticker+'：'+(n>0?'+':'')+displayDecimal(r.difference_pp)+' 个百分点'));}
  b.issues.forEach(i=>root.append(el('p','watch-error',i.message)));
  if(e&&!series)root.append(el('p','company-help','共同日期不足，尚未计算相对涨跌差；已保留基准来源。'));
  root.append(el('p','company-help','ETF 行情基准，非指数点位。涨跌差使用相同共同日期，各自币种未换汇；不是年化收益或风险调整 alpha。'));
}
function drawComparisonChart(){
  if(!comparisonState.result)return;const data=comparisonData(comparisonState.result),chart=data.chart,svg=$('#comparison-chart');
  svg.replaceChildren();$('#comparison-legend').replaceChildren();$('#comparison-tooltip').replaceChildren();
  svg.onpointermove=null;svg.onpointerdown=null;svg.onkeydown=null;
  renderComparisonBenchmark(data);
  $('#comparison-scale').disabled=!chart.found;
  if(!chart.found){$('#comparison-window').textContent=chart.reason;$('#comparison-chart-note').textContent='已保留下方可用资料，调整日期或重新获取行情后再对比。';svg.classList.add('hidden');return;}
  svg.classList.remove('hidden');
  $('#comparison-window').textContent=chart.start+' — '+chart.end+' · '+chart.common_days.toLocaleString()+' 个共同观察日期 · 首日 = 100';
  const basis=chart.basis==='adjusted_close'?'供应商复权收盘价':chart.basis==='split_adjusted_close'?'拆股调整收盘价（未按股息调整）':'未复权 Close 收盘价';
  $('#comparison-chart-note').textContent=(chart.warning||'各自币种的价格序列；未换汇，收盘时点未同步。')+(data.period==='MAX'?' 全部历史以最晚上市或最晚可用数据的共同起点比较。':'')+' '+basis+'。 '+($('#comparison-scale').value==='log'?'对数刻度：相同百分比变化对应相同高度，便于比较涨幅悬殊的长期走势。':'线性刻度：相同指数变化对应相同高度。');
  const width=Math.max(320,Math.round(svg.clientWidth||900)),height=width<500?270:310,left=58,right=22,top=18,bottom=40;
  const log=$('#comparison-scale').value==='log',transform=value=>log?Math.log(Number(value)):Number(value);
  svg.setAttribute('viewBox','0 0 '+width+' '+height);svg.setAttribute('aria-label',chart.series.map(s=>s.ticker).join('、')+' 走势对比，'+(log?'对数':'线性')+'刻度，首日 100，'+chart.start+' 至 '+chart.end+'。左右方向键、Home 和 End 查看共同日期。');
  let min=Infinity,max=-Infinity;chart.series.forEach(s=>s.points.forEach(p=>{min=Math.min(min,transform(p.value));max=Math.max(max,transform(p.value));}));const pad=(max-min)*.08||(log?Math.log(1.1):8);min-=pad;max+=pad;if(!log)min=Math.max(0,min);
  const first=Date.parse(chart.start),last=Date.parse(chart.end),x=p=>left+(Date.parse(p.date)-first)/(last-first)*(width-left-right),y=p=>top+(max-transform(p.value))/(max-min)*(height-top-bottom);
  for(let i=0;i<=4;i++){const yy=top+i/4*(height-top-bottom),value=max-i/4*(max-min),index=log?Math.exp(value):value;
    const label=index>=1e8?displayDecimal(index/1e8)+' 亿':index>=1e4?displayDecimal(index/1e4)+' 万':displayDecimal(index,index>=100?0:2);
    svg.append(svgEl('line',{x1:left,x2:width-right,y1:yy,y2:yy,stroke:'#e2e8f0'}),svgEl('text',{x:left-9,y:yy+4,'text-anchor':'end',fill:'#64748b','font-size':12},label));}
  const baseline=y({value:'100'});svg.append(svgEl('line',{x1:left,x2:width-right,y1:baseline,y2:baseline,stroke:'#94a3b8','stroke-dasharray':'3 5','data-kind':'baseline'}),svgEl('text',{x:width-right,y:baseline-6,'text-anchor':'end',fill:'#64748b','font-size':11},'首日 100'));
  const ticks=width<500?2:4;for(let i=0;i<=ticks;i++){const p=chart.series[0].points[Math.round(i/ticks*(chart.common_days-1))];svg.append(svgEl('text',{x:x(p),y:height-12,'text-anchor':i===0?'start':i===ticks?'end':'middle',fill:'#64748b','font-size':12},p.date.slice(0,7)));}
  chart.series.forEach((s,i)=>{
    const color=comparisonSeriesColor(s,i,data),isBenchmark=s.ticker===data.benchmark?.ticker,path=s.points.map((p,j)=>(j?'L':'M')+x(p).toFixed(2)+','+y(p).toFixed(2)).join(' ');svg.append(svgEl('path',{d:path,fill:'none',stroke:color,'stroke-width':2,'vector-effect':'non-scaling-stroke',...(isBenchmark?{'stroke-dasharray':'5 4','data-kind':'benchmark'}:{})}));
    const legend=el('div','comparison-legend-item');legend.style.setProperty('--series-color',color);legend.append(el('strong','',s.ticker+(isBenchmark?' · ETF 基准':'')),el('span','',displayDecimal(s.change_pct)+'% · '+s.currency),el('small','',s.omitted_days?'共同日期过滤 '+s.omitted_days+' 个日线点':'全部日线均有共同日期'));$('#comparison-legend').append(legend);
  });
  const cursor=svgEl('line',{x1:left,x2:left,y1:top,y2:height-bottom,stroke:'#94a3b8','stroke-dasharray':'4 4'}),dots=chart.series.map((s,i)=>svgEl('circle',{cx:left,cy:baseline,r:3.5,fill:comparisonSeriesColor(s,i,data),stroke:'#fff','stroke-width':1.5}));svg.append(cursor,...dots);
  function inspect(index){comparisonState.index=Math.max(0,Math.min(chart.common_days-1,index));const p=chart.series[0].points[comparisonState.index];cursor.setAttribute('x1',x(p));cursor.setAttribute('x2',x(p));const tooltip=$('#comparison-tooltip');tooltip.replaceChildren(el('strong','',p.date));chart.series.forEach((s,i)=>{const value=s.points[comparisonState.index];dots[i].setAttribute('cx',x(value));dots[i].setAttribute('cy',y(value));const text=el('span','',s.ticker+' 指数 '+displayDecimal(value.value)+' · '+displayDecimal(value.price)+' '+s.currency);text.style.color=comparisonSeriesColor(s,i,data);tooltip.append(text);});}
  svg.onpointermove=event=>{const rect=svg.getBoundingClientRect(),px=(event.clientX-rect.left)/rect.width*width,target=first+(px-left)/(width-left-right)*(last-first);const points=chart.series[0].points;let lo=0,hi=points.length-1;while(lo<hi){const mid=Math.floor((lo+hi)/2);if(Date.parse(points[mid].date)<target)lo=mid+1;else hi=mid;}if(lo>0&&Math.abs(Date.parse(points[lo-1].date)-target)<Math.abs(Date.parse(points[lo].date)-target))lo--;inspect(lo);};
  svg.onpointerdown=svg.onpointermove;
  svg.onkeydown=event=>{const keys={ArrowLeft:comparisonState.index-1,ArrowRight:comparisonState.index+1,Home:0,End:chart.common_days-1};if(event.key in keys){event.preventDefault();inspect(keys[event.key]);}};
  inspect(comparisonState.index);
}
async function runComparison(refreshPrices=false,refreshSources=null){
  if(comparisonState.busy||comparisonState.selected.length<($('#comparison-benchmark').value?1:2))return;
  const asOf=$('#comparison-as-of').value;if(!asOf){$('#comparison-status').textContent='请选择知悉日期。';return;}
  const generation=++comparisonState.generation;comparisonState.busy=true;comparisonSelection();$('#comparison-status').textContent='正在获取各公司行情并核对年度资料；首次获取可能需要几十秒。新结果就绪后更新下方内容。';
  try{const result=await api('/api/comparison',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tickers:comparisonState.selected.map(c=>c.ticker),benchmark:$('#comparison-benchmark').value||null,as_of:asOf,period:$('#comparison-period').value,refresh_prices:refreshPrices,refresh_sources:refreshSources??$('#comparison-refresh-sources').checked,...(comparisonSupportsTtm()?{include_ttm_valuation:$('#comparison-ttm').checked}:{})})});if(generation===comparisonState.generation)renderComparison(result);}
  catch(e){if(generation===comparisonState.generation)$('#comparison-status').textContent=e.message+' 已有对比记录仍可查看，请调整输入或重试。';}
  finally{if(generation===comparisonState.generation){comparisonState.busy=false;comparisonSelection();}}
}
$('#comparison-scale').addEventListener('change',drawComparisonChart);
$('#comparison-benchmark').addEventListener('change',()=>{comparisonSelection();$('#comparison-status').textContent='已更改下一次对比的 ETF 基准；点击开始对比生成新记录。当前保存结果不变。';});
$('#comparison-as-of').addEventListener('change',comparisonSelection);
$('#comparison-ttm').addEventListener('change',()=>$('#comparison-status').textContent='已更改下一次对比的估值选项；点击开始对比生成新记录。当前保存结果不变。');
$('#comparison-search').addEventListener('click',searchComparison);
$('#comparison-query').addEventListener('keydown',e=>{if(e.key==='Enter')searchComparison();});
$('#comparison-query').addEventListener('input',()=>{comparisonState.lookup++;$('#comparison-matches').replaceChildren();comparisonSelection();});
$('#comparison-clear').addEventListener('click',()=>{comparisonState.selected=[];comparisonSelection();$('#comparison-status').textContent='查找并添加 2 至 4 只股票后开始对比。';});
$('#comparison-run').addEventListener('click',()=>runComparison());
new ResizeObserver(()=>{if(!$('#comparison-view').classList.contains('hidden'))drawComparisonChart();}).observe($('#comparison-chart'));
comparisonSelection();
