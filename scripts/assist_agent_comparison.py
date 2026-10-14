"""Mechanical review hints and strict import of a human's citation worksheet.

No model call, inferred support label or reviewer identity. Keep the frozen
worksheet/key untouched; hints are a separate, non-scoring sidecar.
"""

import argparse
import csv
import hashlib
import json
import re
import sys
from copy import deepcopy
from pathlib import Path

# Direct CLI execution sets sys.path[0] to scripts/, whereas tests import this
# module from the repo root. Use the same adjacent scoring implementation.
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.export_agent_comparison import digest

NOTICE = 'Mechanical token hints only; not labels, never used for scoring.'
LABELS = {'support', 'unsupported', 'unclear', 'not_claim'}
FIELDS = ['id', 'response_id', 'question', 'sentence', 'filing_url', 'filing_period_end',
          'contains_number', 'number_tokens', 'contains_period', 'period_tokens',
          'contains_currency', 'currency_tokens', 'currency_symbols', 'share_tokens',
          'unit_tokens', 'cited_ids', 'unresolved_reference_count', 'source_digest',
          'assistance_scope', 'label', 'note']
SOURCE_FIELDS = ['id','response_id','question','sentence','filing_url','filing_period_end',
                 'cited_ids','unresolved_reference_count','source_digest']


def tokens(pattern, text):
    return list(dict.fromkeys(match.group(0) for match in re.finditer(pattern, text, re.I)))


def mechanical_hints(sentence):
    # Hide bracketed reference text only for token scans. Keep original source
    # and statement untouched. Numeric characters inside evidence IDs are not
    # financial numbers; unresolved footnotes remain available in the worksheet.
    text = re.sub(r'\[[^\[\]]*\]', ' ', sentence)
    numbers = tokens(r'(?<![A-Za-z0-9_.])[+-]?\d[\d,]*(?:\.\d+)?(?![A-Za-z0-9_.])', text)
    periods = tokens(r'(?<![A-Za-z0-9_])(?:FY\s*)?(?:19|20)\d{2}(?:[-/]\d{1,2}(?:[-/]\d{1,2})?)?(?![A-Za-z0-9_])|'
                     r'(?:19|20)\d{2}年(?:\d{1,2}月(?:\d{1,2}日)?)?|'
                     r'\bQ[1-4]\b|\b(?:fiscal year|quarter|year ended|as of)\b|'
                     r'财年|季度|年度|上年|本年|截至', text)
    currencies = tokens(r'(?<![A-Za-z0-9_])(?:USD|CNY|RMB|TWD|HKD|EUR|GBP|JPY|CHF|CAD|AUD|KRW)(?![A-Za-z0-9_])|'
                        r'美元|人民币|新台币|港币|港元|欧元|英镑|日元|加元|澳元|韩元', text)
    symbols = tokens(r'HK\$|US\$|NT\$|\$|￥|¥|€|£', text)
    shares = tokens(r'(?<![A-Za-z0-9_])(?:ADS|ADRs?|ordinary shares?|common shares?|per share)(?![A-Za-z0-9_])|'
                    r'普通股|存托股份?|存托凭证|每股', text)
    units = tokens(r'%|％|百分点|亿元|亿美元|万元|百万|十亿|\b(?:million|billion|trillion)\b|倍', text)
    return {'contains_number':bool(numbers),'number_tokens':numbers,
            'contains_period':bool(periods),'period_tokens':periods,
            'contains_currency':bool(currencies or symbols),'currency_tokens':currencies,
            'currency_symbols':symbols,'share_tokens':shares,'unit_tokens':units,
            'scope':NOTICE}


def immutable_item(item):
    return {k:v for k,v in item.items() if k not in ('label','note')}


def csv_cell(value):
    text = str(value)
    # Export untrusted prose safely to spreadsheets. Large all-numeric IDs
    # also need text treatment to avoid Excel truncating their precision.
    if text.lstrip().startswith(('=','+','-','@')) or (text.isdigit() and len(text)>15):
        return "'"+text
    return text


