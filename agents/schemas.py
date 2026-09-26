"""Formats de sortie imposés à chaque agent (sorties structurées)."""

from typing import Literal

from pydantic import BaseModel, Field

Confiance = Literal["eleve", "moyen", "a_confirmer"]


class Fait(BaseModel):
    affirmation: str
    preuves: list[int] = Field(description="Identifiants des preuves du dossier qui fondent ce fait")


class Analyse(BaseModel):
    faits: list[Fait]
    gravite: int = Field(ge=1, le=5)
    impact_acces: Literal["coupe", "menace", "endommage", "aucun"]
    confiance: Confiance
    justification: str
    a_verifier: list[str]


class ContreExpertise(BaseModel):
    verdict: Literal["confirme", "doute", "rejete"]
    explications_alternatives: list[str]
    affirmations_non_etayees: list[str]
    confiance_revisee: Confiance
    justification: str


class Texte(BaseModel):
    titre: str
    contenu: str
    preuves_citees: list[int]


class Controle(BaseModel):
    conforme: bool
    manquements: list[str]
