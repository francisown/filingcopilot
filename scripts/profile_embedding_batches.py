"""Controlled existing-weight probe: change one indexing variable at a time."""
import argparse
import hashlib
import json
import time
from datetime import date
from pathlib import Path

import numpy as np
from fastembed import TextEmbedding

from filingcopilot.corpus import Corpus


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--weights', type=Path, default=Path('.filingcopilot/models/bge-local'))
    parser.add_argument('--ticker', default='PDD')
    parser.add_argument('--sample', type=int, default=128)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    ps = Corpus(args.corpus, readonly=True).passages(date(2026, 10, 11), args.ticker)
    selection = np.linspace(0, len(ps)-1, min(args.sample, len(ps)), dtype=int)
    sample = [ps[int(i)] for i in selection]
    rows = []
    baseline = None
    for threads, bucket in [(2, False), (2, True), (4, True), (8, True)]:
        model = TextEmbedding('BAAI/bge-small-en-v1.5', specific_model_path=str(args.weights),
                              cache_dir=str(args.weights.parent), threads=threads)
        order = sorted(range(len(sample)), key=lambda i: len(sample[i].text)) if bucket else list(range(len(sample)))
        output = [None]*len(sample)
        stamp = time.perf_counter()
        for start in range(0, len(order), 16):
            indices = order[start:start+16]
            values = list(model.embed([sample[i].text for i in indices], batch_size=16))
            for i, vector in zip(indices, values, strict=True):
                output[i] = vector
        vectors = np.asarray(output)
        elapsed = time.perf_counter()-stamp
        if baseline is None:
            baseline = vectors.copy()
        cosine = np.sum(vectors*baseline, axis=1) / (np.linalg.norm(vectors, axis=1)*np.linalg.norm(baseline, axis=1))
        row = {'threads': threads, 'length_bucketed': bucket, 'seconds': round(elapsed, 4),
               'min_cosine_to_baseline': float(cosine.min()),
               'max_absolute_delta': float(np.max(np.abs(vectors-baseline)))}
        rows.append(row)
        print(json.dumps(row), flush=True)
    report = {'ticker': args.ticker, 'passages': len(ps), 'sample_passages': len(sample),
              'sample_sha256': hashlib.sha256(json.dumps([p.model_dump(mode='json') for p in sample], sort_keys=True).encode()).hexdigest(),
              'model_sha256': hashlib.sha256((args.weights/'model_optimized.onnx').read_bytes()).hexdigest(),
              'rows': rows, 'scope': 'Existing identical model weights, fresh model instance per configuration; not whole-corpus or acquisition timing.'}
    args.output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
