"""Naive-datetime helpers shared by the world server and the runner."""

from __future__ import annotations

from datetime import datetime

ISO = "%Y-%m-%dT%H:%M:%S"


def parse_dt(value: str) -> datetime:
    """Accept a full ISO datetime or a bare date.

    Models pass ``2026-08-10`` for a day boundary at least as often as
    ``2026-08-10T00:00:00``; rejecting the short form cost a wasted tool call and produced a
    harness-shaped error in the first live run.
    """
    text = str(value).strip().replace(" ", "T")
    for fmt in (ISO, "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[: len(datetime(2026, 1, 1).strftime(fmt))], fmt)
        except ValueError:
            continue
    raise ValueError(f"unparseable datetime: {value!r}")


def to_ts(moment: datetime) -> str:
    """Slack-style message identifier: epoch seconds with a microsecond tail."""
    return f"{moment.timestamp():.6f}"


def human_time(moment: datetime) -> str:
    return moment.strftime("%a %d %b %H:%M")

