-- Œil Bleu — sources mondiales lisibles sans catalogue (dépôts publics).
SET search_path = terre, public;

INSERT INTO source (code, nom, type, url, licence, attribution) VALUES
    ('sentinel2', 'Sentinel-2 L2A, classification de surface (AWS Earth Search)', 'satellite',
     'https://registry.opendata.aws/sentinel-2-l2a-cogs/',
     'Données Copernicus Sentinel, accès libre et gratuit, à vérifier',
     'Contient des données Copernicus Sentinel modifiées'),
    ('jrc_gsw', 'JRC Global Surface Water, fréquence de l''eau 1984-2021', 'satellite',
     'https://global-surface-water.appspot.com/',
     'Commission européenne JRC, réutilisation libre avec citation, à vérifier',
     'EC JRC / Google, Pekel et al. (2016), Nature 540, 418-422')
ON CONFLICT (code) DO NOTHING;
