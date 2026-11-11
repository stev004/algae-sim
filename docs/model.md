# Reactor model

[Project overview](../README.md) · [Working agreement](../AGENTS.md) · [Architecture](architecture.md) · [Progress](progress.md)

**The model is educational and not validated.** Growth parameters come from published *Chlorella vulgaris* literature (see [Where the numbers come from](#where-the-numbers-come-from)); values without a source are marked illustrative.

## Thermal (`thermal.py`)

A perfectly mixed vessel with one temperature:

    C × dT/dt = P × u − K × (T − Ta)

C = 4180 J/K (about 1 L of water), P = 100 W heater, K = 5 W/K loss to a 20 °C room, u = applied heater fraction (0–1). At full power the equilibrium is Ta + P/K = 40 °C, and the time constant C/K is 836 s. Forward Euler is used with dt = 10 s, well below the 2C/K stability limit; a test checks it against the exact solution. There's no cooling, so setpoints below room temperature can't be reached.

## Growth (`growth.py`)

    dX/dt = μmax · f_T(T) · f_I(I) · f_pH(pH) · X · (1 − X/Xmax) − loss(T) · X

- **f_T**: cardinal temperature model with inflection (Rosso et al., 1993). It's zero at or below 2.2 °C and at or above 42.8 °C, and 1 at the 28.9 °C optimum.
- **f_I**: light curve with photoinhibition, f(I) = I / (I + I_k·(I/I_opt − 1)²) (Bernard & Rémond 2012 form; Schwaderer et al. 2011 "Model 2"). It's 1 at I_opt = 142 µmol/m²/s, with I_k = μmax/α = 23.5 µmol/m²/s, and keeps 0.84 of its best at 400 µmol/m²/s.
- **f_pH**: cardinal pH model (Rosso et al., 1995). It's zero at or below pH 3 and at or above 9, and 1 at the pH 7.5 optimum (about 0.9 at 6.5 and 0.73 at 8.5), from *C. vulgaris* measurements (Choi & Lee 2011). The device's own low-pH trip at 5.5 is a protection limit, separate from the biology. The fall-off at high pH also stands in for carbon limitation.
- **Crowding**: logistic limit Xmax = 2.3 g/L, a crude stand-in for self-shading and nutrients.
- **Loss**: 0.007 /h maintenance, plus 0.05 /h per °C above 42.8 °C (heat stress, illustrative). The heater can't reach 42.8 °C, so heat stress only matters in tests.
- μmax = 0.08 /h (about 1.9 per day), starting biomass 0.05 g/L, batch length 36 h.

There's no nutrient, dissolved-oxygen, mixing or light-path model.

## pH and CO₂ (`carbon.py`)

pH is a setpoint the device holds by dosing CO₂; CO₂ used is measured, not chosen. With V = 1 L and a lumped buffer of β = 0.5 pH per g CO₂ per L:

    dpH/dt = (pH_air − pH)/τ + β · (CO₂ fixed − CO₂ dosed)/V

- **Photosynthesis** fixes 1.83 g CO₂ per g of new biomass (gross production), which raises pH.
- **Dosing**: the valve delivers 1 g CO₂/h while open, which lowers pH.
- **Gas exchange**: pH relaxes towards the air-equilibrium value pH_air = 8.6 with τ = 8 h. CO₂ escapes when pH is held below it, so lower setpoints cost more CO₂: about 19 g over 36 h at pH 6.5, 8 g at 7.8 and 1.6 g at 8.5.
- CO₂ can only lower pH and there's no base dosing, so a setpoint above pH_air can only be held once photosynthesis outpaces gas exchange. At 9.0 a young culture drifts down by about 0.3 and fails pH tracking QC. The medium is assumed to be prepared at the setpoint at inoculation.

A real carbonate system is non-linear and depends on alkalinity; this single buffer coefficient is a teaching simplification.

## Instruments, control and energy

- **Temperature sensor**: Gaussian noise (σ 0.05 °C), 0.1 °C resolution, optional calibration offset and dropout probability.
- **OD750 sensor**: OD = 3.2 × biomass (g/L) + noise (σ 0.01). It saturates at OD 4.
- **pH sensor**: Gaussian noise (σ 0.01), 0.01 resolution, optional offset and dropout probability.
- **Thermostat**: switches the heater on below setpoint − 0.3 °C and off above setpoint + 0.3 °C. A missing reading turns the heater off. A reading at 39 °C or above latches a trip until the reading drops below 37 °C.
- **pH controller**: opens the CO₂ valve above setpoint + 0.05 and closes it below setpoint − 0.05. A missing reading closes the valve (never dose blind). At pH 5.5 or below it trips and closes a separate supply shutoff, which works even if the dosing valve is stuck open, until pH recovers above 6.0. At 9.8 or above it raises a high-pH alarm; with no base dosing it can only report it.
- **Faults**: the heater can fail; the CO₂ valve can stick open (overdose, contained by the low trip) or closed (pH climbs with growth).
- **Energy**: heater energy is integrated from the applied power. Light energy is 0.2 W per µmol/m²/s, an illustrative LED panel.

Each reactor derives independent sensor random streams from its seed, so the same configuration and seed always give identical telemetry. A test checks this.

## Where the numbers come from

Values are literature-typical for *Chlorella vulgaris*, pooled across strains and studies, not a fit to one strain. Confidence says how well the source supports the number *as used here*.

| Parameter | Value | Source | Confidence |
|---|---|---|---|
| μmax | 0.08 /h (1.9 /day) | Pozzobon 2022, *Algal Research* 68:102874, doi:10.1016/j.algal.2022.102874 (2.11 /day measured at 25 °C, 200 µmol/m²/s); Rossi et al. 2023 (1.7–2 /day) | High |
| T_min / T_opt / T_max | 2.2 / 28.9 / 42.8 °C | Rossi, Carecci & Ficara 2023, *Sci. Total Environ.* 873:162275, doi:10.1016/j.scitotenv.2023.162275, Table 3: mean of 13 data sets | High for T_opt; T_min is poorly constrained (range −12 to 13 °C) |
| Light curve: I_opt, I_k | 142 µmol/m²/s, 23.5 µmol/m²/s | Schwaderer et al. 2011, *Limnol. Oceanogr.* 56:589, doi:10.4319/lo.2011.56.2.0589, web appendix: *C. vulgaris* fit (μmax 0.94 /day, α 0.040, I_opt 142.1) to Dauta et al. 1990 data, with the same curve form | High for the form and fit; see the light disagreement below |
| CO₂ per g biomass | 1.83 g/g | Chisti 2007, *Biotechnol. Adv.* 25:294, doi:10.1016/j.biotechadv.2007.02.001 (about 50 % carbon); *C. vulgaris* measured at 48 % (Griffiths 2011 thesis) | High for nitrogen-replete cultures (±20 % with nitrogen status) |
| OD750 per g/L | 3.2 | Griffiths et al. 2011, *J. Microbiol. Methods* 85:119, doi:10.1016/j.mimet.2011.02.005 | Medium: calibrations vary with pigment and instrument |
| Xmax | 2.3 g/L | Griffiths 2011 thesis: lab airlift reactor, 0.29 % CO₂, 250 µmol/m²/s | Medium |
| Maintenance loss | 0.007 /h | Kliphuis et al. 2011, doi:10.1007/s10811-010-9614-7, measured on *C. sorokiniana* | Low–medium (close relative, 37 °C) |
| pH_min / pH_opt / pH_max | 3.0 / 7.5 / 9.0 | Choi & Lee 2011, *J. Korean Soc. Environ. Eng.* 33:511, doi:10.4491/KSEE.2011.33.7.511: growth at pH 3–9, best at 7–7.5, cells died at 3 and 9; Rachlin & Grosso 1991, doi:10.1007/BF01065839, optimum 7.5–8.0 | Medium: one study for the limits |

**Independent check.** Choi & Lee 2011 measured *C. vulgaris* growth at 10–35 °C, data the temperature values were not taken from. Relative to its best rate it gives 0.17, 0.57, 0.75, 0.96, 1.00 and 0.88 at 10, 15, 20, 25, 30 and 35 °C; the model gives 0.21, 0.48, 0.75, 0.94, 1.00 and 0.83.

**Where sources disagree:**
- **Bright light.** Pozzobon 2022 supports the fitted curve (growth continues at very high light). Choi & Lee 2011 saw stronger losses above 200 µmol/m²/s in batch flasks, closer to a Steele curve. The fitted curve comes from a dedicated light-response study, so it is used.
- **pH 9.** Choi & Lee report cells dying at pH 9; Rachlin & Grosso's abstract reports slowed, not stopped, growth at 8.3–9.0.
- **Temperature in a stressed reactor.** Serra-Maia et al. 2016, *Algal Res.* 18:352, doi:10.1016/j.algal.2016.06.016: strain CCAP 211/11B in a bubble-column photobioreactor peaked near 23 °C and stopped growing by 30 °C. The authors attribute this to reactor stress (high oxygen, mortality), since cells in gentler conditions survived 35 °C. Their measured carbon content, 48.5–53 % of dry weight, supports the CO₂ value.
- **Cross-check, other species.** Bernard & Rémond 2012, doi:10.1016/j.biortech.2012.07.022, fit 5.2 / 38.7 / 45.8 °C for a high-temperature *C. pyrenoidosa* strain.

**Not modelled:** light through a dense culture (Yun & Park 2001, doi:10.1007/s002530100639, has *C. vulgaris* attenuation data).
- **Illustrative, no source sought:** the carbonate buffer, air-equilibrium pH, heat-stress loss, thermal and instrument values.
