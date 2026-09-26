"""Test à blanc sur une saison construite à la main."""

from datetime import date

import pytest

import rejeu
from detection import Reglages
from rejeu import Alerte, Crue, evaluer

D = date


def alerte(point_id, jour, confiance="moyen"):
    return Alerte(point_id * 100, point_id, f"P{point_id}", jour, confiance, confiance)


def test_evaluation():
    crues = [Crue(1, "P1", D(2024, 8, 20), D(2024, 9, 20), True, "UNOSAT"),
             Crue(2, "P2", D(2024, 7, 1), D(2024, 10, 31), False, "route restée ouverte"),
             Crue(4, "P4", D(2024, 9, 1), D(2024, 9, 10), True, "ReliefWeb")]
    alertes = [alerte(1, D(2024, 8, 26), "eleve"),   # confirmée, 6 jours après le début
               alerte(2, D(2024, 9, 3), "a_confirmer"),  # fausse : point resté sec
               alerte(3, D(2024, 9, 3))]                  # point absent de la vérité terrain
    b = evaluer(alertes, crues, tolerance=8)
    assert [a.statut for a in alertes] == ["confirmee", "fausse", "non_evaluable"]
    assert (b.confirmees, b.fausses, b.non_evaluables) == (1, 1, 1)
    assert b.precision == 0.5 and not b.critere_atteint
    assert b.rappel == 0.5 and b.delai_median == 6
    # Ne publier qu'à partir de « moyen » aurait écarté la fausse alerte.
    assert b.par_confiance["moyen"]["precision"] == 1.0
    assert "P4" in rejeu.rapport(b, D(2024, 7, 1), D(2024, 10, 31))  # crue manquée listée


def test_tolerance():
    crues = [Crue(1, "P1", D(2024, 9, 1), D(2024, 9, 10), True, "")]
    assert evaluer([alerte(1, D(2024, 9, 17))], crues, tolerance=8).confirmees == 1
    assert evaluer([alerte(1, D(2024, 9, 25))], [Crue(1, "P1", D(2024, 9, 1), D(2024, 9, 10), True, "")],
                   tolerance=8).fausses == 1


@pytest.fixture
def saison(cur, tmp_path):
    """Deux points : l'un inondé début septembre, l'autre avec une fausse alerte (nuage mal classé)."""
    cur.execute("INSERT INTO preuve (source_id, uri, acquise_le, traitement) "
                "SELECT id, 'x', now(), 'x' FROM source WHERE code = 'deafrica' RETURNING id")
    preuve = cur.fetchone()[0]
    ids = {}
    for nom, lon in (("Pont A", -12.1), ("Piste B", -11.5)):
        cur.execute("INSERT INTO infrastructure (nom, type, pays_iso, geom) VALUES "
                    "(%s, 'pont', 'SEN', ST_SetSRID(ST_MakePoint(%s, 15.0), 4326)) RETURNING id", (nom, lon))
        ids[nom] = cur.fetchone()[0]
        cur.execute("INSERT INTO indicateur (infrastructure_id, preuve_id, nom, valeur, unite, mesure_le) "
                    "VALUES (%s, %s, 'frequence_eau_historique', 0.03, 'r', '1984-01-01')", (ids[nom], preuve))
    scenes = {"Pont A": [("2024-08-20", 0), ("2024-09-01", 1), ("2024-09-09", 1), ("2024-09-25", 0), ("2024-10-03", 0)],
              "Piste B": [("2024-08-20", 0), ("2024-09-09", 1), ("2024-09-17", 0), ("2024-09-25", 0)]}
    for nom, serie in scenes.items():
        for quand, eau in serie:
            cur.execute("INSERT INTO indicateur (infrastructure_id, preuve_id, nom, valeur, unite, mesure_le) "
                        "VALUES (%s, %s, 'eau_observee', %s, 'b', %s)", (ids[nom], preuve, eau, quand))
    verite = tmp_path / "verite.csv"
    verite.write_text("nom,pays_iso,debut,fin,inonde,source\n"
                      "Pont A,SEN,2024-08-30,2024-09-20,oui,UNOSAT\n"
                      "Piste B,SEN,2024-07-01,2024-10-31,non,Logistics Cluster\n", encoding="utf-8")
    return str(verite)


def test_saison_rejouee_sans_trace(cur, saison):
    crues = rejeu.charger_verite(cur, saison)
    [(_, b)] = rejeu.balayer(cur, D(2024, 8, 16), D(2024, 10, 10), crues, [Reglages()])
    assert [(a.point, a.statut) for a in b.alertes] == [("Pont A", "confirmee"), ("Piste B", "fausse")]
    assert b.alertes[0].confiance_max == "eleve"   # deux scènes en eau
    assert b.alertes[1].confiance_max == "a_confirmer"
    assert b.par_confiance["moyen"]["precision"] == 1.0
    cur.execute("SELECT count(*) FROM evenement")
    assert cur.fetchone()[0] == 0  # le rejeu ne laisse aucun événement


def test_balayage(cur, saison):
    crues = rejeu.charger_verite(cur, saison)
    resultats = rejeu.balayer(cur, D(2024, 8, 16), D(2024, 10, 10), crues,
                              [Reglages(), Reglages(seuil_historique=0.02)])
    assert len(resultats[0][1].alertes) == 2
    assert resultats[1][1].alertes == []  # seuil sous la fréquence historique : plus rien


def test_verite_invalide(cur, tmp_path):
    f = tmp_path / "v.csv"
    f.write_text("nom,pays_iso,debut,fin,inonde,source\nInconnu,SEN,2024-01-01,2024-01-02,peut-etre,x\n",
                 encoding="utf-8")
    with pytest.raises(ValueError, match="point inconnu"):
        rejeu.charger_verite(cur, str(f))
