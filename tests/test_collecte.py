"""Tests de l'étape 2 : lecture des formats de chaque source, sans accès réseau."""

import json
from datetime import date, datetime, timezone

import pytest

from oeil_bleu.collecte import Observation, deafrica, executer, firms, gdacs, glofas

EMPRISE = (-16.6, 4.0, 15.0, 24.0)

FIRMS_CSV = (
    "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,"
    "confidence,version,bright_ti5,frp,daynight\n"
    "13.51234,-5.98765,330.1,0.4,0.4,2024-09-10,112,N,VIIRS,n,2.0NRT,290.2,4.7,D\n"
    "14.00000,-4.00000,340.0,0.4,0.4,2024-09-10,1345,N,VIIRS,h,2.0NRT,295.0,,N\n"
)


def test_firms():
    obs = firms.lire_csv(FIRMS_CSV, "VIIRS_SNPP_NRT")
    assert len(obs) == 2
    assert obs[0].observe_le == datetime(2024, 9, 10, 1, 12, tzinfo=timezone.utc)
    assert obs[0].valeur == 4.7 and obs[0].brut["confiance"] == 0.6
    assert obs[1].valeur is None
    assert obs[0].cle != obs[1].cle


def _gdacs(lon, lat, eventid):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {"eventtype": "FL", "eventid": eventid, "episodeid": 1, "name": "Crue",
                       "alertlevel": "Orange", "country": "Mali", "iso3": "MLI",
                       "fromdate": "2024-09-01T00:00:00", "todate": "2024-09-15T00:00:00"},
    }


def test_gdacs_filtre_l_emprise():
    texte = json.dumps({"features": [_gdacs(-4.0, 14.0, 1), _gdacs(100.0, 14.0, 2)]})
    obs = gdacs.lire_geojson(texte, EMPRISE)
    assert [o.cle for o in obs] == ["FL:1:1"]
    assert obs[0].valeur == 2
    assert obs[0].observe_le.tzinfo is not None


def test_wofs_classement():
    assert deafrica.classer_wofs(128) == 1
    assert deafrica.classer_wofs(0) == 0
    assert deafrica.classer_wofs(64) is None      # nuage
    assert deafrica.classer_wofs(192) is None     # eau sous nuage : inexploitable
    assert deafrica.lien_https("s3://deafrica-services/a/b.tif") == \
        "https://deafrica-services.s3.af-south-1.amazonaws.com/a/b.tif"


def test_wofs_observations():
    items = [{"id": "sc1", "bbox": [-5, 13, -3, 15], "properties": {"datetime": "2024-09-10T10:30:00Z"},
              "assets": {"water": {"href": "s3://x/sc1.tif"}}}]
    points = [(1, "Pont A", -4.0, 14.0), (2, "Village B", -4.5, 13.5), (3, "Hors scène", 2.0, 14.0)]
    valeurs = {1: 128, 2: 64}
    lecteur = lambda href, lon, lat: valeurs[next(p[0] for p in points if p[2] == lon)]
    obs = deafrica.observations_wofs(items, points, lecteur)
    assert [(o.infrastructure_id, o.valeur) for o in obs] == [(1, 1)]


def test_lecture_pixel_reelle(tmp_path):
    rasterio = pytest.importorskip("rasterio")
    import numpy as np
    from rasterio.transform import from_origin
    from rasterio.warp import transform

    # Petite image en UTM 30N centrée sur (-4, 14), comme une scène WOfS.
    (x,), (y,) = transform("EPSG:4326", "EPSG:32630", [-4.0], [14.0])
    chemin = tmp_path / "wofs.tif"
    donnees = np.zeros((10, 10), dtype="uint8")
    donnees[5, 5] = 128
    with rasterio.open(chemin, "w", driver="GTiff", width=10, height=10, count=1, dtype="uint8",
                       crs="EPSG:32630", transform=from_origin(x - 165, y + 165, 30, 30), nodata=1) as dst:
        dst.write(donnees, 1)
    assert deafrica.lire_pixel(str(chemin), -4.0, 14.0) == 128
    assert deafrica.lire_pixel(str(chemin), 0.0, 14.0) is None


def test_glofas_netcdf(tmp_path):
    xr = pytest.importorskip("xarray")
    pytest.importorskip("netCDF4")
    import numpy as np

    pas = np.array([24, 48], dtype="timedelta64[h]").astype("timedelta64[ns]")
    ds = xr.Dataset(
        {"dis24": (("step", "latitude", "longitude"), np.array([[[10.0, 20.0]], [[11.0, 21.0]]]))},
        coords={"step": pas, "latitude": [14.0], "longitude": [-4.05, -3.95]},
    )
    chemin = tmp_path / "g.nc"
    ds.to_netcdf(chemin)
    obs = glofas.lire_netcdf(chemin, [(7, "Pont", -3.96, 14.0)], date(2024, 9, 10))
    assert [(o.valeur, o.brut["echeance_h"]) for o in obs] == [(20.0, 24), (21.0, 48)]
    assert obs[1].observe_le == datetime(2024, 9, 12, tzinfo=timezone.utc)
    req = glofas.requete(date(2024, 9, 10), [-5, 13, -3, 15])
    assert req["area"] == [15, -5, 13, -3] and req["day"] == ["10"]


def _obs(cle):
    return Observation(cle=cle, variable="alerte_inondation", valeur=2, unite=None,
                       observe_le=datetime(2024, 9, 1, tzinfo=timezone.utc), longitude=-4, latitude=14)


def test_collecte_rejouable(conn):
    assert executer(conn, "gdacs", lambda c: [_obs("a"), _obs("b")]) == 2
    assert executer(conn, "gdacs", lambda c: [_obs("b"), _obs("c")]) == 1
    assert conn.execute("SELECT count(*) FROM terre.observation").fetchone()[0] == 3
    statuts = conn.execute("SELECT statut, nb_nouvelles FROM terre.collecte ORDER BY id").fetchall()
    assert statuts == [("reussie", 2), ("reussie", 1)]


def test_collecte_en_echec_ne_laisse_rien(conn):
    def collecteur(c):
        yield _obs("a")
        raise RuntimeError("serveur indisponible")

    with pytest.raises(RuntimeError):
        executer(conn, "gdacs", collecteur)
    assert conn.execute("SELECT count(*) FROM terre.observation").fetchone()[0] == 0
    statut, erreur = conn.execute("SELECT statut, erreur FROM terre.collecte").fetchone()
    assert statut == "echouee" and "indisponible" in erreur
