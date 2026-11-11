"""verifier : rejoue les contrôles sur un dossier produit (ni village, mentions, le fond se tait, emprise)."""
import gzip
import io
import json
import os

import mapbox_vector_tile
import numpy as np
import shapely
from PIL import Image
from pmtiles.reader import MmapSource, all_tiles
from pmtiles.tile import Compression, deserialize_header
from rasterio.features import rasterize
from rasterio.transform import from_origin
from shapely.geometry import box

from . import licence, manifeste, merc, style as stylemod
from .emprise import depuis_geojson
from .fabriquer import emprise_merc

FICHIERS = ("fond.pmtiles", "relief.pmtiles", "style-clair.json", "style-sombre.json", "LICENCE.txt",
            "manifeste.json", "sprite/papier.json", "sprite/papier.png", "sprite/papier@2x.json",
            "sprite/papier@2x.png")


def lire_tuiles(chemin):
    """[(z, x, y, octets décompressés)] d'un PMTiles."""
    with open(chemin, "rb") as f:
        src = MmapSource(f)
        h = deserialize_header(src(0, 127))
        out = []
        for (z, x, y), data in all_tiles(src):
            if h["tile_compression"] == Compression.GZIP:
                data = gzip.decompress(data)
            out.append((z, x, y, bytes(data)))
        tt = h["tile_type"]
    return out, tt


def _coords(g):
    t = g["type"]
    c = g["coordinates"]
    if t == "Point":
        yield c
    elif t in ("LineString", "MultiPoint"):
        yield from c
    elif t in ("Polygon", "MultiLineString"):
        for r in c:
            yield from r
    elif t == "MultiPolygon":
        for p in c:
            for r in p:
                yield from r


