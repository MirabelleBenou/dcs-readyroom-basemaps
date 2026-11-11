"""Le relief : échantillonné une fois sur la grille maîtresse (pixels du zoom 12), rangé sur disque en pyramide.

Rien ne dépend d'une fenêtre : chaque pixel de la grille se calcule depuis ses seules coordonnées (interpolation
bilinéaire des dalles, séparable, à coordonnées en double précision). Les blocs de la fabrique lisent ensuite des
fenêtres de cette pyramide, avec une marge ; lire une fenêtre plus grande ou plus petite ne change aucune valeur.

La mer se juge UNE fois, sur une grille globale au pixel du zoom 10 : les pixels d'altitude <= 0,5 m reliés au bord
de l'emprise, à condition que leur ensemble contienne au moins un pixel semé entre -0,5 et +0,5 m (une dépression
- mer Morte, Tibériade - n'est jamais de la mer) ; cette grille se dilate d'un pixel. Au pixel fin, la mer est
altitude <= 0,5 m ET pixel de mer de la grille globale.
"""
import math
import os

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.windows import Window
from scipy import ndimage
from shapely.geometry import Polygon
from shapely.validation import make_valid

from . import merc

Z_GRILLE = 12
Z_MER = 10                # la grille globale de connexité de la mer
Z_MIN = 5
ALIGNEMENT = 128          # pixels du zoom 12 par pixel du zoom 5 : tous les zooms 5 à 12 tombent juste
MARGE_BLOCS = 8           # 1 024 pixels du zoom 12 (~ 39 km) de marge autour de la boîte de l'emprise
SEUIL_MER = 0.5           # m
SEMENCE_MER = 0.5         # m : une semence de mer est entre -0,5 et +0,5 m
SIGMA_COURBES = 3.2       # pixels du zoom 12 : environ 50 m au sol, comme l'esquisse de référence
SIGMA_COTE = 1.0
SIGMA_RIVAGE = 2.5
BANDE_LIGNES = 256        # lignes du zoom 12 traitées à la fois (multiple de 128)
ESTOMPAGE = {"azimut": 315.0, "hauteur": 35.0, "exageration": 2.6}


class Grille:
    """Une grille de pixels du zoom 12, alignée sur 128 pixels, qui couvre la boîte de l'emprise avec une marge."""

    def __init__(self, boite_merc, marge_blocs=MARGE_BLOCS):
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

    @property
    def forme(self):
        return (self.haut, self.larg)

    def niveau(self, z):
        """(origine x, origine y, largeur, hauteur) de la grille au zoom z, en pixels du monde à ce zoom."""
        f = Z_GRILLE - z
        return self.ix0 >> f, self.iy0 >> f, self.larg >> f, self.haut >> f

    def transform(self, z=Z_GRILLE):
        ox, oy, _, _ = self.niveau(z)
        px = merc.pixel(z)
        return from_origin(-merc.DEMI_MONDE + ox * px, merc.DEMI_MONDE - oy * px, px, px)


# ---------------------------------------------------------------------- les dalles
def _poids(coord, n):
    """Indices et poids de l'interpolation linéaire à des coordonnées de centre de pixel (bord répété)."""
    i = np.clip(np.floor(coord), 0, max(n - 2, 0)).astype(np.int64)
    t = np.clip(coord - i, 0.0, 1.0) if n > 1 else np.zeros_like(coord)
    return i, t


