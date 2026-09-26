"""Intégrité des publications : la base refuse ce qui engagerait à tort le directeur."""

import psycopg
import pytest

pytest.importorskip("matplotlib")
pytest.importorskip("pydantic")

from oeil_bleu import bulletin  # noqa: E402
from test_agents import JOUR, anomalie  # noqa: E402,F401
from test_bulletin import FauxSMTP, publication  # noqa: E402,F401

REFUS = (psycopg.errors.RaiseException, psycopg.errors.CheckViolation)


def refuse(conn, sql, *params):
    with pytest.raises(REFUS):
        with conn.transaction():
            conn.execute(sql, params)


def _publier(conn, pid):
    bulletin.decider(conn, pid, "A. Directeur", approuve=True)
    bulletin.envoyer(conn, JOUR, ["a@exemple.org"], FauxSMTP(), "x@exemple.org", bulletin.entrees_pretes(conn))


def test_un_accord_suivi_d_un_rejet_vaut_rejet(conn, publication):  # noqa: F811
    bulletin.decider(conn, publication, "A. Directeur", approuve=True)
    conn.execute("INSERT INTO terre.validation (publication_id, validateur, role, decision)"
                 " VALUES (%s, 'A. Directeur', 'humain', 'rejete')", (publication,))
    refuse(conn, "UPDATE terre.publication SET statut = 'publiee', publie_le = now() WHERE id = %s", publication)


def test_texte_publie_fige(conn, publication):  # noqa: F811
    _publier(conn, publication)
    refuse(conn, "UPDATE terre.publication SET contenu = 'réécrit' WHERE id = %s", publication)
    refuse(conn, "UPDATE terre.publication SET titre = 'autre' WHERE id = %s", publication)
    refuse(conn, "UPDATE terre.publication SET publie_le = now() - interval '1 day' WHERE id = %s", publication)
    refuse(conn, "UPDATE terre.publication SET statut = 'brouillon', publie_le = NULL WHERE id = %s", publication)


def test_texte_valide_fige_avant_envoi(conn, publication):  # noqa: F811
    bulletin.decider(conn, publication, "A. Directeur", approuve=True)
    refuse(conn, "UPDATE terre.publication SET contenu = 'changé après accord' WHERE id = %s", publication)


def test_publication_ineffacable(conn, publication):  # noqa: F811
    _publier(conn, publication)
    refuse(conn, "DELETE FROM terre.publication WHERE id = %s", publication)


def test_preuves_et_decisions_indetachables(conn, publication):  # noqa: F811
    _publier(conn, publication)
    refuse(conn, "DELETE FROM terre.publication_preuve WHERE publication_id = %s", publication)
    refuse(conn, "DELETE FROM terre.validation WHERE publication_id = %s", publication)
    refuse(conn, "UPDATE terre.validation SET decision = 'rejete' WHERE publication_id = %s", publication)
    refuse(conn, "DELETE FROM terre.bulletin_publication WHERE publication_id = %s", publication)
    refuse(conn, "DELETE FROM terre.bulletin")


def test_un_brouillon_reste_modifiable(conn, publication):  # noqa: F811
    conn.execute("UPDATE terre.publication SET statut = 'brouillon' WHERE id = %s", (publication,))
    conn.execute("UPDATE terre.publication SET contenu = 'corrigé' WHERE id = %s", (publication,))
    conn.execute("DELETE FROM terre.publication WHERE id = %s", (publication,))


def test_retrait_visible_au_bulletin_suivant(conn, publication):  # noqa: F811
    _publier(conn, publication)
    with pytest.raises(ValueError, match="motif"):
        bulletin.retirer(conn, publication, "A. Directeur", "  ")
    bulletin.retirer(conn, publication, "A. Directeur", "point mal placé")
    assert conn.execute("SELECT statut FROM terre.publication").fetchone()[0] == "retiree"
    refuse(conn, "UPDATE terre.publication SET statut = 'publiee' WHERE id = %s", publication)

    smtp = FauxSMTP()
    bid, n, _ = bulletin.envoyer(conn, JOUR, ["a@exemple.org"], smtp, "x@exemple.org", [])
    assert bid and n == 0
    m = smtp.envoyes[0]
    assert "1 rectificatif" in m["Subject"]
    corps = m.get_body(("plain",)).get_content()
    assert "RECTIFICATIF" in corps and "Pont de Test sous les eaux" in corps and "point mal placé" in corps
    # Annoncé une seule fois.
    assert bulletin.retraits_a_annoncer(conn) == []
    assert bulletin.envoyer(conn, JOUR, ["a@exemple.org"], FauxSMTP(), "x@exemple.org", []) == (0, 0, [])


def test_rien_a_retirer(conn, publication):  # noqa: F811
    with pytest.raises(ValueError, match="rien à retirer"):
        bulletin.retirer(conn, publication, "A. Directeur", "motif")
