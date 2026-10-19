"""Time settings exposed as entities, tunable from the dashboard."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SETTING_TIMES
from .controller import HeatController
from .entity import HeatSettingEntity
from .logic import time_of_day
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
        HeatSettingTime(entry, shared, controller, key) for key in SETTING_TIMES
    )


class HeatSettingTime(HeatSettingEntity, TimeEntity):
    """A time setting: day/night start, away return, solar window."""

    def __init__(
        self,
        entry: ConfigEntry,
        shared: HeatState,
        controller: HeatController,
        key: str,
    ) -> None:
        super().__init__(entry, shared, controller, key)
        self._attr_translation_key = key

    @property
    def native_value(self) -> time:
        return time_of_day(self._current_value())

    async def async_set_value(self, value: time) -> None:
        self._apply_setting(value.strftime("%H:%M:%S"))
