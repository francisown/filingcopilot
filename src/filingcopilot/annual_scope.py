"""One annual comparison scope shared by retrieval and claim validation."""

import re


def explicit_years(question: str) -> set[int]:
    return {int(y) for y in re.findall(r"(?<!\d)(20\d{2})(?!\d)", question)}


def wants_growth(question: str) -> bool:
    return bool(re.search(r"同比|增长|\bgrowth\b|year[- ]over[- ]year|\byoy\b", question, re.I))


def compares_prior_year(question: str) -> bool:
    # Multiple literal years already define the comparison. Do not silently add
    # a third year to a comparison of two named years unless growth is requested.
    return wants_growth(question) or bool(re.search(
        r"上一(?:个)?(?:财)?年|上年|去年|\b(?:prior|previous|preceding) (?:fiscal )?year\b",
        question, re.I,
    )) or (len(explicit_years(question)) == 1 and bool(re.search(
        r"变化|变动|下降|减少|增加|\b(?:change[ds]?|increase[ds]?|decrease[ds]?|decline[ds]?)\b",
        question, re.I,
    )) and not re.search(r"会计|政策|口径|accounting|polic|restat", question, re.I))


def annual_fact_years(question: str) -> set[int]:
    years = explicit_years(question)
    if compares_prior_year(question):
        years |= {year - 1 for year in years}
    return years
