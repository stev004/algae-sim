"""Benchmark AutoLab's optimiser against random and grid search on the simulated reactor.

    uv run python -m algae_sim.autolab.benchmark run OUTPUT_DIR [--runs 30] [--workers 8]
    uv run python -m algae_sim.autolab.benchmark analyse OUTPUT_DIR

Design (agreed with Steven, 2026-11-13):

* **Arms.** AutoLab's optimiser (``bayesian``: 8 random start-up experiments, then
  multi-objective TPE), pure random search, and a fixed 3 x 3 x 2 grid. Every arm
  gets the same budget: 18 experiments x 3 replicate reactors.
* **Paired runs.** Run ``k`` gives all three arms the same simulated world (device
  seed ``k + 1``), so differences come from the strategy, not from luckier noise.
* **Independent seeds.** AutoLab seeds each trial's sampler with ``seed + index``,
  so neighbouring campaign seeds would share most of their start-up designs. Seeds
  are spaced 1000 apart, more than the budget, so no two runs share a sampler seed.
* **Pinned sampler.** ``prefer_gp=False`` keeps TPE even if PyTorch is installed.
* **No injected faults.** The benchmark measures search, not QC.

The score is hypervolume over (OD750, energy, CO2): the share of the outcome space
an arm's results dominate, against a reference point set from the device's limits
alone (see ``REFERENCE``), never from observed data. Best-so-far OD750 is reported
alongside because it is easier to read.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import shutil
import statistics
import sys
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs" / "culture-algae-sim.yaml"
POLICY = ROOT / "configs" / "culture-policy.yaml"

BUDGET = 18
STARTUP = 8
GRID_LEVELS = {
    "temperature": [22.0, 28.0, 34.0],
    "light_intensity": [60.0, 180.0, 400.0],
    "ph": [6.5, 8.0],
}
ARMS = ("optimiser", "random", "grid")

# Worst case per objective, from device limits only (docs/model.md):
# * OD750: 0, nothing grew. Best possible is the sensor's 4.0 ceiling.
# * energy: heater flat out (100 W) plus lights at the device maximum
#   (500 umol/m2/s x 0.2 W) for the full 36 h = 7.2 kWh.
# * CO2: valve open for 36 h at 1 g/h = 36 g.
REFERENCE = {"od": 0.0, "energy": 7.2, "co2": 36.0}
OD_CEILING = 4.0


@dataclass(frozen=True, slots=True)
class Observation:
    """One experiment as AutoLab's optimiser saw it (replicate means)."""

    arm: str
    run: int
    index: int  # 0-based position in the campaign
    temperature: float
    light: float
    ph: float
    od: float | None
    energy: float | None
    co2: float | None
    usable: bool  # passed QC and has every objective


# -- running ---------------------------------------------------------------------------


def run_config(arm: str, run: int) -> dict[str, Any]:
    """The campaign YAML for one arm and run, built from the demo config."""
    config = yaml.safe_load(CONFIG.read_text())
    config["campaign"]["name"] = f"benchmark_{arm}_{run:02d}"
    config["campaign"]["max_experiments"] = BUDGET
    config["campaign"]["seed"] = 1000 * (run + 1)
    settings = config["execution"]["controller"]["settings"]
    settings.update(seed=run + 1, heater_faults={}, culture_crashes={}, co2_valve_faults={})
    optimization = config["optimization"]
    if arm == "optimiser":
        optimization.update(strategy="bayesian", startup_experiments=STARTUP)
    elif arm == "random":
        optimization.update(strategy="random", startup_experiments=0)
    elif arm == "grid":
        optimization.update(strategy="grid", startup_experiments=0, grid_levels=GRID_LEVELS)
    else:
        raise ValueError(f"unknown arm {arm!r}")
    return config


