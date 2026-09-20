"""Time-range validation and chart bucketing for dashboard analytics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math


MAX_CUSTOM_DAYS = 366
BUCKET_CHOICES = (300, 900, 3600, 21600, 86400, 604800)


@dataclass(frozen=True)
class AnalyticsRange:
    name: str
    start: datetime
    end: datetime
    bucket_seconds: int

    @property
    def start_iso(self) -> str:
        return self.start.isoformat()

    @property
    def end_iso(self) -> str:
        return self.end.isoformat()

    def public(self) -> dict[str, str | int]:
        return {
            "name": self.name,
            "start": self.start_iso,
            "end": self.end_iso,
            "bucket_seconds": self.bucket_seconds,
            "timezone": "UTC",
        }


def _parse_datetime(value: str | None, field: str) -> datetime:
    if not value:
        raise ValueError(f"{field} is required for a custom range")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _bucket_for(duration_seconds: float) -> int:
    minimum = max(1, math.ceil(duration_seconds / 120))
    return next((choice for choice in BUCKET_CHOICES if choice >= minimum), BUCKET_CHOICES[-1])


def resolve_analytics_range(name: str, start: str | None = None, end: str | None = None,
                            *, now: datetime | None = None) -> AnalyticsRange:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if name == "today":
        range_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        bucket = 3600
    elif name == "24h":
        range_start = current - timedelta(hours=24)
        bucket = 3600
    elif name == "7d":
        range_start = current - timedelta(days=7)
        bucket = 21600
    elif name == "30d":
        range_start = current - timedelta(days=30)
        bucket = 86400
    elif name == "custom":
        range_start = _parse_datetime(start, "start")
        range_end = _parse_datetime(end, "end")
        if range_end > current + timedelta(minutes=5):
            raise ValueError("Custom range end cannot be in the future")
        if range_start >= range_end:
            raise ValueError("Custom range start must be before its end")
        if range_end - range_start > timedelta(days=MAX_CUSTOM_DAYS):
            raise ValueError(f"Custom ranges are limited to {MAX_CUSTOM_DAYS} days")
        return AnalyticsRange(name, range_start, range_end, _bucket_for((range_end - range_start).total_seconds()))
    else:
        raise ValueError("Range must be today, 24h, 7d, 30d, or custom")
    return AnalyticsRange(name, range_start, current, bucket)
