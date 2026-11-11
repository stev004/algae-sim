# Architecture

[Project overview](../README.md) · [Working agreement](../AGENTS.md) · [Model](model.md) · [Data pipeline](data-pipeline.md) · [Progress](progress.md)

```text
 AutoLab (separate repo, installed as a pinned dependency)
 ┌──────────────────────────────────────────────────────────┐
 │ OptunaStrategy.ask() ──▶ proposal {temperature, light, pH}│
 │        ▲                                                  │
 │ tell(observation | QC_FAILED)                             │
 └────────┼─────────────────────────────┬────────────────────┘
          │                             │ conditions only, never device commands
 algae_sim/autolab/campaign.py          ▼
   QC + analysis ◀── RawSensorMeasurement ◀── ReactorODSensor (ReadOnlySensor)
          ▲              (calibrated, mock-flagged evidence)        │ reads
          │ energy, trips, measured temps                           ▼
 algae_sim (standard library only) — the "device"
   true state ─▶ TemperatureSensor ─▶ HysteresisController ─▶ Heater ──▶ true state
   (hidden)  └──▶ PhSensor ─────────▶ PhController ─────────▶ CO2 valve ┘
                   noise/dropouts       fail-safe, trips         may stick
          └──▶ OpticalDensitySensor ─▶ capture log ─▶ data pipeline (local; Azure planned)
```

## Boundaries

- **Truth vs measurement.** Only the simulator reads true temperature and biomass. Controllers, QC and AutoLab see sensor readings. Telemetry keeps truth in a separate `simulator_truth` block for testing.
- **Proposal vs control.** AutoLab proposes conditions. The reactor's controller owns the heater and enforces its own limits, so it would keep running safely if AutoLab or the cloud disappeared.
- **Measurement seam.** The adapter implements AutoLab's `ReadOnlySensor` (open → self-test → read, with calibration checks) and is found through the `autonomous_lab.sensor_adapters` entry point. AutoLab needs no code changes. Evidence is flagged `is_mock=True` because no hardware ran.
- **QC before learning.** A batch whose measured temperature drifted from the setpoint, tripped protection, or saturated the OD sensor is reported as `QC_FAILED` and never becomes an optimiser observation.

## Full AutoLab culture campaigns (stage 6, culture contract v2)

`algae_sim/autolab/controller.py` implements AutoLab's `CultureEnvironmentController`. It's registered as `algae-sim-reactor` in the `autonomous_lab.culture_controllers` entry-point group and also provides `IntegratedCultureSensor`. AutoLab owns the experiment: it proposes, compiles and validates, issues a receipt, sends one `SetpointCommand`, then sleeps on its clock while polling telemetry and OD750 readings. It always requests safe state at the end, runs eight culture QC checks, and persists everything.

The adapter owns the device:
- Its manifest declares the **controlled variables** with units, ranges, resolution and whether each is measured: `temperature` 20–36 °C (no cooling), `light_intensity` 0–500 µmol/m²/s, `ph` 6.0–9.0. It also declares **protection limits** (39 °C; pH 5.5 and 9.8) and metered **resources** (`energy` in kWh, `co2` in g). AutoLab reads these rather than assuming them. The table lives once, in `device/core.py`.
- A command must name exactly those variables: unknown ones are refused (`unknown_variable`), and so are missing ones (`missing_variable`), because the device has nothing to hold a missing one at. It re-checks every range itself.
- Each accepted command starts a fresh batch, simulating re-inoculation.
- Telemetry reports `applied`, `measured` (temperature and pH; `None` is a probe dropout) and `consumption` (cumulative since the command) dicts, plus trips and faults. Trip faults name the limit: `over_temperature_trip`, `ph_low_trip`, `ph_high_trip`. It never reports true state.
- Safe state turns the heater and lights off and shuts off the CO₂; pH keeps reporting.

**Replicates and faults.** AutoLab sends one setpoint command per replicate reactor (three by default). Each reactor counts its own holds, and its seed comes from the experiment ID plus the reactor ID, so replicates differ realistically but repeatably. `heater_faults`, `culture_crashes` and `co2_valve_faults` in `settings` map reactor → hold numbers, so a fault can hit exactly one replicate of one experiment.

