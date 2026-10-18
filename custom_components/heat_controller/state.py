"""État partagé entre le contrôleur et ses entités."""

from __future__ import annotations

import logging
from typing import Callable, Optional

from homeassistant.core import callback

from .logic import Decision

_LOGGER = logging.getLogger(__name__)

Subscriber = Callable[[], None]


class HeatState:
    """Drapeaux et dernière décision, partagés entre contrôleur et entités.

    Les drapeaux persistent via les entités qui les portent (RestoreEntity) :
    source PAC, préchauffe, demande, actif, mode absent — comme les
    input_boolean des automatisations YAML d'origine.
    """

    def __init__(self) -> None:
        self.actif: bool = False
        self.mode_absent: bool = False
        self.source_pac: bool = False
        self.prechauffe: bool = False
        self.demande: bool = False
        self.decision: Optional[Decision] = None
        self._subscribers: list[Subscriber] = []

    @callback
    def subscribe(self, subscriber: Subscriber) -> Callable[[], None]:
        """Abonne une entité ; retourne une fonction de désabonnement."""
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
            except Exception:  # noqa: BLE001 - ne jamais bloquer la publication
                _LOGGER.exception("Erreur lors de la notification d'une entité")

    @callback
    def set_actif(self, value: bool) -> None:
        self.actif = bool(value)
        self._publish()

    @callback
    def set_mode_absent(self, value: bool) -> None:
        self.mode_absent = bool(value)
        self._publish()

    @callback
    def set_source_pac(self, value: bool) -> None:
        self.source_pac = bool(value)
        self._publish()

    @callback
    def set_prechauffe(self, value: bool) -> None:
        self.prechauffe = bool(value)
        self._publish()

    @callback
    def set_demande(self, value: bool) -> None:
        self.demande = bool(value)
        self._publish()

    @callback
    def set_decision(self, decision: Optional[Decision]) -> None:
        self.decision = decision
        self._publish()
