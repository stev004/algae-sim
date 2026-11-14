"""Educational microalgae growth model. NOT validated; do not use for prediction.

    dX/dt = mu_max * f_T(T) * <f_I(I(z))> * f_pH * X * (1 - X / X_max) - loss(T) * X

* f_T: cardinal temperature model with inflection (Rosso et al. 1993), 0 outside
  [t_min, t_max], 1 at t_opt.
* f_I: light response with photoinhibition (Bernard & Remond 2012 form; "Model 2"
  in Schwaderer et al. 2011): rises with initial slope set by i_k, is 1 at i_opt,
  then declines slowly in bright light.
* <f_I(I(z))>: self-shading. Light fades with depth by Beer-Lambert,
  I(z) = I0 * exp(-k * X * z), and the light response is averaged over the light
  path, so a dense culture grows as if most cells sat in the dark.
* f_pH: the pH response, passed in as ``ph_response`` (see ``algae_sim.carbon``);
  1.0 means pH is not modelled.
* logistic term: crude stand-in for nutrient limits.
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
    # Light curve fitted to C. vulgaris growth data (Dauta et al. 1990) by Schwaderer
    # et al. 2011, web appendix: I_opt 142.1, mu_max 0.94 /day, alpha 0.040.
    i_opt_umol_m2_s: float = 142.0
    # i_k = mu_max / alpha: the irradiance scale of the initial slope (0.94 / 0.040).
    i_k_umol_m2_s: float = 23.5
    carrying_capacity_g_per_l: float = 2.3  # lab batch with CO2 (Griffiths 2011)
    basal_loss_per_h: float = 0.007  # maintenance, C. sorokiniana (Kliphuis et al. 2011)
    heat_stress_loss_per_h_per_c: float = 0.05  # illustrative
    # Effective light extinction per gram of dry biomass, PAR: absorption 227 m2/kg
    # plus back-scattering 0.0008 x 801 m2/kg (Kuhfuss et al. 2022, C. vulgaris).
    # 0 switches self-shading off.
    extinction_m2_per_g: float = 0.228
    # Depth of culture the light crosses: a property of the simulated reactor, not
    # of the alga. 5 cm is typical of a lab flat-panel photobioreactor (illustrative).
    light_path_m: float = 0.05

    def __post_init__(self) -> None:
        if not self.t_min_c < self.t_opt_c < self.t_max_c:
            raise ValueError("require t_min < t_opt < t_max")
        if min(self.mu_max_per_h, self.i_opt_umol_m2_s, self.i_k_umol_m2_s) <= 0:
            raise ValueError("mu_max, i_opt and i_k must be positive")
        if self.carrying_capacity_g_per_l <= 0:
            raise ValueError("carrying capacity must be positive")
        if self.extinction_m2_per_g < 0 or self.light_path_m <= 0:
            raise ValueError("extinction must be >= 0 and the light path positive")


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
    """Light response; dimensionless, 0..1 with a maximum of exactly 1 at i_opt.

        f(I) = I / (I + i_k * (I / i_opt - 1) ** 2)

    Chosen over the Steele curve because, with C. vulgaris parameters, Steele drops
    to 0.46 at 400 umol/m2/s, while measured cultures keep most of their growth
    in bright light (Pozzobon 2022). Choi & Lee 2011 saw stronger losses; see
    docs/model.md.
    """
    if intensity_umol_m2_s <= 0:
        return 0.0
    i = intensity_umol_m2_s
    return i / (i + p.i_k_umol_m2_s * (i / p.i_opt_umol_m2_s - 1.0) ** 2)


SHADING_LAYERS = 16  # midpoint layers across the light path; tests check accuracy


def shaded_light_factor(
    intensity_umol_m2_s: float, biomass_g_per_l: float, p: GrowthParams
) -> float:
    """Light response averaged over the light path of a self-shading culture.

    Averages the light *response* layer by layer rather than responding to the
    average light: the response is curved, so surface cells can be over-lit while
    deep cells starve, and averaging the light first would hide both.
    """
    # k [m2/g] * X [g/L] * 1000 [L/m3] = attenuation per metre of culture.
    attenuation = p.extinction_m2_per_g * biomass_g_per_l * 1000.0
    if attenuation * p.light_path_m < 1e-9:
        return light_factor(intensity_umol_m2_s, p)
    dz = p.light_path_m / SHADING_LAYERS
    return sum(
        light_factor(intensity_umol_m2_s * math.exp(-attenuation * (i + 0.5) * dz), p)
        for i in range(SHADING_LAYERS)
    ) / SHADING_LAYERS


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
        * shaded_light_factor(intensity_umol_m2_s, biomass_g_per_l, p)
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
