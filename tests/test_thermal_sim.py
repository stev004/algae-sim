import math
import unittest

from thermal_sim import run_simulation, run_simulation_history, step_temperature


class StepTemperatureTests(unittest.TestCase):
    def test_history_includes_initial_state_and_each_elapsed_second(self) -> None:
        history = run_simulation_history(20.0, 1.0, 3)
        self.assertEqual([reading[0] for reading in history], [0, 1, 2, 3])
        self.assertEqual(history[0], (0, 20.0))
        self.assertTrue(all(
            earlier[1] < later[1]
            for earlier, later in zip(history, history[1:])
        ))

    def test_zero_duration_records_only_initial_state(self) -> None:
        self.assertEqual(run_simulation_history(25.0, 0.5, 0), [(0, 25.0)])

    def test_negative_duration_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            run_simulation_history(20.0, 1.0, -1)

    def test_full_heater_warms_vessel_at_room_temperature(self) -> None:
        self.assertGreater(step_temperature(20.0, 1.0), 20.0)

    def test_heater_off_cools_warm_vessel(self) -> None:
        self.assertLess(step_temperature(30.0, 0.0), 30.0)

    def test_full_heater_is_balanced_at_40_c(self) -> None:
        self.assertEqual(step_temperature(40.0, 1.0), 40.0)

    def test_heater_setting_must_be_between_off_and_full(self) -> None:
        with self.assertRaises(ValueError):
            step_temperature(20.0, 1.1)

    def test_ten_minute_result_is_close_to_exact_solution(self) -> None:
        simulated_temperature_c = run_simulation(20.0, 1.0, 600)

        equilibrium_temperature_c = 40.0
        time_constant_seconds = 4_180.0 / 5.0
        exact_temperature_c = equilibrium_temperature_c + (
            20.0 - equilibrium_temperature_c
        ) * math.exp(-600.0 / time_constant_seconds)

        self.assertAlmostEqual(simulated_temperature_c, exact_temperature_c, places=2)


if __name__ == "__main__":
    unittest.main()
