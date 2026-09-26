"""Sources mondiales lues sans catalogue : grilles, Sentinel-2, Global Surface Water,
comptages annuels WOfS, et référence historique sans biais."""

from datetime import date, datetime, timezone

import numpy as np
import pytest

from oeil_bleu.collecte import Observation, deafrica, executer, gsw, sentinel2
from oeil_bleu.collecte.tuiles import tuile_deafrica, tuile_gsw, tuiles_mgrs
from oeil_bleu.detection import detecter

pytest.importorskip("pyproj")

DOUNA = (1, "Pont de Douna", -5.903, 13.214)


def test_grilles():
    # Valeurs vérifiées contre les dépôts réels (septembre 2026).
    assert tuiles_mgrs(-5.903, 13.214) == ["30PSV", "29PRQ"]      # bord de zone : tuile voisine
    assert tuiles_mgrs(-4.0187, 5.3144)[0] == "30NUL"
    assert tuiles_mgrs(-43.2, -22.9)[0] == "23KPQ"
    assert tuile_deafrica(-5.903, 13.214) == "x175y094"
    assert tuile_gsw(-5.903, 13.214) == "10W_20N"
    assert tuile_gsw(4.83, 45.76) == "0E_50N" and tuile_gsw(-43.2, -22.9) == "50W_20S"


def test_classes_sentinel2():
    assert sentinel2.classer_scl(6) == 1
    assert [sentinel2.classer_scl(c) for c in (4, 5, 11)] == [0, 0, 0]
    assert [sentinel2.classer_scl(c) for c in (1, 2, 3, 7, 8, 9, 10)] == [None] * 7


def faux_lister(depot, prefixe):
    if prefixe.endswith("/29/P/RQ/2024/9/"):
        return [prefixe + "S2A_T29PRQ_20240913T104458_L2A/", prefixe + "S2B_T29PRQ_20240918T104458_L2A/",
                prefixe + "S2A_T29PRQ_20240903T104458_L2A/"]
    if prefixe.endswith("/30/P/SV/2024/9/"):
        return [prefixe + "S2A_T30PSV_20240913T104458_L2A/"]      # même acquisition, autre tuile
    return []


def test_sentinel2_sans_catalogue():
    classes = {"20240913": 6, "20240918": 9}
    lecteur = lambda href, lon, lat: None if "T29PRQ_20240903" in href else classes[href.split("_")[2][:8]]
    obs = sentinel2.observations([DOUNA], date(2024, 9, 10), date(2024, 9, 30), faux_lister, lecteur)
    # Le 13 : eau, compté une seule fois malgré les deux tuiles ; le 18 : nuage, ignoré ; le 3 : hors période.
    assert [(o.observe_le.day, o.valeur) for o in obs] == [(13, 1)]
    assert obs[0].brut["href"].endswith("S2A_T30PSV_20240913T104458_L2A/SCL.tif")


def test_gsw():
    obs = gsw.observations([DOUNA], lambda href, lon, lat: 36)
    assert obs[0].valeur == 0.36 and "occurrence_10W_20Nv1_4_2021.tif" in obs[0].brut["href"]
    assert gsw.observations([DOUNA], lambda *a: None) == []


def test_comptages_annuels():
    lister = lambda depot, prefixe: [f"{prefixe}{a}--P1Y/" for a in (2022, 2023, 2024)]
    lecteur = lambda href, coords: [2.0] if "count_wet" in href else [10.0]
    obs = deafrica.observations_annuelles([DOUNA], range(2023, 2025), lister, lecteur)
    assert [(o.brut["annee"], o.valeur, o.brut["passages_degages"]) for o in obs] == [(2023, 2.0, 10.0), (2024, 2.0, 10.0)]
    assert obs[0].brut["tuile"] == "x175y094"


def _point(conn):
    return conn.execute(
        "INSERT INTO terre.infrastructure (zone_id, nom, type, geom, surveille)"
        " SELECT id, 'Piste', 'piste', ST_SetSRID(ST_MakePoint(-4, 14), 4326), true"
        " FROM terre.zone WHERE code = 'bassin_niger' RETURNING id").fetchone()[0]


def _annee(i, annee, mouille, degage):
    return Observation(f"a{annee}", "eau_annuelle", mouille, None,
                       datetime(annee, 12, 31, tzinfo=timezone.utc), -4, 14, i,
                       {"annee": annee, "passages_degages": degage})


def test_reference_sans_l_annee_analysee(conn):
    i = _point(conn)
    # Sec de 2015 à 2023, inondé en 2024 : la référence d'un jour de 2024 doit ignorer 2024.
    annees = [_annee(i, a, 0, 20) for a in range(2015, 2024)] + [_annee(i, 2024, 18, 20)]
    executer(conn, "deafrica_wofs", lambda c: annees)
    executer(conn, "sentinel2", lambda c: [Observation(
        "s", "eau_observee", 1, None, datetime(2024, 9, 8, tzinfo=timezone.utc), -4, 14, i)])
    anomalies, _ = detecter(conn, date(2024, 9, 10))
    assert anomalies and anomalies[0].elements["reference"] == "wofs_annuel_2015-2023"
    assert anomalies[0].elements["frequence_historique"] == 0


def test_reference_gsw_hors_afrique(conn):
    i = _point(conn)
    executer(conn, "jrc_gsw", lambda c: [Observation(
        "g", "frequence_eau_historique", 0.02, None, gsw.FIN_PERIODE, -4, 14, i, {"produit": "gsw_occurrence"})])
    executer(conn, "sentinel2", lambda c: [Observation(
        "s", "eau_observee", 1, None, datetime(2024, 9, 8, tzinfo=timezone.utc), -4, 14, i)])
    anomalies, _ = detecter(conn, date(2024, 9, 10))
    assert anomalies[0].elements["reference"] == "gsw_occurrence"
    # Rejouer 2021 avec une référence qui s'arrête fin 2021 : c'est signalé.
    executer(conn, "sentinel2", lambda c: [Observation(
        "s21", "eau_observee", 1, None, datetime(2021, 9, 8, tzinfo=timezone.utc), -4, 14, i)])
    anomalies, _ = detecter(conn, date(2021, 9, 10))
    assert anomalies[0].elements["reference"] == "gsw_occurrence_inclut_la_periode"


def test_carte_sentinel2_en_codes_wofs():
    pytest.importorskip("matplotlib")
    from oeil_bleu.carte import EAU, SEC, en_codes_wofs

    scl = np.array([[6, 4, 5], [11, 9, 0]])
    assert en_codes_wofs(scl).tolist() == [[EAU, SEC, SEC], [SEC, 2, 2]]
