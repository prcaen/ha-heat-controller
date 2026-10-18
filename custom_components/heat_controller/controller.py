"""Contrôleur de chauffage : lit l'état, décide, applique.

Équivalent Python des automatisations YAML « Chauffage - Bascule PAC/chaudière »
et « Chauffage - Programmation jour/nuit/absence avec anticipation ».

Différences volontaires avec les YAML, sans changement de comportement :
- battement de cœur toutes les minutes (au lieu de 5/15 min) + écoute des
  capteurs : les décisions sont idempotentes et protégées par hystérésis ;
- la production solaire vient d'un capteur de puissance réel (centrale
  Solenso) au lieu de la prévision Forecast.Solar.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any, Callable, Mapping, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    BASCULE_FAN_DELAY_SECONDS,
    CONF_ACCESSORY_FANS,
    CONF_BOILER_CLIMATE,
    CONF_CONSIGNE_ABSENT,
    CONF_CONSIGNE_JOUR,
    CONF_CONSIGNE_NUIT,
    CONF_CONSIGNE_SOLAIRE,
    CONF_HEURE_JOUR,
    CONF_HEURE_NUIT,
    CONF_HEURE_RETOUR,
    CONF_HEURE_SOLAIRE_DEBUT,
    CONF_HEURE_SOLAIRE_FIN,
    CONF_HYSTERESIS,
    CONF_INDOOR_TEMP_SENSOR,
    CONF_MARGE_SECURITE,
    CONF_OUTDOOR_TEMP_SENSOR,
    CONF_PAC_CLIMATES,
    CONF_PAC_FANS,
    CONF_PRODUCTION_SENSOR,
    CONF_SEUIL_EXTERIEUR,
    CONF_SEUIL_PRODUCTION,
    CONF_VITESSE_CHAUDIERE,
    CONF_VITESSE_PAC,
    SETTING_DEFAULTS,
    DEFAULT_INDOOR_TEMP,
    DEFAULT_PRODUCTION,
    FAN_WAIT_TIMEOUT_SECONDS,
    HVAC_MODE_DELAY_SECONDS,
    POLL_INTERVAL_SECONDS,
    SERVICE_TIMEOUT_SECONDS,
    STARTUP_DELAY_SECONDS,
)
from .logic import Decision, Inputs, Settings, compute, parse_time, target_temperature
from .state import HeatState

_LOGGER = logging.getLogger(__name__)

HVAC_OFF = "off"
HVAC_HEAT = "heat"
HVAC_AUTO = "auto"


def _as_float(state: Optional[State], default: Optional[float] = None) -> Optional[float]:
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return default
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return default


def settings_from_config(config: Mapping[str, Any]) -> Settings:
    """Construit les réglages à partir de la configuration de l'entrée."""

    def number(key: str) -> float:
        try:
            return float(config.get(key, SETTING_DEFAULTS[key]))
        except (TypeError, ValueError):
            return float(SETTING_DEFAULTS[key])

    def moment(key: str) -> timedelta:
        return parse_time(config.get(key, SETTING_DEFAULTS[key])) or parse_time(
            SETTING_DEFAULTS[key]
        )

    return Settings(
        seuil_exterieur=number(CONF_SEUIL_EXTERIEUR),
        consigne_jour=number(CONF_CONSIGNE_JOUR),
        consigne_nuit=number(CONF_CONSIGNE_NUIT),
        consigne_absent=number(CONF_CONSIGNE_ABSENT),
        consigne_solaire=number(CONF_CONSIGNE_SOLAIRE),
        hysteresis=number(CONF_HYSTERESIS),
        seuil_production=number(CONF_SEUIL_PRODUCTION),
        vitesse_pac=number(CONF_VITESSE_PAC),
        vitesse_chaudiere=number(CONF_VITESSE_CHAUDIERE),
        marge_securite=int(number(CONF_MARGE_SECURITE)),
        heure_jour=moment(CONF_HEURE_JOUR),
        heure_nuit=moment(CONF_HEURE_NUIT),
        heure_retour=moment(CONF_HEURE_RETOUR),
        heure_solaire_debut=moment(CONF_HEURE_SOLAIRE_DEBUT),
        heure_solaire_fin=moment(CONF_HEURE_SOLAIRE_FIN),
    )


