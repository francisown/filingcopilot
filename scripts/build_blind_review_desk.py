"""Generate an offline human review page from a public, unlabeled worksheet."""

import argparse
import base64
import hashlib
import json
from pathlib import Path


def build(sample: Path, output: Path):
    raw = sample.read_bytes()
    rows = json.loads(raw)
    if not isinstance(rows, list) or not rows:
        raise ValueError('Nonempty public worksheet required')
    if any(row.get('label') is not None for row in rows):
        raise ValueError('Blind initial worksheet must not contain labels')
    # Whitelist public fields; never read the separate private decision key.
    fields = {'blind_id', 'question', 'kind', 'answer', 'text', 'evidence_ids', 'evidence', 'label'}
    rows = [{k: v for k, v in row.items() if k in fields} for row in rows]
    ids = [row['blind_id'] for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate blind IDs')
    root = Path(__file__).resolve().parents[1]
    script = (root/'src/filingcopilot/static/blind-review.js').read_text(encoding='utf-8')
    script_hash = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    template = (root/'scripts/blind_review_template.html').read_text(encoding='utf-8')
    payload = json.dumps({'sample_sha256': hashlib.sha256(raw).hexdigest(), 'rows': rows}, ensure_ascii=False).replace('<', '\\u003c')
    html = template.replace('__SCRIPT_HASH__', script_hash).replace('__SCRIPT__', script).replace('__SAMPLE__', payload)
    output.write_text(html, encoding='utf-8')
    return {'rows': len(rows), 'labels_filled': 0, 'sample_sha256': hashlib.sha256(raw).hexdigest(),
            'output': str(output), 'network': 'none; original links opened only by human click'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('sample', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.sample, args.output), ensure_ascii=False))


if __name__ == '__main__':
    main()
