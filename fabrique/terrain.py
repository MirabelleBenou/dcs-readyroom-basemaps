"""Le relief rééchantillonné sur la grille maîtresse (pixels du zoom 12), la mer, les courbes, l'estompage."""
import math

import contourpy
import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject
from scipy import ndimage
from shapely.geometry import Polygon
from shapely.validation import make_valid

from . import merc

Z_GRILLE = 12
ALIGNEMENT = 128          # pixels du zoom 12 par pixel du zoom 5 : tous les zooms 5 à 12 tombent juste
SEUIL_MER = 0.5           # m
SIGMA_COURBES = 3.2       # pixels de la grille : environ 50 m au sol, comme papier.py
SIGMA_COTE = 1.0
SIGMA_RIVAGE = 2.5
ESTOMPAGE = {"azimut": 315.0, "hauteur": 35.0, "exageration": 2.6}


class Grille:
    """Une grille de pixels du zoom 12, alignée sur 128 pixels, qui couvre l'emprise avec une marge."""

    def __init__(self, boite_merc, marge_blocs=1):
        px = merc.pixel(Z_GRILLE)
        self.px = px
        ix0 = math.floor((boite_merc[0] + merc.DEMI_MONDE) / px)
        ix1 = math.ceil((boite_merc[2] + merc.DEMI_MONDE) / px)
        iy0 = math.floor((merc.DEMI_MONDE - boite_merc[3]) / px)
        iy1 = math.ceil((merc.DEMI_MONDE - boite_merc[1]) / px)
        a = ALIGNEMENT
        self.ix0 = (ix0 // a - marge_blocs) * a
        self.iy0 = (iy0 // a - marge_blocs) * a
        self.larg = ((ix1 + a - 1) // a + marge_blocs) * a - self.ix0
        self.haut = ((iy1 + a - 1) // a + marge_blocs) * a - self.iy0
        self.x0 = -merc.DEMI_MONDE + self.ix0 * px
        self.y1 = merc.DEMI_MONDE - self.iy0 * px
        self.transform = from_origin(self.x0, self.y1, px, px)

    @property
    def forme(self):
        return (self.haut, self.larg)

    def xs(self):
        return self.x0 + (np.arange(self.larg) + 0.5) * self.px

    def ys(self):
        return self.y1 - (np.arange(self.haut) + 0.5) * self.px


def construire_dem(grille, entrees):
    """Rééchantillonne les dalles (entrées du manifeste avec `fichier`) sur la grille. Absent : altitude 0."""
    dst = np.full(grille.forme, np.nan, dtype="float32")
    couv = np.zeros(grille.forme, dtype=bool)
    for e in sorted(entrees, key=lambda e: e["dalle"]):
        if not e.get("fichier"):
            continue
        with rasterio.open(e["fichier"]) as ds:
            reproject(rasterio.band(ds, 1), dst, dst_transform=grille.transform, dst_crs="EPSG:3857",
                      dst_nodata=np.nan, init_dest_nodata=False, resampling=Resampling.bilinear, num_threads=1)
        lat, lon = e["lat"], e["lon"]
        xa, xb = merc.lonlat_vers_merc([lon, lon + 1], [lat, lat])[0]
        ya, yb = merc.lonlat_vers_merc([lon, lon], [lat, lat + 1])[1]
        j0 = max(0, int(math.floor((xa - grille.x0) / grille.px)))
        j1 = min(grille.larg, int(math.ceil((xb - grille.x0) / grille.px)))
        i0 = max(0, int(math.floor((grille.y1 - yb) / grille.px)))
        i1 = min(grille.haut, int(math.ceil((grille.y1 - ya) / grille.px)))
        if j1 > j0 and i1 > i0:
            couv[i0:i1, j0:j1] = True
    nan = np.isnan(dst)
    trou = nan & couv
    if trou.any() and (~nan).any():          # coutures entre dalles : la valeur voisine
        idx = ndimage.distance_transform_edt(nan, return_distances=False, return_indices=True)
        dst = np.where(trou, dst[idx[0], idx[1]], dst)
    dst = np.where(np.isnan(dst), 0.0, dst).astype("float32")
    return dst


def masque_emprise(grille, emprise_merc):
    return rasterize([(emprise_merc, 1)], out_shape=grille.forme, transform=grille.transform,
                     fill=0, all_touched=True, dtype="uint8").astype(bool)


def calculer_mer(dem, dedans):
    """Altitude <= 0,5 m reliée au bord de l'emprise ; une dépression intérieure n'est jamais de la mer."""
    graine = dem <= SEUIL_MER
    lab, _ = ndimage.label(graine)
    bord = dedans & ~ndimage.binary_erosion(dedans, border_value=0)
    ids = np.unique(lab[bord & graine])
    ids = ids[ids > 0]
    return np.isin(lab, ids)


def polygones_depuis_remplissage(gen, bas, haut):
    pts_list, off_list = gen.filled(bas, haut)
    out = []
    for pts, off in zip(pts_list, off_list):
        if pts is None or len(pts) == 0:
            continue
        anneaux = [pts[off[i]:off[i + 1]] for i in range(len(off) - 1)]
        anneaux = [a for a in anneaux if len(a) >= 4]
        if not anneaux:
            continue
        p = Polygon(anneaux[0], [a for a in anneaux[1:]])
        if not p.is_valid:
            p = make_valid(p)
        out.append(p)
    return out


class Terrain:
    def __init__(self, grille, dem, dedans, lat_centre):
        self.grille = grille
        self.dem = dem
        self.dedans = dedans
        self.sol = math.cos(math.radians(lat_centre))      # mètre sol par mètre Mercator
        self.mer = calculer_mer(dem, dedans)
        self._xs, self._ys = grille.xs(), grille.ys()
        self._champ_mer = None
        self._terre = None
        self._dist = None

    # ------------------------------------------------------------------ mer
    def champ_mer(self):
        if self._champ_mer is None:
            self._champ_mer = ndimage.gaussian_filter(self.mer.astype("float32"), SIGMA_COTE, mode="nearest")
        return self._champ_mer

    def polygone_mer(self):
        """La mer pleine résolution (avant simplification par bande), en Mercator."""
        if not self.mer.any():
            return None
        gen = contourpy.contour_generator(self._xs, self._ys, self.champ_mer(),
                                          fill_type=contourpy.FillType.OuterOffset)
        polys = polygones_depuis_remplissage(gen, 0.5, 2.0)
        if not polys:
            return None
        from shapely.ops import unary_union
        return unary_union(polys)

    def ligne_cote(self):
        if not self.mer.any():
            return []
        gen = contourpy.contour_generator(self._xs, self._ys, self.champ_mer(), line_type=contourpy.LineType.Separate)
        return [np.asarray(l) for l in gen.lines(0.5)]

    # ------------------------------------------------------------------ rivage
    def champ_distance(self):
        if self._dist is None:
            d = ndimage.distance_transform_edt(self.mer) * (self.grille.px * self.sol)
            self._dist = ndimage.gaussian_filter(d.astype("float32"), SIGMA_RIVAGE, mode="nearest")
        return self._dist

    def lignes_rivage(self, distance_m):
        if not self.mer.any():
            return []
        gen = contourpy.contour_generator(self._xs, self._ys, self.champ_distance(), line_type=contourpy.LineType.Separate)
        return [np.asarray(l) for l in gen.lines(float(distance_m))]

    # ------------------------------------------------------------------ courbes
    def champ_terre(self):
        if self._terre is None:
            z = np.where(self.mer, -1.0, self.dem).astype("float32")
            self._terre = ndimage.gaussian_filter(z, SIGMA_COURBES, mode="nearest")
        return self._terre

    def courbes(self, niveaux):
        """{niveau: [lignes]} ; une ligne fermée a ses deux extrémités égales."""
        gen = contourpy.contour_generator(self._xs, self._ys, self.champ_terre(), line_type=contourpy.LineType.Separate)
        out = {}
        for n in niveaux:
            out[n] = [np.asarray(l) for l in gen.lines(float(n))]
        return out

    def altitude_max(self):
        return float(self.champ_terre().max())


# ---------------------------------------------------------------------- estompage
def piramide(dem, z_min=5, z_max=Z_GRILLE):
    """Le relief moyenné à la taille du pixel de chaque zoom : {z: tableau}. Les blocs sont alignés (128 px)."""
    out = {z_max: dem}
    cur = dem
    for z in range(z_max - 1, z_min - 1, -1):
        h, w = cur.shape
        cur = cur.reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3)).astype("float32")
        out[z] = cur
    return out


def estompage(z, pas, azimut=ESTOMPAGE["azimut"], hauteur=ESTOMPAGE["hauteur"], exageration=ESTOMPAGE["exageration"]):
    """L'estompage C de papier.py (formule de planche.py), pas en mètres au sol ; rend hs dans [0, 1]."""
    zz = np.nan_to_num(z, nan=0.0).astype("float64") * exageration
    gy, gx = np.gradient(zz, pas)
    pente = np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    a, b = math.radians(azimut), math.radians(hauteur)
    hs = np.sin(b) * np.cos(pente) + np.cos(b) * np.sin(pente) * np.cos(a - aspect)
    return np.clip(hs, 0, 1)


ENCRE_RELIEF = (0x6e, 0x55, 0x38)
TEINTE_HAUT = (0xd9, 0xb9, 0x8c)


def encre_relief(hs, alt):
    """L'encre sur transparent : rend (rgb float [0,255], alpha float [0,1]) ; ni mer ni papier."""
    a1 = 0.42 * (1.0 - np.clip(hs, 0, 1) ** 0.85)
    haut = np.clip((alt - 400.0) / 1000.0, 0, 1)
    a2 = 0.04 * haut
    a = a1 + a2 * (1 - a1)
    ink = np.array(ENCRE_RELIEF, dtype="float64")
    tint = np.array(TEINTE_HAUT, dtype="float64")
    num = ink[None, None, :] * a1[..., None] + tint[None, None, :] * (a2 * (1 - a1))[..., None]
    rgb = num / np.maximum(a, 1e-9)[..., None]
    return rgb, a
