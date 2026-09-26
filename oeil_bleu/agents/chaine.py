"""Chaîne éditoriale : Analyste → Contradicteur → Rédacteur → Conformité.

Chaque étape peut arrêter la chaîne. Seul un texte validé par les quatre
arrive au directeur de publication, en statut « en_validation ».
"""

from dataclasses import dataclass
from datetime import date

import psycopg
from psycopg.types.json import Jsonb

from . import consignes
from .client import Appelant, RefusAgent
from .schemas import Analyse, Conformite, Contradiction, Redaction

NIVEAUX = {"eleve": "élevé", "moyen": "moyen", "faible": "faible"}
MENTION_IA = "Texte rédigé avec l'aide d'agents d'intelligence artificielle, relu par la rédaction."


@dataclass
class Issue:
    anomalie_id: int
    resultat: str       # a_valider, ecartee, incertaine, non_conforme, refus
    detail: str
    publication_id: int | None = None


def dossier(conn: psycopg.Connection, anomalie_id: int) -> dict:
    ligne = conn.execute(
        "SELECT a.jour, a.type, a.confiance, a.niveau, a.elements,"
        " i.nom, i.type, ST_Y(ST_PointOnSurface(i.geom)), ST_X(ST_PointOnSurface(i.geom)), z.nom"
        " FROM terre.anomalie a JOIN terre.infrastructure i ON i.id = a.infrastructure_id"
        " JOIN terre.zone z ON z.id = i.zone_id WHERE a.id = %s",
        (anomalie_id,),
    ).fetchone()
    jour, type_a, confiance, niveau, elements, nom, type_i, lat, lon, zone = ligne
    sources = conn.execute(
        "SELECT DISTINCT s.code, s.nom, s.attribution FROM terre.observation o"
        " JOIN terre.source s ON s.id = o.source_id WHERE o.id = ANY(%s) ORDER BY s.code",
        (elements.get("observations", []),),
    ).fetchall()
    return {
        "jour": jour.isoformat(),
        "point": {"nom": nom, "type": type_i, "latitude": round(lat, 5), "longitude": round(lon, 5), "zone": zone},
        "anomalie": {"type": type_a, "confiance_detection": float(confiance), "niveau": niveau},
        "elements": {k: v for k, v in elements.items() if k != "observations"},
        "sources": [{"code": c, "nom": n, "attribution": a} for c, n, a in sources],
    }


def _tracer(conn, anomalie_id, agent, entree, reponse=None, refus=False, modele=""):
    conn.execute(
        "INSERT INTO terre.passage_agent (anomalie_id, agent, modele, entree, sortie, refus,"
        " requete_id, jetons_entree, jetons_sortie) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (anomalie_id, agent, reponse.modele if reponse else modele, Jsonb(entree),
         Jsonb(reponse.sortie.model_dump()) if reponse else None, refus,
         reponse.requete_id if reponse else None,
         reponse.jetons_entree if reponse else None, reponse.jetons_sortie if reponse else None),
    )


def _agent(conn, appeler: Appelant, anomalie_id, nom, consigne, entree, schema):
    try:
        reponse = appeler(consigne, entree, schema)
    except RefusAgent:
        _tracer(conn, anomalie_id, nom, entree, refus=True, modele=getattr(appeler, "modele", "?"))
        raise
    _tracer(conn, anomalie_id, nom, entree, reponse)
    return reponse.sortie


def _statut(conn, anomalie_id, statut, evenement_id=None):
    conn.execute("UPDATE terre.anomalie SET statut = %s, evenement_id = coalesce(%s, evenement_id)"
                 " WHERE id = %s", (statut, evenement_id, anomalie_id))


