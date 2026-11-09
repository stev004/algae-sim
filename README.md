# algae-sim

A simulated algae photobioreactor that plugs into [AutoLab](https://github.com/stev004/autolab) as a stand-in for real lab hardware.

The reactor runs its own local temperature control. AutoLab's optimiser proposes growth conditions (temperature setpoint and light). AutoLab validates them, sends one bounded setpoint command, and holds the culture while polling telemetry and OD750 readings, then QCs and learns from the result. All of this runs through AutoLab's real stage-6 culture campaign and is recorded in its evidence database. The reactor acknowledges what it actually applied, enforces its own limits and protection, and simulates 36 hours in a fraction of a second by following AutoLab's simulated clock.

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
| `algae_sim/autolab/controller.py` | **The device plug-in AutoLab drives**: an `algae-sim-reactor` culture controller (setpoints in, acknowledgements, telemetry, safe state, integrated OD750 probe out) |
| `algae_sim/autolab/campaign.py` | Earlier lightweight loop (no database), kept as a quick demo of AutoLab's optimiser |
| `configs/` | Culture campaign and installation policy for running AutoLab against this reactor |

## Run it

Needs [uv](https://docs.astral.sh/uv/), Python 3.12+, and the AutoLab repository checked out next to this one as `../autolab`, on a branch with stage-6 culture support (currently `stage6-culture-slice-b`, not yet pushed). From this folder:

```sh
uv sync --all-extras          # installs ../autolab (editable) + matplotlib
uv run pytest                 # 46 tests, including a full AutoLab culture campaign
uv run python -m algae_sim run --setpoint 30 --light 150 --out runs/demo --plot runs/demo/batch.png
```

A full AutoLab culture campaign (20 simulated 36 h cultures, about 3 s):

```sh
uv run autolab init --database runs/autolab.sqlite3
uv run autolab culture create configs/culture-algae-sim.yaml --database runs/autolab.sqlite3 --policy configs/culture-policy.yaml --optimizer-root runs/optimizer
uv run autolab culture run <campaign-id> --database runs/autolab.sqlite3 --policy configs/culture-policy.yaml --optimizer-root runs/optimizer --artifact-root runs/artifacts
uv run autolab culture report <campaign-id> --database runs/autolab.sqlite3 --policy configs/culture-policy.yaml
```

Try: `--heater-fails-after 6` (heater dies at hour 6), `--dropout 0.2` (20% of temperature readings lost), or a setpoint of 37 °C (heat stress).

In the campaign, batch 5 has a deliberately failed heater (set in the config's controller `settings`). AutoLab's culture QC fails it on `temperature_control`, because the mean deviation from the setpoint was about 10 °C. The run is kept in the evidence but excluded from what the optimiser learns.

## Roadmap

1. ✅ Reactor simulator: thermal, growth, sensors, controller, faults, reproducible run records.
2. ✅ AutoLab adapter: sensor entry point and closed-loop optimisation demo.
3. ✅ AutoLab stage-6 controller: full culture campaigns (validation, commands, telemetry, QC, evidence ledger) against this reactor.
4. ⏸ **Azure data pipeline** (side project, see [data-pipeline.md](docs/data-pipeline.md)): capture → parse → store → analyse reactor telemetry in Azure. Needs agreement on services and cost first.
5. Later options: several reactors in parallel (needs AutoLab batch proposals), mid-hold pause and resume, a small dashboard.

See [architecture](docs/architecture.md), [model](docs/model.md), [progress and decisions](docs/progress.md) and the [working agreement](AGENTS.md).
