import pytest

from filingcopilot.execution import ResearchControl, ResearchCostExceeded, research_scope
from filingcopilot.models import ResearchRequest
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings


def test_research_timeout_uses_generous_default_when_unset(monkeypatch, tmp_path):
    # Runner configuration lives in .env, which dotenv resolves next to the
    # source tree; a developer who sets a deadline there must not change what
    # the application default means in this test.
    monkeypatch.setattr("filingcopilot.settings.load_dotenv", lambda *a, **k: False)
    monkeypatch.setenv("FC_HOME", str(tmp_path))
    monkeypatch.delenv("FC_RESEARCH_TIMEOUT", raising=False)
    assert Settings.from_env().research_timeout == 3000


def test_research_timeout_can_still_be_disabled_explicitly(monkeypatch, tmp_path):
    monkeypatch.setattr("filingcopilot.settings.load_dotenv", lambda *a, **k: False)
    monkeypatch.setenv("FC_HOME", str(tmp_path))
    monkeypatch.setenv("FC_RESEARCH_TIMEOUT", "0")
    assert Settings.from_env().research_timeout is None


def test_sampling_controls_are_parsed_only_when_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv("FC_HOME", str(tmp_path))
    monkeypatch.setenv("FC_MODEL_TEMPERATURE", "0")
    monkeypatch.setenv("FC_MODEL_TOP_P", "0.8")
    monkeypatch.setenv("FC_MODEL_SEED", "17")
    settings = Settings.from_env()
    assert settings.model_temperature == 0.0
    assert settings.model_top_p == 0.8
    assert settings.model_seed == 17


def test_cost_reporting_requires_both_provider_prices(tmp_path):
    assert not Settings(home=tmp_path, model_input_price_per_million=1.0).pricing_configured
    assert Settings(
        home=tmp_path,
        model_input_price_per_million=1.0,
        model_output_price_per_million=2.0,
    ).pricing_configured


def test_cost_ceiling_rejects_missing_provider_prices(tmp_path):
    with pytest.raises(ValueError, match="requires both"):
        Settings(home=tmp_path, max_cost_usd=0.10)


def test_unlimited_control_does_not_renew_or_expire():
    control = ResearchControl()
    control.start_budget(None)
    control.start_budget(2)
    assert control.time_budget is None
    assert control.remaining is None
    assert not control.expired


def test_research_plan_handles_unlimited_timeout(tmp_path):
    service = FilingCopilot(
        Settings(home=tmp_path, embedding_mode="off", research_timeout=None)
    )
    plan = service.research_plan(
        ResearchRequest(question="苹果股票怎么样？", ticker="AAPL", mode="agent")
    )
    assert plan["time_budget_seconds"] is None
    assert plan["budget"]["time_budget_seconds"] is None
    assert plan["budget"]["estimated_latency_seconds"] > 0


def test_cost_budget_stops_after_a_completed_model_call():
    control = ResearchControl(
        max_cost_usd=0.01,
        input_price_per_million=10.0,
        output_price_per_million=20.0,
    )
    control.record_model({"prompt_tokens": 1000, "completion_tokens": 1000})
    assert control.usage["actual_cost_usd"] == 0.03
    with research_scope(control), pytest.raises(ResearchCostExceeded):
        control.checkpoint()


def test_cost_budget_is_not_enforced_without_both_prices():
    control = ResearchControl(max_cost_usd=0.01, input_price_per_million=10.0)
    control.record_model({"prompt_tokens": 1000, "completion_tokens": 1000})
    control.checkpoint()
    assert not control.cost_exceeded


def test_all_question_shapes_use_the_configured_effective_deadline(monkeypatch, tmp_path):
    """The configured wall-clock ceiling is stable across question shapes."""
    monkeypatch.setattr("filingcopilot.settings.load_dotenv", lambda *a, **k: False)
    monkeypatch.setenv("FC_HOME", str(tmp_path))
    monkeypatch.setenv("FC_RESEARCH_TIMEOUT", "300")

    from datetime import date

    from filingcopilot.models import ResearchRequest
    from filingcopilot.retrieval_query import question_shape

    assert question_shape("What are the FY2022 risks?") == "risk"
    assert question_shape("Why did revenue change?") == "cause"
    assert question_shape("Did the filing deny a demand decline?") == "negation"
    assert question_shape("What was FY2024 revenue?") == "other"

    settings = Settings.from_env()
    from filingcopilot.service_research import ResearchMixin

    class _Stub(ResearchMixin):
        def __init__(self, settings):
            self.settings = settings

    service = _Stub(settings)
    base = ResearchRequest(question="What was FY2024 revenue?", ticker="AAPL", as_of=date(2026, 10, 8))
    narrative = ResearchRequest(question="What are the FY2022 risks?", ticker="AAPL", as_of=date(2026, 10, 8))
    assert service._research_budget(base) == 300
    assert service._research_budget(narrative) == 300
