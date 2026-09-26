-- Œil Bleu — couverture éditoriale : l'Afrique entière, région par région.
-- Un point surveillé peut désormais appartenir à une zone de type « continent ».
SET search_path = terre, public;

ALTER TABLE zone DROP CONSTRAINT zone_type_check;
ALTER TABLE zone ADD CONSTRAINT zone_type_check
    CHECK (type IN ('continent', 'bassin', 'pays', 'commune', 'zone_pilote'));
