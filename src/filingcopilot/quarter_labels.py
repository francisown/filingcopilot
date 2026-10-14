"""Source-disclosed quarter labels; never infer fiscal quarters from calendar months."""

import re
from datetime import date

from .periods import evidence_period, fact_inputs

_ORDINAL = r"first|second|third|fourth|[1-4](?:st|nd|rd|th)?"
_SOURCE = re.compile(
    rf"(?:fiscal\s+(?P<fy>20\d{{2}})\s+(?P<fq>{_ORDINAL})\s+quarter"
    rf"|(?P<q>{_ORDINAL})\s+quarter\s+(?:of\s+)?(?P<fiscal>fiscal\s+)?(?P<year>20\d{{2}}))"
    r"\s+(?:ended|ending)\s+(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2}),?\s+(?P<end_year>20\d{2})",
    re.I,
)
_MONTHS = {
    name.lower(): number
    for number, name in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        1,
    )
}
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "一": 1, "二": 2, "三": 3, "四": 4}
_REFERENCE = re.compile(
    r"(?<![A-Za-z0-9])(?:(?:FY\s*)?(?P<y1>20\d{2})\s*Q(?P<q1>[1-4])"
    r"|Q(?P<q2>[1-4])\s+(?:FY\s*)?(?P<y2>20\d{2})"
    r"|(?:FY\s*)?(?P<y3>20\d{2})年?第?(?P<q3>[1-4一二三四])(?:季度|财季)"
    r"|Q(?P<q4>[1-4])(?![A-Za-z0-9]))"
    r"(?!\d)",
    re.I,
)


def source_quarter_labels(evidence: list[dict]) -> list[dict]:
    """Only explicit label + ended date pairs from cited original passages qualify."""
    ends = {f["end"] for e in evidence for f in fact_inputs(e) if evidence_period(f) == "quarter"}
    labels = []
    for item in evidence:
        if item["kind"] != "passage":
            continue
        for match in _SOURCE.finditer(item["text"]):
            ordinal = (match["fq"] or match["q"]).lower()
            quarter = _ORDINALS.get(ordinal) or int(ordinal[0])
            month = _MONTHS.get(match["month"].lower())
            if month is None:
                continue
            try:
                end = date(int(match["end_year"]), month, int(match["day"])).isoformat()
            except ValueError:
                continue
            # An unrelated forward quarter or another financial window cannot
            # rename a cited quarter calculation. Passage-only claims retain
            # their explicitly disclosed periods for independent review.
            if end > item["filing"]["filed"] or ends and end not in ends:
                continue
            label = {
                "year_label": int(match["fy"] or match["year"]),
                "quarter": quarter,
                "end": end,
                "fiscal_explicit": bool(match["fy"] or match["fiscal"]),
                "source_id": item["id"],
                "source_text": match[0],
            }
            if label not in labels:
                labels.append(label)
    return labels


def quarter_label_conflict(statement: str, evidence: list[dict]) -> str | None:
    labels = source_quarter_labels(evidence)
    for match in _REFERENCE.finditer(statement):
        start = max(statement.rfind(mark, 0, match.start()) for mark in "，,；;。!?") + 1
        end = min(
            [p for mark in "，,；;。!?" if (p := statement.find(mark, match.end())) >= 0]
            or [len(statement)]
        )
        fragment = statement[start:end]
        # Missing/future quarters are questions, not assertions about the
        # disclosed result. "Already/currently" remains an asserted premise.
        if re.search(
            r"尚需|待核|未来|后续|是否|能否|需观察|需核验|尚未披露", fragment
        ) and not re.search(r"已|当前|实际|already|currently", fragment, re.I):
            continue
        year_text = match["y1"] or match["y2"] or match["y3"]
        year = int(year_text) if year_text else None
        value = match["q1"] or match["q2"] or match["q3"] or match["q4"]
        quarter = _ORDINALS.get(value) or int(value)
        relevant = [label for label in labels if year is None or label["year_label"] == year]
        if relevant and not any(label["quarter"] == quarter for label in relevant):
            expected = ", ".join(
                sorted(
                    {
                        f"{'FY' if label['fiscal_explicit'] else ''}{label['year_label']}Q{label['quarter']} ended {label['end']}"
                        for label in relevant
                    }
                )
            )
            return f"Quarter label contradicts cited explicit source disclosure ({expected}); use the source label or exact source dates"
    return None
