"""Génère db/seeds/004_contours_natural_earth.sql à partir de Natural Earth (domaine public).

Source : https://github.com/nvkelso/natural-earth-vector (ne_50m_admin_0_countries.geojson).
Usage : python outils/contours_natural_earth.py chemin/vers/ne_50m_admin_0_countries.geojson
"""

import json
import sys
from pathlib import Path

SORTIE = Path(__file__).resolve().parent.parent / "db" / "seeds" / "004_contours_natural_earth.sql"
CONTINENTS = {  # région ONU Natural Earth → (code, nom)
    "Africa": ("afrique", "Afrique"),
    "Americas": ("ameriques", "Amériques"),
    "Asia": ("asie", "Asie"),
    "Europe": ("europe", "Europe"),
    "Oceania": ("oceanie", "Océanie"),
}


def sql_texte(t: str) -> str:
    return "'" + t.replace("'", "''") + "'"


def main(chemin: str) -> None:
    pays = json.loads(Path(chemin).read_text(encoding="utf-8"))["features"]
    lignes = [
        "-- Œil Bleu — contours réels des pays et des continents (Natural Earth 1:50 m, domaine public).",
        "-- Généré par outils/contours_natural_earth.py : ne pas modifier à la main.",
        "SET search_path = terre, public;",
        "",
        "CREATE TEMP TABLE ne_pays (code text, nom text, continent text, geom geometry) ON COMMIT DROP;",
    ]
    valeurs = []
    for f in pays:
        p = f["properties"]
        region = p.get("REGION_UN")
        if region not in CONTINENTS or not f.get("geometry"):
            continue
        code = p["ADM0_A3"].lower()
        nom = p.get("NAME_FR") or p["NAME"]
        geo = json.dumps(f["geometry"], separators=(",", ":"))
        valeurs.append(f"({sql_texte('pays_' + code)}, {sql_texte(nom)}, {sql_texte(CONTINENTS[region][0])}, "
                       f"ST_Multi(ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON({sql_texte(geo)}), 4326))))")
    lignes.append("INSERT INTO ne_pays VALUES\n" + ",\n".join(valeurs) + ";")
    lignes += [
        "",
        "-- Continents : union des pays. Remplace l'emprise rectangulaire provisoire de l'Afrique.",
        "INSERT INTO zone (code, nom, type, geom, notes)",
        "SELECT c.code, c.nom, 'continent', ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_Union(p.geom)), 3)),",
        "       'Natural Earth 1:50 m'",
        "FROM (VALUES " + ", ".join(f"({sql_texte(c)}, {sql_texte(n)})" for c, n in CONTINENTS.values())
        + ") AS c (code, nom)",
        "JOIN ne_pays p ON p.continent = c.code",
        "GROUP BY c.code, c.nom",
        "ON CONFLICT (code) DO UPDATE SET geom = EXCLUDED.geom, notes = EXCLUDED.notes;",
        "",
        "INSERT INTO zone (code, nom, type, parent_id, geom, notes)",
        "SELECT p.code, p.nom, 'pays', z.id, ST_Multi(ST_CollectionExtract(p.geom, 3)), 'Natural Earth 1:50 m'",
        "FROM ne_pays p JOIN zone z ON z.code = p.continent",
        "ON CONFLICT (code) DO NOTHING;",
        "",
    ]
    SORTIE.write_text("\n".join(lignes), encoding="utf-8")
    print(f"{SORTIE} : {len(valeurs)} pays")


if __name__ == "__main__":
    main(sys.argv[1])
