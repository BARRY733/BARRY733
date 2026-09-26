"""Digital Earth Africa : radar Sentinel-1 (s1_rtc), rétrodiffusion VV sur chaque point surveillé.

Le radar voit à travers les nuages. Un pixel radar isolé est très bruité
(chatoiement) : on moyenne un carré de 5 × 5 pixels (100 m de côté) avant de
passer en décibels. Nécessite l'extra « satellite » (rasterio).
"""

import math
from datetime import date, datetime, timedelta, timezone

from .deafrica import _dans, _emprise_points, chercher, lien_https
from .socle import Observation, points_surveilles

COTE = 5


def en_db(puissance: float) -> float | None:
    return 10 * math.log10(puissance) if puissance and puissance > 0 else None


def lire_moyenne(href: str, lon: float, lat: float, cote: int = COTE) -> float | None:
    """Moyenne des valeurs linéaires (gamma0) sur un carré centré sur le point."""
    import numpy as np
    import rasterio
    from rasterio.warp import transform
    from rasterio.windows import Window

    with rasterio.open(lien_https(href)) as src:
        xs, ys = transform("EPSG:4326", src.crs, [lon], [lat])
        ligne, col = src.index(xs[0], ys[0])
        demi = cote // 2
        if not (demi <= ligne < src.height - demi and demi <= col < src.width - demi):
            return None
        bloc = src.read(1, window=Window(col - demi, ligne - demi, cote, cote), masked=True).astype("float64")
        valeurs = bloc.compressed()
        valeurs = valeurs[np.isfinite(valeurs) & (valeurs > 0)]
        # Au moins les deux tiers du carré doivent être valides.
        if valeurs.size < (cote * cote) * 2 / 3:
            return None
        return float(valeurs.mean())


def observations_vv(items: list[dict], points, lecteur=lire_moyenne) -> list[Observation]:
    resultat = []
    for item in items:
        if "vv" not in item.get("assets", {}):
            continue
        href = item["assets"]["vv"]["href"]
        quand = datetime.fromisoformat(item["properties"]["datetime"].replace("Z", "+00:00"))
        for infra_id, nom, lon, lat in points:
            if not _dans(item, lon, lat):
                continue
            db = en_db(lecteur(href, lon, lat))
            if db is None:
                continue
            resultat.append(Observation(
                cle=f"s1_rtc:{item['id']}:{infra_id}",
                variable="retrodiffusion_vv", valeur=round(db, 2), unite="dB",
                observe_le=quand, longitude=lon, latitude=lat, infrastructure_id=infra_id,
                brut={"element": item["id"], "point": nom, "href": href,
                      "orbite": item["properties"].get("sat:orbit_state")},
            ))
    return resultat


# --- Lecture directe du dépôt (sans catalogue) ------------------------------------------

DEPOT = "https://deafrica-sentinel-1.s3.af-south-1.amazonaws.com"


def tuile_s1(lon: float, lat: float) -> str:
    """Carré de 1° nommé par son coin sud-ouest, comme dans le dépôt (ex. N13W006)."""
    la, lo = math.floor(lat), math.floor(lon)
    return f"{'N' if la >= 0 else 'S'}{abs(la):02d}{'E' if lo >= 0 else 'W'}{abs(lo):03d}"


def lire_moyenne_masquee(href_vv: str, lon: float, lat: float, cote: int = COTE) -> float | None:
    """Moyenne VV sur un carré de 5 × 5 pixels, en ne gardant que les pixels que le
    masque du produit déclare valides (1) : ni absence de donnée, ni repliement, ni ombre."""
    import numpy as np
    import rasterio
    from rasterio.windows import Window

    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
        with rasterio.open(href_vv) as vv, rasterio.open(href_vv.replace("_VV.tif", "_MASK.tif")) as masque:
            ligne, col = vv.index(lon, lat)
            demi = cote // 2
            if not (demi <= ligne < vv.height - demi and demi <= col < vv.width - demi):
                return None
            fenetre = Window(col - demi, ligne - demi, cote, cote)
            valeurs = vv.read(1, window=fenetre).astype("float64")
            valides = (masque.read(1, window=fenetre) == 1) & np.isfinite(valeurs) & (valeurs > 0)
            if valides.sum() < cote * cote * 2 / 3:
                return None
            return float(valeurs[valides].mean())


def observations_depot(points, debut: date, fin: date, lister=None, lecteur=lire_moyenne_masquee):
    from .sentinel2 import mois
    from .tuiles import lister as lister_s3

    lister = lister or lister_s3
    resultat = []
    for infra_id, nom, lon, lat in points:
        tuile = tuile_s1(lon, lat)
        for a, m in mois(debut, fin):
            for jour_dossier in lister(DEPOT, f"s1_rtc/{tuile}/{a}/{m:02d}/"):
                jour = date(a, m, int(jour_dossier.rstrip("/").rsplit("/", 1)[-1]))
                if not debut <= jour <= fin:
                    continue
                for scene_dossier in lister(DEPOT, jour_dossier):
                    scene = scene_dossier.rstrip("/").rsplit("/", 1)[-1]
                    href = f"{DEPOT}/{scene_dossier}s1_rtc_{scene}_{tuile}_{jour:%Y_%m_%d}_VV.tif"
                    db = en_db(lecteur(href, lon, lat))
                    if db is None:
                        continue
                    resultat.append(Observation(
                        cle=f"s1_rtc:{tuile}:{jour}:{scene}:{infra_id}",
                        variable="retrodiffusion_vv", valeur=round(db, 2), unite="dB",
                        observe_le=datetime(jour.year, jour.month, jour.day, tzinfo=timezone.utc),
                        longitude=lon, latitude=lat, infrastructure_id=infra_id,
                        brut={"scene": scene, "tuile": tuile, "href": href, "point": nom},
                    ))
    return resultat


def collecter(conn, jours: int = 16, debut: date | None = None, fin: date | None = None):
    """Par défaut les 16 derniers jours. La première fois, collecter une année
    (--debut/--fin) pour que chaque point ait sa référence radar."""
    points = points_surveilles(conn)
    if not points:
        return []
    fin = fin or date.today()
    debut = debut or fin - timedelta(days=jours)
    return observations_depot(points, debut, fin)
