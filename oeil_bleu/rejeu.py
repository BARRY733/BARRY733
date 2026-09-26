"""Étape 6 : test à blanc.

On rejoue la détection jour par jour sur une saison passée, puis on compare
les alertes aux crues réellement constatées (fichier de vérité terrain).
Rien n'est conservé dans la base : tout se passe dans une transaction annulée.
"""

import csv
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from statistics import median

import psycopg

from .detection import detecter

ORDRE = {"faible": 0, "moyen": 1, "eleve": 2}
TOLERANCE_JOURS = 7   # une alerte jusqu'à 7 jours après la fin de la crue compte encore comme juste


@dataclass
class Crue:
    point: str
    debut: date
    fin: date
    source: str = ""


@dataclass
class Episode:
    """Jours consécutifs d'alerte sur un même point : une seule alerte pour le lecteur."""
    point: str
    debut: date
    fin: date
    niveau_max: str
    confiance_max: float
    crue: Crue | None = None


@dataclass
class Score:
    seuil: str
    episodes: int
    justes: int
    fausses: int
    crues: int
    detectees: int
    delais: list[int] = field(default_factory=list)

    @property
    def taux_fausses_alertes(self) -> float | None:
        return self.fausses / self.episodes if self.episodes else None

    @property
    def taux_detection(self) -> float | None:
        return self.detectees / self.crues if self.crues else None

    @property
    def delai_median(self) -> float | None:
        return median(self.delais) if self.delais else None


def lire_verite(chemin: Path) -> list[Crue]:
    """CSV : point,debut,fin,source — une ligne par crue constatée sur un point surveillé."""
    crues = []
    with open(chemin, newline="", encoding="utf-8") as f:
        for n, l in enumerate(csv.DictReader(f), start=2):
            try:
                c = Crue(l["point"].strip(), date.fromisoformat(l["debut"]), date.fromisoformat(l["fin"]),
                         (l.get("source") or "").strip())
            except (KeyError, ValueError, AttributeError) as e:
                raise ValueError(f"ligne {n} : {e}") from None
            if c.fin < c.debut:
                raise ValueError(f"ligne {n} : fin avant début")
            crues.append(c)
    return crues


def rejouer(conn: psycopg.Connection, debut: date, fin: date) -> list[tuple[date, str, str, float]]:
    """Détection jour par jour ; renvoie (jour, point, niveau, confiance) sans rien enregistrer."""
    alertes = []
    with conn.transaction(force_rollback=True):
        conn.execute("DELETE FROM terre.anomalie WHERE jour BETWEEN %s AND %s AND statut = 'nouvelle'", (debut, fin))
        jour = debut
        while jour <= fin:
            detecter(conn, jour)
            alertes += [(jour, nom, niveau, float(conf)) for nom, niveau, conf in conn.execute(
                "SELECT i.nom, a.niveau, a.confiance FROM terre.anomalie a"
                " JOIN terre.infrastructure i ON i.id = a.infrastructure_id WHERE a.jour = %s", (jour,))]
            jour += timedelta(days=1)
    return alertes


def episodes(alertes: list[tuple[date, str, str, float]], seuil: str) -> list[Episode]:
    par_point = defaultdict(list)
    for jour, point, niveau, conf in alertes:
        if ORDRE[niveau] >= ORDRE[seuil]:
            par_point[point].append((jour, niveau, conf))
    resultat = []
    for point, jours in par_point.items():
        jours.sort()
        courant = None
        for jour, niveau, conf in jours:
            if courant and jour - courant.fin <= timedelta(days=1):
                courant.fin = jour
                if ORDRE[niveau] > ORDRE[courant.niveau_max]:
                    courant.niveau_max = niveau
                courant.confiance_max = max(courant.confiance_max, conf)
            else:
                courant = Episode(point, jour, jour, niveau, conf)
                resultat.append(courant)
    return sorted(resultat, key=lambda e: (e.debut, e.point))


