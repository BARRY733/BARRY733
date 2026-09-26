"""Ligne de commande : python -m oeil_bleu <commande>."""

import argparse
import sys
from datetime import date
from pathlib import Path

from . import db, detection, points
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
    d = sous.add_parser("detecter", help="liste les anomalies du jour sur les points surveillés")
    d.add_argument("--jour", type=date.fromisoformat, default=date.today(), help="AAAA-MM-JJ")
    a = sous.add_parser("agents", help="fait passer les anomalies du jour dans la chaîne des agents")
    a.add_argument("--jour", type=date.fromisoformat, default=date.today(), help="AAAA-MM-JJ")
    a.add_argument("--confiance-min", type=float, default=0.5)
    v = sous.add_parser("verifier-licence", help="le directeur confirme la licence d'une source")
    v.add_argument("source")
    args = parser.parse_args(argv)
    if args.commande == "collecter" and (inconnues := set(args.sources) - set(COLLECTEURS)):
        parser.error(f"source(s) inconnue(s) : {', '.join(sorted(inconnues))}")

    with db.connecter() as conn:
        if args.commande == "migrer":
            faits = db.migrer(conn, avec_seeds=not args.sans_seeds)
            print("\n".join(faits) if faits else "Base déjà à jour.")
        elif args.commande == "detecter":
            afficher_detection(conn, args.jour)
        elif args.commande == "agents":
            return lancer_agents(conn, args.jour, args.confiance_min)
        elif args.commande == "verifier-licence":
            if conn.execute("UPDATE terre.source SET licence_verifiee = true WHERE code = %s",
                            (args.source,)).rowcount == 0:
                print(f"Source « {args.source} » inconnue.", file=sys.stderr)
                return 1
            print(f"Licence de {args.source} confirmée.")
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


NIVEAUX = {"eleve": "ÉLEVÉ", "moyen": "moyen", "faible": "faible"}


def afficher_detection(conn, jour: date) -> None:
    _, sans_reference = detection.detecter(conn, jour)
    lignes = detection.liste_du_jour(conn, jour)
    print(f"Anomalies du {jour:%d/%m/%Y} : {len(lignes)}")
    for nom, type_infra, type_anom, confiance, niv, statut in lignes:
        print(f"  [{NIVEAUX[niv]:>6}] {confiance:.2f}  {nom} ({type_infra})  {type_anom}  {statut}")
    if sans_reference:
        print(f"Points sans fréquence historique, non évalués : {', '.join(sans_reference)}")


def lancer_agents(conn, jour: date, confiance_min: float) -> int:
    from .agents.chaine import traiter_jour
    from .agents.client import AppelantClaude

    issues = traiter_jour(conn, AppelantClaude(), jour, confiance_min)
    print(f"Anomalies traitées le {jour:%d/%m/%Y} : {len(issues)}")
    for i in issues:
        suite = f" → publication {i.publication_id}" if i.publication_id else ""
        print(f"  anomalie {i.anomalie_id} : {i.resultat}{suite}  {i.detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
