"""Import des points surveillés depuis un CSV fourni par le directeur."""

import csv
from dataclasses import dataclass
from pathlib import Path

import psycopg

TYPES = {"route", "pont", "village", "centre_sante", "piste", "barrage"}
COLONNES = ["nom", "type", "latitude", "longitude", "zone_code", "notes"]


@dataclass
class Point:
    nom: str
    type: str
    latitude: float
    longitude: float
    zone_code: str
    notes: str | None


def lire(chemin: Path) -> list[Point]:
    """Lit et valide le CSV ; lève ValueError avec toutes les erreurs trouvées."""
    points, erreurs = [], []
    with open(chemin, newline="", encoding="utf-8") as f:
        lecteur = csv.DictReader(f)
        manquantes = {"nom", "type", "latitude", "longitude"} - set(lecteur.fieldnames or [])
        if manquantes:
            raise ValueError(f"colonnes manquantes : {', '.join(sorted(manquantes))}")
        for n, ligne in enumerate(lecteur, start=2):
            try:
                points.append(_valider(ligne))
            except ValueError as e:
                erreurs.append(f"ligne {n} : {e}")
    if erreurs:
        raise ValueError("\n".join(erreurs))
    return points


def _valider(ligne: dict) -> Point:
    nom = (ligne.get("nom") or "").strip()
    if not nom:
        raise ValueError("nom vide")
    type_ = (ligne.get("type") or "").strip()
    if type_ not in TYPES:
        raise ValueError(f"type « {type_} » inconnu (attendu : {', '.join(sorted(TYPES))})")
    try:
        lat, lon = float(ligne["latitude"]), float(ligne["longitude"])
    except (TypeError, ValueError):
        raise ValueError("latitude ou longitude non numérique") from None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("coordonnées hors limites")
    zone = (ligne.get("zone_code") or "").strip()   # vide : le pays est trouvé d'après les coordonnées
    notes = (ligne.get("notes") or "").strip() or None
    return Point(nom, type_, lat, lon, zone, notes)


def importer(conn: psycopg.Connection, points: list[Point]) -> int:
    """Insère ou met à jour les points ; vérifie qu'ils tombent dans leur zone.
    Sans zone indiquée, le point est rattaché au pays qui le contient."""
    with conn.transaction():
        for p in points:
            if not p.zone_code:
                # Côtes au 1:50 m, précises à 1-2 km : un pont sur une lagune peut tomber « en mer ».
                # On retient le pays qui contient le point, sinon le plus proche à moins de 5 km.
                pays = conn.execute(
                    "SELECT code FROM terre.zone, (SELECT ST_SetSRID(ST_MakePoint(%s, %s), 4326) AS pt) p"
                    " WHERE type = 'pays' AND ST_DWithin(geom::geography, p.pt::geography, 5000)"
                    " ORDER BY ST_Distance(geom::geography, p.pt::geography), ST_Area(geom) LIMIT 1",
                    (p.longitude, p.latitude)).fetchone()
                if pays is None:
                    raise ValueError(f"{p.nom} : aucun pays à ces coordonnées (en mer ? latitude et longitude inversées ?)")
                p.zone_code = pays[0]
            ligne = conn.execute(
                "SELECT id, ST_DWithin(geom::geography, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, 5000)"
                " FROM terre.zone WHERE code = %s",
                (p.longitude, p.latitude, p.zone_code),
            ).fetchone()
            if ligne is None:
                raise ValueError(f"{p.nom} : zone « {p.zone_code} » inconnue")
            zone_id, dedans = ligne
            if not dedans:
                raise ValueError(f"{p.nom} : hors de l'emprise de la zone {p.zone_code}")
            conn.execute(
                "INSERT INTO terre.infrastructure (zone_id, nom, type, geom, surveille, notes)"
                " VALUES (%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), true, %s)"
                " ON CONFLICT (zone_id, nom, type) DO UPDATE"
                " SET geom = EXCLUDED.geom, surveille = true, notes = EXCLUDED.notes",
                (zone_id, p.nom, p.type, p.longitude, p.latitude, p.notes),
            )
    return len(points)
