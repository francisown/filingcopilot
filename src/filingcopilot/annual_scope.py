"""One annual comparison scope shared by retrieval and claim validation."""

import re


def explicit_years(question: str) -> set[int]:
    return {int(y) for y in re.findall(r"(?<!\d)(20\d{2})(?!\d)", question)}


def wants_growth(question: str) -> bool:
    return bool(re.search(r"同比|增长|\bgrowth\b|year[- ]over[- ]year|\byoy\b", question, re.I))


def requests_growth_calculation(question: str) -> bool:
    """A causal explanation may mention growth without asking for its rate.

    Explicit quantities still require calculated evidence, including questions
    that ask both how much and why. Source preparation keeps its broader scope.
    """
    explicit_rate = bool(re.search(
        r"增长率|增速|增幅|同比.{0,8}(?:多少|百分|幅度)|增长.{0,8}(?:多少|百分|幅度)"
        r"|\bgrowth\s+(?:rate|percentage)\b|\bpercentage\s+(?:growth|increase)\b"
        r"|\bhow\s+(?:much|fast|quickly)\b.{0,100}\b(?:grow|grew|growth|increase[ds]?)\b",
        question, re.I,
    ))
    if explicit_rate:
        return True
    # An explanation in another request must not erase the numeric request:
    # "What was revenue growth? Explain supply risk." still needs the rate.
    parts = re.split(
        r"[。；;?!？！]|\.(?=\s)|\band\s+(?=(?:explain|discuss|describe)\b)",
        question, flags=re.I,
    )
    return any(wants_growth(part) and not re.search(
        r"原因|驱动|因素|为什么|为何|\b(?:why|drivers?|causes?|reasons?|explain|drove)\b",
        part, re.I,
    ) for part in parts)


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


def annual_filing_years(question: str) -> set[int]:
    """Growth needs comparative values, not an extra narrative report.

    Typed fact lookup still uses annual_fact_years and verifies both operands.
    An explicit comparison with the previous report keeps both filing years.
    """
    previous_report = bool(re.search(
        r"上一(?:个)?(?:财)?年|上年|去年|\b(?:prior|previous|preceding) (?:fiscal )?year\b",
        question, re.I,
    ))
    if wants_growth(question) and not previous_report:
        return explicit_years(question)
    return annual_fact_years(question)
