"""Base entity of the heat controller."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .rooms import RoomConfig
from .state import HeatState

if TYPE_CHECKING:
    from .controller import HeatController

_VERSION = json.loads(
    (Path(__file__).parent / "manifest.json").read_text(encoding="utf-8")
)["version"]


class HeatControllerEntity:
    """Common mixin: shared state subscription + single device."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, shared: HeatState, key: str) -> None:
        self._shared = shared
        self._key = key
        self._entry = entry
        self._entry_id = entry.entry_id
        self._device_name = entry.title or "Heat Controller"
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._unsubscribe: list = []

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._device_name,
            manufacturer="pierrick",
            model="Heat Controller",
            sw_version=_VERSION,
        )

    async def async_added_to_hass(self) -> None:
        self._unsubscribe.append(
            self._shared.subscribe(self._on_shared_update)
        )

    async def async_will_remove_from_hass(self) -> None:
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe.clear()

    @callback
    def _on_shared_update(self) -> None:
        self.async_write_ha_state()


class HeatSettingEntity(HeatControllerEntity):
    """Base for the settings exposed on the dashboard (number, time).

    Changing the value applies it live to the controller and stores it in the
    config entry options, so it survives restarts without reloading the entry.
    """

    def __init__(
        self,
        entry: ConfigEntry,
        shared: HeatState,
        controller: HeatController,
        key: str,
    ) -> None:
        super().__init__(entry, shared, key)
        self._controller = controller

    def _current_value(self) -> Any:
        return getattr(self._controller.settings, self._key)

    @callback
    def _apply_setting(self, value: Any) -> None:
        self._controller.update_config({**self._controller.config, self._key: value})
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, self._key: value}
        )
        self.async_write_ha_state()


class HeatRoomEntity(HeatControllerEntity):
    """Base of the per-room entities: attached to the room sub-entry.

    No `device_info`: Home Assistant links entities added with a
    `config_subentry_id` to the room sub-entry itself, and passing
    entry or subentry ids here would collide with the ones the platform
    already provides. Names and states are translated with the room
    name as placeholder.
    """

    def __init__(
        self,
        entry: ConfigEntry,
        shared: HeatState,
        controller: HeatController,
        room: RoomConfig,
        key: str,
    ) -> None:
        super().__init__(entry, shared, f"{room.id}_{key}")
        self._controller = controller
        self._room = room
        self._attr_translation_key = key
        self._attr_translation_placeholders = {"room": room.name}

    @property
    def device_info(self) -> None:
        return None

    @property
    def room_state(self):
        return self._controller.room_states.get(self._room.id)

    @property
    def available(self) -> bool:
        return self._shared.house is not None and self.room_state is not None
