"""Reproduce collection before midnight and execution on the next local day.

Only synthetic supplier/test fixture timestamps move; production clocks and
date guards remain untouched. Run with the repository virtual environment.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))


class CollectedYesterday:
    def pytest_collection_finish(self):
        # Imports have finished, as they had just before the observed midnight.
        # Move fixture constants back one day to trigger the same mismatch now.
        import test_comparison_valuation as comparison
        import test_scenarios as scenarios
        import test_valuation as valuation

        from filingcopilot import service_comparison, service_execution, service_planning

        valuation.NOW -= timedelta(days=1)
        valuation.DAY = valuation.NOW.date()
        valuation.END = valuation.DAY - timedelta(days=60)
        scenarios.NOW = valuation.NOW
        comparison.NOW -= timedelta(days=1)
        comparison.DAY = comparison.NOW.date()

        # Keep the modular service date guards on the same synthetic day as the
        # valuation fixtures.
        class FixtureDate(date):
            @classmethod
            def today(cls):
                return valuation.DAY

        for module in (service_comparison, service_execution, service_planning):
            module.date = FixtureDate

    def pytest_sessionfinish(self, session):
        # Restore the direct imports patched in collection_finish before the
        # post-run guard checks every module's clock identity.
        from filingcopilot import (
            api,
            comparison,
            historical_valuation,
            models,
            scenarios,
            service,
            service_comparison,
            service_execution,
            service_planning,
        )

        for module in (service_comparison, service_execution, service_planning):
            module.date = date

        modules = [
            api,
            comparison,
            historical_valuation,
            models,
            scenarios,
            service,
            service_comparison,
            service_execution,
            service_planning,
        ]
        clocks_restored = all(getattr(module, "date", date) is date for module in modules)
        defaults_restored = models.ResearchRequest(question="clock restoration").as_of == date.today()
        if not clocks_restored or not defaults_restored:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
        print(f"\nFixture clock/default restoration: {clocks_restored and defaults_restored}")


if __name__ == "__main__":
    raise SystemExit(
        pytest.main(
            [
                "-q",
                "tests/test_valuation.py::test_eps_is_supplier_labeled_not_fiscal_year_or_sec_fact",
                "tests/test_scenarios.py::test_scenario_math_and_frozen_audit",
            ],
            plugins=[CollectedYesterday()],
        )
    )
