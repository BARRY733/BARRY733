"""Étape 6 : test à blanc.

Rejoue la détection jour après jour sur une saison passée, puis confronte
les alertes qu'Œil Bleu aurait émises à une vérité terrain établie après
coup (rapports humanitaires, cartes UNOSAT ou Copernicus EMS, témoignages).

Définitions retenues :
- une alerte est « confirmée » si la vérité terrain signale le point inondé
  à cette date, à la tolérance près ;
- elle est « fausse » si le point figure dans la vérité terrain sans y être
  signalé inondé à cette date ;
- elle est « non évaluable » si le point n'y figure pas du tout ;
- précision = confirmées / (confirmées + fausses). Le critère de passage à
  la phase suivante est une précision d'au moins 80 %.
"""

import csv
from dataclasses import dataclass, field
from datetime import date, timedelta

from collecte import eaux
from detection import DEFAUT, NIVEAUX, Reglages, detecter

CRITERE_PRECISION = 0.80


@dataclass
class Alerte:
    evenement_id: int
    point_id: int
    point: str
    emise_le: date                 # jour où Œil Bleu l'aurait émise
    confiance_initiale: str
    confiance_max: str
    statut: str = "non_evaluable"  # confirmee | fausse | non_evaluable
    delai_jours: int | None = None


@dataclass
class Crue:
    """Une ligne de la vérité terrain."""
    point_id: int
    point: str
    debut: date
    fin: date
    inonde: bool
    source: str
    detectee: bool = False


@dataclass
class Bilan:
    alertes: list[Alerte]
    crues: list[Crue]
    confirmees: int = 0
    fausses: int = 0
    non_evaluables: int = 0
    precision: float | None = None
    rappel: float | None = None
    delai_median: float | None = None
    par_confiance: dict = field(default_factory=dict)

    @property
    def critere_atteint(self) -> bool:
        return self.precision is not None and self.precision >= CRITERE_PRECISION


def charger_verite(cur, chemin: str) -> list[Crue]:
    """CSV : nom, pays_iso, debut, fin, inonde (oui/non), source."""
    crues, erreurs = [], []
    with open(chemin, newline="", encoding="utf-8") as f:
        for n, l in enumerate(csv.DictReader(f), start=2):
            cur.execute("SELECT id FROM terre.infrastructure WHERE nom = %s AND pays_iso = %s",
                        (l["nom"].strip(), l["pays_iso"].strip().upper()))
            ligne = cur.fetchone()
            inonde = l["inonde"].strip().lower()
            if ligne is None:
                erreurs.append(f"ligne {n} : point inconnu « {l['nom']} »")
            elif inonde not in ("oui", "non"):
                erreurs.append(f"ligne {n} : inonde doit valoir oui ou non")
            else:
                debut, fin = date.fromisoformat(l["debut"].strip()), date.fromisoformat(l["fin"].strip())
                if fin < debut:
                    erreurs.append(f"ligne {n} : fin avant début")
                    continue
                crues.append(Crue(ligne[0], l["nom"].strip(), debut, fin, inonde == "oui", l.get("source", "").strip()))
    if erreurs:
        raise ValueError("\n".join(erreurs))
    return crues


def collecter_saison(cur, debut: date, fin: date, pas: int = 8, fenetre: int = DEFAUT.fenetre_jours) -> int:
    """Charge les scènes de la saison. Les mesures sont de vraies données : elles restent en base."""
    n, jour, premiere = 0, debut, True
    while jour <= fin:
        n += eaux.collecter(cur, jours=fenetre if premiere else pas, aujourd_hui=jour)
        jour, premiere = jour + timedelta(days=pas), False
    return n


def rejouer(cur, debut: date, fin: date, pas: int = 8, reglages: Reglages = DEFAUT) -> list[Alerte]:
    """Détection jour après jour. À exécuter dans un point de sauvegarde annulé ensuite :
    les événements créés ici n'ont pas vocation à rester."""
    alertes: dict[int, Alerte] = {}
    jour = debut
    while jour <= fin:
        for a in detecter(cur, jour, reglages):
            if a.observations_eau == 0 and a.evenement_id not in alertes:
                continue
            e = alertes.setdefault(a.evenement_id, Alerte(a.evenement_id, a.point_id, a.point, jour,
                                                          a.confiance, a.confiance))
            e.confiance_max = max(e.confiance_max, a.confiance, key=NIVEAUX.index)
        jour += timedelta(days=pas)
    return sorted(alertes.values(), key=lambda a: (a.emise_le, a.point))


