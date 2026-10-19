"""Heat controller configuration and options flow."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import UnitOfPower, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TimeSelector,
)

from .const import (
    CONF_ACCESSORY_FANS,
    CONF_AWAY_SETPOINT,
    CONF_BOILER_CLIMATE,
    CONF_BOILER_RATE,
    CONF_DAY_SETPOINT,
    CONF_DAY_START,
    CONF_HEAT_PUMP_CLIMATES,
    CONF_HEAT_PUMP_FANS,
    CONF_HEAT_PUMP_RATE,
    CONF_HYSTERESIS,
    CONF_INDOOR_TEMP_SENSOR,
    CONF_NIGHT_SETPOINT,
    CONF_NIGHT_START,
    CONF_OUTDOOR_TEMP_SENSOR,
    CONF_OUTDOOR_THRESHOLD,
    CONF_PRODUCTION_SENSOR,
    CONF_PRODUCTION_THRESHOLD,
    CONF_RETURN_TIME,
    CONF_SAFETY_MARGIN,
    CONF_SOLAR_END,
    CONF_SOLAR_SETPOINT,
    CONF_SOLAR_START,
    DOMAIN,
    SETTING_DEFAULTS,
)

TEMPERATURE_SELECTOR = NumberSelector(
    NumberSelectorConfig(
        min=5, max=30, step=0.5, unit_of_measurement=UnitOfTemperature.CELSIUS
    )
)


def _solenso_power_sensor(hass: HomeAssistant) -> str | None:
    """Solenso central power sensor, if the integration is present."""
    solenso_entries = {
        entry.entry_id for entry in hass.config_entries.async_entries("solenso")
    }
    if not solenso_entries:
        return None
    registry = er.async_get(hass)
    candidates = [
        entity
        for entity in registry.entities.values()
        if entity.config_entry_id in solenso_entries
        and entity.domain == "sensor"
        and entity.device_class == "power"
    ]
    # The central sensor (unique_id "<id>_power") takes precedence over the
    # micro-inverters ("micro_<sn>_power").
    station = [e for e in candidates if not e.unique_id.startswith("micro_")]
    chosen = (station or candidates)[0] if (station or candidates) else None
    return chosen.entity_id if chosen else None


def _sensors_schema(data: dict[str, Any], suggested_production: str | None) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_OUTDOOR_TEMP_SENSOR,
                default=data.get(CONF_OUTDOOR_TEMP_SENSOR),
            ): EntitySelector(
                EntitySelectorConfig(domain="sensor", device_class="temperature")
            ),
            vol.Required(
                CONF_INDOOR_TEMP_SENSOR,
                default=data.get(CONF_INDOOR_TEMP_SENSOR),
            ): EntitySelector(
                EntitySelectorConfig(domain="sensor", device_class="temperature")
            ),
            vol.Required(
                CONF_PRODUCTION_SENSOR,
                default=data.get(CONF_PRODUCTION_SENSOR, suggested_production),
            ): EntitySelector(
                EntitySelectorConfig(domain="sensor", device_class="power")
            ),
        }
    )


def _devices_schema(data: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_BOILER_CLIMATE, default=data.get(CONF_BOILER_CLIMATE)
            ): EntitySelector(EntitySelectorConfig(domain="climate")),
            vol.Required(
                CONF_HEAT_PUMP_CLIMATES, default=data.get(CONF_HEAT_PUMP_CLIMATES, [])
            ): EntitySelector(
                EntitySelectorConfig(domain="climate", multiple=True)
            ),
            vol.Optional(
                CONF_HEAT_PUMP_FANS, default=data.get(CONF_HEAT_PUMP_FANS, [])
            ): EntitySelector(EntitySelectorConfig(domain="fan", multiple=True)),
            vol.Optional(
                CONF_ACCESSORY_FANS, default=data.get(CONF_ACCESSORY_FANS, [])
            ): EntitySelector(EntitySelectorConfig(domain="fan", multiple=True)),
        }
    )


def _settings_schema(data: dict[str, Any]) -> vol.Schema:
    def setting(key: str) -> Any:
        return data.get(key, SETTING_DEFAULTS[key])

    return vol.Schema(
        {
            vol.Required(
                CONF_DAY_SETPOINT, default=setting(CONF_DAY_SETPOINT)
            ): TEMPERATURE_SELECTOR,
            vol.Required(
                CONF_NIGHT_SETPOINT, default=setting(CONF_NIGHT_SETPOINT)
            ): TEMPERATURE_SELECTOR,
            vol.Required(
                CONF_AWAY_SETPOINT, default=setting(CONF_AWAY_SETPOINT)
            ): TEMPERATURE_SELECTOR,
            vol.Required(
                CONF_SOLAR_SETPOINT, default=setting(CONF_SOLAR_SETPOINT)
            ): TEMPERATURE_SELECTOR,
            vol.Required(
                CONF_HYSTERESIS,
                default=setting(CONF_HYSTERESIS),
            ): NumberSelector(
                NumberSelectorConfig(min=0, max=5, step=0.1, unit_of_measurement="°C")
            ),
            vol.Required(
                CONF_OUTDOOR_THRESHOLD, default=setting(CONF_OUTDOOR_THRESHOLD)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=-5, max=10, step=0.5, unit_of_measurement=UnitOfTemperature.CELSIUS
                )
            ),
            vol.Required(
                CONF_PRODUCTION_THRESHOLD, default=setting(CONF_PRODUCTION_THRESHOLD)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0, max=20000, step=50, mode=NumberSelectorMode.BOX, unit_of_measurement=UnitOfPower.WATT
                )
            ),
            vol.Required(
                CONF_HEAT_PUMP_RATE, default=setting(CONF_HEAT_PUMP_RATE)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0, max=10, step=0.1, unit_of_measurement="°C/h"
                )
            ),
            vol.Required(
                CONF_BOILER_RATE, default=setting(CONF_BOILER_RATE)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0, max=10, step=0.1, unit_of_measurement="°C/h"
                )
            ),
            vol.Required(
                CONF_SAFETY_MARGIN, default=setting(CONF_SAFETY_MARGIN)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0, max=240, step=5, mode=NumberSelectorMode.BOX, unit_of_measurement="min"
                )
            ),
            vol.Required(
                CONF_DAY_START, default=setting(CONF_DAY_START)
            ): TimeSelector(),
            vol.Required(
                CONF_NIGHT_START, default=setting(CONF_NIGHT_START)
            ): TimeSelector(),
            vol.Required(
                CONF_RETURN_TIME, default=setting(CONF_RETURN_TIME)
            ): TimeSelector(),
            vol.Required(
                CONF_SOLAR_START, default=setting(CONF_SOLAR_START)
            ): TimeSelector(),
            vol.Required(
                CONF_SOLAR_END, default=setting(CONF_SOLAR_END)
            ): TimeSelector(),
        }
    )


class HeatControllerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Initial configuration: sensors, devices, settings."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry,
    ) -> HeatControllerOptionsFlow:
        return HeatControllerOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_devices()
        return self.async_show_form(
            step_id="user",
            data_schema=_sensors_schema(
                self._data, _solenso_power_sensor(self.hass)
            ),
            errors=errors,
        )

    async def async_step_devices(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            climates = user_input.get(CONF_HEAT_PUMP_CLIMATES) or []
            fans = user_input.get(CONF_HEAT_PUMP_FANS) or []
            if fans and len(fans) != len(climates):
                errors["base"] = "fan_count_mismatch"
            else:
                self._data.update(user_input)
                return await self.async_step_settings()
        return self.async_show_form(
            step_id="devices",
            data_schema=_devices_schema(self._data),
            errors=errors,
        )

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(title="Heating", data=self._data)
        return self.async_show_form(
            step_id="settings",
            data_schema=_settings_schema(self._data),
        )


class HeatControllerOptionsFlow(OptionsFlow):
    """Edit the same screens as the initial configuration."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        self._data: dict[str, Any] = dict(self.config_entry.data)
        return await self.async_step_user(user_input)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_devices()
        return self.async_show_form(
            step_id="user",
            data_schema=_sensors_schema(self._data, None),
            errors=errors,
        )

    async def async_step_devices(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            climates = user_input.get(CONF_HEAT_PUMP_CLIMATES) or []
            fans = user_input.get(CONF_HEAT_PUMP_FANS) or []
            if fans and len(fans) != len(climates):
                errors["base"] = "fan_count_mismatch"
            else:
                self._data.update(user_input)
                return await self.async_step_settings()
        return self.async_show_form(
            step_id="devices",
            data_schema=_devices_schema(self._data),
            errors=errors,
        )

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            self.hass.config_entries.async_update_entry(
                self.config_entry, data=self._data
            )
            return self.async_create_entry(title="", data={})
        return self.async_show_form(
            step_id="settings",
            data_schema=_settings_schema(self._data),
        )