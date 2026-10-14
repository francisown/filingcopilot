// Display rounding only. Source strings, calculations and exported packages stay unchanged.
function displayDecimal(value, digits=2, minimum=0) {
  const text=String(value ?? '').trim();
  const match=/^([+-]?)(\d+)(?:\.(\d*))?(?:e([+-]?\d+))?$/i.exec(text);
  if(!match || text.length>2000)return '—';
  digits=Math.max(0,Math.min(8,digits));minimum=Math.max(0,Math.min(digits,minimum));
  const exponent=Number(match[4]||0);
  if(!Number.isInteger(exponent)||Math.abs(exponent)>500)return '—';
  const fraction=match[3]||'', magnitude=BigInt(match[2]+fraction), scale=fraction.length-exponent;
  const shift=digits-scale;
  let rounded;
  if(shift>=0)rounded=magnitude*10n**BigInt(shift);
  else {
    const divisor=10n**BigInt(-shift), quotient=magnitude/divisor, twiceRemainder=2n*(magnitude%divisor);
    // Match backend Decimal display rounding so a tie cannot disagree with the answer.
    rounded=quotient+(twiceRemainder>divisor||(twiceRemainder===divisor&&quotient%2n!==0n)?1n:0n);
  }
  const negative=match[1]==='-' && magnitude!==0n;
  if(magnitude!==0n && rounded===0n){
    const threshold=digits?'0.'+'0'.repeat(digits-1)+'1':'1';
    return negative?(typeof FilingI18n!=='undefined'&&FilingI18n.language()==='en'?'between −'+threshold+' and 0':'介于 −'+threshold+' 与 0'):'<'+threshold;
  }
  const factor=10n**BigInt(digits), integer=(rounded/factor).toLocaleString('en-US');
  let decimal=digits?(rounded%factor).toString().padStart(digits,'0'):'';
  while(decimal.length>minimum && decimal.endsWith('0'))decimal=decimal.slice(0,-1);
  return (negative&&rounded!==0n?'−':'')+integer+(decimal?'.'+decimal:'');
}
function displayUnit(unit){
  if(/^iso4217:[A-Z]{3}$/.test(unit))return unit.slice(8);
  if(['shares','xbrli:shares'].includes(unit))return typeof FilingI18n!=='undefined'&&FilingI18n.language()==='en'?'shares':'股';
  if(unit==='xbrli:pure')return typeof FilingI18n!=='undefined'&&FilingI18n.language()==='en'?'dimensionless':'无量纲';
  if(unit==='倍'&&typeof FilingI18n!=='undefined'&&FilingI18n.language()==='en')return 'x';
  if(typeof unit==='string'&&unit.endsWith('/股')&&typeof FilingI18n!=='undefined'&&FilingI18n.language()==='en')return unit.slice(0,-2)+'/share';
  return unit;
}
function displayEvidenceAmount(item) {
  const value=displayDecimal(item.value);
  const unit=displayUnit(item.unit);
  return value+(unit==='%'?'%':' '+unit);
}
function sourceValue(item) {
  const block=el('div','source-text numeric-value',displayEvidenceAmount(item));
  if(/\.\d{3,}|e[+-]?\d/i.test(String(item.value))){
    const english=typeof FilingI18n!=='undefined'&&FilingI18n.language()==='en';
    const detail=el('details','raw-value');detail.append(el('summary','',english?'View full precision':'查看原始精度'),el('code','',item.value+' '+item.unit),el('p','',english?'Display uses at most two decimal places; calculations and exports retain the original precision.':'显示保留至多两位小数；计算与导出保留原始精度。'));block.append(detail);
  }
  return block;
}

function displayScaledAmount(e){
  e={...e,unit:displayUnit(e.unit)};
  if(e.unit==='%'||e.unit==='倍')return displayEvidenceAmount(e);
  if(!/^[A-Z]{3}$/.test(e.unit))return displayEvidenceAmount(e);
  const english=typeof FilingI18n!=='undefined'&&FilingI18n.language()==='en';
  const absolute=Math.abs(Number(e.value));
  const power=english?(absolute>=1e12?12:absolute>=1e9?9:absolute>=1e6?6:absolute>=1e3?3:0):(absolute>=1e8?8:absolute>=1e4?4:0);
  if(!power)return displayEvidenceAmount(e);
  const match=/^(.*?)(?:e([+-]?\d+))?$/i.exec(String(e.value));
  return displayDecimal(match[1]+'e'+(Number(match[2]||0)-power))+(english?({12:' T ',9:' B ',6:' M ',3:' K '})[power]:power===8?' 亿 ':' 万 ')+e.unit;
}
