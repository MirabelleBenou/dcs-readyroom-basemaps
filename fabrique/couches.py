"""Les couches de fond.pmtiles tirées des entités d'OpenStreetMap, bande par bande : simplification, trait tremblé,
coupe à l'emprise. Chaque entité se traite ENTIÈRE (le trait est semé par son identifiant), puis se découpe aux tuiles :
une route qui traverse deux blocs a la même géométrie des deux côtés.

Les couches calculées du relief et de la mer sont dans `derive.py`, la grille dans `grille.py`.
Les noms des couches sont fixes (règle 8).
"""
import math

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.validation import make_valid

from . import bandes, bruit

COUCHES = ("mer", "rivage", "eau", "cours_eau", "courbes", "forets", "localites", "lieux",
           "routes", "rail", "pistes", "frontieres", "grille")
# les couches d'entités du magasin, dans l'ordre où on les traite
ENTITES = ("eau", "cours_eau", "forets", "localites", "lieux", "routes", "rail", "pistes", "frontieres")


def _parties_lignes(g):
    if g is None or g.is_empty:
        return []
    if g.geom_type == "LineString":
        return [g]
    if g.geom_type in ("MultiLineString", "GeometryCollection"):
        out = []
        for x in g.geoms:
            out.extend(_parties_lignes(x))
        return out
    return []


def _parties_polygones(g):
    if g is None or g.is_empty:
        return []
    if g.geom_type == "Polygon":
        return [g]
    if g.geom_type in ("MultiPolygon", "GeometryCollection"):
        out = []
        for x in g.geoms:
            out.extend(_parties_polygones(x))
        return out
    return []


def tremble_ligne(l, b, sel, simplifier=True):
    """Simplifie (Douglas-Peucker au demi-pixel de la bande), puis fait trembler dans la géométrie (semé par `sel`)."""
    if simplifier:
        l = l.simplify(bandes.tolerance(b), preserve_topology=False)
    c = np.asarray(l.coords)
    if len(c) < 2:
        return None
    ferme = bruit.est_ferme(c)
    seed = bruit.graine(sel, b[0])
    out = bruit.tremble(c, seed, bandes.amplitude(b), bandes.longueur_onde(b), ferme=ferme)
    return LineString(out)


def tremble_polygone(p, b, sel):
    """Chaque anneau tremble ; les contours d'un même objet se ferment après déplacement."""
    p = p.simplify(bandes.tolerance(b), preserve_topology=True)
    if p.is_empty:
        return []
    if p.geom_type != "Polygon":
        return [make_valid(x) for x in _parties_polygones(p)]
    anneaux = []
    for i, r in enumerate([p.exterior] + list(p.interiors)):
        c = np.asarray(r.coords)
        seed = bruit.graine(sel, b[0], i)
        anneaux.append(bruit.tremble(c, seed, bandes.amplitude(b), bandes.longueur_onde(b), ferme=True))
    q = Polygon(anneaux[0], anneaux[1:])
    if not q.is_valid:
        q = make_valid(q)
    return _parties_polygones(q)


def dedans(g, emprise):
    """La géométrie coupée à l'emprise (None si rien n'y reste)."""
    if g is None or g.is_empty or not shapely.intersects(g, emprise):
        return None
    if shapely.contains(emprise, g):
        return g
    r = g.intersection(emprise)
    return None if r.is_empty else r


def arbres(poly, b, boite, emprise):
    """Les arbres d'un polygone de forêt à la bande : des points semés par cellule (graine de la cellule, jamais de
    l'ordre de lecture), dans le polygone et dans l'emprise. `boite` borne le travail à la zone d'un bloc (None : tout).
    Pas de maillage sur la boîte d'une union : le polygone seul, par bandes de lignes."""
    s = bandes.ESPACE_ARBRES_PX * bandes.px(b)
    sel = bruit.graine("arbres", b[0]) & 0xFFFFFFFF
    x0, y0, x1, y1 = poly.bounds
    ex = emprise.bounds
    x0, y0, x1, y1 = max(x0, ex[0]), max(y0, ex[1]), min(x1, ex[2]), min(y1, ex[3])
    if boite is not None:
        x0, y0, x1, y1 = max(x0, boite[0]), max(y0, boite[1]), min(x1, boite[2]), min(y1, boite[3])
    if x1 < x0 or y1 < y0:
        return []
    i0, i1 = int(math.floor(x0 / s)), int(math.floor(x1 / s))
    j0, j1 = int(math.floor(y0 / s)), int(math.floor(y1 / s))
    pas_lignes = max(1, int(2e6 // max(1, i1 - i0 + 1)))
    pts = []
    for ja in range(j0, j1 + 1, pas_lignes):
        jb = min(j1, ja + pas_lignes - 1)
        ix, iy = np.meshgrid(np.arange(i0, i1 + 1), np.arange(ja, jb + 1))
        ix, iy = ix.ravel(), iy.ravel()
        u, v = bruit.hasard_cellules(ix, iy, sel)
        x = (ix + 0.15 + 0.7 * u) * s
        y = (iy + 0.15 + 0.7 * v) * s
        m = shapely.contains_xy(poly, x, y) & shapely.contains_xy(emprise, x, y)
        pts.extend(zip(x[m].tolist(), y[m].tolist()))
    return pts


def geometries(couche, ident, props, g, b, emprise, boite=None):
    """Les morceaux d'une entité à la bande : [(couche de sortie, géométrie Mercator, propriétés)] ; entité entière,
    coupée à l'emprise."""
    px = bandes.px(b)
    out = []
    if couche == "eau" or couche == "localites":
        for p in _parties_polygones(g):
            if p.area < bandes.SEUIL_EAU_PX2 * px * px:
                continue
            for q in tremble_polygone(p, b, ident):
                for r in _parties_polygones(dedans(q, emprise)):
                    out.append((couche, r, {}))
    elif couche in ("cours_eau", "routes", "rail", "frontieres"):
        if couche == "cours_eau" and props["classe"] != "river" and b[0] < 9:
            return out
        if couche == "routes" and props["lien"] and b[0] < 11:
            return out                       # les bretelles disparaissent sous la bande 11-12
        if g.length < bandes.SEUIL_LIGNE_PX * px:
            return out
        t = tremble_ligne(g, b, ident)
        if t is None:
            return out
        sortie = {"routes": {"classe": props.get("classe")}, "cours_eau": {"classe": props.get("classe")}}.get(couche, {})
        for l in _parties_lignes(dedans(t, emprise)):
            out.append((couche, l, dict(sortie)))
    elif couche == "pistes":
        # droites, jamais tremblées
        for l in _parties_lignes(dedans(g, emprise)):
            out.append(("pistes", l, {}))
    elif couche == "lieux":
        if shapely.contains(emprise, g):
            out.append(("lieux", g, {"place": props["place"], "nom": props["nom"]}))
    elif couche == "forets":
        for x, y in arbres(g, b, boite, emprise):
            out.append(("forets", Point(x, y), {}))
    return out
