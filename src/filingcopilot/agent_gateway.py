from __future__ import annotations

import json
import os
import random
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
from openai import APIConnectionError, APIStatusError, DefaultHttpxClient, OpenAI

from .execution import (
    ResearchStopped,
    bounded_timeout,
    checkpoint,
    current_control,
    progress,
    wait,
)
from .settings import Settings


class OpenAIGateway:
    def __init__(self, settings: Settings, http_client: httpx.Client | None = None):
        self.settings = settings
        self.next_call: dict[str, float] = {}
        self.lock = threading.Lock()
        self.clock = time.monotonic
        # Models that rejected tool_choice="required" (for example thinking-mode
        # providers). Detected once per model, then served with "auto".
        self._required_unsupported: set[str] = set()
        # Provider headers are optional; ignore malformed JSON configuration.
        extra_headers = {}
        raw_headers = os.getenv("FC_EXTRA_HEADERS", "").strip()
        if raw_headers:
            try:
                parsed = json.loads(raw_headers)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                extra_headers.update({str(key): str(value) for key, value in parsed.items()})
        session_id = os.getenv("FC_SESSION_ID", "").strip()
        if session_id:
            extra_headers.setdefault("x-opencode-session", session_id)
        http_client = http_client or (
            DefaultHttpxClient(headers=extra_headers) if extra_headers else DefaultHttpxClient()
        )
        http_client.event_hooks["request"].append(self._limit_request)
        self.client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.api_base or None,
            timeout=settings.model_timeout,
            max_retries=0,
            http_client=http_client,
        )

    def _limit_request(self, request: httpx.Request) -> None:
        if request.url.path.endswith("/chat/completions"):
            model = json.loads(request.read())["model"]
            # SDK preparation may stall. Reserve only when the prepared request is sent.
            self._wait_for_slot(model)
            checkpoint()
            timeout = request.extensions.get("timeout", {})
            for phase, seconds in timeout.items():
                timeout[phase] = bounded_timeout(
                    seconds if seconds is not None else self.settings.model_timeout
                )
            progress("model_request", "正在等待模型返回；当前请求受单次超时和本轮剩余时间约束。")

    def _wait_for_slot(self, model: str) -> None:
        # Recheck after waking: late sleepers must not bunch up at the same start time.
        while True:
            checkpoint()
            with self.lock:
                now = self.clock()
                delay = self.next_call.get(model, now) - now
                if delay <= 0:
                    self.next_call[model] = now + self.settings.min_model_interval
                    return
            progress("model_wait", "模型调用间隔等待中，可取消。", wait_seconds=round(delay, 1))
            wait(delay)

    @staticmethod
    def _retry_after(exc: APIStatusError) -> float | None:
        headers = exc.response.headers
        try:
            if headers.get("retry-after-ms"):
                return max(0.0, float(headers["retry-after-ms"]) / 1000)
            value = headers.get("retry-after")
            if value is None:
                return None
            try:
                return max(0.0, float(value))
            except ValueError:
                when = parsedate_to_datetime(value)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None

    def complete(self, messages: list, tools: list | None, review: bool = False) -> dict:
        """Complete a request, failing over to the other configured model.

        Research and review normally use different models for independence.
        If the selected model keeps failing (timeouts, repeated 5xx/429), the
        other configured model is tried once so a transient provider problem
        does not discard the whole run.

        A review never silently uses a model that produced the answer: such a
        reviewer is only chosen when nothing independent is reachable, and the
        result then carries ``review_independence = "not_independent"`` so the
        caller can disclose it instead of implying an independent check.
        """
        started = time.perf_counter()
        prior_retries = []
        prior_attempts = 0
        primary = (
            self.settings.review_model if review else self.settings.model
        ) or self.settings.model
        other = self.settings.model if review else self.settings.review_model
        candidates = [primary]
        if other and other != primary:
            candidates.append(other)
        control = current_control()
        produced_answer = control.research_models if control else frozenset()
        if review:
            models = [m for m in candidates if m not in produced_answer] + [
                m for m in candidates if m in produced_answer
            ]
        else:
            models = candidates
        last_exc: Exception | None = None
        for index, model in enumerate(models):
            try:
                result = self._complete_once(model, messages, tools, review)
                # The receipt covers the whole request, including a failed
                # primary. Otherwise successful failover hides its latency and
                # attempts from the saved answer's usage/trace.
                result["duration_ms"] = round((time.perf_counter() - started) * 1000)
                result["attempts"] += prior_attempts
                if prior_retries:
                    result["retries"] = prior_retries + [
                        {**event, "model": model} for event in result["retries"]
                    ]
                if review:
                    if control is None:
                        result["review_independence"] = "unknown"
                    elif model in control.research_models:
                        result["review_independence"] = "not_independent"
                return result
            except (APIConnectionError, APIStatusError) as exc:
                last_exc = exc
                retries = getattr(exc, "gateway_retries", [])
                prior_attempts += max(1, len(retries))
                prior_retries.extend({**event, "model": model} for event in retries)
                exc.gateway_retries = list(prior_retries)
                status = exc.status_code if isinstance(exc, APIStatusError) else None
                retryable = status is None or status in (408, 409, 429) or status >= 500
                cooldown = any(
                    event.get("stopped") == "server cooldown exceeds retry budget"
                    for event in retries
                )
                if not retryable or cooldown or index + 1 >= len(models):
                    raise
                # Failing over costs another full model call. Do not spend it when
                # the remaining research budget cannot absorb it: exhausting the
                # budget would lose more than the failed call did.
                remaining = control.remaining if control else None
                if remaining is not None and remaining <= max(60.0, self.settings.model_timeout):
                    raise
                progress(
                    "model_failover",
                    "当前模型多次失败，改用备用模型重试。",
                    failed_model=model,
                    fallback_model=models[index + 1],
                )
            except ResearchStopped as stop:
                stop.gateway_retries = prior_retries + [
                    {**event, "model": model}
                    for event in getattr(stop, "gateway_retries", [])
                ]
                raise
        raise last_exc  # pragma: no cover

    def _complete_once(
        self, model: str, messages: list, tools: list | None, review: bool
    ) -> dict:
        kwargs = {
            "model": model,
            "messages": messages,
            "max_tokens": self.settings.review_max_tokens
            if review
            else self.settings.model_max_tokens,
        }
        # Send optional sampling controls only when explicitly configured.
        # This keeps reasoning-model compatibility while allowing reproducible
        # A/B runs on providers that support these fields.
        if self.settings.model_temperature is not None:
            kwargs["temperature"] = self.settings.model_temperature
        if self.settings.model_top_p is not None:
            kwargs["top_p"] = self.settings.model_top_p
        if self.settings.model_seed is not None:
            kwargs["seed"] = self.settings.model_seed
        if tools:
            kwargs.update(tools=tools)
        required = bool(tools) and model not in self._required_unsupported
        started = time.perf_counter()
        retries = []
        # A capability downgrade is allowed once in addition to the bounded
        # transient attempts, even when rejection arrives on the last attempt.
        attempt, failures = 0, 0
        while True:
            attempt += 1
            checkpoint()
            try:
                progress(
                    "model_request",
                    "正在等待审查模型返回。" if review else "正在等待研究模型返回。",
                    attempt=attempt,
                    review=review,
                )
                call_kwargs = dict(kwargs)
                if tools:
                    call_kwargs["tool_choice"] = "required" if required else "auto"
                response = self.client.chat.completions.create(
                    **call_kwargs,
                    timeout=bounded_timeout(self.settings.model_timeout),
                )
                break
            except (APIConnectionError, APIStatusError) as exc:
                status = exc.status_code if isinstance(exc, APIStatusError) else None
                # Some providers reject tool_choice="required" (thinking-mode
                # models). Fall back to "auto" for this model instead of failing
                # the whole run; the prompt still asks for a tool call.
                if (
                    required
                    and isinstance(exc, APIStatusError)
                    and status == 400
                    and "tool_choice" in str(exc).lower()
                ):
                    self._required_unsupported.add(model)
                    required = False
                    retries.append(
                        {
                            "attempt": attempt,
                            "error_type": type(exc).__name__,
                            "status": status,
                            "fallback": "tool_choice=auto",
                        }
                    )
                    exc.gateway_retries = retries
                    continue
                retryable = status is None or status in (408, 409, 429) or status >= 500
                failures += 1
                event = {"attempt": attempt, "error_type": type(exc).__name__, "status": status}
                retries.append(event)
                exc.gateway_retries = retries
                try:
                    checkpoint()
                except ResearchStopped as stop:
                    event["stopped"] = type(stop).__name__
                    stop.gateway_retries = retries
                    raise
                server_delay = self._retry_after(exc) if status is not None else None
                # Preserve a provider cooldown even on the final permitted
                # attempt; the outer failover must not treat it as an ordinary
                # exhausted retry and immediately send to another model.
                if (
                    retryable
                    and server_delay is not None
                    and server_delay > self.settings.retry_max_delay
                ):
                    event["stopped"] = "server cooldown exceeds retry budget"
                    raise
                if not retryable or failures >= self.settings.model_attempts:
                    raise
                base = (
                    max(30.0, self.settings.retry_base_delay)
                    if status == 429
                    else self.settings.retry_base_delay
                )
                delay = min(
                    self.settings.retry_max_delay, base * 2**(failures - 1) * random.uniform(0.8, 1.2)
                )
                if server_delay is not None:
                    delay = max(delay, server_delay)
                event["delay_seconds"] = round(delay, 3)
                if status == 429:
                    with self.lock:
                        self.next_call[model] = max(
                            self.next_call.get(model, 0), self.clock() + delay
                        )
                progress(
                    "model_retry",
                    "模型连接暂未完成，等待重试；可取消。",
                    attempt=attempt,
                    wait_seconds=round(delay, 1),
                    error_type=type(exc).__name__,
                    status=status,
                )
                try:
                    wait(delay)
                except ResearchStopped as stop:
                    event["stopped"] = type(stop).__name__
                    stop.gateway_retries = retries
                    raise
        message = response.choices[0].message
        control = current_control()
        if control:
            if not review:
                control.record_author(model)
            control.record_model(response.usage.model_dump() if response.usage else {})
        return {
            "message": {
                "role": "assistant",
                "content": message.content,
                **(
                    {"tool_calls": [c.model_dump() for c in message.tool_calls]}
                    if message.tool_calls
                    else {}
                ),
            },
            "usage": response.usage.model_dump() if response.usage else {},
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "model": model,
            "attempts": attempt,
            "retries": retries,
            "finish_reason": response.choices[0].finish_reason,
        }
