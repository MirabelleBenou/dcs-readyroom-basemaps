"""Les couches de fond.pmtiles, bande par bande : simplification, trait tremblé, découpe à l'emprise.

Une couche = une liste de (géométrie Mercator, propriétés). Les noms des couches sont fixes (règle 8).
"""
import math

import numpy as np
import shapely
from shapely.geometry import LineString, MultiLineString, Point, Polygon
from shapely.ops import linemerge, unary_union
from shapely.validation import make_valid

from . import bandes, bruit, grille as mgrs, merc

COUCHES = ("mer", "rivage", "eau", "cours_eau", "courbes", "forets", "localites", "lieux",
           "routes", "rail", "pistes", "frontieres", "grille")
SEUIL_LOCALITE_M2 = 1.0e6      # plus d'1 km2 au sol
SEUIL_RUISSEAU_M = 2500.0       # un ruisseau ou un canal de plus de 2,5 km
SEUIL_EAU_PX2 = 20.0            # un lac plus petit que ça, à la bande, ne se dessine pas
SEUIL_LIGNE_PX = 4.0            # une ligne plus courte que ça, à la bande, ne se dessine pas


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


def _tourne(l):
    """Oriente une ligne de façon canonique (début <= fin), pour une graine indépendante du sens de lecture."""
    c = np.asarray(l.coords)
    if (round(c[0][0]), round(c[0][1])) > (round(c[-1][0]), round(c[-1][1])):
        return LineString(c[::-1])
    return l


def fusionner(lignes):
    """Fusionne les lignes qui se touchent par leurs extrémités ; ordre et sens stables."""
    if not lignes:
        return []
    m = linemerge(MultiLineString(lignes)) if len(lignes) > 1 else lignes[0]
    out = [_tourne(x) for x in _parties_lignes(m)]
    out.sort(key=lambda l: (round(l.coords[0][0]), round(l.coords[0][1]), round(l.length)))
    return out


def tremble_ligne(l, b, sel, simplifier=True):
    """Simplifie (Douglas-Peucker au demi-pixel de la bande), puis fait trembler dans la géométrie."""
    if simplifier:
        l = l.simplify(bandes.tolerance(b), preserve_topology=False)
    c = np.asarray(l.coords)
    if len(c) < 2:
        return None
    ferme = len(c) > 3 and np.allclose(c[0], c[-1])
    seed = bruit.graine(sel, b[0], int(round(c[0][0])), int(round(c[0][1])))
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
        seed = bruit.graine(sel, b[0], i, int(round(c[0][0])), int(round(c[0][1])))
        anneaux.append(bruit.tremble(c, seed, bandes.amplitude(b), bandes.longueur_onde(b), ferme=True))
    q = Polygon(anneaux[0], anneaux[1:])
    if not q.is_valid:
        q = make_valid(q)
    return _parties_polygones(q)


