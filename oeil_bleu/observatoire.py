"""Observatoire : l'état de chaque site surveillé, tiré de la base et de rien d'autre.

Produit un instantané JSON (sites, mesures, séries, sources, fond de carte) que la
page web affiche. Le même instantané sert à exporter une version autonome.
"""

import json
from datetime import date, datetime, timezone
from pathlib import Path

import psycopg

from .detection import FREQ_RARE, REFERENCE_JOURS, frequence_reference

EAU_PERMANENTE = 0.5    # au-delà, le point est presque toujours en eau : il est mal placé


def _etat(freq, anomalie) -> tuple[str, str]:
    if anomalie:
        return "alerte", f"Anomalie « {anomalie['type']} », confiance {anomalie['niveau']}"
    if freq is None:
        return "incomplet", "Pas encore de fréquence historique : le point n'est pas évalué"
    if freq >= EAU_PERMANENTE:
        return "eau_permanente", "Eau presque toujours présente : aucune crue n'y est détectable, point à déplacer"
    if freq >= FREQ_RARE:
        return "surveille", "Eau fréquente : seules les crues marquées ressortiront"
    return "surveille", "Sous surveillance : aucune eau inhabituelle"


def instantane(conn: psycopg.Connection, jour: date | None = None) -> dict:
    jour = jour or date.today()
    debut = datetime.combine(jour, datetime.min.time(), tzinfo=timezone.utc)
    sites = []
    for iid, nom, type_, lon, lat, pays, notes in conn.execute(
        "SELECT i.id, i.nom, i.type, ST_X(ST_PointOnSurface(i.geom)), ST_Y(ST_PointOnSurface(i.geom)),"
        " coalesce((SELECT z.nom FROM terre.zone z WHERE z.type = 'pays' AND ST_DWithin(z.geom::geography,"
        "   i.geom::geography, 5000) ORDER BY ST_Distance(z.geom::geography, i.geom::geography) LIMIT 1), '—'),"
        " i.notes FROM terre.infrastructure i WHERE i.surveille ORDER BY i.nom"
    ).fetchall():
        freq, reference = frequence_reference(conn, iid, jour, debut)
        freq = freq[1] if freq else None
        annuel = conn.execute(
            "SELECT (brut->>'annee')::int, valeur, (brut->>'passages_degages')::float FROM terre.observation"
            " WHERE infrastructure_id = %s AND variable = 'eau_annuelle' ORDER BY 1", (iid,)).fetchall()
        radar = conn.execute(
            "SELECT observe_le::date, valeur FROM terre.observation WHERE infrastructure_id = %s"
            " AND variable = 'retrodiffusion_vv' AND observe_le::date <= %s ORDER BY 1", (iid, jour)).fetchall()
        optique = conn.execute(
            "SELECT observe_le::date, valeur, coalesce(brut->>'capteur', 'landsat') FROM terre.observation"
            " WHERE infrastructure_id = %s AND variable = 'eau_observee' AND observe_le::date <= %s ORDER BY 1",
            (iid, jour)).fetchall()
        ref_radar = conn.execute(
            "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY valeur), count(*) FROM terre.observation"
            " WHERE infrastructure_id = %s AND variable = 'retrodiffusion_vv'"
            " AND observe_le >= %s::date - %s AND observe_le < %s::date - 16",
            (iid, jour, REFERENCE_JOURS, jour)).fetchone()
        gsw = conn.execute(
            "SELECT valeur FROM terre.observation WHERE infrastructure_id = %s AND variable = 'frequence_eau_historique'"
            " AND brut->>'produit' = 'gsw_occurrence' LIMIT 1", (iid,)).fetchone()
        anomalie = conn.execute(
            "SELECT jour, type, niveau, confiance, statut FROM terre.anomalie WHERE infrastructure_id = %s"
            " AND jour <= %s AND statut <> 'ecartee' ORDER BY jour DESC LIMIT 1", (iid, jour)).fetchone()
        anomalie = dict(zip(("jour", "type", "niveau", "confiance", "statut"), anomalie)) if anomalie else None
        etat, explication = _etat(freq, anomalie)
        sites.append({
            "id": iid, "nom": nom, "type": type_, "pays": pays, "lon": lon, "lat": lat, "notes": notes,
            "etat": etat, "explication": explication, "anomalie": anomalie,
            "frequence": freq, "reference": reference, "gsw": gsw[0] if gsw else None,
            "annuel": [{"annee": a, "eau": w, "degages": c} for a, w, c in annuel],
            "radar": [{"date": d, "db": v} for d, v in radar],
            "radar_reference": ref_radar[0] if ref_radar and ref_radar[1] >= 5 else None,
            "optique": [{"date": d, "eau": int(v), "capteur": c} for d, v, c in optique],
        })
    sources = [dict(zip(("code", "nom", "licence_verifiee", "attribution", "derniere", "statut", "erreur", "nb"), r))
               for r in conn.execute(
        "SELECT s.code, s.nom, s.licence_verifiee, s.attribution, c.debut, c.statut, c.erreur,"
        " (SELECT count(*) FROM terre.observation o WHERE o.source_id = s.id)"
        " FROM terre.source s LEFT JOIN LATERAL (SELECT debut, statut, erreur FROM terre.collecte"
        "   WHERE source_id = s.id ORDER BY debut DESC LIMIT 1) c ON true ORDER BY s.code")]
    compteurs = {
        "a_valider": conn.execute("SELECT count(*) FROM terre.publication WHERE statut = 'en_validation'").fetchone()[0],
        "publiees": conn.execute("SELECT count(*) FROM terre.publication WHERE statut = 'publiee'").fetchone()[0],
    }
    return {"jour": jour, "genere_le": datetime.now(timezone.utc), "sites": sites, "sources": sources,
            "compteurs": compteurs, "fond": fond_de_carte(conn, sites)}


def fond_de_carte(conn, sites, marge: float = 9.0) -> dict:
    """Pays autour des sites, simplifiés pour l'affichage (Natural Earth)."""
    if sites:
        lons, lats = [s["lon"] for s in sites], [s["lat"] for s in sites]
        emprise = [min(lons) - marge, min(lats) - marge * 0.7, max(lons) + marge, max(lats) + marge * 0.7]
    else:
        emprise = [-18.0, 4.0, 16.0, 24.0]    # Afrique de l'Ouest par défaut
    pays = [{"nom": n, "geo": json.loads(g)} for n, g in conn.execute(
        "SELECT nom, ST_AsGeoJSON(ST_Intersection(ST_SimplifyPreserveTopology(geom, 0.04),"
        " ST_MakeEnvelope(%s, %s, %s, %s, 4326)), 3)"
        " FROM terre.zone WHERE type = 'pays' AND geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)",
        (*emprise, *emprise))]
    return {"emprise": emprise, "pays": pays}


def en_json(donnees: dict) -> str:
    return json.dumps(donnees, ensure_ascii=False, default=lambda v: v.isoformat() if hasattr(v, "isoformat") else float(v))


GABARIT = Path(__file__).parent / "web" / "templates" / "observatoire.html"


def fragment(donnees: dict) -> str:
    """Le contenu de la page, données incluses (sans enveloppe HTML)."""
    return GABARIT.read_text(encoding="utf-8").replace(
        "/*DONNEES*/null", en_json(donnees).replace("</", "<\\/"))


def page_autonome(donnees: dict) -> str:
    """Page complète, données incluses : s'ouvre sans serveur, dans n'importe quel navigateur."""
    return ('<!doctype html><html lang="fr"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"></head><body>'
            + fragment(donnees) + "</body></html>")
