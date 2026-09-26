"""Étape 5 : le bulletin.

Circuit : les textes soumis par l'Agent Conformité attendent le directeur ;
il les valide ou les rejette ; le bulletin réunit les textes validés et pas
encore diffusés, avec leur carte, et part par courriel. Les publications ne
passent au statut « publie » que si l'envoi réussit.
"""

import html
import os
import smtplib
from dataclasses import dataclass
from datetime import date
from email.message import EmailMessage
from email.utils import make_msgid

from .carte import carte_evenement

LIBELLE_CONFIANCE = {"eleve": "Confiance élevée", "moyen": "Confiance moyenne", "a_confirmer": "À confirmer"}
COULEUR_CONFIANCE = {"eleve": "#1e7b4a", "moyen": "#b7791f", "a_confirmer": "#7a7a7a"}


# Décisions du directeur -------------------------------------------------------

def a_valider(cur) -> list[tuple]:
    cur.execute("SELECT id, titre, confiance, cree_le FROM terre.publication "
                "WHERE statut = 'soumis' ORDER BY cree_le")
    return cur.fetchall()


def decider(cur, publication_id: int, valide: bool, commentaire: str | None = None) -> None:
    cur.execute("SELECT statut FROM terre.publication WHERE id = %s FOR UPDATE", (publication_id,))
    ligne = cur.fetchone()
    if ligne is None or ligne[0] != "soumis":
        raise ValueError(f"publication {publication_id} : rien à décider (statut {ligne[0] if ligne else 'inconnu'})")
    cur.execute(
        "INSERT INTO terre.validation (publication_id, validateur, decision, commentaire) "
        "VALUES (%s, 'directeur', %s, %s)",
        (publication_id, "valide" if valide else "rejete", commentaire),
    )
    cur.execute("UPDATE terre.publication SET statut = %s WHERE id = %s",
                ("valide" if valide else "brouillon", publication_id))


# Composition -----------------------------------------------------------------

@dataclass
class Article:
    publication_id: int
    titre: str
    contenu: str
    confiance: str
    mention_ia: str
    sources: list[str]
    carte: bytes | None
    cid: str | None = None


def articles_prets(cur) -> list[Article]:
    cur.execute(
        """
        SELECT p.id, p.titre, p.contenu, p.confiance, p.mention_ia,
               array_agg(DISTINCT s.mention_obligatoire ORDER BY s.mention_obligatoire), p.evenement_id
        FROM terre.publication p
        JOIN terre.publication_preuve pp ON pp.publication_id = p.id
        JOIN terre.preuve pr ON pr.id = pp.preuve_id
        JOIN terre.source s ON s.id = pr.source_id
        WHERE p.statut = 'valide'
        GROUP BY p.id ORDER BY p.cree_le
        """
    )
    articles = []
    for pid, titre, contenu, confiance, mention_ia, sources, evenement_id in cur.fetchall():
        carte = carte_evenement(cur, evenement_id) if evenement_id else None
        articles.append(Article(pid, titre, contenu, confiance, mention_ia, list(sources), carte))
    return articles


def gabarit(articles: list[Article], jour: date) -> str:
    e = html.escape
    blocs = []
    for a in articles:
        image = (f'<img src="cid:{a.cid[1:-1]}" alt="Carte avant et après" width="560" '
                 f'style="display:block;max-width:100%;margin:12px 0;border:1px solid #ddd">') if a.cid \
            else (f"<!--carte-{a.publication_id}-->" if a.carte else "")
        paragraphes = "".join(f"<p style='margin:0 0 10px'>{e(p)}</p>" for p in a.contenu.split("\n") if p.strip())
        blocs.append(f"""
<tr><td style="padding:20px 24px;border-bottom:1px solid #e5e5e5">
  <span style="display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;color:#fff;
    background:{COULEUR_CONFIANCE[a.confiance]}">{LIBELLE_CONFIANCE[a.confiance]}</span>
  <h2 style="font-size:19px;margin:10px 0 8px;color:#10233f">{e(a.titre)}</h2>
  {paragraphes}{image}
  <p style="font-size:12px;color:#666;margin:6px 0 0">Sources : {e(" · ".join(a.sources))}<br>{e(a.mention_ia)}</p>
</td></tr>""")
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>Œil Bleu, bulletin du {jour:%d/%m/%Y}</title></head>
<body style="margin:0;background:#f3f5f8;font-family:Arial,Helvetica,sans-serif;color:#222;line-height:1.5">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:16px">
<table role="presentation" width="620" cellpadding="0" cellspacing="0" style="max-width:620px;background:#fff">
<tr><td style="background:#10233f;color:#fff;padding:18px 24px">
  <div style="font-size:22px;font-weight:bold">Œil Bleu</div>
  <div style="font-size:13px;opacity:.85">Bulletin du {jour:%d/%m/%Y} · crues sur les points surveillés</div>
