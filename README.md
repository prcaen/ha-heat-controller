# Heat Controller for Home Assistant

Custom integration for a **hybrid multi-room heating**: the heat pump
splits regulate each room on mild days, the boiler rests and answers the
demands of the rooms it is the only one to heat, and cold days, nights
and absences switch to the boiler following the thermostat room's target.
The solar surplus (production − consumption, Solenso power sensors)
drives a soft overheating of the split rooms.

## What the integration does

- **Hybrid regulation** (see « Rooms and hybrid regulation » below):
  mild days let the splits heat the rooms, the boiler rests at the idle
  setpoint and rises to the demand setpoint while a trigger room is
  below its target; cold days, nights and absences force the splits off
  and the boiler follows the thermostat room.
- **Solar surplus**: on a production surplus (production − consumption
  above the surplus threshold, or the production threshold without a
  consumption sensor) the split rooms aim slightly above their target,
  with a ±threshold hysteresis band so the splits' own consumption
  cannot flip the mode.
- **Safety nets**: a running split that stalls asks the boiler, a split
  changed by hand leaves its room alone, non-forced state changes wait
  for the minimum run/stop time, and commands are sent only once the
  switches confirm ON and the climate entities are available.
- **Learning**: each room's heating speed (°C/h) is learned from the
  observed sessions and persisted; the pre-heating before the day period
  and before a planned return is (target − current) / rate, adjusted
  with the weather forecast when a weather entity is configured.
- **Notifications**: an optional notify service receives the away
  start/end, the unresponsive splits and the boiler takeovers.
- **Settings on the dashboard**: every setpoint, threshold, delay and
  schedule is also exposed as a `number`, `time`, `select` or `switch`
  entity — adjust them from a dashboard; they are applied live and
  stored in the integration options.
- **Manual heat source override**: a `select` entity forces the splits
  (heat pump) or the boiler (Automatic = normal behaviour); the choice
  survives restarts.
- **Frost protection**: the target of a room never goes below the frost
  setpoint (7 °C default, adjustable) and heating is forced as soon as a
  room reaches it — whatever the schedule or away mode says.
- **Absence with a return date**: set the « Away until » `datetime` entity
  to turn away mode on until that date and time; the absence ends by
  itself, with pre-heating before your return (the daily return time
  remains as a fallback when no date is set).
- **Thermostat card**: the `climate.heating_thermostat` entity shows the
  temperature of the thermostat room and the current target, ready for
  the standard thermostat card. During the day its +/− buttons change
  the thermostat room's day target (the room sub-entry follows); at
  night and in away mode they change the night/away setpoints. Its
  on/off button mirrors the heating control switch.

## Created entities ( « Heating » device)

| Entity | Type | YAML equivalent |
|---|---|---|
| `switch.heating_heating_control` | switch | heating on/off |
| `switch.heating_away_mode` | switch | away mode |
| `binary_sensor.heating_day_period` | binary_sensor | (calculated) |
| `binary_sensor.heating_solar_period` | binary_sensor | (calculated) |
| `sensor.heating_heating_state` | sensor | `idle` / `heating` / `preheating` (Inactif / Chauffage / Pré-chauffage) |
| `sensor.heating_target_setpoint` | sensor | (calculated) |
| `sensor.heating_pre_heat_duration` | sensor | (calculated, minutes) |
| `sensor.heating_heat_source` | sensor | `heat_pump` / `boiler` (PAC / Chaudière) |
| `binary_sensor.heating_frost_protection` | binary_sensor | (calculated) |
| `select.heating_heat_source_override` | select | Automatic / Heat pump / Boiler |
| `datetime.heating_away_until` | datetime | absence end (date + time) |
| `climate.heating_thermostat` | climate | thermostat card (room temp + setpoint) |
| `number.heating_*` (15 entities) | number | the numeric settings |
| `time.heating_*` (15 entities) | time | weekly day period start/end (one per weekday), away return time |
| `select.heating_split_fan_preset` | select | split fan preset (global) |
| `switch.heating_split_fan_oscillating` | switch | split fan oscillation |
| `sensor.heating_mode` | sensor | `day` / `night` / `away`, with the solar surplus, the boiler setpoint and the splits state as attributes |
| `binary_sensor.heating_boiler_demand` | binary_sensor | a trigger room (or a stalled split) is asking the boiler |
| `sensor.<room>_heat_source` | sensor | per room: `idle` / `splits` / `boiler`, with the room's target, pre-heating and override state |
| `sensor.<room>_learned_heating_rate` | sensor | per room: learned heating speed (°C/h) |
| `sensor.<room>_next_pre_heating` | sensor | per room: when the next pre-heating starts |
| `select.<room>_fan_preset` | select | per room: split fan preset (`inherit` = the global setting) |

