# Closeout config — algae-sim

A simulated algae photobioreactor that AutoLab drives as real hardware. Its docs are the portfolio record and the handoff between agent sessions (agent-built, Steven-reviewed; see AGENTS.md).

## Doc map

| Fact changed | Owning file |
|---|---|
| Current state, next steps, open threads | `docs/progress.md`, "Current handoff" section (a snapshot; replace, don't append) |
| Decisions, findings, what each slice did | `docs/progress.md`: a dated entry under the handoff (this is the session log) |
| Architecture, boundaries, AutoLab adapters, device service | `docs/architecture.md` |
| Growth, pH, light model; parameters and their sources | `docs/model.md` |
| Check against real data (Pinto et al. 2026) | `docs/validation.md` (+ `algae_sim/validation.py`) |
| Optimiser benchmark: method, numbers, limits | `docs/benchmark.md` (+ `docs/benchmark/*.json[l]`) |
| Data pipeline, local and Azure: resources, permissions, how to run, evidence | `docs/data-pipeline.md` |
| Portfolio narrative and headline numbers | `docs/case-study.md`, then the README summary |
| Setup, commands, layout, roadmap, faults table | `README.md` |
| Working agreement, guardrails | `AGENTS.md` (change only with Steven) |

Headline numbers appear in several docs (README, case study, benchmark, validation, progress). When one changes, grep for the old value everywhere.

## Session log

Path: `docs/progress.md`. Entry format: `## YYYY-MM-DD — <slice title>`, then bullets: **Decision** / **Finding** / **Result** / open threads with resume points. Newest first, below the "Current handoff" section.

## Git policy

- Commit on `main` with descriptive messages ending in the Co-Authored-By line; `uv run pytest` and `uv run ruff check .` must pass first.
- Push only when Steven says so; a `/closeout` request counts as that instruction.
- Never force-push.

## Quality bar

- Slop: "Improved the Azure pipeline."
- Record: "Sink switched to QueuedIngestClient (`algae_sim/azure/sink.py`), because the free cluster refuses streaming writes (403) for the Entra user; 10,032 unique messages after replay; commit d8236f3."

## Never

- Commit `configs/*.local.json` (Steven's Azure addresses; git-ignored) or anything under `runs/` (generated).
- Hand-edit generated files: `algae_sim/azure/schema.kql` (run `python -m algae_sim.azure schema`), `docs/images/diagram-*.svg` (`scripts/draw_diagrams.py`), chart PNGs (`scripts/demo.sh`, the benchmark `analyse` command).
- Modify the AutoLab repo from here; coordinate with an AutoLab session by message.
- Write secrets anywhere. The Azure sign-in is cached in the macOS keychain; `~/.config/algae-sim/azure-auth-record.json` names the account only.
