"""Offline credit-risk retrieval and evidence-package demo.

This script reuses FilingCopilot's SQLite FTS5 corpus and passage model, but
swaps the SEC filing corpus for synthetic credit-policy, model-validation and
portfolio documents. It does not call a language model. The point is to show
the evidence layer that sits around an agent:

  ingest -> retrieve cited passages -> freeze an evidence package -> verify offline

Run ``python examples/credit-risk/demo.py demo`` for the full path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from filingcopilot.corpus import Corpus, stable_id  # noqa: E402
from filingcopilot.models import Filing, Passage  # noqa: E402
from filingcopilot.retrieval import rewrite_query  # noqa: E402
from filingcopilot.retrieval_query import lexical_terms  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_HOME = HERE / ".data"
DEFAULT_CORPUS = HERE / "corpus"
DEFAULT_QUESTIONS = HERE / "questions.json"
DEMO_TICKER = "CREDIT"
AS_OF = date(2026, 10, 1)
FILED = date(2026, 7, 1)
PERIOD_END = date(2026, 6, 30)
CREDIT_ALIASES = {
    "dti": "debt to income",
    "ltv": "loan to value",
    "dpd": "days past due delinquency",
    "pd": "probability of default",
    "psi": "population stability index",
    "revalidated": "revalidation validation annually",
    "recalibrated": "recalibration recalibrate",
    "overlay": "overlay interim action",
}
STOP_WORDS = frozenset(
    "a an the and or of in on for to from with about where which what who whom "
    "does do did is are was were it its say says discuss discusses reported report "
    "review find locate evidence filing filings annual filed please show me explain "
    "according disclosure disclosures how when why must should can could would will "
    "may might not no than then there their they we you i as if into over under above "
    "below more most least at least this that these those has have had".split()
)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _document_name(passage: Passage) -> str:
    return passage.source_url.removeprefix("credit-risk://")


def _form_for(name: str) -> str:
    lowered = name.casefold()
    if "policy" in lowered:
        return "POLICY"
    if "validation" in lowered:
        return "VALIDATION"
    return "REPORT"


def split_document(path: Path) -> tuple[Filing, list[Passage]]:
    """Split a Markdown document into heading-scoped passages with line locators."""
    text = path.read_text(encoding="utf-8")
    filing = Filing(
        id=f"CREDIT:{path.name}",
        ticker=DEMO_TICKER,
        company="Synthetic Retail Credit Portfolio",
        cik="0000000000",
        accession=f"CREDIT-{path.stem.upper()}",
        form=_form_for(path.name),
        filed=FILED,
        period_end=PERIOD_END,
        source_url=f"credit-risk://{path.name}",
        content_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    passages: list[Passage] = []
    section = "Document"
    block: list[str] = []
    start = 0

    def flush(end_line: int) -> None:
        nonlocal block, start
        if not block:
            return
        body = re.sub(r"\s+", " ", " ".join(line.strip() for line in block)).strip()
        if len(body) >= 40:
            passages.append(
                Passage(
                    id=stable_id("passage", path.name, section, start, body[:80]),
                    filing_id=filing.id,
                    ticker=DEMO_TICKER,
                    section=section,
                    text=body,
                    source_url=f"credit-risk://{path.name}",
                    locator=f"L{start}-{end_line}",
                )
            )
        block = []
        start = 0

    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith("#"):
            flush(number - 1)
            section = line.lstrip("#").strip() or "Document"
            continue
        if not line.strip():
            flush(number - 1)
            continue
        if start == 0:
            start = number
        block.append(line)
    flush(len(text.splitlines()))
    return filing, passages


def ingest_corpus(
    corpus_dir: Path = DEFAULT_CORPUS,
    home: Path = DEFAULT_HOME,
    *,
    reset: bool = False,
) -> dict:
    """Ingest every Markdown document except the corpus README into a fresh corpus."""
    if reset and home.exists():
        shutil.rmtree(home)
    corpus = Corpus(home)
    documents = 0
    passage_count = 0
    for path in sorted(corpus_dir.glob("*.md")):
        if path.name.casefold() == "readme.md":
            continue
        filing, passages = split_document(path)
        corpus.ingest(filing, [], passages)
        documents += 1
        passage_count += len(passages)
    return {"home": str(home), "documents": documents, "passages": passage_count}


def _terms(question: str) -> list[str]:
    extra = [
        value
        for key, value in CREDIT_ALIASES.items()
        if re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", question.casefold())
    ]
    expanded = rewrite_query(" ".join([question, *extra]))["expanded"]
    return lexical_terms(expanded)


def _search_text(value: str) -> str:
    """Normalize hyphens and punctuation so phrase matching is stable."""
    value = value.casefold().replace("-", " ").replace("_", " ").replace("/", " ")
    value = re.sub(r"[^a-z0-9%.\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _variants(term: str) -> set[str]:
    forms = {term.casefold()}
    for suffix in ("ations", "ation", "ions", "ion", "ing", "ed", "es", "s"):
        if term.casefold().endswith(suffix) and len(term) - len(suffix) >= 4:
            forms.add(term.casefold()[: -len(suffix)])
    return forms


def _matches(term: str, text: str) -> bool:
    return any(form in text for form in _variants(term))


def _ngrams(question: str) -> set[str]:
    words = [
        word
        for word in _search_text(question).split()
        if word not in STOP_WORDS and len(word) > 1
    ]
    grams: set[str] = set()
    for size in (2, 3):
        for index in range(len(words) - size + 1):
            grams.add(" ".join(words[index : index + size]))
    return grams


def search(question: str, home: Path = DEFAULT_HOME, *, k: int = 3) -> tuple[list[tuple[Passage, float]], list[str]]:
    """Two-stage retrieval: FTS5 recall, then IDF-weighted coverage and phrase rerank."""
    if not (home / "corpus.sqlite3").is_file():
        raise FileNotFoundError(f"No corpus at {home}; run the ingest command first")
    terms = _terms(question)
    corpus = Corpus(home, readonly=True)
    all_passages = corpus.passages(AS_OF, DEMO_TICKER)
    fts = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
    candidates = corpus.lexical(fts, AS_OF, DEMO_TICKER, 100) if fts else []
    pool = {passage.id: passage for passage, _ in candidates} or {
        passage.id: passage for passage in all_passages
    }
    if not terms:
        return [], terms
    document_frequency = {
        term: sum(1 for passage in all_passages if _matches(term, _search_text(passage.text)))
        for term in terms
    }
    idf = {
        term: math.log((len(all_passages) + 1) / (document_frequency[term] + 1)) + 1
        for term in terms
    }
    grams = _ngrams(question)
    ranked: list[tuple[Passage, float]] = []
    for passage in pool.values():
        text = _search_text(passage.text)
        coverage = sum(idf[term] for term in terms if _matches(term, text))
        if coverage <= 0:
            continue
        phrase_hits = sum(1 for gram in grams if gram in text)
        ranked.append((passage, coverage + 1.5 * min(phrase_hits, 3)))
    ranked.sort(key=lambda pair: (-pair[1], pair[0].id))
    return ranked[:k], terms


def _quote_from(text: str, terms: list[str]) -> str:
    """Return a verbatim, contiguous span so the quote can be re-checked offline."""
    if "|" in text:
        return text[:600]
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    if not sentences:
        return text[:600]
    scored = [
        (sum(term.casefold() in sentence.casefold() for term in terms), index)
        for index, sentence in enumerate(sentences)
    ]
    _, best = max(scored, key=lambda item: (item[0], -item[1]))
    start = max(0, best - 1) if best > 0 and scored[best - 1][0] > 0 else best
    end = min(len(sentences), start + 2)
    return " ".join(sentences[start:end])


def answer_question(
    question: str,
    home: Path = DEFAULT_HOME,
    corpus_dir: Path = DEFAULT_CORPUS,
    *,
    k: int = 3,
) -> dict:
    """Return an extractive, citation-bound answer. No model, no free-form advice."""
    hits, terms = search(question, home, k=k)
    citations = []
    for passage, _score in hits[:2]:
        document = _document_name(passage)
        source = corpus_dir / document
        citations.append(
            {
                "passage_id": passage.id,
                "document": document,
                "section": passage.section,
                "locator": passage.locator,
                "quote": _quote_from(passage.text, terms),
                "document_sha256": _sha256_file(source) if source.is_file() else "",
            }
        )
    if not citations:
        return {
            "question": question,
            "as_of": AS_OF.isoformat(),
            "status": "abstained",
            "answer": "No supported evidence found in the ingested corpus.",
            "citations": [],
            "retrieval": {"backend": "sqlite-fts5", "terms": terms, "candidates": k},
        }
    lines = ["Evidence found in the ingested credit-risk corpus:"]
    for citation in citations:
        lines.append(f"- {citation['document']} / {citation['section']} ({citation['locator']}): {citation['quote']}")
    return {
        "question": question,
        "as_of": AS_OF.isoformat(),
        "status": "answered",
        "answer": "\n".join(lines),
        "citations": citations,
        "retrieval": {"backend": "sqlite-fts5", "terms": terms, "candidates": k},
    }


def canonical_hash(payload: dict) -> str:
    body = {key: value for key, value in payload.items() if key != "package_sha256"}
    encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def build_package(
    question: str,
    home: Path = DEFAULT_HOME,
    corpus_dir: Path = DEFAULT_CORPUS,
    output: Path | None = None,
) -> dict:
    result = answer_question(question, home, corpus_dir)
    package = {
        "schema": "credit-risk-evidence/v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        **result,
    }
    package["package_sha256"] = canonical_hash(package)
    target = output or HERE / "packages" / f"{stable_id('package', question)}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    package["package_path"] = str(target)
    return package


def verify_package(path: Path, corpus_dir: Path = DEFAULT_CORPUS) -> dict:
    """Recompute the package hash and check every quote against its source file."""
    package = json.loads(path.read_text(encoding="utf-8"))
    errors: list[str] = []
    checks: list[str] = []
    if package.get("package_sha256") != canonical_hash(package):
        errors.append("package_sha256 mismatch")
    else:
        checks.append("package_sha256")
    for citation in package.get("citations", []):
        document = citation.get("document", "")
        source = corpus_dir / document
        if not source.is_file():
            errors.append(f"missing source document: {document}")
            continue
        if _sha256_file(source) != citation.get("document_sha256"):
            errors.append(f"document hash mismatch: {document}")
            continue
        checks.append(f"document hash: {document}")
        if _normalize(citation.get("quote", "")) not in _normalize(source.read_text(encoding="utf-8")):
            errors.append(f"quote not found in source: {document}")
            continue
        checks.append(f"quote: {document} {citation.get('locator', '')}")
    return {
        "status": "consistent" if not errors else "invalid",
        "checks": checks,
        "errors": errors,
    }


def run_eval(home: Path = DEFAULT_HOME, questions_path: Path = DEFAULT_QUESTIONS) -> dict:
    questions = json.loads(questions_path.read_text(encoding="utf-8"))
    rows = []
    for item in questions:
        hits, _ = search(item["question"], home, k=3)
        documents = [_document_name(passage) for passage, _ in hits]
        rank = next((index + 1 for index, name in enumerate(documents) if name == item["expected_document"]), None)
        section_hit = any(
            item["expected_section"].casefold() in passage.section.casefold()
            for passage, _ in hits
        )
        term_hit = any(
            any(term.casefold() in passage.text.casefold() for term in item.get("expected_terms", []))
            for passage, _ in hits
        )
        rows.append(
            {
                "id": item["id"],
                "document_hit": rank is not None,
                "section_hit": section_hit,
                "term_hit": term_hit,
                "rank": rank,
                "top_document": documents[0] if documents else None,
            }
        )
    count = len(rows) or 1
    summary = {
        "questions": len(rows),
        "document_hit_at_3": sum(row["document_hit"] for row in rows) / count,
        "section_hit_at_3": sum(row["section_hit"] for row in rows) / count,
        "term_hit_at_3": sum(row["term_hit"] for row in rows) / count,
        "mrr": sum(1 / row["rank"] for row in rows if row["rank"]) / count,
        "rows": rows,
    }
    print(f"{'id':14} {'doc':5} {'sect':5} {'term':5} rank  top document")
    for row in rows:
        print(
            f"{row['id']:14} "
            f"{'yes' if row['document_hit'] else 'no':5} "
            f"{'yes' if row['section_hit'] else 'no':5} "
            f"{'yes' if row['term_hit'] else 'no':5} "
            f"{row['rank'] or '-':>4}  {row['top_document']}"
        )
    print(
        f"\ndocument hit@3 {summary['document_hit_at_3']:.0%} | "
        f"section hit@3 {summary['section_hit_at_3']:.0%} | "
        f"term hit@3 {summary['term_hit_at_3']:.0%} | MRR {summary['mrr']:.3f}"
    )
    return summary


def _print_answer(result: dict) -> None:
    print(f"\nQ: {result['question']}")
    print(f"status: {result['status']}")
    for citation in result.get("citations", []):
        print(f"  - {citation['document']} / {citation['section']} ({citation['locator']})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline credit-risk retrieval and evidence demo")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest_parser = sub.add_parser("ingest", help="Ingest the synthetic corpus")
    ingest_parser.add_argument("--home", type=Path, default=DEFAULT_HOME)
    ingest_parser.add_argument("--reset", action="store_true")

    ask_parser = sub.add_parser("ask", help="Ask a question and print cited evidence")
    ask_parser.add_argument("question", nargs="+")
    ask_parser.add_argument("--home", type=Path, default=DEFAULT_HOME)
    ask_parser.add_argument("--package-out", type=Path)

    verify_parser = sub.add_parser("verify", help="Verify an exported evidence package offline")
    verify_parser.add_argument("package", type=Path)
    verify_parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)

    eval_parser = sub.add_parser("eval", help="Run the frozen question set")
    eval_parser.add_argument("--home", type=Path, default=DEFAULT_HOME)
    eval_parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)

    demo_parser = sub.add_parser("demo", help="Run ingest, ask, package, verify and eval")
    demo_parser.add_argument("--home", type=Path, default=DEFAULT_HOME)

    args = parser.parse_args(argv)

    if args.command == "ingest":
        print(json.dumps(ingest_corpus(home=args.home, reset=args.reset), indent=2))
        return 0

    if args.command == "ask":
        question = " ".join(args.question)
        package = build_package(question, args.home, output=args.package_out)
        print(package["answer"])
        _print_answer(package)
        print(f"package: {package['package_path']}")
        print(f"package_sha256: {package['package_sha256']}")
        return 0 if package["status"] == "answered" else 1

    if args.command == "verify":
        report = verify_package(args.package, args.corpus)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "consistent" else 1

    if args.command == "eval":
        run_eval(args.home, args.questions)
        return 0

    if args.command == "demo":
        print(json.dumps(ingest_corpus(home=args.home, reset=True), indent=2))
        for question in (
            "What is the maximum DTI for a prime unsecured loan?",
            "What did the 2026 validation find about near-prime calibration?",
            "Which segment has the highest 30+ DPD rate?",
        ):
            result = answer_question(question, args.home)
            _print_answer(result)
        package = build_package("Who must approve an exception above 10% over policy?", args.home)
        print(f"\npackage: {package['package_path']}")
        print(json.dumps(verify_package(Path(package["package_path"])), indent=2))
        run_eval(args.home)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
