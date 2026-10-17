# Decisions and learning progress

[Project overview](../README.md) · [Working agreement](../AGENTS.md) · [Architecture](architecture.md) · [Model](model.md)

## Current handoff — 2026-10-01

Open `algae-sim` itself as the project root. Setup and environment isolation have been verified by the assistant. Steven is back on his Mac. Visual Studio Code 1.139.1 is installed with Microsoft's Python, Python Environments, Python Debugger and Pylance extensions. The repository is trusted and open as the workspace, its `.venv` Python 3.14.6 interpreter is selected, and both the simulator and all five tests have been run successfully inside VS Code. No cloud resources have been created.

The first manageable thermal slice is implemented in `thermal_sim.py`. It advances one true vessel temperature in one-second steps using a manually fixed heater fraction. `tests/test_thermal_sim.py` checks heating, cooling, the 40 °C full-power equilibrium, heater input bounds and agreement with the exact ten-minute solution. All five tests pass. The example results are 20.00 °C to 30.25 °C with full heat and 30.00 °C to 24.88 °C with the heater off, each over ten simulated minutes.

Steven has worked through the simulator functions and the first four tests. On 2026-10-01, added `run_simulation_history`, which returns a list of (elapsed seconds, true temperature) pairs including time zero and every one-second update. `run_simulation` retains its final-temperature return value by taking the final history reading. The example prints both histories every two minutes. Eight tests pass, including timestamps, initial state, zero duration and rejection of negative duration. Ten-minute results remain 30.25 °C heating and 24.88 °C cooling.

Current learning checkpoint: explain list creation, tuple pairs, `append`, and indexing using the recorded history; discuss how successive temperature changes shrink. A graph and time-step comparison remain pending before milestone 1 is complete. Do not add sensor, controller, biology or cloud work yet.

All five Markdown files now include repository-relative navigation or entry points; setup commands no longer depend on a Documents-folder location. Documentation is sufficient to resume in a session rooted in this repository.

## 2026-09-20 — Milestone 0 setup

- Inspected Documents and ancestor repository instructions. No applicable AGENTS.md or pre-existing algae-sim directory found; unrelated projects were not inspected for implementation details.
- Created a local Git repository, `.venv`, ignore rules and minimal documentation. Used installed Python 3.14.6 with standard-library venv; no downloads or dependencies were needed.
- Keep the initial model standard-library-only. Defer Python/SDK compatibility decisions for Azure until verified against current official documentation.
- Proposed first slice: fixed heater input, one thermal state, a one-second Euler step, heating/cooling output and an analytic equilibrium/transient check. All numerical parameters are illustrative.
- Assistant verified the environment uses its own Python prefix. Steven's environment run, editor/terminal confirmation and discussion are still pending. Milestone 0 is not yet complete.
- Next: discuss setup results and the energy balance, then implement the first thermal slice. Do not advance to control, growth, fleet or cloud before the relevant learning checkpoints.

Future decisions include sensor fault policy, setpoint/protective limits, command expiry and duplicate handling, identities across restarts, telemetry timestamps and units, durable buffer overflow policy, database selection and measured recovery evidence. These are required later, not implemented now.

## Phone-based working adjustment

This was a temporary adjustment while Steven could not access a terminal. As of 2026-09-27 he is back on his Mac with VS Code configured. The assistant can still execute commands and report evidence, while Steven participates directly in the code at deliberate learning checkpoints. Activation/deactivation was checked on his behalf: the activated interpreter is inside algae-sim/.venv with an isolated prefix; after deactivation, python3 resolves to the Homebrew installation without an isolated prefix.
