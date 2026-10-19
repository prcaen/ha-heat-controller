"""State shared between the controller and its entities."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Optional

from homeassistant.core import callback

from .logic import SOURCE_AUTO, SOURCE_OPTIONS, Decision

_LOGGER = logging.getLogger(__name__)

Subscriber = Callable[[], None]


class HeatState:
    """Flags and last decision, shared between controller and entities.

    Flags persist through the entities that carry them (RestoreEntity):
    heat pump source, preheating, demand, active, away mode, forced source
    and absence end — like the input_boolean from the original YAML
    automations.
    """

    def __init__(self) -> None:
        self.active: bool = False
        self.away_mode: bool = False
        self.away_until: Optional[datetime] = None
        self.heat_pump_source: bool = False
        self.preheating: bool = False
        self.demand: bool = False
        self.forced_source: str = SOURCE_AUTO
        self.decision: Optional[Decision] = None
        self._subscribers: list[Subscriber] = []

    @callback
    def subscribe(self, subscriber: Subscriber) -> Callable[[], None]:
        """Subscribe an entity; returns an unsubscribe function."""
        self._subscribers.append(subscriber)

        @callback
        def _unsubscribe() -> None:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

        return _unsubscribe

    @callback
    def _publish(self) -> None:
        for subscriber in list(self._subscribers):
            try:
                subscriber()
            except Exception:  # noqa: BLE001 - never block the publication
                _LOGGER.exception("Error while notifying an entity")

    @callback
    def set_active(self, value: bool) -> None:
        self.active = bool(value)
        self._publish()

    @callback
    def set_away_mode(self, value: bool) -> None:
        self.away_mode = bool(value)
        self._publish()

    @callback
    def set_away_until(self, value: Optional[datetime]) -> None:
        self.away_until = value
        self._publish()

    @callback
    def set_heat_pump_source(self, value: bool) -> None:
        self.heat_pump_source = bool(value)
        self._publish()

    @callback
    def set_preheating(self, value: bool) -> None:
        self.preheating = bool(value)
        self._publish()

    @callback
    def set_demand(self, value: bool) -> None:
        self.demand = bool(value)
        self._publish()

    @callback
    def set_forced_source(self, value: str) -> None:
        self.forced_source = value if value in SOURCE_OPTIONS else SOURCE_AUTO
        self._publish()

    @callback
    def set_decision(self, decision: Optional[Decision]) -> None:
        self.decision = decision
        self._publish()