def evaluer(alertes, crues: list[Crue], seuil: str) -> tuple[Score, list[Episode]]:
    eps = episodes(alertes, seuil)
    for e in eps:
        e.crue = next((c for c in crues if c.point == e.point
                       and e.debut <= c.fin + timedelta(days=TOLERANCE_JOURS) and e.fin >= c.debut), None)
    detectees, delais = 0, []
    for c in crues:
        liees = [e for e in eps if e.crue is c]
        if liees:
            detectees += 1
            delais.append((min(e.debut for e in liees) - c.debut).days)
    justes = sum(1 for e in eps if e.crue)
    return Score(seuil, len(eps), justes, len(eps) - justes, len(crues), detectees, delais), eps


def pourcent(x: float | None) -> str:
    return "—" if x is None else f"{x:.0%}"


def rapport(scores: list[Score], debut: date, fin: date, points_sans_verite: list[str]) -> str:
    lignes = [
        f"Test à blanc du {debut:%d/%m/%Y} au {fin:%d/%m/%Y}",
        "",
        f"{'Seuil':<8}{'Alertes':>9}{'Justes':>8}{'Fausses':>9}{'Fausses %':>11}"
        f"{'Crues':>7}{'Détectées':>11}{'Détection %':>13}{'Délai médian':>14}",
    ]
    for s in scores:
        delai = "—" if s.delai_median is None else f"{s.delai_median:+.0f} j"
        lignes.append(f"{s.seuil:<8}{s.episodes:>9}{s.justes:>8}{s.fausses:>9}{pourcent(s.taux_fausses_alertes):>11}"
                      f"{s.crues:>7}{s.detectees:>11}{pourcent(s.taux_detection):>13}{delai:>14}")
    lignes += [
        "",
        "Alertes : épisodes d'alerte (jours consécutifs sur un même point).",
        f"Juste : l'épisode chevauche une crue constatée sur ce point (tolérance {TOLERANCE_JOURS} jours après la fin).",
        "Délai : jours entre le début constaté de la crue et la première alerte (négatif = alerte en avance).",
    ]
    if points_sans_verite:
        lignes += ["", "Points en alerte sans aucune crue dans la vérité terrain (comptés comme fausses alertes ;",
                   "vérifiez qu'il n'y a vraiment pas eu d'inondation) : " + ", ".join(sorted(points_sans_verite))]
    return "\n".join(lignes)


def ecrire_episodes(chemin: Path, eps_par_seuil: dict[str, list[Episode]]) -> None:
    with open(chemin, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["seuil", "point", "debut", "fin", "niveau_max", "confiance_max", "verdict", "crue_source"])
        for seuil, eps in eps_par_seuil.items():
            for e in eps:
                w.writerow([seuil, e.point, e.debut, e.fin, e.niveau_max, f"{e.confiance_max:.2f}",
                            "juste" if e.crue else "fausse", e.crue.source if e.crue else ""])


def test_a_blanc(conn, debut: date, fin: date, verite: Path, sortie_csv: Path | None = None) -> str:
    crues = lire_verite(verite)
    connus = {r[0] for r in conn.execute("SELECT nom FROM terre.infrastructure WHERE surveille")}
    inconnus = sorted({c.point for c in crues} - connus)
    if inconnus:
        raise ValueError(f"points de la vérité terrain inconnus : {', '.join(inconnus)}")
    alertes = rejouer(conn, debut, fin)
    crues = [c for c in crues if c.debut <= fin and c.fin >= debut]
    resultats = {s: evaluer(alertes, crues, s) for s in ORDRE}
    if sortie_csv:
        ecrire_episodes(sortie_csv, {s: eps for s, (_, eps) in resultats.items()})
    avec_verite = {c.point for c in crues}
    sans = {a[1] for a in alertes} - avec_verite
    return rapport([sc for sc, _ in resultats.values()], debut, fin, list(sans))
