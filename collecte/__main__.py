"""Collecte quotidienne : python -m collecte [gdacs usgs firms eaux]

Chaque source tourne dans sa propre transaction : l'échec de l'une
n'empêche pas les autres, et n'écrit rien de partiel.
"""

import sys

from . import commun, eaux, firms, gdacs, usgs

COLLECTEURS = {"gdacs": gdacs, "usgs": usgs, "firms": firms, "eaux": eaux}


def main(noms: list[str]) -> int:
    echecs = 0
    with commun.connexion() as conn:
        for nom in noms or COLLECTEURS:
            try:
                with conn.transaction(), conn.cursor() as cur:
                    n = COLLECTEURS[nom].collecter(cur)
                print(f"{nom} : {n} enregistrements")
            except Exception as e:  # une source en panne ne bloque pas les autres
                echecs += 1
                print(f"{nom} : ÉCHEC, {type(e).__name__} : {e}", file=sys.stderr)
    return 1 if echecs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
