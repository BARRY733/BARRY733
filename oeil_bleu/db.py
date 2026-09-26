"""Connexion à la base et application des migrations."""

import os
from pathlib import Path

import psycopg

RACINE = Path(__file__).resolve().parent.parent
MIGRATIONS = RACINE / "db" / "migrations"
SEEDS = RACINE / "db" / "seeds"


def connecter(url: str | None = None) -> psycopg.Connection:
    url = url or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL n'est pas défini (voir .env.example)")
    return psycopg.connect(url)


def migrer(conn: psycopg.Connection, avec_seeds: bool = True) -> list[str]:
    """Applique une seule fois chaque fichier SQL, dans l'ordre des noms."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS public.migration_appliquee ("
        " fichier text PRIMARY KEY, applique_le timestamptz NOT NULL DEFAULT now())"
    )
    deja = {r[0] for r in conn.execute("SELECT fichier FROM public.migration_appliquee")}
    fichiers = sorted(MIGRATIONS.glob("*.sql"))
    if avec_seeds:
        fichiers += sorted(SEEDS.glob("*.sql"))
    appliques = []
    for f in fichiers:
        cle = f"{f.parent.name}/{f.name}"
        if cle in deja:
            continue
        with conn.transaction():
            conn.execute(f.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO public.migration_appliquee (fichier) VALUES (%s)", (cle,))
        appliques.append(cle)
    return appliques
