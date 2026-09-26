"""Tests de l'étape 5 : validation humaine, carte, composition et envoi (SMTP simulé)."""

import smtplib
from datetime import date

import numpy as np
import pytest

pytest.importorskip("matplotlib")
pytest.importorskip("pydantic")

from oeil_bleu import bulletin, carte  # noqa: E402
from oeil_bleu.agents.chaine import traiter  # noqa: E402
from test_agents import ANALYSE, CONFIRME, CONFORME, JOUR, REDACTION, Faux, anomalie  # noqa: E402,F401


class FauxSMTP:
    def __init__(self, refuser=()):
        self.envoyes, self.refuser = [], set(refuser)

    def send_message(self, m):
        if m["To"] in self.refuser:
            raise smtplib.SMTPRecipientsRefused({m["To"]: (550, b"inconnu")})
        self.envoyes.append(m)


@pytest.fixture
def publication(conn, anomalie):  # noqa: F811
    conn.execute("UPDATE terre.source SET licence_verifiee = true")
    faux = Faux(analyste=ANALYSE, contradicteur=CONFIRME, redacteur=REDACTION, conformite=CONFORME)
    return traiter(conn, faux, anomalie).publication_id


def test_classement_avant_apres():
    frequence = np.array([[0.9, 0.0], [0.0, np.nan]])
    wofs = np.array([[128, 128], [0, 64]])
    avant, apres = carte.classer(frequence, wofs)
    assert avant.tolist() == [[carte.HABITUELLE, carte.SEC_], [carte.SEC_, carte.SEC_]]
    assert apres.tolist() == [[carte.HABITUELLE, carte.NOUVELLE], [carte.SEC_, carte.NON_VUE]]


def test_dessin_png():
    avant = np.zeros((20, 20), dtype=int)
    apres = np.full((20, 20), carte.NON_VUE)
    png = carte.dessiner(avant, apres, "Pont", JOUR, 1500)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_carte_sans_images_satellites(conn, publication):
    # Les observations de test n'ont pas de lien d'image : pas de carte, sans erreur.
    assert carte.carte_publication(conn, publication) is None


def test_validation_par_le_directeur(conn, publication):
    assert [r[0] for r in bulletin.a_valider(conn)] == [publication]
    bulletin.decider(conn, publication, "A. Directeur", approuve=True)
    assert conn.execute("SELECT statut FROM terre.publication").fetchone()[0] == "validee"
    with pytest.raises(ValueError, match="pas en validation"):
        bulletin.decider(conn, publication, "A. Directeur", approuve=False)


def test_rejet(conn, publication):
    bulletin.decider(conn, publication, "A. Directeur", approuve=False, motif="point mal placé")
    assert conn.execute("SELECT statut FROM terre.publication").fetchone()[0] == "retiree"
    assert bulletin.entrees_pretes(conn) == []


def _carte_factice(conn, pid):
    return carte.dessiner(np.zeros((10, 10), dtype=int), np.full((10, 10), carte.NOUVELLE), "Pont", JOUR, 1500)


def test_envoi_complet(conn, publication):
    bulletin.decider(conn, publication, "A. Directeur", approuve=True)
    entrees = bulletin.entrees_pretes(conn)
    bulletin.ajouter_cartes(conn, entrees, _carte_factice)
    smtp = FauxSMTP(refuser={"b@exemple.org"})
    bid, n, echecs = bulletin.envoyer(conn, JOUR, ["a@exemple.org", "b@exemple.org"], smtp,
                                      "Œil Bleu <bulletin@exemple.org>", entrees)
    assert (n, len(smtp.envoyes), len(echecs)) == (1, 1, 1)

    m = smtp.envoyes[0]
    assert m["To"] == "a@exemple.org" and "1 alerte" in m["Subject"]
    html = m.get_body(("html",)).get_content()
    assert "Pont de Test sous les eaux" in html and "Confiance moyenne" in html and "cid:" in html
    assert any(p.get_content_type() == "image/png" for p in m.walk())
    assert "10 septembre 2024" in m.get_body(("plain",)).get_content()

    statut, publie_le = conn.execute("SELECT statut, publie_le FROM terre.publication").fetchone()
    assert statut == "publiee" and publie_le is not None
    assert conn.execute("SELECT nb_destinataires, nb_echecs FROM terre.bulletin WHERE id = %s",
                        (bid,)).fetchone() == (1, 1)
    # Déjà envoyée : elle ne repart pas.
    assert bulletin.entrees_pretes(conn) == []


def test_rien_a_envoyer(conn):
    assert bulletin.envoyer(conn, JOUR, ["a@exemple.org"], FauxSMTP(), "x@exemple.org", []) == (0, 0, [])
    bid, n, _ = bulletin.envoyer(conn, JOUR, ["a@exemple.org"], FauxSMTP(), "x@exemple.org", [], meme_vide=True)
    assert bid and n == 0


def test_tous_les_envois_echouent(conn, publication):
    bulletin.decider(conn, publication, "A. Directeur", approuve=True)
    entrees = bulletin.entrees_pretes(conn)
    with pytest.raises(RuntimeError, match="aucun envoi"):
        bulletin.envoyer(conn, JOUR, ["b@exemple.org"], FauxSMTP(refuser={"b@exemple.org"}), "x@e.org", entrees)
    # Rien n'est marqué publié : on pourra renvoyer.
    assert conn.execute("SELECT statut FROM terre.publication").fetchone()[0] == "validee"


def test_html_echappe_le_contenu():
    e = bulletin.Entree(1, "<script>", "a < b", "Lieu & Co", "pont", "eleve")
    page = bulletin.composer_html([e], date(2024, 9, 10))
    assert "<script>" not in page and "&lt;script&gt;" in page and "Lieu &amp; Co" in page


def test_destinataires(tmp_path):
    f = tmp_path / "d.csv"
    f.write_text("courriel,nom\na@exemple.org,A\n,vide\n", encoding="utf-8")
    assert bulletin.lire_destinataires(f) == ["a@exemple.org"]
    with pytest.raises(FileNotFoundError):
        bulletin.lire_destinataires(tmp_path / "absent.csv")
