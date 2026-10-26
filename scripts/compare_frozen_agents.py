"""20 paired public-source cases, resumable raw outputs and blind annotation.

The source/evidence subset is identical per pair. No answer key or semantic
label is generated. FinRobot's actual upstream analysis-agent factory body and
instructions run in an isolated pinned environment; its full provider/pipeline
is deliberately outside this small traceability experiment.
"""

import argparse
import ast
import hashlib
import json
import os
import random
import subprocess
import time
import zipfile
from dataclasses import replace
from datetime import date
from pathlib import Path

from filingcopilot.corpus import Corpus
from filingcopilot.eval_support import require_file
from filingcopilot.models import Fact, Filing, Passage
from filingcopilot.retrieval import Retriever
from filingcopilot.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
TOPICS = [
    ('business','business model revenues services','公司的商业模式和主要收入来源是什么？'),
    ('growth','revenue increased growth drivers','这一财年收入增长的主要来源是什么？区分数字与公司解释。'),
    ('profit','net income operating expenses profit','这一财年利润变化与哪些经营因素有关？'),
    ('cash','operating cash flows working capital','经营现金流变化的原因是什么？它能说明哪些盈利质量问题？'),
    ('capex','capital expenditures property equipment investments','公司资本投入主要用于什么？不要把所有投资当作现金资本开支。'),
    ('competition','competition competitors competitive','公司面临哪些竞争压力，披露了哪些优势或限制？'),
    ('risk','risk factors regulation uncertainties','有哪些最值得关注的经营与监管风险？区分风险和已发生的事件。'),
    ('vie','variable interest entities contractual arrangements','美国上市股份与中国经营实体之间是什么关系，VIE安排有什么风险？'),
    ('ads','American depositary shares ordinary shares currency','ADS与普通股、报告币种如何对应？仅报告原文支持的口径。'),
    ('audit','independent registered public accounting opinion internal control','审计意见覆盖什么，是否有保留或持续经营说明？引用原文，不把一般风险当审计结论。'),
]


