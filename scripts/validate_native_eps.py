"""Real cached PDD/BABA/JD ordinary EPS, isolated corpus and offline source audit."""

import argparse
import hashlib
import json
import shutil
import uuid
from datetime import date
from pathlib import Path

from filingcopilot.audit import audit_package
from filingcopilot.corpus import Corpus
from filingcopilot.ifrs_sources import extract_ifrs_facts
from filingcopilot.models import Filing, ResearchRequest
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings


def main():
    root=Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument('--keep-home', action='store_true', help='Keep the temporary native-EPS corpus for forensic inspection')
    args = parser.parse_args()
    home=root/'.filingcopilot'/'native-eps-validation'/uuid.uuid4().hex
    service=FilingCopilot(Settings(home=home,embedding_mode='off'),gateway=False)
    source=Corpus(root/'.filingcopilot',readonly=True)
    rows=[]
    for ticker in ('PDD','BABA','JD'):
        filing=Filing.model_validate(max(source.catalog(date(2026,10,11),ticker),key=lambda f:f['period_end']))
        cache_name=hashlib.sha256(filing.source_url.encode()).hexdigest()+'.cache'
        raw_path=root/'.filingcopilot/sec-cache'/cache_name
        raw=raw_path.read_bytes()
        assert hashlib.sha256(raw).hexdigest()==filing.content_sha256
        facts=extract_ifrs_facts(raw,filing)
        service.corpus.ingest(filing,facts,[])
        target=home/'sec-cache'/cache_name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(raw_path,target)
        answer=service.research(ResearchRequest(question=f'{ticker} FY{filing.period_end.year} 稀释每股收益',ticker=ticker,as_of='2026-10-11',mode='reference'))
        package=service.export_package(answer.id)
        audit=audit_package(package,source_cache=home/'sec-cache')
        eps=[e for e in answer.evidence if e.get('metric')=='diluted_eps']
        assert len(eps)==1 and eps[0]['unit']=='CNY/shares' and audit['status']=='consistent'
        rows.append({'ticker':ticker,'filing':filing.model_dump(mode='json'),
            'ordinary_eps':eps[0],'audit_status':audit['status'],'raw_sources':audit['raw_sources'],
            'answer':answer.model_dump(mode='json'),'scope':'ordinary-share CNY, not USD ADS EPS or an ADS P/E'})
    output=root/'artifacts/third-round-native-eps-real-2026-10-11.json'
    output.write_text(json.dumps({'home':str(home),'home_retained':args.keep_home,'rows':rows,'network':False,'llm':False},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if not args.keep_home:
        shutil.rmtree(home, ignore_errors=True)
    print(json.dumps([{'ticker':r['ticker'],'eps':r['ordinary_eps']['value'],'unit':r['ordinary_eps']['unit'],'audit':r['audit_status']} for r in rows]))


if __name__=='__main__':
    main()
