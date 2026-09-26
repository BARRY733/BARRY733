-- Sources gratuites retenues pour le pilote. Mentions à vérifier auprès de
-- chaque fournisseur lors du cadrage technique.
SET search_path = terre, public;

INSERT INTO source (code, nom, type, url, licence, mention_obligatoire) VALUES
  ('deafrica',  'Digital Earth Africa',        'satellite',     'https://www.digitalearthafrica.org', 'CC BY 4.0',
   'Données Digital Earth Africa'),
  ('sentinel1', 'Sentinel-1 (Copernicus)',     'satellite',     'https://dataspace.copernicus.eu',    'Licence Copernicus',
   'Contient des données Copernicus Sentinel modifiées'),
  ('sentinel2', 'Sentinel-2 (Copernicus)',     'satellite',     'https://dataspace.copernicus.eu',    'Licence Copernicus',
   'Contient des données Copernicus Sentinel modifiées'),
  ('landsat',   'Landsat (NASA, USGS)',        'satellite',     'https://www.usgs.gov/landsat-missions', 'Domaine public',
   'Image Landsat, avec l''aimable autorisation de l''USGS'),
  ('firms',     'FIRMS (NASA)',                'base_publique', 'https://firms.modaps.eosdis.nasa.gov', 'Données ouvertes NASA',
   'NASA FIRMS'),
  ('glofas',    'GloFAS (Copernicus)',         'base_publique', 'https://global-flood.emergency.copernicus.eu', 'Licence Copernicus',
   'Copernicus Emergency Management Service, GloFAS'),
  ('chirps',    'CHIRPS',                      'base_publique', 'https://www.chc.ucsb.edu/data/chirps', 'CC BY 4.0',
   'CHIRPS, Climate Hazards Center, UC Santa Barbara'),
  ('gdacs',     'GDACS',                       'base_publique', 'https://www.gdacs.org', 'Données ouvertes',
   'GDACS'),
  ('usgs',      'USGS séismes',                'base_publique', 'https://earthquake.usgs.gov', 'Domaine public',
   'USGS'),
  ('worldpop',  'WorldPop',                    'base_publique', 'https://www.worldpop.org', 'CC BY 4.0',
   'WorldPop'),
  ('osm',       'OpenStreetMap',               'base_publique', 'https://www.openstreetmap.org', 'ODbL',
   '© contributeurs OpenStreetMap'),
  ('reliefweb', 'ReliefWeb',                   'rapport',       'https://reliefweb.int', 'Variable selon le rapport',
   'ReliefWeb')
ON CONFLICT (code) DO NOTHING;