def write(path, obj):
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(obj, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    temporary.replace(path)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frozen_budgets(settings):
    return {'seconds': settings.research_timeout, 'max_tokens': settings.model_max_tokens,
            'min_interval': settings.min_model_interval, 'model_timeout': settings.model_timeout,
            'attempts': settings.model_attempts, 'review_max_tokens': settings.review_max_tokens,
            'max_rounds': settings.max_rounds, 'max_tool_calls': settings.max_tool_calls}


def validate_budget_models(manifest, settings):
    if manifest['model'] != settings.model or manifest['review_model'] != settings.review_model:
        raise ValueError('Resume cannot change models')
    if manifest['budgets'] != frozen_budgets(settings):
        raise ValueError('Resume cannot change research, retry, pacing, round or token budgets')


def ensure_case_sources(home, bundle):
    """Rebuild missing fixtures from the manifest; reject existing source drift."""
    case_home = (home / bundle['id']).resolve()
    if case_home == home.resolve() or not case_home.is_relative_to(home.resolve()):
        raise ValueError('Frozen case path must remain inside the comparison home')
    if not (case_home / 'corpus.sqlite3').exists():
        Corpus(case_home).ingest(Filing.model_validate(bundle['filing']),
                                [Fact.model_validate(f) for f in bundle['facts']],
                                [Passage.model_validate(p) for p in bundle['passages']])
    corpus = Corpus(case_home, readonly=True)
    expected = {'filings': [bundle['filing']], 'facts': bundle['facts'],
                'passages': bundle['passages']}
    with corpus.connect() as db:
        for table, rows in expected.items():
            actual = [json.loads(row['payload']) for row in db.execute(f'SELECT payload FROM {table}')]
            if sorted(actual, key=lambda row: row['id']) != sorted(rows, key=lambda row: row['id']):
                raise ValueError('Frozen case sources changed: '+bundle['id']+'/'+table)


def freeze(home, output, settings, upstream):
    rows = []
    for ticker in ('PDD','BABA'):
        replay_path = require_file(
            ROOT/f'artifacts/third-round-cold-{ticker}-2026-10-11.json',
            f"frozen {ticker} replay",
        )
        replay = json.loads(replay_path.read_text())
        source_home = ROOT/replay['scope']
        if not source_home.is_dir():
            raise SystemExit(
                f"Missing frozen replay scope for {ticker}: {source_home}. "
                "Restore the local .filingcopilot archive; the summary JSON is tracked, "
                "but the replay corpus was never part of the repository."
            )
        # Cold replay stores evidence directly in the scope, not in main corpus.
        corpus = Corpus(source_home, readonly=True)
        available = [Filing.model_validate(f) for f in corpus.catalog(date(2026,10,11), ticker)]
        filing = max(available, key=lambda f:f.period_end)
        with corpus.connect() as db:
            facts = [Fact.model_validate_json(row['payload']) for row in db.execute('SELECT payload FROM facts WHERE filing_id=? ORDER BY id', (filing.id,))]
        retriever = Retriever(corpus, replace(settings,home=source_home,embedding_mode='off'))
        for topic, seed, question in TOPICS:
            found = retriever.search(seed,date(2026,10,11),ticker,8,filing_id=filing.id,lexical_only=True)
            passages = [hit['passage'] for hit in found['hits']]
            bundle = {'id':f'{ticker}-{topic}', 'ticker':ticker,
                      'question':f'根据期末为{filing.period_end}的年报，'+question,
                      'as_of':'2026-10-11','filing':filing.model_dump(mode='json'),
                      'facts':[f.model_dump(mode='json') for f in facts], 'passages':passages,
                      'selection_seed':seed, 'scope':'8 lexical candidates, no answer/gold used'}
            case_home = home/bundle['id']
            dest = Corpus(case_home)
            dest.ingest(filing,facts,[Passage.model_validate(p) for p in passages])
            rows.append(bundle)
    source = upstream/'finrobot_desktop/finrobot/engine/agents/factory.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='create_sub_agents')
    manifest = {'schema':'filingcopilot.paired-frozen/v1','cases':rows,
        'finrobot_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=upstream,text=True).strip(),
        'factory_sha256':sha(source),'factory_function_ast_sha256':hashlib.sha256(ast.dump(node).encode()).hexdigest(),
        'instructions_sha256':sha(source.parent/'instructions/analysis_agent.md'),
        'configuration':'FinRobot V2 analysis sub-agent, unchanged factory AST/instructions; isolated frozen deps; no other roles/data layer',
        'fc_parent_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'fc_source_sha256':{str(p.relative_to(ROOT)):sha(p) for p in sorted((ROOT/'src/filingcopilot').rglob('*.py'))},
        'model':settings.model, 'review_model':settings.review_model,
        'budgets':frozen_budgets(settings),
        'limitations':['Evidence subset is limited; not full financial research coverage.',
            'FilingCopilot runs tool/review/repair pipeline; comparator runs one upstream analysis sub-agent. Model-call counts differ.',
            'Measures citation traceability only after human annotation, not overall quality or investment accuracy.']}
    write(output,manifest)
    with zipfile.ZipFile(output.with_suffix('.source.zip'),'w',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((ROOT/'src/filingcopilot').rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts:
                archive.write(path, str(path.relative_to(ROOT)))
        for path in (ROOT/'scripts/compare_frozen_agents.py',ROOT/'scripts/run_finrobot_frozen.py',ROOT/'pyproject.toml'):
            archive.write(path, str(path.relative_to(ROOT)))
        for path in (source,source.parent/'instructions/analysis_agent.md',upstream/'LICENSE'):
            archive.write(path, 'upstream/'+str(path.relative_to(upstream)))
    manifest['source_archive_sha256'] = sha(output.with_suffix('.source.zip'))
    write(output, manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--home',type=Path,required=True)
    parser.add_argument('--upstream',type=Path,default=ROOT/'.filingcopilot/competitor-references/FinRobot')
    parser.add_argument('--competitor-python',type=Path,default=ROOT/'.filingcopilot/competitor-env/Scripts/python.exe')
    parser.add_argument('--freeze-only',action='store_true')
    args = parser.parse_args()
    settings = Settings.from_env()
    if not settings.llm_ready:
        raise ValueError('Configured local cheap model is required')
    args.home.mkdir(parents=True,exist_ok=True)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    manifest_path = args.output.with_suffix('.manifest.json')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else freeze(args.home,manifest_path,settings,args.upstream)
    validate_budget_models(manifest, settings)
    snapshot=manifest_path.with_suffix('.source.zip')
    if manifest.get('source_archive_sha256') and sha(snapshot) != manifest['source_archive_sha256']:
        raise ValueError('Frozen source/resource archive changed')
    with zipfile.ZipFile(snapshot) as archive:
        if any(hashlib.sha256(archive.read(path.replace('\\','/'))).hexdigest()!=expected
               for path,expected in manifest['fc_source_sha256'].items()):
            raise ValueError('Frozen application source archive changed')
    if sha(args.upstream/'finrobot_desktop/finrobot/engine/agents/factory.py') != manifest['factory_sha256']:
        raise ValueError('Resume cannot change comparator source')
    if sha(args.upstream/'finrobot_desktop/finrobot/engine/agents/instructions/analysis_agent.md') != manifest['instructions_sha256']:
        raise ValueError('Resume cannot change comparator instructions')
    with zipfile.ZipFile(snapshot) as archive:
        if archive.read('scripts/run_finrobot_frozen.py') != (ROOT/'scripts/run_finrobot_frozen.py').read_bytes():
            raise ValueError('Resume cannot change comparator adapter')
    for bundle in manifest['cases']:
        ensure_case_sources(args.home, bundle)
    if args.freeze_only:
        print(json.dumps({'frozen_cases':len(manifest['cases']),'model':manifest['model'],'calls':0}))
        return
    rows = json.loads(args.output.read_text(encoding='utf-8')) if args.output.exists() else []
    done = {(row['id'],row['arm']) for row in rows}
    for bundle in manifest['cases']:
        for arm in ('filingcopilot','finrobot_analysis'):
            if (bundle['id'],arm) in done:
                continue
            started = time.perf_counter()
            if arm == 'filingcopilot':
                env=dict(os.environ,FC_COMPARE_BASE=settings.api_base,FC_COMPARE_KEY=settings.api_key)
                payload={'snapshot':str(snapshot.resolve()),'bundle':bundle,'home':str((args.home/bundle['id']).resolve()),
                    'model':manifest['model'],'review_model':manifest['review_model'],'budgets':manifest['budgets']}
                try:
                    child=subprocess.run([str(ROOT/'.venv/Scripts/python.exe'),str(ROOT/'scripts/run_fc_frozen.py')],
                        input=json.dumps(payload),text=True,encoding='utf-8',env=env,capture_output=True,timeout=manifest['budgets']['seconds']+30)
                    result=json.loads(child.stdout)
                except Exception as exc:
                    result = {'status':'failed','answer':'','exception':type(exc).__name__,'seconds':round(time.perf_counter()-started,3)}
            else:
                # Separate process has no shared limiter: preserve the same minimum gap.
                time.sleep(settings.min_model_interval)
                env = dict(os.environ,FC_COMPARE_BASE=settings.api_base,FC_COMPARE_KEY=settings.api_key)
                payload = {'bundle':bundle,'upstream':str(args.upstream.resolve()),'model':settings.model,
                           'timeout':settings.research_timeout,'model_timeout':settings.model_timeout,
                           'max_tokens':settings.model_max_tokens,'attempts':settings.model_attempts,'interval':settings.min_model_interval}
                child = subprocess.run([str(args.competitor_python),str(ROOT/'scripts/run_finrobot_frozen.py')],
                    input=json.dumps(payload),text=True,encoding='utf-8',env=env,capture_output=True,timeout=settings.research_timeout+30)
                try:
                    result = json.loads(child.stdout)
                except ValueError:
                    result = {'status':'runner_error','answer':'','exit_code':child.returncode}
            rows.append({'id':bundle['id'],'arm':arm, **result})
            write(args.output,rows)
            print(json.dumps({'completed':len(rows),'total':40,'id':bundle['id'],'arm':arm,'status':result['status'],'seconds':result.get('seconds')}),flush=True)
    blind = []
    for row in rows:
        answer = row['answer']
        text = '\n'.join((c.get('text') or c.get('statement',''))+' ['+', '.join(c['evidence_ids'])+']' for c in answer.get('answer',[])) if isinstance(answer,dict) else answer
        blind.append({'id':hashlib.sha256((row['id']+row['arm']).encode()).hexdigest()[:16],
                      'case_id':row['id'],'text':text,'sentence_labels':[], 'human_reviewer':None})
    random.Random(20261011).shuffle(blind)
    write(args.output.with_suffix('.blind.json'),{'status':'awaiting_human_sentence_labels','items':blind,'labels':['support','unsupported','unclear'],'citation_support_rate':None})


if __name__ == '__main__':
    main()
