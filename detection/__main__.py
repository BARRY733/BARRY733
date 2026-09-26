"""Liste quotidienne des anomalies : python -m detection [AAAA-MM-JJ] [--json]"""

import json
import sys
from dataclasses import asdict
from datetime import date

from collecte.commun import connexion

from . import detecter


def main(args: list[str]) -> None:
    en_json = "--json" in args
    dates = [a for a in args if a != "--json"]
    jour = date.fromisoformat(dates[0]) if dates else date.today()
    with connexion() as conn, conn.transaction(), conn.cursor() as cur:
        anomalies = detecter(cur, jour)
    if en_json:
        print(json.dumps([asdict(a) for a in anomalies], ensure_ascii=False, indent=2))
        return
    print(f"Anomalies du {jour} : {len(anomalies)}")
    for a in sorted(anomalies, key=lambda a: a.confiance != "eleve"):
        etat = "close" if a.close else "en cours"
        print(f"- {a.point} : confiance {a.confiance}, eau sur {a.observations_eau}/{a.observations_claires} "
              f"scènes, historique {a.frequence_historique:.0%}"
              f"{', corroborée' if a.corroboree else ''}, {etat} (événement {a.evenement_id})")


if __name__ == "__main__":
    main(sys.argv[1:])
