// Annual bars reference frozen evidence. Floating point is used only for chart coordinates.
const trendState={selected:{ticker:'AMZN',name:'Amazon'},result:null,generation:0,lookup:0,busy:false,index:0};
const trendMetrics=[['revenue','营业收入'],['operating_income','营业利润'],['net_income','净利润'],['operating_cash_flow','经营现金流'],['free_cash_flow','自由现金流 · 购置毛额'],['cash_capex','现金 PP&E 购置毛额']];
$('#trend-as-of').value=$('#as-of').value;
function trendData(result){return result.trace.find(t=>t.stage==='financial_trend')?.result;}
function trendControls(){
  $('#trend-run').disabled=trendState.busy||!trendState.selected;
  $('#trend-search').disabled=trendState.busy;
  $('#trend-selected').textContent=trendState.selected?trendState.selected.ticker+' · '+(trendState.selected.name||'已选证券'):'请从候选中选择明确的公司与证券代码。';
}
async function searchTrend(){
  const query=$('#trend-query').value.trim();if(!query)return;
  const generation=++trendState.lookup;$('#trend-search').disabled=true;const root=$('#trend-matches');root.replaceChildren(el('p','company-help','正在查找公司及上市市场…'));
  try{const data=await api('/api/stocks/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query,translate:false})});
    if(generation!==trendState.lookup)return;
    root.replaceChildren(el('p','company-help',data.match_count?'请选择公司的年度财务证券代码。':data.message||'未匹配到公司，请输入明确的代码重试。'));
    data.candidates.forEach(candidate=>{const unavailable=candidate.filings_supported===false||candidate.annual_text_only===true&&!candidate.annual_financials;
      const b=el('button','company-candidate',(candidate.name||candidate.ticker)+' · '+candidate.ticker+(candidate.exchange?' · '+candidate.exchange:'')+(unavailable?' · 年度数字未接入':''));b.type='button';b.disabled=unavailable;
      b.addEventListener('click',()=>{trendState.generation++;trendState.selected=candidate;trendState.lookup++;$('#trend-query').value='';root.replaceChildren();$('#trend-result').classList.add('hidden');trendControls();$('#trend-status').textContent='已选择 '+candidate.ticker+'，点击查看财务趋势。';});root.append(b);
    });
    if(data.candidates.some(c=>c.filings_supported===false))root.append(el('p','company-help','仅支持行情的市场可在研究工作台查看价格走势；年度财务请选择已接入的公司。'));
  }catch(e){if(generation===trendState.lookup)root.replaceChildren(el('p','error',e.message+' 可输入证券代码重试。'));}
  finally{if(generation===trendState.lookup)trendControls();}
}
function renderTrend(result,frozen=false){
  const data=trendData(result);if(!data)return;
  if(frozen){trendState.generation++;trendState.busy=false;}
  trendState.result=result;trendState.index=Math.max(0,data.rows.length-1);state.result=result;
  trendState.selected={ticker:data.ticker,name:state.health.companies?.find(c=>c.ticker===data.ticker)?.name||data.ticker};
  $('#trend-as-of').value=result.as_of;$('#trend-years').value=String(data.years);trendControls();
  $('#trend-result').classList.remove('hidden');$('#trend-title').textContent=data.ticker+' · 年度经营表现';$('#trend-method').textContent=data.note;$('#trend-audit').replaceChildren();
  $('#trend-status').textContent=(frozen?'已打开保存的年度趋势；刷新会创建新记录。':'年度趋势已保存。')+' '+(result.elapsed_ms/1000).toFixed(1)+' 秒 · 0 次模型调用。';
  const evidence=new Map(result.evidence.map(e=>[e.id,e])),table=$('#trend-table');table.replaceChildren();
  const head=el('thead'),tr=el('tr');tr.append(el('th','','指标 / 报告期间'));
  data.rows.forEach(row=>{const th=el('th'),basis=evidence.get(row.basis_id);th.scope='col';th.append(el('strong','','FY'+row.fiscal_year));
    if(basis)th.append(el('span','',basis.start+' — '+basis.end),el('span','','公开于 '+basis.filing.filed),el('span','',basis.filing.form+' · '+basis.filing.accession));else th.append(el('span','','未取得完整年度输入'));tr.append(th);});head.append(tr);
  const body=el('tbody');trendMetrics.forEach(([key,label])=>{const row=el('tr'),th=el('th','',label);th.scope='row';row.append(th);data.rows.forEach(year=>{const td=el('td'),e=evidence.get(year.cells[key]);
    if(e){const b=el('button','comparison-number',displayScaledAmount(e));b.type='button';b.setAttribute('aria-label',data.ticker+' FY'+year.fiscal_year+' '+label+' '+b.textContent+' 查看来源');b.addEventListener('click',()=>showEvidence(e));td.append(b);}else td.append(el('span','comparison-missing','—'));row.append(td);});body.append(row);});table.append(head,body);
  $('#trend-gaps').replaceChildren(...result.gaps.map(g=>el('p','',g)));
  const actions=$('#trend-record-actions');actions.replaceChildren(el('span','','知悉日期 '+result.as_of),el('span','','已保存 · '+data.years+' 个财年'));
  const refresh=el('button','secondary','刷新年报并重新生成');refresh.addEventListener('click',()=>runTrend(true));
  const download=el('button','secondary','导出趋势研究包 ↓');download.addEventListener('click',async()=>{download.disabled=true;try{const raw=await api('/api/runs/'+encodeURIComponent(result.id)+'/package',undefined,true);const url=URL.createObjectURL(new Blob([raw],{type:'application/json'}));const a=el('a');a.href=url;a.download=result.id+'-package.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){$('#trend-audit').append(el('p','error',e.message));}finally{download.disabled=false;}});
  const audit=el('button','secondary','复核年度数据');audit.addEventListener('click',async()=>{audit.disabled=true;try{const report=await api('/api/runs/'+encodeURIComponent(result.id)+'/audit');const box=$('#trend-audit');box.replaceChildren(el('strong','',report.status==='consistent'?'年度数据与计算一致性复核通过':'复核发现不一致'),el('p','company-help',report.checks_passed+' 项通过 · '+report.checks_failed+' 项不一致。'));
    const detail=el('details');detail.append(el('summary','','查看复核记录'),el('pre','',JSON.stringify(report,null,2)));box.append(detail);
    if(report.source_validation!=='matched'||report.raw_sources.missing)box.append(el('p','company-help','部分原件没有完成本地比对，可展开复核记录查看缺口。'));
  }catch(e){$('#trend-audit').replaceChildren(el('p','error',e.message));}finally{audit.disabled=false;}});actions.append(refresh,download,audit);
  updateTrendUnits();
}
function updateTrendUnits(){
  if(!trendState.result)return;const data=trendData(trendState.result),evidence=new Map(trendState.result.evidence.map(e=>[e.id,e])),metric=$('#trend-metric').value;
  const units=[...new Set(data.rows.map(row=>evidence.get(row.cells[metric])?.unit).filter(Boolean))],current=$('#trend-unit').value;
  $('#trend-unit').replaceChildren(...units.map(unit=>{const o=el('option','',unit);o.value=unit;return o;}));$('#trend-unit').value=units.includes(current)?current:units[0]||'';$('#trend-unit').disabled=units.length<2;drawTrendChart();
}
function drawTrendChart(){
  if(!trendState.result)return;const data=trendData(trendState.result),evidence=new Map(trendState.result.evidence.map(e=>[e.id,e])),metric=$('#trend-metric').value,unit=$('#trend-unit').value,label=trendMetrics.find(([key])=>key===metric)[1],svg=$('#trend-chart');
  svg.replaceChildren();svg.onpointerdown=null;svg.onkeydown=null;$('#trend-tooltip').replaceChildren();$('#trend-chart-note').textContent='缺少来源的年份保留空位；点柱形或下方数字查看报告出处。';
  const points=data.rows.map(row=>({row,e:evidence.get(row.cells[metric])})),available=points.filter(p=>p.e?.unit===unit);
  $('#trend-window').textContent=data.ticker+' · '+label+' · '+(unit||'未取得币种')+' · 知悉日期 '+data.as_of+' · '+(data.latest_fiscal_year?'FY'+(data.latest_fiscal_year-data.years+1)+' — FY'+data.latest_fiscal_year:'尚无年度财务');
  if(!available.length){svg.classList.add('hidden');$('#trend-tooltip').textContent='此指标尚无有效年度输入。可切换指标查看已有数字，或刷新年报后重试。';return;}
  svg.classList.remove('hidden');const width=Math.max(320,Math.round(svg.clientWidth||900)),height=width<500?270:310,left=70,right=20,top=20,bottom=38;
  svg.setAttribute('viewBox','0 0 '+width+' '+height);svg.setAttribute('aria-label',data.ticker+' '+label+' '+unit+' 完整年度柱形图，缺失年份留空。左右方向键、Home 和 End 查看财年。');
  let min=Math.min(0,...available.map(p=>Number(p.e.value))),max=Math.max(0,...available.map(p=>Number(p.e.value))),pad=(max-min)*.08||1;
  min=min<0?min-pad:0;max=max>0?max+pad:0;if(max===min)max=min+1;
  const scale=Math.max(Math.abs(min),Math.abs(max))>=1e8?1e8:Math.max(Math.abs(min),Math.abs(max))>=1e4?1e4:1,suffix=scale===1e8?' 亿':scale===1e4?' 万':'';
  const slot=(width-left-right)/points.length,x=i=>left+(i+.5)*slot,y=value=>top+(max-value)/(max-min)*(height-top-bottom),zero=y(0);
  for(let i=0;i<=4;i++){const yy=top+i/4*(height-top-bottom);svg.append(svgEl('line',{x1:left,x2:width-right,y1:yy,y2:yy,stroke:'#e2e8f0'}),svgEl('text',{x:left-8,y:yy+4,'text-anchor':'end',fill:'#64748b','font-size':11},displayDecimal((max-i/4*(max-min))/scale)+suffix));}
  svg.append(svgEl('line',{x1:left,x2:width-right,y1:zero,y2:zero,stroke:'#94a3b8','data-kind':'zero-baseline'}));if(min<0&&max>0)svg.append(svgEl('text',{x:left-8,y:zero+4,'text-anchor':'end',fill:'#64748b','font-size':11},'0'));
  const bars=[];points.forEach((point,i)=>{svg.append(svgEl('text',{x:x(i),y:height-12,'text-anchor':'middle',fill:'#64748b','font-size':12},'FY'+point.row.fiscal_year));
    if(point.e?.unit===unit){const v=Number(point.e.value),bar=svgEl('rect',{x:x(i)-slot*.26,y:Math.min(zero,y(v)),width:slot*.52,height:Math.max(1,Math.abs(zero-y(v))),fill:v<0?'#ac5960':'#2463a5',class:'trend-bar','data-year':point.row.fiscal_year});bar.addEventListener('click',()=>showEvidence(point.e));svg.append(bar);bars[i]=bar;}
    else svg.append(svgEl('text',{x:x(i),y:zero-9,'text-anchor':'middle',fill:'#94a3b8','font-size':12},'—'));
  });
  function inspect(index){trendState.index=Math.max(0,Math.min(points.length-1,index));const p=points[trendState.index];bars.forEach((bar,i)=>{if(bar)bar.setAttribute('opacity',i===trendState.index?'1':'.7');});
    const text=p.e?.unit===unit?displayScaledAmount(p.e)+' · '+evidence.get(p.row.basis_id).start+' — '+evidence.get(p.row.basis_id).end+' · 公开于 '+evidence.get(p.row.basis_id).filing.filed:p.e?'来源为 '+p.e.unit+'，当前选择 '+unit+'；未换汇。':'此年度未取得同口径输入，未填零。';
    $('#trend-tooltip').textContent='FY'+p.row.fiscal_year+' · '+text;
  }
  svg.onpointerdown=event=>{const rect=svg.getBoundingClientRect(),px=(event.clientX-rect.left)/rect.width*width;inspect(Math.floor((px-left)/slot));};
  svg.onkeydown=event=>{const keys={ArrowLeft:trendState.index-1,ArrowRight:trendState.index+1,Home:0,End:points.length-1};if(event.key in keys){event.preventDefault();inspect(keys[event.key]);}};inspect(trendState.index);
  $('#trend-chart-note').textContent=(points.some(p=>p.e&&p.e.unit!==unit)?'来源有不同币种，只画当前所选 '+unit+'，其他币种留空且未换汇。 ':'')+'缺少来源的年份保留空位；点柱形或下方数字查看报告出处。'+(metric==='free_cash_flow'?' 自由现金流为经营现金流减现金 PP&E 购置毛额，未抵减出售或补助收款。':'');
}
async function runTrend(refreshSources=null){
  if(trendState.busy||!trendState.selected)return;const asOf=$('#trend-as-of').value;if(!asOf){$('#trend-status').textContent='请选择知悉日期。';return;}
  const generation=++trendState.generation;trendState.busy=true;trendControls();$('#trend-status').textContent='正在核对完整财年并准备缺少的年报，保留已取得的年度资料；新结果就绪后更新。';
  try{const result=await api('/api/financial/trend',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ticker:trendState.selected.ticker,years:Number($('#trend-years').value),as_of:asOf,refresh_sources:refreshSources??$('#trend-refresh').checked})});if(generation===trendState.generation)renderTrend(result);}
  catch(e){if(generation===trendState.generation)$('#trend-status').textContent=e.message+' 已有趋势记录仍可查看，请重试或检查数据覆盖范围。';}
  finally{if(generation===trendState.generation){trendState.busy=false;trendControls();}}
}
$('#trend-run').addEventListener('click',()=>runTrend());$('#trend-search').addEventListener('click',searchTrend);
$('#trend-query').addEventListener('keydown',e=>{if(e.key==='Enter')searchTrend();});
$('#trend-query').addEventListener('input',()=>{trendState.generation++;trendState.lookup++;trendState.busy=false;trendState.selected=null;$('#trend-matches').replaceChildren();$('#trend-result').classList.add('hidden');trendControls();});
$('#trend-metric').addEventListener('change',updateTrendUnits);$('#trend-unit').addEventListener('change',drawTrendChart);
new ResizeObserver(()=>{if(!$('#trend-view').classList.contains('hidden'))drawTrendChart();}).observe($('#trend-chart'));trendControls();
