"""La fabrication : emprise + extraits OSM + dalles Copernicus -> un dossier servable, par blocs, en mémoire bornée.

Le travail se fait par blocs : un bloc est une tuile du zoom pivot (`bloc_zoom`, 7 à 10, 8 par défaut). Chaque bloc lit au
magasin les entités qui le touchent (avec la marge d'un tampon de tuile), les traite entières, les coupe aux tuiles des
zooms >= pivot qu'il contient, encode ces tuiles et les écrit au dépôt : rien ne garde toutes les tuiles en mémoire.
Les tuiles des zooms < pivot (le survol) se rangent sur disque morceau par morceau, puis s'assemblent une à une.
Chaque tuile ne dépend que des données autour d'elle : le résultat ne dépend pas du zoom pivot.
"""
import json
import os
import resource
import shutil
import tempfile
import time

import numpy as np
import shapely
from pmtiles.tile import TileType
from shapely.geometry import box

from . import bandes, couches, derive, glyphes, grille as mgrs, licence, magasin as mag, manifeste, merc, mvt, osm, \
    reliefpm, sprite, style, terrain
from .emprise import lire_emprise
from .progres import Muet
from .sources import preparer_dalles

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLOC_ZOOM_DEFAUT = 8
BLOC_ZOOM_MIN, BLOC_ZOOM_MAX = 7, 10


class SortieErreur(RuntimeError):
    pass


def verifier_dossier_sortie(sortie):
    """Un dossier de sortie qui existe déjà et n'est pas vide : refus, sans rien écrire."""
    if os.path.exists(sortie):
        if not os.path.isdir(sortie):
            raise SortieErreur("%s existe et n'est pas un dossier" % sortie)
        if os.listdir(sortie):
            raise SortieErreur("le dossier de sortie %s existe et n'est pas vide : rien n'est écrit" % sortie)


def poser_droits(dossier):
    """Fichiers en 644 et dossiers en 755, posés explicitement (quel que soit l'umask) ; un lien symbolique est une erreur."""
    for racine, dirs, fichiers in os.walk(dossier):
        for nom in dirs + fichiers:
            p = os.path.join(racine, nom)
            if os.path.islink(p):
                raise SortieErreur("lien symbolique dans la sortie : %s" % p)
        os.chmod(racine, 0o755)
        for f in fichiers:
            os.chmod(os.path.join(racine, f), 0o644)


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


def memoire_mio():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


class Contexte:
    """Ce que partagent les blocs : l'emprise, le magasin, la pyramide, les dépôts, la grille MGRS par bande."""

    def __init__(self, em, em_wgs, mag_, pyr, depot, champs):
        self.em, self.em_wgs, self.magasin, self.pyr, self.depot, self.champs = em, em_wgs, mag_, pyr, depot, champs
        self._grilles = {}

    def grille(self, b):
        """Les lignes et les points de la grille MGRS de la bande, pour toute l'emprise (peu de chose : calculé une fois)."""
        if b not in self._grilles:
            lignes, points = mgrs.construire_grille(self.em, self.em_wgs, bandes.pas_grille(b),
                                                    carres=bandes.carres_100km(b))
            self._grilles[b] = (lignes, points)
        return self._grilles[b]


def _tuiles_touchant(z, em, boite=None):
    """Les tuiles du zoom z qui touchent l'emprise (et la boîte, si donnée), triées."""
    b = em.bounds if boite is None else (max(em.bounds[0], boite[0]), max(em.bounds[1], boite[1]),
                                         min(em.bounds[2], boite[2]), min(em.bounds[3], boite[3]))
    if b[0] > b[2] or b[1] > b[3]:
        return []
    x0, x1, y0, y1 = merc.tuiles_touchant(z, b)
    return [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)
            if shapely.intersects(em, box(*merc.bornes_tuile(z, x, y)))]


