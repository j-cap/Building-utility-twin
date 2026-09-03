"""File-backed persistence for canonical measurements."""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import tempfile
from typing import Iterable

from .contracts import Measurement, Quantity


class JsonLinesMeasurementStore:
    """Atomic JSON Lines store with deterministic ordering."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def replace(self, measurements: Iterable[Measurement]) -> int:
        ordered = sorted(
            measurements,
            key=lambda item: (item.timestamp, item.asset_id, item.channel),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            delete=False,
        )
        temporary_path = Path(handle.name)
        try:
            with handle:
                for measurement in ordered:
                    handle.write(measurement.to_json())
                    handle.write("\n")
            os.replace(temporary_path, self.path)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
        return len(ordered)

    def read_all(self) -> list[Measurement]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as handle:
            return [Measurement.from_json(line) for line in handle if line.strip()]

    def query(
        self,
        *,
        asset_id: str | None = None,
        quantity: Quantity | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Measurement]:
        result = []
        for measurement in self.read_all():
            if asset_id is not None and measurement.asset_id != asset_id:
                continue
            if quantity is not None and measurement.quantity is not quantity:
                continue
            if start is not None and measurement.timestamp < start:
                continue
            if end is not None and measurement.timestamp >= end:
                continue
            result.append(measurement)
        return result

