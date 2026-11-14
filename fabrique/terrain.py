"""Le relief : échantillonné une fois sur la grille maîtresse (pixels du zoom 12), rangé sur disque en pyramide.

Rien ne dépend d'une fenêtre : chaque pixel de la grille se calcule depuis ses seules coordonnées (interpolation
bilinéaire des dalles, séparable, à coordonnées en double précision). Les blocs de la fabrique lisent ensuite des
fenêtres de cette pyramide, avec une marge ; lire une fenêtre plus grande ou plus petite ne change aucune valeur.

La mer se juge UNE fois, sur une grille globale au pixel du zoom 10 : les pixels d'altitude <= 0,5 m reliés au bord
de l'emprise, à condition que leur ensemble contienne au moins un pixel semé entre -0,5 et +0,5 m (une dépression
- mer Morte, Tibériade - n'est jamais de la mer) ; cette grille se dilate d'un pixel. Au pixel fin, la mer est
altitude <= 0,5 m ET pixel de mer de la grille globale.

La grille globale n'existe jamais en entier à un octet par pixel : `MerParBandes` la range tassée par bits (un bit par
pixel) et l'étiquette par bandes de lignes ; sa mémoire ne grandit avec la surface que d'un bit par pixel.
"""
import math
import os

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.windows import Window
from scipy import ndimage
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
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
            c0 = int(min(ds.width - 1, max(0, math.floor(cc.min()))))
            c1 = int(min(ds.width - 1, math.floor(cc.max()) + 1))
            r0 = int(min(ds.height - 1, max(0, math.floor(rr.min()))))
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


TABLE_BITS = np.array([bin(i).count("1") for i in range(256)], dtype=np.int64)


