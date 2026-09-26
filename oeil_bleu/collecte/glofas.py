"""Copernicus GloFAS : débit prévu sur 10 jours au droit de chaque point surveillé.

Passe par l'API EWDS (clé gratuite, fichier ~/.cdsapirc ou variables CDSAPI_URL et
CDSAPI_KEY). Nécessite l'extra « glofas » (cdsapi, xarray, netCDF4).
Limite : la maille GloFAS fait environ 5 km ; le point doit être rattaché
à la maille du cours d'eau, ce que fera l'étape 3.
"""

import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .socle import Observation, points_surveilles

JEU = "cems-glofas-forecast"
ECHEANCES = [str(h) for h in range(24, 241, 24)]


def requete(jour: date, emprise: list[float]) -> dict:
    ouest, sud, est, nord = emprise
    return {
        "system_version": ["operational"],
        "hydrological_model": ["lisflood"],
        "product_type": ["control_forecast"],
        "variable": "river_discharge_in_the_last_24_hours",
        "year": [f"{jour:%Y}"],
        "month": [f"{jour:%m}"],
        "day": [f"{jour:%d}"],
        "leadtime_hour": ECHEANCES,
        "data_format": "netcdf",
        "download_format": "unarchived",
        "area": [nord, ouest, sud, est],
    }


def lire_netcdf(chemin: Path, points, jour: date) -> list[Observation]:
    import xarray as xr

    ds = xr.open_dataset(chemin)
    variable = "dis24" if "dis24" in ds else next(iter(ds.data_vars))
    lat_nom = "latitude" if "latitude" in ds.coords else "lat"
    lon_nom = "longitude" if "longitude" in ds.coords else "lon"
    base = datetime(jour.year, jour.month, jour.day, tzinfo=timezone.utc)
    resultat = []
    for infra_id, nom, lon, lat in points:
        serie = ds[variable].sel({lat_nom: lat, lon_nom: lon}, method="nearest").squeeze()
        dim = next((d for d in ("step", "forecast_period") if d in serie.dims), None)
        if dim is None:
            raise RuntimeError(f"dimension d'échéance introuvable : {serie.dims}")
        for i, pas in enumerate(serie[dim].values):
            valeur = float(serie.isel({dim: i}))
            heures = int(pas.astype("timedelta64[h]").astype(int))
            resultat.append(Observation(
                cle=f"{jour}:{heures}:{infra_id}",
                variable="debit_prevu", valeur=valeur, unite="m3/s",
                observe_le=base + timedelta(hours=heures),
                longitude=lon, latitude=lat, infrastructure_id=infra_id,
                brut={"prevision_du": str(jour), "echeance_h": heures, "point": nom},
            ))
    ds.close()
    return resultat


def collecter(conn, jour: date | None = None):
    import cdsapi

    points = points_surveilles(conn)
    if not points:
        return []
    jour = jour or date.today() - timedelta(days=1)
    lons, lats = [p[2] for p in points], [p[3] for p in points]
    emprise = [min(lons) - 0.1, min(lats) - 0.1, max(lons) + 0.1, max(lats) + 0.1]
    with tempfile.TemporaryDirectory() as dossier:
        chemin = Path(dossier) / "glofas.nc"
        cdsapi.Client().retrieve(JEU, requete(jour, emprise), str(chemin))
        return lire_netcdf(chemin, points, jour)
