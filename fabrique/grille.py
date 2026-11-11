"""La grille MGRS du F10 : le vrai découpage en fuseaux UTM, droite (jamais tremblée)."""
import math

import numpy as np
from pyproj import Transformer
from shapely.geometry import LineString, Point, Polygon, box

from . import merc

LETTRES_BANDES = "CDEFGHJKLMNPQRSTUVWX"
LETTRES_COLONNES = ("ABCDEFGH", "JKLMNPQR", "STUVWXYZ")
LETTRES_LIGNES = "ABCDEFGHJKLMNPQRSTUV"
PAS_DENSIFICATION = 1000.0


def fuseau(lon):
    return int(math.floor((lon + 180.0) / 6.0)) + 1


def epsg_fuseau(zone, nord=True):
    return (32600 if nord else 32700) + zone


def bornes_fuseau(zone):
    """Longitudes ouest et est du fuseau (degrés)."""
    return -180.0 + 6.0 * (zone - 1), -180.0 + 6.0 * zone


def lettre_bande(lat):
    i = int(math.floor((lat + 80.0) / 8.0))
    return LETTRES_BANDES[max(0, min(i, len(LETTRES_BANDES) - 1))]


def carre_100km(zone, est, nord):
    """Les deux lettres du carré de 100 km (comme l'esquisse de référence), pour un point en UTM (mètres)."""
    cols = LETTRES_COLONNES[(zone - 1) % 3]
    c = cols[int(est // 100000) - 1]
    r = LETTRES_LIGNES[(int(nord // 100000) + (5 if zone % 2 == 0 else 0)) % 20]
    return c + r


def _vers_merc(lon, lat):
    x, y = merc.lonlat_vers_merc(lon, lat)
    return np.column_stack([x, y])


def parties_par_fuseau(emprise_merc, emprise_wgs):
    """[(zone, partie de l'emprise dans ce fuseau, en Mercator)] : l'emprise coupée aux limites des fuseaux."""
    lon0, lat0, lon1, lat1 = emprise_wgs.bounds
    out = []
    grand = merc.DEMI_MONDE * 2
    for zone in range(fuseau(lon0), fuseau(lon1 - 1e-12) + 1):
        w, e = bornes_fuseau(zone)
        xw = merc.lonlat_vers_merc(w, 0)[0]
        xe = merc.lonlat_vers_merc(e, 0)[0]
        part = emprise_merc.intersection(box(float(xw), -grand, float(xe), grand))
        if not part.is_empty and part.area > 0:
            out.append((zone, part))
    return out


class HemisphereErreur(ValueError):
    pass


def verifier_hemisphere(emprise_wgs):
    """La grille MGRS de ce lot ne couvre que l'hémisphère nord (aucun théâtre de la liste n'est au sud) : une emprise au
    sud de l'équateur est refusée avec un message."""
    if emprise_wgs.bounds[1] < 0:
        raise HemisphereErreur("l'emprise descend au sud de l'équateur (latitude %.3f) : la grille MGRS de cette fabrique "
                               "ne couvre que l'hémisphère nord" % emprise_wgs.bounds[1])


def _tranches_de_latitude(g):
    """Le polygone (Mercator) coupé aux limites des bandes de latitude MGRS (tous les 8 degrés : 32, 40, 48, 56 N...)."""
    lon0, lat0 = merc.merc_vers_lonlat(g.bounds[0], g.bounds[1])
    lon1, lat1 = merc.merc_vers_lonlat(g.bounds[2], g.bounds[3])
    limites = [-80.0 + 8.0 * k for k in range(0, 21) if lat0 < -80.0 + 8.0 * k < lat1]
    if not limites:
        return [g]
    ys = [-1e9] + [float(merc.lonlat_vers_merc(0.0, l)[1]) for l in limites] + [1e9]
    out = []
    for ya, yb in zip(ys[:-1], ys[1:]):
        h = g.intersection(box(g.bounds[0] - 1, ya, g.bounds[2] + 1, yb))
        out.extend(p for p in (h.geoms if hasattr(h, "geoms") else [h]) if p.geom_type == "Polygon" and p.area > 0)
    return out


def construire_grille(emprise_merc, emprise_wgs, pas, carres=False, etiquettes=True):
    """Les lignes, les étiquettes et les carrés de 100 km de la grille, en Mercator.

    Rend (lignes, points) :
      lignes : [(LineString, {"niveau": pas})]
      points : [(Point, {"texte": ..., "type": "e"|"n"|"carre"})]
    """
    verifier_hemisphere(emprise_wgs)
    lignes, points = [], []
    for zone, part in parties_par_fuseau(emprise_merc, emprise_wgs):
        tr_in = Transformer.from_crs(3857, epsg_fuseau(zone), always_xy=True)
        tr_out = Transformer.from_crs(epsg_fuseau(zone), 3857, always_xy=True)
        # bornes en UTM de la partie (les bords droits en Mercator sont densifiés avant projection)
        exs, eys = [], []
        for poly in (part.geoms if part.geom_type == "MultiPolygon" else [part]):
            x, y = tr_in.transform(*np.asarray(poly.segmentize(5000.0).exterior.coords).T)
            exs.append(x)
            eys.append(y)
        ex, ey = np.concatenate(exs), np.concatenate(eys)
        e0 = math.floor(ex.min() / pas) * pas - pas
        e1 = math.ceil(ex.max() / pas) * pas + pas
        n0 = math.floor(ey.min() / pas) * pas - pas
        n1 = math.ceil(ey.max() / pas) * pas + pas
        ensemble_e = np.arange(e0, e1 + 1, pas)
        ensemble_n = np.arange(n0, n1 + 1, pas)
        ns = np.arange(n0, n1 + 1, PAS_DENSIFICATION)
        es = np.arange(e0, e1 + 1, PAS_DENSIFICATION)
        # lignes d'abscisse constante (est) et d'ordonnée constante (nord)
        for e in ensemble_e:
            x, y = tr_out.transform(np.full_like(ns, e), ns)
            l = LineString(np.column_stack([x, y])).intersection(part)
            for g in _lignes(l):
                lignes.append((g, {"niveau": int(pas), "zone": zone}))
        for n in ensemble_n:
            x, y = tr_out.transform(es, np.full_like(es, n))
            l = LineString(np.column_stack([x, y])).intersection(part)
            for g in _lignes(l):
                lignes.append((g, {"niveau": int(pas), "zone": zone}))
        if etiquettes and pas <= 10000:
            for e in ensemble_e:
                for n in ensemble_n:
                    x, y = tr_out.transform(e, n)
                    p = Point(float(x), float(y))
                    if not part.contains(p):
                        continue
                    points.append((p, {"texte": "%02d" % int(e % 100000 // 1000), "type": "e"}))
                    points.append((p, {"texte": "%02d" % int(n % 100000 // 1000), "type": "n"}))
        if carres:
            for e in np.arange(math.floor(e0 / 100000) * 100000, e1 + 1, 100000):
                for n in np.arange(math.floor(n0 / 100000) * 100000, n1 + 1, 100000):
                    cx = [e, e + 100000, e + 100000, e, e]
                    cy = [n, n, n + 100000, n + 100000, n]
                    # côtés densifiés : un carré UTM n'est pas un rectangle en Mercator
                    seg = []
                    for (xa, ya), (xb, yb) in zip(zip(cx[:-1], cy[:-1]), zip(cx[1:], cy[1:])):
                        t = np.linspace(0, 1, 21, endpoint=False)
                        seg += list(zip(xa + (xb - xa) * t, ya + (yb - ya) * t))
                    sx, sy = zip(*seg)
                    x, y = tr_out.transform(np.asarray(sx), np.asarray(sy))
                    morceau = Polygon(np.column_stack([x, y])).intersection(part)
                    if morceau.is_empty or morceau.area <= 0:
                        continue
                    morceaux = [g for g in (morceau.geoms if hasattr(morceau, "geoms") else [morceau])
                                if g.geom_type == "Polygon" and g.area > 0]
                    # un carré à cheval sur une limite de bande de latitude (32, 40, 48, 56 N) a deux étiquettes : une par morceau
                    tranches = []
                    for m in morceaux:
                        tranches.extend(_tranches_de_latitude(m))
                    # une étiquette par bande de latitude : le plus gros morceau de chaque bande
                    par_bande = {}
                    for t in tranches:
                        p = t.representative_point()
                        lat = float(merc.merc_vers_lonlat(p.x, p.y)[1])
                        b = lettre_bande(lat)
                        if b not in par_bande or t.area > par_bande[b][0].area:
                            par_bande[b] = (t, p)
                    for b in sorted(par_bande):
                        _, p = par_bande[b]
                        ue, un = tr_in.transform(p.x, p.y)
                        texte = "%d%s %s" % (zone, b, carre_100km(zone, ue, un))
                        points.append((p, {"texte": texte, "type": "carre"}))
    return lignes, points


def _lignes(g):
    if g.is_empty:
        return []
    if g.geom_type == "LineString":
        return [g]
    if g.geom_type in ("MultiLineString", "GeometryCollection"):
        return [x for x in g.geoms if x.geom_type == "LineString" and not x.is_empty]
    return []
