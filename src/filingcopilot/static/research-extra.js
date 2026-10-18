// Extended research shares job recovery, citations, exports and offline audits.
async function startCompanyReport(){
  if(state.researchRunning)return;
  const ticker=$('#ticker').value;
  if(!ticker){researchError('先选择一家公司，再生成完整公司研究。');return;}
  // Full reports use the configured model, with source excerpts as the fallback.
  const reportMode=state.health?.agent_ready?'agent':'reference';
  const request={question:'完整公司股票研究：商业模式、增长、现金流、竞争与优势、风险和估值条件',ticker,as_of:$('#as-of').value,mode:reportMode,depth:'full',auto_acquire:true};
  $('#error').classList.add('hidden');$('#result').classList.add('hidden');
  researchBusy(true);$('#busy-title').textContent='正在准备完整公司研究';$('#busy-detail').textContent=reportMode==='agent'?'整理商业模式等六个主题，并逐项生成和审查文字总结。':'当前没有可用模型，先整理六个主题的原文材料；配置模型后可生成文字总结。';
  try{
    const plan=await researchJobApi('/api/research/plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)});
    if(!plan.ready)throw new Error(plan.message);
    if(state.health.research_jobs)await startResearchJob(request);
    else{renderResult(await api('/api/research',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)}));researchBusy(false);}
  }catch(error){researchError(error.message);if(!state.researchJob&&!state.researchPending)researchBusy(false);}
}
function organizeCompanyReport(result,root){
  const layout=result.trace.find(t=>t.stage==='company_report_layout');
  if(!layout)return;
  const sections=el('div','company-report-sections');
  layout.sections.forEach(s=>{
    const card=el('section','company-report-section');const heading=el('div','market-heading');
    heading.append(el('h3','',s.title),el('span','small-tag',s.status==='reviewed'?'分析经审查':s.status==='source_material'?'原文材料':'待补齐'));
    card.append(heading);
    s.claim_indexes.forEach(index=>{const row=root.querySelector('[data-claim-index="'+index+'"]');if(row)card.append(row);});
    if(s.gap)card.append(el('p','company-help',s.gap));
    sections.append(card);
  });
  const first=root.querySelector('.answer-item');if(first)root.insertBefore(sections,first);else root.append(sections);
}
function renderScenarios(result,root){
  const step=result.trace.find(t=>t.stage==='valuation_scenarios');if(!step)return;
  const s=step.result,card=el('section','scenario-output');
  card.append(el('p','company-help',s.eps_period+' · 基准 EPS '+marketNumber(s.base_eps)+' '+s.currency+'/股 · 当前引用价格 '+marketNumber(s.observed_price)+' '+s.currency),el('p','verification-note',s.note));
  if(s.price_reference){const r=s.price_reference;const basis=r.basis==='adjusted_close'?'供应商当前复权收盘价':r.basis==='split_adjusted_close'?'拆股调整收盘价（未含股息）':'Close 收盘价';const ref=el('div','scenario-price-reference');ref.append(el('strong','','历史价格参照（不是历史估值）'),el('span','',r.start+' — '+r.end+' · '+Number(r.trading_days).toLocaleString()+' 个交易日 · '+basis),el('span','', '低 '+marketNumber(r.low)+' · P25 '+marketNumber(r.p25)+' · 中位 '+marketNumber(r.median)+' · P75 '+marketNumber(r.p75)+' · 高 '+marketNumber(r.high)+' '+s.currency),el('small','',r.note));card.append(ref);}
  const inputs=el('div','scenario-assumptions');inputs.append(el('strong','','你使用的假设'),el('span','','年限 '+s.request.years+' 年'),el('span','','年折现率 '+marketNumber(s.request.discount_pct)+'%'));card.append(inputs);
  const wrap=el('div','comparison-table-wrap'),table=el('table','scenario-table');
  const head=el('tr');['情景','EPS 年增长假设','期末 PE 假设','假设期末 EPS','折现情景值','与所引价格差异'].forEach(t=>head.append(el('th','',t)));const thead=el('thead');thead.append(head);table.append(thead);const body=el('tbody');
  s.rows.forEach(r=>{const tr=el('tr');[r.name,marketNumber(r.growth_pct)+'%',marketNumber(r.terminal_pe)+' 倍',marketNumber(r.future_eps),marketNumber(r.discounted_value)+' '+s.currency,marketNumber(r.price_difference_pct)+'%'].forEach(v=>tr.append(el('td','',v)));body.append(tr);});table.append(body);wrap.append(table);card.append(wrap,el('p','company-help','相对价格差异只是这些假设下的算术差异，未计股息和持有期回报。'),el('code','scenario-formula',s.formula));root.append(card);
}
function renderHistoricalValuation(result,root){
  const step=result.trace.find(t=>t.stage==='historical_valuation');
  if(!step)return;
  const s=step.result,card=el('section','historical-valuation-card');
  const stats=s.statistics||{};
  card.append(
    el('h3','',`历史年度市盈率 · ${s.period}`),
    el('p','company-help',`有效交易日 ${Number(s.valid_days||0).toLocaleString()} / ${Number(s.trading_days||0).toLocaleString()} · ${s.start} — ${s.end}`),
    el('p','verification-note',s.note)
  );
  const values=[['低',stats.low],['P25',stats.p25],['中位',stats.median],['P75',stats.p75],['高',stats.high]];
  const row=el('div','historical-valuation-stats');
  values.forEach(([label,value])=>row.append(el('span','',`${label} ${value==null?'—':marketNumber(value)} 倍`)));
  card.append(row);
  const gaps=Object.entries(s.missing_counts||{}).map(([key,value])=>`${key} ${value}日`).join(' · ');
  if(gaps)card.append(el('p','verification-note','留空原因：'+gaps));
  root.append(card);
}
async function runScenarios(){
  const button=$('#scenario-run');if(button.disabled)return;
  const ticker=$('#ticker').value;if(!ticker){researchError('先在顶部选择证券，再生成估值情景。');return;}
  researchBusy(true);$('#busy-title').textContent='正在计算估值情景';$('#busy-detail').textContent='读取 EPS 与匹配报价，并保存你输入的假设和计算公式。';$('#scenario-status').textContent='正在读取有来源的 EPS 与匹配报价，并计算你输入的假设。';
  const cases=[...document.querySelectorAll('.scenario-case')].map(row=>({name:row.querySelector('.scenario-name').value,growth_pct:row.querySelector('.scenario-growth').value,terminal_pe:row.querySelector('.scenario-pe').value}));
  const request={ticker,as_of:$('#as-of').value,basis:$('#scenario-basis').value,years:Number($('#scenario-years').value),discount_pct:$('#scenario-discount').value,cases};
  try{const result=await api('/api/valuation/scenarios',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)});renderResult(result);$('#scenario-status').textContent='情景已保存，可导出研究包复核输入和公式。';}
  catch(error){$('#scenario-status').textContent=error.message;}
  finally{researchBusy(false);}
}
$('#company-report').addEventListener('click',startCompanyReport);
$('#scenario-run').addEventListener('click',runScenarios);
