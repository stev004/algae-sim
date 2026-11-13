"""Scoring and design of the optimiser benchmark (no campaigns are run here)."""

from itertools import pairwise

import pytest

pytest.importorskip("autonomous_lab.culture")

from algae_sim.autolab.benchmark import (
    BUDGET,
    GRID_LEVELS,
    Observation,
    compare,
    hypervolume,
    normalised_hypervolume,
    run_config,
)


def obs(od, energy, co2, usable=True, index=0):
    return Observation("optimiser", 0, index, 25.0, 150.0, 7.5, od, energy, co2, usable)


class TestHypervolume:
    def test_one_point_is_its_box(self):
        assert hypervolume([(2.0, 3.0, 4.0)]) == pytest.approx(24.0)

    def test_overlapping_boxes_are_counted_once(self):
        # Two boxes sharing a 1 x 1 x 1 corner: 2 + 2 - 1.
        assert hypervolume([(2.0, 1.0, 1.0), (1.0, 2.0, 1.0)]) == pytest.approx(3.0)
        assert hypervolume([(2, 1, 1), (1, 2, 1), (1, 1, 2)]) == pytest.approx(4.0)

    def test_dominated_points_add_nothing(self):
        assert hypervolume([(2, 2, 2), (1, 1, 1)]) == pytest.approx(8.0)

    def test_matches_brute_force_on_a_grid(self):
        points = [(3, 1, 2), (1, 3, 1), (2, 2, 3), (1, 1, 1)]
        brute = sum(
            any(x < a and y < b and z < c for a, b, c in points)
            for x in range(3) for y in range(3) for z in range(3)
        )
        assert hypervolume(points) == pytest.approx(brute)

    def test_unusable_and_worse_than_reference_are_ignored(self):
        good = obs(1.0, 3.0, 10.0)
        assert normalised_hypervolume([good, obs(3.0, 1.0, 1.0, usable=False)]) == (
            pytest.approx(normalised_hypervolume([good]))
        )
        assert normalised_hypervolume([obs(1.0, 99.0, 1.0)]) == 0.0
        assert 0 < normalised_hypervolume([good]) < 1


class TestStatistics:
    def test_clear_difference_is_significant_and_the_ci_excludes_zero(self):
        c = compare([1.0 + 0.01 * i for i in range(30)], [0.5] * 30, "random")
        assert c.optimiser_wins == 30 and c.p_value < 0.001 and c.ci_low > 0

    def test_no_difference_is_not_significant(self):
        a = [0.5, 0.6, 0.4, 0.55, 0.45, 0.5] * 5
        b = [0.6, 0.5, 0.45, 0.4, 0.55, 0.5] * 5
        assert compare(a, b, "random").p_value > 0.2


class TestDesign:
    def test_seeds_are_spaced_beyond_the_budget_and_worlds_are_paired(self):
        seeds = [run_config("optimiser", k)["campaign"]["seed"] for k in range(30)]
        assert min(b - a for a, b in pairwise(seeds)) > BUDGET
        worlds = {arm: run_config(arm, 4)["execution"]["controller"]["settings"]["seed"]
                  for arm in ("optimiser", "random", "grid")}
        assert len(set(worlds.values())) == 1

    def test_benchmark_runs_have_no_injected_faults_and_the_same_budget(self):
        for arm in ("optimiser", "random", "grid"):
            config = run_config(arm, 0)
            settings = config["execution"]["controller"]["settings"]
            assert settings["heater_faults"] == settings["co2_valve_faults"] == {}
            assert settings["culture_crashes"] == {}
            assert config["campaign"]["max_experiments"] == BUDGET

    def test_grid_is_exactly_the_budget_and_inside_the_search_space(self):
        config = run_config("grid", 0)
        sizes = [len(levels) for levels in GRID_LEVELS.values()]
        assert sizes[0] * sizes[1] * sizes[2] == BUDGET
        space = config["parameter_space"]
        for name, levels in GRID_LEVELS.items():
            assert all(space[name]["minimum"] <= v <= space[name]["maximum"] for v in levels)


def test_headline_states_the_result_from_the_numbers():
    from algae_sim.autolab.benchmark import headline

    def summary(hv_ci, od_ci):
        return {
            "arms": {"random": {"median_hypervolume": 0.25}},
            "comparisons": [
                {"metric": "hypervolume", "baseline": "random", "median_difference": 0.01,
                 "ci_low": hv_ci[0], "ci_high": hv_ci[1]},
                {"metric": "best_od", "baseline": "random", "median_difference": 0.0,
                 "ci_low": od_ci[0], "ci_high": od_ci[1]},
            ],
        }

    assert headline(summary((0.001, 0.02), (-0.1, 0.1))) == (
        "The optimiser finds 4.0% better trade-offs, not denser cultures"
    )
    # An interval that crosses zero is not claimed as a win.
    assert "no better trade-offs" in headline(summary((-0.01, 0.02), (-0.1, 0.1)))
