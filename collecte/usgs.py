"""USGS : séismes signalés par le réseau officiel (magnitude 4,5 et plus)."""

import json
from datetime import date, datetime, timedelta, timezone

from . import commun

URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"


def gravite(magnitude: float) -> int:
    return min(5, max(1, int(magnitude) - 3))  # 4,x -> 1 ... 8+ -> 5


def collecter(cur, jours: int = 7, aujourd_hui: date | None = None) -> int:
    fin = aujourd_hui or date.today()
    o, s, e, n = commun.emprise(cur)
    url = (f"{URL}?format=geojson&starttime={fin - timedelta(days=jours)}&endtime={fin + timedelta(days=1)}"
           f"&minlatitude={s}&maxlatitude={n}&minlongitude={o}&maxlongitude={e}&minmagnitude=4.5")
    brut = commun.telecharger(url)
    preuve = commun.archiver(cur, "usgs", brut, "geojson", url, "Catalogue FDSN USGS, sans transformation")
    compte = 0
    for f in json.loads(brut).get("features", []):
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"][:2]
        quand = datetime.fromtimestamp(p["time"] / 1000, tz=timezone.utc)
        commun.enregistrer_evenement(
            cur, "usgs", f["id"], "seisme", lon, lat, debut=quand, fin=None,
            gravite=gravite(p["mag"]), confiance="eleve", preuve_id=preuve,
        )
        compte += 1
    return compte
