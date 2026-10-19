"""Heat controller: PAC/boiler changeover and day/night/away scheduling."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import CONF_OUTDOOR_TEMP_SENSOR, CONNECTION_KEYS, DOMAIN, PLATFORMS
from .controller import HeatController
from .state import HeatState


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    hass.data.setdefault(DOMAIN, {})
    return True


def _merged_config(entry: ConfigEntry) -> dict[str, Any]:
    """Entry data (installation) completed by the entry options (settings).

    Settings live in the entry options once the entry has been reconfigured;
    the entry data keeps the installation description (and settings from
    entries created before the options split).
    """
    return {**entry.data, **entry.options}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not entry.data.get(CONF_OUTDOOR_TEMP_SENSOR):
        raise ConfigEntryNotReady("Incomplete configuration")

    shared = HeatState()
    controller = HeatController(hass, entry, _merged_config(entry), shared)
    hass.data[DOMAIN][entry.entry_id] = {"shared": shared, "controller": controller}

    await controller.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = hass.data[DOMAIN].pop(entry.entry_id, None)
    if data is not None:
        await data["controller"].async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply new settings live; reload only on installation changes."""
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if data is None:
        return
    controller: HeatController = data["controller"]
    config = _merged_config(entry)
    if any(config.get(key) != controller.config.get(key) for key in CONNECTION_KEYS):
        await hass.config_entries.async_reload(entry.entry_id)
        return
    controller.update_config(config)
