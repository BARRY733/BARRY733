"""Passe les anomalies détectées dans la chaîne éditoriale.

python -m agents                 # toutes les anomalies au statut « detecte »
python -m agents 12 15           # des événements précis
"""

import sys

from collecte.commun import connexion

from .chaine import client_par_defaut, traiter


def main(args: list[str]) -> int:
    client = client_par_defaut()
    echecs = 0
    with connexion() as conn:
        if args:
            ids = [int(a) for a in args]
        else:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM terre.evenement WHERE statut = 'detecte' "
                            "AND ref_externe LIKE 'detection-%' ORDER BY debut")
                ids = [r[0] for r in cur.fetchall()]
        for eid in ids:
            try:
                with conn.transaction(), conn.cursor() as cur:
                    r = traiter(cur, client, eid)
                suite = f", publication {r.publication_id}" if r.publication_id else ""
                print(f"événement {eid} : {r.issue}{suite}")
                if r.manquements:
                    for m in r.manquements:
                        print(f"  - {m}")
            except Exception as e:  # un événement en échec n'arrête pas les autres
                echecs += 1
                print(f"événement {eid} : ÉCHEC, {type(e).__name__} : {e}", file=sys.stderr)
    return 1 if echecs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
