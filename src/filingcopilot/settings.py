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


@dataclass(frozen=True)
class Settings:
    home: Path
    api_base: str = ""
    api_key: str = ""
    # Optional single-user API guard. It is not a tenant or role system;
    # remote deployments still need TLS and an authenticated proxy.
    auth_token: str | None = None
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
    # Keep the library default generous enough for a complete independent
    # review. Deployments that want the lower-latency bounded review can opt
    # into FC_REVIEW_MAX_TOKENS=4096 explicitly (the example environment does
    # so); direct Settings() users should retain the historical safety margin.
    review_max_tokens: int = 8192
    # Four keeps the usual narrative review within one bounded provider call;
    # explicit smaller values remain available for controlled experiments.
    review_batch_size: int = 4
    min_model_interval: float = 21.0
    model_timeout: float = 60.0
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
    model_attempts: int = 3
    retry_base_delay: float = 1.0
    retry_max_delay: float = 60.0
    model_input_price_per_million: float | None = None
    model_output_price_per_million: float | None = None
    max_cost_usd: float | None = None
    domain_config: Path | None = None
    market_config: Path | None = None
    twelve_data_api_key: str = ""

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
            api_base=os.getenv("FC_API_BASE", "").strip(),
            api_key=os.getenv("FC_API_KEY", "").strip(),
            auth_token=os.getenv("FC_AUTH_TOKEN", "").strip() or None,
            model=os.getenv("FC_MODEL", "").strip(),
            review_model=os.getenv("FC_REVIEW_MODEL", "").strip(),
            sec_user_agent=os.getenv("FC_SEC_USER_AGENT", "").strip(),
            twelve_data_api_key=os.getenv("FC_TWELVE_DATA_API_KEY", "").strip(),
            embedding_mode=os.getenv("FC_EMBEDDING_MODE", "local").strip(),
            embedding_model=os.getenv("FC_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"),
            embedding_threads=max(1, min(8, int(os.getenv("FC_EMBEDDING_THREADS", str(cls.embedding_threads))))),
            rerank_mode=os.getenv("FC_RERANK_MODE", "diversity").strip().lower(),
            max_rounds=max(1, min(12, int(os.getenv("FC_MAX_ROUNDS", "8")))),
            max_tool_calls=max(1, min(30, int(os.getenv("FC_MAX_TOOL_CALLS", "20")))),
            model_max_tokens=max(512, min(8192, int(os.getenv("FC_MODEL_MAX_TOKENS", "8192")))),
            review_max_tokens=max(1024, min(8192, int(os.getenv("FC_REVIEW_MAX_TOKENS", "8192")))),
            review_batch_size=max(1, min(8, int(os.getenv("FC_REVIEW_BATCH_SIZE", "4")))),
            min_model_interval=max(0.0, min(60.0, float(os.getenv("FC_MIN_MODEL_INTERVAL", "21")))),
            model_timeout=max(1.0, min(180.0, float(os.getenv("FC_MODEL_TIMEOUT", "60")))),
            model_temperature=_optional_float("FC_MODEL_TEMPERATURE", minimum=0.0, maximum=2.0),
            model_top_p=_optional_float("FC_MODEL_TOP_P", minimum=0.0, maximum=1.0),
            model_seed=_optional_int("FC_MODEL_SEED"),
            research_timeout=_optional_timeout("FC_RESEARCH_TIMEOUT", 3000.0),
            review_reserve_seconds=max(0.0, min(300.0, float(os.getenv("FC_REVIEW_RESERVE_SECONDS", "30")))),
            refresh_timeout=max(1.0, min(60.0, float(os.getenv("FC_REFRESH_TIMEOUT", "20")))),
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
