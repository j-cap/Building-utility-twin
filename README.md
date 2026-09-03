# Building Utility Twin

Simulation-first development of a building utility digital twin. The project
starts with a deliberately small vertical slice and preserves the same
interfaces when simulated devices are replaced by real meters.

The technical design, experiment sequence, and findings are documented in
[`report/main.tex`](report/main.tex). Iteration A / Experiment 0 is implemented
as a reproducible, end-to-end vertical slice.

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
cd report && pdflatex -interaction=nonstopmode -halt-on-error main.tex
```

The runner writes:

- `measurements.jsonl`: canonical, file-backed measurements;
- `timeseries.csv`: analysis-friendly physical truth and meter state;
- `summary.json`: configuration, conservation results, and content hashes;
- `experiment_0_one_pipe_day.png`: the generated result figure.

The compact summary and figure are versioned. The larger JSONL and CSV streams
are deterministic but ignored by git; running the command above recreates them.

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
2. Add domestic-hot-water temperature and central-boiler energy accounting.
3. Add apartments, multiple meters, imperfect sensing, and anomaly scenarios.
4. Replace simulated adapters with field-device and building-system adapters.
