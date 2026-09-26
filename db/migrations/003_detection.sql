-- Œil Bleu — étape 3 : détection.
-- Une anomalie est un signal sur un point surveillé, pour un jour donné.
-- Les agents de l'étape 4 la transforment (ou non) en événement.
SET search_path = terre, public;

CREATE TABLE anomalie (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    jour               date NOT NULL,
    infrastructure_id  bigint NOT NULL REFERENCES infrastructure (id),
    type               text NOT NULL CHECK (type IN ('eau_hors_etendue', 'menace_crue')),
    confiance          numeric(3, 2) NOT NULL CHECK (confiance BETWEEN 0 AND 1),
    niveau             text NOT NULL CHECK (niveau IN ('faible', 'moyen', 'eleve')),
    elements           jsonb NOT NULL,
    statut             text NOT NULL DEFAULT 'nouvelle'
                       CHECK (statut IN ('nouvelle', 'transmise', 'ecartee')),
    evenement_id       bigint REFERENCES evenement (id),
    detecte_le         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (infrastructure_id, jour, type)
);
CREATE INDEX anomalie_jour_idx ON anomalie (jour DESC, confiance DESC);
