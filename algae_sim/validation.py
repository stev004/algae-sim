"""Check the growth model against an independent Chlorella vulgaris experiment.

    uv run python -m algae_sim.validation

The data are from Pinto et al. 2026, *Marine Drugs* 24:124, doi:10.3390/md24040124
(CC BY 4.0), supplementary Table S1: biomass of strain CCAP 211/11B at days 0, 4,
7 and 14 in 12 batch runs at 18-32 degC. None of the model's parameters came
from this paper, so it is an out-of-sample check.

The rules were fixed before the comparison was run, so the result can't be
tuned to pass:

* **No fitting.** The model runs at the paper's stated conditions: continuous
  light of 55 umol/m2/s, pH 7.25 (the middle of the reported 7-7.5), each run's
  measured starting biomass and temperature. The ambient is set below 18 degC,
  because the simulated reactor has no cooling.
* **Measure 1: early growth.** The mean specific growth rate over days 0-4,
  ln(X4 / X0) / 4, model against measured. **Pass if within +/-30 %**, the
  typical spread of growth rates for one species between labs.
* **Measure 2: the whole curve.** Model / measured biomass at days 4, 7 and 14.
  Late divergence was expected in advance, because the model has no light
  shading, no carbon limitation under air-only aeration and no nitrogen limit.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from algae_sim.reactor import ReactorConfig, run_batch

DAYS = (0, 4, 7, 14)
LIGHT_UMOL_M2_S = 55.0
PH = 7.25
TOLERANCE = 0.30

# Supplementary Table S1: run -> (temperature degC, N:P ratio, mg dw/L at DAYS).
PINTO_2026: dict[str, tuple[float, float, tuple[int, int, int, int]]] = {
    "E1": (25, 9.0, (180, 554, 772, 933)),
    "E2": (25, 9.0, (170, 574, 747, 892)),
    "E3": (32, 9.0, (247, 410, 540, 675)),
    "E4": (18, 9.0, (216, 690, 850, 1150)),
    "E5": (20, 3.0, (182, 617, 745, 980)),
    "E6": (30, 3.0, (177, 463, 615, 800)),
    "E7": (20, 27.0, (165, 625, 760, 1050)),
    "E8": (30, 27.0, (174, 446, 652, 747)),
    "E9": (25, 1.9, (230, 569, 690, 875)),
    "E10": (25, 42.6, (191, 569, 756, 840)),
    "E11": (25, 9.0, (207, 618, 820, 1020)),
    "E12": (25, 9.0, (204, 547, 710, 790)),
}


@dataclass(frozen=True, slots=True)
class RunCheck:
    run: str
    temperature_c: float
    measured_g_per_l: tuple[float, ...]
    model_g_per_l: tuple[float, ...]

    @property
    def measured_rate_per_day(self) -> float:
        return math.log(self.measured_g_per_l[1] / self.measured_g_per_l[0]) / 4

    @property
    def model_rate_per_day(self) -> float:
        return math.log(self.model_g_per_l[1] / self.model_g_per_l[0]) / 4

    @property
    def rate_ratio(self) -> float:
        return self.model_rate_per_day / self.measured_rate_per_day

    @property
    def passes(self) -> bool:
        return abs(self.rate_ratio - 1) <= TOLERANCE


def simulate(run: str) -> RunCheck:
    temperature, _, measured_mg = PINTO_2026[run]
    measured = tuple(x / 1000 for x in measured_mg)
    result = run_batch(ReactorConfig(
        temperature_setpoint_c=temperature,
        light_umol_m2_s=LIGHT_UMOL_M2_S,
        ph_setpoint=PH,
        batch_hours=DAYS[-1] * 24,
        ambient_c=15.0,
        start_temperature_c=temperature,
        start_biomass_g_per_l=measured[0],
        telemetry_interval_s=3600,
    ))
    by_hour = {round(s.elapsed_s / 3600): s.true_biomass_g_per_l for s in result.telemetry}
    model = (measured[0], *(by_hour[d * 24] for d in DAYS[1:]))
    return RunCheck(run, temperature, measured, model)


def spearman(x: list[float], y: list[float]) -> float:
    """Rank correlation (no ties expected in model factors; ties in data get averaged)."""

    def ranks(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            for k in range(i, j + 1):
                out[order[k]] = (i + j) / 2
            i = j + 1
        return out

    return statistics.correlation(ranks(x), ranks(y))


def main() -> None:
    checks = [simulate(run) for run in PINTO_2026]
    print("run   T   rate/day measured  model  model/measured  pass   "
          "X day4 meas/model   X day14 meas/model")
    for c in checks:
        print(f"{c.run:4} {c.temperature_c:3.0f}   {c.measured_rate_per_day:6.3f}  "
              f"{c.model_rate_per_day:6.3f}  {c.rate_ratio:5.2f}x         "
              f"{'yes' if c.passes else 'no ':3}    "
              f"{c.measured_g_per_l[1]:.2f}/{c.model_g_per_l[1]:.2f}         "
              f"{c.measured_g_per_l[3]:.2f}/{c.model_g_per_l[3]:.2f}")
    ratios = [c.rate_ratio for c in checks]
    late = [c.model_g_per_l[3] / c.measured_g_per_l[3] for c in checks]
    from algae_sim.growth import GrowthParams, temperature_factor

    rho = spearman([temperature_factor(c.temperature_c, GrowthParams()) for c in checks],
                   [c.measured_rate_per_day for c in checks])
    print(f"\npassed {sum(c.passes for c in checks)} of {len(checks)} runs "
          f"(early growth within +/-{TOLERANCE:.0%})")
    print(f"median early-growth ratio model/measured: {statistics.median(ratios):.2f}x")
    print(f"median day-14 biomass ratio model/measured: {statistics.median(late):.2f}x")
    print(f"rank correlation, model temperature factor vs measured growth: {rho:+.2f}")


if __name__ == "__main__":
    main()
