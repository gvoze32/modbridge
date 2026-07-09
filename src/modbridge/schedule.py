"""Update schedule window ("only update between 04:00 and 05:00")."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time

_WINDOW_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$")


@dataclass(frozen=True, slots=True)
class ScheduleWindow:
    start: time
    end: time

    @classmethod
    def parse(cls, spec: str) -> ScheduleWindow:
        match = _WINDOW_RE.match(spec.strip())
        if not match:
            raise ValueError(
                f"Invalid schedule window {spec!r}: expected 'HH:MM-HH:MM', e.g. '04:00-05:00'"
            )
        h1, m1, h2, m2 = (int(g) for g in match.groups())
        try:
            start, end = time(h1, m1), time(h2, m2)
        except ValueError as exc:
            raise ValueError(f"Invalid schedule window {spec!r}: {exc}") from exc
        if start == end:
            raise ValueError(f"Invalid schedule window {spec!r}: start and end are equal")
        return cls(start=start, end=end)

    def contains(self, now: datetime) -> bool:
        t = now.time()
        if self.start < self.end:
            return self.start <= t < self.end
        # Overnight window, e.g. 22:00-02:00.
        return t >= self.start or t < self.end

    def __str__(self) -> str:
        return f"{self.start:%H:%M}-{self.end:%H:%M}"
