"""Exponential backoff 30 s -> 8 min for 5xx/timeouts (PRD §5.1 producer
behavior). Gaps are legitimate data; a crashed producer is an outage — so the
policy is always sleep-and-continue, never raise."""

from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)


class Backoff:
    def __init__(self, initial_s: float = 30.0, max_s: float = 480.0) -> None:
        self._initial = initial_s
        self._max = max_s
        self._current = initial_s

    def sleep(self, reason: str) -> None:
        """Sleep the current interval, then double it toward the cap."""
        log.warning("backoff %.0fs: %s", self._current, reason)
        time.sleep(self._current)
        self._current = min(self._current * 2, self._max)

    def reset(self) -> None:
        self._current = self._initial
