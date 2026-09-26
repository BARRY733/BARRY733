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

## Tests

```bash
DATABASE_URL=... pytest -q
```

Les tests recréent le schéma `terre` : à lancer sur une base de test, jamais sur la base de production.

## Étapes suivantes

2. Collecte : FIRMS, GloFAS, GDACS, surfaces en eau Digital Earth Africa sur la zone pilote.
3. Détection : écart à l'étendue historique sur chaque point surveillé.
4. Agents : Analyste, Contradicteur, Rédacteur, Conformité.
5. Bulletin : gabarit, carte avant/après, envoi après validation du directeur.
6. Test à blanc sur les crues 2024 au Sahel.
