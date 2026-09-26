"""Ligne de commande : python -m oeil_bleu <commande>."""

import argparse
import sys
from pathlib import Path

from . import db, points


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="oeil_bleu")
    sous = parser.add_subparsers(dest="commande", required=True)
    m = sous.add_parser("migrer", help="applique le schéma et le référentiel")
    m.add_argument("--sans-seeds", action="store_true")
    p = sous.add_parser("importer-points", help="importe les points surveillés")
    p.add_argument("csv", type=Path, nargs="?", default=db.RACINE / "data" / "points_surveilles.csv")
    args = parser.parse_args(argv)

    with db.connecter() as conn:
        if args.commande == "migrer":
            faits = db.migrer(conn, avec_seeds=not args.sans_seeds)
            print("\n".join(faits) if faits else "Base déjà à jour.")
        else:
            try:
                n = points.importer(conn, points.lire(args.csv))
            except ValueError as e:
                print(f"Import refusé :\n{e}", file=sys.stderr)
                return 1
            print(f"{n} point(s) surveillé(s) importé(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
