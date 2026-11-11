"""Local controllers. They see only sensor measurements, never true state.

``HysteresisController`` (temperature): hysteresis (bang-bang with a dead band)
plus two protective rules:

* fail-safe: a missing reading turns the heater off;
* over-temperature trip: a reading at or above ``trip_c`` latches the heater off
  until the reading falls below ``trip_c - trip_reset_band_c``.

``PhController`` holds pH by dosing CO2; its own rules are on the class.

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


@dataclass(slots=True)
class PhController:
    """Local pH controller: opens the CO2 valve to bring pH down to the setpoint.

    CO2 can only lower pH, so the controller has one lever. Its rules:

    * hysteresis: open the dosing valve at ``setpoint + band`` and close it at
      ``setpoint - band``;
    * fail-safe: a missing pH reading closes the valve, because dosing blind risks
      the dangerous direction (acid overdose);
    * low trip: a reading at or below ``low_trip`` closes the supply shutoff, a
      separate valve that still works if the dosing valve is stuck open. It stays
      closed until pH recovers above ``low_trip + trip_reset_band``;
    * high trip: a reading at or above ``high_trip`` raises an alarm. There is no
      base dosing, so the device can only report it. It typically means the CO2
      supply has failed while photosynthesis keeps raising pH.

    Like the thermostat, it sees measurements only, never the true pH.
    """

    setpoint: float
    band: float = 0.05
    low_trip: float = 5.5
    high_trip: float = 9.8
    trip_reset_band: float = 0.5
    valve_open: bool = False
    low_tripped: bool = False
    high_tripped: bool = False
    trip_count: int = 0
    missing_reading_count: int = 0

    def __post_init__(self) -> None:
        if self.band <= 0:
            raise ValueError("band must be positive")
        if not self.low_trip < self.setpoint - self.band < self.setpoint + self.band < (
            self.high_trip
        ):
            raise ValueError("setpoint plus/minus band must sit inside the pH trips")

    @property
    def tripped(self) -> bool:
        return self.low_tripped or self.high_tripped

    @property
    def shutoff_closed(self) -> bool:
        """True while the low trip holds the supply shutoff closed."""
        return self.low_tripped

    def decide(self, measured: float | None) -> bool:
        """Return whether the dosing valve should be open."""
        if measured is None:
            self.missing_reading_count += 1
            self.valve_open = False
            return False
        self._check_high_trip(measured)
        if self.low_tripped:
            if measured > self.low_trip + self.trip_reset_band:
                self.low_tripped = False
            else:
                self.valve_open = False
                return False
        if measured <= self.low_trip:
            self.low_tripped = True
            self.trip_count += 1
            self.valve_open = False
            return False
        if measured >= self.setpoint + self.band:
            self.valve_open = True
        elif measured <= self.setpoint - self.band:
            self.valve_open = False
        return self.valve_open

    def _check_high_trip(self, measured: float) -> None:
        if self.high_tripped:
            if measured < self.high_trip - self.trip_reset_band:
                self.high_tripped = False
        elif measured >= self.high_trip:
            self.high_tripped = True
            self.trip_count += 1
