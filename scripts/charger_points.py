"""Charge la liste des points surveillés dans le Modèle Terre.

Usage : python scripts/charger_points.py data/points_surveilles.csv

Colonnes attendues : nom, latitude, longitude, type, pays_iso, notes.
Le chargement est idempotent : un point existant (même nom, même pays) est
mis à jour. Toute ligne invalide bloque l'ensemble du chargement.
"""

import csv
import os
import sys
from dataclasses import dataclass

import psycopg

TYPES = {"route", "pont", "village", "centre_sante", "piste", "barrage", "autre"}
COLONNES = ["nom", "latitude", "longitude", "type", "pays_iso", "notes"]


@dataclass
class Point:
    nom: str
    latitude: float
    longitude: float
    type: str
    pays_iso: str
    notes: str | None


def lire(chemin: str) -> list[Point]:
    with open(chemin, newline="", encoding="utf-8") as f:
        lecteur = csv.DictReader(f)
        manquantes = set(COLONNES) - set(lecteur.fieldnames or [])
        if manquantes:
            raise ValueError(f"colonnes manquantes : {', '.join(sorted(manquantes))}")
        points, erreurs = [], []
        for n, ligne in enumerate(lecteur, start=2):
            try:
                points.append(valider(ligne))
            except ValueError as e:
                erreurs.append(f"ligne {n} : {e}")
    if erreurs:
        raise ValueError("\n".join(erreurs))
    return points


def valider(ligne: dict) -> Point:
    nom = (ligne["nom"] or "").strip()
    if not nom:
        raise ValueError("nom vide")
    try:
        lat, lon = float(ligne["latitude"]), float(ligne["longitude"])
    except (TypeError, ValueError):
        raise ValueError("coordonnées non numériques")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f"coordonnées hors limites ({lat}, {lon})")
    type_ = (ligne["type"] or "").strip().lower()
    if type_ not in TYPES:
        raise ValueError(f"type inconnu « {type_} » (attendu : {', '.join(sorted(TYPES))})")
    pays = (ligne["pays_iso"] or "").strip().upper()
    if len(pays) != 3 or not pays.isalpha():
        raise ValueError(f"code pays ISO 3166 alpha-3 attendu, reçu « {pays} »")
    notes = (ligne.get("notes") or "").strip() or None
    return Point(nom, lat, lon, type_, pays, notes)


def charger(conn: psycopg.Connection, points: list[Point]) -> int:
    with conn.transaction(), conn.cursor() as cur:
        for p in points:
            cur.execute(
                """
                INSERT INTO terre.infrastructure (nom, type, pays_iso, notes, geom)
                VALUES (%s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                ON CONFLICT (nom, pays_iso) DO UPDATE
                  SET type = EXCLUDED.type, notes = EXCLUDED.notes, geom = EXCLUDED.geom
                """,
                (p.nom, p.type, p.pays_iso, p.notes, p.longitude, p.latitude),
            )
        # Rattache chaque point à la zone la plus fine qui le contient, si elle existe.
        cur.execute(
            """
            UPDATE terre.infrastructure i SET zone_id = (
              SELECT z.id FROM terre.zone z
              WHERE ST_Contains(z.geom, i.geom)
              ORDER BY ST_Area(z.geom) LIMIT 1)
            WHERE i.zone_id IS NULL
            """
        )
    return len(points)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    try:
        points = lire(sys.argv[1])
    except ValueError as e:
        sys.exit(f"Chargement annulé :\n{e}")
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        n = charger(conn, points)
    print(f"{n} points surveillés chargés.")


if __name__ == "__main__":
    main()
