"""Les couches calculées du relief et de la mer (mer, rivage, courbes), par cellule.

Une cellule est une tuile du zoom `zc` de la bande ; le relief se lit au niveau `nv` de la pyramide (deux zooms de plus
que le haut de la bande, au mieux le zoom 12), sur une fenêtre élargie d'une marge telle que lissage, distance à la côte
et contexte du trait ne voient jamais le bord de la fenêtre. Rien ne dépend donc du bloc qui demande la cellule :
la cellule (bande, x, y) donne les mêmes morceaux où qu'elle soit calculée.

Le trait tremblé de ces lignes est fonction de la position (`bruit.tremble_position`) : deux cellules voisines se
recollent exactement. Chaque morceau est ensuite coupé à la cellule et à l'emprise ; les lignes sont allégées
(Douglas-Peucker à `TOLERANCE_APRES_PX`) APRÈS la coupe, donc morceau par morceau, extrémités conservées.
"""
import math

import contourpy
import numpy as np
import shapely
from scipy import ndimage
from shapely.geometry import LineString, Polygon, box
from shapely.validation import make_valid

from . import bandes, bruit, merc
from .terrain import SIGMA_COTE, SIGMA_COURBES, SIGMA_RIVAGE, Z_GRILLE, polygones_depuis_remplissage

# bande -> (zoom de la cellule, niveau de la pyramide lu)
PARAMS = {(5, 6): (6, 8), (7, 8): (8, 10), (9, 10): (10, 12), (11, 12): (12, 12), (13, 14): (12, 12)}
TOLERANCE_APRES_PX = 0.25      # allègement des lignes, en pixels du zoom médian de la bande, après la coupe à la cellule
MARGE_EXTRA = 8                # pixels du niveau, en plus de la distance à la côte et du lissage de la distance


def cellule_de(b):
    return PARAMS[b]


def bandes_de_la_cellule(zc, nv):
    return [b for b, (z, n) in PARAMS.items() if (z, n) == (zc, nv)]


def distance_max(zc, nv):
    return max(bandes.distances_rivage(b)[-1] for b in bandes_de_la_cellule(zc, nv))


def _lat(y):
    return float(merc.merc_vers_lonlat(0.0, y)[1])


class Champs:
    """Les champs d'une cellule : mer lissée, distance à la côte lissée, relief lissé, sur la fenêtre."""

    def __init__(self, pyr, zc, nv, x, y):
        self.zc, self.nv, self.x, self.y = zc, nv, x, y
        n = 512 << (nv - zc)
        xmin, ymin, xmax, ymax = merc.bornes_tuile(zc, x, y)
        lat_haut = max(abs(_lat(ymin)), abs(_lat(ymax)))
        pas = merc.pixel(nv)
        self.pas = pas
        # la marge : distance à la côte la plus longue de la cellule, vue à la latitude où le pixel vaut le moins de mètres
        # au sol, plus le lissage de la distance, plus un peu
        m = int(math.ceil(distance_max(zc, nv) / (pas * math.cos(math.radians(lat_haut))))) \
            + int(math.ceil(4 * SIGMA_RIVAGE)) + MARGE_EXTRA
        self.marge = m
        self.sol = math.cos(math.radians(_lat((ymin + ymax) / 2)))        # le facteur d'échelle de la tuile
        taille = n + 2 * m
        gx0, gy0 = x * n - m, y * n - m
        self.xs = -merc.DEMI_MONDE + (gx0 + np.arange(taille) + 0.5) * pas
        self.ys = merc.DEMI_MONDE - (gy0 + np.arange(taille) + 0.5) * pas
        dem = pyr.lire("dem", nv, gy0, gx0, taille, taille)
        mer = pyr.lire("mer", nv, gy0, gx0, taille, taille)
        en_mer = mer >= 128
        self.champ_mer = ndimage.gaussian_filter(mer.astype("float32") / 255.0, SIGMA_COTE, mode="nearest")
        if en_mer.all() or not en_mer.any():
            # pas de côte dans la fenêtre : aucune ligne de rivage ; (tout en mer : aucune terre d'où mesurer)
            self.distance = np.full(en_mer.shape, 1e12 if en_mer.all() else -1.0, dtype="float32")
        else:
            d = ndimage.distance_transform_edt(en_mer) * (pas * self.sol)
            self.distance = ndimage.gaussian_filter(d.astype("float32"), SIGMA_RIVAGE, mode="nearest")
        z = np.where(en_mer, -1.0, dem).astype("float32")
        sigma = SIGMA_COURBES * 2.0 ** (nv - Z_GRILLE)
        self.terre = ndimage.gaussian_filter(z, sigma, mode="nearest") if sigma >= 0.4 else z
        self.altitude_max = float(self.terre.max())
        self.a_de_la_mer = bool(en_mer.any())

    def lignes(self, champ, niveau):
        gen = contourpy.contour_generator(self.xs, self.ys, champ, line_type=contourpy.LineType.Separate)
        return [np.asarray(l) for l in gen.lines(float(niveau))]

    def polygones_mer(self):
        gen = contourpy.contour_generator(self.xs, self.ys, self.champ_mer, fill_type=contourpy.FillType.OuterOffset)
        return polygones_depuis_remplissage(gen, 0.5, 2.0)


