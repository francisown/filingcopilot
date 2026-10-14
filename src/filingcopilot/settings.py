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


@dataclass(frozen=True)
class Settings:
    home: Path
    api_base: str = ""
    api_key: str = ""
    model: str = ""
    review_model: str = ""
    sec_user_agent: str = ""
    embedding_mode: str = "local"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_threads: int = min(8, max(1, (os.cpu_count() or 2) // 2))
    rerank_mode: str = "anchor"
    retrieval_query_mode: str = "focused"
    retrieval_candidate_limit: int = 75
    max_rounds: int = 8
    max_tool_calls: int = 20
    model_max_tokens: int = 8192
    review_max_tokens: int = 8192
    min_model_interval: float = 21.0
    model_timeout: float = 60.0
    # A full company brief performs source preparation plus model drafting and
    # independent review. Four minutes was too easy to exhaust on a slow or
    # rate-limited provider; the deadline remains configurable via the env file.
    research_timeout: float = 420.0
    refresh_timeout: float = 20.0
    model_attempts: int = 3
    retry_base_delay: float = 1.0
    retry_max_delay: float = 60.0
    model_input_price_per_million: float | None = None
    model_output_price_per_million: float | None = None
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
            model=os.getenv("FC_MODEL", "").strip(),
            review_model=os.getenv("FC_REVIEW_MODEL", "").strip(),
            sec_user_agent=os.getenv("FC_SEC_USER_AGENT", "").strip(),
            twelve_data_api_key=os.getenv("FC_TWELVE_DATA_API_KEY", "").strip(),
            embedding_mode=os.getenv("FC_EMBEDDING_MODE", "local").strip(),
            embedding_model=os.getenv("FC_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"),
            embedding_threads=max(1, min(8, int(os.getenv("FC_EMBEDDING_THREADS", str(cls.embedding_threads))))),
            rerank_mode=os.getenv("FC_RERANK_MODE", "anchor").strip().lower(),
            max_rounds=max(1, min(12, int(os.getenv("FC_MAX_ROUNDS", "8")))),
            max_tool_calls=max(1, min(30, int(os.getenv("FC_MAX_TOOL_CALLS", "20")))),
            model_max_tokens=max(512, min(8192, int(os.getenv("FC_MODEL_MAX_TOKENS", "8192")))),
            review_max_tokens=max(1024, min(8192, int(os.getenv("FC_REVIEW_MAX_TOKENS", "8192")))),
            min_model_interval=max(0.0, min(60.0, float(os.getenv("FC_MIN_MODEL_INTERVAL", "21")))),
            model_timeout=max(1.0, min(180.0, float(os.getenv("FC_MODEL_TIMEOUT", "60")))),
            research_timeout=max(1.0, min(1800.0, float(os.getenv("FC_RESEARCH_TIMEOUT", "420")))),
            refresh_timeout=max(1.0, min(60.0, float(os.getenv("FC_REFRESH_TIMEOUT", "20")))),
            model_attempts=max(1, min(5, int(os.getenv("FC_MODEL_ATTEMPTS", "3")))),
            retry_base_delay=max(0.0, min(10.0, float(os.getenv("FC_RETRY_BASE_DELAY", "1")))),
            retry_max_delay=max(1.0, min(120.0, float(os.getenv("FC_RETRY_MAX_DELAY", "60")))),
            model_input_price_per_million=_optional_price("FC_MODEL_INPUT_PRICE_PER_MILLION"),
            model_output_price_per_million=_optional_price("FC_MODEL_OUTPUT_PRICE_PER_MILLION"),
            domain_config=Path(os.environ["FC_DOMAIN_CONFIG"]).resolve()
            if os.getenv("FC_DOMAIN_CONFIG")
            else None,
            market_config=Path(os.environ["FC_MARKET_CONFIG"]).resolve()
            if os.getenv("FC_MARKET_CONFIG")
            else None,
        )
