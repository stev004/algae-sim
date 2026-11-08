# Reactor model

[Project overview](../README.md) · [Working agreement](../AGENTS.md) · [Architecture](architecture.md) · [Progress](progress.md)

**Every value below is illustrative. The growth model is educational and not validated.**

## Thermal (`thermal.py`)

A perfectly mixed vessel with one temperature:

    C × dT/dt = P × u − K × (T − Ta)

C = 4180 J/K (about 1 L of water), P = 100 W heater, K = 5 W/K loss to a 20 °C room, u = applied heater fraction (0–1). At full power the equilibrium is Ta + P/K = 40 °C, and the time constant C/K is 836 s. Forward Euler is used with dt = 10 s, well below the 2C/K stability limit; a test checks it against the exact solution. There's no cooling, so setpoints below room temperature can't be reached.

## Growth (`growth.py`)

    dX/dt = μmax · f_T(T) · f_I(I) · X · (1 − X/Xmax) − loss(T) · X

- **f_T**: cardinal temperature model with inflection (Rosso et al., 1993). It's zero at or below 8 °C and at or above 38 °C, and 1 at the 30 °C optimum.
- **f_I**: Steele light curve. It's 1 at 150 µmol/m²/s and drops at higher light (photoinhibition).
- **Crowding**: logistic limit Xmax = 1.5 g/L, a crude stand-in for self-shading and nutrients.
- **Loss**: 0.005 /h basal, plus 0.05 /h per °C above 38 °C (heat stress).
- μmax = 0.08 /h, starting biomass 0.05 g/L, batch length 36 h.

The rough shapes are realistic for green microalgae; the numbers aren't fitted to any strain. There's no pH, CO₂, nutrient, mixing or light-path model.

## Instruments, control and energy

- **Temperature sensor**: Gaussian noise (σ 0.05 °C), 0.1 °C resolution, optional calibration offset and dropout probability.
- **OD750 sensor**: OD = 2.0 × biomass (g/L) + noise (σ 0.01). It saturates at OD 4.
- **Controller**: switches the heater on below setpoint − 0.3 °C and off above setpoint + 0.3 °C. A missing reading turns the heater off. A reading at 39 °C or above latches a trip until the reading drops below 37 °C.
- **Energy**: heater energy is integrated from the applied power. Light energy is 0.2 W per µmol/m²/s, an illustrative LED panel.

Each reactor derives independent sensor random streams from its seed, so the same configuration and seed always give identical telemetry. A test checks this.