def controles(dossier, journal=print):
    """Rend (ok, resultats) ; resultats est un dict des mesures."""
    res = {"echecs": []}

    def echec(msg):
        res["echecs"].append(msg)
        journal("ÉCHEC : " + msg)

    for f in FICHIERS:
        if not os.path.isfile(os.path.join(dossier, f)):
            echec("fichier absent : %s" % f)
    if res["echecs"]:
        return False, res
    with open(os.path.join(dossier, "manifeste.json"), encoding="utf-8") as f:
        m = json.load(f)
    gj = m["entrees"]["emprise"]["geometrie"]
    em_wgs = depuis_geojson(gj)
    em = emprise_merc(em_wgs)
    shapely.prepare(em)
    res["manifeste_comparable_sha256"] = manifeste.sha256_comparable(m)

    # --- critère 2 : ni village
    tuiles, tt = lire_tuiles(os.path.join(dossier, "fond.pmtiles"))
    res["tuiles_fond"] = len(tuiles)
    compte = {"city": 0, "town": 0}
    distincts = {"city": set(), "town": set()}
    hors_emprise_tuile = 0
    hors_marge = 0
    parametres = {"y_coord_down": True}
    for z, x, y, data in tuiles:
        bt = box(*merc.bornes_tuile(z, x, y))
        if not em.intersects(bt):
            hors_emprise_tuile += 1
        t = mapbox_vector_tile.decode(data, default_options=parametres)
        xmin, ymin, xmax, ymax = merc.bornes_tuile(z, x, y)
        taille = xmax - xmin
        bb = em.bounds
        lim = (bb[0] - taille, bb[1] - taille, bb[2] + taille, bb[3] + taille)
        for nom, couche in t.items():
            for feat in couche["features"]:
                p = feat["properties"]
                if "place" in p:
                    if nom != "lieux" or p["place"] not in compte:
                        echec("entité place=%r dans la couche %s (tuile %d/%d/%d)" % (p["place"], nom, z, x, y))
                    else:
                        compte[p["place"]] += 1
                        distincts[p["place"]].add(p["nom"])
                if nom == "lieux" and p.get("place") not in ("city", "town"):
                    echec("entité de la couche lieux sans classe city/town (tuile %d/%d/%d)" % (z, x, y))
                for cx, cy in _coords(feat["geometry"]):
                    wx = xmin + cx / merc.ETENDUE_MVT * taille
                    wy = ymax - cy / merc.ETENDUE_MVT * taille
                    if not (lim[0] <= wx <= lim[2] and lim[1] <= wy <= lim[3]):
                        hors_marge += 1
    res["villes"] = len(distincts["city"])
    res["bourgs"] = len(distincts["town"])
    res["entites_lieux_toutes_tuiles"] = dict(compte)
    journal("ni village : aucune entité place hors city/town ; %d city et %d town distinctes (%d et %d entités sur "
            "toutes les tuiles)" % (res["villes"], res["bourgs"], compte["city"], compte["town"]))

    # --- critère 5 : l'emprise
    if hors_emprise_tuile:
        echec("%d tuiles de fond.pmtiles ne touchent pas l'emprise" % hors_emprise_tuile)
    if hors_marge:
        echec("%d sommets du fond à plus d'une tuile de l'emprise" % hors_marge)
    rel, tt_rel = lire_tuiles(os.path.join(dossier, "relief.pmtiles"))
    res["tuiles_relief"] = len(rel)
    for z, x, y, data in rel:
        b = merc.bornes_tuile(z, x, y)
        if not em.intersects(box(*b)):
            echec("tuile de relief hors de l'emprise : %d/%d/%d" % (z, x, y))
            continue
        a = np.array(Image.open(io.BytesIO(data)).convert("RGBA"))[..., 3]
        tr = from_origin(b[0], b[3], (b[2] - b[0]) / a.shape[1], (b[3] - b[1]) / a.shape[0])
        dedans = rasterize([(em, 1)], out_shape=a.shape, transform=tr, fill=0, all_touched=True, dtype="uint8").astype(bool)
        if (a[~dedans] > 0).any():
            echec("relief non transparent hors de l'emprise : %d/%d/%d" % (z, x, y))
    journal("emprise : %d tuiles de fond et %d tuiles de relief, toutes au contact de l'emprise ; "
            "aucun sommet à plus d'une tuile de marge" % (len(tuiles), len(rel)))

    # --- critère 3 : les mentions
    with open(os.path.join(dossier, "LICENCE.txt"), encoding="utf-8") as f:
        paras = [p for p in f.read().split("\n\n") if p.strip()]
    paras = [p.strip() for p in paras]
    attendus = licence.paragraphes(m["entrees"]["relief"])
    for a in attendus:
        if a not in paras:
            echec("paragraphe absent de LICENCE.txt : %s…" % a[:60])
    src = licence.sources_relief(m["entrees"]["relief"])
    if "glo90" not in src and licence.GLO90_LONG in paras:
        echec("LICENCE.txt porte le paragraphe GLO-90 sans dalle GLO-90 au manifeste")
    if "glo30" not in src and licence.GLO30_LONG in paras:
        echec("LICENCE.txt porte le paragraphe GLO-30 sans dalle GLO-30 au manifeste")
    att = licence.attributions_courtes(m["entrees"]["relief"])
    sts = {}
    for t in ("clair", "sombre"):
        with open(os.path.join(dossier, "style-%s.json" % t), encoding="utf-8") as f:
            sts[t] = json.load(f)
        for nom, attendu in att.items():
            if sts[t]["sources"][nom].get("attribution") != attendu:
                echec("attribution de la source %s du style %s : %r au lieu de %r"
                      % (nom, t, sts[t]["sources"][nom].get("attribution"), attendu))
    journal("mentions : %d paragraphes de LICENCE.txt conformes ; attributions courtes des deux styles conformes" % len(attendus))

    # --- critère 4 : le fond se tait
    for t, s in sts.items():
        trop = stylemod.sature(s)
        if trop:
            echec("style %s : couleurs au-delà de 60 %% de saturation HSL : %s" % (t, trop))
        cs = stylemod.couleurs(s)
        mx = max(stylemod.saturation_hsl(*c) for _, c in cs)
        res["saturation_max_" + t] = round(mx, 3)
    journal("le fond se tait : saturation HSL maximale %.1f %% (clair), %.1f %% (sombre)"
            % (100 * res["saturation_max_clair"], 100 * res["saturation_max_sombre"]))
    return not res["echecs"], res


def verifier(dossier, journal=print):
    ok, res = controles(dossier, journal)
    journal("manifeste comparable sha256 : %s" % res.get("manifeste_comparable_sha256"))
    journal("verifier : " + ("OK" if ok else "ÉCHEC (%d)" % len(res["echecs"])))
    return ok
