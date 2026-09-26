"""NASA FIRMS : foyers actifs VIIRS sur l'emprise du pilote.

API : https://firms.modaps.eosdis.nasa.gov/api/area/ (clé MAP_KEY gratuite).
"""

import csv
import io
import os
from datetime import datetime, timezone

from .socle import Observation, emprise_pilote, telecharger

URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{cle}/{capteur}/{emprise}/{jours}"
CAPTEURS = ("VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT")
CONFIANCE = {"l": 0.3, "n": 0.6, "h": 0.9}


def lire_csv(texte: str, capteur: str) -> list[Observation]:
    observations = []
    for ligne in csv.DictReader(io.StringIO(texte)):
        heure = ligne["acq_time"].zfill(4)
        date = datetime.strptime(f"{ligne['acq_date']} {heure}", "%Y-%m-%d %H%M").replace(tzinfo=timezone.utc)
        lat, lon = float(ligne["latitude"]), float(ligne["longitude"])
        observations.append(Observation(
            cle=f"{capteur}:{ligne['acq_date']}:{heure}:{lat:.5f}:{lon:.5f}",
            variable="puissance_feu",
            valeur=float(ligne["frp"]) if ligne.get("frp") else None,
            unite="MW",
            observe_le=date,
            longitude=lon,
            latitude=lat,
            brut={"capteur": capteur, "confiance": CONFIANCE.get(ligne.get("confidence", ""), None),
                  "jour_nuit": ligne.get("daynight")},
        ))
    return observations


def collecter(conn, jours: int = 2):
    cle = os.environ.get("FIRMS_MAP_KEY")
    if not cle:
        raise RuntimeError("FIRMS_MAP_KEY n'est pas défini (clé gratuite sur firms.modaps.eosdis.nasa.gov)")
    ouest, sud, est, nord = emprise_pilote(conn)
    emprise = f"{ouest},{sud},{est},{nord}"
    for capteur in CAPTEURS:
        texte = telecharger(URL.format(cle=cle, capteur=capteur, emprise=emprise, jours=min(jours, 5))).decode()
        if not texte.startswith("latitude"):
            raise RuntimeError(f"réponse FIRMS inattendue : {texte[:200]}")
        yield from lire_csv(texte, capteur)
