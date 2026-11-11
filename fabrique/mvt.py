"""Découpe en tuiles vectorielles (MVT) et écriture du PMTiles. Ordre fixe, gzip sans date.

Les tuiles se fabriquent au fil de l'eau : un bloc range ses morceaux en mémoire, encode ses tuiles, les écrit dans un
dépôt (un fichier à plat du dossier de travail) et oublie tout. Seul l'index (tuile -> position) reste en mémoire ;
le PMTiles final se lit dans cet index, dans l'ordre des identifiants PMTiles.
"""
import gzip
import json
import tempfile

import mapbox_vector_tile
import numpy as np
import shapely
from pmtiles.tile import Compression, TileType, serialize_directory, serialize_header, zxy_to_tileid
from pmtiles.writer import Writer

from . import merc
from .couches import COUCHES

TAMPON = 64            # unités MVT de marge de découpe
OPTIONS = {"y_coord_down": True, "extents": merc.ETENDUE_MVT}
Z_RACINE = 5           # on descend toujours depuis le zoom 5 : la même suite de coupes, quel que soit le bloc


def tampon(z):
    """La marge de découpe d'une tuile du zoom z, en mètres Mercator."""
    return (2 * merc.DEMI_MONDE / 2 ** z) * TAMPON / merc.ETENDUE_MVT


def boite_tampon(z, x, y):
    xmin, ymin, xmax, ymax = merc.bornes_tuile(z, x, y)
    m = tampon(z)
    return (xmin - m, ymin - m, xmax + m, ymax + m)


def dans_bloc(z, x, y, bloc):
    """La tuile (z, x, y) est-elle dans le bloc (zoom pivot, bx, by) ? bloc None : toujours."""
    if bloc is None:
        return True
    pz, bx, by = bloc
    if z < pz:
        return False
    s = z - pz
    return (x >> s) == bx and (y >> s) == by


def sur_le_chemin_du_bloc(z, x, y, bloc):
    """Cette tuile est-elle le bloc, un de ses ancêtres, ou l'un de ses descendants ?"""
    if bloc is None:
        return True
    pz, bx, by = bloc
    if z >= pz:
        return (x >> (z - pz)) == bx and (y >> (z - pz)) == by
    s = pz - z
    return (bx >> s) == x and (by >> s) == y


class Collecteur:
    """Les morceaux de tuiles d'un bloc, en mémoire : {(z, x, y): {couche: [(clé, géométrie, props)]}}."""

    def __init__(self):
        self.t = {}

    def ajouter(self, z, x, y, couche, cle, geom, props):
        self.t.setdefault((z, x, y), {}).setdefault(couche, []).append((cle, geom, props))

    def tuiles(self):
        return sorted(self.t)

    def prendre(self, z, x, y):
        d = self.t.pop((z, x, y))
        return {c: sorted(l, key=lambda e: e[0]) for c, l in d.items()}


class CollecteurDisque:
    """Les morceaux des tuiles de survol, rangés sur disque (un fichier SQLite) jusqu'à leur assemblage."""

    def __init__(self, fragments):
        self.f = fragments
        self.rang = {c: i for i, c in enumerate(COUCHES)}

    def ajouter(self, z, x, y, couche, cle, geom, props):
        self.f.ajouter(z, x, y, self.rang[couche], cle, geom, props)

    def tuiles(self):
        self.f.indexer()
        return self.f.tuiles()

    def prendre(self, z, x, y):
        d = self.f.de_la_tuile(z, x, y)
        return {COUCHES[r]: l for r, l in d.items()}


# ---------------------------------------------------------------------- répartition aux tuiles
def _coupe(g, boite):
    """g coupée à la boîte ; g elle-même si elle y tient déjà ; None si rien n'y reste."""
    x0, y0, x1, y1 = g.bounds
    if x0 >= boite[0] and y0 >= boite[1] and x1 <= boite[2] and y1 <= boite[3]:
        return g
    h = shapely.clip_by_rect(g, *boite)
    if h is None or h.is_empty:
        return None
    return h


def tuiles_du_point(z, x, y):
    """Les tuiles du zoom z dont la boîte à tampon contient le point (x, y) en Mercator."""
    t = 2 * merc.DEMI_MONDE / 2 ** z
    tx = int(np.floor((x + merc.DEMI_MONDE) / t))
    ty = int(np.floor((merc.DEMI_MONDE - y) / t))
    n = 2 ** z
    out = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            cx, cy = tx + dx, ty + dy
            if not (0 <= cx < n and 0 <= cy < n):
                continue
            b = boite_tampon(z, cx, cy)
            if b[0] <= x <= b[2] and b[1] <= y <= b[3]:
                out.append((cx, cy))
    return out


