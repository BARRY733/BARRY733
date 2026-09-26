"""Réponses attendues de chaque agent, validées à la réception."""

from typing import Literal

from pydantic import BaseModel, Field


class Analyse(BaseModel):
    qualifie: bool = Field(description="true si les éléments décrivent bien une crue en cours")
    gravite: int = Field(ge=1, le=5, description="1 mineure, 5 majeure")
    confiance: float = Field(ge=0, le=1)
    impact_acces: Literal["aucun", "menace", "touche", "coupe"]
    resume: str = Field(description="deux phrases au plus, faits uniquement")
    justification: str


class Contradiction(BaseModel):
    verdict: Literal["confirme", "rejete", "incertain"]
    explications_alternatives: list[str] = Field(description="causes possibles autres qu'une crue")
    objections: list[str]
    confiance_revisee: float = Field(ge=0, le=1)


class Redaction(BaseModel):
    titre: str = Field(description="80 caractères au plus")
    texte: str = Field(description="120 mots au plus, factuel, sans conjecture")


class Conformite(BaseModel):
    conforme: bool
    mise_en_cause: bool = Field(description="le texte met-il en cause une personne, une entreprise, un État ou un groupe ?")
    sujet_securitaire: bool = Field(description="le texte touche-t-il à un conflit, une attaque ou un sujet militaire ?")
    affirmation_non_etayee: bool = Field(description="le texte affirme-t-il un fait absent du dossier ?")
    problemes: list[str]
