// Listing selection starts quotes independently of optional filing acquisition.
const companyState = {selected: null, lookup: null, lookupQuery: '', generation: 0, preparation: null, preparedKey: ''};
const companyInput = $('#company-query');
function clearCompanySelection(){clearCompanyPreparationPreview();companyState.generation++;companyState.selected=null;companyState.preparedKey='';companyInput.value='';$('#company-matches').replaceChildren();$('#company-progress').classList.add('hidden');const all=[...$('#ticker').options].find(o=>o.value==='');if(all)all.textContent='全部公司';}
function companyProgress(message, error=false){const p=$('#company-progress');p.textContent=message;p.classList.remove('hidden');p.classList.toggle('company-failed',error);}
function clearCompanyPreparationPreview(){const root=$('#company-preparation-preview');if(root){root.replaceChildren();root.classList.add('hidden');}}
function renderCompanyPreparation(job){
  const p=job.progress||{},counts=[];
  if(Number.isInteger(p.completed_reports)&&Number.isInteger(p.total_reports))counts.push('原件 '+p.completed_reports+'/'+p.total_reports);
  if(Number.isInteger(p.facts))counts.push(p.facts+' 项数字');
  if(Number.isInteger(p.indexed_passages)&&Number.isInteger(p.total_passages))counts.push('新段落索引 '+p.indexed_passages+'/'+p.total_passages);
  companyProgress(job.message+(counts.length?' · '+counts.join(' · '):''),job.status==='failed');
  const root=$('#company-preparation-preview'),preview=job.preview;
  if(!root||!preview||preview.ticker!==companyState.selected?.ticker||preview.as_of!==$('#as-of').value)return;
  root.replaceChildren(el('h3','','已取得的年度数字 · '+preview.ticker+' FY'+preview.fiscal_year));
  for(const claim of preview.claims||[]){const card=el('div','preview-claim');card.append(el('p','',claim.text));
    for(const id of claim.evidence_ids||[]){const source=(preview.evidence||[]).find(e=>e.id===id);if(!source)continue;const cite=el('button','cite','查看出处');cite.type='button';cite.addEventListener('click',()=>showEvidence(source));card.append(cite);}root.append(card);}
  root.append(el('p','company-help',job.status==='failed'?'其余资料准备未完成；以上已保存数字保留，可重试准备。':job.status==='ready'?'原件准备完成；可继续提问或查看公司概览。':'以上数字已核验并保存；其余原件和检索索引仍在准备。'));
  root.classList.remove('hidden');
}
function addCompanyOption(candidate){
  const all=[...$('#ticker').options].find(o=>o.value==='');if(all)all.textContent='全部公司';
  let option=[...$('#ticker').options].find(o=>o.value===candidate.ticker);
  if(!option){option=el('option','',candidate.name+' · '+candidate.ticker);option.value=candidate.ticker;$('#ticker').prepend(option);}
  $('#ticker').value=candidate.ticker;
}
async function prepareSelectedCompany(){
  const candidate=companyState.selected;
  if(!candidate||candidate.filings_supported===false)return;
  const asOf=$('#as-of').value;
  const key=candidate.ticker+'|'+asOf;
  const current=()=>companyState.selected?.ticker===candidate.ticker&&$('#as-of').value===asOf;
  if(companyState.preparedKey===key)return;
  if(companyState.preparation?.key===key)return companyState.preparation.promise;
  const promise=(async()=>{
    clearCompanyPreparationPreview();
    companyProgress('正在准备 '+candidate.name+' 的年报…');
    let job=await api('/api/companies/prepare',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ticker:candidate.ticker,as_of:asOf,years:2})});
    while(job.status==='running'){
      if(!current())return;
      renderCompanyPreparation(job);
      await new Promise(resolve=>setTimeout(resolve,800));
      job=await api('/api/companies/jobs/'+encodeURIComponent(job.id));
    }
    if(current())renderCompanyPreparation(job);
    if(job.status!=='ready')throw new Error(job.error||job.message);
    if(current()){companyState.preparedKey=key;const count=job.result.filings.filter(f=>/^(10-K|20-F)(\/A)?$/.test(f.form)).length;const ready=job.result.annual_financial_count>0?'年报原文及 '+job.result.annual_financial_count+' 项年度财务指标已就绪；年度 ADS 市盈率须有原件单独披露的美元 EPS，季度仍待接入。':job.result.annual_text_only?(job.result.annual_financials_supported?'年报原文已就绪；原件中尚未提取到已登记的年度数字。':'年报原文已就绪；财务数字仍待接入。'):'可用年报已就绪。';companyProgress(candidate.name+' · '+count+' 份'+ready+(job.result.warning||''));}
    await loadCatalog();
  })();
  companyState.preparation={key,promise};
  try{await promise;}catch(error){if(current())companyProgress(error.message,true);throw error;}
  finally{if(companyState.preparation?.promise===promise)companyState.preparation=null;}
}
async function selectCompany(candidate, query,prepare=true){
  clearCompanyPreparationPreview();
  companyState.selected={...candidate,query};
  addCompanyOption(candidate);
  $('#company-matches').replaceChildren(el('p','company-selected',candidate.name+' · '+candidate.ticker+(candidate.product?' · '+candidate.product:'')+(candidate.currency?' · '+candidate.currency:'')+(candidate.cik?' · CIK '+candidate.cik:'')));
  $('#ticker').dispatchEvent(new Event('change'));
  updateQuestionPlan();
  if(candidate.filings_supported!==false){if(prepare)prepareSelectedCompany().catch(()=>{});}
  else companyProgress(candidate.filing_note||'正在查询所选证券的行情；财报来源尚未接入。价格与走势图无需下载财报，取得数据后显示。');
}
async function lookupCompany(prepare=true){
  const query=companyInput.value.trim();
  if(!query)return;
  if(companyState.selected?.query===query)return prepare?prepareSelectedCompany():undefined;
  if(companyState.lookup&&companyState.lookupQuery===query)return companyState.lookup;
  const generation=++companyState.generation;
  companyState.lookupQuery=query;
  const promise=(async()=>{
    companyProgress('正在查找公司，核对名称与股票代码…');
    $('#find-company').disabled=true;
    let data;
    try{data=await api('/api/stocks/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query})});}
    catch(error){throw error;}
    if(!data.match_count)data=await api('/api/companies/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query,translate:$('#mode').value==='agent'})});
    if(generation!==companyState.generation||companyInput.value.trim()!==query)return;
    const root=$('#company-matches');root.replaceChildren(el('p','company-help',data.message));
    if(data.translation)root.append(el('p','company-help','名称转换为 '+data.resolved_query+'，候选身份来自 SEC 名录（'+(data.translation.usage.total_tokens||0)+' tokens）。'));
    if(data.match_count===1){await selectCompany(data.candidates[0],query,prepare);return;}
    companyProgress(data.match_count?'请选择匹配的公司。':data.message,!data.match_count);
    data.candidates.forEach(candidate=>{
      const button=el('button','company-candidate',candidate.name+' · '+candidate.ticker+(candidate.exchange?' · '+candidate.exchange:'')+(candidate.product?' · '+candidate.product:'')+(candidate.currency?' · '+candidate.currency:'')+(candidate.cik?' · CIK '+candidate.cik:''));
      button.addEventListener('click',()=>selectCompany(candidate,query,prepare).catch(error=>companyProgress(error.message,true)));
      root.append(button);
    });
    if(data.match_count>data.candidates.length)root.append(el('p','company-help','仅显示前 20 项，请输入更完整的名称缩小范围。'));
  })();
  companyState.lookup=promise;
  try{await promise;}catch(error){if(generation===companyState.generation)companyProgress(error.message,true);throw error;}
  finally{if(companyState.lookup===promise){companyState.lookup=null;$('#find-company').disabled=false;}}
}
async function ensureCompany(question='',prepare=true,priceOnly=false){
  if(!state.health.company_discovery||!companyInput.value.trim())return;
  await lookupCompany(prepare&&!priceOnly&&!marketQuestion(question));
  if(!companyState.selected||companyState.selected.query!==companyInput.value.trim())throw new Error('请先在候选列表选择对应的公司。');
  if(!marketQuestion(question)&&!priceOnly){
    if(companyState.selected.filings_supported===false)throw new Error('此代码当前支持行情与长期走势。请使用“股价是多少”或“过去五年涨跌和最大回撤”等问题；财报分析可选择 SEC 美国公司。');
    if(prepare)await prepareSelectedCompany();
  }
  addCompanyOption(companyState.selected);
}
$('#find-company').addEventListener('click',()=>lookupCompany().catch(()=>{}));
companyInput.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();lookupCompany().catch(()=>{});}});
companyInput.addEventListener('input',()=>{clearCompanyPreparationPreview();companyState.generation++;companyState.selected=null;companyState.preparedKey='';$('#company-matches').replaceChildren();$('#company-progress').classList.add('hidden');if(companyInput.value.trim()){$('#ticker').value='';resetMarket('请点击“查找股票”，再选择上市市场和币种。行情无需下载财报。');}else resetMarket();updateQuestionPlan();});
$('#ticker').addEventListener('change',()=>{if(companyInput.value.trim()&&(!companyState.selected||$('#ticker').value!==companyState.selected.ticker))clearCompanySelection();});
$('#as-of').addEventListener('change',()=>{clearCompanyPreparationPreview();if(companyState.selected)prepareSelectedCompany().catch(()=>{});});
