"""Les dalles du relief Copernicus : sélection (GLO-30, repli GLO-90, mer), cache, empreintes."""
import hashlib
import math
import os
import shutil
import urllib.error
import urllib.request

GLO30 = "glo30"
GLO90 = "glo90"
ABSENTE = "absente"

HOTES = {GLO30: "copernicus-dem-30m", GLO90: "copernicus-dem-90m"}
CODES = {GLO30: 10, GLO90: 30}
USER_AGENT = "dcs-readyroom-basemaps (+https://github.com/MirabelleBenou/dcs-readyroom-basemaps)"


class SourceErreur(RuntimeError):
    pass


def nom_dalle(lat, lon):
    """Nom d'une dalle d'un degré, par son coin sud-ouest entier : N35_00_E035_00."""
    return "%s%02d_00_%s%03d_00" % ("N" if lat >= 0 else "S", abs(lat), "E" if lon >= 0 else "W", abs(lon))


def url_dalle(lat, lon, source):
    base = "Copernicus_DSM_COG_%d_%s_DEM" % (CODES[source], nom_dalle(lat, lon))
    return "https://%s.s3.amazonaws.com/%s/%s.tif" % (HOTES[source], base, base)


def dalles_necessaires(boite):
    """Les dalles d'un degré qui recouvrent la boîte (lon_min, lat_min, lon_max, lat_max)."""
    lon0, lat0, lon1, lat1 = boite
    return [(lat, lon) for lat in range(math.floor(lat0), math.ceil(lat1))
            for lon in range(math.floor(lon0), math.ceil(lon1))]


def choisir_dalles(boite, glo30_disponible, glo90_disponible):
    """La sélection, sans réseau : `glo30_disponible(lat, lon)` et `glo90_disponible(lat, lon)` rendent un booléen.

    Chaque dalle vient du GLO-30 ; à défaut du GLO-90 ; à défaut elle est absente (la mer : altitude 0).
    Rend une liste de dicts {dalle, lat, lon, source, url}, dans l'ordre (lat, lon).
    """
    out = []
    for lat, lon in sorted(dalles_necessaires(boite)):
        if glo30_disponible(lat, lon):
            src = GLO30
        elif glo90_disponible(lat, lon):
            src = GLO90
        else:
            src = ABSENTE
        out.append({"dalle": nom_dalle(lat, lon), "lat": lat, "lon": lon, "source": src,
                    "url": None if src == ABSENTE else url_dalle(lat, lon, src)})
    return out


def existe_en_ligne(lat, lon, source):
    """HEAD sur le seau : 200 -> oui ; 404 -> non ; tout autre refus est une erreur (jamais pris pour une absence)."""
    req = urllib.request.Request(url_dalle(lat, lon, source), method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60):
            return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise SourceErreur("le seau %s refuse la dalle %s (HTTP %s)" % (HOTES[source], nom_dalle(lat, lon), e.code))


def sha256_fichier(chemin):
    h = hashlib.sha256()
    with open(chemin, "rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


def telecharger(url, destination, tentatives=3):
    """Télécharge url dans destination (écriture atomique) ; ne retélécharge pas un fichier déjà là."""
    if os.path.exists(destination):
        return destination
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    derniere = None
    for _ in range(tentatives):
        tmp = destination + ".part"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=300) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
            os.replace(tmp, destination)
            return destination
        except (urllib.error.URLError, OSError) as e:
            derniere = e
    raise SourceErreur("téléchargement impossible : %s (%r)" % (url, derniere))


def preparer_dalles(boite, cache, glo30_disponible=None, glo90_disponible=None):
    """Choisit les dalles et les met en cache ; rend les entrées du manifeste (une par dalle)."""
    g30 = glo30_disponible or (lambda la, lo: existe_en_ligne(la, lo, GLO30))
    g90 = glo90_disponible or (lambda la, lo: existe_en_ligne(la, lo, GLO90))
    entrees = []
    for d in choisir_dalles(boite, g30, g90):
        e = {"dalle": d["dalle"], "lat": d["lat"], "lon": d["lon"], "source": d["source"], "adresse": d["url"]}
        if d["url"]:
            local = os.path.join(cache, os.path.basename(d["url"]))
            telecharger(d["url"], local)
            e["fichier"] = local                      # hors manifeste (propre au cache)
            e["taille"] = os.path.getsize(local)
            e["sha256"] = sha256_fichier(local)
        entrees.append(e)
    return entrees
