-- Œil Bleu : le Modèle Terre
-- Chaque information est un objet relié aux autres. Les règles éditoriales
-- critiques (preuve obligatoire, validation du directeur, pas d'effacement)
-- sont appliquées par la base elle-même, pas seulement par les agents.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA IF NOT EXISTS terre;
SET search_path = terre, public;

-- Vocabulaires ---------------------------------------------------------------

CREATE TYPE type_source AS ENUM ('satellite', 'base_publique', 'rapport');
CREATE TYPE type_zone AS ENUM ('pays', 'region', 'bassin', 'commune');
CREATE TYPE type_infrastructure AS ENUM
  ('route', 'pont', 'village', 'centre_sante', 'piste', 'barrage', 'autre');
CREATE TYPE type_evenement AS ENUM
  ('crue', 'feu', 'secheresse', 'deforestation', 'seisme', 'pollution');
CREATE TYPE statut_evenement AS ENUM
  ('detecte', 'qualifie', 'rejete', 'confirme', 'clos');
CREATE TYPE niveau_confiance AS ENUM ('eleve', 'moyen', 'a_confirmer');
CREATE TYPE type_impact AS ENUM ('coupe', 'menace', 'endommage', 'aucun');
CREATE TYPE type_publication AS ENUM ('alerte', 'article', 'rapport', 'bulletin');
CREATE TYPE statut_publication AS ENUM
  ('brouillon', 'soumis', 'valide', 'publie', 'corrige', 'retire');
CREATE TYPE decision_validation AS ENUM ('valide', 'rejete');

-- Objets ---------------------------------------------------------------------

CREATE TABLE source (
  id                  serial PRIMARY KEY,
  code                text UNIQUE NOT NULL,
  nom                 text NOT NULL,
  type                type_source NOT NULL,
  url                 text,
  licence             text NOT NULL,
  mention_obligatoire text NOT NULL -- affichée sous chaque visuel
);

CREATE TABLE zone (
  id        serial PRIMARY KEY,
  nom       text NOT NULL,
  type      type_zone NOT NULL,
  pays_iso  char(3),
  parent_id int REFERENCES zone(id),
  geom      geometry(MultiPolygon, 4326) NOT NULL
);
CREATE INDEX ON zone USING gist (geom);

-- Les points surveillés du pilote sont des infrastructures (routes, ponts,
-- villages, centres de santé) dont on suit l'accès.
CREATE TABLE infrastructure (
  id         serial PRIMARY KEY,
  nom        text NOT NULL,
  type       type_infrastructure NOT NULL,
  pays_iso   char(3) NOT NULL,
  zone_id    int REFERENCES zone(id),
  surveille  boolean NOT NULL DEFAULT true,
  notes      text,
  geom       geometry(Point, 4326) NOT NULL,
  cree_le    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (nom, pays_iso)
);
CREATE INDEX ON infrastructure USING gist (geom);

CREATE TABLE evenement (
  id         bigserial PRIMARY KEY,
  type       type_evenement NOT NULL,
  statut     statut_evenement NOT NULL DEFAULT 'detecte',
  zone_id    int REFERENCES zone(id),
  geom       geometry(Geometry, 4326),
  debut      timestamptz NOT NULL,
  fin        timestamptz,
  gravite    smallint CHECK (gravite BETWEEN 1 AND 5),
  confiance  niveau_confiance NOT NULL DEFAULT 'a_confirmer',
  source_id  int REFERENCES source(id),   -- détecteur d'origine
  ref_externe text,                       -- identifiant chez la source
  cree_le    timestamptz NOT NULL DEFAULT now(),
  CHECK (fin IS NULL OR fin >= debut),
  UNIQUE (source_id, ref_externe)
);
CREATE INDEX ON evenement USING gist (geom);
CREATE INDEX ON evenement (type, debut);

-- Conséquence d'un événement sur un accès : la lecture humanitaire.
CREATE TABLE impact (
  evenement_id      bigint REFERENCES evenement(id) ON DELETE CASCADE,
  infrastructure_id int REFERENCES infrastructure(id),
  nature            type_impact NOT NULL,
  confiance         niveau_confiance NOT NULL,
  PRIMARY KEY (evenement_id, infrastructure_id)
);

CREATE TABLE preuve (
  id               bigserial PRIMARY KEY,
  source_id        int NOT NULL REFERENCES source(id),
  uri              text NOT NULL,           -- image ou donnée brute
  acquise_le       timestamptz NOT NULL,    -- date de prise de vue / mesure
  traitement       text NOT NULL,           -- chaîne de traitement appliquée
  empreinte_sha256 char(64),
  cree_le          timestamptz NOT NULL DEFAULT now()
);

-- Un même fichier brut (une réponse d'API) peut prouver plusieurs événements.
CREATE TABLE evenement_preuve (
  evenement_id bigint REFERENCES evenement(id) ON DELETE CASCADE,
  preuve_id    bigint REFERENCES preuve(id),
  PRIMARY KEY (evenement_id, preuve_id)
);

CREATE TABLE indicateur (
  id           bigserial PRIMARY KEY,
  evenement_id bigint REFERENCES evenement(id) ON DELETE CASCADE,
  infrastructure_id int REFERENCES infrastructure(id),  -- mesure sur un point surveillé
  preuve_id    bigint NOT NULL REFERENCES preuve(id),
  nom          text NOT NULL,     -- surface_inondee, pluie_cumulee...
  valeur       double precision NOT NULL,
  unite        text NOT NULL,
  mesure_le    timestamptz NOT NULL
);
CREATE UNIQUE INDEX ON indicateur (infrastructure_id, nom, mesure_le);

CREATE TABLE population_exposee (
  id           bigserial PRIMARY KEY,
  evenement_id bigint NOT NULL REFERENCES evenement(id) ON DELETE CASCADE,
  zone_id      int REFERENCES zone(id),
  estimation   integer NOT NULL CHECK (estimation >= 0),
  methode      text NOT NULL,
  preuve_id    bigint NOT NULL REFERENCES preuve(id)
);

-- Niveau 1 : publication automatique ; niveau 2 : directeur ;
-- niveau 3 (mise en cause d'un acteur nommé) : exclu au lancement.
CREATE TABLE publication (
  id             bigserial PRIMARY KEY,
  type           type_publication NOT NULL,
  niveau         smallint NOT NULL CHECK (niveau IN (1, 2)),
  langue         text NOT NULL,          -- code BCP 47 : fr, en, bm, ff, wo...
  titre          text NOT NULL,
  contenu        text NOT NULL,
  confiance      niveau_confiance NOT NULL,
  statut         statut_publication NOT NULL DEFAULT 'brouillon',
  mention_ia     text NOT NULL DEFAULT 'Contenu produit avec l''aide de l''IA'
                 CHECK (length(trim(mention_ia)) > 0),  -- AI Act
  corrige_id     bigint REFERENCES publication(id),     -- remplace cette version
  motif_correction text,
  publie_le      timestamptz,
  cree_le        timestamptz NOT NULL DEFAULT now(),
  CHECK (corrige_id IS NULL OR motif_correction IS NOT NULL)
);

CREATE TABLE publication_preuve (
  publication_id bigint REFERENCES publication(id) ON DELETE CASCADE,
  preuve_id      bigint REFERENCES preuve(id),
  PRIMARY KEY (publication_id, preuve_id)
);

CREATE TABLE validation (
  id             bigserial PRIMARY KEY,
  publication_id bigint NOT NULL REFERENCES publication(id),
  validateur     text NOT NULL,   -- 'directeur' ou nom de l'agent
  decision       decision_validation NOT NULL,
  confiance      niveau_confiance,
  commentaire    text,
  cree_le        timestamptz NOT NULL DEFAULT now()
);

-- Règles éditoriales ---------------------------------------------------------

CREATE FUNCTION controle_publication() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.statut = 'publie' AND OLD.statut IS DISTINCT FROM 'publie' THEN
    IF NOT EXISTS (SELECT 1 FROM publication_preuve WHERE publication_id = NEW.id) THEN
      RAISE EXCEPTION 'Publication % : aucune preuve rattachée', NEW.id;
    END IF;
    IF NEW.niveau = 2 AND NOT EXISTS (
      SELECT 1 FROM validation v
      WHERE v.publication_id = NEW.id AND v.validateur = 'directeur'
        AND v.decision = 'valide'
        AND NOT EXISTS (  -- un rejet postérieur annule la validation
          SELECT 1 FROM validation r
          WHERE r.publication_id = NEW.id AND r.validateur = 'directeur'
            AND r.decision = 'rejete' AND r.cree_le > v.cree_le)
    ) THEN
      RAISE EXCEPTION 'Publication % : niveau 2 sans validation du directeur', NEW.id;
    END IF;
    NEW.publie_le := coalesce(NEW.publie_le, now());
  END IF;

  IF OLD.statut IN ('publie', 'corrige', 'retire') THEN
    IF NEW.contenu IS DISTINCT FROM OLD.contenu OR NEW.titre IS DISTINCT FROM OLD.titre THEN
      RAISE EXCEPTION 'Publication % : un contenu publié ne se modifie pas, publier une correction', OLD.id;
    END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER controle_publication BEFORE UPDATE ON publication
  FOR EACH ROW EXECUTE FUNCTION controle_publication();

-- Une publication ne peut pas naître déjà publiée : elle passe par un UPDATE.
CREATE FUNCTION interdit_insertion_publiee() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.statut NOT IN ('brouillon', 'soumis') THEN
    RAISE EXCEPTION 'Une publication se crée en brouillon ou soumise';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER interdit_insertion_publiee BEFORE INSERT ON publication
  FOR EACH ROW EXECUTE FUNCTION interdit_insertion_publiee();

-- Une erreur se corrige visiblement, jamais par un effacement discret.
CREATE FUNCTION interdit_effacement() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.statut IN ('publie', 'corrige', 'retire') THEN
    RAISE EXCEPTION 'Publication % : effacement interdit, publier une correction', OLD.id;
  END IF;
  RETURN OLD;
END $$;

CREATE TRIGGER interdit_effacement BEFORE DELETE ON publication
  FOR EACH ROW EXECUTE FUNCTION interdit_effacement();

-- Une preuve rattachée à une publication diffusée ne se détache pas.
CREATE FUNCTION protege_lien_preuve() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM publication
             WHERE id = OLD.publication_id AND statut IN ('publie', 'corrige', 'retire')) THEN
    RAISE EXCEPTION 'Publication % diffusée : ses preuves sont figées', OLD.publication_id;
  END IF;
  RETURN OLD;
END $$;

CREATE TRIGGER protege_lien_preuve BEFORE DELETE ON publication_preuve
  FOR EACH ROW EXECUTE FUNCTION protege_lien_preuve();

-- Traçabilité : de chaque publication jusqu'aux données brutes.
CREATE VIEW lignage AS
SELECT p.id AS publication_id, p.titre, p.statut, p.publie_le,
       pr.id AS preuve_id, pr.uri, pr.acquise_le, pr.traitement,
       s.nom AS source, s.mention_obligatoire
FROM publication p
JOIN publication_preuve pp ON pp.publication_id = p.id
JOIN preuve pr ON pr.id = pp.preuve_id
JOIN source s ON s.id = pr.source_id;
