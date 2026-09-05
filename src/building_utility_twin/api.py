"""Read API for the Building Utility Twin pilot backend."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from .persistent_backend import PersistentBackend


class IssueReviewUpdate(BaseModel):
    status: Literal["open", "investigating", "resolved"] | None = None
    operator_note: str | None = Field(default=None, max_length=2000)


def create_app(database_url: str) -> FastAPI:
    backend = PersistentBackend(database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        backend.close()

    app = FastAPI(
        title="Building Utility Twin API",
        version="0.8.0",
        description="Operator API over canonical utility measurements.",
        lifespan=lifespan,
    )
    app.state.backend = backend

    @app.get("/health")
    def health() -> dict[str, object]:
        return {"status": "ok", "schema_version": backend.portfolio_summary()["schema_version"]}

    @app.get("/api/v1/portfolio")
    def portfolio() -> dict[str, object]:
        return backend.portfolio_summary()

    @app.get("/api/v1/buildings")
    def buildings() -> list[dict[str, object]]:
        return backend.list_buildings()

    @app.get("/api/v1/buildings/{building_id}/meters")
    def building_meters(building_id: str) -> list[dict[str, object]]:
        try:
            return backend.building_meters(building_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="building not found") from error

    @app.get("/api/v1/portfolio/overview")
    def portfolio_overview() -> dict[str, object]:
        return backend.portfolio_overview()

    @app.get("/api/v1/buildings/{building_id}/water-balance")
    def building_water_balance(building_id: str) -> dict[str, object]:
        try:
            return backend.building_balance(building_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="building not found") from error

    @app.get("/api/v1/meters/{meter_id}/measurements")
    def meter_measurements(
        meter_id: str,
        start: str | None = None,
        end: str | None = None,
        limit: int = Query(default=1000, ge=1, le=10_000),
    ) -> list[dict[str, object]]:
        try:
            return backend.meter_measurements(
                meter_id, start=start, end=end, limit=limit
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="meter not found") from error

    @app.get("/api/v1/meters/{meter_id}/profile")
    def meter_profile(meter_id: str) -> dict[str, object]:
        try:
            return backend.meter_profile(meter_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="meter not found") from error

    @app.get("/api/v1/issues")
    def issues(
        status: Literal["open", "investigating", "resolved"] | None = None,
        building_id: str | None = None,
        include_runtime_fields: bool = True,
    ) -> list[dict[str, object]]:
        return backend.list_issues(
            status=status,
            building_id=building_id,
            include_runtime_fields=include_runtime_fields,
        )

    @app.patch("/api/v1/issues/{issue_id}")
    def update_issue(
        issue_id: str, update: IssueReviewUpdate
    ) -> dict[str, object]:
        if update.status is None and update.operator_note is None:
            raise HTTPException(
                status_code=422,
                detail="status or operator_note must be supplied",
            )
        try:
            return backend.update_issue(
                issue_id,
                status=update.status,
                operator_note=update.operator_note,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="issue not found") from error

    @app.get("/api/v1/imports")
    def imports(include_runtime_fields: bool = True) -> list[dict[str, object]]:
        return backend.list_imports(
            include_runtime_fields=include_runtime_fields
        )

    return app


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("results/pilot_p1/backend.sqlite3"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    import uvicorn

    app = create_app(f"sqlite:///{arguments.database.resolve()}")
    uvicorn.run(app, host=arguments.host, port=arguments.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
