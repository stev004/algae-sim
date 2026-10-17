# Intended architecture

[Project overview](../README.md) · [Working agreement](../AGENTS.md) · [Model](model.md) · [Progress](progress.md)

Status: design only. Local environment setup is verified; the first thermal simulator is the next implementation. No cloud resources exist for this project.

```text
LOCAL COMPUTER — initially one device; later five independent devices
  true vessel state → sensor reading → local controller → applied heater
          ↑                                                   │
          └──────────────── heat input ────────────────────────┘
  device runtime: validate targets, enforce limits, record and buffer
          │ telemetry                         ↑ target request
          ▼                                   │
AZURE — later milestones, not deployed
  IoT Hub → Functions → one database → small backend/dashboard
     ↑                                      │
     └──────── supervisory command service ─┘
```

The controller will use sensor measurements, not hidden true state. The cloud requests a target; the device decides whether to accept and apply it and controls its heater locally. Local control and protective behaviour must survive network outages. Delivery or acknowledgement does not prove a target was reached; subsequent measurements provide that evidence.

Initially the simulator runs locally, including when first connected to Azure. That is cloud-connected, not fully cloud-hosted. Database, command transport, concurrency and deployment choices remain open until their milestones. Each eventual device will have its own authentication identity.
