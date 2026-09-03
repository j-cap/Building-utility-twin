"""Reusable ideal cumulative-register models."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable


@dataclass(frozen=True, slots=True)
class IdealCumulativeMeter:
    """Integrate piecewise-constant interval-average rates.

    The input can be any rate in ``quantity per second``. The output contains
    the initial register followed by one boundary reading per interval.
    """

    initial_register: float = 0.0

    def readings(
        self, interval_rates: Iterable[float], step_seconds: int
    ) -> tuple[float, ...]:
        if step_seconds <= 0:
            raise ValueError("step_seconds must be positive")
        if not math.isfinite(self.initial_register):
            raise ValueError("initial register must be finite")
        values = [float(self.initial_register)]
        for rate in interval_rates:
            rate = float(rate)
            if not math.isfinite(rate):
                raise ValueError("meter input rates must be finite")
            values.append(values[-1] + rate * step_seconds)
        return tuple(values)

