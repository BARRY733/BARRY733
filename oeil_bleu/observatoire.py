"""Observatoire : l'état de chaque site surveillé, tiré de la base et de rien d'autre.

Produit un instantané JSON (objets, mesures, séries, relations, journal, sources,
fond de carte) que l'espace de travail affiche. Le même instantané sert à exporter
une version autonome.
"""

import csv
import io
import json
from datetime import date, datetime, timezone
from pathlib import Path

import psycopg

from . import detection
from .detection import FREQ_RARE, REFERENCE_JOURS, frequence_reference

EAU_PERMANENTE = 0.5    # au-delà, le point est presque toujours en eau : il est mal placé
FOND = Path(__file__).parent / "web" / "fond"
GABARIT = Path(__file__).parent / "web" / "templates" / "observatoire.html"


def _etat(freq, anomalie) -> tuple[str, str]:
    if anomalie:
        return "alerte", f"Anomalie « {anomalie['type'].replace('_', ' ')} », confiance {anomalie['niveau']}"
    if freq is None:
        return "incomplet", "Pas encore de fréquence historique : le site n'est pas évalué"
    if freq >= EAU_PERMANENTE:
        return "eau_permanente", "Eau presque toujours présente : aucune crue n'y est détectable, point à déplacer"
    if freq >= FREQ_RARE:
        return "surveille", "Eau fréquente : seules les crues marquées ressortiront"
    return "surveille", "Sous surveillance : aucune eau inhabituelle"


def _lignes(conn, sql, params=()):
    cur = conn.execute(sql, params)
    noms = [c.name for c in cur.description]
    return [dict(zip(noms, r)) for r in cur.fetchall()]


def _site(conn, s, jour, debut, toutes_anomalies):
    iid = s["id"]
    freq, reference = frequence_reference(conn, iid, jour, debut)
    freq = freq[1] if freq else None
    s["zones"] = _lignes(conn,
        "SELECT z.code, z.nom, z.type FROM terre.zone z, terre.infrastructure i WHERE i.id = %s"
        " AND ST_DWithin(z.geom::geography, i.geom::geography, 5000)"
        " ORDER BY array_position(ARRAY['continent','bassin','pays','commune','zone_pilote'], z.type)", (iid,))
    s["pays"] = next((z["nom"] for z in s["zones"] if z["type"] == "pays"), "—")
    s["annuel"] = _lignes(conn,
        "SELECT (brut->>'annee')::int AS annee, valeur AS eau, (brut->>'passages_degages')::float AS degages"
        " FROM terre.observation WHERE infrastructure_id = %s AND variable = 'eau_annuelle' ORDER BY 1", (iid,))
    s["radar"] = _lignes(conn,
        "SELECT observe_le::date AS date, valeur AS db FROM terre.observation WHERE infrastructure_id = %s"
        " AND variable = 'retrodiffusion_vv' AND observe_le::date <= %s ORDER BY 1", (iid, jour))
    s["optique"] = _lignes(conn,
        "SELECT observe_le::date AS date, valeur::int AS eau, coalesce(brut->>'capteur', 'landsat') AS capteur"
        " FROM terre.observation WHERE infrastructure_id = %s AND variable = 'eau_observee'"
        " AND observe_le::date <= %s ORDER BY 1", (iid, jour))
    ref = conn.execute(
        "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY valeur), count(*) FROM terre.observation"
        " WHERE infrastructure_id = %s AND variable = 'retrodiffusion_vv'"
        " AND observe_le >= %s::date - %s AND observe_le < %s::date - 16",
        (iid, jour, REFERENCE_JOURS, jour)).fetchone()
    s["radar_reference"] = ref[0] if ref and ref[1] >= 5 else None
    gsw = conn.execute(
        "SELECT valeur FROM terre.observation WHERE infrastructure_id = %s"
        " AND variable = 'frequence_eau_historique' AND brut->>'produit' = 'gsw_occurrence' LIMIT 1", (iid,)).fetchone()
    s["gsw"] = gsw[0] if gsw else None
    s["par_source"] = _lignes(conn,
        "SELECT so.code, so.nom, count(*) AS nb, max(o.observe_le)::date AS derniere FROM terre.observation o"
        " JOIN terre.source so ON so.id = o.source_id WHERE o.infrastructure_id = %s GROUP BY 1, 2 ORDER BY 3 DESC", (iid,))
    anomalies = [a for a in toutes_anomalies if a["site_id"] == iid]
    ouverte = next((a for a in anomalies if a["jour"] <= jour and a["statut"] != "ecartee"), None)
    s["frequence"], s["reference"] = freq, reference
    s["etat"], s["explication"] = _etat(freq, ouverte)
    s["anomalie"] = ouverte
    return s