Flags (active, away mode, forced source, absence end) persist across
restarts; the source, demand and pre-heating states are recomputed at
every pass and exposed through the sensors above.

> Exact identifiers are generated by Home Assistant from the names
> (device "Heating" + entity name) ; check them in
> the development tools after installation.

## Installation

### HACS

HACS → menu ⋮ → **Custom repositories** → `https://github.com/<your-account>/ha-heat-controller`,
category **Integration** → install **Heat Controller** → restart
Home Assistant.

### Manual

Copy the `custom_components/heat_controller` folder to
 `/config/custom_components/`, then restart Home Assistant.

## Configuration

Settings → Devices and services → **Add integration** →
**Heat Controller**. Three screens :

1. **Sensors** : outdoor temperature, instantaneous solar power
   production (power sensor), and the optional house consumption power
   sensor (for the solar surplus). If the Solenso integration is
   installed, the central's power sensor is pre-selected.
2. **Boiler and accessory fans** : the boiler's climate (Netatmo) and
   the accessory fans (on with the splits, off with the boiler mode).
   The splits themselves are configured per room (« Add room »).
3. **Settings** : night/away/**idle**/**frost protection** setpoints,
   hysteresis, outdoor threshold, production threshold, the weekly
   schedule (day period start/end per weekday) and the away return time,
   and the hybrid settings (split offset, boiler demand setpoint,
   surplus threshold and bonus, fallback delay, minimum run/stop time,
   pre-heating durations, fan preset and oscillation, optional weather
   entity and notification service).

All is then modifiable via **Configure** on the entry, or directly from a
dashboard through the `number`/`time`/`select`/`switch` entities; settings
are stored in the integration options and applied without reloading.
Diagnostics (settings, rooms, flags, last decisions and sensor readings)
can be downloaded from the entry page.

## Rooms and hybrid regulation

Rooms are added to the entry (« Add room » on the integration page,
editable afterwards — Home Assistant 2025.3 or newer). A room has:
a name, its temperature sensor, a day target (or the target of another
room, e.g. the bathrooms following the living room), the
**thermostat room** flag (the boiler thermostat lives in this
room — usually the living room) and the splits that heat it: power
switches (the only way to turn a split off), climate entities and fan
entities. A room can also override the **fan preset** for its own
fans (form field, or the `select.<room>_fan_preset` entity; `inherit`
keeps the global `split fan preset`), and opt in to **pre-heat with
the heat pump (the splits)**: during a pre-heating, its splits heat
the room via the PAC instead of the boiler. At least one room is required;
the room flagged as thermostat
room drives the boiler and the thermostat card (the first room if
none is flagged).

A room **without** splits can automatically request the boiler on mild
days (the boiler is its only heat source); a room **with** splits
cannot — it falls back to the boiler only through the automatic stall
detection.

The hybrid regulation:

- **Mild day** (outdoor at or above the threshold, or sensor
  unavailable): the splits regulate each room — start below
  target − hysteresis, stop at the target, setpoint = target +
  offset. The boiler rests at the idle setpoint and rises to the
  demand setpoint while a trigger room is below its target, until all
  of them recover.
- **Solar surplus**: on a production surplus (production −
  consumption above the threshold, or above the production threshold
  without a consumption sensor) the split rooms aim slightly above
  their target, with a ±threshold hysteresis band so the splits' own
  consumption cannot flip the mode.
