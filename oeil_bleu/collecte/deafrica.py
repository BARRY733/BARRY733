"""Digital Earth Africa : présence d'eau (WOfS, Landsat) sur chaque point surveillé.

Deux produits, lus pixel par pixel via le catalogue STAC public :
- wofs_ls : une observation par passage satellite (eau / sec / masqué) ;
- wofs_ls_summary_alltime : fréquence historique de l'eau, référence de la détection.
Nécessite l'extra « satellite » (rasterio).
"""

import json
import urllib.request
from datetime import date, datetime, timedelta, timezone

from .socle import AGENT, Observation, points_surveilles

STAC = "https://explorer.digitalearth.africa/stac/search"

# Octets du produit WOfS : 0 = sec et dégagé, 128 = eau et dégagé ; tout autre
# bit signale un masque (nuage, ombre, pente, absence de donnée…).
SEC, EAU = 0, 128


def classer_wofs(octet: int) -> int | None:
    """1 = eau, 0 = sec, None = observation inexploitable."""
    if octet == EAU:
        return 1
    if octet == SEC:
        return 0
    return None


def lien_https(href: str) -> str:
    if href.startswith("s3://"):
        seau, _, cle = href[5:].partition("/")
        return f"https://{seau}.s3.af-south-1.amazonaws.com/{cle}"
    return href


def chercher(collection: str, emprise: list[float], periode: str | None, limite: int = 200) -> list[dict]:
    corps = {"collections": [collection], "bbox": emprise, "limit": limite}
    if periode:
        corps["datetime"] = periode
    requete = urllib.request.Request(
        STAC, data=json.dumps(corps).encode(), method="POST",
        headers={"Content-Type": "application/json", "User-Agent": AGENT},
    )
    with urllib.request.urlopen(requete, timeout=120) as r:
        return json.load(r).get("features", [])


def lire_pixel(href: str, lon: float, lat: float) -> float | None:
    import rasterio
    from rasterio.warp import transform

    with rasterio.open(lien_https(href)) as src:
        xs, ys = transform("EPSG:4326", src.crs, [lon], [lat])
        ligne, col = src.index(xs[0], ys[0])
        if not (0 <= ligne < src.height and 0 <= col < src.width):
            return None
        valeur = next(src.sample([(xs[0], ys[0])]))[0]
        if src.nodata is not None and valeur == src.nodata:
            return None
        return float(valeur)


def _dans(item: dict, lon: float, lat: float) -> bool:
    o, s, e, n = item["bbox"][:4]
    return o <= lon <= e and s <= lat <= n


def _actif(item: dict, prefere: str) -> str:
    actifs = item["assets"]
    if prefere in actifs:
        return actifs[prefere]["href"]
    for a in actifs.values():
        if "tiff" in a.get("type", ""):
            return a["href"]
    raise KeyError(f"aucune image dans l'élément {item.get('id')}")


def observations_wofs(items: list[dict], points, lecteur=lire_pixel) -> list[Observation]:
    resultat = []
    for item in items:
        quand = datetime.fromisoformat(item["properties"]["datetime"].replace("Z", "+00:00"))
        href = _actif(item, "water")
        for infra_id, nom, lon, lat in points:
            if not _dans(item, lon, lat):
                continue
            octet = lecteur(href, lon, lat)
            etat = None if octet is None else classer_wofs(int(octet))
            if etat is None:
                continue
            resultat.append(Observation(
                cle=f"wofs_ls:{item['id']}:{infra_id}",
                variable="eau_observee", valeur=etat, unite="1 eau, 0 sec",
                observe_le=quand, longitude=lon, latitude=lat,
                infrastructure_id=infra_id, brut={"element": item["id"], "point": nom, "href": href},
            ))
    return resultat


def observations_frequence(items: list[dict], points, lecteur=lire_pixel) -> list[Observation]:
    resultat = []
    for infra_id, nom, lon, lat in points:
        item = next((i for i in items if _dans(i, lon, lat)), None)
        if item is None:
            continue
        href = _actif(item, "frequency")
        freq = lecteur(href, lon, lat)
        if freq is None:
            continue
        resultat.append(Observation(
            cle=f"wofs_alltime:{item['id']}:{infra_id}",
            variable="frequence_eau_historique", valeur=freq, unite="fraction 0-1",
            observe_le=datetime.fromisoformat(item["properties"]["datetime"].replace("Z", "+00:00"))
            if item["properties"].get("datetime") else datetime(1984, 1, 1, tzinfo=timezone.utc),
            longitude=lon, latitude=lat, infrastructure_id=infra_id,
            brut={"element": item["id"], "point": nom, "href": href},
        ))
    return resultat


def _emprise_points(points, marge: float = 0.01) -> list[float]:
    lons = [p[2] for p in points]
    lats = [p[3] for p in points]
    return [min(lons) - marge, min(lats) - marge, max(lons) + marge, max(lats) + marge]


def collecter(conn, jours: int = 16):
    points = points_surveilles(conn)
    if not points:
        return []
    emprise = _emprise_points(points)
    fin = date.today()
    periode = f"{fin - timedelta(days=jours)}T00:00:00Z/{fin}T23:59:59Z"
    resultat = observations_frequence(chercher("wofs_ls_summary_alltime", emprise, None), points)
    resultat += observations_wofs(chercher("wofs_ls", emprise, periode), points)
    return resultat
