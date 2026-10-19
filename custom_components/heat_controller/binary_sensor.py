"""Binary state sensors for the heat controller.

Reproduce the YAML input_boolean:
heat pump source, preheating, heating demand, plus two calculated sensors
(day period, solar period).
"""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .entity import HeatControllerEntity
from .state import HeatState

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    shared: HeatState = hass.data["heat_controller"][entry.entry_id]["shared"]
    async_add_entities(
        [
            HeatRestorableBinarySensor(entry, shared, "heat_pump_source"),
            HeatRestorableBinarySensor(entry, shared, "preheating"),
            HeatRestorableBinarySensor(entry, shared, "heating_demand"),
            HeatComputedBinarySensor(entry, shared, "day_period"),
            HeatComputedBinarySensor(entry, shared, "solar_period"),
        ]
    )


class HeatRestorableBinarySensor(HeatControllerEntity, BinarySensorEntity, RestoreEntity):
    """Persistent flag: heat_pump_source, preheating, heating_demand."""

    _FLAGS = {
        "heat_pump_source": ("heat_pump_source", "mdi:heat-pump"),
        "preheating": ("preheating", "mdi:clock-fast"),
        "heating_demand": ("demand", "mdi:radiator"),
    }

    def __init__(self, entry: ConfigEntry, shared: HeatState, key: str) -> None:
        flag, icon = self._FLAGS[key]
        super().__init__(entry, shared, key)
        self._flag = flag
        self._attr_icon = icon
        self._attr_translation_key = key

    @property
    def is_on(self) -> bool | None:
        return getattr(self._shared, self._flag)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            setter = getattr(self._shared, f"set_{self._flag}")
            setter(last_state.state == STATE_ON)


class HeatComputedBinarySensor(HeatControllerEntity, BinarySensorEntity):
    """Calculated period at each decision (day, solar)."""

    def __init__(self, entry: ConfigEntry, shared: HeatState, key: str) -> None:
        super().__init__(entry, shared, key)
        self._attr_translation_key = key
        self._attr_icon = "mdi:weather-sunny" if key == "solar_period" else "mdi:weather-day-party-cloudy"

    @property
    def is_on(self) -> bool | None:
        decision = self._shared.decision
        if decision is None:
            return None
        if self._key == "day_period":
            return decision.in_day_period
        return decision.in_solar_period