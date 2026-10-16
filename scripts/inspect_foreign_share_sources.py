"""Offline diagnostic of EPS contexts and share/audit text in retained originals."""

import hashlib
import json
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]


def main():
    rows = []
    for ticker in ('PDD','BABA'):
        replay = json.loads((ROOT/f'artifacts/third-round-cold-{ticker}-2026-10-11.json').read_text())
        source = max(replay['sources'], key=lambda f:f['period_end'])
        path = ROOT/replay['scope']/'sec-cache'/(hashlib.sha256(source['source_url'].encode()).hexdigest()+'.cache')
        content = path.read_bytes()
        assert hashlib.sha256(content).hexdigest() == source['content_sha256']
        soup = BeautifulSoup(content,'html.parser')
        eps = []
        for n in soup.find_all('ix:nonfraction'):
            if 'earningspershare' not in n.get('name','').lower() and 'earningsperdepositary' not in n.get('name','').lower():
                continue
            context = soup.find('xbrli:context',id=n.get('contextref'))
            unit = soup.find('xbrli:unit',id=n.get('unitref'))
            eps.append({'concept':n.get('name'),'value':n.get_text(strip=True),'id':n.get('id'),
                        'context':str(context),'unit':str(unit)})
        texts = []
        for n in soup.find_all(['p','div']):
            if n.find(['p','div']):
                continue
            text = n.get_text(' ',strip=True)
            lower = text.lower()
            if 20 < len(text) < 2600 and ('each ads' in lower or 'each american depositary' in lower
                or 'opinion on the financial statements' in lower or 'present fairly' in lower
                or 'qualified opinion' in lower or 'going concern' in lower and 'auditor' in lower):
                texts.append({'id':n.get('id'),'text':text})
        rows.append({'ticker':ticker,'source':source,'eps':eps,'text':texts})
    target = ROOT/'artifacts/third-round-foreign-share-diagnostic-2026-10-11.json'
    target.write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps([{'ticker':r['ticker'],'eps_count':len(r['eps']),'texts':r['text'][:6]} for r in rows],ensure_ascii=False))


if __name__ == '__main__':
    main()
