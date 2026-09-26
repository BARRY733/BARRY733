"""Socle commun : téléchargement, journal des collectes, enregistrement idempotent."""

import json
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime

import psycopg

AGENT = "OeilBleu/0.1 (+https://github.com/BARRY733/BARRY733)"


@dataclass
class Observation:
    cle: str
    variable: str
    valeur: float | None
    unite: str | None
    observe_le: datetime
    longitude: float
    latitude: float
    infrastructure_id: int | None = None
    brut: dict = field(default_factory=dict)


def telecharger(url: str, delai: int = 60) -> bytes:
    requete = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(requete, timeout=delai) as reponse:
        return reponse.read()


def emprise_pilote(conn: psycopg.Connection) -> tuple[float, float, float, float]:
    """Rectangle (ouest, sud, est, nord) couvrant toutes les zones de bassin."""
    return conn.execute(
        "SELECT ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e)"
        " FROM (SELECT ST_Extent(geom) AS e FROM terre.zone WHERE type = 'bassin') s"
    ).fetchone()


def points_surveilles(conn: psycopg.Connection) -> list[tuple[int, str, float, float]]:
    """(id, nom, longitude, latitude) de chaque point surveillé."""
    return conn.execute(
        "SELECT id, nom, longitude, latitude FROM terre.point_surveille ORDER BY id"
    ).fetchall()


def executer(
    conn: psycopg.Connection,
    code_source: str,
    collecteur: Callable[[psycopg.Connection], Iterable[Observation]],
) -> int:
    """Lance un collecteur, journalise le passage et renvoie le nombre d'observations nouvelles.

    Une erreur marque la collecte « echouee » sans rien enregistrer de partiel.
    """
    ligne = conn.execute("SELECT id FROM terre.source WHERE code = %s", (code_source,)).fetchone()
    if ligne is None:
        raise ValueError(f"source « {code_source} » inconnue")
    source_id = ligne[0]
    with conn.transaction():
        collecte_id = conn.execute(
            "INSERT INTO terre.collecte (source_id) VALUES (%s) RETURNING id", (source_id,)
        ).fetchone()[0]
    try:
        with conn.transaction():
            nouvelles = 0
            for o in collecteur(conn):
                cur = conn.execute(
                    "INSERT INTO terre.observation (source_id, collecte_id, cle, variable, valeur,"
                    " unite, observe_le, geom, infrastructure_id, brut)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s,"
                    " ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s)"
                    " ON CONFLICT (source_id, cle) DO NOTHING",
                    (source_id, collecte_id, o.cle, o.variable, o.valeur, o.unite, o.observe_le,
                     o.longitude, o.latitude, o.infrastructure_id, json.dumps(o.brut, default=str)),
                )
                nouvelles += cur.rowcount
            conn.execute(
                "UPDATE terre.collecte SET fin = now(), statut = 'reussie', nb_nouvelles = %s"
                " WHERE id = %s",
                (nouvelles, collecte_id),
            )
    except Exception as e:
        with conn.transaction():
            conn.execute(
                "UPDATE terre.collecte SET fin = now(), statut = 'echouee', erreur = %s WHERE id = %s",
                (f"{type(e).__name__}: {e}", collecte_id),
            )
        raise
    return nouvelles
