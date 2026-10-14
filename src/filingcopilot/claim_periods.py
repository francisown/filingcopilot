"""Local period/metric comparison gates; full sentence meaning still needs review."""

import re
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

from .domain import Domain
from .periods import comparable_quarters, evidence_period, fact_inputs
from .polarity import DOWN, UP
from .ttm import trailing_window

SCOPE = re.compile(
    r"(?P<ttm>(?<![A-Za-z0-9_])TTM(?![A-Za-z0-9_])|连续十二个月|滚动十二个月|trailing (?:twelve|12) months)"
    r"|(?P<ytd>(?<![A-Za-z0-9_])YTD(?![A-Za-z0-9_])|年内累计|上半年|前[六九]个月|半年累计)"
    r"|(?P<quarter>(?<![A-Za-z0-9])Q[1-4]\s+(?:FY\s*)?20\d{2}"
    r"|(?<![A-Za-z0-9])(?:FY\s*)?20\d{2}年?第?[1-4一二三四](?:季度|财季)"
    r"|季度|财季|单季|本季|\bquarter\b|(?<![A-Za-z0-9])(?:FY)?(?:20\d{2})?Q[1-4])"
    r"|(?P<annual>年度|全年|\bannual\b|fiscal year|(?<![A-Za-z0-9])FY20\d{2}(?!Q|H|上半年|年?H1))",
    re.I,
)
LIMIT = re.compile(r"是否|能否|尚需|待核|无法|不能|不足|不支持|不等于|未核|可能|预计|若|如果", re.I)
CAPEX_STEMS = (
    "现金投入",
    "现金资本开支",
    "现金PP&E购置",
    "现金 PP&E 购置",
    "PP&E购置",
    "PP&E 购置",
)


def _scope(item: dict) -> str | None:
    if trailing_window(item):
        return "ttm"
    facts = fact_inputs(item)
    kind = evidence_period(facts[0]) if facts else None
    return "ytd" if kind in ("ytd6", "ytd9") else kind


def _subjects(item: dict, domain: Domain) -> set[str]:
    if item["kind"] == "fact":
        return {item["metric"]}
    if item["kind"] != "calculation":
        return set()
    if item["operation"] in ("growth", "difference", "trailing_sum"):
        return _subjects(item["inputs"][0], domain)
    if item["operation"] == "free_cash_flow":
        return {"free_cash_flow"}
    if item["operation"] == "net_cash_capex":
        return {"net_cash_capex"}
    if item["operation"] == "ratio":
        inputs = item["inputs"]
        return {
            key
            for key, spec in domain.ratios.items()
            if _subjects(inputs[0], domain) == {spec.numerator}
            and _subjects(inputs[1], domain) == {spec.denominator}
        }
    return set()


def _comparable(a: dict, b: dict) -> bool:
    scope = _scope(a)
    if scope != _scope(b) or a["ticker"] != b["ticker"] or a["unit"] != b["unit"]:
        return False
    if scope == "ttm":
        wa, wb = trailing_window(a), trailing_window(b)
        return all(
            357 <= (date.fromisoformat(x) - date.fromisoformat(y)).days <= 373
            for x, y in zip(wa, wb)
        )
    fa, fb = fact_inputs(a)[0], fact_inputs(b)[0]
    if scope in ("quarter", "ytd"):
        return comparable_quarters(fa, fb)
    return scope == "annual" and a["fiscal_year"] == b["fiscal_year"] + 1


def _terms(domain: Domain):
    terms = [
        (term, {key})
        for collection in (domain.metrics, domain.ratios)
        for key, spec in collection.items()
        for term in [spec.label, *spec.aliases]
    ]
    terms += [
        ("自由现金流", {"free_cash_flow"}),
        ("净资本开支", {"net_cash_capex"}),
        ("现金PP&E购置净额", {"net_cash_capex"}),
        ("PP&E购置净额", {"net_cash_capex"}),
        ("PP&E 购置净额", {"net_cash_capex"}),
        ("PP&E购置毛额", {"cash_capex"}),
        ("PP&E 购置毛额", {"cash_capex"}),
        ("net capex", {"net_cash_capex"}),
        ("FCF", {"free_cash_flow"}),
        ("盈利", {"net_income", "operating_income"}),
        ("利润", {"net_income", "operating_income"}),
        ("利润率", set(domain.ratios)),
    ]
    terms += [(stem + "毛额", {"cash_capex"}) for stem in CAPEX_STEMS]
    terms += [(stem + "净额", {"net_cash_capex"}) for stem in CAPEX_STEMS]
    terms.sort(key=lambda t: len(t[0]), reverse=True)
    return terms


