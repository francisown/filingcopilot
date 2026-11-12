from __future__ import annotations

import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _optional_price(name: str) -> float | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return max(0.0, value)


def _optional_cost(name: str) -> float | None:
    """Read an optional per-research USD ceiling; blank/zero disables it."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return None if value <= 0 else min(1_000_000.0, value)


def _optional_timeout(name: str, default: float | None = None) -> float | None:
    """Read an optional whole-research deadline.

    Empty values use the caller's default. Zero and common ``none`` spellings
    disable the cooperative deadline explicitly. Per-request timeouts and the
    agent's round/tool limits still apply.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    if raw.strip().lower() in {"0", "none", "null", "off", "unlimited", "infinite"}:
        return None
    try:
        value = float(raw)
    except ValueError:
        return default
    # Keep a generous upper bound for long narrative research while still
    # rejecting accidental values that would effectively disable cancellation.
    return None if value <= 0 else min(7200.0, value)


def _optional_float(name: str, *, minimum: float | None = None, maximum: float | None = None) -> float | None:
    """Read an explicitly configured numeric provider option.

    Sampling controls stay unset by default because some reasoning providers
    reject ``temperature`` or ``seed``. Invalid values are treated as absent.
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _optional_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _api_base(value: str) -> str:
    """Accept a pasted chat-completions URL as a provider base URL.

    Providers commonly document the full ``/chat/completions`` endpoint while
    the OpenAI SDK appends that path itself. Stripping it avoids a confusing
    404 without changing any other path or provider.
    """
    base = str(value or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/chat/completion"):
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _forms(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    forms = []
    for item in raw.split(","):
        form = item.strip().upper()
        if form and form not in forms:
            forms.append(form)
    return tuple(forms[:8]) or default


@dataclass(frozen=True)
class Settings:
    home: Path
    api_base: str = ""
    api_key: str = ""
    # Optional single-user API guard. It is not a tenant or role system;
    # remote deployments still need TLS and an authenticated proxy.
    auth_token: str | None = None
    # Optional hosted-demo access log: request ID, route, status and duration
    # only. Off by default so local use stays quiet.
    access_log: bool = False
    model: str = ""
    review_model: str = ""
    sec_user_agent: str = ""
    embedding_mode: str = "local"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_threads: int = min(8, max(1, (os.cpu_count() or 2) // 2))
    rerank_mode: str = "diversity"
    retrieval_query_mode: str = "focused"
    retrieval_candidate_limit: int = 75
    retrieval_multi_query: bool = True
    max_rounds: int = 8
    max_tool_calls: int = 20
    model_max_tokens: int = 8192
    # The provider counts reasoning against this ceiling too. 6,144 and 8,192
    # produced finish_reason=length and dropped claims in full runs; 16,384
    # completed the full 140-question run without a length row.
    review_max_tokens: int = 16384
    # Four keeps the usual narrative review within one bounded provider call;
    # explicit smaller values remain available for controlled experiments.
    review_batch_size: int = 4
    min_model_interval: float = 21.0
    model_timeout: float = 60.0
    # Semantic review can take longer than an author tool-selection turn.
    review_timeout: float = 300.0
    # Review gets one bounded retry: enough to survive a transient provider
    # failure, without spending the whole research budget on retries.
    review_attempts: int = 2
    # Omitted by default because provider support is not uniform.
    model_temperature: float | None = None
    model_top_p: float | None = None
    model_seed: int | None = None
    # Narrative filing research can require several independent evidence
    # passes.  A generous cooperative ceiling prevents an unfinished request
    # from running forever without imposing the old 300-second cutoff.
    # Set FC_RESEARCH_TIMEOUT=0/none to opt out for a controlled experiment.
    research_timeout: float | None = 3000.0
    # Keep a final-answer review window instead of spending the entire budget
    # on another research round. This is a cooperative guard; the hard deadline
    # remains research_timeout.
    review_reserve_seconds: float = 30.0
    refresh_timeout: float = 20.0
    # Watchlist triggers are an explicit local scan, not an always-on daemon.
    # Enqueueing is opt-in so a saved list alone cannot spend model budget.
    watchlist_triggers_enabled: bool = False
    watchlist_trigger_forms: tuple[str, ...] = ("10-K", "10-Q", "8-K")
    watchlist_trigger_lookback_days: int = 30
    watchlist_trigger_max_per_scan: int = 3
    monitoring_enabled: bool = True
    monitoring_use_model: bool = False
    monitoring_webhook_url: str = ""
    # 0 disables the optional background scan loop.
    monitoring_scan_interval_minutes: int = 0
    monitoring_digest_enabled: bool = False
    monitoring_digest_hour: int = 7
    monitoring_digest_channel: str = "local"
    monitoring_brief_timeout: float = 30.0
    monitoring_brief_max_tokens: int = 2048
    monitoring_brief_max_cost_usd: float | None = None
    slack_webhook_url: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_to: str = ""
    smtp_starttls: bool = True
    model_attempts: int = 3
    retry_base_delay: float = 1.0
    retry_max_delay: float = 60.0
    model_input_price_per_million: float | None = None
    model_output_price_per_million: float | None = None
    max_cost_usd: float | None = None
    domain_config: Path | None = None
    market_config: Path | None = None
    twelve_data_api_key: str = ""

    def __post_init__(self) -> None:
        # A dollar ceiling cannot be enforced without both provider prices.
        # Reject the ambiguous configuration instead of presenting a limit
        # that silently has no effect.
        if self.max_cost_usd is not None and self.max_cost_usd > 0 and not self.pricing_configured:
            raise ValueError(
                "max_cost_usd requires both model_input_price_per_million "
                "and model_output_price_per_million"
            )
        if (
            self.monitoring_brief_max_cost_usd is not None
            and self.monitoring_brief_max_cost_usd > 0
            and not self.pricing_configured
        ):
            raise ValueError(
                "monitoring_brief_max_cost_usd requires both model prices; "
                "leave it unset to disable the brief cost cap"
            )

    @property
    def review_configuration_warnings(self) -> tuple[str, ...]:
        """Explain when semantic review is not independent of the research model."""
        if not self.model and not self.review_model:
            return ("未配置研究模型和审查模型；Agent 研究与独立审查均不可用。",)
        if not self.review_model:
            return (
                "未配置 FC_REVIEW_MODEL；为兼容旧配置，审查会回退主模型，独立性不可保证。",
            )
        if self.model and self.review_model == self.model:
            return (
                "FC_REVIEW_MODEL 与 FC_MODEL 相同；审查模型可能重复主模型的错误，独立审查失效。",
            )
        return ()

    @property
    def llm_ready(self) -> bool:
        return bool(self.api_base and self.api_key and self.model)

    @property
    def pricing_configured(self) -> bool:
        return (
            self.model_input_price_per_million is not None
            and self.model_output_price_per_million is not None
        )

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(override=False)
        # httpx/requests do not discover the Windows Internet Settings proxy themselves.
        for name, value in urllib.request.getproxies().items():
            if name in ("http", "https"):
                os.environ.setdefault(name.upper() + "_PROXY", value)
        os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
        return cls(
            home=Path(os.getenv("FC_HOME", ".filingcopilot")).resolve(),
            api_base=_api_base(os.getenv("FC_API_BASE", "")),
            api_key=os.getenv("FC_API_KEY", "").strip(),
            auth_token=os.getenv("FC_AUTH_TOKEN", "").strip() or None,
            access_log=_flag("FC_ACCESS_LOG", False),
            model=os.getenv("FC_MODEL", "").strip(),
            review_model=os.getenv("FC_REVIEW_MODEL", "").strip(),
            sec_user_agent=os.getenv("FC_SEC_USER_AGENT", "").strip(),
            twelve_data_api_key=os.getenv("FC_TWELVE_DATA_API_KEY", "").strip(),
            embedding_mode=os.getenv("FC_EMBEDDING_MODE", "local").strip(),
            embedding_model=os.getenv("FC_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"),
            embedding_threads=max(1, min(8, int(os.getenv("FC_EMBEDDING_THREADS", str(cls.embedding_threads))))),
            rerank_mode=os.getenv("FC_RERANK_MODE", "diversity").strip().lower(),
            retrieval_multi_query=_flag("FC_RETRIEVAL_MULTI_QUERY", True),
            max_rounds=max(1, min(12, int(os.getenv("FC_MAX_ROUNDS", "8")))),
            max_tool_calls=max(1, min(30, int(os.getenv("FC_MAX_TOOL_CALLS", "20")))),
            model_max_tokens=max(512, min(8192, int(os.getenv("FC_MODEL_MAX_TOKENS", "8192")))),
            review_max_tokens=max(1024, min(32768, int(os.getenv("FC_REVIEW_MAX_TOKENS", "16384")))),
            review_batch_size=max(1, min(8, int(os.getenv("FC_REVIEW_BATCH_SIZE", "4")))),
            min_model_interval=max(0.0, min(60.0, float(os.getenv("FC_MIN_MODEL_INTERVAL", "21")))),
            model_timeout=max(1.0, min(180.0, float(os.getenv("FC_MODEL_TIMEOUT", "60")))),
            review_timeout=max(1.0, min(600.0, float(os.getenv("FC_REVIEW_TIMEOUT", "300")))),
            review_attempts=max(1, min(3, int(os.getenv("FC_REVIEW_ATTEMPTS", "2")))),
            model_temperature=_optional_float("FC_MODEL_TEMPERATURE", minimum=0.0, maximum=2.0),
            model_top_p=_optional_float("FC_MODEL_TOP_P", minimum=0.0, maximum=1.0),
            model_seed=_optional_int("FC_MODEL_SEED"),
            research_timeout=_optional_timeout("FC_RESEARCH_TIMEOUT", 3000.0),
            review_reserve_seconds=max(0.0, min(300.0, float(os.getenv("FC_REVIEW_RESERVE_SECONDS", "30")))),
            # A first-time quarterly refresh can need several SEC documents.
            # Keep an upper bound but allow a realistic acquisition budget.
            refresh_timeout=max(1.0, min(300.0, float(os.getenv("FC_REFRESH_TIMEOUT", "20")))),
            watchlist_triggers_enabled=_flag("FC_WATCHLIST_TRIGGERS_ENABLED"),
            watchlist_trigger_forms=_forms(
                "FC_WATCHLIST_TRIGGER_FORMS", ("10-K", "10-Q", "8-K")
            ),
            watchlist_trigger_lookback_days=max(
                1, min(365, int(os.getenv("FC_WATCHLIST_TRIGGER_LOOKBACK_DAYS", "30")))
            ),
            watchlist_trigger_max_per_scan=max(
                1, min(10, int(os.getenv("FC_WATCHLIST_TRIGGER_MAX_PER_SCAN", "3")))
            ),
            monitoring_enabled=_flag("FC_MONITORING_ENABLED", True),
            monitoring_use_model=_flag("FC_MONITORING_USE_MODEL", False),
            monitoring_webhook_url=os.getenv("FC_MONITORING_WEBHOOK_URL", "").strip(),
            monitoring_scan_interval_minutes=max(
                0,
                min(
                    1440,
                    int(os.getenv("FC_MONITORING_SCAN_INTERVAL_MINUTES", "0") or "0"),
                ),
            ),
            monitoring_digest_enabled=_flag("FC_MONITORING_DIGEST_ENABLED", False),
            monitoring_digest_hour=max(
                0, min(23, int(os.getenv("FC_MONITORING_DIGEST_HOUR", "7") or "7"))
            ),
            monitoring_digest_channel=os.getenv(
                "FC_MONITORING_DIGEST_CHANNEL", "local"
            ).strip().lower(),
            monitoring_brief_timeout=max(
                1.0,
                min(120.0, float(os.getenv("FC_MONITORING_BRIEF_TIMEOUT", "30") or "30")),
            ),
            monitoring_brief_max_tokens=max(
                256,
                min(
                    8192,
                    int(os.getenv("FC_MONITORING_BRIEF_MAX_TOKENS", "2048") or "2048"),
                ),
            ),
            monitoring_brief_max_cost_usd=_optional_cost(
                "FC_MONITORING_BRIEF_MAX_COST_USD"
            ),
            slack_webhook_url=os.getenv("FC_SLACK_WEBHOOK_URL", "").strip(),
            smtp_host=os.getenv("FC_SMTP_HOST", "").strip(),
            smtp_port=max(1, min(65535, int(os.getenv("FC_SMTP_PORT", "587") or "587"))),
            smtp_user=os.getenv("FC_SMTP_USER", "").strip(),
            smtp_password=os.getenv("FC_SMTP_PASSWORD", "").strip(),
            smtp_from=os.getenv("FC_SMTP_FROM", "").strip(),
            smtp_to=os.getenv("FC_SMTP_TO", "").strip(),
            smtp_starttls=_flag("FC_SMTP_STARTTLS", True),
            model_attempts=max(1, min(5, int(os.getenv("FC_MODEL_ATTEMPTS", "3")))),
            retry_base_delay=max(0.0, min(10.0, float(os.getenv("FC_RETRY_BASE_DELAY", "1")))),
            retry_max_delay=max(1.0, min(120.0, float(os.getenv("FC_RETRY_MAX_DELAY", "60")))),
            model_input_price_per_million=_optional_price("FC_MODEL_INPUT_PRICE_PER_MILLION"),
            model_output_price_per_million=_optional_price("FC_MODEL_OUTPUT_PRICE_PER_MILLION"),
            max_cost_usd=_optional_cost("FC_MAX_COST_USD"),
            domain_config=Path(os.environ["FC_DOMAIN_CONFIG"]).resolve()
            if os.getenv("FC_DOMAIN_CONFIG")
            else None,
            market_config=Path(os.environ["FC_MARKET_CONFIG"]).resolve()
            if os.getenv("FC_MARKET_CONFIG")
            else None,
        )
