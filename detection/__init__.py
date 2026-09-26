"""Étape 3 : détection des crues au-delà de l'étendue historique.

Pour chaque point surveillé, les observations récentes (eau ou sec, scènes
claires uniquement) sont comparées à la fréquence historique d'eau du même
pixel. De l'eau là où il n'y en a presque jamais est une anomalie.

Le script ne juge ni la gravité ni l'impact réel sur l'accès : c'est le rôle
de l'Agent Analyste (étape 4). Il produit une liste d'anomalies, chacune avec
un niveau de confiance et ses preuves.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

# Réglages initiaux, à calibrer par le test à blanc (étape 6).
SEUIL_HISTORIQUE = 0.10   # au-delà, le point est souvent en eau : pas d'anomalie
TRES_RARE = 0.05          # en deçà, l'eau y est exceptionnelle
FENETRE_JOURS = 16        # environ deux passages Landsat
RAYON_CORROBORATION_M = 50_000
SECS_POUR_CLORE = 2       # scènes sèches consécutives qui closent un épisode

NIVEAUX = ["a_confirmer", "moyen", "eleve"]


@dataclass(frozen=True)
class Reglages:
    seuil_historique: float = SEUIL_HISTORIQUE
    tres_rare: float = TRES_RARE
    fenetre_jours: int = FENETRE_JOURS
    rayon_corroboration_m: int = RAYON_CORROBORATION_M
    secs_pour_clore: int = SECS_POUR_CLORE


DEFAUT = Reglages()


@dataclass
class Anomalie:
    point_id: int
    point: str
    evenement_id: int
    confiance: str
    observations_eau: int
    observations_claires: int
    frequence_historique: float
    corroboree: bool
    close: bool


def confiance(eau: int, frequence: float, corroboree: bool, tres_rare: float = TRES_RARE) -> str:
    niveau = 0
    if eau >= 2:
        niveau = 2 if frequence < tres_rare else 1
    if corroboree:
        niveau = min(niveau + 1, 2)
    return NIVEAUX[niveau]


def _points(cur):
    cur.execute(
        """
        SELECT i.id, i.nom, h.valeur
        FROM terre.infrastructure i
        JOIN LATERAL (SELECT valeur FROM terre.indicateur
                      WHERE infrastructure_id = i.id AND nom = 'frequence_eau_historique'
                      ORDER BY mesure_le DESC LIMIT 1) h ON true
        WHERE i.surveille
        ORDER BY i.id
        """
    )
    return cur.fetchall()


def _observations(cur, point_id, debut, fin):
    cur.execute(
        """
        SELECT id, valeur = 1, mesure_le, preuve_id FROM terre.indicateur
        WHERE infrastructure_id = %s AND nom = 'eau_observee' AND mesure_le BETWEEN %s AND %s
        ORDER BY mesure_le
        """,
        (point_id, debut, fin),
    )
    return cur.fetchall()


def _dernier_episode(cur, point_id):
    """(id, fin, confiance) du dernier épisode détecté sur ce point, ou None."""
    cur.execute(
        """
        SELECT e.id, e.fin, e.confiance FROM terre.evenement e
        JOIN terre.impact im ON im.evenement_id = e.id
        WHERE im.infrastructure_id = %s AND e.type = 'crue'
          AND e.ref_externe LIKE 'detection-%%'
        ORDER BY e.debut DESC LIMIT 1
        """,
        (point_id,),
    )
    return cur.fetchone()


def _corroboree(cur, point_id, debut, fin, exclure, rayon_m):
    """Un autre signal de crue (GDACS, par exemple) à proximité et sur la même période."""
    cur.execute(
        """
        SELECT EXISTS (
          SELECT 1 FROM terre.evenement e, terre.infrastructure i
          WHERE i.id = %s AND e.type = 'crue' AND e.id <> %s
            AND e.ref_externe NOT LIKE 'detection-%%'
            AND ST_DWithin(e.geom::geography, i.geom::geography, %s)
            AND e.debut <= %s AND coalesce(e.fin, 'infinity') >= %s)
        """,
        (point_id, exclure, rayon_m, fin, debut),
    )
    return cur.fetchone()[0]


def detecter(cur, jour: date | None = None, reglages: Reglages = DEFAUT) -> list[Anomalie]:
    jour = jour or date.today()
    fin = datetime.combine(jour, time.max, timezone.utc)
    debut = fin - timedelta(days=reglages.fenetre_jours)
    cur.execute("SELECT id FROM terre.source WHERE code = 'deafrica'")
    source_id = cur.fetchone()[0]
    anomalies = []

    for point_id, nom, frequence in _points(cur):
        dernier = _dernier_episode(cur, point_id)
        episode = dernier[0] if dernier and dernier[1] is None else None
        obs = _observations(cur, point_id, debut, fin)
        if dernier and dernier[1] is not None:
            # Épisode clos : seules les scènes postérieures peuvent en ouvrir un nouveau.
            obs = [o for o in obs if o[2] > dernier[1]]
        mouillees = [o for o in obs if o[1]]

        if frequence >= reglages.seuil_historique or (not mouillees and episode is None):
            continue

        if episode is None:
            cur.execute(
                """
                INSERT INTO terre.evenement (type, statut, source_id, ref_externe, geom, debut, confiance)
                SELECT 'crue', 'detecte', %s, %s, geom, %s, 'a_confirmer'
                FROM terre.infrastructure WHERE id = %s RETURNING id
                """,
                (source_id, f"detection-{point_id}-{mouillees[0][2]:%Y%m%d}", mouillees[0][2], point_id),
            )
            episode = cur.fetchone()[0]

        # Rattacher les observations en eau et leurs preuves à l'épisode.
        for ind_id, _, _, preuve_id in mouillees:
            cur.execute("UPDATE terre.indicateur SET evenement_id = %s WHERE id = %s", (episode, ind_id))
            cur.execute("INSERT INTO terre.evenement_preuve VALUES (%s, %s) ON CONFLICT DO NOTHING",
                        (episode, preuve_id))

        # Clôture : plusieurs scènes sèches après la dernière scène en eau.
        derniere_eau = max((o[2] for o in mouillees), default=None)
        secs_apres = [o for o in obs if not o[1] and (derniere_eau is None or o[2] > derniere_eau)]
        close = len(secs_apres) >= reglages.secs_pour_clore

        corroboree = _corroboree(cur, point_id, debut, fin, episode, reglages.rayon_corroboration_m)
        # Sans scène claire en eau dans la fenêtre (nuages), on garde le niveau acquis.
        niveau = confiance(len(mouillees), frequence, corroboree, reglages.tres_rare) if mouillees else dernier[2]
        cur.execute(
            "UPDATE terre.evenement SET confiance = %s, fin = %s WHERE id = %s",
            (niveau, secs_apres[0][2] if close else None, episode),
        )
        cur.execute(
            """
            INSERT INTO terre.impact (evenement_id, infrastructure_id, nature, confiance)
            VALUES (%s, %s, 'menace', %s)
            ON CONFLICT (evenement_id, infrastructure_id) DO UPDATE SET confiance = EXCLUDED.confiance
            """,
            (episode, point_id, niveau),
        )
        anomalies.append(Anomalie(point_id, nom, episode, niveau, len(mouillees), len(obs),
                                  frequence, corroboree, close))
    return anomalies