class HeatController:
    """Pilote les clims, les ventilateurs et la source de chaleur."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        config: Mapping[str, Any],
        shared: HeatState,
    ) -> None:
        self.hass = hass
        self.entry_id = entry.entry_id
        self.shared = shared
        self.settings = settings_from_config(config)

        self.outdoor_sensor: str = config.get(CONF_OUTDOOR_TEMP_SENSOR, "")
        self.indoor_sensor: str = config.get(CONF_INDOOR_TEMP_SENSOR, "")
        self.production_sensor: str = config.get(CONF_PRODUCTION_SENSOR, "")

        self.boiler_climate: str = config.get(CONF_BOILER_CLIMATE, "")
        self.pac_climates: list[str] = list(config.get(CONF_PAC_CLIMATES, []))
        pac_fans: list[str] = list(config.get(CONF_PAC_FANS, []))
        # Appairage par ordre : fan[i] alimente clim[i] (None si absent).
        self.pac_fans: list[Optional[str]] = [
            pac_fans[i] if i < len(pac_fans) else None for i in range(len(self.pac_climates))
        ]
        self.accessory_fans: list[str] = list(config.get(CONF_ACCESSORY_FANS, []))

        self._lock = asyncio.Lock()
        self._cancel: list[Callable] = []
        self._first_run = True

    # --- Cycle de vie --------------------------------------------------------

    async def async_start(self) -> None:
        """Démarre l'écoute et le battement de cœur, puis la première passe."""
        watched = [
            entity
            for entity in (self.outdoor_sensor, self.indoor_sensor, self.production_sensor)
            if entity
        ]
        self._cancel.append(
            async_track_state_change_event(self.hass, watched, self._on_state_change)
        )
        self._cancel.append(
            async_track_time_interval(
                self.hass,
                self._on_heartbeat,
                timedelta(seconds=POLL_INTERVAL_SECONDS),
            )
        )
        async_call_later(
            self.hass,
            STARTUP_DELAY_SECONDS,
            self._on_first_run,
        )

    async def async_stop(self) -> None:
        for cancel in self._cancel:
            cancel()
        self._cancel.clear()

    @callback
    def request_update(self) -> None:
        """Demande une passe de pilotage (action utilisateur, changement de réglage)."""
        self.hass.async_create_task(self.async_update())

    @callback
    def _on_state_change(self, event: Event) -> None:
        if (new_state := event.data.get("new_state")) is None:
            return
        old_state = event.data.get("old_state")
        if old_state is not None and old_state.state == new_state.state:
            return
        self.hass.async_create_task(self.async_update())

    @callback
    def _on_heartbeat(self, now) -> None:
        self.hass.async_create_task(self.async_update())

    @callback
    def _on_first_run(self, now) -> None:
        self.hass.async_create_task(self.async_update())

    # --- Décision ------------------------------------------------------------

    def _snapshot_inputs(self) -> Inputs:
        temp_ext = _as_float(self.hass.states.get(self.outdoor_sensor))
        temp_int = _as_float(
            self.hass.states.get(self.indoor_sensor), DEFAULT_INDOOR_TEMP
        )
        production = _as_float(
            self.hass.states.get(self.production_sensor), DEFAULT_PRODUCTION
        )
        return Inputs(
            now=dt_util.now(),
            temp_ext=temp_ext,
            temp_int=temp_int if temp_int is not None else DEFAULT_INDOOR_TEMP,
            production=production if production is not None else DEFAULT_PRODUCTION,
            actif=self.shared.actif,
            mode_absent=self.shared.mode_absent,
            source_pac=self.shared.source_pac,
            prechauffe_flag=self.shared.prechauffe,
            demande_flag=self.shared.demande,
        )

    async def async_update(self) -> None:
        """Passe complète : décision puis application (sérialisée)."""
        if self._lock.locked():
            return
        async with self._lock:
            try:
                await self._update_locked()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Erreur pendant la passe de pilotage")
            finally:
                self._first_run = False

    async def _update_locked(self) -> None:
        shared = self.shared
        if not shared.actif:
            return

        decision = compute(self.settings, self._snapshot_inputs())
        shared.set_decision(decision)

        # L'heure de retour est dépassée : fin du mode absence (comme le YAML).
        if decision.retour_passe and shared.mode_absent:
            shared.set_mode_absent(False)
        shared.set_prechauffe(decision.prechauffe)
        shared.set_demande(decision.besoin_chauffe)

        # Bascule PAC <-> chaudière (ou réapplication au premier passage).
        if decision.bascule:
            await self._apply_bascule(decision.doit_utiliser_pac)
        elif self._first_run:
            await self._enforce_accessory_fans(shared.source_pac)

        # Programmation jour/nuit/absence/solaire sur la source active.
        await self._apply_program(decision)

    # --- Application ---------------------------------------------------------

    async def _call(self, domain: str, service: str, entity_id: str, **data: Any) -> None:
        payload = {ATTR_ENTITY_ID: entity_id, **data}
        try:
            await asyncio.wait_for(
                self.hass.services.async_call(domain, service, payload, blocking=True),
                SERVICE_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            _LOGGER.warning("Timeout du service %s.%s sur %s", domain, service, entity_id)
        except Exception:  # noqa: BLE001 - service ou entité manquante
            _LOGGER.warning(
                "Échec du service %s.%s sur %s", domain, service, entity_id, exc_info=True
            )

    def _is_on(self, entity_id: Optional[str]) -> bool:
        if not entity_id:
            return False
        return self.hass.states.is_state(entity_id, STATE_ON)

    @staticmethod
    def _valid(state: Optional[State]) -> bool:
        return state is not None and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)

    async def _wait_fan_on(self, entity_id: str, timeout: int) -> bool:
        """Attend que le ventilateur soit allumé, comme wait_template du YAML."""
        waited = 0.0
        while waited < timeout:
            if self._is_on(entity_id):
                return True
            await asyncio.sleep(1)
            waited += 1
        return self._is_on(entity_id)

    async def _apply_bascule(self, vers_pac: bool) -> None:
        """Reproduit « Chauffage - Bascule PAC/chaudière »."""
        shared = self.shared
        if vers_pac:
            _LOGGER.info("Bascule vers la PAC (température extérieure suffisante)")
            await self._call("climate", "set_hvac_mode", self.boiler_climate, hvac_mode=HVAC_OFF)
            for fan in self.accessory_fans:
                await self._call("fan", "turn_on", fan)
            await asyncio.sleep(BASCULE_FAN_DELAY_SECONDS)
            for clim in self.pac_climates:
                await self._call("climate", "set_hvac_mode", clim, hvac_mode=HVAC_HEAT)
            shared.set_source_pac(True)
        else:
            _LOGGER.info("Bascule vers la chaudière (température extérieure trop basse)")
            for clim in self.pac_climates:
                await self._call("climate", "set_hvac_mode", clim, hvac_mode=HVAC_OFF)
            # Ventilateurs accessoires (YAML d'origine) et ventilateurs de
            # puissance : tout ce qui alimente la PAC doit s'éteindre.
            fans = dict.fromkeys(
                [*self.accessory_fans, *(fan for fan in self.pac_fans if fan)]
            )
            for fan in fans:
                await self._call("fan", "turn_off", fan)
            await self._call("climate", "set_hvac_mode", self.boiler_climate, hvac_mode=HVAC_AUTO)
            shared.set_source_pac(False)

    async def _enforce_accessory_fans(self, on: bool) -> None:
        """Réaligne les ventilateurs accessoires sur la source active."""
        for fan in self.accessory_fans:
            if on and not self._is_on(fan):
                await self._call("fan", "turn_on", fan)
            elif not on and self._is_on(fan):
                await self._call("fan", "turn_off", fan)

    async def _apply_program(self, decision: Decision) -> None:
        """Reproduit « Chauffage - Programmation jour/nuit/absence »."""
        besoin = decision.besoin_chauffe
        source_pac = self.shared.source_pac

        pairs: list[tuple[str, Optional[str]]] = (
            list(zip(self.pac_climates, self.pac_fans))
            if source_pac
            else [(self.boiler_climate, None)]
        )

        mode_desire = HVAC_OFF if not besoin else (HVAC_HEAT if source_pac else HVAC_AUTO)

        for clim, fan in pairs:
            state = self.hass.states.get(clim)

            # Alimentation du ventilateur de puissance avant d'attaquer la clim.
            fan_on = True
            if fan:
                fan_on = self._is_on(fan)
                if besoin and source_pac and not fan_on:
                    await self._call("fan", "turn_on", fan)
                    fan_on = await self._wait_fan_on(fan, FAN_WAIT_TIMEOUT_SECONDS)

            # Mode HVAC : off / heat (PAC) / auto (chaudière).
            if (
                self._valid(state)
                and state.state != mode_desire  # type: ignore[union-attr]
                and (not source_pac or fan is None or fan_on)
            ):
                await self._call(
                    "climate", "set_hvac_mode", clim, hvac_mode=mode_desire
                )
                await asyncio.sleep(HVAC_MODE_DELAY_SECONDS)
                state = self.hass.states.get(clim)

            # Consigne, arrondie au pas de l'appareil et bornée.
            if besoin and self._valid(state):
                attributes = state.attributes if state else {}  # type: ignore[union-attr]
                cible = target_temperature(
                    decision.consigne_cible,
                    step=float(attributes.get("target_temp_step") or 0.5),
                    minimum=float(attributes.get("min_temp") or 5.0),
                    maximum=float(attributes.get("max_temp") or 30.0),
                )
                try:
                    actuelle = float(attributes.get("temperature") or 0)
                except (TypeError, ValueError):
                    actuelle = 0.0
                if actuelle != cible:
                    await self._call("climate", "set_temperature", clim, temperature=cible)

            # Coupure du ventilateur de puissance quand il n'y a pas de besoin.
            if fan and not besoin and source_pac and self._is_on(fan):
                await self._call("fan", "turn_off", fan)
