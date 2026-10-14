"""Run the real FilingCopilot pipeline from its immutable comparison zip."""

import json
import os
import sys
import time
from dataclasses import replace
from pathlib import Path


def main():
    payload=json.load(sys.stdin)
    sys.path.insert(0,str(Path(payload['snapshot']).resolve())+'/src')
    from filingcopilot.models import ResearchRequest
    from filingcopilot.service import FilingCopilot
    from filingcopilot.settings import Settings

    baseline=Settings.from_env()
    budgets=payload['budgets']
    settings=replace(baseline,home=Path(payload['home']),embedding_mode='off',
        api_base=os.environ['FC_COMPARE_BASE'],api_key=os.environ['FC_COMPARE_KEY'],
        model=payload['model'],review_model=payload['review_model'],
        research_timeout=budgets['seconds'],model_timeout=budgets['model_timeout'],
        min_model_interval=budgets['min_interval'],model_max_tokens=budgets['max_tokens'],
        review_max_tokens=budgets['review_max_tokens'],max_rounds=budgets['max_rounds'],
        max_tool_calls=budgets['max_tool_calls'],model_attempts=budgets['attempts'])
    started=time.perf_counter()
    try:
        service=FilingCopilot(settings)
        bundle=payload['bundle']
        answer=service.research(ResearchRequest(ticker=bundle['ticker'],question=bundle['question'],as_of=bundle['as_of'],mode='agent'))
        result={'status':answer.status,'answer':answer.model_dump(mode='json')}
    except Exception as exc:
        result={'status':'failed','answer':'','exception':type(exc).__name__}
    result['seconds']=round(time.perf_counter()-started,3)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    main()
