"""Entité de base du contrôleur de chauffage."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .state import HeatState


class HeatControllerEntity:
    """Mixin commune : abonnement à l'état partagé + appareil unique."""

    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry, shared: HeatState, key: str) -> None:
        self._shared = shared
        self._key = key
        self._entry_id = entry.entry_id
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._unsubscribe: list = []

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry_id)}, name="Chauffage")

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
        self.async_write_state()