def run_one(
    arm: str, run: int, output_dir: Path, keep_database: bool = False
) -> list[Observation]:
    """Run one campaign in its own directory and database; return its observations.

    Each run's ``campaign.yaml`` is kept. Its database and artifact files (about 20 MB) are
    dropped unless asked for, since the same config and seeds reproduce it exactly.
    """
    from autonomous_lab.campaigns.culture_service import CultureCampaignService
    from autonomous_lab.campaigns.loader import load_culture_campaign, load_culture_policy
    from autonomous_lab.storage import Database, Storage

    work = output_dir / "runs" / f"{arm}-{run:02d}"
    work.mkdir(parents=True, exist_ok=True)
    config_path = work / "campaign.yaml"
    config_path.write_text(yaml.safe_dump(run_config(arm, run), sort_keys=False))
    database = work / "autolab.sqlite3"
    database.unlink(missing_ok=True)
    storage = Storage(Database(database))
    storage.initialize()
    service = CultureCampaignService(
        storage,
        policy=load_culture_policy(POLICY),
        optimizer_root=work / "optimizer",
        artifact_root=work / "artifacts",
        prefer_gp=False,
    )
    campaign_id = service.create(
        load_culture_campaign(config_path),
        campaign_id=uuid5(NAMESPACE_URL, f"algae-sim:benchmark:{arm}:{run}"),
    )
    asyncio.run(service.run(campaign_id))
    observations = read_observations(database, arm, run)
    if not keep_database:
        storage.database.engine.dispose()
        for path in (database, *work.glob("autolab.sqlite3-*")):
            path.unlink(missing_ok=True)
        for folder in ("optimizer", "artifacts"):
            shutil.rmtree(work / folder, ignore_errors=True)
    return observations


def read_observations(database: Path, arm: str, run: int) -> list[Observation]:
    import sqlite3

    db = sqlite3.connect(database)
    try:
        rows = db.execute(
            "select e.ordinal, p.snapshot, o.objective_values, o.qc_eligible "
            "from experiments e join proposals p on p.experiment_id = e.id "
            "left join optimizer_observations o on o.experiment_id = e.id "
            "order by e.ordinal"
        ).fetchall()
    finally:
        db.close()
    observations = []
    for ordinal, snapshot, objectives, qc_eligible in rows:
        values = json.loads(snapshot)["parameter_values"]
        seen = json.loads(objectives) if objectives else {}

        def number(key: str, seen: dict[str, Any] = seen) -> float | None:
            return None if seen.get(key) is None else float(seen[key])

        od, energy, co2 = number("final_od750"), number("energy_kwh"), number("co2_g")
        observations.append(Observation(
            arm=arm,
            run=run,
            index=ordinal,
            temperature=float(values["temperature"]["value"]),
            light=float(values["light_intensity"]["value"]),
            ph=float(values["ph"]["value"]),
            od=od,
            energy=energy,
            co2=co2,
            usable=bool(qc_eligible) and None not in (od, energy, co2),
        ))
    return observations


def _job(args: tuple[str, int, str, bool]) -> list[dict[str, Any]]:
    arm, run, output_dir, keep = args
    return [asdict(o) for o in run_one(arm, run, Path(output_dir), keep)]


