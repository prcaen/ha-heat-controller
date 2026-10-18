"""Logique de décision du contrôleur de chauffage.

Module volontairement indépendant de Home Assistant pour être testé
unitairement. Il reproduit fidèlement les automatisations YAML d'origine :

- « Chauffage - Bascule PAC/chaudière » : bascule de la source de chaleur
  selon la température extérieure, avec hystérésis de 0,5 °C autour du seuil.
- « Chauffage - Programmation jour/nuit/absence avec anticipation » :
  consigne cible selon la période (jour, nuit, absence, solaire), préchauffe
  anticipée proportionnelle à l'écart de température et à la vitesse de
  montée de la source active, hystérésis de la demande de chauffage.

La production solaire utilisée pour la période solaire est la puissance
instantanée réellement produite (capteur de puissance de la centrale, p. ex.
Solenso), à la place d'une prévision.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

# Hystérésis (°C) autour du seuil extérieur de bascule PAC/chaudière.
HYSTERESIS_BASCULE = 0.5


def round_half_up(value: float) -> int:
    """Arrondi à l'entier le plus proche, les 0,5 vers le haut (comme Jinja |round(0))."""
    return math.floor(value + 0.5)


def parse_time(value: object) -> Optional[timedelta]:
    """Convertit « HH:MM » ou « HH:MM:SS » en durée depuis minuit."""
    if value is None:
        return None
    parts = str(value).split(":")
    if len(parts) < 2:
        return None
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        return None
    return timedelta(hours=hours, minutes=minutes, seconds=seconds)


def at_time(now: datetime, since_midnight: timedelta) -> datetime:
    """La datetime du jour « now » à l'heure donnée (durée depuis minuit)."""
    return now.replace(hour=0, minute=0, second=0, microsecond=0) + since_midnight


@dataclass(frozen=True)
class Settings:
    """Réglages du contrôleur, déjà validés et convertis."""

    seuil_exterieur: float = 7.0
    consigne_jour: float = 20.0
    consigne_nuit: float = 16.0
    consigne_absent: float = 12.0
    consigne_solaire: float = 22.0
    hysteresis: float = 0.3
    seuil_production: float = 500.0
    vitesse_pac: float = 1.0
    vitesse_chaudiere: float = 1.0
    marge_securite: int = 15
    heure_jour: timedelta = timedelta(hours=7)
    heure_nuit: timedelta = timedelta(hours=22)
    heure_retour: timedelta = timedelta(hours=18)
    heure_solaire_debut: timedelta = timedelta(hours=11)
    heure_solaire_fin: timedelta = timedelta(hours=15)


@dataclass(frozen=True)
class Inputs:
    """État instantané utilisé pour la décision."""

    now: datetime
    temp_ext: Optional[float]  # None = inconnue -> pas de bascule
    temp_int: float
    production: float  # W produits en ce moment
    actif: bool = True
    mode_absent: bool = False
    source_pac: bool = False
    prechauffe_flag: bool = False
    demande_flag: bool = False


@dataclass(frozen=True)
class Decision:
    """Décision calculée à un instant donné."""

    en_periode_jour: bool
    en_periode_solaire: bool
    minutes_necessaires: int  # durée de préchauffe estimée (min)
    retour_passe: bool  # l'heure de retour est dépassée en mode absent
    mode_absent_effectif: bool
    prechauffe: bool
    consigne_cible: float
    besoin_chauffe: bool
    doit_utiliser_pac: bool
    bascule: bool  # la source doit changer maintenant


