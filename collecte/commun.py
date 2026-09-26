"""Outils partagés par les collecteurs : téléchargement, archivage des
données brutes comme preuves, emprise de la zone pilote, écriture des
événements dans le Modèle Terre."""

import hashlib
import json
import os
import pathlib
import time
import urllib.request
from datetime import datetime, timezone

import psycopg

DONNEES_BRUTES = pathlib.Path(os.environ.get("DONNEES_BRUTES", "data/brut"))
AGENT = "OeilBleu-collecte/0.1"


def telecharger(url: str, donnees: dict | None = None, essais: int = 3) -> bytes:
    """GET, ou POST JSON si `donnees` est fourni. Réessaie sur erreur réseau."""
    corps = json.dumps(donnees).encode() if donnees is not None else None
    requete = urllib.request.Request(url, data=corps, headers={"User-Agent": AGENT})
    if corps is not None:
        requete.add_header("Content-Type", "application/json")
    for essai in range(essais):
        try:
            with urllib.request.urlopen(requete, timeout=60) as r:
                return r.read()
        except OSError:
            if essai == essais - 1:
                raise
            time.sleep(2 ** (essai + 1))
    raise AssertionError("inaccessible")


def id_source(cur, code: str) -> int:
    cur.execute("SELECT id FROM terre.source WHERE code = %s", (code,))
    ligne = cur.fetchone()
    if ligne is None:
        raise LookupError(f"source inconnue : {code}")
    return ligne[0]


def archiver(cur, code_source: str, contenu: bytes, extension: str,
             url: str, traitement: str, acquise_le: datetime | None = None) -> int:
    """Conserve la donnée brute telle que reçue et l'enregistre comme preuve."""
    acquise_le = acquise_le or datetime.now(timezone.utc)
    empreinte = hashlib.sha256(contenu).hexdigest()
    dossier = DONNEES_BRUTES / code_source / acquise_le.strftime("%Y-%m-%d")
    dossier.mkdir(parents=True, exist_ok=True)
    chemin = dossier / f"{empreinte[:16]}.{extension}"
    chemin.write_bytes(contenu)
    cur.execute(
        "INSERT INTO terre.preuve (source_id, uri, acquise_le, traitement, empreinte_sha256) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (id_source(cur, code_source), f"{url} -> {chemin}", acquise_le, traitement, empreinte),
    )
    return cur.fetchone()[0]


def emprise(cur, marge: float = 0.5) -> tuple[float, float, float, float]:
    """(ouest, sud, est, nord) couvrant les points surveillés, élargie de `marge` degrés.
    La variable EMPRISE="o,s,e,n" la remplace."""
    if "EMPRISE" in os.environ:
        o, s, e, n = (float(v) for v in os.environ["EMPRISE"].split(","))
        return o, s, e, n
    cur.execute(
        "SELECT ST_XMin(b), ST_YMin(b), ST_XMax(b), ST_YMax(b) FROM "
        "(SELECT ST_Extent(geom) AS b FROM terre.infrastructure WHERE surveille) x"
    )
    o, s, e, n = cur.fetchone()
    if o is None:
        raise LookupError("aucun point surveillé : charger la liste ou définir EMPRISE")
    return o - marge, s - marge, e + marge, n + marge


def dans_emprise(lon: float, lat: float, boite) -> bool:
    o, s, e, n = boite
    return o <= lon <= e and s <= lat <= n


def enregistrer_evenement(cur, code_source: str, ref: str, type_: str, lon: float, lat: float,
                          debut: datetime, fin: datetime | None, gravite: int | None,
                          confiance: str, preuve_id: int) -> int:
    """Crée ou met à jour l'événement signalé par une source et rattache la preuve."""
    cur.execute(
        """
        INSERT INTO terre.evenement (type, source_id, ref_externe, geom, debut, fin, gravite, confiance)
        VALUES (%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s, %s, %s)
        ON CONFLICT (source_id, ref_externe) DO UPDATE
          SET fin = EXCLUDED.fin, gravite = EXCLUDED.gravite, confiance = EXCLUDED.confiance
        RETURNING id
        """,
        (type_, id_source(cur, code_source), ref, lon, lat, debut, fin, gravite, confiance),
    )
    evenement_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO terre.evenement_preuve VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (evenement_id, preuve_id),
    )
    return evenement_id


def connexion() -> psycopg.Connection:
    return psycopg.connect(os.environ["DATABASE_URL"])
