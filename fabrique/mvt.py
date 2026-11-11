"""Découpe en tuiles vectorielles (MVT) et écriture du PMTiles. Ordre fixe, gzip sans date."""
import gzip

import mapbox_vector_tile
import numpy as np
import shapely
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer
from shapely.geometry import box
from shapely.strtree import STRtree

from . import merc
from .couches import COUCHES

TAMPON = 64            # unités MVT de marge de découpe
OPTIONS = {"y_coord_down": True, "extents": merc.ETENDUE_MVT}


class IndexCouche:
    def __init__(self, features):
        self.features = features
        self.geoms = np.array([g for g, _ in features], dtype=object)
        self.arbre = STRtree(self.geoms) if len(features) else None

    def interroger(self, boite):
        if self.arbre is None:
            return []
        idx = self.arbre.query(box(*boite))
        return sorted(int(i) for i in idx)


def _vers_tuile(g, xmin, ymax, taille):
    k = merc.ETENDUE_MVT / taille

    def f(c):
        out = np.empty_like(c)
        out[:, 0] = (c[:, 0] - xmin) * k
        out[:, 1] = (ymax - c[:, 1]) * k
        return out

    return shapely.set_precision(shapely.transform(g, f), 1.0)


def encoder_tuile(z, x, y, index):
    """Les couches d'une tuile, découpées et arrondies à la grille MVT ; None si rien n'y tombe."""
    xmin, ymin, xmax, ymax = merc.bornes_tuile(z, x, y)
    taille = xmax - xmin
    marge = taille * TAMPON / merc.ETENDUE_MVT
    boite = (xmin - marge, ymin - marge, xmax + marge, ymax + marge)
    couches = []
    for nom in COUCHES:
        ic = index.get(nom)
        if ic is None:
            continue
        feats = []
        for i in ic.interroger(boite):
            g, props = ic.features[i]
            if g.geom_type == "Point":
                if not (boite[0] <= g.x <= boite[2] and boite[1] <= g.y <= boite[3]):
                    continue
                h = g
            else:
                h = shapely.clip_by_rect(g, *boite)
            if h is None or h.is_empty:
                continue
            h = _vers_tuile(h, xmin, ymax, taille)
            if h.is_empty:
                continue
            if h.geom_type in ("Polygon", "MultiPolygon") and h.area < 0.5:
                continue
            feats.append({"geometry": h, "properties": props})
        if feats:
            couches.append({"name": nom, "features": feats})
    if not couches:
        return None
    return mapbox_vector_tile.encode(couches, default_options=OPTIONS)


def tuiles_du_zoom(z, emprise):
    x0, x1, y0, y1 = merc.tuiles_touchant(z, emprise.bounds)
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            if emprise.intersects(box(*merc.bornes_tuile(z, x, y))):
                yield x, y


def ecrire_pmtiles(chemin, tuiles, emprise_wgs, metadata, tile_type=TileType.MVT):
    """tuiles : {(z, x, y): octets (déjà gzippés)} ; écriture dans l'ordre des identifiants PMTiles."""
    ids = sorted((zxy_to_tileid(z, x, y), (z, x, y)) for (z, x, y) in tuiles)
    with open(chemin, "wb") as f:
        w = Writer(f)
        for tid, k in ids:
            w.write_tile(tid, tuiles[k])
        lon0, lat0, lon1, lat1 = emprise_wgs.bounds
        zmin = min(k[0] for _, k in ids)
        zmax = max(k[0] for _, k in ids)
        header = {
            "version": 3, "tile_type": tile_type, "tile_compression": Compression.GZIP if tile_type == TileType.MVT else Compression.NONE,
            "min_lon_e7": int(round(lon0 * 1e7)), "min_lat_e7": int(round(lat0 * 1e7)),
            "max_lon_e7": int(round(lon1 * 1e7)), "max_lat_e7": int(round(lat1 * 1e7)),
            "center_zoom": min(max(11, zmin), zmax),
            "center_lon_e7": int(round((lon0 + lon1) / 2 * 1e7)), "center_lat_e7": int(round((lat0 + lat1) / 2 * 1e7)),
        }
        w.finalize(header, metadata)


def gzip_sans_date(data):
    return gzip.compress(data, compresslevel=9, mtime=0)
