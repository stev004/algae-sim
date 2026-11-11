"""Simulated sensors and actuators.

Sensors turn hidden true state into what a device would actually report: offset,
noise, quantisation, range limits and occasional dropouts. Actuators turn a
controller command into what was actually applied, which can differ (e.g. a failed
heater). Each instrument owns a seeded ``random.Random`` so runs are reproducible.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass(slots=True)
class TemperatureSensor:
    noise_sd_c: float = 0.05
    resolution_c: float = 0.1
    offset_c: float = 0.0  # calibration error
    dropout_probability: float = 0.0
    rng: random.Random = field(default_factory=lambda: random.Random(0))

    def read(self, true_temperature_c: float) -> float | None:
        """Return a measured temperature, or None if the reading was lost."""
        if self.rng.random() < self.dropout_probability:
            return None
        raw = true_temperature_c + self.offset_c + self.rng.gauss(0.0, self.noise_sd_c)
        return round(raw / self.resolution_c) * self.resolution_c


@dataclass(slots=True)
class OpticalDensitySensor:
    """OD750-style reading. Illustrative linear calibration: OD = k * biomass."""

    od_per_g_per_l: float = 2.0
    noise_sd_od: float = 0.01
    max_od: float = 4.0  # above this the instrument saturates
    rng: random.Random = field(default_factory=lambda: random.Random(1))

    def read(self, true_biomass_g_per_l: float) -> float:
        raw = true_biomass_g_per_l * self.od_per_g_per_l + self.rng.gauss(0.0, self.noise_sd_od)
        return round(min(max(raw, 0.0), self.max_od), 3)

    def biomass_from_od(self, od: float) -> float:
        """Inverse calibration, used by analysis rather than by the instrument."""
        return od / self.od_per_g_per_l


@dataclass(slots=True)
class Heater:
    """Applies a commanded fraction. A failed heater applies nothing."""

    failed: bool = False

    def apply(self, commanded_fraction: float) -> float:
        if not 0.0 <= commanded_fraction <= 1.0:
            raise ValueError("commanded heater fraction must be between 0 and 1")
        return 0.0 if self.failed else commanded_fraction


@dataclass(slots=True)
class PhSensor:
    """pH probe: noise, a calibration offset, 0.01 resolution and occasional dropouts."""

    noise_sd: float = 0.01
    resolution: float = 0.01
    offset: float = 0.0
    dropout_probability: float = 0.0
    rng: random.Random = field(default_factory=lambda: random.Random(2))

    def read(self, true_ph: float) -> float | None:
        if self.rng.random() < self.dropout_probability:
            return None
        raw = true_ph + self.offset + self.rng.gauss(0.0, self.noise_sd)
        return round(round(raw / self.resolution) * self.resolution, 2)


@dataclass(slots=True)
class Co2Valve:
    """CO2 dosing valve behind a separate supply shutoff.

    ``stuck`` simulates a mechanical fault: ``"open"`` keeps dosing whatever it is
    told, and ``"closed"`` never doses. The shutoff is the protection path. When it
    is closed, no gas flows, even through a valve stuck open.
    """

    stuck: str | None = None

    def apply(self, commanded_open: bool, *, shutoff_closed: bool) -> bool:
        """Return whether CO2 actually flows this step."""
        if self.stuck not in (None, "open", "closed"):
            raise ValueError("stuck must be None, 'open' or 'closed'")
        if shutoff_closed:
            return False
        if self.stuck is not None:
            return self.stuck == "open"
        return commanded_open