class SourceDEM:
    """Les dalles du manifeste (avec `fichier`), échantillonnées sur la grille. Une dalle absente : altitude 0.
    Hors de la couverture des dalles, la valeur du bord se répète."""

    def __init__(self, entrees):
        self.dalles = sorted(entrees, key=lambda e: e["dalle"])
        self.lon_min = min(e["lon"] for e in self.dalles)
        self.lon_max = max(e["lon"] for e in self.dalles) + 1
        self.lat_min = min(e["lat"] for e in self.dalles)
        self.lat_max = max(e["lat"] for e in self.dalles) + 1
        self._ds = {}

    def _ouvrir(self, e):
        if e["dalle"] not in self._ds:
            self._ds[e["dalle"]] = rasterio.open(e["fichier"])
        return self._ds[e["dalle"]]

    def fermer(self):
        for ds in self._ds.values():
            ds.close()
        self._ds = {}

    def lignes(self, grille, i0, i1):
        """Les lignes [i0, i1) de la grille (altitudes en float32)."""
        px = grille.px
        xs = -merc.DEMI_MONDE + (grille.ix0 + np.arange(grille.larg) + 0.5) * px
        ys = merc.DEMI_MONDE - (grille.iy0 + np.arange(i0, i1) + 0.5) * px
        lon, _ = merc.merc_vers_lonlat(xs, np.zeros_like(xs))
        _, lat = merc.merc_vers_lonlat(np.zeros_like(ys), ys)
        lon = np.clip(lon, self.lon_min, self.lon_max - 1e-9)
        lat = np.clip(lat, self.lat_min, self.lat_max - 1e-9)
        out = np.zeros((i1 - i0, grille.larg), dtype="float32")
        fl_lon, fl_lat = np.floor(lon).astype(int), np.floor(lat).astype(int)
        for e in self.dalles:
            if not e.get("fichier"):
                continue
            cols = np.flatnonzero(fl_lon == e["lon"])
            rows = np.flatnonzero(fl_lat == e["lat"])
            if len(cols) == 0 or len(rows) == 0:
                continue
            ds = self._ouvrir(e)
            gauche, haut = ds.bounds.left, ds.bounds.top
            rx, ry = ds.transform.a, -ds.transform.e
            cc = (lon[cols] - gauche) / rx - 0.5
            rr = (haut - lat[rows]) / ry - 0.5
            c0 = int(max(0, math.floor(cc.min())))
            c1 = int(min(ds.width - 1, math.floor(cc.max()) + 1))
            r0 = int(max(0, math.floor(rr.min())))
            r1 = int(min(ds.height - 1, math.floor(rr.max()) + 1))
            arr = ds.read(1, window=Window(c0, r0, c1 - c0 + 1, r1 - r0 + 1)).astype("float64")
            if ds.nodata is not None:
                arr[arr == ds.nodata] = 0.0
            arr = np.nan_to_num(arr, nan=0.0)
            ci, tc = _poids(cc - c0, arr.shape[1])
            ri, tr = _poids(rr - r0, arr.shape[0])
            tmp = arr[:, ci] * (1.0 - tc) + arr[:, np.minimum(ci + 1, arr.shape[1] - 1)] * tc
            val = tmp[ri] * (1.0 - tr)[:, None] + tmp[np.minimum(ri + 1, arr.shape[0] - 1)] * tr[:, None]
            out[np.ix_(rows, cols)] = val.astype("float32")
        return out


# ---------------------------------------------------------------------- la pyramide sur disque
class Pyramide:
    """Les niveaux 5 à 12 du relief (float32) et de la mer (octet : fraction de mer x 255), dans des fichiers à plat.
    Une lecture ne garde rien : c'est une fenêtre copiée, pas un fichier projeté en mémoire."""

    def __init__(self, grille, dossier):
        self.g = grille
        self.dossier = dossier

    def chemin(self, genre, z):
        return os.path.join(self.dossier, "%s-%d.bin" % (genre, z))

    @staticmethod
    def _type(genre):
        return np.dtype("float32") if genre == "dem" else np.dtype("uint8")

    def creer(self, genre):
        for z in range(Z_MIN, Z_GRILLE + 1):
            _, _, w, h = self.g.niveau(z)
            with open(self.chemin(genre, z), "wb") as f:
                f.truncate(w * h * self._type(genre).itemsize)

    def ecrire(self, genre, z, ligne0, tab):
        """Écrit des lignes entières du niveau z à partir de la ligne `ligne0` de la grille."""
        _, _, w, _ = self.g.niveau(z)
        assert tab.shape[1] == w
        dt = self._type(genre)
        with open(self.chemin(genre, z), "r+b") as f:
            f.seek(ligne0 * w * dt.itemsize)
            f.write(np.ascontiguousarray(tab, dtype=dt).tobytes())

    def lire(self, genre, z, gy0, gx0, hauteur, largeur):
        """La fenêtre [gy0, gy0+hauteur) x [gx0, gx0+largeur), en indices du monde au zoom z ; le bord se répète."""
        ox, oy, w, h = self.g.niveau(z)
        dt = self._type(genre)
        i0, j0 = gy0 - oy, gx0 - ox
        ia, ib = max(0, i0), min(h, i0 + hauteur)
        ja, jb = max(0, j0), min(w, j0 + largeur)
        if ia >= ib or ja >= jb:
            raise ValueError("fenêtre hors de la grille : niveau %d, %r" % (z, (gy0, gx0, hauteur, largeur)))
        bloc = np.empty((ib - ia, jb - ja), dtype=dt)
        n = (jb - ja) * dt.itemsize
        with open(self.chemin(genre, z), "rb") as f:
            for k, i in enumerate(range(ia, ib)):
                f.seek((i * w + ja) * dt.itemsize)
                bloc[k] = np.frombuffer(f.read(n), dtype=dt)
        av_h, ap_h, av_w, ap_w = ia - i0, (i0 + hauteur) - ib, ja - j0, (j0 + largeur) - jb
        if av_h or ap_h or av_w or ap_w:
            bloc = np.pad(bloc, ((av_h, ap_h), (av_w, ap_w)), mode="edge")
        return bloc


