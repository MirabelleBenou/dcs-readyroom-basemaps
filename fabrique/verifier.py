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

from . import glyphes, licence, manifeste, merc, style as stylemod
from .emprise import depuis_geojson
from .fabriquer import emprise_merc

FICHIERS = ("fond.pmtiles", "relief.pmtiles", "style-clair.json", "style-sombre.json", "LICENCE.txt",
            "manifeste.json", "sprite/papier.json", "sprite/papier.png", "sprite/papier@2x.json",
            "sprite/papier@2x.png")


def en_tete(chemin):
    with open(chemin, "rb") as f:
        return deserialize_header(f.read(127))


def lire_tuiles(chemin):
    """Les tuiles d'un PMTiles, une à une (un générateur : rien n'est gardé) : (z, x, y, octets décompressés).
    Un PMTiles sans tuile n'en donne aucune."""
    with open(chemin, "rb") as f:
        src = MmapSource(f)
        h = deserialize_header(src(0, 127))
        if h["tile_entries_count"] == 0:
            return
        for (z, x, y), data in all_tiles(src):
            if h["tile_compression"] == Compression.GZIP:
                data = gzip.decompress(data)
            yield z, x, y, bytes(data)


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
    chemin_fond = os.path.join(dossier, "fond.pmtiles")
    compte = {"city": 0, "town": 0}
    distincts = {"city": set(), "town": set()}
    n_tuiles = hors_emprise_tuile = hors_marge = 0
    parametres = {"y_coord_down": True}
    for z, x, y, data in lire_tuiles(chemin_fond):
        n_tuiles += 1
        bt = box(*merc.bornes_tuile(z, x, y))
        if not em.intersects(bt):
            hors_emprise_tuile += 1
        t = mapbox_vector_tile.decode(data, default_options=parametres)
        xmin, ymin, xmax, ymax = merc.bornes_tuile(z, x, y)
        taille = xmax - xmin
        cx_tous, cy_tous = [], []
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
                    cx_tous.append(cx)
                    cy_tous.append(cy)
        if cx_tous:
            wx = xmin + np.asarray(cx_tous, dtype="float64") / merc.ETENDUE_MVT * taille
            wy = ymax - np.asarray(cy_tous, dtype="float64") / merc.ETENDUE_MVT * taille
            # la marge se mesure à l'emprise elle-même (son polygone), pas à sa boîte : une tuile de marge au plus
            hors_marge += int((~shapely.dwithin(em, shapely.points(wx, wy), taille)).sum())
    res["tuiles_fond"] = n_tuiles
    res["villes"] = len(distincts["city"])
    res["bourgs"] = len(distincts["town"])
    res["entites_lieux_toutes_tuiles"] = dict(compte)
    journal("ni village : aucune entité place hors city/town ; %d city et %d town distinctes (%d et %d entités sur "
            "toutes les tuiles)" % (res["villes"], res["bourgs"], compte["city"], compte["town"]))

    # --- les noms : écrits (vus dans les tuiles), tus (au manifeste), et chaque plage de glyphes demandée existe
    noms_m = m.get("noms", {})
    res["noms"] = {}
    for classe in ("city", "town"):
        ecrits_m = {n for _, n in noms_m.get(classe, {}).get("ecrits", [])}
        tus_m = noms_m.get(classe, {}).get("tus", [])
        res["noms"][classe] = {"ecrits": len(distincts[classe]), "tus": len(tus_m)}
        if distincts[classe] != ecrits_m:
            echec("les noms %s des tuiles ne sont pas ceux du manifeste : %r au lieu de %r"
                  % (classe, sorted(distincts[classe]), sorted(ecrits_m)))
        for nom in sorted(distincts[classe]):
            texte = nom + (nom.upper() if classe == "city" else "")
            for c in sorted(set(texte)):
                debut = ord(c) // 256 * 256
                f = os.path.join(dossier, "glyphs", glyphes.NOM_PILE, "%d-%d.pbf" % (debut, debut + 255))
                if not os.path.isfile(f):
                    echec("le nom %r demande la plage de glyphes %d-%d, absente de la pile %s"
                          % (nom, debut, debut + 255, glyphes.NOM_PILE))
    journal("noms : %d villes et %d bourgs écrits, %d et %d tus (par classe : %s)"
            % (res["noms"]["city"]["ecrits"], res["noms"]["town"]["ecrits"], res["noms"]["city"]["tus"],
               res["noms"]["town"]["tus"], json.dumps(res["noms"], sort_keys=True)))

    # --- critère 5 : l'emprise
    if hors_emprise_tuile:
        echec("%d tuiles de fond.pmtiles ne touchent pas l'emprise" % hors_emprise_tuile)
    if hors_marge:
        echec("%d sommets du fond à plus d'une tuile de l'emprise" % hors_marge)
    n_rel = 0
    for z, x, y, data in lire_tuiles(os.path.join(dossier, "relief.pmtiles")):
        n_rel += 1
        b = merc.bornes_tuile(z, x, y)
        if not em.intersects(box(*b)):
            echec("tuile de relief hors de l'emprise : %d/%d/%d" % (z, x, y))
            continue
        a = np.array(Image.open(io.BytesIO(data)).convert("RGBA"))[..., 3]
        tr = from_origin(b[0], b[3], (b[2] - b[0]) / a.shape[1], (b[3] - b[1]) / a.shape[0])
        dedans = rasterize([(em, 1)], out_shape=a.shape, transform=tr, fill=0, all_touched=True, dtype="uint8").astype(bool)
        if (a[~dedans] > 0).any():
            echec("relief non transparent hors de l'emprise : %d/%d/%d" % (z, x, y))
    res["tuiles_relief"] = n_rel
    journal("emprise : %d tuiles de fond et %d tuiles de relief, toutes au contact de l'emprise ; "
            "aucun sommet à plus d'une tuile de marge (mesurée au polygone)" % (n_tuiles, n_rel))

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
        for couche, cle, texte in stylemod.couleurs_invalides(s):
            echec("style %s : la couleur %r (couche %s, %s) n'est ni #rrggbb ni rgb()/rgba()" % (t, texte, couche, cle))
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
