"""Lecture des extraits OpenStreetMap (.osm, .osm.gz, .osm.pbf) vers le magasin sur disque, en Mercator.

Première passe : chaque fichier est lu une fois ; les entités retenues (celles qui touchent la boîte de l'emprise)
se rangent, géométrie entière, dans le magasin. Les positions des nœuds passent par un index disque de pyosmium
(`sparse_file_array`, dans le dossier de travail) : la mémoire ne grandit pas avec le fichier.
Puis les chemins de même classe qui se joignent par un nœud se recollent en lignes continues (par identifiants de
nœuds, jamais par coordonnées), et les zones résidentielles qui se touchent s'unissent.

Le fichier doit être trié (nœuds, chemins, relations), comme tout extrait Geofabrik ou Overpass `out body`.
Les identifiants servent de graine : une entité garde son identifiant (`n123`, `w456`, `r789`) ; une ligne recollée
prend `c` et le plus petit identifiant de ses chemins.
"""
import json
import math
import os

import numpy as np
import osmium
import shapely
from shapely.geometry import LineString, Point
from shapely.validation import make_valid

from . import merc
from .merc import lonlat_vers_merc

CLASSES_ROUTE = ("motorway", "trunk", "primary", "secondary")
COURS_EAU = ("river", "stream", "canal")
LIEUX = ("city", "town")          # ni village, ni hameau, ni habitation isolée, ni lieu-dit : jamais lus
SEUIL_RUISSEAU_M = 2500.0         # un ruisseau ou un canal de plus de 2,5 km, une fois les tronçons recollés
SEUIL_LOCALITE_M2 = 1.0e6         # plus d'1 km2 au sol, une fois les zones unies

FILTRE_ZONES = (("natural", "water"), ("landuse", "reservoir"), ("landuse", "forest"), ("natural", "wood"),
                ("landuse", "residential"))


def nom_en_usage(tags):
    """Le nom : le premier présent de `name:en`, `int_name`, `name`."""
    for cle in ("name:en", "int_name", "name"):
        v = tags.get(cle)
        if v and v.strip():
            return v
    return None


def _coords(o):
    """([(lon, lat)...], premier et dernier identifiants de nœud) d'un chemin ; les nœuds sans position sont ignorés."""
    pts, refs = [], []
    for n in o.nodes:
        if n.location.valid():
            pts.append((n.lon, n.lat))
            refs.append(n.ref)
    if len(pts) < 2:
        return None, 0, 0
    x, y = lonlat_vers_merc([p[0] for p in pts], [p[1] for p in pts])
    return np.column_stack([x, y]), refs[0], refs[-1]


def _polygone(a):
    wkb = osmium.geom.WKBFactory().create_multipolygon(a)
    g = shapely.from_wkb(bytes.fromhex(wkb) if isinstance(wkb, str) else wkb)
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


def _touche(boite, g):
    x0, y0, x1, y1 = g.bounds
    return not (x1 < boite[0] or x0 > boite[2] or y1 < boite[1] or y0 > boite[3])


class Lecture:
    """Ce que la première passe a vu des noms : les noms écrits et les noms tus, par classe."""

    def __init__(self):
        self.noms = {"city": {"ecrits": [], "tus": []}, "town": {"ecrits": [], "tus": []}}

    def resume(self):
        return {c: {"ecrits": sorted(v["ecrits"]), "tus": sorted(v["tus"])} for c, v in self.noms.items()}


