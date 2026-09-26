"""Tests de l'étape 4 avec des agents simulés : aucun appel réel au modèle."""

from datetime import date, datetime, timezone

import pytest

pytest.importorskip("pydantic")

from oeil_bleu.agents import consignes  # noqa: E402
from oeil_bleu.agents.chaine import MENTION_IA, traiter, traiter_jour  # noqa: E402
from oeil_bleu.agents.client import RefusAgent, Reponse  # noqa: E402
from oeil_bleu.agents.schemas import Analyse, Conformite, Contradiction, Redaction  # noqa: E402
from oeil_bleu.collecte import Observation, executer  # noqa: E402
from oeil_bleu.detection import detecter  # noqa: E402

JOUR = date(2024, 9, 10)
T = datetime(2024, 9, 8, 10, tzinfo=timezone.utc)

ANALYSE = Analyse(qualifie=True, gravite=4, confiance=0.8, impact_acces="coupe",
                  resume="Eau sur le pont.", justification="Eau rare, deux passages.")
CONFIRME = Contradiction(verdict="confirme", explications_alternatives=[], objections=[], confiance_revisee=0.7)
REDACTION = Redaction(titre="Pont de Test sous les eaux", texte="Les images Landsat du 8 septembre montrent de l'eau.")
CONFORME = Conformite(conforme=True, mise_en_cause=False, sujet_securitaire=False,
                      affirmation_non_etayee=False, problemes=[])


class Faux:
    """Appelant simulé : une réponse par consigne, et la liste des dossiers reçus."""

    modele = "faux"

    def __init__(self, **reponses):
        self.reponses = {getattr(consignes, k.upper()): v for k, v in reponses.items()}
        self.recus = []

    def __call__(self, consigne, dossier, schema):
        self.recus.append((consigne, dossier))
        r = self.reponses[consigne]
        if isinstance(r, Exception):
            raise r
        assert isinstance(r, schema)
        return Reponse(sortie=r, modele="faux", requete_id="req_test", jetons_entree=10, jetons_sortie=5)


@pytest.fixture
def anomalie(conn):
    infra = conn.execute(
        "INSERT INTO terre.infrastructure (zone_id, nom, type, geom, surveille)"
        " SELECT id, 'Pont de Test', 'pont', ST_SetSRID(ST_MakePoint(-4, 14), 4326), true"
        " FROM terre.zone WHERE code = 'bassin_niger' RETURNING id"
    ).fetchone()[0]
    obs = lambda cle, var, val, quand: Observation(cle=cle, variable=var, valeur=val, unite=None,
                                                   observe_le=quand, longitude=-4, latitude=14,
                                                   infrastructure_id=infra)
    executer(conn, "deafrica_wofs", lambda c: [
        obs("f", "frequence_eau_historique", 0.01, datetime(1984, 1, 1, tzinfo=timezone.utc)),
        obs("w1", "eau_observee", 1, T),
    ])
    detecter(conn, JOUR)
    return conn.execute("SELECT id FROM terre.anomalie").fetchone()[0]


def _statut_anomalie(conn, i):
    return conn.execute("SELECT statut FROM terre.anomalie WHERE id = %s", (i,)).fetchone()[0]


def test_chaine_complete_avec_licence_confirmee(conn, anomalie):
    conn.execute("UPDATE terre.source SET licence_verifiee = true")
    faux = Faux(analyste=ANALYSE, contradicteur=CONFIRME, redacteur=REDACTION, conformite=CONFORME)
    issue = traiter(conn, faux, anomalie)

    assert issue.resultat == "a_valider"
    assert len(faux.recus) == 4
    # Le Rédacteur reçoit la confiance la plus prudente des deux agents (0,7 → moyen).
    assert faux.recus[2][1]["confiance_retenue"] == "moyen"
    titre, contenu, statut = conn.execute(
        "SELECT titre, contenu, statut FROM terre.publication WHERE id = %s", (issue.publication_id,)).fetchone()
    assert statut == "en_validation" and "Digital Earth Africa" in contenu and MENTION_IA in contenu
    assert _statut_anomalie(conn, anomalie) == "transmise"
    assert conn.execute("SELECT count(*) FROM terre.publication_preuve").fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM terre.passage_agent").fetchone()[0] == 4

    # Le directeur reste indispensable : les agents seuls ne peuvent pas valider.
    import psycopg
    with pytest.raises(psycopg.errors.RaiseException, match="approbation humaine"):
        conn.execute("UPDATE terre.publication SET statut = 'validee' WHERE id = %s", (issue.publication_id,))


