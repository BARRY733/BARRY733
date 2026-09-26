-- Œil Bleu — source radar ajoutée après le lancement du pilote.
SET search_path = terre, public;

INSERT INTO source (code, nom, type, url, licence, attribution) VALUES
    ('deafrica_s1', 'Digital Earth Africa, radar Sentinel-1 (s1_rtc)', 'satellite',
     'https://www.digitalearthafrica.org/', 'Copernicus Sentinel, CC BY 4.0, à vérifier',
     'Digital Earth Africa ; contient des données Copernicus Sentinel modifiées')
ON CONFLICT (code) DO NOTHING;
