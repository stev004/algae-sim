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
        summary = summarise(load_observations(args.output_dir / "observations.jsonl"))
        (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
