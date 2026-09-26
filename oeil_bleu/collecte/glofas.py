"""Copernicus GloFAS : débit prévu sur 10 jours au droit de chaque point surveillé.

Passe par l'API EWDS (clé gratuite, fichier ~/.cdsapirc ou variables CDSAPI_URL et
CDSAPI_KEY). Nécessite l'extra « glofas » (cdsapi, xarray, netCDF4).
La maille GloFAS fait environ 5 km : chaque point est rattaché à la maille
voisine qui porte le fleuve (voir maille_du_fleuve).
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


def maille_du_fleuve(da, lat_nom: str, lon_nom: str, lat: float, lon: float, rayon: int = 1):
    """Parmi la maille la plus proche et ses voisines, garde celle au plus fort débit moyen.

    Un point à 2 km du fleuve tombe souvent sur une maille de berge au débit quasi nul ;
    la maille voisine qui porte le fleuve est celle dont le débit domine.
    """
    import numpy as np

    i = int(np.abs(da[lat_nom].values - lat).argmin())
    j = int(np.abs(da[lon_nom].values - lon).argmin())
    fenetre = da.isel({lat_nom: slice(max(i - rayon, 0), i + rayon + 1),
                       lon_nom: slice(max(j - rayon, 0), j + rayon + 1)})
    autres = [d for d in fenetre.dims if d not in (lat_nom, lon_nom)]
    moyenne = (fenetre.mean(dim=autres) if autres else fenetre).transpose(lat_nom, lon_nom)
    a, b = np.unravel_index(int(np.nanargmax(moyenne.values)), moyenne.shape)
    return fenetre.isel({lat_nom: a, lon_nom: b}).squeeze()


def lire_netcdf(chemin: Path, points, jour: date) -> list[Observation]:
    import xarray as xr

    ds = xr.open_dataset(chemin)
    variable = "dis24" if "dis24" in ds else next(iter(ds.data_vars))
    lat_nom = "latitude" if "latitude" in ds.coords else "lat"
    lon_nom = "longitude" if "longitude" in ds.coords else "lon"
    base = datetime(jour.year, jour.month, jour.day, tzinfo=timezone.utc)
    resultat = []
    for infra_id, nom, lon, lat in points:
        serie = maille_du_fleuve(ds[variable], lat_nom, lon_nom, lat, lon)
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
