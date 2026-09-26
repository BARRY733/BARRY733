"""GDACS : alertes d'inondation touchant l'emprise du pilote.

API publique : https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH
"""

import json
from datetime import date, datetime, timedelta, timezone

from .socle import Observation, emprise_pilote, telecharger

URL = ("https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
       "?eventlist=FL&fromDate={debut}&toDate={fin}")
NIVEAUX = {"Green": 1, "Orange": 2, "Red": 3}


def _date(texte: str) -> datetime:
    d = datetime.fromisoformat(texte)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def lire_geojson(texte: str, emprise: tuple[float, float, float, float]) -> list[Observation]:
    ouest, sud, est, nord = emprise
    observations = []
    for f in json.loads(texte).get("features", []):
        if (f.get("geometry") or {}).get("type") != "Point":
            continue
        lon, lat = f["geometry"]["coordinates"][:2]
        if not (ouest <= lon <= est and sud <= lat <= nord):
            continue
        p = f["properties"]
        observations.append(Observation(
            cle=f"{p['eventtype']}:{p['eventid']}:{p.get('episodeid', 0)}",
            variable="alerte_inondation",
            valeur=NIVEAUX.get(p.get("alertlevel")),
            unite="niveau GDACS (1 vert, 2 orange, 3 rouge)",
            observe_le=_date(p["fromdate"]),
            longitude=lon,
            latitude=lat,
            brut={k: p.get(k) for k in ("name", "description", "alertlevel", "country",
                                        "iso3", "fromdate", "todate", "url")},
        ))
    return observations


def collecter(conn, jours: int = 14, debut: date | None = None, fin: date | None = None):
    """Par défaut les 14 derniers jours ; debut et fin pour une saison passée (test à blanc)."""
    fin = fin or date.today()
    debut = debut or fin - timedelta(days=jours)
    texte = telecharger(URL.format(debut=debut, fin=fin)).decode()
    # GDACS répond vide (ou 204) quand aucun événement n'existe sur la période.
    if not texte.strip():
        return []
    return lire_geojson(texte, emprise_pilote(conn))
