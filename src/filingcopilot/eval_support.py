"""Evaluation-only support classification for numeric answers.

The product verifier handles claim/evidence binding. Evaluation needs a
separate, deliberately strict view: did the evidence actually contain the
answer's key values, units, periods and calculation inputs? This module is
standard-library only so the optional evaluation scripts can share one
definition without importing pandas, model SDKs or the corpus.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Sequence

NUMBER_PATTERN = re.compile(
    r"(?<![\w.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?!\w)"
)
UNIT_PATTERN = re.compile(
    r"(?i)(?<![\w])(?:usd|cny|rmb|eur|jpy|gbp|hkd|aud|cad|chf|"
    r"dollars?|yuan|renminbi|million|millions|billion|billions|thousand|"
    r"percent(?:age)?|basis[ -]?points?|bps?|shares?|per[ -]?share|"
    r"times|x|[$€£¥%])(?![\w])"
)
PERIOD_PATTERN = re.compile(
    r"(?<!\d)(20\d{2})(?:\s*(?:[-/]\s*(?:Q([1-4])|H([12]))|"
    r"\s*(Q([1-4])|H([12]))))?(?!\d)"
)
_EQUATION = re.compile(
    r"(?P<a>[-+]?\d[\d,]*(?:\.\d+)?)\s*(?P<op>[-+*/])\s*"
    r"(?P<b>[-+]?\d[\d,]*(?:\.\d+)?)\s*=\s*"
    r"(?P<c>[-+]?\d[\d,]*(?:\.\d+)?)"
)
_CHANGE = re.compile(
    r"(?:increased|decreased|grew|fell|rose|declined|changed)\s+by\s+"
    r"(?P<a>[-+]?\d[\d,]*(?:\.\d+)?)\s+"
    r"from\s+(?P<b>[-+]?\d[\d,]*(?:\.\d+)?)\s+to\s+"
    r"(?P<c>[-+]?\d[\d,]*(?:\.\d+)?)",
    re.I,
)
_CALC_NOISE = re.compile(
    r"(?i)\b(?:usd|cny|rmb|eur|jpy|gbp|hkd|aud|cad|chf|dollars?|"
    r"yuan|renminbi|million|millions|billion|billions|thousand|"
    r"percent(?:age)?|basis[ -]?points?|bps?|shares?|per[ -]?share|times)\b"
    r"|[$€£¥%]"
)
_WORD_CALC = re.compile(
    r"(?P<a>[-+]?\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<op>minus|less|plus|times|multiplied\s+by|divided\s+by)\s*"
    r"(?P<b>[-+]?\d[\d,]*(?:\.\d+)?)",
    re.I,
)
_CALCULATED_AS = re.compile(
    r"(?:calculated\s+as|computed\s+as|equals?)\s+"
    r"(?P<a>[-+]?\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<op>minus|less|plus|times|multiplied\s+by|divided\s+by)\s*"
    r"(?P<b>[-+]?\d[\d,]*(?:\.\d+)?)",
    re.I,
)


def require_file(path: Path, label: str) -> Path:
    """Fail with an actionable message instead of an unhandled FileNotFound."""
    path = Path(path)
    if not path.is_file():
        raise SystemExit(
            f"Missing {label}: {path}. Restore the frozen evidence archive or pass "
            "the corresponding --data/--source-snapshot path."
        )
    return path


def norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


def _decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", "").rstrip("."))
    except (InvalidOperation, ValueError):
        return None


def numeric_values(value: object) -> set[Decimal]:
    values: set[Decimal] = set()
    for match in NUMBER_PATTERN.finditer(str(value)):
        number = _decimal(match.group(0))
        if number is not None:
            values.add(number)
    return values


def _unit(value: str) -> str:
    folded = value.casefold()
    if folded in {"dollar", "dollars", "$"}:
        return "usd"
    if folded in {"yuan", "renminbi", "rmb", "¥"}:
        return "cny"
    if folded in {"percent", "percentage", "%"}:
        return "%"
    if folded in {"million", "millions"}:
        return "million"
    if folded in {"billion", "billions"}:
        return "billion"
    if folded in {"thousand"}:
        return "thousand"
    if folded.startswith("basis"):
        return "bps"
    return folded.replace(" ", "-")


def unit_values(value: object) -> set[str]:
    return {_unit(match.group(0)) for match in UNIT_PATTERN.finditer(str(value))}


def period_values(value: object) -> set[str]:
    periods: set[str] = set()
    for match in PERIOD_PATTERN.finditer(str(value)):
        year = match.group(1)
        quarter = match.group(2) or match.group(4)
        half = match.group(3) or match.group(5)
        suffix = f"-q{quarter}" if quarter else f"-h{half}" if half else ""
        periods.add(year + suffix)
    return periods


@dataclass(frozen=True)
class Calculation:
    operator: str
    operands: frozenset[Decimal]
    result: Decimal | None
    relation_verified: bool

    @property
    def result_values(self) -> frozenset[Decimal]:
        return frozenset({self.result}) if self.result is not None else frozenset()


@dataclass
class SupportReport:
    status: str
    numbers: frozenset[Decimal] = field(default_factory=frozenset)
    matched_numbers: frozenset[Decimal] = field(default_factory=frozenset)
    missing_numbers: frozenset[Decimal] = field(default_factory=frozenset)
    units: frozenset[str] = field(default_factory=frozenset)
    missing_units: frozenset[str] = field(default_factory=frozenset)
    periods: frozenset[str] = field(default_factory=frozenset)
    missing_periods: frozenset[str] = field(default_factory=frozenset)
    calculations: tuple[Calculation, ...] = ()
    calculation_supported: bool = True
    evidence_count: int = 0

    def as_dict(self) -> dict:
        def numbers(values: Iterable[Decimal]) -> list[str]:
            return [str(value) for value in sorted(values)]

        return {
            "status": self.status,
            "numbers": numbers(self.numbers),
            "matched_numbers": numbers(self.matched_numbers),
            "missing_numbers": numbers(self.missing_numbers),
            "units": sorted(self.units),
            "missing_units": sorted(self.missing_units),
            "periods": sorted(self.periods),
            "missing_periods": sorted(self.missing_periods),
            "calculation_supported": self.calculation_supported,
            "calculations": [
                {
                    "operator": item.operator,
                    "operands": numbers(item.operands),
                    "result": str(item.result) if item.result is not None else None,
                    "relation_verified": item.relation_verified,
                }
                for item in self.calculations
            ],
            "evidence_count": self.evidence_count,
        }


def _apply(operator: str, left: Decimal, right: Decimal) -> Decimal | None:
    if operator == "+":
        return left + right
    if operator == "-":
        return left - right
    if operator == "*":
        return left * right
    if operator == "/" and right:
        return left / right
    return None


def _operator(value: str) -> str:
    return {
        "plus": "+",
        "minus": "-",
        "less": "-",
        "times": "*",
        "multiplied by": "*",
        "divided by": "/",
    }.get(value.casefold(), value)


def calculations(answer: object) -> tuple[Calculation, ...]:
    """Extract verifiable arithmetic relations from the answer text.

    A calculated result is allowed to be absent from the evidence only when
    the relation's inputs are present and the arithmetic relation itself is
    verified. This is intentionally evidence-input based, not string matching
    of the result.
    """
    text = _CALC_NOISE.sub(" ", str(answer))
    found: list[Calculation] = []

    def add(operator: str, a: str, b: str, result: str | Decimal | None):
        left, right = _decimal(a), _decimal(b)
        if left is None or right is None:
            return
        operator = _operator(operator)
        evaluated = _apply(operator, left, right)
        if evaluated is None:
            return
        result_value = _decimal(result) if isinstance(result, str) else result
        if result_value is None:
            # A formula such as "calculated as A minus B" supplies inputs but
            # not a literal result; the verified arithmetic result is inferred.
            result_value = evaluated
            verified = True
        else:
            verified = abs(evaluated - result_value) <= Decimal("0.000001")
        item = Calculation(operator, frozenset({left, right}), result_value, verified)
        if item not in found:
            found.append(item)

    for match in _EQUATION.finditer(text):
        add(match["op"], match["a"], match["b"], match["c"])
    for match in _CHANGE.finditer(text):
        a, b, c = _decimal(match["a"]), _decimal(match["b"]), _decimal(match["c"])
        if a is not None and b is not None and c is not None:
            # "from 2021 to 2023" is a period phrase, not an arithmetic pair.
            if (
                b == b.to_integral_value()
                and c == c.to_integral_value()
                and 1900 <= b <= 2100
                and 1900 <= c <= 2100
            ):
                continue
            found.append(
                Calculation(
                    "change",
                    frozenset({b, c}),
                    a,
                    abs(c - b) == a,
                )
            )
    for match in _CALCULATED_AS.finditer(text):
        add(match["op"], match["a"], match["b"], None)
    for match in _WORD_CALC.finditer(text):
        # A word formula without an explicit result still contributes its
        # operands; the result is inferred and cannot be compared to text.
        add(match["op"], match["a"], match["b"], None)
    return tuple(found)


def classify_support(texts: Sequence[str], answer: object) -> SupportReport:
    """Classify an answer against one or more evidence texts.

    ``full`` means all required numbers, units and periods are covered and any
    detected calculation relation is supported by evidence inputs. ``partial``
    means some required values are present but at least one is missing.
    ``unsupported`` means no required number is covered (or a nonnumeric answer
    has no meaningful text containment).
    """
    evidence = [norm(text) for text in texts if text]
    answer_value = norm(answer)
    if not evidence or not answer_value:
        return SupportReport("unsupported", evidence_count=len(evidence))

    numbers = numeric_values(answer_value)
    units = unit_values(answer_value)
    periods = period_values(answer_value)
    relations = calculations(answer_value)
    evidence_numbers = set().union(*(numeric_values(text) for text in evidence))
    evidence_units = set().union(*(unit_values(text) for text in evidence))
    evidence_periods = set().union(*(period_values(text) for text in evidence))

    if not numbers:
        exact = any(answer_value[:120] in text for text in evidence)
        answer_tokens = set(re.findall(r"[a-z0-9]+", answer_value))
        text_tokens = set().union(*(set(re.findall(r"[a-z0-9]+", text)) for text in evidence))
        overlap = len(answer_tokens & text_tokens) / max(1, len(answer_tokens))
        status = "full" if exact else "partial" if overlap >= 0.80 else "unsupported"
        return SupportReport(status, evidence_count=len(evidence))

    result_values = set().union(*(item.result_values for item in relations)) if relations else set()
    operand_values = set().union(*(item.operands for item in relations)) if relations else set()
    required_numbers = set(numbers)
    if relations:
        # A verified calculation may explain the result value; do not require
        # the result to appear verbatim. Operands still must be present.
        required_numbers -= {value for value in result_values if value not in operand_values}
    matched = required_numbers & evidence_numbers
    missing = required_numbers - evidence_numbers
    missing_units = units - evidence_units
    missing_periods = periods - evidence_periods
    calculation_supported = all(
        item.relation_verified and item.operands <= evidence_numbers for item in relations
    ) if relations else True

    if not missing and not missing_units and not missing_periods and calculation_supported:
        status = "full"
    elif matched or (relations and any(item.operands & evidence_numbers for item in relations)):
        status = "partial"
    else:
        status = "unsupported"
    return SupportReport(
        status=status,
        numbers=frozenset(numbers),
        matched_numbers=frozenset(matched),
        missing_numbers=frozenset(missing),
        units=frozenset(units),
        missing_units=frozenset(missing_units),
        periods=frozenset(periods),
        missing_periods=frozenset(missing_periods),
        calculations=relations,
        calculation_supported=calculation_supported,
        evidence_count=len(evidence),
    )
