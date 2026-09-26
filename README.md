# Œil Bleu

Veille satellitaire des crues sur les bassins du Sénégal et du Niger. Le prototype a un seul but : produire un bulletin réel sur la zone pilote, validé par le directeur de publication.

## Avancement

| Étape | Livrable | État |
| --- | --- | --- |
| 1. Fondations | Base PostGIS avec le Modèle Terre | Fait |
| 2. Collecte | FIRMS, GloFAS, GDACS, Digital Earth Africa | Fait, à tester sur données réelles |
| 3. Détection | Anomalies quotidiennes avec niveau de confiance | À venir |
| 4. Agents | Analyste, Contradicteur, Rédacteur, Conformité | À venir |
| 5. Bulletin | Gabarit, carte avant/après, envoi après validation | À venir |
| 6. Test à blanc | Taux de fausses alertes sur les crues passées | À venir |

## Démarrer

```bash
cp .env.example .env            # puis changer le mot de passe
docker compose up -d db
pip install -e ".[dev]"
export $(grep -v '^#' .env | xargs)
python -m oeil_bleu migrer
python -m oeil_bleu importer-points data/points_surveilles.csv
```

## Collecte (étape 2)

```bash
pip install -e ".[satellite,glofas]"
python -m oeil_bleu migrer                   # ajoute les tables de collecte
python -m oeil_bleu collecter                # toutes les sources
python -m oeil_bleu collecter gdacs firms    # ou quelques-unes
```

| Source | Ce qui est collecté | Où | Clé |
| --- | --- | --- | --- |
| `gdacs` | Alertes d'inondation (vert, orange, rouge) | Emprise des deux bassins | Aucune |
| `firms` | Foyers de feu VIIRS et leur puissance | Emprise des deux bassins | `FIRMS_MAP_KEY` |
| `deafrica` | Eau observée par Landsat, et fréquence historique de l'eau | Chaque point surveillé | Aucune |
| `glofas` | Débit prévu à 1 à 10 jours | Chaque point surveillé | `CDSAPI_KEY` |

Chaque passage est noté dans `terre.collecte` (réussi ou échoué, avec l'erreur). Les données vont dans `terre.observation`. Une collecte peut être rejouée sans doublon ; un échec n'enregistre rien de partiel et n'empêche pas les autres sources.

Pour une collecte quotidienne, une ligne cron suffit :

```cron
0 6 * * * cd /srv/oeil-bleu && .venv/bin/python -m oeil_bleu collecter >> /var/log/oeil-bleu.log 2>&1
```

## Modèle Terre

Schéma `terre` dans `db/migrations/001_modele_terre.sql` : zone, événement, indicateur, population exposée, infrastructure, source, preuve, publication, validation.

La base applique elle-même la charte éditoriale :

- une publication ne peut être validée ou publiée sans au moins une preuve rattachée ;
- une publication de niveau 2 exige l'approbation d'un humain ;
- le niveau 3 (mise en cause d'un acteur nommé) est refusé.

## Points surveillés

Le directeur fournit `data/points_surveilles.csv`, une vingtaine de lignes au départ :

| Colonne | Contenu |
| --- | --- |
| `nom` | Nom du lieu |
| `type` | `route`, `pont`, `village`, `centre_sante`, `piste` ou `barrage` |
| `latitude`, `longitude` | Degrés décimaux WGS 84 |
| `zone_code` | `bassin_senegal` ou `bassin_niger` |
| `notes` | Facultatif |

L'import refuse le fichier entier si une ligne est invalide ou hors de sa zone. Il peut être relancé sans créer de doublons.

## Limites connues

- Les emprises des bassins sont des rectangles approximatifs, à remplacer par les contours HydroBASINS avant l'étape 3.
- Les licences des sources sont marquées « à vérifier » en attendant l'Agent Conformité.
- Les collecteurs n'ont été testés que sur des données d'exemple : l'environnement de développement n'a pas accès aux serveurs des sources. Premier passage réel à surveiller.
- Digital Earth Africa est lu sur un seul pixel de 30 m par point ; GloFAS sur la maille de 5 km la plus proche, qui n'est pas toujours celle du fleuve. L'étape 3 affinera ce rattachement.

## Tests

```bash
TEST_DATABASE_URL=postgresql://… pytest
```

La base de test doit être vide : les tests effacent le schéma `terre`.
