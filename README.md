# Building Utility Twin

Simulation-first development of a building utility digital twin. The project
starts with a deliberately small vertical slice and preserves the same
interfaces when simulated devices are replaced by real meters.

The technical design, experiment sequence, and findings are documented in
[`report/main.tex`](report/main.tex).

## Roadmap

1. **Iteration A / Experiment 0:** one water pipe, one simulated day, a virtual
   cumulative meter, canonical measurement contracts, and file-backed storage.
2. Add domestic-hot-water temperature and central-boiler energy accounting.
3. Add apartments, multiple meters, imperfect sensing, and anomaly scenarios.
4. Replace simulated adapters with field-device and building-system adapters.

