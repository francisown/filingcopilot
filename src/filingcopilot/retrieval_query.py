"""Deterministic retrieval text, independent of evaluation targets."""

import re

from .domain import Domain

_PROMPT_WORDS = frozenset(
    "a an the and or of in on for to from with about where which what does did "
    "is are was were it its say says discuss discusses reported report review "
    "find locate evidence filing filings annual filed please show me explain "
    "according disclosure disclosures how s".split()
)
_ZH_PROMPT = re.compile(
    r"请给出|请从|请|我想查|核对|年报里|年报|申报中|申报|哪些段落说明|"
    r"找出|原文依据|原文证据|原文|是怎样披露的|关于|的表述|表述|披露|怎样|哪些|说明"
)


def retrieval_text(expanded: str, domain: Domain, ticker: str | None, fiscal_year: int | None) -> str:
    """Remove boilerplate and metadata only when the metadata is already scoped.

    No expected passage, section, or gold-set term enters this function. Keep
    unscoped names/years, financial qualifiers and negations in the query.
    """
    value = expanded
    if ticker and ticker in domain.companies:
        company = domain.companies[ticker]
        for name in sorted([ticker, company.name, *company.aliases], key=len, reverse=True):
            if name.isascii():
                value = re.sub(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])", " ", value, flags=re.I)
            else:
                value = value.replace(name, " ")
    if fiscal_year is not None:
        value = re.sub(r"\bFY\s*" + str(fiscal_year) + r"\b|\b" + str(fiscal_year) + r"\b", " ", value, flags=re.I)
    value = _ZH_PROMPT.sub(" ", value)
    terms = re.findall(r"[A-Za-z0-9]+|[\u3400-\u9fff]+", value)
    kept = [term for term in terms if term.casefold() not in _PROMPT_WORDS]
    return " ".join(dict.fromkeys(kept)) or expanded


def lexical_terms(query: str) -> list[str]:
    """Keep Chinese literals as well as Latin finance terms, with bounded size."""
    return list(dict.fromkeys(re.findall(r"[A-Za-z0-9]{2,}|[\u3400-\u9fff]{2,}", query)))[:40]
