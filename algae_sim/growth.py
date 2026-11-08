"""Educational microalgae growth model. NOT validated; do not use for prediction.

    dX/dt = mu_max * f_T(T) * f_I(I) * X * (1 - X / X_max) - loss(T) * X

* f_T: cardinal temperature model with inflection (Rosso et al. 1993), 0 outside
  [t_min, t_max], 1 at t_opt.
* f_I: Steele photoinhibition curve, peaks at i_opt and declines above it.
* logistic term: crude stand-in for self-shading and nutrient limits.
* loss: basal maintenance/death, plus heat stress above t_max.

Default values are loosely Chlorella-like in shape but are illustrative only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GrowthParams:
    mu_max_per_h: float = 0.08
    t_min_c: float = 8.0
    t_opt_c: float = 30.0
    t_max_c: float = 38.0
    i_opt_umol_m2_s: float = 150.0
    carrying_capacity_g_per_l: float = 1.5
    basal_loss_per_h: float = 0.005
    heat_stress_loss_per_h_per_c: float = 0.05

    def __post_init__(self) -> None:
        if not self.t_min_c < self.t_opt_c < self.t_max_c:
            raise ValueError("require t_min < t_opt < t_max")
        if self.mu_max_per_h <= 0 or self.i_opt_umol_m2_s <= 0:
            raise ValueError("mu_max and i_opt must be positive")
        if self.carrying_capacity_g_per_l <= 0:
            raise ValueError("carrying capacity must be positive")


def temperature_factor(temperature_c: float, p: GrowthParams) -> float:
    """Cardinal temperature model with inflection (CTMI); dimensionless, 0..1."""
    t, t_min, t_opt, t_max = temperature_c, p.t_min_c, p.t_opt_c, p.t_max_c
    if t <= t_min or t >= t_max:
        return 0.0
    numerator = (t - t_max) * (t - t_min) ** 2
    denominator = (t_opt - t_min) * (
        (t_opt - t_min) * (t - t_opt) - (t_opt - t_max) * (t_opt + t_min - 2 * t)
    )
    return max(0.0, numerator / denominator)


def light_factor(intensity_umol_m2_s: float, p: GrowthParams) -> float:
    """Steele curve; dimensionless, 0..1 with a maximum at i_opt."""
    if intensity_umol_m2_s <= 0:
        return 0.0
    ratio = intensity_umol_m2_s / p.i_opt_umol_m2_s
    return ratio * math.exp(1.0 - ratio)


def net_rate_per_h(
    biomass_g_per_l: float,
    temperature_c: float,
    intensity_umol_m2_s: float,
    p: GrowthParams,
) -> float:
    """Net specific growth rate (1/h) at the current state; may be negative."""
    crowding = 1.0 - biomass_g_per_l / p.carrying_capacity_g_per_l
    growth = (
        p.mu_max_per_h
        * temperature_factor(temperature_c, p)
        * light_factor(intensity_umol_m2_s, p)
        * crowding
    )
    loss = p.basal_loss_per_h + p.heat_stress_loss_per_h_per_c * max(
        0.0, temperature_c - p.t_max_c
    )
    return growth - loss


def step_biomass(
    biomass_g_per_l: float,
    temperature_c: float,
    intensity_umol_m2_s: float,
    p: GrowthParams,
    dt_s: float,
) -> float:
    """Advance true biomass one forward-Euler step; never returns a negative value."""
    rate = net_rate_per_h(biomass_g_per_l, temperature_c, intensity_umol_m2_s, p)
    return max(0.0, biomass_g_per_l * (1.0 + rate * dt_s / 3600.0))
