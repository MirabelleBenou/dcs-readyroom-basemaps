"""relief.pmtiles : l'estompage en encre sur transparent, tuiles WebP 512 px, zooms 5 à 12."""
import io
import math

import numpy as np
from PIL import Image
from rasterio.features import rasterize
from rasterio.transform import from_origin
from shapely.geometry import box

from . import bandes, merc
from .terrain import Z_GRILLE, ALIGNEMENT, encre_relief, estompage, piramide

TAILLE = merc.TAILLE_TUILE
QUALITE_WEBP = 85


def _fenetre(tab, origine_x, origine_y, gx0, gy0, lignes, colonnes):
    """Extrait [gy0, gy0+lignes) x [gx0, gx0+colonnes) (indices globaux) ; le hors-grille répète le bord."""
    i0, j0 = gy0 - origine_y, gx0 - origine_x
    h, w = tab.shape
    ii = np.clip(np.arange(i0, i0 + lignes), 0, h - 1)
    jj = np.clip(np.arange(j0, j0 + colonnes), 0, w - 1)
    return tab[np.ix_(ii, jj)]


def tuile_relief(z, x, y, pyr, grille, sol, mer, emprise):
    """Une tuile RGBA (uint8) : l'encre du relief, sans mer ni papier, nulle hors de l'emprise."""
    f = 2 ** (Z_GRILLE - z)
    tab = pyr[z]
    ox, oy = grille.ix0 // f, grille.iy0 // f
    gx0, gy0 = x * TAILLE, y * TAILLE
    fen = _fenetre(tab, ox, oy, gx0 - 1, gy0 - 1, TAILLE + 2, TAILLE + 2).astype("float64")
    pas_sol = merc.pixel(z) * sol
    hs = estompage(fen, pas_sol)[1:-1, 1:-1]
    alt = fen[1:-1, 1:-1]
    rgb, a = encre_relief(hs, alt)
    xmin, ymin, xmax, ymax = merc.bornes_tuile(z, x, y)
    tr = from_origin(xmin, ymax, (xmax - xmin) / TAILLE, (ymax - ymin) / TAILLE)
    dedans = rasterize([(emprise, 1)], out_shape=(TAILLE, TAILLE), transform=tr, fill=0, all_touched=True,
                       dtype="uint8").astype(bool)
    masque = dedans
    if mer is not None:
        en_mer = rasterize([(mer, 1)], out_shape=(TAILLE, TAILLE), transform=tr, fill=0, all_touched=False,
                           dtype="uint8").astype(bool)
        masque = masque & ~en_mer
    a = np.where(masque, a, 0.0)
    out = np.zeros((TAILLE, TAILLE, 4), dtype="uint8")
    out[..., :3] = np.clip(np.rint(rgb), 0, 255).astype("uint8")
    out[..., 3] = np.clip(np.rint(a * 255), 0, 255).astype("uint8")
    return out


def encoder_webp(rgba):
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="WEBP", quality=QUALITE_WEBP, method=4, alpha_quality=100)
    return buf.getvalue()


def construire_relief(terrain, emprise, mer):
    """{(z, x, y): octets WebP} pour tous les zooms de bandes.ZOOM_MIN_RELIEF à ZOOM_MAX_RELIEF."""
    pyr = piramide(terrain.dem, bandes.ZOOM_MIN_RELIEF, Z_GRILLE)
    out = {}
    for z in range(bandes.ZOOM_MIN_RELIEF, bandes.ZOOM_MAX_RELIEF + 1):
        x0, x1, y0, y1 = merc.tuiles_touchant(z, emprise.bounds)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                if not emprise.intersects(box(*merc.bornes_tuile(z, x, y))):
                    continue
                rgba = tuile_relief(z, x, y, pyr, terrain.grille, terrain.sol, mer, emprise)
                if rgba[..., 3].max() == 0:
                    continue
                out[(z, x, y)] = encoder_webp(rgba)
    return out
