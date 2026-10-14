"""Blind sentence export and human-only citation-support scoring.

Automatic ID binding is reported separately and is never a support label.
Private arm mapping stays outside artifacts. Full scoring requires a complete
human worksheet and declaration; partial human labels are reported as a subset
only. No script invents reviewer identity.
"""

import argparse
import hashlib
import json
import random
import re
from collections import Counter
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def split_sentences(text):
    """Preserve citation brackets after punctuation and decimal amounts."""
    start = 0
    parts = []
    boundary = r'(?:[。！？!?]|\.(?=\s+[A-Z]|$))(?:[ \t]*\[[^\[\]]+\])*[ \t]*'
    for match in re.finditer(boundary, text):
        part = text[start:match.end()].strip()
        if part:
            parts.append(part)
        start = match.end()
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def sentences(row, *, context=False):
    answer = row['answer']
    if isinstance(answer,dict):
        output = []
        for claim in answer.get('answer', []):
            unit = claim.get('text') or claim.get('statement', '')
            for part in split_sentences(unit):
                refs = claim.get('evidence_ids', [])
                output.append((part,refs,unit,'structured_claim') if context else (part,refs))
        return output
    output = []
    for paragraph in answer.splitlines():
        if not paragraph.strip() or paragraph.strip().startswith('```'):
            continue
        if re.fullmatch(r'[\s|:\-]+',paragraph):
            continue
        # Keep factual table rows and headings; the human can mark not_claim.
        parts = split_sentences(paragraph)
        for part in parts:
            if part.strip():
                explicit = re.findall(r'\[([^\[\]]+)\]',part)
                paragraph_refs = re.findall(r'\[([^\[\]]+)\]',paragraph)
                refs = explicit or paragraph_refs
                scope = 'explicit_sentence' if explicit else 'paragraph_context' if paragraph_refs else 'uncited'
                output.append((part.strip(),refs,paragraph,scope) if context else (part.strip(),refs))
    return output


def export(rows,manifest):
    bundles = {case['id']:case for case in manifest['cases']}
    items, mapping, responses = [], {}, {}
    for row in rows:
        bundle = bundles[row['id']]
        evidence = [*bundle['facts'],*bundle['passages']]
        ids = {e['id'] for e in evidence}
        response_id = digest([row['id'],row['arm']])[:20]
        answer = row['answer']
        responses[response_id] = '\n'.join((c.get('text') or c.get('statement',''))+' ['+', '.join(c.get('evidence_ids',[]))+']' for c in answer.get('answer',[])) if isinstance(answer,dict) else answer
        for index,(text,refs,unit,scope) in enumerate(sentences(row,context=True)):
            key = digest([row['id'],row['arm'],index,text])[:20]
            mapping[key] = row['arm']
            # FinRobot may put several IDs inside one bracket, or use a footnote
            # shorthand: only literal frozen IDs count as mechanically bound.
            bound = [eid for eid in sorted(ids) if any(eid in ref for ref in refs)]
            items.append({'id':key,'question':bundle['question'],'case_id':row['id'],
                'sentence':text,'response_id':response_id,'original_unit':unit,'citation_scope':scope,
                'cited_ids':bound,'unresolved_references':[ref for ref in refs if not any(eid in ref for eid in ids)],
                'evidence':evidence,'filing':bundle['filing'],'label':None,'note':''})
    random.Random(20261011).shuffle(items)
    worksheet = {'schema':'filingcopilot.paired-citation-blind/v1',
        'human_reviewer':None,'human_origin_attestation':False,'items':items,'responses':responses,
        'labels':['support','unsupported','unclear','not_claim'],
        'instructions':'Judge every material part of each sentence against cited evidence and period/currency/share basis. Structured-claim or paragraph-context references are candidates only: verify their scope using original_unit and the full response (including footnotes). Factual claims with no valid associated source are unsupported. Use not_claim only for headings/questions/nonfactual scope notes. Never infer support from an ID match.'}
    return worksheet, {'item_digest':digest(items),'arms':mapping,
        'responses_sha256':digest(responses),
        'frozen':{item['id']:digest({k:v for k,v in item.items() if k not in ('label','note')}) for item in items}}


