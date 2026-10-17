"""A first, deliberately simple model of reactor temperature.

All parameter values are illustrative. They are not measurements from a real
bioreactor.
"""

HEAT_CAPACITY_J_PER_C = 4_180.0
HEATER_POWER_W = 100.0
HEAT_LOSS_W_PER_C = 5.0
AMBIENT_TEMPERATURE_C = 20.0


def step_temperature(
    temperature_c: float,
    heater_fraction: float,
    dt_seconds: float = 1.0,
) -> float:
    """Calculate the true vessel temperature after one simulation step."""
    if not 0.0 <= heater_fraction <= 1.0:
        raise ValueError("heater_fraction must be between 0 and 1")

    heat_from_heater_w = HEATER_POWER_W * heater_fraction
    heat_lost_to_room_w = HEAT_LOSS_W_PER_C * (
        temperature_c - AMBIENT_TEMPERATURE_C
    )
    net_heating_w = heat_from_heater_w - heat_lost_to_room_w

    temperature_change_c = (
        dt_seconds * net_heating_w / HEAT_CAPACITY_J_PER_C
    )
    return temperature_c + temperature_change_c


def run_simulation(
    start_temperature_c: float,
    heater_fraction: float,
    duration_seconds: int,
) -> float:
    """Repeat one-second temperature steps and return the final temperature."""
    history = run_simulation_history(
        start_temperature_c, heater_fraction, duration_seconds
    )
    return history[-1][1]


def run_simulation_history(
    start_temperature_c: float,
    heater_fraction: float,
    duration_seconds: int,
) -> list[tuple[int, float]]:
    """Return (elapsed seconds, true temperature) at every second, including zero."""
    if duration_seconds < 0:
        raise ValueError("duration_seconds must be non-negative")
    if not 0.0 <= heater_fraction <= 1.0:
        raise ValueError("heater_fraction must be between 0 and 1")

    temperature_c = start_temperature_c
    history = [(0, temperature_c)]

    for elapsed_seconds in range(1, duration_seconds + 1):
        temperature_c = step_temperature(temperature_c, heater_fraction)
        history.append((elapsed_seconds, temperature_c))

    return history


def main() -> None:
    """Run two small experiments that are easy to reason about."""
    heating_history = run_simulation_history(
        start_temperature_c=20.0,
        heater_fraction=1.0,
        duration_seconds=600,
    )
    cooling_history = run_simulation_history(
        start_temperature_c=30.0,
        heater_fraction=0.0,
        duration_seconds=600,
    )

    print("Illustrative ten-minute experiments")
    print("Time (min)   Full heater (C)   Heater off (C)")
    for elapsed_seconds in range(0, 601, 120):
        heated_temperature_c = heating_history[elapsed_seconds][1]
        cooled_temperature_c = cooling_history[elapsed_seconds][1]
        print(
            f"{elapsed_seconds / 60:10.0f}"
            f"{heated_temperature_c:18.2f}"
            f"{cooled_temperature_c:17.2f}"
        )


if __name__ == "__main__":
    main()