class Constructeur:
    """Prépare ce qui ne dépend pas de la bande (unions, mer), puis rend les couches d'une bande."""

    def __init__(self, donnees, terrain, emprise_merc, emprise_wgs, lat_centre):
        self.d = donnees
        self.t = terrain
        self.emprise = emprise_merc
        self.emprise_wgs = emprise_wgs
        self.sol = math.cos(math.radians(lat_centre))
        shapely.prepare(self.emprise)
        self.mer = terrain.polygone_mer()
        # forêts : une union des polygones, avec leurs graines stables par cellule
        fp = [make_valid(g) if not g.is_valid else g for _, g in donnees.forets]
        self.forets = unary_union(fp) if fp else None
        # localités : les zones résidentielles unies, plus d'1 km2 au sol
        lp = [make_valid(g) if not g.is_valid else g for _, g in donnees.localites]
        loc = []
        if lp:
            u = unary_union(lp)
            for p in _parties_polygones(u):
                lat = merc.merc_vers_lonlat(p.centroid.x, p.centroid.y)[1]
                if p.area * merc.echelle_sol(float(lat)) ** 2 > SEUIL_LOCALITE_M2:
                    loc.append(p)
        loc.sort(key=lambda p: (round(p.centroid.x), round(p.centroid.y)))
        self.localites = loc

    # ------------------------------------------------------------------ utilitaires
    def _dedans(self, geoms):
        out = []
        for g in geoms:
            if g is None or g.is_empty:
                continue
            if not shapely.intersects(g, self.emprise):
                continue
            out.append(g.intersection(self.emprise))
        return out

    def _lignes_tremblees(self, lignes, b, sel, props):
        res = []
        for l in lignes:
            if l.length < SEUIL_LIGNE_PX * bandes.px(b):
                continue
            t = tremble_ligne(l, b, sel)
            if t is None:
                continue
            for g in self._dedans([t]):
                for p in _parties_lignes(g):
                    res.append((p, dict(props)))
        return res

    # ------------------------------------------------------------------ une bande
    def bande(self, b):
        c = {n: [] for n in COUCHES}
        px = bandes.px(b)

        # mer : polygone simplifié et tremblé ; le trait de côte est son bord, dans l'emprise
        if self.mer is not None:
            morceaux = []
            for p in _parties_polygones(self.mer):
                morceaux.extend(tremble_polygone(p, b, "mer"))
            mer_b = unary_union(morceaux) if morceaux else None
            if mer_b is not None and not mer_b.is_empty:
                dans = mer_b.intersection(self.emprise)
                for p in _parties_polygones(dans):
                    c["mer"].append((p, {}))
                cote = mer_b.boundary.intersection(self.emprise)
                for l in _parties_lignes(cote):
                    c["rivage"].append((l, {"rang": 0}))
        # rivage : lignes à distance croissante de la côte, en mer
        for k, dist in enumerate(bandes.distances_rivage(b), start=1):
            lignes = [LineString(x) for x in self.t.lignes_rivage(dist) if len(x) >= 2]
            lignes = [l for l in lignes if l.length >= SEUIL_LIGNE_PX * px]
            lignes.sort(key=lambda l: (round(l.coords[0][0]), round(l.coords[0][1])))
            c["rivage"].extend(self._lignes_tremblees(lignes, b, "rivage%d" % k, {"rang": k}))

        # courbes : tirées du relief
        niv = bandes.niveaux_courbes(b, self.t.altitude_max())
        for n, lst in self.t.courbes(niv).items():
            lignes = [LineString(x) for x in lst if len(x) >= 2]
            lignes.sort(key=lambda l: (round(l.coords[0][0]), round(l.coords[0][1])))
            props = {"alt": int(n), "maitresse": 1 if n % 1000 == 0 else 0}
            c["courbes"].extend(self._lignes_tremblees(lignes, b, "courbe%d" % n, props))

        # eau : lacs et réservoirs (polygones) ; cours d'eau
        for ident, g in self.d.eau:
            for p in _parties_polygones(g):
                if p.area < SEUIL_EAU_PX2 * px * px:
                    continue
                for q in tremble_polygone(p, b, ident):
                    for r in self._dedans([q]):
                        for pp in _parties_polygones(r):
                            c["eau"].append((pp, {}))
        riv, ruis = [], []
        for ident, classe, l in self.d.cours_eau:
            if classe == "river":
                riv.append(l)
            elif b[0] >= 9:
                lat = merc.merc_vers_lonlat(*np.asarray(l.coords)[0])[1]
                if l.length * merc.echelle_sol(float(lat)) > SEUIL_RUISSEAU_M:
                    ruis.append(l)
        c["cours_eau"].extend(self._lignes_tremblees(fusionner(riv), b, "riviere", {"classe": "river"}))
        c["cours_eau"].extend(self._lignes_tremblees(fusionner(ruis), b, "ruisseau", {"classe": "stream"}))

        # forêts : des points d'arbres, jamais d'aplat
        for pt in self._arbres(b):
            c["forets"].append((pt, {}))

        # localités : zones résidentielles unies, hachurées
        for i, p in enumerate(self.localites):
            if p.area < SEUIL_EAU_PX2 * px * px:
                continue
            for q in tremble_polygone(p, b, "loc%d_%d" % (round(p.centroid.x), round(p.centroid.y))):
                for r in self._dedans([q]):
                    for pp in _parties_polygones(r):
                        c["localites"].append((pp, {}))

        # lieux : villes et bourgs, jamais de village
        for l in self.d.lieux:
            p = Point(l["x"], l["y"])
            if shapely.contains(self.emprise, p):
                c["lieux"].append((p, {"place": l["place"], "nom": l["nom"]}))

        # routes : trois classes, bretelles sous la bande 11-12 supprimées
        for classe in ("secondary", "primary", "trunk", "motorway"):
            for lien in (False, True):
                if lien and not bandes.liens_routes(b):
                    continue
                lst = [l for _, cl, ln, l in self.d.routes if cl == classe and ln == lien]
                c["routes"].extend(self._lignes_tremblees(fusionner(lst), b, "route-%s-%d" % (classe, lien),
                                                          {"classe": classe}))
        # rail
        c["rail"].extend(self._lignes_tremblees(fusionner([l for _, l in self.d.rail]), b, "rail", {}))
        # pistes : droites, jamais tremblées
        for ident, l in self.d.pistes:
            for g in self._dedans([l]):
                for p in _parties_lignes(g):
                    c["pistes"].append((p, {}))
        # frontières (admin_level=2)
        c["frontieres"].extend(self._lignes_tremblees(fusionner([l for _, l in self.d.frontieres]), b, "frontiere", {}))
        # grille MGRS : droite
        lignes, points = mgrs.construire_grille(self.emprise, self.emprise_wgs, bandes.pas_grille(b),
                                                carres=bandes.carres_100km(b))
        for g, pr in lignes:
            c["grille"].append((g, dict(pr, type="ligne")))
        for g, pr in points:
            c["grille"].append((g, dict(pr)))
        return c

    # ------------------------------------------------------------------ arbres
    def _arbres(self, b):
        if self.forets is None or self.forets.is_empty:
            return []
        s = bandes.ESPACE_ARBRES_PX * bandes.px(b)
        pts = []
        sel = bruit.graine("arbres", b[0]) & 0xFFFFFFFF
        for p in _parties_polygones(self.forets):
            x0, y0, x1, y1 = p.bounds
            i0, i1 = int(math.floor(x0 / s)), int(math.floor(x1 / s))
            j0, j1 = int(math.floor(y0 / s)), int(math.floor(y1 / s))
            ix, iy = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1))
            ix, iy = ix.ravel(), iy.ravel()
            u, v = bruit.hasard_cellules(ix, iy, sel)
            x = (ix + 0.15 + 0.7 * u) * s
            y = (iy + 0.15 + 0.7 * v) * s
            m = shapely.contains_xy(p, x, y) & shapely.contains_xy(self.emprise, x, y)
            for xx, yy in zip(x[m], y[m]):
                pts.append((float(xx), float(yy)))
        pts = sorted(set(pts), key=lambda t: (round(t[1], 3), round(t[0], 3)))
        return [Point(x, y) for x, y in pts]
