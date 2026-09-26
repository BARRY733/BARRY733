"""Sentinel-2 (ESA Copernicus), eau observée sur chaque point surveillé, partout dans le monde.

Lit la classification de surface (SCL, 20 m) des produits L2A « collection 1 » du
dépôt public AWS Earth Search, sans catalogue : la case du point se calcule
(grille MGRS). Un passage tous les 2 à 5 jours. Nécessite l'extra « satellite ».
"""

import re
from datetime import date, datetime, timedelta, timezone

from .socle import Observation, points_surveilles
from .tuiles import lister, tuiles_mgrs

DEPOT = "https://e84-earth-search-sentinel-data.s3.us-west-2.amazonaws.com"
COLLECTION = "sentinel-2-c1-l2a"
SCENE = re.compile(r"(S2[ABC])_T(\w{5})_(\d{8}T\d{6})_L2A/$")

# Classes SCL : 6 = eau ; 4 végétation, 5 sol nu, 11 neige = surface dégagée sans eau.
# Tout le reste (nuages, ombres, cirrus, défauts, pixels sombres) est inexploitable.
EAU, SEC = {6}, {4, 5, 11}


def classer_scl(classe: int) -> int | None:
    if classe in EAU:
        return 1
    if classe in SEC:
        return 0
    return None


def mois(debut: date, fin: date):
    a, m = debut.year, debut.month
    while (a, m) <= (fin.year, fin.month):
        yield a, m
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)


def scenes(tuile: str, debut: date, fin: date, lister=lister) -> list[tuple[datetime, str, str]]:
    """(date d'acquisition, capteur, dossier) des scènes d'une tuile sur la période."""
    zone, bande, carre = tuile[:-3], tuile[-3], tuile[-2:]
    resultat = []
    for a, m in mois(debut, fin):
        for dossier in lister(DEPOT, f"{COLLECTION}/{zone}/{bande}/{carre}/{a}/{m}/"):
            trouve = SCENE.search(dossier)
            if not trouve:
                continue
            quand = datetime.strptime(trouve.group(3), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
            if debut <= quand.date() <= fin:
                resultat.append((quand, trouve.group(1), dossier))
    return resultat


def lire_classe(href: str, lon: float, lat: float) -> int | None:
    import rasterio
    from rasterio.warp import transform

    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"), rasterio.open(href) as src:
        xs, ys = transform("EPSG:4326", src.crs, [lon], [lat])
        ligne, col = src.index(xs[0], ys[0])
        if not (0 <= ligne < src.height and 0 <= col < src.width):
            return None
        valeur = int(next(src.sample([(xs[0], ys[0])]))[0])
        return None if valeur == 0 else valeur    # 0 : hors de la fauchée du satellite


def observations(points, debut: date, fin: date, lister=lister, lecteur=lire_classe) -> list[Observation]:
    resultat, vus = [], set()
    for infra_id, nom, lon, lat in points:
        for tuile in tuiles_mgrs(lon, lat):
            for quand, capteur, dossier in scenes(tuile, debut, fin, lister):
                # Une même acquisition peut figurer dans deux tuiles qui se chevauchent.
                cle = f"s2:{quand:%Y%m%dT%H%M}:{infra_id}"
                if cle in vus:
                    continue
                href = f"{DEPOT}/{dossier}SCL.tif"
                classe = lecteur(href, lon, lat)
                if classe is None:
                    continue
                vus.add(cle)
                etat = classer_scl(classe)
                if etat is None:
                    continue
                resultat.append(Observation(
                    cle=cle, variable="eau_observee", valeur=etat, unite="1 eau, 0 sec",
                    observe_le=quand, longitude=lon, latitude=lat, infrastructure_id=infra_id,
                    brut={"capteur": "sentinel-2", "satellite": capteur, "tuile": tuile,
                          "classe_scl": classe, "href": href, "point": nom},
                ))
    return resultat


def collecter(conn, jours: int = 16, debut: date | None = None, fin: date | None = None):
    points = points_surveilles(conn)
    if not points:
        return []
    fin = fin or date.today()
    debut = debut or fin - timedelta(days=jours)
    return observations(points, debut, fin)
