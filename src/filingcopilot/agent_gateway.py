from __future__ import annotations

import json
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
        http_client = http_client or DefaultHttpxClient()
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
        model = self.settings.review_model if review else self.settings.model
        model = model or self.settings.model
        kwargs = {
            "model": model,
            "messages": messages,
            "max_tokens": self.settings.review_max_tokens
            if review
            else self.settings.model_max_tokens,
        }
        if tools:
            kwargs.update(tools=tools, tool_choice="required")
        started = time.perf_counter()
        retries = []
        for attempt in range(self.settings.model_attempts):
            checkpoint()
            try:
                progress(
                    "model_request",
                    "正在等待独立审查模型返回。" if review else "正在等待研究模型返回。",
                    attempt=attempt + 1,
                    review=review,
                )
                response = self.client.chat.completions.create(
                    **kwargs,
                    timeout=bounded_timeout(self.settings.model_timeout),
                )
                break
            except (APIConnectionError, APIStatusError) as exc:
                status = exc.status_code if isinstance(exc, APIStatusError) else None
                retryable = status is None or status in (408, 409, 429) or status >= 500
                event = {"attempt": attempt + 1, "error_type": type(exc).__name__, "status": status}
                retries.append(event)
                exc.gateway_retries = retries
                try:
                    checkpoint()
                except ResearchStopped as stop:
                    event["stopped"] = type(stop).__name__
                    stop.gateway_retries = retries
                    raise
                if not retryable or attempt + 1 >= self.settings.model_attempts:
                    raise
                server_delay = self._retry_after(exc) if status is not None else None
                base = (
                    max(30.0, self.settings.retry_base_delay)
                    if status == 429
                    else self.settings.retry_base_delay
                )
                delay = min(
                    self.settings.retry_max_delay, base * 2**attempt * random.uniform(0.8, 1.2)
                )
                if server_delay is not None:
                    # Do not retry early when the server asks for a wait beyond our budget.
                    if server_delay > self.settings.retry_max_delay:
                        event["stopped"] = "server cooldown exceeds retry budget"
                        raise
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
                    attempt=attempt + 1,
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
            "attempts": attempt + 1,
            "retries": retries,
            "finish_reason": response.choices[0].finish_reason,
        }
