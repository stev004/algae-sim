"""Closed-loop demo: AutoLab's optimiser tunes a simulated algae culture.

    AutoLab optimiser ──proposal (setpoint, light)──▶ reactor runtime
          ▲                                          │ local controller owns the heater
          │                                          ▼ 36 h simulated batch
     observation ◀── QC ◀── analysis ◀── AutoLab sensor read (OD750 evidence)

The optimiser never touches the heater: it proposes conditions, the reactor's own
controller enforces limits and runs the batch, and the outcome comes back only
through the read-only sensor seam plus device-reported energy use.

Run:  uv run python -m algae_sim.autolab.campaign --trials 20 --seed 7
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from autonomous_lab.domain.enums import ObjectiveDirection
from autonomous_lab.domain.objectives import ObjectiveMetric
from autonomous_lab.domain.optimization import OptimizationObservation
from autonomous_lab.domain.parameters import ContinuousParameter, ParameterSpace
from autonomous_lab.hardware.sensors.contracts import RawSensorMeasurement
from autonomous_lab.optimization.interface import (
    FeedbackDisposition,
    OptimizationProblem,
    StrategyFeedback,
)
from autonomous_lab.optimization.optuna_strategy import OptunaStrategy

from algae_sim.autolab.sensor import ReactorBench, ReactorODSensor, default_calibration, od_request
from algae_sim.reactor import BatchResult, Reactor, ReactorConfig

SENSOR_ID = "algae-sim-od-001"
# QC: after warm-up, measured temperature should stay near the setpoint.
QC_WARMUP_H = 3.0
QC_MAX_MEAN_DEVIATION_C = 1.0


def build_problem() -> OptimizationProblem:
    space = ParameterSpace(
        parameters={
            "temperature_setpoint": ContinuousParameter(
                unit="degC", minimum=Decimal(20), maximum=Decimal(36), resolution=Decimal("0.1")
            ),
            "light_intensity": ContinuousParameter(
                unit="micromole / meter ** 2 / second",
                minimum=Decimal(30),
                maximum=Decimal(400),
                resolution=Decimal(1),
            ),
        }
    )
    return OptimizationProblem(
        parameter_space=space,
        objective_metrics=(
            ObjectiveMetric(
                name="final_od750", direction=ObjectiveDirection.MAXIMIZE, unit="dimensionless"
            ),
            ObjectiveMetric(
                name="energy_kwh", direction=ObjectiveDirection.MINIMIZE, unit="kilowatt_hour"
            ),
        ),
        performance_constraints=(),
    )


@dataclass(slots=True)
class TrialOutcome:
    index: int
    setpoint_c: float
    light: float
    disposition: str
    od750: float | None
    energy_kwh: float
    qc_reason: str | None
    measurement: dict[str, Any] | None = field(default=None, repr=False)


def qc_check(result: BatchResult, measurement: RawSensorMeasurement) -> str | None:
    """Return a failure reason, or None if the run is fit to learn from."""
    if measurement.metadata.get("saturated"):
        return "od_saturated"
    if result.trip_count:
        return "over_temperature_trip"
    readings = [
        s.measured_temperature_c
        for s in result.telemetry
        if s.elapsed_s >= QC_WARMUP_H * 3600 and s.measured_temperature_c is not None
    ]
    if len(readings) < 0.9 * len(result.telemetry) * (1 - QC_WARMUP_H / result.config.batch_hours):
        return "too_many_missing_temperature_readings"
    setpoint = result.config.temperature_setpoint_c
    deviation = sum(abs(r - setpoint) for r in readings) / len(readings)
    if deviation > QC_MAX_MEAN_DEVIATION_C:
        return f"temperature_out_of_control (mean deviation {deviation:.1f} °C)"
    return None


async def run_campaign(
    *, trials: int, seed: int, startup: int = 8, faulty_trials: frozenset[int] = frozenset()
) -> list[TrialOutcome]:
    campaign_id = uuid5(NAMESPACE_URL, f"algae-sim:campaign:{seed}")
    strategy = OptunaStrategy(
        campaign_id=campaign_id,
        problem=build_problem(),
        seed=seed,
        startup_experiments=startup,
        prefer_gp=False,
    )
    bench = ReactorBench()
    sensor = ReactorODSensor(
        sensor_id=SENSOR_ID, source=bench, calibration=default_calibration(SENSOR_ID)
    )
    await sensor.open()
    if (await sensor.self_test()).status.value != "passed":
        raise RuntimeError("sensor self-test failed; refusing to run")

    outcomes: list[TrialOutcome] = []
    for index in range(trials):
        proposal = strategy.ask()
        values = proposal.parameter_values
        setpoint = float(values["temperature_setpoint"].value)
        light = float(values["light_intensity"].value)
        config = ReactorConfig(
            temperature_setpoint_c=setpoint,
            light_umol_m2_s=light,
            seed=seed * 1000 + index,
            heater_fails_after_h=4.0 if index in faulty_trials else None,
        )

        # Execution: the reactor runtime owns control for the whole batch.
        reactor_id = f"reactor-{index:03d}"
        reactor = Reactor(config)
        bench.add(reactor_id, reactor)
        result = reactor.run()

        # Measurement: through AutoLab's read-only sensor contract only.
        run_id: UUID = uuid5(NAMESPACE_URL, f"{proposal.experiment_id}:run")
        measurement = await sensor.read(od_request(proposal.experiment_id, run_id, reactor_id))
        bench.remove(reactor_id)

        od = float(measurement.raw_value)
        energy_kwh = round(result.total_energy_wh / 1000, 4)
        reason = qc_check(result, measurement)
        if reason is None:
            observation = OptimizationObservation(
                observation_id=uuid5(NAMESPACE_URL, f"{proposal.experiment_id}:observation"),
                campaign_id=campaign_id,
                experiment_id=proposal.experiment_id,
                result_id=uuid5(NAMESPACE_URL, f"{proposal.experiment_id}:result"),
                objective_values={
                    "final_od750": measurement.raw_value,
                    "energy_kwh": Decimal(str(energy_kwh)),
                },
                constraint_residuals={},
                feasible=True,
                qc_eligible=True,
            )
            strategy.tell(StrategyFeedback.observed(observation, proposal.strategy_trial_id))
            disposition = "observed"
        else:
            strategy.tell(
                StrategyFeedback.excluded(
                    proposal.strategy_trial_id, FeedbackDisposition.QC_FAILED, reason=reason
                )
            )
            disposition = "qc_failed"
        outcomes.append(
            TrialOutcome(
                index=index,
                setpoint_c=setpoint,
                light=light,
                disposition=disposition,
                od750=od,
                energy_kwh=energy_kwh,
                qc_reason=reason,
                measurement=json.loads(measurement.model_dump_json()),
            )
        )
    await sensor.close()
    return outcomes


def pareto_front(outcomes: list[TrialOutcome]) -> list[TrialOutcome]:
    """Observed trials not beaten on both growth (higher) and energy (lower)."""
    observed = [o for o in outcomes if o.disposition == "observed" and o.od750 is not None]
    front = []
    for o in observed:
        assert o.od750 is not None
        dominated = any(
            p.od750 is not None
            and p.od750 >= o.od750
            and p.energy_kwh <= o.energy_kwh
            and (p.od750 > o.od750 or p.energy_kwh < o.energy_kwh)
            for p in observed
        )
        if not dominated:
            front.append(o)
    return sorted(front, key=lambda o: o.energy_kwh)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--trials", type=int, default=20)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--startup", type=int, default=8, help="random trials before TPE")
    parser.add_argument(
        "--faulty", type=int, nargs="*", default=[5], help="trial indices whose heater fails"
    )
    parser.add_argument("--out", type=Path, default=None, help="write trials.jsonl evidence")
    args = parser.parse_args(argv)
    logging.getLogger("optuna").setLevel(logging.WARNING)

    outcomes = asyncio.run(
        run_campaign(
            trials=args.trials,
            seed=args.seed,
            startup=args.startup,
            faulty_trials=frozenset(args.faulty),
        )
    )
    print(" #  setpoint °C  light  OD750  energy kWh  result")
    for o in outcomes:
        note = "observed" if o.qc_reason is None else f"QC FAIL: {o.qc_reason}"
        print(
            f"{o.index:2d}  {o.setpoint_c:10.1f}  {o.light:5.0f}  {o.od750:5.3f}"
            f"  {o.energy_kwh:10.3f}  {note}"
        )
    print("\nPareto front (best growth for the energy spent):")
    for o in pareto_front(outcomes):
        print(f"  {o.setpoint_c:4.1f} °C, {o.light:3.0f} µmol/m²/s -> OD {o.od750:.3f}"
              f" for {o.energy_kwh:.3f} kWh")
    if args.out is not None:
        args.out.mkdir(parents=True, exist_ok=True)
        with (args.out / "trials.jsonl").open("w") as handle:
            for o in outcomes:
                handle.write(json.dumps(asdict(o)) + "\n")
        print(f"\nwrote {args.out / 'trials.jsonl'}")


if __name__ == "__main__":
    main()
