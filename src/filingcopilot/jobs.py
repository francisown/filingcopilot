"""Bounded in-process browser jobs; result packages remain in the corpus."""

from __future__ import annotations

import re
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

from .execution import ResearchControl


class ResearchJobConflict(ValueError):
    """A retained request key or server session cannot safely start this request."""


class ResearchJobs:
    TERMINAL = {"completed", "cancelled", "timed_out", "failed"}

    def __init__(self, service, workers=2, capacity=4, history=64):
        self.service = service
        self.capacity = capacity
        self.history = history
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="research")
        self.lock = threading.RLock()
        self.jobs = OrderedDict()
        self.session = uuid.uuid4().hex

    @staticmethod
    def validate_key(key):
        if key is not None and not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", key):
            raise ValueError("研究请求标识格式无效。")

    def check_session(self, session):
        if session is not None and session != self.session:
            raise ResearchJobConflict(
                "研究服务已重启，无法继续确认之前的启动请求。请先查看研究记录，再重新研究。"
            )

    def start(self, request, request_key=None, session=None):
        self.validate_key(request_key)
        self.check_session(session)
        payload = request.model_dump(mode="json")
        # Check the key before planning or queue limits: reconnecting must not start work.
        with self.lock:
            existing = self._key_job(request_key)
            if existing:
                if existing["request"] != payload:
                    raise ResearchJobConflict("同一研究请求标识不能用于不同的问题。")
                return self.get(existing["id"])
        # Validate before queuing; no HTTP or model calls are needed for this plan.
        plan = self.service.research_plan(request)
        if not plan["ready"]:
            raise ValueError(plan["message"])
        with self.lock:
            # Concurrent starts may have finished planning with the same key.
            existing = self._key_job(request_key)
            if existing:
                if existing["request"] != payload:
                    raise ResearchJobConflict("同一研究请求标识不能用于不同的问题。")
                return self.get(existing["id"])
            active = sum(j["status"] not in self.TERMINAL for j in self.jobs.values())
            if active >= self.capacity:
                raise RuntimeError("当前研究队列已满，请等待一项完成或取消后重试。")
            while len(self.jobs) >= self.history + self.capacity:
                old = next((k for k, j in self.jobs.items() if j["status"] in self.TERMINAL), None)
                if old is None:
                    break
                del self.jobs[old]
            job_id = "job-" + uuid.uuid4().hex
            job = {
                "id": job_id,
                "status": "queued",
                "started": time.monotonic(),
                "finished": None,
                "events": [],
                "result": None,
                "preview": None,
                "error": None,
                "request_key": request_key,
                "request": payload,
            }
            self.jobs[job_id] = job
            settings = getattr(self.service, "settings", None)
            job["control"] = ResearchControl(
                lambda event: self._publish(job_id, event),
                max_cost_usd=getattr(settings, "max_cost_usd", None),
                input_price_per_million=getattr(settings, "model_input_price_per_million", None),
                output_price_per_million=getattr(settings, "model_output_price_per_million", None),
            )
            self._publish(job_id, {"stage": "queued", "message": "研究已排队，等待执行。"})
            job["future"] = self.pool.submit(self._run, job_id, request)
            return self.get(job_id)

    def _key_job(self, key):
        return next((j for j in self.jobs.values() if key and j["request_key"] == key), None)

    def recover(self, request_key, session=None):
        self.validate_key(request_key)
        self.check_session(session)
        with self.lock:
            job = self._key_job(request_key)
            if job is None:
                raise ValueError(
                    "未找到这次研究的启动记录，可能尚未提交或进度记录已过期。"
                    "请先查看研究记录；重新连接只查找已有任务，不会再次提交。"
                )
            return self.get(job["id"])

    def _publish(self, job_id, event):
        with self.lock:
            job = self.jobs[job_id]
            event = dict(event)
            if "preview" in event:
                job["preview"] = deepcopy(event.pop("preview"))
            sequence = job["events"][-1]["sequence"] + 1 if job["events"] else 1
            job["events"].append({**event, "sequence": sequence, "elapsed_ms": self._elapsed(job)})
            job["events"] = job["events"][-100:]

    @staticmethod
    def _elapsed(job):
        return round(((job["finished"] or time.monotonic()) - job["started"]) * 1000)

    def _run(self, job_id, request):
        with self.lock:
            job = self.jobs[job_id]
            job["status"] = "cancelling" if job["control"].cancelled else "running"
        try:
            answer = self.service.research(request, control=job["control"])
            with self.lock:
                job["result"] = answer.model_dump(mode="json")
                job["status"] = (
                    "cancelled"
                    if job["control"].cancelled
                    else "timed_out"
                    if job["control"].expired
                    else "completed"
                )
        except Exception as exc:
            # Exception strings can contain provider requests, keys or headers.
            with self.lock:
                job["status"] = "failed"
                job["error"] = {
                    "type": type(exc).__name__,
                    "message": "研究未完成。已获取的资料仍保存在本地；请检查公司、知悉日期或连接后重试。",
                }
        finally:
            with self.lock:
                job["finished"] = time.monotonic()

    def get(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise ValueError("研究任务不存在或服务已重启。已完成结果可在研究记录中查看。")
            job = self.jobs[job_id]
            return {
                "id": job_id,
                "status": "timing_out"
                if job["status"] not in self.TERMINAL and job["control"].expired
                else job["status"],
                "elapsed_ms": self._elapsed(job),
                "events": list(job["events"]),
                "result": job["result"],
                "preview": deepcopy(job["preview"]),
                "usage": dict(job["control"].usage),
                "error": job["error"],
                "time_budget_seconds": job["control"].time_budget,
                "remaining_seconds": job["control"].remaining
                if job["status"] not in self.TERMINAL
                else None,
            }

    def cancel(self, job_id):
        with self.lock:
            self.get(job_id)
            job = self.jobs[job_id]
            if job["status"] in self.TERMINAL or not job["control"].cancel():
                return self.get(job_id)
            if job["future"].cancel():
                job["status"] = "cancelled"
                job["finished"] = time.monotonic()
                self._publish(job_id, {"stage": "cancelled", "message": "排队中的研究已取消。"})
            else:
                job["status"] = "cancelling"
                self._publish(
                    job_id,
                    {
                        "stage": "cancelling",
                        "message": "已请求取消，等待当前操作返回；随后停止新增调用并保存已核验结果。",
                    },
                )
            return self.get(job_id)

    def close(self):
        with self.lock:
            for job_id in list(self.jobs):
                self.cancel(job_id)
        self.pool.shutdown(wait=False, cancel_futures=True)
