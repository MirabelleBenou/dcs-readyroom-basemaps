"""La fabrication : emprise + extraits OSM + dalles Copernicus -> un dossier servable."""
import json
import os
import resource
import shutil
import sys
import time

import numpy as np
import shapely
from pmtiles.tile import TileType

from . import bandes, glyphes, licence, manifeste, merc, mvt, osm, reliefpm, sprite, style, terrain
from .couches import COUCHES, Constructeur
from .emprise import lire_emprise
from .sources import preparer_dalles, sha256_fichier

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SortieErreur(RuntimeError):
    pass


def verifier_dossier_sortie(sortie):
    """Un dossier de sortie qui existe déjà et n'est pas vide : refus, sans rien écrire."""
    if os.path.exists(sortie):
        if not os.path.isdir(sortie):
            raise SortieErreur("%s existe et n'est pas un dossier" % sortie)
        if os.listdir(sortie):
            raise SortieErreur("le dossier de sortie %s existe et n'est pas vide : rien n'est écrit" % sortie)


def emprise_merc(emprise_wgs):
    def f(c):
        return np.column_stack(merc.lonlat_vers_merc(c[:, 0], c[:, 1]))
    return shapely.transform(emprise_wgs, f)


class Chrono:
    def __init__(self, journal=print):
        self.t0 = time.time()
        self.t = self.t0
        self.journal = journal
        self.etapes = {}

    def etape(self, nom):
        n = time.time()
        self.etapes[nom] = round(n - self.t, 2)
        self.journal("[%6.1f s] %s" % (n - self.t0, nom))
        self.t = n


def fabriquer(chemin_emprise, fichiers_osm, cache_relief, sortie, journal=print, entrees_relief=None):
    """Fabrique le dossier de sortie. `entrees_relief` (tests) remplace le choix et le téléchargement des dalles."""
    verifier_dossier_sortie(sortie)
    for f in fichiers_osm:
        if not os.path.isfile(f):
            raise FileNotFoundError("extrait OSM introuvable : %s" % f)
    chrono = Chrono(journal)
    em_wgs = lire_emprise(chemin_emprise)
    em = emprise_merc(em_wgs)
    lon0, lat0, lon1, lat1 = em_wgs.bounds
    lat_centre = em_wgs.centroid.y
    # --- sources
    if entrees_relief is None:
        entrees_relief = preparer_dalles((lon0, lat0, lon1, lat1), cache_relief)
    chrono.etape("dalles du relief (%d)" % len(entrees_relief))
    donnees = osm.lire(fichiers_osm)
    chrono.etape("lecture d'OpenStreetMap")
    grille = terrain.Grille(em.bounds)
    dem = terrain.construire_dem(grille, entrees_relief)
    dedans = terrain.masque_emprise(grille, em)
    ter = terrain.Terrain(grille, dem, dedans, lat_centre)
    chrono.etape("relief rééchantillonné, mer")
    cons = Constructeur(donnees, ter, em, em_wgs, lat_centre)
    chrono.etape("unions (forêts, localités), polygone de mer")

    tuiles = {}
    champs = {}
    for b in bandes.BANDES:
        c = cons.bande(b)
        index = {nom: mvt.IndexCouche(f) for nom, f in c.items() if f}
        for nom, ic in index.items():
            for _, p in ic.features:
                for k, v in p.items():
                    champs.setdefault(nom, {})[k] = "Number" if isinstance(v, (int, float)) else "String"
        n = 0
        for z in range(b[0], b[1] + 1):
            for x, y in mvt.tuiles_du_zoom(z, em):
                data = mvt.encoder_tuile(z, x, y, index)
                if data is not None:
                    tuiles[(z, x, y)] = mvt.gzip_sans_date(data)
                    n += 1
        chrono.etape("bande %d-%d : %s, %d tuiles" % (b[0], b[1], ", ".join("%s=%d" % (k, len(v)) for k, v in c.items() if v), n))

    rel = reliefpm.construire_relief(ter, em, cons.t.polygone_mer() if cons.mer is None else cons.mer)
    chrono.etape("relief : %d tuiles" % len(rel))

    # --- écriture, dans un dossier voisin puis renommé
    parent = os.path.dirname(os.path.abspath(sortie)) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = os.path.join(parent, os.path.basename(os.path.abspath(sortie)) + ".en-cours")
    if os.path.exists(tmp):
        shutil.rmtree(tmp)
    os.makedirs(tmp)
    att = licence.attributions_courtes(entrees_relief)
    vector_layers = [{"id": n, "fields": dict(sorted(champs.get(n, {}).items())), "minzoom": bandes.ZOOM_MIN_FOND,
                      "maxzoom": bandes.ZOOM_MAX_FOND} for n in COUCHES if n in champs]
    mvt.ecrire_pmtiles(os.path.join(tmp, "fond.pmtiles"), tuiles, em_wgs,
                       {"name": "fond", "format": "pbf", "type": "baselayer", "attribution": att["fond"],
                        "vector_layers": vector_layers})
    mvt.ecrire_pmtiles(os.path.join(tmp, "relief.pmtiles"), rel, em_wgs,
                       {"name": "relief", "format": "webp", "type": "overlay", "attribution": att["relief"]},
                       tile_type=TileType.WEBP)
    chrono.etape("fond.pmtiles et relief.pmtiles écrits")
    centre = ((lon0 + lon1) / 2, (lat0 + lat1) / 2)
    style.ecrire_style(os.path.join(tmp, "style-clair.json"), style.construire(style.CLAIR, att, centre))
    style.ecrire_style(os.path.join(tmp, "style-sombre.json"), style.construire(style.SOMBRE, att, centre))
    sprite.ecrire_sprite(tmp)
    dossier_polices = os.path.join(RACINE, "polices")
    glyphes.fabriquer_glyphes(dossier_polices, tmp)
    with open(os.path.join(tmp, "LICENCE.txt"), "w", encoding="utf-8") as f:
        f.write(licence.texte(entrees_relief))
    chrono.etape("styles, sprite, glyphes, licence")

    with open(chemin_emprise, encoding="utf-8") as f:
        gj = json.load(f)
    m = manifeste.construire(
        RACINE,
        {**manifeste.entree_fichier(chemin_emprise), "geometrie": gj},
        [manifeste.entree_fichier(f) for f in fichiers_osm],
        entrees_relief,
        [manifeste.entree_fichier(os.path.join(dossier_polices, fn), "polices/" + fn) for _, fn in glyphes.POLICES],
        os.path.basename(chemin_emprise))
    manifeste.ecrire(os.path.join(tmp, "manifeste.json"), m)
    # --- mise en place
    if os.path.isdir(sortie):
        os.rmdir(sortie)
    os.replace(tmp, sortie)
    chrono.etape("manifeste écrit ; terminé")
    pic = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    journal("durée totale %.1f s ; mémoire au plus haut %.0f Mio" % (time.time() - chrono.t0, pic))
    return {"etapes": chrono.etapes, "duree_s": round(time.time() - chrono.t0, 1), "memoire_mio": round(pic)}
