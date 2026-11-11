"""Chart a finished AutoLab culture campaign straight from AutoLab's evidence database.

    uv run python -m algae_sim.autolab.plot_campaign DATABASE.sqlite3 OUTPUT_DIR

Reads only what AutoLab recorded (proposals, derived metrics, QC checks), so the
charts show AutoLab's own verdicts, not the simulator's hidden truth.

Writes three charts. Each answers one question, and its title states the answer
for this campaign:

* ``progress.png``: did the optimiser find better conditions?
* ``tradeoffs.png``: what did growth cost, in energy and CO2?
* ``qc.png``: which replicates did quality control reject, and why?

Each chart comes in a light and a ``-dark`` version, so a README can show the one
that matches the reader's GitHub theme. Pass and fail are never shown by colour
alone: they also differ in shape (a tick or a cross) and carry text labels.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Palette: a validated reference palette (colour-blind checked in both themes).
THEMES: dict[str, dict[str, str]] = {
    "light": {
        "surface": "#fcfcfb",
        "ink": "#0b0b0b",
        "secondary": "#52514e",
        "muted": "#898781",
        "grid": "#e1e0d9",
        "axis": "#c3c2b7",
        "context": "#c3c2b7",  # de-emphasised marks
        "series": "#2a78d6",
        "band": "#f0efec",
        "good": "#0ca30c",
        "critical": "#d03b3b",
        "pass_cell": "#eef5ee",
    },
    "dark": {
        "surface": "#1a1a19",
        "ink": "#f0efec",
        "secondary": "#c3c2b7",
        "muted": "#898781",
        "grid": "#2c2c2a",
        "axis": "#383835",
        "context": "#5a5955",
        "series": "#3987e5",
        "band": "#262624",
        "good": "#0ca30c",
        "critical": "#d03b3b",
        "pass_cell": "#1f2a1f",
    },
}

# Most telling first: when a replicate fails several checks, the first one listed
# here is the cause; the others (missing readings and so on) are its consequences.
REASONS: tuple[tuple[str, str], ...] = (
    ("no_protection_trip", "device protection tripped"),
    ("control:temperature", "temperature drifted off setpoint"),
    ("control:ph", "pH drifted off setpoint"),
    ("growth_plausible", "implausible growth (culture crash?)"),
    ("no_contamination_indicator", "contamination indicator"),
    ("no_sensor_saturation", "OD sensor saturated"),
    ("setpoint_acknowledged", "setpoints not acknowledged"),
    ("hold_continuous", "hold interrupted"),
    ("calibration_valid", "probe calibration invalid"),
)
TRIP_NAMES = {
    "ph_low_trip": "low-pH trip",
    "ph_high_trip": "high-pH trip",
    "over_temperature_trip": "over-temperature trip",
}


@dataclass(slots=True)
class Experiment:
    index: int
    temperature: float
    light: float
    ph: float | None
    observed: bool  # passed QC as a whole, so the optimiser learned from it
    od: float | None  # mean final OD750 of the passing replicates
    energy: float | None
    co2: float | None
    reactors: list[str] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)  # reactor -> main reason

    @property
    def conditions(self) -> str:
        ph = "" if self.ph is None else f", pH {self.ph:.2f}"
        return f"{self.temperature:.1f} °C, {self.light:.0f} µmol/m²/s{ph}"


def load(database: Path) -> list[Experiment]:
    db = sqlite3.connect(database)
    try:
        return [_experiment(db, *row) for row in db.execute(
            "select e.ordinal, e.state, e.id, p.snapshot from experiments e "
            "join proposals p on p.experiment_id = e.id order by e.ordinal"
        )]
    finally:
        db.close()


def _experiment(
    db: sqlite3.Connection, ordinal: int, state: str, experiment_id: str, snapshot: str
) -> Experiment:
    values = json.loads(snapshot)["parameter_values"]
    metrics = dict(db.execute(
        "select name, value from derived_metrics where experiment_id = ?", (experiment_id,)
    ).fetchall())
    failed: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    reactors: set[str] = set()
    for name, status, details in db.execute(
        "select c.name, c.status, c.details from qc_checks c "
        "join qc_runs q on q.id = c.qc_run_id where q.experiment_id = ?",
        (experiment_id,),
    ):
        info = json.loads(details)
        reactor = info.get("reactor_id")
        if reactor is None:
            continue
        reactors.add(reactor)
        if status != "passed":
            failed.setdefault(reactor, []).append((name, info))

    def number(key: str) -> float | None:
        return None if key not in metrics else float(metrics[key])

    return Experiment(
        index=ordinal,
        temperature=float(values["temperature"]["value"]),
        light=float(values["light_intensity"]["value"]),
        ph=float(values["ph"]["value"]) if "ph" in values else None,
        observed=state == "observed",
        od=number("final_od750"),
        energy=number("energy_kwh"),
        co2=number("co2_g"),
        reactors=sorted(reactors),
        failures={r: _main_reason(checks) for r, checks in failed.items()},
    )


def _main_reason(checks: list[tuple[str, dict[str, Any]]]) -> str:
    by_name = dict(checks)
    for name, text in REASONS:
        if name in by_name:
            trips = [TRIP_NAMES.get(t, t) for t in by_name[name].get("trips", [])]
            return f"{text} ({', '.join(trips)})" if trips else text
    return checks[0][0].replace("_", " ")


def pareto_front(experiments: list[Experiment]) -> list[Experiment]:
    """Experiments no other beats on growth, energy and CO2 at once."""
    scored = [e for e in experiments if e.od is not None and e.energy is not None]

    def costs(e: Experiment) -> tuple[float, float, float]:
        return (-e.od, e.energy, e.co2 or 0.0)  # type: ignore[operator]

    def dominates(a: Experiment, b: Experiment) -> bool:
        ca, cb = costs(a), costs(b)
        return all(x <= y for x, y in zip(ca, cb, strict=True)) and ca != cb

    return [e for e in scored if not any(dominates(o, e) for o in scored if o is not e)]


# -- drawing ---------------------------------------------------------------------------


def _style(plt: Any, theme: dict[str, str]) -> None:
    plt.rcParams.update({
        "font.family": ["Helvetica Neue", "Arial", "DejaVu Sans"],
        "font.size": 10,
        "figure.facecolor": theme["surface"],
        "axes.facecolor": theme["surface"],
        "savefig.facecolor": theme["surface"],
        "axes.edgecolor": theme["axis"],
        "axes.labelcolor": theme["secondary"],
        "axes.grid": True,
        "axes.axisbelow": True,
        "grid.color": theme["grid"],
        "grid.linewidth": 0.8,
        "xtick.color": theme["muted"],
        "ytick.color": theme["muted"],
        "xtick.labelcolor": theme["secondary"],
        "ytick.labelcolor": theme["secondary"],
        "text.color": theme["ink"],
        "legend.frameon": False,
        "legend.labelcolor": theme["secondary"],
    })


def _frame(ax: Any, theme: dict[str, str]) -> None:
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(theme["axis"])
    ax.tick_params(length=0, pad=6)


def _titles(fig: Any, title: str, subtitle: str, theme: dict[str, str]) -> None:
    fig.text(0.02, 0.97, title, fontsize=14, fontweight="bold", color=theme["ink"],
             va="top", ha="left")
    fig.text(0.02, 0.905, subtitle, fontsize=10, color=theme["secondary"],
             va="top", ha="left")


def _legend(fig: Any, handles: list[Any], labels: list[str]) -> None:
    """One row of legend under the subtitle, clear of the data."""
    fig.legend(handles, labels, loc="upper left", bbox_to_anchor=(0.08, 0.83), ncols=3,
               fontsize=9, handletextpad=0.4, columnspacing=1.6)


def progress_chart(
    experiments: list[Experiment], theme: dict[str, str], startup: int
) -> Any:
    import matplotlib.pyplot as plt

    scored = [e for e in experiments if e.od is not None]
    best = max(scored, key=lambda e: e.od)  # type: ignore[arg-type, return-value]
    fig, ax = plt.subplots(figsize=(8, 4.8))
    fig.subplots_adjust(left=0.09, right=0.97, top=0.72, bottom=0.13)
    _frame(ax, theme)
    _titles(
        fig,
        f"Best culture: OD750 {best.od:.2f}, found in experiment {best.index}",
        "Final optical density per experiment (mean of the replicates that passed QC). "
        "Higher is denser growth.",
        theme,
    )

    last = max(e.index for e in experiments)
    ax.axvspan(-0.5, startup - 0.5, color=theme["band"], lw=0, zorder=0)
    ax.text(startup / 2 - 0.5, 1.02, "random start-up", transform=ax.get_xaxis_transform(),
            ha="center", va="bottom", fontsize=9, color=theme["muted"])
    ax.text((startup + last) / 2, 1.02, "optimiser chooses", ha="center", va="bottom",
            transform=ax.get_xaxis_transform(), fontsize=9, color=theme["muted"])

    # Best so far, as a step line: the curve that should rise as the optimiser learns.
    xs, ys, running = [], [], 0.0
    for e in scored:
        running = max(running, e.od)  # type: ignore[type-var]
        xs.append(e.index)
        ys.append(running)
    xs.append(last + 0.4)
    ys.append(running)
    ax.step(xs, ys, where="post", color=theme["secondary"], lw=1.5, zorder=2,
            label="best so far")

    early = [e for e in scored if e.index < startup]
    later = [e for e in scored if e.index >= startup]
    ring = theme["surface"]
    ax.scatter([e.index for e in early], [e.od for e in early], s=46, color=theme["context"],
               edgecolor=ring, linewidth=1.5, zorder=3, label="random start-up trial")
    ax.scatter([e.index for e in later], [e.od for e in later], s=46, color=theme["series"],
               edgecolor=ring, linewidth=1.5, zorder=3, label="optimiser's choice")

    top = max(e.od for e in scored)  # type: ignore[type-var]
    excluded = [e for e in experiments if not e.observed]
    for e in excluded:
        ax.scatter([e.index], [0.03 * top], marker="X", s=70, color=theme["critical"],
                   edgecolor=ring, linewidth=1, zorder=3)
        ax.annotate("excluded by QC", (e.index, 0.03 * top), xytext=(0, 9),
                    textcoords="offset points", ha="center", fontsize=8,
                    color=theme["secondary"])

    right_side = best.index > last * 0.6
    ax.annotate(
        f"Best: {best.conditions}",
        (best.index, best.od),
        xytext=(-30, 16) if right_side else (30, 16),
        textcoords="offset points",
        ha="right" if right_side else "left",
        va="bottom",
        fontsize=9,
        color=theme["ink"],
        arrowprops={"arrowstyle": "-", "color": theme["muted"], "lw": 0.8,
                    "shrinkA": 2, "shrinkB": 5},
    )
    ax.set_xlim(-0.6, last + 0.6)
    ax.set_ylim(0, top * 1.25)
    ax.set_xticks(range(0, last + 1, 2))
    ax.set_xlabel("experiment (in the order run)")
    ax.set_ylabel("final OD750")
    ax.grid(axis="x", visible=False)
    handles, labels = ax.get_legend_handles_labels()
    order = [labels.index(n) for n in
             ("random start-up trial", "optimiser's choice", "best so far")]
    _legend(fig, [handles[i] for i in order], [labels[i] for i in order])
    return fig


def tradeoff_chart(experiments: list[Experiment], theme: dict[str, str]) -> Any:
    import matplotlib.pyplot as plt

    scored = [e for e in experiments if e.od is not None and e.energy is not None]
    front = pareto_front(scored)
    others = [e for e in scored if e not in front]
    has_co2 = all(e.co2 is not None for e in scored)
    panels = [("energy", "energy used (kWh)")]
    if has_co2:
        panels.append(("co2", "CO₂ used (g)"))
    fig, axes = plt.subplots(1, len(panels), figsize=(8, 5.0), sharey=True, squeeze=False)
    fig.subplots_adjust(left=0.09, right=0.97, top=0.70, bottom=0.12, wspace=0.12)
    costs = "energy and CO₂" if has_co2 else "energy"
    _titles(
        fig,
        f"{len(front)} of {len(scored)} experiments are best trade-offs",
        f"Each dot is one experiment. Up is more growth, left is cheaper. Blue: no other "
        f"experiment grew more\nfor less {costs} at the same time (the Pareto front).",
        theme,
    )
    best = max(scored, key=lambda e: e.od)  # type: ignore[arg-type, return-value]
    cheapest = min(front, key=lambda e: e.energy)  # type: ignore[arg-type, return-value]
    ring = theme["surface"]
    for ax, (key, label) in zip(axes[0], panels, strict=True):
        _frame(ax, theme)
        ax.scatter([getattr(e, key) for e in others], [e.od for e in others], s=46,
                   color=theme["context"], edgecolor=ring, linewidth=1.5, zorder=3,
                   label="other experiments")
        ax.scatter([getattr(e, key) for e in front], [e.od for e in front], s=52,
                   color=theme["series"], edgecolor=ring, linewidth=1.5, zorder=4,
                   label="best trade-offs")
        ax.set_xlabel(label)
        ax.set_xlim(left=0)
    axes[0][0].set_ylabel("final OD750")
    axes[0][0].set_ylim(0, max(e.od for e in scored) * 1.25)  # type: ignore[type-var]
    first = axes[0][0]
    leader = {"arrowstyle": "-", "color": theme["muted"], "lw": 0.8, "shrinkA": 2, "shrinkB": 5}
    first.annotate(f"most growth: {best.conditions}", (best.energy, best.od),
                   xytext=(0.03, 0.97), textcoords="axes fraction", ha="left", va="top",
                   fontsize=8, color=theme["ink"], arrowprops=leader)
    if cheapest is not best:
        first.annotate(f"least energy among the best:\n{cheapest.conditions}",
                       (cheapest.energy, cheapest.od), xytext=(0.03, 0.06),
                       textcoords="axes fraction", ha="left", va="bottom", fontsize=8,
                       color=theme["ink"], arrowprops=leader)
    handles, labels = first.get_legend_handles_labels()
    _legend(fig, handles[::-1], labels[::-1])
    return fig


def qc_chart(experiments: list[Experiment], theme: dict[str, str]) -> Any:
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    reactors = sorted({r for e in experiments for r in e.reactors})
    total = sum(len(e.reactors) for e in experiments)
    failed = [(e, r, why) for e in experiments for r, why in sorted(e.failures.items())]
    excluded = [e for e in experiments if not e.observed]
    notes = len(failed)
    height = 3.0 + 0.24 * notes
    fig = plt.figure(figsize=(8, height))
    grid_top = 1 - 1.15 / height
    grid_bottom = grid_top - 1.3 / height
    ax = fig.add_axes((0.14, grid_bottom, 0.83, grid_top - grid_bottom))
    word = "experiment" if len(excluded) == 1 else "experiments"
    _titles(
        fig,
        f"QC rejected {notes} of {total} replicates and excluded {len(excluded)} {word}",
        "Each square is one reactor in one experiment. An experiment is learned from only "
        "if enough\nof its replicates pass (here 2 of 3).",
        theme,
    )
    ax.set_axis_off()
    last = max(e.index for e in experiments)
    for e in experiments:
        for row, reactor in enumerate(reactors):
            y = len(reactors) - 1 - row
            if reactor not in e.reactors:
                continue
            bad = reactor in e.failures
            ax.add_patch(Rectangle(
                (e.index - 0.42, y - 0.42), 0.84, 0.84,
                facecolor=theme["critical"] if bad else theme["pass_cell"],
                edgecolor="none",
            ))
            if bad:
                ax.scatter([e.index], [y], marker="x", s=36, color="#ffffff", linewidths=2,
                           zorder=3)
            else:
                ax.plot([e.index - 0.18, e.index - 0.04, e.index + 0.2],
                        [y, y - 0.15, y + 0.17], color=theme["good"], lw=1.6,
                        solid_capstyle="round", zorder=3)
        if not e.observed:
            ax.add_patch(Rectangle(
                (e.index - 0.5, -0.55), 1.0, len(reactors) + 0.1, fill=False,
                edgecolor=theme["critical"], linewidth=1.5,
            ))
    for row, reactor in enumerate(reactors):
        ax.text(-0.75, len(reactors) - 1 - row, reactor, ha="right", va="center",
                fontsize=9, color=theme["secondary"])
    for e in experiments:
        ax.text(e.index, len(reactors) - 0.35, str(e.index), ha="center", va="bottom",
                fontsize=8, color=theme["muted"])
    ax.text(-0.75, len(reactors) - 0.35, "experiment", ha="right", va="bottom",
            fontsize=8, color=theme["muted"])
    ax.set_xlim(-0.6, last + 0.6)
    ax.set_ylim(-0.6, len(reactors) - 0.1)

    y = grid_bottom - 0.32 / height
    fig.text(0.14, y, "Why each replicate failed", fontsize=10, fontweight="bold",
             color=theme["ink"], va="top")
    for e, reactor, why in failed:
        y -= 0.24 / height
        tail = "  · experiment excluded" if not e.observed else ""
        fig.text(0.14, y, f"Experiment {e.index}, {reactor}: {why}{tail}", fontsize=9,
                 color=theme["secondary"], va="top")
    return fig


def plot(
    experiments: list[Experiment], output_dir: Path, startup: int = 8
) -> list[Path]:
    """Write every chart in both themes; return the paths written."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for mode, theme in THEMES.items():
        _style(plt, theme)
        suffix = "" if mode == "light" else "-dark"
        for name, fig in (
            ("progress", progress_chart(experiments, theme, startup)),
            ("tradeoffs", tradeoff_chart(experiments, theme)),
            ("qc", qc_chart(experiments, theme)),
        ):
            path = output_dir / f"{name}{suffix}.png"
            fig.savefig(path, dpi=160)
            plt.close(fig)
            written.append(path)
    return written


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        raise SystemExit("usage: python -m algae_sim.autolab.plot_campaign DATABASE OUTPUT_DIR")
    for path in plot(load(Path(args[0])), Path(args[1])):
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
