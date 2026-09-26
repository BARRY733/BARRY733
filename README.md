# Œil Bleu

Média d'information qui observe la planète à partir d'images satellites et de données publiques. Ce dépôt contient le prototype décrit dans le document de conception, section « Plan de construction du prototype ».

## Étape 1 : Fondations (ce dépôt)

| Élément | Fichier |
|---|---|
| Modèle Terre (PostgreSQL + PostGIS) | `db/schema.sql` |
| Sources gratuites du pilote et mentions obligatoires | `db/sources.sql` |
| Base locale ou serveur | `docker-compose.yml` |
| Import des points surveillés | `scripts/charger_points.py` |
| Modèle de fichier à remplir par le directeur | `data/points_surveilles.modele.csv` |

### Règles appliquées par la base elle-même

Les agents ne peuvent pas contourner ces règles, même par erreur :

- une publication sans preuve rattachée ne passe pas au statut `publie` ;
- une publication de niveau 2 exige une validation `directeur` non suivie d'un rejet ;
- le niveau 3 (mise en cause d'un acteur nommé) est refusé ;
- une publication se crée en brouillon ou soumise, jamais directement publiée ;
- un contenu diffusé ne peut être ni effacé, ni réécrit, ni privé de ses preuves : on publie une correction liée (`corrige_id`, `motif_correction`) ;
- la mention IA est obligatoire (AI Act) ;
- la vue `lignage` remonte de chaque publication jusqu'à l'image brute, sa date, son traitement et sa source.

## Démarrer

```bash
cp .env.example .env          # puis changer le mot de passe
docker compose up -d          # crée la base et charge schéma + sources
pip install -r requirements.txt
export DATABASE_URL=postgresql://oeil_bleu:<mot-de-passe>@localhost:5432/oeil_bleu
```

## Charger les points surveillés

Copier `data/points_surveilles.modele.csv` en `data/points_surveilles.csv` et remplir une ligne par point (une vingtaine pour commencer) :

| Colonne | Contenu |
|---|---|
| `nom` | Nom usuel du point |
| `latitude`, `longitude` | Degrés décimaux (WGS 84), par exemple `14.6928`, `-17.4467` |
| `type` | `route`, `pont`, `village`, `centre_sante`, `piste`, `barrage` ou `autre` |
| `pays_iso` | Code ISO alpha-3 : `SEN`, `MLI`, `NER`, `GIN`, `MRT`… |
| `notes` | Facultatif |

```bash
python scripts/charger_points.py data/points_surveilles.csv
```

Le chargement est tout ou rien : une seule ligne invalide annule l'import et le script liste les erreurs ligne par ligne. Relancer le même fichier met à jour les points sans créer de doublon. Les fichiers CSV réels ne sont pas versionnés.

## Étape 2 : Collecte

```bash
python -m collecte              # toutes les sources
python -m collecte gdacs eaux   # une sélection
```

| Source | Module | Ce qui entre dans le Modèle Terre |
|---|---|---|
| GDACS | `collecte/gdacs.py` | Événements crue, séisme, sécheresse, feu dans l'emprise |
| USGS | `collecte/usgs.py` | Séismes de magnitude 4,5 et plus |
| FIRMS (NASA) | `collecte/firms.py` | Foyers de feu VIIRS ; clé `FIRMS_MAP_KEY` requise |
| Digital Earth Africa | `collecte/eaux.py` | Par point surveillé : fréquence historique d'eau et état eau/sec des scènes récentes |

- **Emprise** : celle des points surveillés, élargie de 0,5°, ou la variable `EMPRISE`.
- **Preuves** : chaque réponse est archivée telle quelle dans `DONNEES_BRUTES`, avec son empreinte SHA-256, puis rattachée aux événements et aux mesures qu'elle justifie.
- **Robustesse** : chaque source tourne dans sa propre transaction ; une panne n'écrit rien de partiel et n'arrête pas les autres.
- **Idempotence** : relancer la collecte ne crée ni doublon d'événement ni doublon de mesure.
- **À planifier** une fois par jour (cron ou minuterie systemd) sur le serveur.

GloFAS n'est pas encore branché : il exige un compte Copernicus (EWDS) et une clé d'API.

Les formats des API sont tirés de leur documentation publique ; les tests portent sur des réponses types. Ils restent à confirmer au premier appel réel, depuis un environnement dont le réseau autorise ces domaines :
`www.gdacs.org`, `earthquake.usgs.gov`, `firms.modaps.eosdis.nasa.gov`, `explorer.digitalearth.africa`, `deafrica-services.s3.af-south-1.amazonaws.com`.

## Tests

```bash
DATABASE_URL=... pytest -q
```

Les tests recréent le schéma `terre` : à lancer sur une base de test, jamais sur la base de production.

## Étapes suivantes

2. Collecte : GloFAS reste à brancher.
3. Détection : écart à l'étendue historique sur chaque point surveillé.
4. Agents : Analyste, Contradicteur, Rédacteur, Conformité.
5. Bulletin : gabarit, carte avant/après, envoi après validation du directeur.
6. Test à blanc sur les crues 2024 au Sahel.
