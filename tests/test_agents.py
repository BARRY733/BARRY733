"""Chaîne éditoriale testée avec un client simulé : aucun appel à l'API."""

from datetime import date
from types import SimpleNamespace

import pytest

from agents import chaine, dossier
from agents.schemas import Analyse, ContreExpertise, Controle, Fait, Texte
from detection import detecter


class ClientSimule:
    """Rend, agent par agent, les sorties préparées, et garde trace des requêtes."""

    def __init__(self, sorties):
        self.sorties = list(sorties)
        self.requetes = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **requete):
        self.requetes.append(requete)
        sortie = self.sorties.pop(0)
        if sortie == "refus":
            return SimpleNamespace(stop_reason="refusal", parsed_output=None)
        return SimpleNamespace(stop_reason="end_turn", parsed_output=sortie)


@pytest.fixture
def anomalie(cur):
    """Une crue détectée sur un pont, avec ses preuves."""
    cur.execute("INSERT INTO infrastructure (nom, type, pays_iso, geom) "
                "VALUES ('Pont de Bakel', 'pont', 'SEN', ST_SetSRID(ST_MakePoint(-12.1, 15.0), 4326)) RETURNING id")
    pid = cur.fetchone()[0]
    cur.execute("INSERT INTO preuve (source_id, uri, acquise_le, traitement) "
                "SELECT id, 'stac', now(), 'pixel WOfS' FROM source WHERE code = 'deafrica' RETURNING id")
    preuve = cur.fetchone()[0]
    for nom, valeur, quand in [("frequence_eau_historique", 0.02, "1984-01-01"),
                               ("eau_observee", 1, "2024-09-05"), ("eau_observee", 1, "2024-09-13")]:
        cur.execute("INSERT INTO indicateur (infrastructure_id, preuve_id, nom, valeur, unite, mesure_le) "
                    "VALUES (%s, %s, %s, %s, 'x', %s)", (pid, preuve, nom, valeur, quand))
    [a] = detecter(cur, date(2024, 9, 15))
    return a.evenement_id, preuve


def analyse(preuve, confiance="eleve"):
    return Analyse(faits=[Fait(affirmation="Eau observée sur le pont les 5 et 13 septembre", preuves=[preuve])],
                   gravite=4, impact_acces="menace", confiance=confiance,
                   justification="Deux scènes claires en eau, point presque jamais inondé.", a_verifier=[])


def contre(verdict="confirme", confiance="eleve"):
    return ContreExpertise(verdict=verdict, explications_alternatives=[], affirmations_non_etayees=[],
                           confiance_revisee=confiance, justification="Aucune alternative sérieuse.")


def texte(preuve, confiance="confiance élevée"):
    return Texte(titre="Crue au pont de Bakel",
                 contenu=f"De l'eau recouvre le pont de Bakel ({confiance}). Source : Digital Earth Africa.",
                 preuves_citees=[preuve])


def test_dossier_ferme(cur, anomalie):
    eid, preuve = anomalie
    d = dossier.construire(cur, eid)
    assert d["point"]["nom"] == "Pont de Bakel"
    assert [p["id"] for p in d["preuves"]] == [preuve]
    assert len(d["mesures"]) == 3


def test_chaine_complete_soumise_au_directeur(cur, anomalie):
    eid, preuve = anomalie
    client = ClientSimule([analyse(preuve), contre(), texte(preuve), Controle(conforme=True, manquements=[])])
    r = chaine.traiter(cur, client, eid)
    assert r.issue == "soumis"
    cur.execute("SELECT statut, niveau, mention_ia <> '' FROM publication WHERE id = %s", (r.publication_id,))
    assert cur.fetchone() == ("soumis", 2, True)
    cur.execute("SELECT statut, gravite FROM evenement WHERE id = %s", (eid,))
    assert cur.fetchone() == ("qualifie", 4)
    # Les fiches de mission servent de consignes, et le modèle par défaut est utilisé.
    assert "Agent Contradicteur" in client.requetes[1]["system"]
    assert client.requetes[0]["model"] == chaine.MODELE
    # Sans validation du directeur, la publication reste impossible.
    with pytest.raises(Exception, match="directeur"):
        cur.execute("UPDATE publication SET statut = 'publie' WHERE id = %s", (r.publication_id,))


def test_rejet_du_contradicteur_arrete_la_chaine(cur, anomalie):
    eid, preuve = anomalie
    client = ClientSimule([analyse(preuve), contre("rejete", "a_confirmer")])
    r = chaine.traiter(cur, client, eid)
    assert (r.issue, r.publication_id, len(client.requetes)) == ("rejete", None, 2)
    cur.execute("SELECT statut FROM evenement WHERE id = %s", (eid,))
    assert cur.fetchone()[0] == "rejete"


def test_doute_plafonne_la_confiance(cur, anomalie):
    eid, preuve = anomalie
    client = ClientSimule([analyse(preuve), contre("doute", "eleve"), texte(preuve),
                           Controle(conforme=True, manquements=[])])
    r = chaine.traiter(cur, client, eid)
    # Le texte affiche « confiance élevée » alors que le doute plafonne à « moyen » : bloqué.
    assert r.issue == "bloque"
    assert any("au-delà du plafond" in m for m in r.manquements)
    cur.execute("SELECT statut, confiance FROM publication WHERE id = %s", (r.publication_id,))
    assert cur.fetchone() == ("brouillon", "moyen")


def test_preuve_inventee_bloquee(cur, anomalie):
    eid, preuve = anomalie
    client = ClientSimule([analyse(preuve), contre(), texte(99999), Controle(conforme=True, manquements=[])])
    r = chaine.traiter(cur, client, eid)
    assert r.issue == "bloque"
    assert any("absentes du dossier" in m for m in r.manquements)


def test_conformite_bloque(cur, anomalie):
    eid, preuve = anomalie
    client = ClientSimule([analyse(preuve), contre(), texte(preuve),
                           Controle(conforme=False, manquements=["Consigne vitale non sourcée"])])
    r = chaine.traiter(cur, client, eid)
    assert r.issue == "bloque" and "Consigne vitale non sourcée" in r.manquements
    cur.execute("SELECT decision, commentaire FROM validation WHERE publication_id = %s", (r.publication_id,))
    assert cur.fetchone() == ("rejete", "Consigne vitale non sourcée")


def test_refus_du_modele(cur, anomalie):
    eid, _ = anomalie
    with pytest.raises(chaine.Refus):
        chaine.traiter(cur, ClientSimule(["refus"]), eid)
