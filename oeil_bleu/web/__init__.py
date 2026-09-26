"""Page de validation du directeur de publication.

Lancement : python -m oeil_bleu web  (http://127.0.0.1:8000)
Protégée par mot de passe (DIRECTEUR_MOT_DE_PASSE) et par un jeton contre la
falsification de formulaires. N'écoute que la machine locale par défaut.
"""

import hmac
import os
import secrets
from datetime import date
from functools import wraps

import psycopg
from flask import Flask, Response, abort, flash, g, redirect, render_template, request, session, url_for

from .. import bulletin, db

NIVEAUX = {"eleve": "Confiance élevée", "moyen": "Confiance moyenne", "faible": "Confiance faible"}


def creer_app(url_base: str | None = None, mot_de_passe: str | None = None, directeur: str | None = None,
              smtp=None, fabrique_carte=None) -> Flask:
    """smtp et fabrique_carte se remplacent dans les tests ; par défaut, les vrais."""
    app = Flask(__name__)
    secret = os.environ.get("OEIL_BLEU_SECRET")
    if not secret and os.environ.get("OEIL_BLEU_HTTPS") == "1":
        raise RuntimeError("OEIL_BLEU_SECRET doit être défini en production (voir .env.example)")
    app.secret_key = secret or secrets.token_hex(32)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
                      # Derrière le proxy HTTPS, le cookie de session ne circule jamais en clair.
                      SESSION_COOKIE_SECURE=os.environ.get("OEIL_BLEU_HTTPS") == "1")
    mot_de_passe = mot_de_passe or os.environ.get("DIRECTEUR_MOT_DE_PASSE")
    directeur = directeur or os.environ.get("DIRECTEUR_NOM")
    if not mot_de_passe or not directeur:
        raise RuntimeError("DIRECTEUR_MOT_DE_PASSE et DIRECTEUR_NOM doivent être définis (voir .env.example)")
    cartes: dict[int, bytes | None] = {}

    def conn() -> psycopg.Connection:
        if "conn" not in g:
            g.conn = db.connecter(url_base)
            g.conn.autocommit = True
        return g.conn

    @app.teardown_appcontext
    def fermer(_):
        c = g.pop("conn", None)
        if c is not None:
            c.close()

    def protege(vue):
        @wraps(vue)
        def enveloppe(*a, **kw):
            auth = request.authorization
            if not (auth and auth.password and hmac.compare_digest(auth.password.encode(), mot_de_passe.encode())):
                return Response("Accès réservé au directeur de publication.", 401,
                                {"WWW-Authenticate": 'Basic realm="Oeil Bleu", charset="UTF-8"'})
            if request.method == "POST":
                jeton, attendu = request.form.get("jeton", ""), session.get("jeton", "")
                # Un jeton vide des deux côtés ne prouve rien : on exige un jeton émis par la page.
                if not attendu or not hmac.compare_digest(jeton, attendu):
                    abort(400, "Jeton de formulaire invalide : rechargez la page.")
            session.setdefault("jeton", secrets.token_hex(16))
            return vue(*a, **kw)
        return enveloppe

    @app.after_request
    def entetes(reponse):
        reponse.headers.setdefault("X-Frame-Options", "DENY")
        reponse.headers.setdefault("X-Content-Type-Options", "nosniff")
        reponse.headers.setdefault("Referrer-Policy", "no-referrer")
        return reponse

    @app.get("/")
    @protege
    def accueil():
        c = conn()
        attente = c.execute(
            "SELECT p.id, p.titre, p.contenu, i.nom, i.type, a.niveau, a.confiance, a.jour, a.elements"
            " FROM terre.publication p"
            " JOIN terre.anomalie a ON a.evenement_id = p.evenement_id"
            " JOIN terre.infrastructure i ON i.id = a.infrastructure_id"
            " WHERE p.statut = 'en_validation' ORDER BY a.confiance DESC, p.id"
        ).fetchall()
        avis = {}
        for pid, validateur, decision, commentaire in c.execute(
            "SELECT publication_id, validateur, decision, commentaire FROM terre.validation"
            " WHERE role = 'agent' AND publication_id = ANY(%s) ORDER BY id", ([r[0] for r in attente],)
        ):
            avis.setdefault(pid, []).append((validateur, decision, commentaire))
        pretes = bulletin.entrees_pretes(c)
        retraits = bulletin.retraits_a_annoncer(c)
        publiees = c.execute(
            "SELECT id, titre, publie_le FROM terre.publication WHERE statut = 'publiee'"
            " ORDER BY publie_le DESC LIMIT 10"
        ).fetchall()
        historique = c.execute(
            "SELECT jour, envoye_le, nb_destinataires, nb_echecs,"
            " (SELECT count(*) FROM terre.bulletin_publication bp WHERE bp.bulletin_id = b.id)"
            " FROM terre.bulletin b ORDER BY envoye_le DESC LIMIT 5"
        ).fetchall()
        return render_template("accueil.html", attente=attente, avis=avis, pretes=pretes,
                               historique=historique, retraits=retraits, publiees=publiees, niveaux=NIVEAUX, types=bulletin.TYPES,
                               directeur=directeur, jeton=session["jeton"], aujourdhui=date.today())

    @app.get("/observatoire")
    @protege
    def observatoire():
        from ..observatoire import instantane, page_autonome

        return Response(page_autonome(instantane(conn())), mimetype="text/html")

    @app.get("/carte/<int:pid>.png")
    @protege
    def carte(pid: int):
        if pid not in cartes:
            try:
                if fabrique_carte:
                    cartes[pid] = fabrique_carte(conn(), pid)
                else:
                    from ..carte import carte_publication
                    cartes[pid] = carte_publication(conn(), pid)
            except Exception:
                cartes[pid] = None
        if cartes[pid] is None:
            abort(404)
        return Response(cartes[pid], mimetype="image/png", headers={"Cache-Control": "private, max-age=3600"})

    @app.post("/publication/<int:pid>/decision")
    @protege
    def decision(pid: int):
        choix = request.form.get("choix")
        if choix not in ("valider", "rejeter"):
            abort(400)
        motif = (request.form.get("motif") or "").strip() or None
        if choix == "rejeter" and not motif:
            flash("Indiquez le motif du rejet.", "erreur")
            return redirect(url_for("accueil") + f"#pub-{pid}")
        try:
            bulletin.decider(conn(), pid, directeur, choix == "valider", motif)
        except ValueError as e:
            flash(str(e), "erreur")
        else:
            flash(f"Publication {pid} {'validée' if choix == 'valider' else 'retirée'}.", "ok")
        return redirect(url_for("accueil"))

    @app.post("/publication/<int:pid>/retrait")
    @protege
    def retrait(pid: int):
        try:
            bulletin.retirer(conn(), pid, directeur, request.form.get("motif", ""))
        except ValueError as e:
            flash(str(e), "erreur")
        else:
            flash(f"Publication {pid} retirée ; un rectificatif partira avec le prochain bulletin.", "ok")
        return redirect(url_for("accueil") + "#publiees")

    @app.get("/bulletin/apercu")
    @protege
    def apercu():
        entrees = bulletin.entrees_pretes(conn())
        for n in entrees:
            n.cid = f"<carte-{n.publication_id}>"
        page = bulletin.composer_html(entrees, date.today(), bulletin.retraits_a_annoncer(conn()))
        for n in entrees:
            page = page.replace(f"cid:carte-{n.publication_id}", url_for("carte", pid=n.publication_id))
        return Response(page, mimetype="text/html")

    @app.post("/bulletin/envoyer")
    @protege
    def envoyer():
        if request.form.get("confirmation") != "oui":
            flash("Cochez la case de confirmation pour envoyer.", "erreur")
            return redirect(url_for("accueil") + "#bulletin")
        c = conn()
        try:
            destinataires = bulletin.lire_destinataires()
            entrees = bulletin.entrees_pretes(c)
            bulletin.ajouter_cartes(c, entrees, fabrique_carte)
            serveur = smtp() if smtp else bulletin.smtp_depuis_env()
            with serveur:
                bid, n, echecs = bulletin.envoyer(c, date.today(), destinataires, serveur,
                                                  os.environ.get("SMTP_EXPEDITEUR", "bulletin@localhost"),
                                                  entrees)
        except Exception as e:
            flash(f"Envoi impossible : {e}", "erreur")
            return redirect(url_for("accueil") + "#bulletin")
        if not bid:
            flash("Aucune publication validée : rien n'a été envoyé.", "erreur")
        else:
            reussis = len(destinataires) - len(echecs)
            flash(f"Bulletin envoyé : {n} alerte(s), {reussis} destinataire(s)."
                  + (f" Échecs : {', '.join(echecs)}" if echecs else ""), "ok" if not echecs else "erreur")
        return redirect(url_for("accueil"))

    return app
