"""One simulated photobioreactor running a batch with local temperature control.

Each step of length ``dt_s``:

1. the temperature sensor reads the hidden true temperature;
2. the controller decides a heater command from that measurement only;
3. the heater applies the command (or not, if it has failed);
4. the true temperature and biomass advance using the *applied* heater state.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from algae_sim.control import HysteresisController
from algae_sim.growth import GrowthParams, step_biomass
from algae_sim.instruments import Heater, OpticalDensitySensor, TemperatureSensor
from algae_sim.records import TelemetrySample
from algae_sim.thermal import ThermalParams, step_temperature

DEFAULT_START_TIME = datetime(2026, 1, 1, tzinfo=UTC)
LIGHT_WATTS_PER_UMOL_M2_S = 0.2  # illustrative LED panel efficiency


@dataclass(frozen=True, slots=True)
class ReactorConfig:
    temperature_setpoint_c: float = 30.0
    light_umol_m2_s: float = 150.0
    batch_hours: float = 36.0
    ambient_c: float = 20.0
    start_temperature_c: float = 20.0
    start_biomass_g_per_l: float = 0.05
    dt_s: float = 10.0
    telemetry_interval_s: float = 600.0
    seed: int = 0
    # Fault injection
    heater_fails_after_h: float | None = None
    temperature_dropout_probability: float = 0.0
    temperature_sensor_offset_c: float = 0.0
    thermal: ThermalParams = field(default_factory=ThermalParams)
    growth: GrowthParams = field(default_factory=GrowthParams)

    def __post_init__(self) -> None:
        if self.batch_hours <= 0:
            raise ValueError("batch_hours must be positive")
        if not 0 < self.dt_s < self.thermal.max_stable_dt_s():
            raise ValueError("dt_s must be positive and below the Euler stability limit")
        if self.telemetry_interval_s < self.dt_s:
            raise ValueError("telemetry_interval_s must be at least dt_s")
        if self.light_umol_m2_s < 0:
            raise ValueError("light must be non-negative")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class BatchResult:
    config: ReactorConfig
    telemetry: list[TelemetrySample]
    final_od750: float
    final_true_biomass_g_per_l: float
    heater_energy_wh: float
    light_energy_wh: float
    trip_count: int
    missing_temperature_readings: int
    max_true_temperature_c: float

    @property
    def total_energy_wh(self) -> float:
        return self.heater_energy_wh + self.light_energy_wh

    def summary(self) -> dict[str, Any]:
        return {
            "final_od750": self.final_od750,
            "final_true_biomass_g_per_l": round(self.final_true_biomass_g_per_l, 6),
            "heater_energy_wh": round(self.heater_energy_wh, 3),
            "light_energy_wh": round(self.light_energy_wh, 3),
            "total_energy_wh": round(self.total_energy_wh, 3),
            "trip_count": self.trip_count,
            "missing_temperature_readings": self.missing_temperature_readings,
            "max_true_temperature_c": round(self.max_true_temperature_c, 3),
        }


class Reactor:
    def __init__(self, config: ReactorConfig) -> None:
        self.config = config
        rng = random.Random(config.seed)
        self.temperature_sensor = TemperatureSensor(
            offset_c=config.temperature_sensor_offset_c,
            dropout_probability=config.temperature_dropout_probability,
            rng=random.Random(rng.getrandbits(32)),
        )
        self.od_sensor = OpticalDensitySensor(rng=random.Random(rng.getrandbits(32)))
        self.heater = Heater()
        self.controller = HysteresisController(setpoint_c=config.temperature_setpoint_c)
        # True state: only the simulator may read these directly.
        self.elapsed_s = 0.0
        self.true_temperature_c = config.start_temperature_c
        self.true_biomass_g_per_l = config.start_biomass_g_per_l
        self.heater_energy_wh = 0.0
        self.max_true_temperature_c = config.start_temperature_c
        self.last_measured_c: float | None = None
        self.last_command = 0.0
        self.last_applied = 0.0

    def step(self) -> None:
        cfg = self.config
        fails_at = cfg.heater_fails_after_h
        if fails_at is not None and self.elapsed_s >= fails_at * 3600:
            self.heater.failed = True
        self.last_measured_c = self.temperature_sensor.read(self.true_temperature_c)
        self.last_command = self.controller.decide(self.last_measured_c)
        self.last_applied = self.heater.apply(self.last_command)
        # Growth uses the temperature at the start of the step (explicit Euler).
        self.true_biomass_g_per_l = step_biomass(
            self.true_biomass_g_per_l,
            self.true_temperature_c,
            cfg.light_umol_m2_s,
            cfg.growth,
            cfg.dt_s,
        )
        self.true_temperature_c = step_temperature(
            self.true_temperature_c, self.last_applied, cfg.ambient_c, cfg.thermal, cfg.dt_s
        )
        self.heater_energy_wh += self.last_applied * cfg.thermal.heater_power_w * cfg.dt_s / 3600
        self.max_true_temperature_c = max(self.max_true_temperature_c, self.true_temperature_c)
        self.elapsed_s += cfg.dt_s

    def read_od750(self) -> float:
        """Take one optical-density measurement of the current culture."""
        return self.od_sensor.read(self.true_biomass_g_per_l)

    def sample(self, sequence: int) -> TelemetrySample:
        return TelemetrySample(
            sequence=sequence,
            elapsed_s=self.elapsed_s,
            measured_temperature_c=self.last_measured_c,
            measured_od750=self.read_od750(),
            temperature_setpoint_c=self.config.temperature_setpoint_c,
            heater_command=self.last_command,
            heater_applied=self.last_applied,
            light_umol_m2_s=self.config.light_umol_m2_s,
            controller_tripped=self.controller.tripped,
            true_temperature_c=self.true_temperature_c,
            true_biomass_g_per_l=self.true_biomass_g_per_l,
        )

    def run(self) -> BatchResult:
        cfg = self.config
        total_steps = round(cfg.batch_hours * 3600 / cfg.dt_s)
        steps_per_sample = max(1, round(cfg.telemetry_interval_s / cfg.dt_s))
        telemetry = [self.sample(0)]
        for index in range(1, total_steps + 1):
            self.step()
            if index % steps_per_sample == 0 or index == total_steps:
                telemetry.append(self.sample(len(telemetry)))
        return BatchResult(
            config=cfg,
            telemetry=telemetry,
            final_od750=telemetry[-1].measured_od750,
            final_true_biomass_g_per_l=self.true_biomass_g_per_l,
            heater_energy_wh=self.heater_energy_wh,
            light_energy_wh=LIGHT_WATTS_PER_UMOL_M2_S * cfg.light_umol_m2_s * cfg.batch_hours,
            trip_count=self.controller.trip_count,
            missing_temperature_readings=self.controller.missing_reading_count,
            max_true_temperature_c=self.max_true_temperature_c,
        )


def run_batch(config: ReactorConfig) -> BatchResult:
    return Reactor(config).run()
