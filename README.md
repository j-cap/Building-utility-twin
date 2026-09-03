# Building Utility Twin

Simulation-first development of a building utility digital twin. The project
starts with a deliberately small vertical slice and preserves the same
interfaces when simulated devices are replaced by real meters.

The technical design, experiment sequence, and findings are documented in
[`report/main.tex`](report/main.tex). Iterations A and B are implemented as
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
3. Add imperfect sensing, delayed or missing readings, and register anomalies.
4. Add apartments, building-level aggregation, and shared boiler accounting.
5. Replace simulated adapters with field-device and building-system adapters.
