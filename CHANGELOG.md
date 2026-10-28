# Changelog

## Unreleased

- **Rooms (config sub-entries)**: rooms can now be added to the entry (« Add room » on the integration page, Home Assistant 2025.3+): name, temperature sensor, day target or a room to follow, a boiler-trigger flag (the room may request the boiler), the thermostat-room flag (the boiler thermostat lives in this room) and the splits that heat it (power switches, climate entities, fans). Editing or removing a room reloads the integration. Entries without rooms keep the existing single-zone behaviour. The hybrid multi-room regulation itself lands with the next releases.
- **Manual split override**: in hybrid mode, a split switch changed by someone else (after it had followed the integration's command) leaves its room alone until the next day/night/away transition; the switches must confirm each command, and a command never followed is reported once as unresponsive.
- **Boiler fallback**: in hybrid mode, a split that runs without rising at least 0.2 °C for the fallback delay (2 h default, adjustable) while still below its target makes the boiler take over, like a trigger room; a warning is logged once per stall. The fallback clears when the target is reached again (or the split stops). New setting: boiler fallback delay.
- **Solar self-consumption (soft overheating)**: in hybrid mode, on a sunny surplus the split rooms aim slightly above their day target (bonus 0.5 °C default). The surplus is `production - consumption` from the new optional house consumption sensor (power): the bonus starts at +threshold (500 W default) and stops only at −threshold, so the splits' own consumption cannot flip the mode. Without a consumption sensor, the existing solar production threshold is used. New settings: solar surplus bonus, solar surplus threshold.
- **Hybrid multi-room regulation**: with rooms configured, mild days (outdoor at or above the threshold, or sensor unavailable) are heated by the splits, which regulate each room (start below target − hysteresis, stop at target, setpoint = target + offset); the boiler thermostat rests at the idle setpoint and rises to the new demand setpoint (21 °C default) while a boiler-trigger room is below its target, until they all recover. Cold days, nights and absences force the splits off and the boiler follows the thermostat room's effective target (night/away setpoint, demand raised above it when needed). The boiler stays in `auto` in hybrid mode so it can answer a demand at any time. Safe split start: every switch must confirm ON and every climate entity be available before commands are sent; a room whose sensor is unavailable is left alone. New settings: split setpoint offset, boiler demand setpoint, split fan preset and oscillation (re-applied on each start). The manual source override keeps its meaning (heat pump = splits even in the cold, boiler = never the splits).

- **Breaking (configuration)**: heat pump units are now powered through `switch` entities (`heat_pump_switches`, paired by order with the climate units) instead of the former "power fans" (`heat_pump_fans`). The switch is turned on before any climate command and off when there is no heating need; the climate mode is never used to switch a unit off, and no idle setpoint is pushed to a powered-off unit. Reconfigure the entry to pick the switches (e.g. `switch.salon_clim_gauche`, `switch.salon_clim_droite`).
- Log a warning when a heat pump power switch does not exist or does not confirm ON within the wait delay: the climate unit is then deliberately not driven, and the cause was previously invisible in the logs.
- While the heat pump is the active source, the boiler's setpoint is pushed down to the idle setpoint (12 °C default, adjustable) on every control pass, in addition to the `off` mode set at changeover: the boiler can no longer heat alongside the heat pump if it ignores or loses the `off` mode.
- Fix: schedule times set to `00:00:00` were silently replaced by their default (e.g. the solar start fell back to 11:00).
- Safe split start: the climate state is re-read after the power switch confirms ON (a split climate often reports `unavailable` while unpowered); if the unit does not come back, its commands are skipped for the cycle and retried on the next pass with a warning.

## 0.0.1 - 2026-10-18

- Initial version.
- Heat pump / Boiler changeover according to outdoor temperature, with 0.5 °C hysteresis.
- Day / Night / Away / Solar scheduling with anticipated preheating and heating demand hysteresis.
- Actual solar power production (power sensor, e.g. Solenso) instead of a forecast.
- Heating control switch and Away mode switch, persistent state entities.
- When there is no heating need, the thermostat is kept on and its setpoint is reduced to the configurable idle setpoint (12 °C default) instead of being turned off; heat pump climate units stay in heat mode, the boiler in auto.
- Three-screen configuration flow, modifiable via options; settings changed via Configure are stored in the integration options.
- Diagnostics support: download settings, flags, last decision and sensor readings from the entry page.
- Settings (setpoints, thresholds, rates, schedules) exposed as `number` and `time` entities: tunable from a dashboard, stored in the options, applied without reloading.
- Manual heat source override: `select` entity (Automatic / Heat pump / Boiler), persisted across restarts.
- Frost protection: minimum setpoint (7 °C default, adjustable) and forced heating when the indoor temperature reaches it.
- Absence end date/time: `datetime` entity « Away until » switching away mode on until then, with automatic end and pre-heating.
- Virtual thermostat: `climate.heating_thermostat` (indoor temperature + current setpoint) for the standard thermostat card; adjusting it updates the active period's setpoint, on/off mirrors the heating control switch.
- Example dashboard (`examples/dashboard.yaml`): thermostat card + absence date/time.
- Computed entities become unavailable while no decision is available or when the control is off; the startup timer is cancelled on unload.
- Unit tests for logic, controller, config flow and translations, run in CI together with Hassfest and HACS validation.
