"""FIRMS (NASA) : foyers de feu actifs détectés par VIIRS.

Nécessite une clé gratuite (MAP_KEY) : https://firms.modaps.eosdis.nasa.gov/api/map_key/
à placer dans la variable FIRMS_MAP_KEY.
"""

import csv
import io
import os
from datetime import date, datetime, timezone

from . import commun

URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
CAPTEUR = "VIIRS_SNPP_NRT"
CONFIANCE = {"h": "eleve", "high": "eleve", "n": "moyen", "nominal": "moyen", "l": "a_confirmer", "low": "a_confirmer"}


def gravite(frp: float) -> int:
    """Puissance radiative du feu (MW) ramenée à une échelle de 1 à 5."""
    for seuil, g in ((500, 5), (100, 4), (30, 3), (10, 2)):
        if frp >= seuil:
            return g
    return 1


def collecter(cur, jours: int = 1, aujourd_hui: date | None = None) -> int:
    cle = os.environ.get("FIRMS_MAP_KEY")
    if not cle:
        raise RuntimeError("FIRMS_MAP_KEY absente")
    o, s, e, n = commun.emprise(cur)
    url = f"{URL}/{cle}/{CAPTEUR}/{o},{s},{e},{n}/{min(jours, 10)}"
    if aujourd_hui:
        url += f"/{aujourd_hui}"
    brut = commun.telecharger(url)
    # La clé ne doit pas apparaître dans les preuves publiées.
    preuve = commun.archiver(cur, "firms", brut, "csv", url.replace(cle, "<cle>"),
                             f"FIRMS {CAPTEUR}, sans transformation")
    compte = 0
    for l in csv.DictReader(io.StringIO(brut.decode())):
        heure = l["acq_time"].zfill(4)
        quand = datetime.strptime(f"{l['acq_date']} {heure}", "%Y-%m-%d %H%M").replace(tzinfo=timezone.utc)
        lat, lon = float(l["latitude"]), float(l["longitude"])
        commun.enregistrer_evenement(
            cur, "firms", f"{l['satellite']}-{l['acq_date']}-{heure}-{lat:.4f}-{lon:.4f}", "feu",
            lon, lat, debut=quand, fin=None, gravite=gravite(float(l.get("frp") or 0)),
            confiance=CONFIANCE.get(l["confidence"].strip().lower(), "a_confirmer"), preuve_id=preuve,
        )
        compte += 1
    return compte
