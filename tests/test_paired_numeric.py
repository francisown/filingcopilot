import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "paired_numeric", Path(__file__).resolve().parents[1] / "scripts/check_paired_numeric.py"
)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def source_and_fact():
    item = {
        "kind": "fact",
        "ticker": "AAPL",
        "metric": "revenue",
        "value": "100",
        "concept": "us-gaap:Revenue",
        "unit": "USD",
        "start": "2024-01-01",
        "end": "2024-12-31",
        "filing": {"cik": "0000320193", "accession": "annual"},
    }
    source = {
        "320193": {
            "facts": {
                "us-gaap": {
                    "Revenue": {
                        "units": {
                            "USD": [
                                {
                                    "val": 100,
                                    "start": "2024-01-01",
                                    "end": "2024-12-31",
                                    "accn": "annual",
                                    "filed": "2025-02-01",
                                }
                            ]
                        }
                    },
                    "Income": {
                        "units": {
                            "USD": [
                                {
                                    "val": 20,
                                    "start": "2024-01-01",
                                    "end": "2024-12-31",
                                    "accn": "annual",
                                    "filed": "2025-02-01",
                                }
                            ]
                        }
                    },
                }
            }
        }
    }
    return source, item


@pytest.mark.parametrize(
    "key,value",
    [
        ("value", "20"),
        ("unit", "TWD"),
        ("start", "2024-10-01"),
        ("end", "2023-12-31"),
        ("concept", "us-gaap:Income"),
    ],
)
def test_same_number_elsewhere_does_not_validate_wrong_fact(key, value):
    source, item = source_and_fact()
    item[key] = value
    assert checker.check(item, source, "2025-03-01")[0] == "mismatch"


def test_source_after_knowledge_date_is_not_accepted():
    source, item = source_and_fact()
    assert checker.check(item, source, "2025-01-01")[0] == "mismatch"
    assert checker.check(item, source, "2025-03-01")[0] == "matched"


def test_ratio_is_recomputed_and_missing_source_is_unassessed():
    source, revenue = source_and_fact()
    income = deepcopy(revenue)
    income.update(metric="income", concept="us-gaap:Income", value="20")
    calc = {
        "kind": "calculation",
        "operation": "ratio",
        "inputs": [income, revenue],
        "value": "20",
        "unit": "%",
    }
    assert checker.check(calc, source, "2025-03-01")[0] == "matched"
    calc["value"] = "100"
    assert checker.check(calc, source, "2025-03-01")[0] == "mismatch"
    assert checker.check(calc, {}, "2025-03-01")[0] == "unassessed"
