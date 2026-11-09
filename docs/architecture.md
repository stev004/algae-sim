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

## Full AutoLab culture campaigns (stage 6)

`algae_sim/autolab/controller.py` implements AutoLab's `CultureEnvironmentController`. It's registered as `algae-sim-reactor` in the `autonomous_lab.culture_controllers` entry-point group and also provides `IntegratedCultureSensor`. AutoLab owns the experiment: it proposes, compiles and validates, issues a receipt, sends one `SetpointCommand`, then sleeps on its clock while polling telemetry and OD750 readings. It always requests safe state at the end, runs eight culture QC checks, and persists everything.

The adapter owns the device:
- It re-checks its own limits (20–36 °C, because there's no cooling, and 0–500 µmol/m²/s light).
- Each accepted command starts a fresh batch, simulating re-inoculation.
- It reports applied values, measured temperature, trips, faults and cumulative energy. It never reports true state.
- Safe state turns the heater and lights off.

**Clock coupling.** Before answering any call, the adapter steps the reactor forward to AutoLab's `clock.now()`. With `execution.clock: simulated`, AutoLab's `SteppedClock` jumps ahead, so a 36-hour hold costs a fraction of a second. The manifest declares `is_simulated: true`, which AutoLab requires before it will use a simulated clock. The batch seed comes from the experiment ID, so a resumed campaign recreates the same culture.

`campaign.py` is the earlier, database-free loop. It's kept as a quick demo of the optimiser alone.
