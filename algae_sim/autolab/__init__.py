"""AutoLab integration. Requires the optional extra: ``uv sync --extra autolab``.

* ``sensor``: the simulated reactor's OD750 probe exposed through AutoLab's
  ``ReadOnlySensor`` contract and registered as the ``algae_sim_od`` adapter.
* ``campaign``: a closed loop where AutoLab's optimiser proposes setpoints, the
  reactor's own controller runs the batch, and the result returns through the sensor.

The core ``algae_sim`` package never imports AutoLab; only this subpackage does.
"""
