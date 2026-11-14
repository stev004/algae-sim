# Checking the model against a real experiment

[Project overview](../README.md) · [Model](model.md) · [Benchmark](benchmark.md) · [Progress](progress.md)

**Short answer:** with self-shading, the model gets the typical growth rate of a real *Chlorella vulgaris* culture about right. That holds for a plausible reactor depth, which the paper doesn't report. But it still ranks temperatures nearly **backwards** for this strain: the real cultures grew best at 18–20 °C, while the model favours 25–30 °C. Without shading, the first version of this check failed outright: the model grew twice as fast as measured.

## The experiment

Pinto et al. 2026, *Marine Drugs* 24:124, doi:[10.3390/md24040124](https://doi.org/10.3390/md24040124), CC BY 4.0, supplementary Table S1:
- **Strain and vessels:** *C. vulgaris* CCAP 211/11B in 3 L stirred, temperature-controlled bioreactors.
- **Light and gas:** continuous light at about 55 µmol/m²/s; pH held at 7–7.5; aerated with air only, no added CO₂.
- **Runs:** batch cultures starting near 0.2 g/L, at 18, 20, 25, 30 and 32 °C and several nitrogen:phosphorus ratios. That's 12 runs, with biomass reported at days 0, 4, 7 and 14.
- **Independence:** none of the model's parameters came from this paper.

## The rules, fixed before running

- **No fitting.** The model runs at the paper's conditions with its normal parameters: 55 µmol/m²/s, pH 7.25, each run's measured temperature and starting biomass.
- **Measure 1, early growth:** the mean specific growth rate over days 0–4. **Pass if the model is within ±30 % of the measurement**, the usual spread of growth rates between labs for one species.
- **Measure 2, the whole curve:** model ÷ measured biomass at days 4, 7 and 14.

## Current result, with self-shading

Self-shading needs the depth of culture the light crosses, and the paper doesn't report it for its 3 L vessel. The check uses the simulated reactor's own 5 cm, which was set before this rerun, and reports how the result moves with depth.

| | Result (5 cm light path) |
|---|---|
| Runs passing measure 1 | **9 of 12** |
| Early growth rate, model ÷ measured | median **1.05×** (0.66× at 18 °C to 1.84× at 32 °C) |
| Biomass at day 14, model ÷ measured | median **0.92×** |
| Rank correlation, model temperature response vs measured growth | **−0.77** |

| Light path assumed | Runs passing | Median early-growth ratio |
|---|---|---|
| 3 cm | 5 of 12 | 1.35× |
| 5 cm | 9 of 12 | 1.05× |
| 8 cm | 8 of 12 | 0.75× |

**How to read this:**
- **Shading explains the old 2× overshoot.** For any plausible depth, the typical growth rate is now within about a third of the measurement.
- **The pass count depends on the unknown depth.** 9 of 12 at 5 cm is not a clean pass, because that depth is our reactor's, not theirs.
- **The temperature extremes fail in opposite directions:** too fast at 32 °C and too slow at 18 °C. That's what a wrong temperature curve looks like, and shading can't fix it.

| Temperature | Measured growth, days 0–4 (share of best) | Model temperature factor (share of best) |
|---|---|---|
| 18 °C | 0.91 | 0.65 |
| 20 °C | 1.00 | 0.75 |
| 25 °C | 0.84 | 0.95 |
| 30 °C | 0.75 | 1.00 |
| 32 °C | 0.40 | 0.96 |

The rank correlation, the temperature table and the depth sweep were added after the pass/fail rule was set. They are diagnostics, not part of the test.

## History: the first check failed

Before self-shading (2026-11-14, earlier the same day), the same rules gave:
- **0 of 12** runs passing;
- the early growth rate a median **2.1×** too fast;
- day-14 biomass **2.3×** too high.

The likely causes were listed in advance: no light shading; no carbon limit under air-only aeration; no nitrogen limit; and a strain that prefers cooler conditions. Shading was then added, with its absorption value taken from a different study (Kuhfuß et al. 2022, *C. vulgaris*, 227 m²/kg), not tuned to these data. The carbon limit was left out deliberately: the simulated reactor always doses CO₂, so adding one would only fit this test case.

## What's still wrong

1. **The temperature curve doesn't fit this strain.** CCAP 211/11B prefers cooler conditions than the pooled literature values (optimum 28.9 °C). Serra-Maia et al. 2016, with the same strain, saw growth peak near 23 °C and stop by 30 °C. Strain differences are real, and a pooled curve hides them.
2. **There's no nitrogen limit.** At N:P 9 the nitrate was about 97 % used up (the paper's Table 2). The model's only stand-in is its logistic limit.
3. **The light path of the test vessel is unknown,** so this experiment can't pin the shading strength down.

## What it means for the rest of the project

- **The control, QC, device and pipeline engineering is unaffected.** None of it depends on the growth numbers being right.
- **The benchmark's conclusion held before and after shading.** It's still a result about search on this simulated surface, and on this strain's real surface the best temperature would be lower. The benchmark's limits section says so.

## Reproduce it

```sh
uv run python -m algae_sim.validation
```

The transcribed data and the comparison, including the depth sweep, are in `algae_sim/validation.py`. `tests/test_validation.py` pins the findings above.
