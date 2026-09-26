-- Œil Bleu — étape 4 : agents.
SET search_path = terre, public;

-- Une source ne peut être citée dans un texte qu'une fois sa licence confirmée
-- par le directeur (commande « verifier-licence »).
ALTER TABLE source ADD COLUMN licence_verifiee boolean NOT NULL DEFAULT false;

-- Trace de chaque appel d'agent : ce qu'il a reçu, ce qu'il a répondu.
CREATE TABLE passage_agent (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    anomalie_id   bigint NOT NULL REFERENCES anomalie (id),
    agent         text NOT NULL CHECK (agent IN ('analyste', 'contradicteur', 'redacteur', 'conformite')),
    modele        text NOT NULL,
    entree        jsonb NOT NULL,
    sortie        jsonb,
    refus         boolean NOT NULL DEFAULT false,
    requete_id    text,
    jetons_entree integer,
    jetons_sortie integer,
    cree_le       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX passage_agent_anomalie_idx ON passage_agent (anomalie_id);
