# Œil Bleu

Veille satellitaire des crues sur les bassins du Sénégal et du Niger. Le prototype a un seul but : produire un bulletin réel sur la zone pilote, validé par le directeur de publication.

## Avancement

| Étape | Livrable | État |
| --- | --- | --- |
| 1. Fondations | Base PostGIS avec le Modèle Terre | Fait |
| 2. Collecte | FIRMS, GloFAS, GDACS, Digital Earth Africa | Fait, à tester sur données réelles |
| 3. Détection | Anomalies quotidiennes avec niveau de confiance | Fait, seuils à régler au test à blanc |
| 4. Agents | Analyste, Contradicteur, Rédacteur, Conformité | Fait, à essayer avec une clé API |
| 5. Bulletin | Gabarit, carte avant/après, envoi après validation | Fait, à essayer avec un compte SMTP |
| 6. Test à blanc | Taux de fausses alertes sur les crues passées | Outil prêt, à lancer sur la saison 2024 |

## Démarrer

```bash
cp .env.example .env            # puis changer le mot de passe
docker compose up -d db
pip install -e ".[dev]"
set -a; . ./.env; set +a      # charge les réglages
python -m oeil_bleu migrer
python -m oeil_bleu importer-points data/points_surveilles.csv
```

## Collecte (étape 2)

```bash
pip install -e ".[satellite,glofas,agents,carte]"
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
| `sentinel1` | Signal radar VV, qui traverse les nuages | Chaque point surveillé | Aucune |

Chaque passage est noté dans `terre.collecte` (réussi ou échoué, avec l'erreur). Les données vont dans `terre.observation`. Une collecte peut être rejouée sans doublon ; un échec n'enregistre rien de partiel et n'empêche pas les autres sources.

Chaque matin, `python -m oeil_bleu quotidien` enchaîne collecte, détection et agents, puis indique combien de textes attendent la validation du directeur. Il n'envoie rien : le bulletin reste une décision humaine. Une source en panne n'arrête pas la chaîne ; sans clé API, les agents sont sautés. Une ligne cron suffit :

```cron
0 6 * * * cd /srv/oeil-bleu && set -a && . ./.env && set +a && .venv/bin/python -m oeil_bleu quotidien >> /var/log/oeil-bleu.log 2>&1
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

**Radar Sentinel-1.** En saison des pluies, les nuages masquent souvent Landsat. Le radar voit à travers, mais le sable sec et les pistes renvoient aussi peu de signal que l'eau. Un passage radar ne compte donc comme « eau » que si le signal est sous −18 dB **et** a chuté d'au moins 3 dB par rapport à la médiane du point sur l'année précédente (au moins 5 passages). Un point toujours sombre, comme une dune, ne déclenche rien. Le radar ne sert qu'à voir de l'eau, jamais à déclarer un point sec : une crue sous la végétation peut renforcer le signal. Une eau vue au radar seulement coûte 0,10 de confiance.

Première mise en route : collecter une année de radar pour établir la référence de chaque point.

```bash
python -m oeil_bleu collecter sentinel1 --debut 2023-09-01 --fin 2024-08-31
```

Une alerte GDACS orange ou rouge sans eau confirmée par satellite donne au plus une « menace de crue » de niveau faible. Si le dernier passage dégagé montre un point sec, l'alerte est ignorée pour ce point. Les points sans fréquence historique ne sont pas évalués et sont listés à part.

Les anomalies sont enregistrées dans `terre.anomalie` avec les observations qui les justifient. Un nouveau calcul du même jour remplace les anomalies non traitées et laisse intactes celles déjà transmises ou écartées. Les seuils sont regroupés en tête de `oeil_bleu/detection.py` pour le réglage de l'étape 6.

## Agents (étape 4)

```bash
python -m oeil_bleu agents                        # anomalies du jour, confiance ≥ 0,5
python -m oeil_bleu agents --jour 2024-09-10 --confiance-min 0.75
python -m oeil_bleu verifier-licence deafrica_wofs   # le directeur confirme une licence
```

Chaque anomalie passe par quatre agents, dans l'ordre. Chacun peut arrêter la chaîne.

| Agent | Question | Si non |
| --- | --- | --- |
| Analyste | Est-ce bien une crue ? Gravité, confiance, impact sur l'accès | Anomalie écartée |
| Contradicteur | Une autre explication tient-elle (rizière, barrage, mare, erreur de pixel) ? | Rejet : écartée. Doute : reste ouverte pour le prochain passage |
| Rédacteur | Texte de 120 mots au plus, faits du dossier uniquement | — |
| Conformité | Mise en cause, sujet sécuritaire, fait non étayé, conseil opérationnel ? | Publication bloquée en brouillon |

Un texte validé par les quatre arrive au directeur en statut `en_validation`, avec ses sources, la mention IA et ses preuves rattachées. La base refuse toujours de le valider sans l'approbation d'un humain.

Garde-fous :

