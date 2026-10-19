"""Conservative metric-bound contradiction checks before independent semantic review."""

from __future__ import annotations

import re

from .domain import Domain
from .semantic_boundaries import currency_direction_conflict

UP = r"\b(?:increas\w*|grew|grow\w*|rose|ris\w*|improv\w*|expand\w*)\b|增加|增长|提升|提高|上升|改善|上行|走高|抬升|高于(?:上年|去年|前一年|上一年|上期|前期)|higher than (?:the )?(?:prior|previous|last)"
DOWN = r"\b(?:decreas\w*|declin\w*|fell|fall\w*|drop\w*|reduc\w*|contract\w*)\b|减少|下降|降低|下滑|恶化|下行|走低|回落|低于(?:上年|去年|前一年|上一年|上期|前期)|lower than (?:the )?(?:prior|previous|last)"
UNCERTAIN = r"\b(?:not|no|never|could|may|might|would|expected|expect|potential|whether)\b|没有|并未|未能|可能|预计|有望|是否|尚需|待核|有待|不[会曾]?"


def directions(text: str, domain: Domain, years: set[int]) -> dict[str, set[int]]:
    terms = [
        (term, key, None)
        for key, metric in domain.metrics.items()
        for term in [metric.label, *metric.aliases]
    ]
    terms += [
        (term, key, ratio)
        for key, ratio in domain.ratios.items()
        for term in [ratio.label, *ratio.aliases]
    ]
    terms += [(term, "free_cash_flow", None) for term in ("自由现金流", "free cash flow", "FCF")]
    terms.sort(key=lambda row: len(row[0]), reverse=True)
    result: dict[str, set[int]] = {}
    for clause in re.split(r"[;；。!?]|\.(?=\s|$)", text):
        clause_years = {int(y) for y in re.findall(r"\b20\d{2}\b", clause)}
        if years and clause_years and (not clause_years <= years):
            continue  # Mixed or other periods require semantic interpretation.
        anchors, occupied = [], []
        for term, key, ratio in terms:
            pattern = re.escape(term)
            if term.isascii():
                pattern = r"(?<![a-z0-9_])" + pattern + r"(?![a-z0-9_])"
            for match in re.finditer(pattern, clause, re.I):
                if any(match.start() < end and match.end() > start for start, end in occupied):
                    continue
                anchors.append((match.start(), match.end(), key, ratio, term))
                occupied.append(match.span())
        anchors.sort()
        for idx, (start, end, key, ratio, term) in enumerate(anchors):
            stop = anchors[idx + 1][0] if idx + 1 < len(anchors) else len(clause)
            if re.search(
                r"\bwhether\s*$|是否\s*$|(?:尚需|待核|有待).{0,20}$",
                clause[max(0, start - 25) : start],
                re.I,
            ):
                continue
            segment = clause[end:stop][:90]
            if ratio and re.search(r"\$|\b(?:USD|dollars|EUR|euros)\b|美元|欧元", segment, re.I):
                # Currency-qualified ratios describe their numerator amount, e.g.
                # SEC gross margin dollars. A percentage qualifier takes precedence.
                if not re.search(r"percentage|percent|%|率", term + segment, re.I):
                    key = ratio.numerator
            up, down = re.search(UP, segment, re.I), re.search(DOWN, segment, re.I)
            if not up and not down:
                previous_end = anchors[idx - 1][1] if idx else 0
                segment = clause[max(previous_end, start - 25) : start]
                up, down = re.search(UP, segment, re.I), re.search(DOWN, segment, re.I)
            if re.search(UNCERTAIN, segment, re.I) or bool(up) == bool(down):
                continue
            result.setdefault(key, set()).add(1 if up else -1)
    return result


def causal_period_conflict(statement: str, sources: list[str], domain: Domain) -> str | None:
    """Preserve an explicit TTM window attached to a comparative causal statement."""
    ttm = r"trailing (?:twelve|12) months|(?<![A-Za-z0-9_])TTM(?![A-Za-z0-9_])|连续十二个月|滚动十二个月"
    cause = r"due to|driven by|because|attribut|来源于|源于|由于|归因|得益|导致"
    if re.search(ttm, statement, re.I) or not re.search(cause, statement, re.I):
        return None
    claimed = directions(statement, domain, set())
    scoped, other = set(), set()
    for source in sources:
        for sentence in re.split(r"[;；。!?]|\.(?=\s|$)", source):
            if re.search(cause, sentence, re.I):
                metrics = set(directions(sentence, domain, set()))
                (scoped if re.search(ttm, sentence, re.I) else other).update(metrics)
    mismatches = set(claimed) & (scoped - other)
    if mismatches:
        return "Source causation uses trailing twelve months; explicitly preserve TTM instead of substituting quarter/YTD or 同期"
    return None


def investment_cash_conflict(statement: str, sources: list[str]) -> str | None:
    """Catch a narrow cash-investment/P&L category error, not general causation."""
    cash_investment = (
        r"investing activities|cash (?:payments|outlays).{0,80}(?:invest|acquisit)"
        r"|\binvested\b|purchases.{0,50}marketable securities|投资活动.{0,10}现金"
        r"|现金.{0,15}(?:投资支出|投资支付)|购入.{0,15}(?:股票|证券)"
    )
    earnings_source = (
        r"investment (?:income|gains?|losses)|(?:unrealized|realized).{0,30}(?:gains?|losses)"
        r"|other (?:income|expense)|投资收益|投资利得|投资损益|公允价值.{0,15}(?:损益|收益)"
    )
    if not any(re.search(cash_investment, s, re.I) for s in sources) or any(
        re.search(earnings_source, s, re.I) for s in sources
    ):
        return None  # Additional earnings text still requires independent semantic review.
    for clause in re.split(r"[;；。，!?]|\.(?=\s|$)", statement):
        if not (
            re.search(r"净利|利润|盈利|\bEPS\b|earnings|net income", clause, re.I)
            and re.search(r"投资收益|投资利得|investment (?:income|gains?)", clause, re.I)
            and re.search(
                r"影响|推动|驱动|贡献|得益|源于|来自|因为|由于|导致|拉动"
                r"|attribut|driven|due to|because|contribut|impact|affect|boost",
                clause,
                re.I,
            )
        ):
            continue
        if re.search(r"是否|能否|\bwhether\b", clause, re.I) or re.match(
            r"\s*(?:尚未|未|待|需|须|仍需|仍须).{0,8}(?:核验|验证|确认|判断|查明)", clause
        ):
            continue
        if re.search(r"不证明|无法证明|不足以证明|不代表|不能证明", clause):
            continue
        return (
            "Investment cash activity does not establish investment gains or a profit driver, "
            "even when phrased as possible; cite an earnings source or state an open question"
        )
    return None


def contradiction(
    statement: str, sources: list[str], domain: Domain, years: set[int]
) -> str | None:
    currency_conflict = currency_direction_conflict(statement, sources)
    if currency_conflict:
        return currency_conflict
    claims = directions(statement, domain, years)
    evidence: dict[str, set[int]] = {}
    for source in sources:
        for metric, signs in directions(source, domain, years).items():
            evidence.setdefault(metric, set()).update(signs)
    for metric, signs in claims.items():
        if len(signs) == 1 and evidence.get(metric) == {-next(iter(signs))}:
            detail = (
                "; distinguish gross profit dollars from margin percentage"
                if metric == "gross_margin"
                else ""
            )
            return f"direction contradicts cited evidence for {metric}{detail}"
    return None