def compute(settings: Settings, inputs: Inputs) -> Decision:
    """Calcule la décision complète, équivalente aux deux automatisations."""
    now = inputs.now

    # --- Périodes jour / nuit / solaire (cf. 2e automatisation) -------------
    jour_dt = at_time(now, settings.heure_jour)
    nuit_dt = at_time(now, settings.heure_nuit)
    en_periode_jour = jour_dt <= now < nuit_dt

    solaire_debut = at_time(now, settings.heure_solaire_debut)
    solaire_fin = at_time(now, settings.heure_solaire_fin)
    en_periode_solaire = (
        en_periode_jour
        and solaire_debut <= now < solaire_fin
        and inputs.production >= settings.seuil_production
    )

    # --- Anticipation : durée nécessaire pour rejoindre la consigne jour ----
    vitesse = settings.vitesse_pac if inputs.source_pac else settings.vitesse_chaudiere
    ecart = max(settings.consigne_jour - inputs.temp_int, 0.0)
    if vitesse > 0:
        minutes_necessaires = (
            round_half_up(ecart / vitesse * 60) + settings.marge_securite
        )
    else:
        minutes_necessaires = settings.marge_securite
    minutes = timedelta(minutes=minutes_necessaires)

    # --- Absence et retour --------------------------------------------------
    retour_dt = at_time(now, settings.heure_retour)
    retour_passe = inputs.mode_absent and now >= retour_dt
    mode_absent_effectif = inputs.mode_absent and not retour_passe

    # --- Préchauffe ----------------------------------------------------------
    prochain_jour = jour_dt if now < jour_dt else jour_dt + timedelta(days=1)
    debut_prechauffe_matin = (
        not mode_absent_effectif
        and not en_periode_jour
        and now >= prochain_jour - minutes
    )
    debut_prechauffe_retour = (
        mode_absent_effectif and now >= retour_dt - minutes
    )
    prechauffe_en_cours = inputs.prechauffe_flag and not retour_passe
    prechauffe = debut_prechauffe_matin or debut_prechauffe_retour or (
        prechauffe_en_cours and (mode_absent_effectif or not en_periode_jour)
    )

    # --- Consigne cible -------------------------------------------------------
    if prechauffe:
        consigne_cible = settings.consigne_jour
    elif mode_absent_effectif:
        consigne_cible = settings.consigne_absent
    elif en_periode_solaire:
        consigne_cible = max(settings.consigne_solaire, settings.consigne_jour)
    elif en_periode_jour:
        consigne_cible = settings.consigne_jour
    else:
        consigne_cible = settings.consigne_nuit

    # --- Besoin de chauffage avec hystérésis ---------------------------------
    besoin_chauffe = (
        inputs.demande_flag
        and inputs.temp_int < consigne_cible + settings.hysteresis
    ) or (
        not inputs.demande_flag
        and inputs.temp_int < consigne_cible - settings.hysteresis
    )

    # --- Bascule PAC / chaudière (cf. 1re automatisation) --------------------
    if inputs.temp_ext is None:
        # Température extérieure indisponible : on garde la source actuelle.
        doit_utiliser_pac = inputs.source_pac
        bascule = False
    else:
        seuil = settings.seuil_exterieur + (
            -HYSTERESIS_BASCULE if inputs.source_pac else HYSTERESIS_BASCULE
        )
        doit_utiliser_pac = inputs.temp_ext > seuil
        bascule = doit_utiliser_pac != inputs.source_pac

    return Decision(
        en_periode_jour=en_periode_jour,
        en_periode_solaire=en_periode_solaire,
        minutes_necessaires=minutes_necessaires,
        retour_passe=retour_passe,
        mode_absent_effectif=mode_absent_effectif,
        prechauffe=prechauffe,
        consigne_cible=consigne_cible,
        besoin_chauffe=besoin_chauffe,
        doit_utiliser_pac=doit_utiliser_pac,
        bascule=bascule,
    )


def target_temperature(
    consigne: float,
    step: float = 0.5,
    minimum: float = 5.0,
    maximum: float = 30.0,
) -> float:
    """Arrondit la consigne au pas de l'appareil puis borne [min, max].

    Équivalent du template : ((consigne / pas) | round(0) * pas), borné,
    arrondi à 1 décimale.
    """
    pas = step if step and step > 0 else 0.5
    mini = minimum
    maxi = maximum
    if maxi < mini:
        mini, maxi = maxi, mini
    cible = round_half_up(consigne / pas) * pas
    cible = min(max(cible, mini), maxi)
    return round(cible, 1)
