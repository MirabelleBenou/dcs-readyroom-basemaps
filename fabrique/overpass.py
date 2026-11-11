"""extraire-overpass : rapatrie une petite emprise dans un .osm.gz (Overpass, HTTP seulement)."""
import gzip
import os
import time
import urllib.error
import urllib.request

from shapely.geometry import box

from .emprise import lire_emprise, surface_km2

URL = "http://overpass-api.de/api/interpreter"
USER_AGENT = "dcs-readyroom-basemaps/0 (+https://github.com/MirabelleBenou/dcs-readyroom-basemaps)"
SURFACE_MAX_KM2 = 5000.0
ESSAIS_MAX = 5
PAUSE_S = 35                      # entre 30 et 45 s
CODES_REESSAI = (502, 503, 504)


class OverpassErreur(RuntimeError):
    pass


def verifier_surface(geom, maximum=SURFACE_MAX_KM2):
    """Le refus porte sur la BOÎTE interrogée (c'est elle qu'Overpass reçoit), pas sur la surface du polygone."""
    s = surface_km2(box(*geom.bounds))
    if s > maximum:
        raise OverpassErreur(
            "boîte de l'emprise trop grande pour Overpass : %.0f km2 (maximum %.0f km2) ; "
            "les grandes emprises se lisent dans un extrait de fichier" % (s, maximum))
    return s


def requete(boite):
    """La requête : les clés utiles au fond, sans village, hameau, habitation isolée ni lieu-dit."""
    s, w, n, e = boite[1], boite[0], boite[3], boite[2]
    b = "(%.6f,%.6f,%.6f,%.6f)" % (s, w, n, e)
    return (
        "[out:xml][timeout:300];\n"
        "(\n"
        '  way["natural"="water"]%(b)s;\n'
        '  relation["natural"="water"]%(b)s;\n'
        '  way["landuse"="reservoir"]%(b)s;\n'
        '  relation["landuse"="reservoir"]%(b)s;\n'
        '  way["waterway"~"^(river|stream|canal)$"]%(b)s;\n'
        '  way["landuse"="forest"]%(b)s;\n'
        '  relation["landuse"="forest"]%(b)s;\n'
        '  way["natural"="wood"]%(b)s;\n'
        '  relation["natural"="wood"]%(b)s;\n'
        '  way["landuse"="residential"]%(b)s;\n'
        '  relation["landuse"="residential"]%(b)s;\n'
        '  way["highway"~"^(motorway|trunk|primary|secondary)(_link)?$"]%(b)s;\n'
        '  way["railway"="rail"]%(b)s;\n'
        '  way["aeroway"="runway"]%(b)s;\n'
        '  node["place"~"^(city|town)$"]%(b)s;\n'
        '  way["boundary"="administrative"]["admin_level"="2"]["maritime"!="yes"]%(b)s;\n'
        ");\n"
        "(._;>;);\n"
        "out body;\n"
    ) % {"b": b}


def _interroger(corps, ouvrir=urllib.request.urlopen, dormir=time.sleep, journal=print):
    derniere = None
    for essai in range(1, ESSAIS_MAX + 1):
        req = urllib.request.Request(URL, data=("data=" + urllib.request.quote(corps)).encode(),
                                     headers={"User-Agent": USER_AGENT,
                                              "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with ouvrir(req, timeout=400) as r:
                data = r.read()
            if b"<osm" not in data[:2000]:
                raise OverpassErreur("réponse sans élément osm : %r" % data[:200])
            if b"runtime error" in data[-3000:] or b"<remark> runtime" in data[-3000:]:
                raise OverpassErreur("erreur d'exécution Overpass : %r" % data[-300:])
            return data
        except urllib.error.HTTPError as e:
            derniere = "HTTP %s" % e.code
            if e.code not in CODES_REESSAI:
                raise OverpassErreur("Overpass a répondu %s" % e.code)
        except (urllib.error.URLError, OverpassErreur, TimeoutError, OSError) as e:
            derniere = repr(e)
        journal("Overpass : essai %d/%d refusé (%s)" % (essai, ESSAIS_MAX, derniere))
        if essai < ESSAIS_MAX:
            dormir(PAUSE_S)
    raise OverpassErreur("Overpass a refusé %d fois de suite ; dernière réponse : %s" % (ESSAIS_MAX, derniere))


def extraire(chemin_emprise, sortie, ouvrir=urllib.request.urlopen, dormir=time.sleep, journal=print):
    geom = lire_emprise(chemin_emprise)
    verifier_surface(geom)
    if os.path.exists(sortie):
        raise OverpassErreur("%s existe déjà" % sortie)
    data = _interroger(requete(geom.bounds), ouvrir, dormir, journal)
    tmp = sortie + ".part"
    with open(tmp, "wb") as f:
        with gzip.GzipFile(fileobj=f, mode="wb", mtime=0, compresslevel=9) as g:
            g.write(data)
    os.replace(tmp, sortie)
    return len(data)