def instantane(conn: psycopg.Connection, jour: date | None = None) -> dict:
    jour = jour or date.today()
    debut = datetime.combine(jour, datetime.min.time(), tzinfo=timezone.utc)

    anomalies = _lignes(conn,
        "SELECT a.id, a.infrastructure_id AS site_id, i.nom AS site, a.jour, a.type, a.niveau, a.confiance,"
        " a.statut, a.elements - 'observations' AS elements, a.evenement_id"
        " FROM terre.anomalie a JOIN terre.infrastructure i ON i.id = a.infrastructure_id"
        " WHERE a.jour <= %s ORDER BY a.jour DESC, a.confiance DESC LIMIT 500", (jour,))
    sites = [_site(conn, s, jour, debut, anomalies) for s in _lignes(conn,
        "SELECT id, nom, type, ST_X(ST_PointOnSurface(geom)) AS lon, ST_Y(ST_PointOnSurface(geom)) AS lat,"
        " notes, cree_le::date AS ajoute_le FROM terre.infrastructure WHERE surveille ORDER BY nom")]

    publications = _lignes(conn,
        "SELECT p.id, p.titre, p.type, p.niveau, p.statut, p.cree_le, p.publie_le, a.infrastructure_id AS site_id,"
        " (SELECT count(*) FROM terre.publication_preuve pp WHERE pp.publication_id = p.id) AS preuves"
        " FROM terre.publication p LEFT JOIN terre.anomalie a ON a.evenement_id = p.evenement_id"
        " ORDER BY p.cree_le DESC LIMIT 200")
    validations = _lignes(conn,
        "SELECT v.publication_id, v.validateur, v.role, v.decision, v.commentaire, v.valide_le"
        " FROM terre.validation v ORDER BY v.valide_le DESC LIMIT 500")

    journal = _lignes(conn, """
        SELECT * FROM (
          SELECT c.debut AS quand, 'Collecte' AS nature,
                 s.nom || CASE c.statut WHEN 'reussie' THEN ' : ' || coalesce(c.nb_nouvelles, 0) || ' observation(s) nouvelle(s)'
                                        WHEN 'echouee' THEN ' : échec' ELSE ' : en cours' END AS titre,
                 left(coalesce(c.erreur, ''), 160) AS detail, c.statut AS etat
          FROM terre.collecte c JOIN terre.source s ON s.id = c.source_id
          UNION ALL
          SELECT i.cree_le, 'Site', 'Site ajouté : ' || i.nom, coalesce(i.notes, ''), 'info'
          FROM terre.infrastructure i WHERE i.surveille
          UNION ALL
          SELECT a.detecte_le, 'Anomalie', i.nom || ' : ' || replace(a.type, '_', ' ') || ', confiance ' || a.niveau,
                 'Statut : ' || a.statut, a.niveau
          FROM terre.anomalie a JOIN terre.infrastructure i ON i.id = a.infrastructure_id
          UNION ALL
          SELECT pa.cree_le, 'Agent', initcap(pa.agent) || ' · ' || i.nom, CASE WHEN pa.refus THEN 'Refus du modèle' ELSE '' END, 'info'
          FROM terre.passage_agent pa JOIN terre.anomalie a ON a.id = pa.anomalie_id
          JOIN terre.infrastructure i ON i.id = a.infrastructure_id
          UNION ALL
          SELECT v.valide_le, 'Décision', v.validateur || ' : ' || v.decision || ' · ' || p.titre, coalesce(v.commentaire, ''), v.decision
          FROM terre.validation v JOIN terre.publication p ON p.id = v.publication_id WHERE v.role = 'humain'
          UNION ALL
          SELECT b.envoye_le, 'Bulletin', 'Bulletin envoyé à ' || b.nb_destinataires || ' destinataire(s)',
                 CASE WHEN b.nb_echecs > 0 THEN b.nb_echecs || ' échec(s)' ELSE '' END, 'info'
          FROM terre.bulletin b
        ) j ORDER BY quand DESC LIMIT 300""")

    sources = _lignes(conn,
        "SELECT s.code, s.nom, s.type, s.url, s.licence, s.licence_verifiee, s.attribution,"
        " c.debut AS derniere, c.statut, c.erreur,"
        " (SELECT count(*) FROM terre.observation o WHERE o.source_id = s.id) AS nb,"
        " (SELECT count(*) FROM terre.collecte c2 WHERE c2.source_id = s.id AND c2.statut = 'reussie') AS reussies,"
        " (SELECT count(*) FROM terre.collecte c2 WHERE c2.source_id = s.id AND c2.statut = 'echouee') AS echouees"
        " FROM terre.source s LEFT JOIN LATERAL (SELECT debut, statut, erreur FROM terre.collecte"
        "   WHERE source_id = s.id ORDER BY debut DESC LIMIT 1) c ON true ORDER BY s.nom")

    return {
        "jour": jour, "genere_le": datetime.now(timezone.utc), "sites": sites, "anomalies": anomalies,
        "publications": publications, "validations": validations, "journal": journal, "sources": sources,
        "compteurs": {
            "a_valider": sum(1 for p in publications if p["statut"] == "en_validation"),
            "publiees": sum(1 for p in publications if p["statut"] == "publiee"),
        },
        "seuils": {"freq_rare": FREQ_RARE, "freq_tres_rare": detection.FREQ_TRES_RARE,
                   "eau_permanente": EAU_PERMANENTE, "vv_db": detection.SEUIL_VV_DB,
                   "baisse_vv_db": detection.BAISSE_VV_DB, "fenetre_jours": detection.FENETRE_JOURS},
        "fond": fond_de_carte(conn),
    }


