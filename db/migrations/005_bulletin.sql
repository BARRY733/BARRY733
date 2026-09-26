-- Œil Bleu — étape 5 : bulletin.
SET search_path = terre, public;

CREATE TABLE bulletin (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    jour              date NOT NULL,
    html              text NOT NULL,
    nb_destinataires  integer NOT NULL,
    nb_echecs         integer NOT NULL DEFAULT 0,
    envoye_le         timestamptz NOT NULL DEFAULT now()
);

-- Une publication ne part qu'une fois.
CREATE TABLE bulletin_publication (
    bulletin_id     bigint NOT NULL REFERENCES bulletin (id),
    publication_id  bigint NOT NULL UNIQUE REFERENCES publication (id),
    PRIMARY KEY (bulletin_id, publication_id)
);
