import os

import psycopg
import pytest

from oeil_bleu import db

URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
def conn():
    """Base PostGIS remise à zéro. Nécessite TEST_DATABASE_URL (base vide dédiée aux tests)."""
    if not URL:
        pytest.skip("TEST_DATABASE_URL non défini")
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute("DROP SCHEMA IF EXISTS terre CASCADE")
        c.execute("DROP TABLE IF EXISTS public.migration_appliquee")
        db.migrer(c)
        yield c
