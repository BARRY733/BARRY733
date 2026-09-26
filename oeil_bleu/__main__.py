"""Ligne de commande : python -m oeil_bleu <commande>."""

import argparse
import sys
from pathlib import Path

from . import db, points
from .collecte import executer

COLLECTEURS = {
    # nom de commande : (code de la source en base, module)
    "gdacs": ("gdacs", "gdacs"),
    "firms": ("firms", "firms"),
    "deafrica": ("deafrica_wofs", "deafrica"),
    "glofas": ("glofas", "glofas"),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="oeil_bleu")
    sous = parser.add_subparsers(dest="commande", required=True)
    m = sous.add_parser("migrer", help="applique le schéma et le référentiel")
    m.add_argument("--sans-seeds", action="store_true")
    p = sous.add_parser("importer-points", help="importe les points surveillés")
    p.add_argument("csv", type=Path, nargs="?", default=db.RACINE / "data" / "points_surveilles.csv")
    c = sous.add_parser("collecter", help="récupère les nouvelles données des sources")
    c.add_argument("sources", nargs="*", metavar="source",
                   help=f"parmi {', '.join(COLLECTEURS)} (toutes par défaut)")
    args = parser.parse_args(argv)
    if args.commande == "collecter" and (inconnues := set(args.sources) - set(COLLECTEURS)):
        parser.error(f"source(s) inconnue(s) : {', '.join(sorted(inconnues))}")

    with db.connecter() as conn:
        if args.commande == "migrer":
            faits = db.migrer(conn, avec_seeds=not args.sans_seeds)
            print("\n".join(faits) if faits else "Base déjà à jour.")
        elif args.commande == "collecter":
            return collecter(conn, args.sources or list(COLLECTEURS))
        else:
            try:
                n = points.importer(conn, points.lire(args.csv))
            except ValueError as e:
                print(f"Import refusé :\n{e}", file=sys.stderr)
                return 1
            print(f"{n} point(s) surveillé(s) importé(s).")
    return 0


def collecter(conn, noms: list[str]) -> int:
    """Lance chaque collecteur ; un échec n'empêche pas les suivants."""
    from importlib import import_module

    echecs = 0
    for nom in noms:
        code, module = COLLECTEURS[nom]
        try:
            n = executer(conn, code, import_module(f".collecte.{module}", __package__).collecter)
            print(f"{nom} : {n} observation(s) nouvelle(s)")
        except Exception as e:
            echecs += 1
            print(f"{nom} : échec, {type(e).__name__}: {e}", file=sys.stderr)
    return 1 if echecs else 0


if __name__ == "__main__":
    sys.exit(main())
