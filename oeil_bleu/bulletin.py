"""Étape 5 : validation par le directeur, puis bulletin envoyé par courriel.

Le bulletin ne contient que des publications validées par un humain.
Chaque destinataire reçoit son propre message : aucune adresse n'est exposée.
"""

import csv
import html
import os
import smtplib
import sys
from dataclasses import dataclass
from datetime import date
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

import psycopg

from .db import RACINE

DESTINATAIRES = RACINE / "data" / "destinataires.csv"
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre"]
NIVEAUX = {"eleve": "Confiance élevée", "moyen": "Confiance moyenne", "faible": "Confiance faible"}
TYPES = {"route": "route", "pont": "pont", "village": "village", "centre_sante": "centre de santé",
         "piste": "piste", "barrage": "barrage"}


# --- Validation par le directeur -------------------------------------------------

def a_valider(conn) -> list[tuple]:
    return conn.execute(
        "SELECT p.id, p.titre, p.contenu, e.confiance,"
        " (SELECT string_agg(v.validateur || ' : ' || v.decision, ', ' ORDER BY v.id)"
        "  FROM terre.validation v WHERE v.publication_id = p.id)"
        " FROM terre.publication p LEFT JOIN terre.evenement e ON e.id = p.evenement_id"
        " WHERE p.statut = 'en_validation' ORDER BY p.id"
    ).fetchall()


def decider(conn, publication_id: int, directeur: str, approuve: bool, motif: str | None = None) -> None:
    with conn.transaction():
        statut = conn.execute("SELECT statut FROM terre.publication WHERE id = %s FOR UPDATE",
                              (publication_id,)).fetchone()
        if statut is None:
            raise ValueError(f"publication {publication_id} inconnue")
        if statut[0] != "en_validation":
            raise ValueError(f"publication {publication_id} en statut « {statut[0]} », pas en validation")
        conn.execute(
            "INSERT INTO terre.validation (publication_id, validateur, role, decision, commentaire)"
            " VALUES (%s, %s, 'humain', %s, %s)",
            (publication_id, directeur, "approuve" if approuve else "rejete", motif),
        )
        conn.execute("UPDATE terre.publication SET statut = %s WHERE id = %s",
                     ("validee" if approuve else "retiree", publication_id))


# --- Composition ----------------------------------------------------------------

@dataclass
class Entree:
    publication_id: int
    titre: str
    contenu: str
    lieu: str
    type_point: str
    niveau: str
    carte: bytes | None = None
    cid: str | None = None


def entrees_pretes(conn) -> list[Entree]:
    lignes = conn.execute(
        "SELECT p.id, p.titre, p.contenu, i.nom, i.type, a.niveau"
        " FROM terre.publication p"
        " JOIN terre.anomalie a ON a.evenement_id = p.evenement_id"
        " JOIN terre.infrastructure i ON i.id = a.infrastructure_id"
        " WHERE p.statut = 'validee'"
        " AND NOT EXISTS (SELECT 1 FROM terre.bulletin_publication b WHERE b.publication_id = p.id)"
        " ORDER BY a.confiance DESC, p.id"
    ).fetchall()
    return [Entree(*l) for l in lignes]


def date_longue(jour: date) -> str:
    return f"{jour.day} {MOIS[jour.month - 1]} {jour.year}"


def composer_html(entrees: list[Entree], jour: date) -> str:
    e = html.escape
    blocs = []
    for n in entrees:
        paragraphes = "".join(f'<p style="margin:0 0 10px">{e(p)}</p>'
                              for p in n.contenu.split("\n") if p.strip())
        image = (f'<img src="cid:{n.cid[1:-1]}" alt="Carte avant et après : {e(n.lieu)}" width="600" '
                 'style="display:block;width:100%;max-width:600px;height:auto;margin:12px 0;'
                 'border:1px solid #e4e3dc">') if n.cid else ""
        blocs.append(
            '<tr><td style="padding:20px 24px;border-top:1px solid #e4e3dc">'
            f'<p style="margin:0 0 4px;font-size:13px;color:#5f5e58">{e(n.lieu)} · {e(TYPES.get(n.type_point, n.type_point))}'
            f' · <strong>{e(NIVEAUX[n.niveau])}</strong></p>'
            f'<h2 style="margin:0 0 10px;font-size:19px;line-height:1.3;color:#1f1f1e">{e(n.titre)}</h2>'
            f'{image}<div style="font-size:15px;line-height:1.55;color:#1f1f1e">{paragraphes}</div></td></tr>'
        )
    resume = (f"{len(entrees)} alerte{'s' if len(entrees) > 1 else ''} validée{'s' if len(entrees) > 1 else ''}"
              if entrees else "Aucune crue inhabituelle confirmée sur les points surveillés.")
    return (
        '<!doctype html><html lang="fr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>Œil Bleu — {date_longue(jour)}</title></head>'
        '<body style="margin:0;padding:0;background:#f1f0eb;font-family:Helvetica,Arial,sans-serif">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f1f0eb">'
        '<tr><td align="center" style="padding:16px 8px">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="max-width:648px;background:#fcfcfb;border:1px solid #e4e3dc">'
        '<tr><td style="padding:24px 24px 16px">'
        '<p style="margin:0;font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:#2a78d6">'
        '<strong>Œil Bleu</strong> · Bulletin des crues</p>'
        f'<h1 style="margin:6px 0 4px;font-size:24px;color:#1f1f1e">Bassins du Sénégal et du Niger</h1>'
        f'<p style="margin:0;font-size:14px;color:#5f5e58">{date_longue(jour)} · {e(resume)}</p></td></tr>'
        + "".join(blocs) +
        '<tr><td style="padding:16px 24px 24px;border-top:1px solid #e4e3dc;font-size:12px;line-height:1.5;color:#5f5e58">'
        'Chaque alerte est détectée par satellite, vérifiée par des agents d\'intelligence artificielle, '
        'puis validée par le directeur de publication. Une confiance « moyenne » ou « faible » appelle '
        'une vérification sur le terrain avant toute décision.<br>'
        'Bulletin pilote, diffusion restreinte. Pour ne plus le recevoir, répondez « désinscription ».'
        '</td></tr></table></td></tr></table></body></html>'
    )


