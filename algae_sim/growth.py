"""Educational microalgae growth model. NOT validated; do not use for prediction.

    dX/dt = mu_max * f_T(T) * f_I(I) * f_pH * X * (1 - X / X_max) - loss(T) * X

* f_T: cardinal temperature model with inflection (Rosso et al. 1993), 0 outside
  [t_min, t_max], 1 at t_opt.
* f_I: Steele photoinhibition curve, peaks at i_opt and declines above it.
* f_pH: the pH response, passed in as ``ph_response`` (see ``algae_sim.carbon``);
  1.0 means pH is not modelled.
* logistic term: crude stand-in for self-shading and nutrient limits.
* loss: basal maintenance/death, plus heat stress above t_max.

Default values are drawn from published Chlorella vulgaris literature (sources in
docs/model.md). They are literature-typical values pooled across strains and studies,
not a fit to one strain, and the model itself is still not validated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GrowthParams:
    # Sources and confidence for each value: docs/model.md, "Where the numbers come from".
    mu_max_per_h: float = 0.08  # about 1.9 /day (Pozzobon 2022; Rossi et al. 2023)
    t_min_c: float = 2.2  # cardinal temperatures: Rossi et al. 2023, mean of 13 data sets
    t_opt_c: float = 28.9
    t_max_c: float = 42.8
    # Schwaderer et al. 2011 (Haldane fit). The Steele curve with this optimum
    # over-penalises bright light compared with Pozzobon 2022: pending a better source.
    i_opt_umol_m2_s: float = 142.0
    carrying_capacity_g_per_l: float = 2.3  # lab batch with CO2 (Griffiths 2011)
    basal_loss_per_h: float = 0.007  # maintenance, C. sorokiniana (Kliphuis et al. 2011)
    heat_stress_loss_per_h_per_c: float = 0.05  # illustrative

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


def gross_rate_per_h(
    biomass_g_per_l: float,
    temperature_c: float,
    intensity_umol_m2_s: float,
    p: GrowthParams,
    ph_response: float = 1.0,
) -> float:
    """Specific growth rate before losses (1/h). Negative only above carrying capacity.

    Kept separate from the losses because the carbon model needs gross production:
    that is what takes CO2 out of the medium.
    """
    crowding = 1.0 - biomass_g_per_l / p.carrying_capacity_g_per_l
    return (
        p.mu_max_per_h
        * temperature_factor(temperature_c, p)
        * light_factor(intensity_umol_m2_s, p)
        * ph_response
        * crowding
    )


def net_rate_per_h(
    biomass_g_per_l: float,
    temperature_c: float,
    intensity_umol_m2_s: float,
    p: GrowthParams,
    ph_response: float = 1.0,
) -> float:
    """Net specific growth rate (1/h) at the current state; may be negative."""
    growth = gross_rate_per_h(biomass_g_per_l, temperature_c, intensity_umol_m2_s, p, ph_response)
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
    ph_response: float = 1.0,
) -> float:
    """Advance true biomass one forward-Euler step; never returns a negative value."""
    rate = net_rate_per_h(biomass_g_per_l, temperature_c, intensity_umol_m2_s, p, ph_response)
    return max(0.0, biomass_g_per_l * (1.0 + rate * dt_s / 3600.0))
