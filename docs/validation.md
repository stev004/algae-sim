# Checking the model against a real experiment

[Project overview](../README.md) · [Model](model.md) · [Benchmark](benchmark.md) · [Progress](progress.md)

**Short answer:** with self-shading, the model gets the typical growth rate of a real *Chlorella vulgaris* culture about right, at a light path of 4–8 cm, which the paper doesn't report. That is a weak test, though: a model with **one constant growth rate and no temperature or light response at all passes more runs (11 of 12) than this model does (9 of 12)**. The part of the data that can tell models apart is how growth changes with temperature, and there the model fails: it ranks temperatures nearly **backwards** for this strain. The real cultures grew best at 18–20 °C, while the model favours 25–30 °C. Without shading, the first version of this check failed outright.

**Read this as a calibration, not an out-of-sample validation.** Shading and its light path were added after the first check failed, and the rerun used the same dataset. The first check's own write-up said a fix should be tested against a *different* dataset; that hasn't been done.

## The experiment

Pinto et al. 2026, *Marine Drugs* 24:124, doi:[10.3390/md24040124](https://doi.org/10.3390/md24040124), CC BY 4.0, supplementary Table S1:
- **Strain and vessels:** *C. vulgaris* CCAP 211/11B in stirred, temperature-controlled bioreactors with a 3 L working volume. (An earlier version of this page called them "3 L vessels"; the vessels themselves are larger, which matters for the light path below.)
- **Light and gas:** continuous light at about 55 µmol/m²/s; pH held at 7–7.5; aerated with air only, no added CO₂.
- **Runs:** batch cultures starting near 0.2 g/L, at 18, 20, 25, 30 and 32 °C and several nitrogen:phosphorus ratios. That's 12 runs, with biomass reported at days 0, 4, 7 and 14. Four of them (E1, E2, E11, E12: 25 °C, N:P 9) are replicates of the design's centre point, so there are **9 distinct conditions**, and half the runs sit at 25 °C.
- **Independence:** none of the model's parameters came from this paper.

## The rules

I wrote these rules before the first run, but they were committed together with that run's result (commit `0719092`), so git can't show the order. Take "fixed in advance" on trust, or not.

- **No fitting.** The model runs at the paper's conditions with its normal parameters: 55 µmol/m²/s, pH 7.25, each run's measured temperature and starting biomass.
- **Measure 1, early growth:** the mean specific growth rate over days 0–4. **Pass if the model is within ±30 % of the measurement**, the usual spread of growth rates between labs for one species.
- **Measure 2, the whole curve:** model ÷ measured biomass at days 4, 7 and 14.

## Current result, with self-shading

Self-shading needs the depth of culture the light crosses, and the paper doesn't report it. The check uses 5 cm, the simulated reactor's own light path. That number was introduced in the same commit as this rerun (`b11c8cc`); before it, the model had no light path at all. So 5 cm is a choice made while fixing the failure, not an independent input, and the result depends on it (next table).

| | Result (5 cm light path) |
|---|---|
| Runs passing measure 1 | **9 of 12** |
| Early growth rate, model ÷ measured | median **1.05×** (0.66× at 18 °C to 1.84× at 32 °C) |
| Biomass at day 14, model ÷ measured | median **0.92×** |
| Rank correlation, model temperature response vs measured growth | **−0.77** |

| Light path assumed | Runs passing | Median early-growth ratio |
|---|---|---|
| 2 cm | 3 of 12 | 1.56× |
| 3 cm | 5 of 12 | 1.35× |
| 4 cm | 9 of 12 | 1.18× |
| 5 cm | 9 of 12 | 1.05× |
| 6 cm | 10 of 12 | 0.94× |
| 8 cm | 8 of 12 | 0.75× |
| 10 cm | 3 of 12 | 0.62× |
| 13 cm | 0 of 12 | 0.45× |

**A baseline with no biology in it:**

| Model | Runs passing |
|---|---|
| This model, 5 cm light path | 9 of 12 |
| One constant growth rate, 0.273 /day, no temperature or light response | **11 of 12** |

Any constant from 0.233 to 0.294 /day passes 11 of 12; only the 32 °C run fails.

**How to read this:**
- **The pass rule can't tell a good model from a bad one here.** The ±30 % band is wide, and half the runs share one condition, so matching the average speed is enough. A test the constant beats is not evidence for the model's biology.
- **The light path acts as a free parameter.** The pass count runs from 0 to 10 of 12 as the depth varies over 2–13 cm, and 5 cm sits in the band that works. For vessels with 3 L of culture, 10 cm is plausible, and there the model grows 0.62× as fast as measured.
- **Shading plausibly explains the old overshoot,** within about 4–8 cm, but this dataset can't confirm it.
- **The temperature extremes fail in opposite directions:** too fast at 32 °C and too slow at 18 °C. That's what a wrong temperature curve looks like, and shading can't fix it.

| Temperature | Measured growth, days 0–4 (share of best) | Model temperature factor (share of best) |
|---|---|---|
| 18 °C | 0.91 | 0.65 |
| 20 °C | 1.00 | 0.75 |
| 25 °C | 0.84 | 0.95 |
| 30 °C | 0.75 | 1.00 |
| 32 °C | 0.40 | 0.96 |

The rank correlation, the temperature table, the depth sweep and the constant-rate baseline were added after the pass/fail rule was set. They are diagnostics, not part of the test. The rank correlation is the most informative of them, because it's the only one that asks whether the model's *response* to conditions is right.

## History: the first check failed

Before self-shading (2026-11-14, earlier the same day), the same rules gave:
- **0 of 12** runs passing;
- the early growth rate a median **2.1×** too fast;
- day-14 biomass **2.3×** too high.

The 2.1× understates the miss. At 25 °C the unshaded model started growing at about 1.25 /day against 0.27 /day measured, **about 4.7×**. Over a 4-day window it then ran into its 2.3 g/L ceiling, which pulled the 4-day average down. With the ceiling also removed, the median ratio is 4.4×.

The likely causes were listed in advance: no light shading; no carbon limit under air-only aeration; no nitrogen limit; and a strain that prefers cooler conditions. Shading was then added, with its absorption value taken from a different study (Kuhfuß et al. 2022, *C. vulgaris*, 227 m²/kg), not tuned to these data. The carbon limit was left out deliberately: the simulated reactor always doses CO₂, so adding one would only fit this test case.

## What's still wrong

1. **The temperature curve doesn't fit this strain.** CCAP 211/11B prefers cooler conditions than the pooled literature values (optimum 28.9 °C). Serra-Maia et al. 2016, with the same strain, saw growth peak near 23 °C and stop by 30 °C. Strain differences are real, and a pooled curve hides them.
2. **There's no nitrogen limit.** At N:P 9 the nitrate was about 97 % used up (the paper's Table 2). The model's only stand-in is its logistic limit.
3. **The light path of the test vessel is unknown,** so this experiment can't pin the shading strength down.
4. **The light curve mixes two parameter sets.** I_k = 23.5 µmol/m²/s is μmax ÷ α from a Schwaderer fit with μmax 0.94 /day, but the model's μmax is 1.92 /day, so its initial light slope is about twice that fit's. A consistent I_k (1.92 ÷ 0.040 = 48) gives 8 of 12 at 5 cm and 1 of 12 at 8 cm. The conclusions above don't change. It isn't fixed yet, because changing it moves every benchmark number and needs a full rerun.

## What it means for the rest of the project

- **The control, QC, device and pipeline engineering is unaffected.** None of it depends on the growth numbers being right.
- **The benchmark's conclusion held before and after shading.** It's still a result about search on this simulated surface, and on this strain's real surface the best temperature would be lower. The benchmark's limits section says so.

## Reproduce it

```sh
uv run python -m algae_sim.validation
```

The transcribed data and the comparison, including the depth sweep, are in `algae_sim/validation.py`. `tests/test_validation.py` pins the findings above. The constant-rate baseline, the unshaded rate, the I_k check and the benchmark re-analysis are in `scripts/audit_checks.py` (`uv run python scripts/audit_checks.py`, about 8 minutes).
