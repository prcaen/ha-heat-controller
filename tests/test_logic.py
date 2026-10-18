"""Tests de la logique de décision, équivalents aux automatisations YAML."""

import importlib.util
import os
import sys
import unittest
from datetime import datetime, timedelta

# logic.py ne dépend pas de Home Assistant : chargement direct par chemin
# pour éviter d'importer le package (qui, lui, dépend de homeassistant).
_LOGIC_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "custom_components",
    "heat_controller",
    "logic.py",
)
_spec = importlib.util.spec_from_file_location("heat_controller_logic", _LOGIC_PATH)
logic = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = logic
_spec.loader.exec_module(logic)

HYSTERESIS_BASCULE = logic.HYSTERESIS_BASCULE
Inputs = logic.Inputs
Settings = logic.Settings
compute = logic.compute
parse_time = logic.parse_time
target_temperature = logic.target_temperature


def settings(**kwargs) -> Settings:
    base = dict(
        seuil_exterieur=7.0,
        consigne_jour=20.0,
        consigne_nuit=16.0,
        consigne_absent=12.0,
        consigne_solaire=22.0,
        hysteresis=0.3,
        seuil_production=500.0,
        vitesse_pac=1.0,
        vitesse_chaudiere=1.0,
        marge_securite=15,
        heure_jour=timedelta(hours=7),
        heure_nuit=timedelta(hours=22),
        heure_retour=timedelta(hours=18),
        heure_solaire_debut=timedelta(hours=11),
        heure_solaire_fin=timedelta(hours=15),
    )
    base.update(kwargs)
    return Settings(**base)


def inputs(now, temp_ext=10.0, temp_int=19.0, production=0.0, **kwargs) -> Inputs:
    base = dict(
        actif=True,
        mode_absent=False,
        source_pac=False,
        prechauffe_flag=False,
        demande_flag=False,
    )
    base.update(kwargs)
    return Inputs(now=now, temp_ext=temp_ext, temp_int=temp_int, production=production, **base)


