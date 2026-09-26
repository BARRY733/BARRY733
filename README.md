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

## Étape 3 : Détection

```bash
python -m detection               # anomalies du jour
python -m detection 2024-09-15    # un jour donné
python -m detection --json        # pour les agents
```

Pour chaque point surveillé, les scènes récentes (16 jours, scènes claires uniquement) sont comparées à la fréquence historique d'eau du pixel.

- **Anomalie** : de l'eau observée là où la fréquence historique est inférieure à 10 %.
- **Confiance** : `a_confirmer` pour une scène en eau ; `moyen` pour deux scènes ou plus ; `eleve` pour deux scènes ou plus sur un point presque jamais en eau (moins de 5 %). Un signal de crue GDACS à moins de 50 km sur la même période relève la confiance d'un cran.
- **Épisodes** : les scènes suivantes enrichissent le même événement ; deux scènes sèches consécutives le closent. Une période nuageuse ne fait pas baisser la confiance acquise.
- **Sorties** : un événement `crue` au statut `detecte`, l'impact `menace` sur le point, les mesures et preuves rattachées.
- **Limite volontaire** : ni gravité ni coupure d'accès ; c'est le travail de l'Agent Analyste (étape 4).

Les seuils (`detection/__init__.py`) sont des valeurs de départ, à calibrer par le test à blanc de l'étape 6.

## Étape 4 : Agents

```bash
python -m agents          # toutes les anomalies au statut « detecte »
python -m agents 12       # un événement précis
```

| Agent | Fiche de mission | Rend |
|---|---|---|
| Analyste | `agents/fiches/analyste.md` | Faits rattachés aux preuves, gravité, impact sur l'accès, confiance |
| Contradicteur | `agents/fiches/contradicteur.md` | Verdict `confirme`, `doute` ou `rejete`, explications alternatives |
| Rédacteur | `agents/fiches/redacteur.md` | Titre, texte de 80 à 150 mots, preuves citées |
| Conformité | `agents/fiches/conformite.md` | Conforme ou liste des manquements |

`agents/fiches/commun.md` rappelle à tous la charte d'Œil Bleu.

- **Dossier fermé** : les agents ne lisent pas la base ; le code leur transmet un dossier (point, mesures, signaux proches, preuves). Ce qui n'y figure pas n'existe pas pour eux.
- **Ordre fixé par le code** : un rejet du Contradicteur arrête la chaîne et classe l'événement `rejete`. Un doute plafonne la confiance à `moyen`. La confiance retenue est toujours la plus prudente des avis.
- **Double contrôle** : en plus de l'Agent Conformité, le code vérifie que les preuves citées existent dans le dossier et que la confiance affichée ne dépasse pas celle retenue.
- **Rien n'est publié** : un texte conforme est `soumis`, niveau 2, en attente du directeur ; un texte non conforme reste `brouillon` avec ses manquements.
- **Modèle** : `claude-opus-5`, sorties structurées, réflexion adaptative. Le repli automatique vers un autre modèle en cas de refus est activé (`fallbacks`).

## Étape 5 : Bulletin

Le circuit du directeur de publication :

```bash
python -m bulletin a-valider          # textes soumis par l'Agent Conformité
python -m bulletin lire 12            # texte complet et preuves
python -m bulletin valider 12
python -m bulletin rejeter 13 "Titre trop affirmatif"
python -m bulletin apercu             # écrit apercu_bulletin.html
python -m bulletin envoyer            # diffusion par courriel
```

- **Gabarit** : courriel HTML compatible avec les messageries courantes, avec une version texte. Chaque article affiche sa confiance, ses sources et la mention IA. Le texte des agents est échappé.
- **Carte avant / après** : à gauche l'étendue habituelle de l'eau (fréquence historique), à droite la scène qui a déclenché la détection, sur 6 km autour du point. Jointe au courriel, pas hébergée ailleurs.
- **Envoi tout ou rien** : seuls les textes validés par le directeur partent. Ils ne passent au statut `publie` que si le courriel est accepté par le serveur ; sinon rien n'est marqué diffusé.
- **Confidentialité** : les destinataires sont en copie cachée.
- **Traçabilité** : la table `bulletin` garde chaque envoi et ses publications.

## Étape 6 : Test à blanc

```bash
python -m rejeu data/verite_2024.csv 2024-07-01 2024-10-31 --balayage
```

Rejoue la collecte et la détection jour après jour sur une saison passée, puis compare les alertes qu'Œil Bleu aurait émises à une **vérité terrain** : rapports ReliefWeb, cartes UNOSAT ou Copernicus EMS, informations du Logistics Cluster.

La vérité terrain se prépare dans `data/verite_terrain.modele.csv`, une ligne par point et par période :

| Colonne | Contenu |
|---|---|
| `nom`, `pays_iso` | Un point surveillé déjà chargé |
| `debut`, `fin` | Période couverte (AAAA-MM-JJ) |
| `inonde` | `oui` si le point était inondé ou coupé, `non` s'il est attesté resté praticable |
| `source` | Rapport ou carte qui l'établit |

- **Confirmée** : le point est signalé inondé à la date de l'alerte (tolérance de 8 jours).
- **Fausse** : le point figure dans la vérité terrain sans y être inondé à cette date.
- **Non évaluable** : le point n'y figure pas. Les lignes `non` sont donc aussi précieuses que les lignes `oui`.
- **Précision** = confirmées / (confirmées + fausses) ; critère de passage : 80 %. Le rapport donne aussi le rappel (crues réelles détectées), le délai médian, et la précision obtenue si l'on attendait qu'un épisode atteigne `moyen` ou `eleve` avant de publier.
- **`--balayage`** compare plusieurs seuils de détection sur les mêmes données, pour les calibrer.
- **Sans trace** : les scènes collectées restent en base (ce sont de vraies données), mais les événements créés par le rejeu sont annulés. L'outil refuse une base qui contient déjà des publications : utiliser une base dédiée.

Le rapport est écrit dans `rapport_test_a_blanc.md`.

## Exploitation quotidienne

`scripts/quotidien.sh` enchaîne collecte, détection et agents, puis liste les textes qui attendent le directeur. Rien n'est publié sans lui. Une étape en échec n'arrête pas les suivantes, mais le script sort en erreur pour le signaler.

Sur le serveur, les fichiers `deploy/oeil-bleu-quotidien.service` et `.timer` le lancent chaque jour à 05 h 17 UTC :

```bash
sudo cp deploy/oeil-bleu-quotidien.* /etc/systemd/system/
sudo systemctl enable --now oeil-bleu-quotidien.timer
journalctl -u oeil-bleu-quotidien   # journal des exécutions
```

Ils supposent le code dans `/opt/oeil-bleu`, un environnement Python dans `.venv` et un utilisateur système `oeilbleu`.

## Tests

```bash
DATABASE_URL=... pytest -q
```

GitHub Actions les relance à chaque envoi de code, sur une base PostGIS jetable (`.github/workflows/tests.yml`).

Les tests recréent le schéma `terre` : à lancer sur une base de test, jamais sur la base de production.

## Étapes suivantes

2. Collecte : GloFAS reste à brancher.
6. Test à blanc : préparer la vérité terrain des crues 2024 au Sahel, puis lancer le rejeu avec l'accès réseau.
