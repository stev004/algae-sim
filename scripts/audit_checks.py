# ruff: noqa: E501  (long print lines in a one-off audit report)
"""Independent checks of an external reviewer's claims about validation and benchmark.

    uv run python scripts/audit_checks.py

Read-only: nothing in the repository is modified. Model variants (no shading,
other light paths, other i_k) are built in memory with ``dataclasses.replace``.
Benchmark checks reuse the repo's own scoring (``gains``/``hypervolume``) and
paired test (``compare``) from ``algae_sim.autolab.benchmark`` on the committed
``docs/benchmark/observations.jsonl``.
"""

from __future__ import annotations

import inspect
import math
import random
import statistics
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

from algae_sim import validation as V
from algae_sim.autolab import benchmark as B
from algae_sim.carbon import PhGrowthParams, ph_factor
from algae_sim.growth import GrowthParams, light_factor, temperature_factor
from algae_sim.reactor import ReactorConfig, run_batch

ROOT = Path(__file__).resolve().parents[1]
OBS = ROOT / "docs" / "benchmark" / "observations.jsonl"


def header(text: str) -> None:
    print(f"\n=== {text} ===")


# -- validation helpers ----------------------------------------------------------------


def measured_rates() -> dict[str, float]:
    return {run: math.log(x[1] / x[0]) / 4 for run, (_, _, x) in V.PINTO_2026.items()}


def simulate(run: str, growth: GrowthParams) -> V.RunCheck:
    """Same as validation.simulate, but with any GrowthParams (in memory only)."""
    temperature, _, measured_mg = V.PINTO_2026[run]
    measured = tuple(x / 1000 for x in measured_mg)
    result = run_batch(ReactorConfig(
        temperature_setpoint_c=temperature, light_umol_m2_s=V.LIGHT_UMOL_M2_S,
        ph_setpoint=V.PH, batch_hours=V.DAYS[-1] * 24, ambient_c=15.0,
        start_temperature_c=temperature, start_biomass_g_per_l=measured[0],
        telemetry_interval_s=3600, growth=growth,
    ))
    by_hour = {round(s.elapsed_s / 3600): s.true_biomass_g_per_l for s in result.telemetry}
    model = (measured[0], *(by_hour[d * 24] for d in V.DAYS[1:]))
    return V.RunCheck(run, temperature, measured, model)


def sweep(growth: GrowthParams) -> tuple[int, float, list[V.RunCheck]]:
    checks = [simulate(r, growth) for r in V.PINTO_2026]
    return (sum(c.passes for c in checks),
            statistics.median(c.rate_ratio for c in checks), checks)


