"""Cooperative research control, scoped to one worker (never a shared gateway)."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar

CANCELLED_NOTE = "研究已取消；保留已通过核验的内容，未完成部分未作结论。"
DEADLINE_NOTE = "本轮研究时间预算已用完；保留已核验内容，未完成审查的判断不作结论。可从这只证券已支持的具体问题继续查询，已获取资料保存在本地。"
COST_NOTE = "本轮研究达到已配置的费用预算；保留已核验内容，未完成审查的判断不作结论。"


class ResearchStopped(Exception):
    pass


class ResearchCancelled(ResearchStopped):
    pass


class ResearchDeadlineExceeded(ResearchStopped):
    pass


class ResearchCostExceeded(ResearchStopped):
    pass


class RefreshBudgetExceeded(Exception):
    """An optional source refresh ended; the research can still use cached evidence."""


class ResearchControl:
    def __init__(
        self,
        publish=None,
        *,
        max_cost_usd: float | None = None,
        input_price_per_million: float | None = None,
        output_price_per_million: float | None = None,
    ):
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._sealed = False
        self._deadline = None
        self._expired = False
        self._cost_exceeded = False
        self.time_budget = None
        self.max_cost_usd = max_cost_usd if max_cost_usd and max_cost_usd > 0 else None
        self.input_price_per_million = input_price_per_million
        self.output_price_per_million = output_price_per_million
        self._started = None
        self.events = []
        self.event_count = 0
        self._publish = publish or (lambda event: None)
        self._research_models: set[str] = set()
        self.usage = {
            "model_calls": 0,
            "tool_calls": 0,
            "total_tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }

    def record_model(self, usage):
        self.usage["model_calls"] += 1
        for key in ("total_tokens", "prompt_tokens", "completion_tokens"):
            self.usage[key] += int(usage.get(key) or 0)
        if self.pricing_configured:
            self.usage["actual_cost_usd"] = round(
                self.usage["prompt_tokens"] / 1_000_000 * self.input_price_per_million
                + self.usage["completion_tokens"] / 1_000_000 * self.output_price_per_million,
                6,
            )
            if self.max_cost_usd is not None and self.usage["actual_cost_usd"] >= self.max_cost_usd:
                self._cost_exceeded = True

    @property
    def pricing_configured(self) -> bool:
        return self.input_price_per_million is not None and self.output_price_per_million is not None

    def record_author(self, model: str):
        with self._lock:
            self._research_models.add(model)

    @property
    def research_models(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._research_models)

    def record_tool(self, name=None):
        """Record a tool invocation without exposing arguments or source data."""
        self.usage["tool_calls"] += 1
        if name:
            self.usage["last_tool"] = name

    @property
    def cancelled(self):
        return self._cancel.is_set()

    def start_budget(self, seconds: float | None):
        # Start at execution, not at queue admission; never renew a running budget.
        # ``None`` means no whole-research deadline. The model request timeout,
        # round limit, and tool limit still provide bounded work in that mode.
        if self._started is None:
            self._started = time.monotonic()
            self.time_budget = None if seconds is None or seconds <= 0 else float(seconds)
            self._deadline = (
                None if self.time_budget is None else self._started + self.time_budget
            )

    @property
    def expired(self):
        if not self._sealed and not self.cancelled and self._deadline is not None:
            self._expired |= time.monotonic() >= self._deadline
        return self._expired

    @property
    def cost_exceeded(self):
        return self._cost_exceeded

    @property
    def remaining(self):
        return max(0.0, self._deadline - time.monotonic()) if self._deadline is not None else None

    def cancel(self):
        with self._lock:
            if self._sealed or self.expired:
                return False
            self._cancel.set()
            return True

    def checkpoint(self):
        if self.cancelled:
            raise ResearchCancelled()
        if self.expired:
            raise ResearchDeadlineExceeded()
        if self.cost_exceeded:
            raise ResearchCostExceeded()

    def wait(self, seconds):
        self.checkpoint()
        remaining = self.remaining
        duration = min(seconds, remaining) if remaining is not None else seconds
        if self._cancel.wait(duration):
            raise ResearchCancelled()
        self.checkpoint()

    def report(self, stage, message, **details):
        event = {"stage": stage, "message": message, **details}
        self.event_count += 1
        self.events.append(
            {
                **{key: value for key, value in event.items() if key != "preview"},
                "elapsed_ms": round((time.monotonic() - self._started) * 1000)
                if self._started is not None
                else 0,
            }
        )
        self.events = self.events[-100:]
        self._publish(event)

    def finalize(self, answer):
        # The result commit is the cancellation boundary. A later cancel is a no-op.
        with self._lock:
            expired = self.expired
            self._sealed = True
            if self.cancelled:
                answer.status = "partial" if answer.answer else "abstained"
                if CANCELLED_NOTE not in answer.gaps:
                    answer.gaps.append(CANCELLED_NOTE)
                answer.trace.append({"stage": "cancelled", "accepted_claims": len(answer.answer)})
            elif expired:
                answer.status = "partial" if answer.answer else "abstained"
                if DEADLINE_NOTE not in answer.gaps:
                    answer.gaps.append(DEADLINE_NOTE)
                answer.trace.append(
                    {
                        "stage": "deadline",
                        "accepted_claims": len(answer.answer),
                        "time_budget_seconds": self.time_budget,
                    }
                )
            elif self.cost_exceeded:
                answer.status = "partial" if answer.answer else "abstained"
                if COST_NOTE not in answer.gaps:
                    answer.gaps.append(COST_NOTE)
                answer.trace.append(
                    {
                        "stage": "cost_budget",
                        "accepted_claims": len(answer.answer),
                        "max_cost_usd": self.max_cost_usd,
                        "actual_cost_usd": self.usage.get("actual_cost_usd"),
                    }
                )
        return answer


_current: ContextVar[ResearchControl | None] = ContextVar("research_control", default=None)
_refresh_deadline: ContextVar[float | None] = ContextVar("refresh_deadline", default=None)


@contextmanager
def refresh_scope(seconds):
    deadline = time.monotonic() + max(0.0, seconds)
    previous = _refresh_deadline.get()
    token = _refresh_deadline.set(min(deadline, previous) if previous is not None else deadline)
    try:
        checkpoint()
        yield
        checkpoint()
    finally:
        _refresh_deadline.reset(token)


def refresh_remaining():
    deadline = _refresh_deadline.get()
    return max(0.0, deadline - time.monotonic()) if deadline is not None else None


@contextmanager
def research_scope(control):
    token = _current.set(control)
    try:
        yield
    finally:
        _current.reset(token)


def current_control():
    return _current.get()


def cancelled():
    control = current_control()
    return bool(control and control.cancelled)


def expired():
    control = current_control()
    return bool(control and control.expired)


def stopped():
    control = current_control()
    return cancelled() or expired() or bool(control and control.cost_exceeded)


def bounded_timeout(seconds):
    checkpoint()
    control = current_control()
    remaining = control.remaining if control else None
    refresh = refresh_remaining()
    return min([seconds] + [r for r in (remaining, refresh) if r is not None])


def checkpoint():
    control = current_control()
    if control:
        control.checkpoint()
    if refresh_remaining() == 0:
        raise RefreshBudgetExceeded()


def progress(stage, message, **details):
    control = current_control()
    if control:
        control.report(stage, message, **details)


def wait(seconds):
    checkpoint()
    refresh = refresh_remaining()
    duration = min(seconds, refresh) if refresh is not None else seconds
    control = current_control()
    if control:
        control.wait(duration)
    else:
        time.sleep(duration)
    checkpoint()


def run_step(stage, message, function, *args, **kwargs):
    checkpoint()
    progress(stage, message)
    try:
        result = function(*args, **kwargs)
        checkpoint()
    except Exception as exc:
        progress(
            "step_stopped" if isinstance(exc, ResearchStopped) else "step_error",
            "当前研究步骤未完成。",
            source_stage=stage,
            error_type=type(exc).__name__,
        )
        raise
    return result


@contextmanager
def cancellable_lock(lock):
    if current_control() is None and refresh_remaining() is None:
        with lock:
            yield
        return
    checkpoint()
    while not lock.acquire(timeout=bounded_timeout(0.25)):
        progress("source_wait", "同一公司的资料正在由另一项任务准备，等待完成；可取消。")
        checkpoint()
    try:
        checkpoint()
        yield
    finally:
        lock.release()
