# Building Utility Twin pilot-preparation plan

## Purpose

Experiments 0--5 established the simulation, canonical measurement contract,
meter and telemetry behavior, conservation checks, anomaly residuals, and the
first external-file adapter. The next work packages turn those components into
a demonstrable application without treating synthetic data as field evidence.

Experiment 6 is deliberately reserved for the first unchanged, de-identified
export from a physical meter system. Until such data are available, development
continues in a separately named pilot-preparation track.

## Product boundary

The intended product path is:

```text
source adapter -> canonical measurements -> persistent backend
               -> quality and balance analytics -> operator interface
```

The simulator remains a deterministic regression oracle and portfolio-data
generator. It is not a substitute for validating meter semantics, thresholds,
false-alarm rates, billing rules, or user workflows on real installations.

## Work packages

### P1 -- Synthetic portfolio and persistent backend

Status: **complete**

Build a deterministic multi-building portfolio, a versioned relational schema,
idempotent ingestion, and a read API that a dashboard can consume.

Required capabilities:

- portfolios, buildings, apartments, meters, imports, and canonical
  measurements have explicit relational identities;
- SQLite supports a zero-administration local demonstration while the storage
  interface and schema remain suitable for a later PostgreSQL implementation;
- loading the same snapshot twice does not duplicate topology or measurements;
- every import has a stable content digest and an auditable disposition;
- API endpoints expose health, portfolio summary, buildings, meters,
  measurements, and import history;
- a seeded reference portfolio and all machine-readable outputs are
  reproducible;
- schema-version mismatch fails explicitly rather than silently changing data.

Acceptance evidence:

- topology and measurement counts agree before and after persistence;
- first load accepts every generated measurement and the replay accepts none;
- the replay reports every already stored measurement as a duplicate;
- database queries return timezone-normalized, canonical values;
- two independent runs produce byte-identical summary and API snapshots;
- unit and endpoint tests pass in continuous integration.

### P2 -- Operator dashboard MVP

Status: **complete**

Build a replaceable Streamlit/Plotly client against the P1 API. Initial pages:

1. portfolio health and active issues;
2. building consumption and building-minus-apartment balance;
3. meter register, quality, and lifecycle history;
4. import status with accepted, duplicate, and rejected rows;
5. anomaly review with evidence, status, and operator notes.

The dashboard must label residuals as balance anomalies rather than confirmed
leaks. A custom web frontend remains optional until a real operator workflow is
known.

Implemented capabilities:

- the Streamlit application obtains all operational data through a replaceable
  HTTP client and never reads the SQLite database directly;
- portfolio cards summarize consumption, building-meter completeness, and the
  active review load across all six buildings;
- building views compare the building register with the apartment-register sum
  only at common boundaries and explicitly separate balance evidence from fault
  attribution;
- meter views expose register history, quality markers, completeness, and the
  canonical records behind the visualization;
- import provenance remains visible alongside accepted and duplicate counts;
- deterministic data-quality review items contain threshold evidence, severity,
  status, and a persistent operator note;
- the reference dashboard snapshot and compact overview figure are
  reproducible.

Acceptance evidence:

- all five planned operator pages are represented by tested API calls;
- the six-building portfolio, one complete import, meter quality, and water
  balance are visible through the dashboard contract;
- every review item includes the measurements, completeness, suspect share,
  and thresholds that caused it;
- review status and notes survive a new database session;
- unknown resources and invalid review updates fail explicitly;
- two independent P2 runs produce byte-identical summary and dashboard
  snapshots.

### P3 -- Analytics test bench

Separate analytics into three evidence levels:

- **data quality:** missing, stale, duplicated, conflicting, non-monotonic, or
  unexpectedly sampled readings;
- **accounting and plausibility:** period consumption, topology completeness,
  building/apartment balance, persistent night flow, and thermal balance;
- **diagnostic research:** leak or meter-fault attribution, forecasting, and
  learned anomaly scores.

Synthetic campaigns may verify mechanisms and software behavior. Operational
thresholds and accuracy claims require held-out field data.

### P4 -- Vendor-adapter toolkit

Generalize Experiment 5 into declarative mappings, import preview, adapter
conformance tests, schema/version recognition, templates, and a scaffold for a
new vendor. Preserve raw source files and transformation provenance.

### P5 -- Pilot packaging and operational safeguards

Provide one-command startup, containerized deployment, seeded demonstration
accounts, role boundaries, health checks, logging, backup/export, and an
operator walkthrough. Security and privacy controls can be implemented and
tested structurally, but deployment-specific compliance requires the actual
operator, hosting environment, and data-processing responsibilities.

## Field-data gate: Experiment 6

Experiment 6 begins only when an authentic source is available. Its primary
question is whether every unchanged source row can be explained as accepted,
deliberately deduplicated, or rejected with a precise reason. Anomaly-detection
performance is secondary until source semantics and data quality are understood.

The requested field package should include the raw export, meter/building
registry, vendor documentation, timezone and unit semantics, meter lifecycle
events, current workflow description, expected reporting output, and any known
problem periods that can serve as validation evidence.
