# algae-sim

A simulated algae photobioreactor that plugs into [AutoLab](https://github.com/stev004/autolab) as a stand-in for real lab hardware.

The reactor runs its own local temperature control. AutoLab's optimiser proposes growth conditions (temperature setpoint and light), the reactor carries out a batch, and the result comes back through AutoLab's read-only sensor contract as identity-bound, calibrated evidence. AutoLab never touches the heater, just as it would never touch real hardware directly.

> All parameters are illustrative teaching values. The growth model is educational and **not validated**; nothing here predicts real algae behaviour.

## What's here

| Part | What it does |
|---|---|
| `algae_sim/thermal.py` | Heat balance of one well-mixed vessel (heater in, losses to the room) |
| `algae_sim/growth.py` | Educational growth model: temperature optimum, light optimum with photoinhibition, crowding, heat-stress death |
| `algae_sim/instruments.py` | Noisy, quantised temperature and OD750 sensors with dropouts; a heater that can fail |
| `algae_sim/control.py` | On-device hysteresis thermostat with fail-safe and over-temperature trip |
| `algae_sim/reactor.py` | Runs a batch: sense → decide → actuate → physics; collects telemetry and energy use |
| `algae_sim/records.py` | Run records: `run.json` and `telemetry.jsonl`, the instrument-capture format |
| `algae_sim/autolab/sensor.py` | OD750 probe implementing AutoLab's `ReadOnlySensor`, registered as `algae_sim_od` |
| `algae_sim/autolab/campaign.py` | Closed loop: AutoLab's Bayesian optimiser ↔ reactor ↔ sensor ↔ QC |

## Run it

Needs [uv](https://docs.astral.sh/uv/) and Python 3.12+. From this folder:

```sh
uv sync --all-extras          # installs AutoLab v0.5.0 from GitHub + matplotlib
uv run pytest                 # 37 tests
uv run python -m algae_sim run --setpoint 30 --light 150 --out runs/demo --plot runs/demo/batch.png
uv run python -m algae_sim.autolab.campaign --trials 20 --seed 7
```

Try: `--heater-fails-after 6` (heater dies at hour 6), `--dropout 0.2` (20% of temperature readings lost), or a setpoint of 37 °C (heat stress).

The campaign prints each trial and the trade-off curve between growth and energy. Trial 5 has a deliberately failed heater; QC catches it from the temperature measurements and keeps it out of what the optimiser learns from.

## Roadmap

1. ✅ Reactor simulator: thermal, growth, sensors, controller, faults, reproducible run records.
2. ✅ AutoLab adapter: sensor entry point and closed-loop optimisation demo.
3. ⏸ **Azure data pipeline** (side project, see [data-pipeline.md](docs/data-pipeline.md)): capture → parse → store → analyse reactor telemetry in Azure. Needs agreement on services and cost first.
4. Later options: several reactors in parallel, a native AutoLab "culture" campaign type (AutoLab roadmap stage 6), a small dashboard.

See [architecture](docs/architecture.md), [model](docs/model.md), [progress and decisions](docs/progress.md) and the [working agreement](AGENTS.md).