def validation_checks() -> None:
    rates = measured_rates()
    base = GrowthParams()

    header("Claim 1: constant growth rate, no T/light dependence")
    # Pure exponential growth at constant mu gives ln(X4/X0)/4 == mu exactly, so the
    # model/measured ratio is mu / measured rate; same +/-30 % rule as validation.py.
    def passes(mu: float) -> int:
        return sum(abs(mu / r - 1) <= V.TOLERANCE for r in rates.values())

    print("measured day 0-4 rates:", {k: round(v, 4) for k, v in rates.items()})
    print(f"median measured rate {statistics.median(rates.values()):.4f}/day, "
          f"mean {statistics.fmean(rates.values()):.4f}/day")
    print(f"mu = 0.273/day passes {passes(0.273)} of 12")
    grid = [round(0.15 + i * 0.0001, 4) for i in range(3001)]
    best = max(passes(m) for m in grid)
    best_mus = [m for m in grid if passes(m) == best]
    print(f"best constant: {best} of 12 for mu in [{best_mus[0]:.4f}, {best_mus[-1]:.4f}]/day")
    print("failing runs at mu=0.273:",
          [r for r, v in rates.items() if abs(0.273 / v - 1) > V.TOLERANCE])
    print(f"model at 5 cm passes {sweep(base)[0]} of 12")

    header("Claim 2: pass count vs light path")
    for cm in (2, 3, 4, 5, 6, 8, 10, 13):
        n, med, _ = sweep(replace(base, light_path_m=cm / 100))
        print(f"  {cm:2d} cm: {n:2d}/12 pass, median ratio {med:.3f}x")

    header("Claim 3: pre-shading model (extinction = 0, in memory)")
    noshade = replace(base, extinction_m2_per_g=0.0)
    n, med, checks = sweep(noshade)
    print(f"no shading: {n}/12 pass, median day0-4 ratio {med:.3f}x, "
          f"range {min(c.rate_ratio for c in checks):.2f}-{max(c.rate_ratio for c in checks):.2f}x")
    fph = ph_factor(V.PH, PhGrowthParams())
    for c in checks:
        if c.run not in ("E1", "E3", "E4"):
            continue
        x0 = c.measured_g_per_l[0]
        gross0 = (noshade.mu_max_per_h * temperature_factor(c.temperature_c, noshade)
                  * light_factor(V.LIGHT_UMOL_M2_S, noshade) * fph
                  * (1 - x0 / noshade.carrying_capacity_g_per_l))
        net0 = (gross0 - noshade.basal_loss_per_h) * 24
        print(f"  {c.run} {c.temperature_c:.0f} C: initial net rate {net0:.3f}/day vs measured "
              f"{rates[c.run]:.3f} ({net0 / rates[c.run]:.2f}x); model day0-4 mean "
              f"{c.model_rate_per_day:.3f} ({c.rate_ratio:.2f}x); model X at d4/d7/d14 = "
              + "/".join(f"{x:.2f}" for x in c.model_g_per_l[1:]) + " g/L")
    at25 = [c for c in checks if c.temperature_c == 25]
    init25 = []
    for c in at25:
        x0 = c.measured_g_per_l[0]
        g = (noshade.mu_max_per_h * temperature_factor(25, noshade)
             * light_factor(V.LIGHT_UMOL_M2_S, noshade) * fph * (1 - x0 / 2.3))
        init25.append((g - noshade.basal_loss_per_h) * 24)
    m25 = statistics.fmean(rates[c.run] for c in at25)
    print(f"  25 C (6 runs): mean initial model rate {statistics.fmean(init25):.3f}/day, "
          f"mean measured {m25:.3f}/day, ratio {statistics.fmean(init25) / m25:.2f}x")
    print(f"  factors at 25 C, 55 umol, pH 7.25: f_T={temperature_factor(25, base):.3f} "
          f"f_I={light_factor(55, base):.3f} f_pH={fph:.3f}")
    nocap = replace(noshade, carrying_capacity_g_per_l=1e6)
    n2, med2, _ = sweep(nocap)
    print(f"no shading AND no carrying capacity: {n2}/12, median ratio {med2:.3f}x "
          "(how much the 2.3 g/L cap shrinks the ratio)")

    header("Claim 4: i_k consistency")
    print(f"Schwaderer mu_max/alpha = 0.94/0.040 = {0.94 / 0.040:.2f}; "
          f"model mu_max {base.mu_max_per_h * 24:.2f}/day / 0.040 = "
          f"{base.mu_max_per_h * 24 / 0.040:.1f}")
    for ik in (23.5, 47.5, 48.0):
        row = []
        for cm in (3, 5, 8):
            n, med, _ = sweep(replace(base, i_k_umol_m2_s=ik, light_path_m=cm / 100))
            row.append(f"{cm} cm {n}/12 ({med:.3f}x)")
        print(f"  i_k={ik}: " + ", ".join(row))
    print("  growth.py provenance lines:")
    for line in inspect.getsource(GrowthParams).splitlines():
        if "mu_max" in line or "i_k" in line or "Schwaderer" in line or "I_opt" in line:
            print("   ", line.strip())

    header("Claim 5: replicate structure")
    groups = defaultdict(list)
    for run, (t, np_ratio, _) in V.PINTO_2026.items():
        groups[(t, np_ratio)].append(run)
    for key, runs in groups.items():
        print(f"  T={key[0]} N:P={key[1]}: {runs}")
    print(f"distinct conditions: {len(groups)}")

    header("Claim 6: temperature rank correlation")
    runs = list(V.PINTO_2026)
    f = [temperature_factor(V.PINTO_2026[r][0], base) for r in runs]
    m = [rates[r] for r in runs]
    print(f"spearman over 12 runs (ties averaged): {V.spearman(f, m):+.4f}")
    by_t = defaultdict(list)
    for r in runs:
        by_t[V.PINTO_2026[r][0]].append(rates[r])
    ts = sorted(by_t)
    print(f"spearman over 5 temperature means (n=5): "
          f"{V.spearman([temperature_factor(t, base) for t in ts], [statistics.fmean(by_t[t]) for t in ts]):+.4f}")
    print(f"note: f_T ties: {len(f) - len(set(f))} of 12 values duplicate another "
          "(6 runs at 25 C), despite the docstring 'no ties expected in model factors'")


# -- benchmark helpers -----------------------------------------------------------------


def by_run(observations, arm):
    out = defaultdict(list)
    for o in observations:
        if o.arm == arm:
            out[o.run].append(o)
    return out