def traiter(conn: psycopg.Connection, appeler: Appelant, anomalie_id: int) -> Issue:
    """Fait passer une anomalie dans la chaîne. Tout est enregistré dans une seule transaction."""
    with conn.transaction():
        d = dossier(conn, anomalie_id)
        try:
            analyse: Analyse = _agent(conn, appeler, anomalie_id, "analyste", consignes.ANALYSTE, d, Analyse)
            if not analyse.qualifie:
                _statut(conn, anomalie_id, "ecartee")
                return Issue(anomalie_id, "ecartee", f"Analyste : {analyse.justification}")

            d_contra = d | {"analyse": analyse.model_dump()}
            contra: Contradiction = _agent(conn, appeler, anomalie_id, "contradicteur",
                                           consignes.CONTRADICTEUR, d_contra, Contradiction)
            if contra.verdict == "rejete":
                _statut(conn, anomalie_id, "ecartee")
                return Issue(anomalie_id, "ecartee", "Contradicteur : " + "; ".join(contra.explications_alternatives))
            if contra.verdict == "incertain":
                # Reste « nouvelle » : un prochain passage satellite pourra trancher.
                return Issue(anomalie_id, "incertaine", "Contradicteur : " + "; ".join(contra.objections))

            confiance = min(analyse.confiance, contra.confiance_revisee)
            niveau = "eleve" if confiance >= 0.75 else "moyen" if confiance >= 0.5 else "faible"
            d_redac = d | {"analyse": analyse.model_dump(), "confiance_retenue": NIVEAUX[niveau]}
            redaction: Redaction = _agent(conn, appeler, anomalie_id, "redacteur",
                                          consignes.REDACTEUR, d_redac, Redaction)
            d_conf = d | {"texte_a_verifier": redaction.model_dump()}
            conformite: Conformite = _agent(conn, appeler, anomalie_id, "conformite",
                                            consignes.CONFORMITE, d_conf, Conformite)
        except RefusAgent as e:
            return Issue(anomalie_id, "refus", str(e))

        evenement_id = _creer_evenement(conn, anomalie_id, d, analyse, confiance)
        texte = _texte_final(redaction.texte, d["sources"])
        pub = conn.execute(
            "INSERT INTO terre.publication (evenement_id, type, niveau, langue, titre, contenu, statut)"
            " VALUES (%s, 'alerte', 2, 'fr', %s, %s, 'brouillon') RETURNING id",
            (evenement_id, redaction.titre, texte),
        ).fetchone()[0]
        _rattacher_preuves(conn, anomalie_id, evenement_id, pub)

        licences = [s["code"] for s in d["sources"] if not _licence_ok(conn, s["code"])]
        problemes = list(conformite.problemes)
        if licences:
            problemes.append(f"licence non confirmée : {', '.join(licences)}")
        bloquant = (not conformite.conforme or conformite.mise_en_cause or conformite.sujet_securitaire
                    or conformite.affirmation_non_etayee or licences)
        for validateur, decision, conf, commentaire in (
            ("Agent Analyste", "approuve", analyse.confiance, analyse.justification),
            ("Agent Contradicteur", "approuve", contra.confiance_revisee, "; ".join(contra.objections) or None),
            ("Agent Conformité", "a_corriger" if bloquant else "approuve", None, "; ".join(problemes) or None),
        ):
            conn.execute(
                "INSERT INTO terre.validation (publication_id, validateur, role, decision, confiance, commentaire)"
                " VALUES (%s, %s, 'agent', %s, %s, %s)",
                (pub, validateur, decision, conf, commentaire),
            )
        _statut(conn, anomalie_id, "transmise", evenement_id)
        if bloquant:
            return Issue(anomalie_id, "non_conforme", "; ".join(problemes), pub)
        conn.execute("UPDATE terre.publication SET statut = 'en_validation' WHERE id = %s", (pub,))
        return Issue(anomalie_id, "a_valider", redaction.titre, pub)


def _licence_ok(conn, code: str) -> bool:
    return conn.execute("SELECT licence_verifiee FROM terre.source WHERE code = %s", (code,)).fetchone()[0]


def _texte_final(texte: str, sources: list[dict]) -> str:
    from datetime import date

    attributions = ", ".join(s["attribution"].replace("{annee}", str(date.today().year))
                             for s in sources) or "aucune"
    return f"{texte.strip()}\n\nSources : {attributions}.\n{MENTION_IA}"


def _creer_evenement(conn, anomalie_id, d, analyse: Analyse, confiance: float) -> int:
    return conn.execute(
        "INSERT INTO terre.evenement (type, zone_id, titre, debut, statut, confiance, gravite, geom)"
        " SELECT 'crue', i.zone_id, %s, a.jour, 'confirme', %s, %s, i.geom"
        " FROM terre.anomalie a JOIN terre.infrastructure i ON i.id = a.infrastructure_id"
        " WHERE a.id = %s RETURNING id",
        (f"Crue — {d['point']['nom']}", round(confiance, 2), analyse.gravite, anomalie_id),
    ).fetchone()[0]


def _rattacher_preuves(conn, anomalie_id, evenement_id, publication_id):
    """Chaque observation du dossier devient une preuve citée par la publication."""
    conn.execute(
        "WITH obs AS ("
        "  SELECT o.* FROM terre.observation o, terre.anomalie a"
        "  WHERE a.id = %s AND o.id IN (SELECT jsonb_array_elements_text(a.elements->'observations')::bigint)),"
        " p AS ("
        "  INSERT INTO terre.preuve (evenement_id, source_id, type, uri, acquis_le, traitement)"
        "  SELECT %s, source_id, 'donnee_brute', 'observation:' || id, observe_le,"
        "         variable || ' = ' || coalesce(valeur::text, 'n/a') || coalesce(' ' || unite, '')"
        "  FROM obs RETURNING id)"
        " INSERT INTO terre.publication_preuve SELECT %s, id FROM p",
        (anomalie_id, evenement_id, publication_id),
    )


def traiter_jour(conn: psycopg.Connection, appeler: Appelant, jour: date, confiance_min: float = 0.5) -> list[Issue]:
    """Traite les anomalies nouvelles du jour, des plus sûres aux moins sûres."""
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM terre.anomalie WHERE jour = %s AND statut = 'nouvelle' AND confiance >= %s"
        " ORDER BY confiance DESC", (jour, confiance_min),
    ).fetchall()]
    return [traiter(conn, appeler, i) for i in ids]
