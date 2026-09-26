FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY oeil_bleu ./oeil_bleu
COPY db ./db
COPY outils ./outils
# Installation en place : migrations et données restent lues depuis /app.
RUN pip install --no-cache-dir -e ".[satellite,glofas,agents,carte,web]"
ENV PYTHONUNBUFFERED=1
# --preload : une seule application, donc une seule clé de session pour tous les processus.
CMD ["gunicorn", "--preload", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120", "oeil_bleu.web:creer_app()"]
