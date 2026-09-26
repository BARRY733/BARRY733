"""Digital Earth Africa : surfaces en eau (WOfS) sur chaque point surveillé.

Deux mesures par point :
- frequence_eau_historique : part des observations où le pixel était en eau
  depuis le début des archives Landsat (produit wofs_ls_summary_alltime) ;
- eau_observee : 1 si le pixel est en eau sur une scène récente, 0 s'il est
  sec ; les scènes nuageuses ou indéterminées sont ignorées (produit wofs_ls).

L'étape 3 (détection) compare la seconde à la première.
Noms de collections et d'assets à confirmer au premier appel réel :
https://explorer.digitalearth.africa/stac
"""

import json
from datetime import date, datetime, timedelta, timezone

from . import commun

STAC = "https://explorer.digitalearth.africa/stac/search"
S3 = "https://deafrica-services.s3.af-south-1.amazonaws.com/"
EAU, SEC = 128, 0  # valeurs WOfS : eau claire, sol sec clair ; tout le reste est masqué


def _href(asset: dict) -> str:
    h = asset["href"]
    return S3 + h.removeprefix("s3://deafrica-services/") if h.startswith("s3://") else h


def echantillonner(href: str, lon: float, lat: float) -> float | None:
    """Valeur du pixel sous le point (lon, lat), quel que soit le système de la couche."""
    import rasterio
    from rasterio.warp import transform

    with rasterio.open(href) as couche:
        xs, ys = transform("EPSG:4326", couche.crs, [lon], [lat])
        ligne, col = couche.index(xs[0], ys[0])
        if not (0 <= ligne < couche.height and 0 <= col < couche.width):
            return None
        valeur = float(next(couche.sample([(xs[0], ys[0])]))[0])
        return None if couche.nodata is not None and valeur == couche.nodata else valeur


def _chercher(collection: str, lon: float, lat: float, debut=None, fin=None) -> bytes:
    requete = {"collections": [collection], "intersects": {"type": "Point", "coordinates": [lon, lat]}, "limit": 50}
    if debut:
        requete["datetime"] = f"{debut}T00:00:00Z/{fin}T23:59:59Z"
    return commun.telecharger(STAC, donnees=requete)


def _enregistrer(cur, point_id: int, nom: str, valeur: float, unite: str, quand: datetime, preuve: int,
                 ressource: str) -> bool:
    cur.execute(
        "INSERT INTO terre.indicateur (infrastructure_id, preuve_id, nom, valeur, unite, mesure_le, ressource) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (point_id, preuve, nom, valeur, unite, quand, ressource),
    )
    return cur.rowcount == 1


def _date_item(item: dict) -> datetime:
    return datetime.fromisoformat(item["properties"]["datetime"].replace("Z", "+00:00"))


def collecter(cur, jours: int = 16, aujourd_hui: date | None = None) -> int:
    fin = aujourd_hui or date.today()
    cur.execute("SELECT id, nom, ST_X(geom), ST_Y(geom) FROM terre.infrastructure WHERE surveille ORDER BY id")
    compte = 0
    for point_id, nom, lon, lat in cur.fetchall():
        # Référence historique : calculée une fois, stable.
        cur.execute(
            "SELECT 1 FROM terre.indicateur WHERE infrastructure_id = %s AND nom = 'frequence_eau_historique'",
            (point_id,),
        )
        if cur.fetchone() is None:
            brut = _chercher("wofs_ls_summary_alltime", lon, lat)
            items = json.loads(brut).get("features", [])
            if items:
                item = items[0]
                preuve = commun.archiver(cur, "deafrica", brut, "json", STAC,
                                         f"Pixel WOfS summary alltime (frequency) sous « {nom} »")
                href = _href(item["assets"]["frequency"])
                v = echantillonner(href, lon, lat)
                if v is not None:
                    compte += _enregistrer(cur, point_id, "frequence_eau_historique", v, "ratio",
                                           _date_item(item), preuve, href)

        # Observations récentes.
        brut = _chercher("wofs_ls", lon, lat, fin - timedelta(days=jours), fin)
        items = json.loads(brut).get("features", [])
        if not items:
            continue
        preuve = commun.archiver(cur, "deafrica", brut, "json", STAC,
                                 f"Pixel WOfS (wofs) sous « {nom} », {EAU}=eau, {SEC}=sec, autres valeurs ignorées")
        for item in items:
            href = _href(item["assets"]["wofs"])
            v = echantillonner(href, lon, lat)
            if v in (EAU, SEC):
                compte += _enregistrer(cur, point_id, "eau_observee", 1.0 if v == EAU else 0.0, "booleen",
                                       _date_item(item), preuve, href)
    return compte
