"""Lecture des extraits OpenStreetMap (.osm, .osm.gz, .osm.pbf) : entités utiles au fond, en Mercator.

Le fichier doit être trié (nœuds, chemins, relations), comme tout extrait Geofabrik ou Overpass `out body`.
Les identifiants servent de graine : une entité garde son identifiant (`n123`, `w456`, `r789`).
"""
import numpy as np
import osmium
import shapely
from shapely.geometry import LineString
from shapely.validation import make_valid

from .merc import lonlat_vers_merc

CLASSES_ROUTE = ("motorway", "trunk", "primary", "secondary")
COURS_EAU = ("river", "stream", "canal")
LIEUX = ("city", "town")          # ni village, ni hameau, ni habitation isolée, ni lieu-dit : jamais lus


class Donnees:
    def __init__(self):
        self.lieux = []        # dicts : id, nom, place, x, y
        self.eau = []          # (id, polygone)
        self.cours_eau = []    # (id, classe, ligne)
        self.forets = []       # (id, polygone)
        self.localites = []    # (id, polygone)
        self.routes = []       # (id, classe, lien, ligne)
        self.rail = []         # (id, ligne)
        self.pistes = []       # (id, ligne)
        self.frontieres = []   # (id, ligne)

    def trier(self):
        self.lieux.sort(key=lambda d: d["id"])
        for nom in ("eau", "cours_eau", "forets", "localites", "routes", "rail", "pistes", "frontieres"):
            lst = getattr(self, nom)
            vus, out = set(), []
            for e in sorted(lst, key=lambda t: t[0]):
                if e[0] in vus:
                    continue
                vus.add(e[0])
                out.append(e)
            setattr(self, nom, out)
        vus, out = set(), []
        for d in self.lieux:
            if d["id"] not in vus:
                vus.add(d["id"]); out.append(d)
        self.lieux = out


def nom_en_usage(tags):
    """Le nom en usage d'OSM : `name`, à défaut `name:en`, tel quel."""
    return tags.get("name") or tags.get("name:en") or None


def _ligne(o):
    pts = []
    for n in o.nodes:
        if n.location.valid():
            pts.append((n.lon, n.lat))
    if len(pts) < 2:
        return None
    x, y = lonlat_vers_merc([p[0] for p in pts], [p[1] for p in pts])
    return LineString(np.column_stack([x, y]))


def _polygone(a):
    wkb = osmium.geom.WKBFactory().create_multipolygon(a)
    g = shapely.from_wkb(bytes.fromhex(wkb) if isinstance(wkb, str) else wkb)
    # WKB en WGS84 -> Mercator
    g = shapely.transform(g, lambda c: np.column_stack(lonlat_vers_merc(c[:, 0], c[:, 1])))
    if not g.is_valid:
        g = make_valid(g)
    polys = [p for p in (g.geoms if hasattr(g, "geoms") else [g]) if p.geom_type in ("Polygon", "MultiPolygon")]
    out = []
    for p in polys:
        out.extend(p.geoms if p.geom_type == "MultiPolygon" else [p])
    if not out:
        return None
    return out[0] if len(out) == 1 else shapely.MultiPolygon(out)


def lire(fichiers):
    """Lit un ou plusieurs extraits ; rend une `Donnees` triée et sans doublon."""
    d = Donnees()
    for chemin in fichiers:
        fp = osmium.FileProcessor(str(chemin), osmium.osm.NODE | osmium.osm.WAY | osmium.osm.AREA) \
            .with_locations().with_areas()
        for o in fp:
            t = o.tags
            if o.is_node():
                place = t.get("place")
                if place in LIEUX:
                    nom = nom_en_usage(t)
                    if nom:
                        x, y = lonlat_vers_merc(o.location.lon, o.location.lat)
                        d.lieux.append({"id": "n%d" % o.id, "nom": nom, "place": place, "x": float(x), "y": float(y)})
            elif o.is_area():
                ident = ("w%d" if o.from_way() else "r%d") % o.orig_id()
                if t.get("natural") == "water" or t.get("landuse") == "reservoir":
                    g = _polygone(o)
                    if g is not None:
                        d.eau.append((ident, g))
                elif t.get("landuse") == "forest" or t.get("natural") == "wood":
                    g = _polygone(o)
                    if g is not None:
                        d.forets.append((ident, g))
                elif t.get("landuse") == "residential":
                    g = _polygone(o)
                    if g is not None:
                        d.localites.append((ident, g))
            elif o.is_way():
                ident = "w%d" % o.id
                hw = t.get("highway")
                if hw and hw.replace("_link", "") in CLASSES_ROUTE:
                    l = _ligne(o)
                    if l is not None:
                        d.routes.append((ident, hw.replace("_link", ""), hw.endswith("_link"), l))
                elif t.get("waterway") in COURS_EAU:
                    l = _ligne(o)
                    if l is not None:
                        d.cours_eau.append((ident, t["waterway"], l))
                elif t.get("railway") == "rail":
                    l = _ligne(o)
                    if l is not None:
                        d.rail.append((ident, l))
                elif t.get("aeroway") == "runway":
                    l = _ligne(o)
                    if l is not None:
                        d.pistes.append((ident, l))
                if t.get("boundary") == "administrative" and t.get("admin_level") == "2" and t.get("maritime") != "yes":
                    l = _ligne(o)
                    if l is not None:
                        d.frontieres.append((ident, l))
    d.trier()
    return d
