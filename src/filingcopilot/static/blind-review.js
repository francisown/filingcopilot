(()=>{
  'use strict';
  const data=JSON.parse(document.getElementById('sample').textContent),rows=data.rows;
  const $=id=>document.getElementById(id),allowed=new Set(['support','unsupported','unclear']);
  let index=0,labels={},reviewer='';const key='filingcopilot-blind-'+data.sample_sha256;
  try{const prior=JSON.parse(localStorage.getItem(key)||'null');if(prior){labels=prior.labels||{};reviewer=prior.reviewer||'';}}catch{}
  $('reviewer').value=reviewer;
  function save(){try{localStorage.setItem(key,JSON.stringify({labels,reviewer:$('reviewer').value}));}catch{$('status').textContent='浏览器未允许保存草稿；请在关闭前导出。';}}
  function text(tag,value,cls){const n=document.createElement(tag);n.textContent=value||'';if(cls)n.className=cls;return n;}
  function render(){const row=rows[index];$('position').textContent=(index+1)+' / '+rows.length+' · '+row.blind_id;
    $('question').textContent=row.question||'';$('answer').textContent=row.answer||row.text||'';
    const complete=rows.filter(r=>allowed.has(labels[r.blind_id])).length;$('progress').textContent='已标 '+complete+' / '+rows.length;
    $('previous').disabled=index===0;$('next').disabled=index===rows.length-1;
    document.querySelectorAll('[data-label]').forEach(b=>{const selected=labels[row.blind_id]===b.dataset.label;b.classList.toggle('selected',selected);b.setAttribute('aria-pressed',String(selected));});
    $('evidence').replaceChildren();for(const evidence of row.evidence||[]){const box=text('div','','source');box.append(text('p',(evidence.ticker||'')+' · '+(evidence.section||evidence.label||evidence.kind||'')));
      if(evidence.filing)box.append(text('p',evidence.filing.form+' · 公开 '+evidence.filing.filed+' · 报告期末 '+evidence.filing.period_end,'muted'));
      box.append(text('p',evidence.text||JSON.stringify(evidence,null,2)));
      try{const url=new URL(evidence.source_url);if(url.protocol==='https:'){const link=text('a','打开原始出处');link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';box.append(link);}}catch{}
      $('evidence').append(box);}
  }
  function choose(label){if(!allowed.has(label))return;labels[rows[index].blind_id]=label;save();render();}
  document.querySelectorAll('[data-label]').forEach(b=>b.addEventListener('click',()=>choose(b.dataset.label)));
  $('previous').addEventListener('click',()=>{if(index>0){index--;render();}});$('next').addEventListener('click',()=>{if(index<rows.length-1){index++;render();}});
  $('reviewer').addEventListener('input',save);
  document.addEventListener('keydown',event=>{if(['INPUT','TEXTAREA','SELECT','BUTTON'].includes(event.target.tagName))return;
    const selected={'1':'support','2':'unsupported','3':'unclear'}[event.key];if(selected){event.preventDefault();choose(selected);}
    if(event.key==='ArrowLeft'&&index>0){index--;render();}if(event.key==='ArrowRight'&&index<rows.length-1){index++;render();}});
  $('export').addEventListener('click',()=>{const name=$('reviewer').value.trim();if(!name||!$('human').checked){$('status').textContent='请由真人填写姓名或代号，并确认本人完成判断。';return;}
    const output={schema:'filingcopilot.human-label-export/v1',human_reviewer:name,human_attestation:'self_declared',sample_sha256:data.sample_sha256,
      rows:rows.map(row=>({...row,label:allowed.has(labels[row.blind_id])?labels[row.blind_id]:null}))};
    const url=URL.createObjectURL(new Blob([JSON.stringify(output,null,2)+'\n'],{type:'application/json'})),link=document.createElement('a');link.href=url;link.download='filingcopilot-human-labels.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    $('status').textContent='已导出。未填写的标签保持空白，不能计作完成人工验收。';});
  render();
})();
