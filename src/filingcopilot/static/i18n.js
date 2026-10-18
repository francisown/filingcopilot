// Only presentation labels are localized. Source text, saved answers and exports stay verbatim.
(function(global){
  'use strict';
  const catalog=global.FilingLocaleEnglish||{};
  const key='filingcopilot.language', original=new WeakMap();
  const escape=s=>s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
  const fragments=Object.keys(catalog).filter(s=>s.length>=2).sort((a,b)=>b.length-a.length);
  // A literal such as "0 秒" is a whole label, not the tail of "420 秒".
  // Never let fragment translation change a dynamic numeric value.
  const matcher=fragments.length?new RegExp(fragments.map(s=>(/^\d/.test(s)?'(?<![\\d.])':'')+escape(s)+(/\d$/.test(s)?'(?![\\d.])':'')).join('|'),'g'):null;
  let saved;try{saved=global.localStorage?.getItem(key);}catch(_){}
  let language=['zh','en'].includes(saved)?saved:'en';
  function translate(value){
    const s=String(value??'');
    if(language!=='en')return s;
    if(Object.prototype.hasOwnProperty.call(catalog,s))return catalog[s];
    let result=matcher?s.replace(matcher,(part,index)=>{
      let value=catalog[part];
      if(index&&/^[A-Za-z0-9]/.test(value)&&/[A-Za-z0-9\u4e00-\u9fff]/.test(s[index-1]))value=' '+value;
      if(index+part.length<s.length&&/[A-Za-z0-9]$/.test(value)&&/[A-Za-z0-9\u4e00-\u9fff]/.test(s[index+part.length]))value+=' ';
      return value;
    }):s;
    for(const [before,after] of [[' 至 ',' to '],['/股','/share'],[' 倍','x'],['（','('],['）',')'],['。','. '],['，',', '],['：',': '],['；','; '],['、',', ']])result=result.split(before).join(after);
    return result.replace(/([A-Za-z]):(?=[A-Za-z])/g,'$1: ').replace(/ {2,}/g,' ');
  }
  const protectedSelector='pre,code,script,style,[data-i18n-skip],.source-text,.claim-text,.history-question';
  function protectedNode(node){return !!node?.parentElement?.closest?.(protectedSelector);}
  function localizeText(node){
    if(protectedNode(node)||!node.nodeValue?.trim())return;
    const previous=original.get(node);let raw=node.nodeValue;
    if(previous){
      if(raw===previous.display)raw=previous.raw;
      else if(previous.display&&previous.display!==previous.raw&&raw.includes(previous.display))raw=raw.replace(previous.display,previous.raw);
    }
    const display=translate(raw);original.set(node,{raw,display});
    if(node.nodeValue!==display)node.nodeValue=display;
  }
  function localizeElement(node){
    if(node.matches?.(protectedSelector))return;
    for(const name of ['title','placeholder','aria-label','data-q']){
      if(!node.hasAttribute?.(name))continue;
      const memory=original.get(node)||{};
      const current=node.getAttribute(name),last=memory[name];
      const raw=last&&current===last.display?last.raw:current,display=translate(raw);
      memory[name]={raw,display};original.set(node,memory);
      if(current!==display)node.setAttribute(name,display);
    }
  }
  function apply(root){
    if(!root)return;
    if(root.nodeType===3){localizeText(root);return;}
    if(root.nodeType===1&&root.matches?.(protectedSelector))return;
    if(root.nodeType===1)localizeElement(root);
    if(root.nodeName==='TEXTAREA'||root.nodeName==='INPUT')return;
    for(const node of root.childNodes||[])apply(node);
  }
  function update(){
    if(!global.document)return;
    global.document.documentElement.lang=language==='zh'?'zh-CN':'en';
    apply(global.document.documentElement);
    const select=global.document.querySelector('#language-select');if(select)select.value=language;
    global.document.title=language==='zh'?'FilingCopilot · 股票与财报研究':'FilingCopilot · Stock and Filing Research';
  }
  function setLanguage(value){
    if(!['zh','en'].includes(value))return;
    language=value;try{global.localStorage?.setItem(key,value);}catch(_){}
    update();
    global.dispatchEvent?.(new CustomEvent('filingcopilot:language-change',{detail:{language}}));
  }
  function request(path, options){
    const next={...(options||{})};
    const headers=new Headers(next.headers||{});headers.set('Accept-Language',language);next.headers=headers;
    if(next.body&&typeof next.body==='string'&&/^\/api\/(research(?:\/|$)|comparison$|financial\/trend$|valuation\/scenarios$)/.test(path)){
      try{const body=JSON.parse(next.body);if(!body.language)body.language=language;next.body=JSON.stringify(body);}catch(_){}
    }
    return next;
  }
  global.FilingI18n={translate,language:()=>language,locale:()=>language==='zh'?'zh-CN':'en-US',setLanguage,apply,request};
  function init(){
    update();
    const select=global.document.querySelector('#language-select');select?.addEventListener('change',()=>setLanguage(select.value));
    if(global.MutationObserver){const observer=new MutationObserver(records=>{
      for(const record of records){
        if(record.type==='characterData')apply(record.target);
        else if(record.type==='attributes')localizeElement(record.target);
        else for(const node of record.addedNodes)apply(node);
      }
    });observer.observe(global.document.documentElement,{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:['title','placeholder','aria-label','data-q']});}
  }
  if(global.document){
    if(global.document.readyState==='loading')global.document.addEventListener('DOMContentLoaded',init,{once:true});
    else init();
  }
})(globalThis);
