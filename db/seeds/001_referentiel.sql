-- Œil Bleu — référentiel initial du pilote.
-- Sources gratuites uniquement, et emprises provisoires des deux bassins.
SET search_path = terre, public;

-- Licences à confirmer par l'Agent Conformité avant toute publication.
INSERT INTO source (code, nom, type, url, licence, attribution) VALUES
    ('firms',  'NASA FIRMS (foyers actifs VIIRS/MODIS)', 'satellite',
     'https://firms.modaps.eosdis.nasa.gov/', 'Données ouvertes NASA, à vérifier',
     'NASA FIRMS'),
    ('glofas', 'Copernicus GloFAS (prévision de crues)', 'base_publique',
     'https://global-flood.emergency.copernicus.eu/', 'Licence Copernicus, à vérifier',
     'Copernicus Emergency Management Service, GloFAS'),
    ('gdacs',  'GDACS (alertes de catastrophes)', 'base_publique',
     'https://www.gdacs.org/', 'Conditions GDACS, à vérifier',
     'GDACS, Commission européenne et Nations unies'),
    ('deafrica_wofs', 'Digital Earth Africa, surfaces en eau (WOfS)', 'satellite',
     'https://www.digitalearthafrica.org/', 'CC BY 4.0, à vérifier',
     'Digital Earth Africa')
ON CONFLICT (code) DO NOTHING;

-- Emprises rectangulaires approximatives, à remplacer par les contours
-- HydroBASINS avant la détection (étape 3).
INSERT INTO zone (code, nom, type, geom, notes) VALUES
    ('bassin_senegal', 'Bassin du fleuve Sénégal', 'bassin',
     ST_Multi(ST_MakeEnvelope(-16.6, 10.5, -7.5, 17.5, 4326)),
     'Emprise provisoire approximative'),
    ('bassin_niger', 'Bassin du fleuve Niger', 'bassin',
     ST_Multi(ST_MakeEnvelope(-12.0, 4.0, 15.0, 24.0, 4326)),
     'Emprise provisoire approximative')
ON CONFLICT (code) DO NOTHING;
