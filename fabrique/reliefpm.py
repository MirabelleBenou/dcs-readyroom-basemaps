"""relief.pmtiles : l'estompage en encre sur transparent, tuiles WebP 512 px, zooms 5 à 12.

Chaque tuile se calcule seule, depuis une fenêtre de la pyramide du relief (un pixel de marge pour le gradient) et le
masque raster de la mer : un pixel du niveau z est un pixel de la tuile du zoom z. Le facteur d'échelle Mercator est
celui de la tuile (sa latitude centrale), jamais un seul cos pour toute l'emprise.
"""
import io
import math

import numpy as np
import shapely
from PIL import Image
from rasterio.features import rasterize
from rasterio.transform import from_origin
from shapely.geometry import box

from . import bandes, merc
from .terrain import encre_relief, estompage

TAILLE = merc.TAILLE_TUILE
QUALITE_WEBP = 85


def echelle_tuile(z, x, y):
    """Le facteur d'échelle Mercator (mètre sol par mètre Mercator) de la tuile : le cos de sa latitude centrale."""
    xmin, ymin, xmax, ymax = merc.bornes_tuile(z, x, y)
    lat = float(merc.merc_vers_lonlat(0.0, (ymin + ymax) / 2)[1])
    return math.cos(math.radians(lat))


def tuile_relief(z, x, y, pyr, emprise):
    """Une tuile RGBA (uint8) : l'encre du relief, sans mer ni papier, nulle hors de l'emprise."""
    gx0, gy0 = x * TAILLE, y * TAILLE
    fen = pyr.lire("dem", z, gy0 - 1, gx0 - 1, TAILLE + 2, TAILLE + 2).astype("float64")
    hs = estompage(fen, merc.pixel(z) * echelle_tuile(z, x, y))[1:-1, 1:-1]
    alt = fen[1:-1, 1:-1]
    rgb, a = encre_relief(hs, alt)
    en_mer = pyr.lire("mer", z, gy0, gx0, TAILLE, TAILLE) >= 128
    xmin, ymin, xmax, ymax = merc.bornes_tuile(z, x, y)
    if shapely.contains(emprise, box(xmin, ymin, xmax, ymax)):
        dedans = np.ones((TAILLE, TAILLE), dtype=bool)
    else:
        tr = from_origin(xmin, ymax, (xmax - xmin) / TAILLE, (ymax - ymin) / TAILLE)
        dedans = rasterize([(emprise, 1)], out_shape=(TAILLE, TAILLE), transform=tr, fill=0, all_touched=True,
                           dtype="uint8").astype(bool)
    a = np.where(dedans & ~en_mer, a, 0.0)
    out = np.zeros((TAILLE, TAILLE, 4), dtype="uint8")
    out[..., :3] = np.clip(np.rint(rgb), 0, 255).astype("uint8")
    out[..., 3] = np.clip(np.rint(a * 255), 0, 255).astype("uint8")
    return out


def encoder_webp(rgba):
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="WEBP", quality=QUALITE_WEBP, method=4, alpha_quality=100)
    return buf.getvalue()


def construire_relief(pyr, emprise, depot, journal=None):
    """Écrit dans le dépôt les tuiles WebP de tous les zooms de bandes.ZOOM_MIN_RELIEF à ZOOM_MAX_RELIEF, une à une.
    Rend le nombre de tuiles. Une emprise toute en mer n'en donne aucune (le relief est alors vide, et valide)."""
    n = 0
    for z in range(bandes.ZOOM_MIN_RELIEF, bandes.ZOOM_MAX_RELIEF + 1):
        x0, x1, y0, y1 = merc.tuiles_touchant(z, emprise.bounds)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                if not shapely.intersects(emprise, box(*merc.bornes_tuile(z, x, y))):
                    continue
                rgba = tuile_relief(z, x, y, pyr, emprise)
                if rgba[..., 3].max() == 0:
                    continue
                depot.ecrire(z, x, y, encoder_webp(rgba))
                n += 1
    return n
