"""Ligne de commande : python -m oeil_bleu <commande>."""

import argparse
import sys
from datetime import date
from pathlib import Path

from . import bulletin, db, detection, points
from .collecte import executer

COLLECTEURS = {
    # nom de commande : (code de la source en base, module)
    "gdacs": ("gdacs", "gdacs"),
    "firms": ("firms", "firms"),
    "deafrica": ("deafrica_wofs", "deafrica"),
    "glofas": ("glofas", "glofas"),
}
HISTORIQUE = {"gdacs", "deafrica"}   # sources qui savent collecter une saison passée


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
    c.add_argument("--debut", type=date.fromisoformat, help="saison passée : premier jour (gdacs, deafrica)")
    c.add_argument("--fin", type=date.fromisoformat, help="saison passée : dernier jour")
    t = sous.add_parser("test-a-blanc", help="rejoue la détection sur une saison passée et la note")
    t.add_argument("--debut", type=date.fromisoformat, required=True)
    t.add_argument("--fin", type=date.fromisoformat, required=True)
    t.add_argument("--verite", type=Path, default=db.RACINE / "data" / "verite_terrain.csv")
    t.add_argument("--episodes", type=Path, default=Path("episodes.csv"), help="détail des alertes (CSV)")
    d = sous.add_parser("detecter", help="liste les anomalies du jour sur les points surveillés")
    d.add_argument("--jour", type=date.fromisoformat, default=date.today(), help="AAAA-MM-JJ")
    a = sous.add_parser("agents", help="fait passer les anomalies du jour dans la chaîne des agents")
    a.add_argument("--jour", type=date.fromisoformat, default=date.today(), help="AAAA-MM-JJ")
    a.add_argument("--confiance-min", type=float, default=0.5)
    v = sous.add_parser("verifier-licence", help="le directeur confirme la licence d'une source")
    v.add_argument("source")
    sous.add_parser("a-valider", help="publications en attente du directeur")
    for nom, aide in (("valider", "le directeur approuve une publication"),
                      ("rejeter", "le directeur retire une publication")):
        x = sous.add_parser(nom, help=aide)
        x.add_argument("publication", type=int)
        x.add_argument("--par", required=True, help="nom du directeur de publication")
        x.add_argument("--motif")
    b = sous.add_parser("bulletin", help="compose le bulletin (aperçu par défaut)")
    b.add_argument("--jour", type=date.fromisoformat, default=date.today(), help="AAAA-MM-JJ")
    b.add_argument("--apercu", type=Path, default=Path("bulletin.html"))
    b.add_argument("--envoyer", action="store_true", help="envoie réellement aux destinataires")
    b.add_argument("--meme-vide", action="store_true", help="envoie aussi un bulletin sans alerte")
    args = parser.parse_args(argv)
    if args.commande == "collecter" and (inconnues := set(args.sources) - set(COLLECTEURS)):
        parser.error(f"source(s) inconnue(s) : {', '.join(sorted(inconnues))}")
    if args.commande == "collecter" and (args.debut or args.fin):
        if not (args.debut and args.fin) or args.debut > args.fin:
            parser.error("--debut et --fin vont ensemble, debut avant fin")
        if autres := set(args.sources or COLLECTEURS) - HISTORIQUE:
            parser.error(f"pas de saison passée pour : {', '.join(sorted(autres))} (possibles : gdacs, deafrica)")
    if args.commande == "test-a-blanc" and args.debut > args.fin:
        parser.error("--debut doit précéder --fin")

    with db.connecter() as conn:
        if args.commande == "migrer":
            faits = db.migrer(conn, avec_seeds=not args.sans_seeds)
            print("\n".join(faits) if faits else "Base déjà à jour.")
        elif args.commande == "detecter":
            afficher_detection(conn, args.jour)
        elif args.commande == "a-valider":
            for pid, titre, contenu, confiance, avis in bulletin.a_valider(conn):
                print(f"=== Publication {pid} · confiance {confiance} ===\n{titre}\n\n{contenu}\n\nAvis : {avis}\n")
            print("Valider : python -m oeil_bleu valider <n°> --par \"Nom\"")
        elif args.commande in ("valider", "rejeter"):
            try:
                bulletin.decider(conn, args.publication, args.par, args.commande == "valider", args.motif)
            except ValueError as e:
                print(e, file=sys.stderr)
                return 1
            print(f"Publication {args.publication} {'validée' if args.commande == 'valider' else 'retirée'}.")
        elif args.commande == "bulletin":
            return lancer_bulletin(conn, args)
        elif args.commande == "agents":
            return lancer_agents(conn, args.jour, args.confiance_min)
        elif args.commande == "verifier-licence":
            if conn.execute("UPDATE terre.source SET licence_verifiee = true WHERE code = %s",
                            (args.source,)).rowcount == 0:
                print(f"Source « {args.source} » inconnue.", file=sys.stderr)
                return 1
            print(f"Licence de {args.source} confirmée.")
        elif args.commande == "collecter":
            periode = {"debut": args.debut, "fin": args.fin} if args.debut else {}
            return collecter(conn, args.sources or list(COLLECTEURS), **periode)
        elif args.commande == "test-a-blanc":
            from .rejeu import test_a_blanc
            try:
                print(test_a_blanc(conn, args.debut, args.fin, args.verite, args.episodes))
            except (ValueError, FileNotFoundError) as e:
                print(e, file=sys.stderr)
                return 1
            print(f"\nDétail des alertes : {args.episodes}")
        else:
            try:
                n = points.importer(conn, points.lire(args.csv))
            except ValueError as e:
                print(f"Import refusé :\n{e}", file=sys.stderr)
                return 1
            print(f"{n} point(s) surveillé(s) importé(s).")
    return 0


def collecter(conn, noms: list[str], **periode) -> int:
    """Lance chaque collecteur ; un échec n'empêche pas les suivants."""
    from functools import partial
    from importlib import import_module

    echecs = 0
    for nom in noms:
        code, module = COLLECTEURS[nom]
        try:
            fonction = import_module(f".collecte.{module}", __package__).collecter
            n = executer(conn, code, partial(fonction, **periode))
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


def lancer_bulletin(conn, args) -> int:
    import os

    if not args.envoyer:
        n = bulletin.apercu(conn, args.jour, args.apercu)
        print(f"Aperçu écrit dans {args.apercu} ({n} alerte(s)). Rien n'a été envoyé.")
        print("Pour envoyer : python -m oeil_bleu bulletin --envoyer")
        return 0
    destinataires = bulletin.lire_destinataires()
    with bulletin.smtp_depuis_env() as serveur:
        bid, n, echecs = bulletin.envoyer(conn, args.jour, destinataires, serveur,
                                          os.environ["SMTP_EXPEDITEUR"], meme_vide=args.meme_vide)
    if not bid:
        print("Aucune publication validée : bulletin non envoyé (--meme-vide pour l'envoyer quand même).")
        return 0
    print(f"Bulletin {bid} envoyé : {n} alerte(s), {len(destinataires) - len(echecs)} destinataire(s).")
    for e in echecs:
        print(f"  échec : {e}", file=sys.stderr)
    return 1 if echecs else 0


if __name__ == "__main__":
    sys.exit(main())
