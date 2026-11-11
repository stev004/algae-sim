"""One simulated photobioreactor running a batch with local temperature control.

Each step of length ``dt_s``:

1. the temperature and pH probes read the hidden true state;
2. the thermostat and the pH controller decide from those measurements only;
3. the heater and the CO2 valve apply their commands (or not, if they have failed);
4. true temperature, biomass and pH advance using the *applied* actuator state.

pH is held by dosing CO2 (``algae_sim.carbon``). The medium is assumed to be
prepared at the pH setpoint when the culture is inoculated (illustrative).
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from algae_sim.carbon import CarbonParams, PhGrowthParams, ph_factor, step_ph
from algae_sim.control import HysteresisController, PhController
from algae_sim.growth import GrowthParams, gross_rate_per_h, step_biomass
from algae_sim.instruments import (
    Co2Valve,
    Heater,
    OpticalDensitySensor,
    PhSensor,
    TemperatureSensor,
)
from algae_sim.records import TelemetrySample
from algae_sim.thermal import ThermalParams, step_temperature

DEFAULT_START_TIME = datetime(2026, 1, 1, tzinfo=UTC)
LIGHT_WATTS_PER_UMOL_M2_S = 0.2  # illustrative LED panel efficiency


@dataclass(frozen=True, slots=True)
class ReactorConfig:
    temperature_setpoint_c: float = 30.0
    light_umol_m2_s: float = 150.0
    ph_setpoint: float = 7.5
    start_ph: float | None = None  # None: medium prepared at the pH setpoint
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
    ph_dropout_probability: float = 0.0
    ph_sensor_offset: float = 0.0
    co2_valve_sticks: str | None = None  # "open" or "closed"
    co2_valve_sticks_after_h: float = 0.0
    # Culture crash (e.g. contamination by grazers): biomass is lost from this hour.
    culture_crash_after_h: float | None = None
    culture_crash_loss_per_h: float = 0.15
    thermal: ThermalParams = field(default_factory=ThermalParams)
    growth: GrowthParams = field(default_factory=GrowthParams)
    carbon: CarbonParams = field(default_factory=CarbonParams)
    ph_growth: PhGrowthParams = field(default_factory=PhGrowthParams)

    def __post_init__(self) -> None:
        if self.batch_hours <= 0:
            raise ValueError("batch_hours must be positive")
        if not 0 < self.dt_s < self.thermal.max_stable_dt_s():
            raise ValueError("dt_s must be positive and below the Euler stability limit")
        if self.telemetry_interval_s < self.dt_s:
            raise ValueError("telemetry_interval_s must be at least dt_s")
        if self.light_umol_m2_s < 0:
            raise ValueError("light must be non-negative")
        if self.co2_valve_sticks not in (None, "open", "closed"):
            raise ValueError("co2_valve_sticks must be None, 'open' or 'closed'")

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
    co2_g: float = 0.0
    ph_trip_count: int = 0
    missing_ph_readings: int = 0

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
            "co2_g": round(self.co2_g, 3),
            "ph_trip_count": self.ph_trip_count,
            "missing_ph_readings": self.missing_ph_readings,
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
        # Drawn after the existing instruments so their noise streams are unchanged.
        self.ph_sensor = PhSensor(
            offset=config.ph_sensor_offset,
            dropout_probability=config.ph_dropout_probability,
            rng=random.Random(rng.getrandbits(32)),
        )
        self.heater = Heater()
        self.co2_valve = Co2Valve()
        self.controller = HysteresisController(setpoint_c=config.temperature_setpoint_c)
        self.ph_controller = PhController(setpoint=config.ph_setpoint)
        # True state: only the simulator may read these directly.
        self.elapsed_s = 0.0
        self.true_temperature_c = config.start_temperature_c
        self.true_biomass_g_per_l = config.start_biomass_g_per_l
        self.light_umol_m2_s = config.light_umol_m2_s
        self.heater_energy_wh = 0.0
        self.light_energy_wh = 0.0
        self.true_ph = config.ph_setpoint if config.start_ph is None else config.start_ph
        self.co2_dosed_g = 0.0
        # Safe state: heater forced off and lights off, whatever the controller wants.
        self.safe_state = False
        self.max_true_temperature_c = config.start_temperature_c
        self.last_measured_c: float | None = None
        self.last_command = 0.0
        self.last_applied = 0.0
        self.last_measured_ph: float | None = None
        self.last_valve_command = False
        self.last_valve_open = False

    def step(self) -> None:
        cfg = self.config
        fails_at = cfg.heater_fails_after_h
        if fails_at is not None and self.elapsed_s >= fails_at * 3600:
            self.heater.failed = True
        if cfg.co2_valve_sticks and self.elapsed_s >= cfg.co2_valve_sticks_after_h * 3600:
            self.co2_valve.stuck = cfg.co2_valve_sticks
        self.last_measured_c = self.temperature_sensor.read(self.true_temperature_c)
        decided = self.controller.decide(self.last_measured_c)
        self.last_command = 0.0 if self.safe_state else decided
        self.last_applied = self.heater.apply(self.last_command)
        self.last_measured_ph = self.ph_sensor.read(self.true_ph)
        wants_co2 = self.ph_controller.decide(self.last_measured_ph)
        self.last_valve_command = wants_co2 and not self.safe_state
        # Safe state closes the supply shutoff, like the low-pH trip does.
        self.last_valve_open = self.co2_valve.apply(
            self.last_valve_command,
            shutoff_closed=self.safe_state or self.ph_controller.shutoff_closed,
        )
        light = 0.0 if self.safe_state else self.light_umol_m2_s
        # Growth uses the state at the start of the step (explicit Euler).
        ph_response = ph_factor(self.true_ph, cfg.ph_growth)
        gross = gross_rate_per_h(
            self.true_biomass_g_per_l, self.true_temperature_c, light, cfg.growth, ph_response
        )
        produced_g_per_l = max(0.0, gross) * self.true_biomass_g_per_l * cfg.dt_s / 3600
        self.true_biomass_g_per_l = step_biomass(
            self.true_biomass_g_per_l,
            self.true_temperature_c,
            light,
            cfg.growth,
            cfg.dt_s,
            ph_response,
        )
        carbon = cfg.carbon
        dosed_g = carbon.valve_flow_g_per_h * cfg.dt_s / 3600 if self.last_valve_open else 0.0
        self.co2_dosed_g += dosed_g
        self.true_ph = step_ph(
            self.true_ph,
            co2_dosed_g=dosed_g,
            co2_fixed_g=produced_g_per_l * carbon.volume_l * carbon.co2_per_g_biomass,
            p=carbon,
            dt_s=cfg.dt_s,
        )
        crash_at = cfg.culture_crash_after_h
        if crash_at is not None and self.elapsed_s >= crash_at * 3600:
            self.true_biomass_g_per_l *= 1.0 - cfg.culture_crash_loss_per_h * cfg.dt_s / 3600
        self.true_temperature_c = step_temperature(
            self.true_temperature_c, self.last_applied, cfg.ambient_c, cfg.thermal, cfg.dt_s
        )
        self.heater_energy_wh += self.last_applied * cfg.thermal.heater_power_w * cfg.dt_s / 3600
        self.light_energy_wh += LIGHT_WATTS_PER_UMOL_M2_S * light * cfg.dt_s / 3600
        self.max_true_temperature_c = max(self.max_true_temperature_c, self.true_temperature_c)
        self.elapsed_s += cfg.dt_s

    def enter_safe_state(self) -> None:
        self.safe_state = True

    @property
    def total_energy_wh(self) -> float:
        return self.heater_energy_wh + self.light_energy_wh

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
            light_umol_m2_s=0.0 if self.safe_state else self.light_umol_m2_s,
            controller_tripped=self.controller.tripped,
            true_temperature_c=self.true_temperature_c,
            true_biomass_g_per_l=self.true_biomass_g_per_l,
            measured_ph=self.last_measured_ph,
            ph_setpoint=self.config.ph_setpoint,
            co2_valve_open=self.last_valve_open,
            co2_g=self.co2_dosed_g,
            ph_tripped=self.ph_controller.tripped,
            true_ph=self.true_ph,
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
            light_energy_wh=self.light_energy_wh,
            trip_count=self.controller.trip_count,
            missing_temperature_readings=self.controller.missing_reading_count,
            max_true_temperature_c=self.max_true_temperature_c,
            co2_g=self.co2_dosed_g,
            ph_trip_count=self.ph_controller.trip_count,
            missing_ph_readings=self.ph_controller.missing_reading_count,
        )


def run_batch(config: ReactorConfig) -> BatchResult:
    return Reactor(config).run()