def repartir(coll, couche, cle, geom, props, zooms, bloc=None):
    """Range les morceaux de la géométrie dans les tuiles des `zooms`, coupés à leur boîte à tampon.

    On descend toujours du zoom 5 vers le zoom visé, en coupant le morceau du parent : la suite des coupes d'une tuile
    ne dépend donc pas du bloc (ni du zoom pivot), et un tracé de mille kilomètres ne passe jamais par la boîte de
    toutes les tuiles qu'il survole. `bloc` = (zoom pivot, bx, by) borne le travail aux tuiles de ce bloc."""
    zset = set(zooms)
    zmax = max(zooms)
    if geom.geom_type == "Point":
        for z in sorted(zset):
            for cx, cy in tuiles_du_point(z, geom.x, geom.y):
                if dans_bloc(z, cx, cy, bloc):
                    coll.ajouter(z, cx, cy, couche, cle, geom, props)
        return
    x0, y0, x1, y1 = geom.bounds
    m = tampon(Z_RACINE)
    ax, bx_, ay, by_ = merc.tuiles_touchant(Z_RACINE, (x0 - m, y0 - m, x1 + m, y1 + m))

    def descendre(z, x, y, g):
        if z in zset and dans_bloc(z, x, y, bloc):
            coll.ajouter(z, x, y, couche, cle, g, props)
        if z >= zmax:
            return
        for dx in (0, 1):
            for dy in (0, 1):
                cx, cy = 2 * x + dx, 2 * y + dy
                if not sur_le_chemin_du_bloc(z + 1, cx, cy, bloc):
                    continue
                h = _coupe(g, boite_tampon(z + 1, cx, cy))
                if h is not None:
                    descendre(z + 1, cx, cy, h)

    for x in range(ax, bx_ + 1):
        for y in range(ay, by_ + 1):
            if not sur_le_chemin_du_bloc(Z_RACINE, x, y, bloc):
                continue
            g = _coupe(geom, boite_tampon(Z_RACINE, x, y))
            if g is not None:
                descendre(Z_RACINE, x, y, g)


def repartir_morceau(coll, couche, cle, geom, props, zooms, bloc=None):
    """Pour un morceau déjà petit (une cellule du relief) : on coupe directement à chaque tuile qu'il touche."""
    for z in sorted(zooms):
        if geom.geom_type == "Point":
            tuiles = tuiles_du_point(z, geom.x, geom.y)
        else:
            m = tampon(z)
            x0, y0, x1, y1 = geom.bounds
            ax, bx_, ay, by_ = merc.tuiles_touchant(z, (x0 - m, y0 - m, x1 + m, y1 + m))
            tuiles = [(x, y) for x in range(ax, bx_ + 1) for y in range(ay, by_ + 1)]
        for cx, cy in tuiles:
            if not dans_bloc(z, cx, cy, bloc):
                continue
            g = geom if geom.geom_type == "Point" else _coupe(geom, boite_tampon(z, cx, cy))
            if g is not None:
                coll.ajouter(z, cx, cy, couche, cle, g, props)


# ---------------------------------------------------------------------- encodage
def _vers_tuile(g, xmin, ymax, taille):
    k = merc.ETENDUE_MVT / taille

    def f(c):
        out = np.empty_like(c)
        out[:, 0] = (c[:, 0] - xmin) * k
        out[:, 1] = (ymax - c[:, 1]) * k
        return out

    return shapely.set_precision(shapely.transform(g, f), 1.0)


def encoder_tuile(z, x, y, par_couche, champs=None):
    """Les couches d'une tuile (morceaux déjà coupés à la boîte à tampon), arrondies à la grille MVT ; None si vide.
    Les morceaux de mer de cellules voisines s'unissent (sur la grille MVT, donc sans fente) ; les arbres en double
    (deux forêts qui se recouvrent) s'écrivent une fois. `champs` : un dict qui note, par couche présente, les champs vus."""
    xmin, ymin, xmax, ymax = merc.bornes_tuile(z, x, y)
    taille = xmax - xmin
    couches = []
    for nom in COUCHES:
        lst = par_couche.get(nom)
        if not lst:
            continue
        feats = []
        if nom == "mer":
            morceaux = [_vers_tuile(g, xmin, ymax, taille) for _, g, _ in lst]
            morceaux = [m for m in morceaux if not m.is_empty]
            if morceaux:
                u = shapely.union_all(morceaux, grid_size=1.0)
                parts = [p for p in (u.geoms if hasattr(u, "geoms") else [u]) if p.geom_type == "Polygon"
                         and not p.is_empty and p.area >= 0.5]
                parts.sort(key=lambda p: (p.bounds[0], p.bounds[1], p.area))
                feats = [{"geometry": p, "properties": {}} for p in parts]
        else:
            vus = set()
            for _, g, props in lst:
                h = _vers_tuile(g, xmin, ymax, taille)
                if h.is_empty:
                    continue
                if h.geom_type == "Point":
                    k = (h.x, h.y, json.dumps(props, sort_keys=True))
                    if k in vus:
                        continue
                    vus.add(k)
                if h.geom_type in ("Polygon", "MultiPolygon") and h.area < 0.5:
                    continue
                feats.append({"geometry": h, "properties": dict(sorted(props.items()))})   # ordre des clés fixe, d'où qu'elles viennent
        if feats:
            couches.append({"name": nom, "features": feats})
            if champs is not None:
                c = champs.setdefault(nom, {})
                for feat in feats:
                    for k, v in feat["properties"].items():
                        c[k] = "Number" if isinstance(v, (int, float)) else "String"
    if not couches:
        return None
    return mapbox_vector_tile.encode(couches, default_options=OPTIONS)


