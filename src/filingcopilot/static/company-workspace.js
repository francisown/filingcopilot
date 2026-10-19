// Company sources load independently of quotes and preserve the selected knowledge date.
const companyWorkspace={generation:0,pageGeneration:0,scope:null,controller:null,page:null,capabilities:null,peers:[],selectedPeers:new Set(),statements:null,dcfInputs:null,inlineBusy:false,busyTitle:'',busyKey:null,batchRunning:false,batchStop:false,doneSections:new Set(),sectionRefs:{},summaryCache:{}};
function companyText(value){return typeof FilingI18n!=='undefined'&&typeof FilingI18n.translate==='function'?FilingI18n.translate(value):value;}
const companyProgressStages={
  queued:'研究已排队，等待执行。',planning:'正在检查问题范围与知悉日期。',evidence:'正在读取事实与原文，核对期间并计算。',
  tool:'正在执行研究工具。',verify:'正在核对数值、期间、证据归属与叙述支持。',semantic_review:'正在独立审查叙述与分析。',
  company_preparation:'正在获取所列公司的行情、TTM 估值和年度资料。',annual_refresh:'正在检查最新年度披露。',
  quarterly_preparation:'正在检查最新季报与披露日期。',updates_preparation:'正在检查近 90 天公司公告与发布附件。',
  external_updates_preparation:'正在读取电话会、新闻或交易所公告。',cancelled:'研究已取消；保留已通过核验的内容，未完成部分未作结论。',
  failed:'研究未完成，请重试。'
};
function companyProgressMessage(event){
  const message=String(event?.message||'').trim();
  return companyText(message||companyProgressStages[event?.stage]||'正在开展研究');
}
function renderCompanyInlineProgress(status,job,waited){
  const events=(job?.events||[]).slice(-4),trail=[];
  events.forEach(event=>{const message=companyProgressMessage(event);if(message&&trail.at(-1)!==message)trail.push(message);});
  const seconds=Math.max(0,Math.round(Number(job?.elapsed_ms||waited*1000)/1000));
  const current=job?.status==='timing_out'?companyText('本节研究已到预算边界，正在保存已核验内容…'):(trail.join(' → ')||companyText('正在生成本节文字总结…'));
  status.textContent=current+' · '+companyText(' 已等待 ')+seconds+companyText(' 秒');
  status.dataset.latestStage=events.at(-1)?.stage||'';
}
// One summary runs at a time. Only the running button looks busy; other buttons
// stay clickable and explain the wait, so the page never looks frozen.
function setCompanyInlineBusy(busy,activeButton,title,key){
  companyWorkspace.inlineBusy=busy;companyWorkspace.busyTitle=busy?(title||''):'';companyWorkspace.busyKey=busy?(key||null):null;
  const nodes=globalThis.document?.querySelectorAll?.('.company-section-heading-actions button,#company-workspace-report')||[];
  nodes.forEach(button=>{button.classList.remove('busy-button');button.removeAttribute('aria-busy');});
  if(activeButton){
    if(busy){activeButton.classList.add('busy-button');activeButton.setAttribute('aria-busy','true');}
    else{activeButton.classList.remove('busy-button');activeButton.removeAttribute('aria-busy');}
  }
}
function companyWorkspaceScope(){return {ticker:$('#ticker').value,asOf:$('#as-of').value};}
// Reloading the page data rebuilds every card, so it is only done when there is
// nothing loaded yet or the selected company/date changed. Re-entering the view
// must not throw away an in-flight generation or a delivered summary.
function companyWorkspaceNeedsLoad(){
  const scope=companyWorkspaceScope();
  if(!scope.ticker)return true;
  return !companyWorkspace.page||companyWorkspace.scope?.ticker!==scope.ticker||companyWorkspace.scope?.asOf!==scope.asOf;
}
function companyWorkspaceActive(generation,scope){const now=companyWorkspaceScope();return generation===companyWorkspace.generation&&scope.ticker===now.ticker&&scope.asOf===now.asOf;}
function companyWorkspacePath(scope,suffix,extra={}){return '/api/companies/'+encodeURIComponent(scope.ticker)+'/'+suffix+'?'+new URLSearchParams({as_of:scope.asOf,...extra});}
function companyWorkspaceReset(){
  companyWorkspace.generation++;companyWorkspace.pageGeneration++;companyWorkspace.controller?.abort();companyWorkspace.page=null;companyWorkspace.capabilities=null;companyWorkspace.statements=null;companyWorkspace.dcfInputs=null;companyWorkspace.peers=[];companyWorkspace.selectedPeers.clear();
  companyWorkspace.batchRunning=false;companyWorkspace.batchStop=false;companyWorkspace.doneSections.clear();companyWorkspace.sectionRefs={};companyWorkspace.summaryCache={};
  $('#company-report-inline-result')?.replaceChildren();$('#company-report-inline-result')?.classList.add('hidden');
  ['#company-key-figures','#company-source-sections','#company-peers-options','#company-quarters-table','#company-statements-table','#company-dcf-sources','#company-dcf-result'].forEach(id=>$(id).replaceChildren());
  $('#company-dcf-form').classList.add('hidden');$('#company-dcf-status').textContent='';$('#company-dcf-inputs').disabled=!companyWorkspaceScope().ticker;
  const scope=companyWorkspaceScope();companyWorkspace.scope=scope;$('#company-workspace-title').textContent=scope.ticker?scope.ticker+' · 公司研究':'公司研究';
  $('#company-workspace-scope').textContent=scope.ticker?scope.ticker+' · 知悉日期 '+scope.asOf:'请先在工作台选择明确的公司与上市市场。';
  $('#company-workspace-status').textContent='';$('#company-peers-note').textContent='';$('#company-quarters-status').textContent='';$('#company-statements-status').textContent='';$('#company-peers-compare').disabled=true;
  $('#company-statement-filing').replaceChildren(el('option','','最新可用申报'));$('#company-statement-filing').firstElementChild.value='';
  ['#company-workspace-load','#company-workspace-refresh','#company-workspace-report','#company-quarters-load','#company-quarters-refresh','#company-statements-load','#company-historical-pe','#company-open-scenarios'].forEach(id=>$(id).disabled=!scope.ticker);
  $('#company-workspace-report').textContent='生成全部模块总结';
  $('#company-quarters-refresh').disabled=true;$('#company-dcf-run').disabled=false;
}
function companyWorkspaceCite(e,label='查看出处'){const b=el('button','cite',label);b.type='button';b.addEventListener('click',()=>showEvidence(e));return b;}
function companyInlineEvidenceButton(evidence,label){
  const button=el('button','cite',label);button.type='button';button.addEventListener('click',()=>showEvidence(evidence));return button;
}
function companyInlineCitationMarker(evidence,number){
  const button=el('button','company-inline-marker','['+number+']');
  button.type='button';button.title='打开证据 '+number;button.setAttribute('aria-label','打开证据 '+number);
  button.addEventListener('click',()=>showEvidence(evidence));return button;
}
function companyInlineEvidenceLabel(item){
  const kind=item?.kind||'';
  const label=item?.label||item?.metric||item?.operation||(
    kind==='passage'?'原文段落':kind==='fact'?'财务事实':kind==='calculation'?'计算结果':'证据'
  );
  const filing=item?.filing||{};
  const form=filing.form||'';
  const period=item?.fiscal_year?'FY'+item.fiscal_year:(item?.end||item?.snapshot?.period_end_label||filing.period_end||'');
  return [label,form,period].filter(Boolean).join(' · ');
}
function companyClaimNote(claim){return claim.verification==='inference_review'?'分析基于所引证据，经模型审查；判断仍可人工复核':claim.verification==='semantic_review'?'叙述经模型审查，完整含义仍可人工复核':claim.verification==='exact_source_excerpt'?'原文逐字匹配':'查看证据与核验类别';}
// Numeric evidence cited by the delivered statements is listed as a "key
// figures" strip: the prose stays qualitative (numbers are rendered by code),
// but the amounts and ratios behind each conclusion stay visible instead of
// hiding behind the evidence buttons.
function companyNumberEntry(item){
  const unit=typeof displayUnit==='function'?displayUnit(item.unit):item.unit;
  if(item.kind==='calculation'&&item.value!=null){
    const currency=typeof unit==='string'&&/^[A-Z]{3}$/.test(unit);
    const text=currency?displayScaledAmount({value:item.value,unit}):displayDecimal(item.value)+((unit==='%'||unit==='倍')?(unit||''):(unit?' '+unit:''));
    return {label:item.label||item.operation||'计算',text,period:item.fiscal_year?'FY'+item.fiscal_year:'',evidence:item};
  }
  if((item.kind==='fact'||item.kind==='statement_fact')&&item.value!=null){
    return {label:item.label||item.metric||'事实',text:displayScaledAmount(item),period:item.fiscal_year?'FY'+item.fiscal_year:(item.end||''),evidence:item};
  }
  if(item.kind==='valuation'&&item.snapshot&&item.snapshot.value!=null){
    const text=displayDecimal(item.snapshot.value)+' '+(item.snapshot.currency||'')+(item.snapshot.stale?'（陈旧）':'');
    return {label:'供应商 TTM 稀释每股收益',text,period:item.snapshot.period_end_label||'',evidence:item};
  }
  if(item.kind==='market'&&item.snapshot&&(item.snapshot.points||[]).length){
    const point=item.snapshot.points[item.snapshot.points.length-1];
    const price=point.adjusted!=null?point.adjusted:point.close;
    if(price==null)return null;
    const label=item.snapshot.basis==='adjusted_close'?'最新复权收盘价':'最新收盘价';
    return {label,text:displayDecimal(price)+' '+(item.snapshot.currency||''),period:point.date||item.snapshot.as_of||'',evidence:item};
  }
  return null;
}
function companyNumberEntries(result,claims){
  const evidence=new Map((result.evidence||[]).map(item=>[item.id,item]));
  const entries=[],seen=new Set();
  for(const claim of claims){
    for(const id of claim.evidence_ids||[]){
      if(seen.has(id)||entries.length>=8)continue;
      const item=evidence.get(id);if(!item)continue;
      seen.add(id);
      const entry=companyNumberEntry(item);
      if(entry)entries.push(entry);
    }
  }
  return entries;
}
const companyModuleNumericRules={
  business:item=>item.kind==='fact'||item.kind==='statement_fact',
  operating_change:item=>item.kind==='calculation'&&['growth','ratio','difference'].includes(item.operation)||item.kind==='fact',
  cashflow:item=>item.kind==='calculation'&&['free_cash_flow','growth','difference','net_cash_capex','trailing_sum','ratio'].includes(item.operation)||item.kind==='fact',
  debt:item=>['fact','statement_fact'].includes(item.kind)&&/(debt|borrow|cash|liabilit|借款|现金|负债)/i.test((item.metric||'')+' '+(item.label||'')),
  valuation:item=>item.kind==='market'||item.kind==='valuation'||item.kind==='calculation'&&['annual_pe','provider_ttm_pe'].includes(item.operation),
};
function companyOwnershipEntries(result,claims){
  const evidence=new Map((result.evidence||[]).map(item=>[item.id,item])),entries=[],seen=new Set(),words={one:1,two:2,three:3,four:4,five:5,six:6,eight:8};
  for(const claim of claims){
    for(const id of claim.evidence_ids||[]){
      if(seen.has(id))continue;
      const item=evidence.get(id),text=String(item?.text||'');
      const match=text.match(/(?:one|each)\s+(?:American depositary share|ADS)\s+(?:representing|represents)\s+(\d+|one|two|three|four|five|six|eight)\s+(?:Class\s+[AB]\s+)?ordinary shares/i);
      if(!match)continue;
      const ratio=words[match[1].toLowerCase()]||Number(match[1]);
      if(!Number.isFinite(ratio))continue;
      entries.push({label:'原文存托比率',text:'1 ADS = '+ratio+' ordinary shares',period:'',evidence:item});seen.add(id);
    }
  }
  return entries;
}
function companyModuleNumberEntries(result,section,claims){
  const entries=section.key==='ownership'?companyOwnershipEntries(result,claims):companyNumberEntries(result,claims),seen=new Set(entries.map(entry=>entry.evidence?.id));
  const rule=companyModuleNumericRules[section.key];
  // Prefer figures explicitly cited by a delivered statement.  The fallback
  // below is only used when the model returned no typed figure at all; those
  // rows are shown as available evidence, never silently attached to a prose
  // conclusion.
  if(entries.length||!rule)return entries;
  const evidence=[...(result.evidence||[])].sort((a,b)=>{
    if(section.key!=='valuation')return 0;
    const rank=item=>item.kind==='calculation'&&['annual_pe','provider_ttm_pe'].includes(item.operation)?0:item.kind==='valuation'?1:item.kind==='market'?2:3;
    return rank(a)-rank(b);
  });
  for(const item of evidence){
    if(entries.length>=8||seen.has(item.id)||!rule(item))continue;
    const entry=companyNumberEntry(item);if(!entry)continue;
    entries.push({...entry,uncited:true});seen.add(item.id);
  }
  return entries;
}
function companyDeterministicConclusion(entry){
  const evidence=entry.evidence||{};
  if(evidence.kind!=='calculation')return '';
  if(evidence.operation==='growth'&&evidence.value!=null){
    return (evidence.label||'该指标')+'的可核验方向为'+(Number(evidence.value)>=0?'同比增长':'同比下降')+'；具体幅度见关键数字。';
  }
  if(evidence.operation==='difference'&&evidence.value!=null){
    return (evidence.label||'该指标')+'已按同口径期间核验差额，方向为'+(Number(evidence.value)>=0?'增加':'减少')+'；具体数值见关键数字。';
  }
  if(evidence.operation==='ratio'&&evidence.value!=null){
    return (evidence.label||'该比率')+'已按绑定输入计算为 '+displayDecimal(evidence.value)+(evidence.unit==='%'?'%':' '+(evidence.unit||''))+'；期间和输入可展开核对。';
  }
  if(['free_cash_flow','net_cash_capex'].includes(evidence.operation)){
    return '已形成代码核验的现金流代理口径；它不等同于可分配现金。';
  }
  if(evidence.operation==='trailing_sum'&&evidence.value!=null){
    return (evidence.label||'滚动期间数据')+'已按来源绑定的连续期间计算；具体窗口和输入见关键数字。';
  }
  if(['annual_pe','provider_ttm_pe'].includes(evidence.operation)){
    const value=evidence.value!=null?displayDecimal(evidence.value):'';
    const basis=evidence.operation==='annual_pe'?'年度 EPS 口径':'供应商 TTM 口径';
    return value?basis+'市盈率为 '+value+' 倍；这是估值参照，不单独判断便宜或昂贵。':'已形成标注口径的市盈率输入；单一倍数不能单独判断便宜或昂贵。';
  }
  return '';
}
function renderInlineCompanySummary(host,result,section,claimIndexes=null){
  const evidence=new Map((result.evidence||[]).map(item=>[item.id,item]));
  const module=section.module||{};
  const claims=(result.answer||[]).filter(item=>['analysis','narrative','excerpt'].includes(item.kind)&&(!claimIndexes||claimIndexes.includes(item.index)));
  const status=claims.some(item=>['analysis','narrative'].includes(item.kind))?'分析经审查':claims.some(item=>item.kind==='excerpt')?'原文材料':'待补齐';
  const gaps=[...(result.gaps||[])];
  const cited=[],citationNumbers=new Map();
  const registerEvidence=item=>{
    if(!item?.id)return null;
    if(!citationNumbers.has(item.id)){citationNumbers.set(item.id,cited.length+1);cited.push(item);}
    return citationNumbers.get(item.id);
  };
  const appendMarkers=(parent,ids)=>{
    const markers=[...new Set(ids||[])].map(id=>evidence.get(id)).filter(Boolean).map(item=>{
      const number=registerEvidence(item);return companyInlineCitationMarker(item,number);
    });
    if(markers.length){const wrap=el('span','company-inline-citation-markers');markers.forEach(marker=>wrap.append(marker));parent.append(el('span','',' '),wrap);}
  };
  const appendEvidenceAppendix=()=>{
    if(!cited.length)return;
    const box=el('section','company-inline-evidence');
    box.append(el('strong','company-inline-evidence-title','证据'),el('p','company-inline-evidence-note','正文中的编号对应下列证据；点击“打开证据”查看原文、期间与核验信息。'));
    const list=el('ol','company-inline-evidence-list');
    cited.forEach((item,index)=>{
      const row=el('li','company-inline-evidence-item');
      row.append(el('span','company-inline-evidence-label','['+(index+1)+'] '+companyInlineEvidenceLabel(item)),companyInlineEvidenceButton(item,'打开证据'));
      list.append(row);
    });
    box.append(list);host.append(box);
  };
  host.replaceChildren();
  const heading=el('div','market-heading');
  heading.append(el('h3','',section.title+' · 研究结果'),el('span','small-tag',status));
  host.append(heading,el('p','company-inline-label','文章格式：正文标注 [n]，边界与待补信息随后，证据在文末集中列出'));
  if(claims.length){
    // Keep reviewed claims as connected article paragraphs. Each paragraph
    // carries small numbered markers; the evidence list is appended once.
    const article=el('div','company-inline-article'),groups=[];
    claims.slice(0,4).forEach(claim=>{
      const label=claim.kind==='excerpt'?'原文依据':module.kind==='factual'?'整理':'结论';
      if(!groups.length||groups[groups.length-1].label!==label)groups.push({label,claims:[]});
      groups[groups.length-1].claims.push(claim);
    });
    groups.forEach(group=>{
      const block=el('section','company-inline-group');
      block.append(el('strong','claim-label',group.label));
      group.claims.forEach(claim=>{
        const row=el('div','company-inline-claim');
        const paragraph=el('p','claim-text');paragraph.append(el('span','claim-text-body',claim.text||''));appendMarkers(paragraph,claim.evidence_ids);row.append(paragraph);
        block.append(row);
      });
      block.append(el('p','verification-note',[...new Set(group.claims.map(companyClaimNote))].join(' ')));
      article.append(block);
    });
    host.append(article);
    const numbers=companyModuleNumberEntries(result,section,claims);
    if(numbers.length){
      const box=el('div','company-inline-numbers');
      box.append(el('strong','',companyText('关键数字')),el('p','company-inline-numbers-note',numbers.some(entry=>entry.uncited)?companyText('本节已取得的相关核验数字；文字结论未覆盖时请重试本节。'):companyText('来自本次核验证据，点击可核对。')));
      numbers.forEach(entry=>{
        const row=el('p','company-inline-number');
        row.append(el('span','number-label',entry.label+'：'),el('strong','number-value',entry.text));
        if(entry.period)row.append(el('span','number-period','（'+entry.period+'）'));
        const number=registerEvidence(entry.evidence);if(number)row.append(el('span','',' '),companyInlineCitationMarker(entry.evidence,number));
        box.append(row);
      });
      host.append(box);
    }
    if(module.boundary){const boundary=el('div','company-inline-boundary');boundary.append(el('strong','', '边界'),el('p','',module.boundary));host.append(boundary);}
  }else{
    // Report missing sources separately from model failures.
    const sourceMissing=!(section.evidence_ids||[]).length||section.status==='gap';
    const fallback=sourceMissing?(module.empty_gap||'本节暂未形成可交付文字结论；下方保留具体缺口。'):(gaps.length?'本节暂未形成可交付文字结论；具体原因见下方缺口。':'本节暂未形成可交付文字结论。');
    host.append(el('p','company-source-gap',fallback));
    const numbers=companyModuleNumberEntries(result,section,[]);
    const deterministic=numbers.map(companyDeterministicConclusion).find(Boolean);
    if(deterministic){
      const box=el('div','company-inline-deterministic');
      const deterministicParagraph=el('p','',deterministic);box.append(el('strong','', '可确认结论'),deterministicParagraph);
      const evidence=numbers.find(entry=>companyDeterministicConclusion(entry)===deterministic)?.evidence;
      if(evidence){const number=registerEvidence(evidence);deterministicParagraph.append(el('span','',' '),companyInlineCitationMarker(evidence,number));}
      host.append(box);
    }
    if(numbers.length){
      const box=el('div','company-inline-numbers');
      box.append(el('strong','',companyText('关键数字')),el('p','company-inline-numbers-note',numbers.some(entry=>entry.uncited)?companyText('本节已取得的相关核验数字；文字结论未覆盖时请重试本节。'):companyText('来自本次核验证据，点击可核对。')));
      numbers.forEach(entry=>{
        const row=el('p','company-inline-number');
        row.append(el('span','number-label',entry.label+'：'),el('strong','number-value',entry.text));
        if(entry.period)row.append(el('span','number-period','（'+entry.period+'）'));
        const number=registerEvidence(entry.evidence);if(number)row.append(el('span','',' '),companyInlineCitationMarker(entry.evidence,number));box.append(row);
      });
      host.append(box);
    }
  }
  if(gaps.length){const box=el('div','company-inline-gaps');box.append(el('strong','', '待补信息'));gaps.slice(0,4).forEach(gap=>box.append(el('p','',gap)));host.append(box);}
  appendEvidenceAppendix();
  host.classList.remove('hidden');
}
async function runInlineCompanyResearch(request,host,button,section,renderer=renderInlineCompanySummary,internal=false){
  if(!internal&&(companyWorkspace.inlineBusy||companyWorkspace.batchRunning)){host.classList.remove('hidden');host.replaceChildren(el('p','company-help',companyWorkspace.busyTitle?'已有总结正在生成中（'+companyWorkspace.busyTitle+'）。完成后即可生成本节。':'已有总结正在生成中。完成后即可生成本节。'));return;}
  const generation=companyWorkspace.generation;
  // The section card can be rebuilt while the request runs (source refresh or a
  // later reload). The live card and button are resolved on every write so the
  // result lands where the user is looking instead of a discarded node, and the
  // job keeps running when the user switches to another workspace view.
  const currentHost=()=>companyWorkspace.generation===generation&&section.key&&companyWorkspace.sectionRefs[section.key]?companyWorkspace.sectionRefs[section.key].host:host;
  const currentButton=()=>companyWorkspace.generation===generation&&section.key&&companyWorkspace.sectionRefs[section.key]?companyWorkspace.sectionRefs[section.key].button:button;
  setCompanyInlineBusy(true,button,section.title||'',section.key||null);
  const previous=button.textContent;button.disabled=true;button.textContent='正在生成本节总结…';host.classList.remove('hidden');host.setAttribute('aria-busy','true');
  const status=el('p','company-inline-progress','正在读取本节证据并生成固定格式总结…');status.setAttribute('role','status');status.setAttribute('aria-live','polite');host.replaceChildren(status);
  try{
    const plan=await api('/api/research/plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)});
    if(!plan.ready)throw new Error(plan.message);
    let result;
    if(state.health?.research_jobs){
      const key=crypto.randomUUID(),headers={'Content-Type':'application/json','Idempotency-Key':key};
      if(state.health.research_session)headers['X-Research-Session']=state.health.research_session;
      let job=await api('/api/research/jobs',{method:'POST',headers,body:JSON.stringify(request)});
      let waited=0;
      renderCompanyInlineProgress(status,job,waited);
      while(!['completed','cancelled','timed_out','failed'].includes(job.status)){
        await new Promise(resolve=>setTimeout(resolve,900));
        waited+=0.9;
        const attached=currentHost();
        if(!Array.from(attached.children||[]).includes(status)){attached.replaceChildren(status);attached.classList.remove('hidden');}
        job=await api('/api/research/jobs/'+encodeURIComponent(job.id));
        renderCompanyInlineProgress(status,job,waited);
        if(waited>480)throw new Error('本节研究等待超过 8 分钟；页面保留当前状态，请稍后重试。');
      }
      if(job.result)result=job.result;else throw new Error(job.error?.message||'本节研究未完成，请稍后重试。');
    }else result=await api('/api/research',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)});
    if(section.key){companyWorkspace.summaryCache[section.key]=result;if((result.answer||[]).length)companyWorkspace.doneSections.add(section.key);}
    renderer(currentHost(),result,section);
  }catch(error){const target=currentHost();target.replaceChildren(el('p','company-source-gap','本节总结未生成：'+error.message+'（状态已保留，可再次点击重试）'));target.classList.remove('hidden');}
  finally{
    host.setAttribute('aria-busy','false');
    const restore=currentButton();restore.disabled=false;restore.textContent=previous;
    setCompanyInlineBusy(false,restore);
  }
}
function companySummaryRequest(section){
  const scope=companyWorkspaceScope(),module=section.module||null;
  return {question:module?module.question:'Summarize the selected company using only disclosed evidence.',ticker:scope.ticker,as_of:scope.asOf,mode:state.health?.agent_ready?'agent':'reference',depth:'brief',auto_acquire:false,...(module?{module:section.key}:{})};
}
function startFocusedCompanySummary(section,card,button){
  const scope=companyWorkspaceScope();
  if(!scope.ticker){companyWorkspaceReset();return;}
  const ref=section.key?companyWorkspace.sectionRefs[section.key]:null;
  const host=ref?ref.host:card.querySelector('.company-inline-summary');
  return runInlineCompanyResearch(companySummaryRequest(section),host,button,section);
}
// The toolbar button generates every module summary one at a time. Each card
// shows its full text as soon as that item finishes; sections that already
// delivered a summary are skipped, so a second click resumes the rest, and a
// click while the batch runs stops it after the current item.
async function startAllModuleSummaries(){
  const scope=companyWorkspaceScope();if(!scope.ticker){companyWorkspaceReset();return;}
  const button=$('#company-workspace-report'),host=$('#company-report-inline-result');
  if(companyWorkspace.batchRunning){companyWorkspace.batchStop=true;button.textContent='停止中…';return;}
  if(companyWorkspace.inlineBusy){host.classList.remove('hidden');host.replaceChildren(el('p','company-help',companyWorkspace.busyTitle?'已有总结正在生成中（'+companyWorkspace.busyTitle+'）。完成后即可开始批量生成。':'已有总结正在生成中。完成后即可开始批量生成。'));return;}
  const page=companyWorkspace.page,sections=(page&&page.sections)||[];
  if(!sections.length){host.classList.remove('hidden');host.replaceChildren(el('p','company-help','请先读取公司资料，再生成全部模块总结。'));return;}
  const generation=companyWorkspace.generation;
  companyWorkspace.batchRunning=true;companyWorkspace.batchStop=false;
  const previous=button.textContent;button.textContent='停止批量生成';
  host.classList.remove('hidden');
  let stopped=false;
  try{
    for(const section of sections){
      if(companyWorkspace.generation!==generation)return;
      if(companyWorkspace.batchStop){stopped=true;break;}
      const ref=companyWorkspace.sectionRefs[section.key];
      if(!ref||companyWorkspace.doneSections.has(section.key))continue;
      host.replaceChildren(el('p','company-help','正在逐项生成全部模块总结：'+section.title+'。一项完成后立即显示；点「停止批量生成」可停止后续。'));
      await runInlineCompanyResearch(companySummaryRequest(section),ref.host,ref.button,section,renderInlineCompanySummary,true);
    }
    if(companyWorkspace.generation!==generation)return;
    if(stopped){host.replaceChildren(el('p','company-help','已停止批量生成；已完成的项保留，可再次点击继续。'));}
    else{host.replaceChildren(el('p','company-help','全部模块总结已生成；如需更新某一项，请点击该卡片内的「生成本节文字总结」。'));}
  }finally{
    companyWorkspace.batchRunning=false;companyWorkspace.batchStop=false;
    if(companyWorkspace.generation===generation)button.textContent=previous;
  }
}
function renderCompanyWorkspaceSections(page,merge=false){
  const old=companyWorkspace.page;
  companyWorkspace.page=merge&&old?{...old,sections:old.sections.map(s=>page.sections.find(n=>n.key===s.key)||s),evidence:[...new Map([...old.evidence,...page.evidence].map(e=>[e.id,e])).values()]}:page;
  const data=companyWorkspace.page,evidence=new Map(data.evidence.map(e=>[e.id,e]));
  companyWorkspace.sectionRefs={};
  $('#company-source-sections').replaceChildren(...data.sections.map(section=>{
    const card=el('article','company-source-card'),head=el('div','market-heading'),headingActions=el('div','company-section-heading-actions');
    const summary=el('button','primary','生成本节文字总结');summary.type='button';summary.addEventListener('click',()=>startFocusedCompanySummary(section,card,summary));
    headingActions.append(summary);head.append(el('h2','',section.title),el('span','small-tag',({ready:'有原文',partial:'部分资料',gap:'待补资料'})[section.status]),headingActions);card.append(head);
    card.append(el('p','company-help','公开 / 获取日期：'+(section.source_dates?.join(' / ')||'未取得')));
    const inline=el('div','company-inline-summary hidden');card.append(inline);
    companyWorkspace.sectionRefs[section.key]={card,host:inline,button:summary};
    // Rebuilt cards restore the last delivered summary from the session cache.
    if(companyWorkspace.summaryCache[section.key])renderInlineCompanySummary(inline,companyWorkspace.summaryCache[section.key],section);
    const raw=el('details','company-raw-materials');raw.append(el('summary','', '查看原始报告材料'));
    for(const id of section.evidence_ids){const e=evidence.get(id);if(!e)continue;const material=el('div','company-source-material');
      if(e.filing)material.append(el('small','company-help',e.filing.form+' · 报告截至 '+e.filing.period_end+' · '+(e.section||e.label||'')));
      if(e.kind==='passage'||e.kind==='external_update')material.append(el('p','source-text',String(e.text||'').slice(0,260)+(e.text?.length>260?'…':'')));
      else material.append(el('p','',e.label||e.ticker||'资料'));
      material.append(companyWorkspaceCite(e,e.kind==='fact'?'查看财务事实':'展开原始资料'));raw.append(material);
    }
    if(section.evidence_ids.length)card.append(raw);
    if(section.note)card.append(el('p','company-help',section.note));
    if(section.gap)card.append(el('p','company-source-gap',section.gap));
    const actions=el('div','company-source-actions');
    const retry=el('button','secondary','更新本项资料');retry.type='button';retry.addEventListener('click',()=>{delete companyWorkspace.summaryCache[section.key];companyWorkspace.doneSections.delete(section.key);loadCompanyWorkspace(true,section.key);});
    actions.append(retry);card.append(actions);
    if(companyWorkspace.inlineBusy&&section.key===companyWorkspace.busyKey){summary.disabled=true;summary.textContent='正在生成本节总结…';summary.classList.add('busy-button');summary.setAttribute('aria-busy','true');}
    return card;
  }));
  if(!merge){
    const overview=data.annual_overview,figures=$('#company-key-figures');figures.replaceChildren();
    const ids=new Set((overview?.evidence||[]).filter(e=>e.kind==='fact'&&e.fiscal_year===overview.fiscal_year).map(e=>e.id));
    for(const e of data.evidence.filter(e=>ids.has(e.id))){const card=el('article','company-key-figure');card.append(el('span','',e.label),el('strong','',displayScaledAmount(e)),el('small','',(e.start?e.start+' 至 ':'')+e.end),companyWorkspaceCite(e));figures.append(card);}
  }
}
function renderCompanyWorkspacePeers(data){
  companyWorkspace.peers=(data.peers||[]).map(peer=>typeof peer==='string'?{ticker:peer,name:state.health?.companies?.find(company=>company.ticker===peer)?.name||peer}:peer);companyWorkspace.selectedPeers.clear();
  $('#company-peers-note').textContent=[data.group_label,data.basis,data.note].filter(x=>typeof x==='string').join(' · ');
  if(!companyWorkspace.peers.length)$('#company-peers-note').textContent=data.reason||data.note||'尚未配置合适的同行候选；可在公司对比页自行选择证券。';
  $('#company-peers-options').replaceChildren(...companyWorkspace.peers.map(peer=>{const label=el('label','company-peer-choice'),input=el('input');input.type='checkbox';input.value=peer.ticker;input.addEventListener('change',()=>{input.checked?companyWorkspace.selectedPeers.add(peer.ticker):companyWorkspace.selectedPeers.delete(peer.ticker);$('#company-peers-compare').disabled=!companyWorkspace.selectedPeers.size;});label.append(input,el('span','',peer.ticker+(peer.name&&peer.name!==peer.ticker?' · '+peer.name:'')));return label;}));
}
async function loadCompanyWorkspace(refresh=false,section=null){
  const scope=companyWorkspaceScope();if(!scope.ticker){companyWorkspaceReset();return;}
  if(companyWorkspace.scope?.ticker!==scope.ticker||companyWorkspace.scope?.asOf!==scope.asOf)companyWorkspaceReset();
  companyWorkspace.controller?.abort();const controller=new AbortController(),generation=companyWorkspace.generation,pageGeneration=++companyWorkspace.pageGeneration;companyWorkspace.controller=controller;
  const timeout=setTimeout(()=>controller.abort(),45000);$('#company-workspace-status').textContent=refresh?'正在检查公开披露；已保存材料仍可查看…':'正在读取公司资料…';
  $('#company-workspace-load').disabled=true;$('#company-workspace-refresh').disabled=true;
  const requests=[['page',companyWorkspacePath(scope,'research-page',{include_market:false,refresh,...(section?{section}:{})})]];
  if(!section)requests.push(['peers',companyWorkspacePath(scope,'peers')],['capabilities','/api/capabilities?ticker='+encodeURIComponent(scope.ticker)],['catalog','/api/catalog?as_of='+encodeURIComponent(scope.asOf)]);
  const labels={page:'公司资料',peers:'同行候选',capabilities:'研究能力',catalog:'申报目录'},pending=new Set(requests.map(([key])=>key)),failures=[];
  const active=()=>companyWorkspaceActive(generation,scope)&&pageGeneration===companyWorkspace.pageGeneration;
  const updateStatus=()=>{
    if(!active())return;
    const parts=[];
    if(pending.size)parts.push((companyWorkspace.page?'已显示可用公司资料；':'')+'仍在读取：'+[...pending].map(key=>labels[key]).join('、'));
    if(failures.length)parts.push('部分来源未返回：'+failures.join('；'));
    if(parts.length){
      $('#company-workspace-status').textContent=parts.join('。');
    }else{
      const coverage=companyWorkspace.page?.source_coverage;
      const forms=coverage?.available_forms?.join('、');
      const external=coverage?.external_updates?'，已登记外部更新源':'，未登记独立外部更新源';
      $('#company-workspace-status').textContent=companyText('资料读取完成。已保存的来源类型：')+(forms||companyText('当前可用申报'))+companyText(external)+companyText('；完整分析可另行生成。');
    }
  };
  if(!section)$('#company-peers-note').textContent='正在读取同行候选…';
  await Promise.allSettled(requests.map(async([key,path])=>{
    try{
      const data=await api(path,{signal:controller.signal});if(!active())return;
      // Each ready source renders immediately; another supplier may still be waiting.
      if(key==='page')renderCompanyWorkspaceSections(data,!!section);
      if(key==='peers')renderCompanyWorkspacePeers(data);
      if(key==='capabilities'){
        companyWorkspace.capabilities=data;$('#company-workspace-report').disabled=!!state.researchRunning||!data.filings_supported;
        $('#company-quarters-refresh').disabled=$('#company-quarters-load').disabled||!data.filings_supported||data.annual_only;
        if(data.annual_only)$('#company-quarters-status').textContent='该申报体系当前支持年报；不会将年度或半年数据当成单季度。';
      }
      if(key==='catalog'){
        const filings=data.filings.filter(f=>f.ticker===scope.ticker&&/^(10-[KQ]|20-F)(\/A)?$/.test(f.form));
        const select=$('#company-statement-filing'),selected=select.value;select.replaceChildren(el('option','','最新可用申报'),...filings.map(f=>{const o=el('option','',f.form+' · 期末 '+f.period_end+' · 公开 '+f.filed);o.value=f.id;return o;}));select.firstElementChild.value='';select.value=filings.some(f=>f.id===selected)?selected:'';
      }
    }catch(error){
      if(active()){
        const message=labels[key]+': '+(error.name==='AbortError'?'等待时间用完，可重试读取':error.message);failures.push(message);
        if(key==='peers')$('#company-peers-note').textContent=message+'。已显示的候选保留；可重新读取资料。';
      }
    }finally{pending.delete(key);updateStatus();}
  }));
  clearTimeout(timeout);if(!active())return;
  $('#company-workspace-load').disabled=false;$('#company-workspace-refresh').disabled=false;
}
function companyWorkspaceTable(table,headers,rows){table.replaceChildren();const head=el('thead'),tr=el('tr');headers.forEach(text=>{const th=el('th','',text);th.scope='col';tr.append(th);});head.append(tr);const body=el('tbody');rows.forEach(cells=>{const row=el('tr');cells.forEach(cell=>{const td=el('td');td.append(typeof cell==='string'?el('span','',cell):cell);row.append(td);});body.append(row);});table.append(head,body);}
async function loadCompanyWorkspaceQuarters(refresh=false){
  const scope=companyWorkspaceScope(),generation=companyWorkspace.generation;if(!scope.ticker)return;
  $('#company-quarters-status').textContent=refresh?'正在检查季报并读取单季度数据…':'正在读取已保存的单季度数据…';
  $('#company-quarters-load').disabled=true;$('#company-quarters-refresh').disabled=true;
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),25000);
  try{
    const data=await api(companyWorkspacePath(scope,'quarterly-sequence',{quarters:8,refresh}),{signal:controller.signal});if(!companyWorkspaceActive(generation,scope))return;
    const evidence=new Map((data.evidence||[]).map(e=>[e.id,e]));
    const rows=data.periods.map(period=>[period.period_end,...['revenue','gross_profit','operating_income','net_income'].map(metric=>{const fact=period.facts.find(e=>e.metric===metric),full=fact&&evidence.get(fact.id);return full?companyWorkspaceCite(full,displayScaledAmount(full)):'未披露 / 未取得';})]);
    companyWorkspaceTable($('#company-quarters-table'),['季度期末','营业收入','毛利润','营业利润','净利润'],rows);
    $('#company-quarters-status').textContent=[...data.missing,data.coverage_note,...(data.source_preparation?.warnings||[]),data.source_preparation?.warning].filter(Boolean).join('；')||'已读取 8 个单季度期间；点击数字查看来源。';
  }catch(error){if(companyWorkspaceActive(generation,scope))$('#company-quarters-status').textContent=error.name==='AbortError'?'季报等待时间用完，已有表格保留，可稍后重试。':error.message;}
  finally{clearTimeout(timeout);if(companyWorkspaceActive(generation,scope)){$('#company-quarters-load').disabled=false;$('#company-quarters-refresh').disabled=!companyWorkspace.capabilities?.filings_supported||companyWorkspace.capabilities?.annual_only;}}
}
function renderCompanyWorkspaceStatements(){
  const data=companyWorkspace.statements;if(!data)return;const mode=$('#company-statement-dimensions').value,evidence=new Map(data.evidence.map(e=>[e.id,e]));
  const filtered=data.rows.filter(row=>mode==='all'||(mode==='segments')===(Object.keys(row.dimensions||{}).length>0));
  const rows=filtered.map(row=>{const full=evidence.get(row.id),dims=Object.entries(row.dimensions||{}).map(([axis,member])=>row.dimension_labels?.[member]||row.dimension_labels?.[axis]||member).join(' / ');return [row.label||row.concept,dims||'无维度',full?companyWorkspaceCite(full,displayScaledAmount(full)):'未取得', (row.start?row.start+' 至 ':'')+row.end];});
  companyWorkspaceTable($('#company-statements-table'),['原文项目','分部 / 维度','金额 / 单位','实际期间'],rows);
  $('#company-statements-status').textContent=data.missing.length?data.missing.join('；'):'显示 '+filtered.length+' 个来源项目。数字舍入显示，出处保留原始精度。';
}
async function loadCompanyWorkspaceStatements(){
  const scope=companyWorkspaceScope(),generation=companyWorkspace.generation;if(!scope.ticker)return;
  const selected={statement:$('#company-statement-type').value,filing:$('#company-statement-filing').value};
  $('#company-statements-status').textContent='正在读取所选申报…';$('#company-statements-load').disabled=true;
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),20000);
  try{const data=await api(companyWorkspacePath(scope,'statements',{statement:selected.statement,...(selected.filing?{filing_id:selected.filing}:{})}),{signal:controller.signal});if(!companyWorkspaceActive(generation,scope)||selected.statement!==$('#company-statement-type').value||selected.filing!==$('#company-statement-filing').value)return;companyWorkspace.statements=data;renderCompanyWorkspaceStatements();}
  catch(error){if(companyWorkspaceActive(generation,scope))$('#company-statements-status').textContent=error.name==='AbortError'?'报表等待时间用完，可重试读取。':error.message;}
  finally{clearTimeout(timeout);if(companyWorkspaceActive(generation,scope))$('#company-statements-load').disabled=false;}
}
async function loadCompanyDcfInputs(){
  const scope=companyWorkspaceScope(),generation=companyWorkspace.generation;if(!scope.ticker)return;
  $('#company-dcf-status').textContent='正在匹配年报现金流与普通股数量…';$('#company-dcf-inputs').disabled=true;$('#company-dcf-form').classList.add('hidden');companyWorkspace.dcfInputs=null;$('#company-dcf-result').replaceChildren();$('#company-dcf-sources').replaceChildren();
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),20000);
  try{const data=await api(companyWorkspacePath(scope,'dcf-inputs'),{signal:controller.signal});if(!companyWorkspaceActive(generation,scope))return;
    if(!data.found){$('#company-dcf-status').textContent=data.reason;return;}
    companyWorkspace.dcfInputs=data;const sources=$('#company-dcf-sources');
    sources.append(el('p','company-help','基期自由现金流 '+displayScaledAmount({value:data.inputs.base_free_cash_flow,unit:data.currency})+' · 普通股 '+displayDecimal(data.inputs.shares_outstanding,0)+' 股 · 股数时点 '+data.source_validation.shares_date));
    data.evidence.forEach(e=>sources.append(companyWorkspaceCite(e,e.label+' · '+displayScaledAmount(e))));
    sources.append(el('p','company-help',data.note));$('#company-dcf-form').classList.remove('hidden');$('#company-dcf-status').textContent='输入金额已与原始证据匹配。请调整假设后计算。';
  }catch(error){if(companyWorkspaceActive(generation,scope))$('#company-dcf-status').textContent=error.name==='AbortError'?'输入资料等待时间用完，可重试读取。':error.message;}
  finally{clearTimeout(timeout);if(companyWorkspaceActive(generation,scope))$('#company-dcf-inputs').disabled=false;}
}
async function runCompanyDcf(event){
  event?.preventDefault();const source=companyWorkspace.dcfInputs,scope=companyWorkspaceScope(),generation=companyWorkspace.generation;if(!source||source.ticker!==scope.ticker||source.as_of!==scope.asOf)return;
  const grid=id=>$(id).value.split(/[,，]/).map(value=>value.trim()).filter(Boolean);
  const payload={ticker:scope.ticker,as_of:scope.asOf,currency:source.currency,...source.inputs,forecast_years:Number($('#company-dcf-years').value),growth_pct:$('#company-dcf-growth').value,discount_pct:$('#company-dcf-discount').value,terminal_growth_pct:$('#company-dcf-terminal').value,growth_grid:grid('#company-dcf-growth-grid'),discount_grid:grid('#company-dcf-discount-grid')};
  $('#company-dcf-run').disabled=true;$('#company-dcf-status').textContent='正在核对输入并计算…';
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),20000);
  try{const data=await api('/api/valuation/dcf',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:controller.signal});if(!companyWorkspaceActive(generation,scope))return;
    const assumptions=data.user_assumptions,root=$('#company-dcf-result');root.replaceChildren(el('p','company-help','本次计算：'+assumptions.forecast_years+' 年 · 基准增长 '+displayDecimal(assumptions.growth_pct)+'% · 折现率 '+displayDecimal(assumptions.discount_pct)+'% · 终值增长 '+displayDecimal(assumptions.terminal_growth_pct)+'%'),el('p','company-help','基准现金流折现值 / 普通股：'+displayDecimal(data.base_case.implied_value_per_share)+' '+data.currency+' / 股。不是企业价值或股权目标价。'));
    const wrap=el('div','company-table-wrap'),table=el('table');companyWorkspaceTable(table,['现金流年增长 %','折现率 %','现金流折现值 / 普通股'],data.sensitivity.map(row=>[displayDecimal(row.growth_pct)+'%',displayDecimal(row.discount_pct)+'%',displayDecimal(row.implied_value_per_share)+' '+data.currency+' / 股']));wrap.append(table);root.append(wrap,el('p','company-help',data.note));
    const download=el('button','secondary','导出计算、假设与来源');download.type='button';download.addEventListener('click',()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'})),a=el('a');a.href=url;a.download=scope.ticker+'-cashflow-sensitivity.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});root.append(download);$('#company-dcf-status').textContent='计算完成；显示至多两位小数，导出保留原始精度与来源。';
  }catch(error){if(companyWorkspaceActive(generation,scope))$('#company-dcf-status').textContent=error.name==='AbortError'?'本次计算等待时间用完，可重试计算。':error.message;}
  finally{clearTimeout(timeout);if(companyWorkspaceActive(generation,scope))$('#company-dcf-run').disabled=false;}
}
$('#company-back').addEventListener('click',()=>{changeView('desk');$('#company-query').focus();});
$('#company-workspace-load').addEventListener('click',()=>loadCompanyWorkspace());
$('#company-workspace-refresh').addEventListener('click',()=>loadCompanyWorkspace(true));
$('#company-workspace-report').addEventListener('click',()=>startAllModuleSummaries());
$('#company-quarters-load').addEventListener('click',()=>loadCompanyWorkspaceQuarters());
$('#company-quarters-refresh').addEventListener('click',()=>loadCompanyWorkspaceQuarters(true));
$('#company-statements-load').addEventListener('click',loadCompanyWorkspaceStatements);
$('#company-dcf-inputs').addEventListener('click',loadCompanyDcfInputs);
$('#company-dcf-form').addEventListener('submit',runCompanyDcf);
$('#company-statement-dimensions').addEventListener('change',renderCompanyWorkspaceStatements);
['#company-statement-type','#company-statement-filing'].forEach(id=>$(id).addEventListener('change',()=>{companyWorkspace.statements=null;$('#company-statements-table').replaceChildren();$('#company-statements-status').textContent='申报或报表已改变，点击读取报表。';}));
$('#company-peers-compare').addEventListener('click',()=>{
  if(comparisonState.busy){$('#company-peers-note').textContent='当前对比仍在运行，完成后可加入新的候选。';return;}
  const scope=companyWorkspaceScope();comparisonState.selected=[{ticker:scope.ticker,name:scope.ticker},...companyWorkspace.peers.filter(p=>companyWorkspace.selectedPeers.has(p.ticker))].slice(0,4);$('#comparison-as-of').value=scope.asOf;comparisonSelection();changeView('comparison');$('#comparison-status').textContent='已填入候选，可调整公司后点击开始对比。';
});
$('#company-historical-pe').addEventListener('click',()=>{changeView('desk');$('#mode').value='reference';$('#mode').dispatchEvent(new Event('change'));$('#question').value='历史年度市盈率与估值区间';updateQuestionPlan();$('#question').focus();});
$('#company-open-scenarios').addEventListener('click',()=>{changeView('desk');const section=$('.scenario-controls');section.open=true;section.scrollIntoView({behavior:'smooth',block:'start'});});
['#ticker','#as-of'].forEach(id=>$(id).addEventListener('change',()=>{companyWorkspaceReset();if(!$('#company-view').classList.contains('hidden'))loadCompanyWorkspace();}));
// Typing in the desk search box no longer resets the company workspace: only an
// actual company/date selection (or an explicit refresh) may stop or rebuild a
// running generation.
companyWorkspaceReset();
