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
        "pass_cell": "#f0efec",
    },
    "dark": {
        "surface": "#1a1a19",
        "ink": "#f0efec",
        "secondary": "#c3c2b7",
        "muted": "#898781",
        "grid": "#2c2c2a",
        "axis": "#383835",
        "context": "#76756f",
        "series": "#3987e5",
        "band": "#262624",
        "good": "#0ca30c",
        "critical": "#d03b3b",
        "pass_cell": "#262624",
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


# Layout is in inches, not figure fractions, so every chart has the same margins
# and the same gaps between title, subtitle, legend and plot whatever its height.
LEFT_IN = 0.22  # left edge of title, subtitle, legend and notes
TITLE_TOP_IN = 0.2
SUBTITLE_TOP_IN = 0.56
LINE_IN = 0.19  # one line of 10 pt text, with leading


def _from_top(fig: Any, inches: float) -> float:
    return 1 - inches / fig.get_figheight()


def _from_left(fig: Any, inches: float) -> float:
    return inches / fig.get_figwidth()


def _titles(fig: Any, title: str, subtitle: str, theme: dict[str, str]) -> float:
    """Draw title and subtitle; return how far down (inches) the subtitle ends."""
    x = _from_left(fig, LEFT_IN)
    fig.text(x, _from_top(fig, TITLE_TOP_IN), title, fontsize=14, fontweight="bold",
             color=theme["ink"], va="top", ha="left")
    fig.text(x, _from_top(fig, SUBTITLE_TOP_IN), subtitle, fontsize=10,
             color=theme["secondary"], va="top", ha="left", linespacing=1.3)
    return SUBTITLE_TOP_IN + LINE_IN * (subtitle.count("\n") + 1)


def _legend(fig: Any, handles: list[Any], labels: list[str], top_in: float) -> float:
    """One row of legend, aligned with the title; return where it ends (inches)."""
    fig.legend(handles, labels, loc="upper left",
               bbox_to_anchor=(_from_left(fig, LEFT_IN - 0.05), _from_top(fig, top_in)),
               ncols=len(labels), fontsize=9, handletextpad=0.3, columnspacing=1.8,
               borderaxespad=0, borderpad=0, handlelength=1.4)
    return top_in + LINE_IN


def _plot_top(fig: Any, top_in: float, bottom_in: float) -> None:
    fig.subplots_adjust(top=_from_top(fig, top_in), bottom=bottom_in / fig.get_figheight())


def _callout(ax: Any, text: str, xy: tuple[float, float], theme: dict[str, str],
             *, x_fraction: float) -> None:
    """Label a point from just above it, no leader line, kept inside the plot."""
    ha = "right" if x_fraction > 0.8 else "left" if x_fraction < 0.2 else "center"
    dx = {"right": 6, "left": -6, "center": 0}[ha]
    ax.annotate(text, xy, xytext=(dx, 11), textcoords="offset points", ha=ha, va="bottom",
                fontsize=9, color=theme["ink"], zorder=5)


def progress_chart(
    experiments: list[Experiment], theme: dict[str, str], startup: int
) -> Any:
    import matplotlib.pyplot as plt

    scored = [e for e in experiments if e.od is not None]
    best = max(scored, key=lambda e: e.od)  # type: ignore[arg-type, return-value]
    fig, ax = plt.subplots(figsize=(8, 4.6))
    fig.subplots_adjust(left=0.09, right=0.97)
    _frame(ax, theme)
    below = _titles(
        fig,
        f"Best culture: OD750 {best.od:.2f}, found in experiment {best.index}",
        "Final optical density per experiment (mean of the replicates that passed QC). "
        "Higher is denser growth.",
        theme,
    )

    last = max(e.index for e in experiments)
    # The shaded band is the start-up phase; the legend's grey dot names it.
    ax.axvspan(-0.6, startup - 0.5, color=theme["band"], lw=0, zorder=0)

    # Best so far, as a step line: the curve that should rise as the optimiser learns.
    xs, ys, running = [], [], 0.0
    for e in scored:
        running = max(running, e.od)  # type: ignore[type-var]
        xs.append(e.index)
        ys.append(running)
    xs.append(last + 0.6)
    ys.append(running)
    ax.step(xs, ys, where="post", color=theme["muted"], lw=1.25, zorder=2,
            solid_joinstyle="round", label="best so far")

    early = [e for e in scored if e.index < startup]
    later = [e for e in scored if e.index >= startup]
    ring = theme["surface"]
    dots = {"s": 52, "edgecolor": ring, "linewidth": 2, "zorder": 3}
    ax.scatter([e.index for e in early], [e.od for e in early], color=theme["context"],
               label="random start-up trial", **dots)
    ax.scatter([e.index for e in later], [e.od for e in later], color=theme["series"],
               label="optimiser's choice", **dots)

    top = max(e.od for e in scored)  # type: ignore[type-var]
    for e in experiments:
        if e.observed:
            continue
        ax.scatter([e.index], [0.04 * top], marker="X", s=64, color=theme["critical"],
                   edgecolor=ring, linewidth=1, zorder=3)
        ax.annotate("excluded by QC", (e.index, 0.04 * top), xytext=(0, 9),
                    textcoords="offset points", ha="center", fontsize=8,
                    color=theme["secondary"])

    _callout(ax, f"Best: {best.conditions}", (best.index, best.od), theme,
             x_fraction=(best.index + 0.6) / (last + 1.2))
    ax.set_xlim(-0.6, last + 0.6)
    ax.set_ylim(0, top * 1.2)
    ax.set_xticks(range(0, last + 1, 2))
    ax.set_xlabel("experiment (in the order run)", labelpad=8)
    ax.set_ylabel("final OD750", labelpad=8)
    ax.grid(axis="x", visible=False)
    handles, labels = ax.get_legend_handles_labels()
    order = [labels.index(n) for n in
             ("random start-up trial", "optimiser's choice", "best so far")]
    end = _legend(fig, [handles[i] for i in order], [labels[i] for i in order], below + 0.14)
    _plot_top(fig, end + 0.12, 0.6)
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
    fig, axes = plt.subplots(1, len(panels), figsize=(8, 4.7), sharey=True, squeeze=False)
    fig.subplots_adjust(left=0.09, right=0.97, wspace=0.1)
    costs = "energy and CO₂" if has_co2 else "energy"
    below = _titles(
        fig,
        f"{len(front)} of {len(scored)} experiments are best trade-offs",
        f"Each dot is one experiment. Up is more growth, left is cheaper. Blue: no other "
        f"experiment grew more\nfor less {costs} at the same time (the Pareto front).",
        theme,
    )
    best = max(scored, key=lambda e: e.od)  # type: ignore[arg-type, return-value]
    cheapest = min(front, key=lambda e: e.energy)  # type: ignore[arg-type, return-value]
    ring = theme["surface"]
    dots = {"s": 52, "edgecolor": ring, "linewidth": 2}
    for ax, (key, label) in zip(axes[0], panels, strict=True):
        _frame(ax, theme)
        ax.scatter([getattr(e, key) for e in others], [e.od for e in others],
                   color=theme["context"], zorder=3, label="other experiments", **dots)
        ax.scatter([getattr(e, key) for e in front], [e.od for e in front],
                   color=theme["series"], zorder=4, label="best trade-offs", **dots)
        ax.set_xlabel(label, labelpad=8)
        ax.set_xlim(left=0)
    first = axes[0][0]
    first.set_ylabel("final OD750", labelpad=8)
    first.set_ylim(0, max(e.od for e in scored) * 1.2)  # type: ignore[type-var]
    right = first.get_xlim()[1]
    _callout(first, f"most growth: {best.conditions}", (best.energy, best.od), theme,
             x_fraction=best.energy / right)  # type: ignore[operator]
    if cheapest is not best:
        first.annotate(f"least energy among the best:\n{cheapest.conditions}",
                       (cheapest.energy, cheapest.od), xytext=(4, -12),
                       textcoords="offset points", ha="left", va="top", fontsize=8.5,
                       color=theme["ink"], linespacing=1.35, zorder=5)
    handles, labels = first.get_legend_handles_labels()
    end = _legend(fig, handles[::-1], labels[::-1], below + 0.14)
    _plot_top(fig, end + 0.12, 0.6)
    return fig


def qc_chart(experiments: list[Experiment], theme: dict[str, str]) -> Any:
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch, Rectangle

    reactors = sorted({r for e in experiments for r in e.reactors})
    total = sum(len(e.reactors) for e in experiments)
    failed = [(e, r, why) for e in experiments for r, why in sorted(e.failures.items())]
    excluded = [e for e in experiments if not e.observed]
    # One note per experiment and reason, naming every reactor it hit.
    notes: dict[tuple[int, str], list[str]] = {}
    for e, reactor, why in failed:
        notes.setdefault((e.index, why), []).append(reactor)
    observed = {e.index: e.observed for e in experiments}

    word = "experiment" if len(excluded) == 1 else "experiments"
    subtitle = ("Each square is one reactor in one experiment. An experiment is learned "
                "from only if enough\nof its replicates pass (here 2 of 3).")
    grid_h = 0.36 * (len(reactors) + 1)  # rows plus the experiment-number header
    height = (SUBTITLE_TOP_IN + LINE_IN * 2 + 0.3 + grid_h + 0.3 + LINE_IN * 1.2
              + LINE_IN * len(notes) + 0.25)
    fig = plt.figure(figsize=(8, height))
    below = _titles(
        fig,
        f"QC rejected {len(failed)} of {total} replicates and excluded "
        f"{len(excluded)} {word}",
        subtitle,
        theme,
    )
    grid_top = below + 0.3
    ax = fig.add_axes((0.14, _from_top(fig, grid_top + grid_h), 0.83, grid_h / height))
    ax.set_axis_off()
    last = max(e.index for e in experiments)
    for e in experiments:
        for row, reactor in enumerate(reactors):
            y = len(reactors) - 1 - row
            if reactor not in e.reactors:
                continue
            bad = reactor in e.failures
            ax.add_patch(FancyBboxPatch(
                (e.index - 0.4, y - 0.4), 0.8, 0.8,
                boxstyle="round,pad=0,rounding_size=0.12",
                facecolor=theme["critical"] if bad else theme["pass_cell"],
                edgecolor="none", mutation_aspect=1,
            ))
            if bad:
                ax.scatter([e.index], [y], marker="x", s=30, color="#ffffff",
                           linewidths=1.8, zorder=3)
            else:  # a small tick: passes are the quiet majority
                ax.plot([e.index - 0.13, e.index - 0.03, e.index + 0.15],
                        [y + 0.01, y - 0.11, y + 0.13], color=theme["good"], lw=1.3,
                        solid_capstyle="round", solid_joinstyle="round", zorder=3)
        if not e.observed:
            ax.add_patch(Rectangle(
                (e.index - 0.5, -0.52), 1.0, len(reactors) + 0.04, fill=False,
                edgecolor=theme["critical"], linewidth=1.4,
            ))
    for row, reactor in enumerate(reactors):
        ax.text(-0.75, len(reactors) - 1 - row, reactor, ha="right", va="center",
                fontsize=9, color=theme["secondary"])
    for e in experiments:
        ax.text(e.index, len(reactors) - 0.38, str(e.index), ha="center", va="bottom",
                fontsize=8, color=theme["muted"])
    ax.text(-0.75, len(reactors) - 0.38, "experiment", ha="right", va="bottom",
            fontsize=8, color=theme["muted"])
    ax.set_xlim(-0.6, last + 0.6)
    ax.set_ylim(-0.6, len(reactors) + 0.3)

    x = _from_left(fig, LEFT_IN)
    y_in = grid_top + grid_h + 0.3
    fig.text(x, _from_top(fig, y_in), "Why replicates failed", fontsize=10,
             fontweight="bold", color=theme["ink"], va="top")
    y_in += LINE_IN * 1.2
    for (index, why), hit in notes.items():
        tail = "  (experiment excluded)" if not observed[index] else ""
        fig.text(x, _from_top(fig, y_in), f"Experiment {index}", fontsize=9,
                 color=theme["ink"], va="top")
        fig.text(x + _from_left(fig, 1.05), _from_top(fig, y_in),
                 f"{' and '.join(hit)}: {why}{tail}", fontsize=9,
                 color=theme["secondary"], va="top")
        y_in += LINE_IN
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
