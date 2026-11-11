"""Un jeu d'essai minuscule : 6 km autour de la limite des fuseaux 36 et 37, un relief synthétique, un extrait OSM écrit à la main."""
import json
import os
import sys

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RACINE)

LON0, LON1, LAT0, LAT1 = 35.97, 36.03, 35.40, 35.46


def emprise_geojson(chemin, boite=(LON0, LON1, LAT0, LAT1)):
    lon0, lon1, lat0, lat1 = boite
    pts = [(lon0, lat0), (lon1, lat0), (lon1, lat1), (lon0, lat1), (lon0, lat0)]
    with open(chemin, "w") as f:
        json.dump({"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [[list(p) for p in pts]]}}, f)


def osm_xml(chemin):
    """Nœuds d'abord, puis chemins : l'ordre d'un extrait Overpass `out body`."""
    nodes, ways = [], []
    nid = [1000]

    def noeud(lon, lat, tags=None):
        nid[0] += 1
        t = "".join('<tag k="%s" v="%s"/>' % (k, v) for k, v in (tags or {}).items())
        nodes.append('<node id="%d" lat="%.6f" lon="%.6f">%s</node>' % (nid[0], lat, lon, t) if t else
                     '<node id="%d" lat="%.6f" lon="%.6f"/>' % (nid[0], lat, lon))
        return nid[0]

    def chemin_(wid, pts, tags):
        refs = [noeud(lon, lat) for lon, lat in pts]
        t = "".join('<tag k="%s" v="%s"/>' % (k, v) for k, v in tags.items())
        ways.append('<way id="%d">%s%s</way>' % (wid, "".join('<nd ref="%d"/>' % r for r in refs), t))

    # les lieux : seuls city et town survivent
    noeud(36.00, 35.43, {"place": "city", "name": "Ville"})
    noeud(35.99, 35.44, {"place": "town", "name": "Bourg"})
    noeud(36.01, 35.41, {"place": "village", "name": "Village"})
    noeud(36.02, 35.42, {"place": "hamlet", "name": "Hameau"})
    noeud(36.02, 35.45, {"place": "isolated_dwelling", "name": "Ferme"})
    noeud(35.98, 35.45, {"place": "locality", "name": "Lieu-dit"})
    # une route de chaque classe, une bretelle, un chemin à ne pas prendre
    chemin_(1, [(35.97, 35.43), (35.985, 35.434), (36.0, 35.43), (36.015, 35.426), (36.03, 35.43)], {"highway": "motorway"})
    chemin_(2, [(35.97, 35.41), (35.99, 35.413), (36.03, 35.41)], {"highway": "primary"})
    chemin_(3, [(35.97, 35.45), (36.0, 35.452), (36.03, 35.45)], {"highway": "secondary"})
    chemin_(4, [(35.99, 35.43), (35.995, 35.425)], {"highway": "motorway_link"})
    chemin_(5, [(35.97, 35.44), (36.03, 35.44)], {"highway": "tertiary"})
    chemin_(6, [(35.97, 35.42), (36.0, 35.4225), (36.03, 35.42)], {"railway": "rail"})
    chemin_(7, [(35.995, 35.455), (36.005, 35.455)], {"aeroway": "runway"})
    chemin_(8, [(35.97, 35.405), (36.0, 35.408), (36.03, 35.405)], {"waterway": "river"})
    chemin_(9, [(35.98, 35.456), (35.995, 35.4565), (36.02, 35.4568)], {"waterway": "stream"})
    # un lac, une forêt, une zone résidentielle de plus d'1 km2 (fermés)
    def anneau(wid, x0, y0, x1, y1, tags):
        chemin_(wid, [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)], tags)
    anneau(10, 35.975, 35.445, 35.985, 35.452, {"natural": "water"})
    anneau(11, 36.005, 35.43, 36.027, 35.446, {"landuse": "forest"})
    anneau(12, 35.975, 35.415, 35.995, 35.428, {"landuse": "residential"})
    nodes_fixes = nodes
    with open(chemin, "w") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6" generator="essai">\n')
        f.write("\n".join(nodes_fixes + ways))
        f.write("\n</osm>\n")


@pytest.fixture(scope="session")
def jeu(tmp_path_factory):
    d = tmp_path_factory.mktemp("jeu")
    emprise = str(d / "mini.geojson")
    emprise_geojson(emprise)
    osm = str(d / "mini.osm")
    osm_xml(osm)
    cache = d / "cache"
    cache.mkdir()
    return {"dossier": d, "emprise": emprise, "osm": osm, "cache": str(cache)}


def entrees_relief(jeu, nom="a"):
    """Les dalles synthétiques (la côte à 35,985° E, puis une rampe vers l'est) en entrées de manifeste, sans réseau."""
    from fabrique import sources
    d = jeu["dossier"] / ("dalles_" + nom)
    d.mkdir(exist_ok=True)
    out = []
    for lat, lon in ((35, 35), (35, 36)):
        chemin = str(d / ("Copernicus_DSM_COG_10_%s_DEM.tif" % sources.nom_dalle(lat, lon)))
        n = 360
        lons = lon + (np.arange(n) + 0.5) / n
        z = np.tile(np.clip((lons - 35.985) * 40000, -1, 1500), (n, 1)).astype("float32")
        tr = from_origin(lon, lat + 1, 1.0 / n, 1.0 / n)
        with rasterio.open(chemin, "w", driver="GTiff", height=n, width=n, count=1, dtype="float32",
                           crs="EPSG:4326", transform=tr) as dst:
            dst.write(z, 1)
        out.append({"dalle": sources.nom_dalle(lat, lon), "lat": lat, "lon": lon, "source": "glo30",
                    "adresse": sources.url_dalle(lat, lon, "glo30"), "fichier": chemin,
                    "taille": os.path.getsize(chemin), "sha256": sources.sha256_fichier(chemin)})
    return out