def _mean2(a):
    h, w = a.shape
    return a.reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3), dtype="float32")


def mer_globale(bas, graine, dedans):
    """La mer de la grille globale (pixels du zoom 10) : les ensembles de pixels `bas` (altitude <= 0,5 m quelque part
    dans le pixel) reliés au bord de l'emprise ET portant au moins une `graine` (entre -0,5 et +0,5 m) ; puis dilatée
    d'un pixel."""
    lab, n = ndimage.label(bas)
    if n == 0:
        return np.zeros(bas.shape, dtype=bool)
    bord = dedans & ~ndimage.binary_erosion(dedans, border_value=0)
    ids_bord = np.unique(lab[bord & bas])
    ids_graine = np.unique(lab[graine & bas])
    ids = np.intersect1d(ids_bord, ids_graine)
    ids = ids[ids > 0]
    if len(ids) == 0:
        return np.zeros(bas.shape, dtype=bool)
    mer = np.isin(lab, ids)
    del lab
    return ndimage.binary_dilation(mer, structure=np.ones((3, 3), dtype=bool))


def construire(grille, entrees, emprise_merc, dossier, journal=print):
    """Échantillonne le relief et range la pyramide du relief et de la mer dans `dossier`. Rend la `Pyramide`."""
    pyr = Pyramide(grille, dossier)
    pyr.creer("dem")
    pyr.creer("mer")
    src = SourceDEM(entrees)
    f10 = 1 << (Z_GRILLE - Z_MER)
    h10, w10 = grille.haut // f10, grille.larg // f10
    bas = np.zeros((h10, w10), dtype=bool)
    graine = np.zeros((h10, w10), dtype=bool)
    try:
        for i0 in range(0, grille.haut, BANDE_LIGNES):
            i1 = min(grille.haut, i0 + BANDE_LIGNES)
            fin = src.lignes(grille, i0, i1)
            cur = fin
            pyr.ecrire("dem", Z_GRILLE, i0, cur)
            q = fin.reshape((i1 - i0) // f10, f10, w10, f10)
            bas[i0 // f10:i1 // f10] = q.min(axis=(1, 3)) <= SEUIL_MER
            graine[i0 // f10:i1 // f10] = ((q >= -SEMENCE_MER) & (q <= SEMENCE_MER)).any(axis=(1, 3))
            for z in range(Z_GRILLE - 1, Z_MIN - 1, -1):
                cur = _mean2(cur)
                pyr.ecrire("dem", z, i0 >> (Z_GRILLE - z), cur)
    finally:
        src.fermer()
    px10 = merc.pixel(Z_MER)
    ox, oy, _, _ = grille.niveau(Z_MER)
    tr10 = from_origin(-merc.DEMI_MONDE + ox * px10, merc.DEMI_MONDE - oy * px10, px10, px10)
    dedans = rasterize([(emprise_merc, 1)], out_shape=(h10, w10), transform=tr10, fill=0, all_touched=True,
                       dtype="uint8").astype(bool)
    mer10 = mer_globale(bas, graine, dedans)
    del bas, graine, dedans
    journal("mer : grille globale %d x %d pixels du zoom %d, %d pixels de mer" % (w10, h10, Z_MER, int(mer10.sum())))
    for i0 in range(0, grille.haut, BANDE_LIGNES):
        i1 = min(grille.haut, i0 + BANDE_LIGNES)
        fin = pyr.lire("dem", Z_GRILLE, grille.iy0 + i0, grille.ix0, i1 - i0, grille.larg)
        m = np.repeat(np.repeat(mer10[i0 // f10:i1 // f10], f10, axis=0), f10, axis=1)
        cur = ((fin <= SEUIL_MER) & m).astype("float32")
        pyr.ecrire("mer", Z_GRILLE, i0, np.rint(cur * 255.0))
        for z in range(Z_GRILLE - 1, Z_MIN - 1, -1):
            cur = _mean2(cur)
            pyr.ecrire("mer", z, i0 >> (Z_GRILLE - z), np.rint(cur * 255.0))
    return pyr


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


# ---------------------------------------------------------------------- estompage
def estompage(z, pas, azimut=ESTOMPAGE["azimut"], hauteur=ESTOMPAGE["hauteur"], exageration=ESTOMPAGE["exageration"]):
    """L'estompage C de l'esquisse de référence, pas en mètres au sol ; rend hs dans [0, 1]."""
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
