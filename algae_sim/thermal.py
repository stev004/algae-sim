"""Lumped thermal model of one perfectly mixed vessel.

    C * dT/dt = P * u - K * (T - Ta)

See docs/model.md. All values are illustrative, not measurements of a real vessel.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ThermalParams:
    heat_capacity_j_per_c: float = 4_180.0  # roughly one litre of water
    heater_power_w: float = 100.0
    heat_loss_w_per_c: float = 5.0

    def __post_init__(self) -> None:
        if self.heat_capacity_j_per_c <= 0 or self.heat_loss_w_per_c <= 0:
            raise ValueError("heat capacity and heat-loss coefficient must be positive")
        if self.heater_power_w < 0:
            raise ValueError("heater power must be non-negative")

    @property
    def time_constant_s(self) -> float:
        return self.heat_capacity_j_per_c / self.heat_loss_w_per_c

    def max_stable_dt_s(self) -> float:
        """Forward Euler is stable only below 2C/K for this linear model."""
        return 2.0 * self.time_constant_s


def step_temperature(
    temperature_c: float,
    heater_fraction: float,
    ambient_c: float,
    params: ThermalParams,
    dt_s: float,
) -> float:
    """Advance the true vessel temperature by one forward-Euler step."""
    if not 0.0 <= heater_fraction <= 1.0:
        raise ValueError("heater_fraction must be between 0 and 1")
    if dt_s <= 0:
        raise ValueError("dt_s must be positive")
    heat_in_w = params.heater_power_w * heater_fraction
    heat_out_w = params.heat_loss_w_per_c * (temperature_c - ambient_c)
    return temperature_c + dt_s * (heat_in_w - heat_out_w) / params.heat_capacity_j_per_c


def equilibrium_c(heater_fraction: float, ambient_c: float, params: ThermalParams) -> float:
    return ambient_c + params.heater_power_w * heater_fraction / params.heat_loss_w_per_c


def exact_temperature(
    start_c: float,
    heater_fraction: float,
    ambient_c: float,
    params: ThermalParams,
    elapsed_s: float,
) -> float:
    """Closed-form solution for constant heater input; used to check the Euler step."""
    t_eq = equilibrium_c(heater_fraction, ambient_c, params)
    return t_eq + (start_c - t_eq) * math.exp(-elapsed_s / params.time_constant_s)
