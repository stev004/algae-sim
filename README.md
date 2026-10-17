# Virtual Bioreactor Fleet

A learning and portfolio project for Steven: build five simulated biological reactors with local temperature control, simplified educational growth, Azure telemetry, validated supervisory commands and demonstrable failure recovery. This implements closed-loop control; choosing new experiments autonomously is a future extension.

## Current status

Milestone 1 has started: a small standard-library thermal simulator models one true vessel temperature with a manually fixed heater setting. It now records temperature each simulated second and prints heating/cooling readings every two minutes. Eight tests cover thermal behaviour, the exact solution, history timestamps, zero duration and invalid inputs. The current checkpoint is understanding the list of readings before adding a graph. There are still no sensors, controller, external dependencies, cloud resources or dashboard.

Run the examples with `.venv/bin/python thermal_sim.py` and the tests with `.venv/bin/python -m unittest discover -s tests -v`, from this repository root.

Observed environment: Apple Silicon macOS, zsh, Python 3.14.6, Git 2.50.1; uv is available but not required. Visual Studio Code is installed with Microsoft's Python tooling, the repository is open as the workspace and its `.venv` interpreter is selected. Python suitability for Azure SDKs will be checked at the cloud milestone.

## Open and continue this repository

Open the `algae-sim` folder itself as the project root, rather than its parent Documents folder. Start here; all documentation links are relative to this repository and work wherever it is located.

For a new development session, read the [working agreement](AGENTS.md) and [progress log](docs/progress.md). The first fixed-input thermal results are ready for discussion. Do not jump ahead to the controller or cloud.

## Verify setup

Run these commands from the repository root (the folder containing this README). The assistant can run and explain them on Steven's behalf when useful.

```sh
source .venv/bin/activate
python --version
python -c 'import sys; print(sys.executable); print(sys.prefix != sys.base_prefix)'
git status --short
```

Expected: Python 3.14.6 on the current machine, an executable inside algae-sim/.venv, and `True` for environment isolation. Git lists the initial untracked documentation, but not `.venv`. No initial commit has been made.

To recreate the environment on a machine with Python installed: `python3 -m venv .venv`. No packages need installing. Use `deactivate` to leave the environment.

## Roadmap and checkpoints

0. Confirm scope, environment and first step with Steven.
1. One thermal model: heating, cooling, visualisation and an equilibrium check.
2. Sensor, hysteresis controller and actuator: target and ambient changes, basic protection.
3. Educational growth model, run records and reproducibility.
4. One authenticated Azure device, validated ingestion and one persistent database.
5. Validated, expiring supervisory commands, acknowledgements and telemetry confirmation.
6. Five independent devices, durable bounded buffering, disconnection and restart recovery.
7. Small dashboard, reproducible demonstration, measured results and portfolio documentation.

Each milestone needs meaningful evidence and Steven's opportunity to run and discuss it. Hardware, pH control, PID, optimisation and hosting the simulator in Azure are deferred decisions. No production-readiness or biological predictive claims are intended.

See [architecture](docs/architecture.md), [thermal model](docs/model.md), and [decisions and progress](docs/progress.md).
