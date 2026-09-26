"""GDACS : alertes catastrophes (crues, séismes, sécheresses, feux).

Format de l'API à confirmer au premier appel réel :
https://www.gdacs.org/gdacsapi/swagger/index.html
"""

import json
from datetime import date, datetime, timedelta, timezone

from . import commun

URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
TYPES = {"FL": "crue", "EQ": "seisme", "DR": "secheresse", "WF": "feu"}
GRAVITE = {"green": 1, "orange": 3, "red": 5}


def _date(texte: str | None) -> datetime | None:
    if not texte:
        return None
    d = datetime.fromisoformat(texte.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def collecter(cur, jours: int = 7, aujourd_hui: date | None = None) -> int:
    fin = aujourd_hui or date.today()
    url = (f"{URL}?eventlist={';'.join(TYPES)}&fromdate={fin - timedelta(days=jours)}"
           f"&todate={fin}&alertlevel=green;orange;red")
    brut = commun.telecharger(url)
    boite = commun.emprise(cur)
    preuve = commun.archiver(cur, "gdacs", brut, "json", url, "Liste d'événements GDACS, sans transformation")
    n = 0
    for f in json.loads(brut).get("features", []):
        p = f["properties"]
        type_ = TYPES.get(p.get("eventtype"))
        lon, lat = f["geometry"]["coordinates"][:2]
        if type_ is None or not commun.dans_emprise(lon, lat, boite):
            continue
        commun.enregistrer_evenement(
            cur, "gdacs", f"{p['eventtype']}-{p['eventid']}", type_, lon, lat,
            debut=_date(p.get("fromdate")), fin=_date(p.get("todate")),
            gravite=GRAVITE.get(str(p.get("alertlevel", "")).lower()),
            confiance="eleve", preuve_id=preuve,
        )
        n += 1
    return n