def _parties(g, types):
    if g is None or g.is_empty:
        return []
    if g.geom_type in types:
        return [g]
    if hasattr(g, "geoms"):
        out = []
        for x in g.geoms:
            out.extend(_parties(x, types))
        return out
    return []


def _sel(*parts):
    return bruit.graine(*parts) & 0xFFFFFFFF


def _tremble(coords, b, sel, ferme):
    return bruit.tremble_position(coords, bandes.amplitude(b), bandes.longueur_onde(b), sel, ferme=ferme)


def _lignes_de(champs, champ, niveau, b, nom_sel, props, zone, cle, couche, seuil, sortie):
    """Les lignes du niveau, tremblées par la position, coupées à la zone (cellule x emprise), allégées."""
    tol = TOLERANCE_APRES_PX * bandes.px(b)
    lst = [l for l in champs.lignes(champ, niveau) if len(l) >= 2]
    lst.sort(key=lambda l: (round(float(l[0][0])), round(float(l[0][1])), len(l)))
    sel = _sel(nom_sel, b[0])
    k = 0
    for c in lst:
        if LineString(c).length < seuil:
            continue
        t = _tremble(c, b, sel, bruit.est_ferme(c))
        for g in _parties(LineString(t).intersection(zone), ("LineString",)):
            g = g.simplify(tol, preserve_topology=False)
            if g.is_empty or len(g.coords) < 2:
                continue
            sortie.append((couche, "%s|%06d" % (cle, k), g, dict(props)))
            k += 1


def fragments(champs, b, zone, cle_cellule):
    """Les morceaux de la bande pour la cellule : [(couche, clé de tri, géométrie Mercator, propriétés)].
    `zone` : la cellule coupée à l'emprise (en Mercator)."""
    out = []
    px = bandes.px(b)
    seuil = bandes.SEUIL_LIGNE_PX * px
    if champs.a_de_la_mer:
        # la mer : les anneaux du remplissage tremblent par la position, puis se coupent à la zone
        k = 0
        sel = _sel("mer", b[0])
        polys = champs.polygones_mer()
        polys.sort(key=lambda p: (round(p.bounds[0]), round(p.bounds[1]), round(p.area)))
        for p in polys:
            for q in _parties(p, ("Polygon",)):
                anneaux = [_tremble(np.asarray(r.coords), b, sel, True) for r in [q.exterior] + list(q.interiors)]
                if any(len(a) < 4 for a in anneaux):
                    continue
                r = Polygon(anneaux[0], anneaux[1:])
                if not r.is_valid:
                    r = make_valid(r)
                for g in _parties(r.intersection(zone), ("Polygon",)):
                    out.append(("mer", "%s|mer|%06d" % (cle_cellule, k), g, {}))
                    k += 1
        # le trait de côte : la ligne de la même mer, au même tremblement (donc au bord exact du polygone)
        _lignes_de(champs, champs.champ_mer, 0.5, b, "mer", {"rang": 0}, zone, cle_cellule + "|cote", "rivage", seuil, out)
        for rang, dist in enumerate(bandes.distances_rivage(b), start=1):
            _lignes_de(champs, champs.distance, dist, b, "rivage%d" % rang, {"rang": rang}, zone,
                       cle_cellule + "|rivage%d" % rang, "rivage", seuil, out)
    for n in bandes.niveaux_courbes(b, champs.altitude_max):
        _lignes_de(champs, champs.terre, n, b, "courbe%d" % n, {"alt": int(n), "maitresse": 1 if n % 1000 == 0 else 0},
                   zone, cle_cellule + "|courbe%05d" % n, "courbes", seuil, out)
    return out


def zone_de_cellule(zc, x, y, emprise):
    """La cellule coupée à l'emprise ; None si elle ne la touche pas."""
    b = box(*merc.bornes_tuile(zc, x, y))
    if not shapely.intersects(emprise, b):
        return None
    if shapely.contains(emprise, b):
        return b
    z = emprise.intersection(b)
    return None if z.is_empty else z
