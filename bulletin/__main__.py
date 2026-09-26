"""Outil du directeur de publication.

python -m bulletin a-valider                 # textes en attente de décision
python -m bulletin lire ID                   # texte complet et preuves
python -m bulletin valider ID [commentaire]
python -m bulletin rejeter ID motif
python -m bulletin apercu [fichier.html]     # bulletin tel qu'il partira
python -m bulletin envoyer                   # diffusion par courriel
"""

import base64
import sys
from datetime import date

from collecte.commun import connexion

from . import a_valider, articles_prets, decider, envoyer, gabarit


def main(args: list[str]) -> int:
    if not args:
        print(__doc__)
        return 1
    commande, reste = args[0], args[1:]
    with connexion() as conn, conn.transaction(), conn.cursor() as cur:
        if commande == "a-valider":
            lignes = a_valider(cur)
            print(f"{len(lignes)} texte(s) en attente")
            for pid, titre, confiance, cree in lignes:
                print(f"  {pid:>5}  {cree:%d/%m %H:%M}  [{confiance}]  {titre}")
        elif commande == "lire":
            cur.execute("SELECT titre, contenu, confiance, statut FROM terre.publication WHERE id = %s", (int(reste[0]),))
            titre, contenu, confiance, statut = cur.fetchone()
            print(f"{titre}\n[{confiance} · {statut}]\n\n{contenu}\n\nPreuves :")
            cur.execute("SELECT * FROM terre.lignage WHERE publication_id = %s", (int(reste[0]),))
            for ligne in cur.fetchall():
                print(f"  preuve {ligne[4]} · {ligne[8]} · {ligne[6]:%d/%m/%Y} · {ligne[7]}")
        elif commande in ("valider", "rejeter"):
            if commande == "rejeter" and len(reste) < 2:
                print("Un rejet exige un motif.")
                return 1
            decider(cur, int(reste[0]), commande == "valider", " ".join(reste[1:]) or None)
            print(f"Publication {reste[0]} : {'validée' if commande == 'valider' else 'rejetée'}.")
        elif commande == "apercu":
            articles = articles_prets(cur)
            page = gabarit(articles, date.today())
            # Dans l'aperçu, les cartes sont intégrées directement dans la page.
            for a in articles:
                if a.carte:
                    img = base64.b64encode(a.carte).decode()
                    page = page.replace(f"<!--carte-{a.publication_id}-->",
                                        f'<img src="data:image/png;base64,{img}" width="560" alt="Carte">')
            chemin = reste[0] if reste else "apercu_bulletin.html"
            with open(chemin, "w", encoding="utf-8") as f:
                f.write(page)
            print(f"{len(articles)} article(s) · aperçu écrit dans {chemin}")
        elif commande == "envoyer":
            bulletin_id = envoyer(cur)
            print("Rien à envoyer." if bulletin_id is None else f"Bulletin {bulletin_id} envoyé.")
        else:
            print(__doc__)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