def composer_texte(entrees: list[Entree], jour: date) -> str:
    lignes = [f"ŒIL BLEU — Bulletin des crues — {date_longue(jour)}", ""]
    if not entrees:
        lignes.append("Aucune crue inhabituelle confirmée sur les points surveillés.")
    for n in entrees:
        lignes += ["-" * 60, f"{n.lieu} · {TYPES.get(n.type_point, n.type_point)} · {NIVEAUX[n.niveau]}",
                   n.titre.upper(), "", n.contenu, ""]
    lignes += ["-" * 60, "Bulletin pilote, diffusion restreinte. Pour ne plus le recevoir, répondez « désinscription »."]
    return "\n".join(lignes)


def ajouter_cartes(conn, entrees: list[Entree], fabrique=None) -> None:
    """Ajoute la carte de chaque entrée ; une carte impossible n'empêche pas le bulletin."""
    if fabrique is None:
        from .carte import carte_publication as fabrique
    for n in entrees:
        try:
            n.carte = fabrique(conn, n.publication_id)
        except Exception as err:  # images inaccessibles, réseau…
            print(f"Carte de la publication {n.publication_id} indisponible : {err}", file=sys.stderr)
            n.carte = None
        n.cid = make_msgid(domain="oeil-bleu") if n.carte else None


def message(entrees: list[Entree], jour: date, expediteur: str, destinataire: str) -> EmailMessage:
    m = EmailMessage()
    m["Subject"] = (f"Œil Bleu — {date_longue(jour)} — {len(entrees)} alerte(s)" if entrees
                    else f"Œil Bleu — {date_longue(jour)} — rien à signaler")
    m["From"] = expediteur
    m["To"] = destinataire
    m.set_content(composer_texte(entrees, jour))
    m.add_alternative(composer_html(entrees, jour), subtype="html")
    partie_html = m.get_payload()[1]
    for n in entrees:
        if n.carte:
            partie_html.add_related(n.carte, "image", "png", cid=n.cid,
                                    filename=f"carte-{n.publication_id}.png")
    return m


# --- Envoi ----------------------------------------------------------------------

def lire_destinataires(chemin: Path = DESTINATAIRES) -> list[str]:
    if not chemin.exists():
        raise FileNotFoundError(f"{chemin} introuvable (modèle : data/destinataires.exemple.csv)")
    with open(chemin, newline="", encoding="utf-8") as f:
        adresses = [l["courriel"].strip() for l in csv.DictReader(f) if (l.get("courriel") or "").strip()]
    if not adresses:
        raise ValueError(f"aucun destinataire dans {chemin}")
    return adresses


def smtp_depuis_env():
    hote = os.environ.get("SMTP_HOTE")
    if not hote:
        raise RuntimeError("SMTP_HOTE n'est pas défini (voir .env.example)")
    serveur = smtplib.SMTP(hote, int(os.environ.get("SMTP_PORT", "587")), timeout=60)
    serveur.starttls()
    if os.environ.get("SMTP_UTILISATEUR"):
        serveur.login(os.environ["SMTP_UTILISATEUR"], os.environ["SMTP_MOT_DE_PASSE"])
    return serveur


def envoyer(conn, jour: date, destinataires: list[str], serveur, expediteur: str,
            entrees: list[Entree] | None = None, meme_vide: bool = False) -> tuple[int, int, list[str]]:
    """Envoie le bulletin ; renvoie (id du bulletin ou 0, nombre d'entrées, adresses en échec)."""
    if entrees is None:
        entrees = entrees_pretes(conn)
        ajouter_cartes(conn, entrees)
    if not entrees and not meme_vide:
        return 0, 0, []
    echecs = []
    for adresse in destinataires:
        try:
            serveur.send_message(message(entrees, jour, expediteur, adresse))
        except smtplib.SMTPException as err:
            echecs.append(f"{adresse} ({err})")
    if len(echecs) == len(destinataires):
        raise RuntimeError("aucun envoi réussi : " + "; ".join(echecs))
    with conn.transaction():
        bulletin_id = conn.execute(
            "INSERT INTO terre.bulletin (jour, html, nb_destinataires, nb_echecs)"
            " VALUES (%s, %s, %s, %s) RETURNING id",
            (jour, composer_html(entrees, jour), len(destinataires) - len(echecs), len(echecs)),
        ).fetchone()[0]
        for n in entrees:
            conn.execute("INSERT INTO terre.bulletin_publication VALUES (%s, %s)", (bulletin_id, n.publication_id))
            conn.execute("UPDATE terre.publication SET statut = 'publiee', publie_le = now() WHERE id = %s",
                         (n.publication_id,))
    return bulletin_id, len(entrees), echecs


def apercu(conn, jour: date, chemin: Path) -> int:
    """Écrit le bulletin en HTML local, cartes comprises, sans rien envoyer ni modifier."""
    import base64

    entrees = entrees_pretes(conn)
    ajouter_cartes(conn, entrees)
    page = composer_html(entrees, jour)
    for n in entrees:
        if n.carte:
            page = page.replace(f"cid:{n.cid[1:-1]}",
                                "data:image/png;base64," + base64.b64encode(n.carte).decode())
    chemin.write_text(page, encoding="utf-8")
    return len(entrees)
