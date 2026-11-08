# Architecture

[Project overview](../README.md) · [Working agreement](../AGENTS.md) · [Model](model.md) · [Data pipeline](data-pipeline.md) · [Progress](progress.md)

```text
 AutoLab (separate repo, installed as a pinned dependency)
 ┌──────────────────────────────────────────────────────────┐
 │ OptunaStrategy.ask() ──▶ proposal {setpoint, light}       │
 │        ▲                                                  │
 │ tell(observation | QC_FAILED)                             │
 └────────┼─────────────────────────────┬────────────────────┘
          │                             │ conditions only, never device commands
 algae_sim/autolab/campaign.py          ▼
   QC + analysis ◀── RawSensorMeasurement ◀── ReactorODSensor (ReadOnlySensor)
          ▲              (calibrated, mock-flagged evidence)        │ reads
          │ energy, trips, measured temps                           ▼
 algae_sim (standard library only) — the "device"
   true state ─▶ TemperatureSensor ─▶ HysteresisController ─▶ Heater ─▶ true state
   (hidden)        noise/dropouts       fail-safe, trip          may fail
          └──▶ OpticalDensitySensor ─▶ telemetry.jsonl ─▶ (future) Azure pipeline
```

## Boundaries

- **Truth vs measurement.** Only the simulator reads true temperature and biomass. Controllers, QC and AutoLab see sensor readings. Telemetry keeps truth in a separate `simulator_truth` block for testing.
- **Proposal vs control.** AutoLab proposes conditions. The reactor's controller owns the heater and enforces its own limits, so it would keep running safely if AutoLab or the cloud disappeared.
- **Measurement seam.** The adapter implements AutoLab's `ReadOnlySensor` (open → self-test → read, with calibration checks) and is found through the `autonomous_lab.sensor_adapters` entry point. AutoLab needs no code changes. Evidence is flagged `is_mock=True` because no hardware ran.
- **QC before learning.** A batch whose measured temperature drifted from the setpoint, tripped protection, or saturated the OD sensor is reported as `QC_FAILED` and never becomes an optimiser observation.

## Why the demo loop isn't a full AutoLab campaign

AutoLab v0.5's campaign runner, compiler and validation are built around a plate-based colorimetric assay. A native long-running culture campaign is AutoLab's roadmap stage 6. Until then, `campaign.py` reuses AutoLab's real optimiser, problem definitions and sensor contract and does the execution step itself. That gap is stated openly; it isn't hidden.
