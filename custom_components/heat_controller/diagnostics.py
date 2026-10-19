"""Diagnostics support for Heat Controller.

Downloadable from Settings > Devices and services > Heat Controller:
settings, flags, last decision and watched sensor readings.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .controller import HeatController
from .state import HeatState


def _serialize(value: Any) -> Any:
    """Convert dataclass contents into JSON-serializable values."""
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    diagnostics: dict[str, Any] = {
        "entry_data": dict(entry.data),
        "entry_options": dict(entry.options),
    }

    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if data is None:
        return diagnostics

    shared: HeatState = data["shared"]
    controller: HeatController = data["controller"]

    sensors: dict[str, Any] = {}
    for entity_id in (
        controller.outdoor_sensor,
        controller.indoor_sensor,
        controller.production_sensor,
    ):
        state = hass.states.get(entity_id)
        sensors[entity_id] = state.state if state is not None else None

    diagnostics.update(
        {
            "flags": {
                "active": shared.active,
                "away_mode": shared.away_mode,
                "heat_pump_source": shared.heat_pump_source,
                "preheating": shared.preheating,
                "demand": shared.demand,
            },
            "settings": _serialize(asdict(controller.settings)),
            "decision": _serialize(asdict(shared.decision)) if shared.decision else None,
            "sensors": sensors,
        }
    )
    return diagnostics