def assistance_rows(worksheet, *, safe=True):
    output = []
    for item in worksheet['items']:
        hints = mechanical_hints(item['sentence'])
        row = {'id':item['id'],'response_id':item['response_id'],
               'question':item['question'],'sentence':item['sentence'],
               'filing_url':item['filing'].get('source_url',''),
               'filing_period_end':item['filing'].get('period_end',''),
               **{k:('; '.join(v) if isinstance(v,list) else str(v).lower())
                  for k,v in hints.items() if k!='scope'},
               'cited_ids':'; '.join(item['cited_ids']),
               'unresolved_reference_count':len(item['unresolved_references']),
               'source_digest':digest(immutable_item(item)),
               'assistance_scope':NOTICE,'label':'','note':''}
        output.append({key:csv_cell(row[key]) if safe else str(row[key]) for key in FIELDS})
    return output


def import_human_csv(worksheet, csv_path, reviewer, human_attestation):
    if not reviewer.strip() or not human_attestation:
        raise ValueError('A real human reviewer and explicit human-origin attestation are required')
    if any(item.get('label') for item in worksheet['items']) and worksheet.get('human_reviewer') != reviewer.strip():
        raise ValueError('Do not attribute an existing reviewer\'s labels to someone else')
    expected = {row['id']:row for row in assistance_rows(worksheet,safe=False)}
    escaped_ids = {csv_cell(item_id):item_id for item_id in expected}
    actual_ids, labels = set(), {}
    with csv_path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if not set([*SOURCE_FIELDS,'label','note']).issubset(reader.fieldnames or []):
            raise ValueError('CSV is missing frozen source and annotation columns')
        for row in reader:
            item_id = escaped_ids.get(row['id'],row['id'])
            if item_id not in expected or item_id in actual_ids:
                raise ValueError('CSV has an unknown or duplicate item ID')
            actual_ids.add(item_id)
            for field in SOURCE_FIELDS:
                value = expected[item_id][field]
                # Excel may remove its literal-text apostrophe on CSV export.
                # Both representations must equal the same immutable source.
                if row[field] not in {value,csv_cell(value)}:
                    raise ValueError('CSV changed a source statement or context: '+field)
            # Helper columns can be formatted/removed; they never affect whether
            # a human label is accepted, nor its score or source integrity.
            label = row['label'].strip()
            if label and label not in LABELS:
                raise ValueError('Unknown human label')
            if label:
                labels[item_id] = (label,row['note'])
    if not labels:
        raise ValueError('No human labels supplied; a blank CSV cannot create reviewer provenance')
    result = deepcopy(worksheet)
    for item in result['items']:
        key = item['id']
        if key in labels:
            item['label'],item['note'] = labels[key]
    result['human_reviewer'] = reviewer.strip()
    result['human_origin_attestation'] = True
    return result


def write_json(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--worksheet',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--csv',type=Path)
    p.add_argument('--import-csv',type=Path)
    p.add_argument('--reviewer',default='')
    p.add_argument('--human-attestation',action='store_true')
    args = p.parse_args()
    if args.csv and args.output.resolve() == args.csv.resolve():
        raise ValueError('JSON and CSV outputs must be distinct files')
    if args.output.exists() or (args.csv and args.csv.exists()):
        raise ValueError('Refuse to overwrite an existing worksheet or review artifact')
    worksheet = json.loads(args.worksheet.read_text(encoding='utf-8-sig'))
    if args.import_csv:
        if args.csv:
            raise ValueError('Import and export are separate actions')
        write_json(args.output,import_human_csv(worksheet,args.import_csv,args.reviewer,args.human_attestation))
        return
    rows = assistance_rows(worksheet)
    report = {'schema':'filingcopilot.citation-review-assistance/v1','scope':NOTICE,
              'worksheet':str(args.worksheet),'worksheet_sha256':hashlib.sha256(args.worksheet.read_bytes()).hexdigest(),
              'immutable_content_sha256':digest({'items':[immutable_item(i) for i in worksheet['items']],
                                                'responses':worksheet['responses']}),
              'units':len(rows),'items':[{'id':item['id'],'assistance':mechanical_hints(item['sentence'])}
                                       for item in worksheet['items']],
              'limitations':['Literal token flags can miss words or include years/row numbers; they are not facts or support checks.',
                             'Currency symbols do not determine a currency; hints do not use or change labels.',
                             'Human-selected partial samples can be biased; only scored as annotated subsets.']}
    write_json(args.output,report)
    if args.csv:
        with args.csv.open('w',encoding='utf-8-sig',newline='') as stream:
            writer = csv.DictWriter(stream,fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
    print(json.dumps({'units':len(rows),'human_labels_generated':0,'scope':NOTICE}))


if __name__ == '__main__':
    main()
