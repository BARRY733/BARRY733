-- Œil Bleu — intégrité des publications.
-- Ce qui a été validé ou publié ne peut plus être modifié, effacé ni privé de ses
-- preuves. Une erreur se corrige par un retrait visible (statut « retiree »),
-- jamais par un effacement discret. Ces règles engagent la responsabilité du
-- directeur de publication : la base les impose, quel que soit le programme.
SET search_path = terre, public;

-- 1. La dernière décision humaine fait foi : un accord suivi d'un rejet vaut rejet.
CREATE OR REPLACE FUNCTION controle_publication() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    derniere text;
BEGIN
    IF NEW.statut IN ('validee', 'publiee') THEN
        IF NOT EXISTS (SELECT 1 FROM terre.publication_preuve WHERE publication_id = NEW.id) THEN
            RAISE EXCEPTION 'publication % : aucune preuve rattachée', NEW.id;
        END IF;
        IF NEW.niveau = 2 THEN
            SELECT decision INTO derniere FROM terre.validation
            WHERE publication_id = NEW.id AND role = 'humain'
            ORDER BY valide_le DESC, id DESC LIMIT 1;
            IF derniere IS DISTINCT FROM 'approuve' THEN
                RAISE EXCEPTION 'publication % de niveau 2 : approbation humaine requise (dernière décision : %)',
                    NEW.id, coalesce(derniere, 'aucune');
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

-- Le contrôle doit aussi voir les changements de statut combinés à d'autres colonnes.
DROP TRIGGER publication_controle ON publication;
CREATE TRIGGER publication_controle
    BEFORE UPDATE ON publication
    FOR EACH ROW EXECUTE FUNCTION controle_publication();

-- 2. Cycle de vie : seules ces transitions existent, et le texte est figé dès la validation.
--    brouillon → en_validation | retiree
--    en_validation → validee | retiree | brouillon
--    validee → publiee | retiree
--    publiee → retiree
--    retiree : définitif
CREATE FUNCTION cycle_publication() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.statut <> OLD.statut AND NOT (
        (OLD.statut = 'brouillon' AND NEW.statut IN ('en_validation', 'retiree')) OR
        (OLD.statut = 'en_validation' AND NEW.statut IN ('validee', 'retiree', 'brouillon')) OR
        (OLD.statut = 'validee' AND NEW.statut IN ('publiee', 'retiree')) OR
        (OLD.statut = 'publiee' AND NEW.statut = 'retiree')
    ) THEN
        RAISE EXCEPTION 'publication % : passage de « % » à « % » interdit', OLD.id, OLD.statut, NEW.statut;
    END IF;
    IF OLD.statut IN ('validee', 'publiee', 'retiree') AND (
        NEW.titre, NEW.contenu, NEW.niveau, NEW.langue, NEW.type, NEW.evenement_id, NEW.mention_ia, NEW.cree_le
    ) IS DISTINCT FROM (
        OLD.titre, OLD.contenu, OLD.niveau, OLD.langue, OLD.type, OLD.evenement_id, OLD.mention_ia, OLD.cree_le
    ) THEN
        RAISE EXCEPTION 'publication % : texte figé depuis sa validation, le retirer puis en publier un nouveau', OLD.id;
    END IF;
    IF OLD.publie_le IS NOT NULL AND NEW.publie_le IS DISTINCT FROM OLD.publie_le THEN
        RAISE EXCEPTION 'publication % : la date de publication ne se modifie pas', OLD.id;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER publication_cycle
    BEFORE UPDATE ON publication
    FOR EACH ROW EXECUTE FUNCTION cycle_publication();

-- 3. Rien de validé, publié ou retiré ne s'efface.
CREATE FUNCTION publication_ineffacable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.statut IN ('validee', 'publiee', 'retiree') THEN
        RAISE EXCEPTION 'publication % (« % ») : effacement interdit, utiliser un retrait', OLD.id, OLD.statut;
    END IF;
    RETURN OLD;
END;
$$;
CREATE TRIGGER publication_ineffacable
    BEFORE DELETE ON publication
    FOR EACH ROW EXECUTE FUNCTION publication_ineffacable();

-- 4. Les preuves d'un texte validé restent attachées.
CREATE FUNCTION preuves_attachees() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM terre.publication
               WHERE id = OLD.publication_id AND statut IN ('validee', 'publiee', 'retiree')) THEN
        RAISE EXCEPTION 'publication % : ses preuves ne peuvent plus être détachées', OLD.publication_id;
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;
CREATE TRIGGER publication_preuve_attachee
    BEFORE UPDATE OR DELETE ON publication_preuve
    FOR EACH ROW EXECUTE FUNCTION preuves_attachees();

-- 5. Le registre des décisions ne se réécrit pas ; il ne s'efface qu'avec un brouillon.
CREATE FUNCTION validation_registre() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'validation % : une décision enregistrée ne se modifie pas', OLD.id;
    END IF;
    IF EXISTS (SELECT 1 FROM terre.publication
               WHERE id = OLD.publication_id AND statut IN ('validee', 'publiee', 'retiree')) THEN
        RAISE EXCEPTION 'validation % : décision d''un texte validé, effacement interdit', OLD.id;
    END IF;
    RETURN OLD;
END;
$$;
CREATE TRIGGER validation_registre
    BEFORE UPDATE OR DELETE ON validation
    FOR EACH ROW EXECUTE FUNCTION validation_registre();

-- Le lien entre un bulletin envoyé et ses textes est définitif.
CREATE FUNCTION bulletin_definitif() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'bulletin envoyé : modification interdite';
END;
$$;
CREATE TRIGGER bulletin_publication_definitif
    BEFORE UPDATE OR DELETE ON bulletin_publication
    FOR EACH ROW EXECUTE FUNCTION bulletin_definitif();
CREATE TRIGGER bulletin_definitif
    BEFORE UPDATE OR DELETE ON bulletin
    FOR EACH ROW EXECUTE FUNCTION bulletin_definitif();

-- Un retrait après publication est annoncé aux destinataires dans le bulletin suivant.
CREATE TABLE bulletin_retrait (
    bulletin_id     bigint NOT NULL REFERENCES bulletin (id),
    publication_id  bigint NOT NULL UNIQUE REFERENCES publication (id),
    PRIMARY KEY (bulletin_id, publication_id)
);
CREATE TRIGGER bulletin_retrait_definitif
    BEFORE UPDATE OR DELETE ON bulletin_retrait
    FOR EACH ROW EXECUTE FUNCTION bulletin_definitif();
