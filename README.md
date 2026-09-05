# Building Utility Twin

Simulation-first development of a building utility digital twin. The project
starts with a deliberately small vertical slice and preserves the same
interfaces when simulated devices are replaced by real meters.

The technical design, experiment sequence, and findings are documented in
[`report/main.tex`](report/main.tex). Iterations A through E are implemented as
reproducible, end-to-end vertical slices.

## Experiment 0

The experiment simulates one lossless water pipe for one UTC day at a one-minute
resolution. A seeded event model generates demand, and a virtual cumulative
meter integrates the outlet flow. Both simulated flow and meter-register values
use the same [canonical measurement schema](schemas/measurement.schema.json)
intended for later physical adapters.

Run the complete verification and regenerate the committed results from the
repository root:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python scripts/run_experiment_0.py \
  --config config/experiment_0.json \
  --output results/experiment_0
PYTHONPATH=src python scripts/run_experiment_1.py \
  --config config/experiment_1.json \
  --output results/experiment_1
PYTHONPATH=src python scripts/run_experiment_2.py \
  --config config/experiment_2.json \
  --output results/experiment_2
PYTHONPATH=src python scripts/run_experiment_3.py \
  --config config/experiment_3.json \
  --output results/experiment_3
PYTHONPATH=src python scripts/run_experiment_4.py \
  --config config/experiment_4.json \
  --output results/experiment_4
cd report && pdflatex -interaction=nonstopmode -halt-on-error main.tex
```

The runner writes:

- `measurements.jsonl`: canonical, file-backed measurements;
- `timeseries.csv`: analysis-friendly physical truth and meter state;
- `summary.json`: configuration, conservation results, and content hashes;
- `experiment_0_one_pipe_day.png`: the generated result figure.

The compact summary and figure are versioned. The larger JSONL and CSV streams
are deterministic but ignored by git; running the command above recreates them.

## Experiment 1

Experiment 1 replaces the abstract demand process with fixture-level events
calibrated to a three-person reference day: 130 L total water and 45 L domestic
hot water per person. It splits building inlet flow into cold and hot branches,
couples the hot branch to an ideal central boiler, and persists water,
temperature, thermal-power, and cumulative-energy measurements through the same
canonical contract and file-backed store.

Its runner additionally writes `events.csv`, which makes every toilet, shower,
faucet, laundry, dishwasher, and cleaning draw auditable. Water and energy
balances are verified independently. As in Experiment 0, the compact summary
and figure are versioned while the deterministic raw streams are regenerated.

## Experiment 2

Experiment 2 preserves Experiment 1's physical reference day and inserts an
imperfect device-and-communications layer between the total-water meter and
storage. The reference scenario applies 0.1 L register resolution, noisy
positive increments, five-minute readout, stochastic packet loss, delays of up
to 15 minutes, a 100 L rollover modulus, and one declared reset.

The reconciler sorts packets by observation time, bridges missing samples,
unwraps rollovers, and uses the reset event's pre-reset register to retain the
consumption history. Raw and reconciled values remain canonical cumulative
volume measurements. The runner additionally writes `telemetry.csv`, which
audits every scheduled readout, loss, reception time, delay, device event,
quality flag, and correction.

## Experiment 3

Experiment 3 composes four independently seeded apartments with different
occupancy and demand scales. Apartment total, cold, and hot branches aggregate
into exact building-level flows and cumulative registers. The combined hot
branch is supplied by a 300 L well-mixed storage tank with thermostat
hysteresis, a 30 kW boiler limit, fixed conversion efficiency, and
temperature-dependent standing loss.

The runner persists apartment and building measurements through the same
canonical contract and writes long-form apartment/event ledgers. Its energy
balance explicitly includes delivered hot-water energy, standing loss, boiler
thermal output, plant input, and the change in stored tank energy.

## Experiment 4

Experiment 4 composes the multi-apartment building with finite cumulative
registers, sparse five-minute readout, packet loss, packet delay, rollover, and
a declared building-meter reset. Its reference day injects three controlled
anomalies: a 0.30 L/min unmetered leak, 25% under-registration at one apartment
meter, and fourfold tank standing loss during separate four-hour windows.

The analytics layer reconciles the device registers, evaluates building-minus-
apartment water balance, and checks the shared-tank state equation against the
nominal standing-loss model. It writes auditable water/thermal windows and alarm
ledgers. The aggregate water residual detects missing volume but cannot alone
identify whether its source is a leak or an under-registering apartment meter;
that limitation is an explicit Experiment 4 result.

## Repository structure

```text
config/       Versioned experiment inputs
schemas/      Language-neutral interface contracts
scripts/      Executable experiment entry points
src/          Simulation, contracts, storage, and analysis code
tests/        Contract, storage, conservation, and reproducibility tests
results/      Reproducible experiment artifacts
report/       LaTeX design and findings report
```

## Roadmap

1. **Iteration A / Experiment 0 (complete):** one water pipe, one simulated day,
   a virtual cumulative meter, canonical measurement contracts, and file-backed
   storage.
2. **Iteration B / Experiment 1 (complete):** fixture-level cold/hot-water demand,
   central-boiler energy accounting, and water/energy conservation.
3. **Iteration C / Experiment 2 (complete):** imperfect sensing, delayed or
   missing readings, rollover/reset handling, and auditable reconciliation.
4. **Iteration D / Experiment 3 (complete):** apartments, exact building-level
   aggregation, and a dynamic shared DHW store with boiler and standing losses.
5. **Iteration E / Experiment 4 (complete):** building-wide imperfect telemetry,
   water-balance anomaly detection, and shared-storage loss diagnostics.
6. **Iteration F / Experiment 5 (complete):** replace the simulated source at
   the ingestion boundary with a frozen, representative vendor-format meter
   export while preserving canonical contracts, storage, and water-balance
   analytics. The fixture tests adapter substitution; it is not field evidence.

Run the adapter experiment with:

```bash
python scripts/run_experiment_5.py \
  --config config/experiment_5.json \
  --output results/experiment_5
```

The semicolon-delimited source fixture uses German column names, local
Europe/Vienna timestamps, decimal-comma litre registers, and vendor quality
codes. `scripts/build_experiment_5_fixture.py` documents its controlled
provenance; the experiment runner consumes only the frozen CSV and does not
invoke any simulator.