def gzip_sans_date(data):
    return gzip.compress(data, compresslevel=9, mtime=0)


# ---------------------------------------------------------------------- le dépôt et le PMTiles
class Depot:
    """Les tuiles déjà gzippées, écrites à la suite dans un fichier à plat ; l'index seul reste en mémoire."""

    def __init__(self, chemin):
        self.f = open(chemin, "w+b")
        self.index = {}
        self.pos = 0

    def ecrire(self, z, x, y, data):
        self.f.write(data)
        self.index[(z, x, y)] = (self.pos, len(data))
        self.pos += len(data)

    def lire(self, z, x, y):
        off, n = self.index[(z, x, y)]
        self.f.seek(off)
        return self.f.read(n)

    def fermer(self):
        self.f.close()


def _en_tete(emprise_wgs, tile_type, zmin, zmax):
    lon0, lat0, lon1, lat1 = emprise_wgs.bounds
    return {
        "version": 3, "tile_type": tile_type,
        "tile_compression": Compression.GZIP if tile_type == TileType.MVT else Compression.NONE,
        "min_lon_e7": int(round(lon0 * 1e7)), "min_lat_e7": int(round(lat0 * 1e7)),
        "max_lon_e7": int(round(lon1 * 1e7)), "max_lat_e7": int(round(lat1 * 1e7)),
        "center_zoom": min(max(11, zmin), zmax),
        "center_lon_e7": int(round((lon0 + lon1) / 2 * 1e7)), "center_lat_e7": int(round((lat0 + lat1) / 2 * 1e7)),
    }


def ecrire_pmtiles(chemin, depot, emprise_wgs, metadata, travail, tile_type=TileType.MVT):
    """Écrit le PMTiles dans l'ordre des identifiants, en lisant les tuiles une à une dans le dépôt.
    Un dépôt sans tuile (une emprise toute en mer, pour le relief) donne un PMTiles vide mais valide."""
    ids = sorted((zxy_to_tileid(z, x, y), (z, x, y)) for (z, x, y) in depot.index)
    if not ids:
        return ecrire_vide(chemin, emprise_wgs, metadata, tile_type)
    with open(chemin, "wb") as f:
        w = Writer(f)
        w.tile_f.close()
        w.tile_f = tempfile.TemporaryFile(dir=travail)
        for tid, k in ids:
            w.write_tile(tid, depot.lire(*k))
        zmin = min(k[0] for _, k in ids)
        zmax = max(k[0] for _, k in ids)
        w.finalize(_en_tete(emprise_wgs, tile_type, zmin, zmax), metadata)


def ecrire_vide(chemin, emprise_wgs, metadata, tile_type):
    """Un PMTiles v3 sans tuile : un répertoire racine vide, des métadonnées."""
    racine = serialize_directory([])
    meta = gzip.compress(json.dumps(metadata).encode(), mtime=0)
    h = _en_tete(emprise_wgs, tile_type, 0, 0)
    h.update({"root_offset": 127, "root_length": len(racine), "metadata_offset": 127 + len(racine),
              "metadata_length": len(meta), "leaf_directory_offset": 127 + len(racine) + len(meta),
              "leaf_directory_length": 0, "tile_data_offset": 127 + len(racine) + len(meta), "tile_data_length": 0,
              "addressed_tiles_count": 0, "tile_entries_count": 0, "tile_contents_count": 0, "clustered": True,
              "internal_compression": Compression.GZIP, "min_zoom": 0, "max_zoom": 0, "center_zoom": 0})
    with open(chemin, "wb") as f:
        f.write(serialize_header(h))
        f.write(racine)
        f.write(meta)

