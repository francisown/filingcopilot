"""Optional background scan loop for a long-running local service.

The loop is opt-in. A single user or CLI scan remains the default, so a fresh
workspace never spends model budget or hammers SEC without an explicit setting.
"""

from __future__ import annotations

import threading
from datetime import date, datetime

from .models import DigestRequest, ScanRequest


class MonitoringScheduler:
    def __init__(
        self,
        service,
        interval_minutes: int,
        *,
        digest_enabled: bool = False,
        digest_hour: int = 7,
        digest_channel: str = "local",
    ):
        self.service = service
        self.interval = max(0, int(interval_minutes)) * 60
        self.digest_enabled = digest_enabled
        self.digest_hour = max(0, min(23, int(digest_hour)))
        self.digest_channel = digest_channel
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error: str | None = None
        self.last_digest_date: str | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, name="filingcopilot-monitor", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        # Check at least once a minute so the daily digest can fire close to the
        # configured hour even when the scan interval is longer.
        next_scan = (
            datetime.now().timestamp() + self.interval if self.interval else None
        )
        while not self._stop.wait(30):
            now = datetime.now()
            if (
                self.digest_enabled
                and now.hour >= self.digest_hour
                and self.last_digest_date != date.today().isoformat()
            ):
                try:
                    result = self.service.monitoring.deliver_digest(
                        DigestRequest(channel=self.digest_channel)
                    )
                    if result.get("status") in {"sent", "already_sent"}:
                        self.last_digest_date = date.today().isoformat()
                        self.last_error = None
                    else:
                        self.last_error = result.get("error") or result.get("status")
                except Exception as exc:
                    self.last_error = f"{type(exc).__name__}: {exc}"
            if (
                next_scan is not None
                and datetime.now().timestamp() >= next_scan
            ):
                try:
                    self.service.monitoring.scan(
                        ScanRequest(bootstrap=True, use_model=None)
                    )
                    self.last_error = None
                except Exception as exc:
                    self.last_error = f"{type(exc).__name__}: {exc}"
                next_scan = datetime.now().timestamp() + self.interval

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
