"""Collecteurs testés sur des réponses types, sans accès réseau."""

import json
from datetime import date

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from collecte import commun, eaux, firms, gdacs, usgs


@pytest.fixture
def reseau(monkeypatch, tmp_path):
    """Remplace le téléchargement par des réponses préparées, et range les
    données brutes dans un dossier temporaire."""
    reponses = {}
    appels = []

    def faux(url, donnees=None, essais=3):
        appels.append((url, donnees))
        for motif, corps in reponses.items():
            if motif in url or (donnees and motif in json.dumps(donnees)):
                return corps
        raise OSError(f"aucune réponse préparée pour {url}")

    monkeypatch.setattr(commun, "telecharger", faux)
    monkeypatch.setattr(commun, "DONNEES_BRUTES", tmp_path / "brut")
    monkeypatch.setenv("EMPRISE", "-17,10,0,20")
    return reponses, appels


def compter(cur, table):
    cur.execute(f"SELECT count(*) FROM {table}")
    return cur.fetchone()[0]


def test_gdacs_filtre_types_et_emprise(cur, reseau):
    reponses, _ = reseau
    reponses["gdacsapi"] = json.dumps({"features": [
        {"geometry": {"coordinates": [-12.2, 14.9]}, "properties": {
            "eventtype": "FL", "eventid": 1102, "alertlevel": "Orange",
            "fromdate": "2024-08-20T00:00:00", "todate": "2024-09-10T00:00:00"}},
        {"geometry": {"coordinates": [100.0, 14.0]}, "properties": {  # hors emprise
            "eventtype": "FL", "eventid": 1103, "alertlevel": "Red", "fromdate": "2024-08-20T00:00:00"}},
        {"geometry": {"coordinates": [-5.0, 12.0]}, "properties": {  # cyclone : non suivi
            "eventtype": "TC", "eventid": 7, "alertlevel": "Green", "fromdate": "2024-08-20T00:00:00"}},
    ]}).encode()
    assert gdacs.collecter(cur, aujourd_hui=date(2024, 9, 15)) == 1
    assert gdacs.collecter(cur, aujourd_hui=date(2024, 9, 15)) == 1  # sans doublon
    cur.execute("SELECT type, gravite, confiance, ref_externe FROM evenement")
    assert cur.fetchall() == [("crue", 3, "eleve", "FL-1102")]
    assert compter(cur, "preuve") == 2 and compter(cur, "evenement_preuve") == 2


def test_usgs(cur, reseau):
    reponses, appels = reseau
    reponses["earthquake.usgs.gov"] = json.dumps({"features": [
        {"id": "us7000abcd", "geometry": {"coordinates": [-8.4, 16.1, 10]},
         "properties": {"mag": 5.6, "time": 1726000000000}},
    ]}).encode()
    assert usgs.collecter(cur, aujourd_hui=date(2024, 9, 15)) == 1
    assert "minlongitude=-17.0" in appels[0][0] and "endtime=2024-09-16" in appels[0][0]
    cur.execute("SELECT type, gravite FROM evenement")
    assert cur.fetchone() == ("seisme", 2)


def test_firms_masque_la_cle(cur, reseau, monkeypatch):
    reponses, _ = reseau
    monkeypatch.setenv("FIRMS_MAP_KEY", "cle-secrete")
    reponses["firms.modaps"] = (
        "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,"
        "confidence,version,bright_ti5,frp,daynight\n"
        "13.51,-15.20,330.1,0.4,0.4,2024-09-14,134,N,VIIRS,h,2.0NRT,290.2,45.3,D\n"
        "13.60,-15.10,310.0,0.4,0.4,2024-09-14,1342,N,VIIRS,l,2.0NRT,288.0,2.1,D\n"
    ).encode()
    assert firms.collecter(cur) == 2
    cur.execute("SELECT gravite, confiance, to_char(debut AT TIME ZONE 'UTC', 'HH24:MI') FROM evenement ORDER BY gravite")
    assert cur.fetchall() == [(1, "a_confirmer", "13:42"), (3, "eleve", "01:34")]
    cur.execute("SELECT uri FROM preuve")
    assert "cle-secrete" not in cur.fetchone()[0]


def test_firms_sans_cle(cur, reseau, monkeypatch):
    monkeypatch.delenv("FIRMS_MAP_KEY", raising=False)
    with pytest.raises(RuntimeError, match="FIRMS_MAP_KEY"):
        firms.collecter(cur)


def raster(chemin, valeur, dtype="uint8", nodata=None):
    """Petite couche en EPSG:6933 (projection de Digital Earth Africa) couvrant le Sénégal oriental."""
    import rasterio.warp
    x, y = rasterio.warp.transform("EPSG:4326", "EPSG:6933", [-12.5], [15.5])
    with rasterio.open(chemin, "w", driver="GTiff", width=100, height=100, count=1, dtype=dtype,
                       crs="EPSG:6933", transform=from_origin(x[0], y[0], 1000, 1000), nodata=nodata) as f:
        f.write(np.full((1, 100, 100), valeur, dtype=dtype))
    return str(chemin)


def item(date_iso, **assets):
    return {"properties": {"datetime": date_iso}, "assets": {k: {"href": v} for k, v in assets.items()}}


def test_eaux(cur, reseau, tmp_path):
    reponses, _ = reseau
    cur.execute(
        "INSERT INTO infrastructure (nom, type, pays_iso, geom) VALUES "
        "('Pont de Bakel', 'pont', 'SEN', ST_SetSRID(ST_MakePoint(-12.1, 15.0), 4326))"
    )
    frequence = raster(tmp_path / "freq.tif", 0.05, "float32")
    mouille = raster(tmp_path / "wet.tif", 128)
    nuage = raster(tmp_path / "cloud.tif", 64)
    reponses["wofs_ls_summary_alltime"] = json.dumps({"features": [
        item("1984-01-01T00:00:00Z", frequency=frequence)]}).encode()
    reponses['"wofs_ls"'] = json.dumps({"features": [
        item("2024-09-05T10:40:00Z", wofs=mouille), item("2024-09-13T10:40:00Z", wofs=nuage)]}).encode()

    assert eaux.collecter(cur, aujourd_hui=date(2024, 9, 15)) == 2  # la scène nuageuse est ignorée
    assert eaux.collecter(cur, aujourd_hui=date(2024, 9, 15)) == 0  # rien de neuf
    cur.execute("SELECT nom, round(valeur::numeric, 2)::float FROM indicateur ORDER BY nom")
    assert cur.fetchall() == [("eau_observee", 1.0), ("frequence_eau_historique", 0.05)]


def test_eaux_point_hors_couche(tmp_path):
    assert eaux.echantillonner(raster(tmp_path / "r.tif", 128), 30.0, 0.0) is None


def test_href_s3():
    assert eaux._href({"href": "s3://deafrica-services/wofs_ls/x.tif"}) == eaux.S3 + "wofs_ls/x.tif"
