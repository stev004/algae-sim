"""Command line: ``python -m algae_sim run --setpoint 30 --light 150``."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from algae_sim.reactor import DEFAULT_START_TIME, BatchResult, ReactorConfig, run_batch
from algae_sim.records import write_run


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="algae_sim", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run one batch and print a summary")
    run.add_argument("--setpoint", type=float, default=30.0, help="temperature setpoint, degC")
    run.add_argument("--light", type=float, default=150.0, help="light, umol/m2/s")
    run.add_argument("--hours", type=float, default=36.0, help="batch length, hours")
    run.add_argument("--ambient", type=float, default=20.0, help="room temperature, degC")
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--heater-fails-after", type=float, default=None, metavar="HOURS")
    run.add_argument("--dropout", type=float, default=0.0, help="temp-sensor dropout prob.")
    run.add_argument("--out", type=Path, default=None, help="write run.json + telemetry.jsonl")
    run.add_argument("--plot", type=Path, default=None, help="save a PNG (needs matplotlib)")
    return parser


def print_report(result: BatchResult, every_h: float = 6.0) -> None:
    print("  hour   meas °C   true °C   heater   OD750   biomass g/L")
    step = every_h * 3600
    next_t = 0.0
    for s in result.telemetry:
        if s.elapsed_s + 1e-9 >= next_t or s is result.telemetry[-1]:
            m = s.measured_temperature_c
            measured = "  --  " if m is None else f"{m:6.1f}"
            print(
                f"{s.elapsed_s / 3600:6.1f}   {measured}   {s.true_temperature_c:7.2f}"
                f"   {s.heater_applied:6.0f}   {s.measured_od750:5.3f}"
                f"   {s.true_biomass_g_per_l:8.3f}"
            )
            next_t += step
    print(json.dumps(result.summary(), indent=2))


def plot(result: BatchResult, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    hours = [s.elapsed_s / 3600 for s in result.telemetry]
    fig, (ax_t, ax_g) = plt.subplots(2, 1, sharex=True, figsize=(7, 5))
    ax_t.plot(hours, [s.true_temperature_c for s in result.telemetry], label="true")
    ax_t.plot(
        hours,
        [s.measured_temperature_c for s in result.telemetry],
        ".",
        markersize=2,
        label="measured",
    )
    ax_t.axhline(result.config.temperature_setpoint_c, linestyle="--", linewidth=0.8)
    ax_t.set_ylabel("temperature (°C)")
    ax_t.legend(loc="lower right")
    ax_g.plot(hours, [s.measured_od750 for s in result.telemetry], label="measured OD750")
    ax_g.set_ylabel("OD750")
    ax_g.set_xlabel("hours")
    fig.suptitle(
        f"setpoint {result.config.temperature_setpoint_c} °C, "
        f"light {result.config.light_umol_m2_s} µmol/m²/s (illustrative model)"
    )
    fig.tight_layout()
    fig.savefig(path, dpi=120)


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    config = ReactorConfig(
        temperature_setpoint_c=args.setpoint,
        light_umol_m2_s=args.light,
        batch_hours=args.hours,
        ambient_c=args.ambient,
        start_temperature_c=args.ambient,
        seed=args.seed,
        heater_fails_after_h=args.heater_fails_after,
        temperature_dropout_probability=args.dropout,
    )
    result = run_batch(config)
    print_report(result)
    if args.out is not None:
        write_run(
            args.out,
            device_id="reactor-1",
            start_time=DEFAULT_START_TIME,
            config=config.as_dict(),
            summary=result.summary(),
            telemetry=result.telemetry,
        )
        print(f"wrote {args.out}/run.json and telemetry.jsonl")
    if args.plot is not None:
        plot(result, args.plot)
        print(f"wrote {args.plot}")
