"""Manual heat source override: Automatic / Heat pump / Boiler."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import SETTING_SELECTS
from .controller import HeatController
from .entity import HeatControllerEntity, HeatSettingEntity
from .logic import SOURCE_OPTIONS
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
        [HeatSourceOverrideSelect(entry, shared, controller)]
        + [
            HeatSettingSelect(entry, shared, controller, key)
            for key in SETTING_SELECTS
        ]
    )


class HeatSourceOverrideSelect(HeatControllerEntity, SelectEntity, RestoreEntity):
    """Force the heat pump or the boiler (Automatic = normal changeover).

    The override survives restarts and ignores the outdoor threshold until it
    is set back to Automatic.
    """

    _attr_options = list(SOURCE_OPTIONS)
    _attr_translation_key = "heat_source_override"
    _attr_icon = "mdi:swap-horizontal-bold"

    def __init__(
        self, entry: ConfigEntry, shared: HeatState, controller: HeatController
    ) -> None:
        super().__init__(entry, shared, "heat_source_override")
        self._controller = controller

    @property
    def current_option(self) -> str:
        return self._shared.forced_source

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in SOURCE_OPTIONS:
            self._shared.set_forced_source(last_state.state)

    async def async_select_option(self, option: str) -> None:
        self._shared.set_forced_source(option)
        self._controller.request_update()
        self.async_write_ha_state()


class HeatSettingSelect(HeatSettingEntity, SelectEntity):
    """A string setting (e.g. the split fan preset), applied live."""

    def __init__(
        self,
        entry: ConfigEntry,
        shared: HeatState,
        controller: HeatController,
        key: str,
    ) -> None:
        super().__init__(entry, shared, controller, key)
        self._attr_options = list(SETTING_SELECTS[key]["options"])
        self._attr_translation_key = key

    @property
    def current_option(self) -> str:
        return str(self._current_value())

    async def async_select_option(self, option: str) -> None:
        self._apply_setting(option)
