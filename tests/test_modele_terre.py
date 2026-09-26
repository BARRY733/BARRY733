"""Vérifie que les règles éditoriales sont tenues par la base elle-même."""

import pathlib
import sys

import psycopg
import pytest

RACINE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE / "scripts"))
import charger_points  # noqa: E402


def nouvelle_publication(cur, niveau=1, avec_preuve=True):
    cur.execute(
        "INSERT INTO publication (type, niveau, langue, titre, contenu, confiance) "
        "VALUES ('alerte', %s, 'fr', 'Crue', 'Texte', 'moyen') RETURNING id",
        (niveau,),
    )
    pub = cur.fetchone()[0]
    if avec_preuve:
        cur.execute(
            "INSERT INTO preuve (source_id, uri, acquise_le, traitement) "
            "SELECT id, 's3://images/s1.tif', now(), 'seuil radar' FROM source "
            "WHERE code = 'sentinel1' RETURNING id"
        )
        cur.execute("INSERT INTO publication_preuve VALUES (%s, %s)", (pub, cur.fetchone()[0]))
    return pub


def publier(cur, pub):
    cur.execute("UPDATE publication SET statut = 'publie' WHERE id = %s", (pub,))


def test_sources_chargees(cur):
    cur.execute("SELECT count(*) FROM source")
    assert cur.fetchone()[0] == 12


def test_publication_sans_preuve_refusee(cur):
    pub = nouvelle_publication(cur, avec_preuve=False)
    with pytest.raises(psycopg.errors.RaiseException, match="aucune preuve"):
        publier(cur, pub)


def test_niveau_1_avec_preuve_publie(cur):
    pub = nouvelle_publication(cur)
    publier(cur, pub)
    cur.execute("SELECT publie_le IS NOT NULL, mention_ia FROM publication WHERE id = %s", (pub,))
    date_ok, mention = cur.fetchone()
    assert date_ok and "IA" in mention
    cur.execute("SELECT count(*) FROM lignage WHERE publication_id = %s", (pub,))
    assert cur.fetchone()[0] == 1


def test_niveau_2_exige_le_directeur(cur):
    pub = nouvelle_publication(cur, niveau=2)
    cur.execute(
        "INSERT INTO validation (publication_id, validateur, decision) "
        "VALUES (%s, 'agent_conformite', 'valide')",
        (pub,),
    )
    cur.execute("SAVEPOINT s")
    with pytest.raises(psycopg.errors.RaiseException, match="directeur"):
        publier(cur, pub)
    cur.execute("ROLLBACK TO SAVEPOINT s")
    cur.execute(
        "INSERT INTO validation (publication_id, validateur, decision) "
        "VALUES (%s, 'directeur', 'valide')",
        (pub,),
    )
    publier(cur, pub)


def test_rejet_posterieur_du_directeur_bloque(cur):
    pub = nouvelle_publication(cur, niveau=2)
    cur.execute(
        "INSERT INTO validation (publication_id, validateur, decision, cree_le) VALUES "
        "(%s, 'directeur', 'valide', now() - interval '1 hour'), "
        "(%s, 'directeur', 'rejete', now())",
        (pub, pub),
    )
    with pytest.raises(psycopg.errors.RaiseException, match="directeur"):
        publier(cur, pub)


def test_niveau_3_exclu(cur):
    with pytest.raises(psycopg.errors.CheckViolation):
        nouvelle_publication(cur, niveau=3)


def test_creation_directement_publiee_refusee(cur):
    with pytest.raises(psycopg.errors.RaiseException, match="brouillon"):
        cur.execute(
            "INSERT INTO publication (type, niveau, langue, titre, contenu, confiance, statut) "
            "VALUES ('alerte', 1, 'fr', 'X', 'Y', 'moyen', 'publie')"
        )


def test_publie_ni_efface_ni_modifie(cur):
    pub = nouvelle_publication(cur)
    publier(cur, pub)
    cur.execute("SAVEPOINT s")
    with pytest.raises(psycopg.errors.RaiseException, match="effacement interdit"):
        cur.execute("DELETE FROM publication WHERE id = %s", (pub,))
    cur.execute("ROLLBACK TO SAVEPOINT s")
    with pytest.raises(psycopg.errors.RaiseException, match="ne se modifie pas"):
        cur.execute("UPDATE publication SET contenu = 'autre' WHERE id = %s", (pub,))
    cur.execute("ROLLBACK TO SAVEPOINT s")
    with pytest.raises(psycopg.errors.RaiseException, match="figées"):
        cur.execute("DELETE FROM publication_preuve WHERE publication_id = %s", (pub,))


def test_correction_visible(cur):
    pub = nouvelle_publication(cur)
    publier(cur, pub)
    cur.execute("UPDATE publication SET statut = 'corrige' WHERE id = %s", (pub,))
    cur.execute(
        "INSERT INTO publication (type, niveau, langue, titre, contenu, confiance, "
        "corrige_id, motif_correction) VALUES ('alerte', 1, 'fr', 'Crue (corrigé)', "
        "'Texte corrigé', 'eleve', %s, 'Surface surestimée') RETURNING id",
        (pub,),
    )
    assert cur.fetchone()[0] != pub


def test_chargement_points(tmp_path, cur):
    csv = tmp_path / "points.csv"
    csv.write_text(
        "nom,latitude,longitude,type,pays_iso,notes\n"
        "Pont test,14.5,-11.0,pont,sen,\n"
        "Centre test,13.1,-4.2,Centre_Sante,MLI,accès saison sèche\n",
        encoding="utf-8",
    )
    points = charger_points.lire(str(csv))
    assert charger_points.charger(cur.connection, points) == 2
    charger_points.charger(cur.connection, points)  # idempotent
    cur.execute("SELECT count(*), min(ST_X(geom)) FROM infrastructure")
    assert cur.fetchone() == (2, -11.0)


@pytest.mark.parametrize(
    "ligne, message",
    [
        ("A,95,0,pont,SEN,", "hors limites"),
        ("A,x,0,pont,SEN,", "non numériques"),
        ("A,10,0,hangar,SEN,", "type inconnu"),
        ("A,10,0,pont,SN,", "alpha-3"),
        (",10,0,pont,SEN,", "nom vide"),
    ],
)
def test_lignes_invalides_rejetees(tmp_path, ligne, message):
    csv = tmp_path / "points.csv"
    csv.write_text("nom,latitude,longitude,type,pays_iso,notes\n" + ligne + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        charger_points.lire(str(csv))