class MerParBandes:
    """La mer de la grille globale (pixels du zoom 10), calculée par bandes de lignes. Même définition que la base :
    les ensembles de pixels `bas` (altitude <= 0,5 m quelque part dans le pixel) en connexité 4, reliés au bord de
    l'emprise (`dedans & ~binary_erosion(dedans)`, `border_value=0`) ET portant au moins une `graine` (entre -0,5 et
    +0,5 m) ; puis dilatés d'un pixel (3 x 3).

    Usage : `ajouter(i0, bas, graine)` pour chaque bande, dans l'ordre et sans trou ; `resoudre()` ; puis `bande(i0, i1)`.
    `dedans(i0, i1)` rend les lignes [i0, i1) de l'emprise (booléens) : la grille de l'emprise ne se tient jamais en entier.

    La mémoire : `bas`, `graine` et la mer vivent tassées par bits (un bit par pixel), jamais en un octet par pixel ni en
    étiquettes sur la grille entière. L'étiquetage se fait bande par bande (`ndimage.label`) ; seuls restent les
    étiquettes provisoires (une par composante de bande), leurs équivalences d'une bande à la suivante (les pixels
    voisins de part et d'autre de la limite) et deux drapeaux par étiquette (touche le bord ; porte une graine). Les
    équivalences se résolvent d'un coup (`connected_components`), puis une seconde passe réétiquette chaque bande
    (l'étiquetage d'une bande est le même à chaque fois) et ne garde que les composantes retenues."""

    def __init__(self, haut, larg, dedans):
        self.haut, self.larg = haut, larg
        self._dedans = dedans
        self._octets = (larg + 7) // 8
        self._bas = np.zeros((haut, self._octets), dtype=np.uint8)
        self._graine = np.zeros((haut, self._octets), dtype=np.uint8)
        self._mer = None
        self._bandes = []
        self._suivante = 0

    # ---- les grilles tassées
    def _lire(self, tab, i0, i1):
        return np.unpackbits(tab[i0:i1], axis=1, count=self.larg).astype(bool)

    @staticmethod
    def _ecrire(tab, i0, bande):
        tab[i0:i0 + bande.shape[0]] = np.packbits(bande, axis=1)

    def ajouter(self, i0, bas, graine):
        """La bande de lignes [i0, i0 + len(bas)) : `bas` et `graine` en booléens (hauteur x largeur)."""
        if i0 != self._suivante:
            raise ValueError("les bandes se donnent dans l'ordre et sans trou : attendu la ligne %d, reçu %d"
                             % (self._suivante, i0))
        n = bas.shape[0]
        if bas.shape != graine.shape or bas.shape[1] != self.larg or i0 + n > self.haut or n < 1:
            raise ValueError("bande hors de la grille ou de formes différentes : %r %r" % (bas.shape, graine.shape))
        self._ecrire(self._bas, i0, np.asarray(bas, dtype=bool))
        self._ecrire(self._graine, i0, np.asarray(graine, dtype=bool))
        self._bandes.append((i0, i0 + n))
        self._suivante = i0 + n

    def _bord(self, i0, i1):
        """`dedans & ~binary_erosion(dedans, border_value=0)` sur les lignes [i0, i1) : une ligne de contexte de chaque côté
        (hors de la grille : faux, comme `border_value=0`)."""
        a, b = max(0, i0 - 1), min(self.haut, i1 + 1)
        d = np.zeros((i1 - i0 + 2, self.larg), dtype=bool)
        d[a - (i0 - 1):b - (i0 - 1)] = self._dedans(a, b)
        return (d & ~ndimage.binary_erosion(d, border_value=0))[1:-1]

    def resoudre(self, progres=None):
        """Étiquette, résout les équivalences, retient les composantes de mer, dilate. Libère `bas` et `graine`."""
        if self._suivante != self.haut:
            raise ValueError("la grille n'est pas complète : %d lignes sur %d" % (self._suivante, self.haut))
        nb = len(self._bandes)
        # ---- première passe : étiquettes provisoires, équivalences entre bandes, drapeaux
        decalages, aretes, ids_bord, ids_graine = [], [], [], []
        total, derniere = 0, None
        for k, (i0, i1) in enumerate(self._bandes):
            bas = self._lire(self._bas, i0, i1)
            lab, n = ndimage.label(bas)
            decalages.append(total)
            premiere = lab[0].astype(np.int64)
            premiere[premiere > 0] += total - 1
            premiere[lab[0] == 0] = -1
            if derniere is not None:
                ok = (derniere >= 0) & (premiere >= 0)
                if ok.any():
                    aretes.append(np.unique((derniere[ok] << 32) | premiere[ok]))
            ib = np.unique(lab[self._bord(i0, i1) & bas])
            ids_bord.append(ib[ib > 0].astype(np.int64) + (total - 1))
            ig = np.unique(lab[self._lire(self._graine, i0, i1) & bas])
            ids_graine.append(ig[ig > 0].astype(np.int64) + (total - 1))
            dern = lab[-1].astype(np.int64)
            dern[dern > 0] += total - 1
            dern[lab[-1] == 0] = -1
            derniere = dern
            total += n
            del lab, bas
            if progres is not None:
                progres("mer : étiquetage", k + 1, nb)
        self._graine = None
        if total == 0:
            self._bas = None
            self._mer = np.zeros((self.haut, self._octets), dtype=np.uint8)
            return
        if total >= 1 << 31:
            raise ValueError("trop d'étiquettes provisoires pour la mer (%d)" % total)
        if aretes:
            e = np.concatenate(aretes)
            _, comp = connected_components(
                coo_matrix((np.ones(len(e), dtype=np.int8), (e >> 32, e & 0xFFFFFFFF)), shape=(total, total)),
                directed=False)
        else:
            comp = np.arange(total)
        ncomp = int(comp.max()) + 1
        bord_c = np.zeros(ncomp, dtype=bool)
        graine_c = np.zeros(ncomp, dtype=bool)
        bord_c[comp[np.concatenate(ids_bord)]] = True
        graine_c[comp[np.concatenate(ids_graine)]] = True
        garde = (bord_c & graine_c)[comp]
        del comp, bord_c, graine_c, ids_bord, ids_graine, aretes
        # ---- seconde passe : la mer avant dilatation
        mer0 = np.zeros((self.haut, self._octets), dtype=np.uint8)
        for k, ((i0, i1), dec) in enumerate(zip(self._bandes, decalages)):
            lab, n = ndimage.label(self._lire(self._bas, i0, i1))
            table = np.zeros(n + 1, dtype=bool)
            table[1:] = garde[dec:dec + n]
            self._ecrire(mer0, i0, table[lab])
            del lab
            if progres is not None:
                progres("mer : composantes retenues", k + 1, nb)
        self._bas = None
        del garde
        # ---- dilatation 3 x 3, par bandes avec une ligne de contexte de chaque côté
        self._mer = np.zeros((self.haut, self._octets), dtype=np.uint8)
        for k, (i0, i1) in enumerate(self._bandes):
            a, b = max(0, i0 - 1), min(self.haut, i1 + 1)
            d = ndimage.binary_dilation(self._lire(mer0, a, b), structure=np.ones((3, 3), dtype=bool))
            self._ecrire(self._mer, i0, d[i0 - a:i0 - a + (i1 - i0)])
            if progres is not None:
                progres("mer : dilatation", k + 1, nb)

    def bande(self, i0, i1):
        """Les lignes [i0, i1) de la mer (booléens)."""
        return self._lire(self._mer, i0, i1)

    def compte(self):
        """Le nombre de pixels de mer."""
        return int(sum(TABLE_BITS[self._mer[a:a + 256]].sum() for a in range(0, self.haut, 256)))


