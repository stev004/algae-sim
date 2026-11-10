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

    device = sub.add_parser("device", help="run the reactor bank as a local device service")
    device_sub = device.add_subparsers(dest="device_command", required=True)
    serve = device_sub.add_parser("serve", help="start the device service (Ctrl-C to stop)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8750)
    serve.add_argument(
        "--speed", type=float, default=0.0,
        help="0 = lockstep (clients advance time); N = run at N x real time",
    )
    serve.add_argument(
        "--state", type=Path, default=None,
        help="directory to persist device state in (restored on restart)",
    )
    serve.add_argument("--settings", type=Path, default=None, help="device settings JSON file")
    serve.add_argument("--device-id", default="algae-sim-device")
    serve.add_argument(
        "--capture", type=Path, default=None,
        help="append captured telemetry to DIR/telemetry.jsonl for the data pipeline",
    )
    status = device_sub.add_parser("status", help="print a running device's status")
    status.add_argument("--url", default="http://127.0.0.1:8750")

    pipeline = sub.add_parser("pipeline", help="local instrument-data pipeline")
    pipeline_sub = pipeline.add_subparsers(dest="pipeline_command", required=True)
    ingest = pipeline_sub.add_parser("ingest", help="run the ingest endpoint (Ctrl-C to stop)")
    ingest.add_argument("--data", type=Path, default=Path("runs/pipeline"))
    ingest.add_argument("--host", default="127.0.0.1")
    ingest.add_argument("--port", type=int, default=8760)
    upload = pipeline_sub.add_parser("upload", help="ship a device capture log to the ingest")
    upload.add_argument("--capture", type=Path, required=True, help="the device's capture DIR")
    upload.add_argument("--url", default="http://127.0.0.1:8760")
    upload.add_argument("--follow", action="store_true", help="keep watching for new lines")
    upload.add_argument(
        "--exit-when-idle", type=float, default=None, metavar="SECONDS",
        help="with --follow: stop after this long with nothing new to send",
    )
    upload.add_argument(
        "--replay", action="store_true",
        help="reset the cursor and send the whole log again (tests deduplication)",
    )
    report = pipeline_sub.add_parser("report", help="summarise what the pipeline stored")
    report.add_argument("--data", type=Path, default=Path("runs/pipeline"))
    report.add_argument("--json", action="store_true")
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


def device_main(args: argparse.Namespace) -> None:
    if args.device_command == "status":
        import urllib.request

        with urllib.request.urlopen(f"{args.url.rstrip('/')}/v1/status", timeout=5) as response:
            print(json.dumps(json.load(response), indent=2))
        return

    from algae_sim.device.core import DeviceSettings
    from algae_sim.device.service import DeviceService, serve

    raw = {} if args.settings is None else json.loads(args.settings.read_text())
    service = DeviceService(
        DeviceSettings.from_mapping(raw),
        speed=args.speed,
        state_dir=args.state,
        device_id=args.device_id,
        capture_dir=args.capture,
    )

    def ready(server: object) -> None:
        host, port = server.server_address[:2]  # type: ignore[attr-defined]
        restored = " (restored saved state)" if service.restored else ""
        print(
            f"algae-sim device listening on http://{host}:{port}  mode={service.mode}"
            f" speed={service.speed:g}  device_time={service.core.now.isoformat()}{restored}",
            flush=True,
        )

    import signal

    def stop(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    # Shut down cleanly (state saved) on Ctrl-C or a supervisor's SIGTERM, even when
    # started in the background, where shells ignore SIGINT by default.
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        serve(service, host=args.host, port=args.port, ready=ready)
    except KeyboardInterrupt:
        print("device stopped; state saved" if args.state else "device stopped", flush=True)


def pipeline_main(args: argparse.Namespace) -> None:
    if args.pipeline_command == "report":
        from algae_sim.pipeline.analyse import format_report, report

        data = report(args.data)
        print(json.dumps(data, indent=2) if args.json else format_report(data))
        return
    if args.pipeline_command == "upload":
        from algae_sim.device.service import CAPTURE_FILE
        from algae_sim.pipeline.uploader import Uploader

        uploader = Uploader(args.capture / CAPTURE_FILE, args.url)
        if args.replay:
            uploader.reset()
        try:
            uploader.run(
                follow=args.follow,
                exit_when_idle_s=args.exit_when_idle,
                report=lambda line: print(line, flush=True),
            )
        except KeyboardInterrupt:
            print("uploader stopped; cursor saved", flush=True)
        return

    import signal

    from algae_sim.pipeline.ingest import IngestService, serve

    service = IngestService(args.data)

    def ready(server: object) -> None:
        host, port = server.server_address[:2]  # type: ignore[attr-defined]
        print(f"ingest listening on http://{host}:{port}  data={args.data}", flush=True)

    def stop(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        serve(service, host=args.host, port=args.port, ready=ready)
    except KeyboardInterrupt:
        print("ingest stopped", flush=True)


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    if args.command == "device":
        device_main(args)
        return
    if args.command == "pipeline":
        pipeline_main(args)
        return
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
