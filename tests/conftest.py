from datetime import date

import pytest
from pydantic import BaseModel

from filingcopilot.corpus import Corpus
from filingcopilot.models import Fact, Filing, Passage
from filingcopilot.retrieval import Retriever
from filingcopilot.settings import Settings
from filingcopilot.tools import ResearchTools


@pytest.fixture
def mock_embedding_version(monkeypatch):
    """Injected vector doubles must not depend on an installed optional SDK."""
    monkeypatch.setattr("filingcopilot.retrieval.version", lambda name: "test-fastembed-1")


@pytest.fixture
def fixed_fixture_date(monkeypatch, request):
    """Keep synthetic supplier time and application 'today' on the same day.

    Used only by current-day valuation fixtures. Models bind date.today when
    their schema is built, so patch both module clocks and bound factories,
    then rebuild and restore the schemas before the next test.
    """
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
        valuation,
    )

    rebuilt = set()
    frozen = None
    with monkeypatch.context() as patch:

        def freeze(day):
            nonlocal frozen
            if frozen is not None:
                assert frozen == day, "Supplier fixture clocks must agree"
                return
            frozen = day

            class FixtureDate(date):
                @classmethod
                def today(cls):
                    return day

                @classmethod
                def __get_pydantic_core_schema__(cls, source_type, handler):
                    # FastAPI resolves postponed date annotations after this
                    # patch; preserve the real date wire format and validation.
                    return handler(date)

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
                valuation,
            ]
            modules.append(request.module)
            for module in modules:
                if getattr(module, "date", None) is date:
                    patch.setattr(module, "date", FixtureDate)
                for value in vars(module).values():
                    if not isinstance(value, type) or not issubclass(value, BaseModel):
                        continue
                    for field in value.model_fields.values():
                        factory = field.default_factory
                        if (
                            getattr(factory, "__self__", None) is date
                            and getattr(factory, "__name__", None) == "today"
                        ):
                            patch.setattr(field, "default_factory", FixtureDate.today)
                            rebuilt.add(value)
            for model in rebuilt:
                model.model_rebuild(force=True)

        yield freeze
    for model in rebuilt:
        model.model_rebuild(force=True)


@pytest.fixture
def thread_timeout():
    # A deadlock guard, not a performance assertion. Shared CI workers can be
    # descheduled for several seconds while the rest of the suite is running.
    return 30.0


@pytest.fixture
def tools(tmp_path):
    corpus = Corpus(tmp_path)
    filing = Filing(
        id="AAPL:old",
        ticker="AAPL",
        company="Apple",
        cik="0000320193",
        accession="0000320193-25-000079",
        form="10-K",
        filed="2025-10-31",
        period_end="2025-09-27",
        source_url="https://www.sec.gov/Archives/old.htm",
        content_sha256="a" * 64,
    )
    facts = [
        Fact(
            id=id,
            filing_id=filing.id,
            ticker="AAPL",
            metric=metric,
            label=label,
            concept="us-gaap:Test",
            value=value,
            unit="USD",
            fiscal_year=year,
            start=f"{year - 1}-10-01",
            end=f"{year}-09-27",
            source_url="https://data.sec.gov/test.json",
            locator="source record",
        )
        for id, metric, label, value, year in [
            ("rev25", "revenue", "营业收入", "200", 2025),
            ("rev24", "revenue", "营业收入", "100", 2024),
            ("gross25", "gross_profit", "毛利润", "80", 2025),
        ]
    ]
    passages = [
        Passage(
            id="p1",
            filing_id=filing.id,
            ticker="AAPL",
            section="Risk factors",
            text="The company relies on manufacturing partners. Supply disruptions "
            "could adversely affect operations. No default has occurred.",
            source_url=filing.source_url,
            locator="paragraph 1",
        )
    ]
    corpus.ingest(filing, facts, passages)
    settings = Settings(home=tmp_path, embedding_mode="off", min_model_interval=0)
    return ResearchTools(corpus, Retriever(corpus, settings), date(2026, 10, 8))