def run_all(
    output_dir: Path,
    runs: int,
    workers: int,
    arms: Sequence[str] = ARMS,
    keep_databases: bool = False,
) -> Path:
    """Run every arm x run in parallel processes; write ``observations.jsonl``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    jobs = [(arm, run, str(output_dir), keep_databases) for run in range(runs) for arm in arms]
    results = output_dir / "observations.jsonl"
    with ProcessPoolExecutor(max_workers=workers) as pool:
        batches = list(pool.map(_job, jobs))
    new = [row for batch in batches for row in batch]
    # Keep rows from arms not rerun this time, so a later arm can join the same file.
    kept = [] if not results.exists() else [
        row for row in map(json.loads, results.read_text().splitlines())
        if row["arm"] not in arms
    ]
    rows = sorted(kept + new, key=lambda r: (ARMS.index(r["arm"]), r["run"], r["index"]))
    results.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return results


def load_observations(path: Path) -> list[Observation]:
    return [Observation(**json.loads(line)) for line in path.read_text().splitlines() if line]


# -- scoring ---------------------------------------------------------------------------


def gains(o: Observation) -> tuple[float, float, float]:
    """How far an observation beats the reference point on each objective (>= 0)."""
    return (
        max(0.0, min(o.od, OD_CEILING) - REFERENCE["od"]),  # type: ignore[arg-type]
        max(0.0, REFERENCE["energy"] - o.energy),  # type: ignore[operator]
        max(0.0, REFERENCE["co2"] - o.co2),  # type: ignore[operator]
    )


def hypervolume(points: Iterable[tuple[float, float, float]]) -> float:
    """Volume dominated by ``points`` (all objectives maximised, measured from 0).

    Exact for small sets: sweep the first objective from the top down; between
    two consecutive values every point at or above the slice contributes its
    (second, third) rectangle, and the union of rectangles is a 2-D staircase.
    """
    pts = sorted({p for p in points if min(p) > 0}, reverse=True)
    volume = 0.0
    for i, (a, _, _) in enumerate(pts):
        lower = pts[i + 1][0] if i + 1 < len(pts) else 0.0
        if a == lower:
            continue
        volume += (a - lower) * _area([(p[1], p[2]) for p in pts[: i + 1]])
    return volume


def _area(rects: list[tuple[float, float]]) -> float:
    area, top = 0.0, 0.0
    for b, c in sorted(rects, reverse=True):
        if c > top:
            area += b * (c - top)
            top = c
    return area


def normalised_hypervolume(observations: Iterable[Observation]) -> float:
    """Hypervolume as a share of the box between the reference and the ideal point."""
    box = OD_CEILING * REFERENCE["energy"] * REFERENCE["co2"]
    return hypervolume(gains(o) for o in observations if o.usable) / box


def trajectories(
    observations: Sequence[Observation],
) -> dict[str, dict[int, tuple[list[float], list[float]]]]:
    """Per arm and run: hypervolume and best OD750 after each experiment."""
    by_run: dict[tuple[str, int], list[Observation]] = {}
    for o in observations:
        by_run.setdefault((o.arm, o.run), []).append(o)
    out: dict[str, dict[int, tuple[list[float], list[float]]]] = {}
    for (arm, run), obs in by_run.items():
        obs.sort(key=lambda o: o.index)
        hv, best, so_far = [], [], 0.0
        for n in range(1, len(obs) + 1):
            hv.append(normalised_hypervolume(obs[:n]))
            usable = [o.od for o in obs[:n] if o.usable]
            so_far = max([so_far, *usable])  # type: ignore[list-item]
            best.append(so_far)
        out.setdefault(arm, {})[run] = (hv, best)
    return out


# -- statistics ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Comparison:
    """Paired comparison of the optimiser against one baseline."""

    baseline: str
    pairs: int
    optimiser_wins: int
    median_difference: float
    ci_low: float
    ci_high: float
    p_value: float


def compare(
    optimiser: Sequence[float], baseline: Sequence[float], name: str, seed: int = 0
) -> Comparison:
    """Sign-flip permutation test on paired differences, plus a bootstrap CI.

    Both are distribution-free: with 30 pairs there is no reason to assume the
    differences are normal. The permutation test asks how often randomly swapping
    arm labels within pairs gives a mean difference at least as large (two-sided);
    the bootstrap resamples pairs to put a 95 % interval on the median difference.
    """
    diffs = [a - b for a, b in zip(optimiser, baseline, strict=True)]
    rng = random.Random(seed)
    observed = abs(statistics.fmean(diffs))
    trials = 20_000
    extreme = sum(
        abs(statistics.fmean(d if rng.random() < 0.5 else -d for d in diffs)) >= observed
        for _ in range(trials)
    )
    medians = sorted(
        statistics.median(rng.choices(diffs, k=len(diffs))) for _ in range(5_000)
    )
    return Comparison(
        baseline=name,
        pairs=len(diffs),
        optimiser_wins=sum(d > 0 for d in diffs),
        median_difference=statistics.median(diffs),
        ci_low=medians[int(0.025 * len(medians))],
        ci_high=medians[int(0.975 * len(medians)) - 1],
        p_value=(extreme + 1) / (trials + 1),
    )


def summarise(observations: Sequence[Observation]) -> dict[str, Any]:
    tracks = trajectories(observations)
    final = {arm: {run: hv[-1] for run, (hv, _) in runs.items()} for arm, runs in tracks.items()}
    best = {arm: {run: od[-1] for run, (_, od) in runs.items()} for arm, runs in tracks.items()}
    summary: dict[str, Any] = {
        "arms": {
            arm: {
                "runs": len(final[arm]),
                "median_hypervolume": statistics.median(final[arm].values()),
                "median_best_od": statistics.median(best[arm].values()),
            }
            for arm in final
        },
        "comparisons": [],
    }
    if "optimiser" in final:
        for baseline in (a for a in ARMS if a in final and a != "optimiser"):
            runs = sorted(set(final["optimiser"]) & set(final[baseline]))
            for metric, table in (("hypervolume", final), ("best_od", best)):
                c = compare([table["optimiser"][r] for r in runs],
                            [table[baseline][r] for r in runs], baseline)
                summary["comparisons"].append({"metric": metric, **asdict(c)})
    return summary


# -- charts ---------------------------------------------------------------------------

# Categorical slots 1-3 of the validated palette, light and dark. The light aqua is
# under 3:1 against the surface, so every line is also labelled directly.
ARM_COLOURS = {
    "light": {"optimiser": "#2a78d6", "random": "#eb6834", "grid": "#1baf7a"},
    "dark": {"optimiser": "#3987e5", "random": "#d95926", "grid": "#199e70"},
}
ARM_LABELS = {"optimiser": "AutoLab optimiser", "random": "random search", "grid": "grid"}
END_LABELS = {"optimiser": "optimiser", "random": "random", "grid": "grid"}  # line ends


def _quantiles(values: Sequence[float]) -> tuple[float, float, float]:
    q = statistics.quantiles(values, n=4, method="inclusive")
    return q[0], statistics.median(values), q[2]


def headline(summary: dict[str, Any]) -> str:
    """The progress chart's title: the result, stated from the numbers."""
    found = {(c["metric"], c["baseline"]): c for c in summary["comparisons"]}
    hv, od = found[("hypervolume", "random")], found[("best_od", "random")]
    # The paired median difference, as a share of random search's median.
    gain = hv["median_difference"] / summary["arms"]["random"]["median_hypervolume"]
    trade = f"{gain:.1%} better trade-offs" if hv["ci_low"] > 0 else "no better trade-offs"
    cultures = ("not denser cultures" if od["ci_low"] <= 0 <= od["ci_high"]
                else "and denser cultures" if od["median_difference"] > 0
                else "but less dense cultures")
    return f"The optimiser finds {trade}, {cultures}"


