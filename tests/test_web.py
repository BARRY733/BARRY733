"""Page de validation : accès protégé, décisions, envoi du bulletin (SMTP simulé)."""

import base64
import re
from datetime import date

import numpy as np
import pytest

pytest.importorskip("flask")
pytest.importorskip("matplotlib")

from conftest import URL  # noqa: E402
from oeil_bleu import bulletin, carte  # noqa: E402
from oeil_bleu.web import creer_app  # noqa: E402
from test_bulletin import FauxSMTP, publication  # noqa: E402,F401
from test_agents import anomalie  # noqa: E402,F401

MDP = "secret-de-test"
AUTH = {"Authorization": "Basic " + base64.b64encode(f"directeur:{MDP}".encode()).decode()}


class SMTPContexte(FauxSMTP):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def client(conn, publication, monkeypatch):  # noqa: F811
    envois = SMTPContexte()
    monkeypatch.setattr(bulletin, "lire_destinataires", lambda: ["a@exemple.org"])
    grille = np.zeros((10, 10), dtype=int)
    fabrique = lambda c, pid: carte.dessiner(grille, grille, "Pont", date(2024, 9, 8), 1500)
    app = creer_app(URL, MDP, "A. Directeur", smtp=lambda: envois, fabrique_carte=fabrique)
    app.config["TESTING"] = True
    c = app.test_client()
    c.envois = envois
    return c


def jeton(client):
    page = client.get("/", headers=AUTH).get_data(as_text=True)
    return re.search(r'name="jeton" value="([0-9a-f]+)"', page).group(1)


def test_acces_refuse_sans_mot_de_passe(client):
    assert client.get("/").status_code == 401
    faux = {"Authorization": "Basic " + base64.b64encode(b"x:mauvais").decode()}
    assert client.get("/", headers=faux).status_code == 401


def test_page_affiche_le_texte_a_relire(client, publication):  # noqa: F811
    r = client.get("/", headers=AUTH)
    page = r.get_data(as_text=True)
    assert r.status_code == 200 and "Pont de Test sous les eaux" in page and "Confiance moyenne" in page
    assert "Agent Contradicteur" in page and r.headers["X-Frame-Options"] == "DENY"
    img = client.get(f"/carte/{publication}.png", headers=AUTH)
    assert img.status_code == 200 and img.data[:4] == b"\x89PNG"


def test_formulaire_sans_jeton_refuse(client, publication):  # noqa: F811
    r = client.post(f"/publication/{publication}/decision", data={"choix": "valider"}, headers=AUTH)
    assert r.status_code == 400


def test_rejet_exige_un_motif(client, publication, conn):  # noqa: F811
    t = jeton(client)
    r = client.post(f"/publication/{publication}/decision", data={"choix": "rejeter", "jeton": t},
                    headers=AUTH, follow_redirects=True)
    assert "Indiquez le motif" in r.get_data(as_text=True)
    assert conn.execute("SELECT statut FROM terre.publication").fetchone()[0] == "en_validation"
    client.post(f"/publication/{publication}/decision",
                data={"choix": "rejeter", "motif": "point mal placé", "jeton": t}, headers=AUTH)
    assert conn.execute("SELECT statut FROM terre.publication").fetchone()[0] == "retiree"


def test_validation_puis_envoi(client, publication, conn):  # noqa: F811
    t = jeton(client)
    r = client.post(f"/publication/{publication}/decision", data={"choix": "valider", "jeton": t},
                    headers=AUTH, follow_redirects=True)
    assert "validée" in r.get_data(as_text=True)
    assert conn.execute("SELECT validateur FROM terre.validation WHERE role = 'humain'").fetchone()[0] == "A. Directeur"

    apercu = client.get("/bulletin/apercu", headers=AUTH).get_data(as_text=True)
    assert "Pont de Test sous les eaux" in apercu and f"/carte/{publication}.png" in apercu

    # Sans la case cochée, rien ne part.
    r = client.post("/bulletin/envoyer", data={"jeton": t}, headers=AUTH, follow_redirects=True)
    assert "Cochez la case" in r.get_data(as_text=True) and client.envois.envoyes == []

    r = client.post("/bulletin/envoyer", data={"jeton": t, "confirmation": "oui"}, headers=AUTH, follow_redirects=True)
    assert "Bulletin envoyé : 1 alerte(s), 1 destinataire(s)." in r.get_data(as_text=True)
    assert len(client.envois.envoyes) == 1
    assert conn.execute("SELECT statut FROM terre.publication").fetchone()[0] == "publiee"
    assert "Derniers envois" in client.get("/", headers=AUTH).get_data(as_text=True)


def test_configuration_obligatoire(monkeypatch):
    monkeypatch.delenv("DIRECTEUR_MOT_DE_PASSE", raising=False)
    monkeypatch.delenv("DIRECTEUR_NOM", raising=False)
    with pytest.raises(RuntimeError, match="DIRECTEUR_MOT_DE_PASSE"):
        creer_app("postgresql://inutile")
