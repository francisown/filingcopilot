"""Measure parsing stages on immutable cached originals; no network or indexing."""

import argparse
import hashlib
import json
import shutil
import time
import uuid
from dataclasses import replace
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup

from filingcopilot.corpus import Corpus
from filingcopilot.domain import DEFAULT_DOMAIN
from filingcopilot.ifrs_sources import _extract_ifrs_facts
from filingcopilot.ingestion import _extract_passages
from filingcopilot.models import Filing
from filingcopilot.retrieval import Retriever
from filingcopilot.settings import Settings
from filingcopilot.statements import extract_inline_statement_facts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, default=Path('.filingcopilot'))
    parser.add_argument('--ticker', default='PDD')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--index', action='store_true', help='Fresh derived index; existing model weights only')
    parser.add_argument('--keep-isolated', action='store_true', help='Keep the temporary derived index for inspection')
    args = parser.parse_args()
    filing = Filing.model_validate(Corpus(args.home).catalog(date(2026, 10, 11), args.ticker)[0])
    path = args.home / 'sec-cache' / (hashlib.sha256(filing.source_url.encode()).hexdigest()+'.cache')
    html = path.read_bytes()
    if hashlib.sha256(html).hexdigest() != filing.content_sha256:
        raise ValueError('Cached original differs from filing hash')
    rows = []

    def timed(stage, callback):
        stamp = time.perf_counter()
        value = callback()
        rows.append({'stage': stage, 'seconds': round(time.perf_counter()-stamp, 3),
                     'count': len(value) if isinstance(value, list) else None})
        print(json.dumps(rows[-1]), flush=True)
        return value

    tree = timed('html_tree', lambda: BeautifulSoup(html, 'html.parser'))
    timed('native_facts', lambda: _extract_ifrs_facts(tree, filing, DEFAULT_DOMAIN))
    timed('passages', lambda: _extract_passages(tree, filing, include_tables=True))
    timed('statement_facts_including_second_parse', lambda: extract_inline_statement_facts(html, filing))
    if args.index:
        corpus = Corpus(args.home)
        retriever = Retriever(corpus, Settings(home=args.home))
        timed('model_setup_existing_weights', retriever._embedder)
        if retriever.model is None:
            raise RuntimeError('Local model unavailable; no cold-index timing claimed')
        isolated = args.home / 'cold-index-diagnostics' / uuid.uuid4().hex
        isolated.mkdir(parents=True)
        retriever.settings = replace(retriever.settings, home=isolated)
        timed('fresh_index_all_selected_annual_passages',
              lambda: retriever._vectors(corpus.passages(date(2026, 10, 11), args.ticker)))
    args.output.write_text(json.dumps({'ticker': args.ticker, 'filing': filing.model_dump(mode='json'),
                                     'bytes': len(html), 'stages': rows,
                                     'scope': 'fresh Python process, cached raw source; no HTTP/embeddings; shared host',
                                     'isolated_retained': args.keep_isolated},
                                    ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    if args.index and not args.keep_isolated:
        shutil.rmtree(isolated, ignore_errors=True)


if __name__ == '__main__':
    main()
