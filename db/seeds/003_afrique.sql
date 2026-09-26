-- Œil Bleu — l'Afrique, couverte en entier par Digital Earth Africa (optique et radar).
-- Rectangle approximatif, îles comprises (Cap-Vert à Maurice) : il déborde sur la
-- péninsule Arabique et le sud de l'Europe. À remplacer par le contour du continent.
SET search_path = terre, public;

INSERT INTO zone (code, nom, type, geom, notes) VALUES
    ('afrique', 'Afrique', 'continent',
     ST_Multi(ST_MakeEnvelope(-26.0, -35.0, 64.0, 38.0, 4326)),
     'Emprise provisoire approximative')
ON CONFLICT (code) DO NOTHING;
