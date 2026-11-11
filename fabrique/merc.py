"""Web Mercator (EPSG:3857) : tuiles, pixels, conversions. Tout le trait se calcule dans ce plan."""
import math

import numpy as np

RAYON = 6378137.0
DEMI_MONDE = math.pi * RAYON          # 20037508.342789244
TAILLE_TUILE = 512                    # pixels d'écran d'une tuile à son zoom (MapLibre)
ETENDUE_MVT = 4096


def pixel(z, tuile=TAILLE_TUILE):
    """Taille d'un pixel d'écran, en mètres Mercator, au zoom z (réel ou entier)."""
    return 2 * DEMI_MONDE / (tuile * 2.0 ** z)


def lonlat_vers_merc(lon, lat):
    lon = np.asarray(lon, dtype="float64")
    lat = np.asarray(lat, dtype="float64")
    x = np.radians(lon) * RAYON
    y = np.log(np.tan(math.pi / 4 + np.radians(lat) / 2)) * RAYON
    return x, y


def merc_vers_lonlat(x, y):
    x = np.asarray(x, dtype="float64")
    y = np.asarray(y, dtype="float64")
    lon = np.degrees(x / RAYON)
    lat = np.degrees(2 * np.arctan(np.exp(y / RAYON)) - math.pi / 2)
    return lon, lat


def bornes_tuile(z, x, y):
    """(xmin, ymin, xmax, ymax) en Mercator de la tuile z/x/y (y vers le bas, schéma XYZ)."""
    t = 2 * DEMI_MONDE / 2 ** z
    return (-DEMI_MONDE + x * t, DEMI_MONDE - (y + 1) * t, -DEMI_MONDE + (x + 1) * t, DEMI_MONDE - y * t)


def tuiles_touchant(z, boite):
    """Indices (x0, x1, y0, y1) inclusifs des tuiles du zoom z qui touchent la boîte Mercator."""
    t = 2 * DEMI_MONDE / 2 ** z
    n = 2 ** z
    x0 = int(math.floor((boite[0] + DEMI_MONDE) / t))
    x1 = int(math.floor((boite[2] + DEMI_MONDE) / t))
    y0 = int(math.floor((DEMI_MONDE - boite[3]) / t))
    y1 = int(math.floor((DEMI_MONDE - boite[1]) / t))
    return max(x0, 0), min(x1, n - 1), max(y0, 0), min(y1, n - 1)


def echelle_sol(lat):
    """Rapport mètre sol / mètre Mercator à la latitude donnée (degrés)."""
    return math.cos(math.radians(lat))
