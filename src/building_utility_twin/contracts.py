"""Canonical, device-independent measurement contracts.

The contract is deliberately small but strict. Simulated and physical adapters
must emit the same fields and SI units before data enter the storage layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import json
import math
from typing import Any
from uuid import UUID, uuid5


SCHEMA_VERSION = "1.0"
MEASUREMENT_NAMESPACE = UUID("ef35bc8a-b27c-4c13-9e19-417e1ef2bc86")


class Quantity(str, Enum):
    """Physical quantities supported by Iteration A."""

    VOLUMETRIC_FLOW_RATE = "volumetric_flow_rate"
    CUMULATIVE_VOLUME = "cumulative_volume"


class Quality(str, Enum):
    """Minimal quality vocabulary shared by simulated and real sources."""

    GOOD = "good"
    SUSPECT = "suspect"
    BAD = "bad"


CANONICAL_UNITS = {
    Quantity.VOLUMETRIC_FLOW_RATE: "m3/s",
    Quantity.CUMULATIVE_VOLUME: "m3",
}


def _utc_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _isoformat_utc(value: datetime) -> str:
    return _utc_timestamp(value).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class Measurement:
    """A validated canonical observation.

    ``duration_seconds`` is required for interval-average flow measurements and
    absent for instantaneous cumulative-register readings. This makes the time
    semantics explicit enough to reproduce volume integration.
    """

    measurement_id: str
    asset_id: str
    channel: str
    quantity: Quantity
    timestamp: datetime
    value: float
    unit: str
    quality: Quality
    source: str
    duration_seconds: int | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        UUID(self.measurement_id)
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema version: {self.schema_version}")
        for field_name in ("asset_id", "channel", "source"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must not be empty")
        _utc_timestamp(self.timestamp)
        if not math.isfinite(self.value):
            raise ValueError("value must be finite")
        expected_unit = CANONICAL_UNITS[self.quantity]
        if self.unit != expected_unit:
            raise ValueError(
                f"{self.quantity.value} requires canonical unit {expected_unit!r}"
            )
        if self.quantity is Quantity.VOLUMETRIC_FLOW_RATE:
            if self.duration_seconds is None or self.duration_seconds <= 0:
                raise ValueError("flow measurements require a positive duration_seconds")
        elif self.duration_seconds is not None:
            raise ValueError("cumulative readings must not define duration_seconds")
        if self.quantity is Quantity.CUMULATIVE_VOLUME and self.value < 0.0:
            raise ValueError("cumulative volume must be non-negative")

    @classmethod
    def create(
        cls,
        *,
        asset_id: str,
        channel: str,
        quantity: Quantity,
        timestamp: datetime,
        value: float,
        quality: Quality = Quality.GOOD,
        source: str,
        duration_seconds: int | None = None,
    ) -> "Measurement":
        timestamp = _utc_timestamp(timestamp)
        identity = "|".join(
            (
                SCHEMA_VERSION,
                asset_id,
                channel,
                quantity.value,
                _isoformat_utc(timestamp),
                format(float(value), ".17g"),
                quality.value,
                source,
                "" if duration_seconds is None else str(duration_seconds),
            )
        )
        return cls(
            measurement_id=str(uuid5(MEASUREMENT_NAMESPACE, identity)),
            asset_id=asset_id,
            channel=channel,
            quantity=quantity,
            timestamp=timestamp,
            value=float(value),
            unit=CANONICAL_UNITS[quantity],
            quality=quality,
            source=source,
            duration_seconds=duration_seconds,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the ordered wire representation."""

        return {
            "schema_version": self.schema_version,
            "measurement_id": self.measurement_id,
            "asset_id": self.asset_id,
            "channel": self.channel,
            "quantity": self.quantity.value,
            "timestamp": _isoformat_utc(self.timestamp),
            "value": self.value,
            "unit": self.unit,
            "quality": self.quality.value,
            "source": self.source,
            "duration_seconds": self.duration_seconds,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), ensure_ascii=True)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Measurement":
        timestamp_text = str(payload["timestamp"])
        if timestamp_text.endswith("Z"):
            timestamp_text = timestamp_text[:-1] + "+00:00"
        return cls(
            schema_version=str(payload["schema_version"]),
            measurement_id=str(payload["measurement_id"]),
            asset_id=str(payload["asset_id"]),
            channel=str(payload["channel"]),
            quantity=Quantity(payload["quantity"]),
            timestamp=datetime.fromisoformat(timestamp_text),
            value=float(payload["value"]),
            unit=str(payload["unit"]),
            quality=Quality(payload["quality"]),
            source=str(payload["source"]),
            duration_seconds=(
                None
                if payload.get("duration_seconds") is None
                else int(payload["duration_seconds"])
            ),
        )

    @classmethod
    def from_json(cls, payload: str) -> "Measurement":
        return cls.from_dict(json.loads(payload))

