"""Observatoire : instantané tiré de la base, états des sites, page autonome sûre."""

from datetime import date, datetime, timezone

import pytest

from oeil_bleu.collecte import Observation, executer
from oeil_bleu.observatoire import fragment, instantane, page_autonome


def _point(conn, nom, lon=-4.0, lat=14.0):
    return conn.execute(
        "INSERT INTO terre.infrastructure (zone_id, nom, type, geom, surveille)"
        " SELECT id, %s, 'pont', ST_SetSRID(ST_MakePoint(%s, %s), 4326), true"
        " FROM terre.zone WHERE code = 'bassin_niger' RETURNING id", (nom, lon, lat)).fetchone()[0]


def _annees(i, mouille):
    return [Observation(f"a{i}{a}", "eau_annuelle", mouille, None, datetime(a, 12, 31, tzinfo=timezone.utc),
                        -4, 14, i, {"annee": a, "passages_degages": 10}) for a in range(2015, 2024)]


def test_etats_des_sites(conn):
    fleuve = _point(conn, "Pont sur le fleuve")
    route = _point(conn, "Route inondable", -3.5, 14.0)
    _point(conn, "Point neuf", -3.0, 14.0)
    executer(conn, "deafrica_wofs", lambda c: _annees(fleuve, 8) + _annees(route, 0))
    d = instantane(conn, date(2024, 9, 10))
    etats = {s["nom"]: s["etat"] for s in d["sites"]}
    assert etats == {"Pont sur le fleuve": "eau_permanente", "Route inondable": "surveille", "Point neuf": "incomplet"}
    assert {s["pays"] for s in d["sites"]} == {"Mali"}
    assert d["fond"]["pays"] and len(d["sources"]) == 7


def test_page_autonome_sans_injection(conn):
    _point(conn, "</script><script>alert(1)</script>")
    page = page_autonome(instantane(conn, date(2024, 9, 10)))
    assert page.startswith("<!doctype html>") and "</script><script>alert(1)" not in page
    assert "/*DONNEES*/null" not in fragment(instantane(conn))


def test_route_protegee(conn, monkeypatch):
    pytest.importorskip("flask")
    import base64

    from conftest import URL
    from oeil_bleu.web import creer_app

    app = creer_app(URL, "mdp", "A. Directeur")
    client = app.test_client()
    assert client.get("/observatoire").status_code == 401
    r = client.get("/observatoire", headers={"Authorization": "Basic " + base64.b64encode(b"d:mdp").decode()})
    assert r.status_code == 200 and "État des sites surveillés" in r.get_data(as_text=True)