def produire_entites(ctx, b, zooms, zone, bloc, coll):
    """Les entités du magasin, entières, à la bande ; coupées aux tuiles des `zooms`. `zone` : la boîte interrogée."""
    for couche in couches.ENTITES:
        for ident, props, g in ctx.magasin.entites(couche, zone):
            for k, (cs, gg, pp) in enumerate(couches.geometries(couche, ident, props, g, b, ctx.em, boite=zone)):
                mvt.repartir(coll, cs, "%s|%06d" % (ident, k), gg, pp, zooms, bloc)
    lignes, points = ctx.grille(b)
    for i, (g, pr) in enumerate(lignes):
        if zone is not None and not _touche(zone, g):
            continue
        mvt.repartir(coll, "grille", "l%06d" % i, g, {"niveau": pr["niveau"], "type": "ligne"}, zooms, bloc)
    for i, (p, pr) in enumerate(points):
        if zone is not None and not _touche(zone, p):
            continue
        mvt.repartir(coll, "grille", "p%06d" % i, p, dict(pr), zooms, bloc)


def _touche(boite, g):
    x0, y0, x1, y1 = g.bounds
    return not (x1 < boite[0] or x0 > boite[2] or y1 < boite[1] or y0 > boite[3])


def produire_derive(ctx, actives, zone, bloc, coll):
    """Les couches calculées du relief (mer, rivage, courbes), cellule par cellule ; `actives` : [(bande, zooms)].
    Les bandes qui partagent leurs cellules partagent aussi les champs."""
    groupes = {}
    for b, zooms in actives:
        groupes.setdefault(derive.cellule_de(b), []).append((b, zooms))
    for (zc, nv), lst in sorted(groupes.items()):
        for x, y in _tuiles_touchant(zc, ctx.em, zone):
            z = derive.zone_de_cellule(zc, x, y, ctx.em)
            if z is None:
                continue
            champs = derive.Champs(ctx.pyr, zc, nv, x, y)
            cle = "%02d/%07d/%07d" % (zc, x, y)
            for b, zooms in lst:
                for couche, c, g, props in derive.fragments(champs, b, z, cle):
                    mvt.repartir_morceau(coll, couche, c, g, props, zooms, bloc)
            del champs


def ecrire_tuiles(ctx, coll):
    """Encode les tuiles du collecteur qui touchent l'emprise, une à une, et les écrit au dépôt."""
    n = 0
    for z, x, y in coll.tuiles():
        if not shapely.intersects(ctx.em, box(*merc.bornes_tuile(z, x, y))):
            coll.prendre(z, x, y)             # une tuile voisine, qui ne touche pas l'emprise : ses morceaux de tampon tombent
            continue
        data = mvt.encoder_tuile(z, x, y, coll.prendre(z, x, y), ctx.champs)
        if data is not None:
            ctx.depot.ecrire(z, x, y, mvt.gzip_sans_date(data))
            n += 1
    return n


def traiter_bloc(ctx, bloc):
    pz, bx, by = bloc
    xmin, ymin, xmax, ymax = merc.bornes_tuile(pz, bx, by)
    m = mvt.tampon(pz)
    zone = (xmin - m, ymin - m, xmax + m, ymax + m)
    coll = mvt.Collecteur()
    actives = [(b, [z for z in range(b[0], b[1] + 1) if z >= pz]) for b in bandes.BANDES]
    actives = [(b, zs) for b, zs in actives if zs]
    for b, zooms in actives:
        produire_entites(ctx, b, zooms, zone, bloc, coll)
    produire_derive(ctx, actives, zone, bloc, coll)
    return ecrire_tuiles(ctx, coll)


def traiter_survol(ctx, pz, travail):
    """Les tuiles des zooms au-dessous du pivot : toutes les entités, une à une, morceaux rangés sur disque."""
    actives = [(b, [z for z in range(b[0], b[1] + 1) if z < pz]) for b in bandes.BANDES]
    actives = [(b, zs) for b, zs in actives if zs]
    if not actives:
        return 0
    fr = mag.Fragments(os.path.join(travail, "fragments.sqlite"))
    try:
        coll = mvt.CollecteurDisque(fr)
        for b, zooms in actives:
            produire_entites(ctx, b, zooms, None, None, coll)
        produire_derive(ctx, actives, None, None, coll)
        return ecrire_tuiles(ctx, coll)
    finally:
        fr.fermer()


def blocs_du_pivot(pz, em):
    return [(pz, x, y) for x, y in _tuiles_touchant(pz, em)]


