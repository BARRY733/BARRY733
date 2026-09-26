"""Étape 3 : comparer chaque point surveillé à son étendue d'eau historique.

Règle centrale : de l'eau observée par satellite là où, historiquement, il n'y en
a presque jamais. Les alertes GDACS proches et la tendance GloFAS renforcent ou
nuancent la confiance, mais ne suffisent jamais seules à une anomalie forte.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

import psycopg

# Réglages, à ajuster lors du test à blanc (étape 6).
FENETRE_JOURS = 16            # un passage Landsat tous les 8 jours environ
RAYON_GDACS_KM = 50
FREQ_RARE = 0.20              # eau vue moins de 20 % du temps historiquement
FREQ_TRES_RARE = 0.05
HAUSSE_GLOFAS = 1.5           # débit maximal prévu / débit du premier jour


@dataclass
class Faits:
    """Ce que la base sait d'un point sur la fenêtre étudiée."""
    infrastructure_id: int
    nom: str
    frequence_historique: float | None
    eau: list[tuple[datetime, int]]              # (date, 1 eau / 0 sec), observations dégagées
    gdacs: list[tuple[float, float]]             # (niveau 1-3, distance km)
    debits: list[tuple[int, float]]              # (échéance h, m3/s), dernière prévision
    observations: list[int] = field(default_factory=list)


@dataclass
class Anomalie:
    infrastructure_id: int
    type: str
    confiance: float
    niveau: str
    elements: dict


def niveau(confiance: float) -> str:
    return "eleve" if confiance >= 0.75 else "moyen" if confiance >= 0.5 else "faible"


def tendance_glofas(debits: list[tuple[int, float]]) -> float | None:
    """Rapport entre le débit maximal prévu et celui de la première échéance."""
    if len(debits) < 2:
        return None
    debits = sorted(debits)
    premier = debits[0][1]
    return max(d for _, d in debits) / premier if premier > 0 else None


def evaluer(f: Faits) -> Anomalie | None:
    elements: dict = {"observations": f.observations}
    gdacs_fort = any(n >= 2 for n, _ in f.gdacs)
    gdacs_vert = any(n == 1 for n, _ in f.gdacs)
    tendance = tendance_glofas(f.debits)
    hausse = tendance is not None and tendance >= HAUSSE_GLOFAS
    if f.gdacs:
        elements["gdacs"] = [{"niveau": n, "distance_km": round(d, 1)} for n, d in f.gdacs]
    if tendance is not None:
        elements["glofas_tendance"] = round(tendance, 2)

    eau = sorted(f.eau)
    derniere = eau[-1] if eau else None
    mouille = sum(v for _, v in eau)

    if (f.frequence_historique is not None and derniere and derniere[1] == 1
            and f.frequence_historique < FREQ_RARE):
        c = 0.60 if f.frequence_historique < FREQ_TRES_RARE else 0.45
        if mouille >= 2:
            c += 0.15
        c += 0.15 if gdacs_fort else 0.05 if gdacs_vert else 0
        if hausse:
            c += 0.10
        c = round(min(c, 0.95), 2)
        elements |= {
            "frequence_historique": round(f.frequence_historique, 3),
            "eau_vue_le": derniere[0].date().isoformat(),
            "passages_avec_eau": mouille,
            "passages_degages": len(eau),
        }
        return Anomalie(f.infrastructure_id, "eau_hors_etendue", c, niveau(c), elements)

    # Sans eau confirmée par satellite, une alerte proche reste une simple menace.
    sec_confirme = derniere is not None and derniere[1] == 0
    if gdacs_fort and not sec_confirme:
        c = 0.35 + (0.10 if hausse else 0)
        elements["raison"] = ("aucun passage satellite dégagé" if derniere is None
                              else "dernier passage dégagé sans eau anormale")
        return Anomalie(f.infrastructure_id, "menace_crue", c, niveau(c), elements)
    return None