def evaluer(alertes: list[Alerte], crues: list[Crue], tolerance: int = 8) -> Bilan:
    tol = timedelta(days=tolerance)
    couverts = {c.point_id for c in crues}
    for a in alertes:
        if a.point_id not in couverts:
            continue
        correspondantes = [c for c in crues if c.inonde and c.point_id == a.point_id
                           and c.debut - tol <= a.emise_le <= c.fin + tol]
        a.statut = "confirmee" if correspondantes else "fausse"
        for c in correspondantes:
            c.detectee = True
            a.delai_jours = (a.emise_le - c.debut).days

    b = Bilan(alertes, crues)
    b.confirmees = sum(a.statut == "confirmee" for a in alertes)
    b.fausses = sum(a.statut == "fausse" for a in alertes)
    b.non_evaluables = sum(a.statut == "non_evaluable" for a in alertes)
    if b.confirmees + b.fausses:
        b.precision = b.confirmees / (b.confirmees + b.fausses)
    reelles = [c for c in crues if c.inonde]
    if reelles:
        b.rappel = sum(c.detectee for c in reelles) / len(reelles)
    delais = sorted(a.delai_jours for a in alertes if a.delai_jours is not None)
    if delais:
        m = len(delais) // 2
        b.delai_median = delais[m] if len(delais) % 2 else (delais[m - 1] + delais[m]) / 2

    # Que donnerait la règle « attendre qu'un épisode atteigne tel niveau avant de publier » ?
    for i, niveau in enumerate(NIVEAUX):
        retenues = [a for a in alertes if NIVEAUX.index(a.confiance_max) >= i and a.statut != "non_evaluable"]
        ok = sum(a.statut == "confirmee" for a in retenues)
        b.par_confiance[niveau] = {"alertes": len(retenues), "confirmees": ok,
                                   "precision": ok / len(retenues) if retenues else None}
    return b


def balayer(cur, debut: date, fin: date, crues: list[Crue], combinaisons: list[Reglages],
            pas: int = 8, tolerance: int = 8) -> list[tuple[Reglages, Bilan]]:
    """Évalue plusieurs jeux de seuils sur les mêmes données, sans rien laisser en base."""
    resultats = []
    for reglages in combinaisons:
        cur.execute("SAVEPOINT balayage")
        try:
            bilan = evaluer(rejouer(cur, debut, fin, pas, reglages), [_copie(c) for c in crues], tolerance)
        finally:
            cur.execute("ROLLBACK TO SAVEPOINT balayage")
        resultats.append((reglages, bilan))
    return resultats


def _copie(c: Crue) -> Crue:
    return Crue(c.point_id, c.point, c.debut, c.fin, c.inonde, c.source)


def pourcent(x: float | None) -> str:
    return "n.d." if x is None else f"{x:.0%}"


def rapport(bilan: Bilan, debut: date, fin: date, reglages: Reglages = DEFAUT) -> str:
    b = bilan
    lignes = [
        f"# Test à blanc Œil Bleu, du {debut:%d/%m/%Y} au {fin:%d/%m/%Y}",
        "",
        f"Réglages : seuil historique {reglages.seuil_historique:.0%}, très rare {reglages.tres_rare:.0%}, "
        f"fenêtre {reglages.fenetre_jours} jours.",
        "",
        "| Indicateur | Valeur |",
        "|---|---|",
        f"| Alertes émises | {len(b.alertes)} |",
        f"| Confirmées | {b.confirmees} |",
        f"| Fausses alertes | {b.fausses} |",
        f"| Non évaluables (point absent de la vérité terrain) | {b.non_evaluables} |",
        f"| **Précision** | **{pourcent(b.precision)}** |",
        f"| Crues réelles détectées (rappel) | {pourcent(b.rappel)} |",
        f"| Délai médian après le début de la crue | "
        f"{'n.d.' if b.delai_median is None else f'{b.delai_median:g} jours'} |",
        "",
        f"Critère de passage (précision ≥ {CRITERE_PRECISION:.0%}) : "
        f"**{'atteint' if b.critere_atteint else 'non atteint'}**.",
        "",
        "## Si l'on attendait qu'un épisode atteigne un niveau de confiance avant de publier",
        "",
        "| Niveau minimal | Alertes évaluables | Confirmées | Précision |",
        "|---|---|---|---|",
    ]
    for niveau, v in b.par_confiance.items():
        lignes.append(f"| {niveau} | {v['alertes']} | {v['confirmees']} | {pourcent(v['precision'])} |")
    lignes += ["", "## Alertes", "", "| Émise le | Point | Confiance initiale | Maximale | Statut | Délai |",
               "|---|---|---|---|---|---|"]
    for a in b.alertes:
        lignes.append(f"| {a.emise_le:%d/%m/%Y} | {a.point} | {a.confiance_initiale} | {a.confiance_max} | "
                      f"{a.statut} | {'' if a.delai_jours is None else a.delai_jours} |")
    manquees = [c for c in b.crues if c.inonde and not c.detectee]
    if manquees:
        lignes += ["", "## Crues manquées", ""]
        lignes += [f"- {c.point}, du {c.debut:%d/%m/%Y} au {c.fin:%d/%m/%Y} ({c.source})" for c in manquees]
    return "\n".join(lignes) + "\n"
