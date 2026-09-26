"""Carte avant/après d'un point surveillé, à partir des produits WOfS.

Avant : l'étendue d'eau habituelle (fréquence historique ≥ 20 %).
Après : le dernier passage satellite, où l'eau nouvelle ressort en orange.
Nécessite les extras « satellite » et « carte » (rasterio, matplotlib).
"""

import io
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from .collecte.deafrica import EAU, SEC, lien_https
from .detection import FREQ_RARE

# Palette validée (daltonisme, contraste) avec l'outil de la charte graphique.
FOND = "#fcfcfb"
EAU_HABITUELLE = "#2a78d6"
EAU_NOUVELLE = "#eb6834"
MASQUE = "#c3c2b7"
ENCRE = "#1f1f1e"
ENCRE_SECONDAIRE = "#5f5e58"

SEC_, HABITUELLE, NOUVELLE, NON_VUE = 0, 1, 2, 3


@dataclass
class Grille:
    crs: str
    transform: object
    largeur: int
    hauteur: int
    rayon_m: float


def grille_locale(lon: float, lat: float, rayon_m: float = 1500, resolution: float = 30) -> Grille:
    """Carré de 2 × rayon centré sur le point, dans sa zone UTM."""
    from rasterio.transform import from_origin
    from rasterio.warp import transform

    zone = int((lon + 180) // 6) + 1
    crs = f"EPSG:{32600 + zone if lat >= 0 else 32700 + zone}"
    (x,), (y,) = transform("EPSG:4326", crs, [lon], [lat])
    cote = int(2 * rayon_m / resolution)
    return Grille(crs, from_origin(x - rayon_m, y + rayon_m, resolution, resolution), cote, cote, rayon_m)


def lire_sur_grille(href: str, grille: Grille, remplissage: float) -> np.ndarray:
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT

    with rasterio.open(lien_https(href)) as src, WarpedVRT(
        src, crs=grille.crs, transform=grille.transform, width=grille.largeur, height=grille.hauteur,
        resampling=Resampling.nearest, nodata=remplissage,
    ) as vrt:
        return vrt.read(1)


def classer(frequence: np.ndarray, wofs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Renvoie (avant, après) en classes SEC_, HABITUELLE, NOUVELLE, NON_VUE."""
    habituelle = frequence >= FREQ_RARE
    avant = np.where(habituelle, HABITUELLE, SEC_)
    apres = np.full(wofs.shape, NON_VUE)
    apres[wofs == SEC] = SEC_
    mouille = wofs == EAU
    apres[mouille & habituelle] = HABITUELLE
    apres[mouille & ~habituelle] = NOUVELLE
    return avant, apres


def dessiner(avant: np.ndarray, apres: np.ndarray, titre: str, date_apres: datetime, rayon_m: float) -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    couleurs = ListedColormap([FOND, EAU_HABITUELLE, EAU_NOUVELLE, MASQUE])
    km = rayon_m / 1000
    etendue = (-km, km, -km, km)
    fig, axes = plt.subplots(1, 2, figsize=(8, 4.6), dpi=110, facecolor=FOND)
    for ax, donnees, sous_titre in (
        (axes[0], avant, "Avant : étendue habituelle"),
        (axes[1], apres, f"Après : passage du {date_apres:%d/%m/%Y}"),
    ):
        ax.imshow(donnees, cmap=couleurs, vmin=0, vmax=3, extent=etendue, interpolation="nearest")
        if (donnees == NON_VUE).any():
            # Motif hachuré sur les zones masquées : l'information ne repose pas sur la seule couleur.
            ax.contourf(np.flipud(donnees == NON_VUE).astype(float), levels=[0.5, 1.5], colors="none",
                        hatches=["///"], extent=etendue)
        ax.plot(0, 0, marker="o", markersize=9, markerfacecolor="white", markeredgecolor=ENCRE, markeredgewidth=2)
        ax.set_title(sous_titre, fontsize=10, color=ENCRE, loc="left")
        ax.set_xticks([]), ax.set_yticks([])
        for bord in ax.spines.values():
            bord.set_color(MASQUE)
        ax.plot([km - 1.1, km - 0.1], [-km + 0.15] * 2, color=ENCRE, linewidth=2)
        ax.text(km - 0.6, -km + 0.25, "1 km", ha="center", fontsize=8, color=ENCRE)
    fig.text(0.02, 0.965, titre, ha="left", va="center", fontsize=12, color=ENCRE)
    fig.legend(handles=[
        Patch(facecolor=EAU_HABITUELLE, label="Eau habituelle"),
        Patch(facecolor=EAU_NOUVELLE, label="Eau inhabituelle"),
        Patch(facecolor=MASQUE, hatch="///", label="Non observé (nuages)"),
        Line2D([], [], linestyle="none", marker="o", markersize=8, markerfacecolor="white",
               markeredgecolor=ENCRE, markeredgewidth=2, label="Point surveillé"),
    ], loc="lower center", ncol=4, frameon=False, fontsize=8, labelcolor=ENCRE_SECONDAIRE)
    fig.text(0.98, 0.965, "Source : Digital Earth Africa (WOfS, Landsat)", ha="right", va="center",
             fontsize=7, color=ENCRE_SECONDAIRE)
    fig.tight_layout(rect=(0, 0.07, 1, 0.91))
    sortie = io.BytesIO()
    fig.savefig(sortie, format="png", facecolor=FOND)
    plt.close(fig)
    return sortie.getvalue()


def carte_publication(conn, publication_id: int, lecteur=lire_sur_grille) -> bytes | None:
    """Carte d'une publication, ou None si les images satellites manquent."""
    ligne = conn.execute(
        "SELECT i.nom, ST_X(ST_PointOnSurface(i.geom)), ST_Y(ST_PointOnSurface(i.geom)), a.elements"
        " FROM terre.publication p JOIN terre.anomalie a ON a.evenement_id = p.evenement_id"
        " JOIN terre.infrastructure i ON i.id = a.infrastructure_id WHERE p.id = %s",
        (publication_id,),
    ).fetchone()
    if ligne is None:
        return None
    nom, lon, lat, elements = ligne
    ids = elements.get("observations", [])
    freq = conn.execute(
        "SELECT brut->>'href' FROM terre.observation WHERE id = ANY(%s)"
        " AND variable = 'frequence_eau_historique' AND brut ? 'href' LIMIT 1", (ids,)).fetchone()
    scene = conn.execute(
        "SELECT brut->>'href', observe_le FROM terre.observation WHERE id = ANY(%s)"
        " AND variable = 'eau_observee' AND valeur = 1 AND brut ? 'href'"
        " ORDER BY observe_le DESC LIMIT 1", (ids,)).fetchone()
    if not freq or not scene:
        return None
    grille = grille_locale(lon, lat)
    avant, apres = classer(lecteur(freq[0], grille, np.nan), lecteur(scene[0], grille, 255))
    return dessiner(avant, apres, nom, scene[1], grille.rayon_m)
