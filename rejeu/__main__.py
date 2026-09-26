"""Test à blanc sur une saison passée.

python -m rejeu VERITE.csv DEBUT FIN [--sans-collecte] [--balayage] [--pas 8] [--tolerance 8] [--rapport F.md]

Exemple : python -m rejeu data/verite_2024.csv 2024-07-01 2024-10-31 --balayage

À lancer sur une base dédiée : l'outil refuse une base qui contient déjà
des publications, sauf avec --forcer.
"""

import argparse
import sys
from datetime import date
from itertools import product

from collecte.commun import connexion
from detection import DEFAUT, Reglages

from . import balayer, charger_verite, collecter_saison, pourcent, rapport


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="python -m rejeu", description=__doc__.splitlines()[0])
    p.add_argument("verite")
    p.add_argument("debut", type=date.fromisoformat)
    p.add_argument("fin", type=date.fromisoformat)
    p.add_argument("--sans-collecte", action="store_true", help="réutiliser les scènes déjà en base")
    p.add_argument("--balayage", action="store_true", help="comparer plusieurs jeux de seuils")
    p.add_argument("--pas", type=int, default=8)
    p.add_argument("--tolerance", type=int, default=8)
    p.add_argument("--rapport", default="rapport_test_a_blanc.md")
    p.add_argument("--forcer", action="store_true")
    a = p.parse_args(argv)

    with connexion() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM terre.publication")
            if cur.fetchone()[0] and not a.forcer:
                print("Cette base contient des publications : utiliser une base dédiée au test à blanc.")
                return 1
        if not a.sans_collecte:
            with conn.transaction(), conn.cursor() as cur:
                print(f"Collecte : {collecter_saison(cur, a.debut, a.fin, a.pas)} mesures chargées.")

        with conn.transaction(), conn.cursor() as cur:
            crues = charger_verite(cur, a.verite)
            combinaisons = [DEFAUT]
            if a.balayage:
                combinaisons += [Reglages(seuil_historique=s, tres_rare=r)
                                 for s, r in product((0.05, 0.10, 0.20), (0.02, 0.05)) if r < s and
                                 (s, r) != (DEFAUT.seuil_historique, DEFAUT.tres_rare)]
            resultats = balayer(cur, a.debut, a.fin, crues, combinaisons, a.pas, a.tolerance)

    reglages, bilan = resultats[0]
    texte = rapport(bilan, a.debut, a.fin, reglages)
    if len(resultats) > 1:
        texte += "\n## Balayage des seuils\n\n| Seuil historique | Très rare | Alertes | Précision | Rappel |\n|---|---|---|---|---|\n"
        for r, b in resultats:
            texte += (f"| {r.seuil_historique:.0%} | {r.tres_rare:.0%} | {len(b.alertes)} | "
                      f"{pourcent(b.precision)} | {pourcent(b.rappel)} |\n")
    with open(a.rapport, "w", encoding="utf-8") as f:
        f.write(texte)
    print(f"Précision {pourcent(bilan.precision)}, rappel {pourcent(bilan.rappel)} · "
          f"critère {'atteint' if bilan.critere_atteint else 'non atteint'} · rapport : {a.rapport}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
