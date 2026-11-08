"""Local temperature controller. Sees only sensor measurements, never true state.

Hysteresis (bang-bang with a dead band) plus two protective rules:

* fail-safe: a missing reading turns the heater off;
* over-temperature trip: a reading at or above ``trip_c`` latches the heater off
  until the reading falls below ``trip_c - trip_reset_band_c``.

These rules live on the device so they keep working with no cloud connection.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class HysteresisController:
    setpoint_c: float
    band_c: float = 0.3
    trip_c: float = 39.0
    trip_reset_band_c: float = 2.0
    heater_on: bool = False
    tripped: bool = False
    trip_count: int = 0
    missing_reading_count: int = 0

    def __post_init__(self) -> None:
        if self.band_c <= 0:
            raise ValueError("band_c must be positive")
        if self.setpoint_c + self.band_c >= self.trip_c:
            raise ValueError("setpoint plus band must stay below the protective trip")

    def decide(self, measured_c: float | None) -> float:
        """Return the commanded heater fraction (0.0 or 1.0)."""
        if measured_c is None:
            self.missing_reading_count += 1
            self.heater_on = False
            return 0.0
        if self.tripped:
            if measured_c < self.trip_c - self.trip_reset_band_c:
                self.tripped = False
            else:
                self.heater_on = False
                return 0.0
        if measured_c >= self.trip_c:
            self.tripped = True
            self.trip_count += 1
            self.heater_on = False
            return 0.0
        if measured_c <= self.setpoint_c - self.band_c:
            self.heater_on = True
        elif measured_c >= self.setpoint_c + self.band_c:
            self.heater_on = False
        return 1.0 if self.heater_on else 0.0
