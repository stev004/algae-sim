"""Educational pH / CO2 model for a photobioreactor. NOT validated; illustrative only.

pH is held by dosing CO2, so the two are not independent knobs. Three things move
the true pH of the culture:

* **photosynthesis raises it.** Growing algae take inorganic carbon out of the
  medium. Each gram of new biomass fixes about ``co2_per_g_biomass`` grams of CO2.
* **dosing CO2 lowers it.** Dissolved CO2 forms carbonic acid.
* **gas exchange with the air** pulls pH back towards ``air_equilibrium_ph``: CO2
  escapes when pH is held below that value and dissolves in when pH is above it.

Gas exchange is why a lower pH setpoint costs more CO2: the device has to keep
replacing CO2 that bubbles out. CO2 can only push pH down. A setpoint above the air
equilibrium can be held only once photosynthesis is fast enough, and there is no
base dosing.

The whole chemistry collapses into one buffer coefficient (pH change per gram of
CO2 per litre). A real carbonate system is non-linear and depends on the medium's
alkalinity. That simplification is acceptable for a control demo, not for
prediction.

Growth responds to pH with a cardinal pH model (Rosso et al. 1995): 0 at ``ph_min``
and ``ph_max``, 1 at ``ph_opt``. At high pH the fall-off also stands in for carbon
limitation, because little dissolved CO2 is left at high pH.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CarbonParams:
    volume_l: float = 1.0
    # Lumped buffer: how far one gram of CO2 per litre moves pH (illustrative).
    ph_per_g_co2_per_l: float = 0.5
    # Roughly the carbon content of algal dry biomass, expressed as CO2.
    co2_per_g_biomass: float = 1.8
    # pH the medium settles at under air sparging; depends on alkalinity (illustrative).
    air_equilibrium_ph: float = 8.6
    air_exchange_time_constant_h: float = 8.0
    # CO2 flow through the dosing valve while it is open.
    valve_flow_g_per_h: float = 1.0

    def __post_init__(self) -> None:
        positive = (
            self.volume_l,
            self.ph_per_g_co2_per_l,
            self.air_exchange_time_constant_h,
            self.valve_flow_g_per_h,
        )
        if min(positive) <= 0 or self.co2_per_g_biomass < 0:
            raise ValueError("carbon parameters must be positive")


@dataclass(frozen=True, slots=True)
class PhGrowthParams:
    """Cardinal pH growth response; values are illustrative, not for any strain."""

    ph_min: float = 5.5
    ph_opt: float = 7.8
    ph_max: float = 10.0

    def __post_init__(self) -> None:
        if not self.ph_min < self.ph_opt < self.ph_max:
            raise ValueError("require ph_min < ph_opt < ph_max")


def ph_factor(ph: float, p: PhGrowthParams) -> float:
    """Cardinal pH model; dimensionless, 0..1, with 1 at ``ph_opt``."""
    if ph <= p.ph_min or ph >= p.ph_max:
        return 0.0
    low, high = ph - p.ph_min, ph - p.ph_max
    return (low * high) / (low * high - (ph - p.ph_opt) ** 2)


def step_ph(
    ph: float,
    *,
    co2_dosed_g: float,
    co2_fixed_g: float,
    p: CarbonParams,
    dt_s: float,
) -> float:
    """Advance true pH one forward-Euler step.

    ``co2_dosed_g`` is the CO2 the valve delivered during the step. ``co2_fixed_g``
    is the CO2 that photosynthesis took up during the step.
    """
    exchange = (p.air_equilibrium_ph - ph) * dt_s / (p.air_exchange_time_constant_h * 3600)
    chemistry = p.ph_per_g_co2_per_l * (co2_fixed_g - co2_dosed_g) / p.volume_l
    return ph + exchange + chemistry
