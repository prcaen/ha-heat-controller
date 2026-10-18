# Contrôleur de chauffage pour Home Assistant

Intégration personnalisée qui remplace les automatisations YAML
« Chauffage - Bascule PAC/chaudière » et
« Chauffage - Programmation jour/nuit/absence avec anticipation »
(et tous les `input_*` qui allaient avec) par un seul composant configurable,
et qui pilote la période solaire grâce à la **production réelle** de la
centrale photovoltaïque (capteur de puissance de l'intégration
[Solenso](https://github.com/pismache/ha-solenso)) au lieu d'une prévision
de type Forecast.Solar.

## Ce que fait l'intégration

- **Bascule PAC / chaudière** : la pompe à chaleur prend le relais quand la
  température extérieure dépasse le seuil (7 °C par défaut), la chaudière
  quand elle passe en dessous, avec un hystérésis de ±0,5 °C pour éviter les
  allers-retours. Séquence de bascule identique au YAML : chaudière off,
  ventilateurs accessoires on, attente 15 s, clims PAC en mode `heat`
  (et l'inverse vers la chaudière).
- **Programmation jour / nuit / absence / solaire** : consigne cible selon la
  période (jour, nuit, absence, fenêtre solaire), préchauffe anticipée
  proportionnelle à l'écart de température et à la vitesse de montée de la
  source active (°C/h) + marge de sécurité, hystérésis de la demande de
  chauffage, arrêt automatique du mode absence après l'heure de retour.
- **Période solaire réelle** : la fenêtre solaire ne s'active que si la
  centrale **produit effectivement** au-dessus du seuil (500 W par défaut),
  lu sur le capteur de puissance instantanée — la nuit il lit 0 W, la
  fenêtre ne s'ouvre donc que s'il y a vraiment du soleil.

## Entités créées (appareil « Chauffage »)

| Entité | Type | Équivalent YAML |
|---|---|---|
| `switch.chauffage_pilotage_actif` | interrupteur | `input_boolean.chauffage_actif` |
| `switch.chauffage_mode_absent` | interrupteur | `input_boolean.chauffage_mode_absent` |
| `binary_sensor.chauffage_source_pac` | capteur binaire | `input_boolean.chauffage_source_pac` |
| `binary_sensor.chauffage_prechauffe` | capteur binaire | `input_boolean.chauffage_prechauffe` |
| `binary_sensor.chauffage_demande_chauffe` | capteur binaire | `input_boolean.chauffage_demande` |
| `binary_sensor.chauffage_periode_jour` | capteur binaire | (calculé) |
| `binary_sensor.chauffage_periode_solaire` | capteur binaire | (calculé) |
| `sensor.chauffage_consigne_cible` | capteur | (calculé) |
| `sensor.chauffage_duree_prechauffe` | capteur | (calculé, minutes) |
| `sensor.chauffage_source_de_chaleur` | capteur | `PAC` / `Chaudière` |

Les drapeaux (source PAC, préchauffe, demande, actif, mode absent)
persistent d'un redémarrage à l'autre, comme les `input_boolean` d'origine.

> Les identifiants exacts sont générés par Home Assistant à partir des noms
> (p. ex. `binary_sensor.chauffage_demande_de_chauffe`) ; vérifiez-les dans
> les outils de développement après l'installation.

## Installation

### HACS

HACS → menu ⋮ → **Dépôts personnalisés** → `https://github.com/<votre-compte>/ha-heat-controller`,
catégorie **Intégration** → installer **Contrôleur de chauffage** → redémarrer
Home Assistant.

### Manuelle

Copier le dossier `custom_components/heat_controller` dans
`/config/custom_components/`, puis redémarrer Home Assistant.

## Configuration

Paramètres → Appareils et services → **Ajouter une intégration** →
**Contrôleur de chauffage**. Trois écrans :

1. **Capteurs** : température extérieure, température intérieure, et
   production solaire (capteur de **puissance** instantanée). Si
   l'intégration Solenso est installée, le capteur de puissance de la
   centrale est présélectionné.
2. **Appareils pilotés** : le climate de la chaudière (Netatmo), les clims de
   la PAC **dans l'ordre**, leurs ventilateurs de puissance **dans le même
   ordre** (facultatif), et les ventilateurs accessoires (ceux de la bascule,
   `fan.climate_series_accessory*` dans le YAML d'origine).
3. **Réglages** : consignes jour/nuit/absent/solaire, hystérésis, seuil
   extérieur de bascule, seuil de production, vitesses de montée (°C/h),
   marge de sécurité (min), heures jour/nuit/retour et fenêtre solaire.

Tout est modifiable ensuite via **Configurer** sur l'entrée.

### Correspondance avec la configuration YAML d'origine

| YAML | Intégration |
|---|---|
| `sensor.jardin_station_meteo_externe_temperature` | Capteur « température extérieure » |
| `sensor.salon_station_meteo_interne_temperature` | Capteur « température intérieure » |
| `sensor.power_production_now` (Forecast.Solar) | Capteur « production solaire » : **puissance de la centrale Solenso** |
| `climate.netatmo_smart_thermostat` | Climate chaudière |
| `climate.salon_clim_gauche`, `climate.salon_clim_droite` | Clims PAC |
| `fan.clim_salon_gauche_puissance`, `fan.clim_salon_droite_puissance` | Ventilateurs de puissance (même ordre) |
| `fan.climate_series_accessory`, `fan.climate_series_accessory_2` | Ventilateurs accessoires |
| `input_number.chauffage_*` | Écran Réglages |
| `input_datetime.chauffage_heure_*` | Écran Réglages |
| `input_boolean.chauffage_*` | Interrupteurs et capteurs binaires ci-dessus |

### Migration depuis les YAML

1. Installer et configurer l'intégration (laisser le pilotage **inactif**
   pour commencer).
2. Désactiver les deux automatisations YAML.
3. Activer l'interrupteur « Pilotage actif ».
4. Les `input_number` / `input_datetime` / `input_boolean` ne sont plus
   utilisés par le pilotage ; ne supprimez leurs entrées qu'après avoir
   vérifié que rien d'autre ne les référence (dashboards, autres
   automatisations).

## Différences volontaires avec les YAML

- Battement de cœur toutes les minutes + écoute des capteurs, au lieu de
  5/15 minutes : les décisions sont idempotentes et protégées par
  hystérésis, on réagit juste plus vite.
- À la bascule vers la chaudière, les ventilateurs de puissance de la PAC
  sont aussi coupés (le YAML ne coupait que les ventilateurs accessoires) :
  rien ne reste alimenté inutilement.
- La fenêtre solaire repose sur la production mesurée, donc plus fiable
  qu'une prévision ; en cas de coupure de remontée Solenso en plein soleil,
  le capteur passe indisponible et est traité comme « pas de production ».

## Développement

```bash
# tests de la logique de décision (aucune dépendance)
python3 -m unittest discover -s tests

# smoke test avec un vrai homeassistant (Python 3.13+)
uv venv /tmp/hacheck --python 3.13
uv pip install --python /tmp/hacheck/bin/python homeassistant
/tmp/hacheck/bin/python tests/smoke_test_ha.py
```
