"""Heat controller sensors: target setpoint, preheating duration, active source."""

from __future__ import annotations

from typing import Optional

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import HeatControllerEntity
from .state import HeatState


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    shared: HeatState = hass.data["heat_controller"][entry.entry_id]["shared"]
    async_add_entities(
        [
            HeatTargetSetpointSensor(entry, shared),
            HeatPreheatingDurationSensor(entry, shared),
            HeatSourceSensor(entry, shared),
        ]
    )


class HeatTargetSetpointSensor(HeatControllerEntity, SensorEntity):
    """Instantaneous target setpoint (day, night, away, solar, preheating)."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "target_setpoint"
    _attr_icon = "mdi:thermometer"

    def __init__(self, entry: ConfigEntry, shared: HeatState) -> None:
        super().__init__(entry, shared, "target_setpoint")

    @property
    def available(self) -> bool:
        return self._shared.decision is not None

    @property
    def native_value(self) -> Optional[float]:
        decision = self._shared.decision
        return decision.target_setpoint if decision else None


class HeatPreheatingDurationSensor(HeatControllerEntity, SensorEntity):
    """Estimated temperature rise duration (anticipation), in minutes."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "min"
    _attr_translation_key = "preheating_duration"
    _attr_icon = "mdi:timer-outline"

    def __init__(self, entry: ConfigEntry, shared: HeatState) -> None:
        super().__init__(entry, shared, "preheating_duration")

    @property
    def available(self) -> bool:
        return self._shared.decision is not None

    @property
    def native_value(self) -> Optional[int]:
        decision = self._shared.decision
        return decision.preheating_minutes if decision else None


class HeatSourceSensor(HeatControllerEntity, SensorEntity):
    """Active heat source: heat pump or boiler."""

    _attr_translation_key = "source"
    _attr_icon = "mdi:fire"

    def __init__(self, entry: ConfigEntry, shared: HeatState) -> None:
        super().__init__(entry, shared, "source")

    @property
    def native_value(self) -> Optional[str]:
        return "Heat pump" if self._shared.heat_pump_source else "Boiler"
