"""Simulated algae photobioreactor.

The package separates four things that a real reactor also keeps apart:

* true physical state (``ReactorState``) that only the simulator can see;
* sensor measurements (``instruments``) that are noisy, quantised and can fail;
* controller decisions (``control``) made only from measurements;
* applied actuator state (``instruments.Heater``), which may differ from the command.

All numerical parameters are illustrative teaching values, not measurements of a real
reactor or a validated biological model.
"""

from algae_sim.reactor import BatchResult, Reactor, ReactorConfig, run_batch

__all__ = ["BatchResult", "Reactor", "ReactorConfig", "run_batch"]
__version__ = "0.2.0"
