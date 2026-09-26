"""JRC Global Surface Water : fréquence historique de l'eau (1984-2021), monde entier, 30 m.

Référence mondiale pour les points hors d'Afrique, et hors de portée de Digital
Earth Africa. Pekel et al. (2016), Nature 540, 418-422. Nécessite l'extra « satellite ».
"""

from datetime import datetime, timezone

from .socle import Observation, points_surveilles
from .tuiles import tuile_gsw

URL = ("https://storage.googleapis.com/global-surface-water/downloads2021/occurrence/"
       "occurrence_{tuile}v1_4_2021.tif")
FIN_PERIODE = datetime(2021, 12, 31, 23, 59, 59, tzinfo=timezone.utc)


def lire_occurrence(href: str, lon: float, lat: float) -> int | None:
    import rasterio

    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"), rasterio.open(href) as src:
        ligne, col = src.index(lon, lat)
        if not (0 <= ligne < src.height and 0 <= col < src.width):
            return None
        valeur = int(next(src.sample([(lon, lat)]))[0])
        return valeur if valeur <= 100 else None    # au-delà de 100 : pas de donnée


def observations(points, lecteur=lire_occurrence) -> list[Observation]:
    resultat = []
    for infra_id, nom, lon, lat in points:
        href = URL.format(tuile=tuile_gsw(lon, lat))
        occurrence = lecteur(href, lon, lat)
        if occurrence is None:
            continue
        resultat.append(Observation(
            cle=f"gsw_1984_2021:{infra_id}", variable="frequence_eau_historique",
            valeur=occurrence / 100, unite="fraction 0-1", observe_le=FIN_PERIODE,
            longitude=lon, latitude=lat, infrastructure_id=infra_id,
            brut={"produit": "gsw_occurrence", "periode": "1984-2021", "href": href, "point": nom},
        ))
    return resultat


def collecter(conn, **_):
    """Référence fixe (1984-2021) : lue une seule fois par point."""
    connus = {r[0] for r in conn.execute(
        "SELECT infrastructure_id FROM terre.observation o JOIN terre.source s ON s.id = o.source_id"
        " WHERE s.code = 'jrc_gsw'")}
    return observations([p for p in points_surveilles(conn) if p[0] not in connus])
