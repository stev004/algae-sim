"""The out-of-sample check against Pinto et al. 2026 (docs/validation.md)."""

import pytest

from algae_sim.validation import LIGHT_UMOL_M2_S, PH, PINTO_2026, simulate, spearman


def test_transcribed_data_look_like_the_paper():
    assert len(PINTO_2026) == 12
    temperatures = sorted({t for t, _, _ in PINTO_2026.values()})
    assert temperatures == [18, 20, 25, 30, 32]
    for _, _, biomass in PINTO_2026.values():
        assert 150 <= biomass[0] <= 250  # "started at ca. 0.20 +/- 0.02 g dw/L"
        assert list(biomass) == sorted(biomass)  # batch growth only rises here


def test_simulation_matches_the_papers_conditions():
    check = simulate("E2")
    assert check.model_g_per_l[0] == pytest.approx(0.170)
    assert (LIGHT_UMOL_M2_S, PH) == (55.0, 7.25)
    assert check.measured_rate_per_day == pytest.approx(0.304, abs=0.001)


def test_rank_correlation_handles_ties():
    assert spearman([1, 2, 3], [10, 20, 30]) == pytest.approx(1.0)
    assert spearman([1, 2, 3], [30, 20, 10]) == pytest.approx(-1.0)
    assert spearman([1, 1, 2], [5, 5, 9]) == pytest.approx(1.0)


def test_documented_result_still_holds():
    """Pins the findings in docs/validation.md. If a model change moves these,
    rerun `python -m algae_sim.validation` and update the doc, not this test alone."""
    cold, mid, hot = simulate("E4"), simulate("E2"), simulate("E3")
    # With self-shading the typical run is about right...
    assert mid.passes
    # ...but the temperature extremes go opposite ways: this strain likes it cooler
    # than the pooled temperature curve says.
    assert hot.rate_ratio > 1.3 and cold.rate_ratio < 0.8
    assert cold.measured_rate_per_day > hot.measured_rate_per_day
    assert cold.model_rate_per_day < hot.model_rate_per_day


def test_result_depends_on_the_unreported_light_path():
    shallow, deep = simulate("E2", 0.02), simulate("E2", 0.10)
    assert shallow.rate_ratio > 1.3 > 1.0 > 0.8 > deep.rate_ratio
