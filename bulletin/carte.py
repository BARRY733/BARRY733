"""Carte « avant / après » d'un point surveillé.

Avant : l'étendue habituelle de l'eau (fréquence historique WOfS).
Après : la scène récente en eau qui a déclenché la détection.
Les deux panneaux couvrent la même emprise, centrée sur le point.
"""

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from matplotlib.colors import ListedColormap  # noqa: E402
from rasterio.warp import transform  # noqa: E402
from rasterio.windows import Window  # noqa: E402

from detection import SEUIL_HISTORIQUE  # noqa: E402

DEMI_COTE_M = 3000
EAU = 128
BLEU = "#1f5fa8"
SEC = "#efe9dc"
MASQUE = "#c9c9c9"


def lire_autour(href: str, lon: float, lat: float, demi_cote_m: float = DEMI_COTE_M) -> np.ndarray:
    """Extrait la fenêtre carrée centrée sur le point ; hors couche, les pixels valent NaN."""
    with rasterio.open(href) as couche:
        xs, ys = transform("EPSG:4326", couche.crs, [lon], [lat])
        ligne, col = couche.index(xs[0], ys[0])
        demi = max(1, int(demi_cote_m / abs(couche.transform.a)))
        fenetre = Window(col - demi, ligne - demi, 2 * demi + 1, 2 * demi + 1)
        hors_couche = np.nan if couche.dtypes[0].startswith("float") else 255
        return couche.read(1, window=fenetre, boundless=True, fill_value=hors_couche).astype("float32")


def dessiner(frequence: np.ndarray, scene: np.ndarray, titre_point: str, date_scene: str, mention: str) -> bytes:
    fig, (a, b) = plt.subplots(1, 2, figsize=(8, 4.4), dpi=110)
    c = frequence.shape[0] // 2

    habituel = np.where(np.isnan(frequence), np.nan, (frequence >= SEUIL_HISTORIQUE).astype("float32"))
    a.imshow(habituel, cmap=ListedColormap([SEC, BLEU]), vmin=0, vmax=1, interpolation="nearest")
    a.set_title("Étendue habituelle de l'eau", fontsize=10)

    etat = np.full(scene.shape, 2.0, dtype="float32")  # 2 = masqué (nuage, ombre, hors couche)
    etat[scene == 0] = 0
    etat[scene == EAU] = 1
    b.imshow(etat, cmap=ListedColormap([SEC, BLEU, MASQUE]), vmin=0, vmax=2, interpolation="nearest")
    b.set_title(f"Observation du {date_scene}", fontsize=10)

    for ax in (a, b):
        ax.plot(c, c, marker="^", color="#c0392b", markersize=9, markeredgecolor="white")
        ax.set_xticks([]), ax.set_yticks([])
    fig.suptitle(titre_point, fontsize=12, fontweight="bold")
    fig.text(0.5, 0.02, f"Bleu : eau · gris : non observé · ▲ point surveillé · {2 * DEMI_COTE_M // 1000} km de côté\n"
             f"{mention}", ha="center", fontsize=7, color="#444")
    fig.tight_layout(rect=(0, 0.07, 1, 0.95))
    tampon = io.BytesIO()
    fig.savefig(tampon, format="png")
    plt.close(fig)
    return tampon.getvalue()


def carte_evenement(cur, evenement_id: int) -> bytes | None:
    """Carte de l'événement, ou None si les couches nécessaires manquent."""
    cur.execute(
        """
        SELECT i.nom, ST_X(i.geom), ST_Y(i.geom),
          (SELECT ressource FROM terre.indicateur WHERE infrastructure_id = i.id
             AND nom = 'frequence_eau_historique' AND ressource IS NOT NULL ORDER BY mesure_le DESC LIMIT 1),
          s.ressource, s.mesure_le
        FROM terre.impact im
        JOIN terre.infrastructure i ON i.id = im.infrastructure_id
        LEFT JOIN LATERAL (SELECT ressource, mesure_le FROM terre.indicateur
             WHERE evenement_id = im.evenement_id AND nom = 'eau_observee' AND valeur = 1
               AND ressource IS NOT NULL ORDER BY mesure_le DESC LIMIT 1) s ON true
        WHERE im.evenement_id = %s LIMIT 1
        """,
        (evenement_id,),
    )
    ligne = cur.fetchone()
    if not ligne or not ligne[3] or not ligne[4]:
        return None
    nom, lon, lat, href_freq, href_scene, quand = ligne
    cur.execute("SELECT mention_obligatoire FROM terre.source WHERE code = 'deafrica'")
    mention = cur.fetchone()[0] + ", Landsat (USGS)"
    return dessiner(lire_autour(href_freq, lon, lat), lire_autour(href_scene, lon, lat),
                    nom, f"{quand:%d/%m/%Y}", mention)