def fabriquer(chemin_emprise, fichiers_osm, cache_relief, sortie, journal=print, entrees_relief=None,
              bloc_zoom=BLOC_ZOOM_DEFAUT, progres=None):
    """Fabrique le dossier de sortie. `entrees_relief` (tests) remplace le choix et le téléchargement des dalles.
    `progres` (un `progres.Progres`) dit l'avancement ; par défaut, rien.

    Rien ne reste d'une fabrication qui échoue : ni dossier de sortie, ni dossier `.en-cours`, ni dossier de travail."""
    if not BLOC_ZOOM_MIN <= bloc_zoom <= BLOC_ZOOM_MAX:
        raise ValueError("--bloc-zoom doit être entre %d et %d (reçu %r)" % (BLOC_ZOOM_MIN, BLOC_ZOOM_MAX, bloc_zoom))
    verifier_dossier_sortie(sortie)
    for f in fichiers_osm:
        if not os.path.isfile(f):
            raise FileNotFoundError("extrait OSM introuvable : %s" % f)
    em_wgs = lire_emprise(chemin_emprise)
    mgrs.verifier_hemisphere(em_wgs)
    parent = os.path.dirname(os.path.abspath(sortie)) or "."
    os.makedirs(parent, exist_ok=True)
    base = os.path.basename(os.path.abspath(sortie))
    tmp = tempfile.mkdtemp(prefix=base + ".en-cours-", dir=parent)
    travail = tempfile.mkdtemp(prefix=base + ".travail-", dir=parent)
    try:
        res = _fabriquer(chemin_emprise, em_wgs, fichiers_osm, cache_relief, tmp, travail, journal, entrees_relief,
                         bloc_zoom, progres=progres)
        poser_droits(tmp)
        os.replace(tmp, sortie)          # sur un dossier vide qui existe déjà, le rename le remplace : jamais de rmtree
        tmp = None
        return res
    finally:
        shutil.rmtree(travail, ignore_errors=True)
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def _fabriquer(chemin_emprise, em_wgs, fichiers_osm, cache_relief, tmp, travail, journal, entrees_relief, bloc_zoom,
               progres=None, entrees_osm=None, commit=None, theatre=None):
    """Le travail : écrit le dossier dans `tmp`, avec ses fichiers temporaires dans `travail`.
    `entrees_osm` : les entrées OSM du manifeste (par défaut : les fichiers eux-mêmes) ; `commit` : le commit de la
    fabrique, s'il est donné (sinon Git le dit) ; `theatre` : le nom du théâtre, au manifeste."""
    chrono = Chrono(journal)
    progres = progres if progres is not None else Muet()
    em = emprise_merc(em_wgs)
    shapely.prepare(em)
    lon0, lat0, lon1, lat1 = em_wgs.bounds
    # --- sources
    progres.etape("dalles du relief")
    if entrees_relief is None:
        entrees_relief = preparer_dalles((lon0, lat0, lon1, lat1), cache_relief)
    chrono.etape("dalles du relief (%d)" % len(entrees_relief))
    dossier_polices = os.path.join(RACINE, "polices")
    polices = glyphes.charger_polices(dossier_polices)
    progres.etape("lecture d'OpenStreetMap")
    mg = mag.Magasin(os.path.join(travail, "magasin.sqlite"))
    pyr = depot = depot_rel = None
    try:
        lecture = osm.lire(fichiers_osm, mg, em, travail, couvre=lambda t: glyphes.pile_couvre(polices, t), journal=journal)
        chrono.etape("OpenStreetMap lu dans le magasin (%s)" % ", ".join(
            "%s=%d" % (c, mg.compte(c)) for c in couches.ENTITES if mg.compte(c)))
        grille = terrain.Grille(em.bounds)
        pyr = terrain.construire(grille, entrees_relief, em, travail, journal, progres=progres.rappel())
        chrono.etape("relief échantillonné, pyramide et mer globale")
        depot = mvt.Depot(os.path.join(travail, "fond.tuiles"))
        depot_rel = mvt.Depot(os.path.join(travail, "relief.tuiles"))
        champs = {}
        ctx = Contexte(em, em_wgs, mg, pyr, depot, champs)
        blocs = blocs_du_pivot(bloc_zoom, em)
        n = 0
        progres.etape("blocs du zoom %d" % bloc_zoom, 0, len(blocs))
        for i, bloc in enumerate(blocs, 1):
            n += traiter_bloc(ctx, bloc)
            progres.avancer(i, len(blocs))
        chrono.etape("%d blocs du zoom %d : %d tuiles de fond" % (len(blocs), bloc_zoom, n))
        progres.etape("survol (zooms %d à %d)" % (bandes.ZOOM_MIN_FOND, bloc_zoom - 1))
        n = traiter_survol(ctx, bloc_zoom, travail)
        chrono.etape("survol (zooms %d à %d) : %d tuiles de fond" % (bandes.ZOOM_MIN_FOND, bloc_zoom - 1, n))
        progres.etape("tuiles du relief")
        nr = reliefpm.construire_relief(pyr, em, depot_rel)
        chrono.etape("relief : %d tuiles" % nr)

        # --- écriture
        progres.etape("écriture de fond.pmtiles et relief.pmtiles")
        att = licence.attributions_courtes(entrees_relief)
        vector_layers = [{"id": c, "fields": dict(sorted(champs.get(c, {}).items())), "minzoom": bandes.ZOOM_MIN_FOND,
                          "maxzoom": bandes.ZOOM_MAX_FOND} for c in couches.COUCHES if c in champs]
        mvt.ecrire_pmtiles(os.path.join(tmp, "fond.pmtiles"), depot, em_wgs,
                           {"name": "fond", "format": "pbf", "type": "baselayer", "attribution": att["fond"],
                            "vector_layers": vector_layers}, travail)
        mvt.ecrire_pmtiles(os.path.join(tmp, "relief.pmtiles"), depot_rel, em_wgs,
                           {"name": "relief", "format": "webp", "type": "overlay", "attribution": att["relief"]},
                           travail, tile_type=TileType.WEBP)
        chrono.etape("fond.pmtiles et relief.pmtiles écrits")
        progres.etape("styles, sprite, glyphes, licence")
        centre = ((lon0 + lon1) / 2, (lat0 + lat1) / 2)
        style.ecrire_style(os.path.join(tmp, "style-clair.json"), style.construire(style.CLAIR, att, centre))
        style.ecrire_style(os.path.join(tmp, "style-sombre.json"), style.construire(style.SOMBRE, att, centre))
        sprite.ecrire_sprite(tmp)
        glyphes.fabriquer_glyphes(dossier_polices, tmp, polices)
        with open(os.path.join(tmp, "LICENCE.txt"), "w", encoding="utf-8") as f:
            f.write(licence.texte(entrees_relief))
        chrono.etape("styles, sprite, glyphes, licence")
        with open(chemin_emprise, encoding="utf-8") as f:
            gj = json.load(f)
        progres.etape("manifeste")
        pic = memoire_mio()
        m = manifeste.construire(
            RACINE,
            {**manifeste.entree_fichier(chemin_emprise), "geometrie": gj},
            entrees_osm if entrees_osm is not None else [manifeste.entree_fichier(f) for f in fichiers_osm],
            entrees_relief,
            [manifeste.entree_fichier(os.path.join(dossier_polices, fn), "polices/" + fn) for _, fn in glyphes.POLICES],
            os.path.basename(chemin_emprise),
            noms=lecture.resume(),
            execution={"bloc_zoom": bloc_zoom, "duree_s": round(time.time() - chrono.t0, 1),
                       "memoire_mio": round(pic), "travail_octets": mag.taille_dossier(travail),
                       **({"theatre": True} if theatre is not None else {})},
            commit=commit, theatre=theatre)
        manifeste.ecrire(os.path.join(tmp, "manifeste.json"), m)
        travail_octets = mag.taille_dossier(travail)
    finally:
        for d in (depot, depot_rel):
            if d is not None:
                d.fermer()
        mg.fermer()
    chrono.etape("manifeste écrit ; terminé")
    pic = memoire_mio()
    journal("durée totale %.1f s ; mémoire au plus haut %.0f Mio ; dossier de travail %.0f Mio (effacé)"
            % (time.time() - chrono.t0, pic, travail_octets / 2 ** 20))
    return {"etapes": chrono.etapes, "duree_s": round(time.time() - chrono.t0, 1), "memoire_mio": round(pic),
            "travail_mio": round(travail_octets / 2 ** 20)}
