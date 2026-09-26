"""Tests de l'étape 1. Nécessitent une base PostGIS vide dans TEST_DATABASE_URL."""

import psycopg
import pytest

from oeil_bleu import db, points


def test_migration_idempotente(conn):
    assert db.migrer(conn) == []
    assert conn.execute("SELECT count(*) FROM terre.source").fetchone()[0] == 5
    assert conn.execute("SELECT count(*) FROM terre.zone").fetchone()[0] == 2


def _evenement_et_publication(conn, niveau):
    ev = conn.execute(
        "INSERT INTO terre.evenement (type, zone_id, titre, debut)"
        " SELECT 'crue', id, 'Test', now() FROM terre.zone WHERE code = 'bassin_niger'"
        " RETURNING id"
    ).fetchone()[0]
    pub = conn.execute(
        "INSERT INTO terre.publication (evenement_id, type, niveau, langue, titre, contenu)"
        " VALUES (%s, 'bulletin', %s, 'fr', 'Test', '…') RETURNING id",
        (ev, niveau),
    ).fetchone()[0]
    return ev, pub


def _ajouter_preuve(conn, ev, pub):
    pr = conn.execute(
        "INSERT INTO terre.preuve (evenement_id, source_id, type, uri, acquis_le, traitement)"
        " SELECT %s, id, 'image', 's3://test', now(), 'aucun' FROM terre.source WHERE code = 'firms'"
        " RETURNING id",
        (ev,),
    ).fetchone()[0]
    conn.execute("INSERT INTO terre.publication_preuve VALUES (%s, %s)", (pub, pr))


def test_publication_sans_preuve_refusee(conn):
    _, pub = _evenement_et_publication(conn, 1)
    with pytest.raises(psycopg.errors.RaiseException, match="aucune preuve"):
        conn.execute("UPDATE terre.publication SET statut = 'publiee', publie_le = now() WHERE id = %s", (pub,))


def test_niveau_2_exige_un_humain(conn):
    ev, pub = _evenement_et_publication(conn, 2)
    _ajouter_preuve(conn, ev, pub)
    conn.execute(
        "INSERT INTO terre.validation (publication_id, validateur, role, decision)"
        " VALUES (%s, 'Agent Contradicteur', 'agent', 'approuve')",
        (pub,),
    )
    with pytest.raises(psycopg.errors.RaiseException, match="approbation humaine"):
        conn.execute("UPDATE terre.publication SET statut = 'validee' WHERE id = %s", (pub,))
    conn.execute(
        "INSERT INTO terre.validation (publication_id, validateur, role, decision)"
        " VALUES (%s, 'Directeur de publication', 'humain', 'approuve')",
        (pub,),
    )
    conn.execute("UPDATE terre.publication SET statut = 'validee' WHERE id = %s", (pub,))


def test_niveau_3_exclu(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        _evenement_et_publication(conn, 3)


def test_import_points(conn, tmp_path):
    csv = tmp_path / "points.csv"
    csv.write_text(
        "nom,type,latitude,longitude,zone_code,notes\n"
        "Pont test,pont,14.5,-4.2,bassin_niger,\n",
        encoding="utf-8",
    )
    assert points.importer(conn, points.lire(csv)) == 1
    assert points.importer(conn, points.lire(csv)) == 1  # ré-import sans doublon
    assert conn.execute("SELECT count(*) FROM terre.point_surveille").fetchone()[0] == 1


def test_import_points_hors_zone(conn, tmp_path):
    csv = tmp_path / "points.csv"
    csv.write_text("nom,type,latitude,longitude,zone_code\nLoin,village,40,2,bassin_niger\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hors de l'emprise"):
        points.importer(conn, points.lire(csv))


def test_csv_invalide(tmp_path):
    csv = tmp_path / "points.csv"
    csv.write_text("nom,type,latitude,longitude,zone_code\n,aeroport,abc,2,\n", encoding="utf-8")
    with pytest.raises(ValueError, match="ligne 2"):
        points.lire(csv)
