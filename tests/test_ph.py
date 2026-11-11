"""The illustrative pH / CO2 model, the pH controller and its protection rules."""

import pytest

from algae_sim.carbon import CarbonParams, PhGrowthParams, ph_factor, step_ph
from algae_sim.control import PhController
from algae_sim.instruments import Co2Valve, PhSensor
from algae_sim.reactor import ReactorConfig, run_batch

CP = CarbonParams()
PG = PhGrowthParams()
SHORT = {"batch_hours": 24.0}


class TestChemistry:
    def test_dosing_lowers_and_photosynthesis_raises_ph(self):
        assert step_ph(7.0, co2_dosed_g=0.1, co2_fixed_g=0.0, p=CP, dt_s=1) < 7.0
        assert step_ph(8.6, co2_dosed_g=0.0, co2_fixed_g=0.1, p=CP, dt_s=1) > 8.6

    def test_gas_exchange_pulls_towards_air_equilibrium(self):
        below = step_ph(7.0, co2_dosed_g=0.0, co2_fixed_g=0.0, p=CP, dt_s=600)
        above = step_ph(9.5, co2_dosed_g=0.0, co2_fixed_g=0.0, p=CP, dt_s=600)
        assert 7.0 < below < CP.air_equilibrium_ph < above < 9.5
        assert step_ph(8.6, co2_dosed_g=0.0, co2_fixed_g=0.0, p=CP, dt_s=600) == 8.6

    def test_growth_peaks_at_the_optimum_and_stops_at_the_cardinals(self):
        assert ph_factor(PG.ph_opt, PG) == pytest.approx(1.0)
        assert ph_factor(PG.ph_min, PG) == ph_factor(PG.ph_max, PG) == 0.0
        assert ph_factor(7.0, PG) < 1.0 and ph_factor(8.5, PG) < 1.0
        assert ph_factor(6.0, PG) < ph_factor(7.0, PG)

    @pytest.mark.parametrize("bad", [dict(ph_min=8.0), dict(ph_max=7.0)])
    def test_rejects_disordered_cardinals(self, bad):
        with pytest.raises(ValueError):
            PhGrowthParams(**bad)


class TestController:
    def test_hysteresis_opens_above_and_closes_below_the_band(self):
        c = PhController(setpoint=7.0)
        assert c.decide(7.10) is True
        assert c.decide(7.00) is True  # inside the band: keep doing what it was doing
        assert c.decide(6.90) is False
        assert c.decide(7.00) is False

    def test_missing_reading_closes_the_valve(self):
        c = PhController(setpoint=7.0)
        c.decide(7.5)
        assert c.decide(None) is False and c.missing_reading_count == 1

    def test_low_trip_latches_the_shutoff_until_ph_recovers(self):
        c = PhController(setpoint=7.0)
        assert c.decide(5.5) is False and c.low_tripped and c.shutoff_closed
        assert c.decide(5.9) is False and c.low_tripped  # within the reset band
        c.decide(6.1)
        assert not c.low_tripped and c.trip_count == 1

    def test_high_trip_is_an_alarm_that_counts_once(self):
        c = PhController(setpoint=7.0)
        c.decide(9.8)
        c.decide(9.9)
        assert c.high_tripped and c.trip_count == 1 and not c.shutoff_closed
        c.decide(9.2)
        assert not c.high_tripped

    def test_setpoint_band_must_sit_inside_the_trips(self):
        with pytest.raises(ValueError):
            PhController(setpoint=5.5)
        with pytest.raises(ValueError):
            PhController(setpoint=9.78)


class TestInstruments:
    def test_shutoff_beats_a_valve_stuck_open(self):
        assert Co2Valve(stuck="open").apply(False, shutoff_closed=False) is True
        assert Co2Valve(stuck="open").apply(False, shutoff_closed=True) is False
        assert Co2Valve(stuck="closed").apply(True, shutoff_closed=False) is False
        assert Co2Valve().apply(True, shutoff_closed=False) is True

    def test_probe_reports_to_two_decimals_or_drops_out(self):
        reading = PhSensor().read(7.234)
        assert reading == round(reading, 2) and abs(reading - 7.234) < 0.05
        assert PhSensor(dropout_probability=1.0).read(7.0) is None


class TestInReactor:
    def test_holds_ph_across_the_campaign_range(self):
        for setpoint in (6.5, 7.5, 8.5):
            result = run_batch(ReactorConfig(ph_setpoint=setpoint, **SHORT))
            settled = [s.true_ph for s in result.telemetry if s.elapsed_s >= 3 * 3600]
            assert max(abs(ph - setpoint) for ph in settled) < 0.1

    def test_lower_setpoint_costs_more_co2_and_ph_changes_yield(self):
        low, mid = (run_batch(ReactorConfig(ph_setpoint=ph, **SHORT)) for ph in (6.5, 7.8))
        assert low.co2_g > 2 * mid.co2_g
        assert low.final_true_biomass_g_per_l < mid.final_true_biomass_g_per_l

    def test_valve_stuck_closed_lets_photosynthesis_push_ph_up(self):
        result = run_batch(
            ReactorConfig(ph_setpoint=7.0, co2_valve_sticks="closed", **SHORT)
        )
        assert result.co2_g == 0.0
        assert result.telemetry[-1].true_ph > 8.0

    def test_valve_stuck_open_is_contained_by_the_low_trip(self):
        result = run_batch(
            ReactorConfig(
                ph_setpoint=7.0, co2_valve_sticks="open", co2_valve_sticks_after_h=2.0,
                **SHORT,
            )
        )
        assert result.ph_trip_count >= 1
        assert min(s.true_ph for s in result.telemetry) > 5.3
