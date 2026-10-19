"""Numeric settings exposed as entities, tunable from the dashboard."""

from __future__ import annotations

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SETTING_NUMBERS
from .controller import HeatController
from .entity import HeatSettingEntity
from .state import HeatState


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data = hass.data["heat_controller"][entry.entry_id]
    shared: HeatState = data["shared"]
    controller: HeatController = data["controller"]
    async_add_entities(
        HeatSettingNumber(entry, shared, controller, key) for key in SETTING_NUMBERS
    )


class HeatSettingNumber(HeatSettingEntity, NumberEntity):
    """A numeric setting, applied live and stored in the entry options."""

    def __init__(
        self,
        entry: ConfigEntry,
        shared: HeatState,
        controller: HeatController,
        key: str,
    ) -> None:
        super().__init__(entry, shared, controller, key)
        spec = SETTING_NUMBERS[key]
        self._attr_native_min_value = spec["min"]
        self._attr_native_max_value = spec["max"]
        self._attr_native_step = spec["step"]
        self._attr_native_unit_of_measurement = spec["unit"]
        self._attr_translation_key = key
        if device_class := spec.get("device_class"):
            self._attr_device_class = NumberDeviceClass(device_class)
        if spec.get("mode") == "box":
            self._attr_mode = NumberMode.BOX

    @property
    def native_value(self) -> float:
        return float(self._current_value())

    async def async_set_native_value(self, value: float) -> None:
        self._apply_setting(round(float(value), 3))
