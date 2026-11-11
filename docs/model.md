# Reactor model

[Project overview](../README.md) · [Working agreement](../AGENTS.md) · [Architecture](architecture.md) · [Progress](progress.md)

**Every value below is illustrative. The growth model is educational and not validated.**

## Thermal (`thermal.py`)

A perfectly mixed vessel with one temperature:

    C × dT/dt = P × u − K × (T − Ta)

C = 4180 J/K (about 1 L of water), P = 100 W heater, K = 5 W/K loss to a 20 °C room, u = applied heater fraction (0–1). At full power the equilibrium is Ta + P/K = 40 °C, and the time constant C/K is 836 s. Forward Euler is used with dt = 10 s, well below the 2C/K stability limit; a test checks it against the exact solution. There's no cooling, so setpoints below room temperature can't be reached.

## Growth (`growth.py`)

    dX/dt = μmax · f_T(T) · f_I(I) · f_pH(pH) · X · (1 − X/Xmax) − loss(T) · X

- **f_T**: cardinal temperature model with inflection (Rosso et al., 1993). It's zero at or below 8 °C and at or above 38 °C, and 1 at the 30 °C optimum.
- **f_I**: Steele light curve. It's 1 at 150 µmol/m²/s and drops at higher light (photoinhibition).
- **f_pH**: cardinal pH model (Rosso et al., 1995). It's zero at or below pH 5.5 and at or above 10, and 1 at the pH 7.8 optimum (about 0.67 at 6.5 and 0.9 at 8.5). The fall-off at high pH also stands in for carbon limitation.
- **Crowding**: logistic limit Xmax = 1.5 g/L, a crude stand-in for self-shading and nutrients.
- **Loss**: 0.005 /h basal, plus 0.05 /h per °C above 38 °C (heat stress).
- μmax = 0.08 /h, starting biomass 0.05 g/L, batch length 36 h.

The rough shapes are realistic for green microalgae; the numbers aren't fitted to any strain. There's no nutrient, dissolved-oxygen, mixing or light-path model.

## pH and CO₂ (`carbon.py`)

pH is a setpoint the device holds by dosing CO₂; CO₂ used is measured, not chosen. With V = 1 L and a lumped buffer of β = 0.5 pH per g CO₂ per L:

    dpH/dt = (pH_air − pH)/τ + β · (CO₂ fixed − CO₂ dosed)/V

- **Photosynthesis** fixes 1.8 g CO₂ per g of new biomass (gross production), which raises pH.
- **Dosing**: the valve delivers 1 g CO₂/h while open, which lowers pH.
- **Gas exchange**: pH relaxes towards the air-equilibrium value pH_air = 8.6 with τ = 8 h. CO₂ escapes when pH is held below it, so lower setpoints cost more CO₂: about 19 g over 36 h at pH 6.5, 8 g at 7.8 and 1.6 g at 8.5.
- CO₂ can only lower pH and there's no base dosing, so a setpoint above pH_air can only be held once photosynthesis outpaces gas exchange. At 9.0 a young culture drifts down by about 0.3 and fails pH tracking QC. The medium is assumed to be prepared at the setpoint at inoculation.

A real carbonate system is non-linear and depends on alkalinity; this single buffer coefficient is a teaching simplification.

## Instruments, control and energy

- **Temperature sensor**: Gaussian noise (σ 0.05 °C), 0.1 °C resolution, optional calibration offset and dropout probability.
- **OD750 sensor**: OD = 2.0 × biomass (g/L) + noise (σ 0.01). It saturates at OD 4.
- **pH sensor**: Gaussian noise (σ 0.01), 0.01 resolution, optional offset and dropout probability.
- **Thermostat**: switches the heater on below setpoint − 0.3 °C and off above setpoint + 0.3 °C. A missing reading turns the heater off. A reading at 39 °C or above latches a trip until the reading drops below 37 °C.
- **pH controller**: opens the CO₂ valve above setpoint + 0.05 and closes it below setpoint − 0.05. A missing reading closes the valve (never dose blind). At pH 5.5 or below it trips and closes a separate supply shutoff, which works even if the dosing valve is stuck open, until pH recovers above 6.0. At 9.8 or above it raises a high-pH alarm; with no base dosing it can only report it.
- **Faults**: the heater can fail; the CO₂ valve can stick open (overdose, contained by the low trip) or closed (pH climbs with growth).
- **Energy**: heater energy is integrated from the applied power. Light energy is 0.2 W per µmol/m²/s, an illustrative LED panel.

Each reactor derives independent sensor random streams from its seed, so the same configuration and seed always give identical telemetry. A test checks this.
