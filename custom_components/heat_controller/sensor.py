"""Capteurs du contrôleur : consigne cible, durée de préchauffe, source active."""

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
            HeatConsigneCibleSensor(entry, shared),
            HeatDureePrechauffeSensor(entry, shared),
            HeatSourceSensor(entry, shared),
        ]
    )


class HeatConsigneCibleSensor(HeatControllerEntity, SensorEntity):
    """Consigne visée en ce moment (jour, nuit, absence, solaire, préchauffe)."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "consigne_cible"
    _attr_icon = "mdi:thermometer"

    def __init__(self, entry: ConfigEntry, shared: HeatState) -> None:
        super().__init__(entry, shared, "consigne_cible")

    @property
    def native_value(self) -> Optional[float]:
        decision = self._shared.decision
        return decision.consigne_cible if decision else None


class HeatDureePrechauffeSensor(HeatControllerEntity, SensorEntity):
    """Durée de montée en température estimée (anticipation), en minutes."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "min"
    _attr_translation_key = "duree_prechauffe"
    _attr_icon = "mdi:timer-outline"

    def __init__(self, entry: ConfigEntry, shared: HeatState) -> None:
        super().__init__(entry, shared, "duree_prechauffe")

    @property
    def native_value(self) -> Optional[int]:
        decision = self._shared.decision
        return decision.minutes_necessaires if decision else None


class HeatSourceSensor(HeatControllerEntity, SensorEntity):
    """Source de chaleur active : PAC ou chaudière."""

    _attr_translation_key = "source"
    _attr_icon = "mdi:fire"

    def __init__(self, entry: ConfigEntry, shared: HeatState) -> None:
        super().__init__(entry, shared, "source")

    @property
    def native_value(self) -> Optional[str]:
        return "PAC" if self._shared.source_pac else "Chaudière"
