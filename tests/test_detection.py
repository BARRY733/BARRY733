"""Détection sur des séries d'observations construites à la main."""

from datetime import date

import pytest

import detection
from detection import detecter

JOUR = date(2024, 9, 15)


@pytest.fixture
def point(cur):
    """Un pont surveillé, sa fréquence historique d'eau, et un moyen d'ajouter des scènes."""
    cur.execute("INSERT INTO preuve (source_id, uri, acquise_le, traitement) "
                "SELECT id, 'test', now(), 'test' FROM source WHERE code = 'deafrica' RETURNING id")
    preuve = cur.fetchone()[0]

    def creer(nom="Pont de Bakel", frequence=0.02, lon=-12.1, lat=15.0):
        cur.execute("INSERT INTO infrastructure (nom, type, pays_iso, geom) "
                    "VALUES (%s, 'pont', 'SEN', ST_SetSRID(ST_MakePoint(%s, %s), 4326)) RETURNING id",
                    (nom, lon, lat))
        pid = cur.fetchone()[0]
        mesurer(pid, "frequence_eau_historique", frequence, "1984-01-01")
        return pid

    def mesurer(pid, nom, valeur, quand):
        cur.execute("INSERT INTO indicateur (infrastructure_id, preuve_id, nom, valeur, unite, mesure_le) "
                    "VALUES (%s, %s, %s, %s, 'x', %s)", (pid, preuve, nom, valeur, quand))

    def scene(pid, quand, eau):
        mesurer(pid, "eau_observee", 1.0 if eau else 0.0, quand)

    return creer, scene


def test_point_sec_rien(cur, point):
    creer, scene = point
    pid = creer()
    scene(pid, "2024-09-05", False)
    assert detecter(cur, JOUR) == []


def test_zone_souvent_en_eau_ignoree(cur, point):
    creer, scene = point
    pid = creer(frequence=0.4)  # lit mineur du fleuve : l'eau y est normale
    scene(pid, "2024-09-05", True)
    assert detecter(cur, JOUR) == []


def test_une_scene_en_eau_a_confirmer(cur, point):
    creer, scene = point
    pid = creer()
    scene(pid, "2024-09-05", True)
    [a] = detecter(cur, JOUR)
    assert (a.confiance, a.observations_eau, a.close) == ("a_confirmer", 1, False)
    cur.execute("SELECT nature, confiance FROM impact WHERE infrastructure_id = %s", (pid,))
    assert cur.fetchone() == ("menace", "a_confirmer")
    cur.execute("SELECT count(*) FROM evenement_preuve WHERE evenement_id = %s", (a.evenement_id,))
    assert cur.fetchone()[0] == 1


@pytest.mark.parametrize("frequence, attendu", [(0.02, "eleve"), (0.07, "moyen")])
def test_deux_scenes_en_eau(cur, point, frequence, attendu):
    creer, scene = point
    pid = creer(frequence=frequence)
    scene(pid, "2024-09-05", True)
    scene(pid, "2024-09-13", True)
    [a] = detecter(cur, JOUR)
    assert a.confiance == attendu


def test_corroboration_gdacs_releve_la_confiance(cur, point):
    creer, scene = point
    pid = creer()
    scene(pid, "2024-09-05", True)
    cur.execute("INSERT INTO evenement (type, source_id, ref_externe, geom, debut) "
                "SELECT 'crue', id, 'FL-1', ST_SetSRID(ST_MakePoint(-12.3, 15.1), 4326), '2024-08-30' "
                "FROM source WHERE code = 'gdacs'")
    [a] = detecter(cur, JOUR)
    assert a.corroboree and a.confiance == "moyen"


def test_meme_episode_suivi_puis_clos(cur, point):
    creer, scene = point
    pid = creer()
    scene(pid, "2024-09-05", True)
    [a] = detecter(cur, JOUR)
    scene(pid, "2024-09-13", True)
    [b] = detecter(cur, JOUR)
    assert b.evenement_id == a.evenement_id and b.confiance == "eleve"

    scene(pid, "2024-09-21", False)
    scene(pid, "2024-09-29", False)
    [c] = detecter(cur, date(2024, 9, 30))
    assert c.close and c.evenement_id == a.evenement_id
    cur.execute("SELECT fin::date FROM evenement WHERE id = %s", (a.evenement_id,))
    assert cur.fetchone()[0] == date(2024, 9, 21)

    # Une fois clos, les anciennes scènes en eau n'ouvrent pas de nouvel épisode.
    assert detecter(cur, date(2024, 9, 30)) == []

    scene(pid, "2024-10-07", True)
    [d] = detecter(cur, date(2024, 10, 8))
    assert d.evenement_id != a.evenement_id


def test_nuages_ne_degradent_pas_la_confiance(cur, point):
    creer, scene = point
    pid = creer()
    scene(pid, "2024-09-05", True)
    scene(pid, "2024-09-13", True)
    detecter(cur, JOUR)
    # Plus aucune scène claire dans la fenêtre suivante.
    [a] = detecter(cur, date(2024, 10, 20))
    assert a.confiance == "eleve" and not a.close


def test_points_independants(cur, point):
    creer, scene = point
    p1, p2 = creer("A"), creer("B", lon=-4.0, lat=13.0)
    scene(p1, "2024-09-05", True)
    scene(p2, "2024-09-05", False)
    assert [a.point for a in detecter(cur, JOUR)] == ["A"]


def test_bareme():
    assert detection.confiance(1, 0.01, False) == "a_confirmer"
    assert detection.confiance(2, 0.01, True) == "eleve"
