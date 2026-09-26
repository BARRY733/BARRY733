"""Où trouver l'image d'un point, sans catalogue : grilles des dépôts publics.

Les dépôts d'images (Sentinel-2 sur AWS, Digital Earth Africa, Global Surface
Water) rangent leurs fichiers selon des grilles fixes. Calculer la case d'un
point suffit donc pour lire l'image, même quand les catalogues en ligne sont
inaccessibles.
"""

import math
import re
import urllib.parse
import urllib.request
from functools import lru_cache

from .socle import AGENT

# --- Sentinel-2 : grille MGRS (UTM + carrés de 100 km) ---------------------------------

BANDES = "CDEFGHJKLMNPQRSTUVWX"
COLONNES = {0: "STUVWXYZ", 1: "ABCDEFGH", 2: "JKLMNPQR"}
LIGNES = "ABCDEFGHJKLMNPQRSTUV"


@lru_cache(maxsize=None)
def _vers_utm(zone: int, nord: bool):
    from pyproj import Transformer

    return Transformer.from_crs("EPSG:4326", f"EPSG:{(32600 if nord else 32700) + zone}", always_xy=True)


def tuile_mgrs(lon: float, lat: float, zone: int) -> str | None:
    e, n = _vers_utm(zone, lat >= 0).transform(lon, lat)
    i = int(e // 100000) - 1
    if not 0 <= i < 8 or not -80 <= lat < 84:
        return None
    colonne = COLONNES[zone % 3][i]
    ligne = LIGNES[(int(n // 100000) + (5 if zone % 2 == 0 else 0)) % 20]
    return f"{zone}{BANDES[min(int((lat + 80) // 8), 19)]}{colonne}{ligne}"


def tuiles_mgrs(lon: float, lat: float) -> list[str]:
    """Tuile du point dans sa zone UTM, puis dans les zones voisines : près d'un bord
    de zone, Sentinel-2 ne couvre le point que par une tuile de la zone d'à côté."""
    z = int((lon + 180) // 6) % 60 + 1
    return [t for zz in (z, z - 1, z + 1) if 1 <= zz <= 60 and (t := tuile_mgrs(lon, lat, zz))]


# --- Digital Earth Africa : grille EASE 2.0 (EPSG:6933) de 96 km ------------------------

def tuile_deafrica(lon: float, lat: float) -> str:
    from pyproj import Transformer

    x, y = Transformer.from_crs("EPSG:4326", "EPSG:6933", always_xy=True).transform(lon, lat)
    return f"x{int(x // 96000) + 181:03d}y{int(y // 96000) + 77:03d}"


# --- Global Surface Water : tuiles de 10° nommées par leur coin nord-ouest ---------------

def tuile_gsw(lon: float, lat: float) -> str:
    ouest = int(math.floor(lon / 10) * 10)
    nord = int(math.floor(lat / 10) * 10 + 10)
    return f"{abs(ouest)}{'E' if ouest >= 0 else 'W'}_{abs(nord)}{'N' if nord >= 0 else 'S'}"


# --- Lecture des dépôts S3 publics --------------------------------------------------------

def lister(depot: str, prefixe: str, dossiers: bool = True) -> list[str]:
    """Sous-dossiers (ou fichiers) d'un préfixe dans un dépôt S3 public, toutes pages comprises."""
    resultats, jeton = [], None
    while True:
        params = {"list-type": "2", "prefix": prefixe}
        if dossiers:
            params["delimiter"] = "/"
        if jeton:
            params["continuation-token"] = jeton
        requete = urllib.request.Request(f"{depot}/?{urllib.parse.urlencode(params)}",
                                         headers={"User-Agent": AGENT})
        with urllib.request.urlopen(requete, timeout=60) as r:
            xml = r.read().decode()
        motif = r"<CommonPrefixes><Prefix>([^<]*)</Prefix>" if dossiers else r"<Key>([^<]*)</Key>"
        resultats += re.findall(motif, xml)
        suite = re.search(r"<NextContinuationToken>([^<]*)</NextContinuationToken>", xml)
        if not suite:
            return resultats
        jeton = suite.group(1)
