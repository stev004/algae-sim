"""The simulated photobioreactor as a *device*: firmware plus a network service.

* ``core.DeviceCore``: the device firmware. It owns reactors, setpoints, local
  control and protection, faults, energy accounting, the OD probe and the device
  clock. Standard library only.
* ``service``: hosts one ``DeviceCore`` as a long-running HTTP/JSON service that
  persists its state to disk, so it outlives (and can be restarted independently
  of) whatever lab software is talking to it.

AutoLab reaches it through ``algae_sim.autolab.remote`` (adapter ``algae-sim-device``),
or hosts a ``DeviceCore`` in-process through ``algae_sim.autolab.controller``
(adapter ``algae-sim-reactor``). Both adapters run the same firmware.
"""
