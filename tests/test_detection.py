"""Tests de l'étape 3 : règles de détection, puis calcul complet sur la base."""

from datetime import date, datetime, timedelta, timezone

import pytest

from oeil_bleu.collecte import Observation, executer
from oeil_bleu.detection import Faits, detecter, evaluer, liste_du_jour, tendance_glofas

JOUR = date(2024, 9, 10)
T = datetime(2024, 9, 8, 10, tzinfo=timezone.utc)


def faits(**kw):
    base = dict(infrastructure_id=1, nom="Pont", frequence_historique=0.02, eau=[], gdacs=[], debits=[])
    return Faits(**(base | kw))


def test_eau_hors_etendue_seule():
    a = evaluer(faits(eau=[(T, 1)]))
    assert (a.type, a.confiance, a.niveau) == ("eau_hors_etendue", 0.60, "moyen")


def test_confiance_renforcee():
    a = evaluer(faits(eau=[(T - timedelta(days=8), 1), (T, 1)], gdacs=[(2, 20.0)],
                      debits=[(24, 100.0), (120, 180.0)]))
    assert a.confiance == 0.95 and a.niveau == "eleve"
    assert a.elements["glofas_tendance"] == 1.8


def test_eau_habituelle_ignoree():
    assert evaluer(faits(frequence_historique=0.6, eau=[(T, 1)])) is None


def test_eau_retiree_ignoree():
    # De l'eau il y a 8 jours, sec au dernier passage : pas d'anomalie.
    assert evaluer(faits(eau=[(T - timedelta(days=8), 1), (T, 0)])) is None


def test_alerte_sans_satellite_reste_une_menace():
    a = evaluer(faits(frequence_historique=None, gdacs=[(3, 10.0)]))
    assert (a.type, a.niveau) == ("menace_crue", "faible")


def test_alerte_contredite_par_satellite():
    assert evaluer(faits(eau=[(T, 0)], gdacs=[(3, 10.0)])) is None


def test_alerte_verte_seule_ignoree():
    assert evaluer(faits(frequence_historique=None, gdacs=[(1, 10.0)])) is None


def test_tendance_glofas():
    assert tendance_glofas([(48, 30.0), (24, 20.0)]) == 1.5
    assert tendance_glofas([(24, 0.0), (48, 5.0)]) is None
    assert tendance_glofas([(24, 10.0)]) is None


def _point(conn, nom, lon, lat):
    return conn.execute(
        "INSERT INTO terre.infrastructure (zone_id, nom, type, geom, surveille)"
        " SELECT id, %s, 'pont', ST_SetSRID(ST_MakePoint(%s, %s), 4326), true"
        " FROM terre.zone WHERE code = 'bassin_niger' RETURNING id",
        (nom, lon, lat),
    ).fetchone()[0]


def _obs(cle, variable, valeur, quand, lon, lat, infra=None, **brut):
    return Observation(cle=cle, variable=variable, valeur=valeur, unite=None, observe_le=quand,
                       longitude=lon, latitude=lat, infrastructure_id=infra, brut=brut)


def test_detection_sur_la_base(conn):
    inonde = _point(conn, "Pont inondé", -4.0, 14.0)
    normal = _point(conn, "Pont normal", -3.0, 14.0)
    _point(conn, "Pont sans référence", -2.0, 14.0)
    executer(conn, "deafrica_wofs", lambda c: [
        _obs("f1", "frequence_eau_historique", 0.01, datetime(1984, 1, 1, tzinfo=timezone.utc), -4, 14, inonde),
        _obs("f2", "frequence_eau_historique", 0.01, datetime(1984, 1, 1, tzinfo=timezone.utc), -3, 14, normal),
        _obs("w1", "eau_observee", 1, T, -4, 14, inonde),
        _obs("w2", "eau_observee", 0, T, -3, 14, normal),
        _obs("w3", "eau_observee", 1, T - timedelta(days=40), -3, 14, normal),  # hors fenêtre
    ])
    executer(conn, "gdacs", lambda c: [
        _obs("g1", "alerte_inondation", 2, T - timedelta(days=30), -4.1, 14.1,
             todate="2024-09-09T00:00:00+00:00"),                               # encore active
        _obs("g2", "alerte_inondation", 3, T, 5.0, 14.0),                       # trop loin
    ])

    anomalies, sans_reference = detecter(conn, JOUR)
    assert [(a.infrastructure_id, a.type, a.confiance) for a in anomalies] == \
        [(inonde, "eau_hors_etendue", 0.75)]
    assert sans_reference == ["Pont sans référence"]

    # Un second calcul remplace le premier sans doublon et respecte les anomalies traitées.
    conn.execute("UPDATE terre.anomalie SET statut = 'transmise'")
    detecter(conn, JOUR)
    assert liste_du_jour(conn, JOUR) == [
        ("Pont inondé", "pont", "eau_hors_etendue", pytest.approx(0.75), "eleve", "transmise")]


def test_glofas_maille_du_fleuve():
    xr = pytest.importorskip("xarray")
    import numpy as np

    from oeil_bleu.collecte.glofas import maille_du_fleuve

    pas = np.array([24, 48], dtype="timedelta64[h]").astype("timedelta64[ns]")
    debit = np.zeros((2, 3, 3))
    debit[:, 0, 2] = [500.0, 600.0]   # le fleuve passe dans la maille nord-est
    da = xr.DataArray(debit, dims=("step", "latitude", "longitude"),
                      coords={"step": pas, "latitude": [14.05, 14.0, 13.95], "longitude": [-4.05, -4.0, -3.95]})
    serie = maille_du_fleuve(da, "latitude", "longitude", 14.0, -4.0)
    assert list(serie.values) == [500.0, 600.0]
