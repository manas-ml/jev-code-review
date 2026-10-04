"""Quiet hours: do not send non-urgent notifications at night (user's local time)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class QuietHours:
    start: time = time(22, 0)
    end: time = time(7, 0)
    tz: str = "UTC"

    def is_quiet(self, now: datetime) -> bool:
        local = now.astimezone(ZoneInfo(self.tz)).time()
        return self.start <= local < self.end


def should_send(urgent: bool, quiet: QuietHours, now: datetime) -> bool:
    if urgent:
        return True
    return not quiet.is_quiet(now)
