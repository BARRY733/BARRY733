"""Radar Sentinel-1 : lecture, règles de détection, et le piège du sable sec."""

from datetime import date, datetime, timedelta, timezone

import pytest

from oeil_bleu.collecte import Observation, executer, sentinel1
from oeil_bleu.detection import MALUS_RADAR_SEUL, Faits, detecter, evaluer, radar_eau

T = datetime(2024, 9, 8, 6, tzinfo=timezone.utc)


def faits(**kw):
    base = dict(infrastructure_id=1, nom="Pont", frequence_historique=0.02, eau=[], gdacs=[], debits=[])
    return Faits(**(base | kw))


def test_decibels():
    assert sentinel1.en_db(0.01) == pytest.approx(-20.0)
    assert sentinel1.en_db(0) is None and sentinel1.en_db(None) is None


def test_eau_vue_au_radar_seulement():
    a = evaluer(faits(radar=[(T, -23.0)], reference_vv=-12.0))
    assert a.type == "eau_hors_etendue"
    assert a.confiance == pytest.approx(0.60 - MALUS_RADAR_SEUL)
    assert a.elements["radar"] == {"passages_avec_eau": 1, "chute_max_db": 11.0,
                                   "reference_db": -12.0, "optique_confirme": False}


def test_sable_sec_toujours_sombre_ignore():
    # Signal bas, mais habituel pour ce point : pas de chute, pas d'eau.
    assert radar_eau(faits(radar=[(T, -21.0)], reference_vv=-20.0)) == []
    assert evaluer(faits(radar=[(T, -21.0)], reference_vv=-20.0)) is None


def test_sans_reference_le_radar_est_ignore():
    assert evaluer(faits(radar=[(T, -25.0)], reference_vv=None)) is None


def test_radar_et_optique_se_renforcent():
    a = evaluer(faits(eau=[(T - timedelta(days=4), 1)], radar=[(T, -22.0)], reference_vv=-12.0))
    assert a.confiance == pytest.approx(0.75) and a.elements["radar"]["optique_confirme"] is True


def test_le_radar_ne_declare_jamais_un_point_sec():
    # Optique : eau le 4. Radar du 8 sans chute (végétation inondée ?) : l'alerte tient.
    a = evaluer(faits(eau=[(T - timedelta(days=4), 1)], radar=[(T, -11.0)], reference_vv=-12.0))
    assert a is not None and "radar" not in a.elements


def test_moyenne_du_carre(tmp_path):
    rasterio = pytest.importorskip("rasterio")
    import numpy as np
    from rasterio.transform import from_origin
    from rasterio.warp import transform

    (x,), (y,) = transform("EPSG:4326", "EPSG:32630", [-4.0], [14.0])
    donnees = np.full((20, 20), 0.1, dtype="float32")
    donnees[10, 10] = np.nan                               # un pixel invalide au centre
    chemin = tmp_path / "vv.tif"
    with rasterio.open(chemin, "w", driver="GTiff", width=20, height=20, count=1, dtype="float32",
                       crs="EPSG:32630", transform=from_origin(x - 300, y + 300, 30, 30), nodata=np.nan) as dst:
        dst.write(donnees, 1)
    assert sentinel1.lire_moyenne(str(chemin), -4.0, 14.0) == pytest.approx(0.1)
    assert sentinel1.lire_moyenne(str(chemin), 0.0, 14.0) is None


def test_observations_vv():
    items = [{"id": "s1a", "bbox": [-5, 13, -3, 15], "properties": {"datetime": "2024-09-08T06:00:00Z"},
              "assets": {"vv": {"href": "s3://x/vv.tif"}, "vh": {"href": "s3://x/vh.tif"}}},
             {"id": "sans_vv", "bbox": [-5, 13, -3, 15], "properties": {"datetime": "2024-09-08T06:00:00Z"},
              "assets": {"vh": {"href": "s3://x/vh.tif"}}}]
    obs = sentinel1.observations_vv(items, [(1, "Pont", -4.0, 14.0)], lambda h, lon, lat: 0.01)
    assert [(o.cle, o.valeur, o.unite) for o in obs] == [("s1_rtc:s1a:1", -20.0, "dB")]


def test_detection_radar_sur_la_base(conn):
    infra = conn.execute(
        "INSERT INTO terre.infrastructure (zone_id, nom, type, geom, surveille)"
        " SELECT id, 'Pont', 'pont', ST_SetSRID(ST_MakePoint(-4, 14), 4326), true"
        " FROM terre.zone WHERE code = 'bassin_niger' RETURNING id").fetchone()[0]
    vv = lambda i, quand, db: Observation(f"s1:{i}", "retrodiffusion_vv", db, "dB", quand, -4, 14, infra)
    # Référence : un passage tous les 12 jours pendant l'année précédente, vers -12 dB.
    historique = [vv(i, T - timedelta(days=30 + 12 * i), -12.0 + (i % 3) * 0.5) for i in range(25)]
    executer(conn, "deafrica_s1", lambda c: historique + [vv("crue", T, -22.0)])
    executer(conn, "deafrica_wofs", lambda c: [Observation(
        "f", "frequence_eau_historique", 0.01, None, datetime(1984, 1, 1, tzinfo=timezone.utc), -4, 14, infra)])

    anomalies, _ = detecter(conn, date(2024, 9, 10))
    assert len(anomalies) == 1
    radar = anomalies[0].elements["radar"]
    assert radar["optique_confirme"] is False and radar["chute_max_db"] >= 9.5
    assert anomalies[0].niveau == "moyen"