def test_licence_non_confirmee_bloque(conn, anomalie):
    faux = Faux(analyste=ANALYSE, contradicteur=CONFIRME, redacteur=REDACTION, conformite=CONFORME)
    issue = traiter(conn, faux, anomalie)
    assert issue.resultat == "non_conforme" and "deafrica_wofs" in issue.detail
    assert conn.execute("SELECT statut FROM terre.publication").fetchone()[0] == "brouillon"


def test_contradicteur_rejette(conn, anomalie):
    rejet = Contradiction(verdict="rejete", explications_alternatives=["rizière irriguée"],
                          objections=[], confiance_revisee=0.2)
    faux = Faux(analyste=ANALYSE, contradicteur=rejet)
    issue = traiter(conn, faux, anomalie)
    assert (issue.resultat, _statut_anomalie(conn, anomalie)) == ("ecartee", "ecartee")
    assert "rizière" in issue.detail
    assert conn.execute("SELECT count(*) FROM terre.publication").fetchone()[0] == 0


def test_contradicteur_incertain_laisse_l_anomalie_ouverte(conn, anomalie):
    doute = Contradiction(verdict="incertain", explications_alternatives=[], objections=["un seul passage"],
                          confiance_revisee=0.4)
    issue = traiter(conn, Faux(analyste=ANALYSE, contradicteur=doute), anomalie)
    assert (issue.resultat, _statut_anomalie(conn, anomalie)) == ("incertaine", "nouvelle")


def test_conformite_bloque_une_mise_en_cause(conn, anomalie):
    conn.execute("UPDATE terre.source SET licence_verifiee = true")
    refus = CONFORME.model_copy(update={"conforme": False, "mise_en_cause": True, "problemes": ["cite le barrage X"]})
    faux = Faux(analyste=ANALYSE, contradicteur=CONFIRME, redacteur=REDACTION, conformite=refus)
    issue = traiter(conn, faux, anomalie)
    assert issue.resultat == "non_conforme"
    assert conn.execute("SELECT decision FROM terre.validation WHERE validateur = 'Agent Conformité'"
                        ).fetchone()[0] == "a_corriger"


def test_refus_du_modele(conn, anomalie):
    issue = traiter(conn, Faux(analyste=RefusAgent("refus du modèle (None)")), anomalie)
    assert issue.resultat == "refus" and _statut_anomalie(conn, anomalie) == "nouvelle"
    assert conn.execute("SELECT refus FROM terre.passage_agent").fetchone()[0] is True


def test_erreur_ne_laisse_rien(conn, anomalie):
    faux = Faux(analyste=ANALYSE, contradicteur=RuntimeError("réseau"))
    with pytest.raises(RuntimeError):
        traiter(conn, faux, anomalie)
    assert conn.execute("SELECT count(*) FROM terre.passage_agent").fetchone()[0] == 0
    assert _statut_anomalie(conn, anomalie) == "nouvelle"


def test_traiter_jour_filtre_par_confiance(conn, anomalie):
    faux = Faux(analyste=ANALYSE.model_copy(update={"qualifie": False}))
    assert traiter_jour(conn, faux, JOUR, confiance_min=0.9) == []
    assert [i.resultat for i in traiter_jour(conn, faux, JOUR)] == ["ecartee"]


def test_dossier_sans_identifiants_internes(conn, anomalie):
    faux = Faux(analyste=ANALYSE.model_copy(update={"qualifie": False}))
    traiter(conn, faux, anomalie)
    d = faux.recus[0][1]
    assert d["point"]["nom"] == "Pont de Test" and "observations" not in d["elements"]
    assert d["elements"]["frequence_historique"] == 0.01
