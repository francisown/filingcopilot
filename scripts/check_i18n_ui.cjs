// Browser-label contracts; no providers, models, source translation or financial scoring.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function context(languages,saved){
 const values=new Map(saved?[['filingcopilot.language',saved]]:[]);
 const c=vm.createContext({navigator:{languages},localStorage:{getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v)},Headers,console});
 for(const name of ['locale-en','i18n','format'])vm.runInContext(fs.readFileSync('src/filingcopilot/static/'+name+'.js','utf8'),c);
 return {c,values,run:s=>vm.runInContext(s,c)};
}
const en=context(['en-GB','zh-CN']),zh=context(['zh-CN','en-US'],'zh'),other=context(['fr-FR']);
assert.equal(en.c.FilingI18n.language(),'en');assert.equal(zh.c.FilingI18n.language(),'zh');assert.equal(other.c.FilingI18n.language(),'en');
assert.equal(context(['zh-CN','en-US']).c.FilingI18n.language(),'en');
assert.equal(context(['en-US'],'zh').c.FilingI18n.language(),'zh');
assert.equal(context(['zh-CN'],'invalid').c.FilingI18n.language(),'en');
assert.equal(en.c.FilingI18n.translate('研究工作台'),'Research workspace');
assert.equal(en.c.FilingI18n.translate('查看行情来源与日线价格'),'View source and daily prices');
assert.equal(en.c.FilingI18n.translate('查看行情来源'),'View market source');
for(const label of [
  ' 本轮时间预算 420 秒，到期保存已核验部分，可继续查询具体问题。',
  '执行预算：预计 4 次模型、7 次工具调用，约 17,200 token；延迟约 242 秒，模型成本未配置价格。',
  '本轮预算 420 秒。保留了 2 项通过核验的结果；未完成审查的判断不作结论。',
]) assert.doesNotMatch(en.c.FilingI18n.translate(label), /[\u4e00-\u9fff]/, 'Dynamic budget labels must use the selected language');
assert.match(en.c.FilingI18n.translate('本轮预算 420 秒。保留了 2 项通过核验的结果；未完成审查的判断不作结论。'), /\b420\b/, 'Translation must preserve numeric values');
assert.doesNotMatch(en.c.FilingI18n.translate('耗时 420 秒'), /42 0/, 'A zero-second label must not match inside a longer number');
assert.equal(en.c.displayEvidenceAmount({value:'8.72',unit:'USD/股'}),'8.72 USD/share','protected numeric blocks must use localized display units');
assert.equal(en.run("displayScaledAmount({value:'716924000000',unit:'USD'})"),'716.92 B USD');
assert.equal(zh.run("displayScaledAmount({value:'716924000000',unit:'USD'})"),'7,169.24 亿 USD');
assert.equal(en.run("displayDecimal('-0.00001')"),'between −0.01 and 0');
assert.equal(en.run("displayUnit('shares')"),'shares');
en.c.FilingI18n.setLanguage('zh');assert.equal(en.values.get('filingcopilot.language'),'zh');
en.c.FilingI18n.setLanguage('en');
const options=en.c.FilingI18n.request('/api/research/jobs',{method:'POST',body:JSON.stringify({question:'User question',language:'zh'})});
assert.equal(JSON.parse(options.body).language,'zh','Explicit in-flight job language must survive UI switching and retries');
assert.equal(JSON.parse(en.c.FilingI18n.request('/api/research',{body:'{"question":"User question"}'}).body).language,'en');
assert.equal(options.headers.get('Accept-Language'),'en');
function element(classes=''){
 return {nodeType:1,childNodes:[],nodeName:'DIV',parentElement:null,
  matches(s){return classes.split(' ').some(c=>c&&s.includes('.'+c));},
  closest(s){return this.matches(s)?this:this.parentElement?.closest(s);},hasAttribute(){return false;}};
}
function node(text,parent){const n={nodeType:3,nodeValue:text,parentElement:parent};parent.childNodes.push(n);return n;}
const root=element(),label=node('研究工作台',root),source=element('source-text'),answer=element('claim-text');
source.parentElement=root;answer.parentElement=root;root.childNodes.push(source,answer);
const originalSource=node('净利润下降；原始文字必须保留。',source),originalAnswer=node('营业收入：123 亿美元。',answer);
en.c.FilingI18n.apply(root);assert.equal(label.nodeValue,'Research workspace');
assert.equal(originalSource.nodeValue,'净利润下降；原始文字必须保留。');assert.equal(originalAnswer.nodeValue,'营业收入：123 亿美元。');
en.c.FilingI18n.setLanguage('zh');en.c.FilingI18n.apply(root);assert.equal(label.nodeValue,'研究工作台');
label.nodeValue='公司对比';en.c.FilingI18n.setLanguage('en');en.c.FilingI18n.apply(root);assert.equal(label.nodeValue,'Company comparison');
const shared=JSON.parse(fs.readFileSync('src/filingcopilot/locale-en.json','utf8'));
assert.deepEqual(JSON.parse(JSON.stringify(en.c.FilingLocaleEnglish)),shared);
console.log('Bilingual UI: language detection, persistence, rendering, units, protected originals and retry language passed');