def _anchors(fragment, terms):
    occupied, anchors = [], []
    for term, keys in terms:
        pattern = re.escape(term)
        if term.isascii():
            pattern = r"(?<![A-Za-z0-9_])" + pattern + r"(?![A-Za-z0-9_])"
        for m in re.finditer(pattern, fragment, re.I):
            if any(m.start() < end and m.end() > start for start, end in occupied):
                continue
            occupied.append(m.span())
            anchors.append((m.start(), m.end(), keys))
    return sorted(anchors)


def _expand_capex_coordinates(fragment: str) -> str:
    stems = "|".join(re.escape(stem) for stem in CAPEX_STEMS)
    return re.sub(
        rf"(?P<stem>{stems})(?P<basis>毛额|净额)(?P<join>\s*(?:与|和|及|、)\s*)(?P<other>毛额|净额)",
        lambda match: (
            match["stem"] + match["basis"] + match["join"] + match["stem"] + match["other"]
        ),
        fragment,
    )


def _growth_relations(text, domain):
    pattern = re.compile(
        r"(?:增速|增幅|增长率|growth(?: rate)?)\s*(?:均|都|皆)?\s*(?:明显|显著|远|大幅|进一步)?\s*"
        r"(?:(?P<higher>快于|高于|超过|超出|超|greater than|higher than|faster than|exceed(?:s|ed)?)"
        r"|(?P<lower>慢于|低于|小于|lower than|slower than|less than))",
        re.I,
    )
    inherited = None
    for fragment in re.split(r"[，,；;。!?]", text):
        fragment = _expand_capex_coordinates(fragment)
        mentions = list(SCOPE.finditer(fragment))
        anchors = _anchors(fragment, _terms(domain))
        for match in pattern.finditer(fragment):
            left = [a for a in anchors if a[1] <= match.start()]
            right = [a for a in anchors if a[0] >= match.end()]
            if not left or not right:
                continue
            last = left[-1]
            if LIMIT.search(fragment[max(0, last[0] - 16) : last[0]]):
                continue
            selected = [last]
            for a, b in reversed(list(zip(left[:-1], left[1:]))):
                if not re.fullmatch(r"\s*(?:与|和|及|、|and)\s*", fragment[a[1] : b[0]], re.I):
                    break
                selected.append(a)
            before = [m for m in mentions if m.end() <= selected[-1][0]]
            scope = before[-1].lastgroup if before else inherited
            dates = set(re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", fragment))
            for a in selected:
                yield a[2], right[0][2], scope, 1 if match.group("higher") else -1, dates
        if mentions:
            inherited = mentions[-1].lastgroup


def _growth_order_conflict(statement, evidence, domain):
    for left, right, scope, sign, dates in _growth_relations(statement, domain):
        # A source must state the relative growth itself; two raw amounts do not
        # authorize model arithmetic, and a gross trend cannot stand in for net.
        if any(
            e["kind"] == "passage"
            and any(
                a & left and b & right and s == sign and (scope is None or period == scope)
                for a, b, period, s, _ in _growth_relations(e["text"], domain)
            )
            for e in evidence
        ):
            continue
        growths = [
            e
            for e in evidence
            if e.get("operation") == "growth" and (scope is None or _scope(e) == scope)
        ]

        def window(e):
            return tuple(
                trailing_window(item)
                or (fact_inputs(item)[0]["start"], fact_inputs(item)[0]["end"])
                for item in e["inputs"]
            )

        matched = []
        for a in growths:
            for b in growths:
                if not (_subjects(a, domain) & left and _subjects(b, domain) & right):
                    continue
                if _scope(a) != _scope(b) or window(a) != window(b):
                    continue
                bound_dates = {
                    day for e in (a, b) for f in fact_inputs(e) for day in (f["start"], f["end"])
                }
                bound_dates.update(
                    day for e in (a, b) for interval in window(e) for day in interval
                )
                if dates <= bound_dates:
                    matched.append((Decimal(a["value"]) - Decimal(b["value"])) * sign)
        if not matched:
            return "Relative growth comparison requires both cited growth calculations with matching metric definitions and actual periods; gross purchases do not establish net-purchase growth"
        if not any(value > 0 for value in matched):
            return "Relative growth comparison contradicts the ordering of the cited growth calculations"
    return None


def _assertions(text: str, domain: Domain) -> Iterator[tuple[set[str], set[str], int]]:
    inherited = set()
    for fragment in re.split(r"[，,；;。!?]", text):
        mentions = list(SCOPE.finditer(fragment))
        anchors = _anchors(fragment, _terms(domain))
        for i, (start, end, keys) in enumerate(anchors):
            before = [m for m in mentions if m.end() <= start]
            scopes = inherited
            if before:
                scopes = {before[-1].lastgroup}
                # Explicit coordinated periods require support for each period.
                for left, right in zip(before[-2:], before[-1:]):
                    if re.fullmatch(
                        r"\s*(?:与|和|及|、|and)\s*(?:最新|最近|本)?",
                        fragment[left.end() : right.start()],
                        re.I,
                    ):
                        scopes.add(left.lastgroup)
            stop = anchors[i + 1][0] if i + 1 < len(anchors) else len(fragment)
            if not before:
                after = [m for m in mentions if end <= m.start() < stop]
                if after:
                    scopes = {after[0].lastgroup}
            segment = fragment[end:stop]
            if i + 1 < len(anchors) and re.fullmatch(r"[\s与和及、]*(?:同比)?", segment):
                segment = fragment[end:]
            prefix = fragment[max(0, start - 16) : start]
            limit = LIMIT.search(prefix) or re.search(
                r"(?:需观察|需核验|需跟踪|需证明|需|须).{0,4}$", prefix
            )
            if (limit and not re.search(r"已经|已|实际|确实", segment)) or LIMIT.search(segment):
                continue
            up = re.search(UP + r"|跳升|扩张|走强", segment, re.I)
            down = re.search(DOWN + r"|走弱", segment, re.I)
            if bool(up) != bool(down):
                yield keys, scopes, 1 if up else -1
            elif (
                re.search(r"支撑|支持|提供证据", segment)
                and "ttm" in scopes
                and not re.search(
                    r"(?:价格|股价|估值)(?:的)?合理性(?:仍|还|尚)?(?:需|须|取决于|有赖于)",
                    fragment,
                )
            ):
                yield keys, scopes, 0
        if mentions:
            inherited = {mentions[-1].lastgroup}


def period_comparison_conflict(statement: str, evidence: list[dict], domain: Domain) -> str | None:
    if conflict := _growth_order_conflict(statement, evidence, domain):
        return conflict
    for keys, scopes, sign in _assertions(statement, domain):
        margin = bool(keys & domain.ratios.keys())
        if not scopes and not margin:
            continue
        for scope in scopes or {None}:
            relevant = [
                e
                for e in evidence
                if _subjects(e, domain) & keys and (scope is None or _scope(e) == scope)
            ]
            if any(
                e["kind"] == "passage"
                and any(
                    src_keys & keys and (scope is None or scope in src_scopes) and src_sign == sign
                    for src_keys, src_scopes, src_sign in _assertions(e["text"], domain)
                )
                for e in evidence
            ):
                continue
            comparisons = []
            for e in relevant:
                if e.get("operation") in ("growth", "difference"):
                    comparisons.append(Decimal(e["value"]))
                elif sign == 0:
                    comparisons.append(Decimal(e["value"]))
            for a in relevant:
                for b in relevant:
                    if (
                        a.get("operation") == b.get("operation") == "ratio"
                        and _subjects(a, domain) == _subjects(b, domain)
                        and _comparable(a, b)
                    ):
                        comparisons.append(Decimal(a["value"]) - Decimal(b["value"]))
            if not comparisons:
                return f"Claimed {scope or 'unspecified'} period change/support needs its own observed comparison; both comparable period margins are required for margin changes"
            if sign and not any(value * sign > 0 for value in comparisons):
                return f"Claimed {scope or 'unspecified'} period direction contradicts the cited comparison"
    return None


def comparison_context(item: dict) -> dict:
    """Compact exact windows supplied to the independent reviewer, not UI labels."""
    return {
        "scope": _scope(item),
        "ttm_window": trailing_window(item),
        "inputs": [
            {k: f.get(k) for k in ("metric", "start", "end", "value", "unit", "fiscal_year")}
            for f in fact_inputs(item)
        ],
    }
