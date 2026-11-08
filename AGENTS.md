# Working agreement

[Project overview](README.md) · [Progress](docs/progress.md) · [Architecture](docs/architecture.md) · [Model](docs/model.md) · [Data pipeline](docs/data-pipeline.md)

Treat this repository as the workspace root. Use repository-relative links and document commands relative to this root.

This is Steven's portfolio project. As of 2026-11-08 the working mode is **agent-built, Steven-reviewed**: the assistant implements whole, coherent slices and Steven's job is to understand, question and steer them, not to hand-type code. Every slice must still be explainable in an interview, so:

- End each slice with a short plain-English walkthrough: what was built, why, how to run it, what the evidence shows, and one or two "try this" experiments.
- Keep code readable over clever. Docstrings should say *why*, not just *what*.
- Run commands and verification on Steven's behalf (he may be on his phone) and show concise results.
- Ask before changing direction, scope, or anything listed under guardrails. Larger milestones (see the README roadmap) start only when Steven agrees.

## Engineering rules

- Keep true physical state, sensor measurements, controller decisions and applied actuator state separate. Controllers and external systems see measurements only.
- Local control and protection must work with no cloud or AutoLab connection.
- Label illustrative parameters and unvalidated biological models explicitly. Make no predictive or production-readiness claims.
- The core `algae_sim` package stays standard-library only. AutoLab code lives only in `algae_sim/autolab/`; the AutoLab repository itself is not modified from here.
- Add meaningful tests alongside behaviour. `uv run pytest` and `uv run ruff check .` must pass before committing.

## Guardrails

- Verify current official Azure documentation before choosing SDKs, services, deployment or pricing. Get Steven's explicit agreement before creating any paid resource or incurring charges.
- Never ask for secrets in chat; keep secrets out of Git.
- Do not push, publish, deploy publicly or destroy resources without instruction.
- Keep architecture, model, decision and progress notes concise and current.
