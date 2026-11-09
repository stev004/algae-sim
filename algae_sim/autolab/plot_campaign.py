"""Chart a finished AutoLab culture campaign straight from AutoLab's evidence database.

    uv run python -m algae_sim.autolab.plot_campaign DATABASE.sqlite3 OUTPUT.png

Reads only what AutoLab recorded (proposals, derived metrics, QC checks), so the
chart shows AutoLab's own verdicts, not the simulator's hidden truth.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

REPLICATE_COLOURS = ("tab:blue", "tab:orange", "tab:green", "tab:purple", "tab:brown")


def load(database: Path) -> list[dict[str, Any]]:
    db = sqlite3.connect(database)
    rows = []
    query = (
        "select e.ordinal, p.snapshot, e.id from experiments e "
        "join proposals p on p.experiment_id = e.id order by e.ordinal"
    )
    for ordinal, snapshot, experiment_id in db.execute(query):
        values = json.loads(snapshot)["parameter_values"]
        od = db.execute(
            "select value, uncertainty, details from derived_metrics "
            "where experiment_id = ? and name = 'final_od750'",
            (experiment_id,),
        ).fetchone()
        energy = db.execute(
            "select value from derived_metrics where experiment_id = ? and name = 'energy_kwh'",
            (experiment_id,),
        ).fetchone()
        failed = {
            json.loads(details).get("reactor_id", "?")
            for (details,) in db.execute(
                "select c.details from qc_checks c join qc_runs q on q.id = c.qc_run_id "
                "where q.experiment_id = ? and c.status != 'passed'",
                (experiment_id,),
            )
        }
        rows.append(
            {
                "index": ordinal,
                "setpoint": float(values["temperature_setpoint"]["value"]),
                "light": float(values["light_intensity"]["value"]),
                "od": None if od is None else float(od[0]),
                "sd": None if od is None else float(json.loads(od[1])["standard_deviation"]),
                "replicates": {}
                if od is None
                else {k: float(v) for k, v in json.loads(od[2])["replicates"].items()},
                "energy": None if energy is None else float(energy[0]),
                "failed_replicates": sorted(failed),
            }
        )
    return rows


def plot(rows: list[dict[str, Any]], output: Path, startup: int = 8) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    observed = [r for r in rows if r["od"] is not None]
    reactors = sorted({k for r in rows for k in r["replicates"]} | {
        k for r in rows for k in r["failed_replicates"]
    })
    colour = {k: REPLICATE_COLOURS[i % len(REPLICATE_COLOURS)] for i, k in enumerate(reactors)}

    fig, (left, right) = plt.subplots(1, 2, figsize=(12, 4.8))
    points = left.scatter(
        [r["energy"] for r in observed],
        [r["od"] for r in observed],
        c=[r["setpoint"] for r in observed],
        cmap="coolwarm",
        s=[30 + r["light"] / 4 for r in observed],
        edgecolor="k",
        zorder=3,
    )
    left.errorbar(
        [r["energy"] for r in observed],
        [r["od"] for r in observed],
        yerr=[r["sd"] for r in observed],
        fmt="none",
        ecolor="grey",
        zorder=2,
    )
    for r in observed:
        left.annotate(
            str(r["index"]), (r["energy"], r["od"]), fontsize=7,
            xytext=(4, 3), textcoords="offset points",
        )
    fig.colorbar(points, ax=left, label="temperature setpoint (°C)")
    left.set_xlabel("energy used (kWh)")
    left.set_ylabel("final OD750, mean of passing replicates")
    left.set_title("Growth vs energy (bars = replicate spread, size = light)")

    labelled: set[str] = set()
    for r in rows:
        for reactor, value in r["replicates"].items():
            label = reactor if reactor not in labelled else None
            labelled.add(reactor)
            right.scatter(r["index"], value, color=colour[reactor], s=18, label=label)
        for n, reactor in enumerate(r["failed_replicates"]):
            right.annotate(
                f"✗ {reactor}", (r["index"], 0.05 + 0.07 * n), fontsize=7,
                color="red", ha="center",
            )
        if r["od"] is None:
            right.axvspan(r["index"] - 0.4, r["index"] + 0.4, color="red", alpha=0.15)
    right.plot(
        [r["index"] for r in observed], [r["od"] for r in observed],
        "k-", lw=0.8, label="mean of passing",
    )
    right.axvspan(-0.5, startup - 0.5, color="grey", alpha=0.12, label="random start-up trials")
    right.set_ylim(0, 1.05)
    right.set_xlabel("experiment")
    right.set_ylabel("final OD750 per replicate")
    right.set_title("Replicates per experiment (✗ = failed QC, red = excluded)")
    right.legend(fontsize=7, loc="upper left")
    fig.suptitle(
        "AutoLab culture campaign on the simulated algae reactor (illustrative model)"
    )
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=120)


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        raise SystemExit("usage: python -m algae_sim.autolab.plot_campaign DATABASE OUTPUT.png")
    plot(load(Path(args[0])), Path(args[1]))
    print(f"wrote {args[1]}")


if __name__ == "__main__":
    main()