def charger(fichiers, magasin, emprise_merc, travail, couvre=None, journal=print):
    """Première passe : range dans le magasin les entités retenues. `couvre(texte)` dit si la pile de polices écrit ce
    texte (un nom dont un caractère manque aux deux polices ne s'écrit pas). Rend une `Lecture`."""
    lecture = Lecture()
    boite = emprise_merc.bounds
    shapely.prepare(emprise_merc)
    for i, chemin in enumerate(fichiers):
        index = os.path.join(travail, "noeuds-%d.idx" % i)
        fp = osmium.FileProcessor(str(chemin), osmium.osm.NODE | osmium.osm.WAY | osmium.osm.AREA) \
            .with_locations("sparse_file_array," + index).with_areas(osmium.filter.TagFilter(*FILTRE_ZONES))
        for o in fp:
            t = o.tags
            if o.is_node():
                place = t.get("place")
                if place in LIEUX:
                    nom = nom_en_usage(t)
                    if not nom:
                        continue
                    x, y = lonlat_vers_merc(o.location.lon, o.location.lat)
                    p = Point(float(x), float(y))
                    if not shapely.contains(emprise_merc, p):
                        continue
                    ident = "n%d" % o.id
                    # une ville s'écrit en capitales : le texte transformé doit lui aussi tenir dans la pile
                    ecrit = couvre is None or (couvre(nom) and (couvre(nom.upper()) if place == "city" else True))
                    if not ecrit:
                        lecture.noms[place]["tus"].append(ident)
                        journal("nom tu : %s (%s) : un caractère manque aux deux polices" % (ident, place))
                        continue
                    if magasin.ajouter("lieux", ident, {"place": place, "nom": nom}, p):
                        lecture.noms[place]["ecrits"].append((ident, nom))
            elif o.is_area():
                ident = ("w%d" if o.from_way() else "r%d") % o.orig_id()
                cle = (t.get("natural") == "water" or t.get("landuse") == "reservoir", t.get("landuse") == "forest"
                       or t.get("natural") == "wood", t.get("landuse") == "residential")
                if not any(cle):
                    continue
                g = _polygone(o)
                if g is None or not _touche(boite, g):
                    continue
                if cle[0]:
                    magasin.ajouter("eau", ident, {}, g)
                elif cle[1]:
                    magasin.ajouter("forets", ident, {}, g)
                else:
                    magasin.ajouter_zone(o.orig_id() * 2 + (0 if o.from_way() else 1), {}, g)
            elif o.is_way():
                ident = o.id
                hw = t.get("highway")
                candidates = []
                if hw and hw.replace("_link", "") in CLASSES_ROUTE:
                    candidates.append(("route:%s:%d" % (hw.replace("_link", ""), hw.endswith("_link")),
                                       {"classe": hw.replace("_link", ""), "lien": 1 if hw.endswith("_link") else 0}))
                elif t.get("waterway") in COURS_EAU:
                    cl = "river" if t["waterway"] == "river" else "stream"
                    candidates.append(("cours:%s" % cl, {"classe": cl}))
                elif t.get("railway") == "rail":
                    candidates.append(("rail", {}))
                elif t.get("aeroway") == "runway":
                    candidates.append(("piste", {}))
                if t.get("boundary") == "administrative" and t.get("admin_level") == "2" and t.get("maritime") != "yes":
                    candidates.append(("frontiere", {}))
                for groupe, props in candidates:
                    c, n0, n1 = _coords(o)
                    if c is None:
                        break
                    l = LineString(c)
                    if not _touche(boite, l):
                        break
                    if groupe == "piste":
                        magasin.ajouter("pistes", "w%d" % ident, {}, l)
                    else:
                        magasin.ajouter_brut(groupe, ident, n0, n1, props, l)
    magasin.valider()
    return lecture


# ---------------------------------------------------------------------- recoller les chemins
def chaines(a, b):
    """Les lignes continues d'un groupe de chemins, par leurs extrémités (identifiants de nœuds a et b de chaque chemin).

    Deux chemins se joignent là où exactement deux extrémités se rencontrent (un nœud de degré 2) ; une jonction à
    trois chemins ou plus ne recolle rien. Rend [[(indice du chemin, sens)]] : sens +1 de a vers b, -1 de b vers a ;
    les chemins sont pris dans l'ordre des indices (donc des identifiants), le résultat ne dépend de rien d'autre."""
    n = len(a)
    noeuds = np.concatenate([a, b])
    proprio = np.concatenate([np.arange(n), np.arange(n)])
    cote = np.concatenate([np.zeros(n, dtype=int), np.ones(n, dtype=int)])
    ordre = np.lexsort((cote, proprio, noeuds))
    e, o, c = noeuds[ordre], proprio[ordre], cote[ordre]
    debuts = np.flatnonzero(np.r_[True, e[1:] != e[:-1]]) if len(e) else np.array([], dtype=int)
    tailles = np.diff(np.r_[debuts, len(e)]) if len(e) else np.array([], dtype=int)
    liaison = {}
    for d in debuts[tailles == 2]:
        w1, s1, w2, s2 = int(o[d]), int(c[d]), int(o[d + 1]), int(c[d + 1])
        if w1 == w2:
            continue                       # un chemin fermé : il se suffit
        liaison[(w1, s1)] = (w2, s2)
        liaison[(w2, s2)] = (w1, s1)
    vus = np.zeros(n, dtype=bool)
    out = []
    for w in range(n):
        if vus[w]:
            continue
        # remonter jusqu'au début de la ligne (ou tourner si c'est un cycle)
        cur, entree = w, 0
        while (cur, entree) in liaison:
            w2, s2 = liaison[(cur, entree)]
            if w2 == w:
                cur, entree = w, 0
                break
            cur, entree = w2, 1 - s2
        chaine = [(cur, 1 if entree == 0 else -1)]
        vus[cur] = True
        sortie = 1 - entree
        while (cur, sortie) in liaison:
            w2, s2 = liaison[(cur, sortie)]
            if vus[w2]:
                break
            chaine.append((w2, 1 if s2 == 0 else -1))
            vus[w2] = True
            cur, sortie = w2, 1 - s2
        out.append(chaine)
    return out


def _orienter(c):
    """Sens canonique : début <= fin (coordonnées arrondies), pour une graine indépendante du sens des chemins."""
    if (round(c[0][0]), round(c[0][1])) > (round(c[-1][0]), round(c[-1][1])):
        return c[::-1]
    return c


