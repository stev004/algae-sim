# Checking the model against a real experiment

[Project overview](../README.md) · [Model](model.md) · [Benchmark](benchmark.md) · [Progress](progress.md)

**Short answer: the model fails this check.** In a real 3 L experiment it predicts *Chlorella vulgaris* growth about **twice as fast** as measured. It also ranks temperatures almost **backwards** for this strain: the real cultures grew best at 18–20 °C, while the model favours 25–30 °C. The model is a reasonable teaching simulator with literature-typical parameters. It is not a predictor of any particular strain or reactor, and this check shows exactly why.

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
- **Expected beforehand:** the model would overshoot late in the culture, because it has no light shading in dense culture, no carbon limit under air-only aeration and no nitrogen limit.

## Results

| | Result |
|---|---|
| Runs passing measure 1 | **0 of 12** |
| Early growth rate, model ÷ measured | median **2.1×** (range 1.7× at 18 °C to 4.1× at 32 °C) |
| Biomass at day 14, model ÷ measured | median **2.3×** (model ~2.0 g/L, measured 0.68–1.15 g/L) |
| Rank correlation, model temperature response vs measured growth | **−0.77** |

| Temperature | Measured growth, days 0–4 (share of best) | Model temperature factor (share of best) |
|---|---|---|
| 18 °C | 0.91 | 0.65 |
| 20 °C | 1.00 | 0.75 |
| 25 °C | 0.84 | 0.95 |
| 30 °C | 0.75 | 1.00 |
| 32 °C | 0.40 | 0.96 |

The rank correlation and the temperature table were added after the pass/fail rule was set. They are a diagnostic, not part of the test.

## Why it fails

These are the likely causes, ranked by how much each probably contributes. None was tuned away; fixing them would need new mechanisms, checked against a different dataset.

1. **Light shading.** 55 µmol/m²/s at the surface of a 3 L tank holding 0.2–1 g/L means most cells see much less light than that. The model gives every cell the full surface light. That alone makes it grow too fast, and it gets worse as the culture thickens.
2. **Carbon supply.** The real cultures had air only. The model doses CO₂ to hold pH, so it never runs short of carbon.
3. **Nitrogen.** At N:P 9 the nitrate was about 97 % used up (the paper's Table 2), which explains the real plateau near 1 g/L. The model's only ceiling is its logistic limit of 2.3 g/L.
4. **The strain.** CCAP 211/11B appears to prefer cooler conditions than the pooled literature values the model uses (optimum 28.9 °C). Serra-Maia et al. 2016, with the same strain, saw growth peak near 23 °C and stop by 30 °C. This study agrees. Strain differences are real, and a pooled curve hides them.

## What it means for the rest of the project

- **The control, QC, device and pipeline engineering is unaffected.** None of it depends on the growth numbers being right.
- **The optimiser benchmark is a result about search on this simulated surface.** On this strain's real surface, the best conditions would sit at a different temperature, and the absolute OD values would be lower. The benchmark's limits section says so.
- **"Literature-grounded" now has a measured limit:** the parameters are typical of the species, and this check shows they don't transfer to one strain in one reactor.

## What would improve it

- **Add light shading** (Beer–Lambert attenuation with a *C. vulgaris* extinction coefficient; Yun & Park 2001 has data) and a simple carbon limit for air-only aeration.
- **Optionally, a strain-specific temperature curve.** Then repeat this check against a *different* dataset, so the fix isn't tuned to the data it's tested on.

## Reproduce it

```sh
uv run python -m algae_sim.validation
```

The transcribed data and the comparison are in `algae_sim/validation.py`; `tests/test_validation.py` pins the findings above.
