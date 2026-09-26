-- Œil Bleu — Modèle Terre
-- Socle commun à tous les agents : zones, événements, indicateurs,
-- populations exposées, infrastructures, sources, preuves, publications
-- et validations. Toutes les géométries sont en WGS 84 (EPSG:4326).

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE SCHEMA IF NOT EXISTS terre;
SET search_path = terre, public;

-- Zone : un territoire suivi (bassin, commune, pays).
CREATE TABLE zone (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code        text NOT NULL UNIQUE,
    nom         text NOT NULL,
    type        text NOT NULL CHECK (type IN ('bassin', 'commune', 'pays', 'zone_pilote')),
    parent_id   bigint REFERENCES zone (id),
    geom        geometry(MultiPolygon, 4326) NOT NULL,
    notes       text,
    cree_le     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX zone_geom_idx ON zone USING gist (geom);

-- Source : satellite, base publique, rapport.
CREATE TABLE source (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code         text NOT NULL UNIQUE,
    nom          text NOT NULL,
    type         text NOT NULL CHECK (type IN ('satellite', 'base_publique', 'rapport')),
    url          text,
    licence      text NOT NULL,
    attribution  text NOT NULL
);

-- Événement : crue, feu, sécheresse, déforestation, séisme, pollution.
CREATE TABLE evenement (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    type        text NOT NULL CHECK (type IN ('crue', 'feu', 'secheresse', 'deforestation', 'seisme', 'pollution')),
    zone_id     bigint NOT NULL REFERENCES zone (id),
    titre       text NOT NULL,
    debut       timestamptz NOT NULL,
    fin         timestamptz CHECK (fin IS NULL OR fin >= debut),
    statut      text NOT NULL DEFAULT 'detecte'
                CHECK (statut IN ('detecte', 'en_analyse', 'confirme', 'rejete', 'clos')),
    confiance   numeric(3, 2) CHECK (confiance BETWEEN 0 AND 1),
    gravite     smallint CHECK (gravite BETWEEN 1 AND 5),
    geom        geometry(Geometry, 4326),
    cree_le     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX evenement_geom_idx ON evenement USING gist (geom);
CREATE INDEX evenement_zone_debut_idx ON evenement (zone_id, debut DESC);

-- Indicateur : une mesure (surface inondée, pluie cumulée, couvert forestier).
CREATE TABLE indicateur (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    evenement_id  bigint NOT NULL REFERENCES evenement (id) ON DELETE CASCADE,
    source_id     bigint NOT NULL REFERENCES source (id),
    nom           text NOT NULL,
    valeur        double precision NOT NULL,
    unite         text NOT NULL,
    observe_le    timestamptz NOT NULL,
    geom          geometry(Geometry, 4326)
);
CREATE INDEX indicateur_evenement_idx ON indicateur (evenement_id);

-- Infrastructure : route, pont, village, centre de santé, piste, barrage.
-- Les points surveillés du pilote sont les infrastructures où surveille = true.
CREATE TABLE infrastructure (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    zone_id     bigint NOT NULL REFERENCES zone (id),
    nom         text NOT NULL,
    type        text NOT NULL CHECK (type IN ('route', 'pont', 'village', 'centre_sante', 'piste', 'barrage')),
    geom        geometry(Geometry, 4326) NOT NULL,
    surveille   boolean NOT NULL DEFAULT false,
    notes       text,
    cree_le     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (zone_id, nom, type)
);
CREATE INDEX infrastructure_geom_idx ON infrastructure USING gist (geom);

-- Impact d'un événement sur une infrastructure (accès coupé, menacé…).
CREATE TABLE evenement_infrastructure (
    evenement_id       bigint NOT NULL REFERENCES evenement (id) ON DELETE CASCADE,
    infrastructure_id  bigint NOT NULL REFERENCES infrastructure (id),
    impact             text NOT NULL CHECK (impact IN ('menace', 'touche', 'coupe')),
    PRIMARY KEY (evenement_id, infrastructure_id)
);

-- Population exposée : estimation des personnes concernées.
CREATE TABLE population_exposee (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    zone_id       bigint NOT NULL REFERENCES zone (id),
    evenement_id  bigint NOT NULL REFERENCES evenement (id) ON DELETE CASCADE,
    estimation    integer NOT NULL CHECK (estimation >= 0),
    methode       text NOT NULL,
    source_id     bigint NOT NULL REFERENCES source (id)
);

-- Preuve : image ou donnée brute, datée, avec son traitement.
CREATE TABLE preuve (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    evenement_id  bigint NOT NULL REFERENCES evenement (id) ON DELETE CASCADE,
    source_id     bigint NOT NULL REFERENCES source (id),
    type          text NOT NULL CHECK (type IN ('image', 'donnee_brute')),
    uri           text NOT NULL,
    acquis_le     timestamptz NOT NULL,
    traitement    text NOT NULL,
    sha256        char(64)
);

-- Publication : alerte, article, rapport, bulletin.
-- Niveau 1 : publication automatique. Niveau 2 : validation humaine.
-- Le niveau 3 (mise en cause d'un acteur nommé) est exclu au lancement.
CREATE TABLE publication (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    evenement_id  bigint REFERENCES evenement (id),
    type          text NOT NULL CHECK (type IN ('alerte', 'article', 'rapport', 'bulletin')),
    niveau        smallint NOT NULL CHECK (niveau IN (1, 2)),
    langue        text NOT NULL CHECK (langue IN ('fr', 'en', 'bm', 'ff', 'wo')),
    titre         text NOT NULL,
    contenu       text NOT NULL,
    mention_ia    boolean NOT NULL DEFAULT true,
    statut        text NOT NULL DEFAULT 'brouillon'
                  CHECK (statut IN ('brouillon', 'en_validation', 'validee', 'publiee', 'retiree')),
    publie_le     timestamptz,
    cree_le       timestamptz NOT NULL DEFAULT now(),
    CHECK (statut <> 'publiee' OR publie_le IS NOT NULL)
);

CREATE TABLE publication_preuve (
    publication_id  bigint NOT NULL REFERENCES publication (id) ON DELETE CASCADE,
    preuve_id       bigint NOT NULL REFERENCES preuve (id),
    PRIMARY KEY (publication_id, preuve_id)
);

-- Validation : qui a validé quoi, quand, avec quel niveau de confiance.
CREATE TABLE validation (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    publication_id  bigint NOT NULL REFERENCES publication (id) ON DELETE CASCADE,
    validateur      text NOT NULL,
    role            text NOT NULL CHECK (role IN ('agent', 'humain')),
    decision        text NOT NULL CHECK (decision IN ('approuve', 'rejete', 'a_corriger')),
    confiance       numeric(3, 2) CHECK (confiance BETWEEN 0 AND 1),
    commentaire     text,
    valide_le       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX validation_publication_idx ON validation (publication_id);

-- Règle éditoriale : toute publication doit citer au moins une preuve,
-- et une publication de niveau 2 ne passe à « validee » ou « publiee »
-- qu'après l'approbation d'un humain (le directeur de publication).
CREATE FUNCTION controle_publication() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.statut IN ('validee', 'publiee') THEN
        IF NOT EXISTS (SELECT 1 FROM terre.publication_preuve WHERE publication_id = NEW.id) THEN
            RAISE EXCEPTION 'publication % : aucune preuve rattachée', NEW.id;
        END IF;
        IF NEW.niveau = 2 AND NOT EXISTS (
            SELECT 1 FROM terre.validation
            WHERE publication_id = NEW.id AND role = 'humain' AND decision = 'approuve'
        ) THEN
            RAISE EXCEPTION 'publication % de niveau 2 : approbation humaine requise', NEW.id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER publication_controle
    BEFORE UPDATE OF statut ON publication
    FOR EACH ROW EXECUTE FUNCTION controle_publication();

-- Une publication naît en brouillon : elle ne peut pas être créée déjà validée.
CREATE FUNCTION publication_insertion() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.statut NOT IN ('brouillon', 'en_validation') THEN
        RAISE EXCEPTION 'une publication est créée en brouillon ou en validation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER publication_insertion
    BEFORE INSERT ON publication
    FOR EACH ROW EXECUTE FUNCTION publication_insertion();

-- Vue pratique : les points surveillés avec leurs coordonnées.
CREATE VIEW point_surveille AS
SELECT i.id, z.code AS zone_code, i.nom, i.type,
       ST_Y(ST_PointOnSurface(i.geom)) AS latitude,
       ST_X(ST_PointOnSurface(i.geom)) AS longitude,
       i.notes
FROM infrastructure i
JOIN zone z ON z.id = i.zone_id
WHERE i.surveille;