def longueur_sol(c):
    """Longueur au sol (m) d'une ligne en Mercator : chaque segment pris à l'échelle de sa latitude."""
    c = np.asarray(c)
    if len(c) < 2:
        return 0.0
    seg = np.hypot(*np.diff(c, axis=0).T)
    mid = (c[1:, 1] + c[:-1, 1]) / 2.0
    lat = np.degrees(2 * np.arctan(np.exp(mid / merc.RAYON)) - math.pi / 2)
    return float((seg * np.cos(np.radians(lat))).sum())


def assembler_chaines(magasin):
    """Recolle les chemins bruts en lignes continues (couches routes, cours_eau, rail, frontieres) ; filtre les ruisseaux
    d'au moins 2,5 km APRÈS le recollage. Vide la table des chemins bruts. Rend {couche: nombre}."""
    couche_de = {"route": "routes", "cours": "cours_eau", "rail": "rail", "frontiere": "frontieres"}
    compte = {}
    for groupe in magasin.groupes_bruts():
        lignes = magasin.db.execute("SELECT ident, n0, n1 FROM brut WHERE groupe=? ORDER BY ident", (groupe,)).fetchall()
        if not lignes:
            continue
        ids = np.array([r[0] for r in lignes], dtype=np.int64)
        a = np.array([r[1] for r in lignes], dtype=np.int64)
        b = np.array([r[2] for r in lignes], dtype=np.int64)
        couche = couche_de[groupe.split(":")[0]]
        for chaine in chaines(a, b):
            morceaux, props = [], None
            for k, (i, sens) in enumerate(chaine):
                r = magasin.db.execute("SELECT props, geom FROM brut WHERE groupe=? AND ident=?",
                                       (groupe, int(ids[i]))).fetchone()
                props = json.loads(r[0])
                c = np.asarray(shapely.from_wkb(r[1]).coords)
                if sens < 0:
                    c = c[::-1]
                morceaux.append(c if k == 0 else c[1:])
            coords = _orienter(np.vstack(morceaux))
            if len(coords) < 2:
                continue
            if groupe == "cours:stream" and longueur_sol(coords) <= SEUIL_RUISSEAU_M:
                continue
            ident = "c%d" % min(int(ids[i]) for i, _ in chaine)
            if magasin.ajouter(couche, ident, props, LineString(coords)):
                compte[couche] = compte.get(couche, 0) + 1
    magasin.db.execute("DELETE FROM brut")
    magasin.valider()
    return compte


def assembler_localites(magasin):
    """Unit les zones résidentielles qui se touchent (par un côté ou une surface, pas par un point) ; garde les unions de
    plus d'1 km2 au sol. Une zone par entité `localites`. Rend le nombre d'entités."""
    ids = [r[0] for r in magasin.db.execute("SELECT ident FROM zone ORDER BY ident")]
    parent = {i: i for i in ids}

    def racine(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def geom(i):
        return shapely.from_wkb(magasin.db.execute("SELECT geom FROM zone WHERE ident=?", (i,)).fetchone()[0])

    for i, g in magasin.zones():
        x0, y0, x1, y1 = g.bounds
        shapely.prepare(g)
        for (j,) in magasin.db.execute(
                "SELECT z.ident FROM rtz JOIN zone z ON z.id = rtz.id WHERE rtz.minx<=? AND rtz.maxx>=? AND rtz.miny<=? "
                "AND rtz.maxy>=? AND z.ident>?", (x1, x0, y1, y0, i)).fetchall():
            if racine(i) == racine(j):
                continue
            h = geom(j)
            if shapely.intersects(g, h) and shapely.get_dimensions(shapely.intersection(g, h)) >= 1:
                parent[racine(j)] = racine(i)
    composantes = {}
    for i in ids:
        composantes.setdefault(racine(i), []).append(i)
    n = 0
    for membres in sorted(composantes.values(), key=min):
        u = shapely.union_all([geom(i) for i in sorted(membres)])
        parts = [p for p in (u.geoms if hasattr(u, "geoms") else [u]) if p.geom_type == "Polygon" and not p.is_empty]
        parts.sort(key=lambda p: (round(p.centroid.x), round(p.centroid.y)))
        for k, p in enumerate(parts):
            lat = float(merc.merc_vers_lonlat(p.centroid.x, p.centroid.y)[1])
            if p.area * merc.echelle_sol(lat) ** 2 > SEUIL_LOCALITE_M2:
                if magasin.ajouter("localites", "l%d_%d" % (min(membres), k), {}, p):
                    n += 1
    magasin.db.execute("DELETE FROM zone")
    magasin.db.execute("DELETE FROM rtz")
    magasin.valider()
    return n


def lire(fichiers, magasin, emprise_merc, travail, couvre=None, journal=print):
    """Toute la lecture : première passe, puis recollage. Rend la `Lecture`."""
    lecture = charger(fichiers, magasin, emprise_merc, travail, couvre, journal)
    assembler_chaines(magasin)
    assembler_localites(magasin)
    return lecture