- **Cold day, night or absence**: the splits are off and the boiler
  follows the thermostat room's effective target (raised to the
  demand setpoint while a room still asks for heat). The **pre-heating
  is done by the boiler by default** — before the day period or before
  a planned return, the splits never run unless the room opted in
  (**pre-heat with the heat pump**): its splits then pre-heat it via
  the PAC (the outdoor threshold still applies), and when the
  thermostat room pre-heats that way, the boiler rests at the idle
  setpoint (it still answers the trigger rooms' demand).
- **Safety nets**: a running split that does not progress by 0.2 °C
  for the fallback delay (2 h default) asks the boiler; a split
  changed by hand leaves its room alone until the next mode
  transition; non-forced state changes wait for the minimum run/stop
  time; commands are sent only once the switches confirm ON and the
  climate entities are available.
- **Learning**: each room's heating speed is learned from the
  observed sessions and persisted; the pre-heating duration is
  (target − current) / rate, adjusted with the weather forecast when
  a weather entity is configured.
- **Notifications**: an optional notify service receives the away
  start/end, the unresponsive splits and the boiler takeovers.

Entries without rooms keep the original single-zone behaviour.

### Dashboard

`examples/dashboard.yaml` provides a ready-to-paste card column: the
integration's virtual thermostat (indoor temperature + setpoint), an
« Absence » card with the away mode switch and the « Away until » date/time,
the heat source override and the main settings.

```yaml
type: vertical-stack
cards:
  - type: thermostat
    entity: climate.heating_thermostat
    name: Heating
  - type: entities
    title: Absence
    entities:
      - switch.heating_away_mode
      - datetime.heating_away_until
  # ... heat source, frost status and main setpoints: see the file
```

> Tapping the « Away until » row opens a date/time picker; away mode then
> turns on by itself until that moment.

### YAML configuration comparison

| YAML | Integration |
|---|---|
| `sensor.jardin_station_meteo_externe_temperature` | « Outdoor temperature » sensor |
| `sensor.salon_station_meteo_interne_temperature` | « Indoor temperature » sensor |
| `sensor.power_production_now` (Forecast.Solar) | « Solar power production » sensor: **central's Solenso power** |
| `climate.netatmo_smart_thermostat` | Boiler climate |
| `climate.salon_clim_gauche`, `climate.salon_clim_droite` | Heat pump climate units |
| `switch.salon_clim_gauche`, `switch.salon_clim_droite` | Power switches (same order) |
| `fan.climate_series_accessory`, `fan.climate_series_accessory_2` | Accessory fans |
| `input_number.chauffage_*` | Settings screen or `number` entities |
| `input_datetime.chauffage_heure_*` | Settings screen or `time` entities |
| `input_boolean.chauffage_*` | Switches and binary sensors above |

### Migration from YAML

1. Install and configure the integration (leave control **inactive** to start).
2. Disable the two YAML automations.
3. Enable the « Heating control » switch.
4. The `input_number` / `input_datetime` / `input_boolean` are no longer
   used by the control ; do not delete their entries until you have
   verified that nothing else references them (dashboards, other
   automations).

## Notes

- Heartbeat every minute + sensor listening: decisions are idempotent and
  protected by hysteresis.
- The boiler's HVAC mode is never touched: many boiler thermostats
  (Netatmo) only support off/heat, and a mode set by hand (e.g. off for
  the summer) is respected. Only the setpoint is driven.
- The splits are cut from their power switches only: their climate mode
  is never used to switch them off, and it is set to `heat` right after
  the switch is confirmed on.
- Settings are applied live when changed from the settings screens or from
  the dashboard entities, without reloading the integration.
- In case of a Solenso data feed cut-off in full sun, the sensor becomes
  unavailable and is treated as « no production ».

## Development

```bash
# Unit tests (logic, translations; the controller and config flow tests
# are skipped without homeassistant installed)
python3 -m unittest discover -s tests

# Full run, including controller, config flow and smoke test, with a real
# homeassistant (Python 3.13+)
uv venv /tmp/hacheck --python 3.13
uv pip install --python /tmp/hacheck/bin/python homeassistant
/tmp/hacheck/bin/python -m unittest discover -s tests
/tmp/hacheck/bin/python tests/smoke_test_ha.py
```