</td></tr>
{"".join(blocs)}
<tr><td style="padding:16px 24px;font-size:12px;color:#666;background:#fafafa">
  Chaque information repose sur des images satellites et des données publiques, citées ci-dessus.
  Les niveaux de confiance sont explicites ; toute erreur fait l'objet d'une correction datée et visible.
  Textes préparés avec l'aide de l'IA et validés par le directeur de publication.
</td></tr>
</table></td></tr></table></body></html>"""


def texte_brut(articles: list[Article], jour: date) -> str:
    parties = [f"Œil Bleu, bulletin du {jour:%d/%m/%Y}\n"]
    for a in articles:
        parties.append(f"[{LIBELLE_CONFIANCE[a.confiance]}] {a.titre}\n\n{a.contenu}\n\n"
                       f"Sources : {' · '.join(a.sources)}\n{a.mention_ia}\n")
    return "\n---\n\n".join(parties)


def composer(articles: list[Article], jour: date, expediteur: str, destinataires: list[str]) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = f"Œil Bleu, bulletin du {jour:%d/%m/%Y}"
    msg["From"] = expediteur
    msg["To"] = expediteur
    msg["Bcc"] = ", ".join(destinataires)  # les destinataires ne se voient pas entre eux
    for a in articles:
        a.cid = make_msgid(domain="oeilbleu") if a.carte else None
    msg.set_content(texte_brut(articles, jour))
    msg.add_alternative(gabarit(articles, jour), subtype="html")
    partie_html = msg.get_payload()[1]
    for a in articles:
        if a.carte:
            partie_html.add_related(a.carte, maintype="image", subtype="png", cid=a.cid)
    return msg


# Envoi -----------------------------------------------------------------------

def destinataires() -> list[str]:
    return [d.strip() for d in os.environ.get("BULLETIN_DESTINATAIRES", "").split(",") if d.strip()]


def smtp_envoyer(msg: EmailMessage) -> None:
    with smtplib.SMTP(os.environ["SMTP_HOTE"], int(os.environ.get("SMTP_PORT", "587")), timeout=60) as s:
        s.starttls()
        if os.environ.get("SMTP_UTILISATEUR"):
            s.login(os.environ["SMTP_UTILISATEUR"], os.environ["SMTP_MOT_DE_PASSE"])
        s.send_message(msg)


def envoyer(cur, jour: date | None = None, envoi=smtp_envoyer) -> int | None:
    """Diffuse le bulletin. À appeler dans une transaction : si l'envoi échoue,
    l'exception annule le passage au statut « publie »."""
    jour = jour or date.today()
    articles = articles_prets(cur)
    if not articles:
        return None
    liste = destinataires()
    if not liste:
        raise RuntimeError("BULLETIN_DESTINATAIRES vide")
    msg = composer(articles, jour, os.environ.get("BULLETIN_EXPEDITEUR", "bulletin@oeilbleu.org"), liste)
    # Les règles de la base (preuve, validation du directeur) s'appliquent ici.
    for a in articles:
        cur.execute("UPDATE terre.publication SET statut = 'publie' WHERE id = %s", (a.publication_id,))
    cur.execute("INSERT INTO terre.bulletin (sujet, destinataires) VALUES (%s, %s) RETURNING id",
                (msg["Subject"], len(liste)))
    bulletin_id = cur.fetchone()[0]
    for a in articles:
        cur.execute("INSERT INTO terre.bulletin_publication VALUES (%s, %s)", (bulletin_id, a.publication_id))
    envoi(msg)  # en dernier : rien n'est marqué diffusé si le courriel ne part pas
    return bulletin_id
