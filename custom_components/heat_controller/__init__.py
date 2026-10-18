"""Contrôleur de chauffage : bascule PAC/chaudière et programmation jour/nuit/absence."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import CONF_OUTDOOR_TEMP_SENSOR, DOMAIN, PLATFORMS
from .controller import HeatController
from .state import HeatState


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not entry.data.get(CONF_OUTDOOR_TEMP_SENSOR):
        raise ConfigEntryNotReady("Configuration incomplète")

    shared = HeatState()
    controller = HeatController(hass, entry, entry.data, shared)
    hass.data[DOMAIN][entry.entry_id] = {"shared": shared, "controller": controller}

    await hass.config_entries.async_forward_platform_setups(entry, PLATFORMS)
    await controller.async_start()

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = hass.data[DOMAIN].pop(entry.entry_id, None)
    if data is not None:
        await data["controller"].async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
