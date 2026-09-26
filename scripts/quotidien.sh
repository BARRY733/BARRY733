#!/usr/bin/env bash
# Chaîne quotidienne d'Œil Bleu : collecte, détection, agents.
# Rien n'est publié : les textes attendent la décision du directeur
# (python -m bulletin a-valider). Une étape en échec n'empêche pas les suivantes.
set -u
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && . ./.env; set +a

echo "== $(date -u +%FT%TZ) Œil Bleu, chaîne quotidienne"
statut=0
python -m collecte || statut=1
python -m detection || statut=1
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  python -m agents || statut=1
else
  echo "agents : ANTHROPIC_API_KEY absente, étape sautée"
fi
python -m bulletin a-valider || statut=1
exit $statut
