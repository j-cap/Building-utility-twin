"""Multi-apartment water aggregation and shared DHW storage for Experiment 3."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import json
import math
from pathlib import Path
from typing import Any

from .domestic_hot_water import (
    Experiment1Config,
    FixtureDemandResult,
    generate_fixture_demand,
)
from .meters import IdealCumulativeMeter


@dataclass(frozen=True, slots=True)
class ApartmentSpec:
    apartment_id: str
    occupants: int
    demand_scale: float
    seed: int

    def __post_init__(self) -> None:
        if not self.apartment_id.strip():
            raise ValueError("apartment_id must not be empty")
        if self.occupants <= 0:
            raise ValueError("apartment occupants must be positive")
        if self.demand_scale <= 0.0:
            raise ValueError("apartment demand scale must be positive")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ApartmentSpec":
        return cls(
            apartment_id=str(payload["apartment_id"]),
            occupants=int(payload["occupants"]),
            demand_scale=float(payload["demand_scale"]),
            seed=int(payload["seed"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "apartment_id": self.apartment_id,
            "occupants": self.occupants,
            "demand_scale": self.demand_scale,
            "seed": self.seed,
        }


@dataclass(frozen=True, slots=True)
class SharedTankConfig:
    tank_volume_l: float
    initial_temperature_c: float
    thermostat_lower_c: float
    thermostat_upper_c: float
    minimum_service_temperature_c: float
    ambient_temperature_c: float
    standing_loss_coefficient_w_k: float
    boiler_max_thermal_power_kw: float
    boiler_efficiency: float

    def __post_init__(self) -> None:
        if self.tank_volume_l <= 0.0:
            raise ValueError("tank volume must be positive")
        if not (
            self.ambient_temperature_c
            < self.minimum_service_temperature_c
            <= self.thermostat_lower_c
            < self.thermostat_upper_c
        ):
            raise ValueError("tank temperatures are not ordered consistently")
        if not (
            self.thermostat_lower_c
            <= self.initial_temperature_c
            <= self.thermostat_upper_c
        ):
            raise ValueError("initial temperature must lie in the thermostat band")
        if self.standing_loss_coefficient_w_k < 0.0:
            raise ValueError("standing loss coefficient must be non-negative")
        if self.boiler_max_thermal_power_kw <= 0.0:
            raise ValueError("boiler maximum power must be positive")
        if not 0.0 < self.boiler_efficiency <= 1.0:
            raise ValueError("boiler efficiency must lie in (0, 1]")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SharedTankConfig":
        return cls(
            tank_volume_l=float(payload["tank_volume_l"]),
            initial_temperature_c=float(payload["initial_temperature_c"]),
            thermostat_lower_c=float(payload["thermostat_lower_c"]),
            thermostat_upper_c=float(payload["thermostat_upper_c"]),
            minimum_service_temperature_c=float(
                payload["minimum_service_temperature_c"]
            ),
            ambient_temperature_c=float(payload["ambient_temperature_c"]),
            standing_loss_coefficient_w_k=float(
                payload["standing_loss_coefficient_w_k"]
            ),
            boiler_max_thermal_power_kw=float(
                payload["boiler_max_thermal_power_kw"]
            ),
            boiler_efficiency=float(payload["boiler_efficiency"]),
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "tank_volume_l": self.tank_volume_l,
            "initial_temperature_c": self.initial_temperature_c,
            "thermostat_lower_c": self.thermostat_lower_c,
            "thermostat_upper_c": self.thermostat_upper_c,
            "minimum_service_temperature_c": self.minimum_service_temperature_c,
            "ambient_temperature_c": self.ambient_temperature_c,
            "standing_loss_coefficient_w_k": (
                self.standing_loss_coefficient_w_k
            ),
            "boiler_max_thermal_power_kw": self.boiler_max_thermal_power_kw,
            "boiler_efficiency": self.boiler_efficiency,
        }


@dataclass(frozen=True, slots=True)
class Experiment3Config:
    experiment_id: str
    physical_template: Experiment1Config
    apartments: tuple[ApartmentSpec, ...]
    shared_tank: SharedTankConfig
    asset_ids: dict[str, str]
    aggregation_tolerance_m3: float
    energy_tolerance_j: float

    def __post_init__(self) -> None:
        if not self.experiment_id.strip():
            raise ValueError("experiment_id must not be empty")
        if len(self.apartments) < 2:
            raise ValueError("Experiment 3 requires at least two apartments")
        apartment_ids = [item.apartment_id for item in self.apartments]
        if len(set(apartment_ids)) != len(apartment_ids):
            raise ValueError("apartment identifiers must be unique")
        seeds = [item.seed for item in self.apartments]
        if len(set(seeds)) != len(seeds):
            raise ValueError("apartment seeds must be unique")
        if self.aggregation_tolerance_m3 <= 0.0:
            raise ValueError("aggregation tolerance must be positive")
        if self.energy_tolerance_j <= 0.0:
            raise ValueError("energy tolerance must be positive")
        if set(self.asset_ids) != {"shared_tank", "shared_boiler"}:
            raise ValueError("asset_ids must define shared_tank and shared_boiler")
        if any(not value.strip() for value in self.asset_ids.values()):
            raise ValueError("shared asset identifiers must not be empty")
        if len(set(self.asset_ids.values())) != len(self.asset_ids):
            raise ValueError("shared asset identifiers must be unique")
        cold_c = self.physical_template.cold_supply_temperature_c
        if self.shared_tank.ambient_temperature_c <= cold_c:
            raise ValueError("ambient temperature must exceed cold inlet temperature")

    @classmethod
    def from_json_file(cls, path: str | Path) -> "Experiment3Config":
        path = Path(path)
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        physical_path = path.parent / str(payload["physical_template_config"])
        return cls(
            experiment_id=str(payload["experiment_id"]),
            physical_template=Experiment1Config.from_json_file(physical_path),
            apartments=tuple(
                ApartmentSpec.from_dict(item) for item in payload["apartments"]
            ),
            shared_tank=SharedTankConfig.from_dict(payload["shared_tank"]),
            asset_ids={
                str(key): str(value) for key, value in payload["asset_ids"].items()
            },
            aggregation_tolerance_m3=float(payload["aggregation_tolerance_m3"]),
            energy_tolerance_j=float(payload["energy_tolerance_j"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "physical_template_configuration": self.physical_template.to_dict(),
            "apartments": [item.to_dict() for item in self.apartments],
            "shared_tank": self.shared_tank.to_dict(),
            "asset_ids": dict(self.asset_ids),
            "aggregation_tolerance_m3": self.aggregation_tolerance_m3,
            "energy_tolerance_j": self.energy_tolerance_j,
        }


@dataclass(frozen=True, slots=True)
class ApartmentSimulation:
    spec: ApartmentSpec
    demand: FixtureDemandResult


@dataclass(frozen=True, slots=True)
class SharedTankResult:
    temperature_k: tuple[float, ...]
    stored_energy_j: tuple[float, ...]
    boiler_output_power_w: tuple[float, ...]
    boiler_input_power_w: tuple[float, ...]
    dhw_output_power_w: tuple[float, ...]
    standing_loss_power_w: tuple[float, ...]
    heater_enabled: tuple[bool, ...]
    boiler_output_energy_j: tuple[float, ...]
    boiler_input_energy_j: tuple[float, ...]
    dhw_output_energy_j: tuple[float, ...]
    standing_loss_energy_j: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class BuildingSimulationResult:
    apartments: tuple[ApartmentSimulation, ...]
    timestamps: tuple[datetime, ...]
    total_flow_m3_s: tuple[float, ...]
    cold_flow_m3_s: tuple[float, ...]
    hot_flow_m3_s: tuple[float, ...]
    total_volume_m3: tuple[float, ...]
    cold_volume_m3: tuple[float, ...]
    hot_volume_m3: tuple[float, ...]
    shared_tank: SharedTankResult


def _apartment_config(
    template: Experiment1Config, spec: ApartmentSpec
) -> Experiment1Config:
    return replace(
        template,
        experiment_id=f"{template.experiment_id}_{spec.apartment_id}",
        seed=spec.seed,
        occupants=spec.occupants,
        target_total_l_per_person_day=(
            template.target_total_l_per_person_day * spec.demand_scale
        ),
        target_hot_l_per_person_day=(
            template.target_hot_l_per_person_day * spec.demand_scale
        ),
    )


def _sum_series(series: tuple[tuple[float, ...], ...]) -> tuple[float, ...]:
    return tuple(sum(values) for values in zip(*series))


def _simulate_shared_tank(
    config: Experiment3Config, hot_flow_m3_s: tuple[float, ...]
) -> SharedTankResult:
    physical = config.physical_template
    tank = config.shared_tank
    step_seconds = physical.step_seconds
    cold_k = physical.cold_supply_temperature_c + 273.15
    ambient_k = tank.ambient_temperature_c + 273.15
    lower_k = tank.thermostat_lower_c + 273.15
    upper_k = tank.thermostat_upper_c + 273.15
    capacity_j_k = (
        physical.water_density_kg_m3
        * physical.water_heat_capacity_j_kg_k
        * tank.tank_volume_l
        / 1000.0
    )
    maximum_stored_energy_j = capacity_j_k * (upper_k - cold_k)
    initial_temperature_k = tank.initial_temperature_c + 273.15
    stored_energy = [capacity_j_k * (initial_temperature_k - cold_k)]
    temperatures = [initial_temperature_k]
    boiler_output_power: list[float] = []
    boiler_input_power: list[float] = []
    dhw_output_power: list[float] = []
    standing_loss_power: list[float] = []
    heater_enabled: list[bool] = []
    heater_on = initial_temperature_k <= lower_k
    maximum_power_w = tank.boiler_max_thermal_power_kw * 1000.0

    for hot_flow in hot_flow_m3_s:
        temperature_k = temperatures[-1]
        if heater_on and temperature_k >= upper_k - 1e-12:
            heater_on = False
        elif not heater_on and temperature_k <= lower_k + 1e-12:
            heater_on = True

        dhw_power_w = (
            physical.water_density_kg_m3
            * physical.water_heat_capacity_j_kg_k
            * hot_flow
            * max(temperature_k - cold_k, 0.0)
        )
        loss_power_w = tank.standing_loss_coefficient_w_k * max(
            temperature_k - ambient_k, 0.0
        )
        requested_boiler_power_w = maximum_power_w if heater_on else 0.0
        maximum_admissible_power_w = (
            (maximum_stored_energy_j - stored_energy[-1]) / step_seconds
            + dhw_power_w
            + loss_power_w
        )
        output_power_w = min(
            requested_boiler_power_w,
            max(maximum_admissible_power_w, 0.0),
        )
        next_energy_j = stored_energy[-1] + (
            output_power_w - dhw_power_w - loss_power_w
        ) * step_seconds
        if next_energy_j < -config.energy_tolerance_j:
            raise RuntimeError("shared DHW tank depleted below the cold baseline")
        if next_energy_j > maximum_stored_energy_j + config.energy_tolerance_j:
            raise RuntimeError("shared DHW tank exceeded its upper energy bound")
        next_energy_j = min(max(next_energy_j, 0.0), maximum_stored_energy_j)

        boiler_output_power.append(output_power_w)
        boiler_input_power.append(output_power_w / tank.boiler_efficiency)
        dhw_output_power.append(dhw_power_w)
        standing_loss_power.append(loss_power_w)
        heater_enabled.append(output_power_w > 0.0)
        stored_energy.append(next_energy_j)
        temperatures.append(cold_k + next_energy_j / capacity_j_k)

    meter = IdealCumulativeMeter()
    return SharedTankResult(
        temperature_k=tuple(temperatures),
        stored_energy_j=tuple(stored_energy),
        boiler_output_power_w=tuple(boiler_output_power),
        boiler_input_power_w=tuple(boiler_input_power),
        dhw_output_power_w=tuple(dhw_output_power),
        standing_loss_power_w=tuple(standing_loss_power),
        heater_enabled=tuple(heater_enabled),
        boiler_output_energy_j=meter.readings(
            boiler_output_power, step_seconds
        ),
        boiler_input_energy_j=meter.readings(boiler_input_power, step_seconds),
        dhw_output_energy_j=meter.readings(dhw_output_power, step_seconds),
        standing_loss_energy_j=meter.readings(
            standing_loss_power, step_seconds
        ),
    )


def simulate_building(config: Experiment3Config) -> BuildingSimulationResult:
    apartment_results = tuple(
        ApartmentSimulation(
            spec=spec,
            demand=generate_fixture_demand(
                _apartment_config(config.physical_template, spec)
            ),
        )
        for spec in config.apartments
    )
    total_flow = _sum_series(
        tuple(item.demand.total_flow_m3_s for item in apartment_results)
    )
    cold_flow = _sum_series(
        tuple(item.demand.cold_flow_m3_s for item in apartment_results)
    )
    hot_flow = _sum_series(
        tuple(item.demand.hot_flow_m3_s for item in apartment_results)
    )
    meter = IdealCumulativeMeter()
    step_seconds = config.physical_template.step_seconds
    shared_tank = _simulate_shared_tank(config, hot_flow)
    return BuildingSimulationResult(
        apartments=apartment_results,
        timestamps=apartment_results[0].demand.timestamps,
        total_flow_m3_s=total_flow,
        cold_flow_m3_s=cold_flow,
        hot_flow_m3_s=hot_flow,
        total_volume_m3=meter.readings(total_flow, step_seconds),
        cold_volume_m3=meter.readings(cold_flow, step_seconds),
        hot_volume_m3=meter.readings(hot_flow, step_seconds),
        shared_tank=shared_tank,
    )