def hv_filtered(obs, od_min: float, shift_reference: bool) -> float:
    pts = [o for o in obs if o.usable and o.od > od_min]
    if shift_reference:
        ref_od = od_min
        box = (B.OD_CEILING - ref_od) * B.REFERENCE["energy"] * B.REFERENCE["co2"]
        gains = [(min(o.od, B.OD_CEILING) - ref_od, *B.gains(o)[1:]) for o in pts]
    else:
        box = B.OD_CEILING * B.REFERENCE["energy"] * B.REFERENCE["co2"]
        gains = [B.gains(o) for o in pts]
    return B.hypervolume(gains) / box


def sign_test(diffs) -> float:
    d = [x for x in diffs if x != 0]
    n, k = len(d), sum(x > 0 for x in d)
    tail = sum(math.comb(n, i) for i in range(min(k, n - k) + 1)) / 2**n
    return min(1.0, 2 * tail)


def wilcoxon_exact(diffs) -> float:
    """Exact two-sided Wilcoxon signed-rank p (zeros dropped, midranks via doubling)."""
    d = [x for x in diffs if x != 0]
    order = sorted(range(len(d)), key=lambda i: abs(d[i]))
    ranks2 = [0] * len(d)  # doubled ranks, integers even with ties
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and abs(d[order[j + 1]]) == abs(d[order[i]]):
            j += 1
        for k in range(i, j + 1):
            ranks2[order[k]] = i + j + 2
        i = j + 1
    w_plus = sum(r for r, x in zip(ranks2, d, strict=True) if x > 0)
    total = sum(ranks2)
    dist = defaultdict(int)
    dist[0] = 1
    for r in ranks2:
        new = defaultdict(int)
        for s, c in dist.items():
            new[s] += c
            new[s + r] += c
        dist = new
    n_all = 2 ** len(d)
    lo = min(w_plus, total - w_plus)
    p = 2 * sum(c for s, c in dist.items() if s <= lo) / n_all
    return min(1.0, p)


def perm_median(diffs, seed: int = 0, trials: int = 20_000) -> float:
    rng = random.Random(seed)
    observed = abs(statistics.median(diffs))
    extreme = sum(
        abs(statistics.median([d if rng.random() < 0.5 else -d for d in diffs])) >= observed
        for _ in range(trials)
    )
    return (extreme + 1) / (trials + 1)


