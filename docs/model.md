# First thermal model: proposed next learning step

[Project overview](../README.md) · [Working agreement](../AGENTS.md) · [Architecture](architecture.md) · [Progress](progress.md)

Status: the first fixed-input implementation is in `thermal_sim.py`, with checks in `tests/test_thermal_sim.py`. Ten-minute heating and cooling results are ready for discussion before adding feedback control. The longer transient and time-step experiments described below remain future learning steps.

Treat the vessel as perfectly mixed, with a single temperature. Stored heat changes by heater input minus heat lost to the surroundings:

    C × dT/dt = P × u − K × (T − Ta)

- T: true vessel temperature, degrees Celsius.
- Ta: ambient temperature, degrees Celsius.
- C: effective heat capacity of vessel and contents, joules per kelvin.
- P: heater power, watts (joules per second).
- u: applied heater fraction, from 0 to 1; initially held fixed manually.
- K: heat-transfer coefficient for the whole vessel, watts per kelvin.
- t: simulation time, seconds.

A one-degree Celsius temperature difference equals one kelvin. The right-hand side is net power; dividing by C gives temperature change per second. If the vessel is colder than ambient, the surroundings add heat.

Proposed illustrative values, not measurements of a real reactor: C = 4180 J/K, P = 100 W, K = 5 W/K, Ta = 20 °C and initial T = 20 °C. These omit spatial gradients, evaporation, metabolic heat and changing physical properties.

For fixed input, equilibrium is T_eq = Ta + P × u / K. With the illustrative heater fully on, this is 40 °C. It is approached gradually, not reached instantly. The time constant is C/K = 836 s; after one time constant about 63.2% of the initial gap to equilibrium has closed.

The proposed first implementation uses forward Euler:

    T_next = T + dt × (P × u − K × (T − Ta)) / C

Start with dt = 1 simulated second. For this linear model, Euler is stable when 0 < dt < 2C/K, and avoids alternating around equilibrium when dt <= C/K; accuracy still requires a sufficiently small step. We will compare with the exact constant-input solution and examine a smaller time step.

    T(t) = T_eq + (T_initial − T_eq) × exp(−t / (C/K))

Simulation time advances by dt per update, independently of how quickly the computer runs. The first model is deterministic and needs no random seed; seed handling comes with stochastic sensors or biology. Sensor, controller and actuator will be separate concepts when introduced. No biological model is implemented or validated.