def mer_globale(bas, graine, dedans, hauteur_bande=64):
    """La mer de la grille globale (pixels du zoom 10) : les ensembles de pixels `bas` (altitude <= 0,5 m quelque part
    dans le pixel) reliés au bord de l'emprise ET portant au moins une `graine` (entre -0,5 et +0,5 m) ; puis dilatée
    d'un pixel. Sur des grilles entières (essais, petites emprises) : le calcul est celui de `MerParBandes`, par bandes
    de `hauteur_bande` lignes."""
    haut, larg = bas.shape
    mer = MerParBandes(haut, larg, lambda a, b: dedans[a:b])
    for i0 in range(0, haut, hauteur_bande):
        i1 = min(haut, i0 + hauteur_bande)
        mer.ajouter(i0, bas[i0:i1], graine[i0:i1])
    mer.resoudre()
    return mer.bande(0, haut)


def dedans_par_bande(grille, emprise_merc):
    """Une fonction `(i0, i1) -> booléens` : les lignes [i0, i1) de l'emprise rastérisée au pixel du zoom 10 (all_touched),
    fenêtre de la transformation de la grille : la grille entière de l'emprise ne se tient jamais en mémoire.

    Au pixel près de la rastérisation de la grille entière, hors d'une arête d'emprise qui passe exactement par un coin de
    pixel du zoom 10 : l'origine de la fenêtre se calcule par `(oy + i0) * px10`, et GDAL peut alors trancher autrement un
    contact au bord exact d'un pixel. Aucune emprise réaliste en degrés ne l'a montré, et les empreintes de la zone d'essai
    et de la grande boîte sont identiques à l'octet."""
    f10 = 1 << (Z_GRILLE - Z_MER)
    w10 = grille.larg // f10
    px10 = merc.pixel(Z_MER)
    ox, oy, _, _ = grille.niveau(Z_MER)

    def lignes(i0, i1):
        tr = from_origin(-merc.DEMI_MONDE + ox * px10, merc.DEMI_MONDE - (oy + i0) * px10, px10, px10)
        return rasterize([(emprise_merc, 1)], out_shape=(i1 - i0, w10), transform=tr, fill=0, all_touched=True,
                         dtype="uint8").astype(bool)
    return lignes


def construire(grille, entrees, emprise_merc, dossier, journal=print, progres=None):
    """Échantillonne le relief et range la pyramide du relief et de la mer dans `dossier`. Rend la `Pyramide`.
    `progres(étape, n, total)`, s'il est donné, est appelé à chaque bande."""
    def avancer(nom, n, total):
        if progres is not None:
            progres(nom, n, total)

    pyr = Pyramide(grille, dossier)
    pyr.creer("dem")
    pyr.creer("mer")
    src = SourceDEM(entrees)
    f10 = 1 << (Z_GRILLE - Z_MER)
    h10, w10 = grille.haut // f10, grille.larg // f10
    mer10 = MerParBandes(h10, w10, dedans_par_bande(grille, emprise_merc))
    nb = (grille.haut + BANDE_LIGNES - 1) // BANDE_LIGNES
    try:
        for k, i0 in enumerate(range(0, grille.haut, BANDE_LIGNES)):
            i1 = min(grille.haut, i0 + BANDE_LIGNES)
            fin = src.lignes(grille, i0, i1)
            cur = fin
            pyr.ecrire("dem", Z_GRILLE, i0, cur)
            q = fin.reshape((i1 - i0) // f10, f10, w10, f10)
            mer10.ajouter(i0 // f10, q.min(axis=(1, 3)) <= SEUIL_MER,
                          ((q >= -SEMENCE_MER) & (q <= SEMENCE_MER)).any(axis=(1, 3)))
            for z in range(Z_GRILLE - 1, Z_MIN - 1, -1):
                cur = _mean2(cur)
                pyr.ecrire("dem", z, i0 >> (Z_GRILLE - z), cur)
            avancer("relief : échantillonnage par bandes", k + 1, nb)
    finally:
        src.fermer()
    mer10.resoudre(progres)
    journal("mer : grille globale %d x %d pixels du zoom %d, %d pixels de mer" % (w10, h10, Z_MER, mer10.compte()))
    for k, i0 in enumerate(range(0, grille.haut, BANDE_LIGNES)):
        i1 = min(grille.haut, i0 + BANDE_LIGNES)
        fin = pyr.lire("dem", Z_GRILLE, grille.iy0 + i0, grille.ix0, i1 - i0, grille.larg)
        m = np.repeat(np.repeat(mer10.bande(i0 // f10, i1 // f10), f10, axis=0), f10, axis=1)
        cur = ((fin <= SEUIL_MER) & m).astype("float32")
        pyr.ecrire("mer", Z_GRILLE, i0, np.rint(cur * 255.0))
        for z in range(Z_GRILLE - 1, Z_MIN - 1, -1):
            cur = _mean2(cur)
            pyr.ecrire("mer", z, i0 >> (Z_GRILLE - z), np.rint(cur * 255.0))
        avancer("mer : pyramide par bandes", k + 1, nb)
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
