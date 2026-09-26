"""Base de test commune.

Nécessite DATABASE_URL vers une base PostGIS de test : le schéma `terre`
y est recréé. Chaque test tourne dans une transaction annulée.
"""

import os
import pathlib
import sys

import psycopg
import pytest

RACINE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))


def pytest_collection_modifyitems(items):
    if "DATABASE_URL" in os.environ:
        return
    saut = pytest.mark.skip(reason="DATABASE_URL absent")
    for item in items:
        if "cur" in item.fixturenames:
            item.add_marker(saut)


@pytest.fixture(scope="session")
def base():
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS terre CASCADE")
        for fichier in ("schema.sql", "sources.sql"):
            conn.execute((RACINE / "db" / fichier).read_text())
        yield conn


@pytest.fixture
def cur(base):
    base.autocommit = False
    with base.cursor() as c:
        c.execute("SET search_path = terre, public")
        yield c
    base.rollback()
    base.autocommit = True
