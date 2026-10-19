# Changelog

## 0.0.1 - 2026-10-18

- Initial version.
- Heat pump / Boiler changeover according to outdoor temperature, with 0.5 °C hysteresis.
- Day / Night / Away / Solar scheduling with anticipated preheating and heating demand hysteresis.
- Actual solar power production (power sensor, e.g. Solenso) instead of a forecast.
- Heating control switch and Away mode switch, persistent state entities.
- When there is no heating need, the thermostat is kept on and its setpoint is reduced to the configurable idle setpoint (12 °C default) instead of being turned off; heat pump climate units stay in heat mode, the boiler in auto.
- Three-screen configuration flow, modifiable via options; settings changed via Configure are stored in the integration options.
- Diagnostics support: download settings, flags, last decision and sensor readings from the entry page.
- Computed entities become unavailable while no decision is available or when the control is off; the startup timer is cancelled on unload.
- Unit tests for logic, controller, config flow and translations, run in CI together with Hassfest and HACS validation.