def rassembler(conn: psycopg.Connection, jour: date) -> list[Faits]:
    fin = datetime.combine(jour, time.max, tzinfo=timezone.utc)
    debut = fin - timedelta(days=FENETRE_JOURS)
    faits = []
    for infra_id, nom, geom in conn.execute(
        "SELECT id, nom, geom FROM terre.infrastructure WHERE surveille ORDER BY id"
    ).fetchall():
        freq = conn.execute(
            "SELECT id, valeur FROM terre.observation WHERE infrastructure_id = %s"
            " AND variable = 'frequence_eau_historique' ORDER BY observe_le DESC LIMIT 1",
            (infra_id,),
        ).fetchone()
        eau = conn.execute(
            "SELECT id, observe_le, valeur FROM terre.observation WHERE infrastructure_id = %s"
            " AND variable = 'eau_observee' AND observe_le BETWEEN %s AND %s",
            (infra_id, debut, fin),
        ).fetchall()
        gdacs = conn.execute(
            "SELECT o.id, o.valeur, ST_Distance(o.geom::geography, i.geom::geography) / 1000"
            " FROM terre.observation o, terre.infrastructure i"
            " WHERE i.id = %s AND o.variable = 'alerte_inondation'"
            " AND o.observe_le <= %s"
            " AND coalesce((o.brut->>'todate')::timestamptz, o.observe_le) >= %s"
            " AND ST_DWithin(o.geom::geography, i.geom::geography, %s)",
            (infra_id, fin, debut, RAYON_GDACS_KM * 1000),
        ).fetchall()
        debits = conn.execute(
            "SELECT id, (brut->>'echeance_h')::int, valeur FROM terre.observation"
            " WHERE infrastructure_id = %s AND variable = 'debit_prevu'"
            " AND brut->>'prevision_du' = ("
            "   SELECT max(brut->>'prevision_du') FROM terre.observation"
            "   WHERE infrastructure_id = %s AND variable = 'debit_prevu'"
            "   AND (brut->>'prevision_du')::date <= %s)",
            (infra_id, infra_id, jour),
        ).fetchall()
        faits.append(Faits(
            infrastructure_id=infra_id, nom=nom,
            frequence_historique=freq[1] if freq else None,
            eau=[(d, int(v)) for _, d, v in eau],
            gdacs=[(v, km) for _, v, km in gdacs if v is not None],
            debits=[(h, v) for _, h, v in debits],
            observations=sorted(([freq[0]] if freq else []) + [r[0] for r in eau]
                                + [r[0] for r in gdacs] + [r[0] for r in debits]),
        ))
    return faits


def detecter(conn: psycopg.Connection, jour: date) -> tuple[list[Anomalie], list[str]]:
    """Calcule et enregistre les anomalies du jour ; renvoie aussi les points sans référence."""
    faits = rassembler(conn, jour)
    anomalies = [a for a in map(evaluer, faits) if a]
    sans_reference = [f.nom for f in faits if f.frequence_historique is None]
    with conn.transaction():
        # Un nouveau calcul remplace les anomalies non encore traitées du même jour.
        conn.execute("DELETE FROM terre.anomalie WHERE jour = %s AND statut = 'nouvelle'", (jour,))
        for a in anomalies:
            conn.execute(
                "INSERT INTO terre.anomalie (jour, infrastructure_id, type, confiance, niveau, elements)"
                " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (infrastructure_id, jour, type) DO NOTHING",
                (jour, a.infrastructure_id, a.type, a.confiance, a.niveau, psycopg.types.json.Jsonb(a.elements)),
            )
    return anomalies, sans_reference


def liste_du_jour(conn: psycopg.Connection, jour: date) -> list[tuple]:
    return conn.execute(
        "SELECT i.nom, i.type, a.type, a.confiance, a.niveau, a.statut"
        " FROM terre.anomalie a JOIN terre.infrastructure i ON i.id = a.infrastructure_id"
        " WHERE a.jour = %s ORDER BY a.confiance DESC, i.nom",
        (jour,),
    ).fetchall()
