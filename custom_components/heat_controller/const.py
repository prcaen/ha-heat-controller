"""Constantes du contrôleur de chauffage."""

DOMAIN = "heat_controller"

PLATFORMS = ["binary_sensor", "sensor", "switch"]

# --- Sélection des entités ---------------------------------------------------

CONF_OUTDOOR_TEMP_SENSOR = "outdoor_temp_sensor"
CONF_INDOOR_TEMP_SENSOR = "indoor_temp_sensor"
CONF_PRODUCTION_SENSOR = "production_sensor"
CONF_BOILER_CLIMATE = "boiler_climate"
CONF_PAC_CLIMATES = "pac_climates"
CONF_PAC_FANS = "pac_fans"
CONF_ACCESSORY_FANS = "accessory_fans"

# --- Réglages ----------------------------------------------------------------

CONF_SEUIL_EXTERIEUR = "seuil_exterieur"
CONF_CONSIGNE_JOUR = "consigne_jour"
CONF_CONSIGNE_NUIT = "consigne_nuit"
CONF_CONSIGNE_ABSENT = "consigne_absent"
CONF_CONSIGNE_SOLAIRE = "consigne_solaire"
CONF_HYSTERESIS = "hysteresis"
CONF_SEUIL_PRODUCTION = "seuil_production"
CONF_VITESSE_PAC = "vitesse_pac"
CONF_VITESSE_CHAUDIERE = "vitesse_chaudiere"
CONF_MARGE_SECURITE = "marge_securite"
CONF_HEURE_JOUR = "heure_jour"
CONF_HEURE_NUIT = "heure_nuit"
CONF_HEURE_RETOUR = "heure_retour"
CONF_HEURE_SOLAIRE_DEBUT = "heure_solaire_debut"
CONF_HEURE_SOLAIRE_FIN = "heure_solaire_fin"

# Valeurs par défaut reprises des automatisations YAML d'origine.
DEFAULT_SEUIL_EXTERIEUR = 7.0
DEFAULT_CONSIGNE_JOUR = 20.0
DEFAULT_CONSIGNE_NUIT = 16.0
DEFAULT_CONSIGNE_ABSENT = 12.0
DEFAULT_CONSIGNE_SOLAIRE = 22.0
DEFAULT_HYSTERESIS = 0.3
DEFAULT_SEUIL_PRODUCTION = 500.0
DEFAULT_VITESSE_PAC = 1.0
DEFAULT_VITESSE_CHAUDIERE = 1.0
DEFAULT_MARGE_SECURITE = 15
DEFAULT_HEURE_JOUR = "07:00:00"
DEFAULT_HEURE_NUIT = "22:00:00"
DEFAULT_HEURE_RETOUR = "18:00:00"
DEFAULT_HEURE_SOLAIRE_DEBUT = "11:00:00"
DEFAULT_HEURE_SOLAIRE_FIN = "15:00:00"

DEFAULT_INDOOR_TEMP = 19.0  # repli si le capteur interne est muet
DEFAULT_PRODUCTION = 0.0  # repli si le capteur de production est muet

# --- Réglages par défaut exposés dans le flux de configuration ---------------

SETTING_DEFAULTS = {
    CONF_SEUIL_EXTERIEUR: DEFAULT_SEUIL_EXTERIEUR,
    CONF_CONSIGNE_JOUR: DEFAULT_CONSIGNE_JOUR,
    CONF_CONSIGNE_NUIT: DEFAULT_CONSIGNE_NUIT,
    CONF_CONSIGNE_ABSENT: DEFAULT_CONSIGNE_ABSENT,
    CONF_CONSIGNE_SOLAIRE: DEFAULT_CONSIGNE_SOLAIRE,
    CONF_HYSTERESIS: DEFAULT_HYSTERESIS,
    CONF_SEUIL_PRODUCTION: DEFAULT_SEUIL_PRODUCTION,
    CONF_VITESSE_PAC: DEFAULT_VITESSE_PAC,
    CONF_VITESSE_CHAUDIERE: DEFAULT_VITESSE_CHAUDIERE,
    CONF_MARGE_SECURITE: DEFAULT_MARGE_SECURITE,
    CONF_HEURE_JOUR: DEFAULT_HEURE_JOUR,
    CONF_HEURE_NUIT: DEFAULT_HEURE_NUIT,
    CONF_HEURE_RETOUR: DEFAULT_HEURE_RETOUR,
    CONF_HEURE_SOLAIRE_DEBUT: DEFAULT_HEURE_SOLAIRE_DEBUT,
    CONF_HEURE_SOLAIRE_FIN: DEFAULT_HEURE_SOLAIRE_FIN,
}

# --- Comportement ------------------------------------------------------------

POLL_INTERVAL_SECONDS = 60  # battement de cœur (les décisions sont idempotentes)
STARTUP_DELAY_SECONDS = 5  # attente après démarrage pour laisser les états se restaurer
SERVICE_TIMEOUT_SECONDS = 15  # délai max d'un appel de service
BASCULE_FAN_DELAY_SECONDS = 15  # délai fans accessoires -> clim PAC (YAML d'origine)
HVAC_MODE_DELAY_SECONDS = 5  # délai après changement de mode HVAC (YAML d'origine)
FAN_WAIT_TIMEOUT_SECONDS = 20  # attente de montée d'un ventilateur (YAML d'origine)
