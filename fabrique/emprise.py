"""L'emprise : un fichier GeoJSON (Polygon ou MultiPolygon en WGS84)."""
import json

from pyproj import Geod
from shapely.geometry import shape
from shapely.ops import unary_union


class EmpriseErreur(ValueError):
    pass


def lire_emprise(chemin):
    """Rend la géométrie shapely (WGS84) de l'emprise."""
    with open(chemin, encoding="utf-8") as f:
        gj = json.load(f)
    return depuis_geojson(gj)


def depuis_geojson(gj):
    t = gj.get("type")
    if t == "FeatureCollection":
        geoms = [depuis_geojson(f) for f in gj.get("features", [])]
    elif t == "Feature":
        geoms = [depuis_geojson(gj["geometry"])]
    elif t in ("Polygon", "MultiPolygon"):
        geoms = [shape(gj)]
    else:
        raise EmpriseErreur("l'emprise doit être un Polygon ou un MultiPolygon, pas %r" % (t,))
    if not geoms:
        raise EmpriseErreur("emprise vide")
    for g in geoms:
        if g.geom_type not in ("Polygon", "MultiPolygon"):
            raise EmpriseErreur("l'emprise doit être un Polygon ou un MultiPolygon")
    g = unary_union(geoms)
    if g.is_empty:
        raise EmpriseErreur("emprise vide")
    return g


def surface_km2(geom):
    """Surface géodésique, en km2, d'une géométrie WGS84."""
    geod = Geod(ellps="WGS84")
    total = 0.0
    parts = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
    for p in parts:
        a, _ = geod.geometry_area_perimeter(p)
        total += abs(a)
    return total / 1e6