- la confiance retenue est la plus prudente de l'Analyste et du Contradicteur ;
- une source dont la licence n'est pas confirmée bloque le texte ;
- les textes venus de l'extérieur (descriptions GDACS) sont traités comme des données, jamais comme des consignes ;
- si le modèle décline une demande, l'API bascule sur un autre modèle ; si tous déclinent, l'anomalie reste ouverte pour l'humain ;
- chaque appel est tracé dans `terre.passage_agent` : dossier reçu, réponse, identifiant de requête, jetons consommés.

Modèle : `claude-opus-5` par défaut, modifiable avec `OEIL_BLEU_MODELE`. Les consignes des agents sont dans `oeil_bleu/agents/consignes.py`.

## Bulletin (étape 5)

### Page de validation

```bash
pip install -e ".[web]"
python -m oeil_bleu web          # puis ouvrir http://127.0.0.1:8000
```

Le directeur y lit chaque texte avec sa carte avant/après, l'avis des agents et les éléments de détection, puis clique sur **Valider** ou **Rejeter** (motif obligatoire). La même page montre l'aperçu du bulletin et l'envoie, après une case de confirmation. Les derniers envois y sont listés.

Sécurité : mot de passe (`DIRECTEUR_MOT_DE_PASSE`), jeton contre la falsification de formulaires, et écoute de la seule machine locale par défaut. Pour y accéder depuis un autre appareil, placer la page derrière un proxy HTTPS (Caddy, Nginx) : sans HTTPS, le mot de passe circule en clair.

### En ligne de commande

Les mêmes actions restent disponibles dans le terminal :

```bash
python -m oeil_bleu a-valider                                   # textes en attente, avec l'avis des agents
python -m oeil_bleu valider 12 --par "Nom du directeur"
python -m oeil_bleu rejeter 13 --par "Nom du directeur" --motif "point mal placé"
```

Puis le bulletin :

```bash
python -m oeil_bleu bulletin                 # aperçu dans bulletin.html, rien n'est envoyé
python -m oeil_bleu bulletin --envoyer       # envoi réel aux destinataires
python -m oeil_bleu bulletin --envoyer --meme-vide   # envoie aussi « rien à signaler »
```

- Seules les publications validées par un humain entrent dans le bulletin, et chacune ne part qu'une fois.
- Chaque alerte porte une carte avant/après sur 3 km autour du point : l'étendue d'eau habituelle en bleu, l'eau inhabituelle en orange, les zones sous nuages hachurées. Les couleurs ont été vérifiées pour les daltoniens. Si les images satellites sont inaccessibles, l'alerte part sans carte.
- Chaque destinataire reçoit son propre message : aucune adresse n'est visible des autres.
- Si tous les envois échouent, rien n'est marqué comme publié et le bulletin peut être relancé.
- Envoi par SMTP, compatible avec tout fournisseur (réglages `SMTP_*` dans `.env`).
- Les destinataires sont dans `data/destinataires.csv` (colonnes `courriel,nom,organisation`, modèle dans `data/destinataires.exemple.csv`). Ce fichier contient des données personnelles : il n'est pas versionné.

## Test à blanc (étape 6)

But : mesurer le taux de fausses alertes et le taux de détection avant tout envoi réel, en rejouant une saison passée, par exemple les crues de 2024 au Sahel.

1. Importer les points surveillés (voir plus bas).
2. Collecter la saison : `python -m oeil_bleu collecter gdacs deafrica --debut 2024-07-01 --fin 2024-10-31`, et `sentinel1` du 2023-09-01 au 2024-10-31.
3. Remplir `data/verite_terrain.csv` : une ligne par crue réellement constatée sur un point surveillé (`point,debut,fin,source`), d'après les rapports de situation (OCHA, ReliefWeb, protection civile) et les contacts locaux. Un point absent du fichier est réputé non inondé sur la période.
4. Lancer : `python -m oeil_bleu test-a-blanc --debut 2024-07-01 --fin 2024-10-31`

Le rejeu ne modifie pas la base. Il affiche, pour chaque seuil de confiance (faible, moyen, élevé) :

| Mesure | Définition |
| --- | --- |
| Alertes | Épisodes d'alerte : jours consécutifs sur un même point |
| Fausses % | Part des épisodes sans crue constatée (tolérance de 7 jours après la fin de la crue) |
| Détection % | Part des crues constatées qui ont déclenché au moins une alerte |
| Délai médian | Jours entre le début constaté de la crue et la première alerte |

Le détail de chaque épisode est écrit dans `episodes.csv`, pour examiner les fausses alertes une à une. Les réglages à ajuster sont en tête de `oeil_bleu/detection.py`.

Pour rejouer 2024 avec le radar, collecter aussi `sentinel1` depuis septembre 2023 (référence d'un an). Limites du rejeu : GloFAS et FIRMS ne sont pas rejoués. La fréquence historique de l'eau inclut l'année rejouée elle-même, ce qui rend la détection un peu plus prudente qu'en conditions réelles.

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
- Les seuils radar (−18 dB, chute de 3 dB) sont des valeurs de départ courantes, à calibrer lors du test à blanc.

## Tests

```bash
TEST_DATABASE_URL=postgresql://… pytest
```

La base de test doit être vide : les tests effacent le schéma `terre`.
