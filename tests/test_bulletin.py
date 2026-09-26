"""Bulletin : décisions du directeur, carte, composition, envoi tout ou rien."""

import pytest
from datetime import date

import bulletin
from bulletin import carte
from tests.test_collecte import raster


@pytest.fixture
def texte_soumis(cur, tmp_path):
    """Un événement détecté, cartographiable, et son texte soumis par l'Agent Conformité."""
    cur.execute("INSERT INTO infrastructure (nom, type, pays_iso, geom) "
                "VALUES ('Pont de Bakel', 'pont', 'SEN', ST_SetSRID(ST_MakePoint(-12.1, 15.0), 4326)) RETURNING id")
    pid = cur.fetchone()[0]
    cur.execute("INSERT INTO preuve (source_id, uri, acquise_le, traitement) "
                "SELECT id, 'stac', now(), 'pixel' FROM source WHERE code = 'deafrica' RETURNING id")
    preuve = cur.fetchone()[0]
    cur.execute("INSERT INTO evenement (type, debut, geom) VALUES ('crue', '2024-09-05', "
                "ST_SetSRID(ST_MakePoint(-12.1, 15.0), 4326)) RETURNING id")
    eid = cur.fetchone()[0]
    cur.execute("INSERT INTO impact VALUES (%s, %s, 'menace', 'eleve')", (eid, pid))
    cur.execute("INSERT INTO indicateur (infrastructure_id, evenement_id, preuve_id, nom, valeur, unite, mesure_le, ressource) "
                "VALUES (%s, NULL, %s, 'frequence_eau_historique', 0.02, 'ratio', '1984-01-01', %s), "
                "(%s, %s, %s, 'eau_observee', 1, 'booleen', '2024-09-05', %s)",
                (pid, preuve, raster(tmp_path / "f.tif", 0.02, "float32"),
                 pid, eid, preuve, raster(tmp_path / "w.tif", 128)))
    cur.execute("INSERT INTO publication (type, niveau, evenement_id, langue, titre, contenu, confiance, statut) "
                "VALUES ('alerte', 2, %s, 'fr', 'Crue au pont de Bakel', 'De l''eau <b>recouvre</b> le pont.', "
                "'eleve', 'soumis') RETURNING id", (eid,))
    pub = cur.fetchone()[0]
    cur.execute("INSERT INTO publication_preuve VALUES (%s, %s)", (pub, preuve))
    return pub, eid


def test_carte_png(cur, texte_soumis):
    _, eid = texte_soumis
    png = carte.carte_evenement(cur, eid)
    assert png.startswith(b"\x89PNG") and len(png) > 5000


def test_carte_absente_sans_couches(cur):
    cur.execute("INSERT INTO evenement (type, debut) VALUES ('crue', now()) RETURNING id")
    assert carte.carte_evenement(cur, cur.fetchone()[0]) is None


def test_decisions_du_directeur(cur, texte_soumis):
    pub, _ = texte_soumis
    assert [r[0] for r in bulletin.a_valider(cur)] == [pub]
    bulletin.decider(cur, pub, True)
    assert bulletin.a_valider(cur) == []
    with pytest.raises(ValueError, match="rien à décider"):
        bulletin.decider(cur, pub, True)


def test_rejet_renvoie_en_brouillon(cur, texte_soumis):
    pub, _ = texte_soumis
    bulletin.decider(cur, pub, False, "Titre trop affirmatif")
    cur.execute("SELECT statut FROM publication WHERE id = %s", (pub,))
    assert cur.fetchone()[0] == "brouillon"
    assert bulletin.articles_prets(cur) == []


def test_envoi(cur, texte_soumis, monkeypatch):
    pub, _ = texte_soumis
    monkeypatch.setenv("BULLETIN_DESTINATAIRES", "a@ong.org, b@onu.org")
    bulletin.decider(cur, pub, True)
    envoyes = []
    bid = bulletin.envoyer(cur, date(2024, 9, 15), envoi=envoyes.append)

    [msg] = envoyes
    assert msg["Subject"] == "Œil Bleu, bulletin du 15/09/2024"
    assert msg["Bcc"] == "a@ong.org, b@onu.org"
    html = msg.get_body(("html",)).get_content()
    assert "&lt;b&gt;recouvre&lt;/b&gt;" in html          # le texte des agents est échappé
    assert "Confiance élevée" in html and "Données Digital Earth Africa" in html
    images = [p for p in msg.walk() if p.get_content_type() == "image/png"]
    assert len(images) == 1 and images[0]["Content-ID"][1:-1] in html

    cur.execute("SELECT statut, publie_le IS NOT NULL FROM publication WHERE id = %s", (pub,))
    assert cur.fetchone() == ("publie", True)
    cur.execute("SELECT destinataires FROM bulletin WHERE id = %s", (bid,))
    assert cur.fetchone()[0] == 2
    assert bulletin.envoyer(cur, envoi=envoyes.append) is None  # déjà diffusé


def test_texte_non_valide_jamais_envoye(cur, texte_soumis, monkeypatch):
    monkeypatch.setenv("BULLETIN_DESTINATAIRES", "a@ong.org")
    assert bulletin.envoyer(cur, envoi=pytest.fail) is None


def test_echec_d_envoi_ne_marque_rien(cur, texte_soumis, monkeypatch):
    pub, _ = texte_soumis
    monkeypatch.setenv("BULLETIN_DESTINATAIRES", "a@ong.org")
    bulletin.decider(cur, pub, True)
    cur.execute("SAVEPOINT avant")

    def panne(msg):
        raise OSError("serveur SMTP injoignable")

    with pytest.raises(OSError):
        bulletin.envoyer(cur, envoi=panne)
    cur.execute("ROLLBACK TO SAVEPOINT avant")  # ce que fait la transaction de la commande
    cur.execute("SELECT statut FROM publication WHERE id = %s", (pub,))
    assert cur.fetchone()[0] == "valide"
