# Œil Bleu

Veille satellitaire des crues sur les bassins du Sénégal et du Niger. Le prototype a un seul but : produire un bulletin réel sur la zone pilote, validé par le directeur de publication.

## Avancement

| Étape | Livrable | État |
| --- | --- | --- |
| 1. Fondations | Base PostGIS avec le Modèle Terre | Fait |
| 2. Collecte | FIRMS, GloFAS, GDACS, Digital Earth Africa | Fait, à tester sur données réelles |
| 3. Détection | Anomalies quotidiennes avec niveau de confiance | Fait, seuils à régler au test à blanc |
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

## Détection (étape 3)

```bash
python -m oeil_bleu detecter                   # aujourd'hui
python -m oeil_bleu detecter --jour 2024-09-10
```

Règle centrale : de l'eau vue par satellite sur un point où, par le passé, il y en a moins de 20 % du temps. Sur une fenêtre de 16 jours (deux passages Landsat), la confiance se construit ainsi :

| Élément | Effet sur la confiance |
| --- | --- |
| Eau au dernier passage dégagé, fréquence historique < 5 % | 0,60 de départ |
| Idem, fréquence historique entre 5 et 20 % | 0,45 de départ |
| Au moins deux passages avec eau | + 0,15 |
| Alerte GDACS orange ou rouge à moins de 50 km | + 0,15 (verte : + 0,05) |
| Débit GloFAS prévu en hausse d'au moins 50 % | + 0,10 |
| Plafond | 0,95 |

Niveau : élevé à partir de 0,75, moyen à partir de 0,50, faible en dessous.

Une alerte GDACS orange ou rouge sans eau confirmée par satellite donne au plus une « menace de crue » de niveau faible. Si le dernier passage dégagé montre un point sec, l'alerte est ignorée pour ce point. Les points sans fréquence historique ne sont pas évalués et sont listés à part.

Les anomalies sont enregistrées dans `terre.anomalie` avec les observations qui les justifient. Un nouveau calcul du même jour remplace les anomalies non traitées et laisse intactes celles déjà transmises ou écartées. Les seuils sont regroupés en tête de `oeil_bleu/detection.py` pour le réglage de l'étape 6.

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
- Digital Earth Africa est lu sur un seul pixel de 30 m par point. GloFAS retient, parmi la maille la plus proche et ses 8 voisines, celle au plus fort débit : c'est en général le fleuve, à vérifier point par point.
- Les nuages de la saison des pluies masquent souvent Landsat : une crue peut passer entre deux passages dégagés. Le radar Sentinel-1, qui voit à travers les nuages, serait le prochain ajout.

## Tests

```bash
TEST_DATABASE_URL=postgresql://… pytest
```

La base de test doit être vide : les tests effacent le schéma `terre`.
