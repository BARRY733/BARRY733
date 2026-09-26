"""Construit, à partir du Modèle Terre, le dossier transmis aux agents.

Les agents ne lisent pas la base : ils reçoivent un dossier fermé. Ce qui
n'y figure pas n'existe pas pour eux, ce qui borne leurs affirmations.
"""

from datetime import datetime


def _iso(v):
    return v.isoformat() if isinstance(v, datetime) else v


def construire(cur, evenement_id: int) -> dict:
    cur.execute(
        """
        SELECT e.id, e.type, e.statut, e.debut, e.fin, e.confiance,
               i.id, i.nom, i.type, i.pays_iso, ST_Y(i.geom), ST_X(i.geom), im.nature
        FROM terre.evenement e
        JOIN terre.impact im ON im.evenement_id = e.id
        JOIN terre.infrastructure i ON i.id = im.infrastructure_id
        WHERE e.id = %s
        """,
        (evenement_id,),
    )
    ligne = cur.fetchone()
    if ligne is None:
        raise LookupError(f"événement {evenement_id} introuvable ou sans point rattaché")
    (eid, type_, statut, debut, fin, confiance, pid, nom, ptype, pays, lat, lon, nature) = ligne

    cur.execute(
        """
        SELECT nom, valeur, unite, mesure_le, preuve_id FROM terre.indicateur
        WHERE infrastructure_id = %s AND (evenement_id = %s OR nom = 'frequence_eau_historique'
              OR mesure_le >= %s - interval '32 days')
        ORDER BY mesure_le
        """,
        (pid, eid, debut),
    )
    mesures = [{"mesure": n, "valeur": v, "unite": u, "date": _iso(d), "preuve": p}
               for n, v, u, d, p in cur.fetchall()]

    cur.execute(
        """
        SELECT e.id, e.type, s.nom, e.ref_externe, e.debut, e.fin, e.gravite,
               round((ST_Distance(e.geom::geography, i.geom::geography) / 1000)::numeric)
        FROM terre.evenement e
        JOIN terre.source s ON s.id = e.source_id
        JOIN terre.infrastructure i ON i.id = %s
        WHERE e.id <> %s AND e.ref_externe NOT LIKE 'detection-%%'
          AND ST_DWithin(e.geom::geography, i.geom::geography, 100000)
          AND e.debut <= coalesce(%s, now()) AND coalesce(e.fin, 'infinity') >= %s - interval '15 days'
        """,
        (pid, eid, fin, debut),
    )
    signaux = [{"evenement": r[0], "type": r[1], "source": r[2], "reference": r[3], "debut": _iso(r[4]),
                "fin": _iso(r[5]), "gravite_source": r[6], "distance_km": float(r[7])} for r in cur.fetchall()]

    ids = sorted({m["preuve"] for m in mesures})
    cur.execute(
        """
        SELECT DISTINCT p.id, s.nom, s.mention_obligatoire, p.acquise_le, p.traitement
        FROM terre.preuve p JOIN terre.source s ON s.id = p.source_id
        WHERE p.id = ANY(%s)
           OR p.id IN (SELECT preuve_id FROM terre.evenement_preuve WHERE evenement_id = ANY(%s))
        ORDER BY p.id
        """,
        (ids, [eid] + [s["evenement"] for s in signaux]),
    )
    preuves = [{"id": r[0], "source": r[1], "mention": r[2], "acquise_le": _iso(r[3]), "traitement": r[4]}
               for r in cur.fetchall()]

    return {
        "anomalie": {"evenement": eid, "type": type_, "statut": statut, "debut": _iso(debut),
                     "fin": _iso(fin), "confiance_detection": confiance, "impact_detecte": nature},
        "point": {"id": pid, "nom": nom, "type": ptype, "pays_iso": pays, "latitude": lat, "longitude": lon},
        "mesures": mesures,
        "signaux_proches": signaux,
        "preuves": preuves,
    }
