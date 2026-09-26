"""Tests de l'étape 6 : épisodes, notation, rejeu sans trace, pagination STAC."""

import io
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from oeil_bleu import rejeu
from oeil_bleu.collecte import Observation, deafrica, executer
from oeil_bleu.rejeu import Crue, episodes, evaluer

J = date(2024, 9, 1)


def jours(point, premier, n, niveau="moyen", conf=0.6):
    return [(premier + timedelta(days=i), point, niveau, conf) for i in range(n)]


def test_episodes_regroupent_les_jours_consecutifs():
    alertes = jours("A", J, 3) + jours("A", J + timedelta(days=10), 2, "eleve", 0.8) + jours("B", J, 1, "faible", 0.4)
    eps = episodes(alertes, "faible")
    assert [(e.point, e.debut.day, e.fin.day, e.niveau_max) for e in eps] == [
        ("A", 1, 3, "moyen"), ("B", 1, 1, "faible"), ("A", 11, 12, "eleve")]
    assert [e.point for e in episodes(alertes, "eleve")] == ["A"]


def test_notation():
    crues = [Crue("A", J + timedelta(days=1), J + timedelta(days=5)), Crue("C", J, J + timedelta(days=3))]
    alertes = (jours("A", J, 3)                                   # juste, 1 jour d'avance
               + jours("A", J + timedelta(days=11), 1)            # juste : dans la tolérance de 7 jours
               + jours("A", J + timedelta(days=20), 1)            # fausse : trop tard
               + jours("B", J, 2))                                # fausse : aucune crue
    score, eps = evaluer(alertes, crues, "moyen")
    assert (score.episodes, score.justes, score.fausses) == (4, 2, 2)
    assert (score.crues, score.detectees) == (2, 1)
    assert score.taux_fausses_alertes == 0.5 and score.taux_detection == 0.5
    assert score.delais == [-1]


def test_score_vide():
    score, _ = evaluer([], [], "eleve")
    assert score.taux_fausses_alertes is None and score.taux_detection is None and score.delai_median is None
    assert "—" in rejeu.rapport([score], J, J, [])


def test_lire_verite(tmp_path):
    f = tmp_path / "v.csv"
    f.write_text("point,debut,fin,source\nPont,2024-09-01,2024-09-05,OCHA\n", encoding="utf-8")
    assert rejeu.lire_verite(f) == [Crue("Pont", date(2024, 9, 1), date(2024, 9, 5), "OCHA")]
    f.write_text("point,debut,fin\nPont,2024-09-05,2024-09-01\n", encoding="utf-8")
    with pytest.raises(ValueError, match="fin avant début"):
        rejeu.lire_verite(f)
    f.write_text("point,debut,fin\nPont,05/09/2024,2024-09-06\n", encoding="utf-8")
    with pytest.raises(ValueError, match="ligne 2"):
        rejeu.lire_verite(f)


def test_test_a_blanc_sur_la_base(conn, tmp_path):
    infra = conn.execute(
        "INSERT INTO terre.infrastructure (zone_id, nom, type, geom, surveille)"
        " SELECT id, 'Pont', 'pont', ST_SetSRID(ST_MakePoint(-4, 14), 4326), true"
        " FROM terre.zone WHERE code = 'bassin_niger' RETURNING id").fetchone()[0]
    t = datetime(2024, 9, 3, 10, tzinfo=timezone.utc)
    obs = lambda cle, var, val, quand: Observation(cle, var, val, None, quand, -4, 14, infra)
    executer(conn, "deafrica_wofs", lambda c: [
        obs("f", "frequence_eau_historique", 0.01, datetime(1984, 1, 1, tzinfo=timezone.utc)),
        obs("w", "eau_observee", 1, t)])
    verite = tmp_path / "v.csv"
    verite.write_text("point,debut,fin,source\nPont,2024-09-02,2024-09-10,rapport\n", encoding="utf-8")

    texte = rejeu.test_a_blanc(conn, date(2024, 9, 1), date(2024, 9, 30), verite, tmp_path / "e.csv")
    # Eau vue le 3 : alerte du 3 au 18 (16 jours de fenêtre), un seul épisode, juste.
    assert "moyen" in texte and "100%" in texte
    lignes = (tmp_path / "e.csv").read_text(encoding="utf-8").splitlines()
    assert "faible,Pont,2024-09-03,2024-09-18,moyen,0.60,juste,rapport" in lignes
    # Le rejeu ne laisse aucune trace.
    assert conn.execute("SELECT count(*) FROM terre.anomalie").fetchone()[0] == 0


def test_verite_avec_point_inconnu(conn, tmp_path):
    verite = tmp_path / "v.csv"
    verite.write_text("point,debut,fin\nNulle part,2024-09-02,2024-09-10\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Nulle part"):
        rejeu.test_a_blanc(conn, J, J, verite)


def test_stac_suit_les_pages(monkeypatch):
    pages = [
        {"features": [{"id": "a"}], "links": [{"rel": "next", "href": "https://x/page2", "method": "GET"}]},
        {"features": [{"id": "b"}], "links": []},
    ]
    appels = []

    def faux_urlopen(requete, timeout):
        appels.append((requete.get_method(), requete.full_url))
        return io.BytesIO(json.dumps(pages[len(appels) - 1]).encode())

    monkeypatch.setattr(deafrica.urllib.request, "urlopen", faux_urlopen)
    assert [e["id"] for e in deafrica.chercher("wofs_ls", [0, 0, 1, 1], None)] == ["a", "b"]
    assert appels == [("POST", deafrica.STAC), ("GET", "https://x/page2")]