def benchmark_checks() -> None:
    obs = B.load_observations(OBS)
    summary = B.summarise(obs)
    arms = {a: by_run(obs, a) for a in ("optimiser", "random", "grid", "batch")}
    runs = sorted(arms["optimiser"])

    header("Claim 7: headline medians")
    for arm, s in summary["arms"].items():
        print(f"  {arm:9s} median HV {s['median_hypervolume']:.4f}, best OD {s['median_best_od']:.4f}")
    c = summary["comparisons"][0]
    rnd = summary["arms"]["random"]["median_hypervolume"]
    print(f"  opt-random: median paired diff {c['median_difference']:+.5f} = "
          f"{c['median_difference'] / rnd:+.2%} of random median; ratio of medians "
          f"{summary['arms']['optimiser']['median_hypervolume'] / rnd - 1:+.2%}; "
          f"wins {c['optimiser_wins']}/30; p {c['p_value']:.5f}")

    header("Claim 8: hypervolume counting only points above an OD threshold")
    print("  reference (OD 0, 7.2 kWh, 36 g), box 4.0 x 7.2 x 36 unless 'shifted' "
          "(reference OD = threshold, box (4-thr) x 7.2 x 36); test = repo compare()")
    for thr in (0.0, 0.8, 1.0):
        for shift in (False, True):
            if thr == 0.0 and shift:
                continue
            o = [hv_filtered(arms["optimiser"][r], thr, shift) for r in runs]
            b = [hv_filtered(arms["random"][r], thr, shift) for r in runs]
            cmp = B.compare(o, b, "random")
            diffs = [x - y for x, y in zip(o, b, strict=True)]
            print(f"  OD>{thr} {'shifted' if shift else 'fixed  '}: opt med {statistics.median(o):.4f}, "
                  f"rnd med {statistics.median(b):.4f}, paired med diff {cmp.median_difference:+.5f} "
                  f"({cmp.median_difference / statistics.median(b):+.2%} of rnd med), wins "
                  f"{cmp.optimiser_wins}/30, perm(mean) p {cmp.p_value:.3f}, "
                  f"perm(median) p {perm_median(diffs):.3f}, CI [{cmp.ci_low:+.4f}, {cmp.ci_high:+.4f}]")

    header("Claim 9: share of points with OD < 1.0")
    for arm in ("optimiser", "random"):
        guided = [o for o in obs if o.arm == arm and o.index >= B.STARTUP and o.usable]
        allpts = [o for o in obs if o.arm == arm and o.usable]
        print(f"  {arm:9s}: index>=8 {sum(o.od < 1.0 for o in guided)}/{len(guided)} = "
              f"{sum(o.od < 1.0 for o in guided) / len(guided):.1%}; all 18 "
              f"{sum(o.od < 1.0 for o in allpts)}/{len(allpts)} = "
              f"{sum(o.od < 1.0 for o in allpts) / len(allpts):.1%}")

    header("Claim 10: cheapest qualifying condition per run")
    for thr in (1.0, 1.1, 1.2):
        for field, unit in (("energy", "kWh"), ("co2", "g CO2")):
            cheap = {}
            for arm in ("optimiser", "random"):
                cheap[arm] = {r: min((getattr(o, field) for o in arms[arm][r]
                                      if o.usable and o.od >= thr), default=None) for r in runs}
            both = [r for r in runs if cheap["optimiser"][r] is not None
                    and cheap["random"][r] is not None]
            o = [cheap["optimiser"][r] for r in both]
            b = [cheap["random"][r] for r in both]
            diffs = [x - y for x, y in zip(o, b, strict=True)]
            miss = {a: sum(v is None for v in cheap[a].values()) for a in cheap}
            print(f"  OD>={thr} {field:6s}: pairs {len(both)} (no qualifier: opt {miss['optimiser']}, "
                  f"rnd {miss['random']}); median opt {statistics.median(o):.3f} vs rnd "
                  f"{statistics.median(b):.3f} {unit}; median diff {statistics.median(diffs):+.3f}; "
                  f"opt cheaper {sum(d < 0 for d in diffs)}/{len(both)}; sign p {sign_test(diffs):.3f}; "
                  f"Wilcoxon p {wilcoxon_exact(diffs):.3f}")

    header("Claim 11: permutation statistic")
    src = inspect.getsource(B.compare)
    start = inspect.getsourcelines(B.compare)[1]
    for k, line in enumerate(src.splitlines()):
        if "fmean" in line or "median_difference=" in line:
            print(f"  benchmark.py:{start + k}: {line.strip()}")
    pairs = {
        "hv vs random": ("optimiser", "random", 0), "best_od vs random": ("optimiser", "random", 1),
        "hv vs grid": ("optimiser", "grid", 0), "best_od vs grid": ("optimiser", "grid", 1),
    }
    tracks = B.trajectories(obs)
    for name, (a, b, idx) in pairs.items():
        diffs = [tracks[a][r][idx][-1] - tracks[b][r][idx][-1] for r in runs]
        cmp = B.compare([tracks[a][r][idx][-1] for r in runs],
                        [tracks[b][r][idx][-1] for r in runs], b)
        print(f"  {name:18s}: mean diff {statistics.fmean(diffs):+.5f}, median {statistics.median(diffs):+.5f}; "
              f"perm(mean) p {cmp.p_value:.4f}, perm(median) p {perm_median(diffs):.4f}, "
              f"sign p {sign_test(diffs):.4f}, Wilcoxon p {wilcoxon_exact(diffs):.4f}")

    header("Claim 12: batching equivalence")
    eq = summary["batching"]["equal_experiments"]
    seqmed = summary["batching"]["sequential_median_hypervolume"]
    print(f"  equal experiments: median diff {eq['median_difference']:+.6f}, 95% CI "
          f"[{eq['ci_low']:+.5f}, {eq['ci_high']:+.5f}] = [{eq['ci_low'] / seqmed:+.2%}, "
          f"{eq['ci_high'] / seqmed:+.2%}] of seq median {seqmed:.4f}; wins {eq['optimiser_wins']}/30; "
          f"p {eq['p_value']:.4f}")
    diffs = [tracks["batch"][r][0][17] - tracks["optimiser"][r][0][17] for r in runs]
    rng = random.Random(1)
    meds = sorted(statistics.median(rng.choices(diffs, k=30)) for _ in range(5000))
    lo90, hi90 = meds[int(0.05 * 5000)], meds[int(0.95 * 5000) - 1]
    print(f"  90% bootstrap CI (TOST-style) [{lo90:+.5f}, {hi90:+.5f}] = "
          f"[{lo90 / seqmed:+.2%}, {hi90 / seqmed:+.2%}]; mean diff {statistics.fmean(diffs):+.5f}")
    print(f"  for scale: opt-vs-random edge {summary['comparisons'][0]['median_difference']:+.5f}")


if __name__ == "__main__":
    validation_checks()
    benchmark_checks()
