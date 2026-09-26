-- Œil Bleu — étape 2 : collecte.
-- Les observations brutes arrivent avant tout événement : elles sont stockées
-- à part, puis la détection (étape 3) les transforme en événements.
SET search_path = terre, public;

-- Journal de chaque passage d'un collecteur.
CREATE TABLE collecte (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id    bigint NOT NULL REFERENCES source (id),
    debut        timestamptz NOT NULL DEFAULT now(),
    fin          timestamptz,
    statut       text NOT NULL DEFAULT 'en_cours'
                 CHECK (statut IN ('en_cours', 'reussie', 'echouee')),
    nb_nouvelles integer,
    erreur       text
);
CREATE INDEX collecte_source_debut_idx ON collecte (source_id, debut DESC);

-- Observation : une valeur mesurée par une source, à un endroit et une date.
-- « cle » identifie l'observation chez la source et rend la collecte rejouable.
CREATE TABLE observation (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id          bigint NOT NULL REFERENCES source (id),
    collecte_id        bigint NOT NULL REFERENCES collecte (id),
    cle                text NOT NULL,
    variable           text NOT NULL,
    valeur             double precision,
    unite              text,
    observe_le         timestamptz NOT NULL,
    geom               geometry(Geometry, 4326) NOT NULL,
    infrastructure_id  bigint REFERENCES infrastructure (id),
    brut               jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (source_id, cle)
);
CREATE INDEX observation_geom_idx ON observation USING gist (geom);
CREATE INDEX observation_variable_date_idx ON observation (variable, observe_le DESC);
CREATE INDEX observation_infrastructure_idx ON observation (infrastructure_id, variable, observe_le DESC);
