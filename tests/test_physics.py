"""Thermal and growth models in isolation."""

import math

import pytest

from algae_sim.growth import (
    GrowthParams,
    light_factor,
    net_rate_per_h,
    step_biomass,
    temperature_factor,
)
from algae_sim.thermal import ThermalParams, equilibrium_c, exact_temperature, step_temperature

TP = ThermalParams()
GP = GrowthParams()


class TestThermal:
    def test_heater_warms_and_room_cools(self):
        assert step_temperature(20.0, 1.0, 20.0, TP, 1.0) > 20.0
        assert step_temperature(30.0, 0.0, 20.0, TP, 1.0) < 30.0

    def test_full_heater_balances_at_40_c(self):
        assert equilibrium_c(1.0, 20.0, TP) == pytest.approx(40.0)
        assert step_temperature(40.0, 1.0, 20.0, TP, 1.0) == pytest.approx(40.0)

    def test_euler_matches_exact_solution_over_ten_minutes(self):
        t = 20.0
        for _ in range(600):
            t = step_temperature(t, 1.0, 20.0, TP, 1.0)
        assert t == pytest.approx(exact_temperature(20.0, 1.0, 20.0, TP, 600), abs=0.01)
        assert t == pytest.approx(30.25, abs=0.01)  # result from milestone 1

    def test_one_time_constant_closes_63_percent_of_gap(self):
        t = exact_temperature(20.0, 1.0, 20.0, TP, TP.time_constant_s)
        assert (t - 20.0) / 20.0 == pytest.approx(1 - math.exp(-1))

    @pytest.mark.parametrize("bad", [-0.1, 1.1])
    def test_heater_fraction_is_bounded(self, bad):
        with pytest.raises(ValueError):
            step_temperature(20.0, bad, 20.0, TP, 1.0)


class TestGrowth:
    def test_temperature_factor_cardinal_points(self):
        assert temperature_factor(GP.t_opt_c, GP) == pytest.approx(1.0)
        assert temperature_factor(GP.t_min_c, GP) == 0.0
        assert temperature_factor(GP.t_max_c, GP) == 0.0
        assert 0 < temperature_factor(22.0, GP) < temperature_factor(28.0, GP) < 1

    def test_light_factor_peaks_then_photoinhibits(self):
        assert light_factor(0.0, GP) == 0.0
        assert light_factor(GP.i_opt_umol_m2_s, GP) == pytest.approx(1.0)
        assert light_factor(400.0, GP) < light_factor(200.0, GP) < 1.0

    def test_shading_is_off_without_extinction_and_bites_as_the_culture_thickens(self):
        from dataclasses import replace

        from algae_sim.growth import shaded_light_factor

        clear = replace(GP, extinction_m2_per_g=0.0)
        assert shaded_light_factor(150.0, 1.0, clear) == light_factor(150.0, clear)
        factors = [shaded_light_factor(150.0, x, GP) for x in (0.01, 0.2, 0.5, 1.0)]
        assert factors == sorted(factors, reverse=True) and factors[-1] < 0.3

    def test_dense_cultures_need_more_light(self):
        from algae_sim.growth import shaded_light_factor

        # Dilute: past the optimum, extra light inhibits. Dense: extra light reaches
        # the starved lower layers, so it helps.
        assert shaded_light_factor(400.0, 0.01, GP) < shaded_light_factor(150.0, 0.01, GP)
        assert shaded_light_factor(400.0, 0.5, GP) > shaded_light_factor(150.0, 0.5, GP)

    def test_layer_average_matches_a_fine_reference(self):
        import math

        from algae_sim.growth import shaded_light_factor

        for biomass in (0.05, 0.5, 2.0):
            k = GP.extinction_m2_per_g * biomass * 1000
            fine = sum(
                light_factor(150.0 * math.exp(-k * (i + 0.5) * GP.light_path_m / 2000), GP)
                for i in range(2000)
            ) / 2000
            assert shaded_light_factor(150.0, biomass, GP) == pytest.approx(fine, abs=0.002)

    def test_light_curve_matches_the_fitted_chlorella_data(self):
        # Schwaderer et al. 2011 "Model 2" with C. vulgaris mu_max 0.94 /day,
        # alpha 0.040, I_opt 142.1: rewritten as a fraction of mu_max.
        def model_2(i, mu=0.94, alpha=0.040, i_opt=142.1):
            denominator = (mu / (alpha * i_opt**2)) * i**2 + (1 - 2 * mu / (alpha * i_opt)) * i
            return (mu * i / (denominator + mu / alpha)) / mu

        for intensity in (25.0, 100.0, 400.0, 800.0):
            assert light_factor(intensity, GP) == pytest.approx(model_2(intensity), abs=0.01)
        # Bright light costs some growth, not most of it (Pozzobon 2022).
        assert light_factor(400.0, GP) > 0.8

    def test_growth_stops_at_carrying_capacity(self):
        rate = net_rate_per_h(GP.carrying_capacity_g_per_l, GP.t_opt_c, GP.i_opt_umol_m2_s, GP)
        assert rate == pytest.approx(-GP.basal_loss_per_h)

    def test_heat_stress_kills_culture(self):
        assert net_rate_per_h(0.5, GP.t_max_c + 2, 150.0, GP) < -0.1
        assert step_biomass(0.0, 30.0, 150.0, GP, 10.0) == 0.0

    def test_invalid_cardinal_temperatures_rejected(self):
        with pytest.raises(ValueError):
            GrowthParams(t_min_c=30.0, t_opt_c=25.0)
