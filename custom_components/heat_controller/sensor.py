"""Heat controller sensors: target setpoint, preheating duration, active source.

In multi-room mode also: the global mode (with the solar surplus as
attributes) and, per room, the heat source, the learned heating rate
and the next pre-heating.
"""

from __future__ import annotations

from datetime import datetime
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

from .const import DOMAIN
from .controller import HeatController
from .entity import HeatControllerEntity
from .rooms import RoomConfig
from .state import HeatState


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    shared: HeatState = data["shared"]
    controller: HeatController = data["controller"]
    entities: list[SensorEntity] = [
        HeatTargetSetpointSensor(entry, shared),
        HeatPreheatingDurationSensor(entry, shared),
        HeatSourceSensor(entry, shared),
    ]
    if controller.rooms:
        entities.append(HeatModeSensor(entry, shared, controller))
        async_add_entities(entities)
        for room in controller.rooms:
            async_add_entities(
                [
                    HeatRoomSourceSensor(entry, shared, controller, room),
                    HeatRoomRateSensor(entry, shared, controller, room),
                    HeatRoomNextPreheatSensor(entry, shared, controller, room),
                ],
                config_subentry_id=room.id,
            )
        return
    async_add_entities(entities)


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
    """Active heat source: splits (heat pump) or boiler mode."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["heat_pump", "boiler"]
    _attr_translation_key = "source"
    _attr_icon = "mdi:fire"

    def __init__(self, entry: ConfigEntry, shared: HeatState) -> None:
        super().__init__(entry, shared, "source")

    @property
    def native_value(self) -> Optional[str]:
        return "heat_pump" if self._shared.heat_pump_source else "boiler"


class HeatModeSensor(HeatControllerEntity, SensorEntity):
    """Global mode in multi-room mode: day, night or away."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["day", "night", "away"]
    _attr_translation_key = "mode"
    _attr_icon = "mdi:sun-clock"

    def __init__(self, entry: ConfigEntry, shared: HeatState, controller: HeatController) -> None:
        super().__init__(entry, shared, "mode")
        self._controller = controller

    @property
    def available(self) -> bool:
        return self._shared.house is not None

    @property
    def native_value(self) -> Optional[str]:
        house = self._shared.house
        return house.mode if house else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        house = self._shared.house
        if house is None:
            return {}
        surplus = (
            house.production - house.consumption
            if house.production is not None and house.consumption is not None
            else None
        )
        return {
            "splits_allowed": house.splits_allowed,
            "boiler_demand": house.boiler_demand,
            "boiler_setpoint": house.boiler_setpoint,
            "thermostat_room_target": house.thermostat_room_target,
            "solar_bonus": house.solar_bonus,
            "production": house.production,
            "consumption": house.consumption,
            "surplus": surplus,
        }


class _HeatRoomEntity(HeatControllerEntity):
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


class HeatRoomSourceSensor(_HeatRoomEntity, SensorEntity):
    """What heats the room right now: idle, splits or boiler."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["idle", "splits", "boiler"]
    _attr_icon = "mdi:heat-pump"

    def __init__(
        self,
        entry: ConfigEntry,
        shared: HeatState,
        controller: HeatController,
        room: RoomConfig,
    ) -> None:
        super().__init__(entry, shared, controller, room, "heat_source")

    @property
    def native_value(self) -> Optional[str]:
        state = self.room_state
        return state.source if state else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        state = self.room_state
        if state is None:
            return {}
        decision = (self._shared.house or None) and self._shared.house.room(
            self._room.id
        )
        return {
            "temperature": state.temp,
            "target": state.target,
            "room_mode": state.mode,
            "preheating": bool(decision and decision.preheating),
            "manual_override": state.override_mode is not None,
            "boiler_fallback": state.fallback,
        }


class HeatRoomRateSensor(_HeatRoomEntity, SensorEntity):
    """Learned heating speed of the room (°C/h)."""

    _attr_native_unit_of_measurement = "°C/h"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:speedometer"

    def __init__(
        self,
        entry: ConfigEntry,
        shared: HeatState,
        controller: HeatController,
        room: RoomConfig,
    ) -> None:
        super().__init__(entry, shared, controller, room, "learned_rate")

    @property
    def native_value(self) -> Optional[float]:
        state = self.room_state
        return state.rate if state else None


class HeatRoomNextPreheatSensor(_HeatRoomEntity, SensorEntity):
    """When the next pre-heating of the room starts."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:timer-outline"

    def __init__(
        self,
        entry: ConfigEntry,
        shared: HeatState,
        controller: HeatController,
        room: RoomConfig,
    ) -> None:
        super().__init__(entry, shared, controller, room, "next_preheat")

    @property
    def native_value(self) -> Optional[datetime]:
        state = self.room_state
        return state.next_preheat if state else None
