-- Œil Bleu — licences relevées le 26 septembre 2026 sur les pages officielles des sources.
-- La confirmation (licence_verifiee) reste un acte du directeur de publication :
--   python -m oeil_bleu verifier-licence <source>
-- « {annee} » est remplacé par l'année de publication dans les textes.
SET search_path = terre, public;

UPDATE source AS s SET licence = v.licence, attribution = v.attribution, url = v.url
FROM (VALUES
    ('gdacs', 'CC BY 4.0 (Commission européenne, JRC)',
     'GDACS, Commission européenne (JRC)', 'https://gdacs.org/'),
    ('firms', 'Données ouvertes NASA (ESDIS), mention demandée',
     'NASA FIRMS (LANCE, ESDIS)', 'https://firms.modaps.eosdis.nasa.gov/'),
    ('glofas', 'Licence CEMS-Floods : accès libre, complet et gratuit (règlement UE 377/2014)',
     'Copernicus Emergency Management Service, GloFAS', 'https://ecds.ecmwf.int/licences/cems-floods'),
    ('deafrica_wofs', 'CC BY 4.0 (Digital Earth Africa)',
     'Digital Earth Africa', 'https://registry.opendata.aws/deafrica-wofs/'),
    ('deafrica_s1', 'CC BY 4.0 (Digital Earth Africa) ; données Copernicus Sentinel',
     'Digital Earth Africa ; contient des données Copernicus Sentinel modifiées {annee}',
     'https://registry.opendata.aws/deafrica-sentinel-1/'),
    ('sentinel2', 'Données Copernicus Sentinel : accès libre, complet et gratuit (avis juridique ESA)',
     'Contient des données Copernicus Sentinel modifiées {annee}',
     'https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice'),
    ('jrc_gsw', 'Programme Copernicus : gratuit, sans restriction d''usage, citation demandée',
     'EC JRC/Google (Pekel et al., 2016, Nature 540, 418-422)', 'https://global-surface-water.appspot.com/download')
) AS v (code, licence, attribution, url)
WHERE s.code = v.code;