def score(worksheet,key):
    items = worksheet['items']
    reviewed = bool(str(worksheet.get('human_reviewer') or '').strip()) and worksheet.get('human_origin_attestation') is True
    ids = [item.get('id') for item in items]
    integrity = digest(worksheet.get('responses',{}))==key.get('responses_sha256') and len(ids)==len(set(ids)) and set(ids)==set(key['arms']) and all(
        digest({k:v for k,v in item.items() if k not in ('label','note')})==key['frozen'].get(item['id']) for item in items)
    permitted = {'support','unsupported','unclear','not_claim'}
    integrity = integrity and all(item.get('label') is None or item.get('label') in permitted for item in items)
    accepted = reviewed and integrity
    labelled = sum(item.get('label') in permitted for item in items) if accepted else 0
    complete = accepted and labelled == len(items)
    summaries = {}
    for arm in sorted(set(key['arms'].values())):
        selected = [item for item in items if key['arms'].get(item['id'])==arm]
        submitted = Counter(item['label'] for item in selected if item.get('label'))
        counts = submitted if accepted else Counter()
        decidable = counts['support']+counts['unsupported']
        annotated = sum(counts.values())
        summaries[arm] = {'sentences':len(selected),'automatic_literal_id_binding':sum(bool(item['cited_ids']) for item in selected),
            'automatic_reference_scope_counts':dict(Counter(item.get('citation_scope','legacy') for item in selected)),
            'submitted_labels':dict(submitted),'human_labels':dict(counts),'citation_support_rate':counts['support']/decidable if complete and decidable else None,
            'unclear':counts['unclear'],'decidable_claims':decidable,
            'support_over_all_claims':counts['support']/(decidable+counts['unclear']) if complete and decidable+counts['unclear'] else None,
            'reviewed_subset': {'annotated_units':annotated,'pending_units':len(selected)-annotated,
                'annotation_coverage':annotated/len(selected) if selected and accepted else None,
                'citation_support_rate':counts['support']/decidable if accepted and decidable else None,
                'support_over_annotated_claims':counts['support']/(decidable+counts['unclear']) if accepted and decidable+counts['unclear'] else None,
                'scope':'Human-labelled subset only; selection may be biased. Unlabelled units are not supported or failed claims. Not full-sample acceptance.'}}
    return {'status':'human_scored' if complete else 'partially_human_scored' if labelled else 'awaiting_human_sentence_labels' if integrity else 'invalid_worksheet',
        'human_reviewer':worksheet.get('human_reviewer') if reviewed else None,
        'integrity':integrity,'arms':summaries,
        'scope':'Human citation support for two bounded configurations only. Automatic binding counts candidate IDs in associated sentence/claim/paragraph units, not semantic support. Not overall product superiority or investment accuracy.'}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--results',type=Path,required=True)
    p.add_argument('--worksheet',type=Path,required=True)
    p.add_argument('--key',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--score',action='store_true')
    args = p.parse_args()
    if args.score:
        worksheet = json.loads(args.worksheet.read_text(encoding='utf-8'))
        key = json.loads(args.key.read_text(encoding='utf-8'))
    else:
        if args.worksheet.exists() or args.key.exists():
            raise ValueError('Do not overwrite a potentially human-labelled worksheet or its key')
        rows = json.loads(args.results.read_text(encoding='utf-8'))
        manifest = json.loads(args.results.with_suffix('.manifest.json').read_text(encoding='utf-8'))
        if len(rows)!=40 or len({(r['id'],r['arm']) for r in rows})!=40:
            raise ValueError('Export requires all 20 paired runs, including failures')
        worksheet,key = export(rows,manifest)
        args.key.parent.mkdir(parents=True,exist_ok=True)
        args.worksheet.write_text(json.dumps(worksheet,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        args.key.write_text(json.dumps(key,indent=2)+'\n',encoding='utf-8')
    args.output.write_text(json.dumps(score(worksheet,key),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__ == '__main__':
    main()