def _label_line_ends(ax: Any, ends: list[tuple[float, str]], theme: dict[str, str]) -> None:
    """Label each line at its right end, nudged apart so labels never overlap."""
    low, high = ax.get_ylim()
    gap = 0.07 * (high - low)  # about one line of 8.5 pt text on these axes
    placed: list[float] = []
    for y, arm in sorted(ends, reverse=True):
        y = min([y, *[p - gap for p in placed]])
        placed.append(y)
        ax.annotate(END_LABELS[arm], (1, y), xycoords=("axes fraction", "data"),
                    xytext=(6, 0), textcoords="offset points", va="center",
                    fontsize=8.5, color=theme["secondary"], annotation_clip=False)


def progress_figure(
    observations: Sequence[Observation], summary: dict[str, Any], mode: str
) -> Any:
    import matplotlib.pyplot as plt

    from algae_sim.autolab import plot_campaign as pc

    theme, colours = pc.THEMES[mode], ARM_COLOURS[mode]
    tracks = trajectories(observations)
    arms = [a for a in ARMS if a in tracks]
    fig, axes = plt.subplots(1, 2, figsize=(8, 4.7), squeeze=False)
    fig.subplots_adjust(left=0.08, right=0.89, wspace=0.38)
    n = len(next(iter(tracks[arms[0]].values()))[0])
    runs = len(tracks[arms[0]])
    below = pc._titles(
        fig,
        headline(summary),
        f"Median of {runs} paired runs per method, with the middle half of runs shaded. "
        f"Each run: {n} experiments\nx 3 reactors on the simulated Chlorella reactor. "
        "Run k gives every method the same simulated world.",
        theme,
    )
    xs = list(range(1, n + 1))
    panels = (
        (0, "trade-off covered (hypervolume)", "share of outcome space"),
        (1, "best culture so far", "final OD750"),
    )
    for ax, (which, heading, ylabel) in zip(axes[0], panels, strict=True):
        pc._frame(ax, theme)
        ax.set_title(heading, loc="left", fontsize=10, color=theme["ink"], pad=8)
        ends: list[tuple[float, str]] = []
        for arm in arms:
            series = [track[which] for track in tracks[arm].values()]
            low, mid, high = zip(*(_quantiles([s[i] for s in series]) for i in range(n)),
                                 strict=True)
            ax.fill_between(xs, low, high, color=colours[arm], alpha=0.14, lw=0)
            ax.plot(xs, mid, color=colours[arm], lw=2, solid_capstyle="round",
                    label=ARM_LABELS[arm])
            ends.append((mid[-1], arm))
        if "optimiser" in arms:
            ax.axvline(STARTUP + 0.5, color=theme["muted"], lw=0.8, ls=(0, (3, 3)))
            ax.annotate("optimiser starts\nchoosing", (STARTUP + 0.5, 0.03),
                        xycoords=("data", "axes fraction"), xytext=(4, 0),
                        textcoords="offset points", fontsize=8, color=theme["muted"])
        if which == 0:
            ax.set_ylim(bottom=0)
        _label_line_ends(ax, ends, theme)
        ax.set_xlim(1, n)
        ax.set_xticks([1, 6, 12, 18] if n == 18 else xs[:: max(1, n // 4)])
        ax.set_xlabel("experiments run", labelpad=8)
        ax.set_ylabel(ylabel, labelpad=8)
    handles, labels = axes[0][0].get_legend_handles_labels()
    end = pc._legend(fig, handles, labels, below + 0.14)
    pc._plot_top(fig, end + 0.45, 0.6)
    return fig


def pairs_figure(summary: dict[str, Any], observations: Sequence[Observation], mode: str) -> Any:
    """Per-run paired differences, optimiser minus each baseline, with median and CI."""
    import matplotlib.pyplot as plt

    from algae_sim.autolab import plot_campaign as pc

    theme, colours = pc.THEMES[mode], ARM_COLOURS[mode]
    tracks = trajectories(observations)
    baselines = [a for a in ARMS if a in tracks and a != "optimiser"]
    fig, axes = plt.subplots(1, 2, figsize=(8, 1.9 + 0.75 * len(baselines)), squeeze=False)
    fig.subplots_adjust(left=0.2, right=0.97, wspace=0.12)
    comparisons = {(c["metric"], c["baseline"]): c for c in summary["comparisons"]}
    hv = comparisons[("hypervolume", "random")]
    below = pc._titles(
        fig,
        f"The optimiser beat random search in {hv['optimiser_wins']} of {hv['pairs']} "
        f"paired runs on the trade-off",
        "Each dot is one run: optimiser minus baseline, same simulated world. Right of "
        "zero favours the optimiser.\nBar: 95 % bootstrap interval for the median "
        "difference. p: paired sign-flip permutation test.",
        theme,
    )
    rng = random.Random(0)  # vertical jitter only, so overlapping dots stay visible
    for ax, (metric, heading) in zip(
        axes[0], (("hypervolume", "trade-off covered"), ("best_od", "best OD750")), strict=True
    ):
        pc._frame(ax, theme)
        ax.grid(axis="y", visible=False)
        ax.set_title(heading, loc="left", fontsize=10, color=theme["ink"], pad=8)
        ax.axvline(0, color=theme["axis"], lw=1)
        which = 0 if metric == "hypervolume" else 1
        for row, baseline in enumerate(baselines):
            y = len(baselines) - 1 - row
            runs = sorted(set(tracks["optimiser"]) & set(tracks[baseline]))
            diffs = [tracks["optimiser"][r][which][-1] - tracks[baseline][r][which][-1]
                     for r in runs]
            ax.scatter(diffs, [y + rng.uniform(-0.18, 0.18) for _ in diffs], s=22,
                       color=colours[baseline], edgecolor=theme["surface"], linewidth=1,
                       zorder=3)
            c = comparisons[(metric, baseline)]
            ax.plot([c["ci_low"], c["ci_high"]], [y - 0.34] * 2, color=theme["ink"], lw=2,
                    solid_capstyle="round", zorder=4)
            ax.plot([c["median_difference"]], [y - 0.34], "o", color=theme["ink"], ms=5,
                    zorder=5)
            p = c["p_value"]
            ax.annotate(f"p {'< 0.001' if p < 0.001 else f'= {p:.2f}'}",
                        (1, y - 0.34), xycoords=("axes fraction", "data"), xytext=(-2, 0),
                        textcoords="offset points", ha="right", va="center", fontsize=8,
                        color=theme["secondary"])
        ax.set_yticks(range(len(baselines)))
        ax.set_yticklabels([f"vs {ARM_LABELS[b]}" for b in reversed(baselines)]
                           if ax is axes[0][0] else [])
        ax.set_ylim(-0.7, len(baselines) - 0.5)
        ax.set_xlabel("difference (optimiser minus baseline)", labelpad=8)
    pc._plot_top(fig, below + 0.42, 0.55)
    return fig


def plot(observations: Sequence[Observation], summary: dict[str, Any], output_dir: Path
         ) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from algae_sim.autolab import plot_campaign as pc

    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for mode in ("light", "dark"):
        pc._style(plt, pc.THEMES[mode])
        suffix = "" if mode == "light" else "-dark"
        for name, fig in (
            ("benchmark-progress", progress_figure(observations, summary, mode)),
            ("benchmark-pairs", pairs_figure(summary, observations, mode)),
        ):
            path = output_dir / f"{name}{suffix}.png"
            fig.savefig(path, dpi=pc.DPI)
            plt.close(fig)
            written.append(path)
    return written


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("output_dir", type=Path)
    run.add_argument("--runs", type=int, default=30)
    run.add_argument("--workers", type=int, default=8)
    run.add_argument("--arms", nargs="+", choices=ARMS, default=list(ARMS))
    run.add_argument("--keep-databases", action="store_true")
    analyse = sub.add_parser("analyse")
    analyse.add_argument("output_dir", type=Path)
    args = parser.parse_args(argv)
    if args.command == "run":
        path = run_all(args.output_dir, args.runs, args.workers, args.arms, args.keep_databases)
        print(f"wrote {path}")
    else:
        observations = load_observations(args.output_dir / "observations.jsonl")
        summary = summarise(observations)
        (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
        for path in plot(observations, summary, args.output_dir / "charts"):
            print(f"wrote {path}")


if __name__ == "__main__":
    main(sys.argv[1:])
