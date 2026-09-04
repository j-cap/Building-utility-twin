"""Building Utility Twin core package."""

from .building_system import (
    ApartmentSimulation,
    ApartmentSpec,
    BuildingSimulationResult,
    Experiment3Config,
    SharedTankConfig,
    SharedTankResult,
    simulate_building,
)
from .contracts import Measurement, Quality, Quantity
from .domestic_hot_water import (
    CentralBoiler,
    Experiment1Config,
    FixtureDemandResult,
    FixtureEvent,
    FixtureSpec,
    ThermalSimulationResult,
    generate_fixture_demand,
    simulate_domestic_hot_water,
)
from .meters import IdealCumulativeMeter
from .simulation import ExperimentConfig, SimulationResult, simulate_one_pipe_day
from .telemetry import (
    Experiment2Config,
    MeterObservation,
    ReconciledReading,
    TelemetryFaultConfig,
    TelemetrySimulationResult,
    reconcile_observations,
    simulate_imperfect_telemetry,
)

__all__ = [
    "ExperimentConfig",
    "Experiment1Config",
    "Experiment2Config",
    "Experiment3Config",
    "ApartmentSimulation",
    "ApartmentSpec",
    "BuildingSimulationResult",
    "CentralBoiler",
    "FixtureDemandResult",
    "FixtureEvent",
    "FixtureSpec",
    "IdealCumulativeMeter",
    "Measurement",
    "MeterObservation",
    "Quality",
    "Quantity",
    "ReconciledReading",
    "SimulationResult",
    "SharedTankConfig",
    "SharedTankResult",
    "ThermalSimulationResult",
    "TelemetryFaultConfig",
    "TelemetrySimulationResult",
    "generate_fixture_demand",
    "reconcile_observations",
    "simulate_imperfect_telemetry",
    "simulate_building",
    "simulate_domestic_hot_water",
    "simulate_one_pipe_day",
]