def fond_de_carte(conn) -> dict:
    """Pays du monde (Natural Earth 1:50 m, simplifiés), fleuves et lacs d'Afrique."""
    # L'Afrique au 1:50 m finement simplifié ; le reste du monde plus grossièrement, pour la vue Monde.
    pays = [{"nom": n, "code": c, "afrique": a, "geo": json.loads(g)} for n, c, a, g in conn.execute(
        "SELECT p.nom, p.code, c.code = 'afrique',"
        " CASE WHEN c.code = 'afrique' THEN ST_AsGeoJSON(ST_SimplifyPreserveTopology(p.geom, 0.03), 3)"
        "      ELSE ST_AsGeoJSON(ST_SimplifyPreserveTopology(p.geom, 0.15), 1) END"
        " FROM terre.zone p JOIN terre.zone c ON c.id = p.parent_id WHERE p.type = 'pays'")]
    lire = lambda f: json.loads((FOND / f).read_text(encoding="utf-8")) if (FOND / f).exists() else None
    return {"pays": pays, "rivieres": lire("rivieres_afrique.geojson"), "lacs": lire("lacs_afrique.geojson")}


def en_json(donnees: dict) -> str:
    return json.dumps(donnees, ensure_ascii=False, separators=(",", ":"),
                      default=lambda v: v.isoformat() if hasattr(v, "isoformat") else float(v))


def fragment(donnees: dict, en_ligne: bool = False) -> str:
    """Le contenu de l'espace de travail, données incluses (sans enveloppe HTML).
    en_ligne : servi par l'application, avec import et export actifs."""
    return (GABARIT.read_text(encoding="utf-8")
            .replace("/*DONNEES*/null", en_json(donnees).replace("</", "<\\/"))
            .replace("/*EN_LIGNE*/false", "true" if en_ligne else "false"))


def page_autonome(donnees: dict, en_ligne: bool = False, jeton: str = "") -> str:
    """Page complète : s'ouvre sans serveur, ou servie par l'application."""
    return ('<!doctype html><html lang="fr"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="jeton" content="{jeton}"></head><body>'
            + fragment(donnees, en_ligne) + "</body></html>")


def csv_sites(donnees: dict) -> str:
    """Tableau des sites, relisible par un tableur (et réimportable : mêmes premières colonnes)."""
    sortie = io.StringIO()
    w = csv.writer(sortie)
    w.writerow(["nom", "type", "latitude", "longitude", "notes", "pays", "etat", "eau_habituelle",
                "reference", "gsw", "derniere_eau", "radar_dernier_db"])
    for s in donnees["sites"]:
        eau = [o for o in s["optique"] if o["eau"] == 1]
        w.writerow([s["nom"], s["type"], f"{s['lat']:.5f}", f"{s['lon']:.5f}", s["notes"] or "", s["pays"], s["etat"],
                    "" if s["frequence"] is None else f"{s['frequence']:.3f}", s["reference"] or "",
                    "" if s["gsw"] is None else f"{s['gsw']:.3f}", eau[-1]["date"] if eau else "",
                    f"{s['radar'][-1]['db']:.2f}" if s["radar"] else ""])
    return sortie.getvalue()