**Resume.** Telemetry reports `active_command_id` (cleared at safe state). The simulated reactors live inside the AutoLab process, so if AutoLab restarts they're gone. A resume then finds no matching command and AutoLab safely ends the hold as `hold_interrupted`. That's the correct, conservative outcome; persisting the simulated device between processes would be needed to show a successful resume.

**Clock coupling.** Before answering any call, the adapter steps the reactor forward to AutoLab's `clock.now()`. With `execution.clock: simulated`, AutoLab's `SteppedClock` jumps ahead, so a 36-hour hold costs a fraction of a second. The manifest declares `is_simulated: true`, which AutoLab requires before it will use a simulated clock. The batch seed comes from the experiment ID, so a resumed campaign recreates the same culture.

`campaign.py` is the earlier, database-free loop. It's kept as a quick demo of the optimiser alone.

## The reactor as a separate device (roadmap item 4)

```text
AutoLab process                           device process (python -m algae_sim device serve)
  culture runner                            DeviceService: clock, persistence, HTTP/JSON
   └ algae-sim-device adapter ──HTTP──▶       └ DeviceCore firmware: reactors, limits,
     (autolab/remote.py)                         idempotent commands, safe state, OD probe
```

The same `DeviceCore` firmware runs in-process (`algae-sim-reactor`) or behind the service (`algae-sim-device`), so both adapters behave identically.

**Time.** The device has its own clock.
- *Lockstep* (`--speed 0`): the adapter advances the device to AutoLab's simulated clock; fast and deterministic, used by tests.
- *Paced* (`--speed N`): the device runs at N × real time and can't be pushed. If AutoLab's clock is ahead, the adapter waits for the device; if the device is ahead, the adapter moves AutoLab's simulated clock forward to match.

Every reading and telemetry sample is stamped with device time.

**Failures.**
- *Unreachable* (connection refused): nothing was sent, so the error is retry-safe.
- *No reply* after sending: the adapter resends the same `command_id`. The device returns its original acknowledgement without applying the command again.
- *Persistent silence*: the adapter raises an ambiguous `ControllerError`, which AutoLab never retries and handles by going to safe state.
- An unconfirmed safe state is always reported as ambiguous.

**Persistence and resume.** The service pickles its firmware state after every command and about once a second, written atomically. The state carries a version; a file from older firmware is refused with a clear message rather than misread. Restarting the service resumes from the saved state. If AutoLab crashes, the device keeps holding. On restart, AutoLab reads `active_command_id` from each reactor, confirms the hold, and continues the schedule. Readings whose windows passed during the outage are recorded as missing, and QC judges whether enough of the hold was observed.

**Cost of realism.** Over HTTP an experiment takes about 1 s in lockstep, against about 0.5 s in-process. At high paced speeds, a few real seconds of AutoLab restart become hours of culture time, so the resume demo relaxes telemetry coverage to 75% and reports the gap it actually saw. The service listens on 127.0.0.1 with no authentication: it's for one local machine.

## The data pipeline (roadmap item 5, local version)

```text
device service ─▶ capture log ─▶ uploader ─HTTP─▶ ingest ─▶ raw batches + SQLite ─▶ report
```

With `--capture DIR` the device records what it measured: environment samples every 10 device-minutes, an extra sample the moment a protection trip fires, and every OD reading. It writes them as numbered messages to an append-only log. The uploader is a separate process that ships the log to the ingest endpoint, which validates, deduplicates and stores it. The analysis step summarises each culture hold from the stored data alone. Its flags agree with AutoLab's QC in the demo, which checks the data path end to end.

Boundaries:
- Capture never affects control or what AutoLab sees: the OD readings are identical with capture on or off (tested).
- Each stage can be down independently. The log buffers while the uploader or ingest is down, and the cursor and dedupe keys make resends harmless.
- Simulator truth is dropped at parse, so analysis sees only what a real device could report.

Details and the Azure mapping are in [data-pipeline.md](data-pipeline.md).
