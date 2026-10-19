"""Base entity of the heat controller."""

from __future__ import annotations

import json
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .state import HeatState

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