class TestParseTime(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(parse_time("07:00:00"), timedelta(hours=7))
        self.assertEqual(parse_time("22:30"), timedelta(hours=22, minutes=30))
        self.assertIsNone(parse_time("choux"))
        self.assertIsNone(parse_time(None))


class TestBascule(unittest.TestCase):
    """Seuil extérieur avec hystérésis de 0,5 °C (1re automatisation)."""

    def test_chaudiere_vers_pac(self):
        s = settings()
        # source chaudière : bascule si temp_ext > 7,5
        d = compute(s, inputs(datetime(2026, 1, 15, 12), temp_ext=7.5))
        self.assertFalse(d.bascule)
        d = compute(s, inputs(datetime(2026, 1, 15, 12), temp_ext=7.6))
        self.assertTrue(d.bascule)
        self.assertTrue(d.doit_utiliser_pac)

    def test_pac_vers_chaudiere(self):
        s = settings()
        # source PAC : bascule si temp_ext <= 6,5
        d = compute(s, inputs(datetime(2026, 1, 15, 12), temp_ext=6.5, source_pac=True))
        self.assertTrue(d.bascule)
        self.assertFalse(d.doit_utiliser_pac)
        d = compute(s, inputs(datetime(2026, 1, 15, 12), temp_ext=6.6, source_pac=True))
        self.assertFalse(d.bascule)

    def test_temp_ext_inconnue(self):
        s = settings()
        d = compute(s, inputs(datetime(2026, 1, 15, 12), temp_ext=None))
        self.assertFalse(d.bascule)
        self.assertEqual(d.doit_utiliser_pac, False)


class TestPeriodes(unittest.TestCase):
    def test_periode_jour(self):
        s = settings()
        self.assertTrue(compute(s, inputs(datetime(2026, 1, 15, 7, 0, 0))).en_periode_jour)
        self.assertFalse(compute(s, inputs(datetime(2026, 1, 15, 6, 59, 59))).en_periode_jour)
        self.assertFalse(compute(s, inputs(datetime(2026, 1, 15, 22, 0, 0))).en_periode_jour)
        self.assertTrue(compute(s, inputs(datetime(2026, 1, 15, 21, 59, 59))).en_periode_jour)

    def test_periode_solaire(self):
        s = settings()
        # 12:00, dans la fenêtre 11:00-15:00, production suffisante
        d = compute(s, inputs(datetime(2026, 1, 15, 12), production=500.0))
        self.assertTrue(d.en_periode_solaire)
        # production insuffisante
        d = compute(s, inputs(datetime(2026, 1, 15, 12), production=499.9))
        self.assertFalse(d.en_periode_solaire)
        # hors fenêtre
        d = compute(s, inputs(datetime(2026, 1, 15, 16), production=3000.0))
        self.assertFalse(d.en_periode_solaire)
        # la nuit, même en produisant (néant), pas de période solaire
        d = compute(s, inputs(datetime(2026, 1, 15, 23), production=3000.0))
        self.assertFalse(d.en_periode_solaire)


class TestConsigneCible(unittest.TestCase):
    def test_jour_nuit(self):
        s = settings()
        d = compute(s, inputs(datetime(2026, 1, 15, 12)))
        self.assertEqual(d.consigne_cible, 20.0)
        d = compute(s, inputs(datetime(2026, 1, 15, 23)))
        self.assertEqual(d.consigne_cible, 16.0)

    def test_solaire_max_avec_jour(self):
        s = settings(consigne_solaire=22.0, consigne_jour=23.5)
        d = compute(s, inputs(datetime(2026, 1, 15, 12), production=800.0))
        self.assertEqual(d.consigne_cible, 23.5)

    def test_absent(self):
        s = settings()
        d = compute(s, inputs(datetime(2026, 1, 15, 12), mode_absent=True))
        self.assertTrue(d.mode_absent_effectif)
        self.assertEqual(d.consigne_cible, 12.0)

    def test_absent_prechauffe_vaut_jour(self):
        s = settings()
        # retour 18:00, préchauffe nécessaire ~ (20-15)/1*60+15 = 315 min
        d = compute(
            s,
            inputs(
                datetime(2026, 1, 15, 13, 0, 0),
                temp_int=15.0,
                mode_absent=True,
            ),
        )
        self.assertTrue(d.prechauffe)
        self.assertEqual(d.consigne_cible, 20.0)


class TestPrechauffe(unittest.TestCase):
    def test_minutes_necessaires(self):
        s = settings(vitesse_pac=2.0, marge_securite=15)
        d = compute(s, inputs(datetime(2026, 1, 15, 12), temp_int=17.0, source_pac=True))
        # écart 3 °C / 2 °C/h * 60 = 90 min + 15 = 105
        self.assertEqual(d.minutes_necessaires, 105)

    def test_vitesse_nulle(self):
        s = settings(vitesse_pac=0.0, marge_securite=20)
        d = compute(s, inputs(datetime(2026, 1, 15, 12), temp_int=17.0, source_pac=True))
        self.assertEqual(d.minutes_necessaires, 20)

    def test_debut_prechauffe_matin(self):
        s = settings(marge_securite=15)
        # (20-17)/1*60 + 15 = 195 min -> préchauffe dès 03:45
        d = compute(s, inputs(datetime(2026, 1, 15, 3, 44, 0), temp_int=17.0))
        self.assertFalse(d.prechauffe)
        d = compute(s, inputs(datetime(2026, 1, 15, 3, 45, 0), temp_int=17.0))
        self.assertTrue(d.prechauffe)

    def test_prechauffe_persiste_hors_periode_jour(self):
        s = settings()
        # drapeau levé, on reste en préchauffe tant que la période jour n'a pas commencé
        d = compute(s, inputs(datetime(2026, 1, 15, 5, 0, 0), prechauffe_flag=True))
        self.assertTrue(d.prechauffe)
        # drapeau levé mais période jour commencée : plus de préchauffe
        d = compute(s, inputs(datetime(2026, 1, 15, 12, 0, 0), prechauffe_flag=True))
        self.assertFalse(d.prechauffe)

    def test_debut_prechauffe_retour(self):
        s = settings()
        # retour 18:00, préchauffe 195 min -> 14:45
        d = compute(
            s,
            inputs(
                datetime(2026, 1, 15, 14, 44, 0),
                temp_int=17.0,
                mode_absent=True,
            ),
        )
        self.assertFalse(d.prechauffe)
        d = compute(
            s,
            inputs(
                datetime(2026, 1, 15, 14, 45, 0),
                temp_int=17.0,
                mode_absent=True,
            ),
        )
        self.assertTrue(d.prechauffe)

    def test_retour_passe(self):
        s = settings()
        d = compute(
            s,
            inputs(datetime(2026, 1, 15, 18, 30, 0), mode_absent=True),
        )
        self.assertTrue(d.retour_passe)
        self.assertFalse(d.mode_absent_effectif)
        # comme dans le YAML : le mode absent doit être éteint, consigne jour
        self.assertEqual(d.consigne_cible, 20.0)


class TestBesoinChauffe(unittest.TestCase):
    def test_hysteresis(self):
        s = settings(hysteresis=0.3)
        # sans demande : démarre sous 19,7 (20 - 0,3)
        self.assertFalse(
            compute(s, inputs(datetime(2026, 1, 15, 12), temp_int=19.7)).besoin_chauffe
        )
        self.assertTrue(
            compute(s, inputs(datetime(2026, 1, 15, 12), temp_int=19.6)).besoin_chauffe
        )
        # avec demande : s'arrête à partir de 20,3 (consigne + hystérésis)
        self.assertTrue(
            compute(
                s,
                inputs(datetime(2026, 1, 15, 12), temp_int=20.29, demande_flag=True),
            ).besoin_chauffe
        )
        self.assertFalse(
            compute(
                s,
                inputs(datetime(2026, 1, 15, 12), temp_int=20.31, demande_flag=True),
            ).besoin_chauffe
        )


class TestTargetTemperature(unittest.TestCase):
    def test_arrondi_au_pas(self):
        self.assertEqual(target_temperature(21.0), 21.0)
        self.assertEqual(target_temperature(21.3, step=0.5), 21.5)
        self.assertEqual(target_temperature(21.24, step=0.5), 21.0)
        self.assertEqual(target_temperature(21.7, step=1.0), 22.0)

    def test_bornes(self):
        self.assertEqual(target_temperature(2.0, minimum=5.0), 5.0)
        self.assertEqual(target_temperature(35.0, maximum=30.0), 30.0)

    def test_pas_invalide(self):
        self.assertEqual(target_temperature(21.0, step=0.0), 21.0)


if __name__ == "__main__":
    unittest.main()
