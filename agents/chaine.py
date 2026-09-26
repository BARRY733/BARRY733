"""Chaîne éditoriale : Analyste → Contradicteur → Rédacteur → Conformité.

L'ordre et les arrêts sont décidés par le code, pas par les agents : chaque
agent reçoit un dossier fermé, rend une sortie structurée, et la chaîne
s'interrompt dès qu'un agent bloque. Rien n'est publié : le meilleur
résultat possible est un texte soumis au directeur de publication.
"""

import json
import pathlib
from dataclasses import dataclass

import anthropic
from pydantic import BaseModel

from . import dossier as dossier_mod
from .schemas import Analyse, ContreExpertise, Controle, Texte

MODELE = "claude-opus-5"
FICHES = pathlib.Path(__file__).parent / "fiches"
ORDRE_CONFIANCE = ["a_confirmer", "moyen", "eleve"]


class Refus(RuntimeError):
    """Le modèle a décliné la requête."""


def fiche(nom: str) -> str:
    return (FICHES / "commun.md").read_text() + "\n\n" + (FICHES / f"{nom}.md").read_text()


def appeler(client, agent: str, contenu: dict, format_: type[BaseModel], effort: str) -> BaseModel:
    reponse = client.beta.messages.parse(
        model=MODELE,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        thinking={"type": "adaptive"},
        output_config={"effort": effort},
        system=fiche(agent),
        messages=[{"role": "user", "content": json.dumps(contenu, ensure_ascii=False, indent=1)}],
        output_format=format_,
    )
    if reponse.stop_reason == "refusal":
        raise Refus(f"{agent} : requête déclinée")
    if reponse.parsed_output is None:
        raise RuntimeError(f"{agent} : sortie illisible (arrêt : {reponse.stop_reason})")
    return reponse.parsed_output


@dataclass
class Resultat:
    evenement_id: int
    etape: str                      # dernière étape atteinte
    issue: str                      # rejete | bloque | soumis
    publication_id: int | None
    analyse: Analyse | None = None
    contre_expertise: ContreExpertise | None = None
    texte: Texte | None = None
    controle: Controle | None = None
    manquements: list[str] | None = None


def controles_mecaniques(texte: Texte, dossier: dict, plafond: str) -> list[str]:
    """Ce que le code peut vérifier sans jugement, en complément de l'Agent Conformité."""
    manquements = []
    connues = {p["id"] for p in dossier["preuves"]}
    inconnues = set(texte.preuves_citees) - connues
    if inconnues:
        manquements.append(f"preuves citées absentes du dossier : {sorted(inconnues)}")
    if not texte.preuves_citees:
        manquements.append("aucune preuve citée")
    libelles = {"eleve": "confiance élevée", "moyen": "confiance moyenne", "a_confirmer": "à confirmer"}
    for niveau in ORDRE_CONFIANCE[ORDRE_CONFIANCE.index(plafond) + 1:]:
        if libelles[niveau] in texte.contenu.lower():
            manquements.append(f"confiance affichée « {libelles[niveau]} » au-delà du plafond « {plafond} »")
    if not any(libelles[n] in texte.contenu.lower() for n in ORDRE_CONFIANCE):
        manquements.append("niveau de confiance non affiché")
    return manquements


def traiter(cur, client, evenement_id: int) -> Resultat:
    dossier = dossier_mod.construire(cur, evenement_id)
    res = Resultat(evenement_id, "analyste", "bloque", None)

    res.analyse = appeler(client, "analyste", {"dossier": dossier}, Analyse, "high")
    res.etape = "contradicteur"
    res.contre_expertise = appeler(
        client, "contradicteur", {"dossier": dossier, "analyse": res.analyse.model_dump()}, ContreExpertise, "high")

    ce = res.contre_expertise
    if ce.verdict == "rejete":
        cur.execute("UPDATE terre.evenement SET statut = 'rejete' WHERE id = %s", (evenement_id,))
        res.issue = "rejete"
        return res

    # La confiance retenue est la plus prudente des deux avis ; un doute la plafonne à « moyen ».
    plafond = min(res.analyse.confiance, ce.confiance_revisee, key=ORDRE_CONFIANCE.index)
    if ce.verdict == "doute":
        plafond = min(plafond, "moyen", key=ORDRE_CONFIANCE.index)
    cur.execute(
        "UPDATE terre.evenement SET statut = 'qualifie', gravite = %s, confiance = %s WHERE id = %s",
        (res.analyse.gravite, plafond, evenement_id),
    )
    cur.execute(
        "UPDATE terre.impact SET nature = %s, confiance = %s WHERE evenement_id = %s",
        (res.analyse.impact_acces, plafond, evenement_id),
    )

    res.etape = "redacteur"
    contexte = {"dossier": dossier, "analyse": res.analyse.model_dump(),
                "contre_expertise": ce.model_dump(), "confiance_retenue": plafond}
    res.texte = appeler(client, "redacteur", contexte, Texte, "medium")

    res.etape = "conformite"
    res.controle = appeler(client, "conformite", {**contexte, "texte": res.texte.model_dump()}, Controle, "high")
    manquements = controles_mecaniques(res.texte, dossier, plafond) + res.controle.manquements
    conforme = res.controle.conforme and not manquements
    res.manquements = manquements

    # Le texte est conservé dans tous les cas : soumis s'il est conforme, brouillon sinon.
    cur.execute(
        "INSERT INTO terre.publication (type, niveau, evenement_id, langue, titre, contenu, confiance, statut) "
        "VALUES ('alerte', 2, %s, 'fr', %s, %s, %s, %s) RETURNING id",
        (evenement_id, res.texte.titre, res.texte.contenu, plafond, "soumis" if conforme else "brouillon"),
    )
    res.publication_id = cur.fetchone()[0]
    connues = {p["id"] for p in dossier["preuves"]}
    for preuve in sorted(set(res.texte.preuves_citees) & connues):
        cur.execute("INSERT INTO terre.publication_preuve VALUES (%s, %s)", (res.publication_id, preuve))
    cur.execute(
        "INSERT INTO terre.validation (publication_id, validateur, decision, confiance, commentaire) "
        "VALUES (%s, 'agent_conformite', %s, %s, %s)",
        (res.publication_id, "valide" if conforme else "rejete", plafond, "\n".join(manquements) or None),
    )
    res.issue = "soumis" if conforme else "bloque"
    return res


def client_par_defaut():
    return anthropic.Anthropic()
