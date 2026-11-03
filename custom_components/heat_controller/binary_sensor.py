"""Binary state sensors for the heat controller.

Calculated sensors only (day period, solar period, frost protection,
and the boiler demand in multi-room mode). The source, demand and
pre-heating flags are exposed through sensors instead: see
`sensor.heating_heat_source` and `sensor.heating_heating_state`.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .controller import HeatController
from .entity import HeatControllerEntity
from .state import HeatState


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    shared: HeatState = hass.data["heat_controller"][entry.entry_id]["shared"]
    controller: HeatController = hass.data["heat_controller"][entry.entry_id]["controller"]
    entities: list = [
        HeatComputedBinarySensor(entry, shared, "day_period"),
        HeatComputedBinarySensor(entry, shared, "solar_period"),
        HeatComputedBinarySensor(entry, shared, "frost_protection"),
    ]
    if controller.rooms:
        entities.append(HeatBoilerDemandSensor(entry, shared))
    async_add_entities(entities)


class HeatComputedBinarySensor(HeatControllerEntity, BinarySensorEntity):
    """Calculated state at each decision (day, solar, frost)."""

    _ICONS = {
        "day_period": "mdi:weather-day-party-cloudy",
        "solar_period": "mdi:weather-sunny",
        "frost_protection": "mdi:snowflake-alert",
    }

    def __init__(self, entry: ConfigEntry, shared: HeatState, key: str) -> None:
        super().__init__(entry, shared, key)
        self._attr_translation_key = key
        self._attr_icon = self._ICONS[key]

    @property
    def available(self) -> bool:
        return self._shared.decision is not None

    @property
    def is_on(self) -> bool | None:
        decision = self._shared.decision
        if decision is None:
            return None
        if self._key == "day_period":
            return decision.in_day_period
        if self._key == "solar_period":
            return decision.in_solar_period
        return decision.frost_protection


class HeatBoilerDemandSensor(HeatControllerEntity, BinarySensorEntity):
    """Boiler demand in multi-room mode (trigger room or stalled split)."""

    _attr_translation_key = "boiler_demand"
    _attr_icon = "mdi:radiator"

    def __init__(self, entry: ConfigEntry, shared: HeatState) -> None:
        super().__init__(entry, shared, "boiler_demand")

    @property
    def available(self) -> bool:
        return self._shared.house is not None

    @property
    def is_on(self) -> bool | None:
        house = self._shared.house
        return house.boiler_demand if house else None
