"""End of absence (date + time), manageable from the dashboard.

Setting a date and time turns away mode on until then: the controller ends
the absence (and preheats if needed) automatically at that moment.
"""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .controller import HeatController
from .entity import HeatControllerEntity
from .state import HeatState


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data = hass.data["heat_controller"][entry.entry_id]
    shared: HeatState = data["shared"]
    controller: HeatController = data["controller"]
    async_add_entities([HeatAwayUntilEntity(entry, shared, controller)])


class HeatAwayUntilEntity(HeatControllerEntity, DateTimeEntity, RestoreEntity):
    """When the absence ends; setting it turns away mode on."""

    _attr_translation_key = "away_until"
    _attr_icon = "mdi:calendar-clock"

    def __init__(
        self, entry: ConfigEntry, shared: HeatState, controller: HeatController
    ) -> None:
        super().__init__(entry, shared, "away_until")
        self._controller = controller

    @property
    def native_value(self) -> datetime | None:
        return self._shared.away_until

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is None or last_state.state in (
            STATE_UNAVAILABLE,
            STATE_UNKNOWN,
        ):
            return
        if value := dt_util.parse_datetime(last_state.state):
            self._shared.set_away_until(dt_util.as_local(value))

    async def async_set_value(self, value: datetime) -> None:
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
        self._shared.set_away_until(dt_util.as_local(value))
        self._shared.set_away_mode(True)
        self._controller.request_update()
        self.async_write_ha_state()
