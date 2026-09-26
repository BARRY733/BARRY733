"""Appel d'un agent : une requête Claude, une réponse validée par un schéma."""

import json
import os
from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

M = TypeVar("M", bound=BaseModel)

MODELE = os.environ.get("OEIL_BLEU_MODELE", "claude-opus-5")


class RefusAgent(Exception):
    """Le modèle a décliné la demande : l'anomalie reste pour l'humain."""


@dataclass
class Reponse:
    sortie: BaseModel
    modele: str
    requete_id: str | None = None
    jetons_entree: int | None = None
    jetons_sortie: int | None = None


class Appelant(Protocol):
    def __call__(self, consigne: str, dossier: dict, schema: type[M]) -> Reponse: ...


class AppelantClaude:
    """Appelant réel. La clé est lue par le SDK (ANTHROPIC_API_KEY ou profil « ant auth login »)."""

    def __init__(self, modele: str = MODELE):
        import anthropic

        self.client = anthropic.Anthropic()
        self.modele = modele

    def __call__(self, consigne: str, dossier: dict, schema: type[M]) -> Reponse:
        reponse = self.client.beta.messages.parse(
            model=self.modele,
            max_tokens=16000,
            system=consigne,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            # Repli automatique sur un autre modèle si celui-ci décline la demande.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{
                "role": "user",
                "content": "Dossier :\n```json\n" + json.dumps(dossier, ensure_ascii=False, indent=2, default=str) + "\n```",
            }],
            output_format=schema,
        )
        if reponse.stop_reason == "refusal":
            categorie = reponse.stop_details.category if reponse.stop_details else None
            raise RefusAgent(f"refus du modèle ({categorie})")
        if reponse.stop_reason == "max_tokens" or reponse.parsed_output is None:
            raise RuntimeError(f"réponse incomplète ({reponse.stop_reason})")
        return Reponse(
            sortie=reponse.parsed_output,
            modele=reponse.model,
            requete_id=reponse._request_id,
            jetons_entree=reponse.usage.input_tokens,
            jetons_sortie=reponse.usage.output_tokens,
        )
